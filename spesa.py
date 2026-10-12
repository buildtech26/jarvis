"""Consigli per la spesa: le poche cose da comprare che sbloccano più ricette di Fit with Fun con quello che c'è già in
dispensa, più quello che sta finendo. Su richiesta (consigli_spesa) e il sabato alle 9 su Telegram (controlla).

Le ricette si cercano sul sito con gli ingredienti principali che sono in casa (pollo, tonno, ceci...); per ognuna
dispensa.mancano dice cosa manca. Contano quelle a cui mancano al massimo 2 ingredienti, senza proteine in polvere,
prodotti di marca o ingredienti cari da comprare (alimentazione.CARI: budget medio-basso); gli acquisti si scelgono uno alla volta, ogni volta quello che completa più ricette.
"""
import datetime
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import alimentazione
import dispensa

FILE = Path(__file__).with_name("spesa.json")  # il giorno dell'ultimo messaggio del sabato
GIORNO, ORA = 5, datetime.time(9)  # sabato alle 9
ACQUISTI = 5
MAX_MANCANTI = 2
# gli ingredienti con cui si cercano le ricette, se sono in casa (le parole che il sito trova)
CERCHE = ["pollo", "tonno", "ceci", "lenticchie", "fagioli", "uova", "pasta", "riso", "macinato", "avena", "spinaci",
          "patate", "salsiccia", "maiale", "melanzana", "carote", "funghi", "mozzarella", "cous cous", "gnocchi",
          "speck", "piadina"]
# ricette scartate se manca uno di questi: proteine in polvere e prodotti di marca, non roba da supermercato
SCARTA = re.compile(r"protein|yummy|nutella|pocket coffee|choco|biscuit|dessert proteic|philadelphia protein", re.I)
_errori = {"pausa": 0.0, "volte": 0}


def _adesso() -> datetime.datetime:
    return datetime.datetime.now()


def _ricette(voci: list[dict]) -> list[dict]:
    """Le ricette del sito trovate con gli ingredienti in casa, adatte a un pasto, una volta sola ciascuna."""
    cerche = [c for c in CERCHE if not dispensa.mancano([c], voci)]
    with ThreadPoolExecutor(6) as pool:
        trovate = pool.map(lambda c: alimentazione._cerca_ricette(c, 30), cerche)
    ricette = {}
    for elenco in trovate:
        for r in elenco:
            if any(alimentazione._adatta(r, p) for p in alimentazione.PORTATE):
                ricette.setdefault(r["id"], r)
    return list(ricette.values())


def _chiave(testo: str) -> tuple[str, ...]:
    return tuple(dispensa._parole_ricetta(testo)[0])


def _nome(testo: str) -> str:
    """'yogurt greco 0%' → 'yogurt greco'."""
    return " ".join(re.sub(r"\d+(?:[.,]\d+)?\s*%?|\*", " ", testo).split()).strip(" .,-")


def _proposta(voci: list[dict]) -> dict:
    """Gli acquisti consigliati: {"pronte": ricette con tutto in casa, "acquisti": [(nome, [ricette])]}."""
    ricette = _ricette(voci)
    pronte, vicine = [], []  # vicine: (ricetta, mancanti)
    for r in ricette:
        fuori = alimentazione._non_in_casa(alimentazione._ingredienti(r), voci) or []
        if not fuori:
            pronte.append(r)
        elif len(fuori) <= MAX_MANCANTI and not any(SCARTA.search(f) or alimentazione.CARI.search(f) for f in fuori):
            vicine.append((r, fuori))
    # ogni ingrediente mancante diventa una chiave; con le alternative ("skyr o yogurt greco") vince la più comune
    conta = Counter(_chiave(a) for _, fuori in vicine for f in fuori for a in dispensa._alternative(f))
    nomi: dict[tuple, list[str]] = {}
    servono = []  # (ricetta, insieme di chiavi)
    for r, fuori in vicine:
        chiavi = set()
        for f in fuori:
            alternative = [a for a in dispensa._alternative(f) if _chiave(a)]
            if not alternative:
                continue
            scelta = max(alternative, key=lambda a: conta[_chiave(a)])
            chiavi.add(_chiave(scelta))
            nomi.setdefault(_chiave(scelta), []).append(_nome(scelta))
        if chiavi:
            servono.append((r, chiavi))
    scelte: list[tuple] = []
    for _ in range(ACQUISTI):
        candidate = {k for _, chiavi in servono for k in chiavi} - set(scelte)

        def punti(k):
            completate = sum(chiavi <= set(scelte) | {k} for _, chiavi in servono)
            return completate, sum(k in chiavi for _, chiavi in servono)

        if not candidate:
            break
        migliore = max(candidate, key=punti)
        if punti(migliore)[1] < 2 and len(scelte) >= 3:  # oltre i primi 3, solo cose che servono ad almeno 2 ricette
            break
        scelte.append(migliore)
    completate = [(r, chiavi) for r, chiavi in servono if chiavi <= set(scelte)]
    acquisti = []
    for k in scelte:
        sue = [r for r, chiavi in completate if k in chiavi]
        if sue:
            acquisti.append((min(nomi[k], key=len), sue))
    acquisti.sort(key=lambda x: -len(x[1]))
    return {"pronte": pronte, "acquisti": acquisti}


