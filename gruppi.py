"""Quali strumenti (e quali regole del prompt) mandare a Gemini per una domanda.

Con tutti gli strumenti ogni chiamata pesava ~7.200 token di dichiarazioni più ~1.700 di regole, e il modello
Flash-Lite sceglie peggio quando la lista è lunga. Qui le parole della domanda (più l'ultimo argomento delle 3
precedenti, per i seguiti come "e domani?" o "fammeli vedere") scelgono i gruppi; il gruppo base (web, meteo, notizie, browser, memoria) c'è sempre e basta da
solo per le domande generali. Se con il solo base Gemini risponde "non posso", brain.py riprova con TUTTI.
Le prove sono in prova_gruppi.py: da rilanciare dopo ogni modifica a parole o gruppi.
"""
import re

# nome: (parole che lo attivano, strumenti, regole per il prompt di sistema)
GRUPPI: dict[str, tuple[str, list[str], str]] = {
    "base": (
        r"",
        ["cerca_web", "cerca_nel_browser", "notizie", "meteo", "leggi_pagina", "apri_sito", "apri_app", "ricorda",
         "dimentica", "consumi"],
        """- domande su fatti recenti, persone, risultati sportivi, prezzi o qualsiasi cosa tu non sappia con certezza: cerca_web \
(cerchi tu e rispondi a parole)
- se l'utente vuole VEDERE i risultati ("aprimi", "fammi vedere", "cerca su Chrome/Google/YouTube/Amazon/Maps", \
"apri Chrome e cerca", "trovami su AliExpress/eBay/..."): cerca_nel_browser con sito = il sito nominato, poi rispondi solo con una frase breve. Non aprire \
mai il browser se l'utente non l'ha chiesto: per rispondere a una domanda usa gli altri strumenti
- notizie: notizie; meteo: meteo
- leggere o riassumere un sito o un link preciso: leggi_pagina
- "apri ..." un programma: apri_app; un sito: apri_sito
- "quanto ho consumato", richieste Gemini, quota, ricerche SerpApi: consumi
- memoria permanente: usa ricorda quando l'utente chiede di ricordare qualcosa o dice un fatto stabile su di sé \
(persone, preferenze, abitudini, luoghi); non salvare chiacchiere, cose passeggere o dati sensibili come password. \
"Dimentica che..." = dimentica. Non dire mai "lo terrò a mente" o "memorizzato" senza aver chiamato ricorda""",
    ),
    "pc": (
        r"\bfile\b|cartell|document|pdf|scaric|\bapri|volume|audio|muto|musica|canzon|brano|traccia|spotify|pausa|"
        r"blocca|schermo|\bpc\b|computer",
        ["cerca_file", "apri_file", "volume", "multimediale", "blocca_pc"],
        """- un file sul PC: cerca_file e poi apri_file
- volume, musica, blocco schermo: volume, multimediale, blocca_pc""",
    ),
    "promemoria": (
        r"ricord|promemoria|avvisa|avvertimi|svegli|tra \d+ minut|fra \d+ minut",
        ["crea_promemoria", "lista_promemoria", "cancella_promemoria"],
        """- "ricordami ...": crea_promemoria, calcolando la data dall'ora attuale; elenco e cancellazione: lista_promemoria, \
cancella_promemoria""",
    ),
    "viaggi": (
        r"\bvol[oi]\b|aere|aeroport|ryanair|easyjet|wizz|\bitalo\b|tren[oi]|frecci|regionale|trenitalia|binari|"
        r"ritard|stazion|\bbus\b|autobus|fermat|\blinea\b|\btper\b|passa il|\bdist[ao]|distanz|\bkm\b|quanto ci metto|"
        r"percors|strada|mappa|maps|come (?:ci )?arriv|arrivare|andare a|partir|partenz|viagg|"
        r"da qualche parte|\bmet[ae]\b|dove (?:posso )?andare|weekend|fine settimana|vacanz|\bponte\b|giorni (?:a|in|fuori)|"
        r"all'estero|fuori (?:dall')?italia|fuggire|scappare|\bnott[ei]\b|\bgiorni\b",
        ["cerca_voli", "esplora_voli", "cerca_treni", "mostra_voli", "mostra_treni", "distanza", "mostra_percorso", "stato_treno",
         "segui_treno", "treni_seguiti", "smetti_di_seguire", "bus_in_arrivo"],
        """- prezzi e orari dei voli: cerca_voli con i codici IATA (tutti gli aeroporti della città); se l'utente non dice da \
dove parte, usa l'aeroporto indicato nella memoria o altrimenti Bologna (BLQ), e dillo. Riporta prezzo, compagnia, \
orario e se è diretto, senza elencare tutto. Avvisa che le altre compagnie non sono state controllate SOLO se il \
risultato dice testualmente "solo Ryanair"; se dice "Google Flights (tutte le compagnie)" non aggiungere avvisi
- destinazione NON scelta ("3 giorni da qualche parte", "dove vado a novembre", "un weekend all'estero", "mete \
economiche"): esplora_voli SUBITO, senza chiedere conferme e senza cerca_web; poi proponi 3-5 mete con prezzo e date. \
Non rimandare l'utente a cercare da solo su Google Flights. Se cambia notti o periodo, richiama esplora_voli; \
"mostrami dal sito" = mostra_voli con arrivo e data vuoti. Prezzi, mete e compagnie SOLO dal risultato: se \
l'utente chiede quali compagnie hai guardato, di' che hai cercato su Ryanair e su Google Flights (tutte le \
compagnie) e cita le alternative con ALTRE compagnie del risultato
- treni, orari e prezzi ("che treno prendo", "come arrivo a Roma", "per arrivare in tempo all'aeroporto"): \
cerca_treni con città o stazioni; se l'utente deve arrivare entro un'ora (un volo, un appuntamento) passa \
arrivare_entro, lasciando margine. Per prendere un volo l'arrivo è la stazione dell'aeroporto, non la città \
(FCO = "Fiumicino Aeroporto", BLQ = "Bologna Aeroporto", MXP = "Malpensa Aeroporto"), e arrivare_entro è almeno \
1h30 prima del decollo; per gli aeroporti senza stazione (es. Ciampino) cerca fino alla stazione della città e \
avvisa che serve ancora il bus. Indica \
treno, orari e prezzo come scritti nel risultato. Non dare mai un orario o un numero di treno che non viene da \
cerca_treni: niente cerca_web per gli orari dei treni
- se l'utente vuole VEDERE o aprire voli o treni ("aprimi Ryanair", "fammi vedere i voli", "apri Trenitalia"): \
mostra_voli o mostra_treni con partenza, arrivo e data (riprendili dalla conversazione), mai cerca_nel_browser. \
Per i treni ricorda che deve solo premere Cerca
- ritardi ("il mio treno è in ritardo?", "da che binario parte?"): stato_treno con il numero del treno (se non lo \
sai, riprendilo dalla conversazione o trovalo con cerca_treni). "Avvisami se il treno è in ritardo", "seguimi il \
treno": segui_treno con numero, data, ora di partenza e stazione dell'utente; treni_seguiti e smetti_di_seguire \
per elenco e cancellazione
- bus a Bologna ("quando passa il 20", "prossimo bus alla fermata ..."): bus_in_arrivo con il nome della fermata o \
della via e la linea se detta; se l'utente non dice la fermata usa quella indicata nella memoria, altrimenti chiedila. \
Riporta solo i prossimi 2-3 passaggi e di' se l'orario è da GPS o da orario""",
    ),
    "alloggi": (
        r"\bdorm|allogg|appartament|stanz|airbnb|booking|hotel|albergh|\bb&b\b|ostell|pernott|weekend a",
        ["cerca_alloggi", "mostra_alloggi"],
        """- appartamenti, case, stanze, "dove dormo a ...", Airbnb, Booking: cerca_alloggi; per VEDERLI o aprire Airbnb/Booking \
mostra_alloggi, mai cerca_nel_browser""",
    ),
    "parcheggio": (
        r"parchegg|macchina|\bauto\b|posteggi",
        ["dove_ho_parcheggiato", "nota_parcheggio"],
        "",
    ),
    "calendario": (
        r"impegn|appuntament|calendari|agenda|event|lezion|riunion|incontr|\bsegna\b|sposta|disdic|"
        r"cosa ho (?:oggi|domani|luned|marted|mercoled|gioved|venerd|sabato|domenica)|sono libero|libero (?:oggi|domani)",
        ["eventi_calendario", "crea_evento", "sposta_evento", "cancella_evento"],
        """- impegni e appuntamenti: eventi_calendario (per "oggi" usa da 00:00 a 23:59 di oggi), crea_evento, sposta_evento; \
cancella_evento solo dopo un sì esplicito dell'utente. "Ricordami" = promemoria su Telegram; "segna/metti in \
calendario/appuntamento" = evento""",
    ),
    "notion": (
        r"notion|appunt|guid[ae]|attivit|da fare|to ?do|esam|consegn|scadenz|stud|materi|lezion|corso|"
        r"rotism|meccanic|controlli|reti logiche|completat|fatto il|ho finito",
        ["cerca_notion", "leggi_notion", "attivita", "aggiungi_attivita", "completa_attivita"],
        """- Notion: per domande sugli appunti e le guide allo studio dell'utente usa cerca_notion con le parole chiave \
(cerca anche dentro il testo); se i pezzi trovati non bastano, leggi_notion con l'id. Rispondi basandoti sul \
testo letto e di' da quale pagina viene. To-do, esami, consegne, "cosa devo fare": \
attivita, aggiungi_attivita, completa_attivita. "Aggiungi alle cose da fare" = attività Notion, non promemoria""",
    ),
    "mail": (
        r"\bmail|e-mail|email|posta|gmail|allegat|rispond|scriv\w* a |invia|bozz|archivi|messaggi? d[ai]",
        ["leggi_mail", "apri_mail", "leggi_allegato", "archivia_mail", "prepara_risposta", "scrivi_mail"],
        """- mail: leggi_mail, apri_mail, archivia_mail; per il contenuto di un allegato (PDF, immagini) leggi_allegato; per rispondere o scrivere usa prepara_risposta o scrivi_mail, che creano \
solo una bozza. Non puoi inviare mail: dopo la bozza, ripeti il numero e ricorda di scrivere /invia N per mandarla.""",
    ),
    "archivio": (
        r"articol|salvat|\bieri\b|l'altro ieri|giorni fa|settimana scorsa|avevamo|ti avevo|mi avevi|abbiamo (?:parlato|visto|guardato)|"
        r"diario|quella volta|l'altra volta",
        ["elenca_articoli", "cerca_articoli", "leggi_articolo", "leggi_diario", "cerca_diario"],
        """- articoli che l'utente ha salvato ("ho salvato", "i miei articoli", "quell'articolo su..."): cerca_articoli, \
elenca_articoli, leggi_articolo. Sono diversi dalla ricerca web
- conversazioni dei giorni passati ("cosa ti avevo chiesto ieri", "quel volo che avevamo guardato"): leggi_diario \
per un giorno preciso, cerca_diario per argomento""",
    ),
    "scrittura": (
        r"\bfile\b|lista|elenco|\bnota\b|obsidian|salva|memorizza|scriv|bozza|ricerca su|fai una ricerca|"
        r"informazioni su|cancella il|aggiungi",
        ["ricerca_e_salva", "crea_file", "aggiungi_a_file", "leggi_file", "elenca_file", "cancella_file"],
        """- ricerca da salvare ("cerca info su X e salvale", "fai una ricerca su X e mettila in Obsidian", "memorizza una \
ricerca su X"): ricerca_e_salva, non ricorda e non cerca_web. Senza richiesta di salvare usa cerca_web
- file di testo ("crea/scrivimi un file", "fammi una lista e salvala", "scrivi una bozza in un file"): crea_file con \
il testo già scritto per intero; "aggiungi X alla lista/al file Y": aggiungi_a_file; "leggimi/aprimi il file Y": \
leggi_file; "che file hai creato?": elenca_file; "cancella il file Y": cancella_file. Le liste della spesa e simili \
sono file, non attività Notion né promemoria""",
    ),
    "finanza": (
        r"analizz|analisi|azion|titol|\betf\b|aziend|societ|borsa|ticker|bilanc|investi|fondo|dividend|"
        r"valutazion|cara\?|caro\?|conviene|bandiere rosse|nasdaq|s&p|msci|vwce|swda|trimestral|utili",
        ["analizza_azienda"],
        """- analisi di un'azienda, di un titolo o di un ETF ("analizza Apple", "com'è messa Eni", "Nvidia è cara?", "bandiere \
rosse su Tesla"): analizza_azienda, non cerca_web né ricerca_e_salva; per il solo prezzo o una notizia basta cerca_web""",
    ),
    "spese": (
        r"spes[ao]|speso|spendo|spender|\bconto\b|saldo|budget|soldi|pagat|pagament|moviment|patrimoni|deposito|"
        r"entrat|stipendi|risparm|revolut|abbonament|supermercat|ristorant|quanto ho|euro",
        ["spese_riepilogo", "movimenti_spese"],
        """- soldi dell'utente ("quanto ho speso", "quanto ho sul conto", budget, categorie, patrimonio): spese_riepilogo; per \
un esercente, un giorno o un periodo preciso ("quanto ho speso da Esselunga", "cosa ho pagato ieri"): movimenti_spese. \
Puoi solo leggere: non puoi aggiungere spese né aggiornare i movimenti dalla banca""",
    ),
}

