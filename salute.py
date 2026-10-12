"""I dati di Apple Salute (Moto Watch 120 e iPhone): passi, distanza, calorie, battito, HRV, ossigeno, respirazione,
peso, sonno con le fasi e allenamenti. Strumento dati_salute.

Salute non ha un'API: un Comando Rapido sull'iPhone (automazione, gira solo a telefono sbloccato) legge i dati e crea
una riga nel database Notion "Salute" (creato il 2026-10-12). Ogni riga ha:
- Giorno: "2026-10-12"
- Dati: "passi=8.123;distanza=6,2;battito_medio=78;..." (i dati di oggi fino all'invio; numeri all'italiana)
- Sonno: i campioni del sonno delle ultime 24 ore, "Fase,23:40,01:05|Fase,01:05,02:30|..." in ordine di inizio
- Allenamenti: quelli di oggi, "Tipo,07:10,durata,distanza,calorie|..."
Il Comando gira più volte al giorno: vale l'ultima riga di ogni giorno, le vecchie vanno nel cestino. Il formato è
tollerante (unità attaccate ai numeri, fasi in italiano o inglese, date intere al posto delle ore) perché il Comando
lo costruisce l'utente a mano: le prove sono in prova_salute.py.
"""
import datetime
import os
import re

import notion

SALUTE = os.getenv("NOTION_SALUTE", "f0118a2a-cd14-42db-8908-45246c7150b3").strip()  # data source
VECCHI = datetime.timedelta(hours=12)  # oltre: avvisa che l'iPhone non manda dati da un po'
SESSIONE = 120  # minuti: due pezzi di sonno più lontani di così sono due sonni diversi (la notte e un pisolino)
_GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
_CODICI = {"0": "letto", "1": "sonno", "2": "sveglio", "3": "leggero", "4": "profondo", "5": "rem"}


def _adesso() -> datetime.datetime:
    return datetime.datetime.now()


# --- lettura delle righe ---

def _numero(testo: str) -> float | None:
    """'8.123' → 8123, '6,2 km' → 6.2, '97%' → 97; le cifre all'italiana dell'iPhone, ma anche '6.2'."""
    m = re.search(r"-?\d[\d.,]*", testo or "")
    if not m:
        return None
    t = m.group().rstrip(".,")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _dati(testo: str) -> dict[str, float]:
    dati = {}
    for pezzo in re.split(r"[;\n]", testo or ""):
        chiave, _, valore = pezzo.partition("=")
        chiave = re.sub(r"\W+", "_", chiave.strip().lower()).strip("_")
        n = _numero(valore)
        if chiave and n:  # 0 o vuoto: il dato quel giorno non c'è (orologio non messo)
            dati[chiave] = n
    if "ossigeno" in dati and dati["ossigeno"] <= 1:  # 0,97 → 97%
        dati["ossigeno"] *= 100
    if "distanza" in dati and dati["distanza"] > 100:  # in metri
        dati["distanza"] /= 1000
    return dati


def _fase(testo: str) -> str:
    t = testo.lower().strip()
    if t in _CODICI:  # i codici di HealthKit, se il Comando manda il numero invece del nome
        return _CODICI[t]
    for fase, schema in [("letto", r"bed|letto"), ("sveglio", r"awake|svegl"), ("rem", r"\brem\b"),
                         ("profondo", r"deep|profond"), ("leggero", r"core|princip|nucleo|legger|light")]:
        if re.search(schema, t):
            return fase
    return "sonno"  # "Addormentato", "Asleep", "Sonno": sonno senza fasi


def _ora(testo: str, giorno: datetime.date | None = None) -> tuple[datetime.date | None, int] | None:
    """(data se scritta, minuti dalla mezzanotte)."""
    m = re.search(r"(\d{1,2})[:.](\d{2})", testo)
    if not m:
        return None
    d = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})/(\d{1,2})/(\d{2,4})", testo)
    data = None
    if d and d.group(1):
        data = datetime.date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
    elif d:
        anno = int(d.group(6))
        data = datetime.date(anno + 2000 if anno < 100 else anno, int(d.group(5)), int(d.group(4)))
    return data, int(m.group(1)) * 60 + int(m.group(2))


