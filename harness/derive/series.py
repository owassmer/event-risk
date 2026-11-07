"""Daily series for the channels and the betas, and the latest mark.

A session is a trading day on the New York calendar. A daily bar's date is its session date, and its close counts once
the session is complete: the bar was fetched after 20:00 America/New_York that day, when the extended session has
ended. Returns, betas, and M_c read completed sessions only. A current price as of today is the latest mark instead: a
stock's last trade, or its latest minute bar, from its snapshot; a crypto pair's last trade on Coinbase Exchange, or
where none was read its latest hourly bar. A crypto pair's daily price is its price at 16:00 America/New_York, the
open of the hourly bar that contains 16:00, so that it is observed at the equity close. A return is the simple close-to-close return with the split-adjusted dividend added back
on its ex-dividend date, what holding the instrument earned: the daily bars are adjusted for splits and not for
dividends. It is the unit in which e * b * u is a dollar move.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

NY = ZoneInfo("America/New_York")
FINAL = time(20, 0)  # a daily bar is final, its session complete, once the extended session has ended
CLOSE = time(16, 0)  # the equity close, at which a crypto pair is observed
WINDOW = 504  # two years of trading days
LOOKBACK_DAYS = 770  # calendar days fetched before asof, enough to hold WINDOW + 1 sessions


def lookback(asof: str) -> str:
    return (date.fromisoformat(asof) - timedelta(days=LOOKBACK_DAYS)).isoformat()


def is_crypto(ticker: str) -> bool:
    return ticker.startswith("X:")


def daily_closes(payload: dict, retrieved_at: str) -> dict[str, float]:
    """Each final daily bar's close, by session date."""
    fetched = datetime.fromisoformat(retrieved_at)
    out: dict[str, float] = {}
    for bar in (payload or {}).get("results") or []:
        day = datetime.fromtimestamp(bar["t"] / 1000, NY).date()
        if bar.get("c") is not None and fetched >= datetime.combine(day, FINAL, NY):
            out[day.isoformat()] = float(bar["c"])
    return out


def closes_at_1600(payload: dict, retrieved_at: str) -> dict[str, float]:
    """A crypto pair's price at 16:00 America/New_York each day: the open of the hourly bar
    that contains 16:00."""
    fetched = datetime.fromisoformat(retrieved_at)
    out: dict[str, float] = {}
    for bar in (payload or {}).get("results") or []:
        start = datetime.fromtimestamp(bar["t"] / 1000, NY)
        if start.timetz().replace(tzinfo=None) == CLOSE and bar.get("o") is not None and fetched >= start:
            out[start.date().isoformat()] = float(bar["o"])
    return out


def _moment(stamp) -> str | None:
    """A Massive timestamp, nanoseconds or milliseconds since the epoch, as a New York time."""
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    seconds = stamp / 1e9 if stamp > 1e14 else stamp / 1e3
    return datetime.fromtimestamp(seconds, NY).isoformat(timespec="seconds")


def snapshot_mark(payload: dict | None) -> dict | None:
    """A stock's latest mark from its snapshot: the last trade's price and time, else the latest minute bar's close and
    time; None where the snapshot has neither (a refusal, an unknown ticker, or the hours after it is cleared)."""
    t = (payload or {}).get("ticker") or {}
    trade, minute = t.get("lastTrade") or {}, t.get("min") or {}
    if trade.get("p") and _moment(trade.get("t")):
        return {"price": float(trade["p"]), "time": _moment(trade["t"]), "from": "last trade"}
    if minute.get("c") and _moment(minute.get("t")):
        return {"price": float(minute["c"]), "time": _moment(minute["t"]), "from": "latest minute bar"}
    return None


def trade_mark(payload: dict | None) -> dict | None:
    """A crypto pair's latest mark from Coinbase Exchange's ticker: its last trade's price, at the trade's own time;
    None where the pair has none (a pair Coinbase does not list)."""
    p = payload or {}
    if p.get("status") != "OK" or p.get("price") in (None, "") or not p.get("time"):
        return None
    moment = datetime.fromisoformat(p["time"]).astimezone(NY).isoformat(timespec="seconds")
    return {"price": float(p["price"]), "time": moment, "from": "last trade"}


def hourly_mark(payload: dict | None) -> dict | None:
    """A crypto pair's latest mark: its latest hourly bar's close, at the bar's time."""
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


def dividends(payload: dict | None) -> dict[str, float]:
    """Each ex-dividend date's split-adjusted cash dividend per share, from Massive's
    dividends: a record listed twice (one id) counts once, and two records on one date sum."""
    out: dict[str, float] = {}
    seen: set = set()
    for row in (payload or {}).get("results") or []:
        day, amount, key = row.get("ex_dividend_date"), row.get("split_adjusted_cash_amount"), row.get("id")
        if not day or amount is None or (key is not None and key in seen):
            continue
        seen.add(key)
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


def window_return(closes: dict[str, float], paid: dict[str, float], start: str, end: str) -> float | None:
    """A return over a window: (close_end + the dividends that go ex inside the window) /
    close_start - 1, simple and not reinvested. Inside is after start, up to and including end: a
    dividend ex on the start day is already out of its close. None when either close is missing."""
    before, after = closes.get(start), closes.get(end)
    if before is None or after is None or before == 0:
        return None
    return (after + sum(a for d, a in paid.items() if start < d <= end)) / before - 1.0


def ols(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least squares of y on x with an intercept: the coefficients, intercept first, and
    the residuals."""
    design = np.column_stack([np.ones(len(y)), x]) if x.size else np.ones((len(y), 1))
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    return coefficients, y - design @ coefficients
