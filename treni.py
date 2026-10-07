"""Orari e prezzi dei treni Trenitalia (Frecce, Intercity, regionali) dall'API del sito lefrecce.it.

Italo non ha un'API pubblica equivalente: i suoi treni non compaiono, e il risultato lo dice.
"""
import datetime
import time
from urllib.parse import quote

import requests

_BASE = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website"
_INTESTAZIONI = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
_cache: dict[tuple, tuple[float, str]] = {}


def _stazione(nome: str) -> tuple[int, str]:
    r = requests.get(f"{_BASE}/locations/search", params={"name": nome, "limit": 1}, headers=_INTESTAZIONI, timeout=15)
    r.raise_for_status()
    trovate = r.json()
    if not trovate:
        raise ValueError(f"Stazione '{nome}' non trovata.")
    return trovate[0]["id"], trovate[0]["name"]


def _soluzioni(da: int, a: int, quando: datetime.datetime, quanti: int) -> list[dict]:
    corpo = {
        "departureLocationId": da,
        "arrivalLocationId": a,
        "departureTime": f"{quando:%Y-%m-%dT%H:%M}:00.000",
        "adults": 1,
        "children": 0,
        "criteria": {"frecceOnly": False, "regionalOnly": False, "noChanges": False,
                     "order": "DEPARTURE_DATE", "limit": quanti, "offset": 0},
        "advancedSearchRequest": {"bestFare": False},
    }
    r = requests.post(f"{_BASE}/ticket/solutions", json=corpo, headers=_INTESTAZIONI, timeout=20)
    r.raise_for_status()
    return [s["solution"] for s in r.json().get("solutions", [])]


def _riga(s: dict) -> str:
    # i tratti a piedi tra due stazioni compaiono come "treni" senza numero: non sono cambi
    mezzi = [t for t in s["trains"] if "walk" not in (t.get("trainCategory") or "").lower()]
    treni = " + ".join(f"{t.get('trainCategory') or t.get('acronym', '')} {t.get('name') or ''}".strip() for t in mezzi)
    cambi = len(mezzi) - 1
    giorni_dopo = (datetime.date.fromisoformat(s["arrivalTime"][:10]) - datetime.date.fromisoformat(s["departureTime"][:10])).days
    prezzo = (s.get("price") or {}).get("amount")
    if s.get("status") != "SALEABLE":
        costo = "non acquistabile"
    elif prezzo is None:
        costo = "prezzo non indicato"
    else:
        costo = f"da {prezzo:.0f} €"
    return (
        f"{s['departureTime'][11:16]} {s['origin']} → {s['arrivalTime'][11:16]}"
        + (f" (+{giorni_dopo} giorno)" if giorni_dopo else "")
        + f" {s['destination']} | "
        f"{s['duration'].replace(' ', '')} | {treni} | "
        + ("diretto" if cambi == 0 else f"{cambi} cambi{'o' if cambi == 1 else ''}")
        + f" | {costo}"
    )


def link_treni(partenza: str, arrivo: str, data: str, ora: str = "") -> str:
    """Pagina di ricerca Trenitalia con stazioni, data e ora già compilate (manca solo il clic su Cerca:
    il sito non ha un indirizzo che porti direttamente ai risultati)."""
    giorno = datetime.date.fromisoformat(data)
    ore = int(ora.split(":")[0]) if ora else (datetime.datetime.now().hour if giorno == datetime.date.today() else 6)
    # i nomi devono essere quelli esatti di Trenitalia, altrimenti i campi restano vuoti
    _, nome_da = _stazione(partenza)
    _, nome_a = _stazione(arrivo)
    return ("https://www.lefrecce.it/Channels.Website.WEB/#/white-label/?isRoundTrip=false"
            f"&departureStation={quote(nome_da)}&arrivalStation={quote(nome_a)}"
            f"&departureDate={giorno:%d-%m-%Y}&departureTime={ore}&lang=it")


