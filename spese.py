"""Spese Live in sola lettura: quanto ho speso, saldo, categorie, movimenti.

Spese Live resta un progetto a parte (Vercel + Supabase): Jarvis legge solo la rotta /api/jarvis con il token
SPESE_TOKEN del .env. Non può aggiungere spese, cambiare categorie né lanciare l'aggiornamento dalla banca:
i movimenti arrivano come sempre dal sync di Spese Live (al massimo ogni 6 ore, più il cron delle 7).
"""
import datetime
import os

import requests
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("SPESE_URL", "https://spese-live.vercel.app").rstrip("/")
TOKEN = os.getenv("SPESE_TOKEN", "").strip()


def configurato() -> bool:
    return bool(TOKEN)


def _leggi(**params) -> dict:
    r = requests.get(f"{URL}/api/jarvis", params=params, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=20)
    if r.status_code == 403:
        raise RuntimeError("Spese Live rifiuta il token: controlla SPESE_TOKEN nel .env e JARVIS_TOKEN su Vercel.")
    r.raise_for_status()
    return r.json()


def _e(x: float) -> str:
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def spese_riepilogo(mese: str = "") -> str:
    """Le spese dell'utente da Spese Live (il suo conto Revolut): totale di oggi, della settimana e del mese, spese
    per categoria, budget rimasto, saldo del conto e patrimonio. Usalo per "quanto ho speso oggi/questo mese?",
    "quanto ho sul conto?", "quanto ho speso in ristoranti?", "come sto col budget?". Per una categoria usa questo,
    non movimenti_spese. Le categorie si chiamano: Spesa (supermercati, alimentari), Ristoranti e bar, Trasporti,
    Viaggi, Abbonamenti, Casa e bollette, Salute, Svago, Shopping, Contanti, Bonifici e trasferimenti, Altro.

    Args:
        mese: 'AAAA-MM' per un mese passato (es. '2026-09' per "il mese scorso"); vuoto per il mese in corso
    """
    d = _leggi(vista="riepilogo", **({"m": mese} if mese else {}))
    m = d["mese"]
    righe = [f"Mese {m['mese']}: speso {_e(m['speso'])}, entrate {_e(m['entrate'])}."]
    if m["budget"] is not None:
        righe.append(f"Budget {_e(m['budget'])}, rimasti {_e(m['rimastoNelBudget'])}, giorni rimasti {m['giorniRimasti']}.")
    if m["categorie"]:
        righe.append("Per categoria: " + "; ".join(f"{c['categoria']} {_e(c['speso'])}" for c in m["categorie"]) + ".")
    if d["speseDiOggi"] is not None:
        o = d["speseDiOggi"]
        voci = ", ".join(f"{v['esercente']} {_e(v['importo'])}" + ("" if v["contaComeSpesa"] else " (non conta)")
                         for v in o["voci"])
        righe.append(f"Oggi ({d['oggi']}): {_e(o['totale'])}" + (f" — {voci}." if voci else ", nessuna spesa."))
        s = d["settimana"]
        righe.append(f"Settimana dal {s['dal']}: {_e(s['speso'])} (stessi giorni della settimana scorsa: "
                     f"{_e(s['settimanaScorsaStessiGiorni'])}).")
    if d["saldoConto"]:
        c = d["saldoConto"]
        quando = ""
        if c.get("aggiornatoIl"):
            quando = f", aggiornato il {datetime.datetime.fromisoformat(c['aggiornatoIl'].replace('Z', '+00:00')).astimezone():%d/%m alle %H:%M}"
        righe.append(f"Saldo del conto corrente: {_e(c['importo'])}{quando}.")
    p = d["patrimonio"]
    altro = "; ".join(f"{a['voce']} {_e(a['valore'])} al {a['al']}" for a in p["altro"])
    righe.append(f"Patrimonio totale {_e(p['totale'])} (conti Revolut {_e(p['contiRevolut'])}" + (f"; {altro}" if altro else "") + ").")
    righe.append("I pagamenti con carta arrivano dalla banca con qualche ora di ritardo.")
    return "\n".join(righe)


def movimenti_spese(da: str = "", a: str = "", cerca: str = "") -> str:
    """I singoli movimenti del conto da Spese Live, fra due date, filtrati per esercente o categoria. Usalo per
    "quanto ho speso da Esselunga questo mese?", "cosa ho pagato ieri?", "quanto ho speso su Amazon a settembre?".

    Args:
        da: data iniziale 'AAAA-MM-GG' (vuoto = 6 giorni prima di a); al massimo 92 giorni prima di a
        a: data finale 'AAAA-MM-GG' inclusa (vuoto = oggi)
        cerca: una parola del nome dell'esercente o della categoria (es. 'esselunga', 'amazon', 'spesa'); vuoto =
            tutti. Se non trova niente, prova vuoto e guarda l'elenco invece di indovinare altre parole
    """
    params = {"vista": "movimenti", **({"da": da} if da else {}), **({"a": a} if a else {}), **({"cerca": cerca} if cerca else {})}
    d = _leggi(**params)
    if not d["movimenti"]:
        return f"Nessun movimento dal {d['da']} al {d['a']}" + (f" con '{cerca}'." if cerca else ".")
    righe = [f"Dal {d['da']} al {d['a']}" + (f" con '{cerca}'" if cerca else "")
             + f": {d['numero']} movimenti, spese che contano {_e(d['totaleSpese'])}."]
    for t in d["movimenti"][:40]:
        nota = "" if t["contaComeSpesa"] or t["importo"] > 0 else " (non conta come spesa)"
        righe.append(f"- {t['data']} {_e(t['importo'])} {t['esercente']} [{t['categoria']}]{nota}{' in attesa' if t['inAttesa'] else ''}")
    if d["numero"] > 40:
        righe.append(f"... e altri {d['numero'] - 40}.")
    return "\n".join(righe)


def in_breve(cosa: str) -> str:
    """Risposte dirette senza Gemini, per i comandi in brain.COMANDI: 'oggi', 'mese' o 'saldo'."""
    d = _leggi(vista="riepilogo")
    if cosa == "saldo":
        c = d["saldoConto"]
        return f"Sul conto hai {_e(c['importo'])}, signore." if c else "Saldo non disponibile: nessun conto collegato."
    if cosa == "oggi":
        o = d["speseDiOggi"]
        if not o["voci"]:
            return "Oggi nessuna spesa registrata. I pagamenti con carta arrivano dalla banca con qualche ora di ritardo."
        return f"Oggi hai speso {_e(o['totale'])}: " + ", ".join(f"{v['esercente']} {_e(v['importo'])}" for v in o["voci"][:4]) + "."
    m = d["mese"]
    testo = f"Questo mese hai speso {_e(m['speso'])}"
    if m["categorie"]:
        testo += f", soprattutto in {m['categorie'][0]['categoria'].lower()} ({_e(m['categorie'][0]['speso'])})"
    if m["rimastoNelBudget"] is not None:
        testo += f". Nel budget restano {_e(m['rimastoNelBudget'])}" if m["rimastoNelBudget"] >= 0 else f". Budget sforato di {_e(-m['rimastoNelBudget'])}"
    return testo + "."


STRUMENTI = [spese_riepilogo, movimenti_spese]
