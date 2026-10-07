"""Il cervello: Gemini, con un secondo modello Gemini di riserva.

Fino al 2026-10-03 c'era anche Ollama locale (qwen2.5 3B) come ultima riserva: tolto perché su questo PC
impiegava fino a 100 s e rispondeva male, e il modello occupava 1,75 GB.
"""
import datetime
import os
import re
import threading

from dotenv import load_dotenv

import calendario
import memoria
import notion
import parcheggio
import promemoria
import quota
import ritardi
import siti
import tools

load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# i modelli e le loro pause dopo un 429 sono in quota.py
MAX_PASSI = 4  # quante chiamate a strumenti di fila prima di rispondere comunque
MAX_STORIA = 12  # messaggi di conversazione tenuti in memoria

NOME_UTENTE = os.getenv("JARVIS_NOME_UTENTE", "Mattia")

SISTEMA = """Sei Jarvis, l'assistente personale di """ + NOME_UTENTE + """. Rispondi sempre in italiano, in modo breve e diretto, \
con un tocco di ironia britannica. Le tue risposte possono essere lette ad alta voce: niente elenchi puntati, \
niente markdown, niente link lunghi, al massimo tre frasi salvo richiesta esplicita. Scrivi numeri, prezzi e orari \
in cifre (138 €, 12:10, Frecciarossa 9409), mai in lettere: la sintesi vocale li legge già bene.
Hai accesso a internet e al PC SOLO tramite gli strumenti, e devi usarli:
- domande su fatti recenti, persone, risultati sportivi, prezzi o qualsiasi cosa tu non sappia con certezza: cerca_web \
(cerchi tu e rispondi a parole)
- se l'utente vuole VEDERE i risultati ("aprimi", "fammi vedere", "cerca su Chrome/Google/YouTube/Amazon/Maps", \
"apri Chrome e cerca", "trovami su AliExpress/eBay/..."): cerca_nel_browser con sito = il sito nominato, poi rispondi solo con una frase breve. Non aprire \
mai il browser se l'utente non l'ha chiesto: per rispondere a una domanda usa gli altri strumenti
- notizie: notizie; meteo: meteo
- leggere o riassumere un sito o un link preciso: leggi_pagina
- "apri ..." un programma: apri_app; un sito: apri_sito; un file: cerca_file e poi apri_file
- volume, musica, blocco schermo: volume, multimediale, blocca_pc
- "ricordami ...": crea_promemoria, calcolando la data dall'ora attuale; elenco e cancellazione: lista_promemoria, \
cancella_promemoria
- prezzi e orari dei voli: cerca_voli con i codici IATA (tutti gli aeroporti della città); se l'utente non dice da \
dove parte, usa l'aeroporto indicato nella memoria o altrimenti Bologna (BLQ), e dillo. Riporta prezzo, compagnia, \
orario e se è diretto, senza elencare tutto. Avvisa che le altre compagnie non sono state controllate SOLO se il \
risultato dice testualmente "solo Ryanair"; se dice "Google Flights (tutte le compagnie)" non aggiungere avvisi
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
- appartamenti, case, stanze, "dove dormo a ...", Airbnb, Booking: cerca_alloggi; per VEDERLI o aprire Airbnb/Booking \
mostra_alloggi, mai cerca_nel_browser
- ritardi ("il mio treno è in ritardo?", "da che binario parte?"): stato_treno con il numero del treno (se non lo \
sai, riprendilo dalla conversazione o trovalo con cerca_treni). "Avvisami se il treno è in ritardo", "seguimi il \
treno": segui_treno con numero, data, ora di partenza e stazione dell'utente; treni_seguiti e smetti_di_seguire \
per elenco e cancellazione
- bus a Bologna ("quando passa il 20", "prossimo bus alla fermata ..."): bus_in_arrivo con il nome della fermata o \
della via e la linea se detta; se l'utente non dice la fermata usa quella indicata nella memoria, altrimenti chiedila. \
Riporta solo i prossimi 2-3 passaggi e di' se l'orario è da GPS o da orario
- "quanto ho consumato", richieste Gemini, quota, ricerche SerpApi: consumi
- impegni e appuntamenti: eventi_calendario (per "oggi" usa da 00:00 a 23:59 di oggi), crea_evento, sposta_evento; \
cancella_evento solo dopo un sì esplicito dell'utente. "Ricordami" = promemoria su Telegram; "segna/metti in \
calendario/appuntamento" = evento
- Notion: per domande sugli appunti e le guide allo studio dell'utente usa cerca_notion con le parole chiave \
(cerca anche dentro il testo); se i pezzi trovati non bastano, leggi_notion con l'id. Rispondi basandoti sul \
testo letto e di' da quale pagina viene. To-do, esami, consegne, "cosa devo fare": \
attivita, aggiungi_attivita, completa_attivita. "Aggiungi alle cose da fare" = attività Notion, non promemoria
- mail: leggi_mail, apri_mail, archivia_mail; per il contenuto di un allegato (PDF, immagini) leggi_allegato; per rispondere o scrivere usa prepara_risposta o scrivi_mail, che creano \
solo una bozza. Non puoi inviare mail: dopo la bozza, ripeti il numero e ricorda di scrivere /invia N per mandarla.
- articoli che l'utente ha salvato ("ho salvato", "i miei articoli", "quell'articolo su..."): cerca_articoli, \
elenca_articoli, leggi_articolo. Sono diversi dalla ricerca web
- conversazioni dei giorni passati ("cosa ti avevo chiesto ieri", "quel volo che avevamo guardato"): leggi_diario \
per un giorno preciso, cerca_diario per argomento
- memoria permanente: usa ricorda quando l'utente chiede di ricordare qualcosa o dice un fatto stabile su di sé \
(persone, preferenze, abitudini, luoghi); non salvare chiacchiere, cose passeggere o dati sensibili come password. \
"Dimentica che..." = dimentica. Non dire mai "lo terrò a mente" o "memorizzato" senza aver chiamato ricorda
- ricerca da salvare ("cerca info su X e salvale", "fai una ricerca su X e mettila in Obsidian", "memorizza una \
ricerca su X"): ricerca_e_salva, non ricorda e non cerca_web. Senza richiesta di salvare usa cerca_web
- file di testo ("crea/scrivimi un file", "fammi una lista e salvala", "scrivi una bozza in un file"): crea_file con \
il testo già scritto per intero; "aggiungi X alla lista/al file Y": aggiungi_a_file; "leggimi/aprimi il file Y": \
leggi_file; "che file hai creato?": elenca_file; "cancella il file Y": cancella_file. Le liste della spesa e simili \
sono file, non attività Notion né promemoria
Non dire mai che non hai internet o che non puoi fare qualcosa che uno strumento fa: chiama lo strumento. \
Non inventare dati: se uno strumento non trova nulla, dillo.
Adesso è {ora} ({iso})."""


