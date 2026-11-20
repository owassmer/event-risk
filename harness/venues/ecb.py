"""The European Central Bank's euro foreign exchange reference rates: each currency's daily rate, public and with no
key, by which a dividend paid in another currency than its listing's price is added back in the price's.

The ECB publishes them whole, as one file: `GET https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip`
(https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), a zip of
one CSV, `eurofxref-hist.csv`: a header `Date,USD,JPY,...` naming each currency's column, then a row per working day
from 1999-01-04 to the latest, newest first, each the units of the currency one euro buys, `N/A` where none was set
that day (a currency before it was published, or after it was replaced by the euro). Read live: 7,140 days and 41
currencies, 643 KB in 1.1 s, its dollar column equal to the ECB's own query service's answer on all 539
days of a two-year window. The ECB sets the rates by a concertation between central banks around 14:10 CET and
publishes them around 16:00 CET on every working day but TARGET's closing days, so a currency's rate in US dollars that
day is the dollar's rate over the currency's, both set at the same moment.

Where the file does not answer, the same rates are read from Frankfurter (https://frankfurter.dev, keyless, MIT), which
republishes the ECB's reference rates: `GET https://api.frankfurter.dev/v1/{start}..{end}?base=EUR` answers
`{"base": "EUR", "start_date", "end_date", "rates": {day: {CCY: units one euro buys}}}`, every currency it holds on each
working day of the window (read live: equal to the file's on every day read).

The file is read once for the process and again once its read is AGAIN old: it holds every window, and grows by a day.
Each window asked is cut from it and answered for its currency: `{"status", "currency",
"per_euro": {CCY: {day: rate}, "USD": {day: rate}}}`, the same numbers as the ECB's. A currency the ECB does not
publish is answered `NOT_FOUND`, kept like any answer, so the store does not ask again and reads its rate elsewhere.
The euro is the currency every rate is the price of, so it has no column of its own: its rate in dollars is the
dollar's rate, and it is answered with a rate of one euro per euro on each day the ECB sets the dollar's.
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable

from harness.venues.http import TRANSIENT, VenueError, now, pooled

HISTORY = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
FRANKFURTER = "https://api.frankfurter.dev/v1"
TIMEOUT = 30.0  # seconds a read waits for an answer
ATTEMPTS = 3  # a source asked this many times before the next is read
PAUSE = 1.0  # seconds between attempts
AGAIN = 3600.0  # seconds the file is kept before it is read again: today's rates are published once, in the afternoon
USD = "USD"
EUR = "EUR"  # the currency every reference rate is the price of


def history(body: bytes) -> dict[str, dict[str, float]]:
    """The history file's rates, `{CCY: {day: units one euro buys}}`."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        text = z.read(next(n for n in z.namelist() if n.endswith(".csv"))).decode("utf-8")
    rows = csv.reader(io.StringIO(text))
    header = [c.strip() for c in next(rows)]
    rates: dict[str, dict[str, float]] = {c: {} for c in header[1:] if c}
    for row in rows:
        if not row or not row[0].strip():
            continue
        day = row[0].strip()
        for k in range(1, min(len(row), len(header))):
            try:
                value = float(row[k])
            except ValueError:  # N/A: none set that day
                continue
            if header[k] and value > 0:
                rates[header[k]][day] = value
    return {c: days for c, days in rates.items() if days}


class ECB:
    def __init__(self, *, opener: Callable | None = None, timeout: float = TIMEOUT, pause: float = PAUSE):
        self._open = opener or pooled()
        self.timeout, self.pause = timeout, pause
        self._file: tuple[float, str, dict[str, dict[str, float]]] | None = None  # when read, retrieved_at, rates
        self._lock = threading.Lock()

    def _get(self, url: str, accept: str) -> bytes:
        request = urllib.request.Request(url, headers={"Accept": accept})
        for attempt in range(ATTEMPTS):
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as e:
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

    def _history(self) -> tuple[str, dict[str, dict[str, float]]]:
        """The whole history, read again once its read is AGAIN old: when it was retrieved, and the rates."""
        if self._file is None or time.monotonic() - self._file[0] > AGAIN:
            body = self._get(HISTORY, "application/zip")
            try:
                self._file = (time.monotonic(), now(), history(body))
            except (zipfile.BadZipFile, StopIteration, UnicodeDecodeError):
                raise VenueError("the ECB answered its history with no file") from None
        return self._file[1], self._file[2]

    def _frankfurter(self, start: str, end: str) -> tuple[str, str, dict[str, dict[str, float]]]:
        url = f"{FRANKFURTER}/{start}..{end}?base={EUR}"
        retrieved = now()
        try:
            body = json.loads(self._get(url, "application/json"))
        except ValueError:
            raise VenueError("Frankfurter answered with no rates") from None
        rates: dict[str, dict[str, float]] = {}
        for day, by in (body.get("rates") or {}).items():
            for currency, value in (by or {}).items():
                if isinstance(value, (int, float)) and value > 0:
                    rates.setdefault(currency, {})[day] = float(value)
        return url, retrieved, rates

    def _window(self, start: str, end: str) -> tuple[str, str, dict[str, dict[str, float]]]:
        """Every currency's rates over the window: the URL read, when, and the rates; from the ECB's file, or from
        Frankfurter where the file does not answer."""
        with self._lock:
            try:
                retrieved, rates = self._history()
            except VenueError:
                return self._frankfurter(start, end)
        cut = {c: {d: v for d, v in days.items() if start <= d <= end} for c, days in rates.items()}
        return HISTORY, retrieved, {c: days for c, days in cut.items() if days}

    def rates(self, currency: str, start: str, end: str) -> dict:
        """A currency's daily reference rate and the dollar's, each in units one euro buys, over the window. The euro's
        own is one on every day the ECB sets the dollar's: its rates are the euro's."""
        url, retrieved, rates = self._window(start, end)
        if currency == EUR and USD in rates:
            rates = {**rates, EUR: {day: 1.0 for day in rates[USD]}}
        if currency not in rates or USD not in rates:
            payload: dict[str, Any] = {"status": "NOT_FOUND", "currency": currency,
                                       "error": f"the ECB publishes no reference rate for {currency}"}
        else:
            payload = {"status": "OK", "currency": currency,
                       "per_euro": {currency: rates[currency], USD: rates[USD]}}
        return {"payload": payload, "request": {"method": "GET", "url": url}, "retrieved_at": retrieved,
                "range": (start, end)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        return {"rates": lambda subject, covering: self.rates(subject, covering[0], covering[1])}
