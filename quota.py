"""Quote Gemini: quale modello usare adesso, tenendo conto dei 429 già ricevuti.

Il piano gratuito ha un limite al minuto e uno al giorno, separati per modello. Un 429 al minuto si
risolve in pochi secondi: il modello viene messo in pausa per il tempo indicato da Google (retryDelay)
e intanto si usano gli altri. Un 429 giornaliero lo esclude fino alle 9, quando la quota riparte.
Cervello e trascrizione condividono le pause: consumano la stessa quota.
"""
import datetime
import json
import os
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

MODELLI = [m.strip() for m in os.getenv(
    "GEMINI_MODEL", "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash").split(",") if m.strip()]
ATTESA_MAX = 20  # secondi che vale la pena aspettare un modello invece di rispondere con un errore

_pausa: dict[str, float] = {}  # modello -> istante (time.time) in cui torna utilizzabile
_lock = threading.Lock()


def _riapertura_giornaliera() -> float:
    """La quota giornaliera riparte a mezzanotte del Pacifico, cioè alle 9 italiane."""
    adesso = datetime.datetime.now()
    nove = adesso.replace(hour=9, minute=0, second=0, microsecond=0)
    if nove <= adesso:
        nove += datetime.timedelta(days=1)
    return nove.timestamp()


def segna_errore(modello: str, errore: str, pause: dict[str, float] | None = None) -> str:
    """Registra un 429 e restituisce 'giorno', 'minuto' o '' (errore di altro tipo, nessuna pausa).
    pause: dove segnarlo, se il 429 viene da un'altra chiave (es. quella di company-analyst)."""
    if "429" not in errore and "RESOURCE_EXHAUSTED" not in errore:
        return ""
    if "PerDay" in errore:
        tipo, fino = "giorno", _riapertura_giornaliera()
    else:
        m = re.search(r"retryDelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", errore)
        tipo, fino = "minuto", time.time() + (float(m.group(1)) if m else 60) + 1
    with _lock:
        (_pausa if pause is None else pause)[modello] = fino
    return tipo


def prossimo(gia_provati: set[str]) -> str | None:
    """Il primo modello non ancora provato per questa domanda e non in pausa; se sono tutti in pausa
    per il limite al minuto, aspetta quello che si libera prima (fino a ATTESA_MAX secondi)."""
    candidati = [m for m in MODELLI if m not in gia_provati]
    if not candidati:
        return None
    with _lock:
        adesso = time.time()
        liberi = [m for m in candidati if _pausa.get(m, 0) <= adesso]
        if liberi:
            return liberi[0]
        modello = min(candidati, key=lambda m: _pausa[m])
        attesa = _pausa[modello] - adesso
    if attesa > ATTESA_MAX:
        return None
    time.sleep(attesa)
    return modello


# --- contatore delle richieste -----------------------------------------------------------------
# Google non permette di leggere da API quante richieste restano: le contiamo noi, su file perché
# Jarvis si riavvia. Conta solo quelle fatte da Jarvis con questa chiave.

FILE_CONSUMI = Path(__file__).with_name("consumi.json")


def _giorno_quota() -> str:
    """Il giorno di quota Gemini va da mezzanotte del Pacifico a quella dopo, cioè dalle 9 alle 9 italiane."""
    return (datetime.datetime.now() - datetime.timedelta(hours=9)).date().isoformat()


def registra(modello: str, uso: str, esito: str = "ok"):
    """uso: 'cervello' o 'voce'; esito: 'ok' oppure il tipo di errore ('429', 'altro')."""
    oggi = _giorno_quota()
    with _lock:
        try:
            dati = json.loads(FILE_CONSUMI.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dati = {}
        voce = dati.setdefault(oggi, {}).setdefault(modello, {})
        chiave = uso if esito == "ok" else f"errori {esito}"
        voce[chiave] = voce.get(chiave, 0) + 1
        for giorno in sorted(dati)[:-14]:  # bastano due settimane
            del dati[giorno]
        FILE_CONSUMI.write_text(json.dumps(dati, indent=1), encoding="utf-8")


def consumi() -> str:
    """Quante richieste Gemini ha fatto Jarvis oggi (per modello) e quante ricerche SerpApi (voli e alloggi) restano nel mese."""
    try:
        dati = json.loads(FILE_CONSUMI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dati = {}
    oggi = _giorno_quota()
    righe = [f"Richieste Gemini dalle 9 del {oggi[8:]}/{oggi[5:7]} (giorno di quota):"]
    for modello in MODELLI:
        c = dati.get(oggi, {}).get(modello, {})
        if not c:
            righe.append(f"- {modello}: 0")
            continue
        totale = sum(v for k, v in c.items() if not k.startswith("errori"))
        dettagli = ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
        righe.append(f"- {modello}: {totale} ({dettagli})")
    for modello, c in sorted(dati.get(oggi, {}).items()):
        if modello not in MODELLI:  # la riserva Mistral (riserva.py): compare solo nei giorni in cui è servita
            dettagli = ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
            righe.append(f"- {modello} (riserva Mistral): {dettagli}")
    giorni = sorted(dati)[-7:]
    if len(giorni) > 1:
        righe.append("Ultimi giorni: " + ", ".join(
            f"{g[8:]}/{g[5:7]} {sum(v for m in dati[g].values() for k, v in m.items() if not k.startswith('errori'))}"
            for g in giorni))
    pausa = stato()
    if pausa:
        righe.append("Adesso: " + ("quota giornaliera finita su tutti i modelli" if pausa == "giorno"
                                   else f"limite al minuto raggiunto, libero tra {pausa.split(':')[1]} s"))
    righe.append("(Limiti esatti per modello su aistudio.google.com/usage; la quota giornaliera riparte alle 9.)")
    chiave = os.getenv("SERPAPI_KEY", "").strip()
    if chiave:
        try:
            import requests

            a = requests.get("https://serpapi.com/account.json", params={"api_key": chiave}, timeout=15).json()
            righe.append(f"SerpApi (voli e alloggi):{a.get('this_month_usage', '?')} ricerche usate questo mese, "
                         f"{a.get('plan_searches_left', '?')} rimaste su {a.get('searches_per_month', '?')}.")
        except Exception as e:
            righe.append(f"SerpApi: non riesco a leggere il conteggio ({e}).")
    return "\n".join(righe)


def stato() -> str:
    """Descrizione leggibile delle pause in corso, per spiegare l'errore all'utente."""
    adesso = time.time()
    with _lock:
        in_pausa = {m: t for m, t in _pausa.items() if t > adesso}
    brevi = [t - adesso for t in in_pausa.values() if t - adesso <= 3600]  # le pause giornaliere durano ore
    if brevi:
        return f"minuto:{int(min(brevi)) + 1}"
    if in_pausa and all(m in in_pausa for m in MODELLI):
        return "giorno"
    return ""
