"""Daily series for the channels and the betas, and the latest mark.

A session is a trading day on the New York calendar. A daily bar's date is its session date, and its close counts once
the session is complete: the bar was fetched after 20:00 America/New_York that day, when the extended session has
ended. Returns, betas, and M_c read completed sessions only. A current price as of today is the latest mark instead: a
stock's last trade, or its latest minute bar, from its snapshot; a pair's latest trade (Coinbase Exchange, for a
crypto pair) or rate (Yahoo Finance, for a currency), or where none was read its latest hourly bar. A pair's daily
price is its price at the session's actual close in New York, 16:00 or 13:00 on an early close (session_close): the
open of the hourly bar that begins then, or where that hour has none, the close of the hour before, so that it is
observed when the listings are. A return is the simple close-to-close return with the split-adjusted dividend added back
on its ex-dividend date, what holding the instrument earned: the daily bars are adjusted for splits and not for
dividends. A dividend paid in another currency than the price's is added back in the price's currency, at that day's
rate. It is the unit in which e * b * u is a dollar move.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from functools import lru_cache
from itertools import accumulate
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from harness import sessions
from harness.venues.massive import answered

NY = ZoneInfo("America/New_York")
FINAL = time(20, 0)  # a daily bar is final, its session complete, once the extended session has ended
CLOSE = time(16, 0)  # the equity close, at which a crypto pair is observed
NYSE = "XNYS"  # the New York Stock Exchange's MIC, whose sessions are the store's
WINDOW = 504  # two years of trading days
LOOKBACK_DAYS = 770  # calendar days fetched before asof, enough to hold WINDOW + 1 sessions


def lookback(asof: str) -> str:
    return (date.fromisoformat(asof) - timedelta(days=LOOKBACK_DAYS)).isoformat()


def is_crypto(ticker: str) -> bool:
    return ticker.startswith("X:")


def is_index(ticker: str) -> bool:
    """An index's own values, `I:{symbol}`, read as a listing's daily bars are: an index pays no dividends."""
    return ticker.startswith("I:")


def is_pair(ticker: str) -> bool:
    """A currency or crypto pair, which trades around the clock and has no sessions of its own."""
    return ticker.startswith(("X:", "C:"))


ABROAD = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]:[A-Z]{2}$")
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]*\.([A-Z]{1,3})$")


def is_abroad(ticker: str) -> bool:
    """A listing abroad, whose prices the `home` data gives on its own market in its own currency
    (harness.venues.yahoo): its exchange's own symbol, its ticker there with the exchange's suffix as Yahoo writes it
    (`0700.HK`, `7203.T`, `VOD.L`, `ERIC-B.ST`); or `{ISIN}:{country}`, the share class its ISIN names on its country's
    exchange, the form a store kept before (harness.derive.identity). A US listing's class after a dot (`BRK.B`) has no
    exchange's suffix; of every US listing, one has a suffix that is also an exchange's, McCormick's voting class
    `MKC.V`, which its reference says is the US listing (identity.name_of), and Yahoo charts under its US form
    (harness.venues.yahoo.Yahoo.charted)."""
    if ABROAD.match(ticker):
        return True
    m = SYMBOL.match(ticker)
    return bool(m) and f".{m.group(1)}" in _suffixes()


def mark_kind(ticker: str) -> str:
    """The latest mark a price as of today reads: a pair's last trade or rate, a listing abroad's latest price on its
    home market, and any other listing's snapshot."""
    return "last_trade" if is_pair(ticker) else "home_mark" if is_abroad(ticker) else "snapshot"


@lru_cache(maxsize=1)
def _suffixes() -> frozenset[str]:
    from harness.venues.yahoo import EXCHANGE_SUFFIXES
    return EXCHANGE_SUFFIXES


def symbol_abroad(ticker: str) -> bool:
    """A listing abroad named by its exchange's own symbol, not by `{ISIN}:{country}`."""
    return is_abroad(ticker) and not ABROAD.match(ticker)


def ny_close(day: str) -> datetime:
    """When the New York session of `day` closed."""
    d = date.fromisoformat(day)
    return datetime.combine(d, session_close(d), NY)


