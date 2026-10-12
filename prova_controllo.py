"""Prova del controllo del mattino (controllo.py) con servizi finti: niente rete, controllo.json vero non toccato.

    .venv\\Scripts\\python prova_controllo.py
"""
import datetime
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import controllo


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    controllo.FILE = Path(tempfile.mkdtemp(prefix="prova_controllo_")) / "controllo.json"
    controllo.print = lambda *a, **k: None  # le righe [controllo] del log non servono qui
    rotti, chiamate = set(), []

    def servizio(nome):
        def prova():
            chiamate.append(nome)
            if nome in rotti:
                raise RuntimeError("HTTP 500")
            return "ok"
        return prova

    controllo.prove = lambda: [(n, f"le cose di {n}", servizio(n)) for n in ("Gemini", "Cronometer", "Darebee")]
    adesso = [datetime.datetime(2026, 10, 13, 7, 59)]
    controllo._adesso = lambda: adesso[0]
    orologio = [1000.0]
    controllo.time = SimpleNamespace(time=lambda: orologio[0])  # solo l'orologio di controllo.py

    # --- tutto a posto: nessun messaggio, una volta al giorno ---
    atteso(controllo.controlla() == [] and chiamate == [], "prima delle 8 niente", chiamate)
    adesso[0] = adesso[0].replace(hour=8, minute=0)
    atteso(controllo.controlla() == [] and len(chiamate) == 3, "alle 8 tutte le prove, tutto ok: silenzio", chiamate)
    adesso[0] = adesso[0].replace(hour=12)
    atteso(controllo.controlla() == [] and len(chiamate) == 3, "una volta al giorno", chiamate)

    # --- un singhiozzo: fallisce alle 8, alle 8:10 va: silenzio ---
    adesso[0], chiamate[:] = datetime.datetime(2026, 10, 14, 8, 0), []
    rotti.add("Darebee")
    atteso(controllo.controlla() == [], "il primo fallimento non avvisa subito")
    rotti.clear()
    orologio[0] += 300
    atteso(controllo.controlla() == [] and len(chiamate) == 3, "prima dei 10 minuti non si riprova", chiamate)
    orologio[0] += 301
    atteso(controllo.controlla() == [] and chiamate[3:] == ["Darebee"], "si riprova solo quello fallito, ok: silenzio",
           chiamate)

    # --- rotto davvero: avvisa con a cosa serve, poi "di nuovo a posto" il giorno dopo ---
    adesso[0] = datetime.datetime(2026, 10, 15, 9, 30)  # PC acceso dopo le 8
    rotti.add("Cronometer")
    atteso(controllo.controlla() == [], "primo giro")
    orologio[0] += 601
    messaggi = controllo.controlla()
    atteso(len(messaggi) == 1 and "❌ Cronometer, serve per le cose di Cronometer" in messaggi[0]
           and "HTTP 500" in messaggi[0] and "Darebee" not in messaggi[0], "messaggio del servizio rotto", messaggi)
    orologio[0] += 601
    atteso(controllo.controlla() == [], "lo stesso giorno non si ripete")
    adesso[0] = datetime.datetime(2026, 10, 16, 8, 0)
    rotti.clear()
    messaggi = controllo.controlla()
    atteso(messaggi == ["✅ Di nuovo a posto: Cronometer."], "il giorno dopo dice che è tornato", messaggi)
    adesso[0] = datetime.datetime(2026, 10, 17, 8, 0)
    atteso(controllo.controlla() == [], "e poi non lo dice più")

    # --- /controllo: elenco completo ---
    rotti.add("Gemini")
    r = controllo.rapporto()
    atteso("2 su 3 funzionano" in r and r.index("❌ Gemini") < r.index("✅ Cronometer"), "rapporto a richiesta", r)

    print(f"prova_controllo: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
