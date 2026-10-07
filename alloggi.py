"""Appartamenti e stanze in affitto per brevi soggiorni: Google Vacation Rentals via SerpApi, link precompilati ad Airbnb e Booking."""
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

# Google mette "Casa intera" anche sulle stanze delle guesthouse e il filtro property_types viene ignorato:
# le stanze si riconoscono solo dal nome ("Lisbon Lodge - Double Room", "Bunk Bed in Mixed Dormitory")
_STANZA = re.compile(
    r"\b(room|rooms|camera|stanza|dormitor\w*|bunk|bed in|shared|quarto|habitaci[oó]n|chambre|zimmer|"
    r"hostel|ostello|guest ?house|guesthouse|pensione|b&b|bed and breakfast)\b", re.I)
_TIPI = ("tutti", "interi", "stanze")


def _e_stanza(p: dict) -> bool:
    return bool(_STANZA.search(p.get("name", "")))


def _riga(p: dict, notti: int) -> str:
    totale = p.get("total_rate", {}).get("extracted_lowest")
    notte = p.get("rate_per_night", {}).get("extracted_lowest")
    prezzo = f"{totale} € in tutto ({notte} €/notte)" if totale else f"{notte} €/notte (totale {notti} notti non indicato)"
    tipo = "STANZA" if _e_stanza(p) else "INTERO"
    # "Casa intera" su una stanza confonde: si tiene solo posti letto, camere e metri quadri
    info = ", ".join(i for i in (p.get("essential_info") or []) if "posti" in i.lower() or "m²" in i or "camer" in i.lower())
    voto = f"voto {p['overall_rating']:.1f}/5 ({p.get('reviews', 0)} recensioni)" if p.get("overall_rating") else "nessuna recensione"
    siti = ", ".join(dict.fromkeys(s["source"] for s in p.get("prices", []) if s.get("source"))) or "sito non indicato"
    vicino = ""
    for posto in p.get("nearby_places", [])[:1]:
        mezzo = (posto.get("transportations") or [{}])[0]
        modo = {"Walking": "a piedi", "Taxi": "in taxi", "Public transport": "coi mezzi"}.get(mezzo.get("type"), "")
        vicino = f" | {posto['name']} a {mezzo.get('duration', '?')} {modo}"
    return f"{tipo} | {prezzo} | {p.get('name', '?')} | {info} | {voto} | su {siti}{vicino}"


def _serpapi(luogo: str, check_in: str, check_out: str, ospiti: int, prezzo_max: int, tipo: str) -> str:
    parametri = {
        "engine": "google_hotels",
        "q": luogo,
        "vacation_rentals": "true",
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": ospiti,
        "currency": "EUR",
        "hl": "it",
        "gl": "it",
        "api_key": os.environ["SERPAPI_KEY"].strip(),
    }
    if prezzo_max:
        parametri["max_price"] = prezzo_max
    if tipo == "stanze":
        parametri["sort_by"] = 3  # dal più economico; negli altri casi no, perché la pagina si riempie di stanze
    trovati = []
    for _ in range(2):  # una seconda pagina solo se la prima ha pochi risultati del tipo voluto
        risposta = requests.get("https://serpapi.com/search.json", params=parametri, timeout=60).json()
        if "error" in risposta:
            if trovati or "hasn't returned any results" in risposta["error"]:
                break
            raise RuntimeError(risposta["error"])
        trovati += [p for p in risposta.get("properties", [])
                    if tipo == "tutti" or _e_stanza(p) == (tipo == "stanze")]
        prossima = risposta.get("serpapi_pagination", {}).get("next_page_token")
        if len(trovati) >= 6 or not prossima:
            break
        parametri["next_page_token"] = prossima
    nome = {"tutti": "Alloggi (appartamenti e stanze)", "interi": "Appartamenti interi", "stanze": "Stanze"}[tipo]
    if not trovati:
        return f"{nome}: niente trovato a {luogo} dal {check_in} al {check_out}."
    trovati.sort(key=lambda p: p.get("total_rate", {}).get("extracted_lowest")
                 or p.get("rate_per_night", {}).get("extracted_lowest") or 10**6)
    notti = (datetime.date.fromisoformat(check_out) - datetime.date.fromisoformat(check_in)).days
    if tipo == "tutti":  # le stanze costano sempre meno: senza dividerle gli interi non comparirebbero mai
        scelti = [p for p in trovati if not _e_stanza(p)][:4] + [p for p in trovati if _e_stanza(p)][:4]
    else:
        scelti = trovati[:8]
    return (f"{nome} a {luogo}, {check_in} → {check_out} ({notti} notti, {ospiti} ospiti), da Google "
            f"(annunci di Booking, Expedia, Vrbo e altri; Airbnb quasi mai), dal più economico, prezzi con tasse:\n"
            + "\n".join(_riga(p, notti) for p in scelti))


