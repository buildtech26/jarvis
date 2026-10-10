"""Prova di brain.py con un Gemini finto, senza rete e senza consumare richieste: da rilanciare dopo ogni modifica
al cervello, insieme a prova_gruppi.py e prova_verifica.py.

    .venv\\Scripts\\python prova_cervello.py

Il 10/10 mancava "from google.genai import types" in _ciclo e falliva ogni domanda con strumenti, mentre quelle
senza strumenti andavano: per questo il primo caso chiama sempre uno strumento.
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from google.genai import errors, types

import brain
import quota
import tools
import verifica

_temp = Path(tempfile.mkdtemp(prefix="prova_cervello_"))
quota.FILE_CONSUMI = _temp / "consumi.json"
verifica.FILE = _temp / "verifica.jsonl"
tools.PER_NOME["prova_finta"] = lambda **k: "risultato finto 42"


def _chiamata(nome: str):
    fc = types.FunctionCall(name=nome, args={})
    return SimpleNamespace(function_calls=[fc], text=None,
                           candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))])


def _testo(testo: str):
    return SimpleNamespace(function_calls=None, text=testo, candidates=[])


class GeminiFinto:
    """Restituisce le risposte del copione in ordine; un'eccezione nel copione viene sollevata."""

    def __init__(self, copione: list):
        self.copione = list(copione)
        self.modelli: list[str] = []
        self.models = SimpleNamespace(generate_content=self._genera)

    def _genera(self, model, contents, config):
        self.modelli.append(model)
        passo = self.copione.pop(0)
        if isinstance(passo, Exception):
            raise passo
        return passo


def _prova(domanda: str, copione: list) -> tuple[str, GeminiFinto, list[str]]:
    j = brain.Jarvis()
    j.gemini = GeminiFinto(copione)
    righe: list[str] = []
    risposta = j.chiedi(domanda, log=righe.append, canale="prova")
    return risposta, j.gemini, righe


def main() -> int:
    errori = []

    def atteso(condizione: bool, cosa: str, righe: list[str]):
        if not condizione:
            errori.append(cosa)
            print(f"SBAGLIATO: {cosa}\n  log:\n    " + "\n    ".join(righe))

    # 1. domanda con uno strumento: il giro completo domanda → strumento → risposta
    risposta, g, righe = _prova("quanto vale il numero di prova?", [_chiamata("prova_finta"), _testo("Vale 42.")])
    atteso(risposta == "Vale 42.", f"strumento: risposta {risposta!r}", righe)
    atteso(brain._usati == ["prova_finta"], f"strumento: usati {brain._usati}", righe)
    atteso(len(g.modelli) == 2 and not g.copione, f"strumento: {len(g.modelli)} richieste invece di 2", righe)
    atteso(any("[fine]" in r and "×2" in r and "prova_finta" in r for r in righe), "strumento: riga [fine]", righe)

    # 2. errore nel codice: niente giro sugli altri modelli, risposta che lo dice
    def guasto(*a):
        raise NameError("name 'types' is not defined")

    originale = verifica.controlla
    verifica.controlla = guasto
    try:
        risposta, g, righe = _prova("quanto vale il numero di prova?", [_testo("Vale 42.")] * 6)
    finally:
        verifica.controlla = originale
    atteso("guasto nel mio codice" in risposta and "NameError" in risposta, f"bug: risposta {risposta!r}", righe)
    atteso(len(g.modelli) == 1, f"bug: riprovato su {len(g.modelli)} richieste", righe)
    atteso(any("[errore interno]" in r and "Traceback" in r for r in righe), "bug: traceback nel log", righe)

    # 3. errore di Gemini (503): si passa al modello successivo
    sovraccarico = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
    risposta, g, righe = _prova("dimmi una cosa", [sovraccarico, _testo("Ecco una cosa.")])
    atteso(risposta == "Ecco una cosa.", f"503: risposta {risposta!r}", righe)
    atteso(len(set(g.modelli)) == 2, f"503: modelli {g.modelli}", righe)
    atteso(any("[fine]" in r and "falliti" in r for r in righe), "503: falliti nella riga [fine]", righe)

    # 4. comando diretto: nessuna richiesta
    risposta, g, righe = _prova("che ore sono", [])
    atteso(risposta.startswith("Sono le") and not g.modelli, f"diretto: {risposta!r}, {len(g.modelli)} richieste", righe)
    atteso(any("[fine]" in r and "comando diretto" in r for r in righe), "diretto: riga [fine]", righe)

    print(f"4 casi, errori: {len(errori)}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