def _testo(voci: list[dict], proposta: dict) -> str:
    righe = ["🛒 Consigli per la spesa (ricette di Fit with Fun con quello che hai già)"]
    if proposta["acquisti"]:
        righe.append("Da comprare:")
        for nome, ricette in proposta["acquisti"]:
            esempi = ", ".join(alimentazione._html(r["name"]).split(":")[0].split(" – ")[0] for r in ricette[:3])
            righe.append(f"• {nome[:1].upper() + nome[1:]}: {len(ricette)} "
                         f"ricett{'a' if len(ricette) == 1 else 'e'} in più ({esempi})")
    else:
        righe.append("Non c'è niente che sblocchi ricette nuove: con la dispensa di adesso va bene così.")
    finendo = [v["nome"] for v in voci if v["stato"] == "Sta finendo"]
    finiti = [v["nome"] for v in voci if v["stato"] == "Finito"]
    if finendo:
        righe.append("Stanno finendo: " + ", ".join(finendo))
    if finiti:
        righe.append("Finiti: " + ", ".join(finiti[:15]) + (" e altri" if len(finiti) > 15 else ""))
    righe.append(f"Con quello che hai già puoi fare {len(proposta['pronte'])} ricette del sito: chiedimi "
                 "\"cosa mangio stasera\".")
    return "\n".join(righe)


def consigli_spesa() -> str:
    """Cosa comprare al supermercato: le poche cose che, con quello che c'è già in dispensa, permettono più ricette di
    Fit with Fun (con le ricette che sbloccano), quello che sta finendo e quello che è finito. Per "cosa compro",
    "cosa devo prendere al supermercato", "consigli per la spesa", "lista della spesa"."""
    voci = alimentazione._voci_dispensa()
    if voci is None:
        return "La dispensa su Notion è vuota o non si legge: senza sapere cosa c'è in casa non posso consigliare la spesa."
    return _testo(voci, _proposta(voci))


# --- il sabato alle 9 su Telegram ---

def _carica() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def controlla() -> list[str]:
    """Chiamata ogni minuto dal bot: il sabato dalle 9 (o appena Jarvis è acceso dopo) i consigli, una volta. Se Notion o
    il sito non rispondono riprova ogni 10 minuti; dopo 3 errori lo dice su Telegram."""
    adesso = _adesso()
    if (adesso.weekday() != GIORNO or adesso.time() < ORA or not dispensa.configurata()
            or time.time() < _errori["pausa"]):
        return []
    oggi, stato = str(adesso.date()), _carica()
    if stato.get("sabato") == oggi:
        return []
    try:
        voci = alimentazione._voci_dispensa()
        if voci is None:
            raise RuntimeError("dispensa su Notion vuota o non letta")
        testo = _testo(voci, _proposta(voci))
    except Exception as e:
        print(f"[spesa] consigli non fatti: {e}")
        _errori["volte"] += 1
        _errori["pausa"] = time.time() + 600
        if _errori["volte"] < 3:
            return []
        testo = f"🛒 Consigli per la spesa non fatti: {e}"
    _errori["volte"] = 0
    stato["sabato"] = oggi
    FILE.write_text(json.dumps(stato), encoding="utf-8")
    return [testo]


STRUMENTI = [consigli_spesa]
