"""La dispensa: il cibo che c'è in casa, nel database Notion "Dispensa", per le ricette con quello che c'è
(mancano() dice quali ingredienti di una ricetta non ci sono).

Il cibo entra con aggiorna_dispensa (un messaggio o la voce: "ho comprato pasta e 6 uova") ed esce dai pasti segnati
in Cronometer: alle 21:30, e alle 10 del giorno dopo per i pasti segnati tardi, i cibi nuovi si scalano. I nomi di
Cronometer ("Pasta, Dry") si abbinano alle voci con la colonna "Nomi Cronometer"; quelli che non ci sono li abbina
una richiesta Gemini, che converte anche le quantità (uova a pezzi, pasta cotta in secca), e il nome trovato si
scrive nella colonna, dove l'utente lo può correggere. I cibi già scalati stanno in dispensa.json (fuori da git):
così un cibo non si toglie due volte.
Le quantità sono indicative. Lo stato (C'è, Sta finendo, Finito) lo calcola Notion con una formula: sta finendo
sotto un quarto dell'ultimo acquisto. Il database l'ha creato l'utente (2026-10-11), per l'integrazione Jarvis
serve la connessione (••• → Connessioni → Jarvis).
"""
import copy
import datetime
import io
import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

import requests

import cronometer
import notion
import quota

DISPENSA = os.getenv("NOTION_DISPENSA", "9c147aba-1ad0-42a9-b7af-13d9b3b4525c").strip()  # data source
FILE = Path(__file__).with_name("dispensa.json")  # i cibi di Cronometer già scalati, per giorno
SERALE, MATTINA = datetime.time(21, 31), datetime.time(10)  # 21:31: dopo il riepilogo dell'alimentazione
GIORNI_TENUTI = 7
POSTI = {"frigo": "Frigo", "freezer": "Freezer", "congelator": "Freezer", "surgelat": "Freezer", "dispensa": "Dispensa"}
# unità dette a parole → (unità della dispensa, fattore)
UNITA = {"kg": ("g", 1000), "chili": ("g", 1000), "chilo": ("g", 1000), "hg": ("g", 100), "etti": ("g", 100),
         "etto": ("g", 100), "g": ("g", 1), "gr": ("g", 1), "grammi": ("g", 1), "l": ("ml", 1000),
         "lt": ("ml", 1000), "litri": ("ml", 1000), "litro": ("ml", 1000), "cl": ("ml", 10), "ml": ("ml", 1),
         "pz": ("pz", 1), "pezzi": ("pz", 1), "pezzo": ("pz", 1)}
_NUMERO = r"(\d+(?:[.,]\d+)?)"
_QUANTITA = re.compile(rf"{_NUMERO}\s*({'|'.join(sorted(UNITA, key=len, reverse=True))})?\b", re.I)
_PAROLE_VUOTE = {"di", "del", "della", "dei", "delle", "il", "lo", "la", "le", "gli", "un", "una", "uno", "in", "nel",
                 "nella", "nello", "circa", "confezione", "confezioni", "pacco", "pacchi", "bottiglia", "vasetto"}
_errori = {"pausa": 0.0, "volte": 0}
LETTURA_VALE = 120  # secondi in cui l'ultima lettura della dispensa vale ancora (vedi _voci)
_lettura: dict = {"voci": None, "quando": 0.0}
_lettura_lock = threading.Lock()


# --- Notion ---

def configurata() -> bool:
    return notion.configurato()


def _testo(prop: dict) -> str:
    return "".join(t["plain_text"] for t in prop.get("rich_text") or prop.get("title") or [])


def _voce(p: dict) -> dict:
    props = p["properties"]
    stato = (props.get("Stato") or {}).get("formula") or {}
    quantita = props["Quantità"]["number"]
    return {"id": p["id"], "nome": _testo(props["Nome"]), "quantita": quantita,
            "unita": (props["Unità"]["select"] or {}).get("name") or "pz",
            "dove": (props["Dove"]["select"] or {}).get("name") or "Dispensa",
            "acquisto": props["Ultimo acquisto"]["number"],
            "cronometer": [n.strip() for n in _testo(props["Nomi Cronometer"]).split(";") if n.strip()],
            "stato": stato.get("string") or ("Finito" if (quantita or 0) <= 0 else "C'è")}


def _voci() -> list[dict]:
    """Le voci della dispensa. Una lettura vale LETTURA_VALE secondi: una domanda sul cibo la chiedeva fino a 4 volte
    (consigli_alimentazione, ricette_fit_with_fun e ogni calcola_ricetta), a 0,6-2,5 s l'una. Ogni scrittura di
    Jarvis la butta; una modifica fatta a mano su Notion si vede al più dopo LETTURA_VALE secondi."""
    with _lettura_lock:
        if _lettura["voci"] is None or time.monotonic() - _lettura["quando"] > LETTURA_VALE:
            _lettura["voci"], _lettura["quando"] = _leggi_voci(), time.monotonic()
        return copy.deepcopy(_lettura["voci"])  # chi le riceve le modifica (voce.update, voci.remove)


def _dimentica_voci():
    _lettura["voci"] = None


