"""Google Calendar tramite la web app Apps Script in calendario.gs."""
import datetime
import os
import re
from concurrent.futures import ThreadPoolExecutor
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


def _sovrapposti(inizio: str, fine: str) -> list[dict]:
    """Impegni con orario che si accavallano con [inizio, fine). Quelli di tutto il giorno (compleanni, festività)
    non contano, e nemmeno quelli che finiscono quando l'altro comincia. Il controllo non deve mai impedire di creare
    o spostare: se fallisce, non trova niente."""
    try:
        a, b = _locale(_iso(inizio)), _locale(_iso(fine))
        eventi = _chiama("eventi", da=_iso(inizio), a=_iso(fine), cerca="")["eventi"]
    except Exception as e:
        print(f"[calendario] controllo sovrapposizioni non riuscito: {e}")
        return []
    return [ev for ev in eventi if not ev["tutto_il_giorno"] and _locale(ev["inizio"]) < b and _locale(ev["fine"]) > a]


def _con_controllo(azione: str, inizio: str, fine: str, **dati) -> str:
    """crea/sposta e controllo delle sovrapposizioni in parallelo: Apps Script impiega ~3,5 s a richiesta. Il
    controllo può vedere o no l'evento appena creato o spostato, quindi lo si toglie per id."""
    with ThreadPoolExecutor(max_workers=1) as esecutore:
        controllo = esecutore.submit(_sovrapposti, inizio, fine)
        ev = _chiama(azione, inizio=_iso(inizio), fine=_iso(fine), **dati)
    chiave = "creato" if azione == "crea" else "spostato"
    altri = [a for a in controllo.result() if a["id"] != ev[chiave]["id"]]
    avviso = "\nATTENZIONE, si sovrappone a:\n" + "\n".join(_riga(a) for a in altri) if altri else ""
    return f"{chiave.capitalize()}: {_riga(ev[chiave])}{avviso}"


def crea_evento(titolo: str, inizio: str, fine: str = "", luogo: str = "", tutto_il_giorno: bool = False) -> str:
    """Aggiunge un evento al calendario principale. Se il risultato contiene "ATTENZIONE, si sovrappone a",
    l'evento è creato lo stesso: di' all'utente con cosa si accavalla (titolo e orario) e chiedi se vuole spostarlo.

    Args:
        titolo: nome dell'evento
        inizio: 'AAAA-MM-GG HH:MM' (per eventi di tutto il giorno basta la data con 00:00)
        fine: 'AAAA-MM-GG HH:MM'; se vuoto dura un'ora
        luogo: indirizzo o posto, facoltativo
        tutto_il_giorno: True per eventi senza orario
    """
    if not fine:
        fine = f"{datetime.datetime.strptime(inizio.strip()[:16].replace('T', ' '), '%Y-%m-%d %H:%M') + datetime.timedelta(hours=1):%Y-%m-%d %H:%M}"
    if not tutto_il_giorno and _locale(_iso(fine)) - _locale(_iso(inizio)) > datetime.timedelta(hours=24):
        # il 12/10 "ogni martedì e giovedì fino a Natale" era diventato un unico evento dal 13/10 al 22/12
        return ("Non creato: un evento con orario non può durare più di un giorno. Se l'utente vuole un impegno che "
                "si ripete (ogni martedì, tutti i giorni...), usa crea_eventi_ripetuti.")
    if tutto_il_giorno:
        ev = _chiama("crea", titolo=titolo, inizio=_iso(inizio), fine=_iso(fine), luogo=luogo, tutto_il_giorno=True)
        return "Creato: " + _riga(ev["creato"])
    return _con_controllo("crea", inizio, fine, titolo=titolo, luogo=luogo, tutto_il_giorno=False)


def _giorni_settimana(testo: str) -> list[int]:
    """'martedì, giovedì' / 'mar e gio' / 'tutti' → [1, 3]; 'mar' è martedì (marzo non c'entra qui)."""
    testo = testo.lower()
    if re.search(r"tutti|ogni giorno", testo):
        return list(range(7))
    if "feriali" in testo:
        return list(range(5))
    return sorted({_GIORNI.index(p[:3]) for p in re.findall(r"[a-zì]+", testo) if p[:3] in _GIORNI})


