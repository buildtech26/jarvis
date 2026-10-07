"""Dove ho parcheggiato: la posizione arriva da Telegram (pulsante 📍 o graffetta), ne resta salvata una sola."""
import datetime
import json
from pathlib import Path

import requests

import mappe

FILE = Path(__file__).with_name("parcheggio.json")
_FORMATO = "%Y-%m-%d %H:%M"


def _carica() -> dict:
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text(encoding="utf-8"))


def _scrivi(dati: dict):
    FILE.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")


def _indirizzo(lat: float, lon: float) -> str:
    # Photon dà l'elemento più vicino con la sua via; Nominatim in un parco o in un piazzale
    # risponde solo col quartiere, quindi resta come riserva
    try:
        r = requests.get("https://photon.komoot.io/reverse", params={"lat": lat, "lon": lon, "limit": 1},
                         headers=mappe.INTESTAZIONI, timeout=10)
        r.raise_for_status()
        trovati = r.json().get("features", [])
        if trovati:
            p = trovati[0]["properties"]
            via = " ".join(x for x in (p.get("street"), p.get("housenumber")) if x)
            vicino = f" (vicino a {p['name']})" if via and p.get("name") else ""
            return ", ".join(x for x in (via or p.get("name"), p.get("city")) if x) + vicino
    except requests.RequestException:
        pass
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 18},
            headers=mappe.INTESTAZIONI,
            timeout=10,
        )
        r.raise_for_status()
        a = r.json().get("address", {})
        via = " ".join(x for x in (a.get("road") or a.get("suburb"), a.get("house_number")) if x)
        citta = a.get("city") or a.get("town") or a.get("village") or ""
        return ", ".join(x for x in (via, citta) if x) or f"{lat:.5f}, {lon:.5f}"
    except requests.RequestException:
        return f"{lat:.5f}, {lon:.5f}"  # senza rete si salva lo stesso: il segnaposto basta


def salva(lat: float, lon: float) -> str:
    """Chiamata dal bot quando arriva una posizione; la nota precedente si perde con la macchina vecchia."""
    dati = _carica()
    dati.update(lat=lat, lon=lon, indirizzo=_indirizzo(lat, lon),
                quando=datetime.datetime.now().strftime(_FORMATO), nota="")
    _scrivi(dati)
    return (f"🚗 Salvato: {dati['indirizzo']}, alle {dati['quando'][11:]}.\n"
            "Se vuoi aggiungi una nota (es. \"piano -2, colonna B\"). Quando torni chiedimi \"dov'è la macchina?\"")


def nota_parcheggio(nota: str) -> str:
    """Aggiunge una nota al parcheggio appena salvato (piano, colonna, numero del posto, scadenza del ticket...).

    Args:
        nota: la nota così come l'ha detta l'utente
    """
    dati = _carica()
    if "lat" not in dati:
        return "Non c'è nessun parcheggio salvato: l'utente deve prima mandare la posizione su Telegram col pulsante 📍."
    dati["nota"] = nota.strip()
    _scrivi(dati)
    return f"Nota salvata sul parcheggio in {dati['indirizzo']}: {dati['nota']}"


def dove_ho_parcheggiato() -> str:
    """Dice dove l'utente ha lasciato la macchina ('dov'è la macchina?', 'dove ho parcheggiato?').
    Su Telegram il segnaposto sulla mappa gli arriva da solo subito dopo la tua risposta: non serve dargli link."""
    dati = _carica()
    if "lat" not in dati:
        # letta anche direttamente all'utente (comando diretto in brain.py): scritta per lui, non per il modello
        return ("Non ho nessun parcheggio salvato. Quando parcheggi premi '📍 Ho parcheggiato' su Telegram "
                "(o manda la posizione dalla graffetta).")
    passato = datetime.datetime.now() - datetime.datetime.strptime(dati["quando"], _FORMATO)
    ore, minuti = divmod(int(passato.total_seconds() // 60), 60)
    da_quanto = f"{ore} h {minuti} min" if ore else f"{minuti} min"
    nota = f" Nota: {dati['nota']}." if dati.get("nota") else ""
    return f"Macchina in {dati['indirizzo']}, parcheggiata il {dati['quando']} (da {da_quanto}).{nota}"


def posizione() -> tuple[float, float] | None:
    dati = _carica()
    return (dati["lat"], dati["lon"]) if "lat" in dati else None


STRUMENTI = [dove_ho_parcheggiato, nota_parcheggio]
