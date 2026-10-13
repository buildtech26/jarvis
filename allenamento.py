"""Personal trainer: la scheda è una nota Obsidian, gli allenamenti finiscono da soli in Google Calendar.

Niente palestra: corpo libero in casa (A e B alternate) e corsa all'aperto. Il giorno e l'ora scritti in "pianifica"
nella scheda (domenica 18:00) Jarvis mette in calendario le sedute della settimana dopo, nei buchi fra le lezioni: il
corpo libero prima di sera, la corsa prima di mattina, altrimenti nell'altra fascia. Mezz'ora dopo la fine di ogni seduta chiede su Telegram se è stata fatta (✅/❌); se è saltata
propone un recupero nei 3 giorni dopo. La fase della scheda (1-3) sale con le sedute fatte, non con le settimane.
Con "darebee: sì" nella scheda le sedute vengono da darebee.com (darebee.py): per il corpo libero un allenamento scelto
a ogni seduta, al livello I-II-III della fase; per la corsa i giorni di un programma, in ordine. Gli esercizi della
scheda restano di riserva se il sito o Gemini non rispondono.
Gli eventi spostati o cancellati a mano si rileggono dal calendario ogni mezz'ora. Lo stato (id degli eventi, esiti)
sta in allenamento.json, fuori da git; lo storico leggibile in Obsidian (Storico.md).
"""
import datetime
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import calendario
import darebee

