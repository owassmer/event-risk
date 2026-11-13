"""The portfolio: its holdings at current prices, the look-through into names, and each name's price.

The holdings at current prices and V: as of today, each name's latest mark; for a past as-of, that day's close.
A holding given in dollars, or as a weight of a total, was worth what was given when it was given: its quantity is
implied at the price the store held then, the mark it had read by that moment or, before any, the first it read,
and from then on it is valued at the current price, as a holding given as a quantity is. When it was given is when
its link was made or its figure last edited (Store.given_at). Each holding's move is its price's session move,
in dollars its quantity times the change from the close before; the portfolio's is their sum, and as a fraction of
what the portfolio was worth at those closes. Then the look-through into Names and looks_through_to links, which
gives every Name its e, the dollars of exposure the portfolio has to it, signed, held directly and through funds, by
what each fund's rows state; each holding the dollars of exposure it reaches at each name, by the funds they pass
through, and each fund it expands on its way, with its dollars there; each fund it expands its parts; and each fund
whose holdings it reads what it holds, by its rows' values. Value and exposure are two quantities: a holding's value
moves at its own betas, and need not equal the exposures its rows state. And each Name's type, sic, company (what it
is, from its reference), and current price.
"""

from __future__ import annotations

from dataclasses import replace

import logging
import math
import re
import threading
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from functools import cache
from importlib.resources import files
from typing import Any, Callable, Iterable

from harness import touches
from harness.derive import series
from harness.derive.assets import (COIN, COMMODITY_FUND_SIC, Kinds, cusip_of, figi_kind, isin_of, labelled, to_read,
                                   unclassed)
from harness.derive.rows import (OCC, close_moment, coins, companies_of, establish, established_by_price, fit_slope,
                                 flex_underlying, implied_net_assets, index_identifiers, listed_by_ticker,
                                 option_price, price_subject, report_option_key, reproduced, states_shares,
                                 strike_text, subject_day, index_key, index_symbol, indexes_exposed, notional_codes,
                                 option_symbol, option_underlying, reached,
                                 security_tickers, slope_reference, ticker_reference, with_exposure)
from harness.derive.engine import Derivation, Inputs, Unavailable, Waiting, canonical
from harness.derive.identity import (COMPANY_TYPES, FUND_TYPES, MAPPINGS, RECEIPTS, Lookup, abroad,
                                     company_id, held_identifiers, own_ticker, resolve, row_key, unresolved, us_ticker)
from harness.venues.yahoo import region_of
from harness.observations import read_today
from harness.venues.massive import answered

log = logging.getLogger("harness.derive")  # a read that failed past its retries, for us: the analyst never sees it

# A row's exposure to a company under a quarter of a percent of its fund's net assets, in absolute value and at the
# product of the weights on the path to it, reaches no name: it is a negligible part of the fund, while resolving it to
# its company takes reads of several sources, and the fund's own response carries it. One whose ISIN or FIGI is a
# company's the portfolio already names reaches it whatever its size, with no read.
FLOOR = 0.0025
NAME_TYPES = COMPANY_TYPES  # a share of a company is a name: harness.derive.identity
CLOSED_END = "FUND"  # Massive's type of a closed-end fund, expanded from its Form N-PORT report (MATH.md 3.1)
# the funds ETF Global, which gives exchange-traded funds' holdings, gives none of: a closed-end fund and a mutual fund,
# each read from its Form N-PORT report whole (harness.venues.funds)
REPORT_ONLY = (CLOSED_END, "MF")
CASH = "CASH:USD"  # dollar cash, as a holding of cash:USD is booked: a name no listing has (CASH is Pathward's ticker)


def ticker_of(name_id: str) -> str:
    """The ticker an id names: a company's or a fund's, `name:{TICKER}`. Cash's id names a currency, and a currency's
    code can be another security's ticker (USD is a fund's), so cash gives the security its currency moves as, as a
    holding of it is booked: dollar cash (CASH), or another currency's pair at its rate in dollars (MATH.md 3.1)."""
    kind, _, rest = name_id.partition(":")
    if kind == "cash":
        return CASH if rest.upper() == series.PRICE_CURRENCY else series.dollar_pair(rest)
    return rest


def name_id(ticker: str) -> str:
    return f"name:{ticker}"


def _held(row: dict) -> str:
    """The security a holding's priced row is of (HOLDINGS): its own, or, for a row priced before rows named it, its
    name's ticker."""
    return row.get("security") or ticker_of(row["name"])


def security_of(holding: dict) -> str:
    """The security a holding is of: the ticker the member gave, stated on its holds link, which can be one of several
    securities of its company's name (MATH.md 3.1); for a link written before holdings stated it, its name's ticker. Cash
    in dollars is cash (CASH), and cash in another currency is that currency at its rate in dollars, its pair."""
    to = holding["to"]
    if to.startswith("cash:"):
        return ticker_of(to)
    return (holding.get("stated") or {}).get("ticker") or (to if to.startswith("market:") else ticker_of(to))


def held_name(holding: dict) -> str:
    """The name a holding is valued as: its link's target, a company's or a fund's name or a market; and for cash, the
    name its currency moves as, dollar cash (CASH) or another currency's pair at its rate (MATH.md 3.1)."""
    return name_id(security_of(holding)) if holding["to"].startswith("cash:") else holding["to"]


def pseudo(ticker: str) -> str | None:
    """CASH:USD is dollar cash, as a holding of cash:USD is booked. CASH:{fund} and UNMAPPED:{fund} were a fund's cash
    and its residual in the booking before exposure (look-through v16), which the rows recorded then still name."""
    if ticker.startswith("CASH:"):
        return "cash"
    if ticker.startswith("UNMAPPED:"):
        return "unmapped"
    return None


def shocked_names(ctx: Inputs) -> list[str]:
    """The names an event's consequences run to (MATH.md 1.4)."""
    found = set()
    for p in ctx.objects("Proposition"):
        for relation in ("consequence", "worth"):
            for link in ctx.links(p["id"], relation):
                if link["to"].startswith("name:"):
                    found.add(link["to"])
                shares = ((link["stated"].get("value") or {}).get("value") or {}).get("shares")
                if shares:
                    found.add(shares["name"])
                value = (link["stated"].get("value") or {}).get("value") or {}
                if "cash" in value and value.get("currency", "USD") != "USD":
                    found.add(name_id(series.dollar_pair(value["currency"])))
    return sorted(found)


def asof_of(ctx: Inputs) -> str:
    return ctx.object("portfolio")["stated"]["asof"]


def price_kind(ticker: str) -> str:
    """The data a ticker's prices are read from: a pair's hourly bars, a listing abroad's closes on its home market,
    a US listing's daily bars."""
    return "hourly" if series.is_pair(ticker) else "home" if series.is_abroad(ticker) else "bars"


def no_price(payload: Any) -> bool:
    """Whether a price source answered that it has no prices for what was asked: an answer, kept as data, that it
    lists no such thing (NOT_FOUND). A read that failed is no answer: its value waits, and is read again."""
    return isinstance(payload, dict) and payload.get("status") == "NOT_FOUND"


def unpriced(ticker: str, answer: Callable[[str, str], dict | None]) -> bool:
    """Whether a name has no price (ONTOLOGY.md 2.1; MATH.md 3.1), by what its sources answered: the one definition the
    betas, impact, check() and the store all take. Its price source answered that it has no prices for it (no_price); or
    it is a listing abroad quoted in a currency whose rate's source answered that it has no rates for it, so that it has
    no price in dollars. `answer(kind, subject)` is the newest reading of that kind the reader holds for the subject,
    its `status` and, for a listing abroad, its `currency`; None where it holds none: a name not yet read has its price
    to come. Such a name keeps its exposure, and has no betas and no price metric; its fund's own move carries its
    dollars."""
    if pseudo(ticker):
        return False
    kind = price_kind(ticker)
    got = answer(kind, ticker)
    if no_price(got):
        return True
    currency = (got or {}).get("currency") if kind == "home" else None
    return bool(currency) and currency != series.PRICE_CURRENCY and no_price(answer("hourly",
                                                                                    series.dollar_pair(currency)))


def price_series(ctx: Inputs, ticker: str, asof: str, *, required: bool = True) -> dict:
    """The daily bars of a ticker over the window, a currency or crypto pair's hourly bars, or a listing abroad's
    closes on its home market."""
    found = ctx.data(price_kind(ticker), ticker, covering=(series.lookback(asof), asof), required=required)
    return {"ticker": ticker, "ref": found}


def return_series(ctx: Inputs, ticker: str, asof: str, *, required: bool = True) -> dict:
    """What a ticker's returns read: its price series and, for a listed ticker, its
    dividends over the window. A pair pays none, and an index's values none. Not `required`, a series the store cannot
    read is empty."""
    found = price_series(ctx, ticker, asof, required=required)
    if not series.is_pair(ticker) and not series.is_index(ticker):
        found.update(dividend_inputs(ctx, ticker, (series.lookback(asof), asof), required=required))
    return found


def return_series_many(ctx: Inputs, tickers, asof: str, *, required: Callable[[str], bool] = lambda t: True
                       ) -> dict[str, dict]:
    """return_series for many tickers, keyed by ticker in the order given: each ticker's item is exactly what
    return_series gives it, its prices and its dividend records each read for all the tickers of a kind at once
    (Inputs.data_many), what those records read beyond themselves one ticker at a time, as return_series reads it."""
    tickers = list(dict.fromkeys(tickers))
    window = (series.lookback(asof), asof)
    out: dict[str, dict] = {}
    groups: dict[tuple[str, bool], list[str]] = defaultdict(list)
    for t in tickers:
        groups[(price_kind(t), bool(required(t)))].append(t)
    for (kind, needed), group in groups.items():
        for t, found in ctx.data_many(kind, group, covering=window, required=needed).items():
            out[t] = {"ticker": t, "ref": found}
    payers: dict[bool, list[str]] = defaultdict(list)
    for t in tickers:
        if not series.is_pair(t) and not series.is_index(t):
            payers[bool(required(t))].append(t)
    for needed, group in payers.items():
        found = ctx.data_many("dividends", group, covering=window, required=needed)
        for t, reads in dividend_reads_many(ctx, found, window, needed).items():
            out[t].update(reads)
    return {t: out[t] for t in tickers}


def dividend_inputs(ctx: Inputs, ticker: str, covering: tuple[str, str], *, required: bool = True) -> dict:
    """What a listing's dividends over a window read: its dividend records; for those paid in another currency than
    the price's, each currency's daily rate in the price's over the window (_rate); and, for a record Massive
    gives no split-adjusted amount, the listing's splits. Each is read as the dividends are: a read that fails makes
    the returns wait for it, so a known dividend is never left out (overnight item 13)."""
    return _dividend_reads(ctx, ctx.data("dividends", ticker, covering=covering, required=required), covering,
                           required)


def dividend_reads_many(ctx: Inputs, found: dict[str, dict], covering: tuple[str, str], required: bool
                        ) -> dict[str, dict]:
    """_dividend_reads for many listings' dividend records, keyed as `found`, each exactly what _dividend_reads gives
    it: what any of the records reads beyond itself, a listing's splits and a currency's rates, started for all of
    them side by side first (Inputs.prefetch), so that a call stands aside once for all of them."""
    splits, currencies = [], set()
    for t, ref in found.items():
        if not ref.get("data"):
            continue
        records = series.dividend_records(ctx.payload(ref))
        currencies |= {series.currency_of(r) for r in records} - {series.PRICE_CURRENCY}
        if any(r.get("split_adjusted_cash_amount") is None for r in records):
            splits.append(t)
    ctx.prefetch("splits", splits, covering, wait=False)
    ctx.prefetch("rates", sorted(currencies), covering, wait=False)
    return {t: _dividend_reads(ctx, ref, covering, required) for t, ref in found.items()}


def _dividend_reads(ctx: Inputs, found: dict, covering: tuple[str, str], required: bool) -> dict:
    """What a listing's dividend records read beyond themselves, once they are read (dividend_inputs)."""
    ticker = found["subject"]
    out = {"dividends": found}
    if not found.get("data"):
        return out
    records = series.dividend_records(ctx.payload(out["dividends"]))
    currencies = sorted({series.currency_of(r) for r in records} - {series.PRICE_CURRENCY})
    if currencies:
        out["rates"] = {c: _rate(ctx, c, covering, required) for c in currencies}
    if any(r.get("split_adjusted_cash_amount") is None for r in records):
        out["splits"] = ctx.data("splits", ticker, covering=covering, required=required)
    return out


