"""Diario delle conversazioni: una nota Obsidian al giorno in Jarvis/Diario, scritta a ogni scambio."""
import datetime
import os
import re
import threading
from pathlib import Path

CARTELLA = Path(os.getenv("JARVIS_DIARIO", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Diario"))
CANALI = {"app": "finestra", "voce": "voce", "telegram": "Telegram", "web": "pagina web", "": "terminale"}
_lock = threading.Lock()
_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _file(giorno: datetime.date) -> Path:
    return CARTELLA / f"{giorno:%Y-%m-%d}.md"


def annota(canale: str, domanda: str, risposta: str):
    """Ascoltatore di Jarvis: aggiunge lo scambio alla nota di oggi."""
    adesso = datetime.datetime.now()
    percorso = _file(adesso.date())
    with _lock:
        CARTELLA.mkdir(parents=True, exist_ok=True)
        nuovo = not percorso.exists()
        with open(percorso, "a", encoding="utf-8") as f:
            if nuovo:
                f.write(f"# Diario di Jarvis · {_GIORNI[adesso.weekday()]} {adesso:%d/%m/%Y}\n")
            f.write(
                f"\n### {adesso:%H:%M} · {CANALI.get(canale, canale)}\n"
                f"**Tu:** {domanda.strip()}\n\n**Jarvis:** {risposta.strip()}\n"
            )


def leggi_diario(giorno: str) -> str:
    """Le conversazioni di un giorno passato, per rispondere a "cosa ti avevo chiesto ieri" e simili.

    Args:
        giorno: data 'AAAA-MM-GG' (calcolala dall'ora attuale: ieri, lunedì scorso...)
    """
    try:
        data = datetime.date.fromisoformat(giorno.strip()[:10])
    except ValueError:
        return "Data non valida: usa AAAA-MM-GG."
    percorso = _file(data)
    if not percorso.exists():
        return f"Nessuna conversazione il {data:%d/%m/%Y}."
    testo = percorso.read_text(encoding="utf-8")
    # i giorni lunghi si tagliano dall'inizio: le ultime conversazioni sono le più utili
    return testo if len(testo) <= 8000 else "[…inizio della giornata omesso…]\n" + testo[-8000:]


def cerca_diario(cosa: str, giorni: int = 60) -> str:
    """Cerca nelle conversazioni passate (ultimi N giorni) quelle che parlano di qualcosa.

    Args:
        cosa: parole chiave, es. 'volo Parigi', 'dentista'
        giorni: quanto indietro cercare (massimo 365)
    """
    parole = [p.lower() for p in re.findall(r"\w{3,}", cosa)]
    if not parole or not CARTELLA.exists():
        return "Nessuna conversazione trovata."
    limite = datetime.date.today() - datetime.timedelta(days=max(1, min(giorni, 365)))
    trovati = []
    for percorso in sorted(CARTELLA.glob("*.md"), reverse=True):
        try:
            data = datetime.date.fromisoformat(percorso.stem)
        except ValueError:
            continue
        if data < limite:
            break
        for scambio in percorso.read_text(encoding="utf-8").split("\n### ")[1:]:
            minuscolo = scambio.lower()
            punti = sum(minuscolo.count(p) for p in parole)
            if punti:
                trovati.append((punti, data, scambio.strip()[:600]))
    if not trovati:
        return f"Nelle conversazioni degli ultimi {giorni} giorni non si parla di '{cosa}'."
    trovati.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return "\n\n".join(f"{data:%d/%m/%Y} {scambio}" for _, data, scambio in trovati[:6])


STRUMENTI = [leggi_diario, cerca_diario]