def _leggi_voci() -> list[dict]:
    voci, cursore = [], None
    while True:
        corpo = {"page_size": 100, "sorts": [{"property": "Nome", "direction": "ascending"}]}
        if cursore:
            corpo["start_cursor"] = cursore
        r = notion._chiama("POST", f"/data_sources/{DISPENSA}/query", **corpo)
        voci += [_voce(p) for p in r["results"]]
        if not r.get("has_more"):
            return voci
        cursore = r["next_cursor"]


def _proprieta(**campi) -> dict:
    props = {}
    if "nome" in campi:
        props["Nome"] = {"title": [{"text": {"content": campi["nome"]}}]}
    if "quantita" in campi:
        props["Quantità"] = {"number": campi["quantita"]}
    if "unita" in campi:
        props["Unità"] = {"select": {"name": campi["unita"]}}
    if "dove" in campi:
        props["Dove"] = {"select": {"name": campi["dove"]}}
    if "acquisto" in campi:
        props["Ultimo acquisto"] = {"number": campi["acquisto"]}
    if "cronometer" in campi:
        props["Nomi Cronometer"] = {"rich_text": [{"text": {"content": "; ".join(campi["cronometer"])[:1900]}}]}
    return props


def _cambia(voce: dict, **campi):
    _dimentica_voci()
    notion._chiama("PATCH", f"/pages/{voce['id']}", properties=_proprieta(**campi))
    voce.update(campi)


def _crea(**campi) -> dict:
    _dimentica_voci()
    p = notion._chiama("POST", "/pages", parent={"type": "data_source_id", "data_source_id": DISPENSA},
                       properties=_proprieta(**campi))
    return _voce(p)


# --- testo ---

def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _quanto(voce: dict) -> str:
    q, u = voce["quantita"], voce["unita"]
    if q is None:
        return "quantità non segnata"
    if q <= 0:
        return "finito"
    if u == "g" and q >= 1000:
        return f"{_num(round(q / 1000, 1))} kg"
    if u == "ml" and q >= 1000:
        return f"{_num(round(q / 1000, 1))} l"
    return f"{_num(round(q))} {u}" if u != "pz" else f"{_num(round(q, 1))} pz"


def _radici(testo: str) -> list[str]:
    # singolare e plurale uguali: uovo/uova, pomodoro/pomodori
    return [p[:max(3, min(len(p) - 1, 5))] for p in re.findall(r"[a-zà-ù]{3,}", testo.lower())
            if p not in _PAROLE_VUOTE]


def _trova(nome: str, voci: list[dict]) -> dict | None:
    """La voce della dispensa per un prodotto detto a parole. La prima parola deve essere la stessa ("latte intero"
    → Latte, ma "fiocchi di latte" no) e le parole di uno devono stare tutte nell'altro; vince chi ne ha di più."""
    sue = _radici(nome)
    migliore, punti = None, 0
    for v in voci:
        radici = _radici(v["nome"])
        if not radici or not sue or radici[0] != sue[0]:
            continue
        comuni = set(radici) & set(sue)
        if (set(radici) <= set(sue) or set(sue) <= set(radici)) and len(comuni) > punti:
            migliore, punti = v, len(comuni)
    return migliore


def _leggi_prodotti(prodotti: str) -> list[dict]:
    """'pasta 1 kg, 6 uova, latte 1 l (frigo)' → nome, quantità (nell'unità della dispensa), unità, posto."""
    elenco = []
    for pezzo in re.split(r"[,;\n]|\be\b(?=\s+\d)", prodotti):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        dove = next((posto for chiave, posto in POSTI.items() if chiave in pezzo.lower()), None)
        m = _QUANTITA.search(pezzo)
        quantita, unita = None, None
        if m:
            unita, fattore = UNITA.get((m.group(2) or "pz").lower(), ("pz", 1))
            quantita = float(m.group(1).replace(",", ".")) * fattore
            pezzo = pezzo[:m.start()] + " " + pezzo[m.end():]
        pezzo = re.sub(r"\(?\b(?:(?:in|nel|nella) )?(?:frigo(?:rifero)?|freezer|congelatore|dispensa)\b\)?", " ", pezzo,
                       flags=re.I)
        parole = pezzo.replace("(", " ").replace(")", " ").split()
        while parole and parole[0].lower() in _PAROLE_VUOTE:  # "1 kg di pasta", "il latte": solo all'inizio
            parole.pop(0)
        nome = " ".join(parole).strip(" .:-")
        if nome:
            elenco.append({"nome": nome[:1].upper() + nome[1:], "quantita": quantita, "unita": unita, "dove": dove})
    return elenco


# --- strumenti per Gemini ---

