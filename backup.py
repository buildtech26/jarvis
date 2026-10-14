"""Backup giornaliero di quello che git non salva, cifrato, in OneDrive; poi il codice su GitHub.

Il 14/10 il codice era solo in un git locale, il .env solo in uno zip del 07/10 (senza le chiavi aggiunte dopo) e lo
stato (allenamento, dispensa, promemoria...) e le note di Jarvis in Obsidian solo su questo disco. Ora, una volta al
giorno dopo le 21:45 (dopo il riepilogo delle 21:30 e lo scarico della dispensa delle 21:31), o al primo avvio se un
giorno è saltato:
- uno zip AES-256 con il .env, i file di stato, verifica.jsonl, la cartella file/ e il vault Obsidian (senza la copia
  di Notion, che si riscarica da sola) va in OneDrive\\Backup\\Jarvis; si tengono gli ultimi TENUTI;
- git push dei commit e dei tag su GitHub (buildtech26/jarvis, privato).
La password è BACKUP_PASSWORD nel .env, scelta dall'utente: va tenuta anche fuori dal PC, perché se il disco si rompe
il .env è proprio dentro lo zip. Lo zip si apre con 7-Zip o WinRAR (Esplora file non apre gli zip AES).
Se qualcosa non va arriva un messaggio su Telegram, una volta al giorno; se va tutto bene solo la riga [backup] nel log.

Ripristino su un PC nuovo: git clone https://github.com/buildtech26/jarvis, uv sync, poi estrarre l'ultimo zip con la
password: la cartella jarvis/ dello zip va sopra il clone, obsidian/ in Desktop\\obsidian.
"""
import datetime
import json
import os
import subprocess
import time
from pathlib import Path

CARTELLA = Path(__file__).parent
DESTINAZIONE = Path(os.getenv("JARVIS_BACKUP") or Path(os.getenv("OneDrive") or Path.home() / "OneDrive")
                    / "Backup" / "Jarvis")
OBSIDIAN = Path(os.getenv("JARVIS_OBSIDIAN") or Path.home() / "Desktop" / "obsidian")
FILE = CARTELLA / "backup.json"  # data dell'ultimo backup riuscito e dell'ultimo avviso
ORA = datetime.time(21, 45)
TENUTI = 14
# nel vault: la copia di Notion si rigenera (circa 1 MB), .trash è il cestino di Obsidian
SALTA_OBSIDIAN = {("Jarvis", "Notion"), (".trash",)}
NO_FINESTRA = 0x08000000  # CREATE_NO_WINDOW: senza, pythonw apre una console per ogni git


def _adesso() -> datetime.datetime:  # le prove la sostituiscono
    return datetime.datetime.now()


def _carica() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _salva(stato: dict):
    FILE.write_text(json.dumps(stato, indent=1), encoding="utf-8")


def _file_jarvis() -> list[Path]:
    """Il .env e lo stato fuori da git: i .json e .jsonl della cartella e i file di testo dell'utente in file/."""
    scelti = [p for p in CARTELLA.iterdir() if p.is_file() and (p.name == ".env" or p.suffix in (".json", ".jsonl"))]
    if (CARTELLA / "file").is_dir():
        scelti += [p for p in (CARTELLA / "file").rglob("*") if p.is_file()]
    return sorted(scelti)


def _file_obsidian() -> list[Path]:
    if not OBSIDIAN.is_dir():
        return []
    return sorted(p for p in OBSIDIAN.rglob("*") if p.is_file()
                  and not any(p.relative_to(OBSIDIAN).parts[:len(s)] == s for s in SALTA_OBSIDIAN))


