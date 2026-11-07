"""Synthetic data in Massive's shapes (knowledge/DATA.md), put into a store as data rows
the way the client records them, so the portfolio's derivations run with no network."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np

from harness.derive.series import NY, lookback

ASOF = "2026-11-04"
# After 20:00 America/New_York on the as-of date, so its daily bar is final.
FETCHED = "2026-11-05T02:00:00+00:00"
BASE = "https://api.massive.com"
KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"
CHANNELS = Path(__file__).resolve().parent / "fixtures" / "channels"  # ten channels, for tests across several
PROXIES = ["SPY", "SHY", "TLT", "UUP", "USO", "EFA", "EEM", "GLD", "HYG", "IBIT"]


def weekdays(start: str, end: str) -> list[str]:
    day, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while day <= last:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


SESSIONS = weekdays(lookback(ASOF), ASOF)


def ms(day: str, at: time = time(0, 0)) -> int:
    return int(datetime.combine(date.fromisoformat(day), at, NY).timestamp() * 1000)


def closes_from(returns: dict[str, float], start: float = 100.0) -> dict[str, float]:
    """Closes on each session of the returns, starting from `start` the session before."""
    days = sorted(returns)
    first = SESSIONS[SESSIONS.index(days[0]) - 1]
    out, level = {first: start}, start
    for d in days:
        level *= 1.0 + returns[d]
        out[d] = level
    return out


def put_bars(store, ticker: str, closes: dict[str, float], *, fetched: str = FETCHED,
             dividends: dict[str, float] | None = None) -> str:
    """A ticker's daily bars, and its dividends over the same range (none unless given)."""
    payload = {"ticker": ticker, "adjusted": True, "status": "OK",
               "results": [{"t": ms(d), "o": c, "h": c, "l": c, "c": c, "v": 1000.0} for d, c in sorted(closes.items())]}
    url = f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{lookback(ASOF)}/{ASOF}?adjusted=true&sort=asc&limit=50000"
    put_dividends(store, ticker, dividends or {}, fetched=fetched)
    return store.put_data("bars", ticker, payload, {"method": "GET", "url": url}, retrieved_at=fetched,
                          range=(lookback(ASOF), ASOF))


def put_dividends(store, ticker: str, dividends: dict[str, float], *, fetched: str = FETCHED) -> str:
    """dividends: ex_dividend_date -> split_adjusted_cash_amount, in Massive's dividend shape."""
    results = [{"id": f"E{ticker}{d}", "ticker": ticker, "ex_dividend_date": d, "cash_amount": a,
                "split_adjusted_cash_amount": a, "currency": "USD", "frequency": 12,
                "distribution_type": "recurring"} for d, a in sorted(dividends.items())]
    url = (f"{BASE}/stocks/v1/dividends?ticker={ticker}&ex_dividend_date.gte={lookback(ASOF)}"
           f"&ex_dividend_date.lte={ASOF}&sort=ex_dividend_date.asc&limit=5000")
    return store.put_data("dividends", ticker, {"status": "OK", "results": results}, {"method": "GET", "url": url},
                          retrieved_at=fetched, range=(lookback(ASOF), ASOF))


def put_hourly(store, ticker: str, prices_at_1600: dict[str, float], *, status: str = "OK") -> str:
    """Hourly bars around 16:00 America/New_York each day; the 16:00 bar opens at the given price."""
    results = []
    for d, p in sorted(prices_at_1600.items()):
        results.append({"t": ms(d, time(15, 0)), "o": p * 0.99, "h": p, "l": p, "c": p * 0.995, "v": 1.0})
        results.append({"t": ms(d, time(16, 0)), "o": p, "h": p, "l": p, "c": p * 1.01, "v": 1.0})
        results.append({"t": ms(d, time(17, 0)), "o": p * 1.02, "h": p, "l": p, "c": p * 1.03, "v": 1.0})
    payload = {"ticker": ticker, "status": status, "results": results if status == "OK" else []}
    if status != "OK":
        payload.update({"http_status": 403, "error": "You are not entitled to this data."})
    url = f"{BASE}/v2/aggs/ticker/{ticker}/range/1/hour/{lookback(ASOF)}/{ASOF}?adjusted=true&sort=asc&limit=50000"
    return store.put_data("hourly", ticker, payload, {"method": "GET", "url": url}, retrieved_at=FETCHED,
                          range=(lookback(ASOF), ASOF))


def put_reference(store, ticker: str, kind: str | None, sic: str | None = None, name: str | None = None) -> str:
    if kind is None:
        payload = {"status": "NOT_FOUND", "http_status": 404, "error": "Ticker not found."}
    else:
        payload = {"status": "OK", "results": {"ticker": ticker, "type": kind, "sic_code": sic, "market": "stocks",
                                               "locale": "us", **({"name": name} if name else {})}}
    url = f"{BASE}/v3/reference/tickers/{ticker}?date={ASOF}"
    return store.put_data("ticker_ref", ticker, payload, {"method": "GET", "url": url}, retrieved_at=FETCHED,
                          range=(ASOF, ASOF))


def put_constituents(store, fund: str, rows: list[tuple], *, effective: str = "2026-11-03",
                     status: str = "OK") -> str:
    """rows: (constituent_ticker, weight, asset_class)."""
    results = [{"composite_ticker": fund, "constituent_ticker": t, "weight": w, "asset_class": a,
                "country_of_exchange": "US", "effective_date": effective} for t, w, a in rows]
    payload = {"status": status, "composite_ticker": fund, "effective_date": effective if status == "OK" else None,
               "results": results if status == "OK" else []}
    url = f"{BASE}/etf-global/v1/constituents?composite_ticker={fund}&effective_date={effective}&limit=1000"
    return store.put_data("constituents", fund, payload, {"method": "GET", "url": url}, retrieved_at=FETCHED,
                          range=(ASOF, ASOF))


def random_returns(rng: np.random.Generator, sd: float = 0.01, days: list[str] | None = None) -> dict[str, float]:
    days = days or SESSIONS[1:]
    return {d: float(x) for d, x in zip(days, rng.normal(0.0, sd, len(days)))}


def load_channels(store):
    return store.load_initial(CHANNELS)


def put_market(store, rng):
    """Ten correlated proxies over the sessions."""
    raw = []
    for i, ticker in enumerate(PROXIES):
        own = np.array(list(random_returns(rng, 0.01).values()))
        mixed = own + sum(0.3 * r for r in raw[-2:]) if raw else own
        raw.append(mixed)
        returns = dict(zip(SESSIONS[1:], (float(x) for x in mixed)))
        put_bars(store, ticker, closes_from(returns))
    return raw
