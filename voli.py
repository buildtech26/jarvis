"""Prezzi e orari dei voli: Google Flights via SerpApi (tutte le compagnie), Ryanair come riserva gratuita."""
import datetime
import os
import time
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

_cache: dict[tuple, tuple[float, str]] = {}
_link: dict[tuple, str] = {}  # stessa chiave di _cache -> link Google Flights della ricerca, per aprirla in Chrome


def _serpapi(da: list[str], a: list[str], data: str, ritorno: str) -> tuple[str, str]:
    parametri = {
        "engine": "google_flights",
        "departure_id": ",".join(da),
        "arrival_id": ",".join(a),
        "outbound_date": data,
        "type": "1" if ritorno else "2",  # 1 = andata e ritorno, 2 = solo andata
        "currency": "EUR",
        "hl": "it",
        "gl": "it",
        "api_key": os.environ["SERPAPI_KEY"].strip(),
    }
    if ritorno:
        parametri["return_date"] = ritorno
    risposta = requests.get("https://serpapi.com/search.json", params=parametri, timeout=60).json()
    if "error" in risposta:
        raise RuntimeError(risposta["error"])
    link = risposta.get("search_metadata", {}).get("google_flights_url", "")
    voli = risposta.get("best_flights", []) + risposta.get("other_flights", [])
    if not voli:
        return "Nessun volo trovato.", link
    voli.sort(key=lambda v: v.get("price", 10**6))

    def compagnie(v):
        return ", ".join(dict.fromkeys(t["airline"] for t in v["flights"]))

    def riga(v):
        tratte = v["flights"]
        partenza, arrivo = tratte[0]["departure_airport"], tratte[-1]["arrival_airport"]
        scali = len(tratte) - 1
        ore, minuti = divmod(v.get("total_duration", 0), 60)
        return (
            f"{v.get('price', '?')} € | {compagnie(v)} | {partenza['id']} {partenza['time'][-5:]} → "
            f"{arrivo['id']} {arrivo['time'][-5:]} | {ore}h{minuti:02d} | "
            + ("diretto" if scali == 0 else f"{scali} scal{'o' if scali == 1 else 'i'}")
        )

    primi = voli[:6]
    # le low cost riempiono spesso i primi posti: il più economico di ogni altra compagnia va comunque mostrato
    viste = {compagnie(v) for v in primi}
    altri = []
    for v in voli[6:]:
        if compagnie(v) not in viste:
            viste.add(compagnie(v))
            altri.append(v)
    nota = " (prezzo andata e ritorno; orari dell'andata)" if ritorno else ""
    testo = f"Voli {data}{nota} da Google Flights (tutte le compagnie), dal più economico:\n" + "\n".join(riga(v) for v in primi)
    if altri:
        testo += "\nAltre compagnie (il loro volo più economico):\n" + "\n".join(riga(v) for v in altri[:6])
    return testo, link


def _ryanair(da: list[str], a: list[str], data: str) -> str:
    righe = []
    for origine in da:
        for destinazione in a:
            r = requests.get(
                "https://www.ryanair.com/api/farfnd/v4/oneWayFares",
                params={
                    "departureAirportIataCode": origine, "arrivalAirportIataCode": destinazione,
                    "outboundDepartureDateFrom": data, "outboundDepartureDateTo": data,
                    "currency": "EUR", "market": "it-it",
                },
                timeout=20,
            )
            for tariffa in r.json().get("fares", []) if r.ok else []:
                andata = tariffa["outbound"]
                righe.append((andata["price"]["value"], f"{andata['price']['value']:.2f} € | Ryanair | {origine} "
                              f"{andata['departureDate'][11:16]} → {destinazione} {andata['arrivalDate'][11:16]} | diretto"))
    if not righe:
        return "Nessun volo Ryanair diretto trovato (le altre compagnie non sono disponibili senza SerpApi)."
    return f"Voli Ryanair del {data} (solo Ryanair, solo diretti):\n" + "\n".join(r for _, r in sorted(righe))


def cerca_voli(partenza: str, arrivo: str, data: str, ritorno: str = "") -> str:
    """Prezzi e orari dei voli tra due città, tutte le compagnie.

    Args:
        partenza: codici IATA degli aeroporti di partenza separati da virgola; per città con più aeroporti mettili tutti (Milano = 'MXP,LIN,BGY', Londra = 'LHR,LGW,STN,LTN')
        arrivo: codici IATA di arrivo, stesso formato
        data: giorno di partenza 'AAAA-MM-GG'
        ritorno: giorno di ritorno 'AAAA-MM-GG' se andata e ritorno, altrimenti vuoto
    """
    da, a, chiave = _chiave(partenza, arrivo, data, ritorno)
    # la stessa domanda riformulata ("e con ITA?") non deve consumare un'altra delle 250 ricerche del mese
    salvato = _cache.get(chiave)
    if salvato and time.time() - salvato[0] < 1800:
        return salvato[1]
    if os.getenv("SERPAPI_KEY", "").strip():
        try:
            risultato, _link[chiave] = _serpapi(da, a, data, ritorno)
        except Exception as e:  # ricerche del mese finite o servizio giù: si prova almeno Ryanair
            return f"Google Flights non disponibile ({e}).\n" + _ryanair(da, a, data)
    else:
        risultato = _ryanair(da, a, data)
    _cache[chiave] = (time.time(), risultato)
    return risultato


def _chiave(partenza: str, arrivo: str, data: str, ritorno: str):
    da = [c.strip().upper() for c in partenza.split(",") if c.strip()]
    a = [c.strip().upper() for c in arrivo.split(",") if c.strip()]
    datetime.date.fromisoformat(data)  # errore chiaro se il modello passa una data malformata
    if ritorno:
        datetime.date.fromisoformat(ritorno)
    return da, a, (tuple(sorted(da)), tuple(sorted(a)), data, ritorno)


def link_voli(partenza: str, arrivo: str, data: str, ritorno: str = "", sito: str = "google") -> str:
    """Indirizzo della pagina con i risultati già cercati: Google Flights (tutte le compagnie) o Ryanair."""
    da, a, chiave = _chiave(partenza, arrivo, data, ritorno)
    if sito.lower().strip() == "ryanair":
        # Ryanair accetta un solo aeroporto per lato: il primo della lista
        url = (f"https://www.ryanair.com/it/it/trip/flights/select?adults=1&teens=0&children=0&infants=0"
               f"&dateOut={data}&isReturn={'true' if ritorno else 'false'}&originIata={da[0]}&destinationIata={a[0]}")
        return url + (f"&dateIn={ritorno}" if ritorno else "")
    if chiave not in _link and os.getenv("SERPAPI_KEY", "").strip():
        cerca_voli(partenza, arrivo, data, ritorno)  # se la ricerca è già in memoria non consuma niente
    if _link.get(chiave):
        return _link[chiave]
    # senza SerpApi: Google Flights capisce anche la richiesta scritta per esteso
    q = f"Voli da {da[0]} a {a[0]} il {data}" + (f" ritorno il {ritorno}" if ritorno else " solo andata")
    return "https://www.google.com/travel/flights?hl=it&curr=EUR&q=" + quote(q)
