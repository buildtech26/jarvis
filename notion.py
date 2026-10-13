"""Notion tramite l'API ufficiale, con il token dell'integrazione "Jarvis".

Jarvis vede solo le pagine collegate all'integrazione (in Notion: ••• → Connessioni → Jarvis).
La ricerca dell'API guarda solo i titoli: per cercare nel testo c'è l'indice locale (cerca_notion).
"""
import datetime
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

API = "https://api.notion.com/v1"
VERSIONE = "2026-03-11"
# data source del database "Attività" (creato il 2026-10-04); si cambia con NOTION_ATTIVITA nel .env
ATTIVITA = os.getenv("NOTION_ATTIVITA", "8a20948d-ab1a-4195-91ff-d5a0eee50868").strip()
_lock_indice = threading.Lock()
_GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def configurato() -> bool:
    return bool(os.getenv("NOTION_TOKEN", "").strip())


def _chiama(metodo: str, percorso: str, **dati) -> dict:
    for _ in range(3):
        r = requests.request(
            metodo, f"{API}{percorso}", json=dati or None, timeout=30,
            headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN'].strip()}", "Notion-Version": VERSIONE},
        )
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("Retry-After", 2)))  # troppe richieste: Notion dice quanto aspettare
    if r.status_code == 404:
        raise RuntimeError("Notion non trova la pagina: forse non è collegata all'integrazione Jarvis "
                           "(nella pagina: ••• → Connessioni → Jarvis)")
    if not r.ok:
        raise RuntimeError(f"Notion {r.status_code}: {r.json().get('message', r.text)[:200]}")
    return r.json()


def _titolo(pagina: dict) -> str:
    for prop in pagina.get("properties", {}).values():
        if prop["type"] == "title":
            return "".join(t["plain_text"] for t in prop["title"]) or "(senza titolo)"
    return "(senza titolo)"


# --- copia locale in Obsidian -----------------------------------------------------------------------
# L'API cerca solo nei titoli: per cercare DENTRO gli appunti ogni pagina è copiata come nota in
# obsidian/Jarvis/Notion (scelta dell'utente del 2026-10-04: le copie locali stanno nel vault), con le cartelle
# che seguono la gerarchia di Notion. Copia in SOLA LETTURA: Notion è l'originale e ogni aggiornamento
# sovrascrive la nota. Cercare lì non costa né richieste Notion né Gemini.

def _vault_nota() -> Path:
    return Path(os.getenv("JARVIS_NOTION_NOTE", Path.home() / "Desktop" / "obsidian" / "Jarvis" / "Notion"))


def _tutte_le_pagine() -> list[dict]:
    pagine, cursore = [], None
    while True:
        corpo = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
        if cursore:
            corpo["start_cursor"] = cursore
        r = _chiama("POST", "/search", **corpo)
        pagine += r["results"]
        if not r.get("has_more"):
            return pagine
        cursore = r["next_cursor"]


def _nome_file(titolo: str) -> str:
    # Windows toglie da solo spazi e punti finali ("Appunti " su Notion): meglio toglierli prima
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "", titolo).strip().rstrip(".")[:80] or "Senza titolo"


def _percorsi(pagine: list[dict]) -> dict[str, Path]:
    """id → percorso relativo della nota: una cartella per ogni pagina antenata, come in Notion."""
    per_id = {p["id"]: p for p in pagine}
    percorsi, usati = {}, set()
    for p in pagine:
        cartelle, padre = [], p["parent"].get("page_id")
        while padre in per_id and len(cartelle) < 8:
            cartelle.insert(0, _nome_file(_titolo(per_id[padre])))
            padre = per_id[padre]["parent"].get("page_id")
        nome = _nome_file(_titolo(p))
        percorso = Path(*cartelle, f"{nome}.md")
        if percorso in usati:  # due pagine con lo stesso titolo sotto la stessa pagina
            percorso = Path(*cartelle, f"{nome} ({p['id'][:4]}).md")
        usati.add(percorso)
        percorsi[p["id"]] = percorso
    return percorsi


