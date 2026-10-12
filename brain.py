"""Il cervello: Gemini, con gli altri modelli Gemini a rotazione (quota.py) e, se non risponde nessuno, Mistral
(riserva.py: senza calendario, con l'etichetta "modello di riserva").

Fino al 2026-10-03 c'era anche Ollama locale (qwen2.5 3B) come ultima riserva: tolto perché su questo PC
impiegava fino a 100 s e rispondeva male, e il modello occupava 1,75 GB.
"""
import datetime
import os
import re
import threading
import time
import traceback

import requests
from dotenv import load_dotenv

import allenamento
import calendario
import gruppi
import memoria
import notion
import parcheggio
import promemoria
import quota
import riserva
import ritardi
import sapere
import siti
import spese
import tools
import verifica

load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# i modelli e le loro pause dopo un 429 sono in quota.py
MAX_PASSI = 4  # quante chiamate a strumenti di fila prima di rispondere comunque
MAX_STORIA = 12  # messaggi di conversazione tenuti in memoria
MAX_RECENTI = 2  # domande precedenti di cui Gemini rivede i risultati degli strumenti
# dopo questa pausa la conversazione riparte da zero: senza, i voli chiesti la sera restavano nel prompt (fino a
# 5000 caratteri di risultati) e il gruppo viaggi veniva ereditato dalla domanda del mattino dopo
PAUSA_AZZERA = 30 * 60

NOME_UTENTE = os.getenv("JARVIS_NOME_UTENTE", "Mattia")

SISTEMA = """Sei Jarvis, l'assistente personale di """ + NOME_UTENTE + """. Rispondi sempre in italiano, in modo breve e diretto, \
con un tocco di ironia britannica. Le tue risposte possono essere lette ad alta voce: niente elenchi puntati, \
niente markdown, niente link lunghi, al massimo tre frasi salvo richiesta esplicita. Scrivi numeri, prezzi e orari \
in cifre (138 €, 12:10, Frecciarossa 9409), mai in lettere: la sintesi vocale li legge già bene.
Hai accesso a internet e al PC SOLO tramite gli strumenti, e devi usarli:
{regole}
Non dire mai che non hai internet o che non puoi fare qualcosa che uno strumento fa: chiama lo strumento. \
Non inventare dati: se uno strumento non trova nulla, dillo.
Adesso è {ora} ({iso}).{recenti}"""


def _sistema(regole: str, recenti: list[str]) -> str:
    """Prompt di sistema con le regole dei soli strumenti scelti (gruppi.py), l'ora attuale, i risultati degli strumenti
    delle ultime domande e la memoria, riletta ogni volta: la nota può essere stata modificata in Obsidian."""
    if recenti:
        # la storia tiene solo domande e risposte: senza questi, a "hai guardato solo Ryanair?" il modello non vede più
        # i voli trovati e li completa a memoria (Wizz Air inventata, 07/10)
        recenti_testo = ("\n\nRisultati degli strumenti nelle ultime domande: usali per le domande che ne parlano, e se "
                         "servono dati nuovi chiama di nuovo lo strumento.\n" + "\n---\n".join(recenti))
    else:
        recenti_testo = ""
    testo = SISTEMA.format(regole=regole, ora=tools.data_ora(), iso=f"{datetime.datetime.now():%Y-%m-%d %H:%M}",
                           recenti=recenti_testo)
    ricordi = memoria.contenuto()
    if ricordi:
        testo += f"\n\nCose che sai su {NOME_UTENTE} (dalla tua memoria permanente):\n{ricordi}"
    return testo


_usati: list[str] = []  # strumenti chiamati nella domanda in corso (chiedi è sotto lock, una domanda alla volta)
_risultati: list[str] = []  # i loro risultati, per verifica.py e per le domande successive
_chiamate: dict[str, int] = {}  # richieste Gemini mandate per la domanda in corso, per modello (anche quelle fallite)


