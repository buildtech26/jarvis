"""Prova dei dati di Salute (salute.py) con un database Notion finto: niente rete.

    .venv\\Scripts\\python prova_salute.py
"""
import datetime
import sys

import notion
import salute


def pagina(id, inviato, giorno="", dati="", sonno="", allenamenti=""):
    utc = inviato.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    testo = lambda t: [{"plain_text": t}] if t else []
    return {"id": id, "created_time": utc, "properties": {
        "Giorno": {"title": testo(giorno)}, "Dati": {"rich_text": testo(dati)},
        "Sonno": {"rich_text": testo(sonno)}, "Allenamenti": {"rich_text": testo(allenamenti)}}}


def main() -> int:
    errori = 0

    def atteso(condizione: bool, cosa: str, risultato=""):
        nonlocal errori
        if not condizione:
            errori += 1
            print(f"SBAGLIATO: {cosa}\n  risultato: {risultato!r}")

    for testo, numero in [("8.123", 8123), ("6,2 km", 6.2), ("97%", 97), ("0,97", 0.97), ("78.4", 78.4),
                          ("1.650,5", 1650.5), ("72 bpm", 72), ("", None), ("—", None)]:
        atteso(salute._numero(testo) == numero, f"numero {testo!r}", salute._numero(testo))
    atteso(salute._dati("passi=8.123; distanza=6200 m;ossigeno=0,96;battito_riposo=;Glicemia=95") ==
           {"passi": 8123, "distanza": 6.2, "ossigeno": 96, "glicemia": 95}, "dati: metri, ossigeno, vuoti, altri",
           salute._dati("passi=8.123; distanza=6200 m;ossigeno=0,96;battito_riposo=;Glicemia=95"))

    notte = ("A letto,23:30,07:20|Principale,23:40,01:05|Profondo,01:05,02:00|REM,02:00,03:00|Sveglio,03:00,03:10|"
             "Principale,03:10,07:10")
    ora = lambda g, h, m=0: datetime.datetime(2026, 10, g, h, m)
    pagine = [pagina("a", ora(12, 9), "2026-10-12", "passi=1.200;battito_medio=70", notte),
              pagina("b", ora(12, 21, 30), "2026-10-12",
                     "passi=9.870;distanza=7,1;calorie_attive=430;calorie_riposo=1.650;battito_medio=78;"
                     "battito_min=52;battito_max=151;battito_riposo=58;hrv=45;ossigeno=97;respiro=15;peso=78,4",
                     notte, "Corsa,07:10,32 min,5,1 km,310 kcal"),
              # dopo mezzanotte: il sonno nelle ultime 24 ore è ancora quello della notte prima, non di questa
              pagina("c", ora(13, 0, 30), "2026-10-13", "passi=150", "Principale,03:10,07:10"),
              pagina("d", ora(10, 22), "10/10/2026", "passi=6.000;battito_riposo=60;hrv=40",
                     "Asleep,00:10,06:40"),
              # sonno mandato dal più recente: si gira
              pagina("e", ora(11, 22), "2026-10-11", "passi=11.000;battito_riposo=56;hrv=50",
                     "Core,06:00,08:00|Deep,01:00,06:00|Core,23:00,01:00")]
    cestino = []

    def chiama(metodo, percorso, **dati):
        if metodo == "PATCH":
            cestino.append(percorso.split("/")[-1])
            return {}
        inizio = datetime.datetime.fromisoformat(dati["filter"]["created_time"]["on_or_after"]).replace(tzinfo=None)
        tenute = [p for p in pagine if datetime.datetime.fromisoformat(
            p["created_time"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None) >= inizio
                  and p["created_time"] <= salute._adesso().astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")]
        return {"results": sorted(tenute, key=lambda p: p["created_time"]), "has_more": False}

    notion._chiama = chiama

    pezzi = salute._pezzi_sonno(notte, ora(12, 9))
    sonno = max(salute._sonni(pezzi), key=lambda s: s["dormito"])
    atteso(sonno["inizio"] == ora(11, 23, 40) and sonno["fine"] == ora(12, 7, 10) and sonno["dormito"] == 440
           and sonno["fasi"] == {"profondo": 55, "rem": 60, "leggero": 325} and sonno["sveglio"] == 10,
           "notte: date, minuti senza sovrapposizioni con 'a letto', fasi e risvegli", sonno)
    girato = salute._sonni(salute._pezzi_sonno("Core,06:00,08:00|Deep,01:00,06:00|Core,23:00,01:00", ora(11, 22)))
    atteso(len(girato) == 1 and girato[0]["inizio"] == ora(10, 23) and girato[0]["dormito"] == 540,
           "sonno mandato al contrario", girato)
    atteso([salute._fase(f) for f in ["In Bed", "A letto", "Principale", "Core", "Profondo", "REM", "Sveglio",
                                      "Addormentato", "4", "5"]] ==
           ["letto", "letto", "leggero", "leggero", "profondo", "rem", "sveglio", "sonno", "profondo", "rem"], "fasi")
    solo_letto =salute._sonni(salute._pezzi_sonno("In Bed,23:00,07:00", ora(12, 9)))
    atteso(solo_letto and solo_letto[0]["solo_letto"] and solo_letto[0]["dormito"] == 480, "solo 'a letto'", solo_letto)
    atteso(salute._allenamenti("Corsa,07:10,32 min,5,1 km,310 kcal|Tennis,2026-10-12 18:00,60 min") ==
           ["Corsa alle 07:10 (32 min,5,1 km,310 kcal)", "Tennis alle 18:00 (60 min)"], "allenamenti",
           salute._allenamenti("Corsa,07:10,32 min,5,1 km,310 kcal|Tennis,2026-10-12 18:00,60 min"))

    # il 12 alle 22: l'ultima riga del giorno, la prima nel cestino
    salute._adesso = lambda: ora(12, 22)
    testo = salute.dati_salute()
    atteso("Salute di oggi (dati fino alle 21:30)" in testo and "7 h 20 dormite, dalle 23:40 alle 07:10" in testo
           and "profondo 55 min, REM 1 h 00, leggero 5 h 25, sveglio 10 min" in testo
           and "9.870 passi, 7,1 km" in testo and "430 attive + 1.650 a riposo" in testo
           and "medio 78, da 52 a 151, a riposo 58" in testo and "HRV 45 ms, ossigeno nel sangue 97%" in testo
           and "peso 78,4 kg" in testo and "Corsa alle 07:10" in testo and "Attenzione" not in testo,
           "oggi per intero", testo)
    atteso(cestino == ["a"], "la riga superata va nel cestino", cestino)

    # il 13 alle 0:40: per oggi solo 150 passi; il sonno è quello del 12, non c'è una notte nuova
    salute._adesso = lambda: ora(13, 0, 40)
    testo = salute.dati_salute()
    atteso("Salute di oggi" in testo and "150 passi" in testo and "notte che finisce lun 12/10" in testo,
           "dopo mezzanotte il sonno resta della notte prima (non c'è ancora quella nuova)", testo)

    # la settimana
    salute._adesso = lambda: ora(13, 13)
    testo = salute.dati_salute(4)
    atteso("- lun 12/10: sonno 7 h 20, 9.870 passi" in testo and "- dom 11/10: sonno 9 h 00, 11.000 passi" in testo
           and "- sab 10/10: sonno 6 h 30, 6.000 passi" in testo
           and "Medie (senza oggi, che non è finito): sonno 7 h 37 a notte, 8.957 passi" in testo
           and "battito a riposo 58" in testo and "Attenzione: l'ultimo invio dall'iPhone è di mar 13/10 alle 00:30"
           in testo, "più giorni con le medie", testo)

    pagine.clear()
    atteso("Nessun dato di Salute" in salute.dati_salute(), "senza righe lo dice", salute.dati_salute())

    print(f"prova_salute: errori {errori}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
