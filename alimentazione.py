"""Consigli sull'alimentazione: le linee guida sono una nota Obsidian, calorie e proteine si calcolano dai dati scritti lì.

Niente diario dei pasti: Jarvis legge la nota e risponde da lì. L'obiettivo è perdere grasso: calorie = metabolismo a
riposo (formula di Mifflin-St Jeor) × attività − deficit, mai sotto il metabolismo a riposo.
"""
import datetime
import os
import re
import shutil
from pathlib import Path

from allenamento import _sezione

CARTELLA = Path(os.getenv("JARVIS_ALIMENTAZIONE", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Alimentazione"))
MODELLO = Path(__file__).with_name("alimentazione_iniziale.md")  # copiata in Obsidian se la nota non c'è
# la parte della giornata di ogni pasto, per calorie e proteine
QUOTE = {"colazione": 0.20, "pranzo": 0.35, "cena": 0.35, "spuntino": 0.10}
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


def _obiettivi(imp: dict, oggi: datetime.date, pasto: str = "") -> str:
    peso, altezza, eta = _numero(imp.get("peso")), _numero(imp.get("altezza")), _eta(imp.get("nascita"), oggi)
    sesso = (imp.get("sesso") or "").strip().upper()[:1]
    mancano = [nome for nome, ok in [("peso", peso), ("altezza", altezza), ("sesso (M o F)", sesso in ("M", "F")),
                                     ("data di nascita", eta)] if not ok]
    if mancano:
        return (f"Calorie e proteine non calcolate: nelle Impostazioni della nota mancano {', '.join(mancano)} "
                "(Obsidian, Jarvis\\Alimentazione\\Linee guida.md). Dai solo consigli generali e chiedi di scriverli lì.")
    riposo = 10 * peso + 6.25 * altezza - 5 * eta + (5 if sesso == "M" else -161)
    mantenimento = riposo * (_numero(imp.get("attività")) or 1.55)
    calorie = max(mantenimento - (_numero(imp.get("deficit")) or 400), riposo)
    proteine = peso * (_numero(imp.get("proteine per kg")) or 1.8)
    testo = (f"Obiettivi al giorno: circa {round(calorie / 50) * 50} kcal e almeno {proteine:.0f} g di proteine "
             f"(mantenimento {round(mantenimento / 50) * 50} kcal, metabolismo a riposo {riposo:.0f} kcal; "
             f"peso {peso:g} kg, {eta} anni).")
    quota = QUOTE.get(pasto.strip().lower())
    if quota:
        testo += (f" Per {pasto.strip().lower()}: circa {round(calorie * quota / 10) * 10} kcal e "
                  f"{proteine * quota:.0f} g di proteine.")
    return testo


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


# --- strumenti per Gemini ---

def consigli_alimentazione(pasto: str = "") -> str:
    """Le linee guida sull'alimentazione dell'utente (vuole perdere grasso, cucina da sé con un budget stretto), le
    sue calorie e proteine al giorno e quelle del pasto, e kcal e proteine degli ingredienti per calcolare le dosi.
    Da leggere prima di ogni consiglio su cosa e quanto mangiare e prima di proporre una ricetta.

    Args:
        pasto: 'colazione', 'pranzo', 'cena' (anche per "stasera") o 'spuntino'; vuoto se la domanda non riguarda un pasto
    """
    testo = _nota()
    return f"{_obiettivi(_impostazioni(testo), datetime.date.today(), pasto)}\n\n{_senza_impostazioni(testo)}"


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


STRUMENTI = [consigli_alimentazione, calcola_ricetta]
