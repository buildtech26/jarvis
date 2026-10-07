"""Ritardi dei treni in tempo reale (ViaggiaTreno, RFI) e treni "seguiti" con avviso su Telegram.

Un treno seguito viene controllato ogni 5 minuti da 90 minuti prima della partenza dalla stazione
dell'utente fino alla partenza effettiva; il bot manda un messaggio quando il ritardo cambia di almeno
5 minuti, quando compare il binario e se il treno viene cancellato. ViaggiaTreno conosce solo i treni
del giorno: per quelli di domani i dati arrivano la mattina stessa.
"""
import datetime
import json
import re
import threading
import time
from pathlib import Path

import requests

_BASE = "http://www.viaggiatreno.it/infomobilita/resteasy/viaggiatreno"
FILE = Path(__file__).with_name("treni_seguiti.json")
_lock = threading.Lock()
ANTICIPO = datetime.timedelta(minutes=90)  # da quanto prima della partenza si comincia a controllare
OGNI = 300  # secondi tra un controllo e l'altro dello stesso treno
SOGLIA = 5  # minuti di ritardo (o di variazione) che meritano un messaggio


def _andamento(numero: str, giorno: datetime.date, stazione: str = "") -> dict | None:
    """Dati del treno in quel giorno. Lo stesso numero può esistere con origini diverse: si sceglie
    quello che ferma nella stazione indicata."""
    r = requests.get(f"{_BASE}/cercaNumeroTrenoTrenoAutocomplete/{numero}", timeout=15)
    r.raise_for_status()
    mezzanotte = int(datetime.datetime.combine(giorno, datetime.time()).timestamp() * 1000)
    candidati = []
    for riga in r.text.strip().splitlines():
        if "|" not in riga:
            continue
        num, origine, _ = riga.split("|")[1].split("-")
        d = requests.get(f"{_BASE}/andamentoTreno/{origine}/{num}/{mezzanotte}", timeout=15)
        if d.ok and d.text.strip():
            candidati.append(d.json())
    if not candidati:
        return None
    for c in candidati:
        if stazione and _fermata(c, stazione):
            return c
    return candidati[0]


def _fermata(treno: dict, stazione: str) -> dict | None:
    parole = re.findall(r"[a-z]+", stazione.lower())
    for f in treno.get("fermate") or []:
        nome = f["stazione"].lower()
        if parole and all(p[:5] in nome for p in parole if p not in ("centrale", "c")):
            return f
    return None


def _ora(ms) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M") if ms else "?"


def _riassunto(treno: dict, stazione: str = "") -> tuple[str, int, str, bool]:
    """(testo, ritardo in minuti, binario, cancellato)"""
    nome = f"{treno.get('compNumeroTreno', '').strip() or 'Treno ' + str(treno['numeroTreno'])} {treno['origine']} → {treno['destinazione']}"
    ritardo = int(treno.get("ritardo") or 0)
    cancellato = treno.get("tipoTreno") in ("ST", "PP", "SI", "SF") or treno.get("provvedimento") in (1, 2)
    avviso = (treno.get("subTitle") or "").strip()
    f = _fermata(treno, stazione) if stazione else None
    binario = ""
    if f:
        binario = (f.get("binarioEffettivoPartenzaDescrizione") or f.get("binarioProgrammatoPartenzaDescrizione") or "").strip()
        partito = bool(f.get("effettiva") or f.get("partenzaReale"))
        orario = f"da {f['stazione'].title()}: partenza prevista {_ora(f.get('partenza_teorica') or f.get('programmata'))}"
        orario += f", partito alle {_ora(f.get('partenzaReale') or f.get('effettiva'))}" if partito else ""
        orario += f", binario {binario}" if binario else ", binario non ancora indicato"
    else:
        orario = ""
    if treno.get("nonPartito"):
        stato = "non ancora partito dall'origine"
    elif treno.get("stazioneUltimoRilevamento") and treno.get("stazioneUltimoRilevamento") != "--":
        stato = f"ultimo rilevamento {treno['stazioneUltimoRilevamento'].title()} alle {_ora(treno.get('oraUltimoRilevamento'))}"
    else:
        stato = ""
    ritardo_testo = "in orario" if ritardo <= 0 else f"ritardo {ritardo} min"
    parti = [nome, "CANCELLATO" if cancellato else ritardo_testo, stato, orario, avviso]
    return " | ".join(p for p in parti if p), ritardo, binario, cancellato


def stato_treno(numero: str, stazione: str = "") -> str:
    """Ritardo in tempo reale, posizione e binario di un treno di oggi (Frecciarossa, Intercity, regionali, anche Italo).

    Args:
        numero: numero del treno (es. '9409'); se l'utente non lo sa, trovalo prima con cerca_treni
        stazione: stazione dell'utente, per dire orario e binario di partenza da lì (es. 'Bologna Centrale')
    """
    treno = _andamento(re.sub(r"\D", "", numero), datetime.date.today(), stazione)
    if not treno:
        return f"ViaggiaTreno non ha dati sul treno {numero} di oggi."
    return _riassunto(treno, stazione)[0]


