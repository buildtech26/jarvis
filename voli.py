"""Prezzi e orari dei voli: Google Flights via SerpApi (tutte le compagnie), Ryanair come riserva gratuita."""
import datetime
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode

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
    data, ritorno = chiave[2], chiave[3]
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


_link_esplora = ""  # pagina Google "Esplora" dell'ultima esplora_voli
_esplora: dict = {}  # criteri e fonti dell'ultima esplora_voli: per aprire la pagina che mostra gli stessi risultati
_stati_esplora: dict[tuple, dict] = {}
EUROPA = "/m/02j9z"  # kgmid di Google per l'area di arrivo


def _ryanair_esplora(da: str, inizio: datetime.date, fine: datetime.date, notti: tuple[int, int]) -> list[dict]:
    """Andata e ritorno Ryanair verso tutte le destinazioni: gratis e con le notti esatte."""
    r = requests.get("https://www.ryanair.com/api/farfnd/v4/roundTripFares", params={
        "departureAirportIataCode": da, "outboundDepartureDateFrom": inizio.isoformat(),
        "outboundDepartureDateTo": fine.isoformat(), "inboundDepartureDateFrom": inizio.isoformat(),
        "inboundDepartureDateTo": (fine + datetime.timedelta(days=notti[1])).isoformat(),
        "durationFrom": notti[0], "durationTo": notti[1], "currency": "EUR", "market": "it-it", "adultPaxCount": 1,
    }, timeout=20)
    r.raise_for_status()
    mete = []
    for f in r.json().get("fares", []):
        arrivo = f["outbound"]["arrivalAirport"]
        mete.append({"citta": arrivo["city"]["name"], "paese": arrivo["countryName"], "aeroporto": arrivo["iataCode"],
                     "prezzo": f["summary"]["price"]["value"], "andata": f["outbound"]["departureDate"][:16].replace("T", " "),
                     "ritorno": f["inbound"]["departureDate"][:16].replace("T", " "), "compagnia": "Ryanair",
                     "scali": 0, "fonte": "Ryanair"})
    return mete


def _date_google(inizio: datetime.date, fine: datetime.date, notti: int) -> tuple[datetime.date, datetime.date]:
    """Le date esatte da chiedere a Google in una finestra stretta: il primo giovedì o venerdì (le partenze più
    comuni per un viaggio breve), altrimenti il primo giorno."""
    giorno = next((inizio + datetime.timedelta(days=i) for i in range((fine - inizio).days + 1)
                   if (inizio + datetime.timedelta(days=i)).weekday() in (3, 4)), inizio)
    return giorno, giorno + datetime.timedelta(days=notti)


def _google_esplora(da: str, inizio: datetime.date, fine: datetime.date, notti: tuple[int, int],
                    europa: bool) -> tuple[list[dict], str]:
    """Google Flights 'Esplora' via SerpApi, tutte le compagnie. In una finestra stretta (fino a 12 giorni) chiede date
    esatte con le notti richieste; su un periodo lungo chiede il mese, ma allora la durata è solo weekend (2 notti),
    1 o 2 settimane. Restituisce le mete e una descrizione di cosa ha cercato."""
    global _link_esplora
    parametri = {"engine": "google_travel_explore", "departure_id": da, "currency": "EUR", "hl": "it", "gl": "it",
                 "api_key": os.environ["SERPAPI_KEY"].strip()}
    if (fine - inizio).days <= 12:
        andata, ritorno = _date_google(inizio, fine, notti[0])
        parametri |= {"outbound_date": andata.isoformat(), "return_date": ritorno.isoformat()}
        descrizione = f"date fisse {andata:%d/%m} → {ritorno:%d/%m}"
    else:
        durata = "1" if notti[1] <= 4 else "2" if notti[1] <= 9 else "3"
        parametri |= {"month": str(inizio.month), "travel_duration": durata}
        descrizione = "durata " + {"1": "weekend (2 notti)", "2": "1 settimana", "3": "2 settimane"}[durata]
    if europa:
        parametri["arrival_area_id"] = EUROPA
    risposta = requests.get("https://serpapi.com/search.json", params=parametri, timeout=60).json()
    if "error" in risposta:
        raise RuntimeError(risposta["error"])
    _link_esplora = risposta.get("search_metadata", {}).get("google_travel_explore_url", "") or _link_esplora
    mete = []
    for d in risposta.get("destinations", []):
        if not d.get("flight_price") or not inizio.isoformat() <= d.get("start_date", "") <= fine.isoformat():
            continue
        mete.append({"citta": d.get("name", "?"), "paese": d.get("country", ""), "aeroporto": d.get("destination_airport", {}).get("code", ""),
                     "prezzo": d["flight_price"], "andata": d.get("start_date", ""), "ritorno": d.get("end_date", ""),
                     "compagnia": d.get("airline", ""), "scali": d.get("number_of_stops", 0), "fonte": "Google"})
        # hotel_price non è il prezzo a notte (Parigi: 142 contro hotel da 54 €/notte sulla pagina): non si usa
    return mete, descrizione


