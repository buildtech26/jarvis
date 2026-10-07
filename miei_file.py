"""File di testo creati da Jarvis (liste, appunti, bozze): tutti e solo nella cartella jarvis\\file."""
import ctypes
import difflib
import os
import re
from ctypes import wintypes
from pathlib import Path

CARTELLA = Path(__file__).with_name("file")
ESTENSIONI = {"txt", "md", "csv", "json", "html", "xml", "py", "js", "css", "ini", "log"}
MAX_LETTURA = 3500  # brain.py taglia i risultati degli strumenti a 4000

# parole che non entrano nel nome: "la lista della spesa" -> lista-spesa
_VUOTE = {"il", "lo", "la", "i", "gli", "le", "l", "un", "una", "uno", "di", "del", "della", "dello", "dei", "degli",
          "delle", "da", "a", "al", "alla", "in", "per", "con", "su", "e", "file", "documento", "nota"}


def _base(nome: str) -> tuple[str, str]:
    """'Lista della spesa.md' -> ('lista-spesa', 'md'); estensione vuota se non indicata."""
    nome = nome.strip().strip("\"'")
    est = ""
    if m := re.search(r"\.([a-z0-9]{1,5})$", nome, re.I):
        est, nome = m.group(1).lower(), nome[: m.start()]
    parole = [p for p in re.split(r"[^\wàèéìòù]+", nome.lower()) if p and p not in _VUOTE]
    return "-".join(parole)[:60], est


def _esistenti() -> list[Path]:
    if not CARTELLA.exists():
        return []
    return sorted((f for f in CARTELLA.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)


def _trova(nome: str) -> Path | None:
    """Il file creato che somiglia di più a `nome` ('lista della spesa' trova lista-spesa.txt)."""
    cercato, est = _base(nome)
    file = [f for f in _esistenti() if not est or f.suffix[1:].lower() == est]
    if not cercato or not file:
        return None
    for f in file:  # nome identico, o identico a meno della copia numerata
        if re.sub(r" \(\d+\)$", "", f.stem) == cercato:
            return f
    parole = set(cercato.split("-"))
    contenuti = [f for f in file if parole <= set(f.stem.split("-"))]
    if contenuti:
        return contenuti[0]  # il più recente
    # solo errori di battitura: con 0.6 "lista-palestra" finiva in lista-spesa
    vicini = difflib.get_close_matches(cercato, [f.stem for f in file], n=1, cutoff=0.85)
    return next(f for f in file if f.stem == vicini[0]) if vicini else None


def _elenco_breve() -> str:
    nomi = [f.name for f in _esistenti()[:15]]
    return ", ".join(nomi) if nomi else "nessuno"


def crea_file(nome: str, contenuto: str, estensione: str = "txt", apri: bool = False) -> str:
    """Crea un NUOVO file di testo (lista, appunti, bozza, testo che scrivi tu) nella cartella dei file di Jarvis.
    Se un file con quel nome esiste già ne crea una copia numerata: per aggiungere righe usa aggiungi_a_file.

    Args:
        nome: nome detto dall'utente, oppure ricavalo tu dal contenuto in poche parole (es. 'lista della spesa')
        contenuto: il testo COMPLETO del file, già scritto per intero (una voce per riga nelle liste)
        estensione: 'txt' salvo che l'utente chieda un altro formato di testo (md, csv, html, py...)
        apri: True SOLO se l'utente chiede di aprirlo o vederlo sullo schermo
    """
    base, est = _base(nome)
    est = (est or estensione.lower().lstrip(".") or "txt")
    if est not in ESTENSIONI:
        return f"Posso creare solo file di testo ({', '.join(sorted(ESTENSIONI))}), non .{est}."
    if not base:
        return "Serve un nome per il file."
    CARTELLA.mkdir(exist_ok=True)
    percorso, n = CARTELLA / f"{base}.{est}", 2
    while percorso.exists():
        percorso, n = CARTELLA / f"{base} ({n}).{est}", n + 1
    percorso.write_text(contenuto.rstrip() + "\n", encoding="utf-8")
    if apri:
        os.startfile(percorso)
    return f"Creato {percorso.name}{' e aperto sullo schermo' if apri else ''}."


def aggiungi_a_file(nome: str, testo: str) -> str:
    """Aggiunge testo IN FONDO a un file creato da Jarvis ("aggiungi il latte alla lista della spesa").
    Se il file non esiste lo crea.

    Args:
        nome: il file come lo chiama l'utente (es. 'lista della spesa')
        testo: solo il testo da aggiungere; per le liste una voce per riga
    """
    percorso = _trova(nome)
    if percorso is None:
        esito = crea_file(nome, testo)
        return esito if not esito.startswith("Creato") else esito.replace("Creato", "Il file non c'era: creato", 1)
    vecchio = percorso.read_text(encoding="utf-8")
    separatore = "" if not vecchio or vecchio.endswith("\n") else "\n"
    with percorso.open("a", encoding="utf-8") as f:
        f.write(separatore + testo.rstrip() + "\n")
    return f"Aggiunto a {percorso.name}."


def leggi_file(nome: str, apri: bool = False) -> str:
    """Legge un file creato da Jarvis ("leggimi la lista della spesa", "cosa c'è nella lista?").

    Args:
        nome: il file come lo chiama l'utente
        apri: True se l'utente vuole APRIRLO o vederlo sullo schermo del PC invece di farselo leggere
    """
    percorso = _trova(nome)
    if percorso is None:
        return f"Nessun file simile a '{nome}'. File presenti: {_elenco_breve()}."
    if apri:
        os.startfile(percorso)
        return f"Aperto {percorso.name} sullo schermo."
    testo = percorso.read_text(encoding="utf-8", errors="replace").strip()
    if len(testo) > MAX_LETTURA:
        testo = testo[:MAX_LETTURA] + f"\n[... il file continua: {len(testo)} caratteri in tutto]"
    return f"{percorso.name}:\n{testo or '(vuoto)'}"


def elenca_file() -> str:
    """Elenca i file creati da Jarvis ("che file hai creato?"), dal più recente."""
    file = _esistenti()
    if not file:
        return "Non ho ancora creato nessun file."
    return "\n".join(f"{f.name} ({f.stat().st_size} byte)" for f in file[:30])


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def _nel_cestino(percorso: Path) -> bool:
    # FO_DELETE + FOF_ALLOWUNDO (Cestino) | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op = _SHFILEOPSTRUCTW(wFunc=3, pFrom=str(percorso) + "\0", fFlags=0x40 | 0x10 | 0x04 | 0x400)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not percorso.exists()


def cancella_file(nome: str) -> str:
    """Sposta nel Cestino di Windows un file creato da Jarvis ("cancella la lista della spesa").

    Args:
        nome: il file come lo chiama l'utente
    """
    percorso = _trova(nome)
    if percorso is None:
        return f"Nessun file simile a '{nome}'. File presenti: {_elenco_breve()}."
    if not _nel_cestino(percorso):
        return f"Non sono riuscito a spostare {percorso.name} nel Cestino."
    return f"{percorso.name} spostato nel Cestino."


STRUMENTI = [crea_file, aggiungi_a_file, leggi_file, elenca_file, cancella_file]
