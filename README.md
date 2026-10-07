# Jarvis

Assistente personale: finestra di chat (Alt+J), voce sul PC ("Hey Jarvis") e bot Telegram. Cervello: Gemini
(piano gratuito, `GEMINI_API_KEY` nel `.env`), con un secondo modello Gemini di riserva. Senza Gemini Jarvis
spiega l'errore invece di rispondere: Ollama locale è stato tolto il 2026-10-03 (lento e impreciso su questo PC).

## Avvio

```
uv run main.py testo      # chat da terminale, per provare
uv run main.py voce       # di' "Hey Jarvis", aspetta il bip, parla
uv run main.py telegram   # solo bot
uv run main.py            # finestra + voce + Telegram insieme (è quello che avvia l'attività automatica)
```

## Telegram

1. Su Telegram scrivi a @BotFather → `/newbot` → copia il token.
2. Copia `.env.example` in `.env` e incolla il token in `TELEGRAM_TOKEN`.
3. Avvia `uv run main.py telegram` e scrivi al bot: ti risponde con il tuo ID.
4. Metti quell'ID in `TELEGRAM_UTENTE` e riavvia. Il bot ignora chiunque altro.

Funziona finché il PC è acceso e lo script gira.

## Finestra di chat

**Alt+J** apre e chiude la chat da qualsiasi programma; Esc o la X la nascondono (Jarvis resta attivo).
L'icona **Jarvis** sul desktop la apre col mouse (`crea_icona.py` la ricrea). Nella finestra: 🎤 per parlare senza
"Hey Jarvis", "voce sì/no" per sentire anche le risposte scritte, "Nuova" per azzerare la conversazione.
Mostra anche gli scambi fatti a voce e su Telegram: la conversazione è una sola. Codice in `gui.py`.
Lo sfondo è `immagini/sfera.png`, ritagliata dall'immagine di riferimento in `immagini/` con il bordo sfumato in
trasparenza; si illumina e pulsa mentre Jarvis pensa, ascolta o parla.

## Avvio automatico

L'attività "Jarvis" nell'Utilità di pianificazione avvia `main.py --log` (voce + Telegram) 30 s dopo
l'accesso a Windows e lo riavvia se si chiude. L'output finisce in `jarvis.log`.

    Stop-ScheduledTask Jarvis      # fermarlo (es. per avviarlo a mano mentre lo modifichi)
    Start-ScheduledTask Jarvis     # farlo ripartire dopo una modifica al codice

## Comandi Telegram