CARTELLA = Path(os.getenv("JARVIS_ALLENAMENTO", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Allenamento"))
FILE = Path(__file__).with_name("allenamento.json")
MODELLO = Path(__file__).with_name("scheda_iniziale.md")  # copiata in Obsidian se la scheda non c'è
_lock = threading.RLock()

TIPI = ["Corpo libero A", "Corpo libero B", "Corsa", "Tennis"]
_EMOJI = {"Corpo libero": "🤸", "Corsa": "🏃", "Tennis": "🎾"}
# il tennis si aggiunge alle "sedute a settimana", in giorno e ora fissi ("tennis" nella scheda); non sposta la fase
TENNIS = "Tennis"
_GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
_GIORNI_INTERI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
# i giorni scelti per primi: con 5 sedute il riposo cade di giovedì e di domenica
_PREFERENZA = [0, 1, 2, 4, 5, 3, 6]
# corpo libero e corsa a giorni alterni: con 5 sedute 3 di corpo libero e 2 di corsa. Le lettere A e B del corpo
# libero si alternano anche fra una settimana e l'altra
_CICLO = ["Corpo libero", "Corsa"]
_ALTERNATA = "Corpo libero"
ATTESA = datetime.timedelta(minutes=30)  # dopo la fine della seduta, prima di chiedere com'è andata
TROPPO_TARDI = datetime.timedelta(hours=36)  # PC spento più a lungo: non si chiede più
SYNC_OGNI = 30 * 60
_ultimo_sync = 0.0
_pausa_fino = 0.0  # dopo un errore di calendario la pianificazione automatica aspetta 10 minuti

_PREDEFINITE = {"inizio": "", "sedute a settimana": "5", "mattina": "07:00-09:00", "sera": "19:00-22:00",
                "margine": "30", "durata corsa": "45", "durata corpo libero": "45",
                "pianifica": "domenica 18:00", "fase 2 dopo": "15", "fase 3 dopo": "35",
                "darebee": "sì", "darebee tipo": "strength", "darebee difficoltà": "easy, normal",
                "programma corsa": "8weeks-to-5k", "tennis": "", "durata tennis": "60"}
# le zone di Darebee per seduta: se ne estrae una e poi l'allenamento, così il corpo intero non prende tutto
_ZONE = {"Corpo libero A": ["lower-body", "full-body"], "Corpo libero B": ["upper-body", "abs", "full-body"]}
_VALIDE = ("fatto", "in programma", "chiesto")  # le sedute che contano come fatte o da fare


# --- scheda (Obsidian) ---

def _scheda() -> str:
    file = CARTELLA / "Scheda.md"
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODELLO, file)
    return file.read_text(encoding="utf-8")


def _sezione(testo: str, titolo: str, livello: int) -> str:
    """Il corpo della sezione con quel titolo (anche solo l'inizio: "Fase 1" trova "Fase 1 — 3 giri"), fino al
    titolo successivo dello stesso livello o superiore. Le sottosezioni restano dentro."""
    corpo, dentro = [], False
    for riga in testo.splitlines():
        m = re.match(r"(#+)\s+(.*)", riga)
        if m and len(m.group(1)) <= livello:
            if dentro:
                break
            dentro = len(m.group(1)) == livello and m.group(2).strip().lower().startswith(titolo.lower())
            continue
        if dentro:
            corpo.append(riga)
    return "\n".join(corpo).strip()


def _impostazioni() -> dict:
    valori = dict(_PREDEFINITE)
    for riga in _sezione(_scheda(), "Impostazioni", 2).splitlines():
        m = re.match(r"\s*[-*]\s*([^:]+?)\s*:\s*(.*)$", riga)
        if m and m.group(1).lower() in valori:
            valori[m.group(1).lower()] = m.group(2).strip()
    return valori


def _numero(imp: dict, chiave: str) -> int:
    m = re.search(r"\d+", imp[chiave]) or re.search(r"\d+", _PREDEFINITE[chiave])
    return int(m.group())


def _finestra(imp: dict, chiave: str) -> tuple[datetime.time, datetime.time] | None:
    """'07:00-09:00' → (7:00, 9:00); vuota o scritta male = fascia non usata."""
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", imp[chiave])
    if not m:
        return None
    a, b, c, d = map(int, m.groups())
    return datetime.time(a, b), datetime.time(c, d)


def _inizio(imp: dict) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(imp["inizio"].strip()[:10])
    except ValueError:
        return None


def _quando_pianificare(imp: dict) -> tuple[int, datetime.time]:
    testo = imp["pianifica"].lower()
    giorno = next((i for i, g in enumerate(_GIORNI_INTERI) if g.rstrip("ì") in testo), 6)
    m = re.search(r"(\d{1,2})[:.](\d{2})", testo)
    return giorno, datetime.time(int(m.group(1)), int(m.group(2))) if m else datetime.time(18, 0)


def _tennis(imp: dict) -> tuple[int, datetime.time] | None:
    """Giorno della settimana e ora del tennis ('domenica 10:00'); vuoto o senza ora = niente tennis."""
    testo = imp["tennis"].lower()
    giorno = next((i for i, g in enumerate(_GIORNI_INTERI) if g.rstrip("ì") in testo), None)
    m = re.search(r"(\d{1,2})[:.](\d{2})", testo)
    return (giorno, datetime.time(int(m.group(1)), int(m.group(2)))) if giorno is not None and m else None


def _base(tipo: str) -> str:
    return _ALTERNATA if tipo.startswith(_ALTERNATA) else tipo


def _durata(imp: dict, tipo: str) -> datetime.timedelta:
    return datetime.timedelta(minutes=_numero(imp, f"durata {_base(tipo).lower()}"))


def _fasce(imp: dict, tipo: str) -> list[tuple[datetime.time, datetime.time]]:
    ordine = ["mattina", "sera"] if _base(tipo) == "Corsa" else ["sera", "mattina"]
    return [f for f in (_finestra(imp, nome) for nome in ordine) if f]


def _intro(sezione: str) -> str:
    """Il testo della sezione prima delle fasi: riscaldamento e defaticamento."""
    righe = sezione.splitlines()
    primo = next((i for i, r in enumerate(righe) if r.startswith("###")), len(righe))
    return "\n".join(righe[:primo]).strip()


def _contenuto(tipo: str, fase: int) -> str:
    """Riscaldamento ed esercizi di quella seduta nella fase; se la fase manca (scheda accorciata a mano) vale la
    più alta presente."""
    sezione = _sezione(_scheda(), tipo, 2)
    if not sezione:
        return f"(nella scheda manca la sezione '## {tipo}')"
    intro = _intro(sezione)
    for f in range(fase, 0, -1):
        esercizi = _sezione(sezione, f"Fase {f}", 3)
        if esercizi:
            return f"Fase {f}. {intro}\n{esercizi}".strip()
    return sezione


def _prima_dopo(intro: str) -> tuple[str, str]:
    """L'introduzione della scheda divisa in quello che si fa prima (riscaldamento) e dopo ("alla fine ...")."""
    intro = " ".join(intro.split())
    m = re.search(r"[;.]?\s*alla fine:?\s*", intro, re.I)
    if not m:
        return intro, ""
    prima, dopo = intro[:m.start()].strip().rstrip(".;"), intro[m.end():].strip()
    return (prima + "." if prima else ""), (f"Alla fine: {dopo}" if dopo else "")


def _darebee(imp: dict) -> bool:
    return imp["darebee"].strip().lower() in ("sì", "si", "yes", "true", "1", "on")


def _lista(imp: dict, chiave: str) -> list[str]:
    voci = [v.strip().lower() for v in re.split(r"[,;]", imp[chiave]) if v.strip()]
    return voci or [v.strip() for v in _PREDEFINITE[chiave].split(",")]


def _corsa_n(stato: dict, s: dict) -> int:
    """Quale corsa del programma tocca a questa seduta: 1 + le corse prima di lei fatte o in programma. Una corsa
    saltata non conta, così la successiva la ripete."""
    return 1 + sum(1 for x in stato["sedute"] if _base(x["tipo"]) == "Corsa" and x["n"] != s["n"]
                   and x["stato"] in _VALIDE and _dt(x["inizio"]) < _dt(s["inizio"]))


def _contenuto_seduta(s: dict, stato: dict, imp: dict, fase: int) -> str:
    """Cosa c'è da fare in una seduta: l'allenamento Darebee scelto per lei o il giorno di corsa del programma (che
    dipende dalle corse fatte prima, quindi si ricalcola ogni volta), altrimenti la scheda."""
    if s["tipo"] == TENNIS:
        prima, dopo = _prima_dopo(_intro(_sezione(_scheda(), TENNIS, 2)))
        passi = [p for p in (prima, f"La partita: {imp['durata tennis']} min.", dopo) if p]
        return "\n".join(f"{i}. {p}" for i, p in enumerate(passi, 1))
    if not _darebee(imp) or (_base(s["tipo"]) == _ALTERNATA and not s.get("darebee")):
        return _contenuto(s["tipo"], fase)
    prima, dopo = _prima_dopo(_intro(_sezione(_scheda(), s["tipo"], 2)))
    try:
        if _base(s["tipo"]) == "Corsa":
            return darebee.corsa(imp["programma corsa"].strip(), _corsa_n(stato, s), prima, dopo)
        return darebee.testo(s["darebee"], s["darebee_titolo"], fase, prima, dopo)
    except Exception as e:
        print(f"[darebee] {s['tipo']} di {_quando(s)} non letto, uso la scheda: {e}")
        if _base(s["tipo"]) == "Corsa":
            return _contenuto(s["tipo"], fase)
        return (f"Darebee · {s['darebee_titolo']}: gli esercizi sono nel poster su {darebee.link(s['darebee'])} "
                f"(non sono riuscito a leggerlo). Se la pagina non si apre, dalla scheda:\n{_contenuto(s['tipo'], fase)}")


def _nome(s: dict) -> str:
    return f"{s['tipo']} ({s['darebee_titolo']})" if s.get("darebee_titolo") else s["tipo"]


# --- stato (allenamento.json) e storico (Obsidian) ---

def _carica() -> dict:
    if not FILE.exists():
        return {"prossimo": 1, "lettera": "A", "settimane": [], "sedute": []}
    return json.loads(FILE.read_text(encoding="utf-8"))


def _salva(stato: dict):
    FILE.write_text(json.dumps(stato, ensure_ascii=False, indent=1), encoding="utf-8")


def _adesso() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _dt(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso).astimezone()


def _ora(giorno: datetime.date, ora: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(giorno, ora).astimezone()


def _lunedi(giorno: datetime.date) -> datetime.date:
    return giorno - datetime.timedelta(days=giorno.weekday())


def _quando(s: dict) -> str:
    d = _dt(s["inizio"])
    return f"{_GIORNI[d.weekday()]} {d:%d/%m} alle {d:%H:%M}"


def _fatte(stato: dict) -> int:
    """Le sedute fatte che fanno salire la fase: il tennis no."""
    return sum(s["stato"] == "fatto" and s["tipo"] != TENNIS for s in stato["sedute"])


def _fase(fatte: int, imp: dict) -> int:
    return 3 if fatte >= _numero(imp, "fase 3 dopo") else 2 if fatte >= _numero(imp, "fase 2 dopo") else 1


def _storico(s: dict):
    file = CARTELLA / "Storico.md"
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# Storico allenamenti\n\nScritto da Jarvis a ogni esito (✅ fatto, ❌ saltato).\n\n",
                        encoding="utf-8")
    d = _dt(s["inizio"])
    simbolo = {"fatto": "✅", "saltato": "❌"}.get(s["stato"], "·")
    with file.open("a", encoding="utf-8") as f:
        f.write(f"- {d:%Y-%m-%d} {_GIORNI[d.weekday()]} {d:%H:%M} · {_nome(s)} · fase {s['fase']} · {simbolo} {s['stato']}\n")


# --- calendario ---

def _occupati(giorno: datetime.date) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Gli impegni con orario di quel giorno (una richiesta per giorno: Apps Script ne restituisce al massimo 50)."""
    eventi = calendario._chiama("eventi", da=_ora(giorno, datetime.time(0)).isoformat(),
                                a=_ora(giorno, datetime.time(23, 59)).isoformat(), cerca="")["eventi"]
    return [(calendario._locale(e["inizio"]), calendario._locale(e["fine"])) for e in eventi if not e["tutto_il_giorno"]]


def _slot(giorno, durata, fasce, occupati, margine, dopo) -> datetime.datetime | None:
    """Il primo orario (a passi di 30 min, nelle fasce in ordine) libero da impegni con il margine attorno."""
    for a, b in fasce:
        t, limite = _ora(giorno, a), _ora(giorno, b)
        while t + durata <= limite:
            if t >= dopo and not any(x < t + durata + margine and y > t - margine for x, y in occupati):
                return t
            t += datetime.timedelta(minutes=30)
    return None


def _titolo(tipo: str) -> str:
    # "Allenamento" nel titolo: è la parola con cui _sincronizza ritrova gli eventi
    return f"{_EMOJI[_base(tipo)]} Allenamento: {tipo}"


def _scegli_darebee(seduta: dict, stato: dict, imp: dict):
    """Per il corpo libero un allenamento Darebee mai fatto né già in calendario; se il sito non risponde la seduta
    resta con gli esercizi della scheda."""
    esclusi = {x["darebee"] for x in stato["sedute"] if x.get("darebee") and x["stato"] in _VALIDE}
    try:
        voce = darebee.scegli_e_leggi(_ZONE.get(seduta["tipo"], ["full-body"]), _lista(imp, "darebee tipo"),
                                      _lista(imp, "darebee difficoltà"), esclusi)
        seduta["darebee"], seduta["darebee_titolo"] = voce["u"], voce["t"]
    except Exception as e:
        print(f"[darebee] nessun allenamento per {seduta['tipo']}, uso la scheda: {e}")


def _crea(stato: dict, imp: dict, tipo: str, inizio: datetime.datetime, **altro) -> dict:
    """`altro` finisce nella seduta: recupero_di, e per un recupero l'allenamento Darebee di quella saltata."""
    fase = _fase(_fatte(stato), imp)
    fine = inizio + _durata(imp, tipo)
    seduta = {"n": stato["prossimo"], "tipo": tipo, "fase": fase, "inizio": inizio.isoformat(),
              "fine": fine.isoformat(), "stato": "in programma", **altro}
    if _darebee(imp) and _base(tipo) == _ALTERNATA and not seduta.get("darebee"):
        _scegli_darebee(seduta, stato, imp)
    ev = calendario._chiama("crea", titolo=_titolo(tipo), inizio=inizio.isoformat(), fine=fine.isoformat(), luogo="",
                            descrizione=f"{_contenuto_seduta(seduta, stato, imp, fase)}\n\nMesso in calendario da Jarvis.",
                            tutto_il_giorno=False)["creato"]
    seduta["id"] = ev["id"]
    stato["prossimo"] += 1
    stato["sedute"].append(seduta)
    _salva(stato)  # dopo ogni evento: se il successivo fallisce, al nuovo tentativo questo giorno risulta già preso
    return seduta


def _sequenza(n: int, lettera: str, salta: int = 0) -> tuple[list[str], str]:
    tipi = []
    for i in range(salta, salta + n):
        tipo = _CICLO[i % len(_CICLO)]
        if tipo == _ALTERNATA:
            tipo, lettera = f"{_ALTERNATA} {lettera}", "B" if lettera == "A" else "A"
        tipi.append(tipo)
    return tipi, lettera


def _della_settimana(stato: dict, lunedi: datetime.date) -> list[dict]:
    return [s for s in stato["sedute"] if s["stato"] != "cancellato" and _lunedi(_dt(s["inizio"]).date()) == lunedi]


def _pianifica(lunedi: datetime.date, dopo: datetime.datetime) -> str:
    """Mette in calendario le sedute che mancano in quella settimana, nei giorni dopo `dopo`."""
    with _lock:
        stato, imp = _carica(), _impostazioni()
        inizio = _inizio(imp)
        gia = _della_settimana(stato, lunedi)
        normali = [s for s in gia if s["tipo"] != TENNIS]  # il tennis è in più rispetto alle "sedute a settimana"
        tennis = _tennis(imp)
        giorno_tennis = lunedi + datetime.timedelta(days=tennis[0]) if tennis else None
        presi = {_dt(s["inizio"]).date() for s in gia} | {giorno_tennis}
        quante = _numero(imp, "sedute a settimana") - len(normali)
        candidati = [lunedi + datetime.timedelta(days=i) for i in _PREFERENZA]
        candidati = [g for g in candidati if g not in presi and g >= dopo.date() and (not inizio or g >= inizio)]
        create = []
        if quante > 0 and candidati:
            with ThreadPoolExecutor(max_workers=7) as esecutore:
                occupati = dict(zip(candidati, esecutore.map(_occupati, candidati)))
            margine = datetime.timedelta(minutes=_numero(imp, "margine"))
            tutte = [f for f in (_finestra(imp, "mattina"), _finestra(imp, "sera")) if f]
            lunga = max(_durata(imp, t) for t in TIPI if t != TENNIS)
            # i giorni in ordine di preferenza, poi le sedute assegnate in ordine di data
            scelti = sorted([g for g in candidati if _slot(g, lunga, tutte, occupati[g], margine, dopo)][:quante])
            tipi, lettera = _sequenza(len(scelti), stato.get("lettera", "A"), salta=len(normali))
            for giorno, tipo in zip(scelti, tipi):
                ora = _slot(giorno, _durata(imp, tipo), _fasce(imp, tipo), occupati[giorno], margine, dopo)
                if ora:
                    create.append(_crea(stato, imp, tipo, ora))
            stato["lettera"] = lettera
        nuove = len(create)
        # il tennis all'ora fissa, senza cercare buchi: se la partita è un altro giorno l'utente sposta l'evento
        if (tennis and _ora(giorno_tennis, tennis[1]) >= dopo and (not inizio or giorno_tennis >= inizio)
                and not any(s["tipo"] == TENNIS for s in gia)):
            create.append(_crea(stato, imp, TENNIS, _ora(giorno_tennis, tennis[1])))
        if str(lunedi) not in stato["settimane"]:
            stato["settimane"].append(str(lunedi))
        _salva(stato)
        tutte_sedute = sorted(_della_settimana(stato, lunedi), key=lambda s: s["inizio"])
        prima = [s for s in _della_settimana(stato, lunedi - datetime.timedelta(days=7)) if s["stato"] != "in programma"]

    testo = f"🗓 Allenamenti della settimana del {lunedi:%d/%m}"
    if not create:
        testo += (": non ho aggiunto niente, " + ("ci sono già tutte." if quante <= 0 else
                  "non trovo buchi liberi nelle tue fasce (mattina e sera nella scheda)."))
    else:
        testo += " in calendario: " + "; ".join(f"{_quando(s)} {_nome(s)}" for s in create) + "."
        giorni = {_dt(s["inizio"]).weekday() for s in tutte_sedute}
        riposo = [_GIORNI[i] for i in range(7) if i not in giorni and lunedi + datetime.timedelta(days=i) >= dopo.date()]
        if riposo:
            testo += f" Riposo: {', '.join(riposo)}."
        if nuove < quante:
            testo += f" Ne ho trovate {nuove} su {quante}: negli altri giorni non c'era un buco libero."
        testo += " Se un orario non va, spostalo dal calendario: me ne accorgo da solo."
    if prima:
        testo += f" La settimana scorsa: {sum(s['stato'] == 'fatto' for s in prima)} fatte su {len(prima)}."
    return testo


def _sincronizza():
    """Rilegge dal calendario le sedute ancora da fare: spostate a mano → nuovo orario, cancellate → tolte."""
    global _ultimo_sync
    _ultimo_sync = time.time()
    with _lock:
        aperte = [s for s in _carica()["sedute"] if s["stato"] == "in programma"]
    if not aperte:
        return
    da = _lunedi(min(_dt(s["inizio"]) for s in aperte).date())
    a = max(_dt(s["inizio"]) for s in aperte).date() + datetime.timedelta(days=14)
    eventi = calendario._chiama("eventi", da=_ora(da, datetime.time(0)).isoformat(),
                                a=_ora(a, datetime.time(23, 59)).isoformat(), cerca="Allenamento")["eventi"]
    if not eventi:
        return  # nessun risultato con sedute in programma: più probabile un guasto della ricerca che tutto cancellato
    per_id = {e["id"]: e for e in eventi}
    with _lock:
        stato = _carica()
        for s in stato["sedute"]:
            if s["stato"] != "in programma":
                continue
            ev = per_id.get(s["id"])
            if ev is None:
                s["stato"] = "cancellato"
                print(f"[allenamento] {s['tipo']} di {_quando(s)} non è più in calendario")
            elif calendario._locale(ev["inizio"]) != _dt(s["inizio"]) or calendario._locale(ev["fine"]) != _dt(s["fine"]):
                s["inizio"] = calendario._locale(ev["inizio"]).isoformat()
                s["fine"] = calendario._locale(ev["fine"]).isoformat()
                print(f"[allenamento] {s['tipo']} spostata a {_quando(s)}")
        _salva(stato)


def controlla() -> list[tuple[str, list]]:
    """Chiamata ogni minuto dal bot: pianifica le settimane che mancano e chiede l'esito delle sedute finite.
    Restituisce (testo, righe di pulsanti) da mandare su Telegram."""
    global _pausa_fino
    if not calendario.configurato():
        return []
    adesso = _adesso()
    messaggi = []
    with _lock:
        stato, imp = _carica(), _impostazioni()
    inizio = _inizio(imp)
    lun = _lunedi(adesso.date())
    prossimo = lun + datetime.timedelta(days=7)
    giorno_p, ora_p = _quando_pianificare(imp)
    da_fare = []
    # la settimana in corso non è stata pianificata (Jarvis spento all'ora giusta): i giorni che restano
    if str(lun) not in stato["settimane"] and (not inizio or lun + datetime.timedelta(days=6) >= inizio):
        da_fare.append((lun, adesso + datetime.timedelta(hours=1)))
    if ((adesso.weekday(), adesso.time()) >= (giorno_p, ora_p) and str(prossimo) not in stato["settimane"]
            and (not inizio or prossimo + datetime.timedelta(days=6) >= inizio)):
        da_fare.append((prossimo, max(_ora(prossimo, datetime.time(0)), adesso + datetime.timedelta(hours=1))))
    if da_fare and time.time() >= _pausa_fino:
        for lunedi, dopo in da_fare:
            try:
                messaggi.append((_pianifica(lunedi, dopo), []))
            except Exception as e:
                print(f"[allenamento] pianificazione del {lunedi} non riuscita, riprovo tra 10 min: {e}")
                _pausa_fino = time.time() + 600

    finite = [s for s in stato["sedute"] if s["stato"] == "in programma" and adesso >= _dt(s["fine"]) + ATTESA]
    if finite or time.time() - _ultimo_sync > SYNC_OGNI:
        try:
            _sincronizza()
        except Exception as e:
            print(f"[allenamento] calendario non riletto: {e}")
    with _lock:
        stato = _carica()
        for s in stato["sedute"]:
            if s["stato"] != "in programma" or adesso < _dt(s["fine"]) + ATTESA:
                continue
            if adesso - _dt(s["fine"]) > TROPPO_TARDI:
                s["stato"] = "senza risposta"
                _storico(s)
                continue
            s["stato"] = "chiesto"
            messaggi.append((f"Allora, {s['tipo']} di {_quando(s)}: fatto?",
                             [[("✅ Fatto", f"all:{s['n']}:fatto"), ("❌ Saltato", f"all:{s['n']}:saltato")]]))
        _salva(stato)
    return messaggi


# --- esiti (pulsanti di Telegram e voce) ---

def _recupero(s: dict, stato: dict, imp: dict) -> datetime.datetime | None:
    """Il primo buco per la stessa seduta da oggi a 3 giorni, nei giorni senza altre sedute."""
    adesso = _adesso()
    presi = {_dt(x["inizio"]).date() for x in stato["sedute"] if x["stato"] in ("in programma", "chiesto", "fatto")}
    giorni = [adesso.date() + datetime.timedelta(days=i) for i in range(4)]
    giorni = [g for g in giorni if g not in presi]
    if not giorni:
        return None
    with ThreadPoolExecutor(max_workers=4) as esecutore:
        occupati = list(esecutore.map(_occupati, giorni))
    margine = datetime.timedelta(minutes=_numero(imp, "margine"))
    for giorno, occ in zip(giorni, occupati):
        ora = _slot(giorno, _durata(imp, s["tipo"]), _fasce(imp, s["tipo"]), occ, margine,
                    adesso + datetime.timedelta(hours=1))
        if ora:
            return ora
    return None


def _esito(s: dict, azione: str, stato: dict, imp: dict, quando: datetime.datetime | None = None) -> tuple[str, list]:
    if azione == "fatto":
        prima = _fase(_fatte(stato), imp)
        s["stato"] = "fatto"
        _storico(s)
        fatte = _fatte(stato)
        if s["tipo"] == TENNIS:
            _salva(stato)
            return "✅ Segnato: tennis fatto. Bella partita?", []
        testo = f"✅ Segnato: {s['tipo']} fatta. Sedute fatte in tutto: {fatte}."
        if _fase(fatte, imp) > prima:
            testo += (f" Passi alla fase {_fase(fatte, imp)}: da adesso esercizi e serie cambiano; negli eventi già in "
                      "calendario c'è ancora la fase di prima, chiedimi l'allenamento del giorno.")
        _salva(stato)
        return testo, []
    if azione == "saltato":
        s["stato"] = "saltato"
        _storico(s)
        _salva(stato)
        if s["tipo"] == TENNIS:  # dipende dagli amici e dal campo: niente recupero proposto
            return ("❌ Segnato: tennis saltato. Se giocate un altro giorno, spostalo in calendario o dimmelo: lo "
                    "rimetto come fatto."), []
        testo = f"❌ Segnato: {s['tipo']} saltata."
        risposte = [x for x in stato["sedute"] if x["stato"] in ("fatto", "saltato") and x["tipo"] != TENNIS]
        if len(risposte) >= 2 and all(x["stato"] == "saltato" for x in risposte[-2:]):
            testo += " Sono due di fila: anche 20 minuti di corpo libero contano più di niente."
        ora = _recupero(s, stato, imp)
        if not ora:
            return testo + " Nei prossimi 3 giorni non vedo buchi liberi: si riparte dalla prossima.", []
        breve = f"{_GIORNI[ora.weekday()]} {ora:%H:%M}"
        return (testo + f" Posso recuperarla {_GIORNI[ora.weekday()]} {ora:%d/%m} alle {ora:%H:%M}.",
                [[(f"🔁 Recupera {breve}", f"all:{s['n']}:rec:{ora:%Y%m%d%H%M}"), ("Lascia stare", f"all:{s['n']}:no")]])
    if azione == "rec":
        if s.get("recuperata"):
            return "L'ho già rimessa in calendario.", []
        stesso = {k: s[k] for k in ("darebee", "darebee_titolo") if k in s}  # si recupera lo stesso allenamento
        nuova = _crea(stato, imp, s["tipo"], quando, recupero_di=s["n"], **stesso)
        s["recuperata"] = nuova["n"]
        _salva(stato)
        return f"🔁 In calendario: {s['tipo']} {_quando(nuova)}.", []
    return "👍 Va bene, si riparte dalla prossima.", []


def pulsante(dati: str) -> tuple[str | None, list]:
    """Un pulsante premuto su Telegram: 'all:<n>:fatto|saltato|no' oppure 'all:<n>:rec:AAAAMMGGHHMM'. Testo None se
    la seduta ha già quell'esito: il messaggio non va toccato."""
    parti = dati.split(":")
    with _lock:
        stato, imp = _carica(), _impostazioni()
        s = next((x for x in stato["sedute"] if str(x["n"]) == parti[1]), None)
        if s is None:
            return "Non trovo più questa seduta.", []
        if parti[2] in ("fatto", "saltato") and s["stato"] == parti[2]:
            # il 13/10 "Saltato" premuto 3 volte mentre il recupero cercava buchi in calendario: 3 righe nello storico
            return None, []
        quando = None
        if parti[2] == "rec":
            quando = datetime.datetime.strptime(parti[3], "%Y%m%d%H%M").astimezone()
        return _esito(s, parti[2], stato, imp, quando)


def _giorno(testo: str, passato: bool = False) -> datetime.date:
    """'oggi', 'domani', 'ieri', un giorno della settimana o 'AAAA-MM-GG'. Il giorno della settimana è il prossimo
    con quel nome, oggi compreso ("domenica" di lunedì 12/10 = 18/10), oppure con passato=True l'ultimo ("ho fatto
    la corsa lunedì")."""
    testo = testo.strip().lower()
    adesso = _adesso()
    oggi = adesso.date()
    scarto = {"oggi": 0, "": 0, "domani": 1, "ieri": -1, "dopodomani": 2}
    if adesso.hour < 5:
        # alle 00:30 "domani" è il giorno appena cominciato: chi non è ancora andato a dormire conta così
        scarto.update(domani=0, dopodomani=1)
    if testo in scarto:
        return oggi + datetime.timedelta(days=scarto[testo])
    nome = next((i for i, g in enumerate(_GIORNI_INTERI) if g.rstrip("ì") in testo), None)
    if nome is not None and not re.match(r"\d{4}-\d{2}-\d{2}", testo):
        if passato or "scors" in testo:
            return oggi - datetime.timedelta(days=(oggi.weekday() - nome) % 7 or (7 if "scors" in testo else 0))
        avanti = (nome - oggi.weekday()) % 7
        return oggi + datetime.timedelta(days=avanti or (7 if "prossim" in testo else 0))
    return datetime.date.fromisoformat(testo[:10])


# --- strumenti per Gemini ---

def seduta_allenamento(giorno: str = "oggi") -> str:
    """L'allenamento in calendario per un giorno: riscaldamento ed esercizi (da Darebee, al livello della fase
    attuale, o dalla scheda) con il link alla pagina Darebee da riportare all'utente.

    Args:
        giorno: 'oggi', 'domani', 'ieri', il nome di un giorno come lo dice l'utente ('domenica', 'prossima domenica':
            lo strumento sa quale data è) oppure 'AAAA-MM-GG'
    """
    data = _giorno(giorno)
    with _lock:
        stato, imp = _carica(), _impostazioni()
    fase = _fase(_fatte(stato), imp)
    sedute = [s for s in stato["sedute"] if s["stato"] != "cancellato" and _dt(s["inizio"]).date() == data]
    if not sedute and str(_lunedi(data)) not in stato["settimane"]:
        return (f"La settimana del {_lunedi(data):%d/%m} non è ancora in calendario: la pianifico da solo "
                f"({imp['pianifica']}), oppure chiedimelo.")
    if not sedute:
        dopo = sorted((s for s in stato["sedute"] if s["stato"] == "in programma" and _dt(s["inizio"]).date() > data),
                      key=lambda s: s["inizio"])
        testo = f"{_GIORNI_INTERI[data.weekday()].capitalize()} {data:%d/%m}: nessun allenamento in calendario, riposo."
        return testo + (f" Il prossimo: {dopo[0]['tipo']} {_quando(dopo[0])}." if dopo else "")
    return "\n\n".join(f"{s['tipo']} {_quando(s)} ({s['stato']}).\n{_contenuto_seduta(s, stato, imp, fase)}"
                       for s in sedute)


def allenamenti_settimana(settimana: str = "questa") -> str:
    """Le sedute della settimana con il loro stato (in programma, fatto, saltato), le sedute fatte e la fase.

    Args:
        settimana: 'questa', 'prossima' o 'scorsa'
    """
    lunedi = _lunedi(_adesso().date()) + datetime.timedelta(days={"prossima": 7, "scorsa": -7}.get(settimana, 0))
    with _lock:
        stato, imp = _carica(), _impostazioni()
    fatte, fase = _fatte(stato), _fase(_fatte(stato), imp)
    sedute = sorted(_della_settimana(stato, lunedi), key=lambda s: s["inizio"])
    if sedute:
        righe = [f"{_quando(s)} {_nome(s)}: {s['stato']}" for s in sedute]
    elif str(lunedi) in stato["settimane"]:
        righe = ["Nessuna seduta: non c'erano buchi liberi."]
    else:
        righe = [f"Non ancora pianificata: lo faccio da solo ({imp['pianifica']}) o quando me lo chiedi."]
    soglia = {1: _numero(imp, "fase 2 dopo"), 2: _numero(imp, "fase 3 dopo")}.get(fase)
    prossima = f", la fase {fase + 1} parte a {soglia}" if soglia else ""
    return (f"Settimana del {lunedi:%d/%m}:\n" + "\n".join(righe)
            + f"\nSedute fatte in tutto: {fatte} (fase {fase}{prossima}).")


def scheda_allenamento(seduta: str = "") -> str:
    """La scheda dell'utente (niente palestra): una seduta ('Corpo libero A', 'Corpo libero B', 'Corsa') nella fase
    attuale, oppure, senza seduta, fase, struttura della settimana e regole per rendere gli esercizi più difficili.

    Args:
        seduta: nome della seduta; vuoto = panoramica
    """
    with _lock:
        stato, imp = _carica(), _impostazioni()
    fase = _fase(_fatte(stato), imp)
    tipo = next((t for t in TIPI if seduta and t.lower().startswith(seduta.strip().lower())), None)
    if tipo and _darebee(imp):
        prossima = sorted((s for s in stato["sedute"] if s["tipo"] == tipo and s["stato"] == "in programma"
                           and _dt(s["fine"]) > _adesso()), key=lambda s: s["inizio"])
        if prossima:
            return f"{tipo}, la prossima ({_quando(prossima[0])}). {_contenuto_seduta(prossima[0], stato, imp, fase)}"
        if _base(tipo) == _ALTERNATA:
            return (f"{tipo}: a ogni seduta scelgo un allenamento Darebee nuovo, lo vedi quando è in calendario. "
                    f"Riserva della scheda: {_contenuto(tipo, fase)}")
    if tipo:
        return f"{tipo}. {_contenuto(tipo, fase)}"
    fonte = ""
    if _darebee(imp):
        fonte = (" Le sedute vengono da darebee.com: per il corpo libero un allenamento senza attrezzi diverso ogni "
                 f"volta (A gambe o corpo intero, B parte alta, addominali o corpo intero) al livello {'I' * fase} del "
                 f"poster; la corsa segue il programma {imp['programma corsa']}.")
    if _tennis(imp):
        fonte += f" In più il tennis, {imp['tennis']} ({imp['durata tennis']} min): se la partita cambia, si sposta l'evento."
    return (f"Fase {fase}, {_fatte(stato)} sedute fatte. {_numero(imp, 'sedute a settimana')} sedute a settimana: "
            f"corpo libero in casa (A e B alternate) e corsa a giorni alterni, niente palestra.{fonte} Fasce: mattina "
            f"{imp['mattina'] or 'non usata'}, sera {imp['sera'] or 'non usata'}.\nCome progredire:\n"
            f"{_sezione(_scheda(), 'Come progredire', 2)}")


def pianifica_allenamenti(settimana: str = "prossima", rifai: bool = False) -> str:
    """Mette in calendario gli allenamenti di una settimana nei buchi fra gli impegni (di solito lo fa da solo la
    domenica sera). Riporta all'utente giorni e orari come scritti nel risultato.

    Args:
        settimana: 'questa' (solo i giorni che restano) o 'prossima'
        rifai: True SOLO se l'utente chiede di rifare la settimana: toglie dal calendario le sedute future ancora
            da fare e le rimette da capo
    """
    adesso = _adesso()
    lunedi = _lunedi(adesso.date()) + datetime.timedelta(days=7 if settimana == "prossima" else 0)
    with _lock:
        imp = _impostazioni()
        inizio = _inizio(imp)
        if inizio and lunedi + datetime.timedelta(days=6) < inizio:
            return f"La scheda comincia il {inizio:%d/%m} (Impostazioni della scheda): quella settimana è prima."
        if rifai:
            stato = _carica()
            tolte = [s for s in _della_settimana(stato, lunedi) if s["stato"] == "in programma" and _dt(s["inizio"]) > adesso]
            for s in tolte:
                try:
                    calendario._chiama("cancella", id=s["id"])
                except RuntimeError as e:  # già cancellato a mano
                    print(f"[allenamento] {s['tipo']} di {_quando(s)}: {e}")
                s["stato"] = "cancellato"
            lettere = [s["tipo"][-1] for s in tolte if _base(s["tipo"]) == _ALTERNATA]
            if lettere:
                stato["lettera"] = lettere[0]  # le lettere riprendono da dove erano
            _salva(stato)
        return _pianifica(lunedi, adesso + datetime.timedelta(hours=1))


def segna_allenamento(esito: str, giorno: str = "oggi", seduta: str = "") -> str:
    """Segna com'è andato l'allenamento in calendario di un giorno ("ho fatto la corsa", "oggi l'ho saltato",
    "ho giocato a tennis").

    Args:
        esito: 'fatto', 'saltato', oppure 'recupera' (solo se l'utente chiede di recuperarlo: lo rimette nel primo
            buco libero dei prossimi 3 giorni)
        giorno: 'oggi', 'ieri', il nome di un giorno passato ('lunedì') oppure 'AAAA-MM-GG'
        seduta:'tennis', 'corsa' o 'corpo libero' se l'utente lo nomina; vuoto = l'ultima del giorno. Il tennis
            giocato in un giorno diverso da quello in calendario si trova lo stesso, nella stessa settimana
    """
    data = _giorno(giorno, passato=True)
    with _lock:
        stato, imp = _carica(), _impostazioni()
        sedute = [s for s in stato["sedute"] if s["stato"] != "cancellato" and _dt(s["inizio"]).date() == data]
        if seduta.strip():
            sedute = [s for s in sedute if s["tipo"].lower().startswith(seduta.strip().lower())]
            if not sedute and seduta.strip().lower().startswith("tennis"):
                # la partita si è spostata e l'evento no: vale il tennis ancora aperto di quella settimana
                sedute = [s for s in stato["sedute"] if s["tipo"] == TENNIS and s["stato"] in ("in programma", "chiesto")
                          and _lunedi(_dt(s["inizio"]).date()) == _lunedi(data)]
        if not sedute:
            return f"Il {data:%d/%m} non c'era nessun allenamento {seduta.strip() or ''} in calendario.".replace("  ", " ")
        s = sedute[-1]
        if esito == "recupera" and s["tipo"] == TENNIS:
            return "Il tennis non lo recupero da solo: dimmi quando giocate, oppure sposta l'evento in calendario."
        if esito != "recupera":
            testo, pulsanti = _esito(s, "fatto" if esito == "fatto" else "saltato", stato, imp)
            if pulsanti:
                testo += " Se vuoi recuperarla, dimmelo."
            return testo
        if s["stato"] != "saltato":
            s["stato"] = "saltato"
            _storico(s)
            _salva(stato)
        ora = _recupero(s, stato, imp)
        if not ora:
            return "Nei prossimi 3 giorni non vedo buchi liberi per recuperarla."
        return _esito(s, "rec", stato, imp, ora)[0]


STRUMENTI = [seduta_allenamento, allenamenti_settimana, scheda_allenamento, pianifica_allenamenti, segna_allenamento]