def assigned(moment: str, sessions: list[str]) -> str | None:
    """The New York session a price struck at `moment` belongs to: the first whose close is at or after it, so that
    the session's return is the first to carry what the price reflects; None after the last."""
    when = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    i = bisect_left(sessions, when.astimezone(NY).date().isoformat())
    for day in sessions[i:i + 8]:
        if ny_close(day) >= when:
            return day
    return None


def spans(closes: dict[str, float], sessions: list[str], paid: dict[str, float] | None = None
          ) -> list[tuple[int, int, float]]:
    """Each return between consecutive closes a series has on `sessions`: (i, j, r), the closes on sessions[i] and
    sessions[j], j > i, and r the return from the first to the second with the dividends that went ex after the first
    and by the second added back. A listing that did not trade on the sessions between has one return over them."""
    days = [i for i, d in enumerate(sessions) if d in closes]
    out = []
    for i, j in zip(days, days[1:]):
        before, after = closes[sessions[i]], closes[sessions[j]]
        if not before:
            continue
        cash = sum(a for d, a in (paid or {}).items() if sessions[i] < d <= sessions[j])
        out.append((i, j, (after + cash) / before - 1.0))
    return out


def daily_closes(payload: dict, retrieved_at: str) -> dict[str, float]:
    """Each final daily bar's close, by session date."""
    fetched = datetime.fromisoformat(retrieved_at)
    out: dict[str, float] = {}
    for bar in (payload or {}).get("results") or []:
        day = datetime.fromtimestamp(bar["t"] / 1000, NY).date()
        if bar.get("c") is not None and fetched >= datetime.combine(day, FINAL, NY):
            out[day.isoformat()] = float(bar["c"])
    return out


def session_closes(payload: dict, retrieved_at: str) -> dict[str, float]:
    """A pair's price at each day's session close in New York (session_close), once the close has passed: the open of
    the hourly bar that begins then; where that hour has no bar (an hour with no trade, or no rate), the close of the
    hour that ends then, its last price before the close."""
    fetched = datetime.fromisoformat(retrieved_at)
    at: dict[str, float] = {}
    before: dict[str, float] = {}
    for bar in (payload or {}).get("results") or []:
        start = datetime.fromtimestamp(bar["t"] / 1000, NY)
        close = datetime.combine(start.date(), session_close(start.date()), NY)
        if start == close and bar.get("o") is not None and fetched >= start:
            at[start.date().isoformat()] = float(bar["o"])
        elif start + timedelta(hours=1) == close and bar.get("c") is not None and fetched >= close:
            before[start.date().isoformat()] = float(bar["c"])
    return {**before, **at}


def dollar_pair(currency: str) -> str:
    """The pair whose price is a currency's rate in dollars: the dollars one unit of it buys."""
    return f"C:{currency.upper()}{PRICE_CURRENCY}"


def rate_at(payload: dict | None, moment: str, day: str | None = None) -> dict | None:
    """A pair's price at a moment, from its hourly bars: the latest at or before it, the open of the hour it falls in,
    else the close of the latest hour that ended by then; with the time that price was struck. Before its first hour,
    the daily fixing of `day`, the date the price being converted belongs to, where the pair's rates carry one
    (harness.venues.fixings), with its moment and source. None where neither gives one."""
    return rate_for(rate_index(payload), moment, day)


def rate_index(payload: dict | None) -> tuple[list[float], list[float], dict[str, dict]]:
    """A pair's prices from its hourly bars, each at the moment it was struck, in time order: the open at the hour's
    start, the close at its end, the later where two fall at one moment; and its daily fixings by date, for the days
    before its first hour (harness.venues.fixings). What rate_for reads, for many moments."""
    found: dict[float, float] = {}
    for bar in (payload or {}).get("results") or []:
        start = bar["t"] / 1000
        if bar.get("o") is not None:
            found.setdefault(start, float(bar["o"]))
        if bar.get("c") is not None:
            found[start + 3600] = float(bar["c"])
    times = sorted(found)
    fixings = {f["day"]: f for f in (payload or {}).get("fixings") or [] if f.get("rate")}
    return times, [found[t] for t in times], fixings


