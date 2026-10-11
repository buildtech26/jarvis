"""Controllo dei dati senza fonte: prezzi, orari, numeri di treno, compagnie e azioni che compaiono nella risposta di
Gemini ma in nessun risultato di strumento, domanda o memoria.

La regola "non inventare dati" nel prompt non bastava: Flash-Lite il 07/10 ha citato Wizz Air senza che nessuno
strumento l'avesse trovata, e ha detto "ho impostato tre notti" con prezzi "intorno ai 40 o 50 €" senza chiamare
niente. Il controllo è solo testo, senza richieste: brain.py, se trova dubbi, chiede una volta a Gemini di
correggersi, poi aggiunge un avviso. prova_verifica.py lo prova sui casi veri del diario.
"""
import datetime
import json
import re
import threading
from pathlib import Path

# ogni risposta di Gemini con le sue fonti, per tarare il controllo sui casi veri: dice sia i falsi allarmi
# (dubbi su dati che c'erano) sia le invenzioni sfuggite (dati senza dubbi che le fonti non contengono)
FILE = Path(__file__).with_name("verifica.jsonl")
_lock = threading.Lock()

# compagnie e marchi di trasporto: citarli senza che una fonte li nomini è il caso tipico di dato inventato
MARCHI = [
    "Ryanair", "Wizz Air", "easyJet", "Vueling", "Volotea", "ITA Airways", "Lufthansa", "Air France", "KLM",
    "Transavia", "Aeroitalia", "British Airways", "Iberia", "Eurowings", "Austrian", "Swiss", "Brussels Airlines",
    "TAP", "Aegean", "Pegasus", "Turkish Airlines", "Emirates", "Norwegian", "Jet2", "Binter", "Air Dolomiti",
    "Frecciarossa", "Frecciargento", "Frecciabianca", "Italo", "Intercity", "Trenitalia", "Flixbus", "Itabus",
]

_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
_SEP = r"(?:,\s+|\s+(?:e|o|ed|a|-|–)\s+(?:i |gli |l')?|\s*[-–]\s*)"
# "40 €", "16,99 euro", "40 o 50 €", "tra i 40 e i 50 €", "40, 45 e 56 €"
_PREZZI_RISPOSTA = re.compile(rf"((?:(?:{_NUM}){_SEP})*(?:{_NUM}))\s*(?:€|euro\b)", re.I)
# nelle fonti basta il numero vicino al simbolo, prima o dopo: "40 €", "€40", "EUR 40", "54 €/notte"
_PREZZI_FONTE = re.compile(rf"({_NUM})\s*(?:€|euro\b|eur\b)|(?:€|\beur\b)\s*({_NUM})", re.I)
_ORA = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")
_ALLE = re.compile(r"\b(?:alle|all'|ore)\s*([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\b", re.I)
_TRENO = re.compile(r"\b(?:Frecciarossa|Frecciargento|Frecciabianca|Italo|Intercity|Regionale(?: Veloce)?|FR|FA|FB|IC|RV)"
                    r"\s+(\d{3,5})\b")
# azioni dichiarate al passato: senza uno strumento chiamato adesso non sono successe
_AZIONE = re.compile(r"\bho (?:appena |già |anche |subito )?(?:aperto|impostato|segnato|salvato|aggiunto|creato|inviato|"
                     r"mandato|spostato|cancellato|eliminato|annotato|registrato|programmato|archiviato|fissato|messo|"
                     r"tolto|rimosso|chiuso|avviato|prenotato|attivato|disattivato)\b", re.I)
# "Fatto." da solo: il 06/10 la voce ha captato una frase non rivolta a Jarvis ("manda maurizio") e lui ha risposto
# "Fatto." senza chiamare niente. Solo a inizio frase e seguito da punteggiatura, per lasciare stare "di fatto",
# "il fatto è che"
_FATTO = re.compile(r"(?:^|[.!?]\s+)((?:ecco\s+)?fatto|eseguito)(?=\s*(?:[.,;:!—–-]|$))", re.I)
# azioni promesse e non fatte: il 12/10, a "non tutti i giorni ma solo martedì giovedì e venerdì", Jarvis ha
# risposto "Provvedo subito a inserire le lezioni" senza chiamare niente
_PROMESSA = re.compile(r"\b(?:provvedo|procedo|rimedio)\b|\b(?:lo |le |li |la )?(?:faccio|inserisco|metto|aggiungo|"
                       r"sistemo|correggo|creo|segno|sposto|cancello) (?:subito|immediatamente|ora|adesso)\b", re.I)
# ricerche al passato: possono riferirsi alle domande precedenti ("ho cercato solo su Ryanair" dopo esplora_voli)
_RICERCA = re.compile(r"\bho (?:cercato|controllato|verificato|letto|trovato|guardato)\b", re.I)


def _valori(num: str) -> set[float]:
    """Un numero scritto in qualunque formato, con tutte le letture possibili: "1.234" può essere mille o 1,234."""
    s = num.strip()
    if "," in s and "." in s:
        return {float(s.replace(".", "").replace(",", "."))}
    if "," in s:
        return {float(s.replace(",", "."))}
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        return {float(s.replace(".", "")), float(s)} if s.count(".") == 1 else {float(s.replace(".", ""))}
    return {float(s)}