def dispensa(dove: str = "") -> str:
    """Cosa c'è in casa da mangiare secondo la dispensa su Notion: ogni prodotto con la quantità indicativa e dove sta
    (dispensa, frigo, freezer), poi quelli che stanno finendo e quelli finiti (da ricomprare). Le quantità sono
    stime: dille con "circa".

    Args:
        dove: 'frigo', 'freezer' o 'dispensa' per un posto solo; vuoto per tutto
    """
    voci = _voci()
    posto = next((p for chiave, p in POSTI.items() if chiave in dove.lower()), "")
    if posto:
        voci = [v for v in voci if v["dove"] == posto]
    if not voci:
        return (f"Nella dispensa su Notion non c'è niente{' in ' + posto.lower() if posto else ''}: si riempie "
                "dicendomi cosa compri (es. \"ho comprato pasta, 6 uova e il latte\").")
    righe = []
    for p in ["Dispensa", "Frigo", "Freezer"]:
        ci_sono = [v for v in voci if v["dove"] == p and v["stato"] != "Finito"]
        if ci_sono:
            righe.append(f"{p}: " + "; ".join(f"{v['nome']} {_quanto(v)}" + (" (sta finendo)"
                                                                            if v["stato"] == "Sta finendo" else "")
                                              for v in ci_sono))
    finiti = [v["nome"] for v in voci if v["stato"] == "Finito"]
    if finiti:
        righe.append("Finiti: " + ", ".join(finiti))
    return "\n".join(righe)


def aggiorna_dispensa(prodotti: str, azione: str = "aggiungi") -> str:
    """Aggiorna la dispensa su Notion quando l'utente compra, usa, finisce o butta del cibo, o corregge una quantità.
    Le quantità sono indicative: se l'utente non le dice stimale tu (una confezione normale: pasta 500 g, latte 1 l,
    uova 6) e scrivile; frutta, uova e scatolette a pezzi. Non serve per quello che mangia e segna in Cronometer: quello
    si scala da solo la sera.

    Args:
        prodotti: separati da virgole, ognuno con quantità e unità (g, kg, ml, l, pz) e, se lo sai, il posto
            (frigo, freezer, dispensa); es. 'pasta 1 kg, uova 6, latte 1 l frigo, piselli 450 g freezer'
        azione: 'aggiungi' (ha comprato), 'togli' (ha usato o buttato quella quantità), 'finito' (non ce n'è più),
            'imposta' (quanto ne ha adesso, per correggere), 'elimina' (toglie la voce dalla dispensa)
    """
    azione = azione.strip().lower()
    if azione not in {"aggiungi", "togli", "finito", "imposta", "elimina"}:
        return "Azione non valida: aggiungi, togli, finito, imposta o elimina."
    elenco = _leggi_prodotti(prodotti)
    if not elenco:
        return "Dimmi quali prodotti."
    if azione == "aggiungi":
        return _aggiungi(elenco)
    voci, fatti, problemi = _leggi_voci(), [], []  # per scrivere si legge sempre da Notion
    for prodotto in elenco:
        voce = _trova(prodotto["nome"], voci)
        q, u = prodotto["quantita"], prodotto["unita"]
        if voce is None:
            problemi.append(prodotto["nome"])
            continue
        if azione == "elimina":
            _dimentica_voci()
            notion._chiama("PATCH", f"/pages/{voce['id']}", in_trash=True)
            voci.remove(voce)
            fatti.append(f"{voce['nome']}: tolto dalla dispensa")
            continue
        if azione == "finito" or (azione == "togli" and q is None):
            nuova = 0.0
        elif u and u != voce["unita"]:
            problemi.append(f"{prodotto['nome']} (in dispensa è in {voce['unita']}, non in {u})")
            continue
        else:
            nuova = q if azione == "imposta" else max((voce["quantita"] or 0) - q, 0)
        campi = {"quantita": nuova}
        if azione == "imposta" and nuova > (voce["acquisto"] or 0):
            campi["acquisto"] = nuova
        _cambia(voce, **campi)
        fatti.append(f"{voce['nome']}: {_quanto(voce)}")
    testo = "Dispensa aggiornata. " + "; ".join(fatti) if fatti else "Niente cambiato nella dispensa."
    if problemi:
        testo += f" Non in dispensa o non cambiati: {', '.join(problemi)}."
    return testo


def _aggiungi(elenco: list[dict]) -> str:
    """Prodotti comprati (nome, quantità, unità, posto; quantità e posto possono mancare) dentro la dispensa."""
    voci, fatti = _leggi_voci(), []  # per scrivere si legge sempre da Notion
    for prodotto in elenco:
        voce = _trova(prodotto["nome"], voci)
        q, u = prodotto.get("quantita"), prodotto.get("unita")
        if voce is None:
            voce = _crea(nome=prodotto["nome"], quantita=q or 1, unita=u or "pz", dove=prodotto.get("dove") or "Dispensa",
                         acquisto=q or 1)
            voci.append(voce)
            fatti.append(f"{voce['nome']}: nuovo, {_quanto(voce)} in {voce['dove'].lower()}")
            continue
        if q is None:  # "ho ricomprato il latte": torna alla quantità dell'ultima spesa
            q, u = voce["acquisto"] or 1, voce["unita"]
        prima = max(voce["quantita"] or 0, 0) if u == voce["unita"] else 0
        campi = {"quantita": prima + q, "unita": u, "acquisto": prima + q}
        if prodotto.get("dove"):
            campi["dove"] = prodotto["dove"]
        _cambia(voce, **campi)
        fatti.append(f"{voce['nome']}: ora {_quanto(voce)}")
    return "Dispensa aggiornata. " + "; ".join(fatti)


