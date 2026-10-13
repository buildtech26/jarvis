"""Cronometer, il diario dei pasti che l'utente tiene sul telefono: Jarvis legge i totali dei giorni e i cibi.

Cronometer non ha un'API per i privati: qui si entra nel sito come fa il browser, con lo stesso giro di
cronometer-mcp (MIT, github.com/cphoskins/cronometer-mcp). Login con il token anti-CSRF della pagina, chiamata GWT-RPC
"authenticate" per l'id utente, "generateAuthorizationToken" per un token breve, poi i CSV di /export. I due codici
GWT (permutation e header) cambiano a ogni versione del sito e si rileggono da cronometer.nocache.js. Se Cronometer
cambia il giro questo modulo si rompe: gli errori dicono a che passo. Email e password stanno nel .env
(CRONOMETER_EMAIL, CRONOMETER_PASSWORD); la sessione (cookie, mai la password) in cronometer.json, fuori da git.
"""
import csv
import datetime
import io
import json
import os
import re
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

EMAIL = os.getenv("CRONOMETER_EMAIL", "").strip()
PASSWORD = os.getenv("CRONOMETER_PASSWORD", "").strip()
FILE = Path(__file__).with_name("cronometer.json")
SITO = "https://cronometer.com"
BASE_GWT = f"{SITO}/cronometer/"
# ultimi valori noti (cronometer-mcp, agosto 2026): servono solo se la lettura da nocache.js non riesce
_PERMUTATION, _HEADER = "CBC38FBB0A1527BD5E68722DD9DABD27", "76FC4464E20E53D16663AC9A96A486B3"
_SERVIZIO = "com.cronometer.shared.rpc.CronometerService"
_AUTENTICA = (f"7|0|5|{BASE_GWT}|{{header}}|{_SERVIZIO}|authenticate|java.lang.Integer/3438268394|"
              "1|2|3|4|1|5|5|{fuso}|")
_GENERA_TOKEN = (f"7|0|8|{BASE_GWT}|{{header}}|{_SERVIZIO}|generateAuthorizationToken|java.lang.String/2004016611|"
                 "I|com.cronometer.shared.user.AuthScope/2065601159|{nonce}|1|2|3|4|4|5|6|6|7|8|{utente}|3600|7|2|")
_lock = threading.Lock()
_http = requests.Session()
_http.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis"
_stato: dict = {}  # nonce, utente, permutation, header
CACHE = 60  # secondi in cui un'esportazione già fatta vale ancora (un pasto segnato adesso si vede al più 1 min dopo)
ATTESA_429 = 8  # secondi prima di riprovare un'esportazione rifiutata per troppe richieste
_cache: dict[tuple, tuple[float, list[dict]]] = {}  # (tipo, da, a) → (time.monotonic(), righe)


class _Scaduta(Exception):
    pass


def configurato() -> bool:
    return bool(EMAIL and PASSWORD)


def _cookie(nome: str) -> str | None:
    """L'ultimo cookie con quel nome: dopo un ripristino da file possono essercene due (dominio vuoto e del sito)."""
    valori = [c.value for c in _http.cookies if c.name == nome]
    return valori[-1] if valori else None


def _salva():
    FILE.write_text(json.dumps({**_stato, "cookie": {c.name: c.value for c in _http.cookies}}, indent=1),
                    encoding="utf-8")


def _ripristina() -> bool:
    if _stato.get("utente"):
        return True
    try:
        dati = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for nome, valore in dati.pop("cookie", {}).items():
        _http.cookies.set(nome, valore, domain="cronometer.com")
    _stato.update(dati)
    return bool(_stato.get("utente"))


def _codici():
    """Permutation e header GWT della versione attuale del sito."""
    _stato["permutation"], _stato["header"] = _PERMUTATION, _HEADER
    try:
        js = _http.get(f"{BASE_GWT}cronometer.nocache.js", timeout=20).text
        perm = re.search(r"='([A-F0-9]{32})'", js)
        if not perm:
            return
        _stato["permutation"] = perm.group(1)
        cache = _http.get(f"{BASE_GWT}{perm.group(1)}.cache.js", timeout=60).text
        header = re.search(r"'app','([A-F0-9]{32})'", cache)
        if header:
            _stato["header"] = header.group(1)
    except requests.RequestException as e:
        print(f"[cronometer] codici GWT non letti, uso gli ultimi noti: {e}")


def _gwt(corpo: str) -> str:
    r = _http.post(f"{BASE_GWT}app", data=corpo.encode("utf-8"), timeout=30, headers={
        "Content-Type": "text/x-gwt-rpc; charset=UTF-8", "X-GWT-Module-Base": BASE_GWT,
        "X-GWT-Permutation": _stato["permutation"]})
    r.raise_for_status()
    return r.text