def _notti(x: dict) -> int | None:
    try:
        return (datetime.date.fromisoformat(x["ritorno"][:10]) - datetime.date.fromisoformat(x["andata"][:10])).days
    except ValueError:
        return None


def esplora_voli(dal: str, al: str = "", notti_min: int = 2, notti_max: int = 4, partenza: str = "BLQ",
                 estero: bool = False, europa: bool = True) -> str:
    """Le mete più economiche quando l'utente NON ha ancora scelto la destinazione ("3 giorni da qualche parte a
    novembre", "dove posso andare con poco?", "un weekend fuori dall'Italia", "mete economiche a dicembre"). Cerca
    andata e ritorno verso tutte le destinazioni, con partenza fra dal e al. Riporta 3-5 mete con prezzo, date e
    compagnia, prese SOLO dal risultato. Se l'utente cambia notti o periodo ("per tre notti", "a fine mese"),
    richiamalo con i nuovi valori. Se ne sceglie una, cerca_voli per gli orari precisi. Per VEDERE i risultati
    ("mostrami dal sito", "fammeli vedere"): mostra_voli con arrivo e data vuoti, mai cerca_nel_browser.

    Args:
        dal: primo giorno possibile di partenza 'AAAA-MM-GG' ("novembre" = 1 novembre, "metà novembre" = 10 novembre,
            "fine novembre" = 20 novembre)
        al: ultimo giorno possibile di partenza 'AAAA-MM-GG' ("novembre" = 30 novembre, "metà novembre" = 20
            novembre); vuoto = fine del mese di dal
        notti_min: notti minime fuori. Un viaggio di N giorni = N-1 notti (4 giorni = 3 notti), weekend = 2;
            "tre notti" = 3
        notti_max: notti massime: uguale a notti_min se l'utente dice un numero preciso di notti, altrimenti
            notti_min + 1
        partenza: codice IATA dell'aeroporto di partenza (uno solo); senza indicazioni BLQ
        estero: True se l'utente vuole uscire dall'Italia ("fuori Italia", "all'estero")
        europa: False solo se l'utente vuole anche mete extraeuropee
    """
    global _link_esplora, _esplora
    inizio = max(datetime.date.fromisoformat(dal), datetime.date.today() + datetime.timedelta(days=1))
    if al:
        fine = datetime.date.fromisoformat(al)
    else:
        fine = datetime.date(inizio.year + inizio.month // 12, inizio.month % 12 + 1, 1) - datetime.timedelta(days=1)
    fine = max(fine, inizio)
    notti = (max(1, int(notti_min)), max(int(notti_min), int(notti_max)))
    da = partenza.split(",")[0].strip().upper()
    periodo = f"partenza dal {inizio:%d/%m} al {fine:%d/%m}"

    chiave = ("esplora", da, inizio, fine, notti, estero, europa)
    salvato = _cache.get(chiave)
    if salvato and time.time() - salvato[0] < 1800:
        _esplora = _stati_esplora.get(chiave, _esplora)
        _link_esplora = _esplora.get("google", _link_esplora)
        return salvato[1]

    mete, avvisi, cerca_google = [], [], ""
    try:
        mete += _ryanair_esplora(da, inizio, fine, notti)
    except Exception as e:
        avvisi.append(f"Ryanair non risponde ({e})")
    _link_esplora = ""
    if os.getenv("SERPAPI_KEY", "").strip():
        try:
            trovate, cerca_google = _google_esplora(da, inizio, fine, notti, europa)
            mete += trovate
        except Exception as e:
            avvisi.append(f"Google Flights non disponibile ({e}): solo Ryanair")
    else:
        avvisi.append("senza SerpApi: solo Ryanair")
    if not _link_esplora:
        _link_esplora = "https://www.google.com/travel/explore?hl=it&curr=EUR"
    _esplora = {"da": da, "inizio": inizio, "fine": fine, "notti": notti, "google": _link_esplora,
                "fonti": {x["aeroporto"]: x["fonte"] for x in sorted(mete, key=lambda x: -x["prezzo"])}}
    _stati_esplora[chiave] = _esplora

    if estero:
        mete = [x for x in mete if x["paese"].lower() != "italia"]
    if not mete:
        return f"Nessuna meta trovata da {da}, {periodo}" + (f" ({'; '.join(avvisi)})" if avvisi else "") + "."
    # una riga per città, la più economica; prima quelle con le notti chieste
    migliori: dict[str, dict] = {}
    for x in sorted(mete, key=lambda x: x["prezzo"]):
        migliori.setdefault(x["citta"].lower(), x)
    giuste = [x for x in migliori.values() if _notti(x) is not None and notti[0] <= _notti(x) <= notti[1]]
    altre = [x for x in migliori.values() if x not in giuste]

    def riga(x):
        scali = "diretto" if not x["scali"] else f"{x['scali']} scal{'o' if x['scali'] == 1 else 'i'}"
        return (f"{x['prezzo']:.0f} € | {x['citta']} ({x['aeroporto']}, {x['paese']}) | {x['andata']} → {x['ritorno']}, "
                f"{_notti(x)} notti | {x['compagnia']}, {scali}")

    criteri = (f"da {da}, {periodo}, {notti[0]} notti" if notti[0] == notti[1] else
               f"da {da}, {periodo}, da {notti[0]} a {notti[1]} notti") + (", solo estero" if estero else "")
    righe = [f"CRITERI USATI (ripetili all'utente in breve): {criteri}.",
             "Mete andata e ritorno, dalla più economica (prezzo a persona, volo A/R, bagaglio a mano piccolo):"]
    mostrate = sorted(giuste, key=lambda x: x["prezzo"])[:10]
    righe += [riga(x) for x in mostrate] or ["(nessuna con queste notti)"]
    # Ryanair con le date flessibili vince quasi sempre: le altre compagnie vanno comunque fatte vedere
    viste = {y["citta"].lower() for y in mostrate}
    non_ryanair = [x for x in mete if x["compagnia"] != "Ryanair" and x["citta"].lower() not in viste
                   and _notti(x) is not None and notti[0] <= _notti(x) <= notti[1]]
    if non_ryanair:
        righe.append("Le più economiche con ALTRE compagnie (date fisse di Google):")
        prese = []
        for x in sorted(non_ryanair, key=lambda x: x["prezzo"]):
            if x["citta"].lower() not in {y["citta"].lower() for y in prese}:
                prese.append(x)
            if len(prese) == 3:
                break
        righe += [riga(x) for x in prese]
        mostrate += prese
    if altre and len(giuste) < 5:
        righe.append("Con un numero di notti DIVERSO da quello chiesto (da proporre solo se mancano alternative):")
        diverse = sorted(altre, key=lambda x: x["prezzo"])[:4]
        righe += [riga(x) for x in diverse]
        mostrate += diverse
    compagnie = sorted({c.strip() for x in mostrate for c in re.split(r",| e ", x["compagnia"]) if c.strip()})
    righe.append("Compagnie nelle mete qui sopra: " + ", ".join(compagnie) + ". Non citarne altre.")
    fonti = ["Ryanair (date flessibili nel periodo)"]
    if cerca_google:
        fonti.append(f"Google Flights, tutte le compagnie ({cerca_google})")
    righe.append("Fonti: " + "; ".join(fonti) + ".")
    periodo_stretto = inizio.day > 1 or fine < _fine_mese(inizio)
    righe.append("mostra_voli senza arrivo apre Ryanair \"Trova tariffe\" con le stesse tariffe" + (
        f", ma per tutto {_MESI[inizio.month - 1]}: lì compaiono anche le date fuori dal periodo chiesto" if periodo_stretto
        else "") + "; mostra_voli con arrivo e date di una meta apre la sua prenotazione con quelle date.")
    if avvisi:
        righe.append("Nota: " + "; ".join(avvisi) + ".")
    risultato = "\n".join(righe)
    _cache[chiave] = (time.time(), risultato)
    return risultato


_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
         "novembre", "dicembre"]


