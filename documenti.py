"""Lettura di file: allegati delle mail e documenti mandati su Telegram.

PDF e immagini li legge Gemini (una richiesta per file); i file di testo si leggono qui, senza spendere richieste.
"""
import mimetypes
import os
import re
from html import unescape

import quota

LIMITE = 15 * 1024 * 1024  # oltre, la richiesta a Gemini con il file dentro diventa troppo grossa
TIPI_GEMINI = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
TIPI_TESTO = {"application/json", "application/xml", "application/x-tex"}
ISTRUZIONI = """Questo è il file "{nome}"{origine}. Rispondi in italiano, senza markdown.
Prima una riga su cos'è il documento. {richiesta}
Riporta importi, date, scadenze e nomi esattamente come sono scritti. Non inventare: se un'informazione non c'è, dillo."""


class NonLeggibile(Exception):
    """Il file non si può leggere (formato non supportato o troppo grande): il messaggio va detto all'utente."""


def senza_tag(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return unescape(re.sub(r"(?s)<[^>]+>", " ", html))


def tipo(nome: str, dichiarato: str = "") -> str:
    # molti client mandano i PDF come application/octet-stream: allora decide l'estensione
    if not dichiarato or dichiarato == "application/octet-stream":
        return mimetypes.guess_type(nome)[0] or "application/octet-stream"
    return dichiarato


def peso(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def e_testo(mime: str) -> bool:
    return mime.startswith("text/") or mime in TIPI_TESTO


def testo(dati: bytes, mime: str, charset: str = "") -> str:
    """Contenuto di un file di testo (CSV, txt, ics, html...), ripulito e tagliato a 8000 caratteri."""
    contenuto = dati.decode(charset or "utf-8", errors="replace")
    if mime == "text/html":
        contenuto = senza_tag(contenuto)
    contenuto = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", contenuto)).strip()
    return contenuto[:8000]


def leggi(dati: bytes, nome: str, mime: str, domanda: str = "", origine: str = "", charset: str = "") -> str:
    """Testo o risposta di Gemini sul file. Solleva NonLeggibile se il formato o la dimensione non vanno."""
    if e_testo(mime):
        return testo(dati, mime, charset)
    if mime not in TIPI_GEMINI:
        raise NonLeggibile(f"{nome} è un file {mime}: so leggere solo PDF, immagini e file di testo.")
    if len(dati) > LIMITE:
        raise NonLeggibile(f"{nome} pesa {peso(len(dati))}: troppo per leggerlo (massimo {peso(LIMITE)}).")
    return _gemini(dati, mime, nome, domanda, origine)


def _gemini(dati: bytes, mime: str, nome: str, domanda: str, origine: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        http_options=types.HttpOptions(timeout=90000),
    )
    richiesta = (f"Poi rispondi a questa domanda dell'utente: {domanda}" if domanda else
                 "Poi elenca i punti importanti: importi, date, scadenze, cosa bisogna fare.")
    istruzioni = ISTRUZIONI.format(nome=nome, origine=origine, richiesta=richiesta)
    scartati: set[str] = set()
    errore = "nessun modello disponibile"
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = client.models.generate_content(
                model=modello, contents=[types.Part.from_bytes(data=dati, mime_type=mime), istruzioni], config=config)
            quota.registra(modello, "documenti")
            return (r.text or "").strip()[:6000]
        except Exception as e:
            errore = str(e)
            tipo_errore = quota.segna_errore(modello, errore)
            quota.registra(modello, "documenti", "429" if tipo_errore else "altro")
            if tipo_errore != "minuto":
                scartati.add(modello)
    raise RuntimeError(f"Gemini non riesce a leggere il file: {errore[:120]}")
