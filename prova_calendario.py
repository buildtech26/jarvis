"""Prova dell'avviso di sovrapposizione in calendario.py, con un calendario finto: niente rete e niente eventi veri.

    .venv\\Scripts\\python prova_calendario.py
"""
import sys

import calendario

LEZIONE = {"id": "lez", "titolo": "RETI LOGICHE T", "inizio": calendario._iso("2026-10-16 14:00"),
           "fine": calendario._iso("2026-10-16 16:00"), "tutto_il_giorno": False, "luogo": "AULA 2.9",
           "calendario": "UniBo"}
PRIMA = {**LEZIONE, "id": "prima", "titolo": "CONTROLLI AUTOMATICI", "inizio": calendario._iso("2026-10-16 11:30"),
         "fine": calendario._iso("2026-10-16 14:00")}
COMPLEANNO = {**LEZIONE, "id": "comp", "titolo": "Compleanno di Luca", "inizio": calendario._iso("2026-10-16 00:00"),
              "fine": calendario._iso("2026-10-17 00:00"), "tutto_il_giorno": True}


def _finto(eventi: list[dict], guasto: bool = False):
    chiamate: list[str] = []

    def chiama(azione: str, **d) -> dict:
        chiamate.append(azione)
        if azione == "eventi":
            if guasto:
                raise RuntimeError("rete assente")
            return {"eventi": eventi}
        nuovo = {"id": d.get("id", "nuovo"), "titolo": d.get("titolo") or "Call", "inizio": d["inizio"],
                 "fine": d.get("fine") or d["inizio"], "tutto_il_giorno": d.get("tutto_il_giorno", False),
                 "luogo": "", "calendario": "mio"}
        return {"creato": nuovo, "spostato": nuovo}

    calendario._chiama = chiama
    return chiamate


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato: str):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    # 1. call durante la lezione: creata e segnalata; il controllo, in parallelo, può già vedere la call stessa
    _finto([LEZIONE, {**LEZIONE, "id": "nuovo", "titolo": "Call"}])
    r = calendario.crea_evento("Call", "2026-10-16 14:30")
    atteso(r.startswith("Creato:") and "RETI LOGICHE" in r and r.count("Call") == 1, "accavallata", r)

    # 2. finisce quando l'altra comincia, giornata intera: nessun avviso
    _finto([PRIMA, COMPLEANNO])
    r = calendario.crea_evento("Call", "2026-10-16 14:00", "2026-10-16 15:00")
    atteso(r.startswith("Creato:") and "si sovrappone" not in r, "attaccata / tutto il giorno", r)

    # 3. spostata sopra la lezione: segnalata, ma non con sé stessa
    _finto([LEZIONE, {**LEZIONE, "id": "nuovo", "titolo": "Call"}])
    r = calendario.sposta_evento("nuovo", "2026-10-16 15:00", "2026-10-16 16:00")
    atteso(r.count("RETI LOGICHE") == 1 and r.count("Call") == 1, "spostata", r)

    # 4. il controllo fallisce: l'evento si crea lo stesso, senza avviso
    chiamate = _finto([LEZIONE], guasto=True)
    r = calendario.crea_evento("Call", "2026-10-16 14:30")
    atteso(r.startswith("Creato:") and "si sovrappone" not in r and "crea" in chiamate, "controllo guasto", r)

    # 5. evento di tutto il giorno: nessun controllo
    chiamate = _finto([LEZIONE])
    r = calendario.crea_evento("Gita", "2026-10-16 00:00", tutto_il_giorno=True)
    atteso(chiamate == ["crea"], "tutto il giorno", r)

    # 6. evento con orario lungo settimane (il 12/10 "fino a Natale"): rifiutato, con l'indicazione dello strumento
    chiamate = _finto([])
    r = calendario.crea_evento("Lezione con Baldini", "2026-10-13 20:30", "2026-12-22 21:30")
    atteso(r.startswith("Non creato") and "crea_eventi_ripetuti" in r and not chiamate, "evento lungo settimane", r)

    # 7. ripetuti: martedì e giovedì dal 13/10 a Natale, un evento per data, ora legale e solare giuste,
    # sovrapposizioni segnalate (una lezione il 15/10 alle 21)
    serale = {**LEZIONE, "id": "ser", "titolo": "Calcetto", "inizio": calendario._iso("2026-10-15 21:00"),
              "fine": calendario._iso("2026-10-15 22:00")}
    creati: list[dict] = []

    def chiama(azione: str, **d) -> dict:
        if azione == "eventi":
            a, b = calendario._locale(d["da"]), calendario._locale(d["a"])
            return {"eventi": [e for e in [serale] if calendario._locale(e["inizio"]) < b and calendario._locale(e["fine"]) > a]}
        creati.append(d)
        return {"creato": {"id": f"n{len(creati)}", **d, "calendario": "mio"}}
    calendario._chiama = chiama
    r = calendario.crea_eventi_ripetuti("Lezione con Baldini", "martedì e giovedì", "20:30", "21:30", "2026-10-13",
                                        "2026-12-24")
    inizi = sorted(c["inizio"] for c in creati)
    atteso(len(creati) == 22 and inizi[0] == "2026-10-13T20:30:00+02:00" and inizi[-1] == "2026-12-24T20:30:00+01:00"
           and "Creati 22 eventi" in r and "mar e gio 20:30-21:30" in r, "martedì e giovedì fino a Natale", r)
    atteso("ATTENZIONE" in r and r.count("Calcetto") == 1, "sovrapposizione segnalata una volta", r)
    creati.clear()
    r = calendario.crea_eventi_ripetuti("Lezione con Baldini", "venerdì", "14:30", "", "2026-10-13", "2026-12-24")
    atteso(len(creati) == 10 and "ven 14:30-15:30" in r and "18/12" in r and "ATTENZIONE" not in r, "venerdì", r)
    atteso(calendario._giorni_settimana("mar, gio e ven") == [1, 3, 4] and calendario._giorni_settimana("boh") == [],
           "nomi dei giorni", "")

    print(f"7 casi, errori: {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