def rate_for(index: tuple, moment: str, day: str | None) -> dict | None:
    """A price's rate in dollars from a rate_index: where the moment is at or after the pair's first hourly price,
    the latest hourly price struck by then; before it, the fixing of `day`, its own session's date, so a close is
    converted at its own date's fixing and never at an earlier date's (harness.venues.fixings). With the time it was
    struck (a fixing's moment, none where its source states only the date) and its source; None where neither
    gives one."""
    times, prices, fixings = index
    when = datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp()
    if times and when >= times[0]:
        i = bisect_right(times, when)
        return {"price": prices[i - 1], "time": datetime.fromtimestamp(times[i - 1], NY).isoformat(timespec="seconds"),
                "source": "hourly"}
    fixing = fixings.get(day) if day else None
    if fixing is None:
        return None
    return {"price": float(fixing["rate"]), "time": fixing.get("moment"), "source": fixing.get("source")}


def daily_rates(payload: dict | None, retrieved_at: str) -> dict[str, float]:
    """A currency pair's rate on each day, as a dividend or a notional reads it, that day's rate: its price at the
    New York session's close (session_closes) where its hourly prices reach, and before them its daily fixing."""
    fixings = {f["day"]: float(f["rate"]) for f in (payload or {}).get("fixings") or [] if f.get("rate")}
    return {**fixings, **session_closes(payload, retrieved_at)}


def _moment(stamp) -> str | None:
    """A Massive timestamp, nanoseconds or milliseconds since the epoch, as a New York time."""
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    seconds = stamp / 1e9 if stamp > 1e14 else stamp / 1e3
    return datetime.fromtimestamp(seconds, NY).isoformat(timespec="seconds")


def snapshot_mark(payload: dict | None) -> dict | None:
    """A stock's latest mark from its snapshot: the last trade's price and time, else the latest minute bar's close and
    time; a mutual fund's, its last net asset value, struck at New York's close (harness.venues.funds); None where the
    snapshot has none (a refusal, an unknown ticker, or the hours after it is cleared)."""
    t = (payload or {}).get("ticker") or {}
    trade, minute, nav = t.get("lastTrade") or {}, t.get("min") or {}, t.get("nav") or {}
    if nav.get("p") and _moment(nav.get("t")):
        return {"price": float(nav["p"]), "time": _moment(nav["t"]), "from": "net asset value"}
    if trade.get("p") and _moment(trade.get("t")):
        return {"price": float(trade["p"]), "time": _moment(trade["t"]), "from": "last trade"}
    if minute.get("c") and _moment(minute.get("t")):
        return {"price": float(minute["c"]), "time": _moment(minute["t"]), "from": "latest minute bar"}
    return None


def trade_mark(payload: dict | None) -> dict | None:
    """A pair's latest mark: a crypto pair's last trade on Coinbase Exchange, a currency pair's latest rate (Yahoo
    Finance), at its own time; None where the pair has none (a pair its source does not list)."""
    p = payload or {}
    if p.get("status") != "OK" or p.get("price") in (None, "") or not p.get("time"):
        return None
    moment = datetime.fromisoformat(p["time"]).astimezone(NY).isoformat(timespec="seconds")
    return {"price": float(p["price"]), "time": moment,
            "from": "latest rate" if str(p.get("ticker") or "").startswith("C:") else "last trade"}


def home_mark(payload: dict | None) -> dict | None:
    """A listing abroad's latest mark, in its own currency, at the moment it was struck; None where it has none."""
    p = payload or {}
    if p.get("status") != "OK" or p.get("price") is None or not p.get("time"):
        return None
    moment = datetime.fromisoformat(str(p["time"]).replace("Z", "+00:00")).astimezone(NY).isoformat(timespec="seconds")
    return {"price": float(p["price"]), "time": moment, "from": "latest price"}


def hourly_mark(payload: dict | None) -> dict | None:
    """A pair's latest mark where none was read: its latest hourly bar's close, at the bar's time."""
    bars = [b for b in (payload or {}).get("results") or [] if b.get("c") is not None and b.get("t")]
    if not bars:
        return None
    last = max(bars, key=lambda b: b["t"])
    return {"price": float(last["c"]), "time": _moment(last["t"]), "from": "latest hourly bar"}


def window(calendar: dict[str, float], asof: str, days: int = WINDOW) -> list[str]:
    """The sessions of the window: the `days` sessions ending at asof, with the session
    before them, whose close the first return needs."""
    sessions = sorted(d for d in calendar if d <= asof)
    return sessions[-(days + 1):]


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset[date]:
    """The weekdays of a year the New York Stock Exchange holds no session: its holidays, as observed, and the closures
    no rule foresees (a national day of mourning, as on 2025-01-09), from its calendar (harness.sessions)."""
    return frozenset(sessions.holidays(NYSE, year))