def _sistema() -> str:
    """Prompt di sistema con ora attuale e memoria, riletta ogni volta: la nota può essere stata modificata in Obsidian."""
    testo = SISTEMA.format(ora=tools.data_ora(), iso=f"{datetime.datetime.now():%Y-%m-%d %H:%M}")
    ricordi = memoria.contenuto()
    if ricordi:
        testo += f"\n\nCose che sai su {NOME_UTENTE} (dalla tua memoria permanente):\n{ricordi}"
    return testo


_usati: list[str] = []  # strumenti chiamati nella domanda in corso (chiedi è sotto lock, una domanda alla volta)

# Comandi diretti: (frase intera, nome, azione). Ogni frase qui risparmia almeno 2 richieste Gemini; le
# espressioni descrivono la frase INTERA, così "cosa devo fare per arrivare a Roma" va comunque al modello
_CITTA = r"(?: (?:a|ad|in) ([a-zà-ù' ]+?))?"
COMANDI = [
    (r"(?:quanto ho consumato|consumi|quante richieste (?:ho fatto|mi restano)(?: oggi)?)", "consumi",
     lambda m: quota.consumi()),
    (r"(?:dove ho (?:parcheggiato|lasciato la macchina)|dov'è la (?:mia )?macchina|dove sta la macchina)",
     "dove_ho_parcheggiato", lambda m: parcheggio.dove_ho_parcheggiato()),
    (r"(?:(?:che )?cosa (?:devo|ho da) fare|(?:le mie |elenca le |dimmi le )?(?:attività|cose da fare|to ?do))"
     r"(?: oggi| questa settimana| su notion)?", "attivita", lambda m: notion.attivita_in_breve()),
    (r"(?:(?:che )?cosa ho|che impegni ho|impegni(?: di)?|agenda(?: di)?) (oggi|domani)", "impegni",
     lambda m: calendario.impegni_in_breve(0 if m.group(1) == "oggi" else 1)),
    (r"(?:che tempo fa|che tempo farà|com'è il tempo|meteo|previsioni(?: del tempo)?)(?: oggi| domani)?" + _CITTA
     + r"(?: oggi| domani)?", "meteo", lambda m: tools.meteo((m.group(1) or "Bologna").strip())),
    (r"(?:(?:quali sono |elenca |dimmi )?i (?:miei )?promemoria|promemoria)", "lista_promemoria",
     lambda m: promemoria.lista_promemoria()),
    (r"(?:che |quali )?treni (?:stai seguendo|segui)", "treni_seguiti", lambda m: ritardi.treni_seguiti()),
    (r"alza (?:il |un po' (?:il )?)?volume", "volume", lambda m: tools.volume("su")),
    (r"abbassa (?:il |un po' (?:il )?)?volume", "volume", lambda m: tools.volume("giu")),
    (r"(?:muto|silenzia|togli l'audio|metti (?:il )?muto)", "volume", lambda m: tools.volume("muto")),
    (r"(?:metti )?(?:pausa|play|stop)(?: (?:la )?musica)?|riprendi la musica", "multimediale",
     lambda m: tools.multimediale("play")),
    # serve la parola canzone/traccia/brano: "la successiva?" da sola di solito parla di treni o bus
    (r"(?:(?:canzone|traccia|brano) successiv[ao]|(?:prossima canzone|prossimo brano)|salta (?:la )?(?:canzone|traccia))",
     "multimediale", lambda m: tools.multimediale("avanti")),
    (r"(?:canzone|traccia|brano) precedente", "multimediale", lambda m: tools.multimediale("indietro")),
    (r"blocca (?:il )?(?:pc|computer|schermo)", "blocca_pc", lambda m: tools.blocca_pc()),
]
RICORDA = re.compile(r"^(?:jarvis,?\s+)?(?:ricordati|ricorda|tieni a mente|memorizza)\b(?:\s+che)?\s+(.+)", re.I | re.S)