def in_casa() -> str:
    """Una riga per consigli_alimentazione: quello che c'è, quello che sta finendo. '' se la dispensa è vuota."""
    voci = [v for v in _voci() if v["stato"] != "Finito"]
    if not voci:
        return ""
    finendo = [v["nome"] for v in voci if v["stato"] == "Sta finendo"]
    return ("In casa (dispensa su Notion, quantità indicative): " + "; ".join(f"{v['nome']} {_quanto(v)}" for v in voci)
            + "." + (f" Stanno finendo: {', '.join(finendo)}." if finendo else ""))


# --- ingredienti di una ricetta: cosa manca in casa ---

# le parole che non cambiano l'ingrediente: "tonno al naturale sgocciolato" è il tonno, "cacao magro in polvere" il cacao
_GENERICA = re.compile(r"natural\w*|sgocciolat\w*|cott\w|crud\w|secc\w+|fresc\w+|light|magr\w|inter\w|integral\w|"
                       r"tritat\w|grattugiat\w|polvere|preferit\w|basmati|sbucciat\w|scolat\w|battut\w|"
                       r"denocciolat\w|piccol\w|baby|evo|sgusciat\w|tostat\w|scart\w|netto|peso|circa|tipo|scatol\w*|"
                       r"q|b|g|gr|kg|ml")
# le parole dopo queste fanno un altro cibo: "burro di arachidi" non è il burro, "latte alla nocciola" non è il latte
_LEGANTI = {"di", "d", "del", "della", "dei", "delle", "al", "alla", "allo", "ai", "agli", "alle"}
_VUOTE_RICETTE = _PAROLE_VUOTE | _LEGANTI | {"e", "a", "da", "per", "con", "o"}
# stessi ingredienti con nomi diversi (fra ricette e dispensa); grana diventa parmigiano perché non sia grano
_SINONIMI = [(r"\bparmigian\w*|\bgrana(?: padano)?\b|\bformaggio grattugiat\w*", "parmigiano"),
             (r"\b(?:passata|polpa) di pomodor\w*", "salsa di pomodoro"),
             (r"\b(?:album\w*|tuorl\w*|uov\w*)", "uova"), (r"\bpan(?:e)? gratt\w*", "pangrattato"),
             (r"\bcarne macinat\w*|^\s*macinat\w*(?: magr\w)?(?: di (?:manzo|bovino|vitello))?", "carne macinata"), (r"\bdolcificant\w*", "zucchero"),
             (r"\b(?:senza|non) zuccher\w*", " ")]
# non si controllano: ci sono sempre, o "spezie preferite" vuol dire quelle che ci sono
_SEMPRE = re.compile(r"\bsale\b|\bpepe\b|\bacqua\b|\bspezie\b|\berbe aromatic", re.I)
_FORMATI_PASTA = {"spaghett", "penn", "fusill", "ditalin", "tortiglion", "mezz", "farfall", "gramign", "rigaton",
                  "maccheron", "linguin", "orecchiett", "paccher", "tagliatell", "bucatin", "sedanin", "conchigli"}
_FORME = {"fiocch", "gocc"}  # "avena" sta in Fiocchi d'avena, "cioccolato" in Gocce di cioccolato


def _radice_parola(parola: str) -> str:
    # pomodoro e pomodori, uovo e uova; più lunga di _radici, perché zucchina non è zucchero
    return parola[:-1] if len(parola) > 3 and parola[-1] in "aeiouàèéìòù" else parola


def _stessa(a: str, b: str) -> bool:
    corta, lunga = sorted((a, b), key=len)
    return a == b or (len(corta) >= 4 and lunga.startswith(corta) and lunga[len(corta):] in {"in", "ett", "ell"})


def _parole_ricetta(testo: str) -> tuple[list[str], set[str]]:
    """Le radici delle parole che contano e quelle che vengono subito dopo "di", "al"... (vedi _LEGANTI)."""
    testo = testo.lower()
    for vecchio, nuovo in _SINONIMI:
        testo = re.sub(vecchio, nuovo, testo)
    parole, legate, dopo_legante = [], set(), False
    for p in re.findall(r"[a-zà-ù]+", testo):
        if p in _VUOTE_RICETTE or _GENERICA.fullmatch(p):
            dopo_legante = dopo_legante if _GENERICA.fullmatch(p) else p in _LEGANTI
            continue
        parole.append(_radice_parola(p))
        if dopo_legante:
            legate.add(parole[-1])
        dopo_legante = False
    return parole, legate


def _alternative(testo: str) -> list[str]:
    """'petto di pollo o tacchino' → le due; '(o spinaci)' è un'alternativa, '(leggi le note)' no."""
    testo = testo.lower().replace("e/o", " o ")
    alternative = re.split(r"/|\bo\b", re.sub(r"\([^)]*\)", " ", testo))
    for dentro in re.findall(r"\(([^)]*)\)", testo):
        if re.match(r"\s*o\b", dentro):
            alternative += re.split(r"/|\bo\b", dentro)
    return [a for a in alternative if a.strip()]