def cerca_treni(partenza: str, arrivo: str, data: str, ora: str = "", arrivare_entro: str = "") -> str:
    """Orari reali e prezzi dei treni Trenitalia (Frecciarossa, Intercity, regionali) tra due stazioni.

    Args:
        partenza: città o stazione di partenza (es. 'Bologna Centrale', 'Roma', 'Fiumicino Aeroporto')
        arrivo: città o stazione di arrivo. Se il treno serve per prendere un volo, metti la STAZIONE DELL'AEROPORTO del volo, non la città: FCO = 'Fiumicino Aeroporto', BLQ = 'Bologna Aeroporto', MXP = 'Malpensa Aeroporto'
        data: giorno 'AAAA-MM-GG'
        ora: ora di partenza minima 'HH:MM'; di solito vuoto (= dalle 06:00, o adesso se è oggi)
        arrivare_entro: 'HH:MM' se l'utente deve arrivare entro un'ora precisa; per un volo, 1h30 prima del decollo. Mostra i treni che arrivano in tempo, i più comodi per primi
    """
    giorno = datetime.date.fromisoformat(data)
    adesso = datetime.datetime.now()
    if ora:
        inizio = datetime.datetime.combine(giorno, datetime.time.fromisoformat(ora))
    elif giorno == adesso.date():
        inizio = adesso
    else:
        inizio = datetime.datetime.combine(giorno, datetime.time(6, 0))
    limite = datetime.datetime.combine(giorno, datetime.time.fromisoformat(arrivare_entro)) if arrivare_entro else None
    if limite and not ora:
        # nessuno parte più di 8 ore prima per un tragitto in Italia: si cerca da lì in avanti
        inizio = max(inizio, limite - datetime.timedelta(hours=8))

    chiave = (partenza.lower().strip(), arrivo.lower().strip(), f"{inizio:%Y-%m-%d %H:%M}", arrivare_entro)
    salvato = _cache.get(chiave)
    if salvato and time.time() - salvato[0] < 600:
        return salvato[1]

    id_da, nome_da = _stazione(partenza)
    id_a, nome_a = _stazione(arrivo)
    def arrivo_di(s):
        return datetime.datetime.fromisoformat(s["arrivalTime"]).replace(tzinfo=None)

    soluzioni = _soluzioni(id_da, id_a, inizio, 10)
    if limite:
        # l'API dà una decina di soluzioni per volta: si va avanti finché i treni non arrivano oltre il limite
        for _ in range(5):
            if not soluzioni or arrivo_di(soluzioni[-1]) > limite:
                break
            dopo = datetime.datetime.fromisoformat(soluzioni[-1]["departureTime"]).replace(tzinfo=None)
            nuove = [s for s in _soluzioni(id_da, id_a, dopo + datetime.timedelta(minutes=1), 10)
                     if s["departureTime"] > soluzioni[-1]["departureTime"]]
            if not nuove:
                break
            soluzioni += nuove
        soluzioni = [s for s in soluzioni if arrivo_di(s) <= limite]
        soluzioni = soluzioni[-5:][::-1]  # i più comodi (che partono più tardi) per primi
    if not soluzioni:
        risultato = f"Nessun treno Trenitalia da {nome_da} a {nome_a} " + (
            f"che arrivi entro le {arrivare_entro} del {data}." if limite else f"dalle {inizio:%H:%M} del {data}.")
    else:
        titolo = f"Treni Trenitalia {nome_da} → {nome_a} del {data}"
        titolo += f", in arrivo entro le {arrivare_entro} (dal più tardi):" if limite else f", dalle {inizio:%H:%M}:"
        risultato = titolo + "\n" + "\n".join(_riga(s) for s in soluzioni[:8])
        risultato += "\n(Italo non è incluso: non ha un'API pubblica.)"
    _cache[chiave] = (time.time(), risultato)
    return risultato
