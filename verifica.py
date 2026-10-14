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
# kcal: Flash-Lite sbagliava di 100-150 kcal facendo i conti da solo (per questo c'è calcola_ricetta) e la riserva
# Mistral le dava senza chiamarlo
_KCAL = re.compile(rf"({_NUM})\s*(?:kcal|calorie)\b", re.I)
# una differenza fra due valori delle fonti vale solo se la frase parla di quello che resta o che manca ("ti restano
# 1490 kcal"): il 14/10 "un totale di 460 kcal" per il riso nero, inventato, passava come 740 (quota del pranzo) − 284
# (la pasta di una ricetta precedente). Con una decina di numeri nelle fonti, le differenze coprivano quasi tutto
_DIFFERENZA = re.compile(r"rest|manc|avanz|ancora|differenz|in più|in meno|sotto|sopra|oltre|margine|scart|"
                         r"risparmi|deficit|eccesso|superat|sforat", re.I)
# dubbio sugli ingredienti di una ricetta di Fit with Fun che non sono in dispensa e la risposta non nomina
MANCANTI = "non hai detto che in casa non ci sono: "
# giorno della settimana che non corrisponde alla data: la riserva Mistral sbagliava di un giorno 6 volte su 8
# ("martedì 15/10", che è giovedì). Si controlla senza fonti: una coppia così è sbagliata e basta
DATA_SBAGLIATA = "giorno della settimana sbagliato: "
_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
         "novembre", "dicembre"]
_GIORNO_DATA = re.compile(r"\b(lunedì|lunedi|martedì|martedi|mercoledì|mercoledi|giovedì|giovedi|venerdì|venerdi|"
                          r"sabato|domenica|lun|mar|mer|gio|ven|sab|dom)\.?,?\s+(?:il\s+)?(\d{1,2})"
                          r"(?:/(\d{1,2})(?:/(\d{4}|\d{2}))?|\s+(" + "|".join(_MESI) + r"))\b", re.I)
# impegni dati per assenti senza aver letto il calendario: il 12/10 "Non hai annotato alcun impegno per la prossima
# domenica" con il tennis in calendario. "se non hai impegni, ..." è un consiglio, non un'affermazione
_ASSENZA = re.compile(r"(?<!se )\bnon hai (?:(?:annotato |segnato |in calendario )?(?:alcun|nessun)\w* |altri |)"
                      r"(?:impegn|appuntament|eventi|lezion|allenament|niente in (?:calendario|programma)|"
                      r"nulla in (?:calendario|programma))\w*|\bnessun (?:impegno|appuntamento|evento|allenamento)\b|"
                      r"\bsei liber[oa]\b|\bgiornata libera\b|\bnon c'è (?:niente|nulla) in (?:calendario|programma)",
                      re.I)
SENZA_CALENDARIO = " senza aver letto il calendario"
_LEGGONO_CALENDARIO = re.compile(r"\b(?:eventi_calendario|seduta_allenamento|allenamenti_settimana|crea_evento|"
                                 r"crea_eventi_ripetuti|sposta_evento):")


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


def _kcal_senza_fonte(risposta: str, tutto: str) -> list[str]:
    """Le kcal della risposta che non stanno in nessuna fonte, solo quando le fonti parlano di kcal (domande sul cibo;
    "una banana ha circa 90 kcal" detto a memoria va bene). Le somme non valgono mai: con gli ingredienti di una
    ricetta quasi ogni numero inventato sarebbe la somma di due righe (il totale lo dà calcola_ricetta)."""
    fonte = [v for m in _KCAL.finditer(tutto) for v in _valori(m.group(1))]
    if not fonte:
        return []
    differenze = [abs(a - b) for a in fonte for b in fonte]
    dubbi = []
    for m in _KCAL.finditer(risposta):
        frase = re.split(r"[.!?;:]\s", risposta[max(0, m.start() - 80):m.start()])[-1]
        ammessi = fonte + differenze if _DIFFERENZA.search(frase) else fonte
        if not any(abs(v - c) <= max(5.0, 0.03 * c) for v in _valori(m.group(1)) for c in ammessi):
            dubbi.append(f"{m.group(1)} kcal")
    return dubbi


