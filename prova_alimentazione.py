"""Prova dei consigli sull'alimentazione (alimentazione.py) con una cartella temporanea e un Cronometer finto: la nota
vera, Cronometer e alimentazione.json non vengono toccati, niente rete.

    .venv\\Scripts\\python prova_alimentazione.py
"""
import datetime
import sys
import tempfile
from pathlib import Path

import alimentazione
import cronometer
import dispensa
import openfood


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    cartella = Path(tempfile.mkdtemp(prefix="prova_alimentazione_"))
    alimentazione.CARTELLA = cartella / "Alimentazione"
    alimentazione.FILE = cartella / "alimentazione.json"
    cronometer.configurato = lambda: False
    dispensa.configurata = lambda: False  # la dispensa vera è su Notion: qui non si legge
    su_open_food = {}  # Open Food Facts finto: niente trovato, salvo quello che la prova mette qui
    chiesti_a_open_food = []

    def open_food(nomi):
        chiesti_a_open_food.extend(nomi)
        return {n: su_open_food.get(n) for n in nomi}

    openfood.valori = open_food
    oggi = datetime.date(2026, 10, 11)

    r = alimentazione.consigli_alimentazione()
    nota = cartella / "Alimentazione" / "Linee guida.md"
    atteso(nota.exists(), "nota copiata in Obsidian")
    atteso("mancano peso, altezza, sesso" in r, "dati mancanti nella nota nuova", r[:200])
    atteso("- deficit:" not in r and "Cosa vogliono dire" not in r and "## Il piatto" in r and "## Cosa evitare" in r,
           "linee guida senza impostazioni")

    imp = alimentazione._impostazioni(nota.read_text(encoding="utf-8"))
    atteso(imp["nascita"] == "2006-03-01" and imp["attività"] == "1.55", "impostazioni lette", imp)
    atteso(alimentazione._eta(imp["nascita"], oggi) == 20, "età prima e dopo il compleanno")
    atteso(alimentazione._eta(imp["nascita"], datetime.date(2027, 2, 12)) == 20, "età il giorno prima del compleanno")

    # 80 kg, 180 cm, M, 20 anni: riposo 1830, mantenimento 2836, −400 = 2436 → 2450; proteine 80 × 1,8 = 144
    m80 = {**imp, "peso": "80 kg", "altezza": "180", "sesso": "m"}
    r = alimentazione._obiettivi(m80, oggi)
    atteso("2450 kcal" in r and "144 g" in r and "2850 kcal" in r, "obiettivi M", r)
    n = alimentazione._numeri(m80, oggi)
    # 55,5 kg con la virgola, F, deficit enorme: non scende sotto il metabolismo a riposo (1325 → 1350)
    r = alimentazione._obiettivi({**imp, "peso": "55,5", "altezza": "165 cm", "sesso": "F", "deficit": "1500"}, oggi)
    atteso("circa 1350 kcal" in r and "100 g" in r, "obiettivi F con il minimo", r)

    # --- quote dei pasti ---
    sera, mezzogiorno = datetime.datetime(2026, 10, 12, 20), datetime.datetime(2026, 10, 12, 12)
    # senza Cronometer la cena è il 35%: 2450 × 0,35 = 857 → 860 kcal, 144 × 0,35 = 50 g
    r = alimentazione._per_pasto("cena", n, None, sera)
    atteso(r == "Per cena: circa 860 kcal e 50 g di proteine.", "cena con le quote fisse", r)
    atteso(alimentazione._quota_rimasta("cena", sera) == 1, "alle 20 la cena prende tutto quello che resta")
    atteso(abs(alimentazione._quota_rimasta("cena", mezzogiorno) - 0.35 / 0.80) < 1e-9, "a mezzogiorno pranzo e spuntino "
           "devono ancora arrivare")
    atteso(abs(alimentazione._quota_rimasta("pranzo", mezzogiorno) - 0.35 / 0.80) < 1e-9, "pranzo a mezzogiorno")
    # mangiate 1700 kcal e 100 g: restano 750 e 44, tutti alla cena
    r = alimentazione._per_pasto("cena", n, {"kcal": 1700, "proteine": 100}, sera)
    atteso(r == "Per cena, contando quello che ha già mangiato: circa 750 kcal e 44 g di proteine.", "cena col resto", r)
    # segnata solo la colazione (400 kcal): 2050 per la cena sarebbero troppi, si ferma a 1,5 × 857 = 1286 → 1290
    r = alimentazione._per_pasto("cena", n, {"kcal": 400, "proteine": 20}, sera)
    atteso("circa 1290 kcal (limitate" in r, "tetto alla cena", r)
    r = alimentazione._per_pasto("cena", n, {"kcal": 2400, "proteine": 150}, sera)
    atteso("già raggiunte" in r, "kcal già finite", r)

    # --- con Cronometer (finto) ---
    testo = nota.read_text(encoding="utf-8")
    for chiave, valore in [("peso", "80"), ("altezza", "180"), ("sesso", "M")]:
        testo = testo.replace(f"- {chiave}: \n", f"- {chiave}: {valore}\n")
    nota.write_text(testo, encoding="utf-8")
    domenica = datetime.date(2026, 10, 18)
    dati = {domenica - datetime.timedelta(days=i): {"kcal": k, "proteine": p, "carboidrati": 200, "grassi": 60,
                                                     "completo": True}
            for i, (k, p) in enumerate([(1700, 100), (2400, 150), (2300, 140), (150, 5), (2500, 120), (2200, 135),
                                        (2350, 145)])}
    guasto = [False]

    def giorni(da, a):
        if guasto[0]:
            raise RuntimeError("Cronometer finto spento")
        return {g: t for g, t in dati.items() if da <= g <= a}

    cronometer.configurato = lambda: True
    cronometer.giorni = giorni
    def cibo(nome, gruppo, kcal, proteine):
        return {"nome": nome, "quantita": "100 g", "gruppo": gruppo, "kcal": kcal, "proteine": proteine,
                "carboidrati": 0, "grassi": 0}

    # in ordine sparso, come può arrivare dal diario; "Merenda" è un gruppo con un nome personalizzato
    cronometer.cibi = lambda g: [cibo("Pasta", "Lunch", 284, 9.6), cibo("Yogurt greco", "Breakfast", 57, 10),
                                 cibo("Tonno", "Lunch", 110, 25), cibo("Frittata", "Dinner", 420, 30),
                                 cibo("Mela", "merenda", 50, 0)]
    adesso = [datetime.datetime(2026, 10, 18, 20)]
    alimentazione._adesso = lambda: adesso[0]

    r = alimentazione.consigli_alimentazione("cena")
    atteso("finora: 1700 kcal e 100 g di proteine; restano 750 kcal e 44 g" in r
           and "circa 750 kcal e 44 g" in r, "consigli con Cronometer", r[:400])
    adesso[0] = datetime.datetime(2026, 10, 19, 20)  # lunedì: niente segnato
    r = alimentazione.consigli_alimentazione("cena")
    atteso("non c'è ancora niente" in r and "Per cena: circa 860 kcal" in r, "giornata vuota: quote fisse", r[:400])
    guasto[0] = True
    r = alimentazione.consigli_alimentazione("cena")
    atteso("Cronometer non risponde" in r and "Per cena: circa 860 kcal" in r, "Cronometer guasto", r[:400])
    guasto[0] = False

    r = alimentazione.mangiato("2026-10-18")
    atteso("1700 kcal su 2450" in r and "100 g su 144" in r and "- Pasta (100 g): 284 kcal" in r, "mangiato un giorno", r)
    atteso(r.index("Colazione: 57 kcal, 10 g") < r.index("Pranzo: 394 kcal, 35 g") < r.index("Cena: 420 kcal")
           < r.index("Merenda: 50 kcal"), "kcal per pasto, in ordine", r)
    adesso[0] = datetime.datetime(2026, 10, 18, 12)
    # i giorni con meno di 500 kcal (150) non contano: 6 giorni, media 13450/6 = 2242, proteine 790/6 = 132
    r = alimentazione.mangiato("settimana")
    atteso("6 giorni segnati su 7" in r and "media 2242 kcal su 2450" in r and "132 g su 144" in r
           and "Proteine raggiunte in 4 giorni su 6" in r, "settimana", r)

    # --- riepilogo delle 21:30 ---
    atteso(alimentazione.controlla() == [], "niente prima delle 21:30")
    adesso[0] = datetime.datetime(2026, 10, 18, 21, 30)
    messaggi = alimentazione.controlla()
    atteso(len(messaggi) == 1 and "Oggi finora: 1700 kcal su 2450 e 100 g su 144" in messaggi[0]
           and "Proteine basse" in messaggi[0] and "📊 Settimana 12/10-18/10" in messaggi[0]
           and "Colazione 57 kcal · Pranzo 394 kcal · Cena 420 kcal · Merenda 50 kcal" in messaggi[0],
           "riepilogo domenica", messaggi)
    atteso(alimentazione.controlla() == [], "una volta sola al giorno")
    adesso[0] = datetime.datetime(2026, 10, 17, 22)
    r = alimentazione.controlla()
    atteso(len(r) == 1 and "📊" not in r[0] and "Proteine basse" not in r[0], "sabato senza settimana", r)
    adesso[0] = datetime.datetime(2026, 10, 16, 22)
    guasto[0] = True
    for _ in range(3):
        alimentazione._pausa_fino = 0.0
        r = alimentazione.controlla()
    atteso(len(r) == 1 and "Cronometer non risponde" in r[0], "dopo 3 errori lo dice", r)

    # --- CSV di Cronometer ---
    riga = {"Date": "2026-10-18", "Energy (kcal)": "1712.5", "Net Carbs (g)": "150", "Carbs (g)": "180.2",
            "Fat (g)": "55", "Saturated (g)": "20", "Protein (g)": "101.3", "Completed": "true"}
    atteso(cronometer._macro(riga) == {"kcal": 1712.5, "proteine": 101.3, "carboidrati": 180.2, "grassi": 55}
           and cronometer._data(riga) == domenica, "colonne del CSV", cronometer._macro(riga))

    # il 12/10: la cotoletta Lidl nel dettaglio senza valori, ma contata nel totale del giorno
    cotoletta = {"nome": "Lidl, Cotoletta", "quantita": "200.00 g", "gruppo": "Dinner", "kcal": 0.0, "proteine": 0.0,
                 "carboidrati": 0.0, "grassi": 0.0}
    yogurt = {"nome": "Yogurt", "quantita": "150 g", "gruppo": "Breakfast", "kcal": 90.0, "proteine": 15.0,
              "carboidrati": 6.0, "grassi": 0.0}
    totali = {"kcal": 586.0, "proteine": 37.0, "carboidrati": 36.0, "grassi": 32.0}
    r = alimentazione._giorno_testo(oggi, totali, [yogurt, cotoletta], None)
    atteso("Cena: 496 kcal, 22 g di proteine" in r and "(dal totale del giorno" in r and "Colazione: 90 kcal" in r,
           "un cibo senza valori prende la differenza col totale", r)
    r = alimentazione._giorno_testo(oggi, totali, [yogurt, cotoletta, {**cotoletta, "nome": "Lidl, Burger"}], None)
    atteso("Senza valori nel dettaglio di Cronometer: Lidl, Cotoletta, Lidl, Burger; insieme fanno circa 496 kcal" in r,
           "più cibi senza valori: la differenza a parte", r)

    # ingredienti delle ricette di Fit with Fun: il grano saraceno finiva su "parmigiano o grana", il cacao magro sullo
    # yogurt magro; dolcificanti e lievito non hanno kcal, le proteine alla vaniglia sì
    r = alimentazione.calcola_ricetta("farina di grano saraceno 100, cacao magro 10, albume 100, funghi 100, "
                                      "eritritolo 10, lievito per dolci 5, proteine alla vaniglia 30")
    atteso("(come grano saraceno): 340 kcal" in r and "(come cacao amaro)" in r and "(come albume): 50 kcal" in r
           and "(come verdure)" in r and "(come proteine in polvere): 114 kcal" in r and "eritritolo" not in r
           and "Non contati" not in r, "ingredienti di Fit with Fun", r)

    # Open Food Facts: solo per quello che nella tabella non c'è uguale; se non lo trova vale la riga più vicina
    chiesti_a_open_food.clear()
    su_open_food.update({"mozzarella light": {"kcal": 163, "proteine": 18, "prodotti": 12, "esempio": "Mozzarella light"},
                         "piadina": {"kcal": 300, "proteine": 8, "prodotti": 9, "esempio": "Piadina romagnola"}})
    r = alimentazione.calcola_ricetta("petto di pollo 150, mozzarella light 100, piadina 60, uova 2, "
                                      "zucchine 200, skyr 100, sale 1, proteine al cioccolato 30, olio evo 10, "
                                      "olio extravergine di oliva 5")
    atteso(sorted(chiesti_a_open_food) == ["mozzarella light", "piadina", "skyr"]
           and "petto pollo 150 g (come petto di pollo): 165 kcal" in r
           and "mozzarella light 100 g (Open Food Facts, mediana di 12 prodotti" in r and "163 kcal, 18.0 g" in r
           and "piadina 60 g (Open Food Facts" in r and "(come proteine in polvere)" in r
           and "olio evo 10 g (come olio extravergine d'oliva): 90 kcal" in r
           and "olio extravergine oliva 5 g (come olio extravergine d'oliva): 45 kcal" in r and "uova 100 g (come uova)" in r and "(come verdure)" in r
           and "Non contati (non sono né nella tabella della nota né su Open Food Facts, o manca la quantità): skyr 100"
           in r, "tabella per i nomi uguali, Open Food Facts per gli altri", r)
    for nome, prodotto, giusto in [("olio extravergine di oliva", "Olio extra vergine di oliva", True),
                                   ("olio extravergine di oliva", "Focaccelle con olio extravergine di oliva", False),
                                   ("mozzarella light", "Santa Lucia Mozzarella Light Tris", True),
                                   ("mozzarella light", "Mozzarella", False),
                                   ("ceci lessati", "Ceci lessati bio", True),
                                   ("pasta integrale", "Sugo per pasta integrale", False)]:
        atteso(openfood._adatto(nome, prodotto) == giusto, f"Open Food Facts: {prodotto!r} per {nome!r}")

    # --- ricette di Fit with Fun (sito finto: niente rete) ---
    def ricetta(id, nome, ingredienti, portate=("Cena", "Pranzo")):
        return {"id": id, "parent_post_id": str(id + 1000), "name": nome, "nutrition": {"calories": 400}, "servings": 1,
                "tags": {"course": [{"name": p} for p in portate]},
                "servings_unit": "persona", "total_time": "10",
                "ingredients_flat": [{"type": "ingredient", "amount": "1", "unit": "", "name": n, "notes": ""}
                                     for n in ingredienti],
                "instructions_flat": [{"type": "instruction", "text": "<p>Cuoci tutto &amp; servi.</p>"}]}

    sito = {"pollo": [ricetta(1, "Pollo al curry", ["petto di pollo", "curry"]),
                      ricetta(2, "Pollo e piselli", ["petto di pollo", "piselli"])],
            "piselli": [ricetta(2, "Pollo e piselli", ["petto di pollo", "piselli"]),
                        ricetta(3, "Vellutata di piselli", ["piselli", "patate"])],
            "ceci": [ricetta(4, "Brownies ai ceci", ["ceci", "cacao"], ("Dessert", "Merenda")),
                     ricetta(5, "Burger di ceci", ["ceci", "quinoa"], ("Main Course",))]}
    cercate = []

    def cerca(cosa, quante):
        cercate.append(cosa)
        return sito.get(cosa, [])[:quante]

    alimentazione._cerca_ricette = cerca
    alimentazione._link_ricette = lambda articoli: {1002: "https://fitwithfun.it/pollo-e-piselli/"}
    r = alimentazione.ricette_fit_with_fun("un'idea per cena col pollo")
    atteso(cercate == ["cena pollo", "cena", "pollo"] and "Pollo al curry" in r, "parole inutili tolte", (cercate, r))
    cercate.clear()
    r = alimentazione.ricette_fit_with_fun("pollo e piselli")
    atteso(cercate == ["pollo piselli", "pollo", "piselli"] and r.index("Pollo e piselli") < r.index("Pollo al curry")
           and r.count("Pollo e piselli") == 1, "parola per parola, prima chi le ha tutte", (cercate, r))
    atteso("Cuoci tutto & servi." in r and "Link: https://fitwithfun.it/pollo-e-piselli/" in r
           and "Link: https://fitwithfun.it/?p=1001" in r and "400 kcal a porzione" in r,
           "testo della ricetta (link vero, o ?p= se il sito non lo dà)", r)
    r = alimentazione.ricette_fit_with_fun("tofu")
    atteso("nessuna ricetta" in r, "niente trovato", r)
    r = alimentazione.ricette_fit_with_fun("ceci", "pranzo")
    atteso("Burger di ceci" in r and "Brownies" not in r, "solo le ricette da pranzo", r)
    r = alimentazione.ricette_fit_with_fun("ceci", "spuntino")
    atteso("Brownies" in r and "Burger" not in r, "solo le ricette da spuntino", r)
    r = alimentazione.ricette_fit_with_fun("ceci", "colazione")
    atteso("nessuna ricetta da colazione" in r, "niente da colazione", r)
    atteso("dispensa" not in r.lower(), "senza dispensa non si parla di cosa manca", r)

    # --- con la dispensa: prima la ricetta con meno ingredienti che mancano, e l'avviso ---
    alimentazione._voci_dispensa = lambda: [{"nome": n, "stato": "C'è"} for n in ["Petto di pollo", "Piselli"]]
    r = alimentazione.ricette_fit_with_fun("pollo")
    atteso(r.index("Pollo e piselli") < r.index("Pollo al curry") and "In dispensa c'è tutto" in r
           and "Non in dispensa: curry" in r and "di' SEMPRE" in r, "ricette ordinate per cosa c'è in casa", r)
    r = alimentazione.calcola_ricetta("petto di pollo 150, piselli 100, limone 10, sale 1")
    atteso("NON IN DISPENSA: limone." in r and "Totale" in r, "calcola_ricetta avvisa di cosa manca (sale no)", r)
    r = alimentazione.calcola_ricetta("petto di pollo 150, piselli 100")
    atteso("In dispensa ci sono tutti gli ingredienti." in r, "calcola_ricetta: c'è tutto", r)
    r = alimentazione.calcola_ricetta("peperone 100")
    atteso("(come verdure)" in r, "il peperone ha le kcal (non è pepe)", r)

    print(f"prova_alimentazione: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
