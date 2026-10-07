"""Link di ricerca per qualunque sito: tabella italiana, poi i "bang" di DuckDuckGo, poi Feeling Ducky."""
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, quote_plus

import requests

FILE_BANG = Path(__file__).with_name("bang.json")
GIORNI_BANG = 30  # ogni quanto riscaricare la lista (circa 13.500 siti)

# Versioni italiane dei siti più usati: i bang puntano spesso a .com / .co.uk / .de.
# Verificati il 2026-10-04 con Chrome headless (Decathlon mostra una verifica anti-bot in headless).
SITI = {
    "google": "https://www.google.com/search?q={}",
    "immagini": "https://www.google.com/search?tbm=isch&q={}",
    "notizie": "https://www.google.com/search?tbm=nws&q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "amazon": "https://www.amazon.it/s?k={}",
    "maps": "https://www.google.com/maps/search/{}",
    "wikipedia": "https://it.wikipedia.org/w/index.php?search={}",
    "aliexpress": "https://it.aliexpress.com/wholesale?SearchText={}",
    "ebay": "https://www.ebay.it/sch/i.html?_nkw={}",
    "subito": "https://www.subito.it/annunci-italia/vendita/usato/?q={}",
    "vinted": "https://www.vinted.it/catalog?search_text={}",
    "zalando": "https://www.zalando.it/catalogo/?q={}",
    "mediaworld": "https://www.mediaworld.it/it/search.html?query={}",
    "unieuro": "https://www.unieuro.it/online/?q={}",
    "temu": "https://www.temu.com/search_result.html?search_key={}",
    "ikea": "https://www.ikea.com/it/it/search/?q={}",
    "decathlon": "https://www.decathlon.it/search?Ntt={}",
}
SINONIMI = {"ali": "aliexpress", "ali express": "aliexpress", "amazon.it": "amazon", "yt": "youtube",
            "google maps": "maps", "google immagini": "immagini", "google notizie": "notizie"}

_bang = None


def _carica_bang() -> list:
    """Lista [trigger, dominio, modello] dei bang, scaricata da DuckDuckGo e tenuta in cache."""
    global _bang
    if _bang is not None:
        return _bang
    vecchio = not FILE_BANG.exists() or time.time() - FILE_BANG.stat().st_mtime > GIORNI_BANG * 86400
    if vecchio:
        try:
            dati = requests.get("https://duckduckgo.com/bang.js", timeout=20).json()
            ridotti = [[b["t"], b.get("d", ""), b["u"]] for b in dati if "{{{s}}}" in b.get("u", "")]
            FILE_BANG.write_text(json.dumps(ridotti, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # si usa la copia vecchia, se c'è
    try:
        _bang = json.loads(FILE_BANG.read_text(encoding="utf-8"))
    except Exception:
        _bang = []
    return _bang


def _etichetta(dominio: str) -> str:
    """'www.aliexpress.com' -> 'aliexpress', 'm.ebay.co.uk' -> 'ebay'."""
    parti = [p for p in dominio.lower().split(".") if p not in ("www", "m", "it", "com", "co", "uk", "org", "net", "de", "fr", "es")]
    return parti[-1] if parti else ""


def _da_bang(nome: str) -> str | None:
    bang = _carica_bang()
    per_trigger = {t: u for t, _, u in bang}
    if nome + "it" in per_trigger:  # es. decathlonit, leroymerlinit
        return per_trigger[nome + "it"]
    candidati = [(t, d, u) for t, d, u in bang if _etichetta(d) == nome]
    if candidati:
        # prima il dominio .it, poi il bang col nome esatto, poi il più corto (di solito la ricerca generale)
        t, d, u = min(candidati, key=lambda c: (not c[1].endswith(".it"), c[0] != nome, len(c[0])))
        return u
    return per_trigger.get(nome)


def dominio(nome: str) -> str | None:
    """'github' -> 'github.com', 'amazon' -> 'www.amazon.it'; None se il nome non è un sito noto."""
    nome = SINONIMI.get(nome.lower().strip(), nome.lower().strip())
    if nome in SITI:
        return SITI[nome].split("/")[2]
    nome = re.sub(r"[\s\-_']", "", nome)
    candidati = [(t, d) for t, d, _ in _carica_bang() if d and _etichetta(d) == nome]
    if not candidati:
        return None
    # prima il dominio .it, poi quello del bang col nome esatto, poi il più corto (niente help./gist./dvd.)
    scelto = min(candidati, key=lambda c: (not c[1].endswith(".it"), c[0] != nome, c[1].count("."), len(c[1])))[1]
    # web.telegram.org, api.whatsapp.com, open.spotify.com: sono app, decide il modello
    return None if scelto.startswith(("web.", "api.", "open.", "app.")) else scelto


def link_ricerca(cosa: str, sito: str = "google") -> tuple[str, str]:
    """Restituisce (url, come) per cercare `cosa` su `sito`; `come` dice da dove viene il link."""
    nome = sito.lower().strip().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")
    nome = SINONIMI.get(nome, nome)
    if nome in SITI:
        return SITI[nome].format(quote_plus(cosa)), "ricerca"
    base = _etichetta(nome) if "." in nome else re.sub(r"[\s\-_']", "", nome)
    if base in SITI:
        return SITI[base].format(quote_plus(cosa)), "ricerca"
    modello = _da_bang(base)
    if modello:
        url = modello.replace("{{{s}}}", quote(cosa))
        return re.sub(r"^http://", "https://", url), "ricerca"
    # sito sconosciuto: Feeling Ducky porta dritto al primo risultato su quel sito
    filtro = f"site:{nome}" if "." in nome else sito
    return f"https://duckduckgo.com/?q={quote_plus(chr(92) + cosa + ' ' + filtro)}", "primo risultato"


def separa_sito(cosa: str) -> tuple[str, str] | None:
    """'tastiera retroilluminata aliexpress' -> ('tastiera retroilluminata', 'aliexpress'), solo per i siti della tabella."""
    parole = cosa.split()
    if parole and parole[0].lower() == "su":
        parole = parole[1:]
    if len(parole) < 2:
        return None
    negozi = SITI.keys() - {"google", "immagini", "notizie", "maps", "wikipedia"}
    for sito, resto in ((parole[-1], parole[:-1]), (parole[0], parole[1:])):
        if resto and resto[-1].lower() == "su":
            resto = resto[:-1]
        elif resto and resto[0].lower() == "su":
            resto = resto[1:]
        nome = SINONIMI.get(sito.lower(), sito.lower())
        if nome in negozi and resto:
            return " ".join(resto), nome
    return None