def _fine_mese(giorno: datetime.date) -> datetime.date:
    return datetime.date(giorno.year + giorno.month // 12, giorno.month % 12 + 1, 1) - datetime.timedelta(days=1)


def link_esplora(sito: str = "") -> str:
    """La pagina con i risultati dell'ultima esplora_voli. Di base Ryanair "Trova tariffe" (verificato il 07/10: stesse
    tariffe e date della API, ma il periodo è sempre il mese intero); con sito='google' la pagina Esplora di Google
    con le date fisse usate per le altre compagnie."""
    if sito.lower().strip().startswith("google") or not _esplora:
        return _link_esplora or "https://www.google.com/travel/explore?hl=it&curr=EUR"
    e = _esplora
    return ("https://www.ryanair.com/it/it/fare-finder?" + urlencode({
        "originIata": e["da"], "destinationIata": "ANY", "isReturn": "true", "isMacDestination": "false",
        "promoCode": "", "adults": 1, "teens": 0, "children": 0, "infants": 0,
        "dateOut": e["inizio"].replace(day=1).isoformat(), "dateIn": _fine_mese(e["inizio"]).isoformat(),
        "daysTrip": e["notti"][0] + 1, "nightsFrom": e["notti"][0], "nightsTo": e["notti"][1], "dayOfWeek": "",
        "isExactDate": "false", "outboundFromHour": "00:00", "outboundToHour": "23:59", "inboundFromHour": "00:00",
        "inboundToHour": "23:59", "priceValueTo": "", "currency": "EUR", "isFlexibleDay": "true"}))


def fonte_meta(arrivo: str) -> str:
    """'ryanair' se il prezzo della meta (codice IATA) nell'ultima esplora_voli viene da Ryanair, altrimenti 'google'."""
    codice = arrivo.split(",")[0].strip().upper()
    return "ryanair" if _esplora.get("fonti", {}).get(codice) == "Ryanair" else "google"


def _chiave(partenza: str, arrivo: str, data: str, ritorno: str):
    da = [c.strip().upper() for c in partenza.split(",") if c.strip()]
    a = [c.strip().upper() for c in arrivo.split(",") if c.strip()]
    data, ritorno = data.strip()[:10], ritorno.strip()[:10]  # dalle righe di esplora_voli arriva anche l'ora
    datetime.date.fromisoformat(data)  # errore chiaro se il modello passa una data malformata
    if ritorno:
        datetime.date.fromisoformat(ritorno)
    return da, a, (tuple(sorted(da)), tuple(sorted(a)), data, ritorno)


def link_voli(partenza: str, arrivo: str, data: str, ritorno: str = "", sito: str = "google") -> str:
    """Indirizzo della pagina con i risultati già cercati: Google Flights (tutte le compagnie) o Ryanair."""
    da, a, chiave = _chiave(partenza, arrivo, data, ritorno)
    data, ritorno = chiave[2], chiave[3]
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