def _oggi() -> datetime.date:  # le prove la sostituiscono
    return datetime.date.today()


def _date_sbagliate(risposta: str) -> list[str]:
    """'martedì 15/10' quando il 15/10 è giovedì. Senza anno vale la data più vicina a oggi."""
    oggi, sbagliate = _oggi(), []
    for m in _GIORNO_DATA.finditer(risposta):
        nome, giorno = m.group(1).lower()[:3], int(m.group(2))
        mese = int(m.group(3)) if m.group(3) else _MESI.index(m.group(5).lower()) + 1
        anni = [int(m.group(4)) + (2000 if len(m.group(4)) == 2 else 0)] if m.group(4) else [oggi.year + d for d in (-1, 0, 1)]
        try:
            data = min((datetime.date(a, mese, giorno) for a in anni), key=lambda d: abs((d - oggi).days))
        except ValueError:  # 31/02 e simili: non è un giorno della settimana sbagliato, lo lascio stare
            continue
        if _GIORNI[data.weekday()][:3] != nome:
            sbagliate.append(f"{m.group(0).strip()} (il {data:%d/%m/%Y} è {_GIORNI[data.weekday()]})")
    return sbagliate


def _radice(parola: str) -> str:
    return parola[:max(3, min(len(parola) - 1, 5))]


def _mancanti_taciuti(risposta: str, risultati_ora: list[str]) -> list[str]:
    """Gli ingredienti "Non in dispensa" della ricetta di Fit with Fun citata (dal link) che la risposta non nomina.
    Il 13/10 Gemini ha preso il cous cous di cavolfiore, ha tolto cavolfiore, avocado, limone, zenzero e menta senza
    dirlo e l'ha data come ricetta di Fit with Fun, mentre l'istruzione dello strumento chiede di dirlo sempre."""
    minuscolo = risposta.lower()
    # senza i link: quello della ricetta contiene spesso proprio l'ingrediente che manca ("...-di-cavolfiore-...")
    parole_risposta = {_radice(p) for p in re.findall(r"[a-zà-ù]{4,}", re.sub(r"https?://\S+", " ", minuscolo))}
    taciuti = []
    for risultato in risultati_ora:
        if not risultato.startswith("ricette_fit_with_fun:"):
            continue
        for blocco in re.split(r"\n\n(?=\d+\. )", risultato):
            link, manca = re.search(r"Link: (\S+)", blocco), re.search(r"Non in dispensa: (.+)", blocco)
            if not (link and manca and link.group(1).rstrip("/").lower() in minuscolo):
                continue
            for ingrediente in manca.group(1).split(", "):
                parole = {_radice(p) for p in re.findall(r"[a-zà-ù]{4,}", ingrediente.lower())}
                if parole and not parole & parole_risposta:
                    taciuti.append(ingrediente.strip())
    return taciuti


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

    dubbi += _kcal_senza_fonte(risposta, tutto)
    if taciuti := _mancanti_taciuti(risposta, risultati_ora):
        dubbi.append(MANCANTI + ", ".join(taciuti))
    dubbi += [DATA_SBAGLIATA + d for d in _date_sbagliate(risposta)]
    # le fonti con il calendario possono essere anche delle ultime domande ("e domenica?" dopo "cosa ho sabato")
    if (m := _ASSENZA.search(risposta)) and not _LEGGONO_CALENDARIO.search(tutto):
        dubbi.append(f"«{m.group(0)}»{SENZA_CALENDARIO}")

    if not strumenti_usati:
        if ((m := _AZIONE.search(risposta)) or (m := _PROMESSA.search(risposta))
                or (not strumenti_prima and (m := _RICERCA.search(risposta)))):
            dubbi.append(f"«{m.group(0)}» senza aver usato strumenti")
        elif m := _FATTO.search(risposta):
            dubbi.append(f"«{m.group(1)}» senza aver usato strumenti")
    return list(dict.fromkeys(dubbi))