def _controlla(check_in: str, check_out: str, tipo: str) -> str:
    # errore chiaro se il modello passa date malformate o al contrario
    if datetime.date.fromisoformat(check_out) <= datetime.date.fromisoformat(check_in):
        raise ValueError("check_out deve essere dopo check_in")
    tipo = tipo.lower().strip()
    return tipo if tipo in _TIPI else "tutti"


def cerca_alloggi(luogo: str, check_in: str, check_out: str, ospiti: int = 2, prezzo_max: int = 0, tipo: str = "tutti") -> str:
    """Appartamenti, case e stanze in affitto disponibili in una città per certe date (non hotel), con prezzo totale e
    per notte. Ogni risultato è segnato INTERO (alloggio tutto per sé) o STANZA (camera in una casa o guesthouse, a volte
    con bagno condiviso): dillo sempre all'utente. Riporta SOLO gli alloggi e i prezzi scritti nel risultato: non
    inventare disponibilità, annunci o prezzi di Airbnb. Di' sempre se il prezzo è totale o per notte. Rispondi con
    i 2-3 più convenienti; NON aprire il browser dopo questa ricerca: mostra_alloggi solo se l'utente lo chiede.

    Args:
        luogo: città o zona, anche con il quartiere (es. 'Lisbona', 'Roma Trastevere')
        check_in: giorno di arrivo 'AAAA-MM-GG'
        check_out: giorno di partenza 'AAAA-MM-GG' (dopo check_in)
        ospiti: numero di adulti; se l'utente non lo dice usa 2 e dillo
        prezzo_max: prezzo massimo PER NOTTE in euro, 0 = nessun limite. Se l'utente dà un budget totale dividilo per le notti
        tipo: 'tutti' (predefinito), 'interi' se vuole un appartamento/casa tutto per sé, 'stanze' se vuole solo una camera
    """
    tipo = _controlla(check_in, check_out, tipo)
    chiave = (luogo.strip().lower(), check_in, check_out, int(ospiti), int(prezzo_max), tipo)
    # la stessa domanda riformulata non deve consumare altre ricerche SerpApi (le stesse dei voli)
    salvato = _cache.get(chiave)
    if salvato and time.time() - salvato[0] < 1800:
        return salvato[1]
    if not os.getenv("SERPAPI_KEY", "").strip():
        return "Ricerca alloggi non disponibile: manca SERPAPI_KEY. Posso solo aprire Airbnb o Booking con mostra_alloggi."
    try:
        risultato = _serpapi(luogo.strip(), check_in, check_out, int(ospiti), int(prezzo_max), tipo)
    except Exception as e:  # ricerche del mese finite o servizio giù
        return (f"Ricerca alloggi non disponibile ({e}). Dillo all'utente e chiedigli se vuole che apra Airbnb o "
                "Booking; non aprirli senza la sua risposta.")
    _cache[chiave] = (time.time(), risultato)
    return risultato


def link_alloggi(luogo: str, check_in: str, check_out: str, ospiti: int = 2, sito: str = "airbnb", tipo: str = "tutti") -> str:
    """Indirizzo della ricerca già compilata su Airbnb o Booking."""
    tipo = _controlla(check_in, check_out, tipo)
    if sito.lower().strip() == "booking":
        parametri = {"ss": luogo, "checkin": check_in, "checkout": check_out, "group_adults": ospiti,
                     "no_rooms": 1, "group_children": 0}
        if tipo == "interi":
            parametri["nflt"] = "ht_id=201"  # 201 = appartamenti
        return "https://www.booking.com/searchresults.it.html?" + urlencode(parametri)
    parametri = {"checkin": check_in, "checkout": check_out, "adults": ospiti}
    if tipo != "tutti":
        parametri["room_types[]"] = "Entire home/apt" if tipo == "interi" else "Private room"
    return f"https://www.airbnb.it/s/{quote(luogo)}/homes?" + urlencode(parametri)
