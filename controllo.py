"""Controllo del mattino: ogni servizio esterno di Jarvis provato con una richiesta in sola lettura.

Alle 8 (o appena Jarvis è acceso dopo) le prove girano in parallelo; quelle fallite si riprovano dopo 10 minuti, per
non avvisare di un singhiozzo della rete, e quelle ancora rotte arrivano su Telegram con a cosa servono. Se è tutto a
posto non arriva niente (solo la riga [controllo] nel log); un servizio rotto ieri e tornato a posto si dice una volta.
/controllo su Telegram fa tutte le prove subito e mostra l'elenco completo.

Niente scrive: Gemini una richiesta per modello (contata in /consumi come "controllo"), SerpApi solo account.json,
che non consuma ricerche.
"""
import datetime
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

import alimentazione
import calendario
import cronometer
import darebee
import dispensa
import mappe
import openfood
import posta
import quota
import riserva
import ritardi
import spese
import tper
import treni
import voli

FILE = Path(__file__).with_name("controllo.json")  # il giorno dell'ultimo controllo e i servizi rotti
ORA = datetime.time(8)
RIPROVA = 600  # secondi prima di riprovare quelli falliti
SERPAPI_MINIMO = 20  # ricerche rimaste nel mese sotto cui avvisare
NUTELLA = "3017620422003"  # un codice a barre che Open Food Facts conosce di sicuro

_stato = {"rotti": None, "riprova": 0.0}  # il primo giro di oggi con dei falliti, in attesa di riprovarli


def _adesso() -> datetime.datetime:
    return datetime.datetime.now()


# --- le prove: ognuna restituisce un dettaglio breve o solleva un'eccezione ---

def _gemini(modello: str):
    def prova() -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
        config = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="low"),
                                             http_options=types.HttpOptions(timeout=30000))
        try:
            r = client.models.generate_content(model=modello, contents="Rispondi solo con la parola OK.", config=config)
        except Exception as e:
            quota.segna_errore(modello, str(e))
            quota.registra(modello, "controllo", "429" if "429" in str(e) else "altro")
            # 503/504 = troppa richiesta da Google in quel momento, non un guasto: il 13/10 3.6-flash ha dato 503
            # sia alle 8 sia 10 minuti dopo ed è arrivato un avviso su Telegram inutile (la rotazione salta il modello)
            if re.search(r"\b50[34]\b|UNAVAILABLE|DEADLINE_EXCEEDED", str(e)):
                return "sovraccarico da Google in questo momento (503/504): la rotazione dei modelli lo salta"
            raise
        quota.registra(modello, "controllo")
        if not (r.text or "").strip():
            raise RuntimeError("risposta vuota")
        return "risponde"
    return prova


def _calendario() -> str:
    oggi = _adesso().date()
    eventi = calendario._chiama("eventi", da=calendario._iso(f"{oggi} 00:00"),
                                a=calendario._iso(f"{oggi + datetime.timedelta(days=1)} 00:00"), cerca="")["eventi"]
    return f"{len(eventi)} eventi oggi"


def _notion() -> str:
    voci = dispensa._leggi_voci()  # la prova deve arrivare a Notion, non alla copia in memoria
    if not voci:
        raise RuntimeError("la dispensa risulta vuota")
    return f"{len(voci)} voci in dispensa"


def _cronometer() -> str:
    oggi = _adesso().date()
    giorni = cronometer.giorni(oggi - datetime.timedelta(days=1), oggi)
    return f"accesso ed esportazione ok ({len(giorni)} giorni con dati fra ieri e oggi)"


def _darebee() -> str:
    voci = darebee._get("workouts.json").json()
    if len(voci) < 100:
        raise RuntimeError(f"catalogo con solo {len(voci)} allenamenti")
    return f"{len(voci)} allenamenti nel catalogo"


def _fit_with_fun() -> str:
    if not alimentazione._cerca_ricette("pollo", 1):
        raise RuntimeError("nessuna ricetta per 'pollo'")
    return "ricette trovate"


def _open_food_facts() -> str:
    if openfood._cerca("yogurt greco") is None:
        raise RuntimeError("ricerca 'yogurt greco' senza risultati utili")
    if not dispensa._open_food_facts(NUTELLA):
        raise RuntimeError("il codice a barre della Nutella non si trova")
    return "ricerca e codici a barre ok"


def _lefrecce() -> str:
    da, _ = treni._stazione("Bologna Centrale")
    a, _ = treni._stazione("Milano Centrale")
    domani = datetime.datetime.combine(_adesso().date() + datetime.timedelta(days=1), datetime.time(8))
    if not treni._soluzioni(da, a, domani, 1):
        raise RuntimeError("nessun treno Bologna-Milano domani mattina")
    return "treni trovati"


