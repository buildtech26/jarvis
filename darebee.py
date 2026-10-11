"""Allenamenti da darebee.com per il personal trainer (allenamento.py).

Corpo libero: per ogni seduta un allenamento Darebee senza attrezzi, scelto dal catalogo del sito (workouts.json, lo
stesso che carica la pagina Workouts) e mai uno già fatto o già in calendario. Il livello del poster (I, II, III) è la
fase della scheda. Corsa: i giorni di corsa di un programma Darebee (8 Weeks to 5K) uno dopo l'altro; i giorni di
circuito del programma si saltano, il corpo libero c'è già. Un giorno è di corsa se la pagina non ha il timer delle
serie (verificato sui 56 giorni del 5K: 32 di corsa, 24 di circuito).

Gemini legge il poster e la spiegazione della pagina una volta sola e ne ricava gli esercizi in italiano; il risultato,
il catalogo (riscaricato ogni 7 giorni) e i giorni di corsa dei programmi restano in darebee.json, fuori da git.
Uso personale: Jarvis legge il sito come un browser e nell'evento c'è sempre il link alla pagina originale.
"""
import html
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

import quota

SITO = "https://darebee.com/"
FILE = Path(__file__).with_name("darebee.json")
CATALOGO_OGNI = 7 * 24 * 3600
# senza Accept il sito risponde 404 anche a workouts.json
_INTESTAZIONI = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/140.0 Safari/537.36", "Accept": "*/*"}
_lock = threading.RLock()

ISTRUZIONI = """Questo è un allenamento del sito Darebee: il poster (immagine) e la spiegazione della sua pagina \
(testo sotto). Ricava un JSON in italiano:
- livelli: cosa chiede il poster per LEVEL I, LEVEL II e LEVEL III, in quest'ordine (es. "3 serie"); lista vuota se \
il poster non ha livelli
- riposo: il riposo indicato (es. "fino a 2 minuti fra le serie"); stringa vuota se non c'è
- esercizi: uno per elemento, nell'ordine del poster, con la quantità in cifre e il nome SEMPRE tradotto in un \
italiano comprensibile, mai in inglese (es. "30 slanci della gamba indietro (15 per gamba)", "10 sollevamenti sulle \
punte", "plank: 30 secondi"); se il testo dice "per gamba" o "per lato" scrivilo. Per una corsa gli intervalli (es. \
"2 min di corsa + 2 min di camminata, 2 volte (8 min in tutto)")
- come: 1-2 frasi su come si fa (circuito di fila e poi riposo, ordine, tempi), dal testo
- consigli: 1-2 frasi di tecnica dal testo, più come renderlo più facile se il testo lo dice
Niente che non sia nel poster o nel testo.
Testo della pagina:
{testo}"""
_SCHEMA = {"type": "object", "properties": {
    "livelli": {"type": "array", "items": {"type": "string"}}, "riposo": {"type": "string"},
    "esercizi": {"type": "array", "items": {"type": "string"}}, "come": {"type": "string"},
    "consigli": {"type": "string"}}, "required": ["livelli", "riposo", "esercizi", "come", "consigli"]}
_INGLESE = re.compile(r"\b(the|and|your|with|sets|seconds|crunches|raises|squats|lunges|push-ups)\b", re.I)


def _carica() -> dict:
    vuoto = {"catalogo": {"quando": 0, "voci": []}, "letti": {}, "programmi": {}, "scartati": []}
    if not FILE.exists():
        return vuoto
    return {**vuoto, **json.loads(FILE.read_text(encoding="utf-8"))}


def _salva(dati: dict):
    FILE.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")


def _get(percorso: str) -> requests.Response:
    r = requests.get(SITO + percorso.lstrip("/"), headers=_INTESTAZIONI, timeout=30)
    r.raise_for_status()
    return r


def link(percorso: str) -> str:
    return SITO + percorso.lstrip("/")


# --- catalogo e scelta ---

def catalogo() -> list[dict]:
    """Le voci di workouts.json: t titolo, u pagina, ty tipo, f zona, d difficoltà, e attrezzi."""
    with _lock:
        dati = _carica()
        if dati["catalogo"]["voci"] and time.time() - dati["catalogo"]["quando"] < CATALOGO_OGNI:
            return dati["catalogo"]["voci"]
    try:
        voci = [{k: v[k] for k in ("t", "u", "ty", "f", "d", "e") if k in v} for v in _get("workouts.json").json()]
    except Exception as e:
        if dati["catalogo"]["voci"]:  # sito giù: va bene anche il catalogo vecchio
            print(f"[darebee] catalogo non aggiornato, uso quello del {time.strftime('%d/%m', time.localtime(dati['catalogo']['quando']))}: {e}")
            return dati["catalogo"]["voci"]
        raise
    with _lock:
        dati = _carica()
        dati["catalogo"] = {"quando": time.time(), "voci": voci}
        _salva(dati)
    return voci


def scegli(zone: list[str], tipi: list[str], difficolta: list[str], esclusi: set[str]) -> dict | None:
    """Un allenamento senza attrezzi a caso, fuori da `esclusi`: prima si estrae la zona (così il corpo intero, che
    è la maggior parte del catalogo, non si mangia le altre), poi l'allenamento. Se sono finiti tutti, si ripete."""
    with _lock:
        scartati = set(_carica()["scartati"])
    adatti = [v for v in catalogo() if v.get("e") == "none" and v.get("ty") in tipi and v.get("d") in difficolta
              and v["u"] not in scartati]
    for gruppo in ([v for v in adatti if v["u"] not in esclusi], adatti):
        for zona in random.sample(zone, len(zone)):
            scelta = [v for v in gruppo if v.get("f") == zona]
            if scelta:
                return random.choice(scelta)
    return None


# --- pagine e lettura con Gemini ---

def _pagina(percorso: str) -> tuple[str, str, bool]:
    """Testo della spiegazione, percorso del poster, e se la pagina ha il timer delle serie (allenamento o circuito;
    i giorni di corsa non ce l'hanno)."""
    h = _get(percorso).text
    m = re.search(r'<div class="infotext">([\s\S]*?)</div>', h)
    testo = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""
    # il poster: /images/workouts/<nome>.jpg (non muscles/ né infobox/) o il giorno di un programma
    p = re.search(r'src="/?(images/(?:workouts/[^"/?]+|programs/[^"?]+/days/[^"/?]+)\.jpg)', h)
    return testo, p.group(1) if p else "", "TIMER" in h


def _gemini(immagine: bytes, testo: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=_SCHEMA,
                                         system_instruction="Scrivi SEMPRE in italiano: il sito è in inglese, "
                                                            "traduci tutto, nomi degli esercizi compresi.",
                                         thinking_config=types.ThinkingConfig(thinking_level="low"),
                                         http_options=types.HttpOptions(timeout=60000))
    parti = ([types.Part.from_bytes(data=immagine, mime_type="image/jpeg")] if immagine else []) + [
        ISTRUZIONI.format(testo=testo or "(nessuno)")]
    scartati: set[str] = set()
    errore = "nessun modello disponibile"
    in_inglese = None
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = client.models.generate_content(model=modello, contents=parti, config=config)
            quota.registra(modello, "darebee")
            letto = json.loads(r.text)
            # Flash-Lite a volte copia il testo inglese: si riprova una volta con il modello dopo
            if in_inglese is None and _INGLESE.search(json.dumps(letto, ensure_ascii=False)):
                print(f"[darebee] {modello} ha risposto in inglese, riprovo")
                in_inglese = letto
                scartati.add(modello)
                continue
            return letto
        except Exception as e:
            errore = str(e)
            tipo = quota.segna_errore(modello, errore)
            quota.registra(modello, "darebee", "429" if tipo else "altro")
            if tipo != "minuto":
                scartati.add(modello)
    if in_inglese:
        return in_inglese  # meglio in inglese che niente
    raise RuntimeError(f"Gemini non riesce a leggere l'allenamento: {errore[:120]}")


def leggi(percorso: str) -> dict:
    """Livelli, riposo, esercizi in italiano, come e consigli di una pagina Darebee (letta una volta sola)."""
    with _lock:
        gia = _carica()["letti"].get(percorso)
    if gia:
        return gia
    testo, poster, _ = _pagina(percorso)
    if not poster and not testo:
        raise RuntimeError("la pagina non ha né poster né spiegazione")
    letto = _gemini(_get(poster).content if poster else b"", testo)
    if not letto.get("esercizi"):
        raise RuntimeError("nel poster non ho trovato esercizi")
    with _lock:
        dati = _carica()
        dati["letti"][percorso] = letto
        _salva(dati)
    return letto


def scegli_e_leggi(zone: list[str], tipi: list[str], difficolta: list[str], esclusi: set[str]) -> dict:
    """Come scegli, ma l'allenamento deve avere i livelli I-II-III: quelli senza (es. Power Squat, 50 ripetizioni in
    tutto, pochi minuti) non bastano per una seduta e finiscono negli scartati per sempre."""
    for _ in range(4):
        voce = scegli(zone, tipi, difficolta, esclusi)
        if voce is None:
            raise RuntimeError("nel catalogo non c'è nessun allenamento adatto")
        if leggi(voce["u"]).get("livelli"):
            return voce
        print(f"[darebee] {voce['t']} non ha i livelli: scartato")
        with _lock:
            dati = _carica()
            dati["scartati"].append(voce["u"])
            _salva(dati)
    raise RuntimeError("quattro allenamenti di fila senza livelli")


def _passi(titolo: str, letto: dict, centrale: str, percorso: str, prima: str, dopo: str) -> str:
    """La seduta come sequenza da seguire: riscaldamento, esercizi in elenco, defaticamento, consigli, link."""
    passi = ([prima] if prima else []) + [f"{centrale} {letto.get('come', '')}".strip()]
    righe = [titolo] + [f"{i}. {p}" for i, p in enumerate(passi, 1)]
    righe += [f"   - {e}" for e in letto["esercizi"]]
    if dopo:
        righe.append(f"{len(passi) + 1}. {dopo}")
    if letto.get("consigli"):
        righe.append(f"Consigli: {letto['consigli']}")
    righe.append(f"Poster con le figure e timer: {link(percorso)}")
    return "\n".join(righe)


def testo(percorso: str, titolo: str, fase: int, prima: str = "", dopo: str = "") -> str:
    """L'allenamento in italiano al livello della fase (fase 1 = Level I), passo per passo, con il link alla pagina.
    prima e dopo: riscaldamento e defaticamento della scheda."""
    letto = leggi(percorso)
    intestazione, centrale = f"Darebee · {titolo}.", "Esercizi"
    livelli = letto.get("livelli") or []
    if livelli:
        n = min(fase, len(livelli))
        intestazione = f"Darebee · {titolo}, livello {'I' * n if n < 4 else n} (fase {fase})."
        centrale += f": {livelli[n - 1]}"
    if letto.get("riposo"):
        centrale += f", riposo {letto['riposo']}"
    return _passi(intestazione, letto, centrale + ".", percorso, prima, dopo)


# --- programmi di corsa ---

def giorni_corsa(programma: str) -> list[int]:
    """I giorni di corsa di un programma (es. '8weeks-to-5k'), in ordine. Si leggono una volta tutte le pagine."""
    with _lock:
        gia = _carica()["programmi"].get(programma)
    if gia:
        return gia
    h = _get(f"programs/{programma}.html").text
    m = re.search(r'data-total="(\d+)"', h)
    if not m:
        raise RuntimeError(f"non trovo i giorni del programma {programma}")
    totale = int(m.group(1))
    with ThreadPoolExecutor(max_workers=6) as esecutore:
        timer = list(esecutore.map(lambda n: _pagina(f"programs/{programma}.html?start={n}")[2], range(1, totale + 1)))
    giorni = [n for n, t in enumerate(timer, 1) if not t]
    if not giorni:
        raise RuntimeError(f"nel programma {programma} non trovo giorni di corsa")
    with _lock:
        dati = _carica()
        dati["programmi"][programma] = giorni
        _salva(dati)
    return giorni


def corsa(programma: str, n: int, prima: str = "", dopo: str = "") -> str:
    """La n-esima corsa del programma (dalla 1), passo per passo. Finito il programma resta l'ultima, la corsa
    continua."""
    giorni = giorni_corsa(programma)
    giorno = giorni[min(n, len(giorni)) - 1]
    percorso = f"programs/{programma}.html?start={giorno}"
    intestazione = f"Darebee · programma {programma}, giorno {giorno} (corsa {min(n, len(giorni))} di {len(giorni)})."
    if n > len(giorni):
        intestazione += " Il programma è finito: tieni la corsa continua dell'ultimo giorno, oppure chiedi un programma nuovo."
    return _passi(intestazione, leggi(percorso), "La corsa.", percorso, prima, dopo)