def _pezzi_sonno(testo: str, inviato: datetime.datetime) -> list[tuple[str, datetime.datetime, datetime.datetime]]:
    """I pezzi di sonno con le date: se il Comando manda solo le ore, i pezzi sono in ordine di inizio e l'ultimo
    finisce prima dell'invio, quindi le date si ricavano contando le mezzenotti a ritroso dall'invio."""
    grezzi = []
    for pezzo in re.split(r"[|\n]", testo or ""):
        campi = [c.strip() for c in pezzo.split(",")]
        ore = [o for o in (_ora(c) for c in campi[1:]) if o]
        if len(ore) >= 2:
            grezzi.append((_fase(campi[0]), ore[0], ore[1]))
    # dal più recente: si gira. Nell'ordine giusto gli inizi vanno avanti sull'orologio per meno di un giro
    giro = lambda inizi: sum((b - a) % 1440 for a, b in zip(inizi, inizi[1:]))
    inizi = [o[1] for _, o, _ in grezzi]
    if giro(inizi[::-1]) < giro(inizi):
        grezzi.reverse()
    pezzi, relativi = [], []
    giorno, prima = 0, None
    for fase, (d1, m1), (d2, m2) in grezzi:
        if d1 and d2:
            inizio = datetime.datetime.combine(d1, datetime.time()) + datetime.timedelta(minutes=m1)
            fine = datetime.datetime.combine(d2, datetime.time()) + datetime.timedelta(minutes=m2)
            pezzi.append((fase, inizio, fine))
            continue
        if prima is not None and m1 < prima - 60:  # passata la mezzanotte
            giorno += 1
        prima = m1
        inizio = giorno * 1440 + m1
        relativi.append((fase, inizio, inizio + (m2 - m1) % 1440))
    if relativi:
        ultimo = max(f for _, _, f in relativi)
        for indietro in range(4):
            base = datetime.datetime.combine(inviato.date() - datetime.timedelta(days=indietro), datetime.time())
            if base + datetime.timedelta(minutes=ultimo) <= inviato + datetime.timedelta(minutes=10):
                break
        pezzi += [(fase, base + datetime.timedelta(minutes=i), base + datetime.timedelta(minutes=f))
                  for fase, i, f in relativi]
    return sorted(pezzi, key=lambda p: p[1])


def _minuti(intervalli: list[tuple[datetime.datetime, datetime.datetime]]) -> float:
    """Minuti coperti, senza contare due volte le sovrapposizioni (orologio e iPhone scrivono insieme)."""
    totale, fine = 0.0, None
    for i, f in sorted(intervalli):
        if fine is None or i > fine:
            totale += (f - i).total_seconds() / 60
            fine = f
        elif f > fine:
            totale += (f - fine).total_seconds() / 60
            fine = f
    return totale


def _sonni(pezzi: list) -> list[dict]:
    """I sonni (la notte, i pisolini): pezzi vicini insieme. Con le sole righe "a letto" (iPhone senza orologio) il
    sonno è il tempo a letto."""
    dormiti = [p for p in pezzi if p[0] not in ("letto", "sveglio")] or [p for p in pezzi if p[0] == "letto"]
    gruppi: list[list] = []
    for p in dormiti:
        if gruppi and (p[1] - max(q[2] for q in gruppi[-1])).total_seconds() / 60 <= SESSIONE:
            gruppi[-1].append(p)
        else:
            gruppi.append([p])
    sonni = []
    for g in gruppi:
        inizio, fine = g[0][1], max(p[2] for p in g)
        fasi = {fase: _minuti([(p[1], p[2]) for p in g if p[0] == fase]) for fase in ("profondo", "rem", "leggero")}
        svegli = [(max(p[1], inizio), min(p[2], fine)) for p in pezzi if p[0] == "sveglio" and p[1] < fine and p[2] > inizio]
        sonni.append({"inizio": inizio, "fine": fine, "dormito": _minuti([(p[1], p[2]) for p in g]),
                      "fasi": {k: v for k, v in fasi.items() if v}, "sveglio": _minuti(svegli),
                      "solo_letto": g[0][0] == "letto"})
    return sonni


