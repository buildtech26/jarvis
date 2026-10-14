"""La chat di Jarvis nel browser, da qualsiasi PC (scelta dell'utente del 14/10: anche PC non suoi, solo chat scritta).

Il server ascolta solo su 127.0.0.1:PORTA; da fuori ci si arriva con Tailscale Funnel (https://<pc>.<tailnet>.ts.net),
che fa anche l'HTTPS. Siccome l'indirizzo è pubblico, si entra solo con WEB_PASSWORD del .env (riletta a ogni
tentativo, almeno 12 caratteri): dopo TENTATIVI password sbagliate in BLOCCO secondi il login si ferma, e su Telegram
arriva un avviso per ogni accesso riuscito e per ogni blocco. La sessione dura DURATA e il cookie sparisce chiudendo il
browser, perché su un PC non suo l'utente non resti dentro. La conversazione è la stessa di finestra e Telegram:
la pagina mostra anche gli scambi degli altri canali (ascoltatore di brain.Jarvis).
Solo libreria standard: niente dipendenze nuove per un server di poche righe.
"""
import hmac
import http.cookies
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import dotenv_values

CARTELLA = Path(__file__).parent
PAGINE = CARTELLA / "web"
PORTA = int(os.getenv("JARVIS_WEB_PORTA", "47822"))
DURATA = 12 * 3600  # secondi di una sessione
TENTATIVI, BLOCCO = 5, 15 * 60  # password sbagliate ammesse in BLOCCO secondi, poi login fermo per BLOCCO secondi
MIN_PASSWORD = 12
TENUTI = 60  # scambi tenuti in memoria per la pagina
MAX_CORPO = 20_000  # byte di una richiesta
COOKIE = "jarvis_sessione"
STATICI = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
           "/stile.css": ("stile.css", "text/css"), "/icona.png": ("icona.png", "image/png"),
           # per installarla come app (telefono e Chrome/Edge)
           "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
           **{f"/icona-{n}.png": (f"icona-{n}.png", "image/png") for n in (180, 192, 512)}}
SICUREZZA = {"Content-Security-Policy": "default-src 'self'; img-src 'self'; frame-ancestors 'none'",
             "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
             "Cache-Control": "no-store"}
CANALI = {"app": "finestra", "voce": "voce", "telegram": "Telegram", "web": "web", "": "terminale"}

_lock = threading.Lock()
_sessioni: dict[str, float] = {}  # token → scadenza (time.time())
_sbagliate: list[float] = []  # quando sono arrivate le password sbagliate
_bloccato_fino = [0.0]
_scambi: list[dict] = []  # {id, ora, canale, domanda, risposta}
_prossimo = [1]
_fase = {"testo": ""}  # cosa sta facendo Jarvis per la domanda dalla pagina ("uso eventi calendario…")
_jarvis = None


def annota(canale: str, domanda: str, risposta: str):
    """Ascoltatore di brain.Jarvis: ogni scambio, da qualsiasi canale, entra nella pagina."""
    with _lock:
        _scambi.append({"id": _prossimo[0], "ora": time.strftime("%H:%M"), "canale": CANALI.get(canale, canale),
                        "domanda": domanda, "risposta": risposta})
        _prossimo[0] += 1
        del _scambi[:-TENUTI]


def _password() -> str:
    return (dotenv_values(CARTELLA / ".env").get("WEB_PASSWORD") or os.getenv("WEB_PASSWORD") or "").strip()


def _avvisa(testo: str):
    """Messaggio su Telegram, in un thread: il login non deve aspettare Telegram."""
    token, utente = os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_UTENTE", "").strip()
    if not token or not utente:
        return

    def manda():
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": utente, "text": testo},
                          timeout=20)
        except requests.RequestException as e:
            print(f"[web] avviso Telegram non partito: {type(e).__name__}")

    threading.Thread(target=manda, daemon=True).start()