def _tabella(m: re.Match) -> str:
    rientro, corpo = m.group(1), m.group(2)
    righe = []
    for riga in re.findall(r"<tr>(.*?)</tr>", corpo, re.S):
        celle = [c.strip().replace("\n", "<br>").replace("|", "\\|") for c in re.findall(r"<td>(.*?)</td>", riga, re.S)]
        righe.append(f"{rientro}| " + " | ".join(celle) + " |")
    if not righe:
        return ""
    # in markdown la prima riga è sempre l'intestazione, anche quando in Notion non lo era
    colonne = righe[0].count(" | ") + 1
    righe.insert(1, f"{rientro}|" + " --- |" * colonne)
    # senza righe vuote attorno Obsidian non riconosce la tabella o ci attacca la riga successiva
    return f"{rientro}\n" + "\n".join(righe) + f"\n{rientro}"


def _per_obsidian(md: str, link: dict[str, str], url_pagina: str) -> str:
    """Il markdown di Notion nei formati di Obsidian: riquadri, tabelle, esercizi a scomparsa, formule, link."""
    md = re.sub(r"\$`(.+?)`\$", r"$\1$", md, flags=re.S)  # formule in linea: $`x`$ → $x$
    md = md.replace("<empty-block/>", "")
    md = re.sub(r"(?m)^(\t*)<table[^>]*>\n(.*?)\n\t*</table>", _tabella, md, flags=re.S)
    md = re.sub(r'<page url="[^"]*?([0-9a-f]{32})">(.*?)</page>',
                lambda m: f"[[{link[m.group(1)]}|{m.group(2).strip()}]]" if m.group(1) in link else m.group(2), md)
    # gli allegati hanno link firmati che scadono dopo un'ora: si rimanda alla pagina su Notion
    md = re.sub(r'<file src="[^"]*?/([^/?"]+)\?[^"]*"[^>]*>.*?</file>',
                lambda m: f"📎 {m.group(1)} ([allegato su Notion]({url_pagina}))", md)
    righe, livello = [], 0
    for riga in md.split("\n"):
        s = riga.strip()
        if s.startswith("<callout"):
            icona = re.search(r'icon="([^"]*)"', s)
            if not livello:
                righe.append("")
            righe.append("> " * livello + f"> [!note] {icona.group(1) if icona else ''}".rstrip())
            livello += 1
            continue
        if s == "<details>":
            continue  # il titolo arriva con <summary>
        m = re.fullmatch(r"<summary>(.*)</summary>", s)
        if m:
            if not livello:
                righe.append("")
            righe.append("> " * livello + f"> [!example]- {m.group(1)}")
            livello += 1
            continue
        if s in ("</callout>", "</details>"):
            livello = max(0, livello - 1)
            if not livello:
                righe.append("")
            continue
        for _ in range(livello):  # dentro i riquadri Notion rientra il testo con un tab per livello
            riga = riga[1:] if riga.startswith("\t") else riga
        righe.append("> " * livello + riga if livello else riga)
    return "\n".join(righe)


def _meta(nota: Path) -> dict:
    """I campi del frontmatter scritti da Jarvis; {} se la nota non è una copia di Notion."""
    try:
        testa = nota.read_text(encoding="utf-8")[:600]
    except OSError:
        return {}
    if not testa.startswith("---") or "notion_id:" not in testa:
        return {}
    meta = dict(re.findall(r"(?m)^(notion_id|modificata|titolo): (.*)$", testa))
    if meta.get("titolo", "").startswith('"'):
        meta["titolo"] = json.loads(meta["titolo"])
    return meta