def correzione(dubbi: list[str]) -> str:
    """Messaggio per il secondo tentativo: arriva a Gemini come se fosse un turno dell'utente."""
    mancanti = [d[len(MANCANTI):] for d in dubbi if d.startswith(MANCANTI)]
    date = [d[len(DATA_SBAGLIATA):] for d in dubbi if d.startswith(DATA_SBAGLIATA)]
    assenze = [d for d in dubbi if d.endswith(SENZA_CALENDARIO)]
    dati = [d for d in dubbi if not d.startswith((MANCANTI, DATA_SBAGLIATA)) and d not in assenze]
    testo = "[Controllo automatico, non è l'utente a scrivere]"
    if date:
        testo += (f" Il giorno della settimana non corrisponde alla data: {'; '.join(date)}. Ricontrolla le date "
                  "con lo strumento e correggile.")
    if assenze:
        testo += (" Hai detto che un impegno non c'è senza aver letto il calendario: leggilo con eventi_calendario "
                  "(gli allenamenti sono eventi del calendario) e rispondi da lì.")
    if dati:
        testo += (f" Nella risposta ci sono dati che non vengono da nessuno strumento né dalla conversazione: "
                  f"{'; '.join(dati)}. Se servono, chiama lo strumento che li verifica. Altrimenti riscrivi la "
                  "risposta senza quei dati e di' chiaramente cosa non hai controllato. Non dire di aver fatto azioni "
                  "che non hai fatto con uno strumento.")
    if any(d.endswith(" kcal") for d in dati):
        testo += (" Le kcal di una ricetta non si stimano: chiama calcola_ricetta con gli ingredienti a crudo e i "
                  "grammi, e riporta il suo totale.")
    if mancanti:
        testo += (f" La ricetta che citi chiede ingredienti che l'utente non ha in casa: {', '.join(mancanti)}. "
                  "Riscrivi la risposta dicendolo per ognuno (anche se l'hai tolto o sostituito, e con cosa).")
    return testo


def registra(domanda: str, strumenti: list[str], risultati: list[str], precedenti: list[str],
             tentativi: list[tuple[str, list[str]]], modello: str = ""):
    """Aggiunge una riga a verifica.jsonl. precedenti: risultati delle ultime domande (per i seguiti);
    tentativi: (risposta, dubbi) del primo ed eventuale secondo giro; modello: solo per la riserva (Mistral)."""
    riga = {"quando": f"{datetime.datetime.now():%Y-%m-%d %H:%M}", "domanda": domanda, "strumenti": strumenti,
            "risultati": [r[:1500] for r in risultati], "precedenti": [r[:1500] for r in precedenti],
            "tentativi": [{"risposta": r, "dubbi": d} for r, d in tentativi]}
    if modello:
        riga["modello"] = modello
    try:
        with _lock, FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(riga, ensure_ascii=False) + "\n")
    except OSError as e:  # il registro non deve mai far fallire una risposta
        print(f"[verifica] registro non scritto: {e}")


def con_avviso(risposta: str, dubbi: list[str]) -> str:
    """Ultima difesa dopo il secondo tentativo: la risposta resta, ma con un avviso che si legge anche a voce."""
    assenze = [d for d in dubbi if d.endswith(SENZA_CALENDARIO)]
    azioni = [d for d in dubbi if d.startswith("«") and d not in assenze]
    mancanti = [d[len(MANCANTI):] for d in dubbi if d.startswith(MANCANTI)]
    date = [d[len(DATA_SBAGLIATA):] for d in dubbi if d.startswith(DATA_SBAGLIATA)]
    dati = [d for d in dubbi if not d.startswith(("«", MANCANTI, DATA_SBAGLIATA))]
    avvisi = []
    if date:
        avvisi.append(f"controlla le date, {'; '.join(date)}")
    if assenze:
        avvisi.append("non ho guardato il calendario, quindi non so se hai impegni")
    if dati:
        avvisi.append(f"non ho una fonte per {', '.join(dati)}")
    if azioni:
        avvisi.append("per questa risposta non ho usato nessuno strumento")
    if mancanti:
        avvisi.append(f"la ricetta originale chiede anche {', '.join(mancanti)}, che in casa non hai")
    return f"{risposta.rstrip()} (Attenzione: {' e '.join(avvisi)}.)"
