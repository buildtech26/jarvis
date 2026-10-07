"""Gmail via IMAP/SMTP con password per le app.

Jarvis legge, cerca, archivia e prepara bozze da solo; l'invio vero avviene solo
con il comando /invia su Telegram, che il modello non può chiamare.
"""
import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
import smtplib
import threading
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

import documenti

load_dotenv(Path(__file__).with_name(".env"))

IMAP, SMTP = "imap.gmail.com", "smtp.gmail.com"
FILE_BOZZE = Path(__file__).with_name("bozze.json")
_lock = threading.Lock()
# l'elenco scarica solo l'inizio di ogni mail: il testo viene prima degli allegati, che restano sul server
ANTEPRIMA_BYTE = 200_000


def configurata() -> bool:
    return bool(os.getenv("GMAIL_INDIRIZZO") and os.getenv("GMAIL_PASSWORD_APP"))


def _credenziali():
    return os.environ["GMAIL_INDIRIZZO"].strip(), os.environ["GMAIL_PASSWORD_APP"].replace(" ", "")


def _connetti() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(IMAP)
    imap.login(*_credenziali())
    return imap


def _cartella(imap, attributo: str) -> str:
    """Nome della cartella speciale (\\Drafts, \\All): in Gmail italiano è [Gmail]/Bozze, non Drafts."""
    _, righe = imap.list()
    for riga in righe:
        riga = riga.decode()
        if attributo in riga:
            return riga.rsplit(' "/" ', 1)[1].strip()
    raise RuntimeError(f"Cartella {attributo} non trovata")


def _decodifica(valore) -> str:
    return str(make_header(decode_header(valore or "")))


def _corpo(msg) -> str:
    html = None
    for parte in msg.walk():
        if parte.get_content_maintype() == "multipart" or parte.get_filename():
            continue
        tipo = parte.get_content_type()
        dati = parte.get_payload(decode=True) or b""
        testo = dati.decode(parte.get_content_charset() or "utf-8", errors="replace")
        if tipo == "text/plain":
            return testo
        if tipo == "text/html" and html is None:
            html = testo
    return documenti.senza_tag(html) if html else ""


def _allegati(msg) -> list[tuple[str, object]]:
    """(nome, parte) degli allegati veri, esclusi i loghi delle firme che l'HTML mostra con src="cid:...\""""
    html = "".join((p.get_payload(decode=True) or b"").decode("latin-1")
                   for p in msg.walk() if p.get_content_type() == "text/html" and not p.get_filename())
    risultato = []
    for parte in msg.walk():
        nome = parte.get_filename()
        if not nome or parte.get_content_maintype() == "multipart":
            continue
        cid = (parte["Content-ID"] or "").strip("<> ")
        if cid and f"cid:{cid}" in html:
            continue
        risultato.append((_decodifica(nome), parte))
    return risultato


def _pulisci(testo: str) -> str:
    # via le righe citate delle risposte precedenti e gli spazi in eccesso
    righe = [r for r in testo.splitlines() if not r.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "\n".join(righe))).strip()


def leggi_mail(cerca: str = "", quante: int = 5, solo_non_lette: bool = False) -> str:
    """Elenca le mail più recenti della posta in arrivo, con mittente, oggetto e anteprima.

    Args:
        cerca: filtro in sintassi Gmail, es. 'from:amazon', 'subject:fattura', 'newer_than:2d'; vuoto = tutte
        quante: quante mail mostrare (max 15)
        solo_non_lette: True per vedere solo quelle non lette
    """
    filtro = " ".join(x for x in [cerca, "is:unread" if solo_non_lette else ""] if x) or "in:inbox"
    imap = _connetti()
    try:
        imap.select("INBOX", readonly=True)
        _, dati = imap.uid("SEARCH", "X-GM-RAW", f'"{filtro}"')
        uids = dati[0].split()[-max(1, min(quante, 15)):][::-1]
        if not uids:
            return "Nessuna mail trovata."
        risultati = []
        for uid in uids:
            _, parti = imap.uid("FETCH", uid, f"(FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.{ANTEPRIMA_BYTE}>)")
            intestazione, testo, letta = b"", b"", False
            # l'ordine dei pezzi nella risposta non è garantito: si riconoscono dal prefisso
            for p in parti:
                prefisso = p[0] if isinstance(p, tuple) else p
                letta = letta or b"\\Seen" in prefisso
                if isinstance(p, tuple):
                    if b"HEADER" in p[0]:
                        intestazione = p[1]
                    else:
                        testo = p[1]
            msg = email.message_from_bytes(intestazione + testo)
            nuova = "" if letta else " [NON LETTA]"
            allegati = " [CON ALLEGATI]" if _allegati(msg) else ""
            anteprima = _pulisci(_corpo(msg))[:200].replace("\n", " ")
            risultati.append(
                f"id {uid.decode()}{nuova}{allegati} | {_decodifica(msg['From'])} | {_decodifica(msg['Subject'])} | "
                f"{msg['Date']}\n{anteprima}"
            )
        return "\n\n".join(risultati)
    finally:
        imap.logout()