def _c_e(sue: list[str], legate: set[str], in_casa: list[list[str]]) -> bool:
    if "poll" in sue and any("poll" in v for v in in_casa):  # pollo: un taglio vale l'altro
        return True
    if sue == ["past"]:  # "pasta": un formato qualsiasi
        return any(v[0] in _FORMATI_PASTA for v in in_casa)
    for v in in_casa:
        # tutte le sue parole nella voce ("tonno" → Tonno in scatola) con la stessa prima parola o una forma
        # (non "patate" → Insaporitore per patate) ...
        if ((_stessa(sue[0], v[0]) or v[0] in _FORME)
                and all(any(_stessa(a, b) for b in v) for a in sue)):
            return True
        # ... o tutte quelle della voce nelle sue, con la stessa prima, se le altre non fanno un altro cibo
        # ("grana padano" → Grana, "gocce di cioccolato fondente" → Gocce di cioccolato; non "burro di arachidi")
        if _stessa(sue[0], v[0]) and all(any(_stessa(b, a) for a in sue) for b in v):
            altre = [a for a in sue if not any(_stessa(a, b) for b in v)]
            if not any(a in legate for a in altre):
                return True
    return False


def mancano(ingredienti: list[str], voci: list[dict]) -> list[str]:
    """Gli ingredienti di una ricetta che non sono in dispensa (o sono finiti); sale, pepe e acqua non si controllano.
    Con le alternative ("petto di pollo o tacchino", "latte/acqua") ne basta una; un nome con le virgole
    ("capperi, olive") sono più ingredienti."""
    in_casa = [p for p, _ in (_parole_ricetta(v["nome"]) for v in voci if v["stato"] != "Finito") if p]
    fuori = []
    for ingrediente in ingredienti:
        for pezzo in re.split(r",(?![^(]*\))", ingrediente):  # non le virgole fra parentesi
            pezzo = pezzo.strip()
            if not pezzo or _SEMPRE.search(pezzo):
                continue
            # "(o preferito)" non è un'alternativa: senza parole che contano non dice quale cibo
            alternative = [p for p in map(_parole_ricetta, _alternative(pezzo)) if p[0]]
            if alternative and not any(_c_e(sue, legate, in_casa) for sue, legate in alternative):
                fuori.append(re.sub(r"\s*\([^)]*\)", "", pezzo).strip(" *"))
    return fuori


# --- Cronometer: quello che ha mangiato esce dalla dispensa ---

def _adesso() -> datetime.datetime:
    return datetime.datetime.now()


def _chiave(cibo: dict) -> str:
    return f"{cibo['nome']}|{cibo['quantita']}|{cibo['gruppo']}"


def _carica() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _salva(stato: dict):
    giorni = sorted(g for g in stato if re.fullmatch(r"\d{4}-\d\d-\d\d", g))
    for g in giorni[:-GIORNI_TENUTI]:
        del stato[g]
    FILE.write_text(json.dumps(stato, ensure_ascii=False, indent=1), encoding="utf-8")


def _grammi(quantita: str) -> tuple[float, str] | None:
    """'120.00 g' → (120, 'g'), '250 ml' → (250, 'ml'); None per 'large', 'cup' e simili."""
    m = re.fullmatch(rf"\s*{_NUMERO}\s*(g|ml|kg|l)\s*", quantita or "", re.I)
    if not m:
        return None
    unita, fattore = UNITA[m.group(2).lower()]
    return float(m.group(1).replace(",", ".")) * fattore, unita


ISTRUZIONI = """Abbina i cibi mangiati (segnati nell'app Cronometer, nomi spesso in inglese) alle voci della dispensa \
di casa da cui vengono, e di' quanto togliere dalla voce, nella SUA unità (g, ml o pz).
- Un cibo non in dispensa (pasto fuori, prodotto che non c'è nell'elenco): voce 0.
- Converti: un uovo = 1 pz; pasta, riso e legumi COTTI pesano circa 2,5 volte i secchi (100 g cotti = 40 g secchi); \
per una voce in pz conta i pezzi; per una voce in g stima i grammi.
- Abbina solo se è chiaramente lo stesso prodotto: "Milk, skim" va col latte, non con lo yogurt.

Dispensa:
{voci}

Cibi mangiati:
{cibi}"""


