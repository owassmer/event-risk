"""Every kind of fact Massive answers, read where no key is needed, each in the shape Massive gives it, so that nothing
that reads one learns which source answered (harness.analyst.clients: these are the base, and Massive's fetchers take
their kinds where MASSIVE_API_KEY is set).

Where each is read:
- **A US listing's daily prices, dividends, splits and latest mark:** Yahoo Finance's chart (harness.venues.yahoo),
  `GET /v8/finance/chart/{symbol}?period1=&period2=&interval=1d&events=div,splits,capitalGains`, the symbol in
  Yahoo's form (a class after a dash, `BRK-B`; an index `^GSPC` for `I:SPX`, `^` before the rest). Each bar is
  stamped at its session's opening in New York (`timestamp`, seconds), `indicators.quote[0]` its open, high, low,
  close and volume, the close adjusted for splits and not for dividends, as Massive's adjusted bars are; `events`
  holds `dividends` and `capitalGains` (`{stamp: {amount, date}}`, each amount on today's share basis) and `splits`
  (`{numerator, denominator, date}`). Its `meta` gives `instrumentType` (`EQUITY`, `ETF`, `MUTUALFUND`, `INDEX`),
  `longName`, `currency`, `exchangeName` (Yahoo's exchange code), `regularMarketPrice` and `regularMarketTime`.
  Read live: AAPL's two years, 501 bars and 8 dividends, in 0.3 s.
- **Whether a dividend is recurring:** Yahoo states no schedule, so it is read from the payments' own spacing: the
  median gap between ex-dates over the window gives the schedule (12, 4, 2 or 1 a year), each payment on it is
  recurring at that frequency, and one paid inside a third of a period after the one before is not, nor is a capital
  gains distribution. A lone payment states no schedule, and is not recurring.
- **What a US ticker is (`ticker_ref`):** OpenFIGI's mapping of the ticker on the US composite (harness.venues.openfigi,
  `TICKER` with `exchCode` US): its `securityType` (Common Stock, REIT, MLP and the like a share, ADR a depositary
  receipt, ETP an exchange-traded fund or trust, Closed-End Fund, Preference), `compositeFIGI` and
  `shareClassFIGI`, read live for AAPL, TSM, VTI, BND, IBIT, GLD, PDI, O, EPD in 0.5 s; the SEC's lists of tickers for
  its CIK and whether it trades over the counter, and the registrant's submissions record for its SIC code
  (harness.venues.sec); FinanceDatabase's file for what the company does (harness.venues.equities); and the chart
  for its name, currency and exchange. A ticker the chart names a mutual fund, or a listing abroad, is no US
  listing's: it is answered NOT_FOUND, as Massive answers it, and read as a mutual fund's share class or a listing
  abroad (harness.venues.funds). Each reference is as the ticker stands now, a past date's (`ticker_on`) included.
- **A company's listings by its CIK:** the SEC's list of tickers, each exchange listing under the CIK.
- **Every US exchange listing's composite FIGI (`listings`):** FinanceDatabase's rows on a US exchange that is not a
  dealer's market, listed now.
- **The listing that carries a CUSIP (`cusip_listing`):** FinanceDatabase's row of a US listing under that CUSIP,
  and where it holds none, OpenFIGI's mapping of the CUSIP on the US composite: as the security stands now. A US
  ISIN's listings (`figi`) are found the same way, by its CUSIP, and every other ISIN's at OpenFIGI.
- **The ISIN of a share class a FIGI names, the ISIN a US listing carries, where an ISIN trades:** FinanceDatabase's
  rows, which give a company's listings with their ISIN and FIGIs.
- **A fund's holdings:** as its issuer publishes them, where an issuer here does (harness.venues.issuers), and
  otherwise from its Form N-PORT report (harness.venues.sec), which the look-through reads where no holdings answer.
- **What these clients do not read:** a ticker's past changes, and listed options' quotes, contracts and minute bars: each answered NOT_FOUND, as Massive
  answers a fund, a ticker or a contract it holds none for, so that what reads it takes its other source.
"""

from __future__ import annotations

import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from time import monotonic as _clock
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from harness.venues.http import VenueError, now
from harness.venues.yahoo import DEALERS, EXCHANGES

