"""A series' history on the store's sessions (MATH.md 3.1, 3.2): what each kind of instrument's returns read, and its
closes and returns on the New York sessions of the window, in dollars and, for a listing abroad, in its own currency
translated.

Three kinds of series:
- A US listing: its daily bars and dividends (harness.derive.portfolio.return_series), each close its session's.
- A currency or crypto pair: its hourly prices, read at each New York session's close (series.session_closes).
- A listing abroad, `{ISIN}:{country}`: its closes on its home market in its own currency, each with the moment its
  exchange struck it, and its dividends (`home`, harness.venues.yahoo); and, where its currency is not the dollar, that
  currency's hourly rate in dollars (`hourly` of C:{CCY}USD). Each close belongs to the first New York session whose
  close is at or after its moment (series.assigned), the later of two in one session standing; its dollar close is its
  local close times the rate struck at its moment where the currency's hourly rates reach, and before them the daily
  fixing of its own local session's date (series.rate_for). Its translated local return over a span,
  a = r_local (1 + r_FX), with r_FX the rate's return between the two closes' moments, makes the dollar return
  exactly a + r_FX, so the currency enters at a coefficient of one and is fitted once, on New York's clock, for every
  listing quoted in it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from harness.derive import series
from harness.derive.engine import Inputs, Unavailable
from harness.derive.portfolio import (closes, dividend_inputs, dividend_reads_many, income, paid, price_series,
                                      return_series, return_series_many)

USD = series.PRICE_CURRENCY


def ordered_channels(ctx: Inputs) -> list[dict]:
    return sorted(ctx.objects("Factor"), key=lambda c: (c["stated"]["order"], c["id"]))


def calendar_inputs(ctx: Inputs, asof: str) -> dict:
    """The sessions' calendar: the first channel's proxy's daily bars, the market's own sessions."""
    channels = ordered_channels(ctx)
    if not channels:
        raise Unavailable("the store has no factors")
    first = channels[0]["stated"]["proxy"]
    if series.is_pair(first) or series.is_abroad(first):
        raise Unavailable(f"the first factor's proxy is listed in the US, since every factor is measured over New "
                          f"York's sessions; {first} is not")
    return price_series(ctx, first, asof)


def window_sessions(calendar: dict, load, asof: str, days: int = series.WINDOW) -> list[str]:
    """The window's sessions: the `days` sessions ending at the as-of, with the session before them, whose close the
    first return needs."""
    return series.window(closes(calendar, load), asof, days)


def series_inputs(ctx: Inputs, ticker: str, asof: str, *, required: bool = True) -> dict:
    """What a series' history reads over the window, by its kind."""
    window = (series.lookback(asof), asof)
    if series.is_abroad(ticker):
        home = ctx.data("home", ticker, covering=window, required=required)
        out = {"ticker": ticker, "kind": "abroad", "ref": home}
        currency = (ctx.payload(home) or {}).get("currency") if home.get("data") else None
        if currency and currency != USD:
            out["currency"] = currency
            out["rate"] = ctx.data("hourly", series.dollar_pair(currency), covering=window, required=required)
        return out
    item = return_series(ctx, ticker, asof, required=required)
    return {**item, "kind": "pair" if series.is_pair(ticker) else "listing"}


def moves_inputs(ctx: Inputs, ticker: str, span: tuple[str, str], *, record: tuple[str, str] | None = None,
                 required: bool = True) -> dict:
    """What a proxy's moves over a span read (MATH.md 3.2), as series_inputs reads its history over the window: a US
    listing's prices over the span and its distributions over `record`, the span its income accrues over (the span
    itself where none is given); a pair's prices; and a listing abroad's home prices, which carry its dividends, with
    its currency's rates, over `record`."""
    reach = tuple(record or span)
    if series.is_abroad(ticker):
        home = ctx.data("home", ticker, covering=reach, required=required)
        out = {"ticker": ticker, "kind": "abroad", "ref": home}
        currency = (ctx.payload(home) or {}).get("currency") if home.get("data") else None
        if currency and currency != USD:
            out["currency"] = currency
            out["rate"] = ctx.data("hourly", series.dollar_pair(currency), covering=reach, required=required)
        return out
    if series.is_pair(ticker):
        return {"ticker": ticker, "kind": "pair", "ref": ctx.data("hourly", ticker, covering=span, required=required)}
    return {"ticker": ticker, "kind": "listing", "ref": ctx.data("bars", ticker, covering=span, required=required),
            **dividend_inputs(ctx, ticker, reach, required=required)}