def _abbina(cibi: list[dict], voci: list[dict]) -> list[tuple[dict, dict | None, float]]:
    """(cibo, voce o None, quanto togliere) per ogni cibo: prima i nomi già abbinati in Notion con i grammi, poi una
    richiesta Gemini per gli altri."""
    risultato, chiedere = [], []
    for c in cibi:
        voce = next((v for v in voci if c["nome"].lower() in (n.lower() for n in v["cronometer"])), None)
        grammi = _grammi(c["quantita"])
        if voce and grammi and grammi[1] == voce["unita"]:
            risultato.append((c, voce, grammi[0]))
        else:
            chiedere.append(c)
    if chiedere:
        testo = ISTRUZIONI.format(
            voci="\n".join(f"{i}. {v['nome']} ({v['unita']})" + (f" — in Cronometer: {', '.join(v['cronometer'])}"
                                                                 if v["cronometer"] else "")
                           for i, v in enumerate(voci, 1)),
            cibi="\n".join(f"{i}. {c['nome']} — {c['quantita']}" for i, c in enumerate(chiedere, 1)))
        for riga in _gemini(testo):
            i, j = int(riga.get("cibo", 0)) - 1, int(riga.get("voce", 0)) - 1
            if 0 <= i < len(chiedere):
                togli = float(riga.get("togli") or 0)
                risultato.append((chiedere[i], voci[j] if 0 <= j < len(voci) and togli > 0 else None, togli))
                chiedere[i] = None
        risultato += [(c, None, 0.0) for c in chiedere if c is not None]  # saltati da Gemini: non in dispensa
    return risultato


_ABBINAMENTI = {"type": "array", "items": {"type": "object", "properties": {
    "cibo": {"type": "integer"}, "voce": {"type": "integer"}, "togli": {"type": "number"}},
    "required": ["cibo", "voce", "togli"]}}


def _gemini(testo: str, schema: dict = _ABBINAMENTI) -> list[dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema,
                                         thinking_config=types.ThinkingConfig(thinking_level="low"),
                                         http_options=types.HttpOptions(timeout=60000))
    scartati: set[str] = set()
    errore = "nessun modello disponibile"
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = client.models.generate_content(model=modello, contents=testo, config=config)
            quota.registra(modello, "dispensa")
            return json.loads(r.text)
        except Exception as e:
            errore = str(e)
            tipo = quota.segna_errore(modello, errore)
            quota.registra(modello, "dispensa", "429" if tipo else "altro")
            if tipo != "minuto":
                scartati.add(modello)
    raise RuntimeError(f"Gemini non risponde per la dispensa: {errore[:120]}")


def scala(giorno: datetime.date) -> str:
    """Toglie dalla dispensa i cibi di Cronometer di quel giorno non ancora scalati. '' se non c'è niente di nuovo."""
    stato = _carica()
    gia = Counter(stato.get(str(giorno), []))
    nuovi = []
    for c in cronometer.cibi(giorno):
        if gia[_chiave(c)] > 0:
            gia[_chiave(c)] -= 1
        else:
            nuovi.append(c)
    if not nuovi:
        return ""
    voci = [v for v in _leggi_voci() if (v["quantita"] or 0) > 0]  # per scrivere si legge sempre da Notion
    tolti: dict[str, float] = {}
    saltati = []  # cibi che non vengono da nessuna voce: prima si perdevano in silenzio
    if voci:
        for cibo, voce, togli in _abbina(nuovi, voci):
            if voce is None:
                saltati.append(cibo)
                continue
            tolti[voce["id"]] = tolti.get(voce["id"], 0) + togli
            if cibo["nome"].lower() not in (n.lower() for n in voce["cronometer"]):
                voce["cronometer"] = voce["cronometer"] + [cibo["nome"]]
                voce["nuovo_nome"] = True
    righe, finiti = [], []
    for voce in voci:
        if voce["id"] not in tolti and not voce.get("nuovo_nome"):
            continue
        campi = {"cronometer": voce["cronometer"]} if voce.get("nuovo_nome") else {}
        if voce["id"] in tolti:
            campi["quantita"] = round(max(voce["quantita"] - tolti[voce["id"]], 0), 1)
            unita = voce["unita"]
            righe.append(f"{voce['nome']} {_num(round(tolti[voce['id']], 1))} {unita}")
            if campi["quantita"] <= 0:
                finiti.append(voce["nome"])
        _cambia(voce, **campi)
    stato[str(giorno)] = stato.get(str(giorno), []) + [_chiave(c) for c in nuovi]
    _salva(stato)
    # il 13/10 il pranzo era una ricetta creata in Cronometer ("Pranzo", 1.00 full recipe): l'esportazione gratuita
    # ne dà solo il nome, Gemini non l'ha abbinata e la dispensa è rimasta com'era senza dirlo
    ricette = [c for c in saltati if "recipe" in c["quantita"].lower()]
    altri = [c for c in saltati if c not in ricette]
    nota = ""
    if ricette:
        nota += (f" Non ho tolto {', '.join(c['nome'] for c in ricette)}: è una ricetta creata in Cronometer e mi "
                 "arriva solo il nome. Se vuoi che la tolga, segna gli ingredienti uno per uno o dimmi cosa c'era.")
    if altri:
        nota += f" Non vengono dalla dispensa: {', '.join(c['nome'] for c in altri)}."
    if not righe:
        return f"🥫 Dalla dispensa non ho tolto niente.{nota}" if nota else ""
    return ("🥫 Dalla dispensa ho tolto: " + ", ".join(righe) + "."
            + (f" Finiti: {', '.join(finiti)}." if finiti else "") + nota)