def accedi(password: str, chi: str) -> tuple[int, dict, str | None]:
    """(stato HTTP, risposta, token della sessione nuova o None)."""
    adesso = time.time()
    with _lock:
        if adesso < _bloccato_fino[0]:
            minuti = int((_bloccato_fino[0] - adesso) // 60) + 1
            return 429, {"errore": f"Troppe password sbagliate: riprova fra {minuti} minuti."}, None
        vera = _password()
        if len(vera) < MIN_PASSWORD:
            return 503, {"errore": f"Accesso web spento: manca WEB_PASSWORD nel .env (almeno {MIN_PASSWORD} "
                                   "caratteri)."}, None
        if hmac.compare_digest(password.encode("utf-8"), vera.encode("utf-8")):
            token = secrets.token_urlsafe(32)
            _sessioni[token] = adesso + DURATA
            for t in [t for t, scade in _sessioni.items() if scade < adesso]:
                del _sessioni[t]
            _sbagliate.clear()
            print(f"[web] accesso riuscito ({chi})")
            _avvisa(f"🔐 Accesso alla pagina web di Jarvis alle {time.strftime('%H:%M')} ({chi}). "
                    "Se non sei stato tu, cambia WEB_PASSWORD nel .env e riavvia Jarvis.")
            return 200, {"ok": True}, token
        _sbagliate[:] = [t for t in _sbagliate if adesso - t < BLOCCO] + [adesso]
        print(f"[web] password sbagliata ({len(_sbagliate)}/{TENTATIVI}, {chi})")
        if len(_sbagliate) >= TENTATIVI:
            _bloccato_fino[0] = adesso + BLOCCO
            _sbagliate.clear()
            _avvisa(f"⚠️ Pagina web di Jarvis: {TENTATIVI} password sbagliate di fila ({chi}). Login fermo per "
                    f"{BLOCCO // 60} minuti.")
        return 401, {"errore": "Password sbagliata."}, None


def valida(token: str | None) -> bool:
    with _lock:
        scade = _sessioni.get(token or "")
        if scade is None:
            return False
        if scade < time.time():
            del _sessioni[token]
            return False
        return True


def esci(token: str | None):
    with _lock:
        _sessioni.pop(token or "", None)


def messaggi(dopo: int) -> dict:
    with _lock:
        return {"messaggi": [s for s in _scambi if s["id"] > dopo], "fase": _fase["testo"]}


def chiedi(testo: str) -> str:
    def log(riga):
        print(riga)
        if "[strumento]" in riga:
            _fase["testo"] = "uso " + riga.split("]", 1)[1].strip().split("(", 1)[0].replace("_", " ") + "…"

    _fase["testo"] = "sto pensando…"
    try:
        return _jarvis.chiedi(testo, log=log, canale="web")
    finally:
        _fase["testo"] = ""


class _Gestore(BaseHTTPRequestHandler):
    server_version = "Jarvis"
    sys_version = ""

    def log_message(self, formato, *args):  # niente righe per ogni richiesta nel log (la pagina interroga ogni 3 s)
        pass

    def _chi(self) -> str:
        """Da dove arriva la richiesta, per gli avvisi: Funnel passa l'indirizzo vero in X-Forwarded-For."""
        ip = self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
        browser = self.headers.get("User-Agent", "")
        for nome in ("Edg", "Chrome", "Firefox", "Safari"):
            if nome in browser:
                browser = {"Edg": "Edge"}.get(nome, nome)
                break
        sistema = next((s for s in ("Windows", "Android", "iPhone", "iPad", "Mac OS", "Linux") if s in
                        self.headers.get("User-Agent", "")), "")
        return ", ".join(x for x in (ip, f"{browser} {sistema}".strip()[:40]) if x)

    def _token(self) -> str | None:
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return cookie[COOKIE].value if COOKIE in cookie else None

    def _manda(self, stato: int, corpo: bytes, tipo: str, intestazioni: dict | None = None):
        self.send_response(stato)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8" if tipo.startswith("text") or tipo.endswith("json")
                         else tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for nome, valore in {**SICUREZZA, **(intestazioni or {})}.items():
            self.send_header(nome, valore)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, stato: int, dati: dict, intestazioni: dict | None = None):
        self._manda(stato, json.dumps(dati, ensure_ascii=False).encode("utf-8"), "application/json", intestazioni)

    def _corpo(self) -> dict | None:
        lunghezza = int(self.headers.get("Content-Length") or 0)
        if lunghezza > MAX_CORPO or "application/json" not in self.headers.get("Content-Type", ""):
            return None  # anche contro i moduli di altri siti: un form HTML non può mandare JSON
        try:
            dati = json.loads(self.rfile.read(lunghezza) or b"{}")
        except ValueError:
            return None
        return dati if isinstance(dati, dict) else None

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in STATICI:
            nome, tipo = STATICI[url.path]
            try:
                self._manda(200, (PAGINE / nome).read_bytes(), tipo)
            except OSError:
                self._json(404, {"errore": "non trovato"})
        elif url.path == "/api/messaggi":
            if not valida(self._token()):
                return self._json(401, {"errore": "accesso richiesto"})
            try:
                dopo = int(parse_qs(url.query).get("dopo", ["0"])[0])
            except ValueError:
                dopo = 0
            self._json(200, messaggi(dopo))
        else:
            self._json(404, {"errore": "non trovato"})

    def do_POST(self):
        dati = self._corpo()
        if dati is None:
            return self._json(400, {"errore": "richiesta non valida"})
        percorso = urlparse(self.path).path
        if percorso == "/api/accedi":
            stato, risposta, token = accedi(str(dati.get("password", "")), self._chi())
            cookie = ({"Set-Cookie": f"{COOKIE}={token}; HttpOnly; Secure; SameSite=Strict; Path=/"}
                      if token else None)
            return self._json(stato, risposta, cookie)
        if not valida(self._token()):
            return self._json(401, {"errore": "accesso richiesto"})
        if percorso == "/api/esci":
            esci(self._token())
            return self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/"})
        if percorso == "/api/chiedi":
            testo = str(dati.get("testo", "")).strip()[:4000]
            if not testo:
                return self._json(400, {"errore": "domanda vuota"})
            try:
                return self._json(200, {"risposta": chiedi(testo)})
            except Exception as e:
                print(f"[web] errore: {type(e).__name__}: {e}")
                return self._json(500, {"errore": f"Qualcosa è andato storto: {type(e).__name__}"})
        self._json(404, {"errore": "non trovato"})


def avvia(jarvis, porta: int = PORTA) -> ThreadingHTTPServer:
    """Server in un thread; jarvis deve avere chiedi() e ascoltatori."""
    global _jarvis
    _jarvis = jarvis
    jarvis.ascoltatori.append(annota)
    server = ThreadingHTTPServer(("127.0.0.1", porta), _Gestore)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[web] pagina su http://127.0.0.1:{porta}" + ("" if len(_password()) >= MIN_PASSWORD
                                                         else " (spenta: manca WEB_PASSWORD nel .env)"))
    return server
