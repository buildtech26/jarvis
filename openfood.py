"""Kcal e proteine di un ingrediente da Open Food Facts (search.openfoodfacts.org, senza chiave), per calcola_ricetta.

Open Food Facts ha i prodotti confezionati, ognuno con i suoi valori: per un ingrediente generico ("mozzarella light",
"pasta integrale") si prendono i prodotti che si chiamano così e si usa la MEDIANA, così un prodotto sbagliato o
farcito non sposta il risultato. I cibi freschi (pollo crudo, verdure) ci sono poco e male (per "petto di pollo"
escono gli affettati cotti): per quelli resta la tabella della nota, vedi alimentazione.calcola_ricetta.
I risultati restano in openfood.json (fuori da git) per GIORNI giorni: lo stesso ingrediente non si cerca due volte.
"""
import datetime
import json
import re
import statistics
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

API = "https://search.openfoodfacts.org/search"
FILE = Path(__file__).with_name("openfood.json")
GIORNI = 90
MINIMO = 3  # prodotti che servono per fidarsi della mediana
_VUOTE = {"di", "da", "del", "dello", "della", "dei", "degli", "delle", "al", "allo", "alla", "ai", "agli", "alle",
          "con", "in", "e", "il", "lo", "la", "le", "gli", "un", "una", "uno", "per"}
_lock = threading.Lock()


def _semplice(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", testo.lower())
    return "".join(c for c in testo if not unicodedata.combining(c))


def _radici(testo: str) -> list[str]:
    return [p[:max(3, min(len(p) - 1, 5))] for p in re.findall(r"[a-z]{3,}", _semplice(testo)) if p not in _VUOTE]


def _adatto(nome: str, prodotto: str) -> bool:
    """Il prodotto si chiama come l'ingrediente: ci sono tutte le sue parole, la prima entro le prime tre parole del
    nome (prima può esserci la marca: "Santa Lucia Mozzarella Light") e non dopo un "con" ("Focaccelle con olio")."""
    sue = _radici(nome)
    parole = re.findall(r"[a-z]+", _semplice(prodotto))

    def uguale(radice: str, parola: str) -> bool:  # "extravergine" e "extra vergine"
        sua = _radici(parola) or [""]
        return parola.startswith(radice) or (len(sua[0]) >= 4 and radice.startswith(sua[0]))

    if not sue or not all(any(uguale(s, p) for p in parole) for s in sue):
        return False
    prima = next(i for i, p in enumerate(parole) if uguale(sue[0], p))
    return prima <= 2 and not _VUOTE & set(parole[:prima])  # "Sugo per pasta", "Filetti di tonno": altro


def _plausibile(kcal, proteine) -> bool:
    try:
        kcal, proteine = float(kcal), float(proteine)
    except (TypeError, ValueError):
        return False
    return 0 <= kcal <= 900 and 0 <= proteine <= 100 and proteine * 4 <= kcal * 1.1 + 5


def _cerca(nome: str) -> dict | None:
    r = requests.get(API, timeout=15, headers={"User-Agent": "Jarvis-personale/1.0"},
                     params={"q": nome, "page_size": 50, "langs": "it", "fields": "product_name,nutriments"})
    r.raise_for_status()
    buoni = []
    for p in r.json().get("hits") or []:
        n = p.get("nutriments") or {}
        k, pr = n.get("energy-kcal_100g"), n.get("proteins_100g")
        if p.get("product_name") and _adatto(nome, p["product_name"]) and _plausibile(k, pr):
            buoni.append((float(k), float(pr), p["product_name"]))
    if len(buoni) < MINIMO:
        return None
    return {"kcal": statistics.median(b[0] for b in buoni), "proteine": statistics.median(b[1] for b in buoni),
            "prodotti": len(buoni), "esempio": buoni[0][2]}


def _carica() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def valori(nomi: list[str]) -> dict[str, dict | None]:
    """{nome: {"kcal", "proteine" (per 100 g), "prodotti", "esempio"} o None se non c'è}. Gli errori di rete danno
    None senza scriverlo in memoria, così la volta dopo si riprova."""
    oggi = datetime.date.today()
    with _lock:
        memoria = _carica()
    risultati, da_cercare = {}, []
    for nome in dict.fromkeys(n.strip().lower() for n in nomi if n.strip()):
        v = memoria.get(nome)
        if v and (oggi - datetime.date.fromisoformat(v["giorno"])).days < GIORNI:
            risultati[nome] = v["valori"]
        else:
            da_cercare.append(nome)

    def cerca(nome):
        try:
            return nome, _cerca(nome), True
        except (requests.RequestException, ValueError) as e:
            print(f"[openfood] {nome}: {e}")
            return nome, None, False

    if da_cercare:
        with ThreadPoolExecutor(4) as pool:
            trovati = list(pool.map(cerca, da_cercare))
        with _lock:
            memoria = _carica()
            for nome, v, ok in trovati:
                risultati[nome] = v
                if ok:
                    memoria[nome] = {"giorno": str(oggi), "valori": v}
            FILE.write_text(json.dumps(memoria, ensure_ascii=False, indent=1), encoding="utf-8")
    return risultati