def apri_mail(id: int) -> str:
    """Testo completo di una mail (l'id viene da leggi_mail). La segna come letta."""
    imap = _connetti()
    try:
        imap.select("INBOX")
        _, parti = imap.uid("FETCH", str(int(id)), "(BODY[])")
        if not parti or parti[0] is None:
            return f"Mail {id} non trovata."
        msg = email.message_from_bytes(parti[0][1])
        testa = f"Da: {_decodifica(msg['From'])}\nOggetto: {_decodifica(msg['Subject'])}\nData: {msg['Date']}\n"
        allegati = _allegati(msg)
        if allegati:
            testa += "Allegati (contenuto con leggi_allegato): " + "; ".join(
                f"{nome} ({documenti.peso(len(parte.get_payload(decode=True) or b''))})"
                for nome, parte in allegati) + "\n"
        return testa + "\n" + _pulisci(_corpo(msg))[:5000]
    finally:
        imap.logout()


def leggi_allegato(id: int, nome: str = "", domanda: str = "") -> str:
    """Legge il contenuto di un allegato di una mail (PDF, immagini, file di testo). Usalo quando l'utente chiede
    cosa c'è in un allegato, una fattura, un biglietto, un documento arrivato per mail. I nomi degli allegati
    li dà apri_mail; leggi_mail segna con [CON ALLEGATI] le mail che ne hanno.

    Args:
        id: id della mail (da leggi_mail)
        nome: nome del file o una parte (es. 'fattura' per 'Fattura_2026_10.pdf'); vuoto se c'è un solo allegato
        domanda: cosa vuole sapere l'utente dell'allegato (es. 'quanto devo pagare e entro quando'); vuoto = riassunto
    """
    imap = _connetti()
    try:
        imap.select("INBOX", readonly=True)
        _, parti = imap.uid("FETCH", str(int(id)), "(BODY.PEEK[])")
        if not parti or parti[0] is None:
            return f"Mail {id} non trovata."
        msg = email.message_from_bytes(parti[0][1])
    finally:
        imap.logout()
    allegati = _allegati(msg)
    if not allegati:
        return f"La mail {id} non ha allegati."
    elenco = ", ".join(n for n, _ in allegati)
    cercato = nome.strip().lower()
    scelti = ([a for a in allegati if a[0].lower() == cercato] or [a for a in allegati if cercato in a[0].lower()])
    if not scelti:
        return f"Nessun allegato corrisponde a '{nome}'. Allegati della mail {id}: {elenco}"
    if not cercato and len(allegati) > 1:
        return f"La mail {id} ha più allegati, dimmi quale: {elenco}"
    nome_file, parte = scelti[0]
    dati = parte.get_payload(decode=True) or b""
    mime = documenti.tipo(nome_file, parte.get_content_type())
    try:
        contenuto = documenti.leggi(dati, nome_file, mime, domanda, origine=" allegato a una mail",
                                    charset=parte.get_content_charset() or "")
    except documenti.NonLeggibile as e:
        return str(e)
    return f"Contenuto di {nome_file}:\n{contenuto}"