def crea_eventi_ripetuti(titolo: str, giorni: str, ora_inizio: str, ora_fine: str = "", dal: str = "", al: str = "",
                         luogo: str = "") -> str:
    """Impegno che si ripete ("ogni martedì e giovedì alle 20:30 fino a Natale"): crea un evento separato per ogni
    giorno, MAI un solo evento lungo settimane. Giorni con orari diversi = una chiamata per ogni orario. Riporta
    all'utente quanti eventi, i giorni e le sovrapposizioni come scritti nel risultato.

    Args:
        titolo: nome dell'evento
        giorni: giorni della settimana separati da virgole ('martedì, giovedì'), 'tutti' o 'feriali'
        ora_inizio: 'HH:MM'
        ora_fine: 'HH:MM'; vuoto = un'ora dopo
        dal: prima data 'AAAA-MM-GG'; vuoto = oggi
        al: ultima data compresa 'AAAA-MM-GG' ("fino a Natale" = il 24/12)
        luogo: facoltativo
    """
    numeri = _giorni_settimana(giorni)
    if not numeri:
        return f"Non capisco i giorni '{giorni}': scrivili per nome (es. 'martedì, giovedì')."
    primo = datetime.date.fromisoformat(dal.strip()[:10]) if dal.strip() else datetime.date.today()
    ultimo = datetime.date.fromisoformat(al.strip()[:10]) if al.strip() else primo + datetime.timedelta(days=90)
    date = [primo + datetime.timedelta(days=i) for i in range((ultimo - primo).days + 1)
            if (primo + datetime.timedelta(days=i)).weekday() in numeri]
    if not date:
        return "Fra quelle date non cade nessuno di quei giorni: nessun evento creato."
    if len(date) > 120:
        return f"Sarebbero {len(date)} eventi: troppi. Chiedi all'utente una data di fine più vicina."
    if not ora_fine:
        ora_fine = f"{datetime.datetime.strptime(ora_inizio, '%H:%M') + datetime.timedelta(hours=1):%H:%M}"
    orari = [(_iso(f"{d} {ora_inizio}"), _iso(f"{d} {ora_fine}")) for d in date]
    # Apps Script impiega ~3,5 s a richiesta e ne restituisce al massimo 50 eventi: creazione e controllo delle
    # sovrapposizioni (prima della creazione, una richiesta per data) tutti in parallelo
    with ThreadPoolExecutor(max_workers=8) as esecutore:
        controlli = list(esecutore.map(lambda o: _sovrapposti(*(_locale(x).strftime("%Y-%m-%d %H:%M") for x in o)), orari))
        creati = list(esecutore.map(lambda o: _chiama("crea", titolo=titolo, inizio=o[0], fine=o[1], luogo=luogo,
                                                      tutto_il_giorno=False)["creato"], orari))
    sovrapposti = [_riga(ev) for trovati in controlli for ev in trovati if ev["titolo"] != titolo]
    nomi = " e ".join(_GIORNI[n] for n in numeri)
    testo = (f"Creati {len(creati)} eventi '{titolo}', {nomi} {ora_inizio}-{ora_fine}, dal {date[0]:%d/%m} al "
             f"{date[-1]:%d/%m}: {', '.join(f'{d:%d/%m}' for d in date)}.")
    if sovrapposti:
        testo += "\nATTENZIONE, si sovrappongono a:\n" + "\n".join(sovrapposti)
    return testo


def sposta_evento(id: str, inizio: str, fine: str, nuovo_titolo: str = "") -> str:
    """Cambia orario (ed eventualmente titolo) di un evento esistente; l'id viene da eventi_calendario. Se il
    risultato contiene "ATTENZIONE, si sovrappone a", di' all'utente con cosa si accavalla."""
    return _con_controllo("sposta", inizio, fine, id=id, titolo=nuovo_titolo)


def cancella_evento(id: str, confermato: bool = False) -> str:
    """Cancella un evento. Va chiamata con confermato=True SOLO dopo che l'utente ha detto esplicitamente di sì."""
    if not confermato:
        return "Prima di cancellare chiedi conferma all'utente indicando titolo e data dell'evento."
    return "Cancellato: " + _chiama("cancella", id=id)["cancellato"]


STRUMENTI = [eventi_calendario, crea_evento, crea_eventi_ripetuti, sposta_evento, cancella_evento]
