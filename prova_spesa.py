"""Prova dei consigli per la spesa (spesa.py) con un Fit with Fun e una dispensa finti: niente rete, spesa.json vero
non toccato.

    .venv\\Scripts\\python prova_spesa.py
"""
import datetime
import sys
import tempfile
from pathlib import Path

import alimentazione
import dispensa
import spesa


def ricetta(id, nome, ingredienti, portate=("Cena",)):
    return {"id": id, "name": nome, "tags": {"course": [{"name": p} for p in portate]},
            "ingredients_flat": [{"type": "ingredient", "name": n, "notes": ""} for n in ingredienti]}


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    spesa.FILE = Path(tempfile.mkdtemp(prefix="prova_spesa_")) / "spesa.json"
    voci = [{"nome": n, "stato": "C'è"} for n in ["Petto di pollo", "Uova", "Spinaci a cubetti", "Riso"]]
    voci += [{"nome": "Latte", "stato": "Sta finendo"}, {"nome": "Burro", "stato": "Finito"}]
    sito = {"pollo": [ricetta(1, "Pollo e riso", ["petto di pollo", "riso", "sale e pepe"]),
                      ricetta(2, "Pollo allo yogurt", ["petto di pollo", "yogurt greco 0%"]),
                      ricetta(3, "Pollo e zucchine", ["petto di pollo", "zucchine", "yogurt greco 2% (o preferito)"]),
                      ricetta(4, "Pollo al curry proteico", ["petto di pollo", "proteine alla vaniglia"]),
                      ricetta(5, "Torta al pollo", ["pollo", "farina", "lievito", "zucchero"], ("Dessert",))],
            "uova": [ricetta(6, "Frittata di spinaci", ["uova", "spinaci", "formaggio light"]),
                     ricetta(7, "Omelette ripiena", ["uovo", "formaggio light", "skyr o yogurt greco 0%"]),
                     ricetta(2, "Pollo allo yogurt", ["petto di pollo", "yogurt greco 0%"])],
            "riso": [ricetta(8, "Riso al salmone", ["riso", "salmone", "avocado", "sesamo"]),
                     ricetta(12, "Riso e anacardi", ["riso", "anacardi"]),
                     ricetta(13, "Riso e gamberi", ["riso", "gamberi"])]}
    cercate = []

    def cerca(cosa, quante):
        cercate.append(cosa)
        return sito.get(cosa, [])

    alimentazione._cerca_ricette = cerca
    alimentazione._voci_dispensa = lambda: voci
    dispensa.configurata = lambda: True

    proposta = spesa._proposta(voci)
    atteso(sorted(cercate) == ["pollo", "riso", "spinaci", "uova"], "si cerca solo con quello che c'è in casa", cercate)
    atteso([r["name"] for r in proposta["pronte"]] == ["Pollo e riso"], "ricette con tutto in casa (dessert no)",
           proposta["pronte"])
    acquisti = {nome: sorted(r["id"] for r in ricette) for nome, ricette in proposta["acquisti"]}
    atteso(acquisti.get("yogurt greco") == [2, 3, 7] and acquisti.get("formaggio light") == [6, 7]
           and acquisti.get("zucchine") == [3] and "salmone" not in acquisti and "proteine alla vaniglia" not in acquisti
           and "anacardi" not in acquisti and "gamberi" not in acquisti,
           "yogurt greco (0%, 2%, skyr o yogurt) è un acquisto solo; niente proteine in polvere, ingredienti cari né "
           "ricette a cui mancano 3 cose", proposta["acquisti"])
    testo = spesa.consigli_spesa()
    atteso(testo.index("Yogurt greco: 3 ricette in più") < testo.index("Formaggio light: 2")
           and "Stanno finendo: Latte" in testo and "Finiti: Burro" in testo and "puoi fare 1 ricette" in testo,
           "testo del messaggio", testo)

    # --- il sabato alle 9, una volta ---
    venerdi = datetime.datetime(2026, 10, 16, 20)
    sabato = datetime.datetime(2026, 10, 17, 8, 59)
    for quando, quanti, cosa in [(venerdi, 0, "il venerdì niente"), (sabato, 0, "prima delle 9 niente"),
                                 (sabato.replace(hour=9, minute=0), 1, "sabato alle 9"),
                                 (sabato.replace(hour=12), 0, "una volta sola")]:
        spesa._adesso = lambda q=quando: q
        messaggi = spesa.controlla()
        atteso(len(messaggi) == quanti and all("🛒" in m for m in messaggi), cosa, messaggi)

    alimentazione._voci_dispensa = lambda: None
    atteso("dispensa su Notion è vuota" in spesa.consigli_spesa(), "senza dispensa lo dice", spesa.consigli_spesa())

    print(f"prova_spesa: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
