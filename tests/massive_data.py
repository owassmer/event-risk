"""Synthetic data in Massive's shapes (knowledge/DATA.md), put into a store as data rows
the way the client records them, so the portfolio's derivations run with no network."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import yaml

from harness.derive.series import NY, lookback, session_close

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


def flat_prices(store, close: float = 100.0) -> None:
    """A close for every security a test holds without putting its prices, at the as-of and the session before, with
    no dividends: a holding is priced or the portfolio is not read (MATH.md 3.1), so a test whose subject is what a fund
    holds gives each holding the price any security has. Prices a test puts are found before these are asked for. The
    market's calendar, SPY's sessions, is a test's own to give: without it, a listing is measured across its own sessions
    (harness.derive.portfolio.CALENDAR)."""
    from harness.derive.engine import Unavailable

    def bars(subject, covering):
        if subject == "SPY":
            raise Unavailable("no client here reads the daily prices of SPY")
        return {"payload": {"ticker": subject, "adjusted": True, "status": "OK",
                            "results": [{"t": ms(d), "o": close, "h": close, "l": close, "c": close, "v": 1000.0}
                                        for d in ("2026-11-03", ASOF)]},
                "request": {"method": "GET", "url": f"{BASE}/v2/aggs/ticker/{subject}"}, "retrieved_at": FETCHED,
                "range": covering}

    def dividends(subject, covering):
        return {"payload": {"status": "OK", "results": []},
                "request": {"method": "GET", "url": f"{BASE}/stocks/v1/dividends?ticker={subject}"},
                "retrieved_at": FETCHED, "range": covering}
    store.fetchers.setdefault("bars", bars)
    store.fetchers.setdefault("dividends", dividends)


def put_dividends(store, ticker: str, dividends: dict[str, float], *, fetched: str = FETCHED,
                  covering: tuple[str, str] | None = None) -> str:
    """dividends: ex_dividend_date -> split_adjusted_cash_amount, in Massive's dividend shape, each a monthly recurring
    distribution, read over `covering` (the history's span unless given)."""
    start, end = covering or (lookback(ASOF), ASOF)
    results = [{"id": f"E{ticker}{d}", "ticker": ticker, "ex_dividend_date": d, "cash_amount": a,
                "split_adjusted_cash_amount": a, "currency": "USD", "frequency": 12,
                "distribution_type": "recurring"} for d, a in sorted(dividends.items())]
    url = (f"{BASE}/stocks/v1/dividends?ticker={ticker}&ex_dividend_date.gte={start}"
           f"&ex_dividend_date.lte={end}&sort=ex_dividend_date.asc&limit=5000")
    return store.put_data("dividends", ticker, {"status": "OK", "results": results}, {"method": "GET", "url": url},
                          retrieved_at=fetched, range=(start, end))


def put_hourly(store, ticker: str, prices_at_1600: dict[str, float], *, status: str = "OK") -> str:
    """Hourly bars around each day's session close in New York (16:00, 13:00 on an early close); the bar that begins
    at the close opens at the given price."""
    results = []
    for d, p in sorted(prices_at_1600.items()):
        h = session_close(date.fromisoformat(d)).hour
        results.append({"t": ms(d, time(h - 1, 0)), "o": p * 0.99, "h": p, "l": p, "c": p * 0.995, "v": 1.0})
        results.append({"t": ms(d, time(h, 0)), "o": p, "h": p, "l": p, "c": p * 1.01, "v": 1.0})
        results.append({"t": ms(d, time(h + 1, 0)), "o": p * 1.02, "h": p, "l": p, "c": p * 1.03, "v": 1.0})
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
                                               "locale": "us", "composite_figi": f"FIGI{ticker}",
                                               **({"name": name} if name else {})}}
    url = f"{BASE}/v3/reference/tickers/{ticker}?date={ASOF}"
    return store.put_data("ticker_ref", ticker, payload, {"method": "GET", "url": url}, retrieved_at=FETCHED,
                          range=(ASOF, ASOF))


