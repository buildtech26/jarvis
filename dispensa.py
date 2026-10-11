"""La dispensa: il cibo che c'è in casa, nel database Notion "Dispensa", per le ricette con quello che c'è.

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
import datetime
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

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
    notion._chiama("PATCH", f"/pages/{voce['id']}", properties=_proprieta(**campi))
    voce.update(campi)


def _crea(**campi) -> dict:
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
    voci, fatti, problemi = _voci(), [], []
    for prodotto in elenco:
        voce = _trova(prodotto["nome"], voci)
        q, u = prodotto["quantita"], prodotto["unita"]
        if azione == "aggiungi":
            if voce is None:
                campi = {"nome": prodotto["nome"], "quantita": q or 1, "unita": u or "pz",
                         "dove": prodotto["dove"] or "Dispensa"}
                voce = _crea(**campi, acquisto=q or 1)
                voci.append(voce)
                fatti.append(f"{voce['nome']}: nuovo, {_quanto(voce)} in {voce['dove'].lower()}")
                continue
            if q is None:  # "ho ricomprato il latte": torna alla quantità dell'ultima spesa
                q, u = voce["acquisto"] or 1, voce["unita"]
            prima = max(voce["quantita"] or 0, 0) if u == voce["unita"] else 0
            campi = {"quantita": prima + q, "unita": u, "acquisto": prima + q}
            if prodotto["dove"]:
                campi["dove"] = prodotto["dove"]
            _cambia(voce, **campi)
            fatti.append(f"{voce['nome']}: ora {_quanto(voce)}")
            continue
        if voce is None:
            problemi.append(prodotto["nome"])
            continue
        if azione == "elimina":
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


def in_casa() -> str:
    """Una riga per consigli_alimentazione: quello che c'è, quello che sta finendo. '' se la dispensa è vuota."""
    voci = [v for v in _voci() if v["stato"] != "Finito"]
    if not voci:
        return ""
    finendo = [v["nome"] for v in voci if v["stato"] == "Sta finendo"]
    return ("In casa (dispensa su Notion, quantità indicative): " + "; ".join(f"{v['nome']} {_quanto(v)}" for v in voci)
            + "." + (f" Stanno finendo: {', '.join(finendo)}." if finendo else ""))


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


def _gemini(testo: str) -> list[dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    schema = {"type": "array", "items": {"type": "object", "properties": {
        "cibo": {"type": "integer"}, "voce": {"type": "integer"}, "togli": {"type": "number"}},
        "required": ["cibo", "voce", "togli"]}}
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
    raise RuntimeError(f"Gemini non riesce ad abbinare i cibi alla dispensa: {errore[:120]}")


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
    voci = [v for v in _voci() if (v["quantita"] or 0) > 0]
    tolti: dict[str, float] = {}
    if voci:
        for cibo, voce, togli in _abbina(nuovi, voci):
            if voce is None:
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
    if not righe:
        return ""
    return ("🥫 Dalla dispensa ho tolto: " + ", ".join(righe) + "."
            + (f" Finiti: {', '.join(finiti)}." if finiti else ""))


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


STRUMENTI = [dispensa, aggiorna_dispensa]
