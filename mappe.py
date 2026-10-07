"""Distanze e percorsi: calcolo con OpenStreetMap (gratis, senza chiave) e apertura di Google Maps."""
from urllib.parse import urlencode

import requests

# Nominatim chiede un User-Agent che identifichi l'app e al massimo una richiesta al secondo
INTESTAZIONI = {"User-Agent": "Jarvis-assistente-personale/1.0", "Accept-Language": "it"}

# il server dimostrativo di OSRM conosce solo l'auto: quelli di openstreetmap.de hanno anche bici e piedi
PROFILI = {
    "auto": "routed-car",
    "piedi": "routed-foot",
    "bici": "routed-bike",
}
SINONIMI = {"macchina": "auto", "a piedi": "piedi", "camminando": "piedi", "bicicletta": "bici"}
MODI_MAPS = {"auto": "driving", "piedi": "walking", "bici": "bicycling", "mezzi": "transit"}


def _mezzo(mezzo: str) -> str:
    mezzo = mezzo.lower().strip()
    return SINONIMI.get(mezzo, mezzo)


BOLOGNA = {"lat": 44.4949, "lon": 11.3426}


def _coordinate(luogo: str) -> tuple[float, float, str] | None:
    # Photon capisce i nomi a parole ("Stazione Bologna Centrale"), Nominatim solo gli indirizzi precisi.
    # La preferenza per Bologna è leggera: "Duomo di Milano" resta a Milano, "Piazza Maggiore" finisce a Bologna
    try:
        r = requests.get("https://photon.komoot.io/api/", params={"q": luogo, "limit": 1, **BOLOGNA},
                         headers=INTESTAZIONI, timeout=10)
        r.raise_for_status()
        trovati = r.json().get("features", [])
        if trovati:
            p = trovati[0]["properties"]
            via = " ".join(x for x in (p.get("street"), p.get("housenumber")) if x)
            nome = ", ".join(dict.fromkeys(x for x in (p.get("name"), via, p.get("city")) if x))
            lon, lat = trovati[0]["geometry"]["coordinates"]
            return lon, lat, nome or luogo
    except requests.RequestException:
        pass
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": luogo, "format": "json", "limit": 1},
        headers=INTESTAZIONI,
        timeout=10,
    )
    r.raise_for_status()
    trovati = r.json()
    if not trovati:
        return None
    t = trovati[0]
    # il nome completo è lunghissimo (fino al CAP e alla nazione): bastano le prime tre parti
    nome = ", ".join(t["display_name"].split(", ")[:3])
    return float(t["lon"]), float(t["lat"]), nome


def _durata(secondi: float) -> str:
    minuti = round(secondi / 60)
    if minuti < 60:
        return f"{minuti} min"
    return f"{minuti // 60} h {minuti % 60:02d} min"


def distanza(partenza: str, arrivo: str, mezzo: str = "auto") -> str:
    """Calcola km e tempo di viaggio tra due luoghi, per RISPONDERE all'utente senza aprire niente.
    Usalo per 'quanto dista', 'quanto ci metto', 'quanti km'. Non inventare mai distanze o tempi: chiamalo.

    Args:
        partenza: indirizzo o luogo di partenza, sempre con la città (es. 'viale del Risorgimento 2, Bologna'). Se l'utente dice 'casa' usa l'indirizzo che ricordi
        arrivo: indirizzo o luogo di arrivo, sempre con la città
        mezzo: 'auto', 'piedi' o 'bici'. Per bus e treni NON va bene: usa mostra_percorso con mezzo 'mezzi'
    """
    mezzo = _mezzo(mezzo)
    if mezzo not in PROFILI:
        return "Per i mezzi pubblici non so calcolare i tempi: usa mostra_percorso con mezzo 'mezzi'."
    da, a = _coordinate(partenza), _coordinate(arrivo)
    if da is None:
        return f"Non trovo il luogo '{partenza}'. Chiedi all'utente un indirizzo più preciso, con la città."
    if a is None:
        return f"Non trovo il luogo '{arrivo}'. Chiedi all'utente un indirizzo più preciso, con la città."
    url = (f"https://routing.openstreetmap.de/{PROFILI[mezzo]}/route/v1/driving/"
           f"{da[0]},{da[1]};{a[0]},{a[1]}")
    r = requests.get(url, params={"overview": "false"}, headers=INTESTAZIONI, timeout=15)
    r.raise_for_status()
    dati = r.json()
    if dati.get("code") != "Ok" or not dati.get("routes"):
        return f"Non trovo un percorso {mezzo} da {da[2]} a {a[2]}."
    percorso = dati["routes"][0]
    km = percorso["distance"] / 1000
    traffico = " senza contare il traffico" if mezzo == "auto" else ""
    return (f"Da {da[2]} a {a[2]} ({mezzo}): {km:.1f} km, circa {_durata(percorso['duration'])}{traffico}. "
            "Se uno dei due luoghi trovati non c'entra con quello che ha detto l'utente, diglielo invece di dare i numeri.")


def link_percorso(partenza: str, arrivo: str, mezzo: str = "auto") -> str:
    parametri = {"api": 1, "destination": arrivo, "travelmode": MODI_MAPS.get(_mezzo(mezzo), "driving")}
    if partenza:
        parametri["origin"] = partenza  # senza partenza Google Maps usa la posizione attuale
    return "https://www.google.com/maps/dir/?" + urlencode(parametri)