def aggiorna_indice() -> str:
    """Riscarica solo le pagine modificate dall'ultima volta; le note delle pagine sparite da Notion si tolgono."""
    with _lock_indice:
        cartella = _vault_nota()
        cartella.mkdir(parents=True, exist_ok=True)
        esistenti = {}
        for nota in cartella.rglob("*.md"):
            meta = _meta(nota)
            if meta:
                esistenti[meta["notion_id"]] = (nota, meta.get("modificata", ""))
        # le righe del database Attività si leggono con attivita: niente note
        pagine = [p for p in _tutte_le_pagine() if p["parent"]["type"] != "data_source_id"]
        percorsi = _percorsi(pagine)
        vault = cartella.parents[1]  # obsidian/: i wikilink partono dalla radice del vault
        link = {}
        for id, percorso in percorsi.items():
            try:
                link[id.replace("-", "")] = (cartella / percorso).relative_to(vault).with_suffix("").as_posix()
            except ValueError:  # cartella fuori dal vault (prove): link relativi alla cartella
                link[id.replace("-", "")] = percorso.with_suffix("").as_posix()
        scaricate, errori = 0, 0
        for p in pagine:
            dest = cartella / percorsi[p["id"]]
            prima = esistenti.pop(p["id"], None)
            if prima and prima[1] == p["last_edited_time"] and prima[0] == dest:
                continue
            time.sleep(0.4)  # Notion regge circa 3 richieste al secondo
            try:
                md = _chiama("GET", f"/pages/{p['id']}/markdown")["markdown"]
            except Exception:
                errori += 1
                continue  # resta la nota vecchia, se c'era: si riprova al prossimo giro
            titolo = _titolo(p)
            # titolo tra virgolette (JSON è YAML valido): "Guida: ..." romperebbe le proprietà di Obsidian
            testa = (f"---\nnotion_id: {p['id']}\ntitolo: {json.dumps(titolo, ensure_ascii=False)}\nmodificata: {p['last_edited_time']}\n"
                     f"notion_url: {p['url']}\n---\n"
                     f"> [!warning] Copia di Notion in sola lettura\n"
                     f"> Jarvis la riscrive a ogni aggiornamento: le modifiche vanno fatte [su Notion]({p['url']}).\n\n")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(testa + _per_obsidian(md, link, p["url"]), encoding="utf-8")
            if prima and prima[0] != dest:
                prima[0].unlink(missing_ok=True)  # pagina rinominata o spostata su Notion
            scaricate += 1
        tolte = 0
        if pagine:  # una lista vuota è più probabilmente un errore che un Notion svuotato
            for nota, _ in esistenti.values():
                nota.unlink(missing_ok=True)
                tolte += 1
        for sotto in sorted((d for d in cartella.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
            if not any(sotto.iterdir()):
                sotto.rmdir()
    esito = f"Notion → Obsidian: {len(pagine)} pagine, {scaricate} aggiornate"
    return esito + (f", {tolte} tolte" if tolte else "") + (f", {errori} errori" if errori else "")


def ciclo_indice():
    """Thread di Jarvis: aggiorna l'indice poco dopo l'avvio e poi ogni 3 ore."""
    import rete

    time.sleep(60)  # non subito: all'avvio la rete del PC appena acceso può non esserci ancora
    while True:
        if not rete.disponibile():  # al risveglio dallo standby: si riprova fra 5 minuti, non fra 3 ore
            time.sleep(300)
            continue
        try:
            print(aggiorna_indice())
        except Exception as e:
            print(f"[notion] indice non aggiornato: {e}")
        time.sleep(3 * 3600)


def _normalizza(testo: str) -> str:
    senza_accenti = unicodedata.normalize("NFD", testo.lower())
    return "".join(c for c in senza_accenti if unicodedata.category(c) != "Mn")


def _radice(parola: str) -> str:
    # togliere l'ultima lettera fa combaciare singolare e plurale: rotismo/rotismi, formula/formule
    return parola[:-1] if len(parola) > 4 else parola


def _note() -> list[tuple[dict, str, str]]:
    """(meta, percorso leggibile, testo) di ogni nota copiata da Notion."""
    cartella, note = _vault_nota(), []
    for nota in cartella.rglob("*.md") if cartella.exists() else []:
        meta = _meta(nota)
        if not meta:
            continue
        testo = nota.read_text(encoding="utf-8")
        testo = testo.split("\n---\n", 1)[-1].split("\n\n", 1)[-1]  # senza frontmatter e avviso
        note.append((meta, " / ".join(nota.relative_to(cartella).with_suffix("").parts), testo))
    return note


def _pezzi(testo: str, massimo: int = 1200) -> list[str]:
    """Il testo diviso per titoli markdown; le sezioni lunghe a loro volta a blocchi di paragrafi."""
    pezzi = []
    for sezione in re.split(r"\n(?=#{1,3} )", testo):
        corrente = ""
        for riga in sezione.split("\n"):  # Notion separa i blocchi con un solo a capo
            if corrente and len(corrente) + len(riga) > massimo:
                pezzi.append(corrente)
                corrente = ""
            corrente += riga + "\n"
        if corrente.strip():
            pezzi.append(corrente)
    return pezzi


_VUOTE = {"come", "cosa", "che", "del", "della", "dei", "delle", "nel", "nella", "per", "con", "una", "uno", "gli",
          "dove", "quando", "quale", "sono", "mio", "miei", "mie", "appunti", "guida", "studio", "pagina", "notion",
          "avevo", "scritto", "dice", "spiegami", "parla", "cerca", "trova", "allo", "alla", "sul", "sulla", "dai"}


def cerca_notion(cosa: str) -> str:
    """Cerca negli appunti e nelle guide allo studio dell'utente su Notion, sia nei titoli sia DENTRO il testo.
    Restituisce i pezzi di testo più pertinenti con la pagina da cui vengono: spesso bastano per rispondere;
    per la pagina intera usa leggi_notion con l'id.

    Args:
        cosa: argomento o parole chiave, es. 'formula di Willis', 'rotismi epicicloidali', 'mappe di Karnaugh'
    """
    note = _note()
    if not note:
        return "La copia degli appunti non è ancora pronta (si crea qualche minuto dopo l'avvio di Jarvis)."
    termini = [_radice(w) for w in re.findall(r"\w+", _normalizza(cosa)) if len(w) > 2 and w not in _VUOTE]
    if not termini:
        return "Dimmi l'argomento da cercare negli appunti."
    candidati, percorsi = [], {}
    for meta, percorso, testo in note:
        id = meta["notion_id"]
        percorsi[id] = percorso
        titolo = _normalizza(meta.get("titolo", ""))
        bonus_titolo = sum(t in titolo for t in termini)
        for pezzo in _pezzi(testo):
            normale = _normalizza(pezzo)
            # una parola vale se è nel pezzo o nel titolo della pagina ("Laplace" nel titolo, "gradino" nel testo)
            trovati = sum(t in normale or t in titolo for t in termini)
            if trovati:
                # prima conta quanti termini diversi ci sono, poi quante volte compaiono, poi il titolo
                punti = trovati * 10 + min(sum(normale.count(t) for t in termini), 10) + bonus_titolo * 3
                candidati.append((punti, trovati, id, pezzo))
    # servono tutte le parole (2/3 se sono più di 3): con una sola in comune "ricetta della carbonara" trovava
    # "non è una ricetta magica" negli appunti di Reti Logiche. Se non c'è niente, risultati parziali dichiarati
    pieno = len(termini) if len(termini) <= 3 else -(-2 * len(termini) // 3)
    avviso = ""
    buoni = [c for c in candidati if c[1] >= pieno]
    if not buoni:
        buoni = [c for c in candidati if c[1] >= (len(termini) + 1) // 2 and len(termini) > 1]
        avviso = f"Nessuna pagina parla di tutto '{cosa}'; risultati PARZIALI, controlla che c'entrino:\n\n"
    if not buoni:
        return f"Negli appunti su Notion non trovo niente su '{cosa}'."
    candidati = [(punti, id, pezzo) for punti, _, id, pezzo in buoni]
    candidati.sort(key=lambda c: -c[0])
    risultati, viste = [], set()
    for _, id, pezzo in candidati:
        if id in viste:
            continue  # un pezzo per pagina: meglio tre pagine diverse che tre pezzi della stessa
        viste.add(id)
        risultati.append(f"[{percorsi[id]} | id {id}]\n{pezzo.strip()[:1200]}")
        if len(risultati) == 3:
            break
    return avviso + "\n\n".join(risultati)


def leggi_notion(id: str, parte: int = 1) -> str:
    """Testo di una pagina Notion in markdown (l'id viene da cerca_notion), a parti di circa 3500 caratteri.
    Le formule sono in LaTeX.

    Args:
        id: id della pagina
        parte: 1 per l'inizio; le pagine lunghe dicono quante parti hanno
    """
    testo = _chiama("GET", f"/pages/{id}/markdown")["markdown"]
    # i risultati degli strumenti vengono tagliati a 4000 caratteri: oltre, il modello non vedrebbe il resto.
    # Qui le sezioni si uniscono fino a riempire il blocco (cerca_notion invece le tiene separate)
    blocchi, corrente = [], ""
    for riga in testo.split("\n"):  # Notion separa i blocchi con un solo a capo
        if corrente and len(corrente) + len(riga) > 3500:
            blocchi.append(corrente)
            corrente = ""
        corrente += riga + "\n"
    blocchi = blocchi + [corrente] if corrente.strip() or not blocchi else blocchi
    parte = max(1, min(int(parte), len(blocchi)))
    if len(blocchi) == 1:
        return testo[:3800]
    return f"[parte {parte} di {len(blocchi)}]\n{blocchi[parte - 1][:3700]}"


def _riga_attivita(p: dict) -> str:
    props = p["properties"]
    riga = _titolo(p)
    scadenza = (props["Scadenza"]["date"] or {}).get("start")
    if scadenza:
        giorno = datetime.date.fromisoformat(scadenza[:10])
        riga += f" | entro {_GIORNI[giorno.weekday()]} {giorno:%d/%m}" + (f" {scadenza[11:16]}" if len(scadenza) > 10 else "")
    if props["Materia"]["select"]:
        riga += f" | {props['Materia']['select']['name']}"
    note = "".join(t["plain_text"] for t in props["Note"]["rich_text"])
    if note:
        riga += f" | note: {note[:150]}"
    if props["Fatto"]["checkbox"]:
        riga += " | FATTA"
    return f"{riga} | id {p['id']}"


def attivita(anche_fatte: bool = False, materia: str = "") -> str:
    """Elenca le attività del database Notion "Attività" (to-do, esami, scadenze), dalla scadenza più vicina.

    Args:
        anche_fatte: True per includere quelle già completate
        materia: filtra per materia, es. 'Reti Logiche'; vuoto = tutte
    """
    filtri = [] if anche_fatte else [{"property": "Fatto", "checkbox": {"equals": False}}]
    if materia:
        filtri.append({"property": "Materia", "select": {"equals": materia}})
    corpo = {"sorts": [{"property": "Scadenza", "direction": "ascending"}], "page_size": 50}
    if filtri:
        corpo["filter"] = {"and": filtri}
    righe = _chiama("POST", f"/data_sources/{ATTIVITA}/query", **corpo)["results"]
    return "\n".join(_riga_attivita(p) for p in righe) if righe else "Nessuna attività da fare."


def attivita_in_breve() -> str:
    """Le attività da fare in una frase, per i comandi diretti (anche letta ad alta voce): niente id."""
    righe = _chiama("POST", f"/data_sources/{ATTIVITA}/query", page_size=50,
                    filter={"property": "Fatto", "checkbox": {"equals": False}},
                    sorts=[{"property": "Scadenza", "direction": "ascending"}])["results"]
    if not righe:
        return "Niente da fare su Notion, signore. Si goda la tregua."
    voci = [_riga_attivita(p).rsplit(" | id ", 1)[0].split(" | note:")[0] for p in righe[:5]]
    altre = f" E altre {len(righe) - 5}." if len(righe) > 5 else ""
    return f"Ha {len(righe)} cose da fare: " + "; ".join(voci) + "." + altre


def aggiungi_attivita(nome: str, scadenza: str = "", materia: str = "", note: str = "") -> str:
    """Aggiunge un'attività (to-do, esame, consegna) al database Notion "Attività".

    Args:
        nome: cosa fare, breve (es. 'Consegnare esercitazione 3')
        scadenza: 'AAAA-MM-GG', oppure 'AAAA-MM-GG HH:MM' se c'è un orario; vuoto se non c'è
        materia: 'Controlli Automatici 1', 'Meccanica Applicata', 'Reti Logiche', 'Università' o 'Personale'; vuoto se non è chiara
        note: dettagli facoltativi
    """
    props = {"Nome": {"title": [{"text": {"content": nome}}]}}
    if scadenza:
        s = scadenza.strip().replace(" ", "T")
        props["Scadenza"] = {"date": {"start": s[:16] + ":00", "time_zone": "Europe/Rome"} if "T" in s else {"start": s[:10]}}
    if materia:
        props["Materia"] = {"select": {"name": materia}}
    if note:
        props["Note"] = {"rich_text": [{"text": {"content": note}}]}
    pagina = _chiama("POST", "/pages", parent={"type": "data_source_id", "data_source_id": ATTIVITA}, properties=props)
    return "Aggiunta su Notion: " + _riga_attivita(pagina)


def completa_attivita(id: str) -> str:
    """Segna come fatta un'attività del database Notion (l'id viene da attivita)."""
    pagina = _chiama("PATCH", f"/pages/{id}", properties={"Fatto": {"checkbox": True}})
    return "Fatta: " + _titolo(pagina)


STRUMENTI = [cerca_notion, leggi_notion, attivita, aggiungi_attivita, completa_attivita]
