"""La riserva quando nessun modello Gemini risponde: Mistral, con l'account gratuito (MISTRAL_API_KEY nel .env).

Cervello: ministral-14b (30 richieste e circa 900.000 token al minuto, gratis). Il 12/10 l'ho provato sulle domande
vere di verifica.jsonl: italiano buono e strumenti sensati, ma sbaglia di un giorno quando trasforma "martedì" o
"domenica" in una data (6 volte su 8, anche con l'elenco dei giorni nel prompt). Per questo, su scelta dell'utente,
la riserva non tocca il calendario né le azioni con una data da calcolare (ESCLUSI). Usa anche elenchi e grassetti
che Gemini non usa: senza_markdown li toglie. Ogni risposta finisce con ETICHETTA, così si sa da dove viene.
mistral-small e mistral-medium sul piano gratuito hanno limite zero. La trascrizione con voxtral è stata tolta
insieme alla voce, il 2026-10-14.
Groq era l'altra possibilità: 8.000 token al minuto, troppo pochi per gli strumenti; tolto su richiesta dell'utente.
"""
import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

import quota

load_dotenv(Path(__file__).with_name(".env"))

URL = "https://api.mistral.ai/v1"
MODELLO = os.getenv("RISERVA_MODELLO", "ministral-14b-latest").strip()
ETICHETTA = "(modello di riserva)"
# calendario, promemoria e allenamenti da pianificare o segnare: tutti vogliono una data calcolata dal modello
ESCLUSI = {"eventi_calendario", "crea_evento", "crea_eventi_ripetuti", "sposta_evento", "cancella_evento",
           "pianifica_allenamenti", "segna_allenamento", "crea_promemoria"}
NOTA = ("\n\nIn questo momento Gemini non risponde e stai lavorando come modello di riserva: calendario, promemoria e "
        "pianificazione degli allenamenti sono spenti. Se l'utente chiede impegni, eventi, promemoria o di organizzare "
        "o segnare allenamenti, digli che con il modello di riserva non posso farlo e di riprovare tra poco: non dire "
        "mai se un impegno c'è o non c'è. Niente elenchi, niente grassetti, niente markdown.")
SPENTO = ("Strumento spento con il modello di riserva: di' all'utente che adesso non posso farlo e di riprovare tra "
          "poco, quando torna Gemini. Non dire che l'hai fatto e non dire cosa c'è in calendario.")


def configurata() -> bool:
    return bool(os.getenv("MISTRAL_API_KEY", "").strip())


def _intestazioni() -> dict:
    return {"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY'].strip()}"}


def _schema(s):
    """Lo schema di google-genai (tipi in maiuscolo, any_of) come JSON Schema."""
    if not isinstance(s, dict):
        return s
    fuori = {}
    for k, v in s.items():
        if k == "type" and isinstance(v, str):
            fuori["type"] = v.lower()
        elif k == "properties":
            fuori[k] = {nome: _schema(x) for nome, x in v.items()}
        elif k == "items":
            fuori[k] = _schema(v)
        elif k == "any_of":
            fuori["anyOf"] = [_schema(x) for x in v]
        elif k in ("description", "required", "enum", "default"):
            fuori[k] = v
    return fuori


_dichiarazioni: dict[str, dict] = {}


def dichiarazione(funzione) -> dict:
    """Lo strumento nel formato di Mistral, ricavato dalla funzione come fa google-genai per Gemini (stessa docstring)."""
    if funzione.__name__ not in _dichiarazioni:
        from google.genai import types

        fd = types.FunctionDeclaration.from_callable_with_api_option(callable=funzione, api_option="GEMINI_API")
        parametri = (_schema(fd.parameters.model_dump(exclude_none=True, mode="json")) if fd.parameters
                     else {"type": "object", "properties": {}})
        _dichiarazioni[funzione.__name__] = {"type": "function", "function": {
            "name": fd.name, "description": fd.description or "", "parameters": parametri}}
    return _dichiarazioni[funzione.__name__]


def _chiama(messaggi: list[dict], strumenti: list[dict] | None) -> dict:
    corpo = {"model": MODELLO, "messages": messaggi, "temperature": 0.3}
    if strumenti:
        corpo["tools"] = strumenti
    r = requests.post(f"{URL}/chat/completions", headers=_intestazioni(), json=corpo, timeout=30)
    quota.registra(MODELLO, "cervello", "ok" if r.ok else "429" if r.status_code == 429 else "altro")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def _testo(contenuto) -> str:
    if isinstance(contenuto, list):  # a volte Mistral manda il testo a pezzi
        return "".join(p.get("text", "") for p in contenuto if isinstance(p, dict))
    return contenuto or ""


def rispondi(sistema: str, storia: list[dict], funzioni: list, esegui, max_passi: int) -> tuple[str, int]:
    """Domanda → strumenti → risposta, come brain._ciclo. esegui(nome, argomenti) fa girare uno strumento.
    Restituisce la risposta e quante richieste sono partite. Gli errori di rete e di Mistral salgono come
    requests.RequestException."""
    strumenti = [dichiarazione(f) for f in funzioni if f.__name__ not in ESCLUSI]
    messaggi = [{"role": "system", "content": sistema + NOTA}]
    messaggi += [{"role": m["role"], "content": m["content"]} for m in storia]
    for passo in range(max_passi + 1):
        m = _chiama(messaggi, strumenti if passo < max_passi else None)
        if not m.get("tool_calls"):
            return _testo(m.get("content")), passo + 1
        messaggi.append({"role": "assistant", "content": _testo(m.get("content")), "tool_calls": m["tool_calls"]})
        for c in m["tool_calls"]:
            nome, argomenti = c["function"]["name"], c["function"].get("arguments") or {}
            if nome in ESCLUSI:
                esito = SPENTO
            else:
                try:
                    argomenti = json.loads(argomenti) if isinstance(argomenti, str) else argomenti
                    esito = esegui(nome, argomenti)
                except ValueError:
                    esito = f"Errore: argomenti non validi ({str(argomenti)[:200]})"
            messaggi.append({"role": "tool", "tool_call_id": c["id"], "name": nome, "content": esito})
    return "", max_passi + 1


def senza_markdown(testo: str) -> str:
    """Via grassetti, titoli, codice e segni degli elenchi: le risposte devono somigliare a quelle di Gemini."""
    testo = re.sub(r"\[[^\]]*\]\((https?://[^)\s]+)\)", r"\1", testo)  # [Fit with Fun](https://...) → l'indirizzo
    testo = re.sub(r"(\*\*|__|`)", "", testo)
    testo = re.sub(r"(?m)^\s*#+\s*", "", testo)
    testo = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", "", testo)
    testo = re.sub(r"(?<!\w)\*(\S[^*\n]*?)\*(?!\w)", r"\1", testo)  # *corsivo*
    return re.sub(r"\n{3,}", "\n\n", testo).strip()


def prova() -> str:
    """Per il controllo del mattino: una richiesta minima, contata come "controllo"."""
    r = requests.post(f"{URL}/chat/completions", headers=_intestazioni(), timeout=30, json={
        "model": MODELLO, "messages": [{"role": "user", "content": "Rispondi solo con la parola OK."}], "max_tokens": 5})
    quota.registra(MODELLO, "controllo", "ok" if r.ok else "429" if r.status_code == 429 else "altro")
    r.raise_for_status()
    return f"risponde ({r.headers.get('x-ratelimit-limit-req-minute', '?')} richieste al minuto)"
