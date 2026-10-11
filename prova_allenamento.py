"""Prova del personal trainer (allenamento.py) con un calendario finto e una cartella temporanea: niente rete, niente
eventi veri, la scheda e lo stato veri non vengono toccati.

    .venv\\Scripts\\python prova_allenamento.py

Settimana del 12/10: lunedì lezione fino alle 18:45, martedì lezione dalle 8, mercoledì pieno, sabato un compleanno
(tutto il giorno, non conta). Darebee è finto: catalogo, pagine lette e giorni di corsa scritti qui.
"""
import datetime
import random
import re
import sys
import tempfile
from pathlib import Path

import allenamento
import brain
import calendario
import darebee

CATALOGO = [{"t": t, "u": f"workouts/{u}.html", "ty": ty, "f": f, "d": d, "e": e} for t, u, ty, f, d, e in [
    ("Gambe Uno", "gambe-1", "strength", "lower-body", "easy", "none"),
    ("Gambe Due", "gambe-2", "strength", "lower-body", "normal", "none"),
    ("Intero Uno", "intero-1", "strength", "full-body", "normal", "none"),
    ("Intero Due", "intero-2", "strength", "full-body", "easy", "none"),
    ("Alto Uno", "alto-1", "strength", "upper-body", "normal", "none"),
    ("Addome Uno", "addome-1", "strength", "abs", "easy", "none"),
    ("Manubri", "manubri", "strength", "lower-body", "easy", "dumbbells"),
    ("Troppo Duro", "duro", "strength", "full-body", "hard", "none"),
    ("Solo Cardio", "cardio", "cardio", "full-body", "easy", "none")]]


def darebee_zona(s: dict) -> str:
    v = next(v for v in CATALOGO if v["u"] == s["darebee"])
    return v["f"] if v["e"] == "none" and v["d"] in ("easy", "normal") and v["ty"] == "strength" else "sbagliato"


def _letto_finto(percorso: str) -> dict:
    if "start=" in percorso:
        return {"livelli": [], "riposo": "", "esercizi": [f"corsa del giorno {percorso.split('start=')[1]}"],
                "come": "", "consigli": ""}
    return {"livelli": ["3 serie", "5 serie", "7 serie"], "riposo": "fino a 2 minuti",
            "esercizi": [f"10 squat di {percorso}"], "come": "Di fila, poi riposo.", "consigli": ""}


def _t(testo: str) -> datetime.datetime:
    return datetime.datetime.strptime(testo, "%Y-%m-%d %H:%M").astimezone()


