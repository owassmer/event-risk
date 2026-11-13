"""The European Central Bank's euro foreign exchange reference rates: each currency's daily rate, public and with no
key, by which a dividend paid in another currency than its listing's price is added back in the price's.

`GET https://data-api.ecb.europa.eu/service/data/EXR/D..EUR.SP00.A?startPeriod=&endPeriod=&format=csvdata&detail=dataonly`
(https://data.ecb.europa.eu/help/api/data) answers, as CSV, every daily reference rate over the period, one row per
currency and day: `CURRENCY`, `TIME_PERIOD` (the day, `YYYY-MM-DD`), and `OBS_VALUE`, the units of the currency one
euro buys. The ECB sets them by a concertation between central banks around 14:10 CET and publishes them around 16:00
CET on every working day but TARGET's closing days, for twenty-nine currencies, the US dollar among them
(https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), so a
currency's rate in US dollars that day is the dollar's rate over the currency's, both set at the same moment. A key
naming one currency the ECB does not publish answers without that currency's rows, and one naming none answers 404.

The ECB's certificate chains to a root (Sectigo Public Server Authentication Root E46) that some OpenSSL bundles
lack, macOS's among them; the process's connections verify every source against the machine's own trust store
(harness.venues.http.Pooled).

A query it has not answered before can take tens of seconds to begin answering, and the same query asked again
answers in half a second, its answer by then kept at the ECB. Read live on Nov 12, 2026, eight new queries, from
2019-02 and from 2024-11 to the day, each of every currency and of two: the first byte came after 8 to 25 seconds,
whatever the span or the number of currencies, which changed only the transfer after it (0.3 s for two currencies over
eight years, 3.6 MB of every currency 9 s); asked again, 0.5 s. Its gateway gives up a request it holds at 30 seconds
(504), where the same request asked again answers (read live: one request of six on one afternoon, and one of a
first read's two). So a read waits TIMEOUT seconds for an answer to begin, past the gateway's own limit, and its 504
is what has it asked again: a read given up sooner is a query begun again from nothing, which a slow start never
answers.

One request answers every currency over the window, so the client reads it once for a window and gives each currency
its own data row, re-keyed from that answer: one start's wait for every currency, where a request a currency would
wait each its own: `{"status", "currency",
"per_euro": {CCY: {day: rate}, "USD": {day: rate}}}`, the same numbers as the ECB's. A currency the ECB does not
publish is answered `NOT_FOUND`, kept like any answer, so the store does not ask again and reads its rate elsewhere.
The euro is the currency every rate is the price of, so it has no rows of its own: its rate in dollars is the dollar's
rate, and it is answered with a rate of one euro per euro on each day the ECB sets the dollar's.
"""

from __future__ import annotations

import csv
import io
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


from harness.venues.http import TRANSIENT, Cooldown, VenueError, now, pooled

BASE = "https://data-api.ecb.europa.eu/service/data/EXR/D..EUR.SP00.A"
TIMEOUT = 35.0  # seconds a read waits for an answer to begin: past the gateway's 30, whose 504 has it asked again
ATTEMPTS = 6  # a read asked this many times before it fails: the gateway held four in a row, two minutes, once
PAUSE = 2.0  # seconds between attempts
USD = "USD"
EUR = "EUR"  # the currency every reference rate is the price of


class ECB:
    def __init__(self, *, opener: Callable | None = None, timeout: float = TIMEOUT):
        self._open = opener or pooled()
        self.timeout = timeout
        self._read: dict[tuple[str, str], tuple[str, str, dict[str, dict[str, float]]]] = {}  # by window
        self._lock = threading.Lock()
        self.cooldown = Cooldown("The ECB")  # where it does not answer, it is not asked again for a minute

    def url(self, start: str, end: str) -> str:
        return BASE + "?" + urllib.parse.urlencode({"startPeriod": start, "endPeriod": end, "format": "csvdata",
                                                    "detail": "dataonly"})

    def _window(self, start: str, end: str) -> tuple[str, str, dict[str, dict[str, float]]]:
        """Every currency's rates over the window, read once: the URL, when it was read, and the rates."""
        with self._lock:
            if (start, end) in self._read:
                return self._read[(start, end)]
            self.cooldown.check()
            try:
                return self._fetch(start, end)
            except VenueError:
                self.cooldown.failed()
                raise

    def _fetch(self, start: str, end: str) -> tuple[str, str, dict[str, dict[str, float]]]:
        """Every currency's rates over the window, read now and kept for the window."""
        url = self.url(start, end)
        request = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "event-risk"})
        failed = 0
        while True:
            try:
                with self._open(request, timeout=self.timeout) as response:
                    text = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    text = ""
                    break
                if e.code >= 500 and failed < ATTEMPTS - 1:
                    failed += 1
                    time.sleep(PAUSE)
                    continue
                raise VenueError(f"the ECB answered {e.code}") from None
            except TRANSIENT as e:
                if failed < ATTEMPTS - 1:
                    failed += 1
                    time.sleep(PAUSE)
                    continue
                raise VenueError(f"the ECB: {e}") from None
        rates: dict[str, dict[str, float]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                rates.setdefault(row["CURRENCY"], {})[row["TIME_PERIOD"]] = float(row["OBS_VALUE"])
            except (KeyError, TypeError, ValueError):
                continue
        self._read[(start, end)] = (url, now(), rates)
        return self._read[(start, end)]

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
            payload = {"status": "OK", "currency": currency, "per_euro": {currency: rates[currency], USD: rates[USD]}}
        return {"payload": payload, "request": {"method": "GET", "url": url}, "retrieved_at": retrieved,
                "range": (start, end)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        return {"rates": lambda subject, covering: self.rates(subject, covering[0], covering[1])}