def _di_servizio(e: Exception) -> bool:
    """Errori di Gemini o della rete (quota, credito, sovraccarico, timeout): ha senso provare un altro modello.
    Tutto il resto è un errore nel codice di Jarvis, che un altro modello ripeterebbe identico: il 10/10
    "name 'types' is not defined" è stato riprovato 6 volte su 3 modelli e spiegato come "Gemini non risponde"."""
    import httpx
    from google.genai import errors

    return isinstance(e, (errors.APIError, errors.UnknownApiResponseError, httpx.HTTPError, ConnectionError,
                          TimeoutError))

# Comandi diretti: (frase intera, nome, azione). Ogni frase qui risparmia almeno 2 richieste Gemini; le
# espressioni descrivono la frase INTERA, così "cosa devo fare per arrivare a Roma" va comunque al modello
_CITTA = r"(?: (?:a|ad|in) ([a-zà-ù' ]+?))?"
_GIORNO = (r"(oggi|domani|dopodomani|(?:(?:la )?prossim[oa] )?(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|"
           r"venerd[iì]|sabato|domenica))")
COMANDI = [
    (r"(?:quanto ho consumato|consumi|quante richieste (?:ho fatto|mi restano)(?: oggi)?)", "consumi",
     lambda m: quota.consumi()),
    (r"(?:dove ho (?:parcheggiato|lasciato la macchina)|dov'è la (?:mia )?macchina|dove sta la macchina)",
     "dove_ho_parcheggiato", lambda m: parcheggio.dove_ho_parcheggiato()),
    (r"(?:(?:che )?cosa (?:devo|ho da) fare|(?:le mie |elenca le |dimmi le )?(?:attività|cose da fare|to ?do))"
     r"(?: oggi| questa settimana| su notion)?", "attivita", lambda m: notion.attivita_in_breve()),
    (r"(?:(?:che )?cosa ho|che impegni ho|impegni(?: di)?|agenda(?: di)?) (oggi|domani)", "impegni",
     lambda m: calendario.impegni_in_breve(0 if m.group(1) == "oggi" else 1)),
    (rf"(?:(?:che |qual è l'|quale )?allenamento (?:ho |devo fare |c'è |di |per )?{_GIORNO}|"
     rf"(?:cosa|che) (?:faccio|devo fare|ho) {_GIORNO} di allenamento|(?:la )?scheda di {_GIORNO})",
     "seduta_allenamento", lambda m: allenamento.seduta_allenamento(next(g for g in m.groups() if g))),
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
    (r"(?:quanto ho speso|spese di) oggi", "spese_riepilogo", lambda m: spese.in_breve("oggi")),
    (r"(?:quanto ho speso|spese del)(?: questo| nel)? mese", "spese_riepilogo", lambda m: spese.in_breve("mese")),
    (r"(?:quanto ho sul conto|(?:qual è il |dimmi il )?(?:mio )?saldo(?: del conto)?)", "spese_riepilogo",
     lambda m: spese.in_breve("saldo")),
]
# strumenti il cui risultato arriva all'utente così com'è, senza il riassunto del modello: l'allenamento va detto per
# intero (il 12/10 Flash-Lite aveva risposto con una battuta e il solo link, senza esercizi); la spesa è già una lista
INTEGRALI = {"seduta_allenamento", "consigli_spesa"}
RICORDA = re.compile(r"^(?:jarvis,?\s+)?(?:ricordati|ricorda|tieni a mente|memorizza)\b(?:\s+che)?\s+(.+)", re.I | re.S)


def _esegui(nome: str, args: dict, log) -> str:
    _usati.append(nome)
    log(f"  [strumento] {nome}({args})")
    funzione = tools.PER_NOME.get(nome)
    try:
        esito = funzione(**args) if funzione else f"Strumento '{nome}' inesistente."
    except Exception as e:  # il modello a volte passa argomenti sbagliati
        esito = f"Errore: {e}"
    esito = str(esito)[:4000]
    _risultati.append(f"{nome}: {esito}")
    return esito


