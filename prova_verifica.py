"""Prova di verifica.py, senza Gemini e senza rete: da rilanciare dopo ogni modifica al controllo.

    .venv\\Scripts\\python prova_verifica.py

I casi "inventati" vengono dal diario del 07/10; quelli "buoni" sono risposte corrette che non devono far scattare
un secondo tentativo (ogni falso allarme costa una richiesta Gemini e un avviso inutile).
"""
import sys

import verifica

VOLI = ("esplora_voli: CRITERI USATI: da BLQ, 13-17/11, 3 notti\n"
        "40 € | Parigi (BVA, Francia) | 13/11 → 16/11, 3 notti | Ryanair\n"
        "45 € | Barcellona (GRO, Spagna) | 13/11 → 16/11, 3 notti | Ryanair\n"
        "56 € | Budapest (BUD, Ungheria) | 14/11 → 17/11, 3 notti | Ryanair")
RYANAIR = "cerca_voli: 16.99 € | Ryanair | BLQ 06:25 → TIA 08:10\n14.99 € | Ryanair | TIA 21:05 → BLQ 22:50"
SPESE = "spese_riepilogo: Mese: 1.234,56 € spesi, Spesa 312,40 €, Ristoranti 98,00 €"
TRENI = "cerca_treni: Frecciarossa 9409 | 14:10 → 16:25 | da 39 €\nItalo 8911 | 14:35 → 16:40"

# (risposta, fonti, strumenti usati in questa domanda, risultati di questa domanda, dubbi attesi: parole che devono
# comparire tra i dubbi; lista vuota = nessun dubbio)
CASI = [
    # --- inventati (diario 07/10 e 03/10) ---
    ("Uso Google Flights, che controlla tutte le compagnie. I risultati includono Ryanair, Wizz Air e le principali.",
     [VOLI, "hai guardato solo ryanair?"], [], [], ["Wizz Air"]),
    ("Ho impostato tre notti a metà novembre, Mattia. Praga e Vienna restano le più convenienti, intorno ai 40 o 50 €.",
     [VOLI, "puoi cercare per tre notti"], [], [], ["50 €", "ho impostato"]),
    ("Prendi il Frecciarossa 9409 delle 14:10, costa 39 €.",
     ["come arrivo a Roma domani?"], [], [], ["9409", "14:10", "39 €", "Frecciarossa"]),
    ("Ho aperto Google Flights con la ricerca per tre notti.", ["mostrami dal sito"], [], [], ["ho aperto"]),
    # diario 06/10: la voce ha captato una frase non rivolta a Jarvis
    ("Fatto. Speriamo che Maurizio sia più fortunato di Germanono.", ["ma di germanono non viene, manda maurizio"],
     [], [], ["fatto"]),
    ("Ecco fatto, Mattia.", ["spegni la luce"], [], [], ["ecco fatto"]),
    ("Ti ho appena aperto Google Maps.", ["aprimi maps"], [], [], ["aperto"]),
    ("Va bene. Ho fissato la call per venerdì.", ["fissa la call venerdì"], [], [], ["ho fissato"]),
    ("Ah, la matematica non è il mio forte. Provvedo subito a inserire le lezioni nei giorni corretti.",
     ["non tutti i giorni ma solo martedì giovedi e venerdì"], [], [], ["provvedo"]),
    ("Le metto subito in calendario.", ["segnale"], [], [], ["metto subito"]),
    ("Se vuoi lo faccio domani, intanto riposa.", ["ok"], [], [], []),
    # --- buoni ---
    ("Parigi a 40 €, Barcellona a 45 € o Budapest a 56 €, tutte con Ryanair.",
     [VOLI, "4 giorni fuori l'italia a metà novembre"], ["esplora_voli"], [VOLI], []),
    ("No, solo Ryanair: gli altri voli trovati costavano di più.", [VOLI, "hai guardato solo ryanair?"], [], [], []),
    ("Tirana andata e ritorno a 31,98 €: 16,99 € all'andata e 14,99 € al ritorno, partenza alle 06:25.",
     [RYANAIR, "voli per tirana"], ["cerca_voli"], [RYANAIR], []),
    ("Questo mese hai speso circa 1.235 €, di cui 312,40 € di spesa.", [SPESE], ["spese_riepilogo"], [SPESE], []),
    ("C'è il Frecciarossa 9409 alle 14:10 da 39 €, oppure l'Italo 8911 alle 14:35.",
     [TRENI, "che treno prendo per Roma?"], ["cerca_treni"], [TRENI], []),
    ("Fatto, ho segnato la call in calendario per stasera alle 20:30.",
     ["crea_evento: Evento creato: Call, 10/10 20:30–21:30", "mi metti in calendario stasera alle 20:30 una call"],
     ["crea_evento"], ["crea_evento: Evento creato: Call, 10/10 20:30–21:30"], []),
    ("Con 300 € di budget ci stai largo.", ["ho un budget di 300 € per il weekend"], [], [], []),
    ("Ti sveglio alle 8:00 allora.", ["svegliami alle 8"], [], [], []),
    ("Esci alle 13:40 per prendere il treno con calma.", [TRENI, "a che ora esco?"], ["cerca_treni"], [TRENI], []),
    ("La Francia ha circa 68 milioni di abitanti, signore.", ["quanti abitanti ha la Francia?"], [], [], []),
    ("Buongiorno Mattia, cosa posso fare per lei?", ["ciao"], [], [], []),
    ("Di fatto conviene Barcellona. Il fatto è che a novembre piove meno.", ["meglio Parigi o Barcellona?"], [], [], []),
    ("Ricevuto, li tengo d'occhio entrambi.", ["lascia li"], [], [], []),
    ("Fatto, promemoria cancellato.", ["togli questo promemoria"], ["cancella_promemoria"], ["cancellato"], []),
    # prova reale del 10/10: vero, parla della ricerca della domanda prima (strumenti_prima)
    ("Ho cercato solo su Ryanair. Se preferisci apro Google Flights per le altre compagnie.",
     [VOLI, "hai guardato solo ryanair?"], [], [], [], True),
    ("Ho impostato la ricerca per tre notti.", [VOLI, "puoi cercare per tre notti"], [], [], ["ho impostato"], True),
]


def main() -> int:
    errori = 0
    for risposta, fonti, usati, ora, attesi, *prima in CASI:
        dubbi = verifica.controlla(risposta, fonti, usati, ora, bool(prima and prima[0]))
        testo = " | ".join(dubbi).lower()
        mancanti = [a for a in attesi if a.lower() not in testo]
        if mancanti or (not attesi and dubbi):
            errori += 1
            print(f"SBAGLIATO: {risposta[:70]!r}\n  dubbi trovati: {dubbi}\n  attesi: {attesi}")
    print(f"{len(CASI)} casi, errori: {errori}")
    print("esempio di avviso:", verifica.con_avviso("Ci sono voli Wizz Air.", ["Wizz Air", "«ho aperto» senza aver usato strumenti"]))
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
