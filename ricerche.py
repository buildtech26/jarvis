"""Ricerche su richiesta: Jarvis cerca sul web, legge le pagine migliori e salva una nota in Obsidian, in raw/.

Da raw/ la nota passa nella wiki con il 'compile' descritto in obsidian/claude.md: Jarvis non scrive mai in wiki/.
Se sullo stesso argomento c'è già una ricerca di Jarvis non ancora compilata, viene aggiornata invece di duplicarla;
se è già stata compilata (_COMPILED) se ne scrive una nuova, che il compile userà per aggiornare l'articolo.
"""
import datetime
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

import requests
from ddgs import DDGS

import quota

CARTELLA = Path(os.getenv("JARVIS_RICERCHE", Path.home() / "Desktop" / "obsidian" / "raw"))
FONTE = "Jarvis (ricerca web)"  # nel frontmatter: distingue le note di Jarvis da quelle messe in raw/ dall'utente
PAGINE = 4
CARATTERI_PAGINA = 7000

ISTRUZIONI = """Sei il ricercatore di {nome}. Scrivi in italiano una nota Markdown su: {argomento}
{domanda}
Usa SOLO le informazioni nelle pagine qui sotto: niente dati presi dalla tua memoria. Per numeri che cambiano nel \
tempo (prezzi, TER, patrimonio, rendimenti) scrivi la data a cui si riferiscono, se la pagina la indica. Se un dato \
importante non c'è, scrivi che non l'hai trovato. Quando le fonti si contraddicono, riporta entrambe le versioni.
{precedente}
Rispondi con un JSON:
- titolo: il nome dell'argomento, 2-8 parole, senza data (es. "iShares Core MSCI World UCITS ETF", "Vanguard FTSE All-World"). \
{titoli}
- alias: altri nomi con cui lo si cerca (ticker, ISIN, sigle), anche lista vuota
- corpo: la nota in Markdown, SENZA frontmatter, SENZA titolo H1 e SENZA sezione fonti (le aggiungo io). Inizia con \
2-3 righe di introduzione, poi "## Punti chiave" con 3-7 punti, poi le sezioni che servono (per un ETF: dati \
principali in tabella con ISIN, TER, replica, distribuzione, domicilio, valuta, patrimonio; indice replicato; \
composizione; costi e fiscalità se presenti)
- sintesi: 1-2 frasi senza markdown per dire a voce cosa hai trovato

PAGINE LETTE:
{pagine}"""


def _testo_pagina(url: str) -> str:
    r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    r.raise_for_status()
    if "html" not in r.headers.get("Content-Type", "html"):
        return ""  # PDF e altri file: il testo grezzo sarebbe illeggibile
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)\b.*?</\1>", " ", r.text)
    testo = unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    return re.sub(r"\s{2,}", "\n", testo).strip()


def _leggi_risultati(query: str) -> list[dict]:
    risultati = DDGS().text(query, region="it-it", max_results=8) or []
    risultati = [r for r in risultati if not r["href"].lower().endswith(".pdf")][:PAGINE + 2]

    def leggi(r):
        try:
            return {**r, "testo": _testo_pagina(r["href"])[:CARATTERI_PAGINA]}
        except Exception:
            return {**r, "testo": ""}

    with ThreadPoolExecutor(max_workers=len(risultati) or 1) as pool:
        letti = list(pool.map(leggi, risultati))
    # le pagine che non si scaricano (blocchi anti-bot) restano con l'estratto della ricerca
    buoni = [r for r in letti if len(r["testo"]) > 500][:PAGINE]
    return buoni or [{**r, "testo": r["body"]} for r in letti[:PAGINE]]


def _frontmatter(testo: str) -> dict:
    m = re.match(r"(?s)^---\n(.*?)\n---", testo)
    if not m:
        return {}
    dati, lista = {}, None
    for riga in m.group(1).splitlines():
        if riga.startswith("  - ") and lista is not None:
            dati[lista].append(riga[4:].strip().strip('"'))
        elif ":" in riga:
            chiave, valore = riga.split(":", 1)
            lista = chiave.strip() if not valore.strip() else None
            dati[chiave.strip()] = [] if lista else valore.strip().strip('"')
    return dati


