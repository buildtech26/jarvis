"""Prova di web.py con un Jarvis finto, su una porta libera di 127.0.0.1: login, blocco dopo le password sbagliate,
sessioni, chat e messaggi degli altri canali. Niente Gemini, niente Telegram (gli avvisi finiscono in una lista).

    .venv\\Scripts\\python prova_web.py
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

import web


class JarvisFinto:
    def __init__(self):
        self.ascoltatori = []

    def chiedi(self, testo, log=print, canale=""):
        log("  [strumento] eventi_calendario({})")
        risposta = f"Risposta a: {testo}"
        for a in self.ascoltatori:
            a(canale, testo, risposta)
        return risposta


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    avvisi = []
    web._avvisa = avvisi.append
    web.print = lambda *a, **k: None  # niente righe [web] nell'uscita della prova
    password = ["una password lunga di prova"]
    web._password = lambda: password[0]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    jarvis = JarvisFinto()
    server = web.avvia(jarvis, porta)
    base = f"http://127.0.0.1:{porta}"

    def richiesta(percorso, corpo=None, token=None, tipo="application/json", grezzo=None):
        dati = grezzo if grezzo is not None else (json.dumps(corpo).encode() if corpo is not None else None)
        r = urllib.request.Request(base + percorso, data=dati, method="POST" if dati is not None else "GET")
        if dati is not None:
            r.add_header("Content-Type", tipo)
        if token:
            r.add_header("Cookie", f"{web.COOKIE}={token}")
        try:
            with urllib.request.urlopen(r, timeout=10) as risposta:
                return risposta.status, risposta.headers, risposta.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def token_da(intestazioni) -> str | None:
        cookie = intestazioni.get("Set-Cookie") or ""
        return cookie.split(";", 1)[0].split("=", 1)[1] if cookie.startswith(web.COOKIE + "=") else None

    # --- pagina e intestazioni di sicurezza ---
    stato, intestazioni, corpo = richiesta("/")
    atteso(stato == 200 and b"J.A.R.V.I.S." in corpo and "default-src 'self'" in intestazioni["Content-Security-Policy"]
           and intestazioni["X-Frame-Options"] == "DENY", "pagina e intestazioni", (stato, dict(intestazioni)))
    stato, intestazioni, corpo = richiesta("/manifest.webmanifest")
    icone = [i["src"] for i in json.loads(corpo)["icons"]] if stato == 200 else []
    atteso(stato == 200 and "manifest+json" in intestazioni["Content-Type"]
           and all(richiesta(i)[0] == 200 for i in icone + ["/icona-180.png"]), "installabile come app", (stato, icone))
    atteso(richiesta("/../.env")[0] == 404 and richiesta("/web.py")[0] == 404, "niente file fuori dalla pagina")

    # --- senza sessione non si legge e non si chiede niente ---
    atteso(richiesta("/api/messaggi")[0] == 401, "messaggi senza accesso")
    atteso(richiesta("/api/chiedi", {"testo": "ciao"})[0] == 401, "chiedi senza accesso")
    atteso(richiesta("/api/chiedi", {"testo": "ciao"}, token="inventato")[0] == 401, "token inventato")

    # --- password sbagliate: blocco dopo TENTATIVI, avviso su Telegram ---
    for i in range(web.TENTATIVI):
        stato, intestazioni, _ = richiesta("/api/accedi", {"password": f"sbagliata {i}"})
        atteso(stato == 401 and token_da(intestazioni) is None, f"password sbagliata {i}", stato)
    stato, _, corpo = richiesta("/api/accedi", {"password": password[0]})
    atteso(stato == 429 and "riprova fra" in corpo.decode(), "bloccato anche con quella giusta", (stato, corpo))
    atteso(len(avvisi) == 1 and "password sbagliate" in avvisi[0], "avviso del blocco", avvisi)
    web._bloccato_fino[0] = 0  # passati i 15 minuti

    # --- un modulo HTML di un altro sito non può accedere (non manda JSON) ---
    atteso(richiesta("/api/accedi", tipo="application/x-www-form-urlencoded",
                     grezzo=f"password={password[0]}".encode())[0] == 400, "solo JSON")
    atteso(richiesta("/api/chiedi", tipo="application/json", grezzo=b"x" * (web.MAX_CORPO + 1))[0] == 400,
           "richiesta troppo grande")

    # --- accesso giusto ---
    stato, intestazioni, _ = richiesta("/api/accedi", {"password": password[0]})
    token = token_da(intestazioni)
    cookie = intestazioni.get("Set-Cookie", "")
    atteso(stato == 200 and token and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Strict" in cookie
           and "Max-Age" not in cookie and "Expires" not in cookie, "accesso e cookie di sessione", cookie)
    atteso(len(avvisi) == 2 and "Accesso alla pagina web" in avvisi[1] and "127.0.0.1" in avvisi[1], "avviso dell'accesso",
           avvisi)

    # --- chat: la risposta, e lo scambio nei messaggi con quelli degli altri canali ---
    jarvis.chiedi("che impegni ho domani?", canale="telegram")
    stato, _, corpo = richiesta("/api/chiedi", {"testo": "che ore sono"}, token=token)
    atteso(stato == 200 and json.loads(corpo)["risposta"] == "Risposta a: che ore sono", "chiedi", corpo)
    stato, _, corpo = richiesta("/api/messaggi?dopo=0", token=token)
    m = json.loads(corpo)["messaggi"]
    atteso([(x["canale"], x["domanda"]) for x in m] == [("Telegram", "che impegni ho domani?"), ("web", "che ore sono")],
           "messaggi di tutti i canali", m)
    stato, _, corpo = richiesta(f"/api/messaggi?dopo={m[0]['id']}", token=token)
    atteso([x["domanda"] for x in json.loads(corpo)["messaggi"]] == ["che ore sono"], "solo i nuovi", corpo)
    atteso(richiesta("/api/chiedi", {"testo": "   "}, token=token)[0] == 400, "domanda vuota")

    # --- sessione scaduta, uscita ---
    web._sessioni[token] = time.time() - 1
    atteso(richiesta("/api/messaggi", token=token)[0] == 401, "sessione scaduta")
    _, intestazioni, _ = richiesta("/api/accedi", {"password": password[0]})
    token = token_da(intestazioni)
    atteso(richiesta("/api/esci", {}, token=token)[0] == 200 and richiesta("/api/messaggi", token=token)[0] == 401,
           "dopo Esci il token non vale più")

    # --- senza password nel .env l'accesso è spento ---
    password[0] = "corta"
    stato, _, corpo = richiesta("/api/accedi", {"password": "corta"})
    atteso(stato == 503 and "WEB_PASSWORD" in corpo.decode(), "password troppo corta: accesso spento", (stato, corpo))

    server.shutdown()
    print(f"prova_web: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.pop("TELEGRAM_TOKEN", None)
    sys.exit(main())