def crea_zip(password: str) -> tuple[Path, int]:
    """Lo zip cifrato di oggi in DESTINAZIONE: (percorso, numero di file). Scritto prima con un altro nome, così
    OneDrive non carica mai uno zip a metà."""
    import pyzipper

    DESTINAZIONE.mkdir(parents=True, exist_ok=True)
    finale = DESTINAZIONE / f"jarvis_{_adesso():%Y-%m-%d}.zip"
    parziale = finale.with_suffix(".zip.parziale")
    elenco = [(p, Path("jarvis") / p.relative_to(CARTELLA)) for p in _file_jarvis()]
    elenco += [(p, Path("obsidian") / p.relative_to(OBSIDIAN)) for p in _file_obsidian()]
    with pyzipper.AESZipFile(parziale, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(password.encode("utf-8"))
        for origine, nome in elenco:
            try:
                z.write(origine, nome.as_posix())
            except OSError as e:  # un file tenuto aperto da un altro programma: il resto del backup vale comunque
                print(f"[backup] saltato {origine.name}: {e}")
    parziale.replace(finale)
    vecchi = sorted(DESTINAZIONE.glob("jarvis_*.zip"))[:-TENUTI]
    for p in vecchi:
        p.unlink()
    return finale, len(elenco)


def carica_su_github() -> str:
    """git push di rami e tag; '' se è andato bene (o non c'era niente da caricare), se no l'errore."""
    for argomenti in (["push", "-q", "origin", "--all"], ["push", "-q", "origin", "--tags"]):
        r = subprocess.run(["git", *argomenti], cwd=CARTELLA, capture_output=True, text=True, timeout=120,
                           creationflags=NO_FINESTRA)
        if r.returncode:
            return (r.stderr or r.stdout).strip()[:200]
    return ""


def esegui() -> list[str]:
    """Backup e push subito: i problemi, uno per riga (lista vuota se è andato tutto bene)."""
    from dotenv import dotenv_values

    problemi = []
    # riletta dal .env ogni volta: la password aggiunta a mano vale senza riavviare Jarvis
    password = (dotenv_values(CARTELLA / ".env").get("BACKUP_PASSWORD") or os.getenv("BACKUP_PASSWORD") or "").strip()
    if not password:
        problemi.append("manca BACKUP_PASSWORD nel .env, quindi .env, stato e note non sono salvati in OneDrive")
    else:
        inizio = time.monotonic()
        try:
            zip_, quanti = crea_zip(password)
            print(f"[backup] {zip_.name}: {quanti} file, {zip_.stat().st_size / 1e6:.1f} MB, "
                  f"{time.monotonic() - inizio:.1f} s")
        except Exception as e:
            problemi.append(f"zip in OneDrive non riuscito ({type(e).__name__}: {e})")
    try:
        errore = carica_su_github()
    except Exception as e:
        errore = f"{type(e).__name__}: {e}"
    if errore:
        problemi.append(f"codice non caricato su GitHub ({errore})")
    return problemi


def segna_fatto(stato: dict, adesso: datetime.datetime):
    """Un backup prima di ORA (il recupero di un giorno saltato, o /backup) vale per ieri: quello della sera deve
    partire lo stesso, se no il riepilogo delle 21:30 e la dispensa scalata aspetterebbero fino al giorno dopo."""
    giorno = adesso.date() if adesso.time() >= ORA else adesso.date() - datetime.timedelta(days=1)
    stato["ultimo"] = max(stato.get("ultimo", ""), giorno.isoformat())


def fatto_ora():
    """Per /backup su Telegram: segna il backup appena riuscito."""
    stato = _carica()
    segna_fatto(stato, _adesso())
    _salva(stato)


def controlla() -> list[str]:
    """Per il bot, una volta al minuto: il backup di oggi dopo ORA, o subito se ieri è saltato. Messaggi solo per i
    problemi, al più uno al giorno."""
    adesso = _adesso()
    stato = _carica()
    oggi, ieri = adesso.date().isoformat(), (adesso.date() - datetime.timedelta(days=1)).isoformat()
    ultimo = stato.get("ultimo", "")
    if ultimo >= oggi or (adesso.time() < ORA and ultimo >= ieri) or stato.get("tentato") == adesso.strftime("%Y-%m-%d %H"):
        return []
    stato["tentato"] = adesso.strftime("%Y-%m-%d %H")  # dopo un errore si riprova l'ora dopo, non ogni minuto
    problemi = esegui()
    if not problemi:
        segna_fatto(stato, adesso)
        print("[backup] fatto")
    else:
        print(f"[backup] problemi: {'; '.join(problemi)}")
    messaggi = []
    if problemi and stato.get("avvisato") != oggi:
        stato["avvisato"] = oggi
        messaggi.append("💾 Backup di Jarvis non riuscito: " + "; ".join(problemi) + ". Riprovo fra un'ora.")
    _salva(stato)
    return messaggi
