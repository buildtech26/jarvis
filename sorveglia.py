"""Tiene Jarvis acceso: lo lancia l'attività pianificata "Jarvis sorveglia" all'accensione e poi ogni 5 minuti, anche
senza nessuno connesso a Windows (installa_servizio.ps1).

Se Jarvis non risponde sulla porta dell'istanza unica, avvia main.py in modo "servizio" (Telegram, pagina web e
messaggi automatici, senza finestra né voce). Il 14/10 Windows Update ha riavviato il PC alle 08:13 e Jarvis è rimasto
spento fino all'accesso delle 09:54; e un Jarvis chiuso per un errore non ripartiva mai. All'accensione non avvisa;
dopo, un riavvio vuol dire che Jarvis si era fermato, e arriva un messaggio su Telegram.
Quando si entra in Windows, il Jarvis completo prende il posto del servizio (main.unica_istanza).
Solo libreria standard: deve partire anche se il venv ha un problema.
"""
import ctypes
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CARTELLA = Path(__file__).parent
PORTA = 47821  # main.PORTA
ACCENSIONE = 10 * 60  # secondi dall'accensione in cui un avvio è normale, non un guasto
LOG = CARTELLA / "jarvis.log"
NO_FINESTRA, STACCATO, FUORI_DAL_JOB = 0x08000000, 0x00000008, 0x01000000


def vivo() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORTA), timeout=3):
            return True
    except OSError:
        return False


def _pythonw() -> Path:
    """Il pythonw.exe di uv, come l'attività "Jarvis" e riavvia.ps1 (vedi l'inizio di main.py)."""
    for riga in (CARTELLA / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        if riga.split("=")[0].strip() == "home":
            return Path(riga.split("=", 1)[1].strip()) / "pythonw.exe"
    raise RuntimeError("home non trovata in .venv\\pyvenv.cfg")


def _env() -> dict:
    valori = {}
    for riga in (CARTELLA / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in riga and not riga.lstrip().startswith("#"):
            chiave, valore = riga.split("=", 1)
            valori[chiave.strip()] = valore.strip().strip('"')
    return valori


def _scrivi_log(testo: str):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[sorveglia] {time.strftime('%Y-%m-%d %H:%M:%S')} {testo}\n")


def _avvisa(testo: str):
    env = _env()
    if not env.get("TELEGRAM_TOKEN") or not env.get("TELEGRAM_UTENTE"):
        return
    corpo = json.dumps({"chat_id": env["TELEGRAM_UTENTE"], "text": testo}).encode("utf-8")
    richiesta = urllib.request.Request(f"https://api.telegram.org/bot{env['TELEGRAM_TOKEN']}/sendMessage", data=corpo,
                                       headers={"Content-Type": "application/json"})
    for _ in range(6):  # dopo un guasto la rete può non esserci ancora: riprova per un minuto
        try:
            urllib.request.urlopen(richiesta, timeout=15).close()
            return
        except OSError:
            time.sleep(10)


def secondi_da_accensione() -> float:
    return ctypes.windll.kernel32.GetTickCount64() / 1000


def avvia_servizio():
    comando = [str(_pythonw()), str(CARTELLA / "main.py"), "servizio", "--log"]
    try:
        # fuori dal job dell'attività pianificata: quando sorveglia.py finisce, Jarvis deve restare acceso
        subprocess.Popen(comando, cwd=CARTELLA, creationflags=NO_FINESTRA | STACCATO | FUORI_DAL_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(comando, cwd=CARTELLA, creationflags=NO_FINESTRA | STACCATO, close_fds=True)


def main() -> int:
    if vivo():
        return 0
    avvio = secondi_da_accensione() < ACCENSIONE
    _scrivi_log("Jarvis non risponde: avvio il servizio" + (" (accensione del PC)" if avvio else ""))
    avvia_servizio()
    for _ in range(90):  # l'avvio dura fino a un minuto
        time.sleep(2)
        if vivo():
            break
    else:
        _scrivi_log("il servizio non è partito")
        _avvisa("⚠️ Jarvis si è fermato e non riesco a farlo ripartire. Guarda jarvis.log sul PC.")
        return 1
    if not avvio:
        _avvisa(f"⚠️ Jarvis si era fermato: l'ho fatto ripartire alle {time.strftime('%H:%M')}, per ora senza finestra "
                "né voce (Telegram e pagina web funzionano). Per riaverle apri Jarvis dall'icona.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
