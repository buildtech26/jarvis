"""Articoli salvati col Web Clipper di Obsidian nella cartella Jarvis/Articoli del vault."""
import datetime
import os
import re
from pathlib import Path

CARTELLA = Path(os.getenv("JARVIS_ARTICOLI", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Articoli"))


def _articoli() -> list[Path]:
    CARTELLA.mkdir(parents=True, exist_ok=True)
    return sorted(CARTELLA.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)


def _corpo(testo: str) -> str:
    # via le proprietà iniziali (--- ... ---) che il Web Clipper aggiunge in cima
    return re.sub(r"\A---\n.*?\n---\n", "", testo, flags=re.S).strip()


def _proprieta(testo: str, nome: str) -> str:
    m = re.search(rf"^{nome}:\s*\"?(.+?)\"?\s*$", testo, flags=re.M)
    return m.group(1) if m else ""


def elenca_articoli(quanti: int = 10) -> str:
    """Elenca gli articoli salvati più di recente."""
    elenco = _articoli()[: max(1, min(quanti, 30))]
    if not elenco:
        return "Non ci sono articoli salvati."
    return "\n".join(
        f"{datetime.date.fromtimestamp(p.stat().st_mtime):%d/%m} | {p.stem}" for p in elenco
    )


def cerca_articoli(cosa: str) -> str:
    """Cerca tra gli articoli salvati quelli che parlano di un argomento (cerca nel titolo e nel testo).

    Args:
        cosa: una o più parole chiave, es. 'ETF', 'intelligenza artificiale'
    """
    parole = [p.lower() for p in re.findall(r"\w{3,}", cosa)]
    if not parole:
        return "Dimmi almeno una parola da cercare."
    punteggi = []
    for p in _articoli():
        testo = p.read_text(encoding="utf-8", errors="replace")
        titolo, corpo = p.stem.lower(), testo.lower()
        punti = sum(5 * titolo.count(w) + corpo.count(w) for w in parole)
        if punti:
            punteggi.append((punti, p, testo))
    if not punteggi:
        return f"Nessun articolo salvato parla di '{cosa}'."
    punteggi.sort(key=lambda x: x[0], reverse=True)
    righe = []
    for _, p, testo in punteggi[:5]:
        corpo = _corpo(testo)
        inizio = next((corpo.lower().find(w) for w in parole if w in corpo.lower()), 0)
        estratto = corpo[max(0, inizio - 100): inizio + 250].replace("\n", " ")
        righe.append(f"« {p.stem} » ({_proprieta(testo, 'source') or 'fonte sconosciuta'})\n…{estratto}…")
    return "\n\n".join(righe)


def leggi_articolo(titolo: str) -> str:
    """Testo completo di un articolo salvato, per riassumerlo o rispondere a domande su di esso.

    Args:
        titolo: il titolo dell'articolo, anche parziale (da elenca_articoli o cerca_articoli)
    """
    candidati = [p for p in _articoli() if titolo.lower() in p.stem.lower()]
    if not candidati:
        return f"Nessun articolo salvato con '{titolo}' nel titolo."
    testo = candidati[0].read_text(encoding="utf-8", errors="replace")
    return f"« {candidati[0].stem} »\n\n{_corpo(testo)[:8000]}"


STRUMENTI = [elenca_articoli, cerca_articoli, leggi_articolo]