class CalendarioFinto:
    def __init__(self):
        self.eventi: dict[str, dict] = {}
        self.creati = 0
        for titolo, a, b in [("ANALISI T-1", "2026-10-12 17:00", "2026-10-12 18:45"),
                             ("FISICA", "2026-10-13 08:00", "2026-10-13 10:00"),
                             ("Gita", "2026-10-14 06:00", "2026-10-14 23:00")]:
            self._aggiungi(titolo, _t(a).isoformat(), _t(b).isoformat(), False)
        self._aggiungi("Compleanno di Luca", _t("2026-10-17 00:00").isoformat(), _t("2026-10-18 00:00").isoformat(), True)

    def _aggiungi(self, titolo, inizio, fine, tutto, descrizione="") -> dict:
        ev = {"id": f"ev{len(self.eventi) + 1}@finto", "titolo": titolo, "inizio": inizio, "fine": fine,
              "tutto_il_giorno": tutto, "luogo": "", "calendario": "finto", "descrizione": descrizione}
        self.eventi[ev["id"]] = ev
        return ev

    def chiama(self, azione: str, **d) -> dict:
        if azione == "eventi":
            da, a = datetime.datetime.fromisoformat(d["da"]), datetime.datetime.fromisoformat(d["a"])
            trovati = [e for e in self.eventi.values()
                       if datetime.datetime.fromisoformat(e["inizio"]) < a and datetime.datetime.fromisoformat(e["fine"]) > da
                       and d["cerca"].lower() in e["titolo"].lower()]
            return {"eventi": sorted(trovati, key=lambda e: e["inizio"])}
        if azione == "crea":
            self.creati += 1
            return {"creato": self._aggiungi(d["titolo"], d["inizio"], d["fine"], False, d.get("descrizione", ""))}
        if azione == "cancella":
            if d["id"] not in self.eventi:
                raise RuntimeError("evento non trovato")
            return {"cancellato": self.eventi.pop(d["id"])["titolo"]}
        raise RuntimeError(f"azione {azione}")

    def allenamenti(self) -> list[dict]:
        return sorted((e for e in self.eventi.values() if "Allenamento" in e["titolo"]), key=lambda e: e["inizio"])


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    cartella = Path(tempfile.mkdtemp(prefix="prova_allenamento_"))
    allenamento.CARTELLA = cartella / "Allenamento"
    allenamento.FILE = cartella / "allenamento.json"
    adesso = [_t("2026-10-11 17:00")]
    allenamento._adesso = lambda: adesso[0]
    cal = CalendarioFinto()
    calendario._chiama = cal.chiama
    calendario.configurato = lambda: True
    random.seed(7)
    darebee.FILE = cartella / "darebee.json"
    darebee.catalogo = lambda: CATALOGO
    darebee.leggi = _letto_finto
    darebee.giorni_corsa = lambda programma: [1, 3, 5, 7]

    def controlla():
        allenamento._ultimo_sync = 0.0
        return allenamento.controlla()

    def orari() -> list[str]:
        return [f"{allenamento._GIORNI[allenamento._dt(e['inizio']).weekday()]} {allenamento._dt(e['inizio']):%H:%M} {e['titolo'].split(': ')[1]}" for e in cal.allenamenti()]

    # 1. la scheda: copiata dal modello, impostazioni e fasi lette dalle sezioni
    imp = allenamento._impostazioni()
    atteso((cartella / "Allenamento" / "Scheda.md").exists(), "scheda copiata in Obsidian")
    atteso(imp["inizio"] == "2026-10-12" and allenamento._numero(imp, "margine") == 30, "impostazioni", imp)
    atteso(allenamento._finestra(imp, "sera") == (datetime.time(19), datetime.time(22)), "fascia sera", imp["sera"])
    atteso([allenamento._fase(n, imp) for n in (0, 14, 15, 34, 35)] == [1, 1, 2, 2, 3], "soglie delle fasi")
    a1, a3 = allenamento._contenuto("Corpo libero A", 1), allenamento._contenuto("Corpo libero A", 3)
    atteso("Squat a corpo libero" in a1 and "Riscaldamento" in a1 and "zaino" not in a1, "Corpo libero A fase 1", a1)
    atteso("Squat con lo zaino carico" in a3, "Corpo libero A fase 3", a3)
    atteso("Rematore" in allenamento._contenuto("Corpo libero B", 1), "corpo libero B fase 1")
    atteso("Rematore" not in allenamento._contenuto("Corpo libero A", 2), "sezioni separate")

    # 2. domenica alle 17 la settimana dopo non è ancora pianificata, quella in corso è prima dell'inizio
    atteso(controlla() == [] and cal.creati == 0, "niente prima dell'ora di pianificare", cal.creati)
    r = allenamento.seduta_allenamento("domani")
    atteso("non è ancora in calendario" in r, "settimana non pianificata: non è riposo", r)

    # 3. alle 18:05 la pianifica: mercoledì pieno salta, il giovedì lo sostituisce
    adesso[0] = _t("2026-10-11 18:05")
    messaggi = controlla()
    atteso(len(messaggi) == 1 and "in calendario" in messaggi[0][0], "messaggio della settimana", messaggi)
    atteso(orari() == ["lun 19:30 Corpo libero A", "mar 19:00 Corsa", "gio 19:00 Corpo libero B", "ven 07:00 Corsa",
                       "sab 19:00 Corpo libero A", "dom 10:00 Tennis"], "giorni e orari, tennis in più", orari())
    atteso("Riposo: mer." in messaggi[0][0] and "dom 18/10 alle 10:00 Tennis" in messaggi[0][0], "giorni di riposo",
           messaggi[0][0])
    tennis = cal.allenamenti()[5]
    atteso(tennis["titolo"] == "🎾 Allenamento: Tennis" and tennis["fine"].startswith("2026-10-18T11:00")
           and re.match(r"1\. Riscaldamento: .*palleggi.*\n2\. La partita: 60 min\.\n3\. Alla fine: ", tennis["descrizione"]),
           "evento del tennis", tennis)
    desc = cal.allenamenti()[0]["descrizione"]
    atteso("Riscaldamento" in desc and "livello I (fase 1)" in desc and "Esercizi: 3 serie" in desc
           and "darebee.com/workouts/" in desc,
           "allenamento Darebee nella descrizione", desc)
    atteso("corsa del giorno 1" in cal.allenamenti()[1]["descrizione"] and "corsa del giorno 3" in cal.allenamenti()[3]["descrizione"],
           "le due corse sono i giorni 1 e 3 del programma", [e["descrizione"] for e in cal.allenamenti()])
    zone = {s["tipo"]: darebee_zona(s) for s in allenamento._carica()["sedute"] if s.get("darebee")}
    atteso(zone.get("Corpo libero A") in ("lower-body", "full-body") and zone.get("Corpo libero B") in ("upper-body", "abs", "full-body"),
           "zone di A e B, senza attrezzi, né hard, né cardio", allenamento._carica()["sedute"])
    usati = [s["darebee"] for s in allenamento._carica()["sedute"] if s.get("darebee")]
    atteso(len(usati) == 3 and len(set(usati)) == 3, "tre allenamenti diversi", usati)
    atteso("(Gambe" in messaggi[0][0] or "(Intero" in messaggi[0][0], "titolo Darebee nel messaggio", messaggi[0][0])
    atteso(allenamento._carica()["lettera"] == "B", "la settimana dopo comincia con la B")

    # 4. un secondo giro non duplica niente
    adesso[0] = _t("2026-10-11 18:10")
    atteso(controlla() == [] and cal.creati == 6, "nessun doppione", cal.creati)

    # 5. rifai: toglie le sei e le rimette uguali, con le lettere di prima
    r = allenamento.pianifica_allenamenti("prossima", rifai=True)
    atteso(len(cal.allenamenti()) == 6 and orari()[0] == "lun 19:30 Corpo libero A" and orari()[4].endswith("Corpo libero A")
           and orari()[5] == "dom 10:00 Tennis", "rifai", f"{r} {orari()}")

    # 6. lunedì alle 21:01 (fine 20:15 + 30 min) chiede com'è andata, con i pulsanti
    adesso[0] = _t("2026-10-12 21:01")
    messaggi = controlla()
    atteso(len(messaggi) == 1 and "Corpo libero A" in messaggi[0][0] and len(messaggi[0][1][0]) == 2, "domanda", messaggi)
    dati_fatto = messaggi[0][1][0][0][1]
    testo, pulsanti = allenamento.pulsante(dati_fatto)
    atteso(testo.startswith("✅") and not pulsanti, "pulsante fatto", testo)
    storico = (cartella / "Allenamento" / "Storico.md").read_text(encoding="utf-8")
    atteso(re.search(r"Corpo libero A \((Gambe|Intero) \w+\) · fase 1 · ✅ fatto", storico), "storico in Obsidian", storico)
    atteso(controlla() == [], "non richiede due volte")

    # 7. corsa spostata a mano alle 20, Corpo libero B di giovedì cancellato a mano
    corsa, libero_b = cal.allenamenti()[1], cal.allenamenti()[2]
    corsa["inizio"], corsa["fine"] = _t("2026-10-13 20:00").isoformat(), _t("2026-10-13 20:45").isoformat()
    del cal.eventi[libero_b["id"]]
    adesso[0] = _t("2026-10-13 20:20")
    atteso(controlla() == [], "corsa spostata: non ancora finita")
    stati = {s["tipo"]: s["stato"] for s in allenamento._carica()["sedute"] if s["stato"] != "cancellato"}
    atteso("Corpo libero B" not in stati, "cancellata a mano", stati)

    # 8. saltata: propone il recupero giovedì mattina (mercoledì è pieno, giovedì ora è libero)
    adesso[0] = _t("2026-10-13 21:16")
    messaggi = controlla()
    atteso(len(messaggi) == 1 and "Corsa" in messaggi[0][0], "domanda sulla corsa", messaggi)
    testo, pulsanti = allenamento.pulsante(messaggi[0][1][0][1][1])
    atteso("gio 15/10 alle 07:00" in testo and pulsanti[0][0][1].endswith(":rec:202610150700"), "recupero", testo)
    testo, _ = allenamento.pulsante(pulsanti[0][0][1])
    atteso(testo.startswith("🔁") and "gio 07:00 Corsa" in orari(), "recupero in calendario", f"{testo} {orari()}")
    atteso(allenamento.pulsante(pulsanti[0][0][1])[0].startswith("L'ho già"), "recupero non doppio")

    # 9. a voce: venerdì "ho fatto la corsa"; giorno senza sedute
    adesso[0] = _t("2026-10-16 09:00")
    r = allenamento.segna_allenamento("fatto", "oggi")
    atteso("Corsa fatta" in r and "in tutto: 2" in r, "segna a voce", r)
    r = allenamento.seduta_allenamento("2026-10-14")
    atteso("riposo" in r and "Il prossimo: Corsa gio 15/10" in r, "giorno di riposo con il prossimo (il recupero)", r)
    r = allenamento.seduta_allenamento("domani")
    atteso("Corpo libero A" in r and "Darebee ·" in r and "10 squat di workouts/" in r, "allenamento di domani", r)
    r = allenamento.allenamenti_settimana()
    atteso("fatto" in r and "saltato" in r and "Sedute fatte in tutto: 2" in r, "settimana", r)

    # 10. comando diretto senza Gemini
    for frase, giorno in [("cosa faccio domani di allenamento", "domani"), ("allenamento di oggi", "oggi"),
                          ("che allenamento ho domani", "domani")]:
        trovato = [m for schema, nome, _ in brain.COMANDI if nome == "seduta_allenamento"
                   and (m := re.fullmatch(schema, frase))]
        atteso(trovato and next(g for g in trovato[0].groups() if g) == giorno, f"comando diretto {frase!r}")

    # 11. Darebee: la corsa saltata martedì si ripete (il recupero di giovedì è la corsa 1, venerdì la 2), il
    # livello segue la fase, sito giù = link e scheda, allenamenti senza livelli scartati, lettura della pagina
    r = allenamento.seduta_allenamento("2026-10-15")
    atteso("corsa del giorno 1" in r and "corsa 1 di 4" in r, "il recupero è la corsa 1", r)
    r = allenamento.seduta_allenamento("2026-10-16")
    atteso("corsa del giorno 3" in r, "venerdì la corsa 2 (giorno 3)", r)
    sab = next(s for s in allenamento._carica()["sedute"] if s["stato"] == "in programma" and s["tipo"] == "Corpo libero A")
    r = darebee.testo(sab["darebee"], sab["darebee_titolo"], 2)
    atteso("livello II (fase 2)" in r and "Esercizi: 5 serie" in r, "fase 2 = livello II", r)

    def giu(percorso):
        raise RuntimeError("sito giù")
    darebee.leggi = giu
    r = allenamento.seduta_allenamento("domani")
    atteso("non sono riuscito a leggerlo" in r and "Squat a corpo libero" in r and "darebee.com/workouts/" in r,
           "sito giù: link e scheda", r)
    r = allenamento.seduta_allenamento("2026-10-16")
    atteso("8 volte: 1 min" in r, "sito giù: la corsa della scheda", r)

    # passo per passo: riscaldamento, esercizi, "alla fine"; e alle 00:30 "domani" è il giorno appena cominciato
    darebee.leggi = _letto_finto
    r = allenamento.seduta_allenamento("domani")
    atteso(re.search(r"\n1\. Riscaldamento: .*\n2\. Esercizi: 3 serie, riposo fino a 2 minuti\. Di fila.*\n   - 10 squat"
                     r".*\n3\. Alla fine: 5 min di stretching", r), "seduta passo per passo", r)
    r = allenamento.seduta_allenamento("2026-10-16")
    atteso(re.search(r"\n1\. All'aperto.*camminata veloce\.\n2\. La corsa\.\n   - corsa del giorno 3\n3\. Alla fine: 5 min", r),
           "corsa passo per passo", r)
    adesso[0] = _t("2026-10-17 00:30")
    atteso(allenamento._giorno("domani") == datetime.date(2026, 10, 17), "alle 00:30 domani = oggi")
    adesso[0] = _t("2026-10-17 06:00")
    atteso(allenamento._giorno("domani") == datetime.date(2026, 10, 18), "alle 6 domani = domani")
    adesso[0] = _t("2026-10-16 09:00")

    letti = []

    def senza_livelli(percorso):
        letti.append(percorso)
        return {**_letto_finto(percorso), "livelli": [] if len(letti) == 1 else ["3 serie"]}
    darebee.leggi = senza_livelli
    voce = darebee.scegli_e_leggi(["lower-body"], ["strength"], ["easy", "normal"], set())
    atteso(len(letti) == 2 and voce["u"] == letti[1] and darebee._carica()["scartati"] == [letti[0]],
           "allenamento senza livelli scartato", (letti, voce))

    pagina = ('<img src="/images/workouts/muscles/x.jpg"><img src="/images/workouts/x-workout.jpg?v=1">'
              '<div class="infotext"><p>Rest up to 2 minutes &amp; breathe.</p></div>')
    darebee._get = lambda percorso: type("R", (), {"text": pagina})()
    atteso(darebee._pagina("workouts/x.html") == ("Rest up to 2 minutes & breathe.", "images/workouts/x-workout.jpg", False),
           "lettura della pagina", darebee._pagina("workouts/x.html"))

    # 12. tennis: giocato mercoledì invece di domenica, si trova lo stesso; non fa salire la fase; saltato senza
    # recupero; la settimana dopo c'è di nuovo
    darebee.leggi = _letto_finto
    adesso[0] = _t("2026-10-14 20:00")
    fatte = allenamento._fatte(allenamento._carica())
    r = allenamento.segna_allenamento("fatto", "oggi", "tennis")
    atteso(r.startswith("✅ Segnato: tennis") and allenamento._fatte(allenamento._carica()) == fatte,
           "tennis spostato segnato, fase ferma", r)
    atteso("non c'era nessun allenamento tennis" in allenamento.segna_allenamento("fatto", "oggi", "tennis"),
           "il tennis della settimana è già segnato")
    adesso[0] = _t("2026-10-18 18:05")
    messaggi = controlla()
    atteso(any("dom 25/10 alle 10:00 Tennis" in m[0] for m in messaggi), "tennis la settimana dopo", messaggi)
    adesso[0] = _t("2026-10-25 11:31")
    messaggi = controlla()
    domanda = next((m for m in messaggi if "Tennis" in m[0]), None)
    atteso(domanda is not None, "domanda dopo il tennis", messaggi)
    if domanda:
        testo, pulsanti = allenamento.pulsante(domanda[1][0][1][1])
        atteso("tennis saltato" in testo and not pulsanti, "tennis saltato senza recupero", testo)
    imp = dict(allenamento._impostazioni(), tennis="")
    atteso(allenamento._tennis(imp) is None and allenamento._tennis(dict(imp, tennis="sabato 9.30")) == (5, datetime.time(9, 30)),
           "impostazione del tennis")

    print(f"12 gruppi di casi, errori: {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
