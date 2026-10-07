"""Ponte verso company-analyst: gira con il Python di company-analyst, mai con quello di Jarvis.

Jarvis lo lancia come sottoprocesso (vedi analista.py), così pandas e yfinance restano nel venv di company-analyst
e non pesano sulla memoria di Jarvis quando non servono. Stampa su stdout un JSON con il prompt completo
(scheda dei fatti + istruzioni) costruito dagli stessi moduli che usa la web app.

Uso: <python di company-analyst> analista_ponte.py <cartella company-analyst> <società o ticker> [tipo di analisi]
"""
import json
import sys
from pathlib import Path


def _ticker(query: str) -> str:
    """Il ticker dal nome ("apple" → AAPL); un ticker scritto già bene passa così com'è."""
    from src.data import search_companies

    risultati = search_companies(query.strip(), max_results=5)
    # Yahoo mette per primo il simbolo esatto quando gli si passa un ticker; se non trova niente si prova lo stesso
    return risultati[0]["symbol"] if risultati else query.strip().upper()


def _fatti_societa(ticker: str, peers: tuple[str, ...]):
    """Come build_facts di api.py, senza FastAPI."""
    from src import comparison, narrative
    from src.catalysts import build_calendar
    from src.data import (load_analyst_view, load_calendar, load_company, load_dividends, load_earnings_history,
                          load_news, load_ownership, load_peer_snapshot)
    from src.metrics import compute_ratios, extract_lines
    from src.quarterly import guidance_record, quarter_names, quarterly_summary
    from src.scoring import build_scorecard, red_flag_report
    from src.valuation import build_scenarios, is_financial

    company = load_company(ticker)
    if not company.is_valid:
        raise LookupError(f"Nessun dato per «{ticker}». Le borse non americane usano un suffisso: .MI, .DE, .PA, .L.")
    lines = extract_lines(company)
    ratios = compute_ratios(lines)
    earnings = load_earnings_history(ticker)
    snapshot = load_peer_snapshot((ticker, *peers)) if peers else None
    return company, narrative.Facts(
        company=company,
        lines=lines,
        ratios=ratios,
        scorecard=build_scorecard(company, lines, ratios),
        report=red_flag_report(company, lines, ratios),
        quarters=quarterly_summary(company),
        quarter_names=quarter_names(company),
        guidance=guidance_record(earnings),
        catalysts=build_calendar(company, lines, load_calendar(ticker), earnings, load_dividends(ticker)),
        scenarios=build_scenarios(company, lines) if not is_financial(company) else [],
        peer_snapshot=snapshot if snapshot is not None and not snapshot.empty else None,
        peer_reading=comparison.peer_reading(snapshot, ticker) if peers else None,
        analyst=load_analyst_view(ticker),
        news=load_news(ticker),
        ownership=load_ownership(ticker),
    )


def _fatti_fondo(ticker: str):
    """Come build_fund_facts di api.py, senza competitor (il confronto fra ETF resta alla web app)."""
    from src import funds, narrative
    from src.data import load_fund, load_news, load_prices

    fund = load_fund(ticker)
    prices = load_prices(ticker, "5y")
    shape = funds.composition(fund)
    return fund, narrative.FundFacts(
        fund=fund,
        cost=funds.cost(fund),
        identity=funds.identity(fund),
        composition=shape,
        multiples=funds.portfolio_multiples(fund),
        bonds=funds.bond_profile(fund, shape),
        calendar=funds.calendar_returns(prices),
        trailing=funds.trailing_returns(prices),
        rolling=funds.rolling_returns(prices),
        risk=funds.risk(prices),
        news=load_news(ticker),
    )


def prompt(query: str, tipo: str) -> dict:
    from src import funds, narrative
    from src.data import load_company, suggest_peers

    ticker = _ticker(query)
    if funds.is_fund(load_company(ticker).info):
        analisi = narrative.FUND_ANALYSES_BY_KEY.get(tipo) or narrative.FUND_ANALYSES_BY_KEY["fondo-report"]
        fund, fatti = _fatti_fondo(ticker)
        return {"ticker": ticker, "nome": fund.name, "fondo": True, "analisi": analisi.title,
                "sistema": narrative.SYSTEM_PROMPT, "prompt": narrative.build_fund_prompt(analisi, fatti)}

    analisi = narrative.ANALYSES_BY_KEY.get(tipo) or narrative.ANALYSES_BY_KEY["report"]
    peers = tuple(p for p in suggest_peers(ticker) if p != ticker)[:5] if analisi.needs_peers or "competitor" in analisi.blocks else ()
    company, fatti = _fatti_societa(ticker, peers)
    return {"ticker": ticker, "nome": company.name, "fondo": False, "analisi": analisi.title,
            "sistema": narrative.SYSTEM_PROMPT, "prompt": narrative.build_prompt(analisi, fatti)}


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        esito = prompt(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "report")
    except Exception as e:
        esito = {"errore": f"{type(e).__name__}: {e}"}
    print(json.dumps(esito, ensure_ascii=False))
