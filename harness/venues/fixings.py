"""A currency's daily fixings in dollars, before its hourly rates begin (MATH.md 3.1, 3.2): what a close struck abroad, a
dividend paid in another currency, and a notional stated in one are converted at where Yahoo Finance's hourly rates
(harness.venues.yahoo) do not reach back, some 1,020 days before the day they are read. Each fixing is kept with its
date, the source that fixed it, and the moment it was struck where the source states one.

Where a date's fixing comes from: the first of three sources that fixes the currency that date.
1. The European Central Bank's reference rates (harness.venues.ecb), for the thirty-odd currencies it publishes, set
   around 14:10 Frankfurt time: the dollar's rate over the currency's, both set at that moment. The euro's is the
   dollar's rate itself, the dollars one euro buys, since every reference rate is the price of a euro.
2. The Federal Reserve's H.10 (https://www.federalreserve.gov/releases/h10/about.htm: "noon buying rates in New York
   for cable transfers payable in the listed currencies"), for twenty-three currencies, Taiwan's dollar and Sri Lanka's
   rupee among those the ECB does not publish, and on the ECB's closing days for the rest. One request answers every
   currency over a window: `GET https://www.federalreserve.gov/datadownload/Output.aspx?rel=H10&series=
   60f32914ab61dfab590e0e470153e3ae&from=MM/DD/YYYY&to=MM/DD/YYYY&filetype=csv&label=include&layout=seriescolumn`,
   the Data Download Program's package of the release's daily series, read live: five label rows, the fourth
   `Currency:` naming each column's currency and the fifth its identifier, then `Time Period` and a row per date
   (`ND` where none was set). The release states its rates "in currency units per U.S. dollar except as noted by an
   asterisk"; the noted ones, the Australian and New Zealand dollars, the euro, and the pound, are the identifiers
   `RXI$US_...`, dollars per unit.
3. The Bank for International Settlements' daily US dollar exchange rates (`WS_XRU`), compiled from the ECB, the
   Federal Reserve, and each country's central bank (https://www.bis.org/statistics/xrusd/xrusd_doc.pdf), for the
   currencies neither of the first two publishes (the Gulf states', Chile's, Colombia's, Peru's, Egypt's, among
   others): `GET https://stats.bis.org/api/v2/data/dataflow/BIS/WS_XRU/1.0/D..{CCY}.A?startPeriod=&endPeriod=
   &format=csv` (https://stats.bis.org/api-doc/v2/), a row per area quoting the currency and date, `OBS_VALUE` units
   per dollar; where several areas quote one currency, the area with the most dates is taken. Its figures state their
   date and not their moment. Chile's and Colombia's central banks date the rate they publish for a day from the
   previous business day's trades (Chile's dolar observado, Colombia's tasa representativa del mercado), and the
   BIS keeps their dates: read against Yahoo's hourly rates at New York's close over the two years both cover, the
   figure dated d moved with the market of d-1 (correlations 0.62 for CLP and 0.71 for COP, against 0.26 and -0.01
   with d), where every other currency's moved with its own date's. So each of their figures is kept as the market
   day before its date (MARKET_DAY_BEFORE). The BIS answers about a week behind today, which does not matter here:
   fixings are read only for days the hourly rates do not reach.

A source that publishes the currency answers for the window or fails the read, so the call waits: an H.10 answer that
labels no currency is no answer (read on Nov 12, 2026, a store whose euro fixings all came from the BIS, from an H.10
read that held none). Which currencies H.10 publishes is read from its labels, whatever a window's values are, and a
404 is an empty answer only from the BIS, whose answer it is that it holds no series for the key. A store keeps what it
read, so a payload kept before this form keeps its fixings.

What is kept, on the currency pair's rates (`hourly` of `C:{CCY}USD`): `fixings`, `[{day, rate, source, moment}]`,
`rate` the dollars one unit of the currency buys, `source` `ECB`, `Federal Reserve H.10`, or `BIS ({area})`, and
`moment` the ISO time it was struck where the source states it, else none. Readers (harness.derive.series) take a
fixing for a day only before the pair's first hourly rate.
"""

from __future__ import annotations

import csv
import io
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as clock, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from harness.venues.http import TRANSIENT, VenueError, now, pooled

FED = "https://www.federalreserve.gov/datadownload/Output.aspx"
FED_PACKAGE = "60f32914ab61dfab590e0e470153e3ae"  # H.10's daily series, the Data Download Program's package
BIS = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_XRU/1.0/D..{currency}.A"
ECB_SOURCE, FED_SOURCE = "ECB", "Federal Reserve H.10"
ECB_STRUCK = (clock(14, 10), ZoneInfo("Europe/Berlin"))
FED_STRUCK = (clock(12, 0), ZoneInfo("America/New_York"))
# The currencies whose central bank dates the day's rate from the previous business day's trades, as above.
MARKET_DAY_BEFORE = frozenset({"CLP", "COP"})
# H.10's code for Venezuela's bolivar is the one it took before 2008's redenomination; its identifier names the new.
FED_CODES = {"VEB": "VES"}
# The form a currency pair's rates are in: 3, with its fixings before its first hourly rate; 4, the euro's from the ECB
# and no source's empty answer read as its not publishing the currency.
FORM = 4
ATTEMPTS = 4
PAUSE = 2.0
USD = "USD"
EUR = "EUR"  # the currency every ECB reference rate is the price of


