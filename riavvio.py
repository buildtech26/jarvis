"""Lancia riavvia.ps1 da dentro Jarvis (/riavvia su Telegram o nella finestra).

Lo script chiude anche la copia che lo lancia, quindi deve sopravviverle: parte staccato e senza console.
"""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("riavvia.ps1")


def lancia(da_telegram: bool):
    """da_telegram: finestra nascosta e avviso su Telegram se il riavvio fallisce; altrimenti riparte con la
    finestra aperta, come dall'icona."""
    comando = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)]
    if da_telegram:
        comando.append("-DaTelegram")
    flag = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # partito dall'attività pianificata, Jarvis è dentro un job di Windows: meglio uscirne
        subprocess.Popen(comando, creationflags=flag | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:  # il job non lo permette
        subprocess.Popen(comando, creationflags=flag, close_fds=True)
    print(f"[riavvio] lanciato riavvia.ps1{' -DaTelegram' if da_telegram else ''}")