def archivia_mail(id: int) -> str:
    """Archivia una mail: la toglie dalla posta in arrivo senza cancellarla."""
    imap = _connetti()
    try:
        imap.select("INBOX")
        # MOVE su un id inesistente risponde comunque OK: prima si controlla che la mail ci sia
        _, trovata = imap.uid("FETCH", str(int(id)), "(UID)")
        if not trovata or trovata[0] is None:
            return f"La mail {id} non è nella posta in arrivo."
        # in Gmail spostare da INBOX a "Tutti i messaggi" equivale ad archiviare
        stato, risposta = imap.uid("MOVE", str(int(id)), _cartella(imap, "\\All"))
        if stato != "OK":
            return f"Archiviazione della mail {id} NON riuscita: {risposta}"
        return f"Mail {id} archiviata."
    finally:
        imap.logout()


def _carica_bozze() -> dict:
    return json.loads(FILE_BOZZE.read_text(encoding="utf-8")) if FILE_BOZZE.exists() else {}


def _nuova_bozza(msg: EmailMessage) -> str:
    """Salva la bozza in Gmail (modificabile dall'app) e in locale per /invia."""
    mittente, _ = _credenziali()
    msg["From"] = mittente
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="jarvis.local")
    imap = _connetti()
    try:
        imap.append(_cartella(imap, "\\Drafts"), "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        imap.logout()
    with _lock:
        bozze = _carica_bozze()
        numero = str(max((int(n) for n in bozze), default=0) + 1)
        bozze[numero] = {"raw": msg.as_string()}
        FILE_BOZZE.write_text(json.dumps(bozze, ensure_ascii=False), encoding="utf-8")
    return (
        f"Bozza {numero} salvata in Gmail.\nA: {msg['To']}\nOggetto: {msg['Subject']}\n\n{msg.get_content()}\n\n"
        f"NON è stata inviata: per inviarla bisogna scrivere /invia {numero} su Telegram."
    )


def prepara_risposta(id: int, testo: str) -> str:
    """Prepara una bozza di risposta a una mail (non la invia).

    Args:
        id: id della mail a cui rispondere (da leggi_mail)
        testo: il testo della risposta, completo di saluti
    """
    imap = _connetti()
    try:
        imap.select("INBOX", readonly=True)
        _, parti = imap.uid("FETCH", str(int(id)), "(BODY.PEEK[HEADER])")
        if not parti or parti[0] is None:
            return f"Mail {id} non trovata."
        originale = email.message_from_bytes(parti[0][1])
    finally:
        imap.logout()
    oggetto = _decodifica(originale["Subject"])
    msg = EmailMessage()
    msg["To"] = originale["Reply-To"] or originale["From"]
    msg["Subject"] = oggetto if oggetto.lower().startswith("re:") else f"Re: {oggetto}"
    if originale["Message-ID"]:
        msg["In-Reply-To"] = originale["Message-ID"]
        msg["References"] = " ".join(x for x in [originale["References"], originale["Message-ID"]] if x)
    msg.set_content(testo)
    return _nuova_bozza(msg)


def scrivi_mail(destinatario: str, oggetto: str, testo: str) -> str:
    """Prepara una bozza di mail nuova (non la invia)."""
    msg = EmailMessage()
    msg["To"], msg["Subject"] = destinatario, oggetto
    msg.set_content(testo)
    return _nuova_bozza(msg)


def invia_bozza(numero: str) -> str:
    """Chiamata SOLO dal comando /invia di Telegram, mai dal modello."""
    with _lock:
        bozze = _carica_bozze()
        if numero not in bozze:
            return f"Non esiste la bozza {numero}."
        msg = email.message_from_string(bozze[numero]["raw"], _class=EmailMessage, policy=email.policy.default)
        with smtplib.SMTP_SSL(SMTP) as smtp:
            smtp.login(*_credenziali())
            smtp.send_message(msg)
        del bozze[numero]
        FILE_BOZZE.write_text(json.dumps(bozze, ensure_ascii=False), encoding="utf-8")
    # toglie la copia dalle bozze di Gmail, sennò resta lì come se non fosse partita
    imap = _connetti()
    try:
        imap.select(_cartella(imap, "\\Drafts"))
        _, dati = imap.uid("SEARCH", "HEADER", "Message-ID", msg["Message-ID"])
        for uid in dati[0].split():
            imap.uid("STORE", uid, "+FLAGS", "\\Deleted")
        imap.expunge()
    finally:
        imap.logout()
    return f"Inviata a {msg['To']}: {msg['Subject']}"


STRUMENTI = [leggi_mail, apri_mail, leggi_allegato, archivia_mail, prepara_risposta, scrivi_mail]
