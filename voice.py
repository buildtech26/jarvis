"""Voce: parola d'attivazione "Hey Jarvis", trascrizione con Gemini, risposta parlata.

La trascrizione era con Whisper (faster-whisper "base"): tolto il 2026-10-03 perché Gemini sbagliava meno
("in dipendenza" invece di "Indipendenza") e Whisper occupava ~130 MB di RAM. Costa una richiesta Gemini a frase.
"""
import asyncio
import io
import os
import subprocess
import tempfile
import wave

import numpy as np
import sounddevice as sd

import quota
import riserva

FREQ = 16000
BLOCCO = 1280  # 80 ms, il formato che openwakeword si aspetta
VOCE = os.getenv("JARVIS_VOCE", "it-IT-DiegoNeural")
NIENTE = "[nessun parlato]"
ISTRUZIONI = (
    "Trascrivi esattamente il parlato, in italiano. Rispondi solo con la trascrizione, senza commenti. "
    f"Se non c'è nessuna voce comprensibile rispondi esattamente {NIENTE}"
)
MIME = {".oga": "audio/ogg", ".ogg": "audio/ogg", ".mp3": "audio/mp3", ".wav": "audio/wav", ".m4a": "audio/mp4"}

_client = None


def _gemini():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    return _client


def _wav(audio: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FREQ)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


def trascrivi(audio) -> str:
    """Accetta un array float32 a 16 kHz (microfono) oppure il percorso di un file audio (vocali Telegram)."""
    from google.genai import types

    if isinstance(audio, np.ndarray):
        dati, mime = _wav(audio), "audio/wav"
    else:
        with open(audio, "rb") as f:
            dati = f.read()
        mime = MIME.get(os.path.splitext(audio)[1].lower(), "audio/ogg")
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        http_options=types.HttpOptions(timeout=30000),
    )
    scartati: set[str] = set()
    for _ in range(6):
        modello = quota.prossimo(scartati)
        if modello is None:
            break
        try:
            r = _gemini().models.generate_content(
                model=modello, contents=[types.Part.from_bytes(data=dati, mime_type=mime), ISTRUZIONI], config=config
            )
            quota.registra(modello, "voce")
            testo = (r.text or "").strip()
            return "" if NIENTE in testo else testo
        except Exception as e:  # limite al minuto: pausa e si passa a un altro modello; altri errori: scartato
            tipo = quota.segna_errore(modello, str(e))
            quota.registra(modello, "voce", "429" if tipo else "altro")
            print(f"[trascrizione] {modello} non disponibile: {str(e)[:80]}")
            if tipo != "minuto":
                scartati.add(modello)
    if riserva.configurata():  # nessun Gemini: voxtral di Mistral
        try:
            testo = riserva.trascrivi(dati, "audio" + {"audio/wav": ".wav", "audio/mp3": ".mp3", "audio/mp4": ".m4a"}
                                      .get(mime, ".ogg"))
            print("[trascrizione] fatta con la riserva Mistral")
            return testo
        except Exception as e:
            print(f"[trascrizione] anche la riserva Mistral non risponde: {str(e)[:80]}")
    return ""


def registra_frase(stream, max_secondi=12, silenzio_fine=1.0) -> np.ndarray:
    """Registra finché l'utente parla; si ferma dopo un secondo di silenzio."""
    blocchi, silenzio, parlato = [], 0.0, False
    durata_blocco = BLOCCO / FREQ
    # soglia calibrata sul rumore di fondo dei primi 300 ms
    fondo = np.mean([np.abs(stream.read(BLOCCO)[0][:, 0]).mean() for _ in range(4)])
    soglia = max(fondo * 3, 300)
    for _ in range(int(max_secondi / durata_blocco)):
        dati = stream.read(BLOCCO)[0][:, 0]
        blocchi.append(dati.copy())
        if np.abs(dati).mean() > soglia:
            parlato, silenzio = True, 0.0
        else:
            silenzio += durata_blocco
            limite = silenzio_fine if parlato else 4.0  # 4 s per iniziare a parlare
            if silenzio >= limite:
                break
    return np.concatenate(blocchi).astype(np.float32) / 32768.0


def parla(testo: str):
    """Sintesi vocale con le voci neurali di Edge; se manca internet usa la voce di Windows."""
    try:
        import edge_tts

        percorso = os.path.join(tempfile.gettempdir(), "jarvis_voce.mp3")
        asyncio.run(edge_tts.Communicate(testo, VOCE, rate="+8%").save(percorso))
        # Jarvis gira senza console: senza CREATE_NO_WINDOW ogni frase aprirebbe una scheda del Terminale
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", percorso], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        testo_ps = testo.replace("'", "''")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{testo_ps}')"],
            check=False, creationflags=subprocess.CREATE_NO_WINDOW,
        )


def bip(freq=880, durata=0.12):
    t = np.linspace(0, durata, int(FREQ * durata), False)
    sd.play((np.sin(2 * np.pi * freq * t) * 0.3).astype(np.float32), FREQ)
    sd.wait()


def ciclo_vocale(jarvis):
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    download_models(["hey_jarvis"])
    rilevatore = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    soglia = float(os.getenv("WAKEWORD_SOGLIA", "0.5"))

    print('In ascolto. Di\' "Hey Jarvis" (Ctrl+C per uscire).')
    with sd.InputStream(samplerate=FREQ, channels=1, dtype="int16", blocksize=BLOCCO) as stream:
        while True:
            blocco = stream.read(BLOCCO)[0][:, 0]
            punteggio = rilevatore.predict(blocco)["hey_jarvis"]
            if punteggio < soglia:
                continue

            bip()
            frase = trascrivi(registra_frase(stream))
            rilevatore.reset()
            # il 06/10 si è attivato su una frase non rivolta a Jarvis: i punteggi servono a tarare WAKEWORD_SOGLIA
            print(f"[voce] attivato con {punteggio:.2f} (soglia {soglia}): {(frase or '')[:60]!r}")
            if not frase:
                bip(440)
                continue
            print(f"Tu: {frase}")
            if any(p in frase.lower() for p in ("dimentica tutto", "nuova conversazione")):
                jarvis.dimentica()
                parla("Fatto, ho dimenticato la conversazione.")
                continue

            risposta = jarvis.chiedi(frase, canale="voce")
            print(f"Jarvis: {risposta}")
            stream.stop()  # non ascoltarsi mentre parla
            parla(risposta)
            stream.start()
            rilevatore.reset()
