"""Prova di rete.py e della coda dei messaggi automatici del bot, senza rete e senza Telegram:

    .venv\\Scripts\\python prova_rete.py

Il caso vero: al risveglio dallo standby Jarvis riparte prima del Wi-Fi. Un promemoria scaduto veniva tolto
dall'elenco e poi non partiva; il riepilogo e la domanda "fatto?" venivano segnati come mandati senza arrivare.
"""
import sys
import time

import requests

import rete
import telegram_bot

errori = 0


def atteso(condizione, nome, dettaglio=""):
    global errori
    if not condizione:
        errori += 1
        print(f"SBAGLIATO: {nome}\n  risultato: {dettaglio!r}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    su = [False]

    # --- rete.disponibile: prova vera solo ogni VALIDO secondi ---
    prove = []
    rete._prova = lambda: prove.append(1) or su[0]
    rete._stato.update(quando=0.0, ok=True, assente_da=None)
    atteso(rete.disponibile() is False and rete.disponibile() is False and len(prove) == 1, "una prova ogni 15 s",
           prove)
    su[0] = True
    rete._stato["quando"] = time.time() - rete.VALIDO - 1
    atteso(rete.disponibile() is True and rete._stato["assente_da"] is None, "rete tornata")
    rete._stato["quando"] = time.time() + 3600  # orologio tornato indietro: la prova si rifà
    su[0] = False
    atteso(rete.disponibile() is False, "orologio indietro")

    # --- il bot: senza rete niente giri, i messaggi falliti in coda e poi consegnati in ordine ---
    bot = telegram_bot.BotTelegram(None, "finto", 1)
    mandati, giu = [], [True]

    def invia(chat_id, testo, tastiera=False):
        if giu[0]:
            raise requests.ConnectionError("Failed to resolve 'api.telegram.org'")
        mandati.append(testo)

    def invia_pulsanti(chat_id, testo, pulsanti):
        invia(chat_id, f"{testo} {pulsanti}")

    bot.invia, bot.invia_pulsanti = invia, invia_pulsanti
    rete._stato["quando"] = 0.0
    atteso(bot._rete_pronta() is False, "giro saltato senza rete")

    bot.consegna("⏰ Compilare il form")
    bot.consegna("Com'è andata la corsa?", [[("✅", "all:7:fatto")]])
    atteso(len(bot.coda) == 2 and not mandati, "in coda", bot.coda)

    su[0], giu[0] = True, False
    rete._stato["quando"] = 0.0
    atteso(bot._rete_pronta() is True and mandati == ["⏰ Compilare il form", "Com'è andata la corsa? "
                                                      "[[('✅', 'all:7:fatto')]]"] and not bot.coda,
           "coda consegnata in ordine", mandati)

    # la rete risponde alla prova ma Telegram cade a metà coda: resta quello non partito, nello stesso ordine
    mandati.clear()
    bot.coda = [("uno", None), ("due", None)]
    conta = [0]

    def invia_una(chat_id, testo, tastiera=False):
        conta[0] += 1
        if conta[0] > 1:
            raise requests.ConnectionError("giù")
        mandati.append(testo)

    bot.invia = invia_una
    rete._stato["quando"] = 0.0
    atteso(bot._rete_pronta() is False and mandati == ["uno"] and bot.coda == [("due", None)], "coda a metà",
           (mandati, bot.coda))

    print(f"prova_rete: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