def struck(day: str, at: tuple[clock, ZoneInfo]) -> str:
    return datetime.combine(date.fromisoformat(day), at[0], at[1]).isoformat(timespec="seconds")


def fed_rates(text: str) -> dict[str, dict[str, float]]:
    """H.10's CSV as dollars per unit of each currency, by date."""
    rows = list(csv.reader(io.StringIO(text)))
    labels = {r[0].strip().rstrip(":"): r for r in rows if r and not r[0][:1].isdigit()}
    codes, ids = labels.get("Currency"), labels.get("Unique Identifier")
    if not codes or not ids:
        return {}
    out: dict[str, dict[str, float]] = {FED_CODES.get(c.strip(), c.strip()): {} for c in codes[1:] if c.strip()}
    for r in rows:
        if not r or not r[0][:1].isdigit():
            continue
        for k in range(1, min(len(r), len(codes))):
            try:
                value = float(r[k])
            except ValueError:  # ND: no rate that date
                continue
            if value <= 0:
                continue
            code = FED_CODES.get(codes[k].strip(), codes[k].strip())
            out.setdefault(code, {})[r[0]] = value if "RXI$US_" in ids[k] else 1.0 / value
    return out


def bis_rates(text: str) -> tuple[str | None, dict[str, float]]:
    """The BIS's CSV as dollars per unit of the currency by date, from the area quoting most dates, and that area."""
    by_area: dict[str, dict[str, float]] = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            value = float(r.get("OBS_VALUE") or "")
        except ValueError:
            continue
        if value > 0 and r.get("TIME_PERIOD"):
            by_area.setdefault(r.get("REF_AREA") or "", {})[r["TIME_PERIOD"]] = 1.0 / value
    if not by_area:
        return None, {}
    area = max(sorted(by_area), key=lambda a: len(by_area[a]))
    return area, by_area[area]


class Fixings:
    """The three sources, read through one client each: the ECB's (harness.venues.ecb.ECB, which keeps what it reads
    of a window), and the Federal Reserve's and the BIS's here, each window read once."""

    def __init__(self, ecb: Any, *, opener: Callable | None = None, timeout: float = 30.0, pause: float = PAUSE):
        self.ecb = ecb
        self._open = opener or pooled()
        self.timeout, self.pause = timeout, pause
        self._fed: dict[tuple[str, str], tuple[str, dict]] = {}
        self._bis: dict[tuple[str, str, str], tuple[str, tuple]] = {}
        self._lock = threading.Lock()

    def _text(self, url: str, *, absent: bool = False) -> str:
        """A source's answer; with `absent`, a 404 is its answer that it holds nothing for the request (the BIS's)."""
        request = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "Mozilla/5.0 event-risk"})
        for attempt in range(ATTEMPTS):
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 404 and absent:
                    return ""
                if (e.code == 429 or e.code >= 500) and attempt < ATTEMPTS - 1:
                    time.sleep(self.pause * (attempt + 1))
                    continue
                raise VenueError(f"GET {url.split('?')[0]} answered {e.code}") from None
            except TRANSIENT as e:
                if attempt < ATTEMPTS - 1:
                    time.sleep(self.pause * (attempt + 1))
                    continue
                raise VenueError(f"GET {url.split('?')[0]}: {e}") from None
        raise VenueError(f"GET {url.split('?')[0]}: no answer")

    def fed(self, start: str, end: str) -> tuple[str, dict[str, dict[str, float]]]:
        with self._lock:
            if (start, end) not in self._fed:
                fmt = lambda d: date.fromisoformat(d).strftime("%m/%d/%Y")  # noqa: E731
                url = FED + "?" + urllib.parse.urlencode({"rel": "H10", "series": FED_PACKAGE, "from": fmt(start),
                                                          "to": fmt(end), "filetype": "csv", "label": "include",
                                                          "layout": "seriescolumn"})
                for attempt in range(ATTEMPTS):  # it labels every currency it publishes, whatever the window: an
                    rates = fed_rates(self._text(url))  # answer labelling none is no answer, asked again (read live,
                    if rates:  # Nov 12: three such in a row, then its CSV)
                        break
                    if attempt < ATTEMPTS - 1:
                        time.sleep(self.pause * (attempt + 1))
                else:
                    raise VenueError(f"the Federal Reserve's H.10 answered no currencies for {start} to {end}")
                self._fed[(start, end)] = (url, rates)
            return self._fed[(start, end)]

    def bis(self, currency: str, start: str, end: str) -> tuple[str, tuple[str | None, dict[str, float]]]:
        with self._lock:
            if (currency, start, end) not in self._bis:
                url = BIS.format(currency=currency) + "?" + urllib.parse.urlencode(
                    {"startPeriod": start, "endPeriod": end, "format": "csv"})
                self._bis[(currency, start, end)] = (url, bis_rates(self._text(url, absent=True)))
            return self._bis[(currency, start, end)]

    def daily(self, currency: str, start: str, end: str) -> tuple[list[dict], list[str]]:
        """The currency's fixings over the window, a day's from the first source that fixes it, and the requests
        read; each in dollars per unit of the currency."""
        found: dict[str, dict] = {}
        urls: list[str] = []
        url, _, per_euro = self.ecb._window(start, end)
        urls.append(url)
        dollar = per_euro.get(USD) or {}
        unit = {day: 1.0 for day in dollar} if currency == EUR else per_euro.get(currency) or {}  # a euro is a euro
        for day, v in unit.items():
            if dollar.get(day) and v:
                found[day] = {"day": day, "rate": dollar[day] / v, "source": ECB_SOURCE,
                              "moment": struck(day, ECB_STRUCK)}
        url, fed = self.fed(start, end)
        urls.append(url)
        for day, v in (fed.get(currency) or {}).items():
            found.setdefault(day, {"day": day, "rate": v, "source": FED_SOURCE, "moment": struck(day, FED_STRUCK)})
        if currency not in fed and not unit:  # neither publishes the currency: its central bank's, through the BIS
            later = (date.fromisoformat(end) + timedelta(days=7)).isoformat()  # a market day's figure dated after it
            url, (area, bis) = self.bis(currency, start, later if currency in MARKET_DAY_BEFORE else end)
            urls.append(url)
            days = sorted(bis)
            for i, day in enumerate(days):
                market = days[i - 1] if currency in MARKET_DAY_BEFORE else day
                if currency in MARKET_DAY_BEFORE and i == 0:
                    continue  # the first figure's market day is before the window's first date the BIS gave
                if start <= market <= end:
                    found.setdefault(market, {"day": market, "rate": bis[day], "source": f"BIS ({area})",
                                              "moment": None})
        return [found[d] for d in sorted(found)], urls


