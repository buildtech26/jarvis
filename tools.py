"""Strumenti che Jarvis può usare: info dal web e controllo del PC."""
import ctypes
import datetime
import os
import re
import subprocess
import webbrowser
from html import unescape
from pathlib import Path

import requests
from ddgs import DDGS
from dotenv import load_dotenv

import alloggi
import analista
import articoli
import calendario
import diario
import mappe
import memoria
import miei_file
import notion
import parcheggio
import posta
import promemoria
import quota
import ricerche
import ritardi
import siti
import spese
import tper
import treni
import voli

load_dotenv()  # prima di posta.configurata()


def cerca_web(query: str) -> str:
    """Cerca sul web e restituisce i primi risultati con titolo, estratto e link."""
    # pochi risultati e niente link: su CPU ogni 1000 caratteri di contesto costano secondi di attesa
    risultati = DDGS().text(query, region="it-it", max_results=4)
    if not risultati:
        return "Nessun risultato."
    return "\n\n".join(f"{r['title']}: {r['body']}" for r in risultati)[:1800]


def notizie(argomento: str = "") -> str:
    """Ultime notizie, opzionalmente su un argomento."""
    risultati = DDGS().news(argomento or "notizie Italia", region="it-it", max_results=5)
    if not risultati:
        return "Nessuna notizia trovata."
    return "\n".join(f"{r['title']} ({r.get('date', '')[:10]})" for r in risultati)