def _notional_windows(filed: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Each currency other than the dollar the reports state a swap's or a future's notional under, and the window its
    daily rate is read over: the ten days to the latest of their dates."""
    days: dict[str, set[str]] = defaultdict(set)
    for payload in filed.values():
        if payload.get("effective_date"):
            for code in notional_codes(payload):
                days[code].add(payload["effective_date"])
    return {c: ((date.fromisoformat(min(d)) - timedelta(days=10)).isoformat(), max(d)) for c, d in sorted(days.items())}


def _notional_ahead(ctx: Inputs, filed: dict[str, Any]) -> None:
    """Start each notional currency's rate as soon as the reports that state it are read, side by side: the indexes the
    funds' derivatives reach wait on them (_notional_reads), and nothing else they need does."""
    for c, window in _notional_windows(filed).items():
        ctx.prefetch("rates", [c], window, wait=False)


def _notional_reads(ctx: Inputs, filed: dict[str, Any]) -> dict[str, dict]:
    """Each notional currency's daily rate over its window (_notional_windows, _rate), the last of which, on or before a
    report's date, is its rate then (series.rate_on)."""
    windows = _notional_windows(filed)
    for c, window in windows.items():  # read side by side, then each in turn
        ctx.prefetch("rates", [c], window, wait=False)
    return {c: _rate(ctx, c, window, required=False) for c, window in windows.items()}


def _notional_rates(m: dict, load) -> dict[str, dict[str, float]]:
    """What _notional_reads read: each currency's rate in dollars, by day."""
    return {c: series.reference_rates(load(ref)) if ref["kind"] == "rates" else
            series.daily_rates(load(ref), ref["retrieved_at"])
            for c, ref in (m.get("notional") or {}).items() if ref and ref.get("data")}


def _rate(ctx: Inputs, currency: str, covering: tuple[str, str], required: bool = True) -> dict:
    """A currency's daily rate in the price's over the window: the ECB's reference rate, one request for every
    currency and keyless (harness.venues.ecb); for a currency the ECB does not publish, its rate at each session's
    close in New York from the hourly rates of `C:{CCY}USD` (harness.venues.yahoo), and before they begin its daily
    fixing (harness.venues.fixings, series.daily_rates). A read that fails makes the returns wait for it; the ECB not
    answering is not the ECB not publishing the currency."""
    found = ctx.data("rates", currency, covering=covering, required=required)
    if found.get("data") and (ctx.payload(found) or {}).get("status") == "OK":
        return found
    return ctx.data("hourly", f"C:{currency}{series.PRICE_CURRENCY}", covering=covering, required=required)


def paid(item: dict, load, *, recurring: bool | None = None) -> dict[str, float]:
    """The dividends a return series read, by ex-dividend date, split-adjusted and in the price's currency; with
    `recurring`, only those its venue calls recurring, or only the others (series.dividends). A dividend whose rate on
    its ex-date or split adjustment the reads did not give is not left out: the returns wait (Waiting), and which
    dividend and what it lacks (series.dividends) is logged."""
    if not (item.get("dividends") or {}).get("data"):
        return {}
    rates = {c: series.reference_rates(load(ref)) if ref["kind"] == "rates" else
             series.daily_rates(load(ref), ref["retrieved_at"]) for c, ref in (item.get("rates") or {}).items()
             if ref.get("data")}
    splits = item.get("splits") or {}
    try:
        return series.dividends(load(item["dividends"]), rates, load(splits) if splits.get("data") else None,
                                recurring=recurring)
    except series.Incomplete as e:
        raise Waiting(f"returns: {e}") from None


def income(item: dict, load, since: str | None = None, until: str | None = None) -> series.Accrual:
    """The recurring income a listing's dividends accrue, day by day (series.Accrual): its recurring distributions going
    ex from `since` through `until`, the record read, as paid gives them, each with the frequency its venue gives it."""
    if not (item.get("dividends") or {}).get("data"):
        return series.Accrual()
    return series.Accrual(paid(item, load, recurring=True), series.cadence(load(item["dividends"])), since, until)


_CLOSES: OrderedDict = OrderedDict()  # each series' closes, by its payload's content, read only (closes)
_CLOSES_KEPT = 512  # about 25 kB a series of two years: a portfolio's names, its proxies and its funds' references
_CLOSES_LOCK = threading.Lock()


def closes(item: dict, load) -> dict[str, float]:
    """A series' closes by session: a listing abroad's in its own currency, by its own session's date. They follow from
    the payload, when it was read and how the series is read, so they are kept by those, and the same dict is handed
    to every reader: it is read only, as the payload is."""
    ref = item["ref"]
    if not ref.get("data"):  # a series the store could not read
        return {}
    how = "pair" if series.is_pair(item["ticker"]) else "abroad" if series.is_abroad(item["ticker"]) else "daily"
    key = (ref.get("sha256"), ref["retrieved_at"], how)
    if key[0] is not None:
        with _CLOSES_LOCK:
            if key in _CLOSES:
                _CLOSES.move_to_end(key)
                return _CLOSES[key]
    payload, fetched = load(ref), ref["retrieved_at"]
    if how == "pair":
        got = series.session_closes(payload, fetched)
    elif how == "abroad":  # each close of a session complete when read
        got = {r["session"]: float(r["c"]) for r in (payload or {}).get("results") or []}
    else:
        got = series.daily_closes(payload, fetched)
    if key[0] is not None:
        with _CLOSES_LOCK:
            _CLOSES[key] = got
            while len(_CLOSES) > _CLOSES_KEPT:
                _CLOSES.popitem(last=False)
    return got


def reference_type(payload: Any) -> str | None:
    if not answered(payload):
        return None
    return (payload.get("results") or {}).get("type")


def kind_of(reference_payload: Any) -> str:
    """A ticker is a name, a fund, or other."""
    t = reference_type(reference_payload)
    return "name" if t in NAME_TYPES else "fund" if t in FUND_TYPES else "other"


# ---------------------------------------------------------------- a current price


def read_date(retrieved_at: str) -> str:
    """The New York date a fetch was made on."""
    return datetime.fromisoformat(retrieved_at).astimezone(series.NY).date().isoformat()


def is_today(asof: str) -> bool:
    """Whether the as-of is New York's date now. It decides what a value reads of the present: each latest mark, an
    option's quote now, and the day a contract is valued on; so a value that asks it, where the as-of is not yet past
    and the answer can still change, reads TODAY (harness.touches)."""
    today = touches.today()
    if asof >= today:
        touches.read(touches.TODAY)
    return today == asof


def unlisted(payload: Any, ticker: str) -> str | None:
    """Why a ticker has no prices, where its source does not list it, in plain words, the source's own text left to
    the record of its read. A name a
    fund is exposed to can be identified and have no price (a listing abroad its source does not cover): it keeps its
    exposure, and has no price."""
    if no_price(payload):
        return f"its source lists no prices for it, so {ticker} has no prices"
    return None


def current_inputs(ctx: Inputs, ticker: str, asof: str, at: str | None = None) -> dict:
    """What a ticker's current price reads: its series to the as-of, and, when the series was read on the
    as-of date itself, the as-of is today and its latest mark: a stock's snapshot, a pair's latest trade or rate. With
    `at`, the mark the store held at that moment, in place of the latest."""
    item = price_series(ctx, ticker, asof)
    why = unlisted(ctx.payload(item["ref"]), ticker) if item["ref"].get("data") else None
    if why:
        raise Unavailable(why)
    today = item["ref"].get("data") is not None and read_date(item["ref"]["retrieved_at"]) == asof
    out = {"ticker": ticker, "asof": asof, "series": item, "today": today}
    if today:
        kind = series.mark_kind(ticker)
        out[kind] = ctx.data(kind, ticker, covering=(asof, asof), required=False, at=at)
    if series.is_abroad(ticker):  # its own sessions, on its home market, which New York's do not count
        return out
    if ticker != CALENDAR:  # the market's sessions, which a move is measured across; the calendar's own reads them
        try:
            out["calendar"] = ctx.value("calendar", "market", ref=True)
        except Unavailable:
            pass  # without them, the listing's own sessions
    return out


# The market's calendar: its sessions are the days the S&P 500's own fund trades, read from SPY's daily bars, and the
# as-of when SPY has a mark on it, so its session has opened. A session opens at the start of the extended hours, as a
# daily bar and a latest mark span them.
CALENDAR = "SPY"


def _calendar_inputs(ctx: Inputs, subject: str) -> dict:
    asof = asof_of(ctx)
    bars = ctx.data("bars", CALENDAR, covering=(series.lookback(asof), asof))
    out = {"asof": asof, "bars": bars}
    if bars.get("data") and read_date(bars["retrieved_at"]) == asof:
        out["snapshot"] = ctx.data("snapshot", CALENDAR, covering=(asof, asof), required=False)
    return out


def _calendar_compute(m: dict, load) -> dict:
    """The latest session to open, on or before the as-of, and the session before it."""
    sessions = set(series.daily_closes(load(m["bars"]), m["bars"]["retrieved_at"]) if m["bars"].get("data") else {})
    if (m.get("snapshot") or {}).get("data"):
        mark = series.snapshot_mark(load(m["snapshot"]))
        if mark and mark["time"][:10] == m["asof"]:
            sessions.add(m["asof"])
    days = sorted(d for d in sessions if d <= m["asof"])
    return {"session": days[-1] if days else None, "previous": days[-2] if len(days) > 1 else None}


CALENDAR_DERIVATION = Derivation("calendar", 1, "Portfolio", _calendar_inputs, _calendar_compute,
                                 subject_of=lambda store, entity: "market")


def _traded_since(out: dict, day: str) -> bool:
    """Whether a price is from after the close of `day`: a mark after that session's close in New York (16:00, or
    13:00 on an early close), or a close of a later session."""
    if out.get("time"):
        d = date.fromisoformat(day)
        return datetime.fromisoformat(out["time"]) > datetime.combine(d, series.session_close(d), series.NY)
    return out["session"] > day


def current_compute(m: dict, load) -> dict | None:
    """The current price with its time, what it is, and its session; the close it is measured against; and its move
    from that close, in dollars and as a fraction. When the as-of is today the price is the latest mark, whose session
    is its New York date; a today with no mark reads the latest completed close, and a past as-of that day's close.
    The move is the price against the close of the session before the latest one to open, by the market's calendar:
    during a session, that day's move; after it, until the next opens, that session's whole move; and a listing that
    has not traded since that close has not moved, however long ago it last traded. Without the calendar, the close
    is the one before the price's own session."""
    found = closes(m["series"], load)
    mark = None
    if m["today"]:
        if series.is_pair(m["ticker"]):  # its latest trade or rate, or, where none was read, its latest hourly bar
            if (m.get("last_trade") or {}).get("data"):
                mark = series.trade_mark(load(m["last_trade"]))
            mark = mark or series.hourly_mark(load(m["series"]["ref"]))
        elif series.is_abroad(m["ticker"]) and (m.get("home_mark") or {}).get("data"):
            mark = series.home_mark(load(m["home_mark"]))
        elif (m.get("snapshot") or {}).get("data"):
            mark = series.snapshot_mark(load(m["snapshot"]))
    if mark is not None:
        out = {**mark, "session": mark["time"][:10]}
    else:
        days = sorted(d for d in found if d <= m["asof"])
        if not days:
            return None
        out = {"price": found[days[-1]], "time": None, "session": days[-1], "from": "close"}
    calendar = load(m["calendar"]) if m.get("calendar") else {}
    previous = calendar.get("previous")
    if previous:
        before = sorted(d for d in found if d <= previous)
        if not before:
            return out
        if not _traded_since(out, previous):  # no trade since that close: no move
            return {**out, "previous_close": out["price"], "previous_session": before[-1], "session_change": 0.0,
                    "session_move": 0.0}
    else:
        before = sorted(d for d in found if d < out["session"])
    if before:
        out.update(previous_close=found[before[-1]], previous_session=before[-1],
                   session_change=out["price"] - found[before[-1]],
                   session_move=out["price"] / found[before[-1]] - 1.0 if found[before[-1]] else None)
    return out


# ---------------------------------------------------------------- a Name's price and reference


def dollar_inputs(ctx: Inputs, ticker: str, asof: str, at: str | None = None) -> dict:
    """What a price in dollars reads: its current price's, and for a listing abroad quoted in another currency, that
    currency's rate in dollars, at each price's moment."""
    out = current_inputs(ctx, ticker, asof, at=at)
    currency = (ctx.payload(out["series"]["ref"]) or {}).get("currency") if series.is_abroad(ticker) and \
        out["series"]["ref"].get("data") else None
    if currency and currency != series.PRICE_CURRENCY:
        out["rate"] = ctx.data("hourly", series.dollar_pair(currency), covering=(series.lookback(asof), asof))
    return out


def dollar_compute(m: dict, load) -> dict | None:
    """A price in dollars, its close before, and its move: current_compute's, a listing abroad's converted."""
    found = current_compute(m, load)
    if found is not None and series.is_abroad(m["ticker"]):
        found = _in_dollars(found, m, load)
    return found


def _price_inputs(ctx: Inputs, nid: str) -> dict:
    kind = pseudo(ticker_of(nid))
    if kind:
        return {"name": nid, "pseudo": kind}
    return {"name": nid, **dollar_inputs(ctx, ticker_of(nid), asof_of(ctx))}


def _in_dollars(found: dict, m: dict, load) -> dict:
    """A listing abroad's price in dollars, as a holding's dollars read it: its price in its own currency, and that
    times its currency's rate in dollars struck at the price's moment, its close before likewise."""
    payload = load(m["series"]["ref"]) or {}
    currency = payload.get("currency") or series.PRICE_CURRENCY
    out = {**found, "local": found.get("price"), "currency": currency}
    if currency == series.PRICE_CURRENCY:
        return out
    rates = series.rate_index(load(m["rate"])) if (m.get("rate") or {}).get("data") else None
    moments = {r["session"]: r["moment"] for r in payload.get("results") or []}

    def at(price, moment, day):
        """At the rate struck at the price's moment, or before the pair's hourly rates, its own date's fixing."""
        rate = (series.rate_for(rates, moment, day) or {}).get("price") if rates and price is not None and moment \
            else None
        return (price * rate, rate) if rate else (None, None)

    moment = found.get("time") or moments.get(found.get("session"))
    out["price"], out["rate"] = at(found.get("price"), moment, found.get("session") or (moment or "")[:10] or None)
    if found.get("previous_close") is not None:
        before, _ = at(found["previous_close"], moments.get(found.get("previous_session")),
                       found.get("previous_session"))
        out["previous_close"] = before
        out["session_change"] = out["price"] - before if before is not None and out["price"] is not None else None
        out["session_move"] = out["price"] / before - 1.0 if before and out["price"] is not None else None
    return out


def _price_compute(m: dict, load) -> dict:
    if m.get("pseudo") == "cash":
        return {"prices": {"price": 1.0, "session": None, "time": None, "from": "cash"}}
    if m.get("pseudo"):
        return {"prices": None}
    return {"prices": dollar_compute(m, load)}


PRICE = Derivation("price", 7, "Name", _price_inputs, _price_compute, fields=("prices",))


def reference_ticker(nid: str) -> str | None:
    """The ticker whose reference says what a name is; None for cash, which is cash in its currency, a pseudo ticker,
    and a listing abroad named `{ISIN}:{country}`, which no reference names (one named by its symbol has its own
    market's)."""
    if nid.startswith("cash:") or pseudo(ticker_of(nid)):
        return None
    if series.is_abroad(ticker_of(nid)) and not series.symbol_abroad(ticker_of(nid)):
        return None
    return ticker_of(nid)


def _reference_inputs(ctx: Inputs, nid: str) -> dict:
    if nid.startswith("cash:"):
        return {"name": nid, "pseudo": "cash"}
    kind = pseudo(ticker_of(nid))
    if kind:
        return {"name": nid, "pseudo": kind}
    ticker = reference_ticker(nid)
    if ticker is None:
        return {"name": nid, "abroad": True}
    asof = asof_of(ctx)
    return {"name": nid, "reference": ctx.data("ticker_ref", ticker, covering=(asof, asof))}


def company_of(payload: Any) -> dict | None:
    """What a ticker is, from its reference: its company's name, or the fund's; what it does, in the reference's own
    words; and its industry, its SIC code's description. None where the reference holds nothing."""
    if not reference_type(payload):
        return None
    found = payload.get("results") or {}
    return {"name": found.get("name"), "does": found.get("description"), "industry": found.get("sic_description")}


def _reference_compute(m: dict, load) -> dict:
    if m.get("pseudo"):
        return {"type": m["pseudo"], "company": None}
    if m.get("abroad"):
        return {"type": None, "company": None}
    payload = load(m["reference"])
    results = (payload.get("results") or {}) if reference_type(payload) else {}
    return {"type": results.get("type"), "company": company_of(payload)}


REFERENCE = Derivation("reference", 4, "Name", _reference_inputs, _reference_compute,
                       fields=("type", "company"))


# ---------------------------------------------------------------- holdings and V


def _holdings_inputs(ctx: Inputs, subject: str) -> dict:
    portfolio = ctx.object("portfolio")
    holds = ctx.links("portfolio", "holds")
    asof = portfolio["stated"]["asof"]
    held = [security_of(h) for h in holds if not h["to"].startswith("market:") and not pseudo(security_of(h))]
    # what the look-through and the betas read of a holding needs its ticker alone: started now, kept where it is read
    ctx.prefetch("ticker_ref", held, (asof, asof), wait=False)
    _series_ahead(ctx, held, asof)
    for kind in ("bars", "hourly"):  # every holding's series side by side, then each price reads its own
        ctx.prefetch(kind, [t for t in held if (kind == "hourly") == series.is_pair(t)]
                     + ([CALENDAR] if kind == "bars" else []), (series.lookback(asof), asof), stage="holdings")
    if is_today(asof):  # and each one's latest mark, which a price as of today reads
        ctx.prefetch("snapshot", [t for t in held if not series.is_pair(t)], (asof, asof), stage="holdings")
        ctx.prefetch("last_trade", [t for t in held if series.is_pair(t)], (asof, asof), stage="holdings")
    prices: dict[str, dict] = {}
    given: dict[str, dict] = {}  # for a holding given in dollars or as a weight, the price the store held then
    for h in holds:
        ticker = security_of(h)
        if ticker == CASH:
            continue
        if h["to"].startswith("market:"):
            result = ctx.value("market_settlement", h["to"])
            settled = result["output"].get("settlement") or {}
            if settled.get("settled"):
                mark, at, source, basis = settled.get("result"), settled.get("at"), "settlement", result["basis"]
            else:
                quote = ctx.value("market_price", h["to"])
                got = quote["output"]
                mark, at, source, basis = got.get("price"), got.get("quoted_at"), "market", quote["basis"]
            if mark is None:
                raise Unavailable(f"{h['to']} has no current price for its held contract")
            price = mark if h["stated"]["side"] == "yes" else 1.0 - mark
            prices[h["id"]] = {"output": {"prices": {"price": price, "from": source, "time": at}}, "basis": basis}
            continue
        # Every holding is priced, or the portfolio is not read: a holding given in dollars or as a weight is the shares
        # it bought then, worth what its price makes them now (MATH.md 3.1), so with no price it has no value to carry.
        prices[h["id"]] = ctx.value("price", name_id(ticker))
        if ((prices[h["id"]].get("output") or {}).get("prices") or {}).get("price") is None:
            raise Unavailable(f"{ticker} has no price on or before {asof}")
        if "quantity" not in h["stated"] and not pseudo(ticker):
            try:
                given[h["id"]] = dollar_inputs(ctx, ticker, asof, at=ctx.given_at(h["id"]))
            except Unavailable:
                pass
    return {"portfolio": portfolio, "holds": holds, "prices": prices, "given": given}


def _holdings_compute(m: dict, load) -> dict:
    total = m["portfolio"]["stated"].get("total")
    rows: dict[str, dict] = {}
    for h in m["holds"]:
        stated = h["stated"]
        given = next(k for k in ("quantity", "dollars", "weight", "cash") if k in stated)
        cash = security_of(h) == CASH
        current = {} if cash else (m["prices"][h["id"]].get("output") or {}).get("prices") or {}
        price = 1.0 if cash else current.get("price")
        then = None  # the price the store held when a holding in dollars or a weight was given
        if given in ("quantity", "cash"):  # cash's amount is in its currency, as many units at the currency's rate
            dollars = stated[given] * price
            quantity = stated[given]
        else:
            worth = stated["dollars"] if given == "dollars" else stated["weight"] * total  # what it was worth then
            if h["id"] in m["given"]:
                then = (dollar_compute(m["given"][h["id"]], load) or {}).get("price")
            if then and price:  # its quantity implied then, valued now
                quantity, dollars = worth / then, worth * (price / then)
            else:
                dollars = worth
                quantity = dollars / price if price else None
        before = current.get("previous_close")
        rows[h["id"]] = {"name": held_name(h), "security": security_of(h), "price": price, "session": current.get("session"),
                         **({"cash": h["to"].split(":", 1)[1]} if h["to"].startswith("cash:") else {}),
                         "price_time": current.get("time"), "price_from": current.get("from"),
                         "previous_close": before, "session_move": 0.0 if cash else current.get("session_move"),
                         "session_move_dollars": 0.0 if cash or current.get("session_change") == 0.0 else (
                             dollars - quantity * before if quantity is not None and before else None),
                         "stated": given, "dollars": dollars, "quantity": quantity,
                         **({"contract": True, "side": stated["side"]} if h["to"].startswith("market:") else {}),
                         **({"given_price": then} if then else {})}
    value = sum(r["dollars"] for r in rows.values())
    for r in rows.values():
        r["weight"] = r["dollars"] / value if value else None
    moved = [r["session_move_dollars"] for r in rows.values()]
    change = sum(moved) if moved and None not in moved else None
    return {"V": value, "holds": rows, "session_move_dollars": change,
            "session_move": change / (value - change) if change is not None and value - change else None}


def _holding_amounts(output: dict, link_id: str) -> dict:
    """The two amounts a holding was not given, at its current price."""
    row = output["holds"].get(link_id) or {}
    return {k: row[k] for k in ("quantity", "dollars", "weight") if k in row and k != row.get("stated")}


HOLDINGS = Derivation("holdings", 11, "Portfolio", _holdings_inputs, _holdings_compute, fields=("value",),
                      pick=lambda out, pid: {"value": out["V"]},
                      subject_of=lambda store, entity: "portfolio", stage="holdings",
                      extra=(("holds", ("quantity", "dollars", "weight"), _holding_amounts),))


# ---------------------------------------------------------------- look-through


_ROWS: OrderedDict[int, tuple[Any, tuple[dict, ...]]] = OrderedDict()  # each payload's rows, by the payload itself
_ROWS_LOCK = threading.Lock()
ROWS_KEPT = 64  # the payloads whose rows are kept: a portfolio's funds' holdings and reports, several times over


def _valid_rows(payload: Any) -> tuple[dict, ...]:
    """A fund's constituent rows. A row identical in every field to an earlier one is that
    row reported twice, and counts once (a fund of bonds can return each bond twice, doubling its weights); rows
    that share a ticker but differ in any field are distinct holdings, such as several bonds under one ticker.

    Kept for each payload while it is read again: an inputs function is given the same parsed payload on each pass
    (Engine.read), read-only like everything a derivation reads, so its rows are told apart once and not every pass.
    The entry holds the payload, so the object it is kept by is never another's."""
    if not answered(payload):
        return ()
    with _ROWS_LOCK:
        held = _ROWS.get(id(payload))
        if held is not None and held[0] is payload:
            _ROWS.move_to_end(id(payload))
            return held[1]
    seen: set[str] = set()
    rows = []
    for r in payload.get("results") or []:
        if not isinstance(r, dict):
            continue
        key = canonical(r)
        if key not in seen:
            seen.add(key)
            rows.append(r)
    kept = tuple(rows)
    with _ROWS_LOCK:
        _ROWS[id(payload)] = (payload, kept)
        _ROWS.move_to_end(id(payload))
        while len(_ROWS) > ROWS_KEPT:
            _ROWS.popitem(last=False)
    return kept


def _targets(row: dict) -> list[tuple[dict, float]]:
    """The companies a fund's row is exposed to, by the rows' contract (harness.derive.rows: `exposure`, signed, per
    dollar of the net assets it is stated against; `payoff`; `to`), each as resolution reads it, with its exposure per
    dollar of the fund: a holding's company, or a derivative's referenced company, at the row's exposure; an index's
    companies, where the rows establish them, at the row's exposure times each one's weight in the index. None for
    money (cash, and a currency, which moves the fund through its own response), for an index whose companies are not
    established, for a row whose company is not identified, and for a row whose exposure is not stated (an option's,
    whose delta its terms give). A derivative's reference is a company only where it is on equity (MATH.md 3.1), which
    the rows decide (harness.derive.rows.on_equity): a row on anything else carries no company."""
    x = row.get("exposure")
    if not x or row.get("payoff") == "cash":
        return []
    to = row.get("to") or {}
    dated = {"effective_date": row["effective_date"]} if row.get("effective_date") else {}
    if "company" in to:  # a holding is resolved by its own row, which carries every identifier its source gives it
        return [(row if row.get("payoff") == "holding" else {**to["company"], **dated}, x)]
    if "index" in to:
        return [({**{k: v for k, v in c.items() if k != "weight"}, **dated}, x * c["weight"])
                for c in to.get("companies") or [] if isinstance(c, dict) and isinstance(c.get("weight"), (int, float))]
    return []


def _is_listed(row: dict) -> bool:
    """A constituent resolves to a company only when its fund's holdings class it as a share (harness.derive.assets):
    ETF Global's Equity or Real Estate, in any case, other than a warrant, a right, or a mutual fund, or N-PORT's
    common or preferred equity. A bond's or a coin's identifiers can lead to a listed company or fund (a when-issued
    Treasury's ticker can be a listed company's, and a coin's a listed fund's). Every other kind, or none, maps to the
    fund's residual, or to its cash."""
    return labelled(row) == "equity"


def _resolvable(row: dict) -> bool:
    """A row whose company identifiers can confirm: a listed share, with a weight, named by a ticker, an ISIN, or a
    CUSIP."""
    return row.get("weight") is not None and _is_listed(row) and \
        bool(row.get("constituent_ticker") or row.get("isin") or row.get("cusip"))


# ---------------------------------------------------------------- what a fund holds


@cache
def _places() -> dict[str, str]:
    """Each country's short name by its two-letter ISO 3166 code, from the IANA time zone database's iso3166.tab,
    which the tzdata package ships."""
    text = files("tzdata").joinpath("zoneinfo", "iso3166.tab").read_text(encoding="utf-8")
    rows = (line.split("\t") for line in text.splitlines() if line and not line.startswith("#"))
    return {row[0]: row[1] for row in rows if len(row) == 2}


def listed_in(code: str | None) -> str | None:
    """Where a holding is listed, the country of its exchange, by name; a code the table lacks reads as given."""
    return _places().get(code, code) if code else None


def us_composites(payload: Any) -> frozenset[str]:
    """The composite FIGIs of the US listings Massive lists, each a listing in the US."""
    if not answered(payload):
        return frozenset()
    return frozenset(r["composite_figi"] for r in payload.get("results") or []
                     if isinstance(r, dict) and r.get("composite_figi") and r.get("locale") == "us")


def where_listed(row: dict, us: frozenset[str]) -> str | None:
    """Where a holding is listed, decided by its identifiers where they decide it: a row whose FIGI is a US listing's
    composite FIGI is listed in the US, whatever country the fund's data gives its exchange; any other row is listed
    where the fund's data says. Only identifiers are compared, never names."""
    return "US" if row.get("figi") and row["figi"] in us else \
        row.get("country_of_exchange") or (row.get("listing") or {}).get("country") or None


def _group(rows: list[tuple[dict, float]], key, label, dollars: float) -> list[dict]:
    """Rows summed by a key, each group named as its heaviest row names it, largest weight first."""
    found: dict[Any, dict] = {}
    for row, w in rows:
        k = key(row)
        g = found.setdefault(k, {**label(row), "weight": 0.0, "heaviest": abs(w)})
        g["weight"] += w
        if abs(w) > g["heaviest"]:
            g.update({**label(row), "heaviest": abs(w)})
    out = []
    for g in sorted(found.values(), key=lambda g: -abs(g["weight"])):
        g.pop("heaviest")
        out.append({**g, "dollars": dollars * g["weight"]})
    return out


def _divisions(rows: list[tuple[dict, float]], dollars: float, us: frozenset[str], kinds: Kinds,
               inner: Callable[[dict], list[dict] | None] = lambda r: None) -> dict:
    """Rows divided by where they are listed and by kind of asset: each place by name, null where a row names no
    exchange; each kind in the one set of kinds every source's are put in (harness.derive.assets). A row that is a fund
    divides by kind as that fund's own holdings do (`inner`, its by_kind), so a bond fund a fund holds is debt."""
    split: list[tuple[dict, float]] = []
    for r, w in rows:
        division = [d for d in inner(r) or [] if d["weight"]]
        total = sum(d["weight"] for d in division)
        split += [({**r, "_kind": d["kind"]}, w * d["weight"] / total) for d in division] if total else [(r, w)]
    kind = lambda r: r.get("_kind") or kinds.of(r)  # noqa: E731
    return {"by_listing": _group(rows, lambda r: where_listed(r, us),
                                 lambda r: {"listed": listed_in(where_listed(r, us))}, dollars),
            "by_kind": _group(split, kind, lambda r: {"kind": kind(r)}, dollars)}


def unstated_kind(reference: Any, held: Iterable[str], left: float) -> str:
    """The kind of what a fund's rows leave unstated, the fund's dollars less theirs. A commodity pool's is its
    commodity exposure: the collateral behind futures its rows state at their marked value. Where the rows state more
    than the fund and it holds derivatives, the difference is their financing: a source states a swap's asset leg at
    its notional and its financing leg at no value (read live: a fund at three times an index, its asset legs 214% of
    it), so the financing is a derivative's. Any other is other, which nothing says."""
    if commodity_fund(reference):
        return "commodity"
    return "derivative" if left < 0 and "derivative" in set(held) else "other"


def commodity_fund(reference: Any) -> bool:
    """Whether a fund's own record classes it as a commodity trust or pool (harness.derive.assets.COMMODITY_FUND_SIC)."""
    record = reference.get("results") if answered(reference) else None
    return str((record or {}).get("sic_code") or "") == COMMODITY_FUND_SIC


def fund_kinds(kinds: Kinds, reference: Any, payload: Any = None) -> Kinds:
    """The kinds a fund's rows take: in a commodity trust or pool, a row nothing labels is a commodity; and where its
    rows (`payload`, fund_rows) say a row that names nothing and is stated in dollars is cash, as its report decides
    (harness.derive.rows.cash_and_one_reference), it is cash."""
    if commodity_fund(reference):
        kinds = replace(kinds, unlabelled="commodity")
    unnamed = payload.get("unnamed") if isinstance(payload, dict) else None
    return replace(kinds, unnamed=unnamed["kind"]) if isinstance(unnamed, dict) and unnamed.get("kind") else kinds


def _placed(row: dict, us: frozenset[str], resolved: Callable[[dict], dict] | None) -> dict:
    """A row with where it is listed and its ticker, where its own fields give neither and its identifiers resolve
    (a Form N-PORT report's rows state no exchange or ticker): a listing in the US, or its home market's country."""
    if where_listed(row, us) or resolved is None or not _resolvable(row):
        return row
    found = resolved(row)
    ticker, market = found.get("ticker"), found.get("market")
    place = "US" if market in ("stocks", "otc", "funds") else _country_of(ticker) if market == "home" and ticker \
        else None
    if not place:
        return row
    return {**row, "country_of_exchange": place,
            **({"constituent_ticker": ticker} if place == "US" and not row.get("constituent_ticker") else {})}


def what_it_holds(fund: str, payload: Any, references: dict, dollars: float, priced: set[str],
                  us: frozenset[str] = frozenset(), resolved: Callable[[dict], dict] | None = None,
                  kinds: Kinds | None = None, funds: dict[str, list[dict]] | None = None) -> dict | None:
    """What a fund holds, from its holdings, every row with a weight counted whether or not the look-through prices
    it: each holding of at least the floor by name, with its ticker as the fund gives it, where it is listed, its kind
    of asset, its industry where its company is confirmed, its weight, and its dollars of the portfolio's, and the name
    the store prices it as; the rest summed, with how many and how they divide; and the whole fund divided by where its
    holdings are listed and by kind of asset, in the one set of kinds every source's words are put in
    (harness.derive.assets), which is the only classification of a holding given. A holding's name is its company's, from the reference of the US listing
    its identifiers confirm (`resolved`), and the fund's own otherwise. None where its holdings cannot be read."""
    kinds = fund_kinds(kinds or Kinds(), references.get(fund), payload)

    def inner(r: dict) -> list[dict] | None:
        """How a row that is a fund divides by kind, as that fund's own holdings do (`funds`, by its ticker)."""
        if not funds or resolved is None or not _resolvable(r):
            return None
        found = resolved(r)
        return funds.get(found["ticker"]) if found.get("kind") == "fund" else None
    rows = [(_placed(r, us, resolved), r["weight"]) for r in _valid_rows(payload) if r.get("weight") is not None]
    if not rows:
        return None
    named, rest = [], []
    for r, w in sorted(rows, key=lambda x: -abs(x[1])):
        if abs(w) < FLOOR:
            rest.append((r, w))
            continue
        found = resolved(r) if resolved is not None and _resolvable(r) else {}
        listing = found.get("ticker")
        known = company_of(references.get(listing)) if listing else None
        nid = name_id(listing) if listing and name_id(listing) in priced else None
        named.append({"name": (known or {}).get("name") or r.get("constituent_name"),
                      "ticker": r.get("constituent_ticker"),
                      "listed": listed_in(where_listed(r, us)), "kind": max(inner(r) or [{"kind": kinds.of(r), "weight": 1.0}],
                                  key=lambda d: d["weight"])["kind"],
                      "industry": (known or {}).get("industry"),
                      "weight": w, "dollars": dollars * w, "priced_as": nid})
    rest_weight = sum(w for _, w in rest)
    # the whole fund by kind: its rows, and what they leave unstated, by the kind that is (unstated_kind); by where
    # its holdings are listed, its rows
    left = 1.0 - sum(w for _, w in rows)
    unstated = [({"_kind": unstated_kind(references.get(fund), {kinds.of(r) for r, _ in rows}, left)}, left)] \
        if left else []
    return {"fund": fund, "holdings_asof": payload.get("effective_date"), "count": len(rows),
            "weights_sum": sum(w for _, w in rows), "dollars": dollars, "named": named,
            "rest": {"count": len(rest), "weight": rest_weight, "dollars": dollars * rest_weight,
                     **_divisions(rest, dollars, us, kinds, inner)},
            **_divisions(rows, dollars, us, kinds, inner),
            "by_kind": _divisions(rows + unstated, dollars, us, kinds, inner)["by_kind"]}


class _Reads:
    """What the look-through reads while its inputs are gathered, by kind and subject. A read the store has been asked
    for is read; any other is asked for, and comes back empty this pass: each pass's asks are fetched side by side
    before the next, so the resolution of every row reads, in the end, all it needs, fetched as few times as it can.
    A read that fails past its retries raises, and the look-through waits for it: what the portfolio owns is never
    told from a partial picture (TOOLS.md 5). A kind with no client here (its key not set) is read as having nothing."""

    def __init__(self, ctx: Inputs, asof: str):
        self.ctx, self.asof = ctx, asof
        self.refs: dict[str, dict[str, dict]] = defaultdict(dict)
        self._payloads: dict[tuple[str, str], Any] = {}
        self.wanted: dict[str, set[str]] = defaultdict(set)
        self.again: dict[str, set[str]] = defaultdict(set)  # what was read again from its source in this read

    def covering(self, kind: str) -> tuple[str, str]:
        return (series.lookback(self.asof), self.asof) if kind == "bars" else (self.asof, self.asof)

    def fetch_all(self, asked: dict[str, list]) -> None:
        """Each kind's subjects, every kind's reads started together (Store.prefetch), then each kept as `fetch`
        keeps it."""
        for kind, subjects in asked.items():
            todo = [s for s in dict.fromkeys(subjects) if s and s not in self.refs[kind]]
            self.ctx.prefetch(kind, todo, self.covering(kind), wait=False)
        for kind, subjects in asked.items():
            self.fetch(kind, subjects)

    def fetch(self, kind: str, subjects, *, about: str | None = None) -> None:
        """Each subject's data of `kind`, fetched side by side."""
        todo = [s for s in dict.fromkeys(subjects) if s and s not in self.refs[kind]]
        if not todo:
            return
        self.ctx.prefetch(kind, todo, self.covering(kind), stage="funds", about=about)
        self.refs[kind].update(self.ctx.data_many(kind, todo, covering=self.covering(kind), required=False))

    def read(self, kind: str, subject: str) -> Any:
        if subject not in self.refs[kind]:
            self.wanted[kind].add(subject)
            return None
        if (kind, subject) not in self._payloads:
            ref = self.refs[kind][subject]
            self._payloads[(kind, subject)] = self.ctx.payload(ref) if ref.get("data") else None
        return self._payloads[(kind, subject)]

    def settle(self, work: Callable[[Lookup], Any]) -> Any:
        """`work` run until it reads only what the store has been asked for, and no mapping it read, kept from an
        earlier day, names what no longer resolves (harness.derive.identity.unresolved): such a mapping is read again,
        once."""
        while True:
            self.wanted.clear()
            out = work(Lookup(self.read, self.asof))
            if any(self.wanted.values()):
                self.fetch_all({kind: sorted(subjects) for kind, subjects in self.wanted.items()})
                continue
            stale = self._stale()
            if not stale:
                return out
            for kind, subjects in stale.items():
                self.ctx.read_again(kind, subjects, self.covering(kind))
                for subject in subjects:
                    self.again[kind].add(subject)
                    self._payloads.pop((kind, subject), None)
                self.refs[kind].update(self.ctx.data_many(kind, subjects, covering=self.covering(kind),
                                                          required=False))

    def _stale(self) -> dict[str, list[str]]:
        """The mappings read, kept from before today and not read again in this read, that no longer resolve."""
        look = Lookup(self.read, self.asof)
        asked = self.refs["ticker_ref"].__contains__
        out: dict[str, list[str]] = {}
        for kind in MAPPINGS:
            for subject, ref in list(self.refs[kind].items()):
                if subject in self.again[kind] or not ref.get("data") or read_today(ref["retrieved_at"]):
                    continue
                if unresolved(kind, self.read(kind, subject), look, asked):
                    out.setdefault(kind, []).append(subject)
        return out


def _series_ahead(ctx: Inputs, tickers, asof: str) -> None:
    """Start what each listing's returns over the window read (harness.derive.history.series_inputs): a US listing's
    bars and dividends, a listing abroad's closes at home. Its identity is all they need, so they are read while the
    rest of the look-through is, and the betas find them read (Store.prefetch)."""
    window = (series.lookback(asof), asof)
    listed = sorted({t for t in tickers if t and us_ticker(t)})
    ctx.prefetch("bars", listed, window, wait=False, later=True)
    ctx.prefetch("dividends", listed, window, wait=False, later=True)
    ctx.prefetch("home", sorted({t for t in tickers if t and series.is_abroad(t)}), window, wait=False, later=True)


def _listings_ahead(ctx: Inputs, rows: Iterable[dict], asof: str) -> None:
    """Start the reads of each listing a fund's shares trade under, by which the portfolio's weights class each company
    by industry and home first (harness.derive.owned): which listings follows from the rows alone, by the rule the
    weights read them by, so they are read while the look-through is, and the weights find them read. They wait behind
    none of the look-through's own reads."""
    from harness.derive.owned import symbols_of  # it reads this module's as-of
    ctx.prefetch("listing_sector", [f"{s}@{asof[:7]}" for s in symbols_of(r for r in rows if _is_listed(r))],
                 (asof, asof), wait=False, later=True)


def _rates_ahead(ctx: Inputs, rows: Iterable[dict], asof: str) -> None:
    """Start each currency's rates over the window for every currency other than the dollar a fund's rows are in, its
    money and the listings abroad it holds: the rates the betas convert those listings' closes at and fit each
    currency's response on (harness.derive.channels), read while the rest of the first read is, so that the betas
    find them read. One no value asks for stays with the read-ahead (Store.prefetch)."""
    codes = {str(r.get("currency_traded") or r.get("currency") or "").strip().upper() for r in rows}
    pairs = sorted(series.dollar_pair(c) for c in codes if re.fullmatch("[A-Z]{3}", c) and c != series.PRICE_CURRENCY)
    ctx.prefetch("hourly", pairs, (series.lookback(asof), asof), wait=False, later=True)


def _weighted(ref: dict, ctx: Inputs) -> bool:
    """Whether a fund's holdings were read and give a weight for at least one."""
    return bool(ref.get("data")) and any(r.get("weight") is not None for r in _valid_rows(ctx.payload(ref)))


def _lookthrough_inputs(ctx: Inputs, subject: str) -> dict:
    """What the look-through reads: the holdings, at their dollars now, and each fund's rows (FUND_ROWS), with
    everything those rows were read from, which it books by."""
    rows = ctx.value("fund_rows", "portfolio", ref=True)
    return {**ctx.read_of(rows), "holdings": ctx.value("holdings", "portfolio"), "fund_rows": rows}


def _rows_inputs(ctx: Inputs, subject: str) -> dict:
    """What each fund's rows are read from (_rows_reads), with each fund a fund held directly reaches through its
    derivatives read as a fund it holds is (MATH.md 3.1: a fund held by a fund is expanded once more, at the product of the
    two exposures): an option's underlying, a swap's or a future's reference, where it is a fund. What a derivative
    refers to, and its exposure, are known only once the rows are read, so the reads are made again with those funds
    among the funds held inside a fund; every read is kept, so the second pass reads what the first fetched."""
    through: dict[str, float] = {}
    while True:
        m = _rows_reads(ctx, subject, through)
        more = m.pop("through")
        if all(through.get(t, 0.0) >= w for t, w in more.items()):
            return m
        through = {t: max(w, through.get(t, 0.0)) for t, w in {**through, **more}.items()}


def _holdings_sources(ctx: Inputs, level: list, asof: str) -> tuple[list[str], dict[str, str], list[str]]:
    """Where a level of funds' holdings are read: ETF Global's, for each fund it reads; for a mutual fund whose fund an
    exchange also lists as a share class, that class's, which are its own, published each day (harness.venues.funds),
    beside its report, and the later stands; and each fund's report."""
    etf_global = [f for f, _, _, kind in level if kind not in REPORT_ONLY]
    traded = {f: e for f, _, _, kind in level if kind == "MF"
              for e in [((ctx.payload(ctx.data("ticker_ref", f, covering=(asof, asof))) or {}).get("results")
                         or {}).get("exchange_traded")] if e}
    return etf_global, traded, [f for f, _, _, _ in level]


def _rows_reads(ctx: Inputs, subject: str, through: dict[str, float]) -> dict:
    """Read what each fund's rows are read from, which what a fund holds decides and today's prices do not: each
    holding's reference; each fund's holdings, ETF Global's, and its latest
    Form N-PORT report publicly available by the as-of: what the fund holds from ETF Global's latest processing, and
    its derivatives from its report, the one source that states each one's notional by one rule (harness.derive.rows);
    the report's rows whole for a fund ETF Global gives no holdings for; and the report's codes for what ETF Global
    gives no kind (MATH.md 3.1); and, for each listed
    constituent at or above the floor, what confirms its company (harness.derive.identity): the reference under its
    own ticker, its share class's US listings by ISIN, the SEC's CIK of a US listing, the company's exchange listings
    by CIK, and the recent closes of a listing over the counter. A fund constituent resolved to a fund is walked into,
    one level of funds deep. Then the reference of each listing a constituent resolves to, which says what it is; what
    each holding's kind reads beyond its fund's words (harness.derive.assets): the Treasury's bills, where a fund holds
    debt with a CUSIP, the SEC's filings of each other fund a fund holds by ticker, the codes its fund's own report
    gives, and OpenFIGI's reading of the CUSIP of a row nothing else decides; and OpenFIGI's reading of each identifier
    a report gives an index its derivatives reference, with, for one it does not know as an index, whether it names a
    US listing. Then, for each index a fund's derivatives are exposed to that OpenFIGI names, the listings whose
    returns match its values on Massive, and the holdings of the closest that gives companies; and what confirms each
    company a fund's derivatives reach, a security one references or an index's companies, where it reaches the
    fund's holder at or above the floor, as the floor reads a constituent: its exposure times the fund's weight."""
    asof = asof_of(ctx)
    reads = _Reads(ctx, asof)
    constituents: dict[str, dict] = {}
    held = [security_of(h) for h in ctx.links("portfolio", "holds")  # cash is cash, in whatever currency
            if not h["to"].startswith(("cash:", "market:"))]
    if any(series.is_abroad(t) for t in held):  # a listing abroad held: its company, by its identifiers, as name_of
        # names it, a symbol's by its own market's reference and its share class, `{ISIN}:{country}` by its ISIN
        reads.fetch("ticker_ref", [t for t in held if series.symbol_abroad(t)])
        reads.settle(lambda look: [abroad(t, look.reference(t), look) if series.symbol_abroad(t) else
                                   resolve(_abroad_row(t), look) for t in held if series.is_abroad(t)])
    ctx.prefetch("ticker_ref", held, (asof, asof), stage="funds")
    # a holding's reference is required: without it the portfolio is not read
    reads.refs["ticker_ref"].update(ctx.data_many("ticker_ref", held, covering=(asof, asof)))
    level = [(t, 1, 1.0, reference_type(reads.read("ticker_ref", t))) for t in held
             if kind_of(reads.read("ticker_ref", t)) == "fund"]
    held_funds_directly = [t for t, _, _, _ in level]  # each read for whether it is a money market fund, as a row's is
    if level:  # each held fund's holdings, started beside whether it is a money market fund, which they do not wait on
        etf_global, traded, filed = _holdings_sources(ctx, level, asof)
        ctx.prefetch("nport", filed, (asof, asof), wait=False)
        ctx.prefetch("constituents", etf_global + sorted(set(traded.values())), (asof, asof), wait=False)
    if level:  # a money market fund held is cash (MATH.md 3.1): its holdings are not read
        ctx.prefetch("money_market", held_funds_directly, (asof, asof), stage="funds")
        cash = {t for t, ref in ctx.data_many("money_market", held_funds_directly, covering=(asof, asof),
                                              required=False).items()
                if ref and ref.get("data") and (ctx.payload(ref) or {}).get("money_market")}
        level = [x for x in level if x[0] not in cash]
    if level:  # what a fund's holdings are read against needs only the as-of: every US listing, and the bills
        ctx.prefetch("listings", ["stocks"], (asof, asof), wait=False)
        ctx.prefetch("bills", ["US Treasury"], (asof, asof), wait=False)

    def holdings_of(kind: str, fund: str) -> dict:  # a read that fails raises: the look-through waits for it
        return ctx.data(kind, fund, covering=(asof, asof))

    resolved: dict[str, dict] = {}
    reports: dict[str, dict] = {}  # each fund's report, where it is read
    # a holdings source that no longer reads a fund publishes none for it (MATH.md 3.1): ETF Global processes each fund it
    # reads every day, so a fund it last processed before the session before its latest processing is one it does not
    feed = ctx.data("holdings_processed", "ETF Global", covering=(asof, asof), required=False) if level else None
    latest = (ctx.payload(feed) or {}).get("processed_date") if feed and feed.get("data") else None
    line = _session_before(latest) if latest else None
    weights: dict[str, float] = {}  # each fund's largest weight in the portfolio's holdings it is reached through
    depths: dict[str, int] = {}  # how deep each fund read is: held directly, or held inside a fund
    while level:
        level = [x for x in level if x[0] not in constituents]
        held_here = any(depth == 1 for _, depth, _, _ in level)
        etf_global, traded, filed = _holdings_sources(ctx, level, asof)
        ctx.prefetch("nport", filed, (asof, asof), stage="funds", wait=False)  # beside the holdings, not after
        ctx.prefetch("constituents", etf_global + sorted(set(traded.values())), (asof, asof), stage="funds")
        found = {f: holdings_of("constituents", f) for f in etf_global}
        found.update({f: holdings_of("constituents", e) for f, e in traded.items()})
        ctx.prefetch("nport", filed, (asof, asof), stage="funds")
        for f in filed:
            report = ctx.data("nport", f, covering=(asof, asof), required=False)
            if report is None:
                continue
            reports[f] = report
        _notional_ahead(ctx, {f: ctx.payload(ref) for f, ref in reports.items() if ref.get("data")})
        for f in filed:
            if f not in reports:
                continue
            report = reports[f]
            # its holdings are those published for it as of the latest date (MATH.md 3.1): its report where its holdings
            # source gives none with weights, or none as late, as where that source no longer reads the fund
            if f not in found or not _weighted(found[f], ctx) or _later(report, found[f], ctx) or \
                    (_lapsed(found[f], line, ctx) and _weighted(report, ctx)):
                found[f] = report
        for f in list(found):  # a fund its holdings source no longer reads, and that files no report: none published
            if found[f] is not reports.get(f) and _lapsed(found[f], line, ctx):
                found[f] = {"lapsed": (ctx.payload(found[f]) or {}).get("processed_date")}
        rows = []
        for fund, depth, path_weight, _ in level:
            weights[fund] = max(weights.get(fund, 0.0), abs(path_weight))
            constituents[fund] = found[fund]
            depths[fund] = depth
            if not found[fund].get("data"):
                continue
            rows += [(r, depth, path_weight) for r in _valid_rows(ctx.payload(found[fund]))
                     if _resolvable(r) and abs(path_weight * r["weight"]) >= FLOOR]
        _listings_ahead(ctx, [r for fund, _, _, _ in level if found[fund].get("data")
                             for r in _valid_rows(ctx.payload(found[fund]))], asof)  # every row is classified
        # what each row's kind reads beyond its fund's words, and the indexes its fund's derivatives reference: started
        # beside the rows' identities, read where the kinds and the indexes are
        loaded_here = [r for f in filed if found.get(f, {}).get("data") for r in _valid_rows(ctx.payload(found[f]))]
        ctx.prefetch("money_market", to_read(loaded_here)[1], (asof, asof), wait=False)
        ctx.prefetch("figi_index", sorted({i for f in filed for ref in (found.get(f), reports.get(f))
                                           if ref and ref.get("data") for i in index_identifiers(ctx.payload(ref))}),
                     (asof, asof), wait=False)
        reads.fetch_all({"ticker_ref": [own_ticker(r) for r, _, _ in rows], "figi": [r.get("isin") for r, _, _ in rows],
                         "figi_cusip": [r.get("cusip") for r, _, _ in rows if not r.get("isin")]})
        resolved.update(reads.settle(lambda look: {row_key(r): resolve(r, look) for r, _, _ in rows}))
        _series_ahead(ctx, [x.get("ticker") for x in resolved.values()], asof)
        # a fund held inside a fund, one level deep; one whose holdings were read already was read at a larger weight
        level = [(resolved[row_key(r)]["ticker"], 2, path_weight * r["weight"], resolved[row_key(r)].get("type"))
                 for r, depth, path_weight in rows
                 if depth == 1 and resolved[row_key(r)].get("kind") == "fund"
                 and resolved[row_key(r)]["ticker"] not in constituents]
        if held_here and through:  # and each fund those funds' derivatives reach, at the weight they reach it at
            inner = {t: w for t, _, w, _ in level}
            reads.fetch("ticker_ref", [t for t in through if t not in constituents and t not in inner])
            level = [(t, 2, max(w, through.get(t, 0.0)), kind) for t, _, w, kind in level] + \
                [(t, 2, w, reference_type(reads.read("ticker_ref", t))) for t, w in sorted(through.items())
                 if t not in constituents and t not in inner]
    reads.fetch("ticker_ref", [r["ticker"] for r in resolved.values() if us_ticker(r.get("ticker"))])
    shocked = [ticker_of(n) for n in shocked_names(ctx) if ticker_of(n) not in held]
    if constituents:  # what names each company held directly, or named by a consequence, wherever its shares are
        # listed: the floor's exception
        reads.fetch("ticker_ref", [t for t in shocked if us_ticker(t) and not _isin_form(t)])
        names = [t for t in held + shocked if kind_of(reads.read("ticker_ref", t)) == "name"]
        reads.settle(lambda look: [abroad(t, look.reference(t), look) if series.symbol_abroad(t) else
                                   held_identifiers(t, look.reference(t), look) for t in names] +
                     [look.composites_of_isin(t.split(":")[0]) for t in shocked if _isin_form(t)])
    listings = None
    if constituents:  # where each fund's holdings are listed, by their identifiers; a read that fails raises
        listings = ctx.data("listings", "stocks", covering=(asof, asof), required=False)
    # what each holding's kind reads beyond its labels: the Treasury's bills, which funds held, mutual or exchange-
    # traded, are money market funds, the codes each fund's own report gives, and OpenFIGI's reading of the CUSIP of
    # a row nothing else decides
    # (harness.derive.assets); a read that fails raises
    loaded = [r for ref in constituents.values() if ref.get("data") for r in _valid_rows(ctx.payload(ref))]
    # where each share a fund's report names by its ISIN alone trades, as the funds' holdings say, read for all of them
    # at once (harness.venues.massive.isin_listings): its ticker there and its exchange's country, which the fund's
    # division by listing and the portfolio's lists read (fund_rows)
    unplaced = sorted({f"{isin_of(r)}@{asof[:7]}" for r in loaded if _unplaced(r)})
    ctx.prefetch("isin_listings", unplaced, (asof, asof), stage="funds")
    placements = ctx.data_many("isin_listings", unplaced, covering=(asof, asof), required=False) if unplaced else {}
    _rates_ahead(ctx, loaded, asof)  # once the identities are read, so that none of their reads waits behind these
    bills, held_funds = to_read(loaded)
    held_funds = sorted(set(held_funds) | set(held_funds_directly))
    kinds: dict[str, Any] = {"bills": ctx.data("bills", "US Treasury", covering=(asof, asof), required=False)
                             if bills else None}
    ctx.prefetch("money_market", held_funds, (asof, asof), stage="funds")
    kinds["money_market"] = ctx.data_many("money_market", held_funds, covering=(asof, asof), required=False)
    kinds["reports"] = {f: ref for f, ref in reports.items() if ref is not constituents.get(f)}
    so_far = _kinds({"kinds": kinds}, ctx.payload)
    unknown = unclassed(loaded, so_far)
    ctx.prefetch("figi_cusip", unknown, (asof, asof), stage="funds")
    kinds["sectors"] = ctx.data_many("figi_cusip", unknown, covering=(asof, asof), required=False)
    assets = _kinds({"kinds": kinds}, ctx.payload)
    # each coin a fund holds, by the code its source gives it, where Coinbase Exchange lists its pair against the
    # dollar (harness.derive.rows.coin)
    candidates = sorted({p for ref in constituents.values() if ref.get("data")
                         for p in coins(ctx.payload(ref), assets)})
    ctx.prefetch("pair", candidates, (asof, asof), stage="funds")
    pairs = ctx.data_many("pair", candidates, covering=(asof, asof), required=False)
    listed_pairs = _pairs({"pairs": pairs}, ctx.payload)
    # each listed option a fund's holdings name, at the delta its market quotes on the as-of, which Massive's snapshot
    # gives only now; and, for one on a US listing, OpenFIGI's share classes of its ticker; a FLEX contract, which no
    # market lists, is not asked (harness.derive.rows.option_symbol, flex_underlying)
    named_options = sorted({option_symbol(r) for r in loaded} - {None})
    listed_options = [s for s in named_options if not flex_underlying(s)]
    quotes: dict[str, dict] = {}
    if listed_options and is_today(asof):
        ctx.prefetch("option", listed_options, (asof, asof), stage="funds")
        quotes = ctx.data_many("option", listed_options, covering=(asof, asof), required=False)
    # each listed contract's price standing at its holdings' close, by which its row is read as counting contracts or
    # its underlying's shares (harness.derive.rows.held_contracts); and at a past as-of, each one's terms then and its
    # price standing at the as-of's close, which its market's snapshot, being now's, does not give (MATH.md 3.1)
    held_at = _dated(ctx, "option_at", sorted({f"{option_symbol(r)}@{close_moment(r['effective_date'])}"
                                               for r in loaded if r.get("effective_date")
                                               and option_symbol(r) in listed_options}))
    # the day each contract its market does not quote now is valued on, as its rows' date is (MATH.md 3.1): at a past
    # as-of, every one, on the as-of; today, one the snapshot does not know (a contract since expired, or one no market
    # lists), on the latest date a fund's rows hold it, when its market's record still has it if it ever had
    valued_on: dict[str, str] = {}
    if not is_today(asof):
        valued_on = {s: asof for s in listed_options}
    else:
        unknown = {s for s, ref in quotes.items() if ref.get("data")
                   and (ctx.payload(ref) or {}).get("error") == "NOT_FOUND"}
        for r in loaded:
            if option_symbol(r) in unknown and r.get("effective_date"):
                s, day = option_symbol(r), str(r["effective_date"])[:10]
                valued_on[s] = max(valued_on.get(s, day), day)
    # each one's terms in its market's record on that day, and its price standing at that day's close
    contract_terms = _dated(ctx, "option_contract", [f"{s}@{d}" for s, d in sorted(valued_on.items())])
    at_close = _dated(ctx, "option_at", [f"{s}@{close_moment(d)}" for s, d in sorted(valued_on.items())])
    # a listed contract's underlying, as its record names it, with what its CFI code says it is; and a contract its
    # record does not know, which no market lists: priced at what the holdings state for it, its underlying its
    # symbol's root, read as a FLEX contract's is (harness.derive.rows._listed_option)
    past: dict[str, dict] = {}
    unlisted: list[str] = []
    for k, ref in contract_terms.items():
        found = (ctx.payload(ref) or {}) if ref.get("data") else {}
        terms = found.get("results") or {}
        if terms.get("underlying_ticker"):
            past[k.rpartition("@")[0]] = {"root": str(terms["underlying_ticker"]),
                                          "kind": str(terms.get("cfi") or "")[3:4] or None, "day": subject_day(k)}
        elif found.get("status") == "NOT_FOUND":
            unlisted.append(k.rpartition("@")[0])
    underlyings = {((ctx.payload(ref) or {}).get("underlying_asset") or {}).get("ticker")
                   for ref in quotes.values() if ref.get("data")} - {None, ""}
    on_shares = sorted(t for t in underlyings if not t.startswith("I:") and us_ticker(t))
    ctx.prefetch("figi_ticker", on_shares, (asof, asof), stage="funds")
    options = {"quotes": quotes, "flex": [s for s in named_options if flex_underlying(s)],
               "classes": ctx.data_many("figi_ticker", on_shares, covering=(asof, asof), required=False),
               "held_at": held_at, "contracts": contract_terms, "at_close": at_close, "past": past,
               "unlisted": unlisted}
    # the underlying of each FLEX contract, its symbol's root less its digit, of one no market lists, its root, and of
    # a listed one its market does not quote now, its record's, read in Massive's own references as of the as-of
    # (harness.derive.rows.option_underlying)
    named_by = _named_by(options)
    options["underlying_refs"] = _underlying_refs(ctx, sorted({r for r, _ in named_by.values()}), asof)
    underlying = {s: u for s, (root, kind) in named_by.items()
                  if (u := option_underlying(root, kind, *_refs_of(options["underlying_refs"], root, ctx.payload)))}
    # a listing whose reference gives no share class FIGI: today, OpenFIGI's share classes of its ticker, as a listed
    # contract's underlying is confirmed; at a past as-of, none, since OpenFIGI maps a ticker as it stands now
    unconfirmed = sorted({u["ticker"] for u in underlying.values() if not u["ticker"].startswith("I:")
                          and not u.get("company")} - set(options["classes"]))
    if unconfirmed and is_today(asof):
        ctx.prefetch("figi_ticker", unconfirmed, (asof, asof), stage="funds")
        options["classes"].update(ctx.data_many("figi_ticker", unconfirmed, covering=(asof, asof), required=False))
    # what each index a fund's derivatives reference is, as OpenFIGI reads its identifier (harness.derive.rows)
    # and the ticker a report gives a security an option of its refers to, read as a FLEX root is (an index's, or a
    # share class's), where the report is its fund's rows (_report_options)
    stated = {str(((r["derivative"].get("reference") or {}).get("security") or {}).get("ticker") or "")
              for f, ref in constituents.items() if ref.get("data") and ref is reports.get(f)
              for r in (ctx.payload(ref) or {}).get("results") or [] if isinstance(r, dict) and report_option_key(r)}
    identifiers = sorted({i for ref in [*constituents.values(), *reports.values()] if ref.get("data")
                          for i in index_identifiers(ctx.payload(ref))} | (stated - {""}))
    ctx.prefetch("figi_index", identifiers, (asof, asof), stage="funds")
    indexes = ctx.data_many("figi_index", identifiers, covering=(asof, asof), required=False)
    unread = [i for i, ref in indexes.items() if ref is not None and ref.get("data")
              and not (ctx.payload(ref) or {}).get("index") and us_ticker(i)]
    ctx.prefetch("figi_ticker", unread, (asof, asof), stage="funds")
    listed = ctx.data_many("figi_ticker", unread, covering=(asof, asof), required=False)
    # each US ticker a report names a derivative's security by and by nothing else: its share classes, by which that
    # listing is the security (harness.derive.rows.ticker_reference)
    named = sorted({t for ref in reports.values() if ref.get("data") for t in security_tickers(ctx.payload(ref))
                    if us_ticker(t)} - set(listed))
    ctx.prefetch("figi_ticker", named, (asof, asof), stage="funds")
    listed.update(ctx.data_many("figi_ticker", named, covering=(asof, asof), required=False))
    # each fund's report beside the ETF Global holdings it is read with, for its derivatives (harness.derive.rows)
    derivatives = {f: ref for f, ref in reports.items() if ref is not constituents.get(f) and ref.get("data")}
    # each index a fund's derivatives are exposed to, as OpenFIGI names it: whether Massive gives its values, the
    # listings whose returns match them, and the holdings of the closest that gives companies, which are the index's
    # (harness.venues.trackers); read only where a held fund's derivative is exposed to an index
    readings = _index_readings({"indexes": {"readings": indexes, "listed": listed}}, ctx.payload)
    filed = {f: ctx.payload(ref) for f, ref in reports.items() if ref.get("data")}
    # the rate of each currency a report states a swap's or a future's notional under, at its report's date, by which
    # the notional's units are established and it is converted (harness.derive.rows._dollars)
    notional = _notional_reads(ctx, filed)
    rates = _notional_rates({"notional": notional}, ctx.payload)
    # and each index a report's option refers to, where the report is its fund's rows, whose basket pays its dividends
    on_options = {index_symbol(readings.get(((r["derivative"].get("reference") or {}).get("index") or {})
                                            .get("identifier") or ""))
                  for f, ref in constituents.items() if ref.get("data") and ref is reports.get(f)
                  for r in (ctx.payload(ref) or {}).get("results") or [] if isinstance(r, dict) and report_option_key(r)}
    symbols = sorted({s for p in filed.values() for s in indexes_exposed(p, readings, rates).values()} |
                     ({s for s in on_options if s}) |
                     {t for t in underlyings if t.startswith("I:")} |
                     {u["ticker"] for u in underlying.values() if u["ticker"].startswith("I:")})
    ctx.prefetch("tracker", symbols, (asof, asof), stage="funds")
    trackers = ctx.data_many("tracker", symbols, covering=(asof, asof), required=False)
    baskets: dict[str, dict] = {}
    firsts = [(ctx.payload(ref) or {}).get("matches") or [] if ref.get("data") else [] for ref in trackers.values()]
    ctx.prefetch("constituents", sorted({m[0]["listing"] for m in firsts if m}), (asof, asof), stage="funds")
    for ref in trackers.values():  # each index's companies: those of the first of its matches whose holdings give any
        for match in (ctx.payload(ref) or {}).get("matches") or [] if ref.get("data") else []:
            if match["listing"] not in baskets:
                baskets[match["listing"]] = ctx.data("constituents", match["listing"], covering=(asof, asof),
                                                     required=False)
            basket = baskets[match["listing"]]
            if basket.get("data") and companies_of(ctx.payload(basket)):
                break
    indexed = {"readings": indexes, "listed": listed, "trackers": trackers, "baskets": baskets}
    # every company a fund's derivatives reach, a security referenced or an index's companies, confirmed as a
    # constituent is where it reaches the fund's holder at or above the floor (MATH.md 3.1)
    established = _established({"indexes": indexed}, ctx.payload)
    # what a delta is worked out from where no market quotes one (harness.derive.rows.terms_inputs): for a FLEX
    # contract, or a listed one its market quotes none, the underlying's closes and dividends, an index's basket
    # fund's, which pays for it, and the Treasury's par yield curve over the as-of's year and the year before
    # each option of a fund whose rows are its report: the listed contracts its stated terms name on the report's
    # date, and its underlying, an index's values or its share class's US listing (harness.derive.rows._report_option)
    options["report"] = _report_options(ctx, reads, constituents, reports, readings)
    options["terms"] = _terms_reads(ctx, asof, quotes, underlying, established, {s: p["day"] for s, p in past.items()},
                                    [v["ticker"] for v in options["report"].values()])
    held_options = _options({"options": options, "indexes": indexed}, ctx.payload)
    # each row of a fund's current holdings that states shares of a listing its price names, confirmed by the fund's
    # returns (harness.derive.rows.established_by_price), and the report's row of the same position, which it states
    # as of the report's date
    priced = _priced_reads(ctx, reads, constituents, reports, assets, resolved, weights, asof)
    by_price = _priced({"asof": asof, "constituents": constituents, "priced": priced,
                        "identity": {kind: dict(refs) for kind, refs in reads.refs.items()}}, ctx.payload, assets)
    # each fund measured on its one reference (harness.derive.rows.slope_reference): its own returns and the
    # reference's, an index's values or the company's listing, over the window the betas read
    measured: dict[str, dict] = {}
    for fund, ref in constituents.items():
        if not ref.get("data"):
            continue
        report = ctx.payload(derivatives[fund]) if fund in derivatives else None
        rows = (with_exposure(ctx.payload(ref), assets, readings, established, report, options=held_options,
                              pairs=listed_pairs, rates=rates, priced=by_price.get(fund)) or {})
        found = slope_reference(rows.get("results") or [] if isinstance(rows, dict) else [])
        if found is None:
            continue
        if found["series"]:
            reference = price_series(ctx, found["series"], asof)
        else:
            listing = reads.settle(lambda look: resolve(found["to"]["company"], look))
            if not listing.get("ticker") or listing.get("market") == "home":
                continue  # a company priced abroad, in another currency: its report states its size
            reference = return_series(ctx, listing["ticker"], asof)
        measured[fund] = {"fund": return_series(ctx, fund, asof), "reference": reference,
                          "census": ctx.data("ncen", fund, covering=(asof, asof), required=False)}
    reach: list[dict] = []
    # what each reach of a fund held directly reaches of its holder, for the funds its derivatives reach (_rows_inputs):
    # its size, or, for an option whose delta its terms give later, none; False for a fund held inside a fund
    sizes: list[float | None | bool] = []
    cusips: set[str] = set()  # a row below the floor named by a US CUSIP and no FIGI: its listing's, by the CUSIP
    for fund, ref in constituents.items():
        if ref.get("data"):
            rows = (with_exposure(ctx.payload(ref), assets, readings, established,
                                  report=ctx.payload(derivatives[fund]) if fund in derivatives else None,
                                  options=held_options, slope=_slope(measured.get(fund), ctx.payload, asof),
                                  pairs=listed_pairs, rates=rates, priced=by_price.get(fund)) or {})
            rows = rows.get("results") or [] if isinstance(rows, dict) else []
            found_here = [(row, size) for row, size in reached(rows, weights.get(fund, 1.0))
                          if size is None or size >= FLOOR]
            reach += [row for row, _ in found_here]
            sizes += [size if depths.get(fund) == 1 else False for _, size in found_here]
            cusips |= {c for r in rows for t, x in _targets(r)
                       if abs(weights.get(fund, 1.0) * x) < FLOOR and not t.get("figi") and (c := cusip_of(t))
                       and (isin_of(t) or "").startswith("US")}
    reads.fetch_all({"ticker_ref": [own_ticker(r) for r in reach], "figi": [r.get("isin") for r in reach],
                     "figi_cusip": [r.get("cusip") for r in reach if not r.get("isin")]})
    found_rows = reads.settle(lambda look: [resolve(r, look) for r in reach])
    # each fund a derivative of a fund held directly reaches, not read as a fund held: read as one held inside a fund
    # at the largest weight a derivative reaches it at, and where an option's delta is not yet known, at every weight,
    # as an index's companies are (rows.reached)
    reaches_funds: dict[str, float] = {}
    for size, got in zip(sizes, found_rows):
        if size is not False and got.get("kind") == "fund" and got.get("ticker") and got["ticker"] not in constituents:
            reaches_funds[got["ticker"]] = max(reaches_funds.get(got["ticker"], 0.0), math.inf if size is None else size)
    # Such a row can be a company the portfolio names only where one it names, held directly or named by a
    # consequence, is a listing no row at the floor resolved to: only then are their listings read.
    at_floor = {x.get("ticker") for x in list(resolved.values()) + list(found_rows)}
    named = [t for t in held + shocked if _isin_form(t) or kind_of(reads.read("ticker_ref", t)) == "name"]
    if not any(t not in at_floor for t in named):
        cusips = set()
    reads.fetch("cusip_listing", [f"{c}@{asof}" for c in sorted(cusips)])
    # each company's id, its first US ticker (harness.derive.identity.first_ticker), by the CIK its listings name
    given = {t: ((reads.read("ticker_ref", t) or {}).get("results") or {}).get("cik") for t in held}
    reads.settle(lambda look: [look.first_ticker(c) for c in sorted(({f.get("cik") for f in found_rows} |
                                                                      {look.company_cik(t, given[t]) for t in held})
                                                                     - {None})])
    return {"through": reaches_funds, "asof": asof, "references": dict(reads.refs["ticker_ref"]), "notional": notional,
            "shocked": shocked if constituents else [], "cusips": sorted(cusips),
            "constituents": constituents, "derivatives": derivatives, "listings": listings, "kinds": kinds,
            "indexes": indexed, "options": options, "measured": measured, "pairs": pairs, "priced": priced,
            "placements": {s.rsplit("@", 1)[0]: ref for s, ref in (placements or {}).items() if ref},
            "identity": {kind: dict(refs) for kind, refs in sorted(reads.refs.items()) if kind != "ticker_ref"}}


def _kinds(m: dict, load) -> Kinds:
    """What the look-through read to decide its holdings' kinds: the CUSIPs of the Treasury's bills; the funds held
    the SEC's filings show to be money market funds; the kind each fund's own report gives a security, by its CUSIP
    and ISIN, where that report was read beside the holdings loaded; and OpenFIGI's reading of a CUSIP."""
    found = m.get("kinds") or {}
    ref = found.get("bills")
    bills = (load(ref) or {}).get("results") or [] if ref and ref.get("data") else []
    funds = {t: load(r) if r and r.get("data") else None for t, r in (found.get("money_market") or {}).items()}
    codes: dict[str, str] = {}
    for ref in (found.get("reports") or {}).values():
        for r in _valid_rows(load(ref)) if ref and ref.get("data") else []:
            kind = labelled(r)
            if kind != "other":
                codes.update({key: kind for key in (cusip_of(r), r.get("isin")) if key})
    sectors = {}
    for cusip, ref in (found.get("sectors") or {}).items():
        kind = figi_kind((load(ref) or {}).get("sectors") or []) if ref and ref.get("data") else None
        if kind:
            sectors[cusip] = kind
    return Kinds(bills=frozenset(b["cusip"] for b in bills if isinstance(b, dict) and b.get("cusip")),
                 money_market=frozenset(t for t, p in funds.items() if isinstance(p, dict) and p.get("money_market")),
                 codes=codes, sectors=sectors)


def _index_readings(m: dict, load) -> dict[str, dict]:
    """Each index identifier a fund's report gives, as OpenFIGI reads it, and for one it does not know as an index, and
    each US ticker a report names a security by alone, the US share classes OpenFIGI gives the ticker (`listed`;
    harness.derive.rows.index_key, ticker_reference)."""
    found = m.get("indexes") or {}
    out = {}
    for identifier, ref in (found.get("readings") or {}).items():
        if ref and ref.get("data"):
            out[identifier] = dict(load(ref) or {})
    for identifier, ref in (found.get("listed") or {}).items():
        if ref and ref.get("data"):
            out.setdefault(identifier, {})["listed"] = list((load(ref) or {}).get("share_classes") or [])
    return out


def _named_ticker(named: str, readings: dict[str, dict]) -> str | None:
    """The series a security a report names by a ticker alone is read under: an index's values, where OpenFIGI reads
    the ticker as an index Massive gives values for, or the US listing it is (a report's identifiers are OpenFIGI's
    to read, as every identifier a report gives is)."""
    key = index_key({"identifier": named}, readings.get(named))
    if key is not None and key[0] == "figi" and index_symbol(readings.get(named)):
        return index_symbol(readings.get(named))
    return named if ticker_reference(named, readings.get(named)) else None


def _underlying_refs(ctx: Inputs, roots: list[str], asof: str) -> dict[str, dict]:
    """Each root an option's underlying is named by, in Massive's references as of the as-of: as a ticker, and as an
    index's symbol, `I:{root}` (harness.derive.rows.option_underlying)."""
    ctx.prefetch("ticker_ref", sorted(set(roots) | {f"I:{r}" for r in roots}), (asof, asof), stage="funds")
    listings = ctx.data_many("ticker_ref", roots, covering=(asof, asof), required=False)
    indexes = ctx.data_many("ticker_ref", [f"I:{r}" for r in roots], covering=(asof, asof), required=False)
    return {r: {"listing": listings[r], "index": indexes[f"I:{r}"]} for r in roots}


def _refs_of(refs: dict[str, dict], root: str, load) -> tuple[Any, Any]:
    """A root's two references' payloads, as option_underlying reads them: None where either was not read."""
    found = refs.get(root) or {}
    return tuple(load(r) if r and r.get("data") else None for r in (found.get("listing"), found.get("index")))


def _dated(ctx: Inputs, kind: str, subjects: list[str]) -> dict[str, dict]:
    """Each subject's data of a kind read as of the day its subject names (`{what}@{date}` or `{what}@{moment}`, the
    moment's day in New York), every day's reads side by side."""
    days: dict[str, list[str]] = defaultdict(list)
    for s in subjects:
        days[subject_day(s)].append(s)
    for day, named in days.items():
        ctx.prefetch(kind, named, (day, day), stage="funds", wait=False)
    return {s: ctx.data(kind, s, covering=(subject_day(s), subject_day(s)), required=False) for s in subjects}


def _report_options(ctx: Inputs, reads: _Reads, constituents: dict, reports: dict,
                    readings: dict[str, dict]) -> dict[str, dict]:
    """For each option a fund's report states, where the report is the fund's rows (rows.report_option_key): its
    underlying's series, an index's values as OpenFIGI reads the report's identifier for it, or the one US listing of
    the share class the report names by ISIN; and the listed contracts its stated terms name on the report's date
    (Massive.option_listings), which state what the report does not: its exercise style and the shares each
    controls."""
    rows = [r for f, ref in constituents.items() if ref.get("data") and ref is reports.get(f)
            for r in (ctx.payload(ref) or {}).get("results") or [] if isinstance(r, dict) and report_option_key(r)]
    isins = sorted({((r["derivative"].get("reference") or {}).get("security") or {}).get("isin") for r in rows}
                   - {None, ""})
    reads.fetch("figi", isins)
    listed = reads.settle(lambda look: {i: sorted({x["ticker"] for x in look.us_listings({"isin": i})}) for i in isins})
    out: dict[str, dict] = {}
    for r in rows:
        d, ref = r["derivative"], r["derivative"].get("reference") or {}
        if "index" in ref:
            ticker = index_symbol(readings.get(ref["index"].get("identifier") or ""))
            root = ticker[2:] if ticker and ticker.startswith("I:") else None
        else:  # the one US listing of its share class, or else what OpenFIGI reads the ticker the report gives as
            found = listed.get((ref.get("security") or {}).get("isin") or "") or []
            ticker = root = found[0] if len(found) == 1 else None
            named = str((ref.get("security") or {}).get("ticker") or "")
            if not root and named:
                ticker = _named_ticker(named, readings)
                root = ticker[2:] if ticker and ticker.startswith("I:") else ticker
        if root:
            out[report_option_key(r)] = {"ticker": ticker, "subject": f"{root}|{d['expiry']}|{d['right']}|"
                                                                      f"{strike_text(d['strike'])}@{r['effective_date']}"}
    found = _dated(ctx, "option_listings", sorted({v["subject"] for v in out.values()}))
    return {k: {**v, "ref": found[v["subject"]]} for k, v in out.items()}


def _named_by(options: dict) -> dict[str, tuple[str, str | None]]:
    """What names each option's underlying where its market quotes it not now (harness.derive.rows.option_underlying):
    a FLEX contract's root less its style digit, and one no market lists, its root, each with no record to say what it
    is; a listed one, its record's underlying and what its CFI code says it is."""
    return {s: (flex_underlying(s), None) for s in options.get("flex") or []} | \
        {s: (OCC.fullmatch(s[2:]).group(1), None) for s in options.get("unlisted") or []} | \
        {s: (p["root"], p["kind"]) for s, p in (options.get("past") or {}).items()}


def _terms_reads(ctx: Inputs, asof: str, quotes: dict, underlying: dict[str, dict], established: dict,
                 past: dict[str, str] | None = None, more: list[str] | None = None) -> dict:
    """What the look-through reads to work out an option's delta from its terms (harness.derive.rows.terms_inputs):
    for each underlying of a FLEX contract or of a listed one its market quotes no delta, its closes and dividends,
    and for an index, its basket fund's, which pays its dividends; the Treasury's par yield curve over the as-of's
    year and the one before; and, for a listed contract, its underlying's minute bars on the day of the moment its
    price stood at, the underlying's price then (harness.derive.rows.price_at): its quote's or trade's moment where its
    market quotes no delta now, or the as-of's close at a past as-of. Nothing where every option has its market's
    delta."""
    minutes: set[str] = set()
    unquoted: set[str] = set()
    for r in quotes.values():
        found = (ctx.payload(r) or {}) if r.get("data") else {}
        if found.get("status") != "OK" or (found.get("greeks") or {}).get("delta"):
            continue
        ticker, at = (found.get("underlying_asset") or {}).get("ticker"), option_price(found)[2]
        unquoted.add(ticker)
        if ticker and at:
            minutes.add(f"{ticker}@{subject_day(f'@{at}')}")
    for s, day in (past or {}).items():  # a listed contract its market quotes not now: its underlying at the close
        if s in underlying:                # of the day it is valued on
            minutes.add(f"{underlying[s]['ticker']}@{day}")
    tickers = sorted(({u["ticker"] for u in underlying.values()} | unquoted | set(more or [])) - {None, ""})
    if not tickers:
        return {}
    payers = {t: (((established.get(("values", t)) or {}).get("basket") or {}).get("fund") if t.startswith("I:")
                  else t) for t in tickers}
    named = sorted(set(tickers) | {p for p in payers.values() if p})
    year = int(asof[:4])
    ctx.prefetch("yield_curve", [str(year - 1), str(year)], (asof, asof), wait=False)  # side by side, read below
    return {"series": return_series_many(ctx, named, asof, required=lambda t: False), "payers": payers,
            "curves": {str(y): ctx.data("yield_curve", str(y), covering=(asof, asof), required=False)
                       for y in (year - 1, year)}, "minutes": _dated(ctx, "minutes", sorted(minutes))}


def _options(m: dict, load) -> dict:
    """What the look-through read of the listed options its funds hold (harness.derive.rows.etf_global_rows): each
    contract's snapshot by its symbol, the share classes of each US ticker one is on, and what a delta is worked out
    from where no market quotes one (`terms`): always what the contracts' own records and references say (each FLEX,
    unlisted or past contract's underlying, as its ticker and what it refers to; the contracts no market lists; each
    listed one's terms in its record and its price at the close it is valued at), and where any was read, each
    underlying's and payer's closes and dividends and the Treasury's curves."""
    found = m.get("options") or {}

    def kept(reads: dict | None) -> dict:
        return {s: load(r) for s, r in (reads or {}).items() if r and r.get("data")}

    out = {"quotes": kept(found.get("quotes")), "held_at": kept(found.get("held_at")),
           "report": {k: {"ticker": v["ticker"], "listings": (load(v["ref"]) or {}).get("results") or []}
                      for k, v in (found.get("report") or {}).items() if v.get("ref") and v["ref"].get("data")},
           "classes": {t: (load(r) or {}).get("share_classes") or []
                       for t, r in (found.get("classes") or {}).items() if r and r.get("data")}}
    # what the contracts' own records and references say, whether or not anything was read to work out a delta: each
    # underlying resolved, the contracts no market lists, each listed one's terms and its price at the close
    named_by = _named_by(found)
    established = _established(m, load) if named_by else {}
    flex = {}
    for s, (root, kind) in named_by.items():
        u = option_underlying(root, kind, *_refs_of(found.get("underlying_refs") or {}, root, load))
        if not u:
            continue
        ticker = u["ticker"]
        if ticker.startswith("I:"):
            info = established.get(("values", ticker)) or {}
            to = {"index": {"values": ticker}, **({"companies": info["companies"], "basket": info["basket"]}
                                                 if info.get("companies") else {})}
        else:  # a listing whose reference gives no FIGI reaches its company where its share classes confirm it
            to = {"company": u["company"]} if u.get("company") else None  # (harness.derive.rows._listed_option)
        flex[s] = {"ticker": ticker, **({"to": to} if to else {})}
    out["terms"] = {"underlying": flex, "unlisted": sorted(found.get("unlisted") or []),
                    "contracts": {s.rpartition("@")[0]: (p or {}).get("results") or {}
                                  for s, p in kept(found.get("contracts")).items()},
                    "at_close": {s.rpartition("@")[0]: p for s, p in kept(found.get("at_close")).items()}}
    # and what was read to work out a delta: the underlyings' and payers' closes, dividends and minutes, the curves
    terms = found.get("terms") or {}
    if terms:
        series_read = terms.get("series") or {}
        minutes: dict[str, dict[str, list]] = defaultdict(dict)
        for subject, payload in kept(terms.get("minutes")).items():
            ticker, _, day = subject.rpartition("@")
            minutes[ticker][day] = [[r["t"], r["c"]] for r in (payload or {}).get("results") or []]
        out["terms"].update({
            "payer": terms.get("payers") or {}, "minutes": dict(minutes),
            "closes": {t: closes(item, load) for t, item in series_read.items()},
            "paid": {t: paid(item, load) for t, item in series_read.items()},
            "curves": sorted((c for ref in (terms.get("curves") or {}).values() if ref and ref.get("data")
                              for c in (load(ref) or {}).get("results") or []), key=lambda c: c["date"])})
    return out


def _pairs(m: dict, load) -> frozenset[str]:
    """The pairs Coinbase Exchange lists of the coins the look-through's funds hold (harness.derive.rows.coin)."""
    return frozenset(p for p, r in (m.get("pairs") or {}).items()
                     if r and r.get("data") and (load(r) or {}).get("status") == "OK")


def _priced_reads(ctx: Inputs, reads: "_Reads", constituents: dict, reports: dict, assets: Kinds,
                  resolved: dict[str, dict], weights: dict[str, float], asof: str) -> dict:
    """What establishes the listing each row of a fund's current holdings that states shares names (MATH.md 3.1;
    harness.derive.rows.states_shares, established_by_price), for each row whose exposure reaches the fund's holder at
    or above the floor: the listings whose close on the row's date is its value per share (harness.venues.trackers,
    closed_at); the fund's own returns, each listing's, and each share's the fund holds that its identifiers resolve
    to a US listing, which together say whether the rows reproduce the fund's returns; OpenFIGI's share classes of
    each listing established, by which its company is confirmed as a security named by its ticker is
    (harness.derive.rows.listed_by_ticker); and OpenFIGI's listings of each ISIN its report names a derivative's
    security by, by which the report's row of the same position is known."""
    stated: dict[str, list[str]] = {}
    for fund, ref in constituents.items():
        if not ref.get("data") or ref is reports.get(fund):
            continue
        rows = _valid_rows(ctx.payload(ref))
        net = implied_net_assets(rows)
        subjects = sorted({price_subject(r) for r in rows if net and states_shares(r, assets.of(r))
                           and abs(r["market_value"] / net * weights.get(fund, 1.0)) >= FLOOR})
        if subjects:
            stated[fund] = subjects
    if not stated:
        return {}
    every = sorted({s for subjects in stated.values() for s in subjects})
    ctx.prefetch("closed_at", every, (asof, asof), stage="funds")
    answers = ctx.data_many("closed_at", every, covering=(asof, asof))
    base = {fund: [[found["ticker"], r["weight"]] for r in _valid_rows(ctx.payload(constituents[fund]))
                   if isinstance(r.get("weight"), (int, float)) and (found := resolved.get(row_key(r)))
                   and found.get("ticker") and found.get("market") != "home"] for fund in stated}
    matched = {t for ref in answers.values() if ref.get("data")
               for t in (x["ticker"] for x in (ctx.payload(ref) or {}).get("listings") or [])}
    held = {t for rows in base.values() for t, _ in rows}
    tickers = list(stated) + sorted((matched | held) - set(stated))
    priced = {"answers": answers, "funds": stated, "base": base,
              "series": return_series_many(ctx, tickers, asof, required=lambda t: t in stated)}
    chosen = _priced({"asof": asof, "constituents": constituents, "priced": priced}, ctx.payload, assets,
                     identify=False)
    named = sorted({x["ticker"] for found in chosen.values() for x in found["rows"].values()})
    ctx.prefetch("figi_ticker", named, (asof, asof), stage="funds")
    priced["classes"] = ctx.data_many("figi_ticker", named, covering=(asof, asof), required=False)
    isins = sorted({str(((r["derivative"].get("reference") or {}).get("security") or {}).get("isin") or "")
                    for fund in chosen if fund in reports and reports[fund].get("data")
                    for r in (ctx.payload(reports[fund]) or {}).get("results") or []
                    if isinstance(r, dict) and r.get("derivative")} - {""})
    reads.fetch("figi", isins)
    priced["isins"] = isins
    return priced


def _priced(m: dict, load, assets: Kinds | None = None, *, identify: bool = True) -> dict[str, dict]:
    """Each fund's rows that state shares of a listing its price names, as _priced_reads read them
    (harness.derive.rows.established_by_price), by fund: `rows`, by each row's price subject, the listing, its
    company's identifiers (the listing named by its ticker, which resolution confirms), the session whose close it
    is, every listing that closed there, and what the fund's rows reproduce of its returns; and `listed`, the US
    listings of each ISIN its report names a derivative's security by. A listing OpenFIGI does not read as one share
    class names no company here. With `identify` False, the listings alone, before their share classes are read."""
    found = m.get("priced") or {}
    if not found:
        return {}
    assets = assets if assets is not None else _kinds(m, load)
    answers, items = found["answers"], found["series"]
    figis = ((m.get("identity") or {}).get("figi") or {})
    listed = {isin: [str(x.get("ticker") or "").replace("/", ".") for x in (load(ref) or {}).get("listings") or []]
              for isin, ref in figis.items() if isin in set(found.get("isins") or []) and ref.get("data")}
    memo: dict[str, tuple[dict, dict]] = {}

    def returns_of(ticker: str) -> tuple[dict, dict]:
        if ticker not in memo:
            item = items.get(ticker)
            memo[ticker] = (closes(item, load), paid(item, load)) if item else ({}, {})
        return memo[ticker]

    out: dict[str, dict] = {}
    for fund, subjects in found["funds"].items():
        rows = _valid_rows(load(m["constituents"][fund]))
        net = implied_net_assets(rows)
        wanted = set(subjects)
        legs = [(price_subject(r), r["market_value"] / net) for r in rows
                if net and states_shares(r, assets.of(r)) and price_subject(r) in wanted]
        read = {s: load(answers[s]) or {} for s in subjects if (answers.get(s) or {}).get("data")}
        matches = {s: [x["ticker"] for x in read[s].get("listings") or []] for s in read}
        base = [(w, returns_of(t)) for t, w in found["base"].get(fund) or []]
        own = returns_of(fund)

        def reproduces(chosen: dict[str, str], own=own, base=base, legs=legs) -> float | None:
            return reproduced(own, base + [(x, returns_of(chosen[s])) for s, x in legs if s in chosen], m["asof"])

        chosen = established_by_price([(s, matches.get(s, [])) for s in subjects], reproduces)
        share = reproduces(chosen) if chosen else None
        named = {}
        for s, ticker in chosen.items():
            classes = (load(found["classes"][ticker]) or {}).get("share_classes") \
                if identify and (found.get("classes") or {}).get(ticker, {}).get("data") else None
            company = listed_by_ticker(ticker, classes) if identify else None
            if identify and company is None:
                continue
            named[s] = {"ticker": ticker, "company": {**(company or {}), "named_by": "price"},
                        "session": read[s].get("session"),
                        "close": next((x["close"] for x in read[s]["listings"] if x["ticker"] == ticker), None),
                        "matches": matches[s], "reproduced": share}
        if named:
            out[fund] = {"rows": named, "listed": listed}
    return out


def _slope(item: dict | None, load, asof: str) -> dict | None:
    """A fund's slope on its reference, from the two series the look-through read for it (harness.derive.rows.
    fit_slope), each read as the betas read a return: its closes, with its dividends added back."""
    if not item:
        return None
    return fit_slope((closes(item["fund"], load), paid(item["fund"], load)),
                     (closes(item["reference"], load), paid(item["reference"], load)), asof)


def _established(m: dict, load) -> dict:
    """What the look-through read establishes of each index a fund's derivatives are exposed to (harness.derive.rows):
    its values' symbol where Massive gives them, and the companies of the fund whose returns match them."""
    found = m.get("indexes") or {}
    return establish(_index_readings(m, load),
                     {s: load(r) for s, r in (found.get("trackers") or {}).items() if r and r.get("data")},
                     {f: load(r) for f, r in (found.get("baskets") or {}).items() if r and r.get("data")})


def _look(m: dict, references: dict, constituents: dict, resolved: Callable[[dict], dict],
          known: dict[str, str], assets: Kinds) -> dict:
    """Book each holding to what it is exposed to (MATH.md 1.2, 3.1). A company held directly, or a fund booked whole, is
    a name at its value. A fund expanded books, row by row, the dollars of exposure each row states, signed: the
    holding's dollars in the fund times the row's exposure per dollar of the fund, at each company the row reaches at
    or above the floor, by the listing its identifiers resolve to (`resolved`), or below it, where its identifiers are
    `known` as a company the portfolio names; a fund held inside a fund is expanded once more. Exposures need not sum
    to value: a fund's value is its holding's, which its own response moves (g), and its rows say which companies'
    own shocks reach it, and by how much. Money, a currency, an index whose companies are not established, and what
    resolves to no listing reach no name. Also each row resolved to a company at or above the floor, by its row; each
    fund expanded on each holding's path, with its dollars there; and the dollars of exposure the rows of each fund
    whose rows were read have that reach no name, expanded or booked whole, which its prior reads (channels.betas): to
    shares, to each currency other than the dollar, to each index whose values are read and each crypto pair, and what
    the rows leave undetermined (_unnamed)."""
    e: dict[str, float] = defaultdict(float)
    kinds: dict[str, dict] = {}
    links: dict[str, dict] = {}
    funds: dict[str, dict] = {}
    by_holding: dict[str, dict[str, float]] = {}  # each holding's dollars of exposure at each name it reaches
    paths: dict[str, dict[tuple, float]] = {}  # each holding's dollars at each name, by the funds they pass through
    reaches: dict[str, list[dict]] = {}  # each fund each holding expands, by the funds it is held through, its dollars
    reached: dict[str, float] = defaultdict(float)  # each fund's dollars, by every path to it
    holding = [None]
    inside: list[str] = []  # the funds being expanded, outermost first
    companies: list[tuple[dict, str]] = []  # each row resolved to a company at or above the floor, and its listing
    rest: dict[str, dict] = {}  # each fund's dollars of exposure reaching no name: to shares, and to each currency
    # each fund's exposure per dollar of it, on each path that reached it, with that path's dollars: to each name and
    # fund it reaches at the next level, and to what reaches no name (booked's parts and rest)
    part_plans: dict[str, list[tuple[float, dict[str, float]]]] = defaultdict(list)
    rest_plans: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    owned: dict[str, dict] = {}  # every dollar of the portfolio, by what it is (MATH.md 3.1's three lists; owned_entry)

    def own(key: str, dollars: float, **about) -> None:
        found = owned.setdefault(key, {"dollars": 0.0, **about})
        found["dollars"] += dollars

    account = own

    def book(ticker: str, dollars: float, kind: str, beta: str | None) -> None:
        """Dollars of exposure that stop at a name, the series its own betas are fitted on, none for cash; and the
        funds they reach it through."""
        nid = name_id(ticker)
        e[nid] += dollars
        kinds[nid] = {"kind": kind, "beta": beta}
        at = by_holding.setdefault(holding[0], {})
        at[nid] = at.get(nid, 0.0) + dollars
        path = paths.setdefault(holding[0], {})
        path[(tuple(inside), nid)] = path.get((tuple(inside), nid), 0.0) + dollars

    weighed: dict[str, float] = {}  # the path weight each link's weight was read at

    def link(fund: str, ticker: str, dollars: float, weight: float, dated: str | None, path_weight: float) -> None:
        """The link's dollars, summed over every path to the fund; its weight, the company's exposure per dollar of
        the fund, its rows' summed within one reading of the fund's rows, from the reading at the largest path weight,
        the one whose floor maps the most rows; and the date its oldest row is stated as of."""
        lid = f"looks_through_to:{name_id(fund)}>{name_id(ticker)}"
        found = links.setdefault(lid, {"relation": "looks_through_to", "from": name_id(fund), "to": name_id(ticker),
                                       "fields": {"weight": None, "dollars": 0.0, "holdings_asof": dated}})
        found["fields"]["dollars"] += dollars
        if dated and (found["fields"]["holdings_asof"] is None or dated < found["fields"]["holdings_asof"]):
            found["fields"]["holdings_asof"] = dated
        if abs(path_weight) > weighed.get(lid, -1.0):
            found["fields"]["weight"], weighed[lid] = weight, abs(path_weight)

    def whole(fund: str, reason: str | None) -> bool:
        funds.setdefault(fund, {"expanded": False, "reason": reason})
        return False

    def expand(fund: str, dollars: float, depth: int, path_weight: float) -> bool:
        payload = constituents.get(fund)
        if isinstance(payload, dict) and payload.get("status") == "ABSENT":  # no client here reads its holdings
            return whole(fund, None)
        if isinstance(payload, dict) and payload.get("status") == "LAPSED":  # no source still publishes them (MATH.md 3.1)
            reached[fund] += dollars
            return whole(fund, f"no source still publishes its holdings: the last were published on {payload['since']}")
        rows = _valid_rows(payload)  # each with what it is exposed to, and by how much (harness.derive.rows)
        reached[fund] += dollars
        if not rows:
            return whole(fund, f"no holdings are published for it on or before {m['asof']}")
        plan = []  # each listing the fund's rows reach, at its exposure per dollar of the fund, and the row's date
        unnamed = _reaching_none()  # per dollar of the fund, reaching no name
        stated = 0.0  # the fund's dollars its rows' values state
        mine: list[tuple] = []  # what its rows own, counted once the fund is expanded
        own = lambda key, amount, **about: mine.append((key, amount, about))  # noqa: E731
        # the rows a measured slope stands for: their value is the measured row's, whose exposure carries what they do
        standing = dollars * sum(float(r.get("weight") or 0.0) for r in rows if r.get("measured_by"))
        held_kinds: set[str] = set()  # the kinds its rows hold, by which what they leave unstated is read
        rows_kinds = fund_kinds(assets, references.get(fund), payload)  # the kinds its rows take, as fund_rows read them
        for r in rows:
            dated = r.get("report_date") or payload.get("effective_date")
            targets = _targets(r)
            value = dollars * float(r.get("weight") or 0.0)
            stated += value
            if r.get("measured_by"):
                continue  # counted with the measured row
            if r.get("measured"):
                value += standing
            kind = rows_kinds.of(r)
            held_kinds.add(kind)
            own(f"kind:{kind}", value - dollars * sum(x for _, x in targets), kind=kind)
            for target, x in targets:
                if abs(path_weight * x) < FLOOR:
                    company = known.get(target.get("isin")) or known.get(target.get("figi")) or \
                        known.get(cusip_of(target))
                    if company:
                        plan.append(("fund" if kind_of(references.get(company)) == "fund" else "name", company, x,
                                     dated, target))
                    else:
                        unnamed["shares"] += x
                        own(_row_key(target), dollars * x, row=_row_about(target))
                    continue
                found = resolved(target)
                if found.get("ticker"):
                    plan.append((found["kind"], found["ticker"], x, dated, target))
                    if found["kind"] == "name":
                        companies.append((target, found["ticker"]))
                else:
                    unnamed["shares"] += x
                    own(_row_key(target), dollars * x, row=_row_about(target))
            if not targets:
                _unnamed(r, unnamed)
        at = rest.setdefault(fund, _reaching_none())  # booked whole or expanded
        rest_plans[fund].append((dollars, unnamed))
        for key in ("shares", "undetermined"):
            at[key] += dollars * unnamed[key]
        for c, x in unnamed["currencies"].items():
            at["currencies"][c] += dollars * x
        for u, x in unnamed["underlyings"].items():
            at["underlyings"][u] += dollars * x
        left = unstated_kind(references.get(fund), held_kinds, dollars - stated)
        own(f"kind:{left}", dollars - stated, kind=left)
        if not plan:
            return whole(fund, "none of its holdings is a US-listed share or fund")
        for key, amount, about in mine:
            account(key, amount, **about)
        reaches.setdefault(holding[0], []).append({"funds": list(inside), "fund": fund, "dollars": dollars})
        inside.append(fund)
        company = defaultdict(float)  # each listing's exposure per dollar of the fund, its rows summed
        for _, ticker, x, _, _ in plan:
            company[ticker] += x
        part_plans[fund].append((dollars, {name_id(t): x for t, x in company.items()}))
        for kind, ticker, x, dated, target in plan:
            amount = dollars * x
            link(fund, ticker, amount, company[ticker], dated, path_weight)
            if kind == "name":
                book(ticker, amount, "name", ticker)
                # a depositary receipt's ISIN is the receipt's, issued where it trades: it names no company's home
                receipt = reference_type(references.get(ticker)) in RECEIPTS
                account(name_id(ticker), amount, row={**_row_about(target), **({"receipt": True} if receipt else {})})
            elif not (depth == 1 and expand(ticker, amount, 2, path_weight * x)):
                book(ticker, amount, "fund", ticker)  # a fund met at the next depth, or one that does not expand
                account(f"whole:{ticker}", amount, **_held_in(references.get(ticker)))
        inside.pop()
        # a fund reached by several paths is as deep as its deepest: one held inside another fund on any path is
        # fitted before that fund, whose prior reads it (channels.betas)
        before = funds.get(fund) or {}
        funds[fund] = {"expanded": True, "depth": max(depth, before["depth"]) if before.get("expanded") else depth}
        return True

    for hid, row in m["holdings"]["output"]["holds"].items():
        holding[0] = hid
        ticker, dollars = _held(row), row["dollars"]
        if row.get("contract"):
            by_holding[hid], paths[hid], reaches[hid] = {}, {}, []
            own("kind:derivative", dollars, kind="derivative")
            continue
        if ticker == CASH:
            book(CASH, dollars, "cash", None)
            own("kind:cash", dollars, kind="cash")
            continue
        if row.get("cash"):  # cash in another currency: that currency, at its rate, and cash (MATH.md 3.1)
            book(ticker, dollars, "other", ticker)
            own("kind:cash", dollars, kind="cash")
            continue
        kind = _abroad_kind(resolved(_abroad_row(ticker))) if _isin_form(ticker) else None
        kind = kind or kind_of(references.get(ticker))
        if kind == "fund" and ticker in assets.money_market:  # a money market fund is cash (MATH.md 3.1), and moves with
            book(ticker, dollars, "cash", None)               # nothing (3.2), as dollars held are
            own("kind:cash", dollars, kind="cash")
            continue
        if kind == "fund":
            if not expand(ticker, dollars, 1, 1.0):
                book(ticker, dollars, "fund", ticker)
                own(f"whole:{ticker}", dollars, **_held_in(references.get(ticker)))
        else:
            book(ticker, dollars, kind, ticker)
            if kind == "name":
                own(name_id(ticker), dollars)
            else:
                asset = COIN if series.is_crypto(ticker) else "currency" if series.is_pair(ticker) else "other"
                own(f"kind:{asset}", dollars, kind=asset)
    return {"e": e, "kinds": kinds, "links": links, "funds": funds, "by_holding": by_holding, "paths": paths,
            "reaches": reaches, "reached": reached, "companies": companies, "rest": rest, "owned": owned,
            "part_plans": part_plans, "rest_plans": rest_plans}


def _per_dollar(plans: list[tuple[float, dict[str, float]]]) -> dict[str, float]:
    """A fund's exposure per dollar of it, from each path that reached it: where every path's rows give the same
    exposures, as a fund reached once does, those exposures exactly, which no price moves; otherwise their mix, each
    path at its dollars, as the floor read them differently on paths of different weight."""
    if not plans:
        return {}
    first = plans[0][1]
    if all(p == first for _, p in plans[1:]):
        return dict(sorted(first.items()))
    total = sum(d for d, _ in plans)
    keys = sorted({k for _, p in plans for k in p})
    return {k: sum(d * p.get(k, 0.0) for d, p in plans) / total for k in keys} if total else {}


def _row_key(row: dict) -> str:
    """A share no name stands for, by what identifies it: its ISIN, or the one its US CUSIP makes; else its FIGI; else
    its ticker where it trades; else its name, so that two securities never stand as one."""
    isin = isin_of(row)
    if isin:
        return f"row:{isin}"
    if row.get("figi"):
        return f"row:{row['figi']}"
    if row.get("constituent_ticker"):
        return f"row:{row['constituent_ticker']}:{row.get('country_of_exchange') or ''}"
    return f"row::{row.get('constituent_name') or row.get('title') or ''}"


def _row_about(row: dict) -> dict:
    """What a share no name stands for says of itself: its name, its ticker where it trades, its ISIN (or its CUSIP's),
    and where."""
    about = {k: row.get(k) for k in ("constituent_name", "constituent_ticker", "country_of_exchange", "listing")
             if row.get(k)}
    name = row.get("constituent_name") or row.get("title")
    return {**({"constituent_name": name} if name else {}), **about,
            **({"isin": isin_of(row)} if isin_of(row) else {})}


def _reaching_none() -> dict:
    """A fund's exposure that reaches no name, per dollar of the fund: to shares, to each currency other than the
    dollar, to each index whose own values are read and each crypto pair (its underlying, by the series it is read
    under), signed; and what the rows leave undetermined, in absolute value."""
    return {"shares": 0.0, "currencies": defaultdict(float), "underlyings": defaultdict(float), "undetermined": 0.0}


def _unnamed(row: dict, unnamed: dict) -> None:
    """What a row that names no company is exposed to, for the fund's prior (MATH.md 3.2), each at the response of the
    asset it refers to: money in a currency other than the dollar, that currency; an index whose own values are read
    and whose companies are not established, that index (`to.index.values`), and a crypto pair (`to.pair`, its series'
    ticker), its own response; a share, or a derivative its report codes equity (Form N-PORT's DE) on what has no
    series of its own, shares, which keep the listed share's prior; a dollar of cash, none, which a dollar's response
    is. Everything else is the fund's own history's to determine (`undetermined`, in absolute value, since what each
    such row does is unknown and so is how they move together): a bond, an interest rate or credit derivative, a
    commodity, a coin or freight with no series, a swap whose underlying is not identified, money in a currency not
    named. An option, or a derivative whose exposure its rows do not state, is undetermined by its value's share of
    the fund, the only size its row states. A row a fund's measured slope stands for (`measured_by`: its swaps and
    futures on the reference, and its shares of it) adds nothing: the measured row carries it."""
    if row.get("measured_by"):
        return
    x = row.get("exposure")
    if x is None:
        if row.get("payoff") != "cash" and row.get("kind") in ("derivative", "commodity", "other") \
                and isinstance(row.get("weight"), (int, float)):
            unnamed["undetermined"] += abs(row["weight"])
        return
    if not x:
        return
    to = row.get("to") or {}
    currency = str(to.get("currency") or "").upper()
    category = str(row.get("asset_category") or "").upper()
    underlying = (to.get("index") or {}).get("values") or _pair(to.get("pair"))
    if currency == series.PRICE_CURRENCY or not currency and "cash" in (row.get("payoff"), row.get("kind")):
        return  # a dollar, which moves with nothing
    if currency:
        unnamed["currencies"][currency] += x
    elif underlying:
        unnamed["underlyings"][underlying] += x
    elif row.get("kind") == "equity" or category == "DE":
        unnamed["shares"] += x
    else:
        unnamed["undetermined"] += abs(x)


def _pair(ticker: Any) -> str | None:
    """The crypto pair a row's `to.pair` names, the ticker its series is read under (`X:BTCUSD`)."""
    return ticker if isinstance(ticker, str) and series.is_pair(ticker) else None


def _session_before(day: str) -> str:
    """The New York session before a day."""
    from harness import sessions
    d = date.fromisoformat(day[:10]) - timedelta(days=1)
    while not sessions.is_session("XNYS", d):
        d -= timedelta(days=1)
    return d.isoformat()


def _lapsed(given: dict, line: str | None, ctx: Inputs) -> bool:
    """Whether a fund's holdings record is from a source that no longer reads it: its latest processing before `line`,
    the session before the source's own latest processing (read live: IAU's and SLV's last processing 2019-07-03)."""
    if not line or not given.get("data"):
        return False
    processed = (ctx.payload(given) or {}).get("processed_date")
    return bool(processed and str(processed)[:10] < line)


def _later(report: dict, given: dict, ctx: Inputs) -> bool:
    """Whether a fund's Form N-PORT report is of a later date than the holdings its holdings source gives: read live,
    ETF Global's latest record of IAU and SLV is of 2019-07-03, and of TLCI, PJFV and PBJN of 2025, each last processed
    then, while each fund still trades."""
    if not (report.get("data") and given.get("data")):
        return False
    filed, held = (ctx.payload(report) or {}).get("effective_date"), (ctx.payload(given) or {}).get("effective_date")
    return bool(filed and held and str(filed) > str(held))


def fund_rows(m: dict, load, assets: Kinds | None = None) -> dict[str, Any]:
    """Each fund's rows as the look-through reads them, from its manifest: what each holds, each with what it is
    exposed to and by how much; its derivatives from its report where its holdings are ETF Global's; an index's
    companies where a fund matches it; its options at their deltas; and its slope where it is measured
    (rows.with_exposure, with everything the look-through read for them). The one reading of a fund's rows: the
    look-through computes from it, and so does whatever measures what the look-through makes of them."""
    assets = assets if assets is not None else _kinds(m, load)
    readings, derivatives = _index_readings(m, load), m.get("derivatives") or {}
    established, options, measured = _established(m, load), _options(m, load), m.get("measured") or {}
    pairs, rates = _pairs(m, load), _notional_rates(m, load)
    by_price = _priced(m, load, assets)
    placements = {isin: load(ref) for isin, ref in (m.get("placements") or {}).items() if ref.get("data")}
    return {f: with_exposure(_listed(load(r), placements), assets, readings, established,
                             report=load(derivatives[f]) if f in derivatives else None,
                             options=options, slope=_slope(measured.get(f), load, m["asof"]), pairs=pairs, rates=rates,
                             priced=by_price.get(f), census=_census(measured.get(f), load))
            if r.get("data") else {"status": "LAPSED", "since": r["lapsed"]} if r.get("lapsed")
            else {"status": "ABSENT"} for f, r in m["constituents"].items()}


def _unplaced(row: dict) -> bool:
    """A share a fund's report names by its ISIN alone, with no ticker and no exchange: where it trades is read from
    where the funds' holdings say it does."""
    return _is_listed(row) and bool(isin_of(row)) and not row.get("constituent_ticker") and \
        not row.get("country_of_exchange")


def _listed(payload: Any, placements: dict[str, dict]) -> Any:
    """A fund's holdings with each share its report names by its ISIN alone placed where the funds' holdings say it
    trades (harness.venues.massive.isin_listings): `listing`, `{"ticker", "country"}`, its ticker there and its
    exchange's country as most of the funds holding it give them. Its own fields stay as the report gives them, and its
    company is resolved by its identifiers as every row's is (harness.derive.identity)."""
    if not placements or not isinstance(payload, dict) or not payload.get("results"):
        return payload
    rows = []
    for r in payload["results"]:
        found = (placements.get(isin_of(r) or "") or {}).get("listings") if _unplaced(r) else None
        rows.append({**r, "listing": {"ticker": found[0]["ticker"], "country": found[0]["country"]}} if found else r)
    return {**payload, "results": rows}


def _census(item: dict | None, load) -> dict | None:
    """A measured fund's latest census, as the look-through read it (harness.venues.sec.ncen)."""
    ref = (item or {}).get("census")
    return load(ref) if ref and ref.get("data") else None


def multiples(constituents: dict[str, Any], resolved: Callable[[dict], dict]) -> dict[str, dict]:
    """Each fund on one reference (MATH.md 1.6, 3.1): one whose slope on its one reference is measured, which is the
    multiple of its own value the fund carries in its reference, whatever its census says (its census stays a stated
    fact of the fund, `seeks_multiple`). Its `multiple` is that slope, signed; its `reference` the name whose move it
    multiplies: the company, by the listing its identifiers resolve to, or for an index the fund whose returns follow
    the index's values. A fund that is itself its index's following fund pays the index's move by being it, and has
    no multiple of its own: it moves by its own price, as every fund looked through does."""
    out: dict[str, dict] = {}
    for fund, payload in constituents.items():
        if not isinstance(payload, dict) or not payload.get("measured"):
            continue
        row = next((r for r in payload.get("results") or [] if r.get("measured")), None)
        to = (row or {}).get("to") or {}
        follower = (to.get("basket") or {}).get("fund")
        listing = resolved(to["company"]) if not follower and isinstance(to.get("company"), dict) else {}
        reference = follower or listing.get("ticker")
        if reference and name_id(reference) != name_id(fund):
            out[fund] = {"reference": name_id(reference), "multiple": payload["measured"]["slope"]}
    return out


def _fund_rows_compute(m: dict, load) -> dict:
    """Each fund's rows (fund_rows), by fund."""
    return {"rows": fund_rows(m, load)}


def _lookthrough_compute(m: dict, load) -> dict:
    references = {t: load(r) if r.get("data") else None for t, r in m["references"].items()}
    assets = _kinds(m, load)
    constituents = load(m["fund_rows"])["rows"]
    listings = m.get("listings")
    us = us_composites(load(listings)) if listings and listings.get("data") else frozenset()
    held = {kind: {s: (load(ref) if ref.get("data") else None) for s, ref in refs.items()}
            for kind, refs in (m.get("identity") or {}).items()}
    held["ticker_ref"] = references
    look = Lookup(lambda kind, s: (held.get(kind) or {}).get(s), m["asof"])
    memo: dict[str, dict] = {}

    def resolved(row: dict) -> dict:
        key = row_key(row)
        if key not in memo:
            memo[key] = resolve(row, look)
        return memo[key]

    return {**booked(m, references, constituents, resolved, look, assets, us),
            "multiples": multiples(constituents, resolved)}


def booked(m: dict, references: dict, constituents: dict, resolved: Callable[[dict], dict], look: Lookup,
           assets: Kinds, us: frozenset[str] = frozenset()) -> dict:
    """What the look-through books from each fund's rows, each row's company resolved by `resolved` (MATH.md 1.2, 3.1):
    every name's e, the dollars of exposure the portfolio has to it, held directly and through funds, signed; each
    holding's dollars at each name, by the funds they pass through, and each fund it expands, with its dollars there;
    each fund's parts, the exposure per dollar of the fund to each name and each fund it holds at the next level, which
    its response leans on where its history is short; and what each fund whose rows were read holds."""
    # The companies the portfolio names at the floor, held directly or above it in a fund: a row below the floor whose
    # ISIN or FIGI is one of theirs reaches it whatever its size, so its own shock reaches every dollar of it.
    at_floor = _look(m, references, constituents, resolved, {}, assets)
    known: dict[str, str] = {}
    for row, listing in at_floor["companies"]:  # a company resolved at the floor, by every listing of its share class
        known.update({x: listing for x in (row.get("isin"), row.get("figi"), cusip_of(row), *look.composites_of(row))
                      if x})
    if constituents:  # a company held directly, by every listing of its share classes and its ordinary's
        for row in m["holdings"]["output"]["holds"].values():
            ticker = _held(row)
            if not row.get("cash") and kind_of(references.get(ticker)) == "name":
                for x in identifiers_of(ticker, references, look):
                    known.setdefault(x, ticker)
    for ticker in m.get("shocked") or []:  # a company or a fund a consequence names, whose own shock reaches every
        # dollar of exposure to it (MATH.md 1.6, 3.1): known as one held directly is
        if _isin_form(ticker):
            isin = ticker.split(":")[0]
            for x in (isin, *look.composites_of_isin(isin)):
                known.setdefault(x, ticker)
        elif kind_of(references.get(ticker)) in ("name", "fund"):
            for x in identifiers_of(ticker, references, look):
                known.setdefault(x, ticker)
    for nid, info in at_floor["kinds"].items():
        found = (references.get(ticker_of(nid)) or {}).get("results") or {} if info["kind"] == "name" else {}
        known.update({x: ticker_of(nid) for x in (found.get("composite_figi"), found.get("share_class_figi")) if x})
    for cusip in m.get("cusips") or []:  # a row below the floor named by its CUSIP alone: by its listing's FIGIs
        if cusip not in known:
            listed = next((known[x] for r in look.listing_then(cusip, m["asof"])
                           for x in (r.get("composite_figi"), r.get("share_class_figi")) if x in known), None)
            if listed:
                known[cusip] = listed
    found = _look(m, references, constituents, resolved, known, assets)
    e, kinds, links, funds, paths = found["e"], found["kinds"], found["links"], found["funds"], found["paths"]
    for passed in (1, 2):  # each fund from its own rows; then each whose rows are funds, by those funds' own kinds
        divided = {f: (funds[f].get("holds") or {}).get("by_kind") for f in funds if passed == 2}
        for fund, payload in constituents.items():
            if fund in funds:
                priced = {rec["to"] for rec in links.values() if rec["from"] == name_id(fund)}
                funds[fund]["holds"] = what_it_holds(fund, payload, references, found["reached"][fund], priced, us,
                                                     resolved, assets, {f: d for f, d in divided.items() if d})
    found, securities, holdings = as_companies(found, m, references, resolved, look)
    e, kinds, links, paths = found["e"], found["kinds"], found["links"], found["paths"]
    # An expanded fund is fitted on its own series, as every holding is; its parts are what its rows reach at the next
    # level, each name and each fund held inside it, per dollar of the fund, each name by its company's id; and its
    # rest, what they reach no name with, per dollar of the fund (_per_dollar). They need not sum to one, and no price
    # moves them, so the fits that read them (channels.betas) are made again only when what a fund holds changes.
    company = {name_id(s): cid for cid, by in securities.items() for s in by}

    def by_company(plan: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for nid, x in plan.items():
            out[company.get(nid, nid)] += x
        return dict(out)

    for fund, info in funds.items():
        total = found["reached"][fund]
        plans = found["rest_plans"].get(fund)
        if plans:  # its rows were read: what they reach no name with, per dollar of the fund
            info["rest"] = {**{key: _per_dollar([(d, {key: p[key]}) for d, p in plans]).get(key, 0.0)
                               for key in ("shares", "undetermined")},
                            **{key: _per_dollar([(d, dict(p[key])) for d, p in plans])
                               for key in ("currencies", "underlyings")}}
        if info["expanded"]:
            kinds.setdefault(name_id(fund), {"kind": "fund", "beta": fund})["expanded"] = True
            info["dollars"] = total
            info["parts"] = _per_dollar([(d, by_company(p)) for d, p in found["part_plans"].get(fund) or []])
    named = {rec["to"] for rec in links.values()}
    return {"asof": m["asof"], "e": dict(e), "kinds": kinds, "funds": funds, "by_holding": found["by_holding"],
            "securities": securities, "holdings": holdings, "owned": found["owned"],
            "paths": {hid: [{"funds": list(through), "name": nid, "dollars": dollars}
                            for (through, nid), dollars in reaching.items()] for hid, reaching in paths.items()},
            "reaches": found["reaches"],
            "objects": {nid: {"type": "Name", "fields": {}} for nid in sorted(named)}, "links": links}


def _held_in(reference: Any) -> dict:
    """What a fund booked whole holds its value in where no holdings of it say: a commodity, for a fund the SEC
    classes as a commodity trust or pool (commodity_fund), as its unlabelled rows are; nothing said otherwise."""
    return {"kind": "commodity"} if commodity_fund(reference) else {}


def _abroad_kind(found: dict) -> str | None:
    """What a listing abroad held is, where its share class's listings in the US say (identity.resolve, as name_of
    reads it): a share of its company, or a fund's; None where its identifiers named no listing of it."""
    return found.get("kind") if found.get("cik") or found.get("type") else None


def _abroad_row(listing: str) -> dict:
    """A listing abroad, `{ISIN}:{country}`, as a fund's row of it reads, for resolution (identity.name_of's)."""
    isin, country = listing.split(":", 1)
    return {"isin": isin, "country_of_exchange": country, "asset_class": "Equity"}


def as_companies(found: dict, m: dict, references: dict, resolved: Callable[[dict], dict],
                 look: Lookup) -> tuple[dict, dict, dict]:
    """The look-through's bookings by company (MATH.md 3.1): a company is one name, whatever securities the portfolio holds
    it by, each booked by the security it rides on. A security's company is the CIK the SEC gives its US listing, else
    the one Massive's record of it gives (Lookup.company_cik), where one does; else, for a listing abroad, its share
    class (its ISIN); else the listing alone. The company's id is the one the
    store's links name it by, a holding's name or a name a consequence or a value names, where one does: chosen once and
    kept. Else it is its first US ticker, else its home listing: of a share class held on several markets, the one in
    the country its ISIN names, else the first. Its pricing security is the one the member holds; of several held, its home listing where that is one of
    them, else the first given; and where the member holds none, its listing on its home market, a row abroad whose ISIN
    is not a US one (the one in its ISIN's country first), else its id's listing where the portfolio holds it there,
    else the first of its listings. Returns the bookings with every name a company's, each company's dollars by
    security (`securities`), and each holding's name and security (`holdings`)."""
    kinds = found["kinds"]
    records: dict[str, dict] = {}  # each listing a row resolved to, with the CIK that named its company
    for row, _ in found["companies"]:
        rec = resolved(row)
        if rec.get("ticker"):
            records.setdefault(rec["ticker"], rec)
    held: dict[str, list[str]] = defaultdict(list)  # each security held directly, by holding, in the order given
    for hid, row in m["holdings"]["output"]["holds"].items():
        held[_held(row)].append(hid)
    secs = [ticker_of(n) for n, info in kinds.items() if info["kind"] == "name"]

    def held_abroad(sec: str) -> dict:  # a listing abroad the member holds or a consequence names, by its symbol
        reference = (references.get(sec) or {}).get("results") or {}
        return abroad(sec, reference, look) if series.symbol_abroad(sec) and reference.get("market") == "home" else {}

    def cik_of(sec: str) -> int | None:
        if records.get(sec, {}).get("cik"):
            return records[sec]["cik"]
        if sec in held and _isin_form(sec):  # a listing abroad the member holds: its share class's company
            return resolved(_abroad_row(sec)).get("cik")
        if series.symbol_abroad(sec):
            return held_abroad(sec).get("cik")
        if us_ticker(sec):
            return look.company_cik(sec, ((references.get(sec) or {}).get("results") or {}).get("cik"))
        return None

    def isin_of(sec: str) -> str | None:  # a listing abroad's share class, by its ISIN
        if _isin_form(sec):
            return sec.split(":")[0]
        return records.get(sec, {}).get("isin") or held_abroad(sec).get("isin") if series.symbol_abroad(sec) else None

    def key_of(sec: str) -> str:
        cik, isin = cik_of(sec), isin_of(sec)
        return f"cik:{cik}" if cik else f"isin:{isin}" if isin else f"ticker:{sec}"

    groups: dict[str, list[str]] = defaultdict(list)
    for sec in secs:
        groups[key_of(sec)].append(sec)
    # The ids the store's links name a company by: a holding's name, and a name a consequence or a value names. An id
    # is chosen once by the rule and kept (MATH.md 3.1), so where a link names the company, its id is the one the link
    # names, whatever the rule would choose now (a ticker the SEC now lists first, a form of id since replaced).
    # Only a company's id names a company: a fund's reference can carry its sponsor's CIK (Massive gives XLF State
    # Street's, 93751), and a fund is its own name, never the company its sponsor is.
    linked: dict[str, set[str]] = defaultdict(set)
    for row in m["holdings"]["output"]["holds"].values():
        if not row.get("contract") and not row.get("cash") and row.get("name") and \
                (kinds.get(row["name"]) or {}).get("kind") == "name":
            linked[key_of(_held(row))].add(ticker_of(row["name"]))
    for ticker in m.get("shocked") or []:
        if (kinds.get(name_id(ticker)) or {}).get("kind") == "name" or kind_of(references.get(ticker)) == "name":
            linked[key_of(ticker)].add(ticker)

    company: dict[str, tuple[str, str]] = {}  # each security: its company's id, and the security pricing it
    for key, members in groups.items():
        members = sorted(members)
        homes = sorted((s for s in members if _home(s, isin_of(s))), key=lambda s: (not _native(s, isin_of(s)), s))
        named = min(linked[key]) if linked.get(key) else \
            company_id(look, int(key[4:]) if key.startswith("cik:") else None, homes[0] if homes else members[0])
        given = sorted((s for s in members if s in held), key=lambda s: min(held[s]))
        for s in members:
            company[s] = (named, pricing_security(named, given, members, isin_of))

    def to(nid: str) -> str:
        found_ = company.get(ticker_of(nid)) if nid.startswith("name:") else None
        return name_id(found_[0]) if found_ else nid

    e: dict[str, float] = defaultdict(float)
    securities: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for nid, dollars in found["e"].items():
        e[to(nid)] += dollars
        if ticker_of(nid) in company:
            securities[to(nid)][ticker_of(nid)] += dollars
    remapped = {nid: info for nid, info in kinds.items() if ticker_of(nid) not in company or info["kind"] != "name"}
    for sec, (named, pricing) in company.items():
        remapped[name_id(named)] = {"kind": "name", "beta": pricing, "security": pricing}
    by_holding = {hid: dict(_summed((to(n), x) for n, x in at.items())) for hid, at in found["by_holding"].items()}
    paths = {hid: dict(_summed(((through, to(n)), x) for (through, n), x in at.items()))
             for hid, at in found["paths"].items()}
    links: dict[str, dict] = {}
    for rec in found["links"].values():
        target = to(rec["to"])
        lid = f"looks_through_to:{rec['from']}>{target}"
        if lid not in links:
            links[lid] = {**rec, "to": target, "fields": dict(rec["fields"])}
            continue
        f, g = links[lid]["fields"], rec["fields"]
        f["dollars"] += g["dollars"]
        f["weight"] = (f["weight"] or 0.0) + (g["weight"] or 0.0) if f["weight"] is not None or g["weight"] else None
        if g["holdings_asof"] and (f["holdings_asof"] is None or g["holdings_asof"] < f["holdings_asof"]):
            f["holdings_asof"] = g["holdings_asof"]
    holdings = {hid: {"name": row["name"] if row.get("contract") else f"cash:{row['cash']}" if row.get("cash") else
                      to(name_id(_held(row))),
                      "security": _held(row), **({"contract": True, "side": row["side"]} if row.get("contract") else {})}
                for hid, row in m["holdings"]["output"]["holds"].items()}
    owned: dict[str, dict] = {}
    for key, entry in found.get("owned", {}).items():
        target = to(key) if key.startswith("name:") else key
        merged = owned.setdefault(target, {k: v for k, v in entry.items() if k != "dollars"} | {"dollars": 0.0})
        merged["dollars"] += entry["dollars"]
    out = {**found, "e": e, "kinds": remapped, "by_holding": by_holding, "paths": paths, "links": links,
           "owned": owned}
    return out, {nid: dict(by) for nid, by in securities.items()}, holdings


def identifiers_of(ticker: str, references: dict, look: Lookup) -> set[str]:
    """What names a company held directly or named by a consequence wherever its shares are listed, so a fund's row of
    it is known as it at any weight (MATH.md 3.1, the floor's exception): a US listing's share classes' listings and its
    ordinary's (identity.held_identifiers); a listing abroad by its symbol, every listing's composite FIGI of its share
    class and its ISIN (identity.abroad)."""
    reference = (references.get(ticker) or {}).get("results") or {}
    if series.symbol_abroad(ticker) and reference.get("market") == "home":
        found = abroad(ticker, reference, look)
        return set(found["composites"]) | ({found["isin"]} if found["isin"] else set())
    return held_identifiers(ticker, reference, look)


def _country_of(listing: str) -> str:
    """The country a listing abroad trades in: its exchange's, by its symbol's suffix (harness.venues.yahoo.region_of),
    or the country `{ISIN}:{country}` names."""
    return listing.split(":")[1] if _isin_form(listing) else region_of(listing).upper()


def _isin_form(ticker: str) -> bool:
    """A listing abroad named `{ISIN}:{country}`, as a store kept it before a listing was named by its symbol."""
    return series.is_abroad(ticker) and not series.symbol_abroad(ticker)


def _home(sec: str, isin: str | None = None) -> bool:
    """Whether a security is a listing abroad of a share class not registered in the US: a company's home line. `isin`
    is its share class's, which `{ISIN}:{country}` carries in its name."""
    isin = isin or (sec.split(":")[0] if _isin_form(sec) else None)
    return series.is_abroad(sec) and not (isin or "").startswith("US")


def _native(sec: str, isin: str | None = None) -> bool:
    """Whether a listing abroad is in the country its ISIN names: its exchange's, by its symbol's suffix (as a region
    of listings is, harness.venues.yahoo.region_of), or the country `{ISIN}:{country}` names."""
    if _isin_form(sec):
        return sec[:2] == sec.split(":")[1]
    return series.symbol_abroad(sec) and bool(isin) and region_of(sec).upper() == isin[:2]


def pricing_security(named: str, given: list[str], members, isin_of: Callable[[str], str | None] = lambda s: None
                     ) -> str:
    """The security a company is priced through (MATH.md 3.1): the one the member holds (`given`, in the order given); of
    several, its home listing where that is one of them, else the first; and where the member holds none, its listing
    on its home market among the securities the portfolio holds it by (`members`), the one in its ISIN's country first,
    else its id's listing (`named`) where the portfolio holds it there, else the first of them."""
    if given:
        return next((s for s in given if _home(s, isin_of(s)) or s == named), given[0]) if len(given) > 1 else given[0]
    members = sorted(members)
    homes = sorted((s for s in members if _home(s, isin_of(s))), key=lambda s: (not _native(s, isin_of(s)), s))
    return homes[0] if homes else named if named in members or not members else members[0]


def _summed(pairs) -> dict:
    out: dict = defaultdict(float)
    for k, x in pairs:
        out[k] += x
    return out


def _name_fields(output: dict, nid: str) -> dict:
    """What the look-through gives every Name, its e, and a fund whose holdings it read, what it holds."""
    fund = output["funds"].get(ticker_of(nid)) or {}
    return {"exposure": output["e"].get(nid, 0.0), **({"holds": fund["holds"]} if fund.get("holds") else {})}


# Each fund's rows, read whole once for what the funds hold: a fund's rows change when a source publishes its holdings,
# never with today's prices, so they are a value of their own, which the look-through and what values a fund's
# contracts read (harness.derive.option_funds, valuation) name by their Computation.
FUND_ROWS = Derivation(
    "fund_rows", 6, "Portfolio", _rows_inputs, _fund_rows_compute,
    subject_of=lambda store, entity: "portfolio", stage="funds",
)

LOOKTHROUGH = Derivation(
    "lookthrough", 66, "Portfolio", _lookthrough_inputs, _lookthrough_compute,
    subject_of=lambda store, entity: "portfolio", stage="funds",
    produces=("Name", "looks_through_to"),
    # Every Name has an e: what the look-through books to it, and 0 where it books nothing.
    extra=(("Name", ("exposure", "holds"), _name_fields),),
)

DERIVATIONS = (CALENDAR_DERIVATION, PRICE, REFERENCE, HOLDINGS, FUND_ROWS, LOOKTHROUGH)
