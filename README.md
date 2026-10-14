# Jarvis

Personal assistant: chat window (Alt+J), Telegram bot and web page. Brain: Gemini
(free tier, `GEMINI_API_KEY` in `.env`), with three Gemini models in rotation. If none of them responds, it falls back
to Mistral (`riserva.py`, `MISTRAL_API_KEY`, free account, ministral-14b). The fallback labels its replies
"(modello di riserva)" and leaves calendar, reminders and workout planning alone, because it gets the weekdays wrong.
If the fallback doesn't respond either, Jarvis explains the error. Local Ollama was removed on 2026-10-03 (slow and
inaccurate on this PC). Voice ("Hey Jarvis", spoken replies, the microphone in the window, Telegram voice messages)
was removed on 2026-10-14 at the user's request.

Jarvis talks in Italian: commands, button labels and replies are in Italian and are quoted as such below.

## Running

```
uv run main.py testo      # terminal chat, for testing
uv run main.py telegram   # bot only
uv run main.py            # window + Telegram + web page (what the scheduled task starts)
```

## Telegram

1. On Telegram, message @BotFather → `/newbot` → copy the token.
2. Copy `.env.example` to `.env` and paste the token into `TELEGRAM_TOKEN`.
3. Start `uv run main.py telegram` and message the bot: it replies with your ID.
4. Put that ID in `TELEGRAM_UTENTE` and restart. The bot ignores everyone else.

It works as long as the PC is on and the script is running.

## Chat window

**Alt+J** opens and closes the chat from any program; Esc or the X hide it (Jarvis keeps running).
The **Jarvis** desktop icon opens it with the mouse (`crea_icona.py` recreates it). In the window, "Nuova" resets
the conversation. It also shows the exchanges made on Telegram and on the web page: there is a single conversation.
Code in `gui.py`. The background is a flat colour (the central sphere was removed by the user's choice); while Jarvis
is thinking, the status line at the top says so. The input bar is a pill, like Gemini's, with the send arrow inside.

## Automatic start

The "Jarvis" task in Task Scheduler starts `main.py --log` (hidden window + Telegram) 30 s after logging in to
Windows and restarts it if it closes. Output goes to `jarvis.log`.

    Stop-ScheduledTask Jarvis      # stop it (e.g. to run it by hand while editing it)
    Start-ScheduledTask Jarvis     # start it again after a code change

## Telegram commands

- `/nuova` resets the conversation
- `/promemoria` lists the reminders
- `/schermo` sends a screenshot of the PC
- `/invia N` sends email draft N: Jarvis prepares emails but cannot send them on its own
- a **photo** (or an image sent as a file, at full quality: better for whiteboards and documents) is read by
  Gemini and saved to Obsidian; if the caption is a question, Jarvis answers it
- `/consumi` Gemini requests for the current quota day (9 am to 9 am) per model, and SerpApi searches left
- `/controllo` tests all external services right away and says which ones work (the same check runs by itself
  at 8 am and only writes if something is broken)
- `/treni` trains being followed for delay alerts

## Files

- `quota.py`: order of the Gemini models (3.5-flash-lite, 3.1-flash-lite, 3.6-flash; configurable with
  `GEMINI_MODEL`) and pauses after a 429. The per-minute limit is per model: the one that was hit stays paused for the
  `retryDelay` given by Google while the others answer; if they are all paused, it waits up to 20 s. A daily 429
  excludes the model until 9 am
- `brain.py`: dialogue with the model, direct commands ("apri", "cerca", "che ore sono"), fallback to web search
- `tools.py`: tools (web, news, weather, apps, sites, files, volume, music, screen lock). Known apps are in the `APP`
  dictionary
- `telegram_bot.py`: long polling, accepts text, photos and documents (replies to voice messages asking for text),
  delivers reminders every 20 s
- `promemoria.py`: reminders in `promemoria.json`; those that fall due while the PC is off arrive at startup, marked
  "in ritardo" (late)
- `calendario.py` + `calendario.gs`: Google Calendar through an Apps Script web app (script.google.com, project
  "Jarvis Calendario"); if you change the `.gs` you need "Manage deployments → Edit → New version", otherwise the URL
  keeps serving the old version
- `memoria.py`: long-term memory in `Desktop\obsidian\Jarvis\Memoria.md` (configurable with `JARVIS_MEMORIA`),
  reread at every question, so edits made by hand in Obsidian apply immediately. If the sentence starts with
  "ricordati/ricorda che…" ("remember that…") and the model doesn't save it, `brain.py` saves it anyway
- `tools.cerca_nel_browser`: opens a search in Chrome (Google, images, news, YouTube, Amazon, Maps, Wikipedia,
  flights) when the user wants to SEE the results; `cerca_web` instead searches in the background and answers in words
- `articoli.py`: articles saved with Chrome's Web Clipper in `Desktop\obsidian\Jarvis\Articoli` (in the Clipper:
  Settings → Default template → note location `Jarvis/Articoli`); keyword search
