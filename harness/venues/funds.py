"""A mutual fund's share class: what it is, from the SEC's list of the funds' share classes, and its daily net asset
value and distributions, from Yahoo Finance; each in the shape Massive gives a listing's, so that whatever reads a held
security's reference, prices, dividends and latest mark reads a mutual fund's the same way (MATH.md 3.1). A mutual fund is
a fund, read from its Form N-PORT report whole (harness.venues.sec.nport, by the series the same list names).

Which it is. A ticker is a mutual fund's share class where the SEC lists it as a fund's share class
(`company_tickers_mf.json`, harness.venues.sec.FUNDS: every open-end fund's and ETF's class, `{fields: [cik, seriesId,
classId, symbol], data}`) and no listing's reference gives it (Massive's `/v3/reference/tickers/{ticker}` answers
`NOT_FOUND`): an ETF is on the SEC's list too, and Massive gives it as a listing. Read live, 2026-11-11: VFIAX, FXAIX and
AGTHX are on the SEC's list with their series (S000002839, S000006027, S000009228) and Massive answers NOT_FOUND for each.
A listing is one over any span, those before it listed among them, where it has no prices: so a ticker no listing's
reference gives on the day a read ends, but one does today, is a listing (read live: TSDD, listed
2023-08-21, NOT_FOUND on 2020-03-23 and OK on 2023-10-12 and today, and on the SEC's list as every ETF's class is). It
is decided once a day for each ticker and day, and every other ticker is read from Massive as it always was.

Its prices. Yahoo's chart (`GET https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?period1=&period2=&interval=1d
&events=div`, the surface its own pages read, harness.venues.yahoo) gives a mutual fund's `meta.instrumentType`
`MUTUALFUND`, its `meta.currency` (USD), its `meta.longName`, one bar a day stamped 09:30 in New York on the date its
net asset value is struck for (`timestamp`, seconds), each bar's `indicators.quote[0].close` that value, and its
distributions as `events.dividends`, `{stamp: {amount, date}}`, dated by their ex-date. The value is struck once a
day at New York's close and posted in the evening (read live: VFIAX's value of 2026-11-11 stood in `meta.regularMarketTime`
at 20:08 that evening), so the fund has no mark during a session, and its latest mark is its last value.

A listing abroad named by its exchange's own symbol (`0700.HK`) is a ticker Massive's reference answers NOT_FOUND
(read live: 0700.HK, VOD.L, 7203.T, SAP.DE); its reference is its own market's, from Yahoo's chart
(harness.venues.yahoo.Yahoo.reference), `market` "home", so that what a held security is reads the same way for it.

Shapes. Each answer is Massive's for the same kind: `ticker_ref`, `{status, results: {ticker, name, type: "MF",
market: "funds", active, currency_name, cik, series, class, exchange_traded}}`, the last another class of the same
fund that an exchange lists, where one does, whose holdings are this fund's; `bars`, `{status, ticker, results: [{t, o, h, l, c}]}`, `t`
in milliseconds at midnight in New York of the value's date, each price the value; `dividends`, `{status, results:
[{ticker, ex_dividend_date, cash_amount, currency}]}`; `splits`, none; `snapshot`, `{status, ticker: {ticker, nav: {p,
t}}}`, `t` in nanoseconds at New York's close of the value's date (harness.derive.series.snapshot_mark). Each request
is the one made, so a kept answer says where it came from.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from harness.derive import series
from harness.venues.http import now

NY = ZoneInfo("America/New_York")
MUTUAL = "MF"  # the type a mutual fund's share class is given, in the place of a listing's (harness.derive.identity)
CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{}"
ROUTED = ("ticker_ref", "ticker_on", "bars", "dividends", "splits", "snapshot")
# The form of a reference this client composes, a listing's and a mutual fund's alike, so that one kept in an earlier form
# is read again (harness.observations): 2, a mutual fund's naming its exchange-traded class (exchange_traded); 3, a
# listing abroad named by its symbol answered from its own market, where Massive's NOT_FOUND stood before.
REFERENCE_FORM = 3


def _seconds(day: str, at: time = time(0, 0)) -> int:
    return int(datetime.combine(date.fromisoformat(day), at, NY).timestamp())


class MutualFunds:
    """A mutual fund's facts, over Massive's for every listing: `massive` gives a listing's reference, `sec` the funds'
    list, `yahoo` the chart."""

    def __init__(self, massive: Any, sec: Any, yahoo: Any):
        self.massive, self.sec, self.yahoo = massive, sec, yahoo
        self._decided: dict[tuple[str, str], dict | None] = {}
        self._lock = threading.Lock()

    def exchange_traded(self, entry: dict, ticker: str, day: str) -> str | None:
        """Another share class of the same fund that an exchange lists, an ETF, where its series has one: the SEC
        lists every class of a series under it, and a listing's reference gives the exchange-traded one (read live:
        VTSAX's series, S000002848, has VTI). Its holdings are this fund's, published each day."""
        from harness.venues.sec import FUNDS
        listed = self.sec._list(FUNDS)
        at = {f: i for i, f in enumerate(listed.get("fields") or [])}
        for symbol in sorted({str(r[at["symbol"]]).upper() for r in listed.get("data") or []
                              if r[at["seriesId"]] == entry.get("series")} - {ticker}):
            got = self.massive.ticker(symbol, day)["payload"]
            if got.get("status") == "OK" and (got.get("results") or {}).get("type") == "ETF":
                return symbol
        return None

    def _class(self, ticker: str) -> dict | None:
        """The SEC's entry for a fund's share class of this ticker: its trust's CIK, its series and its class."""
        from harness.venues.sec import FUNDS
        listed = self.sec._list(FUNDS)
        at = {f: i for i, f in enumerate(listed.get("fields") or [])}
        for row in listed.get("data") or []:
            if str(row[at["symbol"]]).upper() == ticker:
                return {"cik": str(row[at["cik"]]).zfill(10), "series": row[at["seriesId"]], "class": row[at["classId"]]}
        return None

    def mutual(self, ticker: str, day: str) -> dict | None:
        """The SEC's entry for the share class where `ticker` is a mutual fund's at `day`: on the SEC's list, and given
        by no listing's reference, today or on `day`. None for every other ticker."""
        key = (ticker.upper(), day)
        with self._lock:
            if key in self._decided:
                return self._decided[key]
        found = self._class(key[0])
        if found is not None and not self._unlisted(key[0], datetime.now(NY).date().isoformat()):
            found = None
        if found is not None and not self._unlisted(key[0], day):
            found = None
        with self._lock:
            self._decided[key] = found
        return found

    def _unlisted(self, ticker: str, day: str) -> bool:
        """Whether no listing's reference gives the ticker on the day, asked once a day."""
        key = (ticker, "listed", day)
        with self._lock:
            if key in self._decided:
                return self._decided[key] is None
        unlisted = self.massive.ticker(ticker, day)["payload"].get("status") == "NOT_FOUND"
        with self._lock:
            self._decided[key] = None if unlisted else {}
        return unlisted

    def _chart(self, ticker: str, start: str, end: str) -> tuple[dict, str]:
        params = {"period1": _seconds(start), "period2": _seconds((date.fromisoformat(end) + timedelta(days=1)).isoformat()),
                  "interval": "1d", "events": "div"}
        found = self.yahoo._chart(ticker, params)
        return (found or {}), CHART.format(ticker)

    @staticmethod
    def _answer(payload: dict, url: str, params: dict, covering: tuple[str, str], retrieved: str) -> dict:
        return {"payload": payload, "request": {"method": "GET", "url": url, "params": params}, "retrieved_at": retrieved,
                "range": tuple(covering)}

    def reference(self, ticker: str, day: str, entry: dict) -> dict:
        retrieved = now()
        chart, url = self._chart(ticker, (date.fromisoformat(day) - timedelta(days=7)).isoformat(), day)
        meta = chart.get("meta") or {}
        return self._answer({"status": "OK", "results": {
            "ticker": ticker, "name": meta.get("longName") or meta.get("shortName"), "type": MUTUAL, "market": "funds",
            "active": True, "currency_name": str(meta.get("currency") or "USD").lower(), **entry,
            "exchange_traded": self.exchange_traded(entry, ticker, day)}},
            url, {"interval": "1d"}, (day, day), retrieved)

    def bars(self, ticker: str, start: str, end: str) -> dict:
        retrieved = now()
        chart, url = self._chart(ticker, start, end)
        stamps = chart.get("timestamp") or []
        closes = (((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
        results = []
        for stamp, value in zip(stamps, closes):
            if value is None:
                continue
            day = datetime.fromtimestamp(stamp, NY).date().isoformat()
            if start <= day <= end:
                results.append({"t": _seconds(day) * 1000, "o": value, "h": value, "l": value, "c": value})
        return self._answer({"status": "OK", "ticker": ticker, "results": results}, url, {"interval": "1d"},
                            (start, end), retrieved)

    def dividends(self, ticker: str, start: str, end: str) -> dict:
        retrieved = now()
        chart, url = self._chart(ticker, start, end)
        paid = ((chart.get("events") or {}).get("dividends") or {}).values()
        results = sorted(({"ticker": ticker, "ex_dividend_date": datetime.fromtimestamp(d["date"], NY).date().isoformat(),
                           "cash_amount": d["amount"], "currency": (chart.get("meta") or {}).get("currency") or "USD"}
                          for d in paid if isinstance(d, dict) and d.get("date") and d.get("amount") is not None),
                         key=lambda r: r["ex_dividend_date"])
        results = [r for r in results if start <= r["ex_dividend_date"] <= end]
        return self._answer({"status": "OK", "results": results}, url, {"events": "div"}, (start, end), retrieved)

    def snapshot(self, ticker: str, day: str) -> dict:
        retrieved = now()
        chart, url = self._chart(ticker, (date.fromisoformat(day) - timedelta(days=7)).isoformat(), day)
        stamps = chart.get("timestamp") or []
        closes = (((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
        last = [(s, v) for s, v in zip(stamps, closes) if v is not None]
        nav = None
        if last:
            struck = datetime.fromtimestamp(last[-1][0], NY).date().isoformat()
            nav = {"p": last[-1][1], "t": _seconds(struck, time(16, 0)) * 1_000_000_000}
        return self._answer({"status": "OK", "ticker": {"ticker": ticker, **({"nav": nav} if nav else {})}}, url,
                            {"interval": "1d"}, (day, day), retrieved)

    def fetchers(self, base: dict[str, Callable[[str, Any], dict]]) -> dict[str, Callable[[str, Any], dict]]:
        """Massive's kinds of a held security, each answered for a mutual fund's share class from its own facts and for
        every other ticker by Massive's own fetcher in `base`."""
        def routed(kind: str) -> Callable[[str, Any], dict]:
            listing = base[kind]
            form = REFERENCE_FORM if kind in ("ticker_ref", "ticker_on") else getattr(listing, "parse", None)

            def one(subject: str, covering: Any) -> dict:
                ticker, day = (subject.split("@", 1) if kind == "ticker_on" else (subject, covering[1]))
                entry = self.mutual(ticker, day)
                if kind in ("ticker_ref", "ticker_on"):  # every reference in this client's form, a listing's as well
                    got = listing(subject, covering) if entry is None else self.reference(ticker, day, entry)
                    if kind == "ticker_ref" and entry is None and series.symbol_abroad(ticker) and \
                            (got.get("payload") or {}).get("status") == "NOT_FOUND":  # a listing abroad, by its symbol
                        got = self.yahoo.reference(ticker, day)
                    if isinstance(got.get("payload"), dict):
                        got["payload"]["parse"] = form
                    return got
                if entry is None:
                    return listing(subject, covering)
                if kind in ("bars", "dividends"):
                    got = (self.bars if kind == "bars" else self.dividends)(ticker, covering[0], covering[1])
                    if form is not None:
                        got["payload"]["parse"] = form
                    return got
                if kind == "snapshot":
                    return self.snapshot(ticker, covering[1])
                return self._answer({"status": "OK", "results": []}, CHART.format(ticker), {}, tuple(covering), now())
            if form is not None:
                one.parse = form  # type: ignore[attr-defined]
            return one
        return {kind: routed(kind) for kind in ROUTED if kind in base}