TUTTI = list(GRUPPI)

# risposte che con il solo gruppo base fanno pensare a uno strumento mancante
RINUNCIA = re.compile(r"non (?:posso|riesco|sono in grado|ho (?:accesso|modo|la possibilità|uno strumento|strumenti))"
                      r"|non (?:mi )?è possibile|non dispongo", re.I)

_SCHEMI = {nome: re.compile(parole, re.I) for nome, (parole, _, _) in GRUPPI.items() if parole}


def scegli(testi: list[str]) -> list[str]:
    """I gruppi per questi testi (domanda attuale e precedenti). Solo ['base'] = nessun gruppo riconosciuto."""
    testo = " ".join(testi).replace("’", "'")
    return ["base"] + [nome for nome, schema in _SCHEMI.items() if schema.search(testo)]


def per_conversazione(domande: list[str]) -> list[str]:
    """I gruppi dell'ultima domanda più quelli dell'ultima fra le 3 precedenti che ne aveva: "e domani?",
    "fammeli vedere", "puoi cercare per tre notti" non dicono l'argomento, che resta quello di prima."""
    scelti = scegli(domande[-1:])
    for precedente in reversed(domande[-4:-1]):
        prima = scegli([precedente])
        if len(prima) > 1:
            return scelti + [g for g in prima if g not in scelti]
    return scelti


def strumenti_e_regole(gruppi: list[str], per_nome: dict) -> tuple[list, str]:
    """Le funzioni da passare a Gemini e le regole da mettere nel prompt. Gli strumenti spenti (mail, calendario,
    Notion, spese senza configurazione) non sono in per_nome e restano fuori anche se il gruppo è scelto."""
    nomi = list(dict.fromkeys(n for g in gruppi for n in GRUPPI[g][1]))
    return [per_nome[n] for n in nomi if n in per_nome], "\n".join(GRUPPI[g][2] for g in gruppi if GRUPPI[g][2])