def _accedi():
    _http.cookies.clear()  # con un cookie vecchio la pagina di login arriva senza il token anti-CSRF
    _stato.clear()
    _codici()
    pagina = _http.get(f"{SITO}/login/", timeout=20)
    pagina.raise_for_status()
    csrf = re.search(r'name="anticsrf"\s+value="([^"]+)"', pagina.text)
    if not csrf:
        raise RuntimeError("Cronometer ha cambiato la pagina di login (manca il token anti-CSRF).")
    r = _http.post(f"{SITO}/login", data={"anticsrf": csrf.group(1), "username": EMAIL, "password": PASSWORD},
                   timeout=20)
    r.raise_for_status()
    try:
        esito = r.json()
    except ValueError:
        raise RuntimeError(f"Cronometer: risposta del login inattesa ({r.text[:120]!r}).")
    if esito.get("error") or not (esito.get("success") or esito.get("redirect")):
        raise RuntimeError(f"Cronometer non accetta email e password del .env: {esito.get('error') or esito}")
    fuso = int(datetime.datetime.now().astimezone().utcoffset().total_seconds() // 60)
    risposta = _gwt(_AUTENTICA.format(header=_stato["header"], fuso=fuso))
    utente = re.search(r"OK\[(\d+),", risposta)
    if not utente:
        raise RuntimeError(f"Cronometer: login riuscito ma 'authenticate' non dà l'id utente ({risposta[:120]!r}).")
    _stato["utente"] = utente.group(1)
    _stato["nonce"] = _cookie("sesnonce")
    if not _stato["nonce"]:
        raise RuntimeError("Cronometer: login riuscito ma manca il cookie sesnonce.")
    _salva()
    print(f"[cronometer] accesso fatto, utente {_stato['utente']}")


def _token() -> str:
    risposta = _gwt(_GENERA_TOKEN.format(header=_stato["header"], nonce=_stato["nonce"], utente=_stato["utente"]))
    if risposta.lstrip().startswith("//EX"):  # sessione scaduta: HTTP 200 con un'eccezione GWT dentro
        raise _Scaduta(risposta[:200])
    token = re.search(r'"([^"]+)"', risposta)
    if not token:
        raise RuntimeError(f"Cronometer: token di esportazione non trovato ({risposta[:120]!r}).")
    return token.group(1)


def _esporta(tipo: str, da: datetime.date, a: datetime.date) -> list[dict]:
    """Un CSV di /export ('dailySummary' o 'servings') come righe. Le stesse righe valgono CACHE secondi: il 13/10
    quattro esportazioni in pochi secondi hanno dato 429, e una domanda sul cibo, il riepilogo delle 21:30 e lo
    scarico della dispensa leggono gli stessi giorni."""
    chiave = (tipo, da, a)
    with _lock:
        salvato = _cache.get(chiave)
        if salvato and time.monotonic() - salvato[0] < CACHE:
            return list(salvato[1])
        for tentativo in range(2):
            try:
                if not _ripristina():
                    _accedi()
                token = _token()
            except (_Scaduta, requests.HTTPError):
                _accedi()
                token = _token()
            r = _http.get(f"{SITO}/export", timeout=30, params={"nonce": token, "generate": tipo, "start": str(da),
                                                                 "end": str(a)})
            if r.status_code != 429 or tentativo:
                break
            time.sleep(ATTESA_429)  # troppe richieste: una sola attesa breve, poi l'errore
        r.raise_for_status()
        testo = r.text.lstrip("﻿")
        if testo.lstrip().startswith("<"):
            raise RuntimeError("Cronometer non dà l'esportazione dei dati (pagina web al posto del CSV): l'account "
                               "potrebbe non essere Gold.")
        righe = list(csv.DictReader(io.StringIO(testo)))
        _cache[chiave] = (time.monotonic(), righe)
        return list(righe)


def _colonna(riga: dict, *inizi: str) -> float:
    """Il valore della prima colonna che comincia con uno di quei nomi ('Energy' → 'Energy (kcal)')."""
    for inizio in inizi:
        for nome, valore in riga.items():
            if nome and nome.strip().lower().startswith(inizio.lower()):
                try:
                    return float(valore or 0)
                except ValueError:
                    return 0.0
    return 0.0


def _data(riga: dict) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat((riga.get("Date") or riga.get("Day") or "")[:10])
    except ValueError:
        return None


def _macro(riga: dict) -> dict:
    return {"kcal": _colonna(riga, "Energy"), "proteine": _colonna(riga, "Protein"),
            "carboidrati": _colonna(riga, "Carbs (", "Carbohydrate"), "grassi": _colonna(riga, "Fat (")}


def giorni(da: datetime.date, a: datetime.date) -> dict[datetime.date, dict]:
    """I totali di ogni giorno fra da e a (compresi) che ha qualcosa segnato: kcal, proteine, carboidrati, grassi
    e 'completo' (il giorno segnato come finito in Cronometer)."""
    risultato = {}
    for riga in _esporta("dailySummary", da, a):
        giorno = _data(riga)
        if giorno:
            risultato[giorno] = {**_macro(riga), "completo": (riga.get("Completed") or "").lower() == "true"}
    return risultato


def cibi(giorno: datetime.date) -> list[dict]:
    """I cibi segnati quel giorno, nell'ordine del diario: nome, quantità, gruppo (Breakfast, Lunch...) e macro."""
    return [{"nome": r.get("Food Name", "").strip(), "quantita": r.get("Amount", "").strip(),
             "gruppo": r.get("Group", "").strip(), **_macro(r)}
            for r in _esporta("servings", giorno, giorno) if _data(r) == giorno]