@lru_cache(maxsize=None)
def session_close(day: date) -> time:
    """When the New York exchange's session closes on a day, by its calendar (harness.sessions): 16:00, or 13:00
    on its early closes (the day after Thanksgiving, July 3 and December 24 when each is a session). A day the exchange
    holds no session has none; its 16:00 is returned, as a pair's price is read every day."""
    closed = sessions.close_of(NYSE, day)
    return closed.astimezone(NY).time().replace(tzinfo=None) if closed is not None else CLOSE


def sessions_after(start: str, end: str) -> int:
    """The sessions after `start` up to and including `end` on the New York calendar: the weekdays the exchange is
    open by its holiday rule (nyse_holidays)."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    closed = set().union(*(nyse_holidays(y) for y in range(a.year, b.year + 1)))
    return sum(1 for k in range(1, (b - a).days + 1)
               if (d := a + timedelta(days=k)).weekday() < 5 and d not in closed)


PRICE_CURRENCY = "USD"  # every listing Massive prices is a US one, quoted in US dollars (its reference's currency_name)
DERIVED = ("id", "historical_adjustment_factor")  # a dividend record's fields that are no fact of the dividend


def dividend_records(payload: dict | None) -> list[dict]:
    """Massive's dividend records, each once: a record identical to another in every field but its id and its
    `historical_adjustment_factor` is one dividend listed twice, and counts once. The factor is no fact of the
    dividend: Massive derives it cumulatively along the ticker's records, so a dividend listed twice carries two. Records
    that differ in any other field are each a dividend, as an ordinary and an extraordinary dividend of one amount on
    one date are, told apart only by their `distribution_type`."""
    out, seen = [], set()
    for row in (payload or {}).get("results") or []:
        if not isinstance(row, dict) or not row.get("ex_dividend_date"):
            continue
        key = json.dumps({k: v for k, v in row.items() if k not in DERIVED}, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def currency_of(record: dict) -> str:
    """The currency a dividend is paid in; a record that names none is in the price's."""
    return str(record.get("currency") or PRICE_CURRENCY).strip().upper()


def reference_rates(payload: dict | None) -> dict[str, float]:
    """A currency's daily rate in US dollars, by day, from the ECB's reference rates (harness.venues.ecb): the dollar's
    rate over the currency's, each in units one euro buys, both set at the same moment of the same day."""
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        return {}
    per = payload.get("per_euro") or {}
    own, usd = per.get(payload.get("currency")) or {}, per.get(PRICE_CURRENCY) or {}
    return {day: usd[day] / rate for day, rate in own.items() if rate and usd.get(day)}


def rate_on(rates: dict[str, float], day: str) -> float | None:
    """That day's rate: the day's own, otherwise the last before it (a reference rate is set on working days only)."""
    if day in rates:
        return rates[day]
    before = [d for d in rates if d < day]
    return rates[max(before)] if before else None


def split_factor(splits: dict | None, day: str) -> float | None:
    """What puts a per-share amount of `day` on the share basis of the split-adjusted bars: the
    `historical_adjustment_factor` of the first split executed after that day, or 1 when none was; None where the
    splits were not read. A split takes effect overnight before its execution date, so an amount of that date is
    already on the new basis (https://massive.com/docs/rest/stocks/corporate-actions/splits)."""
    if not answered(splits):
        return None
    later = sorted((s for s in splits.get("results") or [] if (s.get("execution_date") or "") > day),
                   key=lambda s: s["execution_date"])
    if not later:
        return 1.0
    first = later[0]
    if first.get("historical_adjustment_factor") is not None:
        return float(first["historical_adjustment_factor"])
    factor = 1.0  # the factor is each later split's old shares over its new, multiplied
    for s in later:
        factor *= float(s["split_from"]) / float(s["split_to"])
    return factor


RECURRING = "recurring"  # Massive's distribution_type for a distribution "paid on a regular schedule"; the others
# (special, supplemental, irregular, unknown) are no income a holding accrues
# (https://massive.com/docs/rest/stocks/corporate-actions/dividends)