class Jarvis:
    def __init__(self):
        self.storia: list[dict] = []
        self.recenti: list[str] = []  # risultati degli strumenti delle ultime MAX_RECENTI domande che li hanno usati
        self.ultima = 0.0  # time.time() dell'ultimo scambio, per PAUSA_AZZERA
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
            risposta = self._chiedi(testo, log, canale)
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
            self._scadenza(print)
            self.storia.append({"role": "user", "content": contesto or domanda})
            self.storia.append({"role": "assistant", "content": risposta})
            self.ultima = time.time()
        for ascoltatore in self.ascoltatori:
            try:
                ascoltatore(canale, domanda, risposta)
            except Exception as e:
                print(f"[ascoltatore] {e}")

    def _chiedi(self, testo: str, log, canale: str) -> str:
        _usati.clear()
        _risultati.clear()
        _chiamate.clear()
        inizio = time.monotonic()
        # una riga per domanda: senza, le righe [strumento] non dicono né quando né per quale domanda
        log(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {canale or 'terminale'}: {testo[:150]!r}")
        self._scadenza(log)
        self.storia.append({"role": "user", "content": testo})
        risposta = self._comando_diretto(testo, log)
        diretto = risposta is not None
        errore = ""
        falliti: list[str] = []
        if risposta is None and self.gemini:
            scartati: set[str] = set()  # modelli falliti per motivi diversi dal limite al minuto
            for _ in range(6):
                modello = quota.prossimo(scartati)
                if modello is None:
                    break
                try:
                    risposta = self._chiedi_gemini(modello, log)
                    break
                except Exception as e:
                    if not _di_servizio(e):
                        log("  [errore interno]\n" + traceback.format_exc().rstrip())
                        risposta = (f"Ho un guasto nel mio codice ({type(e).__name__}: {e}), signore: "
                                    "riprovare non serve, i dettagli sono in jarvis.log.")
                        falliti.append(f"{modello}: errore interno")
                        break
                    # quota (429), rete assente, modello sovraccarico (503), credito (402)
                    errore = str(e)
                    falliti.append(f"{modello}: {errore[:40]}")
                    tipo = quota.segna_errore(modello, errore)
                    quota.registra(modello, "cervello", "429" if tipo else "altro")
                    log(f"  [gemini] {modello} non disponibile{' (limite al ' + tipo + ')' if tipo else ''}: {errore[:80]}")
                    if tipo != "minuto":
                        scartati.add(modello)
        riserva_provata = False
        if risposta is None and riserva.configurata():
            riserva_provata = True
            risposta = self._chiedi_riserva(log, falliti)
        if risposta is None:
            risposta = self._spiega_errore(errore) + (" Non risponde nemmeno il modello di riserva." if riserva_provata
                                                      else "")

        m = RICORDA.match(testo.strip())
        if m and "ricorda" not in _usati and "ricerca_e_salva" not in _usati:
            # il modello a volte risponde "terrò a mente" senza salvare niente: lo salviamo noi
            log(f"  [ripiego] ricorda({m.group(1)!r})")
            memoria.ricorda(m.group(1).rstrip(".!"))

        risposta = risposta.strip() or "Non ho una risposta, signore."
        self.storia.append({"role": "assistant", "content": risposta})
        self.ultima = time.time()
        if _risultati:
            self.recenti = (self.recenti + [f"Domanda: {testo}\n" + "\n".join(_risultati)])[-MAX_RECENTI:]
        # tempo e costo della domanda: una sola domanda può arrivare a ~20 richieste (riprova con tutti gli
        # strumenti, secondo tentativo della verifica, fino a MAX_PASSI giri di strumenti)
        if diretto:
            come = f"comando diretto {_usati[0] if _usati else ''}".rstrip()
        else:
            come = ", ".join(f"{m} ×{n}" for m, n in _chiamate.items()) or "nessuna richiesta Gemini"
            if _usati:
                come += f" · strumenti: {', '.join(_usati)}"
        if falliti:
            come += f" · falliti: {'; '.join(falliti)}"
        log(f"  [fine] {time.monotonic() - inizio:.1f} s · {come}")
        return risposta

    def _chiedi_gemini(self, modello: str, log) -> str:
        domande = [m["content"] for m in self.storia if m["role"] == "user"]
        scelti = gruppi.per_conversazione(domande)
        if "sapere" not in scelti and (noti := sapere.citati(domande[-1])):
            # la domanda parla di un argomento già studiato: Gemini deve poter leggere la nota prima del web
            log(f"  [sapere] argomento già studiato: {', '.join(noti[:3])}")
            scelti.append("sapere")
        risposta = self._giro_gemini(modello, scelti, log)
        if scelti == ["base"] and not _usati and gruppi.RINUNCIA.search(risposta):
            # nessuna parola aveva indicato un gruppo e il modello dice di non poterlo fare: forse lo strumento
            # giusto era fuori dal gruppo base. Si riprova una volta con tutti
            log(f"  [gruppi] risposta di rinuncia con il solo gruppo base, riprovo con tutti: {risposta[:60]!r}")
            risposta = self._giro_gemini(modello, gruppi.TUTTI, log)
        return self._integrale(risposta, log)

    def _integrale(self, risposta: str, log) -> str:
        """Con i soli strumenti di INTEGRALI la risposta è il loro risultato, non il riassunto del modello."""
        esiti = [r.split(": ", 1)[1] for r in dict.fromkeys(_risultati)]
        # un errore dello strumento (argomenti sbagliati) invece lo spiega il modello
        if _usati and set(_usati) <= INTEGRALI and not any(e.startswith("Errore:") for e in esiti):
            log(f"  [integrale] risposta = risultato di {', '.join(dict.fromkeys(_usati))}")
            return "\n\n".join(esiti)
        return risposta

    def _chiedi_riserva(self, log, falliti: list[str]) -> str | None:
        """Nessun modello Gemini risponde: Mistral, senza il gruppo calendario e senza gli strumenti di
        riserva.ESCLUSI. Un solo tentativo: se la verifica trova dati senza fonte, l'avviso va subito nella risposta.
        None se anche Mistral non risponde."""
        domande = [m["content"] for m in self.storia if m["role"] == "user"]
        scelti = [g for g in gruppi.per_conversazione(domande) if g != "calendario"]
        funzioni, regole = gruppi.strumenti_e_regole(scelti, tools.PER_NOME)
        log(f"  [riserva] {riserva.MODELLO}: {', '.join(scelti)}")
        try:
            risposta, richieste = riserva.rispondi(_sistema(regole, [r[:2500] for r in self.recenti]),
                                                   self.storia[-MAX_STORIA:], funzioni,
                                                   lambda nome, args: _esegui(nome, args, log), MAX_PASSI)
        except requests.RequestException as e:
            _chiamate[riserva.MODELLO] = _chiamate.get(riserva.MODELLO, 0) + 1
            falliti.append(f"{riserva.MODELLO}: {str(e)[:40]}")
            log(f"  [riserva] non risponde: {str(e)[:120]}")
            return None
        except Exception as e:  # un errore nel codice della riserva, non di Mistral
            log("  [errore interno]\n" + traceback.format_exc().rstrip())
            falliti.append(f"{riserva.MODELLO}: errore interno")
            return (f"Gemini non risponde e il modello di riserva ha un guasto nel mio codice ({type(e).__name__}: "
                    f"{e}), signore: i dettagli sono in jarvis.log.")
        _chiamate[riserva.MODELLO] = _chiamate.get(riserva.MODELLO, 0) + richieste
        risposta = self._integrale(riserva.senza_markdown(risposta), log)
        dubbi = verifica.controlla(risposta, self._fonti(), _usati, _risultati, bool(self.recenti))
        verifica.registra(self.storia[-1]["content"], list(_usati), list(_risultati), self.recenti, [(risposta, dubbi)],
                          modello=riserva.MODELLO)
        if dubbi:
            log(f"  [verifica] dati senza fonte (riserva): {'; '.join(dubbi)}, aggiungo l'avviso")
            risposta = verifica.con_avviso(risposta, dubbi)
        return f"{risposta.rstrip()} {riserva.ETICHETTA}" if risposta.strip() else None

    def _giro_gemini(self, modello: str, scelti: list[str], log) -> str:
        from google.genai import types

        strumenti, regole = gruppi.strumenti_e_regole(scelti, tools.PER_NOME)
        log(f"  [gruppi] {', '.join(scelti)} ({len(strumenti)} strumenti)")
        config = types.GenerateContentConfig(
            system_instruction=_sistema(regole, [r[:2500] for r in self.recenti]),
            tools=strumenti,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            http_options=types.HttpOptions(timeout=20000),
        )
        contenuti = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
            for m in self.storia[-MAX_STORIA:]
        ]
        risposta = self._ciclo(modello, config, contenuti, log)
        # "ricordati che ..." lo salva comunque il ripiego di RICORDA dopo la risposta: lì "Fatto" è vero
        usati = _usati if _usati or not RICORDA.match(self.storia[-1]["content"].strip()) else ["ricorda (ripiego)"]
        dubbi = verifica.controlla(risposta, self._fonti(), usati, _risultati, bool(self.recenti))
        tentativi = [(risposta, dubbi)]
        if dubbi:
            # un solo secondo tentativo, sugli stessi contenuti: i risultati già ottenuti non si richiedono
            log(f"  [verifica] dati senza fonte: {'; '.join(dubbi)} — riprovo: {risposta[:80]!r}")
            contenuti.append(types.Content(role="model", parts=[types.Part(text=risposta)]))
            contenuti.append(types.Content(role="user", parts=[types.Part(text=verifica.correzione(dubbi))]))
            risposta = self._ciclo(modello, config, contenuti, log)
            dubbi = verifica.controlla(risposta, self._fonti(), usati, _risultati, bool(self.recenti))
            tentativi.append((risposta, dubbi))
            if dubbi:
                log(f"  [verifica] ancora senza fonte: {'; '.join(dubbi)}, aggiungo l'avviso")
                risposta = verifica.con_avviso(risposta, dubbi)
        verifica.registra(self.storia[-1]["content"], list(_usati), list(_risultati), self.recenti, tentativi)
        return risposta

    def _fonti(self) -> list[str]:
        """Da dove può venire legittimamente un dato: strumenti di adesso e delle ultime domande, domande dell'utente
        (anche il contesto delle foto), memoria, ora attuale. Le risposte precedenti di Jarvis no: ripeterebbero
        un dato inventato come se fosse verificato."""
        domande = [m["content"] for m in self.storia if m["role"] == "user"]
        return [*_risultati, *self.recenti, *domande, memoria.contenuto() or "",
                f"{tools.data_ora()} {datetime.datetime.now():%H:%M}"]

    def _ciclo(self, modello: str, config, contenuti: list, log) -> str:
        """Domanda → strumenti → risposta, al massimo MAX_PASSI giri di strumenti."""
        from google.genai import types

        for _ in range(MAX_PASSI):
            r = self._genera(modello, contenuti, config)
            if not r.function_calls:
                return r.text or ""
            # il contenuto del modello va rimandato intero: contiene le firme del ragionamento
            contenuti.append(r.candidates[0].content)
            contenuti.append(types.Content(role="user", parts=[
                types.Part.from_function_response(name=fc.name, response={"risultato": _esegui(fc.name, dict(fc.args or {}), log)})
                for fc in r.function_calls
            ]))
        r = self._genera(modello, contenuti, config.model_copy(update={"tools": None}))
        return r.text or ""

    def _genera(self, modello: str, contenuti: list, config):
        _chiamate[modello] = _chiamate.get(modello, 0) + 1  # contata prima: anche una richiesta fallita è partita
        r = self.gemini.models.generate_content(model=modello, contents=contenuti, config=config)
        quota.registra(modello, "cervello")
        return r

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
            _risultati.append(f"{nome}: {risposta}")  # "e in tutto quanto fa?" deve poter usare questi numeri
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

    def _scadenza(self, log):
        """Azzera la conversazione se l'ultimo scambio è più vecchio di PAUSA_AZZERA (da chiamare sotto lock)."""
        if self.storia and time.time() - self.ultima > PAUSA_AZZERA:
            log(f"  [contesto] {int((time.time() - self.ultima) // 60)} min senza domande: conversazione azzerata")
            self.dimentica()

    def dimentica(self):
        self.storia.clear()
        self.recenti.clear()


if __name__ == "__main__":
    # modalità testo per provare il cervello da terminale
    j = Jarvis()
    while True:
        try:
            domanda = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if domanda:
            print("Jarvis:", j.chiedi(domanda, canale="terminale"))
