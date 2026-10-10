"""Prova di gruppi.py, senza Gemini e senza rete: da rilanciare dopo ogni modifica alle parole o ai gruppi.

    .venv\\Scripts\\python prova_gruppi.py

Controlla che ogni strumento stia in un gruppo, che per ogni domanda tipica lo strumento che serve sia tra quelli
mandati a Gemini, e che le domande generali non attivino gruppi a caso.
"""
import sys

import gruppi
import tools

# (domande precedenti + domanda, strumento che deve esserci)
CASI = [
    (["che notizie ci sono oggi?"], "notizie"),
    (["chi ha vinto la partita del Bologna ieri sera?"], "cerca_web"),
    (["riassumimi questa pagina https://example.com"], "leggi_pagina"),
    (["cercami su youtube un tutorial di blender"], "cerca_nel_browser"),
    (["ricorda che il mio colore preferito è il blu"], "ricorda"),
    (["dimentica che abito a Padova"], "dimentica"),
    (["apri il file della tesi"], "cerca_file"),
    (["alza il volume"], "volume"),
    (["metti in pausa la musica"], "multimediale"),
    (["blocca il computer"], "blocca_pc"),
    (["ricordami tra 20 minuti di togliere la pasta"], "crea_promemoria"),
    (["quali promemoria ho?"], "lista_promemoria"),
    (["cancella il promemoria della pasta"], "cancella_promemoria"),
    (["quanto costa un volo per Londra a novembre?"], "cerca_voli"),
    (["fammi vedere i voli per Barcellona"], "mostra_voli"),
    (["vorrei farmi 3 giorni da qualche parte fuori l'italia a novembre"], "esplora_voli"),
    (["dove posso andare con poco a dicembre?"], "esplora_voli"),
    (["un weekend all'estero il mese prossimo"], "esplora_voli"),
    (["che treno prendo per Milano domani mattina?"], "cerca_treni"),
    (["come arrivo a Firenze sabato?"], "cerca_treni"),
    (["apri Trenitalia per Roma"], "mostra_treni"),
    (["il Frecciarossa 9409 è in ritardo?"], "stato_treno"),
    (["da che binario parte il mio treno?"], "stato_treno"),
    (["seguimi il treno delle 8"], "segui_treno"),
    (["quando passa il 20 in via Irnerio?"], "bus_in_arrivo"),
    (["quanto dista Modena da Bologna?"], "distanza"),
    (["mostrami il percorso per casa di Luca"], "mostra_percorso"),
    (["trovami un appartamento a Lisbona per il weekend"], "cerca_alloggi"),
    (["dove dormo a Napoli il 12?"], "cerca_alloggi"),
    (["dove ho parcheggiato?"], "dove_ho_parcheggiato"),
    (["che impegni ho giovedì?"], "eventi_calendario"),
    (["segna dentista venerdì alle 15"], "crea_evento"),
    (["sposta la riunione a domani"], "sposta_evento"),
    (["cosa c'è negli appunti di meccanica sui rotismi?"], "cerca_notion"),
    (["aggiungi alle cose da fare la relazione di fisica"], "aggiungi_attivita"),
    (["ho finito la consegna di reti logiche"], "completa_attivita"),
    (["ho mail nuove?"], "leggi_mail"),
    (["cosa c'è nell'allegato della mail del prof?"], "leggi_allegato"),
    (["rispondi alla mail di Giulia che va bene"], "prepara_risposta"),
    (["scrivi una mail a Marco per dirgli che arrivo tardi"], "scrivi_mail"),
    (["archivia la mail di Amazon"], "archivia_mail"),
    (["cosa ti avevo chiesto ieri?"], "leggi_diario"),
    (["quel volo che avevamo guardato la settimana scorsa"], "cerca_diario"),
    (["quell'articolo che ho salvato sugli ETF"], "cerca_articoli"),
    (["fai una ricerca su VWCE e salvala in Obsidian"], "ricerca_e_salva"),
    (["cosa sai sull'ETF SWDA?"], "cerca_sapere"),
    (["cosa avevi trovato sui mini PC?"], "cerca_sapere"),
    (["che ricerche hai fatto?"], "cerca_sapere"),
    (["fammi una lista della spesa con latte e uova"], "crea_file"),
    (["aggiungi il pane alla lista della spesa"], "aggiungi_a_file"),
    (["leggimi il file lista-spesa"], "leggi_file"),
    (["che file hai creato?"], "elenca_file"),
    (["analizza Apple"], "analizza_azienda"),
    (["com'è messa Eni come azienda?"], "analizza_azienda"),
    (["Nvidia è cara?"], "analizza_azienda"),
    (["ci sono bandiere rosse sui conti di Ferrari?"], "analizza_azienda"),
    (["quanto ho speso in ristoranti questo mese?"], "spese_riepilogo"),
    (["come sto col budget?"], "spese_riepilogo"),
    (["quanto ho speso da Esselunga?"], "movimenti_spese"),
    (["cosa ho pagato ieri?"], "movimenti_spese"),
    (["quante richieste gemini ho fatto oggi?"], "consumi"),
    (["che esercizi devo fare domani?"], "seduta_allenamento"),
    (["quanti allenamenti ho fatto questa settimana?"], "allenamenti_settimana"),
    (["come rendo più difficili gli squat?"], "scheda_allenamento"),
    (["organizzami gli allenamenti della prossima settimana"], "pianifica_allenamenti"),
    (["oggi ho saltato la corsa"], "segna_allenamento"),
    (["ho fatto il corpo libero"], "segna_allenamento"),
    (["cosa mi cucino stasera con ceci e riso?"], "consigli_alimentazione"),
    (["quante calorie devo mangiare per dimagrire?"], "consigli_alimentazione"),
    (["quanta pasta posso mangiare a pranzo?"], "consigli_alimentazione"),
    (["cosa compro di proteico che costi poco?"], "consigli_alimentazione"),
    (["ho sempre fame la sera, che faccio?"], "consigli_alimentazione"),
    # seguiti: la domanda da sola non dice niente, la precedente sì
    (["che treno prendo per Milano domani?", "e dopodomani?"], "cerca_treni"),
    (["quanto ho speso questo mese?", "e il mese scorso?"], "spese_riepilogo"),
    (["ho mail nuove?", "aprimi la seconda"], "apri_mail"),
    # la conversazione vera del 07/10: due domande senza indizi dopo quella sui voli
    (["un viaggio di 4 giorni fuori l'italia a metà novembre", "puoi farmi vedere dal sito", "hai guardato solo ryanair?",
      "mostrami dal sito"], "mostra_voli"),
    (["un viaggio di 4 giorni fuori l'italia a metà novembre", "puoi farmi vedere dal sito", "puoi cercare per tre notti"],
     "esplora_voli"),
]