def cadence(payload: dict | None) -> dict[str, int]:
    """How many times a year each recurring distribution is expected, by its ex-date, as its venue gives it (`frequency`:
    12 monthly, 4 quarterly, 1 annual; 0 for none expected)."""
    out = {}
    for row in dividend_records(payload):
        if row.get("distribution_type") == RECURRING and isinstance(row.get("frequency"), int):
            out[row["ex_dividend_date"]] = row["frequency"]
    return out


class Incomplete(Exception):
    """A dividend whose inputs the reads did not give: its returns wait for them."""


def dividends(payload: dict | None, rates: dict[str, dict[str, float]] | None = None,
              splits: dict | None = None, *, recurring: bool | None = None) -> dict[str, float]:
    """Each ex-dividend date's cash dividend per share, split-adjusted and in the price's currency, from Massive's
    dividends: each dividend once (dividend_records), and two dividends on one date summed. The split-adjusted
    amount is Massive's own where it gives one, and otherwise the cash amount put on today's share basis by `splits`,
    the listing's splits (Massive gives none for a dividend paid in another currency than USD). A dividend paid in
    another currency is converted at `rates`, each currency's daily rate in the price's currency, on its ex-dividend
    date (rate_on). A record with no cash amount is no dividend paid; one whose split adjustment or rate the reads did
    not give raises Incomplete, so the returns wait rather than leave a known dividend out. With `recurring`, only the
    distributions its venue calls recurring, or only those it does not (RECURRING)."""
    out: dict[str, float] = {}
    for row in dividend_records(payload):
        if recurring is not None and (row.get("distribution_type") == RECURRING) != recurring:
            continue
        day = row["ex_dividend_date"]
        amount = row.get("split_adjusted_cash_amount")
        if amount is None:
            if row.get("cash_amount") is None:
                continue
            factor = split_factor(splits, day)
            if factor is None:
                raise Incomplete(f"the dividend of {day} needs the listing's splits, which were not read")
            amount = float(row["cash_amount"]) * factor
        currency = currency_of(row)
        if currency != PRICE_CURRENCY:
            rate = rate_on((rates or {}).get(currency) or {}, day)
            if rate is None:
                raise Incomplete(f"the dividend of {day} is paid in {currency}, whose rate on that day was not read")
            amount = float(amount) * rate
        out[day] = out.get(day, 0.0) + float(amount)
    return out


def returns(closes: dict[str, float], sessions: list[str], paid: dict[str, float] | None = None) -> dict[str, float]:
    """The return on each session whose close and the previous session's close exist:
    (close_t + dividend_t) / close_{t-1} - 1, where dividend_t is what went ex on session t.
    A dividend whose ex-date is not a session goes ex on the first session after it."""
    ex: dict[str, float] = {}
    for day, amount in (paid or {}).items():
        i = bisect_left(sessions, day)
        if 0 < i < len(sessions):
            ex[sessions[i]] = ex.get(sessions[i], 0.0) + amount
    out: dict[str, float] = {}
    for previous, day in zip(sessions, sessions[1:]):
        before, after = closes.get(previous), closes.get(day)
        if before is not None and after is not None and before != 0:
            out[day] = (after + ex.get(day, 0.0)) / before - 1.0
    return out


MISSED = 2.5  # schedule's periods between two distributions beyond which payments were missed (Accrual)


