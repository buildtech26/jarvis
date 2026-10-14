"""Finestra di chat in stile J.A.R.V.I.S.: Alt+J la apre e la chiude da qualsiasi programma, Esc la nasconde.

La chat è disegnata su un Canvas: sfondo in tinta unita, senza la sfera centrale (scelta dell'utente).
"""
import ctypes
import ctypes.wintypes
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

CARTELLA = os.path.dirname(os.path.abspath(__file__))

# grafite e blu, gli stessi colori della pagina web (web/stile.css)
SFONDO = "#0d0f12"
PANNELLO = "#15181d"
CAMPO = "#1c2027"
BORDO = "#2a2f38"
TESTO = "#e6e8eb"
TENUE = "#8b929d"
ACCENTO = "#4c8dff"
PULSANTE = "#2f6fe0"  # blu più scuro dell'accento: il bianco sopra si legge
TESTO_TU = "#a9c6ff"
ERRORE = "#f47067"

# barra del titolo di Windows (quella con il nome e i pulsanti chiudi/riduci)
TITOLO = "J.A.R.V.I.S."
BARRA = PANNELLO
# scritta dello stesso colore della barra: nella finestra il nome non si vede (scelta dell'utente),
# ma il titolo resta per Alt+Tab e la barra delle applicazioni
BARRA_TESTO = BARRA

CANALI = {"telegram": "✈ da Telegram", "web": "🌐 dalla pagina web"}
MOD_ALT, MOD_NOREPEAT, WM_HOTKEY = 0x0001, 0x4000, 0x0312


def saluto() -> tuple:
    ora = time.localtime().tm_hour
    # dopo mezzanotte l'ora è 0-4: senza il 13 <= ora diceva "Buon pomeriggio"
    testo = "Buongiorno" if 5 <= ora < 13 else "Buon pomeriggio" if 13 <= ora < 18 else "Buonasera"
    # la virgola serve: mostra() riconosce il saluto da ciò che sta prima
    return ("Jarvis", f"{testo}, Mattia", "jarvis", "")


def _pulsante(lato: int, stato: str) -> ImageTk.PhotoImage:
    """Pulsante d'invio rotondo in stile HUD: alone, anello turchese e aeroplanino di carta.

    stato: 'normale' o 'sopra' (mouse sopra).
    """
    from PIL import ImageDraw, ImageFilter

    s = 4  # si disegna 4 volte più grande e poi si riduce: bordi lisci
    n = lato * s
    m = 5 * s  # spazio per l'alone attorno all'anello
    pieno = stato == "sopra"
    riempimento = PULSANTE if pieno else "#181c22"

    alone = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(alone).ellipse((m, m, n - m, n - m), outline=ACCENTO, width=4 * s)
    alone = alone.filter(ImageFilter.GaussianBlur(3 * s))
    intensita = 0.5 if stato != "normale" else 0.15  # alone appena accennato, non neon
    alone.putalpha(alone.getchannel("A").point(lambda a: int(a * intensita)))

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.alpha_composite(alone)
    d = ImageDraw.Draw(img)
    d.ellipse((m, m, n - m, n - m), fill=riempimento, outline=ACCENTO, width=2 * s)

    def p(x, y):  # coordinate relative (0-1) del pulsante
        return (x * n, y * n)

    colore = "#ffffff" if pieno else ACCENTO
    # aeroplanino di carta: punta a destra, coda incavata
    d.polygon([p(0.31, 0.30), p(0.73, 0.50), p(0.31, 0.70), p(0.39, 0.50)], fill=colore)
    return ImageTk.PhotoImage(img.resize((lato, lato), Image.LANCZOS))


