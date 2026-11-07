"""Coinbase Exchange: a crypto pair's hourly prices and its latest trade, public and with no key. Base
`https://api.exchange.coinbase.com`; each shape below is verified against its documentation page and a live response.

- `hourly`: `GET /products/{BASE}-USD/candles?granularity=3600&start=&end=`
  (https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles) answers at most 300 candles a
  request, newest first, each `[time, low, high, open, close, volume]`: `time` the hour's start in Unix seconds, `open`
  its first trade, `close` its last, the hour in progress included; an hour with no trades has no candle. Two years
  are sixty-two requests, a few seconds. The candles are kept in the shape of Massive's aggregates, `{"results":
  [{"t", "o", "h", "l", "c", "v"}]}` with `t` in Unix milliseconds, the same numbers re-keyed, so a pair's price at
  16:00 New York each session reads as any hourly series does (harness.derive.series).
- `last_trade`: `GET /products/{BASE}-USD/ticker`
  (https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductticker) answers the last trade, `{price,
  size, time, trade_id, bid, ask, volume}`, `time` in ISO 8601 UTC to the nanosecond: a pair's latest mark.

A pair `X:{BASE}USD` is the product `{BASE}-USD`. A product Coinbase does not list answers 404, `{"message":
"NotFound"}`, kept as data like any answer, so the store does not ask again and says why the pair has no prices.
Public endpoints allow ten requests a second per IP, fifteen in a burst, and answer 429 past it
(https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits): the client keeps an interval between requests, and a
429 is waited out.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from harness.derive.series import NY
from harness.venues.http import Http, VenueError, now

BASE = "https://api.exchange.coinbase.com"
CANDLES = 300  # the most a request answers
WORKERS = 4  # requests in flight; the interval keeps them within the public limit
INTERVAL = 0.11  # seconds between requests: ten a second is the public limit


def product(ticker: str) -> str | None:
    """X:BTCUSD is BTC-USD; a ticker that is not a USD pair has no product."""
    if not ticker.startswith("X:") or not ticker.endswith("USD") or len(ticker) <= 5:
        return None
    return f"{ticker[2:-3]}-USD"


def _unlisted(ticker: str, pid: str | None, url: str, retrieved: str, covering: tuple[str, str]) -> dict:
    """A pair Coinbase does not list, as an answer kept as data."""
    said = f"Coinbase Exchange lists no {pid}" if pid else f"{ticker} is not a pair priced in US dollars"
    return {"payload": {"status": "NOT_FOUND", "error": said}, "request": {"method": "GET", "url": url},
            "retrieved_at": retrieved, "range": covering}


class Coinbase:
    def __init__(self, http: Http | None = None):
        self.http = http or Http(interval=INTERVAL)

    def hourly(self, ticker: str, start: str, end: str) -> dict:
        """A pair's hourly candles from the start of `start`, in UTC, to the end of `end` in New York, the as-of's
        day, or now if sooner: as of today, the latest candle is the hour now."""
        pid, retrieved = product(ticker), now()
        if pid is None:
            return _unlisted(ticker, None, f"{BASE}/products", retrieved, (start, end))
        lo = datetime.combine(date.fromisoformat(start), datetime.min.time(), timezone.utc)
        hi = min(datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), NY),
                 datetime.now(timezone.utc))
        spans, t = [], lo
        while t < hi:
            spans.append((t, min(t + timedelta(hours=CANDLES), hi)))
            t += timedelta(hours=CANDLES)
        z = lambda x: x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
        urls = [f"{BASE}/products/{pid}/candles?granularity=3600&start={z(a)}&end={z(b - timedelta(seconds=1))}"
                for a, b in spans]
        first = self.http.call("GET", urls[0])  # a pair Coinbase does not list is answered once
        if first[0] == 404:
            return _unlisted(ticker, pid, urls[0], retrieved, (start, end))
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            pages = [first] + list(pool.map(lambda u: self.http.call("GET", u), urls[1:]))
        refused = next((status for status, _ in pages if status != 200), None)
        if refused is not None:
            raise VenueError(f"Coinbase Exchange answered {refused} for {pid}'s candles")
        found: dict[int, dict] = {}
        for _, rows in pages:
            for time_, low, high, open_, close, volume in rows or []:
                found[int(time_)] = {"t": int(time_) * 1000, "o": float(open_), "h": float(high), "l": float(low),
                                     "c": float(close), "v": float(volume)}
        return {"payload": {"status": "OK", "ticker": ticker, "product": pid,
                            "results": [found[k] for k in sorted(found)]},
                "request": {"method": "GET", "url": urls[0], "pages": len(urls)}, "retrieved_at": retrieved,
                "range": (start, end)}

    def last_trade(self, ticker: str, day: str) -> dict:
        """A pair's last trade now, its latest mark on the as-of date `day`."""
        pid, retrieved = product(ticker), now()
        url = f"{BASE}/products/{pid}/ticker" if pid else f"{BASE}/products"
        if pid is None:
            return _unlisted(ticker, None, url, retrieved, (day, day))
        status, body = self.http.call("GET", url)
        if status == 404:
            return _unlisted(ticker, pid, url, retrieved, (day, day))
        if status != 200:
            raise VenueError(f"Coinbase Exchange answered {status} for {pid}'s last trade")
        return {"payload": {"status": "OK", "ticker": ticker, "product": pid, **body},
                "request": {"method": "GET", "url": url}, "retrieved_at": retrieved, "range": (day, day)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        """The data kinds this client fetches, each a function of (subject, covering)."""
        return {"hourly": lambda subject, covering: self.hourly(subject, covering[0], covering[1]),
                "last_trade": lambda subject, covering: self.last_trade(subject, covering[1])}
