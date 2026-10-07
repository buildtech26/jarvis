"""Analisi di società ed ETF con company-analyst: i numeri vengono dal suo codice, il testo da Gemini di Jarvis.

company-analyst resta un progetto a parte: Jarvis lancia analista_ponte.py con il Python del venv di company-analyst
(pandas e yfinance non entrano nel venv di Jarvis) e riceve il prompt già pronto, scheda dei fatti più istruzioni.
L'analisi completa finisce in Obsidian, in raw/ come le ricerche salvate, e passa nella wiki con il 'compile'.
Una nuova analisi dello stesso tipo sullo stesso titolo sovrascrive la nota finché non è stata compilata.
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

import quota
import ricerche

CARTELLA_ANALYST = Path(os.getenv("COMPANY_ANALYST", Path.home() / "Desktop" / "prog" / "company-analyst"))
PONTE = Path(__file__).with_name("analista_ponte.py")
FONTE = "Jarvis (company-analyst)"  # nel frontmatter: distingue queste note da quelle dell'utente e dalle ricerche web

ISTRUZIONI = """{prompt}

---

# Formato della risposta

Rispondi con un JSON:
- corpo: l'analisi completa in Markdown, seguendo il compito qui sopra, SENZA frontmatter e SENZA titolo H1
- sintesi: 2-3 frasi senza markdown, in italiano parlato, da leggere ad alta voce: il succo dell'analisi con al \
massimo due numeri, scritti in cifre. Nessuna raccomandazione di acquisto o vendita."""


def _prompt(azienda: str, tipo: str) -> dict:
    python = CARTELLA_ANALYST / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise RuntimeError(f"Non trovo company-analyst in {CARTELLA_ANALYST} (manca .venv).")
    r = subprocess.run([str(python), str(PONTE), str(CARTELLA_ANALYST), azienda, tipo], cwd=CARTELLA_ANALYST,
                       capture_output=True, text=True, encoding="utf-8", timeout=180,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    righe = [x for x in r.stdout.splitlines() if x.startswith("{")]
    if not righe:
        raise RuntimeError(f"company-analyst non ha risposto: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(righe[-1])


def _scrivi(dati: dict) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    config = types.GenerateContentConfig(
        system_instruction=dati["sistema"],
        response_mime_type="application/json",
        response_schema={
            "type": "object",
            "properties": {"corpo": {"type": "string"}, "sintesi": {"type": "string"}},
            "required": ["corpo", "sintesi"],
        },
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        http_options=types.HttpOptions(timeout=150000),
    )
    testo = ISTRUZIONI.format(prompt=dati["prompt"])
    scartati: set[str] = set()
    errore = "nessun modello disponibile"
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = client.models.generate_content(model=modello, contents=testo, config=config)
            quota.registra(modello, "analisi")
            return json.loads(r.text)
        except Exception as e:
            errore = str(e)
            tipo = quota.segna_errore(modello, errore)
            quota.registra(modello, "analisi", "429" if tipo else "altro")
            if tipo != "minuto":
                scartati.add(modello)
    raise RuntimeError(f"Gemini non riesce a scrivere l'analisi: {errore[:120]}")


def _file_nota(titolo: str) -> tuple[Path, Path | None]:
    """Dove scrivere, e la nota già compilata da aggiornare al compile (se c'è)."""
    cartella = ricerche.CARTELLA
    file = cartella / f"{titolo}.md"
    compilata = cartella / f"{titolo}_COMPILED.md"
    if file.exists() and ricerche._frontmatter(file.read_text(encoding="utf-8")).get("fonte") != FONTE:
        file = cartella / f"{titolo} (analisi Jarvis).md"  # una nota dell'utente con lo stesso nome: non si tocca
    return file, compilata if compilata.exists() else None


def analizza_azienda(azienda: str, tipo: str = "report") -> str:
    """Analisi fondamentale di una società quotata o di un ETF con i dati di Yahoo Finance (bilanci, indici,
    valutazione, rischi), e la SALVA in Obsidian (raw/). Usalo quando l'utente chiede di analizzare un'azienda, un
    titolo o un ETF ("analizza Apple", "com'è messa Ferrari", "fammi un'analisi di Eni", "spiegami Nvidia da
    principiante", "ci sono bandiere rosse su Tesla?"). Per il solo prezzo o una notizia usa cerca_web. Impiega
    30-60 secondi. Dopo, rispondi con la sintesi e di' che l'analisi completa è in Obsidian; mai consigli di acquisto.

    Args:
        azienda: nome o ticker della società o dell'ETF (es. 'Apple', 'ENI.MI', 'VWCE')
        tipo: 'report' (quadro completo, predefinito), 'earnings' (ultimo trimestre), 'redflags' (bandiere rosse,
            conti sospetti), 'moat' (vantaggio competitivo), 'valuation' (costa caro rispetto ai concorrenti?),
            'dcf' (fair value e scenari), 'catalysts' (cosa può muovere il titolo nei prossimi mesi), 'management'
            (qualità della gestione), 'debate' (pro e contro), 'beginner' (spiegazione semplice). Per un ETF:
            'fondo-report', 'fondo-costi', 'fondo-dentro' (composizione), 'fondo-rischio', 'fondo-orizzonte'
    """
    dati = _prompt(azienda, tipo)
    if "errore" in dati:
        return f"company-analyst non è riuscito ad analizzare '{azienda}': {dati['errore']}"
    a = _scrivi(dati)

    titolo = re.sub(r'[\\/:*?"<>|#^\[\]]', "", f"{dati['analisi']} — {dati['nome']} ({dati['ticker']})").strip()[:120]
    file, compilata = _file_nota(titolo)
    righe = ["---", f"fonte: {FONTE}", f"data: {datetime.datetime.now():%Y-%m-%d %H:%M}", "tipo: analisi",
             f"ticker: {dati['ticker']}", f'analisi: "{dati["analisi"]}"',
             f'richiesta: "{(azienda + " — " + tipo).replace(chr(34), chr(39))}"']
    if compilata:
        righe.append(f'aggiorna: "raw/{compilata.name}"')  # al compile: aggiornare l'articolo già scritto
    righe += ["alias:", f'  - "{dati["ticker"]}"', f'  - "{dati["nome"]}"', "---", f"# {titolo}", "",
              "> Numeri da Yahoo Finance calcolati con company-analyst; testo scritto da Gemini. "
              "Non è una raccomandazione di investimento.", "", a["corpo"].strip(), ""]
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("\n".join(righe), encoding="utf-8")
    return f"Salvata in Obsidian la nota raw/{file.name}. {a['sintesi']}"


STRUMENTI = [analizza_azienda]
