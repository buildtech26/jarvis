"""Prova dei consigli sull'alimentazione (alimentazione.py) con una cartella temporanea: la nota vera non viene toccata.

    .venv\\Scripts\\python prova_alimentazione.py
"""
import datetime
import sys
import tempfile
from pathlib import Path

import alimentazione


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    cartella = Path(tempfile.mkdtemp(prefix="prova_alimentazione_"))
    alimentazione.CARTELLA = cartella / "Alimentazione"
    oggi = datetime.date(2026, 10, 11)

    r = alimentazione.consigli_alimentazione()
    nota = cartella / "Alimentazione" / "Linee guida.md"
    atteso(nota.exists(), "nota copiata in Obsidian")
    atteso("mancano peso, altezza, sesso" in r, "dati mancanti nella nota nuova", r[:200])
    atteso("- deficit:" not in r and "Cosa vogliono dire" not in r and "## Il piatto" in r and "## Cosa evitare" in r,
           "linee guida senza impostazioni")

    imp = alimentazione._impostazioni(nota.read_text(encoding="utf-8"))
    atteso(imp["nascita"] == "2006-03-01" and imp["attività"] == "1.55", "impostazioni lette", imp)
    atteso(alimentazione._eta(imp["nascita"], oggi) == 20, "età prima e dopo il compleanno")
    atteso(alimentazione._eta(imp["nascita"], datetime.date(2027, 2, 12)) == 20, "età il giorno prima del compleanno")

    # 80 kg, 180 cm, M, 20 anni: riposo 1830, mantenimento 2836, −400 = 2436 → 2450; proteine 80 × 1,8 = 144
    r = alimentazione._obiettivi({**imp, "peso": "80 kg", "altezza": "180", "sesso": "m"}, oggi)
    atteso("2450 kcal" in r and "144 g" in r and "2850 kcal" in r, "obiettivi M", r)
    # 55,5 kg con la virgola, F, deficit enorme: non scende sotto il metabolismo a riposo (1325 → 1350)
    r = alimentazione._obiettivi({**imp, "peso": "55,5", "altezza": "165 cm", "sesso": "F", "deficit": "1500"}, oggi)
    atteso("circa 1350 kcal" in r and "100 g" in r, "obiettivi F con il minimo", r)

    print(f"prova_alimentazione: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
