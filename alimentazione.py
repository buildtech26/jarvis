"""Consigli sull'alimentazione: le linee guida sono una nota Obsidian, calorie e proteine si calcolano dai dati scritti lì.

Niente diario dei pasti: Jarvis legge la nota e risponde da lì. L'obiettivo è perdere grasso: calorie = metabolismo a
riposo (formula di Mifflin-St Jeor) × attività − deficit, mai sotto il metabolismo a riposo.
"""
import datetime
import os
import re
import shutil
from pathlib import Path

from allenamento import _sezione

CARTELLA = Path(os.getenv("JARVIS_ALIMENTAZIONE", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Alimentazione"))
MODELLO = Path(__file__).with_name("alimentazione_iniziale.md")  # copiata in Obsidian se la nota non c'è


def _nota() -> str:
    file = CARTELLA / "Linee guida.md"
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODELLO, file)
    return file.read_text(encoding="utf-8")


def _impostazioni(testo: str) -> dict:
    valori = {}
    for riga in _sezione(testo, "Impostazioni", 2).splitlines():
        m = re.match(r"\s*[-*]\s*([^:]+?)\s*:\s*(.*)$", riga)
        if m:
            valori[m.group(1).lower()] = m.group(2).strip()
    return valori


def _numero(testo: str | None) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", testo or "")
    return float(m.group().replace(",", ".")) if m else None


def _eta(testo: str | None, oggi: datetime.date) -> int | None:
    try:
        nascita = datetime.date.fromisoformat((testo or "").strip()[:10])
    except ValueError:
        return None
    return oggi.year - nascita.year - ((oggi.month, oggi.day) < (nascita.month, nascita.day))


def _obiettivi(imp: dict, oggi: datetime.date) -> str:
    peso, altezza, eta = _numero(imp.get("peso")), _numero(imp.get("altezza")), _eta(imp.get("nascita"), oggi)
    sesso = (imp.get("sesso") or "").strip().upper()[:1]
    mancano = [nome for nome, ok in [("peso", peso), ("altezza", altezza), ("sesso (M o F)", sesso in ("M", "F")),
                                     ("data di nascita", eta)] if not ok]
    if mancano:
        return (f"Calorie e proteine non calcolate: nelle Impostazioni della nota mancano {', '.join(mancano)} "
                "(Obsidian, Jarvis\\Alimentazione\\Linee guida.md). Dai solo consigli generali e chiedi di scriverli lì.")
    riposo = 10 * peso + 6.25 * altezza - 5 * eta + (5 if sesso == "M" else -161)
    mantenimento = riposo * (_numero(imp.get("attività")) or 1.55)
    calorie = max(mantenimento - (_numero(imp.get("deficit")) or 400), riposo)
    proteine = peso * (_numero(imp.get("proteine per kg")) or 1.8)
    return (f"Obiettivi al giorno: circa {round(calorie / 50) * 50} kcal e almeno {proteine:.0f} g di proteine "
            f"(mantenimento {round(mantenimento / 50) * 50} kcal, metabolismo a riposo {riposo:.0f} kcal; "
            f"peso {peso:g} kg, {eta} anni).")


def _senza_impostazioni(testo: str) -> str:
    righe, dentro = [], False
    for riga in testo.splitlines():
        if riga.startswith("## "):
            dentro = riga[3:].strip().lower().startswith("impostazioni")
        if not dentro:
            righe.append(riga)
    return "\n".join(righe).strip()


# --- strumenti per Gemini ---

def consigli_alimentazione() -> str:
    """Le linee guida sull'alimentazione dell'utente (vuole perdere grasso, cucina da sé con un budget stretto) con le
    sue calorie e proteine al giorno. Da leggere prima di ogni consiglio su cosa e quanto mangiare."""
    testo = _nota()
    return f"{_obiettivi(_impostazioni(testo), datetime.date.today())}\n\n{_senza_impostazioni(testo)}"


STRUMENTI = [consigli_alimentazione]
