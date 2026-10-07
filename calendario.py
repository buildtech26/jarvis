"""Google Calendar tramite la web app Apps Script in calendario.gs."""
import datetime
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

_GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def configurato() -> bool:
    return bool(os.getenv("CALENDARIO_URL", "").strip() and os.getenv("CALENDARIO_TOKEN", "").strip())


def _chiama(azione: str, **dati) -> dict:
    # Apps Script risponde con un redirect: requests lo segue e legge il JSON finale
    r = requests.post(
        os.environ["CALENDARIO_URL"].strip(),
        json={"token": os.environ["CALENDARIO_TOKEN"].strip(), "azione": azione, **dati},
        timeout=30,
    )
    r.raise_for_status()
    risposta = r.json()
    if "errore" in risposta:
        raise RuntimeError(risposta["errore"])
    return risposta


def _iso(testo: str) -> str:
    """'AAAA-MM-GG HH:MM' in ora italiana → ISO con fuso orario (gestisce anche il cambio dell'ora legale)."""
    momento = datetime.datetime.strptime(testo.strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    return momento.astimezone().isoformat()


def _locale(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def _riga(ev: dict) -> str:
    inizio, fine = _locale(ev["inizio"]), _locale(ev["fine"])
    quando = f"{_GIORNI[inizio.weekday()]} {inizio:%d/%m}"
    quando += " tutto il giorno" if ev["tutto_il_giorno"] else f" {inizio:%H:%M}-{fine:%H:%M}"
    luogo = f" @ {ev['luogo']}" if ev.get("luogo") else ""
    return f"{quando} | {ev['titolo']}{luogo} | calendario {ev['calendario']} | id {ev['id']}"


def eventi_calendario(da: str, a: str, cerca: str = "") -> str:
    """Elenca gli impegni del calendario in un intervallo di tempo.

    Args:
        da: inizio intervallo 'AAAA-MM-GG HH:MM' (es. oggi alle 00:00)
        a: fine intervallo 'AAAA-MM-GG HH:MM'
        cerca: parola da cercare nel titolo o nella descrizione; vuoto = tutti
    """
    eventi = _chiama("eventi", da=_iso(da), a=_iso(a), cerca=cerca)["eventi"]
    return "\n".join(_riga(e) for e in eventi) if eventi else "Nessun impegno in quell'intervallo."


def impegni_in_breve(giorni_da_oggi: int = 0) -> str:
    """Gli impegni di oggi (0) o domani (1) in una frase, per i comandi diretti: niente id né nomi di calendario."""
    giorno = datetime.date.today() + datetime.timedelta(days=giorni_da_oggi)
    eventi = _chiama("eventi", da=_iso(f"{giorno} 00:00"), a=_iso(f"{giorno} 23:59"), cerca="")["eventi"]
    quando = "oggi" if giorni_da_oggi == 0 else "domani"
    if not eventi:
        return f"Nessun impegno {quando}."
    voci = []
    for ev in eventi:
        inizio = _locale(ev["inizio"])
        voce = ev["titolo"] if ev["tutto_il_giorno"] else f"{inizio:%H:%M} {ev['titolo']}"
        voci.append(voce + (f" ({ev['luogo']})" if ev.get("luogo") else ""))
    return f"{quando.capitalize()}: " + "; ".join(voci) + "."


def crea_evento(titolo: str, inizio: str, fine: str = "", luogo: str = "", tutto_il_giorno: bool = False) -> str:
    """Aggiunge un evento al calendario principale.

    Args:
        titolo: nome dell'evento
        inizio: 'AAAA-MM-GG HH:MM' (per eventi di tutto il giorno basta la data con 00:00)
        fine: 'AAAA-MM-GG HH:MM'; se vuoto dura un'ora
        luogo: indirizzo o posto, facoltativo
        tutto_il_giorno: True per eventi senza orario
    """
    if not fine:
        fine = f"{datetime.datetime.strptime(inizio.strip()[:16].replace('T', ' '), '%Y-%m-%d %H:%M') + datetime.timedelta(hours=1):%Y-%m-%d %H:%M}"
    ev = _chiama("crea", titolo=titolo, inizio=_iso(inizio), fine=_iso(fine), luogo=luogo, tutto_il_giorno=tutto_il_giorno)
    return "Creato: " + _riga(ev["creato"])


def sposta_evento(id: str, inizio: str, fine: str, nuovo_titolo: str = "") -> str:
    """Cambia orario (ed eventualmente titolo) di un evento esistente; l'id viene da eventi_calendario."""
    ev = _chiama("sposta", id=id, inizio=_iso(inizio), fine=_iso(fine), titolo=nuovo_titolo)
    return "Spostato: " + _riga(ev["spostato"])


def cancella_evento(id: str, confermato: bool = False) -> str:
    """Cancella un evento. Va chiamata con confermato=True SOLO dopo che l'utente ha detto esplicitamente di sì."""
    if not confermato:
        return "Prima di cancellare chiedi conferma all'utente indicando titolo e data dell'evento."
    return "Cancellato: " + _chiama("cancella", id=id)["cancellato"]


STRUMENTI = [eventi_calendario, crea_evento, sposta_evento, cancella_evento]