def moves_inputs_many(ctx: Inputs, tickers, span: tuple[str, str], *, record: tuple[str, str] | None = None,
                      required: bool = True) -> dict[str, dict]:
    """moves_inputs for many proxies, keyed by ticker in the order given: each item exactly what moves_inputs gives
    it. Every read of every kind is started first, side by side (Inputs.prefetch), so that a call stands aside once for
    all of them; then each kind is read for all its proxies at once (Inputs.data_many), and what a listing's dividend
    records and a listing abroad's currency read beyond themselves after them, again all at once."""
    tickers = list(dict.fromkeys(tickers))
    reach = tuple(record or span)
    abroad = [t for t in tickers if series.is_abroad(t)]
    pairs = [t for t in tickers if series.is_pair(t)]
    listed = [t for t in tickers if t not in abroad and t not in pairs]
    ctx.prefetch("bars", listed, span, wait=False)
    ctx.prefetch("dividends", listed, reach, wait=False)
    ctx.prefetch("hourly", pairs, span, wait=False)
    ctx.prefetch("home", abroad, reach, wait=False)
    out: dict[str, dict] = {}
    for t, home in (ctx.data_many("home", abroad, covering=reach, required=required) if abroad else {}).items():
        out[t] = {"ticker": t, "kind": "abroad", "ref": home}
        currency = (ctx.payload(home) or {}).get("currency") if home.get("data") else None
        if currency and currency != USD:
            out[t]["currency"] = currency
    quoted = list(dict.fromkeys(series.dollar_pair(i["currency"]) for i in out.values() if i.get("currency")))
    rates = ctx.data_many("hourly", quoted, covering=reach, required=required) if quoted else {}
    for item in out.values():
        if item.get("currency"):
            item["rate"] = rates[series.dollar_pair(item["currency"])]
    for t, ref in (ctx.data_many("hourly", pairs, covering=span, required=required) if pairs else {}).items():
        out[t] = {"ticker": t, "kind": "pair", "ref": ref}
    if listed:
        bars = ctx.data_many("bars", listed, covering=span, required=required)
        reads = dividend_reads_many(ctx, ctx.data_many("dividends", listed, covering=reach, required=required), reach,
                                    required)
        for t in listed:
            out[t] = {"ticker": t, "kind": "listing", "ref": bars[t], **reads[t]}
    return {t: out[t] for t in tickers}


def moves(item: dict, load, sessions: list[str], since: str | None = None, until: str | None = None) -> series.Moves:
    """Every window's move over a proxy's series, in dollars (MATH.md 3.2): what holding it earned less the income it
    accrued (series.Moves), its income accruing from the distributions going ex from `since` through `until`, the
    record read (series.Accrual), whatever else the payload holds. A US listing's distributions are added back on their ex-dates and its recurring ones
    accrue; a pair pays none. A listing abroad moves by its dollar closes, each at the rate struck with it
    (home_closes), on New York's `sessions`; each dividend is added back on the close it goes ex by, at that close's
    rate, as its returns add it back (returns), and accrues as income, each session's accrual at its close's rate:
    its source states no kind of dividend, and an ordinary dividend is a regular one."""
    if item["kind"] == "pair":
        return series.Moves(closes(item, load))
    if item["kind"] != "abroad":
        return series.Moves(closes(item, load), paid(item, load), income(item, load, since, until))
    at = home_closes(item, load, sessions)
    local = dividends(item, load)
    accrual = series.Accrual(local, since=since, until=until)
    days = [d for d in sessions if d in at and at[d]["close"] is not None]
    paid_out, accrued, total = {}, {}, 0.0
    for i, d in enumerate(days):
        if i:
            a, b = at[days[i - 1]], at[d]
            cash = sum(v for x, v in local.items() if a["day"] < x <= b["day"])
            if cash:
                paid_out[d] = cash * b["rate"]
            total += (accrual(b["day"]) - accrual(a["day"])) * b["rate"]
        accrued[d] = total
    return series.Moves({d: at[d]["close"] for d in days}, paid_out, accrued.__getitem__)