def _viaggiatreno() -> str:
    # lo stesso indirizzo e le stesse intestazioni di ritardi._andamento; il 9505 è un Frecciarossa di ogni giorno
    r = requests.get(f"{ritardi._BASE}/cercaNumeroTrenoTrenoAutocomplete/9505", headers=ritardi._INTESTAZIONI,
                     timeout=15)
    r.raise_for_status()
    if "|" not in r.text:
        raise RuntimeError(f"risposta inattesa ({r.text[:80]!r})")
    return "risponde"


def _tper() -> str:
    fermate = tper._trova("Stazione Centrale")
    if not fermate:
        raise RuntimeError("fermata 'Stazione Centrale' non trovata nell'elenco locale")
    if not tper._passaggi(fermate[0][0]):
        raise RuntimeError("HelloBus risponde vuoto")
    return "risponde"


def _ryanair() -> str:
    oggi = _adesso().date()
    mete = voli._ryanair_esplora("BLQ", oggi + datetime.timedelta(days=14), oggi + datetime.timedelta(days=21), (2, 4))
    if not mete:
        raise RuntimeError("nessuna meta da Bologna fra due settimane")
    return f"{len(mete)} mete da Bologna"


def _serpapi() -> str:
    a = requests.get("https://serpapi.com/account.json", params={"api_key": os.environ["SERPAPI_KEY"].strip()},
                     timeout=15).json()
    if a.get("error"):
        raise RuntimeError(a["error"])
    rimaste = a.get("plan_searches_left")
    if rimaste is not None and rimaste < SERPAPI_MINIMO:
        raise RuntimeError(f"restano solo {rimaste} ricerche nel mese")
    return f"{rimaste} ricerche rimaste nel mese"


def _mappe() -> str:
    testo = mappe.distanza("Piazza Maggiore, Bologna", "Stazione di Bologna Centrale", "piedi")
    if " km" not in testo:
        raise RuntimeError(testo[:120])
    return "luoghi e percorsi ok"


def _gmail() -> str:
    imap = posta._connetti()
    try:
        esito, _ = imap.select("INBOX", readonly=True)
        if esito != "OK":
            raise RuntimeError(f"INBOX non si apre ({esito})")
    finally:
        imap.logout()
    return "accesso ok"


def _spese_live() -> str:
    spese._leggi(vista="riepilogo")["mese"]
    return "risponde"


def _meteo() -> str:
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=10,
                     params={"latitude": mappe.BOLOGNA["lat"], "longitude": mappe.BOLOGNA["lon"],
                             "current": "temperature_2m"})
    r.raise_for_status()
    r.json()["current"]["temperature_2m"]
    return "risponde"


def _ricerca_web() -> str:
    from ddgs import DDGS

    if not DDGS().text("meteo Bologna", region="it-it", max_results=1):
        raise RuntimeError("nessun risultato")
    return "risponde"


def _pagina_web() -> str:
    """L'indirizzo pubblico della chat (Tailscale Funnel). Il 14/10, chiusa l'app di Tailscale, il servizio restava
    in "NoState" e Funnel non rispondeva più, mentre la pagina su 127.0.0.1 andava: da qui non si vedeva."""
    r = requests.get(os.environ["WEB_INDIRIZZO"].strip(), timeout=15)
    r.raise_for_status()
    if "<title>Jarvis</title>" not in r.text:  # il nome non è più scritto nella pagina, solo nel titolo
        raise RuntimeError("risponde, ma non è la pagina di Jarvis")
    return "risponde"


