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

    print(f"5 casi, errori: {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
