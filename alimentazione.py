"""Consigli sull'alimentazione: le linee guida sono una nota Obsidian, calorie e proteine si calcolano dai dati scritti lì.

L'obiettivo è perdere grasso: calorie = metabolismo a riposo (formula di Mifflin-St Jeor) × attività − deficit, mai
sotto il metabolismo a riposo. I pasti l'utente li segna in Cronometer (cronometer.py): con quei dati la quota di un
pasto si calcola su quello che resta della giornata, e alle 21:30 arriva su Telegram il riepilogo del giorno (la
domenica anche quello della settimana). Senza Cronometer nel .env restano i consigli con le quote fisse.
"""
import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

import cronometer
from allenamento import _giorno, _sezione

CARTELLA = Path(os.getenv("JARVIS_ALIMENTAZIONE", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Alimentazione"))
MODELLO = Path(__file__).with_name("alimentazione_iniziale.md")  # copiata in Obsidian se la nota non c'è
FILE = Path(__file__).with_name("alimentazione.json")  # i giorni in cui il riepilogo è già partito
# la parte della giornata di ogni pasto, per calorie e proteine
QUOTE = {"colazione": 0.20, "pranzo": 0.35, "cena": 0.35, "spuntino": 0.10}
ORDINE = ["colazione", "pranzo", "spuntino", "cena"]
# dopo quest'ora un pasto si dà per fatto (o saltato): quello che resta della giornata va ai pasti che mancano
FINE = {"colazione": datetime.time(10, 30), "pranzo": datetime.time(15), "spuntino": datetime.time(18, 30),
        "cena": datetime.time(23)}
TETTO = 1.5  # un pasto non supera 1,5 volte la sua quota fissa, anche se in Cronometer manca mezza giornata
SERALE = datetime.time(21, 30)
_pausa_fino = 0.0  # dopo un errore di Cronometer il riepilogo riprova fra 10 minuti
_tentativi: dict[str, int] = {}
# contate come la riga "verdure" della tabella; controllate prima della tabella ("fagiolini" non sono fagioli secchi)
VERDURE = re.compile(r"spinac|pomodor|passata|pelati|zucchin|broccol|fagiolin|carot|cavol|verz|insalat|lattug|rucol|"
                     r"peperon|melanzan|finocch|cipoll|aglio|funghi|minestron|bietol|cicori|zucca|piselli surgel|sedano",
                     re.I)
SENZA_KCAL = re.compile(r"\bsale|\bpepe|spezie|origano|basilico|prezzemol|rosmarino|peperoncin|aceto|limone|\bacqua",
                        re.I)
UOVO = 50  # grammi di un uovo medio senza guscio: "uova 3" sono 3 uova, non 3 grammi


def _nota() -> str:
    file = CARTELLA / "Linee guida.md"
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODELLO, file)
    return file.read_text(encoding="utf-8")


def _impostazioni(testo: str) -> dict:
    valori = {}
    for riga in _sezione(testo, "Impostazioni", 2).splitlines():
        m = re.match(r"\s*[-*]\s*([^:]+?)\s*:\s*(.*)$", riga)
        if m:
            valori[m.group(1).lower()] = m.group(2).strip()
    return valori


def _numero(testo: str | None) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", testo or "")
    return float(m.group().replace(",", ".")) if m else None


def _eta(testo: str | None, oggi: datetime.date) -> int | None:
    try:
        nascita = datetime.date.fromisoformat((testo or "").strip()[:10])
    except ValueError:
        return None
    return oggi.year - nascita.year - ((oggi.month, oggi.day) < (nascita.month, nascita.day))


def _numeri(imp: dict, oggi: datetime.date) -> dict | list[str]:
    """Calorie e proteine al giorno, oppure i dati che mancano nelle Impostazioni."""
    peso, altezza, eta = _numero(imp.get("peso")), _numero(imp.get("altezza")), _eta(imp.get("nascita"), oggi)
    sesso = (imp.get("sesso") or "").strip().upper()[:1]
    mancano = [nome for nome, ok in [("peso", peso), ("altezza", altezza), ("sesso (M o F)", sesso in ("M", "F")),
                                     ("data di nascita", eta)] if not ok]
    if mancano:
        return mancano
    riposo = 10 * peso + 6.25 * altezza - 5 * eta + (5 if sesso == "M" else -161)
    mantenimento = riposo * (_numero(imp.get("attività")) or 1.55)
    return {"calorie": round(max(mantenimento - (_numero(imp.get("deficit")) or 400), riposo) / 50) * 50,
            "proteine": round(peso * (_numero(imp.get("proteine per kg")) or 1.8)),
            "mantenimento": round(mantenimento / 50) * 50, "riposo": round(riposo), "peso": peso, "eta": eta}


def _obiettivi(imp: dict, oggi: datetime.date) -> str:
    n = _numeri(imp, oggi)
    if isinstance(n, list):
        return (f"Calorie e proteine non calcolate: nelle Impostazioni della nota mancano {', '.join(n)} "
                "(Obsidian, Jarvis\\Alimentazione\\Linee guida.md). Dai solo consigli generali e chiedi di scriverli lì.")
    return (f"Obiettivi al giorno: circa {n['calorie']} kcal e almeno {n['proteine']} g di proteine "
            f"(mantenimento {n['mantenimento']} kcal, metabolismo a riposo {n['riposo']} kcal; "
            f"peso {n['peso']:g} kg, {n['eta']} anni).")


def _quota_rimasta(pasto: str, adesso: datetime.datetime) -> float:
    """La parte di quello che resta della giornata che spetta al pasto: i pasti prima di lui la cui ora è passata si
    danno per fatti, quelli dopo e quelli ancora in tempo dividono il resto."""
    i = ORDINE.index(pasto)
    da_fare = [p for j, p in enumerate(ORDINE) if j >= i or adesso.time() < FINE[p]]
    return QUOTE[pasto] / sum(QUOTE[p] for p in da_fare)


def _per_pasto(pasto: str, n: dict, mangiato: dict | None, adesso: datetime.datetime) -> str:
    fisse = n["calorie"] * QUOTE[pasto], n["proteine"] * QUOTE[pasto]
    if not mangiato or mangiato["kcal"] < 1:
        return f"Per {pasto}: circa {round(fisse[0] / 10) * 10} kcal e {fisse[1]:.0f} g di proteine."
    restano = n["calorie"] - mangiato["kcal"], n["proteine"] - mangiato["proteine"]
    if restano[0] < 150:
        return (f"Per {pasto}: le kcal di oggi sono già raggiunte (ne restano {max(restano[0], 0):.0f}): se ha fame, "
                "un piatto leggero di verdure e una proteina magra, circa 200 kcal.")
    quota = _quota_rimasta(pasto, adesso)
    kcal, proteine = restano[0] * quota, max(restano[1], 0) * quota
    testo = f"Per {pasto}, contando quello che ha già mangiato: circa {round(min(kcal, fisse[0] * TETTO) / 10) * 10} kcal"
    if kcal > fisse[0] * TETTO:
        testo += " (limitate: in Cronometer sembra mancare qualche pasto)"
    return testo + f" e {min(proteine, fisse[1] * TETTO):.0f} g di proteine."


def _valori(testo: str) -> dict[str, tuple[float, float]]:
    """La tabella della nota: 'Pasta 355 · 12 g; riso 350 · 7 g' → {'pasta': (355, 12), 'riso': (350, 7)}, per 100 g.
    Solo le righe dell'elenco; le parentesi sono note a margine, gli intervalli (20-35) valgono il punto di mezzo,
    senza proteine (mela 50) valgono 0."""
    numero = r"(\d+(?:[.,]\d+)?)(?:-(\d+(?:[.,]\d+)?))?"
    valori, riga_aperta = {}, ""
    righe = []
    for riga in _sezione(testo, "Valori degli ingredienti", 2).splitlines():  # le voci possono andare a capo
        if re.match(r"\s*[-*]\s", riga):
            righe.append(riga_aperta)
            riga_aperta = riga.strip()[1:]
        elif riga_aperta and riga.strip():
            riga_aperta += " " + riga.strip()
    righe.append(riga_aperta)
    for riga in righe:
        for voce in re.sub(r"\([^)]*\)", "", riga).split(";"):
            m = re.match(rf"\s*([^\d·]+?)\s+{numero}\s*(?:·\s*{numero})?", voce)
            if m:
                kcal = (_numero(m.group(2)) + _numero(m.group(3) or m.group(2))) / 2
                proteine = (_numero(m.group(4)) + _numero(m.group(5) or m.group(4))) / 2 if m.group(4) else 0.0
                valori[m.group(1).strip().lower()] = (kcal, proteine)
    return valori


def _radice(parola: str) -> str:
    return parola[:max(3, min(len(parola) - 1, 5))]


def _trova(nome: str, valori: dict) -> str | None:
    """La riga della tabella per un ingrediente detto a parole: conta le parole in comune (per radice, così pomodoro
    e pomodori sono uguali); a parità vince la riga che comincia con una di quelle parole ("latte" → latte, non
    fiocchi di latte)."""
    if VERDURE.search(nome):
        return next((v for v in valori if v.startswith("verdur")), None)
    if re.search(r"scatol|cott|lessat|sgocciol", nome, re.I) and re.search(r"cec|lentic|fagiol|legum|pisell", nome, re.I):
        return next((v for v in valori if "scatol" in v), None)
    sue = {_radice(p) for p in re.findall(r"[a-zà-ù]{4,}", nome.lower())}
    migliore, punti = None, (0, False)
    for voce in valori:
        parole = [_radice(p) for p in re.findall(r"[a-zà-ù]{4,}", voce)]
        p = (sum(r in sue for r in parole), bool(parole) and parole[0] in sue)
        if p > punti:
            migliore, punti = voce, p
    return migliore


def _senza_impostazioni(testo: str) -> str:
    righe, dentro = [], False
    for riga in testo.splitlines():
        if riga.startswith("## "):
            dentro = riga[3:].strip().lower().startswith("impostazioni")
        if not dentro:
            righe.append(riga)
    return "\n".join(righe).strip()


# --- Cronometer: quello che ha mangiato ---

def _adesso() -> datetime.datetime:
    return datetime.datetime.now()


def _obiettivi_attuali(oggi: datetime.date) -> dict | None:
    n = _numeri(_impostazioni(_nota()), oggi)
    return None if isinstance(n, list) else n


def _su(valore: float, obiettivo: dict | None, chiave: str, unita: str) -> str:
    return f"{valore:.0f}{unita}" + (f" su {obiettivo[chiave]}" if obiettivo else "")


def _giorno_testo(giorno: datetime.date, totali: dict | None, cibi: list[dict], n: dict | None) -> str:
    if not totali or totali["kcal"] < 1:
        return f"{giorno:%d/%m}: in Cronometer non c'è niente segnato."
    testo = (f"{giorno:%d/%m} in Cronometer: {_su(totali['kcal'], n, 'calorie', ' kcal')}, "
             f"{_su(totali['proteine'], n, 'proteine', ' g')} di proteine, {totali['carboidrati']:.0f} g di "
             f"carboidrati, {totali['grassi']:.0f} g di grassi.")
    if cibi:
        testo += "\nCibi:\n" + "\n".join(
            f"- {c['nome']} ({c['quantita']}): {c['kcal']:.0f} kcal, {c['proteine']:.0f} g di proteine" for c in cibi)
    return testo


def _settimana(fine: datetime.date, n: dict | None) -> str:
    """Le medie degli ultimi 7 giorni fino a fine, sui giorni con almeno 500 kcal segnate (gli altri sono dimenticati,
    non digiuni)."""
    inizio = fine - datetime.timedelta(days=6)
    segnati = [t for t in cronometer.giorni(inizio, fine).values() if t["kcal"] >= 500]
    if not segnati:
        return f"Settimana {inizio:%d/%m}-{fine:%d/%m}: in Cronometer non c'è quasi niente segnato."
    kcal = sum(t["kcal"] for t in segnati) / len(segnati)
    proteine = sum(t["proteine"] for t in segnati) / len(segnati)
    testo = (f"Settimana {inizio:%d/%m}-{fine:%d/%m}, {len(segnati)} giorni segnati su 7: media "
             f"{_su(kcal, n, 'calorie', ' kcal')} e {_su(proteine, n, 'proteine', ' g')} di proteine.")
    if n:
        ok = sum(t["proteine"] >= n["proteine"] * 0.9 for t in segnati)
        testo += f" Proteine raggiunte in {ok} giorni su {len(segnati)}."
    return testo


def riepilogo(giorno: datetime.date) -> str:
    """Il messaggio delle 21:30: kcal e proteine del giorno rispetto agli obiettivi; la domenica anche la settimana."""
    n = _obiettivi_attuali(giorno)
    t = cronometer.giorni(giorno, giorno).get(giorno)
    if not t or t["kcal"] < 1:
        testo = "🍽 Oggi in Cronometer non c'è niente segnato: se hai mangiato, segnalo prima di dormire."
    else:
        testo = (f"🍽 Oggi finora: {_su(t['kcal'], n, 'calorie', ' kcal')} e "
                 f"{_su(t['proteine'], n, 'proteine', ' g')} di proteine.")
        if n and t["proteine"] < n["proteine"] * 0.85:
            testo += (f" Proteine basse ({n['proteine'] - t['proteine']:.0f} g sotto): domani uno yogurt greco o due "
                      "uova in più.")
        if n and t["kcal"] > n["calorie"] + 200:
            testo += " Sopra di parecchio: niente di grave, domani si riparte normale senza saltare pasti."
        elif n and t["kcal"] < n["calorie"] * 0.7:
            testo += " Molto sotto: se ceni dopo o hai dimenticato qualcosa, segnalo; se no, mangia ancora qualcosa."
    if giorno.weekday() == 6:
        testo += "\n\n📊 " + _settimana(giorno, n)
    return testo


def _carica() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def controlla() -> list[str]:
    """Chiamata ogni minuto dal bot: alle 21:30 (o appena Jarvis è acceso dopo) il riepilogo del giorno, una volta.
    Se Cronometer non risponde riprova ogni 10 minuti; dopo 3 errori lo dice su Telegram e lascia stare."""
    global _pausa_fino
    adesso = _adesso()
    if not cronometer.configurato() or adesso.time() < SERALE or time.time() < _pausa_fino:
        return []
    oggi, stato = str(adesso.date()), _carica()
    if stato.get("serale") == oggi:
        return []
    try:
        testo = riepilogo(adesso.date())
    except Exception as e:
        print(f"[alimentazione] riepilogo non riuscito: {e}")
        _tentativi[oggi] = _tentativi.get(oggi, 0) + 1
        _pausa_fino = time.time() + 600
        if _tentativi[oggi] < 3:
            return []
        testo = f"🍽 Riepilogo di oggi non fatto: Cronometer non risponde ({e})."
    stato["serale"] = oggi
    FILE.write_text(json.dumps(stato), encoding="utf-8")
    return [testo]


# --- strumenti per Gemini ---

def consigli_alimentazione(pasto: str = "") -> str:
    """Le linee guida sull'alimentazione dell'utente (vuole perdere grasso, cucina da sé con un budget stretto), le
    sue calorie e proteine al giorno e quelle del pasto (contando quello che ha già segnato oggi, se è in
    Cronometer), e kcal e proteine degli ingredienti per calcolare le dosi. Da leggere prima di ogni consiglio su cosa
    e quanto mangiare e prima di proporre una ricetta.

    Args:
        pasto: 'colazione', 'pranzo', 'cena' (anche per "stasera") o 'spuntino'; vuoto se la domanda non riguarda un pasto
    """
    testo, adesso = _nota(), _adesso()
    n = _numeri(_impostazioni(testo), adesso.date())
    righe = [_obiettivi(_impostazioni(testo), adesso.date())]
    segnato = None
    if cronometer.configurato():
        try:
            segnato = cronometer.giorni(adesso.date(), adesso.date()).get(adesso.date())
        except Exception as e:
            righe.append(f"Cronometer non risponde ({e}): calcolo sulla giornata intera.")
        else:
            if segnato and segnato["kcal"] >= 1 and isinstance(n, dict):
                righe.append(f"Oggi in Cronometer finora: {segnato['kcal']:.0f} kcal e {segnato['proteine']:.0f} g "
                             f"di proteine; restano {n['calorie'] - segnato['kcal']:.0f} kcal e "
                             f"{n['proteine'] - segnato['proteine']:.0f} g.")
            elif not segnato or segnato["kcal"] < 1:
                righe.append("Oggi in Cronometer non c'è ancora niente: uso le quote normali dei pasti.")
    if pasto.strip().lower() in QUOTE and isinstance(n, dict):
        righe.append(_per_pasto(pasto.strip().lower(), n, segnato, adesso))
    return " ".join(righe) + f"\n\n{_senza_impostazioni(testo)}"


def mangiato(giorno: str = "oggi") -> str:
    """Cosa ha mangiato l'utente secondo Cronometer, l'app in cui segna i pasti: kcal, proteine, carboidrati e grassi
    rispetto ai suoi obiettivi e i cibi uno per uno; con 'settimana' le medie degli ultimi 7 giorni.

    Args:
        giorno: 'oggi', 'ieri', 'AAAA-MM-GG' oppure 'settimana'
    """
    oggi = _adesso().date()
    n = _obiettivi_attuali(oggi)
    if giorno.strip().lower().startswith("settiman"):
        return _settimana(oggi, n)
    data = _giorno(giorno)
    return _giorno_testo(data, cronometer.giorni(data, data).get(data), cronometer.cibi(data), n)


def calcola_ricetta(ingredienti: str) -> str:
    """Kcal e proteine di una ricetta, calcolate con i valori degli ingredienti della nota. Da usare per ogni ricetta
    proposta: i totali da riportare sono questi.

    Args:
        ingredienti: ingredienti a crudo con i grammi, separati da virgole, es. 'pasta 80, tonno al naturale 120,
            pomodori 200, olio 10'; le uova si contano a pezzi ('uova 3')
    """
    valori = _valori(_nota())
    righe, mancano, kcal, proteine = [], [], 0.0, 0.0
    for pezzo in re.split(r"[,;\n]", ingredienti):
        quanto = _numero(pezzo)
        nome = " ".join(re.sub(r"\d+(?:[.,]\d+)?|\b(?:g|gr|grammi|di|da|q\.?b\.?)\b", " ", pezzo).split()).strip(" .:-").lower()
        if not nome or SENZA_KCAL.search(nome):
            continue
        voce = _trova(nome, valori)
        if voce is None or quanto is None:
            mancano.append(pezzo.strip())
            continue
        grammi = quanto * UOVO if voce.startswith("uov") and quanto <= 12 else quanto
        k, p = valori[voce][0] * grammi / 100, valori[voce][1] * grammi / 100
        kcal, proteine = kcal + k, proteine + p
        righe.append(f"{nome} {grammi:g} g (come {voce}): {k:.0f} kcal, {p:.1f} g di proteine")
    testo = "\n".join(righe) + f"\nTotale: {round(kcal / 10) * 10:.0f} kcal e {proteine:.0f} g di proteine."
    if mancano:
        testo += (f"\nNon contati (non sono nella tabella della nota o manca la quantità): {', '.join(mancano)}. "
                  "Dillo all'utente.")
    return testo


STRUMENTI = [consigli_alimentazione, calcola_ricetta] + ([mangiato] if cronometer.configurato() else [])