def controlla() -> list[str]:
    """Chiamata ogni minuto dal bot: alle 21:31 scala i pasti di oggi, alle 10 quelli di ieri segnati dopo. Se
    Cronometer, Notion o Gemini non rispondono riprova ogni 10 minuti; dopo 3 errori lo dice su Telegram."""
    if not (cronometer.configurato() and configurata()) or time.time() < _errori["pausa"]:
        return []
    adesso = _adesso()
    stato = _carica()
    if "inizio" not in stato:  # i pasti di prima della dispensa non vanno tolti da una dispensa riempita dopo
        stato["inizio"] = str(adesso.date())
        _salva(stato)
    turni = []
    if adesso.time() >= MATTINA:
        turni.append(("mattina", adesso.date() - datetime.timedelta(days=1)))
    if adesso.time() >= SERALE:
        turni.append(("sera", adesso.date()))
    messaggi = []
    for turno, giorno in turni:
        if stato.get(turno) == str(adesso.date()) or str(giorno) < stato["inizio"]:
            continue
        try:
            testo = scala(giorno)
        except Exception as e:
            print(f"[dispensa] non scalata ({turno}): {e}")
            _errori["volte"] += 1
            _errori["pausa"] = time.time() + 600
            if _errori["volte"] < 3:
                return messaggi
            testo = f"🥫 Dispensa non aggiornata con i pasti di Cronometer: {e}"
        _errori["volte"] = 0
        stato = _carica()  # scala l'ha appena riscritto
        stato[turno] = str(adesso.date())
        _salva(stato)
        if testo:
            messaggi.append(testo if turno == "sera" else testo.replace("ho tolto:", "ho tolto anche (pasti di ieri "
                                                                                     "segnati tardi):"))
    return messaggi


# --- foto su Telegram: scontrino e codici a barre ---
# Lo scontrino lo riconosce foto.py (tipo "scontrino") e ne trascrive il testo; i codici a barre si leggono qui, in
# locale con zxing-cpp, e i prodotti si cercano su Open Food Facts (gratis, senza chiave). In tutti e due i casi
# Gemini dà nomi generici in italiano (gli stessi della dispensa se il prodotto c'è già) e l'utente conferma con i
# pulsanti prima che qualcosa entri in Notion: sugli scontrini le sigle sono criptiche.

FORMATI = {"EAN-13", "EAN-8", "UPC-A", "UPC-E"}  # i codici dei prodotti: QR e Code 128 degli scontrini no
OFF = "https://world.openfoodfacts.org/api/v2/product/{}.json"
_OFF_CAMPI = "product_name_it,product_name,generic_name_it,brands,quantity,categories_tags"
IN_ATTESA = 10  # liste da confermare tenute in dispensa.json
ESTRAI = """Dal testo qui sotto ({fonte}) ricava i prodotti da mangiare o da bere comprati, per la dispensa di casa.
- nome: generico, in italiano, come lo direbbe una persona ("Petto di pollo", "Spaghetti", "Latte parzialmente \
scremato", "Passata di pomodoro"), senza marca né sigle. Se il prodotto è già in dispensa usa ESATTAMENTE quel nome.
- quantita e unita (g, ml o pz): il totale comprato (2 confezioni da 500 g = 1000 g), dal peso scritto; se non c'è \
stima una confezione normale. Uova, frutta e verdura a pezzi, scatolette e vasetti a pezzi.
- dove: frigo, freezer o dispensa.
- Salta quello che non si mangia (detersivi, igiene, sacchetti), sconti, totali, pagamento e righe illeggibili.

Già in dispensa: {voci}

Testo:
{testo}"""
_PRODOTTI = {"type": "array", "items": {"type": "object", "properties": {
    "nome": {"type": "string"}, "quantita": {"type": "number"},
    "unita": {"type": "string", "enum": ["g", "ml", "pz"]},
    "dove": {"type": "string", "enum": ["frigo", "freezer", "dispensa"]}},
    "required": ["nome", "quantita", "unita", "dove"]}}


def codici(dati: bytes) -> list[str]:
    """I codici a barre dei prodotti nella foto (anche più di uno), senza doppioni."""
    import zxingcpp
    from PIL import Image, ImageOps

    try:
        immagine = ImageOps.exif_transpose(Image.open(io.BytesIO(dati)))
    except Exception:
        return []
    return list(dict.fromkeys(c.text for c in zxingcpp.read_barcodes(immagine) if str(c.format) in FORMATI))


