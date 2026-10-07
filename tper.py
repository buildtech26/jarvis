"""Bus TPER (Bologna e provincia): prossimi passaggi in tempo reale da HelloBus.

HelloBus vuole il codice numerico della fermata: i nomi si cercano nell'elenco linee-fermate degli open
data TPER, scaricato una volta e aggiornato ogni settimana in fermate_tper.csv.
"""
import csv
import io
import re
import time
from pathlib import Path

import requests

FILE = Path(__file__).with_name("fermate_tper.csv")
_OPEN_DATA = "https://solweb.tper.it/web/tools/open-data/"
_HELLOBUS = "https://hellobuswsweb.tper.it/web-services/hello-bus.asmx/QueryHellobus"
_fermate: dict[str, dict] | None = None  # codice -> {nome, indirizzo, comune, linee}


def _scarica_elenco():
    pagina = requests.get(_OPEN_DATA + "open-data.aspx", timeout=30).text
    versione = re.search(r"filename=lineefermate&amp;version=(\d+)", pagina).group(1)
    r = requests.get(_OPEN_DATA + "open-data-download.aspx", timeout=60, params={
        "source": "solweb.tper.it", "filename": "lineefermate", "version": versione, "format": "csv"})
    r.raise_for_status()
    FILE.write_bytes(r.content)


def _carica() -> dict[str, dict]:
    global _fermate
    if _fermate is not None:
        return _fermate
    if not FILE.exists() or time.time() - FILE.stat().st_mtime > 7 * 86400:
        try:
            _scarica_elenco()
        except Exception as e:  # senza rete si usa l'elenco vecchio, se c'è
            if not FILE.exists():
                raise RuntimeError(f"Elenco fermate TPER non disponibile: {e}")
    grezzo = FILE.read_bytes()
    try:
        testo = grezzo.decode("utf-8-sig")
    except UnicodeDecodeError:
        testo = grezzo.decode("cp1252")
    fermate: dict[str, dict] = {}
    for riga in csv.DictReader(io.StringIO(testo), delimiter=";"):
        f = fermate.setdefault(riga["codice_fermata"], {
            "nome": riga["denominazione"].strip(), "indirizzo": riga["ubicazione"].strip(),
            "comune": riga["comune"].strip(), "linee": set()})
        f["linee"].add(riga["codice_linea"].strip())
    _fermate = fermate
    return fermate


def _combacia(parola: str, testo: str) -> bool:
    # TPER tronca i nomi a 25 caratteri ("PORTA SARAGOZZA - RISORGI"): vale anche una parola troncata
    return any(t.startswith(parola) or (len(t) >= 4 and parola.startswith(t)) for t in re.findall(r"[a-z0-9]+", testo))


def _trova(fermata: str, linea: str = "") -> list[tuple[str, dict]]:
    """Fermate il cui nome o indirizzo contiene tutte le parole cercate: prima quelle servite dalla linea
    richiesta, poi quelle di Bologna città, poi quelle in cui le parole sono nel nome."""
    fermate = _carica()
    if fermata.strip().isdigit():
        return [(fermata.strip(), fermate.get(fermata.strip(), {"nome": "?", "indirizzo": "", "comune": "", "linee": set()}))]
    parole = [p for p in re.findall(r"[a-z0-9]+", fermata.lower()) if p not in ("fermata", "via", "viale", "piazza", "di", "del", "della")]
    trovate = [(c, f) for c, f in fermate.items() if all(_combacia(p, f"{f['nome']} {f['indirizzo']}".lower()) for p in parole)]
    linea = linea.strip().upper()
    if linea and any(linea in f["linee"] for _, f in trovate):
        trovate = [(c, f) for c, f in trovate if linea in f["linee"]]
    trovate.sort(key=lambda cf: (
        cf[1]["comune"] != "BOLOGNA",
        not all(_combacia(p, cf[1]["nome"].lower()) for p in parole),
    ))
    return trovate


def _passaggi(codice: str, linea: str = "", ora: str = "") -> str:
    r = requests.get(_HELLOBUS, params={"fermata": codice, "linea": linea, "oraHHMM": ora.replace(":", "")}, timeout=20)
    r.raise_for_status()
    testo = unescape_xml(re.sub(r"(?s)<[^>]+>", "", r.text)).strip()
    testo = re.sub(r"^\(x\d\d:\d\d\)\s*", "", testo.removeprefix("TperHellobus:").strip())
    testo = re.sub(r"\s*\(Bus\d+[^)]*\)", "", testo)  # numero di vettura e pedana: rumore
    return testo.replace("DaSatellite", "(GPS)").replace("Previsto", "(da orario)")


def unescape_xml(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'").replace("&quot;", '"')


def bus_in_arrivo(fermata: str, linea: str = "", ora: str = "") -> str:
    """Prossimi bus TPER a una fermata di Bologna (o provincia): in tempo reale, o da un'ora precisa in poi.

    Args:
        fermata: nome della fermata o della via (es. 'Risorgimento', 'Stazione Centrale', 'Porta San Mamolo') oppure il codice numerico
        linea: numero della linea se l'utente ne vuole una sola (es. '14', '25'), altrimenti vuoto
        ora: 'HH:MM' per i passaggi da quell'ora in poi (es. '05:00' per "il primo bus di domattina"); vuoto = adesso. Vale per oggi o, se l'ora è già passata, per domani
    """
    trovate = _trova(fermata, linea)
    if not trovate:
        return f"Nessuna fermata TPER contiene '{fermata}'. Prova con il nome della via o della fermata."
    righe = []
    # una fermata ha spesso due codici, uno per senso di marcia: si mostrano entrambi
    for codice, f in trovate[:4]:
        try:
            passaggi = _passaggi(codice, linea, ora)
        except Exception as e:
            passaggi = f"HelloBus non risponde ({e})"
        linee = ", ".join(sorted(f["linee"], key=lambda l: (len(l), l)))
        righe.append(f"{f['nome']} ({f['indirizzo']}, cod. {codice}, linee {linee}): {passaggi}")
    altre = f"\n(Altre {len(trovate) - 4} fermate corrispondono: se non è quella giusta chiedi un nome più preciso.)" if len(trovate) > 4 else ""
    return "\n".join(righe) + altre + "\n(GPS) = posizione reale del bus, (da orario) = orario previsto."