NY = ZoneInfo("America/New_York")
CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{}"
FORM = "keyless-1"  # the form these answers are kept in (harness.observations), apart from Massive's
INDEXES = {"I:SPX": "^GSPC"}  # an index whose symbol at Yahoo is not `^` and its own
# OpenFIGI's security types, as Massive types a listing (https://massive.com/docs/rest/stocks/tickers/ticker-types)
TYPES = {"Common Stock": "CS", "REIT": "CS", "MLP": "CS", "Ltd Part": "CS", "Tracking Stk": "CS", "Stapled Security": "CS",
         "Royalty Trst": "CS", "ADR": "ADRC", "NY Reg Shrs": "NYRS", "GDR": "GDR", "Preference": "PFD",
         "Preferred": "PFD", "ETP": "ETF", "Closed-End Fund": "FUND", "Open-End Fund": "MF", "Mutual Fund": "MF",
         "ETN": "ETN", "Right": "RIGHT", "Warrant": "WARRANT", "Unit": "UNIT"}
REFERENCES_AT_ONCE = 8  # references built side by side: their charts and records go out at each source's pace
META_FOR = 60.0  # seconds a chart's meta serves a reference and a mark: the mark is read again every minute
SCHEDULES = (12, 4, 2, 1)  # payments a year a regular schedule can be
SERIES_GAP = {12: 30.4, 4: 91.3, 2: 182.6, 1: 365.25}  # its days between payments


def yahoo_symbol(ticker: str) -> str:
    """A ticker in Massive's form, as Yahoo writes it."""
    if ticker.startswith("I:"):
        return INDEXES.get(ticker) or f"^{ticker[2:]}"
    return ticker.replace(".", "-")


def _ms(day: str) -> int:
    return int(datetime.combine(date.fromisoformat(day), time(0, 0), NY).timestamp() * 1000)


def _answer(payload: dict, url: str, covering: tuple[str, str], retrieved: str, **request: Any) -> dict:
    return {"payload": {**payload, "parse": FORM}, "request": {"method": "GET", "url": url, **request},
            "retrieved_at": retrieved, "range": tuple(covering)}


def schedule(days: list[str]) -> int | None:
    """The payments a year the ex-dates keep to, from their median gap; None for fewer than two."""
    if len(days) < 2:
        return None
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(days, days[1:])]
    typical = statistics.median(gaps)
    return min(SCHEDULES, key=lambda n: abs(SERIES_GAP[n] - typical))