class Finestra:
    def __init__(self, jarvis):
        self.jarvis = jarvis
        self.coda: queue.Queue = queue.Queue()  # tkinter va toccato solo dal thread principale
        self.occupato = False
        self.messaggi: list[tuple[str, str, str, str]] = []  # (chi, testo, stile, nota): ridisegnati a ogni resize
        jarvis.ascoltatori.append(self._da_altro_canale)

        try:
            # senza questo, con lo zoom di Windows al 125% il testo è sfocato e le misure sbagliate
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title(TITOLO)
        self.root.configure(bg=SFONDO)
        self._colora_barra()
        icona = os.path.join(CARTELLA, "jarvis.ico")
        if os.path.exists(icona):
            self.root.iconbitmap(icona)
        self.scala = self.root.winfo_fpixels("1i") / 96
        larghezza, altezza = self._px(440), self._px(640)
        self.root.minsize(self._px(360), self._px(440))
        x = self.root.winfo_screenwidth() - larghezza - self._px(16)
        y = self.root.winfo_screenheight() - altezza - self._px(90)  # sopra la barra delle applicazioni
        self.root.geometry(f"{larghezza}x{altezza}+{x}+{y}")
        self.root.protocol("WM_DELETE_WINDOW", self.nascondi)
        self.root.bind("<Escape>", lambda e: self.nascondi())

        famiglie = tkfont.families()
        carattere = next((f for f in ("Bahnschrift", "Segoe UI") if f in famiglie), "TkDefaultFont")
        self.f_testo = (carattere, 11)
        self.f_piccolo = (carattere, 9)
        self.f_titolo = (carattere, 14, "bold")

        self._costruisci()
        self._messaggio(*saluto())

        threading.Thread(target=self._tasto_rapido, daemon=True).start()
        self.root.after(80, self._svuota_coda)

    def _px(self, valore: float) -> int:
        return int(valore * self.scala)

    def _colora_barra(self):
        """Colora la barra del titolo con DWM (Windows 11): sfondo, scritta e bordo; tema scuro per i pulsanti."""
        def colorref(esadecimale: str) -> ctypes.c_int:
            r, g, b = (int(esadecimale[i:i + 2], 16) for i in (1, 3, 5))
            return ctypes.c_int(r | g << 8 | b << 16)  # Windows vuole 0x00BBGGRR

        try:
            self.root.update_idletasks()  # senza, la finestra vera di Windows non esiste ancora
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            dwm = ctypes.windll.dwmapi
            # 20 tema scuro, 34 bordo, 35 sfondo barra, 36 scritta
            for attributo, valore in ((20, ctypes.c_int(1)), (34, colorref(BORDO)),
                                      (35, colorref(BARRA)), (36, colorref(BARRA_TESTO))):
                dwm.DwmSetWindowAttribute(hwnd, attributo, ctypes.byref(valore), ctypes.sizeof(valore))
        except Exception:
            pass  # Windows 10 o più vecchio: resta la barra normale

    # ---------- interfaccia ----------

    def _costruisci(self):
        testata = tk.Frame(self.root, bg=PANNELLO, padx=self._px(16), pady=self._px(10))
        testata.pack(fill="x")
        # niente nome in alto (scelta dell'utente): resta solo lo stato, vuoto a riposo
        self.stato = tk.Label(testata, text="", font=self.f_piccolo, fg=TENUE, bg=PANNELLO)
        self.stato.pack(side="left")
        tk.Button(
            testata, text="Nuova", font=self.f_piccolo, fg=TENUE, bg=PANNELLO, relief="flat", bd=0, padx=8,
            pady=2, cursor="hand2", activebackground=CAMPO, activeforeground=TESTO, command=self._nuova,
        ).pack(side="right")
        tk.Frame(self.root, bg=BORDO, height=1).pack(fill="x")

        # la barra di scrittura va fissata in basso PRIMA della chat, sennò se manca spazio sparisce lei
        piede = tk.Frame(self.root, bg=PANNELLO, padx=self._px(12), pady=self._px(12))
        piede.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=BORDO, height=1).pack(side="bottom", fill="x")

        self.tela = tk.Canvas(self.root, bg=SFONDO, highlightthickness=0, bd=0, yscrollincrement=self._px(20))
        self.tela.pack(fill="both", expand=True)
        self.tela.bind("<Configure>", lambda e: self._dopo_resize())
        for widget in (self.tela, self.root):
            widget.bind("<MouseWheel>", self._rotella)

        # invio a destra, il campo prende lo spazio che resta. Il pulsante è un'immagine disegnata con PIL a
        # risoluzione quadrupla: i cerchi del Canvas di Tk hanno i bordi seghettati
        lato = self._px(48)
        self.img_invia = {s: _pulsante(lato, s) for s in ("normale", "sopra")}

        self.btn_invia = tk.Label(piede, image=self.img_invia["normale"], bg=PANNELLO, bd=0, cursor="hand2")
        self.btn_invia.bind("<Button-1>", lambda e: self._invia())
        self.btn_invia.bind("<Enter>", lambda e: self.btn_invia.config(image=self.img_invia["sopra"]))
        self.btn_invia.bind("<Leave>", lambda e: self.btn_invia.config(image=self.img_invia["normale"]))
        self.btn_invia.pack(side="right", padx=(self._px(8), 0))

        cornice = tk.Frame(piede, bg=CAMPO, padx=self._px(10), pady=self._px(6),
                           highlightthickness=1, highlightbackground=BORDO, highlightcolor=ACCENTO)
        cornice.pack(side="left", fill="both", expand=True)
        self.campo = tk.Text(
            cornice, height=2, width=1, wrap="word", bg=CAMPO, fg=TESTO, insertbackground=ACCENTO, font=self.f_testo,
            relief="flat", bd=0, highlightthickness=0,
        )
        self.campo.pack(fill="both", expand=True)
        self.campo.bind("<Return>", self._invio)

    # ---------- chat disegnata ----------

    def _dopo_resize(self):
        if getattr(self, "_attesa_resize", None):
            self.root.after_cancel(self._attesa_resize)
        self._attesa_resize = self.root.after(120, self._applica_resize)

    def _applica_resize(self):
        self._attesa_resize = None
        self._ridisegna()

    def _ridisegna(self):
        self.tela.delete("msg")
        w = self.tela.winfo_width()
        margine, y = self._px(18), self._px(14)
        larghezza_testo = max(100, w - 2 * margine - self._px(40))
        for chi, testo, stile, nota in self.messaggi:
            destra = stile == "tu"
            x, ancora, allinea = (w - margine, "ne", "right") if destra else (margine, "nw", "left")
            # niente nome sopra i messaggi (scelta dell'utente): chi parla si vede dal lato e dal colore,
            # sopra resta solo la nota (ora e canale) quando c'è
            if stile != "info" and nota:
                id_nome = self.tela.create_text(x, y, text=nota, anchor=ancora, justify=allinea,
                                                fill=TENUE, font=self.f_piccolo, tags="msg")
                y = self.tela.bbox(id_nome)[3] + self._px(3)
            colore = {"tu": TESTO_TU, "info": TENUE, "errore": ERRORE}.get(stile, TESTO)
            carattere = self.f_piccolo if stile == "info" else self.f_testo
            ultimo = self.tela.create_text(x, y, text=testo, anchor=ancora, justify=allinea,
                                           width=larghezza_testo, fill=colore, font=carattere, tags="msg")
            y = self.tela.bbox(ultimo)[3] + self._px(16)
        h = self.tela.winfo_height()
        self.tela.configure(scrollregion=(0, 0, w, max(y, h)))
        self.tela.yview_moveto(1.0)

    def _rotella(self, evento):
        self.tela.yview_scroll(int(-evento.delta / 120), "units")

    def _messaggio(self, chi: str, testo: str, stile: str, nota: str = ""):
        self.messaggi.append((chi, testo, stile, nota))
        self.messaggi = self.messaggi[-200:]
        self._ridisegna()

    def _info(self, testo: str, stile: str = "info"):
        self._messaggio("", testo, stile)

    def _imposta_stato(self, testo: str, colore: str = TENUE):
        self.stato.config(text=testo, fg=colore)

    def _nuova(self):
        self.jarvis.dimentica()
        self.messaggi.clear()
        self._messaggio("Jarvis", "Conversazione azzerata. La memoria permanente resta.", "jarvis")

    # ---------- azioni ----------

    def _invio(self, evento):
        if evento.state & 0x0001:  # Shift premuto: a capo normale
            return None
        self._invia()
        return "break"

    def _invia(self, testo: str | None = None):
        if testo is None:
            testo = self.campo.get("1.0", "end").strip()
            self.campo.delete("1.0", "end")
        if not testo or self.occupato:
            return
        if testo.lower() == "/riavvia":
            # come su Telegram, ma riparte con la finestra aperta; senza questo finiva a Gemini come domanda
            import riavvio

            self._messaggio("Tu", testo, "tu")
            self._messaggio("Jarvis", "Mi riavvio, signore: la finestra si riapre tra un minuto circa. "
                                     "Se le prove falliscono resto come sono e le scrivo su Telegram.", "jarvis")
            riavvio.lancia(da_telegram=False)
            return
        self.occupato = True
        self._messaggio("Tu", testo, "tu")
        self._imposta_stato("sto pensando…", ACCENTO)
        threading.Thread(target=self._lavora, args=(testo,), daemon=True).start()

    def _lavora(self, testo: str):
        def log(riga):
            print(riga)
            if "[strumento]" in riga:
                nome = riga.split("]", 1)[1].strip().split("(", 1)[0]
                self.coda.put(("stato", f"uso {nome.replace('_', ' ')}…"))

        try:
            risposta = self.jarvis.chiedi(testo, log=log, canale="app")
        except Exception as e:
            risposta = f"Qualcosa è andato storto: {e}"
        self.coda.put(("risposta", risposta))
        self.coda.put(("fine", None))

    def _da_altro_canale(self, canale: str, domanda: str, risposta: str):
        if canale != "app":
            self.coda.put(("altro", (canale, domanda, risposta)))

    def _svuota_coda(self):
        try:
            while True:
                tipo, dato = self.coda.get_nowait()
                if tipo == "risposta":
                    self._messaggio("Jarvis", dato, "jarvis")
                elif tipo == "stato":
                    self._imposta_stato(dato, ACCENTO)
                elif tipo == "fine":
                    self.occupato = False
                    self._imposta_stato("")
                elif tipo == "altro":
                    canale, domanda, risposta = dato
                    nota = CANALI.get(canale, canale)
                    self._messaggio("Tu", domanda, "tu", nota)
                    self._messaggio("Jarvis", risposta, "jarvis", nota)
                elif tipo == "mostra":
                    self.mostra()
                elif tipo == "alterna":
                    self.alterna()
        except queue.Empty:
            pass
        self.root.after(80, self._svuota_coda)

    # ---------- mostra / nascondi ----------

    def mostra(self):
        # la finestra nasce nascosta all'accesso a Windows: il saluto del mattino non deve restare fino a sera
        if len(self.messaggi) == 1 and self.messaggi[0][2] == "jarvis" and self.messaggi != [saluto()]:
            if self.messaggi[0][1].split(",")[0] in ("Buongiorno", "Buon pomeriggio", "Buonasera"):
                self.messaggi = [saluto()]
                self._ridisegna()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self.campo.focus_set()

    def nascondi(self):
        self.root.withdraw()

    def alterna(self):
        if self.root.state() == "withdrawn" or self.root.focus_get() is None:
            self.mostra()
        else:
            self.nascondi()

    def _tasto_rapido(self):
        """Alt+J globale: RegisterHotKey vale solo per il thread che lo registra, quindi qui c'è il suo ciclo."""
        tasto = os.getenv("JARVIS_TASTO", "J").upper()[:1]
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_NOREPEAT, ord(tasto)):
            print(f"[finestra] Alt+{tasto} è già usato da un altro programma")
            return
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                self.coda.put(("alterna", None))

    def avvia(self, visibile: bool = True):
        if not visibile:
            self.root.withdraw()
        self.root.mainloop()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    from brain import Jarvis

    Finestra(Jarvis()).avvia()
