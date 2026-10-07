"""Memoria a lungo termine: una nota Markdown nel vault Obsidian, che Jarvis legge a ogni domanda."""
import datetime
import os
import threading
from pathlib import Path

FILE = Path(os.getenv("JARVIS_MEMORIA", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Memoria.md"))
MAX_CARATTERI = 8000  # oltre, la nota occupa troppo contesto: meglio sfoltirla in Obsidian
_lock = threading.Lock()

INTESTAZIONE = """# Memoria di Jarvis

Quello che Jarvis sa di me. Lo legge a ogni domanda: puoi aggiungere, correggere o cancellare righe a mano.
Non scrivere qui password o dati sensibili: il contenuto viene inviato a Gemini.

## Fatti
"""


def _leggi() -> str:
    if not FILE.exists():
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(INTESTAZIONE, encoding="utf-8")
    return FILE.read_text(encoding="utf-8")


def contenuto() -> str:
    """Le righe utili della nota, per il prompt di sistema (l'intestazione esplicativa non serve al modello)."""
    righe = [r for r in _leggi().splitlines() if r.strip() and not r.startswith("# ") and "Lo legge a ogni domanda" not in r
             and "Non scrivere qui password" not in r]
    testo = "\n".join(righe)
    return testo[:MAX_CARATTERI]


def ricorda(fatto: str) -> str:
    """Salva nella memoria permanente un fatto stabile sull'utente (preferenze, persone, abitudini, cose da ricordare).

    Args:
        fatto: una frase breve e autonoma, es. 'Luca è il fratello di Mattia'
    """
    fatto = " ".join(fatto.split())
    with _lock:
        testo = _leggi()
        if fatto.lower() in testo.lower():
            return "Lo sapevo già."
        if not testo.endswith("\n"):
            testo += "\n"
        testo += f"- {fatto} <!-- {datetime.date.today():%Y-%m-%d} -->\n"
        FILE.write_text(testo, encoding="utf-8")
    return f"Memorizzato: {fatto}"


def dimentica(cosa: str) -> str:
    """Toglie dalla memoria permanente la riga che contiene questo testo.

    Args:
        cosa: parole contenute nella riga da cancellare
    """
    with _lock:
        righe = _leggi().splitlines()
        trovate = [r for r in righe if r.lstrip().startswith("-") and cosa.lower() in r.lower()]
        if not trovate:
            return f"Nella memoria non c'è niente che contenga '{cosa}'."
        if len(trovate) > 1:
            return "Più righe corrispondono, sii più preciso:\n" + "\n".join(trovate)
        righe.remove(trovate[0])
        FILE.write_text("\n".join(righe) + "\n", encoding="utf-8")
    tolta = trovate[0].lstrip("- ").split(" <!--")[0]
    # si cancella la riga intera: se conteneva anche altro di ancora vero, il modello deve risalvarlo
    return (f"Cancellata l'intera riga: '{tolta}'. Se una parte di quella riga è ancora vera, "
            "salvala subito di nuovo con ricorda, senza la parte da dimenticare.")


STRUMENTI = [ricorda, dimentica]
