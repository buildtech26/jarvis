"""Prova di brain.py con un Gemini finto, senza rete e senza consumare richieste: da rilanciare dopo ogni modifica
al cervello, insieme a prova_gruppi.py e prova_verifica.py.

    .venv\\Scripts\\python prova_cervello.py

Il 10/10 mancava "from google.genai import types" in _ciclo e falliva ogni domanda con strumenti, mentre quelle
senza strumenti andavano: per questo il primo caso chiama sempre uno strumento.
"""
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from google.genai import errors, types

import brain
import memoria
import quota
import tools
import verifica

_temp = Path(tempfile.mkdtemp(prefix="prova_cervello_"))
quota.FILE_CONSUMI = _temp / "consumi.json"
verifica.FILE = _temp / "verifica.jsonl"
memoria.FILE = _temp / "Memoria.md"
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


def _prova(domanda: str, copione: list, j: brain.Jarvis | None = None) -> tuple[str, GeminiFinto, list[str]]:
    j = j or brain.Jarvis()
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

    # 5. conversazione: resta dopo 5 minuti, si azzera dopo 31 (PAUSA_AZZERA)
    for minuti, resta in ((5, True), (31, False)):
        j = brain.Jarvis()
        j.storia = [{"role": "user", "content": "voli per Tirana"}, {"role": "assistant", "content": "16,99 €"}]
        j.recenti = ["Domanda: voli per Tirana\ncerca_voli: 16.99 €"]
        j.ultima = time.time() - minuti * 60
        _, g, righe = _prova("e il ritorno?", [_testo("Ecco.")], j)
        atteso((len(j.storia) == 4) == resta and bool(j.recenti) == resta,
               f"dopo {minuti} min: storia di {len(j.storia)} messaggi, recenti {len(j.recenti)}", righe)
        atteso(any("[contesto]" in r for r in righe) != resta, f"dopo {minuti} min: riga [contesto]", righe)

    # 6. "Fatto." senza strumenti: secondo tentativo; con "ricordati che" invece è vero (salva il ripiego)
    risposta, g, righe = _prova("manda maurizio", [_testo("Fatto."), _testo("Non so a chi mandarlo, signore.")])
    atteso(len(g.modelli) == 2 and risposta.startswith("Non so"), f"fatto: {len(g.modelli)} richieste, {risposta!r}", righe)
    risposta, g, righe = _prova("ricordati che il prova-test è martedì", [_testo("Fatto, lo terrò a mente.")])
    atteso(len(g.modelli) == 1 and risposta.startswith("Fatto"), f"ricordati: {len(g.modelli)} richieste", righe)
    atteso("prova-test" in memoria.contenuto(), "ricordati: non salvato in memoria", righe)

    # 7. strumento integrale (l'allenamento): arriva il risultato per intero, non il riassunto del modello
    tools.PER_NOME["seduta_allenamento"] = lambda **k: "Corpo libero A.\n1. Riscaldamento\n   - 10 squat"
    risposta, g, righe = _prova("volevo dire oggi scusa", [_chiamata("seduta_allenamento"),
                                                            _testo("Un delizioso circuito, ecco il link.")])
    atteso(risposta == "Corpo libero A.\n1. Riscaldamento\n   - 10 squat", f"integrale: {risposta!r}", righe)
    atteso(any("[integrale]" in r for r in righe), "integrale: riga nel log", righe)

    print(f"7 casi, errori: {len(errori)}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
