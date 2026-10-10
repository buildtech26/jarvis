"""Bot Telegram: messaggi di testo e vocali, risponde solo al proprietario."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import requests

import brain
import documenti
import foto
import parcheggio
import posta
import promemoria
import quota
import riavvio
import ritardi
import voice


# tastiera fissa sotto la chat: il pulsante chiede al telefono il GPS e lo manda con un tocco
TASTIERA = {"keyboard": [[{"text": "📍 Ho parcheggiato", "request_location": True}]],
            "resize_keyboard": True, "is_persistent": True}
# scritto da /riavvia: la copia nuova lo trova all'avvio e conferma su Telegram che è ripartita
RIAVVIO = Path(__file__).with_name("riavvio.json")


class BotTelegram:
    def __init__(self, jarvis, token: str, utente_autorizzato: int):
        self.jarvis = jarvis
        self.api = f"https://api.telegram.org/bot{token}"
        self.file_api = f"https://api.telegram.org/file/bot{token}"
        self.utente = utente_autorizzato
        self.aggiornamento = 0  # update_id del messaggio in gestione

    def invia(self, chat_id, testo, tastiera=False):
        # Telegram rifiuta messaggi oltre 4096 caratteri
        for i in range(0, max(len(testo), 1), 4000):
            dati = {"chat_id": chat_id, "text": testo[i:i + 4000]}
            if tastiera:
                dati["reply_markup"] = TASTIERA
            requests.post(f"{self.api}/sendMessage", json=dati, timeout=30)

    def manda_parcheggio(self, chat_id):
        punto = parcheggio.posizione()
        if punto:  # il segnaposto si apre in Google Maps / Apple Mappe e da lì parte la navigazione
            requests.post(f"{self.api}/sendLocation",
                          json={"chat_id": chat_id, "latitude": punto[0], "longitude": punto[1]}, timeout=30)

    def scrive(self, chat_id):
        requests.post(f"{self.api}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)

    def scarica_vocale(self, file_id) -> str:
        info = requests.get(f"{self.api}/getFile", params={"file_id": file_id}, timeout=30).json()["result"]
        audio = requests.get(f"{self.file_api}/{info['file_path']}", timeout=60).content
        percorso = os.path.join(tempfile.gettempdir(), "jarvis_vocale.oga")
        with open(percorso, "wb") as f:
            f.write(audio)
        return percorso

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
            self.scrive(chat_id)
            testo = voice.trascrivi(self.scarica_vocale((msg.get("voice") or msg["audio"])["file_id"]))
            if not testo:
                self.invia(chat_id, "Non ho capito il vocale.")
                return
            self.invia(chat_id, f"🎙 {testo}")
        else:
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
        risposta, riassunto = foto.salva_foto(dati, estensione, didascalia)
        self.invia(chat_id, risposta)
        domanda = f"📷 foto{': ' + didascalia if didascalia else ''}"
        self.jarvis.aggiungi_scambio("telegram", domanda, risposta, contesto=f"{domanda}\n{riassunto}")

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
        self.invia(chat_id, "Mi riavvio, signore: torno tra un minuto circa.")
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
            try:
                for messaggio in promemoria.scaduti():
                    self.invia(self.utente, messaggio)  # in chat privata l'id della chat è l'id dell'utente
            except Exception as e:
                print(f"[promemoria] errore: {e}")
            try:
                for messaggio in ritardi.controlla():  # ogni treno seguito viene interrogato al massimo ogni 5 min
                    self.invia(self.utente, messaggio)
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
            except requests.RequestException as e:
                print(f"[telegram] connessione persa ({e}), riprovo tra 5 s")
                time.sleep(5)