def with_fixings(hourly: Callable[[str, Any], dict], fixings: Fixings) -> Callable[[str, Any], dict]:
    """A currency pair's rates: its hourly rates where they reach, and, where the window begins before its first
    hour, its daily fixings in dollars for the days before that hour (Fixings.daily). A pair whose hourly rates do
    not reach the window at all, and no source fixes, is refused as the hourly read refuses it (LookupError)."""

    def fetch(subject: str, covering: Any) -> dict:
        start, end = covering
        dollar_pair = subject.startswith("C:") and subject.endswith(USD) and len(subject) == 8
        try:
            got: dict | None = hourly(subject, covering)
        except LookupError:
            if not dollar_pair:
                raise
            got = None
        if not dollar_pair or (got is not None and (got.get("payload") or {}).get("status") != "OK"):
            return _form(got)  # a pair not quoted in dollars, or one its source does not list
        payload = dict((got or {}).get("payload") or {})
        # how far back Yahoo's hours reached when read (its payload's `first`), not the window's first hour
        reach = payload.get("first") or min((b["t"] for b in payload.get("results") or []), default=None)
        first = datetime.fromtimestamp(reach / 1000, ZoneInfo("America/New_York")).date().isoformat() \
            if reach else None
        if first is not None and first < start:  # every day of the window has hours from its start
            return _form(got)
        fixed, urls = fixings.daily(subject[2:5], start, first or end)
        if first is not None:
            fixed = [f for f in fixed if f["day"] <= first]
        if got is None and not fixed:
            raise LookupError(f"no rate for {subject[2:5]} in dollars from {start} to {end}: Yahoo Finance's hourly "
                              f"rates do not reach back so far, and no central bank's fixing is published for it")
        if got is None:
            payload = {"status": "OK", "ticker": subject, "symbol": None, "base": subject[2:5], "quote": USD,
                       "results": []}
        payload["fixings"] = fixed
        payload["parse"] = FORM
        request = {"hourly": (got or {}).get("request"), "fixings": [{"method": "GET", "url": u} for u in urls]}
        return {"payload": payload, "request": request, "retrieved_at": (got or {}).get("retrieved_at") or now(),
                "range": (start, end)}

    fetch.parse = FORM  # type: ignore[attr-defined]
    return fetch


def _form(got: dict) -> dict:
    """A pair's hourly answer in this form, where it needs no fixings: a pair Yahoo does not list as well, so that it
    is not asked again on every read."""
    payload = got.get("payload")
    return {**got, "payload": {**payload, "parse": FORM}} if isinstance(payload, dict) else got
