"""Promemoria salvati su file; il bot Telegram li consegna all'ora giusta."""
import datetime
import json
import threading
from pathlib import Path

FILE = Path(__file__).with_name("promemoria.json")
_lock = threading.Lock()
_FORMATO = "%Y-%m-%d %H:%M"
RIPETIZIONI = {"": None, "ogni giorno": datetime.timedelta(days=1), "ogni settimana": datetime.timedelta(weeks=1)}


def _carica() -> list[dict]:
    if not FILE.exists():
        return []
    return json.loads(FILE.read_text(encoding="utf-8"))


def _salva(elenco: list[dict]):
    elenco.sort(key=lambda p: p["quando"])
    FILE.write_text(json.dumps(elenco, ensure_ascii=False, indent=1), encoding="utf-8")


def crea_promemoria(quando: str, testo: str, ripeti: str = "") -> str:
    """Crea un promemoria che arriverà su Telegram.

    Args:
        quando: data e ora nel formato 'AAAA-MM-GG HH:MM', calcolata a partire dall'ora attuale
        testo: cosa ricordare
        ripeti: '' (una volta sola), 'ogni giorno' oppure 'ogni settimana'
    """
    try:
        momento = datetime.datetime.strptime(quando.strip()[:16].replace("T", " "), _FORMATO)
    except ValueError:
        return "Data non valida: usa il formato AAAA-MM-GG HH:MM."
    if ripeti not in RIPETIZIONI:
        return "Ripetizione non valida: usa '', 'ogni giorno' o 'ogni settimana'."
    if momento < datetime.datetime.now() and not ripeti:
        return "Quell'ora è già passata."
    with _lock:
        elenco = _carica()
        elenco.append({"quando": momento.strftime(_FORMATO), "testo": testo, "ripeti": ripeti})
        _salva(elenco)
    return f"Promemoria fissato per {momento:%d/%m alle %H:%M}{' (' + ripeti + ')' if ripeti else ''}: {testo}"


def lista_promemoria() -> str:
    """Elenca i promemoria in attesa, numerati."""
    elenco = _carica()
    if not elenco:
        return "Nessun promemoria in attesa."
    return "\n".join(
        f"{i}. {p['quando']} {p['testo']}{' (' + p['ripeti'] + ')' if p['ripeti'] else ''}"
        for i, p in enumerate(elenco, 1)
    )


def cancella_promemoria(numero: int) -> str:
    """Cancella il promemoria con quel numero (vedi lista_promemoria)."""
    with _lock:
        elenco = _carica()
        if not 1 <= numero <= len(elenco):
            return f"Non esiste il promemoria numero {numero}."
        tolto = elenco.pop(numero - 1)
        _salva(elenco)
    return f"Cancellato: {tolto['testo']}"


def scaduti() -> list[str]:
    """Toglie (o ripianifica) i promemoria arrivati all'ora e restituisce i messaggi da mandare."""
    adesso = datetime.datetime.now()
    messaggi = []
    with _lock:
        elenco, restano = _carica(), []
        for p in elenco:
            momento = datetime.datetime.strptime(p["quando"], _FORMATO)
            if momento > adesso:
                restano.append(p)
                continue
            # se il PC era spento all'ora giusta il promemoria arriva comunque, segnalato come in ritardo
            ritardo = " (in ritardo: il PC era spento)" if adesso - momento > datetime.timedelta(minutes=5) else ""
            messaggi.append(f"⏰ {p['testo']}{ritardo}")
            passo = RIPETIZIONI.get(p["ripeti"])
            if passo:
                while momento <= adesso:
                    momento += passo
                restano.append({**p, "quando": momento.strftime(_FORMATO)})
        if messaggi:
            _salva(restano)
    return messaggi
