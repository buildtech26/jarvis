"""Avvio di Jarvis.

    uv run main.py            finestra (Alt+J) + voce + Telegram; la finestra parte nascosta
    uv run main.py mostra     come sopra ma con la finestra aperta; se Jarvis gira già, la apre e basta
    uv run main.py voce       solo voce
    uv run main.py telegram   solo Telegram
    uv run main.py testo      chat da terminale, utile per provare
"""
import os
import site
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

CARTELLA = Path(__file__).parent
if sys.prefix == sys.base_prefix:
    # avvio automatico e icona usano il pythonw.exe di uv, non quello del venv: il lanciatore del venv avvia
    # python.exe, che apre una console (una scheda del Terminale, e chiuderla uccide Jarvis). Le librerie
    # del venv si aggiungono qui
    site.addsitedir(str(CARTELLA / ".venv" / "Lib" / "site-packages"))

from dotenv import load_dotenv  # noqa: E402
os.chdir(CARTELLA)
load_dotenv(CARTELLA / ".env")

class _LogCensurato:
    """Toglie dal log i segreti del .env: gli errori di requests riportano l'URL intero, e quello di Telegram
    contiene il token del bot (/bot<token>/getUpdates), quello di SerpApi la chiave (?api_key=...)."""

    def __init__(self, file):
        self._file = file
        self._segreti = sorted({v for k, v in os.environ.items()
                                if any(p in k.upper() for p in ("TOKEN", "KEY", "PASSWORD", "SECRET"))
                                and len(v) >= 8}, key=len, reverse=True)

    def write(self, testo):
        for s in self._segreti:
            testo = testo.replace(s, "***")
        return self._file.write(testo)

    def __getattr__(self, nome):  # fileno (per faulthandler), flush, ...
        return getattr(self._file, nome)


if "--log" in sys.argv or sys.stdout is None:
    # avvio automatico (attività "Jarvis"): nessuna console visibile, si scrive su jarvis.log
    sys.argv.remove("--log") if "--log" in sys.argv else None
    sys.stdout = sys.stderr = _LogCensurato(open(CARTELLA / "jarvis.log", "a", encoding="utf-8", buffering=1))

import atexit  # noqa: E402
import faulthandler  # noqa: E402

# il 2026-10-03 Jarvis si è chiuso senza lasciare nulla nel log: un crash dentro Tk o nell'audio uccide il processo
# senza traceback Python. faulthandler scrive comunque lo stack; atexit distingue una chiusura normale da un crash
faulthandler.enable(sys.stderr)
atexit.register(lambda: print(f"--- chiusura normale {time.strftime('%Y-%m-%d %H:%M:%S')}"))

import logging  # noqa: E402

# avvisi dell'SDK di Gemini che non dicono niente di utile: "Direct use of automatic function calling" compare a ogni
# avvio e dopo ogni trascrizione, "non-text parts" quando una risposta ha anche chiamate a strumenti (brain._testo le
# gestisce). Gli altri avvisi dell'SDK restano
for _nome in ("google_genai.models", "google_genai.types"):  # un filtro vale solo per il logger esatto
    logging.getLogger(_nome).addFilter(
        lambda r: not r.getMessage().startswith(("Direct use of automatic function calling",
                                                 "Warning: there are non-text parts")))

from brain import Jarvis  # noqa: E402  (dopo load_dotenv)

PORTA = 47821
_su_richiesta = []  # funzioni da chiamare quando un secondo avvio chiede di mostrare la finestra
# l'avvio dura circa un minuto: un clic sull'icona in quel tempo arriva prima che la finestra esista e andava perso
_richiesta_in_anticipo = threading.Event()


def unica_istanza(modo: str):
    """Una sola copia: due bot si rubano i messaggi (errore 409 di Telegram).

    Il secondo avvio (es. l'icona sul desktop) non parte: chiede alla copia attiva di mostrare la finestra.
    """
    server = socket.socket()
    try:
        server.bind(("127.0.0.1", PORTA))
    except OSError:
        if modo in ("tutto", "mostra"):
            with socket.create_connection(("127.0.0.1", PORTA), timeout=5) as c:
                c.sendall(b"mostra")
        print("Jarvis è già in esecuzione.")
        sys.exit(0)
    server.listen()

    def ascolta():
        while True:
            conn, _ = server.accept()
            with conn:
                if conn.recv(16) == b"mostra":
                    _richiesta_in_anticipo.set()
                    for f in _su_richiesta:
                        f()

    threading.Thread(target=ascolta, daemon=True).start()


def avvia_telegram(jarvis, in_background: bool) -> bool:
    token, utente = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_UTENTE")
    if not token or not utente:
        print("Telegram non configurato: imposta TELEGRAM_TOKEN e TELEGRAM_UTENTE nel file .env")
        return False
    from telegram_bot import BotTelegram

    bot = BotTelegram(jarvis, token, int(utente))
    if in_background:
        threading.Thread(target=bot.avvia, daemon=True).start()
    else:
        bot.avvia()
    return True


def avvia_voce(jarvis):
    def ciclo():
        # il 12/10, al risveglio dallo standby, il microfono ha dato "There is no driver installed" (PortAudio) e la
        # voce è rimasta spenta fino al riavvio: ora riprova, rileggendo i dispositivi audio, dopo 1, 2, 5, 10 min
        attese = [60, 120, 300, 600]
        errori = 0
        while True:
            try:
                import voice

                if errori:
                    import sounddevice as sd

                    sd._terminate()  # senza, PortAudio tiene l'elenco dei dispositivi di prima dello standby
                    sd._initialize()
                voice.ciclo_vocale(jarvis)
                return
            except Exception:
                # niente microfono o errore audio: la voce si ferma, il resto deve restare vivo
                if errori == 0:
                    traceback.print_exc()
                attesa = attese[min(errori, len(attese) - 1)]
                errori += 1
                if errori <= len(attese):  # poi riprova ogni 10 minuti senza riempire il log
                    print(f"Voce disattivata (tentativo {errori}), riprovo tra {attesa // 60} min.")
                time.sleep(attesa)

    threading.Thread(target=ciclo, daemon=True).start()


def main():
    modo = sys.argv[1] if len(sys.argv) > 1 else "tutto"
    if modo != "testo":
        unica_istanza(modo)
    print(f"--- avvio {time.strftime('%Y-%m-%d %H:%M:%S')} modo={modo}")
    jarvis = Jarvis()
    import diario

    # registrato qui e non in brain.py: le prove fatte importando brain non devono finire nel diario
    jarvis.ascoltatori.append(diario.annota)
    import notion

    if modo != "testo" and notion.configurato():
        threading.Thread(target=notion.ciclo_indice, daemon=True).start()
    try:
        if modo == "testo":
            while True:
                domanda = input("Tu: ").strip()
                if domanda:
                    print("Jarvis:", jarvis.chiedi(domanda))
        elif modo == "telegram":
            avvia_telegram(jarvis, in_background=False)
        elif modo == "voce":
            import voice

            voice.ciclo_vocale(jarvis)
        else:
            avvia_telegram(jarvis, in_background=True)
            avvia_voce(jarvis)
            from gui import Finestra

            finestra = Finestra(jarvis)
            _su_richiesta.append(lambda: finestra.coda.put(("mostra", None)))
            finestra.avvia(visibile=modo == "mostra" or _richiesta_in_anticipo.is_set())
    except (KeyboardInterrupt, EOFError):
        print("\nA presto, signore.")


if __name__ == "__main__":
    main()
