"""Disegna immagini/sfera.png, la sfera di sfondo della finestra (gui.py la scurisce a riposo e la fa pulsare).

L'originale è andata persa il 03/10 (sovrascritta da uno screenshot): questa è ridisegnata da zero, globo a reticolo
in proiezione ortografica con anelli HUD intorno, nel turchese della finestra su sfondo trasparente.

    .venv\\Scripts\\python crea_sfera.py
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

USCITA = Path(__file__).parent / "immagini" / "sfera.png"
LATO = 1024
S = LATO * 2  # si disegna al doppio e si riduce: linee senza scalini
C = S / 2
R = S * 0.33  # raggio del globo
INCLINAZIONE = math.radians(22)  # il globo è visto un po' dall'alto: i paralleli diventano ellissi
TURCHESE = (63, 224, 208)  # ACCENTO in gui.py
CHIARO = (190, 255, 248)


def proietta(lat: float, lon: float) -> tuple[float, float, float]:
    """Punto del globo (gradi) → coordinate sull'immagine e profondità (>0 = metà verso chi guarda)."""
    f, l = math.radians(lat), math.radians(lon)
    x, y, z = math.cos(f) * math.sin(l), math.sin(f), math.cos(f) * math.cos(l)
    y2 = y * math.cos(INCLINAZIONE) - z * math.sin(INCLINAZIONE)
    z2 = y * math.sin(INCLINAZIONE) + z * math.cos(INCLINAZIONE)
    return C + R * x, C - R * y2, z2


def linea_sul_globo(d: ImageDraw.ImageDraw, punti: list[tuple[float, float]], spessore: int):
    """Spezzata sul globo: davanti piena, dietro tenue, con passaggio graduale sul bordo."""
    proiettati = [proietta(lat, lon) for lat, lon in punti]
    for (x1, y1, z1), (x2, y2, z2) in zip(proiettati, proiettati[1:]):
        z = (z1 + z2) / 2
        alfa = int(55 + 150 * max(0.0, min(1.0, (z + 0.15) / 0.5)))
        d.line((x1, y1, x2, y2), fill=(*TURCHESE, alfa), width=spessore)


def arco(d: ImageDraw.ImageDraw, raggio: float, da: float, a: float, alfa: int, spessore: int):
    d.arc((C - raggio, C - raggio, C + raggio, C + raggio), da, a, fill=(*TURCHESE, alfa), width=spessore)


def radiale(colore: tuple, alfa_centro: int, raggio: float, esponente: float) -> Image.Image:
    """Alone circolare: alfa_centro al centro, 0 a `raggio`."""
    maschera = Image.radial_gradient("L").resize((S, S))  # 0 al centro, 255 al bordo del quadrato
    scala = (S / 2) / raggio
    maschera = maschera.point(lambda v: int(alfa_centro * max(0.0, 1 - min(1.0, v / 255 * scala)) ** esponente))
    img = Image.new("RGBA", (S, S), (*colore, 0))
    img.putalpha(maschera)
    return img


def main():
    linee = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(linee)

    # reticolo: meridiani ogni 15°, paralleli ogni 15°
    for lon in range(0, 180, 15):
        meridiano = [(lat, lon) for lat in range(-90, 91, 2)] + [(lat, lon + 180) for lat in range(90, -91, -2)]
        linea_sul_globo(d, meridiano, 3)
    for lat in range(-75, 76, 15):
        linea_sul_globo(d, [(lat, lon) for lon in range(0, 361, 2)], 4 if lat == 0 else 3)

    # punti luminosi sparsi sulla metà davanti
    caso = random.Random(7)
    for _ in range(140):
        x, y, z = proietta(math.degrees(math.asin(caso.uniform(-1, 1))), caso.uniform(0, 360))
        if z > 0.1:
            r = caso.choice((2, 3, 3, 5))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(*CHIARO, int(120 + 120 * z)))

    # contorno del globo e anelli HUD
    arco(d, R, 0, 360, 230, 6)
    arco(d, R * 1.10, 0, 360, 90, 3)
    for inizio in range(0, 360, 10):  # anello tratteggiato
        arco(d, R * 1.18, inizio, inizio + 6, 170, 7)
    for gradi in range(0, 360, 5):  # tacche, più lunghe ogni 30°
        lunga = gradi % 30 == 0
        a = math.radians(gradi)
        r1, r2 = R * 1.25, R * (1.33 if lunga else 1.28)
        d.line((C + r1 * math.cos(a), C + r1 * math.sin(a), C + r2 * math.cos(a), C + r2 * math.sin(a)),
               fill=(*TURCHESE, 210 if lunga else 120), width=5 if lunga else 3)
    for da, a in ((-70, -20), (110, 160), (200, 225), (20, 35)):  # archi spessi, asimmetrici
        arco(d, R * 1.40, da, a, 210, 12)
    arco(d, R * 1.46, 0, 360, 60, 2)

    # alone: le linee sfocate sotto quelle nitide, più un bagliore che riempie il globo e un nucleo chiaro
    bagliore = linee.filter(ImageFilter.GaussianBlur(S * 0.012))
    bagliore = Image.merge("RGBA", (*bagliore.split()[:3], bagliore.getchannel("A").point(lambda v: min(255, v * 2))))
    sfera = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sfera = Image.alpha_composite(sfera, radiale(TURCHESE, 70, R * 1.55, 2.2))
    sfera = Image.alpha_composite(sfera, radiale(CHIARO, 150, R * 0.55, 1.8))
    sfera = Image.alpha_composite(sfera, bagliore)
    sfera = Image.alpha_composite(sfera, linee)

    # bordo dell'immagine sempre trasparente: niente spigoli quando la finestra è stretta
    sfuma = radiale((0, 0, 0), 255, S * 0.5, 0.25).getchannel("A")
    sfera.putalpha(ImageChops.multiply(sfera.getchannel("A"), sfuma))
    sfera.resize((LATO, LATO), Image.LANCZOS).save(USCITA)
    print("Creata", USCITA)


if __name__ == "__main__":
    main()