def _open_food_facts(codice: str) -> str | None:
    """Il prodotto in una riga ('Spaghetti n.5 (Barilla) — 500 g — ...'); None se Open Food Facts non lo conosce."""
    r = requests.get(OFF.format(codice), params={"fields": _OFF_CAMPI}, timeout=20,
                     headers={"User-Agent": "Jarvis/1.0 (assistente personale)"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    dati = r.json()
    p = dati.get("product") or {}
    nome = p.get("product_name_it") or p.get("product_name") or p.get("generic_name_it")
    if dati.get("status") != 1 or not nome:
        return None
    categorie = [c.split(":", 1)[-1].replace("-", " ") for c in (p.get("categories_tags") or [])[-4:]]
    return " — ".join(x for x in [f"{nome} ({p['brands']})" if p.get("brands") else nome, p.get("quantity"),
                                  p.get("generic_name_it"), "categorie: " + ", ".join(categorie) if categorie else ""] if x)


def _estrai(testo: str, fonte: str) -> list[dict]:
    try:
        nomi = ", ".join(v["nome"] for v in _voci()) or "niente"
    except Exception:
        nomi = "(non letta)"
    prodotti = _gemini(ESTRAI.format(fonte=fonte, voci=nomi, testo=testo), _PRODOTTI)
    return [{"nome": p["nome"].strip()[:60], "quantita": round(float(p["quantita"]), 1), "unita": p["unita"],
             "dove": p["dove"].capitalize()} for p in prodotti if p.get("nome", "").strip() and p.get("quantita")]


def _etichetta(p: dict) -> str:
    return f"{p['nome']} · {_quanto(p)} · {p['dove'].lower()}"


def _pulsanti(id: str, attesa: dict) -> list:
    righe = [[(("✅ " if i in attesa["scelti"] else "⬜ ") + _etichetta(p), f"dis:t:{id}:{i}")]
             for i, p in enumerate(attesa["prodotti"])]
    return righe + [[("➕ Aggiungi", f"dis:ok:{id}"), ("❌ Annulla", f"dis:no:{id}")]]


def _proponi(prodotti: list[dict], intestazione: str) -> tuple[str, list]:
    """Il messaggio con un pulsante per prodotto (tutti scelti all'inizio) e la lista messa in attesa."""
    id = uuid.uuid4().hex[:8]
    stato = _carica()
    attesa = stato.setdefault("attesa", {})
    attesa[id] = {"prodotti": prodotti, "scelti": list(range(len(prodotti))), "quando": _adesso().isoformat()}
    for vecchio in sorted(attesa, key=lambda k: attesa[k]["quando"])[:-IN_ATTESA]:
        del attesa[vecchio]
    _salva(stato)
    return (f"{intestazione}\nTocca quelli da lasciare fuori, poi ➕ Aggiungi. Le quantità le puoi correggere dopo, "
            "a parole o su Notion."), _pulsanti(id, attesa[id])


def da_scontrino(trascrizione: str) -> tuple[str, list] | None:
    """I prodotti di uno scontrino da confermare; None se non c'è niente da mangiare (non è una spesa)."""
    prodotti = _estrai(trascrizione, "lo scontrino di una spesa, con le sigle del supermercato")
    if not prodotti:
        return None
    return _proponi(prodotti, f"🧾 Dallo scontrino ho letto {len(prodotti)} prodotti da mangiare.")


def da_codici(trovati: list[str]) -> tuple[str, list]:
    """I prodotti dei codici a barre da confermare; quelli che Open Food Facts non conosce si dicono a parte."""
    righe, sconosciuti = [], []
    for codice in trovati:
        try:
            riga = _open_food_facts(codice)
        except Exception as e:
            print(f"[dispensa] Open Food Facts non risponde per {codice}: {e}")
            riga = None
        if riga:
            righe.append(riga)
        else:
            sconosciuti.append(codice)
    fuori = (f"\nNon trovati su Open Food Facts: {', '.join(sconosciuti)}. Dimmi tu cosa sono (es. \"ho comprato "
             "...\").") if sconosciuti else ""
    prodotti = _estrai("\n".join(righe), "prodotti trovati con il codice a barre, uno per riga") if righe else []
    if not prodotti:
        return f"🔎 Codice a barre letto, ma non so che prodotto è.{fuori}", []
    testo, pulsanti = _proponi(prodotti, f"🔎 Dal codice a barre: {len(prodotti)} prodott{'o' if len(prodotti) == 1 else 'i'}.")
    return testo + fuori, pulsanti


def pulsante(dati: str) -> tuple[str | None, list]:
    """Un pulsante della lista ('dis:t:id:3' scegli/togli, 'dis:ok:id', 'dis:no:id'). (None, pulsanti) = cambia solo i
    pulsanti; (testo, []) = la lista è chiusa e il testo si aggiunge al messaggio."""
    parti = dati.split(":")
    azione, id = parti[1], parti[2]
    stato = _carica()
    attesa = stato.get("attesa", {}).get(id)
    if attesa is None:
        return "Questa lista non c'è più: rimanda la foto.", []
    if azione == "t":
        i = int(parti[3])
        attesa["scelti"] = sorted(set(attesa["scelti"]) ^ {i})
        _salva(stato)
        return None, _pulsanti(id, attesa)
    del stato["attesa"][id]
    _salva(stato)
    scelti = [p for i, p in enumerate(attesa["prodotti"]) if i in attesa["scelti"]]
    if azione == "no" or not scelti:
        return "❌ Niente aggiunto alla dispensa.", []
    try:
        return "➕ " + _aggiungi(scelti), []
    except Exception as e:
        stato = _carica()
        stato.setdefault("attesa", {})[id] = attesa  # si può ripremere
        _salva(stato)
        return f"Notion non risponde ({e}): riprova fra poco.", _pulsanti(id, attesa)


STRUMENTI = [dispensa, aggiorna_dispensa]