def _ricerche_esistenti() -> list[dict]:
    """Le note di ricerca di Jarvis in raw/, compilate o no."""
    note = []
    for f in CARTELLA.glob("*.md"):
        try:
            fm = _frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm.get("fonte") == FONTE:
            note.append({"file": f, "titolo": f.stem.removesuffix("_COMPILED"), "alias": fm.get("alias", []),
                         "fonti": fm.get("fonti", []), "compilata": f.stem.endswith("_COMPILED")})
    return note


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _trova(nome: str, note: list[dict]) -> dict | None:
    """La ricerca già fatta sullo stesso argomento: stesso titolo o un alias (ticker, ISIN) uguale. Tra una
    compilata e una no vince quella ancora da compilare, che si può aggiornare."""
    n = _norm(nome)
    if not n:
        return None
    simili = [x for x in note if n == _norm(x["titolo"]) or n in {_norm(a) for a in x["alias"]}
              or (len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", _norm(x["titolo"])))]
    simili.sort(key=lambda x: x["compilata"])
    return simili[0] if simili else None


def _scrivi(dati: dict, pagine: list[dict]) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    schema = {
        "type": "object",
        "properties": {"titolo": {"type": "string"}, "alias": {"type": "array", "items": {"type": "string"}},
                       "corpo": {"type": "string"}, "sintesi": {"type": "string"}},
        "required": ["titolo", "alias", "corpo", "sintesi"],
    }
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        http_options=types.HttpOptions(timeout=90000),
    )
    testo = ISTRUZIONI.format(**dati, pagine="\n\n".join(
        f"[{i}] {p['title']} — {p['href']}\n{p['testo']}" for i, p in enumerate(pagine, 1)))
    scartati: set[str] = set()
    errore = "nessun modello disponibile"
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = client.models.generate_content(model=modello, contents=testo, config=config)
            quota.registra(modello, "ricerca")
            return r.text
        except Exception as e:
            errore = str(e)
            tipo = quota.segna_errore(modello, errore)
            quota.registra(modello, "ricerca", "429" if tipo else "altro")
            if tipo != "minuto":
                scartati.add(modello)
    raise RuntimeError(f"Gemini non riesce a scrivere la nota: {errore[:120]}")


def ricerca_e_salva(argomento: str, domanda: str = "") -> str:
    """Fa una ricerca approfondita sul web e la SALVA come nota in Obsidian (cartella raw/). Usalo SOLO quando
    l'utente chiede di salvare, memorizzare o mettere in Obsidian una ricerca ("cerca info sull'ETF SWDA e salvale",
    "fai una ricerca su X e mettila in Obsidian"). Per una semplice domanda usa cerca_web. Se esiste già una ricerca
    sullo stesso argomento viene aggiornata. Dopo, rispondi con la sintesi in 1-2 frasi e il nome della nota.

    Args:
        argomento: cosa cercare, il più preciso possibile (es. 'ETF SWDA iShares Core MSCI World', 'VWCE')
        domanda: aspetti precisi che l'utente vuole sapere, se li ha detti (es. 'costi e fiscalità'); altrimenti vuoto
    """
    note = _ricerche_esistenti()
    vecchia = _trova(argomento, note)
    pagine = _leggi_risultati(f"{argomento} {domanda}".strip())
    if not pagine:
        return f"La ricerca su '{argomento}' non ha dato risultati: non ho salvato niente."

    da_aggiornare = vecchia if vecchia and not vecchia["compilata"] else None
    precedente = ""
    if da_aggiornare:
        corpo_vecchio = da_aggiornare["file"].read_text(encoding="utf-8").split("\n---\n", 1)[-1]
        precedente = ("Esiste già una nota su questo argomento, qui sotto: aggiornala. Tieni quello che è ancora "
                      "valido, sostituisci i dati con quelli più recenti delle pagine e aggiungi le novità.\n"
                      f"NOTA ESISTENTE:\n{corpo_vecchio[:8000]}\n")
    if vecchia:
        titoli = f'Usa esattamente il titolo "{vecchia["titolo"]}".'
    elif note:
        titoli = ("Se l'argomento è lo stesso di una di queste ricerche già salvate, usa esattamente quel titolo: "
                  + "; ".join(f'"{x["titolo"]}"' for x in note[:40]))
    else:
        titoli = ""
    a = json.loads(_scrivi({
        "nome": os.getenv("JARVIS_NOME_UTENTE", "Mattia"), "argomento": argomento,
        "domanda": f"L'utente vuole sapere in particolare: {domanda}" if domanda else "",
        "precedente": precedente, "titoli": titoli,
    }, pagine))

    titolo = re.sub(r'[\\/:*?"<>|#^\[\]]', "", a["titolo"]).strip()[:80] or argomento
    vecchia = vecchia or _trova(titolo, note)  # Gemini ha riconosciuto una ricerca che il nome non trovava
    if vecchia and not vecchia["compilata"]:
        file = vecchia["file"]
        titolo = vecchia["titolo"]
    else:
        file = CARTELLA / f"{titolo}.md"
        if file.exists():  # una nota dell'utente con lo stesso nome: non si tocca
            file = CARTELLA / f"{titolo} (ricerca Jarvis).md"
    fonti = list(dict.fromkeys([p["href"] for p in pagine] + (vecchia["fonti"] if vecchia else [])))
    alias = list(dict.fromkeys(a.get("alias", []) + (vecchia["alias"] if vecchia else [])))

    righe = ["---", f"fonte: {FONTE}", f"data: {datetime.datetime.now():%Y-%m-%d %H:%M}", "tipo: ricerca",
             f'richiesta: "{(argomento + (" — " + domanda if domanda else "")).replace(chr(34), chr(39))}"']
    if vecchia and vecchia["compilata"]:
        righe.append(f'aggiorna: "raw/{vecchia["file"].name}"')  # al compile: aggiornare l'articolo già scritto
    righe.append("alias:" if alias else "alias: []")
    righe += [f'  - "{x}"' for x in alias]
    righe.append("fonti:")
    righe += [f"  - {u}" for u in fonti]
    righe += ["---", f"# {titolo}", "", a["corpo"].strip(), "", "## Fonti"]
    # DuckDuckGo a volte incolla più titoli uno dopo l'altro: oltre i 90 caratteri basta il dominio
    righe += [f"- [{p['title'] if len(p['title']) <= 90 else p['href'].split('/')[2]}]({p['href']})" for p in pagine]
    CARTELLA.mkdir(parents=True, exist_ok=True)
    file.write_text("\n".join(righe) + "\n", encoding="utf-8")

    azione = "Aggiornata" if vecchia and not vecchia["compilata"] else "Salvata"
    return f"{azione} in Obsidian la nota raw/{file.name} ({len(pagine)} pagine lette). {a['sintesi']}"


STRUMENTI = [ricerca_e_salva]