# domande che devono restare leggere: se attivano tutti i gruppi qualcosa è troppo largo
LEGGERE = ["che tempo fa a Bologna?", "chi è il presidente della Francia?", "apri Spotify"]


def main() -> int:
    errori = 0
    in_gruppi = {n for _, nomi, _ in gruppi.GRUPPI.values() for n in nomi}
    for f in tools.TUTTI:
        if f.__name__ not in in_gruppi:
            print(f"SENZA GRUPPO: {f.__name__} (finirebbe solo nelle domande senza indizi)")
            errori += 1
    for nome in sorted(in_gruppi - {f.__name__ for f in tools.TUTTI}):
        print(f"(non attivo, probabilmente non configurato: {nome})")

    tutti = len(tools.TUTTI)
    pesi = []
    for domande, atteso in CASI:
        scelti = gruppi.per_conversazione(domande)
        strumenti, _ = gruppi.strumenti_e_regole(scelti, tools.PER_NOME)
        nomi = [f.__name__ for f in strumenti]
        if atteso not in tools.PER_NOME:
            continue  # strumento spento (es. mail non configurata): niente da verificare
        if atteso not in nomi:
            print(f"MANCA {atteso:20} per {domande[-1]!r} (gruppi: {', '.join(scelti)})")
            errori += 1
        pesi.append(len(nomi))
    for d in LEGGERE:
        scelti = gruppi.scegli([d])
        if len(scelti) > 2:
            print(f"TROPPO LARGO: {d!r} attiva {', '.join(scelti)}")
            errori += 1
    print(f"{len(CASI)} domande, strumenti mandati in media {sum(pesi) / len(pesi):.1f} su {tutti}; errori: {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
