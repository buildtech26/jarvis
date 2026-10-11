"""Prova della dispensa (dispensa.py) con un Notion, un Cronometer e un Gemini finti: il database vero e
dispensa.json non vengono toccati, niente rete.

    .venv\\Scripts\\python prova_dispensa.py
"""
import datetime
import sys
import tempfile
import uuid
from pathlib import Path

import cronometer
import dispensa
import notion


class NotionFinto:
    """Le chiamate che usa dispensa.py, su un dizionario di pagine; Stato come la formula del database."""

    def __init__(self):
        self.pagine: dict[str, dict] = {}

    def _pagina(self, id: str) -> dict:
        props = self.pagine[id]
        q, acquisto = props["Quantità"]["number"], props["Ultimo acquisto"]["number"]
        stato = ("Finito" if (q or 0) <= 0 else "Sta finendo" if (acquisto or 0) > 0 and q <= acquisto * 0.25
                 else "C'è")
        return {"id": id, "properties": {**props, "Stato": {"formula": {"type": "string", "string": stato}}}}

    def __call__(self, metodo: str, percorso: str, **dati) -> dict:
        if percorso.endswith("/query"):
            ordinate = sorted(self.pagine, key=lambda i: self.pagine[i]["Nome"]["title"][0]["text"]["content"])
            return {"results": [self._pagina(i) for i in ordinate], "has_more": False}
        if metodo == "POST" and percorso == "/pages":
            id = str(uuid.uuid4())
            vuote = {"Quantità": {"number": None}, "Unità": {"select": None}, "Dove": {"select": None},
                     "Ultimo acquisto": {"number": None}, "Nomi Cronometer": {"rich_text": []}}
            self.pagine[id] = {**vuote, **self._normali(dati["properties"])}
            return self._pagina(id)
        if metodo == "PATCH":
            id = percorso.rsplit("/", 1)[1]
            if dati.get("in_trash"):
                del self.pagine[id]
                return {}
            self.pagine[id].update(self._normali(dati["properties"]))
            return self._pagina(id)
        raise AssertionError(f"chiamata inattesa {metodo} {percorso}")

    @staticmethod
    def _normali(props: dict) -> dict:
        """Come le restituisce Notion: i testi hanno plain_text."""
        fuori = {}
        for nome, valore in props.items():
            if "title" in valore or "rich_text" in valore:
                chiave = "title" if "title" in valore else "rich_text"
                fuori[nome] = {chiave: [{**t, "plain_text": t["text"]["content"]} for t in valore[chiave]]}
            else:
                fuori[nome] = valore
        return fuori

    def voce(self, nome: str) -> dict:
        return next(dispensa._voce(self._pagina(i)) for i in self.pagine
                    if self.pagine[i]["Nome"]["title"][0]["text"]["content"] == nome)


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    cartella = Path(tempfile.mkdtemp(prefix="prova_dispensa_"))
    dispensa.FILE = cartella / "dispensa.json"
    finto = NotionFinto()
    notion._chiama = finto
    dispensa.configurata = lambda: True

    # --- lettura dei prodotti detti a parole ---
    p = dispensa._leggi_prodotti("1 kg di pasta, 6 uova, latte 1 l (frigo), piselli surgelati 450g nel freezer, "
                                 "mezzo etto di parmigiano")
    atteso([(x["nome"], x["quantita"], x["unita"], x["dove"]) for x in p] ==
           [("Pasta", 1000, "g", None), ("Uova", 6, "pz", None), ("Latte", 1000, "ml", "Frigo"),
            ("Piselli surgelati", 450, "g", "Freezer"), ("Mezzo etto di parmigiano", None, None, None)],
           "prodotti letti", p)

    r = dispensa.dispensa()
    atteso("non c'è niente" in r, "dispensa vuota", r)

    # --- aggiungi ---
    r = dispensa.aggiorna_dispensa("pasta 1 kg, uova 6, latte 1 l frigo, fiocchi di latte 200 g frigo")
    atteso("Pasta: nuovo, 1 kg in dispensa" in r and "Uova: nuovo, 6 pz" in r and "Latte: nuovo, 1 l in frigo" in r
           and "Fiocchi di latte: nuovo" in r, "prima spesa", r)
    r = dispensa.aggiorna_dispensa("pasta 500 g")
    atteso("Pasta: ora 1,5 kg" in r and finto.voce("Pasta")["acquisto"] == 1500, "pasta in più", r)
    r = dispensa.aggiorna_dispensa("latte intero 500 ml")
    atteso("Latte: ora 1,5 l" in r and finto.voce("Fiocchi di latte")["quantita"] == 200,
           "'latte intero' va sul latte, non sui fiocchi di latte", r)

    # --- togli, imposta, finito ---
    r = dispensa.aggiorna_dispensa("uova 5", "togli")
    atteso("Uova: 1 pz" in r, "uova usate", r)
    r = dispensa.dispensa()
    atteso("Uova 1 pz (sta finendo)" in r and "Frigo: Fiocchi di latte 200 g; Latte 1,5 l" in r, "elenco per posto", r)
    r = dispensa.aggiorna_dispensa("pasta 300 g", "imposta")
    atteso("Pasta: 300 g" in r, "correzione della quantità", r)
    r = dispensa.aggiorna_dispensa("latte", "finito")
    atteso("Latte: finito" in r, "latte finito", r)
    r = dispensa.dispensa()
    atteso("Finiti: Latte" in r and "Latte 1,5" not in r, "i finiti a parte", r)
    r = dispensa.dispensa("frigo")
    atteso(r.startswith("Frigo:") and "Pasta" not in r, "solo il frigo", r)
    r = dispensa.aggiorna_dispensa("latte 1 l")
    atteso("Latte: ora 1 l" in r, "latte ricomprato", r)
    r = dispensa.aggiorna_dispensa("latte")
    atteso("Latte: ora 2 l" in r, "ricomprato senza quantità: quella dell'ultima spesa", r)
    r = dispensa.aggiorna_dispensa("pasta 2 pz", "togli")
    atteso("in dispensa è in g, non in pz" in r and finto.voce("Pasta")["quantita"] == 300, "unità diversa", r)
    r = dispensa.aggiorna_dispensa("tonno 2", "togli")
    atteso("Non in dispensa o non cambiati: Tonno" in r, "prodotto che non c'è", r)
    r = dispensa.aggiorna_dispensa("fiocchi di latte", "elimina")
    atteso("tolto dalla dispensa" in r and "Fiocchi" not in dispensa.dispensa(), "voce eliminata", r)
    atteso("azione non valida" in dispensa.aggiorna_dispensa("pasta", "mangia").lower(), "azione sbagliata")

    r = dispensa.in_casa()
    atteso(r.startswith("In casa (dispensa su Notion") and "Pasta 300 g" in r and "Stanno finendo: Pasta, Uova" in r,
           "riga per i consigli", r)

    # --- Cronometer: si scala ---
    giorno = datetime.date(2026, 10, 12)
    cibi = [{"nome": "Pasta, Dry", "quantita": "80.00 g", "gruppo": "Lunch"},
            {"nome": "Egg, Whole", "quantita": "1.00 large", "gruppo": "Dinner"},
            {"nome": "Pizza margherita", "quantita": "1.00 slice", "gruppo": "Dinner"}]
    cronometer.cibi = lambda g: list(cibi) if g == giorno else []
    cronometer.configurato = lambda: True
    richieste = []

    def gemini(testo):
        richieste.append(testo)
        voci = testo.split("Dispensa:\n")[1].split("\n\nCibi mangiati:")[0].splitlines()
        numero = {riga.split(". ", 1)[1].split(" (")[0]: int(riga.split(".")[0]) for riga in voci}
        risposta = []
        for i, riga in enumerate(testo.split("Cibi mangiati:\n")[1].splitlines(), 1):
            nome, quantita = riga.split(". ", 1)[1].split(" — ")
            if nome == "Pasta, Dry":
                risposta.append({"cibo": i, "voce": numero.get("Pasta", 0), "togli": float(quantita.split()[0])})
            elif nome == "Egg, Whole":
                risposta.append({"cibo": i, "voce": numero.get("Uova", 0), "togli": 1})
            else:
                risposta.append({"cibo": i, "voce": 0, "togli": 0})
        return risposta

    dispensa._gemini = gemini
    r = dispensa.scala(giorno)
    atteso(r == "🥫 Dalla dispensa ho tolto: Pasta 80 g, Uova 1 pz. Finiti: Uova.", "scalati", r)
    atteso(finto.voce("Pasta")["quantita"] == 220 and finto.voce("Pasta")["cronometer"] == ["Pasta, Dry"]
           and finto.voce("Uova")["quantita"] == 0, "quantità e nomi di Cronometer in Notion",
           (finto.voce("Pasta"), finto.voce("Uova")))
    atteso("1. Latte (ml)" in richieste[0] and "2. Pasta (g)" in richieste[0] and "Pizza margherita — 1.00 slice"
           in richieste[0], "domanda a Gemini", richieste[0])
    atteso(dispensa.scala(giorno) == "" and len(richieste) == 1, "una volta sola")
    # più tardi segna un'altra porzione di pasta: il nome è già abbinato, niente Gemini
    cibi.append({"nome": "Pasta, Dry", "quantita": "80.00 g", "gruppo": "Dinner"})
    r = dispensa.scala(giorno)
    atteso(r == "🥫 Dalla dispensa ho tolto: Pasta 80 g." and len(richieste) == 1
           and finto.voce("Pasta")["quantita"] == 140, "nome già abbinato senza Gemini", r)

    # --- turni del bot ---
    dispensa.FILE.unlink()
    finto.pagine.clear()
    dispensa.aggiorna_dispensa("pasta 1 kg")
    adesso = [datetime.datetime(2026, 10, 12, 20)]
    dispensa._adesso = lambda: adesso[0]
    atteso(dispensa.controlla() == [], "prima delle 21:31 niente")
    adesso[0] = datetime.datetime(2026, 10, 12, 21, 31)
    r = dispensa.controlla()
    atteso(len(r) == 1 and "Pasta 160 g" in r[0], "sera", r)
    atteso(dispensa.controlla() == [], "una volta per sera")
    cibi.append({"nome": "Pasta, Dry", "quantita": "100.00 g", "gruppo": "Dinner"})  # segnata dopo le 21:31
    adesso[0] = datetime.datetime(2026, 10, 13, 10)
    r = dispensa.controlla()
    atteso(len(r) == 1 and "ho tolto anche (pasti di ieri segnati tardi): Pasta 100 g" in r[0], "mattina dopo", r)
    # al primo avvio i giorni prima della dispensa non si scalano
    dispensa.FILE.unlink()
    adesso[0] = datetime.datetime(2026, 10, 13, 10)
    atteso(dispensa.controlla() == [], "ieri è prima dell'inizio")
    errori_prima = dispensa._errori["volte"]
    atteso(errori_prima == 0, "nessun errore contato", errori_prima)

    print(f"prova_dispensa: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