- `diario.py`: conversation journal, one note per day in `Desktop\obsidian\Jarvis\Diario`, written at every exchange
  (registered in `main.py`, so tests that only import `brain` don't end up in it)
- `voli.py`: Google Flights via SerpApi (`SERPAPI_KEY`, 250 free searches/month), with the public Ryanair API as
  fallback; identical searches stay cached for 30 minutes. The `fast-flights` library was discarded: its results
  were incomplete (low-cost direct flights were missing)
- `treni.py`: real Trenitalia times and prices from the unofficial lefrecce.it API (no key); with `arrivare_entro` it
  finds trains that arrive in time, e.g. at "Fiumicino Aeroporto" station for a flight. Italo is not included (no
  public API). Added on 2026-10-03 because Jarvis was making up train times
- `mappe.distanza`: km and time by car, on foot or by bike, computed without a key. Places are found by Photon
  (photon.komoot.io, understands names in words; Nominatim as fallback, wants precise addresses), with a slight
  preference for Bologna; routing is done by OSRM on routing.openstreetmap.de (the OSRM demo server only knows cars).
  Photon always finds something, even for wrong names ("stazione di Bologna Centrale" → Roveri): the answer names the
  places found and asks the model to notice. No times for buses and trains
- `parcheggio.py`: on Telegram the fixed "📍 Ho parcheggiato" ("I parked") button (`request_location`) sends the GPS
  position; the bot saves it in `parcheggio.json` (only one, with the address from Photon reverse: in parks Nominatim
  only gives the neighbourhood). `nota_parcheggio` adds floor/space, `dove_ho_parcheggiato` answers, and the bot then
  sends the pin (`sendLocation`) if the tool was used. The button is shown once (`pulsante_mostrato`) and with /start
- `tools.mostra_percorso`: opens Google Maps on the route (`maps/dir/?api=1&origin=…&destination=…&travelmode=…`),
  public transport included; without a starting point it uses the current location
- `tools.mostra_voli` / `tools.mostra_treni`: open the page in Chrome with the search already done. Google Flights
  uses the exact link returned by SerpApi (no extra search if the same one is cached); Ryanair has a direct link to
  the results (one airport per side). Trenitalia opens the filled-in form
  (`#/white-label/?departureStation=…&arrivalStation=…&departureDate=DD-MM-YYYY&departureTime=HH`) but needs a click
  on Search: no URL leads straight to the results. Station names must be Trenitalia's exact ones, which is why they go
  through `treni._stazione`
- `ritardi.py`: real-time delay, position and platform from ViaggiaTreno (RFI, free, Italo included). Followed trains
  are kept in `treni_seguiti.json`: the bot checks them every 5 minutes starting 90 minutes before departure and writes
  on Telegram if the delay changes by at least 5 minutes, if the platform is announced or changes, or if the train is
  cancelled. ViaggiaTreno only knows the current day's trains
- `tper.py`: real-time TPER buses from HelloBus. Stops are searched by name in TPER's open data list
  (`fermate_tper.csv`, downloaded again every week); TPER truncates names to 25 characters ("PORTA SARAGOZZA -
  RISORGI"), which is why the search accepts truncated words
- `quota.py` also counts Gemini requests in `consumi.json` (last 14 days): Google doesn't expose the remaining quota
  through an API
- `foto.py`: photos from Telegram → `Desktop\obsidian\raw` (configurable with `JARVIS_FOTO`), the inbox of the
  knowledge base described in `obsidian\claude.md`: image + note with description and transcription (formulas in
  LaTeX), which Claude then compiles into `wiki/`. Jarvis never writes to `wiki/`. The photo also enters the
  conversation (with the transcribed text) and the journal
- `posta.py`: Gmail via IMAP/SMTP with an app password; drafts in `bozze.json` and in Gmail's Drafts