def prove() -> list[tuple[str, str, object]]:
    """(nome, a cosa serve, funzione) dei servizi configurati in questo .env."""
    elenco = [(f"Gemini {m}", "il cervello" +(" (modello principale)" if i == 0 else " (riserva)"),
               _gemini(m)) for i, m in enumerate(quota.MODELLI)]
    if riserva.configurata():
        elenco.append((f"Riserva Mistral {riserva.MODELLO}", "rispondere quando Gemini non risponde", riserva.prova))
    elenco += [
        ("Ricerca web (DuckDuckGo)", "cerca_web, notizie e le ricerche salvate", _ricerca_web),
        ("Meteo (Open-Meteo)", "il meteo", _meteo),
        ("Mappe (Photon e OSRM)", "distanze e tempi di viaggio", _mappe),
        ("Treni (lefrecce)", "orari e prezzi dei treni", _lefrecce),
        ("Ritardi (ViaggiaTreno)", "i treni seguiti e il loro ritardo", _viaggiatreno),
        ("Bus (TPER HelloBus)", "i bus in arrivo", _tper),
        ("Ryanair", "i voli Ryanair e le mete economiche", _ryanair),
        ("Darebee", "gli allenamenti a corpo libero e la corsa", _darebee),
        ("Fit with Fun", "le ricette e i consigli per la spesa", _fit_with_fun),
        ("Open Food Facts", "kcal delle ricette e codici a barre", _open_food_facts),
    ]
    if calendario.configurato():
        elenco.append(("Calendario (Apps Script)", "impegni, allenamenti in calendario e promemoria", _calendario))
    if dispensa.configurata():
        elenco.append(("Notion", "dispensa, attività e appunti", _notion))
    if cronometer.configurato():
        elenco.append(("Cronometer", "riepilogo delle 21:30, cosa ho mangiato e lo scarico della dispensa",
                       _cronometer))
    if os.getenv("SERPAPI_KEY", "").strip():
        elenco.append(("SerpApi", "voli di tutte le compagnie e alloggi", _serpapi))
    if posta.configurata():
        elenco.append(("Gmail", "leggere e preparare le mail", _gmail))
    if spese.configurato():
        elenco.append(("Spese Live", "quanto ho speso e il saldo", _spese_live))
    if os.getenv("WEB_INDIRIZZO", "").strip():
        elenco.append(("Pagina web (Tailscale Funnel)", "aprire Jarvis dal browser da qualsiasi PC", _pagina_web))
    return elenco


def esegui(solo: set[str] | None = None) -> list[dict]:
    """Le prove (tutte, o solo quelle con quei nomi) in parallelo: nome, serve, ok, dettaglio, secondi."""
    scelte = [p for p in prove() if solo is None or p[0] in solo]

    def una(prova) -> dict:
        nome, serve, funzione = prova
        inizio = time.time()
        try:
            dettaglio, ok = funzione(), True
        except Exception as e:
            dettaglio, ok = f"{type(e).__name__}: {e}"[:200], False
        return {"nome": nome, "serve": serve, "ok": ok, "dettaglio": dettaglio,
                "secondi": round(time.time() - inizio, 1)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(una, scelte))


def _log(esiti: list[dict], giro: str):
    rotti = [e for e in esiti if not e["ok"]]
    print(f"[controllo] {giro}: {len(esiti) - len(rotti)}/{len(esiti)} ok"
          + "".join(f" · ROTTO {e['nome']} ({e['dettaglio']})" for e in rotti))


def rapporto() -> str:
    """/controllo: tutte le prove adesso, con l'esito di ognuna."""
    esiti = esegui()
    _log(esiti, "a richiesta")
    rotti = [e for e in esiti if not e["ok"]]
    righe = [f"🔧 Controllo dei servizi: {len(esiti) - len(rotti)} su {len(esiti)} funzionano."]
    righe += [f"❌ {e['nome']}: {e['dettaglio']}\n    serve per {e['serve']}" for e in rotti]
    righe += [f"✅ {e['nome']}: {e['dettaglio']} ({e['secondi']:g} s)" for e in esiti if e["ok"]]
    return "\n".join(righe)


def _carica() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def controlla() -> list[str]:
    """Chiamata ogni minuto dal bot: il controllo del mattino, una volta al giorno."""
    adesso = _adesso()
    if adesso.time() < ORA or time.time() < _stato["riprova"]:
        return []
    oggi, salvato = str(adesso.date()), _carica()
    if salvato.get("giorno") == oggi:
        return []
    if _stato["rotti"] is None:
        esiti = esegui()
        _log(esiti, "mattino")
        rotti = [e["nome"] for e in esiti if not e["ok"]]
        if rotti:  # un singhiozzo della rete non merita un messaggio: si riprova fra 10 minuti
            _stato["rotti"], _stato["riprova"] = rotti, time.time() + RIPROVA
            return []
        ancora = []
    else:
        esiti = esegui(set(_stato["rotti"]))
        _log(esiti, "riprova")
        ancora = [e for e in esiti if not e["ok"]]
    _stato["rotti"], _stato["riprova"] = None, 0.0
    prima = set(salvato.get("rotti", []))
    tornati = sorted(prima - {e["nome"] for e in ancora})
    FILE.write_text(json.dumps({"giorno": oggi, "rotti": [e["nome"] for e in ancora]}, ensure_ascii=False),
                    encoding="utf-8")
    righe = []
    if ancora:
        righe.append("🔧 Controllo del mattino: questi servizi non rispondono (riprovato dopo 10 minuti).")
        righe += [f"❌ {e['nome']}, serve per {e['serve']}.\n    {e['dettaglio']}" for e in ancora]
    if tornati:
        righe.append(f"✅ Di nuovo a posto: {', '.join(tornati)}.")
    return ["\n".join(righe)] if righe else []
