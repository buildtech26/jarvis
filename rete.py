"""La rete c'è? Per i giri automatici del bot (allenamento, alimentazione, controllo del mattino, promemoria).

Il PC va in standby (Modern Standby) e al risveglio Jarvis riparte prima del Wi-Fi: ogni mattina il log aveva
"Failed to resolve" per calendario, dispensa e Cronometer, il riepilogo o la domanda "fatto?" potevano essere segnati
come mandati senza arrivare, e un promemoria scaduto in quel momento si perdeva. Ora i giri aspettano la rete: senza,
saltano il minuto senza contare errori.
"""
import socket
import threading
import time

PROVA = ("api.telegram.org", 443)  # serve comunque per mandare i messaggi: se risponde lui, la rete c'è
VALIDO = 15  # secondi in cui l'ultima prova vale ancora (i giri sono quattro, al minuto)

_lock = threading.Lock()
_stato = {"quando": 0.0, "ok": True, "assente_da": None}


def _prova() -> bool:
    try:
        socket.create_connection(PROVA, timeout=3).close()
        return True
    except OSError:
        return False


def disponibile() -> bool:
    """Vero se la rete risponde. Scrive nel log quando sparisce e quando torna, una volta."""
    with _lock:
        # time.time e non monotonic: l'orologio di sistema va avanti anche durante lo standby, così una prova di
        # prima del sonno non vale al risveglio
        if 0 <= time.time() - _stato["quando"] < VALIDO:
            return _stato["ok"]
        ok = _prova()
        _stato["quando"], _stato["ok"] = time.time(), ok
        if not ok and _stato["assente_da"] is None:
            _stato["assente_da"] = time.time()
            print(f"[rete] assente dalle {time.strftime('%H:%M:%S')}: i giri automatici aspettano")
        elif ok and _stato["assente_da"] is not None:
            print(f"[rete] tornata dopo {time.time() - _stato['assente_da']:.0f} s")
            _stato["assente_da"] = None
        return ok