class Accrual:
    """The recurring income a share has accrued through each day, cumulatively (MATH.md 3.2). Each distribution
    accrues evenly over the days since the one before it, so that by its ex-date it has accrued whole, as a fund's price
    carries its income from one ex-date to the next whatever period the distribution is for.

    Where none came before it, or payments its schedule gives were missed, it accrues over its schedule's period alone
    (365 days over its venue's `frequency`), or, where its venue states none, over as many days as lie between it and
    the next (a year where there is none). Payments were missed where it comes more than MISSED of its schedule's
    periods after the one before, a year being the period where none is stated: two or more, by a line set halfway
    between counts of them, so that the days by which a schedule drifts never decide it, and so that a fund that pays
    twice in December and next in February, its January payment paid early, reads as it is paid.

    After the latest, and after one followed by missed payments, income goes on accruing at its rate for one more period
    of it, and stops where the next begins to accrue, so that no day accrues twice: a distribution that does not come by
    then was not earned. The record is the distributions going ex on or after `since`, where what the derivation reads
    begins, through `until`, where it ends, so that no accrual depends on how far a payload happens to reach. Days are
    calendar days, as a bond's accrued interest counts them."""

    def __init__(self, income: dict[str, float] | None = None, frequency: dict[str, int] | None = None,
                 since: str | None = None, until: str | None = None):
        days = sorted(d for d, a in (income or {}).items()
                      if a and (since is None or d >= since) and (until is None or d <= until))
        ex = [date.fromisoformat(d).toordinal() for d in days]
        amount = [float(income[d]) for d in days]
        n = len(days)
        stated = [(frequency or {}).get(d) for d in days]

        def missed(i: int) -> bool:  # none before it, or payments missed since the one before
            return i == 0 or ex[i] - ex[i - 1] > MISSED * (365.0 / stated[i] if stated[i] else 365.0)

        def own(i: int) -> float:  # the days it accrues over, ending on its ex-date
            if not missed(i):
                return float(ex[i] - ex[i - 1])
            alone = 365.0 / stated[i] if stated[i] else float(ex[i + 1] - ex[i]) if i + 1 < n else 365.0
            return alone if i == 0 else min(alone, float(ex[i] - ex[i - 1]))
        period = [own(i) for i in range(n)]
        stretches: list[tuple[float, float, float]] = []  # (from, to, amount): in order, none overlapping
        for i in range(n):
            stretches.append((ex[i] - period[i], float(ex[i]), amount[i]))
            if i + 1 == n or missed(i + 1):  # after it, at its rate for one more period, to where the next begins
                to = ex[i] + period[i] if i + 1 == n else min(ex[i] + period[i], ex[i + 1] - period[i + 1])
                if to > ex[i]:
                    stretches.append((float(ex[i]), to, amount[i] * (to - ex[i]) / period[i]))
        self.start = [s for s, _, _ in stretches]
        self.stretches = stretches
        self.before = [0.0, *accumulate(a for _, _, a in stretches)]  # what accrued before each stretch

    def __call__(self, day: str) -> float:
        d = date.fromisoformat(day).toordinal()
        k = bisect_right(self.start, d) - 1  # the last stretch begun by the day; every one before it has ended
        if k < 0:
            return 0.0
        start, end, amount = self.stretches[k]
        return self.before[k] + amount * min(max((d - start) / (end - start), 0.0), 1.0)


class Moves:
    """Every window's move over one series (MATH.md 3.2): what holding the instrument earned over it, less the income it
    accrued over it. Each session's return adds back every distribution on the close it goes ex on (`returns`), and
    takes away the recurring income accrued since the close before (Accrual), over that close; a window's move is
    those compounded over its sessions, in order. A distribution that is no income, as a special one, stays in what
    holding earned, so no ex-date steps the move. Computed once over all the closes; None where a close at either end,
    or a return between, is missing."""

    def __init__(self, closes: dict[str, float], paid: dict[str, float] | None = None,
                 accrual: Accrual | None = None):
        self.closes = closes
        self.days = sorted(closes)
        self.at = {d: i for i, d in enumerate(self.days)}
        daily = returns(closes, self.days, paid or {})
        self.clean: dict[str, float] = {}
        for previous, day in zip(self.days, self.days[1:]):
            if day in daily:
                accrued = 0.0 if accrual is None else (accrual(day) - accrual(previous)) / closes[previous]
                self.clean[day] = daily[day] - accrued

    def __call__(self, start: str, end: str) -> float | None:
        before, after = self.closes.get(start), self.closes.get(end)
        if before is None or after is None or before == 0:
            return None
        grown = 1.0
        for d in self.days[self.at[start] + 1:self.at[end] + 1]:
            if d not in self.clean:
                return None
            grown *= 1.0 + self.clean[d]
        return grown - 1.0


def window_move(closes: dict[str, float], start: str, end: str, paid: dict[str, float] | None = None,
                accrual: Accrual | None = None) -> float | None:
    """A window's move (MATH.md 3.2): what holding the instrument earned from the close of start to the close of end,
    less the income it accrued over it (Moves)."""
    return Moves(closes, paid, accrual)(start, end)


def ols(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least squares of y on x with an intercept: the coefficients, intercept first, and
    the residuals."""
    design = np.column_stack([np.ones(len(y)), x]) if x.size else np.ones((len(y), 1))
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    return coefficients, y - design @ coefficients
