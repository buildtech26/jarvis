"""Quello che Jarvis ha già studiato: le note in Obsidian, in raw/ (ricerche salvate, analisi, note dell'utente) e
nella wiki compilata da raw/. Così "cosa sai su SWDA?" risponde dalla nota salvata invece di rifare la ricerca.

Le note restano in raw/ e passano nella wiki con il 'compile' (obsidian/claude.md): qui si leggono tutte e due,
così l'argomento resta noto anche dopo il compile. Jarvis qui legge soltanto.
"""
import datetime
import os
import re
from pathlib import Path

VAULT = Path(os.getenv("JARVIS_VAULT", Path.home() / "Desktop" / "obsidian"))
PARTE = 3500  # brain.py taglia i risultati degli strumenti a 4000 caratteri
# parole dei titoli troppo generiche per dire che la domanda parla di quella nota
_GENERICHE = {"core", "ucits", "etf", "fund", "index", "della", "delle", "degli", "dell", "nella", "sulla", "come",
              "guida", "note", "nota", "ricerca", "analisi", "jarvis", "indice", "wiki", "locale", "intelligenza",
              "artificiale"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9à-ù]+", " ", s.lower()).strip()


def _frontmatter(testo: str) -> tuple[dict, str]:
    """(proprietà, corpo senza frontmatter). Le liste YAML ("  - x") diventano liste."""
    m = re.match(r"(?s)^---\n(.*?)\n---\n?", testo)
    if not m:
        return {}, testo
    dati, lista = {}, None
    for riga in m.group(1).splitlines():
        if riga.startswith("  - ") and lista is not None:
            dati[lista].append(riga[4:].strip().strip('"'))
        elif ":" in riga and not riga.startswith(" "):
            chiave, valore = riga.split(":", 1)
            valore = valore.strip()
            lista = chiave.strip() if not valore else None
            if valore.startswith("[") and valore.endswith("]"):
                dati[chiave.strip()] = [v.strip().strip('"') for v in valore[1:-1].split(",") if v.strip()]
            else:
                dati[chiave.strip()] = [] if lista else valore.strip('"')
    return dati, testo[m.end():]


def _note() -> list[dict]:
    """Le note .md di raw/ e wiki/ (indici esclusi), dalla più recente."""
    file = list((VAULT / "raw").glob("*.md")) + [
        f for f in (VAULT / "wiki").rglob("*.md") if f.stem not in ("indice", "indice_wiki")]
    note = []
    for f in file:
        try:
            testo = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, corpo = _frontmatter(testo)
        h1 = re.search(r"^# (.+)$", corpo, re.M)
        alias = fm.get("alias") or fm.get("aliases") or []
        note.append({
            "file": f, "titolo": h1.group(1).strip() if h1 else f.stem.removesuffix("_COMPILED"),
            "alias": alias if isinstance(alias, list) else [alias],
            "corpo": (corpo[:h1.start()] + corpo[h1.end():] if h1 else corpo).strip(),  # il titolo c'è già
            "dove": f.relative_to(VAULT).as_posix(),
            "data": fm.get("data_aggiornamento") or fm.get("data") or f"{datetime.date.fromtimestamp(f.stat().st_mtime)}",
        })
    note.sort(key=lambda n: n["file"].stat().st_mtime, reverse=True)
    return note


def citati(testo: str) -> list[str]:
    """Titoli delle note di cui parla la domanda (per alias, titolo intero o almeno 2 parole significative del
    titolo): brain.py li usa per mandare a Gemini gli strumenti qui sotto anche senza parole come "cosa sai"."""
    t = f" {_norm(testo)} "
    parole_domanda = set(t.split())
    trovati = []
    for n in _note():
        if any(len(_norm(a)) >= 3 and f" {_norm(a)} " in t for a in n["alias"]) or f" {_norm(n['titolo'])} " in t:
            trovati.append(n["titolo"])
            continue
        significative = {p for p in _norm(n["titolo"]).split() if len(p) >= 4 and p not in _GENERICHE}
        if significative and len(significative & parole_domanda) >= min(2, len(significative)):
            trovati.append(n["titolo"])
    return trovati


def cerca_sapere(cosa: str) -> str:
    """Cerca tra le cose che hai già studiato: le ricerche che hai salvato in Obsidian e la wiki dell'utente. Usalo
    PRIMA di cerca_web quando l'utente chiede cosa sai o cosa avevi trovato su un argomento, o fa una domanda su un
    argomento che hai già ricercato. Restituisce titolo, data ed estratti: per il testo intero usa leggi_sapere.

    Args:
        cosa: l'argomento o le parole chiave, es. 'SWDA', 'mini PC per IA'
    """
    parole = [p for p in _norm(cosa).split() if len(p) >= 3]
    if not parole:
        return "Dimmi almeno una parola da cercare."
    punteggi = []
    for n in _note():
        titolo = _norm(n["titolo"] + " " + " ".join(n["alias"]))
        corpo = n["corpo"].lower()
        punti = sum(10 * titolo.count(p) + corpo.count(p) for p in parole)
        if punti:
            punteggi.append((punti, n))
    if not punteggi:
        return f"Nelle note salvate non c'è niente su '{cosa}': se serve, cerca sul web."
    punteggi.sort(key=lambda x: x[0], reverse=True)
    righe = []
    for _, n in punteggi[:4]:
        corpo = n["corpo"]
        punti_chiave = re.search(r"(?s)## Punti chiave\n(.*?)(?:\n## |\Z)", corpo)
        if punti_chiave:
            estratto = punti_chiave.group(1).strip()[:700]
        else:
            inizio = next((corpo.lower().find(p) for p in parole if p in corpo.lower()), 0)
            estratto = "…" + corpo[max(0, inizio - 150): inizio + 450].replace("\n", " ") + "…"
        righe.append(f"« {n['titolo']} » ({n['dove']}, aggiornata {n['data']})\n{estratto}")
    return "\n\n".join(righe)


def leggi_sapere(titolo: str, parte: int = 1) -> str:
    """Testo completo di una nota che hai già studiato (trovata con cerca_sapere), a parti di circa 3500 caratteri.

    Args:
        titolo: il titolo della nota, anche parziale
        parte: 1 per l'inizio; la risposta dice se ci sono altre parti
    """
    t = _norm(titolo)
    candidati = [n for n in _note() if t and (t in _norm(n["titolo"]) or t in {_norm(a) for a in n["alias"]})]
    if not candidati:
        return f"Nessuna nota salvata con '{titolo}' nel titolo: prova cerca_sapere."
    n = candidati[0]
    pezzi = [n["corpo"][i:i + PARTE] for i in range(0, len(n["corpo"]), PARTE)] or [""]
    parte = max(1, min(parte, len(pezzi)))
    seguito = f"\n\n[parte {parte} di {len(pezzi)}: per continuare leggi_sapere con parte={parte + 1}]" \
        if parte < len(pezzi) else ""
    return f"« {n['titolo']} » ({n['dove']}, aggiornata {n['data']})\n\n{pezzi[parte - 1]}{seguito}"


STRUMENTI = [cerca_sapere, leggi_sapere]
