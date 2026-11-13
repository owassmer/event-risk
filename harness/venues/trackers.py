"""Which fund's holdings are an index's companies, established by prices (MATH.md 3.1): a fund that holds an index's
companies moves with the index's own values session after session, and a fund of another basket differs from it
every session by what the two do not share. Asked, lazily, for an index a held fund's derivatives are exposed to,
named by the symbol Massive gives its values under (`I:NDX`): the index as the fund's report identifies it and
OpenFIGI reads that identifier (harness.derive.rows). Then the look-through reads the holdings of the closest fund
that gives companies, which are the index's.

The rule. Over the SESSIONS completed sessions to the as-of, every US exchange listing's daily return is set against
the index's. How closely a listing tracks the index is the median absolute deviation of the two returns' difference
over the median absolute deviation of the index's own returns: the typical session's difference against the index's
typical move.
- **The statistic reads the typical session, not every session.** A fund's distribution falls on one dated session,
  and so does a split, while Massive's index close is now and then struck at another moment than the funds' closes
  (read live: on 2026-06-26 every S&P 500 fund stood 0.7% to 0.8% from I:SPX, and back the next session). A standard
  deviation is ruled by those few sessions; the median absolute deviation is not. So the closes are read as traded,
  with no dividend or split read.
- **A fund matches the index where its figure is below LINE.**

Measured on the indexes the derivatives of the 500 most traded US funds reference, where Massive carries the index
(Nasdaq-100, S&P 500, Dow Jones Industrial Average, Russell 2000, Technology and Financial Select Sector, Dow Jones
U.S. Select Home Construction, PHLX Semiconductor, Dow Jones U.S. Semiconductors, MSCI EAFE and Emerging Markets), and
on commodity and bitcoin indexes Massive carries. Each was taken over windows of 5 to 252 sessions, each ending at 13
dates 21 sessions apart in the two years to 2026-11-06, against every US ETF.
- **63 sessions.** A fund of the index stood at most 0.077 from it in every window (QQQ, SPY's, DIA, IWM, XLK, XLF,
  ITB, SOXQ). Where no fund holds the index, the best fund stood at least 0.195 (the semiconductors index Dow Jones
  computes, whose nearest fund holds another semiconductor basket), and an index whose values are struck abroad or
  around the clock at least 0.35.
- **Shorter windows.** At 21 sessions the two overlapped (a fund of the index to 0.137, the best where none holds it
  from 0.089). Under 15, a median over so few sessions puts unrelated funds near zero.
- **The line** sits at the middle of the gap, in ratio terms, 0.12: for two baskets of the same volatility, a
  correlation near 0.993.
- **Near ties.** A fund of nearly the same companies (another broad large-company fund against the S&P 500, a
  sector fund of another index provider against a Select Sector index) stood at 0.04 to 0.14. Either basket serves
  where it is closest.

The endpoints, each checked against a live answer:
- **The index's values.** `GET /v2/aggs/ticker/{I:symbol}/range/1/day/{from}/{to}`
  (https://massive.com/docs/rest/indices/aggregates/custom-bars), `{results: [{o, h, l, c, t}]}`, `t` the session's
  start in Unix milliseconds. A symbol Massive does not carry is answered 404 (`I:INDU`, `I:RTY`: its feeds' symbols
  for the Dow and the Russell 2000 are `I:DJI` and `I:RUT`, where OpenFIGI's are Bloomberg's).
- **Every listing's close on a session.** `GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=false`
  (https://massive.com/docs/rest/stocks/aggregates/daily-market-summary), `{results: [{T, c, ...}]}`: every exchange
  listing that traded that session, some 12,500 and 1.4 MB a session, read once on this machine and kept by session
  (harness.observations).

What is kept, `{status, index, sessions: [first, last], compared, line, matches: [{listing, tracking}], nearest:
[{listing, tracking}]}`:
- `matches`: every listing below the line, closest first.
- `nearest`: the NEAREST closest whatever the line, so that the answer shows how far the next basket stands.
- `from`: `candidates` where the funds whose issuers state they track the index (FOLLOWERS) were compared and one
  matched, with `candidates`, the funds compared; `market` where every listing was.
- `NOT_FOUND`: where Massive gives no values for the index over the sessions.

The candidates. Which funds follow an index is the same for every reader, and reading every listing's closes to find
them costs 64 requests and some 88 MB on a machine that holds none of the sessions. So the funds whose issuers state
the index as their benchmark are compared first, each on its own closes as traded over the same sessions, which are
the closes the whole market's read gives it (VOO's on 2026-08-05: 707.6 in both); where one matches, the closest of
them is the answer, and where none does, every listing is compared. The rule is the same either way: a fund
establishes the index's companies where it stands below the line. Measured live, the closest fund of the
whole market was a candidate for each of seven indexes Massive gives values for
(I:NDX QQQ, I:SPX VOO, I:DJI DIA, I:RUT IWM, I:SIXT XLK, I:SIXM XLF, I:DJSHMB ITB).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np

from harness import sessions as calendars
from harness.observations import Observations, today
from harness.venues.http import now
from harness.venues.massive import Massive, MassiveError, _session, answered

SESSIONS = 63  # completed sessions compared: the shortest window in which a fund of the index and the best fund
# where none holds it stood apart in every window measured
LINE = 0.12  # a fund matches the index below this: the middle of the measured gap, 0.077 to 0.195, in ratio terms
NEAREST = 5
# Sessions read side by side. Massive allows a paid plan any number of requests
# (https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis), so what bounds
# the read is its 88 MB: measured cold on 2026-11-10, 64 sessions took 10.8 s five at a time, 4.6 s to 5.6 s
# sixteen at a time, and 5.0 s thirty-two at a time, some 18 MB a second.
WORKERS = 16
NYSE = "XNYS"
GROUPED = "/v2/aggs/grouped/locale/us/market/stocks/{day}"


def _settled(day: str, retrieved: str) -> bool:
    """Whether a read of a session's closes, retrieved at `retrieved`, was made once its closing prices were published:
    SETTLE after the session's close by the New York exchange's calendar."""
    close = calendars.close_of(NYSE, date.fromisoformat(day))
    moment = datetime.fromisoformat(retrieved)
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return close is None or moment >= close + timedelta(seconds=calendars.SETTLE)
BARS = "/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
# The funds whose issuers state an index as their benchmark, by the symbol Massive gives the index's values under: each
# entry from the issuer's page for the fund, which names the index.
FOLLOWERS = {
    # Invesco: QQQ "tracks the Nasdaq-100 index"
    # https://www.invesco.com/qqq-etf/en/home.html
    "I:NDX": ("QQQ",),
    # State Street, Benchmark "S&P 500 Index"; iShares, Benchmark Index "S&P 500 Index"; Vanguard, "seeks to track the
    # investment performance of the S&P 500 Index"
    # https://www.ssga.com/us/en/intermediary/etfs/spdr-sp-500-etf-trust-spy
    # https://www.ssga.com/us/en/intermediary/etfs/spdr-portfolio-sp-500-etf-spym
    # https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf
    # https://investor.vanguard.com/investment-products/etfs/profile/voo
    "I:SPX": ("SPY", "IVV", "VOO", "SPYM"),
    # State Street, Benchmark "Dow Jones Industrial Average"
    # https://www.ssga.com/us/en/intermediary/etfs/spdr-dow-jones-industrial-average-etf-trust-dia
    "I:DJI": ("DIA",),
    # iShares, "seeks to track the Russell 2000 Index", "the Russell 1000 Index"; Vanguard, "seeks to track the
    # investment performance of the Russell 2000 Index", "of the Russell 1000 Index"
    # https://www.ishares.com/us/products/239710/ishares-russell-2000-etf
    # https://investor.vanguard.com/investment-products/etfs/profile/vtwo
    # https://www.ishares.com/us/products/239707/ishares-russell-1000-etf
    # https://investor.vanguard.com/investment-products/etfs/profile/vone
    "I:RUT": ("IWM", "VTWO"),
    "I:RUI": ("IWB", "VONE"),
    # iShares, Benchmark Index "Dow Jones U.S. Select Home Construction Index"
    # https://www.ishares.com/us/products/239512/ishares-us-home-construction-etf
    "I:DJSHMB": ("ITB",),
    # The Select Sector SPDRs, each page's Benchmark the sector's Select Sector Index
    # https://www.sectorspdrs.com/mainfund/XLY (and /XLP, /XLE, /XLF, /XLV, /XLI, /XLB, /XLRE, /XLK, /XLU)
    "I:SIXY": ("XLY",),
    "I:SIXR": ("XLP",),
    "I:SIXE": ("XLE",),
    "I:SIXM": ("XLF",),
    "I:SIXV": ("XLV",),
    "I:SIXI": ("XLI",),
    "I:SIXB": ("XLB",),
    "I:SIXRE": ("XLRE",),
    "I:SIXT": ("XLK",),
    "I:SIXU": ("XLU",),
}


def _mad(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """The median absolute deviation from the median."""
    return np.median(np.abs(x - np.median(x, axis=axis, keepdims=True)), axis=axis)


def tracking(index: np.ndarray, listings: dict[str, np.ndarray], *, line: float = LINE) -> dict:
    """How closely each listing's daily returns follow the index's over the same sessions: the median absolute
    deviation of their difference over the index's own, for every listing with a return on each session; the
    listings below `line`, closest first, and the NEAREST closest whatever the line."""
    scale = float(_mad(index)) if index.size else 0.0
    if not listings or scale <= 0:
        return {"compared": 0, "matches": [], "nearest": []}
    names = sorted(listings)
    found = _mad(np.vstack([listings[n] for n in names]) - index) / scale
    order = np.argsort(found, kind="stable")
    ranked = [{"listing": names[i], "tracking": round(float(found[i]), 4)} for i in order]
    return {"compared": len(names), "matches": [r for r in ranked if r["tracking"] < line],
            "nearest": ranked[:NEAREST]}


class Trackers:
    def __init__(self, massive: Massive, observations: Observations, *, workers: int = WORKERS):
        self.massive, self.obs, self.workers = massive, observations, workers
        self._reading = threading.Lock()  # the missing sessions are read by one test at a time
        # a session this machine holds from a read made before its closing prices were published holds a moment of
        # the session, and so does each listing-at-a-price answer resting on it: dropped, to be read again (closes)
        observations.forget_days(d for d, kept in observations.market_days().items()
                                 if not _settled(d, kept["retrieved_at"]))

    @staticmethod
    def sessions(asof: str, count: int = SESSIONS + 1) -> list[str]:
        """The New York exchange's last `count` sessions on or before the as-of that closed before today, oldest
        first: what a session's closes were is settled once its day has passed."""
        day = date.fromisoformat(asof)
        start = (day - timedelta(days=int(count * 7 / 5) + 30)).isoformat()
        moment = datetime.now(timezone.utc).timestamp()
        found = [d for d, closes in calendars.closes(NYSE, start, asof).items() if closes <= moment and d < today()]
        return sorted(found)[-count:]

    def _read_day(self, day: str) -> dict[str, float]:
        """Every listing's close on a session, read from Massive, and kept on this machine where it was read once the
        session's closing prices were published (_settled)."""
        url = self.massive.url(GROUPED.format(day=day), {"adjusted": "false"})
        retrieved = now()
        status, body = self.massive.get(url)
        if status != 200 or not answered(body):
            raise MassiveError(f"Massive gave no grouped bars for {day}: {body.get('status')}")
        if _settled(day, retrieved):
            self.obs.keep_day(day, body.get("results") or [], {"method": "GET", "url": url}, retrieved)
        return {r["T"]: float(r["c"]) for r in body.get("results") or []
                if r.get("T") and isinstance(r.get("c"), (int, float))}

    def _closes_now(self, day: str) -> dict[str, float]:
        """Every listing's close on today's session, read from Massive and kept nowhere."""
        url = self.massive.url(GROUPED.format(day=day), {"adjusted": "false"})
        status, body = self.massive.get(url)
        if status != 200 or not answered(body):
            raise MassiveError(f"Massive gave no grouped bars for {day}: {body.get('status')}")
        return {r["T"]: float(r["c"]) for r in body.get("results") or []
                if r.get("T") and isinstance(r.get("c"), (int, float))}

    def closes(self, days: list[str]) -> dict[str, dict[str, float]]:
        """Every listing's close on each session, read where this machine does not hold the session's closes yet: a
        session it holds from a read made before its closing prices were published holds a moment of the session, not
        its closes (read live: a read at 1:15 PM of a session's day held EWY at 149.56, its close 147.81, and 11,431
        listings of some 12,600), and is read again. Two indexes' tests side by side need the same sessions: the second
        waits for the first's reads and reads none of them again (read live, a portfolio with two index-referencing
        funds read 128 sessions for its 64)."""
        read: dict[str, dict[str, float]] = {}
        with self._reading:
            kept = self.obs.market_days()
            todo = [d for d in days if d not in kept or not _settled(d, kept[d]["retrieved_at"])]
            if todo:
                with ThreadPoolExecutor(max_workers=min(self.workers, len(todo))) as pool:
                    read = dict(zip(todo, pool.map(self._read_day, todo)))
        out = self.obs.day_closes([d for d in days if d not in read])
        for day, closes in read.items():  # as read, kept or not
            for t, c in closes.items():
                out.setdefault(t, {})[day] = c
        return out

    def values(self, symbol: str, days: list[str]) -> tuple[dict[str, float] | None, str]:
        """The index's close on each session, as Massive gives its values; None where it gives none."""
        url = self.massive.url(f"/v2/aggs/ticker/{symbol}/range/1/day/{days[0]}/{days[-1]}",
                               {"sort": "asc", "limit": 50000})
        status, body = self.massive.get(url)
        if status == 404 or (status == 200 and answered(body) and not body.get("results")):
            return None, url
        if status != 200 or not answered(body):
            raise MassiveError(f"Massive gave no values for {symbol}: {body.get('status')}")
        wanted = set(days)
        found = {}
        for r in body.get("results") or []:
            d = _session(r.get("t"))
            if d in wanted and isinstance(r.get("c"), (int, float)) and r["c"] > 0:
                found[d] = float(r["c"])
        return (found or None), url

    def _own_closes(self, ticker: str, days: list[str]) -> tuple[dict[str, float], str]:
        """A listing's close on each session, as traded, from its own daily bars: the close the whole market's read
        gives it."""
        url = self.massive.url(BARS.format(ticker=ticker, start=days[0], end=days[-1]),
                               {"adjusted": "false", "sort": "asc", "limit": 50000})
        status, body = self.massive.get(url)
        if status != 200 or not answered(body):
            raise MassiveError(f"Massive gave no bars for {ticker}: {body.get('status')}")
        wanted = set(days)
        return {d: float(r["c"]) for r in body.get("results") or []
                if (d := _session(r.get("t"))) in wanted and isinstance(r.get("c"), (int, float))}, url

    @staticmethod
    def _returns(closes: dict[str, dict[str, float]], pairs: list[tuple[str, str]]) -> dict[str, np.ndarray]:
        """Each listing's returns over the index's sessions, for a listing with a close on every one."""
        return {t: np.array([c[b] / c[a] - 1 for a, b in pairs]) for t, c in closes.items()
                if all(a in c and b in c and c[a] > 0 for a, b in pairs)}

    def tracker(self, symbol: str, asof: str) -> dict:
        retrieved = now()
        days = self.sessions(asof)
        if len(days) < 2:
            raise MassiveError(f"no completed sessions to {asof}")
        index, url = self.values(symbol, days)
        request = {"method": "GET", "url": url, "sessions": [days[0], days[-1]],
                   "closes": self.massive.url(GROUPED.format(day="{date}"), {"adjusted": "false"})}
        if index is None:
            return {"payload": {"status": "NOT_FOUND", "index": symbol, "sessions": [days[0], days[-1]]},
                    "request": request, "retrieved_at": retrieved, "range": (asof, asof)}
        # the sessions whose return the index gives: its close on the session and on the one before
        pairs = [(a, b) for a, b in zip(days, days[1:]) if a in index and b in index]
        target = np.array([index[b] / index[a] - 1 for a, b in pairs])
        candidates = FOLLOWERS.get(symbol, ())
        if candidates:
            with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
                read = dict(zip(candidates, pool.map(lambda t: self._own_closes(t, days), candidates)))
            request["candidates"] = [url for _, url in read.values()]
            found = tracking(target, self._returns({t: c for t, (c, _) in read.items()}, pairs))
            if found["matches"]:
                payload = {"status": "OK", "index": symbol, "sessions": [days[0], days[-1]], "returns": len(pairs),
                           "line": LINE, "from": "candidates", "candidates": list(candidates), **found}
                return {"payload": payload, "request": request, "retrieved_at": retrieved, "range": (asof, asof)}
        found = tracking(target, self._returns(self.closes(days), pairs))
        payload = {"status": "OK", "index": symbol, "sessions": [days[0], days[-1]], "returns": len(pairs),
                   "line": LINE, "from": "market", **found}
        return {"payload": payload, "request": request, "retrieved_at": retrieved, "range": (asof, asof)}

    def closed_at(self, subject: str, asof: str) -> dict:
        """The listings whose close on a session lay between two prices: `subject` is `{day}|{low}|{high}`, and the
        session is the New York exchange's last on or before `day` that has closed, whose closes are what the market
        stood at on `day` (MATH.md 3.1: a fund's row that states shares and their value names the listing whose close
        it is). Every listing's close, as traded that session, from the whole market's read (`closes`,
        harness.observations): `{status, day, session, low, high, listings: [{ticker, close}]}`, closest to the
        middle first, or `NOT_FOUND` where no session on or before `day` has closed."""
        retrieved = now()
        day, low, high = subject.split("|")
        lo, hi = float(low), float(high)
        start = (date.fromisoformat(day) - timedelta(days=10)).isoformat()
        moment = datetime.now(timezone.utc).timestamp()
        found = sorted(d for d, closes in calendars.closes(NYSE, start, day).items() if closes <= moment)
        request = {"method": "GET", "closes": self.massive.url(GROUPED.format(day="{date}"), {"adjusted": "false"})}
        if not found:
            return {"payload": {"status": "NOT_FOUND", "day": day, "low": lo, "high": hi}, "request": request,
                    "retrieved_at": retrieved, "range": (asof, asof)}
        session = found[-1]
        request["sessions"] = [session, session]
        # a past session's closes from the machine's read of it; today's, once its session has closed, read now and
        # not kept there, since a session's closes are kept once its day has passed (harness.observations)
        closes = ({t: c[session] for t, c in self.closes([session]).items() if session in c} if session < today()
                  else self._closes_now(session))
        middle = (lo + hi) / 2
        listings = sorted(({"ticker": t, "close": c} for t, c in closes.items() if lo <= c <= hi),
                          key=lambda x: (abs(x["close"] - middle), x["ticker"]))
        payload = {"status": "OK", "day": day, "session": session, "low": lo, "high": hi, "listings": listings}
        return {"payload": payload, "request": request, "retrieved_at": retrieved, "range": (asof, asof)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        return {"tracker": lambda subject, covering: self.tracker(subject, covering[1]),
                "closed_at": lambda subject, covering: self.closed_at(subject, covering[1])}