# --- treni seguiti -------------------------------------------------------------------------------

def _carica() -> list[dict]:
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []


def _salva(elenco: list[dict]):
    FILE.write_text(json.dumps(elenco, ensure_ascii=False, indent=1), encoding="utf-8")


def segui_treno(numero: str, data: str, ora_partenza: str, stazione: str) -> str:
    """Tiene d'occhio un treno e avvisa su Telegram se è in ritardo, cancellato, o quando esce il binario.

    Args:
        numero: numero del treno (es. '9409')
        data: giorno del viaggio 'AAAA-MM-GG'
        ora_partenza: ora di partenza dalla stazione dell'utente 'HH:MM' (dal risultato di cerca_treni)
        stazione: stazione da cui parte l'utente (es. 'Bologna Centrale')
    """
    numero = re.sub(r"\D", "", numero)
    partenza = datetime.datetime.combine(datetime.date.fromisoformat(data), datetime.time.fromisoformat(ora_partenza))
    if partenza < datetime.datetime.now() - datetime.timedelta(hours=1):
        return "Quel treno è già partito."
    with _lock:
        elenco = [t for t in _carica() if not (t["numero"] == numero and t["data"] == data)]
        elenco.append({"numero": numero, "data": data, "partenza": partenza.strftime("%Y-%m-%d %H:%M"),
                       "stazione": stazione, "ritardo_avvisato": 0, "binario_avvisato": "", "cancellato": False,
                       "ultimo_controllo": 0})
        elenco.sort(key=lambda t: t["partenza"])
        _salva(elenco)
    inizio = partenza - ANTICIPO
    return (f"Seguo il treno {numero} del {partenza:%d/%m} (partenza {partenza:%H:%M} da {stazione}): dalle "
            f"{inizio:%H:%M} ti scrivo su Telegram se è in ritardo, cancellato o quando esce il binario.")


def treni_seguiti() -> str:
    """Elenca i treni che Jarvis sta seguendo."""
    elenco = _carica()
    if not elenco:
        return "Non sto seguendo nessun treno."
    return "\n".join(f"{i}. treno {t['numero']} del {t['partenza']} da {t['stazione']}" for i, t in enumerate(elenco, 1))


def smetti_di_seguire(numero: str) -> str:
    """Smette di seguire un treno (numero del treno)."""
    numero = re.sub(r"\D", "", numero)
    with _lock:
        elenco = _carica()
        restano = [t for t in elenco if t["numero"] != numero]
        _salva(restano)
    return f"Non seguo più il treno {numero}." if len(restano) < len(elenco) else f"Non stavo seguendo il treno {numero}."


def controlla() -> list[str]:
    """Chiamata dal bot ogni 20 s: controlla i treni nella loro finestra e restituisce i messaggi da mandare."""
    adesso = datetime.datetime.now()
    messaggi = []
    with _lock:
        elenco = _carica()
    cambiati = False
    for t in elenco:
        partenza = datetime.datetime.strptime(t["partenza"], "%Y-%m-%d %H:%M")
        fine = partenza + datetime.timedelta(minutes=max(t["ritardo_avvisato"], 0) + 15)
        if adesso < partenza - ANTICIPO or adesso > fine or time.time() - t["ultimo_controllo"] < OGNI:
            continue
        t["ultimo_controllo"] = time.time()
        cambiati = True
        try:
            treno = _andamento(t["numero"], partenza.date(), t["stazione"])
        except Exception as e:
            print(f"[ritardi] {t['numero']}: {e}")
            continue
        if not treno:
            continue
        testo, ritardo, binario, cancellato = _riassunto(treno, t["stazione"])
        motivi = []
        if cancellato and not t["cancellato"]:
            motivi.append("⚠️ cancellato o limitato")
            t["cancellato"] = True
        if abs(ritardo - t["ritardo_avvisato"]) >= SOGLIA:
            motivi.append(f"🕒 ritardo {ritardo} min" if ritardo > 0 else "✅ tornato in orario")
            t["ritardo_avvisato"] = ritardo
        if binario and binario != t["binario_avvisato"]:
            motivi.append(f"🚉 binario {binario}" + (" (cambiato)" if t["binario_avvisato"] else ""))
            t["binario_avvisato"] = binario
        if motivi:
            messaggi.append(f"🚆 Treno {t['numero']}: {', '.join(motivi)}\n{testo}")
    with _lock:
        # i treni passati si tolgono; il file si riscrive solo se qualcosa è cambiato
        attuali = {(t["numero"], t["data"]): t for t in _carica()}
        for t in elenco:
            if (t["numero"], t["data"]) in attuali:
                attuali[(t["numero"], t["data"])] = t
        restano = [t for t in attuali.values()
                   if datetime.datetime.strptime(t["partenza"], "%Y-%m-%d %H:%M") + datetime.timedelta(hours=3) > adesso]
        if cambiati or len(restano) != len(attuali):
            _salva(restano)
    return messaggi