def local_moves(item: dict, load, sessions: list[str], since: str | None = None, until: str | None = None
                ) -> tuple[series.Moves, dict[str, float]]:
    """A listing abroad's moves in its own currency, as `moves` reads them in dollars: its local closes on New York's
    `sessions`, each dividend added back on the close it goes ex by and accruing as income; and its rate in dollars on
    each of those sessions, as struck with the close (home_closes)."""
    at = home_closes(item, load, sessions)
    local = dividends(item, load)
    accrual = series.Accrual(local, since=since, until=until)
    days = [d for d in sessions if d in at and at[d]["local"] is not None]
    paid_out, accrued, total = {}, {}, 0.0
    for i, d in enumerate(days):
        if i:
            a, b = at[days[i - 1]], at[d]
            cash = sum(v for x, v in local.items() if a["day"] < x <= b["day"])
            if cash:
                paid_out[d] = cash
            total += accrual(b["day"]) - accrual(a["day"])
        accrued[d] = total
    rates = {d: at[d]["rate"] for d in days if at[d]["rate"]}
    return series.Moves({d: at[d]["local"] for d in days}, paid_out, accrued.__getitem__), rates


def series_inputs_many(ctx: Inputs, tickers, asof: str, *, required: Callable[[str], bool] = lambda t: True
                       ) -> dict[str, dict]:
    """series_inputs for many tickers, keyed by ticker in the order given: each ticker's item is exactly what
    series_inputs gives it, what every ticker of a kind reads first read for all of them at once (Inputs.data_many):
    a listing abroad's closes, then its currency's rates one ticker at a time; every other ticker's prices and
    dividends (portfolio.return_series_many)."""
    tickers = list(dict.fromkeys(tickers))
    window = (series.lookback(asof), asof)
    out: dict[str, dict] = {}
    abroad = [t for t in tickers if series.is_abroad(t)]
    for needed in (True, False):
        group = [t for t in abroad if bool(required(t)) == needed]
        for t, home in (ctx.data_many("home", group, covering=window, required=needed) if group else {}).items():
            out[t] = {"ticker": t, "kind": "abroad", "ref": home}
    for t in abroad:
        home = out[t]["ref"]
        found = (ctx.payload(home) or {}).get("currency") if home.get("data") else None
        if found and found != USD:
            out[t]["currency"] = found
            out[t]["rate"] = ctx.data("hourly", series.dollar_pair(found), covering=window, required=required(t))
    rest = [t for t in tickers if not series.is_abroad(t)]
    for t, item in return_series_many(ctx, rest, asof, required=required).items():
        out[t] = {**item, "kind": "pair" if series.is_pair(t) else "listing"}
    return {t: out[t] for t in tickers}


def currency(item: dict, load) -> str:
    """The currency a series' prices are quoted in."""
    if item["kind"] == "abroad":
        return item.get("currency") or ((load(item["ref"]) or {}).get("currency") if item["ref"].get("data") else
                                        None) or USD
    if item["kind"] == "pair":
        payload = load(item["ref"]) if item["ref"].get("data") else {}
        return str((payload or {}).get("quote") or USD)
    return USD


def _rate(item: dict, load) -> Any:
    ref = item.get("rate")
    return load(ref) if ref and ref.get("data") else None


def home_closes(item: dict, load, sessions: list[str]) -> dict[str, dict]:
    """A listing abroad's closes by the New York session each belongs to: its local close, the moment it was struck,
    its local session's date, and the rate in dollars it converts at with the dollar close (none where no rate was
    struck): the rate struck at that moment where the pair's hourly rates reach, and before them the daily fixing of
    its own local session's date (series.rate_for), with when (`rate_moment`) and by whom (`rate_source`) it was
    struck."""
    if not item["ref"].get("data"):
        return {}
    payload = load(item["ref"]) or {}
    rates = series.rate_index(_rate(item, load)) if item.get("currency") else None
    out: dict[str, dict] = {}
    for row in payload.get("results") or []:
        day = series.assigned(row["moment"], sessions)
        if day is None or (day in out and out[day]["moment"] >= row["moment"]):
            continue
        found = None if rates is None else series.rate_for(rates, row["moment"], row["session"])
        rate = 1.0 if rates is None else (found or {}).get("price")
        out[day] = {"local": float(row["c"]), "moment": row["moment"], "day": row["session"], "rate": rate,
                    "close": float(row["c"]) * rate if rate else None,
                    **({"rate_moment": found["time"], "rate_source": found["source"]} if found else {})}
    return out


