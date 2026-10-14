"""Bot Telegram: messaggi di testo, foto e documenti, risponde solo al proprietario. I vocali non li ascolta più
(la voce è stata tolta il 2026-10-14, su richiesta dell'utente)."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import requests

import alimentazione
import allenamento
import backup
import brain
import calendario
import controllo
import cronometer
import dispensa
import spesa
import documenti
import foto
import parcheggio
import posta
import promemoria
import quota
import rete
import riavvio
import ritardi


# tastiera fissa sotto la chat: il pulsante chiede al telefono il GPS e lo manda con un tocco
TASTIERA = {"keyboard": [[{"text": "📍 Ho parcheggiato", "request_location": True}]],
            "resize_keyboard": True, "is_persistent": True}
# scritto da /riavvia: la copia nuova lo trova all'avvio e conferma su Telegram che è ripartita
RIAVVIO = Path(__file__).with_name("riavvio.json")


def _in_linea(pulsanti) -> dict:
    """Pulsanti attaccati al messaggio; una lista vuota li toglie (con editMessageText)."""
    return {"inline_keyboard": [[{"text": testo, "callback_data": dati} for testo, dati in riga] for riga in pulsanti]}


class BotTelegram:
    def __init__(self, jarvis, token: str, utente_autorizzato: int):
        self.jarvis = jarvis
        self.api = f"https://api.telegram.org/bot{token}"
        self.file_api = f"https://api.telegram.org/file/bot{token}"
        self.utente = utente_autorizzato
        self.aggiornamento = 0  # update_id del messaggio in gestione
        self.coda: list[tuple[str, list | None]] = []  # messaggi automatici non partiti per la rete (consegna)
        self._coda_lock = threading.Lock()

    def invia(self, chat_id, testo, tastiera=False):
        # Telegram rifiuta messaggi oltre 4096 caratteri
        for i in range(0, max(len(testo), 1), 4000):
            dati = {"chat_id": chat_id, "text": testo[i:i + 4000]}
            if tastiera:
                dati["reply_markup"] = TASTIERA
            requests.post(f"{self.api}/sendMessage", json=dati, timeout=30)

    def invia_pulsanti(self, chat_id, testo, pulsanti):
        """pulsanti: righe di (etichetta, dati); i dati tornano in gestisci_pulsante quando ne premi uno."""
        dati = {"chat_id": chat_id, "text": testo[:4000]}
        if pulsanti:
            dati["reply_markup"] = _in_linea(pulsanti)
        requests.post(f"{self.api}/sendMessage", json=dati, timeout=30)

    def gestisci_pulsante(self, cq):
        dati, msg = cq.get("data", ""), cq.get("message") or {}
        # la risposta toglie la rotellina dal pulsante sul telefono; per l'allenamento anche un avviso, perché il
        # messaggio cambia solo dopo qualche secondo (il recupero legge il calendario) e il 13/10 "Saltato" è stato
        # premuto 3 volte
        avviso = {"text": "Ricevuto, un attimo…"} if dati.startswith("all:") else {}
        requests.post(f"{self.api}/answerCallbackQuery", json={"callback_query_id": cq["id"], **avviso}, timeout=10)
        if cq.get("from", {}).get("id") != self.utente:
            return
        if dati.startswith("dis:"):
            self.pulsante_dispensa(dati, msg)
            return
        if not dati.startswith("all:"):
            return
        print(f"[allenamento] pulsante {dati}")
        testo, pulsanti = allenamento.pulsante(dati)
        if testo is None:  # già segnato da una pressione precedente
            return
        # l'esito si aggiunge al messaggio della domanda, che perde i pulsanti: non si può premere due volte
        requests.post(f"{self.api}/editMessageText", json={
            "chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
            "text": f"{msg.get('text', '')}\n\n{testo}"[:4000], "reply_markup": _in_linea(pulsanti)}, timeout=30)

    def pulsante_dispensa(self, dati, msg):
        """La lista di uno scontrino o di un codice a barre: un prodotto scelto/tolto cambia solo i pulsanti,
        Aggiungi e Annulla chiudono la lista e scrivono l'esito sotto."""
        print(f"[dispensa] pulsante {dati}")
        testo, pulsanti = dispensa.pulsante(dati)
        base = {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"], "reply_markup": _in_linea(pulsanti)}
        if testo is None:
            requests.post(f"{self.api}/editMessageReplyMarkup", json=base, timeout=30)
        else:
            requests.post(f"{self.api}/editMessageText", json={**base, "text": f"{msg.get('text', '')}\n\n{testo}"[:4000]},
                          timeout=30)
            self.jarvis.aggiungi_scambio("telegram", "(pulsante della lista per la dispensa)", testo)

    def consegna(self, testo, pulsanti=None):
        """Un messaggio automatico all'utente. I giri lo segnano come mandato appena lo restituiscono (riepilogo,
        domanda "fatto?", promemoria tolto dall'elenco): se la rete cade proprio allora resta in coda e riparte
        appena torna, invece di perdersi."""
        try:
            self.invia_pulsanti(self.utente, testo, pulsanti) if pulsanti else self.invia(self.utente, testo)
        except requests.RequestException as e:
            print(f"[telegram] messaggio in coda, rete assente ({type(e).__name__}): {testo[:60]!r}")
            with self._coda_lock:
                self.coda.append((testo, pulsanti))

    def _rete_pronta(self) -> bool:
        """Prima di ogni giro automatico: senza rete il giro salta il minuto; con la rete parte la coda."""
        if not rete.disponibile():
            return False
        with self._coda_lock:
            coda, self.coda = self.coda, []
        for i, (testo, pulsanti) in enumerate(coda):
            try:
                self.invia_pulsanti(self.utente, testo, pulsanti) if pulsanti else self.invia(self.utente, testo)
            except requests.RequestException:
                with self._coda_lock:
                    self.coda = coda[i:] + self.coda
                return False
        return True

    def ciclo_allenamento(self):
        """Pianificazione della settimana e domande "fatto?" dopo le sedute (allenamento.py), una volta al minuto."""
        while True:
            if self._rete_pronta():
                try:
                    for testo, pulsanti in allenamento.controlla():
                        self.consegna(testo, pulsanti)
                except Exception as e:
                    print(f"[allenamento] errore: {e}")
            time.sleep(60)

    def ciclo_alimentazione(self):
        """Il riepilogo delle 21:30 con i dati di Cronometer (alimentazione.py), i pasti tolti dalla dispensa
        (dispensa.py) e i consigli per la spesa del sabato (spesa.py), controllati una volta al minuto."""
        while True:
            if self._rete_pronta():
                for modulo in (alimentazione, dispensa, spesa):
                    try:
                        for testo in modulo.controlla():
                            self.consegna(testo)
                    except Exception as e:
                        print(f"[{modulo.__name__}] errore: {e}")
            time.sleep(60)

    def ciclo_controllo(self):
        """Il controllo dei servizi esterni del mattino (controllo.py) e il backup della sera (backup.py): arriva un
        messaggio solo se qualcosa è rotto."""
        while True:
            if self._rete_pronta():  # senza rete il controllo darebbe tutto rotto e il push non partirebbe
                for modulo in (controllo, backup):
                    try:
                        for testo in modulo.controlla():
                            self.consegna(testo)
                    except Exception as e:
                        print(f"[{modulo.__name__}] errore: {e}")
            time.sleep(60)

    def manda_parcheggio(self, chat_id):
        punto = parcheggio.posizione()
        if punto:  # il segnaposto si apre in Google Maps / Apple Mappe e da lì parte la navigazione
            requests.post(f"{self.api}/sendLocation",
                          json={"chat_id": chat_id, "latitude": punto[0], "longitude": punto[1]}, timeout=30)

    def scrive(self, chat_id):
        requests.post(f"{self.api}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)

    def gestisci(self, msg):
        chat_id = msg["chat"]["id"]
        mittente = msg.get("from", {}).get("id")
        if mittente != self.utente:
            # chiunque trovi il bot non deve poter comandare il tuo PC
            self.invia(chat_id, f"Non ti conosco. Il tuo ID è {mittente}.")
            return

        documento = msg.get("document") or {}
        if "photo" in msg or documento.get("mime_type", "").startswith("image/"):
            self.gestisci_foto(chat_id, msg)
            return
        if documento:
            self.gestisci_documento(chat_id, msg)
            return
        if "location" in msg:
            punto = msg["location"]
            risposta = parcheggio.salva(punto["latitude"], punto["longitude"])
            self.invia(chat_id, risposta, tastiera=True)
            # nella conversazione, così "piano -2" detto subito dopo diventa la nota del parcheggio
            self.jarvis.aggiungi_scambio("telegram", "📍 posizione del parcheggio", risposta)
            return
        if "voice" in msg or "audio" in msg:
            self.invia(chat_id, "I vocali non li ascolto più, signore: me lo scriva, per favore.")
            return
        testo = msg.get("text", "").strip()
        if not testo:
            return
        if testo in ("/start", "/nuova"):
            self.jarvis.dimentica()
            self.invia(chat_id, "Pronto, signore. Conversazione azzerata.", tastiera=True)
            return
        if testo == "/promemoria":
            self.invia(chat_id, promemoria.lista_promemoria())
            return
        if testo == "/consumi":
            self.invia(chat_id, quota.consumi())  # senza modello: non consuma a sua volta una richiesta
            return
        if testo == "/controllo":
            self.scrive(chat_id)
            self.invia(chat_id, controllo.rapporto())  # circa 10 s: tutte le prove in parallelo
            return
        if testo == "/backup":
            self.scrive(chat_id)
            problemi = backup.esegui()
            if not problemi:
                backup.fatto_ora()
            self.invia(chat_id, "💾 Backup fatto: zip cifrato in OneDrive e codice su GitHub." if not problemi
                       else "💾 Backup con problemi: " + "; ".join(problemi))
            return
        if testo == "/treni":
            self.invia(chat_id, ritardi.treni_seguiti())
            return
        if testo == "/schermo":
            self.manda_screenshot(chat_id)
            return
        if testo == "/riavvia":
            self.riavvia(chat_id)
            return
        if testo.startswith("/invia"):
            # l'unica strada per spedire una mail: il modello prepara, tu confermi
            numero = testo.removeprefix("/invia").strip()
            if not numero:
                self.invia(chat_id, "Scrivi /invia seguito dal numero della bozza, es. /invia 1")
                return
            self.invia(chat_id, posta.invia_bozza(numero))
            return

        self.scrive(chat_id)
        self.invia(chat_id, self.jarvis.chiedi(testo, canale="telegram"))
        if "dove_ho_parcheggiato" in brain._usati:
            self.manda_parcheggio(chat_id)

    def gestisci_foto(self, chat_id, msg):
        """Foto (compressa da Telegram) o immagine mandata come file (qualità piena, meglio per lavagne e documenti)."""
        self.scrive(chat_id)
        if "photo" in msg:
            file_id, estensione = msg["photo"][-1]["file_id"], ".jpg"  # l'ultima è la risoluzione più alta
        else:
            documento = msg["document"]
            file_id = documento["file_id"]
            estensione = os.path.splitext(documento.get("file_name", ""))[1] or ".jpg"
        info = requests.get(f"{self.api}/getFile", params={"file_id": file_id}, timeout=30).json()["result"]
        dati = requests.get(f"{self.file_api}/{info['file_path']}", timeout=60).content
        didascalia = msg.get("caption", "").strip()
        domanda = f"📷 foto{': ' + didascalia if didascalia else ''}"
        if self.foto_dispensa(chat_id, dati, domanda):
            return
        a = foto.analizza(dati, estensione, didascalia)
        if a.get("tipo") == "scontrino" and dispensa.configurata():
            # lo scontrino della spesa va nella dispensa, non in Obsidian; se non ci sono cibi si salva come sempre
            try:
                proposta = dispensa.da_scontrino(a.get("trascrizione", ""))
            except Exception as e:
                print(f"[dispensa] scontrino non letto: {e}")
                proposta = None
            if proposta:
                testo = (a.get("risposta", "").strip() + "\n\n" if "?" in didascalia else "") + proposta[0]
                self.manda_proposta(chat_id, domanda, testo, proposta[1])
                return
        risposta, riassunto = foto.salva_foto(dati, estensione, didascalia, a)
        self.invia(chat_id, risposta)
        self.jarvis.aggiungi_scambio("telegram", domanda, risposta, contesto=f"{domanda}\n{riassunto}")

    def foto_dispensa(self, chat_id, dati: bytes, domanda: str) -> bool:
        """Se nella foto ci sono codici a barre di prodotti, la lista per la dispensa al posto della foto in Obsidian."""
        if not dispensa.configurata():
            return False
        try:
            trovati = dispensa.codici(dati)
            if not trovati:
                return False
            testo, pulsanti = dispensa.da_codici(trovati)
        except Exception as e:
            print(f"[dispensa] codice a barre non gestito: {e}")
            return False
        self.manda_proposta(chat_id, domanda, testo, pulsanti)
        return True

    def manda_proposta(self, chat_id, domanda: str, testo: str, pulsanti):
        self.invia_pulsanti(chat_id, testo, pulsanti)
        # i prodotti stanno sui pulsanti: nella conversazione vanno scritti, per "il pollo era un chilo"
        prodotti = [etichetta[2:] for riga in pulsanti[:-1] for etichetta, _ in riga]
        self.jarvis.aggiungi_scambio("telegram", domanda, testo, contesto=f"{domanda}\n{testo}\nProdotti proposti "
                                     f"(non ancora in dispensa finché l'utente non preme Aggiungi): {'; '.join(prodotti)}")

    def gestisci_documento(self, chat_id, msg):
        """PDF, testo e altri file: la didascalia è la domanda; senza didascalia arriva un riassunto."""
        documento = msg["document"]
        nome = documento.get("file_name", "documento")
        if documento.get("file_size", 0) > 20 * 1024 * 1024:
            self.invia(chat_id, f"{nome} supera i 20 MB: Telegram non lascia scaricare ai bot file così grandi.")
            return
        self.scrive(chat_id)
        info = requests.get(f"{self.api}/getFile", params={"file_id": documento["file_id"]}, timeout=30).json()["result"]
        dati = requests.get(f"{self.file_api}/{info['file_path']}", timeout=120).content
        didascalia = msg.get("caption", "").strip()
        mime = documenti.tipo(nome, documento.get("mime_type", ""))
        domanda = f"📄 {nome}{': ' + didascalia if didascalia else ''}"
        if documenti.e_testo(mime):
            # un file di testo entra nella domanda: risponde il cervello, con gli strumenti a disposizione
            contenuto = documenti.testo(dati, mime)
            richiesta = didascalia or "Riassumi questo file in poche frasi."
            self.invia(chat_id, self.jarvis.chiedi(f"{richiesta}\n\nFile {nome}:\n{contenuto}", canale="telegram"))
            return
        try:
            risposta = documenti.leggi(dati, nome, mime, didascalia, origine=" mandato dall'utente su Telegram")
        except documenti.NonLeggibile as e:
            self.invia(chat_id, str(e))
            return
        self.invia(chat_id, risposta)
        # nella conversazione resta la lettura del file: le domande successive ("e la scadenza?") la conoscono
        self.jarvis.aggiungi_scambio("telegram", domanda, risposta, contesto=domanda)

    def manda_screenshot(self, chat_id):
        from PIL import ImageGrab

        percorso = os.path.join(tempfile.gettempdir(), "jarvis_schermo.jpg")
        ImageGrab.grab(all_screens=True).convert("RGB").save(percorso, quality=70)
        with open(percorso, "rb") as f:
            requests.post(f"{self.api}/sendPhoto", data={"chat_id": chat_id}, files={"photo": f}, timeout=60)

    def riavvia(self, chat_id):
        self.invia(chat_id, "Mi riavvio, signore: prima faccio le prove, poi torno tra un minuto circa.")
        # conferma a Telegram questo messaggio: altrimenti la copia nuova, che riparte senza offset, lo riceve
        # di nuovo e si riavvia all'infinito
        requests.get(f"{self.api}/getUpdates", params={"offset": self.aggiornamento + 1, "timeout": 0}, timeout=30)
        RIAVVIO.write_text(json.dumps({"chat_id": chat_id, "quando": time.strftime("%H:%M")}), encoding="utf-8")
        riavvio.lancia(da_telegram=True)

    def conferma_riavvio(self):
        if not RIAVVIO.exists():
            return
        try:
            dati = json.loads(RIAVVIO.read_text(encoding="utf-8"))
            self.invia(dati["chat_id"], f"Di nuovo operativo, signore (riavvio chiesto alle {dati['quando']}).")
        except (OSError, ValueError, KeyError, requests.RequestException) as e:
            print(f"[telegram] conferma del riavvio non mandata: {e}")
        RIAVVIO.unlink(missing_ok=True)

    def ciclo_promemoria(self):
        while True:
            # senza rete il promemoria resta nell'elenco e arriva appena torna (prima veniva tolto e perso)
            if self._rete_pronta():
                try:
                    for messaggio in promemoria.scaduti():
                        self.consegna(messaggio)  # in chat privata l'id della chat è l'id dell'utente
                except Exception as e:
                    print(f"[promemoria] errore: {e}")
                try:
                    for messaggio in ritardi.controlla():  # ogni treno seguito viene interrogato al massimo ogni 5 min
                        self.consegna(messaggio)
                except Exception as e:
                    print(f"[ritardi] errore: {e}")
            time.sleep(20)

    def presenta_pulsante(self):
        """La tastiera resta finché il bot non la toglie: basta mandarla una volta, non a ogni avvio di Jarvis."""
        dati = parcheggio._carica()
        if dati.get("pulsante_mostrato"):
            return
        try:
            self.invia(self.utente, "Novità: quando parcheggi premi \"📍 Ho parcheggiato\" qui sotto e mi segno dov'è "
                                    "la macchina. Poi chiedimi \"dov'è la macchina?\"", tastiera=True)
        except requests.RequestException:
            return  # niente rete: ci riprova al prossimo avvio
        dati["pulsante_mostrato"] = True
        parcheggio._scrivi(dati)

    def avvia(self):
        threading.Thread(target=self.ciclo_promemoria, daemon=True).start()
        threading.Thread(target=self.ciclo_controllo, daemon=True).start()
        if calendario.configurato():
            threading.Thread(target=self.ciclo_allenamento, daemon=True).start()
        if cronometer.configurato():
            threading.Thread(target=self.ciclo_alimentazione, daemon=True).start()
        print("Bot Telegram attivo.")
        self.presenta_pulsante()
        self.conferma_riavvio()
        offset = None
        while True:
            try:
                aggiornamenti = requests.get(
                    f"{self.api}/getUpdates", params={"timeout": 50, "offset": offset}, timeout=60
                ).json()
                for agg in aggiornamenti.get("result", []):
                    offset = agg["update_id"] + 1
                    self.aggiornamento = agg["update_id"]
                    if "message" in agg:
                        try:
                            self.gestisci(agg["message"])
                        except Exception as e:
                            print(f"[telegram] errore: {e}")
                            self.invia(agg["message"]["chat"]["id"], f"Qualcosa è andato storto: {e}")
                    elif "callback_query" in agg:
                        try:
                            self.gestisci_pulsante(agg["callback_query"])
                        except Exception as e:
                            print(f"[telegram] errore del pulsante: {e}")
                            self.invia(self.utente, f"Il pulsante non è andato: {e}")
            except requests.RequestException as e:
                print(f"[telegram] connessione persa ({e}), riprovo tra 5 s")
                time.sleep(5)
