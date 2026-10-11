"""Foto mandate su Telegram: Gemini le legge e finiscono in Obsidian, in raw/ (l'inbox della knowledge base).

Per ogni foto si salvano in raw/ l'immagine e una nota .md con descrizione e trascrizione del testo, così
quando Claude compila raw/ nella wiki ha già il contenuto leggibile. Le regole del vault (claude.md) dicono
che raw/ è popolata dall'utente: Jarvis ci scrive per conto suo, mai in wiki/.
"""
import datetime
import json
import os
import re
from pathlib import Path

import quota

CARTELLA = Path(os.getenv("JARVIS_FOTO", Path.home() / "Desktop" / "obsidian" / "raw"))
ISTRUZIONI = """Analizza questa foto mandata da {nome} e rispondi in italiano con un JSON.
- titolo: 2-6 parole che identificano il contenuto (es. "Lavagna limiti notevoli", "Orari segreteria DEI"), senza data
- tipo: uno tra lavagna, appunti, documento, slide, schermata, scontrino, volantino, foto
- descrizione: 1-3 frasi su cosa mostra
- trascrizione: TUTTO il testo leggibile, fedele, in markdown (formule in LaTeX tra $...$, tabelle in markdown); \
stringa vuota se non c'è testo
- risposta: SEMPRE IN ITALIANO. Se la didascalia contiene una domanda o una richiesta, rispondi qui in modo breve \
e diretto, con un tocco di ironia da maggiordomo, senza markdown, con gli accenti corretti, orari e numeri in \
cifre (14:30, non "quattordici e trenta"); altrimenti una frase che dica cosa hai salvato
Didascalia dell'utente: {didascalia}"""


def _nome_file(titolo: str) -> str:
    pulito = re.sub(r'[\\/:*?"<>|#^\[\]]', "", titolo).strip()[:60] or "Foto"
    base = f"{datetime.datetime.now():%Y-%m-%d %H%M} {pulito}"
    nome, n = base, 2
    while (CARTELLA / f"{nome}.md").exists():  # due foto uguali nello stesso minuto non si sovrascrivono
        nome, n = f"{base} {n}", n + 1
    return nome


def _analizza(dati: bytes, mime: str, didascalia: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    schema = {
        "type": "object",
        "properties": {k: {"type": "string"} for k in ("titolo", "tipo", "descrizione", "trascrizione", "risposta")},
        "required": ["titolo", "tipo", "descrizione", "trascrizione", "risposta"],
    }
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        http_options=types.HttpOptions(timeout=60000),
    )
    testo = ISTRUZIONI.format(nome=os.getenv("JARVIS_NOME_UTENTE", "Mattia"), didascalia=didascalia or "(nessuna)")
    scartati: set[str] = set()
    errore = "nessun modello disponibile"
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = client.models.generate_content(
                model=modello, contents=[types.Part.from_bytes(data=dati, mime_type=mime), testo], config=config)
            quota.registra(modello, "foto")
            return json.loads(r.text)
        except Exception as e:
            errore = str(e)
            tipo = quota.segna_errore(modello, errore)
            quota.registra(modello, "foto", "429" if tipo else "altro")
            if tipo != "minuto":
                scartati.add(modello)
    raise RuntimeError(f"Gemini non riesce a leggere la foto: {errore[:120]}")


def analizza(dati: bytes, estensione: str, didascalia: str = "") -> dict:
    """Titolo, tipo, descrizione, trascrizione e risposta; se Gemini non risponde, una foto da completare."""
    mime = {".png": "image/png", ".webp": "image/webp"}.get(estensione.lower(), "image/jpeg")
    try:
        return _analizza(dati, mime, didascalia)
    except Exception as e:
        return {"titolo": "Foto", "tipo": "foto", "descrizione": "", "trascrizione": "",
                "risposta": f"Ho salvato la foto, ma non sono riuscito a leggerla ({e})."}


def salva_foto(dati: bytes, estensione: str, didascalia: str = "", a: dict | None = None) -> tuple[str, str]:
    """Salva la foto in raw/ con la sua nota (analizzata qui se a manca) e restituisce (risposta per l'utente,
    riassunto per la conversazione). Se Gemini non risponde la foto si salva comunque, con una nota da completare."""
    a = a or analizza(dati, estensione, didascalia)
    nome = _nome_file(a.get("titolo", "Foto"))
    CARTELLA.mkdir(parents=True, exist_ok=True)
    immagine = CARTELLA / f"{nome}{estensione.lower()}"
    immagine.write_bytes(dati)
    adesso = datetime.datetime.now()
    nota = [
        "---",
        f"fonte: Telegram (Jarvis)",
        f"data: {adesso:%Y-%m-%d %H:%M}",
        f"tipo: {a.get('tipo', 'foto')}",
        "---",
        f"# {a.get('titolo', 'Foto')}",
        "",
        f"![[{immagine.name}]]",
        "",
    ]
    if didascalia:
        nota += [f"**Didascalia:** {didascalia}", ""]
    if a.get("descrizione"):
        nota += ["## Descrizione", a["descrizione"], ""]
    if a.get("trascrizione", "").strip():
        nota += ["## Trascrizione", a["trascrizione"].strip(), ""]
    (CARTELLA / f"{nome}.md").write_text("\n".join(nota), encoding="utf-8")
    risposta = f"{a.get('risposta', '').strip()}\n\n📎 Salvata in Obsidian: raw/{nome}.md"
    # quello che resta nella conversazione: basta per le domande successive ("e la terza riga?")
    riassunto = (f"[Foto salvata in raw/{nome}.md] {a.get('descrizione', '')}\n"
                 f"Testo nella foto: {a.get('trascrizione', '')[:2500]}")
    return risposta, riassunto