class Keyless:
    """The kinds Massive answers, from Yahoo's chart, OpenFIGI, the SEC and FinanceDatabase."""

    def __init__(self, yahoo: Any, sec: Any, openfigi: Any, equities: Any, issuers: Any = None):
        self.yahoo, self.sec, self.openfigi, self.equities = yahoo, sec, openfigi, equities
        self.issuers = issuers  # each fund's holdings as its issuer publishes them (harness.venues.issuers)
        self._charts: dict[tuple[str, str, str], dict | None] = {}  # one chart serves a window's bars and dividends
        self._mapped: dict[str, dict] = {}  # OpenFIGI's mapping of each US ticker, once in a process
        self._sic: dict[str, dict] = {}  # each registrant's SIC code, from its submissions record
        self._metas: dict[str, tuple[float, dict | None]] = {}  # each symbol's latest chart's meta, and when read
        self._lock = threading.Lock()

    def _meta(self, ticker: str) -> dict | None:
        """A symbol's chart's meta, what the listing is and its latest mark: one read serves the reference and the
        mark asked within META_FOR seconds of it; None where Yahoo charts nothing under the symbol."""
        symbol = yahoo_symbol(ticker)
        with self._lock:
            held = self._metas.get(symbol)
        if held is not None and _clock() - held[0] < META_FOR:
            return held[1]
        chart = self.yahoo._chart(symbol, {"interval": "1d", "range": "5d"})
        meta = (chart.get("meta") or {}) if chart is not None else None
        with self._lock:
            self._metas[symbol] = (_clock(), meta)
        return meta

    # ---------------------------------------------------------------- the chart

    def _chart(self, ticker: str, start: str, end: str) -> dict | None:
        key = (ticker, start, end)
        with self._lock:
            if key in self._charts:
                return self._charts[key]
        lo = _ms(start) // 1000
        hi = _ms((date.fromisoformat(end) + timedelta(days=2)).isoformat()) // 1000
        found = self.yahoo._chart(yahoo_symbol(ticker), {"interval": "1d", "period1": lo, "period2": hi,
                                                         "events": "div,splits,capitalGains"})
        with self._lock:
            if len(self._charts) > 512:
                self._charts.clear()
            self._charts[key] = found
            self._metas[yahoo_symbol(ticker)] = (_clock(), (found.get("meta") or {}) if found is not None else None)
        return found

    @staticmethod
    def _day(stamp: Any) -> str:
        return datetime.fromtimestamp(stamp, NY).date().isoformat()

    def bars(self, ticker: str, start: str, end: str) -> dict:
        retrieved = now()
        chart = self._chart(ticker, start, end)
        url = CHART.format(yahoo_symbol(ticker))
        if chart is None:
            return _answer({"status": "NOT_FOUND", "ticker": ticker, "results": []}, url, (start, end), retrieved)
        quote = (((chart.get("indicators") or {}).get("quote") or [{}])[0]) or {}
        results = []
        for k, stamp in enumerate(chart.get("timestamp") or []):
            c = (quote.get("close") or [None] * (k + 1))[k]
            day = self._day(stamp)
            if c is None or not start <= day <= end:
                continue
            bar = {"t": _ms(day), "c": c}
            for field, name in (("open", "o"), ("high", "h"), ("low", "l"), ("volume", "v")):
                values = quote.get(field) or []
                if k < len(values) and values[k] is not None:
                    bar[name] = values[k]
            results.append(bar)
        return _answer({"status": "OK", "ticker": ticker, "results": results}, url, (start, end), retrieved,
                       params={"interval": "1d", "events": "div,splits,capitalGains"})

    def dividends(self, ticker: str, start: str, end: str) -> dict:
        """Each distribution's ex-date, amount per share on today's basis, its currency, and whether it is recurring
        (module docstring)."""
        retrieved = now()
        chart = self._chart(ticker, start, end) or {}
        currency = (chart.get("meta") or {}).get("currency") or "USD"
        events = chart.get("events") or {}
        paid = sorted((self._day(d["date"]), float(d["amount"])) for d in (events.get("dividends") or {}).values()
                      if isinstance(d, dict) and d.get("date") and d.get("amount") is not None)
        days = [d for d, _ in paid]
        frequency = schedule(days)
        results = []
        for k, (day, amount) in enumerate(paid):
            gap = (date.fromisoformat(day) - date.fromisoformat(days[k - 1])).days if k else None
            regular = frequency is not None and (gap is None or gap > SERIES_GAP[frequency] / 3)
            results.append({"ticker": ticker, "ex_dividend_date": day, "cash_amount": amount,
                            "split_adjusted_cash_amount": amount, "currency": currency,
                            "distribution_type": "recurring" if regular else "special",
                            **({"frequency": frequency} if regular else {})})
        for g in (events.get("capitalGains") or {}).values():
            if isinstance(g, dict) and g.get("date") and g.get("amount") is not None:
                results.append({"ticker": ticker, "ex_dividend_date": self._day(g["date"]),
                                "cash_amount": float(g["amount"]), "split_adjusted_cash_amount": float(g["amount"]),
                                "currency": currency, "distribution_type": "special"})
        results = sorted((r for r in results if start <= r["ex_dividend_date"] <= end),
                         key=lambda r: r["ex_dividend_date"])
        return _answer({"status": "OK", "results": results}, CHART.format(yahoo_symbol(ticker)), (start, end),
                       retrieved, params={"events": "div,splits,capitalGains"})

    def splits(self, ticker: str, start: str, end: str) -> dict:
        """Each split from the window's start to now, with the factor that puts a quantity before it on today's basis."""
        retrieved = now()
        today = date.today().isoformat()
        chart = self._chart(ticker, start, max(end, today)) or {}
        found = sorted((self._day(s["date"]), float(s["numerator"]), float(s["denominator"]))
                       for s in ((chart.get("events") or {}).get("splits") or {}).values()
                       if isinstance(s, dict) and s.get("date") and s.get("numerator") and s.get("denominator"))
        found = [s for s in found if s[0] >= start]
        results = []
        for k, (day, to, frm) in enumerate(found):
            factor = 1.0
            for _, t2, f2 in found[k:]:
                factor *= f2 / t2
            results.append({"ticker": ticker, "execution_date": day, "split_from": frm, "split_to": to,
                            "adjustment_type": "forward_split" if to > frm else "reverse_split",
                            "historical_adjustment_factor": factor})
        return _answer({"status": "OK", "results": results}, CHART.format(yahoo_symbol(ticker)), (start, today),
                       retrieved)

    def snapshot(self, ticker: str, day: str) -> dict:
        """The latest mark: the chart's regular-market price and the moment it was struck."""
        retrieved = now()
        meta = self._meta(ticker) or {}
        price, at = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        mark = {"lastTrade": {"p": float(price), "t": int(at) * 1_000_000_000}} if price and at else {}
        return _answer({"status": "OK", "ticker": {"ticker": ticker, **mark}}, CHART.format(yahoo_symbol(ticker)),
                       (day, day), retrieved, params={"range": "5d"})

    # ---------------------------------------------------------------- what a ticker is

    def _siblings(self, tickers: list[str]) -> list[str]:
        """The tickers asked next after these, in the order they are: the other classes of a fund's series, and the
        other tickers the SEC lists under a company's CIK, which a company's listings and its first ticker read in turn."""
        funds, companies = self.sec._list(self.sec_url("FUNDS")), self.sec._list(self.sec_url("URL"))
        out: list[str] = []
        for listed, key, name in ((funds, "seriesId", "symbol"), (companies, "cik", "ticker")):
            at = {f: i for i, f in enumerate(listed.get("fields") or [])}
            rows = listed.get("data") or []
            wanted = {str(t).upper().replace(".", "-") for t in tickers}
            groups = {r[at[key]] for r in rows if str(r[at[name]]).upper() in wanted and r[at[key]]}
            out += [str(r[at[name]]).replace("-", ".") for r in rows if r[at[key]] in groups]
        return list(dict.fromkeys(out))

    def _mapping(self, tickers: list[str]) -> dict[str, dict]:
        """OpenFIGI's listing of each US ticker on the US composite, mapped as many to a request as its key allows: a
        request that would go out part filled carries the tickers that would be asked next (_siblings)."""
        with self._lock:
            todo = [t for t in dict.fromkeys(tickers) if t not in self._mapped]
        if todo and len(todo) % self.openfigi.jobs:
            room = self.openfigi.jobs - len(todo) % self.openfigi.jobs
            with self._lock:
                todo += [t for t in self._siblings(todo) if t not in self._mapped and t not in todo][:room]
        if todo:
            jobs = [{"idType": "TICKER", "idValue": t.replace(".", "/").replace("-", "/"), "exchCode": "US"}
                    for t in todo]
            answers: list[dict] = []
            for k in range(0, len(jobs), self.openfigi.jobs):
                answers += self.openfigi._post(jobs[k:k + self.openfigi.jobs])
            with self._lock:
                for t, a in zip(todo, answers):
                    if isinstance(a, dict) and (a.get("data") or "No identifier" in str(a.get("warning") or "")):
                        self._mapped[t] = (a.get("data") or [{}])[0]
        with self._lock:
            return {t: self._mapped.get(t) for t in tickers}

    def _listed(self, ticker: str) -> tuple[str | None, str | None, str | None]:
        """The SEC's CIK of a US ticker, its exchange there, and its registrant's name."""
        companies = self.sec._list(self.sec_url("URL"))
        at = {f: i for i, f in enumerate(companies.get("fields") or [])}
        want = ticker.upper().replace(".", "-")
        for row in companies.get("data") or []:
            if str(row[at["ticker"]]).upper() == want:
                return str(row[at["cik"]]).zfill(10), row[at["exchange"]], row[at["name"]]
        funds = self.sec._list(self.sec_url("FUNDS"))
        at = {f: i for i, f in enumerate(funds.get("fields") or [])}
        for row in funds.get("data") or []:
            if str(row[at["symbol"]]).upper() == want:
                return str(row[at["cik"]]).zfill(10), None, None
        return None, None, None

    @staticmethod
    def sec_url(name: str) -> str:
        from harness.venues import sec
        return getattr(sec, name)

    def _industry(self, cik: str | None) -> dict:
        """A registrant's SIC code and its description, from its submissions record, once in a process."""
        if not cik:
            return {}
        with self._lock:
            if cik in self._sic:
                return self._sic[cik]
        body = self.sec._json(f"{self.sec_url('SUBMISSIONS')}/CIK{cik}.json")
        found = {k: v for k, v in (("sic_code", body.get("sic")), ("sic_description", body.get("sicDescription")))
                 if v}
        with self._lock:
            self._sic[cik] = found
        return found

    def references(self, tickers: list[str], day: str) -> dict[str, dict]:
        """Each US ticker's reference, in the shape of Massive's (module docstring), its OpenFIGI mappings asked
        together."""
        from harness.derive import series  # a listing abroad, or an index, is no US ticker OpenFIGI maps on the US composite
        mapped = self._mapping([t for t in tickers if not t.startswith("I:") and not series.symbol_abroad(t)])
        if len(tickers) < 2:
            return {t: self._reference(t, day, mapped.get(t)) for t in tickers}
        with ThreadPoolExecutor(min(REFERENCES_AT_ONCE, len(tickers))) as pool:  # each chart and record at its
            # source's own pace (harness.venues.http.Pace, harness.venues.sec)
            return dict(zip(tickers, pool.map(lambda t: self._reference(t, day, mapped.get(t)), tickers)))

    def _reference(self, ticker: str, day: str, figi: dict | None) -> dict:
        retrieved = now()
        url = CHART.format(yahoo_symbol(ticker))

        def none(said: str) -> dict:
            return _answer({"status": "NOT_FOUND", "error": said}, url, (day, day), retrieved)

        found = self._meta(ticker)
        meta = found or {}
        exchange = EXCHANGES.get(meta.get("exchangeName") or "")
        if ticker.startswith("I:"):
            return none(f"{ticker} is an index") if found is None else _answer(
                {"status": "OK", "results": {"ticker": ticker, "name": meta.get("longName") or meta.get("shortName"),
                                             "market": "indices", "locale": "us", "active": True}},
                url, (day, day), retrieved)
        if found is None or meta.get("instrumentType") == "MUTUALFUND" or (exchange and exchange[1] != "US"):
            return none(f"no US listing is charted under {ticker}")  # a mutual fund's class, or a listing abroad
        cik, listed_on, registrant = self._listed(ticker)
        kind = TYPES.get((figi or {}).get("securityType") or "") or \
            {"EQUITY": "CS", "ETF": "ETF"}.get(meta.get("instrumentType") or "")
        over_the_counter = listed_on == "OTC" or meta.get("exchangeName") in ("PNK", "OQB", "OQX", "OTC")
        symbol = yahoo_symbol(ticker)
        results = {"ticker": ticker, "name": meta.get("longName") or meta.get("shortName") or registrant,
                   "type": kind, "market": "otc" if over_the_counter else "stocks", "locale": "us",
                   "primary_exchange": exchange[0] if exchange else None,
                   "currency_name": str(meta.get("currency") or "USD").lower(), "active": True,
                   "cik": cik, "composite_figi": (figi or {}).get("compositeFIGI"),
                   "share_class_figi": (figi or {}).get("shareClassFIGI"), **self._industry(cik),
                   "description": self.equities.summary(symbol) if self.equities is not None else None}
        return _answer({"status": "OK", "results": {k: v for k, v in results.items() if v is not None}}, url,
                       (day, day), retrieved, figi="TICKER exchCode US")

    def ticker(self, ticker: str, day: str) -> dict:
        return self.references([ticker], day)[ticker]

    def company_listings(self, cik: str, day: str) -> dict:
        """The company's exchange listings, by its CIK, as the SEC's list of tickers gives them."""
        retrieved = now()
        companies = self.sec._list(self.sec_url("URL"))
        at = {f: i for i, f in enumerate(companies.get("fields") or [])}
        tickers = [str(r[at["ticker"]]).replace("-", ".") for r in companies.get("data") or []
                   if str(r[at["cik"]]).lstrip("0") == str(cik).lstrip("0") and r[at["exchange"]] not in (None, "OTC")]
        refs = self.references(tickers, day) if tickers else {}
        rows = [r["payload"]["results"] for r in refs.values() if r["payload"].get("status") == "OK"]
        return _answer({"status": "OK", "results": rows}, self.sec_url("URL"), (day, day), retrieved)

    # ---------------------------------------------------------------- identifiers

    def _us_rows(self, **by: str) -> list[dict]:
        if self.equities is None:
            return []
        return [r for r in self.equities.rows(**by) if not r["delisted"] and r.get("exchange") in EXCHANGES
                and EXCHANGES[r["exchange"]][1] == "US" and r["exchange"] not in DEALERS]

    def us_composites(self, day: str) -> dict:
        """Every US exchange listing's composite FIGI, from FinanceDatabase's file."""
        retrieved = now()
        rows = self.equities.us_listings() if self.equities is not None else []
        return _answer({"status": "OK", "results": [{"ticker": r["symbol"].replace("-", "."),
                                                     "composite_figi": r["composite_figi"], "locale": "us"}
                                                    for r in rows if r.get("composite_figi")]},
                       "https://github.com/JerBouma/FinanceDatabase", (day, day), retrieved)

    def cusip_listings(self, subjects: list[str]) -> dict[str, dict]:
        """The US listing that carries each CUSIP (`{CUSIP}@{date}`): FinanceDatabase's, else OpenFIGI's, as each
        stands now."""
        retrieved = now()
        out: dict[str, dict] = {}
        ask: list[str] = []
        for s in subjects:
            cusip = s.split("@", 1)[0]
            rows = [r for r in (self.equities.rows(cusip=cusip) if self.equities is not None else [])
                    if not r["delisted"] and EXCHANGES.get(r.get("exchange") or "", ("", ""))[1] == "US"]
            if rows:
                out[s] = [{"ticker": r["symbol"].replace("-", "."), "composite_figi": r.get("composite_figi"),
                           "share_class_figi": r.get("shareclass_figi"), "name": r.get("name")} for r in rows]
            else:
                ask.append(s)
        if ask:
            jobs = [{"idType": "ID_CINS" if s[:1].isalpha() else "ID_CUSIP", "idValue": s.split("@", 1)[0],
                     "exchCode": "US"} for s in ask]
            answers: list[dict] = []
            for k in range(0, len(jobs), self.openfigi.jobs):
                answers += self.openfigi._post(jobs[k:k + self.openfigi.jobs])
            for s, a in zip(ask, answers):
                if not isinstance(a, dict) or a.get("error") and not str(a["error"]).startswith("Invalid"):
                    continue  # not answered just now: read again where it is asked
                out[s] = [{"ticker": str(d["ticker"]).replace("/", "."), "composite_figi": d.get("compositeFIGI"),
                           "share_class_figi": d.get("shareClassFIGI"), "name": d.get("name")}
                          for d in a.get("data") or [] if d.get("ticker")]
        answered: dict[str, dict] = {}
        for s, rows in out.items():
            for r in rows:
                cik, _, _ = self._listed(r["ticker"])
                if cik:
                    r["cik"] = cik
            day = s.split("@", 1)[1] if "@" in s else date.today().isoformat()
            answered[s] = {"payload": {"status": "OK" if rows else "NOT_FOUND", "results": rows, "parse": FORM},
                           "request": {"method": "POST", "url": "https://api.openfigi.com/v3/mapping"},
                           "retrieved_at": retrieved, "range": (day, day)}
        return answered

    def isin_mappings(self, isins: list[str], day: str) -> dict[str, dict]:
        """Each ISIN's US listings and its listings' composite FIGIs, in OpenFIGI's form (harness.venues.openfigi,
        `figi`): a US ISIN, which is its CUSIP with the country before it and a check digit after it, by the listing
        that carries its CUSIP, as `cusip_listing` finds it, FinanceDatabase's where it holds the CUSIP on a US
        exchange; every other from OpenFIGI, and so is one whose listing's ticker OpenFIGI already maps to other FIGIs
        than the file's, as a company reorganized since the file's snapshot is (Ferguson's new CUSIP beside Ferguson
        plc's FIGIs): which is how a mapping the resolution finds in conflict with its reference, read again, is read
        from OpenFIGI (harness.derive.identity.unresolved)."""
        from harness.venues.openfigi import FORMS, PARSE
        retrieved = now()
        out: dict[str, dict] = {}
        ask: list[str] = []
        for isin in isins:
            rows = [r for r in (self.equities.rows(cusip=isin[2:11]) if self.equities is not None else [])
                    if not r["delisted"] and EXCHANGES.get(r.get("exchange") or "", ("", ""))[1] == "US"] \
                if isin[:2] == "US" and len(isin) == 12 else []
            with self._lock:  # a listing whose ticker OpenFIGI maps to other FIGIs: the file is stale for it
                stale = any((m := self._mapped.get(r["symbol"].replace("-", "."))) and m.get("compositeFIGI")
                            and m["compositeFIGI"] != r.get("composite_figi") for r in rows)
            if not rows or stale:
                ask.append(isin)
                continue
            lines = self.equities.rows(isin=isin)
            out[isin] = {"payload": {"status": "OK", "isin": isin, "parse": FORMS.get("figi", PARSE),
                                     "listings": [{"ticker": r["symbol"].replace("-", "/"), "exchCode": "US",
                                                   "compositeFIGI": r.get("composite_figi"),
                                                   "shareClassFIGI": r.get("shareclass_figi"), "name": r.get("name")}
                                                  for r in rows],
                                     "composites": sorted({r["composite_figi"] for r in rows + lines
                                                           if r.get("composite_figi")})},
                         "request": {"method": "GET", "url": "https://github.com/JerBouma/FinanceDatabase",
                                     "cusip": isin[2:11]},
                         "retrieved_at": retrieved, "range": (day, day)}
        if ask:
            out.update(self.openfigi._map("figi", ask, day))
        return out

    def figi_isin(self, figi: str, day: str) -> dict:
        retrieved = now()
        found = None
        if self.equities is not None:
            for column in ("composite_figi", "figi", "shareclass_figi"):
                rows = self.equities.rows(**{column: figi})
                if rows:
                    found = rows[0].get("isin")
                    break
        return _answer({"status": "OK" if found else "NOT_FOUND", "figi": figi, "isin": found},
                       "https://github.com/JerBouma/FinanceDatabase", (day, day), retrieved)

    def ticker_isin(self, ticker: str, day: str) -> dict:
        retrieved = now()
        found = sorted({r["isin"] for r in self._us_rows(symbol=yahoo_symbol(ticker)) if r.get("isin")})
        return _answer({"status": "OK" if found else "NOT_FOUND", "ticker": ticker, "isins": found},
                       "https://github.com/JerBouma/FinanceDatabase", (day, day), retrieved)

    def isin_listings(self, subjects: list[str], day: str) -> dict[str, dict]:
        """Where each ISIN's share class trades (`{ISIN}@{YYYY-MM}`): its listing on an exchange of the ISIN's own
        country, the primary market first, by its local ticker and that country, from FinanceDatabase's rows of the
        ISIN. The file lists a company's lines on every market it is quoted on (Milan's `1AAPL` beside Apple's own), and
        none of them says which line a fund holds; a share class's home exchange is where its ISIN was issued, so that
        listing alone is answered, and an ISIN with none there is NOT_FOUND: where it trades is then unknown."""
        from harness.venues.yahoo import pick
        retrieved = now()
        out = {}
        for s in subjects:
            isin = s.rsplit("@", 1)[0]
            rows = [r for r in (self.equities.rows(isin=isin) if self.equities is not None else [])
                    if not r["delisted"] and r.get("exchange") not in DEALERS]
            home = pick([{"symbol": r["symbol"], "exchange": r["exchange"], "quoteType": "EQUITY"} for r in rows],
                        isin[:2].upper())
            listings = [(home.rsplit(".", 1)[0] if "." in home else home, isin[:2].upper())] if home else []
            out[s] = {"payload": {"status": "OK" if listings else "NOT_FOUND", "isin": isin,
                                  "listings": [{"ticker": t, "country": c} for t, c in listings], "parse": FORM},
                      "request": {"method": "GET", "url": "https://github.com/JerBouma/FinanceDatabase"},
                      "retrieved_at": retrieved, "range": (day, day)}
        return out

    # ---------------------------------------------------------------- a company's record

    def company(self, subject: str, day: str) -> dict:
        """A kind of a company's record (harness.world.search_company), as Massive answers it: its results and analysts'
        ratings answered with none, which search_company reads from its listing's record (harness.venues.yahoo
        `record`); its annual and current reports with none, which it reads from EDGAR (harness.venues.sec); its news,
        guidance and risk factors, which no source here gives without Massive's key, NOT_AUTHORIZED."""
        import json as _json
        kind = _json.loads(subject)["kind"]
        status = "NOT_AUTHORIZED" if kind in ("news", "guidance", "risk_factors") else "OK"
        return _answer({"status": status, **({"results": []} if status == "OK" else {})}, "keyless:company",
                       (day, day), now())

    def _issued(self, fund: str, covering: Any) -> dict | None:
        """A fund's holdings as its issuer publishes them, where an issuer here does (harness.venues.issuers)."""
        got = self.issuers.constituents(fund, covering[1]) if self.issuers is not None else None
        if got is not None:
            got["payload"]["parse"] = FORM
            got["range"] = tuple(covering)
        return got

    # ---------------------------------------------------------------- what no keyless source publishes

    @staticmethod
    def _none(kind: str, said: str, covering: tuple[str, str], **payload: Any) -> dict:
        return {"payload": {"status": "NOT_FOUND", "error": "NOT_FOUND", "message": said, **payload, "parse": FORM},
                "request": {"method": "GET", "url": f"keyless:{kind}"}, "retrieved_at": now(),
                "range": tuple(covering)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        def many(read: Callable[[list[str], str], dict[str, dict]]) -> Callable[[str, Any], dict]:
            def one(subject: str, covering: Any) -> dict:
                return read([subject], covering[1])[subject]
            one.many = lambda subjects, covering: read(list(subjects), covering[1])  # type: ignore[attr-defined]
            one.parse = FORM  # type: ignore[attr-defined]
            return one

        def formed(f: Callable[[str, Any], dict]) -> Callable[[str, Any], dict]:
            f.parse = FORM  # type: ignore[attr-defined]
            return f

        def isin_mapped(read: Callable[[list[str], str], dict[str, dict]]) -> Callable[[str, Any], dict]:
            from harness.venues.openfigi import FORMS, PARSE

            def one(subject: str, covering: Any) -> dict:
                found = read([subject], covering[1]).get(subject)
                if found is None:
                    raise VenueError(f"OpenFIGI could not map {subject} just now")
                return found
            one.many = lambda subjects, covering: read(list(subjects), covering[1])  # type: ignore[attr-defined]
            one.parse = FORMS.get("figi", PARSE)  # type: ignore[attr-defined]
            return one

        held = "no issuer here publishes the fund's holdings by the as-of: its Form N-PORT report is read"
        return {
            "ticker_ref": many(self.references),
            "ticker_on": formed(lambda subject, covering: self.ticker(subject.split("@", 1)[0], covering[1])),
            "cusip_listing": many(lambda subjects, day: self.cusip_listings(subjects)),
            "ticker_events": formed(lambda subject, covering: self._none(
                "ticker_events", "no keyless source lists a ticker's changes", covering, results={"events": []})),
            "listings": formed(lambda subject, covering: self.us_composites(covering[1])),
            "company_listings": formed(lambda subject, covering: self.company_listings(subject, covering[1])),
            "bars": formed(lambda subject, covering: self.bars(subject, covering[0], covering[1])),
            "dividends": formed(lambda subject, covering: self.dividends(subject, covering[0], covering[1])),
            "splits": formed(lambda subject, covering: self.splits(subject, covering[0], covering[1])),
            "snapshot": formed(lambda subject, covering: self.snapshot(subject, covering[1])),
            "constituents": formed(lambda subject, covering: self._issued(subject, covering) or self._none(
                "constituents", held, covering, composite_ticker=subject, results=[])),
            "holdings_history": formed(lambda subject, covering: self._none("holdings_history", held, covering,
                                                                            days={})),
            "holdings_processed": formed(lambda subject, covering: self._none(
                "holdings_processed", held, covering, source="ETF Global", processed_date=None)),
            "figi_isin": formed(lambda subject, covering: self.figi_isin(subject, covering[1])),
            "ticker_isin": formed(lambda subject, covering: self.ticker_isin(subject, covering[1])),
            "isin_listings": many(self.isin_listings),
            "company": formed(lambda subject, covering: self.company(subject, covering[1])),
            "figi": isin_mapped(self.isin_mappings),
            "option": many(lambda subjects, day: {s: self._none("option", "no keyless source quotes a listed option",
                                                                (day, day), ticker=s) for s in subjects}),
            "option_at": formed(lambda subject, covering: self._none(
                "option_at", "no keyless source gives an option's price at a moment", covering)),
            "option_contract": formed(lambda subject, covering: self._none(
                "option_contract", "no keyless source holds a listed contract's record", covering)),
            "minutes": formed(lambda subject, covering: self._none("minutes", "no keyless source gives minute bars",
                                                                   covering, results=[])),
            "option_listings": formed(lambda subject, covering: self._none(
                "option_listings", "no keyless source lists a market's contracts", covering, results=[])),
        }


class _Addresses:
    """The addresses a tracker's answer records its reads by, without a key: Yahoo's chart."""

    @staticmethod
    def url(path: str, params: dict | None = None) -> str:
        return f"{CHART.format('')}{path}"


def keyless_trackers(keyless: Keyless, observations: Any):
    """Which fund's holdings are an index's companies, without a key (harness.venues.trackers): the index's values and
    each candidate fund's closes from Yahoo's chart. No keyless source gives every US listing's close on a session, so
    an index none of its candidate funds matches has no tracker found, and a listing that closed at a price is not
    looked for (`closed_at`, NOT_FOUND)."""
    from harness.venues.trackers import Trackers

    class Tracking(Trackers):
        def values(self, symbol: str, days: list[str]) -> tuple[dict[str, float] | None, str]:
            found = self._closes_of(symbol, days)
            return (found or None), CHART.format(yahoo_symbol(symbol))

        def _own_closes(self, ticker: str, days: list[str]) -> tuple[dict[str, float], str]:
            return self._closes_of(ticker, days), CHART.format(yahoo_symbol(ticker))

        def _closes_of(self, ticker: str, days: list[str]) -> dict[str, float]:
            wanted = set(days)
            bars = keyless.bars(ticker, days[0], days[-1])["payload"].get("results") or []
            return {d: float(b["c"]) for b in bars
                    if (d := datetime.fromtimestamp(b["t"] / 1000, NY).date().isoformat()) in wanted and b["c"] > 0}

        def closes(self, days: list[str]) -> dict[str, dict[str, float]]:
            return {}

        def _closes_now(self, day: str) -> dict[str, float]:
            return {}

        def closed_at(self, subject: str, asof: str) -> dict:
            return Keyless._none("closed_at", "no keyless source gives every listing's close on a session", (asof, asof))

    return Tracking(_Addresses(), observations)