def struck_early(item: dict, load, sessions: list[str]) -> bool:
    """Whether most of a series' closes in the window are struck before the New York close of the session each
    belongs to (series.assigned), by each close's moment: a listing on an exchange that closes before New York's,
    whose prices carry the rest of New York's session on its next close. Where its closes divide evenly, its latest
    decides, its exchange's hours as they now stand. Every other series' closes are New York's own, or read at it."""
    if item["kind"] != "abroad" or not item["ref"].get("data"):
        return False
    latest: dict[str, str] = {}  # each session's close, its latest moment, as home_closes takes it
    for row in (load(item["ref"]) or {}).get("results") or []:
        day = series.assigned(row["moment"], sessions)
        if day is not None and (day not in latest or row["moment"] > latest[day]):
            latest[day] = row["moment"]
    early = [datetime.fromisoformat(moment.replace("Z", "+00:00")) < series.ny_close(day)
             for day, moment in sorted(latest.items())]
    if not early:
        return False
    return early[-1] if 2 * sum(early) == len(early) else 2 * sum(early) > len(early)


def dollar_closes(item: dict, load, sessions: list[str]) -> dict[str, float]:
    """A series' close in dollars on each New York session it has one for."""
    if item["kind"] == "abroad":
        return {d: c["close"] for d, c in home_closes(item, load, sessions).items() if c["close"] is not None}
    return closes(item, load)


def dividends(item: dict, load) -> dict[str, float]:
    """A US listing's dividends by ex-date in dollars (portfolio.paid); a listing abroad's by its local ex-date in its
    own currency; a pair pays none."""
    if item["kind"] == "abroad":
        payload = load(item["ref"]) if item["ref"].get("data") else {}
        out: dict[str, float] = {}
        for d in (payload or {}).get("dividends") or []:
            out[d["ex_date"]] = out.get(d["ex_date"], 0.0) + float(d["amount"])
        return out
    if item["kind"] == "pair":
        return {}
    return paid(item, load)


def returns(item: dict, load, sessions: list[str]) -> dict:
    """A series' returns over spans of `sessions` (series.spans): `dollar`, what holding it earned in dollars; `own`,
    what it is fitted on, the dollar return for a US listing or a pair and the translated local return for a listing
    abroad; and, abroad, `fx`, its currency's return between the closes' moments."""
    if item["kind"] != "abroad":
        found = series.spans(closes(item, load), sessions, dividends(item, load))
        return {"dollar": found, "own": found, "fx": []}
    at = home_closes(item, load, sessions)
    paid_local = dividends(item, load)
    days = [i for i, d in enumerate(sessions) if d in at]
    dollar, own, fx = [], [], []
    for i, j in zip(days, days[1:]):
        a, b = at[sessions[i]], at[sessions[j]]
        if not a["local"]:
            continue
        cash = sum(v for d, v in paid_local.items() if a["day"] < d <= b["day"])
        local = (b["local"] + cash) / a["local"] - 1.0
        if not a["rate"] or not b["rate"]:
            continue
        r_fx = b["rate"] / a["rate"] - 1.0
        own.append((i, j, local * (1.0 + r_fx)))
        fx.append((i, j, r_fx))
        dollar.append((i, j, (1.0 + local) * (1.0 + r_fx) - 1.0))
    return {"dollar": dollar, "own": own, "fx": fx}


def first_close(item: dict, load, sessions: list[str]) -> str | None:
    """The first session of the window a series has a close on."""
    found = dollar_closes(item, load, sessions)
    days = [d for d in sessions if d in found]
    return days[0] if days else None