def _vicino(x: float, y: float) -> bool:
    return abs(x - y) <= max(1.0, 0.02 * y)  # arrotondamenti: "circa 40 €" per 39,99


def _prezzi_fonte(testo: str) -> list[float]:
    return [v for m in _PREZZI_FONTE.finditer(testo) for v in _valori(m.group(1) or m.group(2))]


def _orari(testo: str) -> set[tuple[int, int]]:
    trovati = {(int(h), int(m)) for h, m in _ORA.findall(testo)}
    trovati |= {(int(h), int(m or 0)) for h, m in _ALLE.findall(testo)}
    return trovati


def controlla(risposta: str, fonti: list[str], strumenti_usati: list[str], risultati_ora: list[str],
              strumenti_prima: bool = False) -> list[str]:
    """Dati della risposta che nessuna fonte giustifica. fonti: risultati degli strumenti (di questa domanda e delle
    ultime), domande dell'utente, memoria, ora attuale. risultati_ora: solo quelli di questa domanda.
    strumenti_prima: le domande precedenti hanno usato strumenti, quindi "ho cercato" può parlare di quelle."""
    tutto = "\n".join(fonti)
    dubbi: list[str] = []

    prezzi = _prezzi_fonte(tutto)
    somme = [a + b for i, a in enumerate(prezzi) for b in prezzi[i:]]  # andata + ritorno
    for gruppo in _PREZZI_RISPOSTA.finditer(risposta):
        for num in re.findall(_NUM, gruppo.group(1)):
            valori = _valori(num)
            if not any(_vicino(v, p) for v in valori for p in prezzi + somme):
                dubbi.append(f"{num} €")

    orari_fonte = _orari(tutto)
    # un orario calcolato ("esci alle 7:45") è plausibile se lo strumento di adesso ha dato degli orari
    if not _orari("\n".join(risultati_ora)):
        for h, m in sorted({(int(a), int(b)) for a, b in _ORA.findall(risposta)}):
            if (h, m) not in orari_fonte:
                dubbi.append(f"{h}:{m:02d}")

    for numero in _TRENO.findall(risposta):
        if not re.search(rf"\b{numero}\b", tutto):
            dubbi.append(f"treno {numero}")

    minuscolo = tutto.lower()
    for marchio in MARCHI:
        if re.search(rf"\b{re.escape(marchio)}\b", risposta, re.I) and marchio.lower() not in minuscolo:
            dubbi.append(marchio)

    if not strumenti_usati:
        if ((m := _AZIONE.search(risposta)) or (m := _PROMESSA.search(risposta))
                or (not strumenti_prima and (m := _RICERCA.search(risposta)))):
            dubbi.append(f"«{m.group(0)}» senza aver usato strumenti")
        elif m := _FATTO.search(risposta):
            dubbi.append(f"«{m.group(1)}» senza aver usato strumenti")
    return list(dict.fromkeys(dubbi))


def correzione(dubbi: list[str]) -> str:
    """Messaggio per il secondo tentativo: arriva a Gemini come se fosse un turno dell'utente."""
    return ("[Controllo automatico, non è l'utente a scrivere] Nella risposta ci sono dati che non vengono da nessuno "
            f"strumento né dalla conversazione: {'; '.join(dubbi)}. Se servono, chiama lo strumento che li verifica. "
            "Altrimenti riscrivi la risposta senza quei dati e di' chiaramente cosa non hai controllato. "
            "Non dire di aver fatto azioni che non hai fatto con uno strumento.")


def registra(domanda: str, strumenti: list[str], risultati: list[str], precedenti: list[str],
             tentativi: list[tuple[str, list[str]]]):
    """Aggiunge una riga a verifica.jsonl. precedenti: risultati delle ultime domande (per i seguiti);
    tentativi: (risposta, dubbi) del primo ed eventuale secondo giro."""
    riga = {"quando": f"{datetime.datetime.now():%Y-%m-%d %H:%M}", "domanda": domanda, "strumenti": strumenti,
            "risultati": [r[:1500] for r in risultati], "precedenti": [r[:1500] for r in precedenti],
            "tentativi": [{"risposta": r, "dubbi": d} for r, d in tentativi]}
    try:
        with _lock, FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(riga, ensure_ascii=False) + "\n")
    except OSError as e:  # il registro non deve mai far fallire una risposta
        print(f"[verifica] registro non scritto: {e}")


def con_avviso(risposta: str, dubbi: list[str]) -> str:
    """Ultima difesa dopo il secondo tentativo: la risposta resta, ma con un avviso che si legge anche a voce."""
    azioni = [d for d in dubbi if d.startswith("«")]
    dati = [d for d in dubbi if not d.startswith("«")]
    avvisi = []
    if dati:
        avvisi.append(f"non ho una fonte per {', '.join(dati)}")
    if azioni:
        avvisi.append("per questa risposta non ho usato nessuno strumento")
    return f"{risposta.rstrip()} (Attenzione: {' e '.join(avvisi)}.)"