def _allenamenti(testo: str) -> list[str]:
    """'Corsa,07:10,1920,5,1,310' non si legge bene a virgole (5,1 km): si tiene il tipo e l'ora, poi il resto."""
    fatti = []
    for pezzo in re.split(r"[|\n]", testo or ""):
        pezzo = pezzo.strip(" ,")
        if not pezzo:
            continue
        tipo, _, resto = pezzo.partition(",")
        ora = _ora(resto)
        descrizione = tipo.strip()
        if ora:
            descrizione += f" alle {ora[1] // 60:02d}:{ora[1] % 60:02d}"
            resto = re.sub(r"^\s*(?:[\d/-]+\s+)?\d{1,2}[:.]\d{2}(?::\d{2})?\s*,?", "", resto)
        resto = resto.strip(" ,")
        if resto:
            descrizione += f" ({resto})"
        fatti.append(descrizione)
    return fatti


def _riga(p: dict) -> dict:
    props = p["properties"]
    testo = lambda nome: "".join(t["plain_text"] for t in (props.get(nome) or {}).get("rich_text")
                                 or (props.get(nome) or {}).get("title") or [])
    inviato = datetime.datetime.fromisoformat(p["created_time"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    giorno = _ora("00:00 " + testo("Giorno"))
    return {"id": p["id"], "giorno": (giorno and giorno[0]) or inviato.date(), "inviato": inviato,
            "dati": _dati(testo("Dati")), "pezzi": _pezzi_sonno(testo("Sonno"), inviato),
            "allenamenti": _allenamenti(testo("Allenamenti"))}


def _righe(dal: datetime.date) -> list[dict]:
    """Le righe mandate dal giorno prima di `dal` (il sonno della notte prima), dalla più vecchia."""
    righe, cursore = [], None
    inizio = datetime.datetime.combine(dal - datetime.timedelta(days=1), datetime.time()).astimezone()
    while True:
        corpo = {"page_size": 100, "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
                 "filter": {"timestamp": "created_time", "created_time": {"on_or_after": inizio.isoformat()}}}
        if cursore:
            corpo["start_cursor"] = cursore
        r = notion._chiama("POST", f"/data_sources/{SALUTE}/query", **corpo)
        righe += [_riga(p) for p in r["results"]]
        if not r.get("has_more"):
            return righe
        cursore = r["next_cursor"]


def _per_giorno(righe: list[dict]) -> tuple[dict, list[str]]:
    """{giorno: {"dati", "allenamenti", "inviato", "sonno"}} con l'ultima riga di ogni giorno, e gli id delle righe
    superate. Il sonno va al giorno in cui ci si sveglia, preso dall'ultima riga che lo contiene (la più completa)."""
    giorni, superate = {}, []
    for r in righe:  # dalla più vecchia: le più recenti sovrascrivono
        if r["giorno"] in giorni:
            superate.append(giorni[r["giorno"]]["id"])
        sonno = giorni.get(r["giorno"], {}).get("sonno")
        giorni[r["giorno"]] = {"id": r["id"], "dati": r["dati"], "allenamenti": r["allenamenti"],
                               "inviato": r["inviato"], "sonno": sonno}
    sonni: dict[datetime.date, dict] = {}
    for r in righe:
        for s in _sonni(r["pezzi"]):
            d = s["fine"].date()
            # la notte e non il pisolino; e l'invio che la contiene tutta (dopo mezzanotte le 24 ore ne tagliano l'inizio)
            if d not in sonni or s["dormito"] >= sonni[d]["dormito"]:
                sonni[d] = s
    for d, s in sonni.items():
        giorni.setdefault(d, {"id": None, "dati": {}, "allenamenti": [], "inviato": None, "sonno": None})["sonno"] = s
    return giorni, superate


# --- testo ---

def _n(x: float, cifre: int = 0) -> str:
    testo = f"{x:,.{cifre}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return testo


def _durata(minuti: float) -> str:
    h, m = divmod(round(minuti), 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def _data(d: datetime.date) -> str:
    return f"{_GIORNI[d.weekday()]} {d.day}/{d.month}"


def _testo_sonno(s: dict) -> str:
    cosa = "a letto" if s["solo_letto"] else "dormite"
    testo = f"{_durata(s['dormito'])} {cosa}, dalle {s['inizio']:%H:%M} alle {s['fine']:%H:%M}"
    fasi = [f"{nome} {_durata(s['fasi'][k])}" for k, nome in
            [("profondo", "profondo"), ("rem", "REM"), ("leggero", "leggero")] if k in s["fasi"]]
    if s["sveglio"]:
        fasi.append(f"sveglio {_durata(s['sveglio'])}")
    return testo + (f" ({', '.join(fasi)})" if fasi else "")


def _testo_dati(d: dict) -> list[str]:
    righe = []
    movimento = []
    if "passi" in d:
        movimento.append(f"{_n(d['passi'])} passi")
    if "distanza" in d:
        movimento.append(f"{_n(d['distanza'], 1)} km")
    if "piani" in d:
        movimento.append(f"{_n(d['piani'])} piani")
    if "esercizio" in d:
        movimento.append(f"{_n(d['esercizio'])} min di esercizio")
    if movimento:
        righe.append("Movimento: " + ", ".join(movimento))
    calorie = [f"{_n(d[k])} {nome}" for k, nome in [("calorie_attive", "attive"), ("calorie_riposo", "a riposo")]
               if k in d]
    if calorie:
        righe.append("Calorie bruciate: " + " + ".join(calorie) + " kcal")
    cuore = []
    if "battito_medio" in d:
        cuore.append(f"medio {_n(d['battito_medio'])}")
    if "battito_min" in d and "battito_max" in d:
        cuore.append(f"da {_n(d['battito_min'])} a {_n(d['battito_max'])}")
    if "battito_riposo" in d:
        cuore.append(f"a riposo {_n(d['battito_riposo'])}")
    if cuore:
        righe.append("Battito (bpm): " + ", ".join(cuore))
    altro = [f"{nome} {_n(d[k], cifre)}{unita}" for k, nome, cifre, unita in
             [("hrv", "HRV", 0, " ms"), ("ossigeno", "ossigeno nel sangue", 0, "%"),
              ("respiro", "respirazione", 0, " atti/min"), ("peso", "peso", 1, " kg")] if k in d]
    noti = {"passi", "distanza", "piani", "esercizio", "calorie_attive", "calorie_riposo", "battito_medio",
            "battito_min", "battito_max", "battito_riposo", "hrv", "ossigeno", "respiro", "peso"}
    altro += [f"{k.replace('_', ' ')} {_n(v, 1)}" for k, v in d.items() if k not in noti]
    if altro:
        righe.append(", ".join(altro)[:1].upper() + ", ".join(altro)[1:])
    return righe


def _giorno_breve(d: datetime.date, g: dict) -> str:
    pezzi = []
    if g["sonno"]:
        pezzi.append(f"sonno {_durata(g['sonno']['dormito'])}")
    dati = g["dati"]
    for k, fmt in [("passi", "{} passi"), ("calorie_attive", "{} kcal attive"), ("battito_riposo", "riposo {} bpm"),
                   ("hrv", "HRV {} ms")]:
        if k in dati:
            pezzi.append(fmt.format(_n(dati[k])))
    if g["allenamenti"]:
        pezzi.append("allenamenti: " + "; ".join(g["allenamenti"]))
    return f"- {_data(d)}: " + (", ".join(pezzi) if pezzi else "nessun dato")


def dati_salute(giorni: int = 1) -> str:
    """I dati di Apple Salute (orologio e iPhone): passi, distanza, calorie bruciate, battito (medio, minimo, massimo,
    a riposo), HRV, ossigeno nel sangue, respirazione, peso, sonno con orari e fasi (profondo, REM, leggero) e
    allenamenti registrati. Per "come ho dormito", "quanti passi ho fatto", "che battito ho", "quante calorie ho
    bruciato", "come sto andando questa settimana".

    Args:
        giorni: 1 = oggi e la notte scorsa (con ieri se oggi non c'è ancora niente), 7 = l'ultima settimana giorno per
            giorno con le medie
    """
    giorni = max(1, min(int(giorni or 1), 31))
    oggi = _adesso().date()
    dal = oggi - datetime.timedelta(days=giorni - 1)
    righe = _righe(dal)
    if not righe:
        return ("Nessun dato di Salute su Notion negli ultimi giorni: il Comando Rapido sull'iPhone non ha mandato "
                "niente (gira solo a telefono sbloccato; controlla in Comandi → Automazione).")
    per_giorno, superate = _per_giorno(righe)
    for id_ in superate:  # le righe superate dello stesso giorno: nel cestino, così il database resta pulito
        try:
            notion._chiama("PATCH", f"/pages/{id_}", in_trash=True)
        except Exception as e:
            print(f"[salute] riga {id_} non cestinata: {e}")
    ultimo = max(r["inviato"] for r in righe)
    avviso = ""
    if _adesso() - ultimo > VECCHI:
        avviso = (f"\nAttenzione: l'ultimo invio dall'iPhone è di {_data(ultimo.date())} alle {ultimo:%H:%M}, i dati "
                  "di adesso possono mancare.")
    if giorni == 1:
        d = oggi if oggi in per_giorno and (per_giorno[oggi]["dati"] or per_giorno[oggi]["allenamenti"]) else max(
            (g for g in per_giorno if per_giorno[g]["dati"]), default=oggi)
        g = per_giorno.get(d) or {"dati": {}, "allenamenti": [], "inviato": None, "sonno": None}
        quando = "oggi" if d == oggi else ("ieri" if d == oggi - datetime.timedelta(days=1) else _data(d))
        testa = f"Salute di {quando}" + (f" (dati fino alle {g['inviato']:%H:%M})" if g["inviato"] else "")
        righe_testo = [testa + ":"]
        # dopo mezzanotte "la notte scorsa" è ancora quella finita ieri mattina
        sonno = (per_giorno.get(oggi) or {}).get("sonno") or max(
            (s for s in (x["sonno"] for x in per_giorno.values()) if s and _adesso() - s["fine"] < datetime.timedelta(hours=20)),
            key=lambda s: s["fine"], default=None)
        if sonno:
            righe_testo.append(f"Sonno della notte che finisce {_data(sonno['fine'].date())}: {_testo_sonno(sonno)}")
        else:
            righe_testo.append("Sonno: nessun dato per la notte scorsa")
        righe_testo += _testo_dati(g["dati"])
        if g["allenamenti"]:
            righe_testo.append("Allenamenti: " + "; ".join(g["allenamenti"]))
        if len(righe_testo) == 2 and not sonno:
            righe_testo.append("Nessun dato: forse l'orologio non era al polso o non si è sincronizzato.")
        return "\n".join(righe_testo) + avviso
    giorni_visti = sorted((d for d in per_giorno if d >= dal), reverse=True)
    righe_testo = [f"Salute degli ultimi {giorni} giorni (Apple Salute):"]
    righe_testo += [_giorno_breve(d, per_giorno[d]) for d in giorni_visti]
    medie = []
    sonni = [per_giorno[d]["sonno"]["dormito"] for d in giorni_visti if per_giorno[d]["sonno"]]
    if sonni:
        medie.append(f"sonno {_durata(sum(sonni) / len(sonni))} a notte")
    for k, fmt in [("passi", "{} passi"), ("calorie_attive", "{} kcal attive"), ("battito_riposo", "battito a riposo {}"),
                   ("hrv", "HRV {} ms")]:
        valori = [per_giorno[d]["dati"][k] for d in giorni_visti if k in per_giorno[d]["dati"] and d != oggi]
        if valori:  # oggi non è finito: fuori dalle medie
            medie.append(fmt.format(_n(sum(valori) / len(valori))))
    if medie:
        righe_testo.append("Medie (senza oggi, che non è finito): " + ", ".join(medie))
    return "\n".join(righe_testo) + avviso


STRUMENTI = [dati_salute]