def put_constituents(store, fund: str, rows: list[tuple], *, effective: str = "2026-11-03",
                     status: str = "OK") -> str:
    """rows: (constituent_ticker, weight, asset_class)."""
    results = [{"composite_ticker": fund, "constituent_ticker": t, "weight": w, "asset_class": a,
                "country_of_exchange": "US", "effective_date": effective, **({"figi": f"FIGI{t}"} if t else {})}
               for t, w, a in rows]
    payload = {"status": status, "composite_ticker": fund, "effective_date": effective if status == "OK" else None,
               "results": results if status == "OK" else []}
    url = f"{BASE}/etf-global/v1/constituents?composite_ticker={fund}&effective_date={effective}&limit=1000"
    return store.put_data("constituents", fund, payload, {"method": "GET", "url": url}, retrieved_at=FETCHED,
                          range=(ASOF, ASOF))


def random_returns(rng: np.random.Generator, sd: float = 0.01, days: list[str] | None = None) -> dict[str, float]:
    days = days or SESSIONS[1:]
    return {d: float(x) for d, x in zip(days, rng.normal(0.0, sd, len(days)))}


def factors(directory: Path, named) -> Path:
    """`directory`, its channels.yaml the market and the factors of the ten (CHANNELS) whose proxies are `named`, in
    their order: a test's arithmetic runs on the factors it names, and each factor more is fitted and filled for
    nothing."""
    doc = yaml.safe_load((CHANNELS / "channels.yaml").read_text())
    doc["channels"] = [c for c in doc["channels"] if c["proxy"] == "SPY" or c["proxy"] in named]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "channels.yaml").write_text(yaml.safe_dump(doc))
    return directory


def load_channels(store, named=None):
    """The ten channels, or the market and those whose proxies `named` names."""
    return store.load_initial(CHANNELS if named is None else factors(Path(store.dir) / "factors", named))


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


def put_universe(store, exchange: str, rows: list[dict], *, as_listed: str = "point in time") -> str:
    """An exchange's listings as of the as-of, in the shape data's `universe` kind keeps: each row its listing, type,
    industry, CIK, first and last sessions, mean daily value traded, and, for a fund, its holdings' kinds by weight."""
    payload = {"status": "OK", "exchange": exchange, "asof": ASOF, "as_listed": as_listed, "results": rows}
    return store.put_data("universe", exchange, payload, {"method": "GET", "url": f"universe:{exchange}"},
                          retrieved_at=FETCHED, range=(ASOF, ASOF))


def put_home(store, listing: str, closes: dict[str, float], *, currency: str = "KRW", mic: str = "XKRX",
             moment: str = "06:30:00Z", dividends: dict[str, float] | None = None) -> str:
    """A listing abroad's closes on its home market, in its own currency, each with the moment it was struck, in the
    shape the `home` kind keeps (harness.venues.yahoo)."""
    payload = {"status": "OK", "listing": listing, "symbol": f"{listing}.SYM", "exchange": "home", "mic": mic,
               "timezone": "Asia/Seoul", "currency": currency,
               "results": [{"session": d, "moment": f"{d}T{moment}", "c": c} for d, c in sorted(closes.items())],
               "dividends": [{"ex_date": d, "amount": a} for d, a in sorted((dividends or {}).items())], "splits": []}
    return store.put_data("home", listing, payload, {"method": "GET", "url": f"home:{listing}"},
                          retrieved_at=FETCHED, range=(lookback(ASOF), ASOF))


def put_rate(store, pair: str, levels: dict[str, float], *, base: str, quote: str) -> str:
    """A currency pair's hourly rates: each day's level from an hour before New York's close to an hour after, with
    its base and quote currencies as its source gives them."""
    results = []
    for d, p in sorted(levels.items()):
        h = session_close(date.fromisoformat(d)).hour
        for k in range(-14, 3):  # through the night before, so a price struck abroad has its rate
            results.append({"t": ms(d, time(h, 0)) + k * 3600 * 1000, "o": p, "h": p, "l": p, "c": p, "v": 1.0})
    payload = {"ticker": pair, "status": "OK", "base": base, "quote": quote,
               "results": sorted(results, key=lambda r: r["t"])}
    return store.put_data("hourly", pair, payload, {"method": "GET", "url": f"hourly:{pair}"}, retrieved_at=FETCHED,
                          range=(lookback(ASOF), ASOF))