def _esegui(nome: str, args: dict, log) -> str:
    _usati.append(nome)
    log(f"  [strumento] {nome}({args})")
    funzione = tools.PER_NOME.get(nome)
    try:
        esito = funzione(**args) if funzione else f"Strumento '{nome}' inesistente."
    except Exception as e:  # il modello a volte passa argomenti sbagliati
        esito = f"Errore: {e}"
    return str(esito)[:4000]


class Jarvis:
    def __init__(self):
        self.storia: list[dict] = []
        self.lock = threading.Lock()
        self.gemini = None
        if GEMINI_KEY:
            from google import genai

            self.gemini = genai.Client(api_key=GEMINI_KEY)
        # funzioni chiamate con (canale, domanda, risposta) dopo ogni scambio: la finestra le usa per
        # mostrare anche quello che passa da voce e Telegram
        self.ascoltatori: list = []

    def chiedi(self, testo: str, log=print, canale: str = "") -> str:
        # voce, Telegram e finestra condividono modello e conversazione: una domanda alla volta
        with self.lock:
            risposta = self._chiedi(testo, log)
        for ascoltatore in self.ascoltatori:
            try:
                ascoltatore(canale, testo, risposta)
            except Exception as e:
                print(f"[ascoltatore] {e}")
        return risposta

    def aggiungi_scambio(self, canale: str, domanda: str, risposta: str, contesto: str = ""):
        """Scambio gestito fuori dal modello (es. una foto): entra nella conversazione, così le domande successive
        lo conoscono, e arriva agli ascoltatori (diario, finestra). contesto è ciò che il modello vedrà al posto della
        domanda (per una foto: descrizione e testo trascritto)."""
        with self.lock:
            self.storia.append({"role": "user", "content": contesto or domanda})
            self.storia.append({"role": "assistant", "content": risposta})
        for ascoltatore in self.ascoltatori:
            try:
                ascoltatore(canale, domanda, risposta)
            except Exception as e:
                print(f"[ascoltatore] {e}")

    def _chiedi(self, testo: str, log) -> str:
        _usati.clear()
        self.storia.append({"role": "user", "content": testo})
        risposta = self._comando_diretto(testo, log)
        errore = ""
        if risposta is None and self.gemini:
            scartati: set[str] = set()  # modelli falliti per motivi diversi dal limite al minuto
            for _ in range(6):
                modello = quota.prossimo(scartati)
                if modello is None:
                    break
                try:
                    risposta = self._chiedi_gemini(modello, log)
                    break
                except Exception as e:  # quota (429), rete assente, modello sovraccarico (503), credito (402)
                    errore = str(e)
                    tipo = quota.segna_errore(modello, errore)
                    quota.registra(modello, "cervello", "429" if tipo else "altro")
                    log(f"  [gemini] {modello} non disponibile{' (limite al ' + tipo + ')' if tipo else ''}: {errore[:80]}")
                    if tipo != "minuto":
                        scartati.add(modello)
        if risposta is None:
            risposta = self._spiega_errore(errore)

        m = RICORDA.match(testo.strip())
        if m and "ricorda" not in _usati and "ricerca_e_salva" not in _usati:
            # il modello a volte risponde "terrò a mente" senza salvare niente: lo salviamo noi
            log(f"  [ripiego] ricorda({m.group(1)!r})")
            memoria.ricorda(m.group(1).rstrip(".!"))

        risposta = risposta.strip() or "Non ho una risposta, signore."
        self.storia.append({"role": "assistant", "content": risposta})
        return risposta

    def _chiedi_gemini(self, modello: str, log) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=_sistema(),
            tools=tools.TUTTI,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            http_options=types.HttpOptions(timeout=20000),
        )
        contenuti = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
            for m in self.storia[-MAX_STORIA:]
        ]
        for _ in range(MAX_PASSI):
            r = self.gemini.models.generate_content(model=modello, contents=contenuti, config=config)
            quota.registra(modello, "cervello")
            if not r.function_calls:
                return r.text or ""
            # il contenuto del modello va rimandato intero: contiene le firme del ragionamento
            contenuti.append(r.candidates[0].content)
            contenuti.append(types.Content(role="user", parts=[
                types.Part.from_function_response(name=fc.name, response={"risultato": _esegui(fc.name, dict(fc.args or {}), log)})
                for fc in r.function_calls
            ]))
        config.tools = None
        r = self.gemini.models.generate_content(model=modello, contents=contenuti, config=config)
        quota.registra(modello, "cervello")
        return r.text or ""

    def _spiega_errore(self, errore: str) -> str:
        """Senza Gemini non c'è un modello di riserva: meglio dire chiaramente cosa non va."""
        if not self.gemini:
            return "Manca la chiave di Gemini: aggiungi GEMINI_API_KEY nel file .env."
        pausa = quota.stato()
        if pausa == "giorno":
            return "Ho finito le richieste gratuite di oggi su tutti i modelli Gemini: la quota riparte alle 9."
        if pausa.startswith("minuto") or "429" in errore:
            secondi = pausa.split(":")[1] if pausa else "60"
            return f"Troppe richieste a Gemini in questo minuto, signore. Riprovi tra {secondi} secondi."
        if "402" in errore:
            return "Gemini rifiuta le richieste per credito esaurito sul progetto della chiave: controlla la fatturazione su AI Studio."
        if "401" in errore or "403" in errore or "API key" in errore:
            return "La chiave di Gemini non è valida: controlla GEMINI_API_KEY nel file .env."
        return "Gemini non risponde in questo momento (rete assente o servizio sovraccarico). Riprova tra poco."

    def _comando_diretto(self, testo: str, log) -> str | None:
        """Comandi ovvi gestiti senza modello: istantanei e senza consumare richieste Gemini."""
        t = testo.lower().strip().rstrip(".!?").replace("’", "'")
        t = re.sub(r"^(?:ehi |hey )?jarvis[,!]?\s+", "", t)
        if re.search(r"\bche (ore sono|ora è|ora e|giorno è|giorno e|giorno è oggi)\b", t):
            return f"Sono le {datetime.datetime.now():%H:%M} di {tools.data_ora().split(',')[0]}."
        for schema, nome, azione in COMANDI:
            m = re.fullmatch(schema, t)
            if not m:
                continue
            try:
                risposta = azione(m)
            except Exception as e:  # rete o servizio giù: decide il modello, che sa spiegare l'errore
                log(f"  [diretto] {nome} fallito ({e}), passo a Gemini")
                return None
            log(f"  [diretto] {nome}")
            _usati.append(nome)  # Telegram lo usa per mandare il segnaposto del parcheggio
            return risposta
        m = re.match(r"^(?:jarvis,?\s+)?(?:apri|avvia|lancia|aprimi)\s+(?:il sito |sito |l'app |app |il |la |lo |l')?(.+)$", t)
        if m:
            oggetto = m.group(1).strip()
            if re.search(r"\.[a-z]{2,}$", oggetto) or "sito" in t:
                log(f"  [diretto] apri_sito({oggetto!r})")
                return tools.apri_sito(oggetto.replace(" ", ""))
            if oggetto in tools.APP:
                log(f"  [diretto] apri_app({oggetto!r})")
                return tools.apri_app(oggetto)
            # "apri github": sito noto e nessuna app installata con quel nome (Telegram, Discord... restano app)
            if len(oggetto.split()) <= 2 and not tools.app_installata(oggetto) and (dominio := siti.dominio(oggetto)):
                log(f"  [diretto] apri_sito({dominio!r})")
                return tools.apri_sito(dominio)
        return None  # tutto il resto lo decide il modello

    def dimentica(self):
        self.storia.clear()


if __name__ == "__main__":
    # modalità testo per provare il cervello da terminale
    j = Jarvis()
    while True:
        try:
            domanda = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if domanda:
            print("Jarvis:", j.chiedi(domanda))
