"""Crea jarvis.ico da immagini/logo.jpg e il collegamento "Jarvis" sul desktop (apre la finestra);
aggiorna anche l'icona fissata sulla barra, se c'è. Da rilanciare se si sposta la cartella o cambia il logo."""
import ctypes
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

CARTELLA = Path(__file__).parent
ICONA = CARTELLA / "jarvis.ico"
LOGO = CARTELLA / "immagini" / "logo.jpg"
BARRA = Path(os.environ["APPDATA"]) / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar/Jarvis.lnk"
# il badge nel jpg di Gemini (2816x1536): centro e raggio dell'anello esterno e del disco blu interno
CENTRO, R_ANELLO, R_DISCO = (1408, 770), 578, 440
# il pythonw.exe di uv e non quello del venv, che apre una console: vedi l'inizio di main.py
CASA = next(r.split("=", 1)[1].strip() for r in (CARTELLA / ".venv" / "pyvenv.cfg").read_text().splitlines()
            if r.startswith("home"))
PYTHONW = Path(CASA) / "pythonw.exe"

originale = Image.open(LOGO).convert("RGBA")


def cerchio(r: int, lato: int) -> Image.Image:
    """Ritaglia il cerchio di raggio r attorno a CENTRO, trasparente fuori, bordo senza scalini."""
    cx, cy = CENTRO
    img = originale.crop((cx - r, cy - r, cx + r, cy + r))
    maschera = Image.new("L", (8 * r, 8 * r), 0)
    ImageDraw.Draw(maschera).ellipse((0, 0, 8 * r - 1, 8 * r - 1), fill=255)
    img.putalpha(maschera.resize((2 * r, 2 * r), Image.LANCZOS))
    return img.resize((lato, lato), Image.LANCZOS)


# dai 48 px in su il badge intero; sotto, solo il disco blu, se no la "A" si perde nell'anello d'acciaio
grandi = [cerchio(R_ANELLO, n) for n in (256, 64, 48)]
piccole = [cerchio(R_DISCO, n) for n in (32, 24, 16)]
grandi[0].save(ICONA, append_images=grandi[1:] + piccole)

desktop = Path(subprocess.run(
    ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
    capture_output=True, text=True,
).stdout.strip())
script = ""
if BARRA.exists():
    script += f"""
$b = (New-Object -ComObject WScript.Shell).CreateShortcut('{BARRA}')
$b.TargetPath = '{PYTHONW}'
$b.IconLocation = '{ICONA}'
$b.Save()
"""
script += f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{desktop / "Jarvis.lnk"}')
$s.TargetPath = '{PYTHONW}'
$s.Arguments = '"{CARTELLA / "main.py"}" mostra --log'
$s.WorkingDirectory = '{CARTELLA}'
$s.IconLocation = '{ICONA}'
$s.Description = 'Apri la chat di Jarvis (anche con Alt+J)'
$s.Save()
"""
subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
# Windows tiene le icone in cache per percorso, e jarvis.ico ha sempre lo stesso: senza questo resta il logo vecchio
# anche dopo il riavvio (SHCNE_ASSOCCHANGED fa rileggere le icone a Esplora risorse)
ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
print("Creati", ICONA, "e", desktop / "Jarvis.lnk")
