"""Prova del personal trainer (allenamento.py) con un calendario finto e una cartella temporanea: niente rete, niente
eventi veri, la scheda e lo stato veri non vengono toccati.

    .venv\\Scripts\\python prova_allenamento.py

Settimana del 12/10: lunedì lezione fino alle 18:45, martedì lezione dalle 8, mercoledì pieno, sabato un compleanno
(tutto il giorno, non conta).
"""
import datetime
import re
import sys
import tempfile
from pathlib import Path

import allenamento
import brain
import calendario


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
    a1, a3 = allenamento._contenuto("Palestra A", 1), allenamento._contenuto("Palestra A", 3)
    atteso("Goblet" in a1 and "Riscaldamento" in a1 and "bilanciere" not in a1, "Palestra A fase 1", a1)
    atteso("Squat con bilanciere" in a3, "Palestra A fase 3", a3)
    atteso("3 giri" in allenamento._contenuto("Corpo libero", 1), "corpo libero fase 1")
    atteso("Palestra B" not in allenamento._contenuto("Palestra A", 2), "sezioni separate")

    # 2. domenica alle 17 la settimana dopo non è ancora pianificata, quella in corso è prima dell'inizio
    atteso(controlla() == [] and cal.creati == 0, "niente prima dell'ora di pianificare", cal.creati)
    r = allenamento.seduta_allenamento("domani")
    atteso("non è ancora in calendario" in r, "settimana non pianificata: non è riposo", r)

    # 3. alle 18:05 la pianifica: mercoledì pieno salta, il giovedì lo sostituisce
    adesso[0] = _t("2026-10-11 18:05")
    messaggi = controlla()
    atteso(len(messaggi) == 1 and "in calendario" in messaggi[0][0], "messaggio della settimana", messaggi)
    atteso(orari() == ["lun 19:30 Palestra A", "mar 19:00 Corsa", "gio 19:00 Palestra B", "ven 07:00 Corpo libero",
                       "sab 19:00 Palestra A"], "giorni e orari", orari())
    atteso("Riposo: mer, dom" in messaggi[0][0], "giorni di riposo", messaggi[0][0])
    atteso("Goblet" in cal.allenamenti()[0]["descrizione"], "esercizi nella descrizione")
    atteso(allenamento._carica()["palestra"] == "B", "la settimana dopo comincia con la B")

    # 4. un secondo giro non duplica niente
    adesso[0] = _t("2026-10-11 18:10")
    atteso(controlla() == [] and cal.creati == 5, "nessun doppione", cal.creati)

    # 5. rifai: toglie le cinque e le rimette uguali, con le lettere di prima
    r = allenamento.pianifica_allenamenti("prossima", rifai=True)
    atteso(len(cal.allenamenti()) == 5 and orari()[0] == "lun 19:30 Palestra A" and orari()[4].endswith("Palestra A"),
           "rifai", f"{r} {orari()}")

    # 6. lunedì alle 21:01 (fine 20:30 + 30 min) chiede com'è andata, con i pulsanti
    adesso[0] = _t("2026-10-12 21:01")
    messaggi = controlla()
    atteso(len(messaggi) == 1 and "Palestra A" in messaggi[0][0] and len(messaggi[0][1][0]) == 2, "domanda", messaggi)
    dati_fatto = messaggi[0][1][0][0][1]
    testo, pulsanti = allenamento.pulsante(dati_fatto)
    atteso(testo.startswith("✅") and not pulsanti, "pulsante fatto", testo)
    storico = (cartella / "Allenamento" / "Storico.md").read_text(encoding="utf-8")
    atteso("Palestra A · fase 1 · ✅ fatto" in storico, "storico in Obsidian", storico)
    atteso(controlla() == [], "non richiede due volte")

    # 7. corsa spostata a mano alle 20, Palestra B di giovedì cancellata a mano
    corsa, palestra_b = cal.allenamenti()[1], cal.allenamenti()[2]
    corsa["inizio"], corsa["fine"] = _t("2026-10-13 20:00").isoformat(), _t("2026-10-13 20:45").isoformat()
    del cal.eventi[palestra_b["id"]]
    adesso[0] = _t("2026-10-13 20:20")
    atteso(controlla() == [], "corsa spostata: non ancora finita")
    stati = {s["tipo"]: s["stato"] for s in allenamento._carica()["sedute"] if s["stato"] != "cancellato"}
    atteso("Palestra B" not in stati, "cancellata a mano", stati)

    # 8. saltata: propone il recupero giovedì mattina (mercoledì è pieno, giovedì ora è libero)
    adesso[0] = _t("2026-10-13 21:16")
    messaggi = controlla()
    atteso(len(messaggi) == 1 and "Corsa" in messaggi[0][0], "domanda sulla corsa", messaggi)
    testo, pulsanti = allenamento.pulsante(messaggi[0][1][0][1][1])
    atteso("gio 15/10 alle 07:00" in testo and pulsanti[0][0][1].endswith(":rec:202610150700"), "recupero", testo)
    testo, _ = allenamento.pulsante(pulsanti[0][0][1])
    atteso(testo.startswith("🔁") and "gio 07:00 Corsa" in orari(), "recupero in calendario", f"{testo} {orari()}")
    atteso(allenamento.pulsante(pulsanti[0][0][1])[0].startswith("L'ho già"), "recupero non doppio")

    # 9. a voce: venerdì "ho fatto il corpo libero"; giorno senza sedute
    adesso[0] = _t("2026-10-16 09:00")
    r = allenamento.segna_allenamento("fatto", "oggi")
    atteso("Corpo libero fatta" in r and "in tutto: 2" in r, "segna a voce", r)
    r = allenamento.seduta_allenamento("2026-10-14")
    atteso("riposo" in r and "Il prossimo: Corsa gio 15/10" in r, "giorno di riposo con il prossimo (il recupero)", r)
    r = allenamento.seduta_allenamento("domani")
    atteso("Palestra A" in r and "Goblet" in r, "allenamento di domani", r)
    r = allenamento.allenamenti_settimana()
    atteso("fatto" in r and "saltato" in r and "Sedute fatte in tutto: 2" in r, "settimana", r)

    # 10. comando diretto senza Gemini
    for frase, giorno in [("cosa faccio domani in palestra", "domani"), ("allenamento di oggi", "oggi"),
                          ("che allenamento ho domani", "domani")]:
        trovato = [m for schema, nome, _ in brain.COMANDI if nome == "seduta_allenamento"
                   and (m := re.fullmatch(schema, frase))]
        atteso(trovato and next(g for g in trovato[0].groups() if g) == giorno, f"comando diretto {frase!r}")

    print(f"10 gruppi di casi, errori: {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
