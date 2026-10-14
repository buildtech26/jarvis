"""Prova di backup.py senza OneDrive, senza GitHub e senza toccare i file veri: cartelle temporanee.

    .venv\\Scripts\\python prova_backup.py
"""
import datetime
import os
import sys
import tempfile
from pathlib import Path

import pyzipper

import backup


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    temp = Path(tempfile.mkdtemp(prefix="prova_backup_"))
    jarvis, vault = temp / "jarvis", temp / "obsidian"
    for percorso, testo in [(jarvis / ".env", "GEMINI_API_KEY=finta"), (jarvis / "dispensa.json", "{}"),
                            (jarvis / "verifica.jsonl", "{}\n"), (jarvis / "brain.py", "codice"),
                            (jarvis / "file" / "lista-spesa.txt", "uova"),
                            (vault / "Jarvis" / "Memoria.md", "ricordi"), (vault / "Jarvis" / "Notion" / "Appunti.md", "x"),
                            (vault / "raw" / "nota.md", "nota"), (vault / ".trash" / "vecchia.md", "x")]:
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(testo, encoding="utf-8")
    backup.CARTELLA, backup.OBSIDIAN = jarvis, vault
    backup.DESTINAZIONE, backup.FILE = temp / "OneDrive" / "Backup" / "Jarvis", jarvis / "backup.json"
    push = []
    backup.carica_su_github = lambda: push.append(1) or ""
    adesso = [datetime.datetime(2026, 10, 14, 21, 50)]
    backup._adesso = lambda: adesso[0]
    os.environ["BACKUP_PASSWORD"] = "prova segreta"

    # --- zip cifrato: cosa c'è dentro ---
    zip_, quanti = backup.crea_zip("prova segreta")
    with pyzipper.AESZipFile(zip_) as z:
        nomi = set(z.namelist())
        z.setpassword(b"prova segreta")
        env = z.read("jarvis/.env").decode()
    atteso({"jarvis/.env", "jarvis/dispensa.json", "jarvis/verifica.jsonl", "jarvis/file/lista-spesa.txt",
            "obsidian/Jarvis/Memoria.md", "obsidian/raw/nota.md"} <= nomi, "file salvati", nomi)
    atteso("jarvis/brain.py" not in nomi and not any("Notion" in n or ".trash" in n for n in nomi),
           "niente codice (è su GitHub), copia di Notion e cestino", nomi)
    atteso(env == "GEMINI_API_KEY=finta", "il .env si legge con la password", env)
    with pyzipper.AESZipFile(zip_) as z:
        z.setpassword(b"sbagliata")
        try:
            z.read("jarvis/.env")
            atteso(False, "con la password sbagliata non si apre")
        except RuntimeError:
            pass
    atteso(not list(backup.DESTINAZIONE.glob("*.parziale")), "nessuno zip a metà")

    # --- solo gli ultimi TENUTI ---
    for giorno in range(1, 20):
        (backup.DESTINAZIONE / f"jarvis_2026-09-{giorno:02d}.zip").write_bytes(b"x")
    backup.crea_zip("prova segreta")
    rimasti = sorted(p.name for p in backup.DESTINAZIONE.glob("jarvis_*.zip"))
    atteso(len(rimasti) == backup.TENUTI and rimasti[-1] == "jarvis_2026-10-14.zip", "rotazione", rimasti)

    # --- quando parte ---
    backup.FILE.write_text('{"ultimo": "2026-10-13"}', encoding="utf-8")
    adesso[0] = datetime.datetime(2026, 10, 14, 12, 0)
    atteso(backup.controlla() == [] and not push, "ieri fatto, prima delle 21:45: niente")
    adesso[0] = datetime.datetime(2026, 10, 14, 21, 46)
    atteso(backup.controlla() == [] and len(push) == 1 and backup._carica()["ultimo"] == "2026-10-14",
           "dopo le 21:45: backup e push", backup._carica())
    atteso(backup.controlla() == [] and len(push) == 1, "una volta sola al giorno")
    adesso[0] = datetime.datetime(2026, 10, 16, 9, 0)
    atteso(backup.controlla() == [] and len(push) == 2, "un giorno saltato (PC spento): subito al mattino")
    # 14/10: il recupero del mattino vale per ieri, quello della sera parte lo stesso
    adesso[0] = datetime.datetime(2026, 10, 16, 21, 50)
    atteso(backup.controlla() == [] and len(push) == 3 and backup._carica()["ultimo"] == "2026-10-16",
           "dopo il recupero, la sera di nuovo", backup._carica())

    # --- problemi: un messaggio al giorno, nuovo tentativo l'ora dopo ---
    del os.environ["BACKUP_PASSWORD"]
    backup.carica_su_github = lambda: "fatal: unable to access github.com"
    tentativi = []
    originale = backup.esegui
    backup.esegui = lambda: tentativi.append(1) or originale()
    adesso[0] = datetime.datetime(2026, 10, 17, 21, 50)
    r = backup.controlla()
    atteso(len(r) == 1 and "BACKUP_PASSWORD" in r[0] and "GitHub" in r[0], "avviso dei problemi", r)
    atteso(backup.controlla() == [] and len(tentativi) == 1, "nella stessa ora non riprova", tentativi)
    adesso[0] = datetime.datetime(2026, 10, 17, 22, 50)
    r = backup.controlla()
    atteso(r == [] and len(tentativi) == 2 and backup._carica()["ultimo"] == "2026-10-16",
           "riprova l'ora dopo, senza un secondo avviso", (r, tentativi, backup._carica()))

    print(f"prova_backup: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