_CHROME = [
    Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def _apri_nel_browser(url: str):
    chrome = next((p for p in _CHROME if p.exists()), None)
    if chrome:
        subprocess.Popen([str(chrome), url])
    else:
        webbrowser.open(url)


def cerca_nel_browser(cosa: str, sito: str = "google") -> str:
    """Apre una ricerca in Chrome sullo schermo del PC, per far VEDERE i risultati all'utente.

    Args:
        cosa: solo cosa cercare, senza il nome del sito (es. 'PS5', non 'PS5 Amazon')
        sito: QUALUNQUE sito: google, immagini, notizie, youtube, maps, wikipedia oppure il nome o il dominio di un sito o negozio (amazon, aliexpress, ebay, subito, vinted, zalando, mediaworld, github, reddit, ...). Se l'utente nomina un sito, il sito va QUI e non in cosa: si apre direttamente la ricerca su quel sito. Per voli e treni NON usare questo strumento: usa mostra_voli o mostra_treni. Per un percorso da A a B usa mostra_percorso
    """
    if re.fullmatch(r"(https?://)?(www\.)?[\w-]+(\.[\w-]+)*\.[a-z]{2,}(/\S*)?", cosa.strip(), re.I):
        return apri_sito(cosa.strip())  # Gemini a volte passa un link da aprire come se fosse una ricerca
    if sito.lower().strip() == "google" and (diviso := siti.separa_sito(cosa)):
        cosa, sito = diviso  # Gemini a volte mette il sito dentro la ricerca
    url, come = siti.link_ricerca(cosa, sito)
    _apri_nel_browser(url)
    if come == "primo risultato":
        return f"{sito} non ha una ricerca nota: aperto in Chrome il primo risultato per '{cosa}' su quel sito."
    return f"Aperta in Chrome la ricerca '{cosa}' su {sito}."


def mostra_voli(partenza: str = "BLQ", arrivo: str = "", data: str = "", ritorno: str = "", sito: str = "google") -> str:
    """Apre in Chrome la pagina con i voli già cercati, quando l'utente vuole VEDERE i voli o aprire Google Flights/Ryanair.
    Dopo esplora_voli (destinazione non scelta) lascia arrivo e data vuoti: si apre la pagina Esplora di Google.
    Mai codici inventati per un'area (niente 'EUR').

    Args:
        partenza: codici IATA di partenza separati da virgola (Roma = 'FCO,CIA')
        arrivo: codici IATA di arrivo, stesso formato; vuoto per la pagina Esplora dell'ultima esplora_voli
        data: giorno di partenza 'AAAA-MM-GG'; vuoto insieme ad arrivo
        ritorno: giorno di ritorno 'AAAA-MM-GG' se andata e ritorno, altrimenti vuoto
        sito: 'google' (Google Flights, tutte le compagnie) oppure 'ryanair' se l'utente nomina Ryanair
    """
    if not arrivo.strip() or not data.strip():
        _apri_nel_browser(voli.link_esplora())
        return "Aperta in Chrome la pagina Esplora di Google Flights con le mete dell'ultima ricerca."
    _apri_nel_browser(voli.link_voli(partenza, arrivo, data, ritorno, sito))
    nome = "Ryanair" if sito.lower().strip() == "ryanair" else "Google Flights"
    return f"Aperti in Chrome su {nome} i voli {partenza} → {arrivo} del {data}."


def mostra_treni(partenza: str, arrivo: str, data: str, ora: str = "") -> str:
    """Apre in Chrome la ricerca Trenitalia già compilata, quando l'utente vuole VEDERE i treni o aprire il sito Trenitalia.

    Args:
        partenza: città o stazione di partenza
        arrivo: città o stazione di arrivo
        data: giorno 'AAAA-MM-GG'
        ora: ora di partenza 'HH:MM', se l'utente l'ha detta o se l'hai trovata con cerca_treni
    """
    _apri_nel_browser(treni.link_treni(partenza, arrivo, data, ora))
    return (f"Aperta in Chrome la ricerca Trenitalia {partenza} → {arrivo} del {data}, già compilata: "
            "l'utente deve solo premere Cerca (il sito non permette di saltare questo passaggio).")


def mostra_alloggi(luogo: str, check_in: str, check_out: str, ospiti: int = 2, sito: str = "airbnb", tipo: str = "tutti") -> str:
    """Apre in Chrome la ricerca già compilata su Airbnb o Booking, SOLO quando l'utente chiede esplicitamente di
    VEDERE gli alloggi o di aprire Airbnb/Booking ("aprimi Airbnb", "fammeli vedere"). Mai dopo cerca_alloggi di tua iniziativa.

    Args:
        luogo: città o zona
        check_in: giorno di arrivo 'AAAA-MM-GG'
        check_out: giorno di partenza 'AAAA-MM-GG'
        ospiti: numero di adulti (riprendilo dalla conversazione, altrimenti 2)
        sito: 'airbnb' (predefinito) oppure 'booking' se l'utente nomina Booking
        tipo: 'tutti', 'interi' (solo appartamenti/case intere) o 'stanze' (solo camere)
    """
    _apri_nel_browser(alloggi.link_alloggi(luogo, check_in, check_out, ospiti, sito, tipo))
    nome = "Booking" if sito.lower().strip() == "booking" else "Airbnb"
    return f"Aperta in Chrome su {nome} la ricerca a {luogo} dal {check_in} al {check_out} per {ospiti} ospiti."


def mostra_percorso(arrivo: str, partenza: str = "", mezzo: str = "auto") -> str:
    """Apre Google Maps in Chrome con il percorso già calcolato, quando l'utente vuole VEDERE la strada o la mappa,
    oppure per i tempi con bus e treni (quelli li sa solo Google Maps).

    Args:
        arrivo: indirizzo o luogo di arrivo, con la città
        partenza: indirizzo o luogo di partenza; vuoto = posizione attuale. Se l'utente dice 'casa' usa l'indirizzo che ricordi
        mezzo: 'auto', 'piedi', 'bici' o 'mezzi' (bus, treni, mezzi pubblici)
    """
    _apri_nel_browser(mappe.link_percorso(partenza, arrivo, mezzo))
    return f"Aperto Google Maps con il percorso {partenza or 'posizione attuale'} → {arrivo} ({mezzo})."


def leggi_pagina(url: str) -> str:
    """Scarica una pagina web e ne restituisce il testo, per leggere o riassumere un sito o un link preciso."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    r.raise_for_status()
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", r.text)
    testo = unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    testo = re.sub(r"\s{2,}", "\n", testo).strip()
    return testo[:6000] or "La pagina non contiene testo leggibile."


def meteo(citta: str) -> str:
    """Meteo attuale e previsioni di oggi e domani per una città."""
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": citta, "count": 1, "language": "it"},
        timeout=10,
    ).json()
    if not geo.get("results"):
        return f"Città '{citta}' non trovata."
    luogo = geo["results"][0]
    dati = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": luogo["latitude"],
            "longitude": luogo["longitude"],
            "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "forecast_days": 2,
            "timezone": "auto",
        },
        timeout=10,
    ).json()
    c, d = dati["current"], dati["daily"]
    return (
        f"{luogo['name']}: ora {c['temperature_2m']}°C (percepiti {c['apparent_temperature']}°C), "
        f"{_descrivi_meteo(c['weather_code'])}, vento {c['wind_speed_10m']} km/h. "
        f"Oggi min {d['temperature_2m_min'][0]}° max {d['temperature_2m_max'][0]}°, pioggia {d['precipitation_probability_max'][0]}%. "
        f"Domani min {d['temperature_2m_min'][1]}° max {d['temperature_2m_max'][1]}°, "
        f"{_descrivi_meteo(d['weather_code'][1])}, pioggia {d['precipitation_probability_max'][1]}%."
    )


def _descrivi_meteo(codice: int) -> str:
    if codice == 0:
        return "sereno"
    if codice <= 3:
        return "parzialmente nuvoloso"
    if codice <= 48:
        return "nebbia"
    if codice <= 67 or 80 <= codice <= 82:
        return "pioggia"
    if codice <= 77 or 85 <= codice <= 86:
        return "neve"
    return "temporale"


def data_ora() -> str:
    """Data e ora attuali."""
    giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    ora = datetime.datetime.now()
    return f"{giorni[ora.weekday()]} {ora:%d/%m/%Y}, ore {ora:%H:%M}"


def apri_sito(url: str) -> str:
    """Apre DIRETTAMENTE un sito o una pagina precisa in Chrome. Usalo quando l'utente dice "apri <sito>" o
    "apri la pagina ..." e conosci (o hai appena trovato con cerca_web) il link: passa qui l'URL, mai a cerca_nel_browser.

    Args:
        url: indirizzo completo (https://github.com/utente/progetto) oppure solo il dominio (github.com)
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    _apri_nel_browser(url)
    return f"Aperto {url}"


# Nomi comuni -> comando Windows. Si possono aggiungere app qui.
APP = {
    "blocco note": "notepad",
    "notepad": "notepad",
    "calcolatrice": "calc",
    "esplora risorse": "explorer",
    "explorer": "explorer",
    "paint": "mspaint",
    "impostazioni": "ms-settings:",
    "task manager": "taskmgr",
    "chrome": "chrome",
    "edge": "msedge",
    "spotify": "spotify:",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "vscode": "code",
    "visual studio code": "code",
    "terminale": "wt",
}


_MENU_START = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
               Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs"]


def app_installata(nome: str) -> bool:
    """True se nel menu Start c'è un collegamento con esattamente quel nome ('GitHub Desktop' non conta per 'github')."""
    nome = re.sub(r"\s", "", nome.lower())
    return any(re.sub(r"\s", "", lnk.stem.lower()) == nome
               for cartella in _MENU_START if cartella.exists() for lnk in cartella.rglob("*.lnk"))


def apri_app(nome: str) -> str:
    """Apre un'applicazione installata sul PC."""
    comando = APP.get(nome.lower().strip(), nome)
    try:
        os.startfile(comando)
    except OSError:
        # alcuni comandi (code, wt) sono nel PATH ma non registrati come app
        try:
            subprocess.Popen(comando, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            return f"Non riesco ad aprire '{nome}'."
    return f"Aperto {nome}"


def cerca_file(nome: str) -> str:
    """Cerca file per nome in Desktop, Documenti e Download."""
    casa = Path.home()
    trovati = []
    for cartella in ["Desktop", "Documents", "Downloads", "OneDrive/Desktop", "OneDrive/Documents"]:
        base = casa / cartella
        if not base.exists():
            continue
        for f in base.rglob(f"*{nome}*"):
            if any(p.startswith(".") or p in ("node_modules", ".venv", "venv") for p in f.parts):
                continue
            trovati.append(str(f))
            if len(trovati) >= 10:
                break
    return "\n".join(trovati) if trovati else f"Nessun file con '{nome}' nel nome."


def apri_file(percorso: str) -> str:
    """Apre un file o una cartella con il programma predefinito."""
    if not Path(percorso).exists():
        return f"Il percorso '{percorso}' non esiste."
    os.startfile(percorso)
    return f"Aperto {percorso}"


_VK = {"su": 0xAF, "giu": 0xAE, "muto": 0xAD}


def volume(azione: str, passi: int = 5) -> str:
    """Alza ('su'), abbassa ('giu') o silenzia ('muto') il volume del PC."""
    tasto = _VK.get(azione.lower().replace("ù", "u"))
    if tasto is None:
        return "Azione non valida: usa su, giu o muto."
    for _ in range(1 if tasto == 0xAD else max(1, min(passi, 25))):
        ctypes.windll.user32.keybd_event(tasto, 0, 0, 0)
        ctypes.windll.user32.keybd_event(tasto, 0, 2, 0)
    return f"Volume: {azione}"


def multimediale(azione: str) -> str:
    """Controlla la riproduzione: 'play' (play/pausa), 'avanti', 'indietro'."""
    tasti = {"play": 0xB3, "pausa": 0xB3, "avanti": 0xB0, "indietro": 0xB1}
    tasto = tasti.get(azione.lower())
    if tasto is None:
        return "Azione non valida: usa play, avanti o indietro."
    ctypes.windll.user32.keybd_event(tasto, 0, 0, 0)
    ctypes.windll.user32.keybd_event(tasto, 0, 2, 0)
    return f"Fatto: {azione}"


def blocca_pc() -> str:
    """Blocca lo schermo del PC."""
    ctypes.windll.user32.LockWorkStation()
    return "PC bloccato."


# data_ora non è tra gli strumenti: l'ora va già nel prompt di sistema
TUTTI = [
    cerca_web, cerca_nel_browser, notizie, meteo, leggi_pagina, apri_sito, apri_app, cerca_file, apri_file, volume, multimediale, blocca_pc,
    promemoria.crea_promemoria, promemoria.lista_promemoria, promemoria.cancella_promemoria,
    voli.cerca_voli, voli.esplora_voli, treni.cerca_treni, mostra_voli, mostra_treni, mappe.distanza, mostra_percorso,
    alloggi.cerca_alloggi, mostra_alloggi,
    ritardi.stato_treno, ritardi.segui_treno, ritardi.treni_seguiti, ritardi.smetti_di_seguire,
    tper.bus_in_arrivo, *parcheggio.STRUMENTI, quota.consumi, memoria.ricorda, memoria.dimentica, *articoli.STRUMENTI, *diario.STRUMENTI,
    *ricerche.STRUMENTI, *miei_file.STRUMENTI, *analista.STRUMENTI,
]
if posta.configurata():
    TUTTI += posta.STRUMENTI
if calendario.configurato():
    TUTTI += calendario.STRUMENTI
if notion.configurato():
    TUTTI += notion.STRUMENTI
if spese.configurato():
    TUTTI += spese.STRUMENTI
PER_NOME = {f.__name__: f for f in TUTTI}