- `/nuova` azzera la conversazione
- `/promemoria` elenca i promemoria
- `/schermo` manda uno screenshot del PC
- `/invia N` spedisce la bozza di mail N: Jarvis prepara le mail ma non può inviarle da solo
- una **foto** (o un'immagine mandata come file, a qualità piena: meglio per lavagne e documenti) viene letta da
  Gemini e salvata in Obsidian; se la didascalia è una domanda Jarvis risponde
- `/consumi` richieste Gemini del giorno di quota (dalle 9 alle 9) per modello e ricerche SerpApi rimaste
- `/treni` treni seguiti per gli avvisi di ritardo

## File

- `quota.py`: ordine dei modelli Gemini (3.5-flash-lite, 3.1-flash-lite, 3.6-flash; cambiabile con `GEMINI_MODEL`)
  e pause dopo un 429. Il limite al minuto vale per modello: quello colpito resta fermo per il `retryDelay` indicato
  da Google mentre rispondono gli altri; se sono tutti fermi, si aspetta fino a 20 s. Un 429 giornaliero esclude
  il modello fino alle 9. Cervello e trascrizione condividono le pause
- `brain.py`: dialogo con il modello, comandi diretti ("apri", "cerca", "che ore sono"), ripiego sulla ricerca web
- `tools.py`: strumenti (web, notizie, meteo, app, siti, file, volume, musica, blocco schermo). Le app conosciute sono nel dizionario `APP`
- `voice.py`: parola d'attivazione (openwakeword), trascrizione (Gemini; Whisper tolto il 2026-10-03: sbagliava di più e occupava ~130 MB), voce (edge-tts, con la voce di Windows se manca la rete)
- `telegram_bot.py`: long polling, accetta testo e vocali, consegna i promemoria ogni 20 s
- `promemoria.py`: promemoria in `promemoria.json`; quelli scaduti a PC spento arrivano all'accensione, segnati "in ritardo"
- `calendario.py` + `calendario.gs`: Google Calendar tramite una web app Apps Script (script.google.com, progetto
  "Jarvis Calendario"); se modifichi il `.gs` serve "Gestisci deployment → Modifica → Nuova versione", altrimenti l'URL
  continua a servire la versione vecchia
- `memoria.py`: memoria permanente in `Desktop\obsidian\Jarvis\Memoria.md` (cambiabile con `JARVIS_MEMORIA`),
  riletta a ogni domanda, quindi le modifiche fatte a mano in Obsidian valgono subito. Se la frase inizia con
  "ricordati/ricorda che…" e il modello non salva, salva comunque `brain.py`
- `tools.cerca_nel_browser`: apre una ricerca in Chrome (Google, immagini, notizie, YouTube, Amazon, Maps, Wikipedia,
  voli) quando l'utente vuole VEDERE i risultati; `cerca_web` invece cerca in background e risponde a parole
- `articoli.py`: articoli salvati col Web Clipper di Chrome in `Desktop\obsidian\Jarvis\Articoli` (nel Clipper:
  Impostazioni → modello Default → posizione della nota `Jarvis/Articoli`); ricerca per parole chiave
- `diario.py`: diario delle conversazioni, una nota al giorno in `Desktop\obsidian\Jarvis\Diario`, scritta a ogni
  scambio (registrato in `main.py`, così le prove che importano solo `brain` non ci finiscono)
- `voli.py`: Google Flights via SerpApi (`SERPAPI_KEY`, 250 ricerche/mese gratis), riserva sull'API pubblica Ryanair;
  le ricerche identiche restano in memoria 30 minuti. Scartata la libreria `fast-flights`: dava risultati incompleti
  (mancavano i diretti low cost)
- `treni.py`: orari e prezzi reali Trenitalia dall'API non ufficiale di lefrecce.it (nessuna chiave); con
  `arrivare_entro` trova i treni per arrivare in tempo, ad es. alla stazione "Fiumicino Aeroporto" per un volo.
  Italo non è incluso (nessuna API pubblica). Aggiunto il 2026-10-03 perché Jarvis inventava orari dei treni
- `mappe.distanza`: km e tempo in auto, a piedi o in bici, calcolati senza chiave. I luoghi li trova Photon
  (photon.komoot.io, capisce i nomi a parole; Nominatim di riserva, vuole indirizzi precisi), con una leggera
  preferenza per Bologna; il percorso lo fa OSRM su routing.openstreetmap.de (il server demo di OSRM conosce solo
  l'auto). Photon trova sempre qualcosa, anche per nomi sbagliati ("stazione di Bologna Centrale" → Roveri): la
  risposta nomina i luoghi trovati e chiede al modello di accorgersene. Per bus e treni i tempi non ci sono
- `parcheggio.py`: su Telegram il pulsante fisso "📍 Ho parcheggiato" (`request_location`) manda il GPS; il bot lo
  salva in `parcheggio.json` (uno solo, l'indirizzo con Photon reverse: Nominatim nei parchi dà solo il quartiere).
  `nota_parcheggio` aggiunge piano/posto, `dove_ho_parcheggiato` risponde e il bot manda dopo il segnaposto
  (`sendLocation`) se lo strumento è stato usato. Il pulsante si presenta una volta (`pulsante_mostrato`) e con /start
- `tools.mostra_percorso`: apre Google Maps sul percorso (`maps/dir/?api=1&origin=…&destination=…&travelmode=…`),
  anche con i mezzi pubblici; senza partenza usa la posizione attuale
- `tools.mostra_voli` / `tools.mostra_treni`: aprono in Chrome la pagina con la ricerca già fatta. Google Flights
  usa il link esatto restituito da SerpApi (senza ricerca in più se la stessa è in memoria); Ryanair ha un link
  diretto ai risultati (un aeroporto per lato). Trenitalia apre il modulo compilato
  (`#/white-label/?departureStation=…&arrivalStation=…&departureDate=GG-MM-AAAA&departureTime=HH`) ma serve un clic
  su Cerca: nessun indirizzo porta direttamente ai risultati. I nomi di stazione devono essere quelli esatti di
  Trenitalia, per questo passano da `treni._stazione`
- `ritardi.py`: ritardo, posizione e binario in tempo reale da ViaggiaTreno (RFI, gratuito, anche Italo). I treni
  seguiti stanno in `treni_seguiti.json`: il bot li controlla ogni 5 minuti da 90 minuti prima della partenza e
  scrive su Telegram se il ritardo cambia di almeno 5 minuti, se esce o cambia il binario, se il treno è cancellato.
  ViaggiaTreno conosce solo i treni del giorno
- `tper.py`: bus TPER in tempo reale da HelloBus. Le fermate si cercano per nome nell'elenco open data TPER
  (`fermate_tper.csv`, riscaricato ogni settimana); TPER tronca i nomi a 25 caratteri ("PORTA SARAGOZZA - RISORGI"),
  per questo la ricerca accetta parole troncate
- `quota.py` conta anche le richieste Gemini in `consumi.json` (ultimi 14 giorni): Google non espone il residuo via API
- `foto.py`: foto da Telegram → `Desktop\obsidian\raw` (cambiabile con `JARVIS_FOTO`), l'inbox della knowledge base
  descritta in `obsidian\claude.md`: immagine + nota con descrizione e trascrizione (formule in LaTeX), che Claude
  poi compila in `wiki/`. Jarvis non scrive mai in `wiki/`. La foto entra anche nella conversazione (con il testo
  trascritto) e nel diario
- `posta.py`: Gmail via IMAP/SMTP con password per le app; bozze in `bozze.json` e nelle Bozze di Gmail
