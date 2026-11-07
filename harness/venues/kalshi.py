"""Kalshi: markets, events, and series with their own filters; books and daily candlesticks. Base
`https://api.elections.kalshi.com/trade-api/v2`; these reads need no key, and the free Basic tier allows twenty a
second, which the client keeps to. Each shape below is verified against its documentation page and a live response.

Search and listings, `GET {endpoint}` with the analyst's parameters unchanged, each paged by `cursor`:
- `/series` (https://docs.kalshi.com/api-reference/market/get-series-list) answers `{series: [{ticker, title,
  category, categories, tags, settlement_sources, frequency, ...}]}`, every series matching, unpaged.
- `/events` (https://docs.kalshi.com/api-reference/events/get-events) answers `{events, cursor}`, each event with
  `markets` only under `with_nested_markets=true`; `limit` at most 200; multivariate events are left out.
- `/markets` (https://docs.kalshi.com/api-reference/market/get-markets) answers `{markets, cursor}`; `limit` at most
  1000; its `status` filter takes `unopened`, `open`, `paused`, `closed`, `settled`.

A market's record is `GET /markets/{ticker}` with its event, `GET /events/{event_ticker}`, and its series,
`GET /series/{series_ticker}`, which carries the category, the tags, and the settlement sources. The question is
the market's `title`, the rules `rules_primary` and `rules_secondary`, and the close time `close_time`. A market's
own `status` (https://docs.kalshi.com/api-reference/market/get-market) is one of `initialized`, `inactive`,
`active`, `closed`, `determined`, `disputed`, `amended`, `finalized`; `active` is the one that trades. Its
`last_price_dollars` is its last trade, and a market that has never traded has none.

Books: `GET /markets/{ticker}/orderbook`, and `GET /markets/orderbooks?tickers=` for up to 100
(https://docs.kalshi.com/api-reference/market/get-market-orderbook,
https://docs.kalshi.com/getting_started/orderbook_responses), answering `{orderbook_fp: {yes_dollars, no_dollars}}`:
bids only, each level `[price, count]` as strings, ascending with the best bid last. A NO bid at q is a YES ask at
1 - q.

History: `GET /series/{series}/markets/{ticker}/candlesticks?start_ts=&end_ts=&period_interval=1440`
(https://docs.kalshi.com/api-reference/market/get-market-candlesticks), each candle `{end_period_ts, yes_bid:
{close_dollars}, yes_ask: {close_dollars}, price, volume_fp, open_interest_fp}`. A daily candle ends at 00:00 New
York, and the daily price is the mid of its closing YES bid and ask.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterator

from harness.venues.http import Http, now, query
from harness.venues.polymarket import NY, ny_date

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ENDPOINTS = ("/markets", "/events", "/series")
READS_PER_SECOND = 20
BOOKS_PER_CALL = 100
LAST = ("last_price_dollars", "volume_fp", "status")  # what a book keeps of the market read beside it


def listing(record: dict) -> dict:
    m = record.get("market") or {}
    event = record.get("event") or {}
    series = record.get("series") or {}
    sources = series.get("settlement_sources") or event.get("settlement_sources") or []
    named = [f"{s.get('name')} ({s.get('url')})" if s.get("url") else str(s.get("name")) for s in sources
             if isinstance(s, dict) and s.get("name")]
    return {
        "question": m.get("title") or event.get("title"),
        "rules": "\n\n".join(r for r in (m.get("rules_primary"), m.get("rules_secondary")) if r),
        "resolution_source": "; ".join(named) or None,
        "close_time": ny_date(m.get("close_time")),
        "tags": list(series.get("tags") or []),
        "categories": list(series.get("categories") or ([event["category"]] if event.get("category") else [])),
        "series": [s for s in (event.get("series_ticker") or series.get("ticker"),) if s],
        "yes_token": m.get("ticker"),
    }


SERIES_FIELDS = ("ticker", "title", "category", "tags", "frequency")


def lists(endpoint: str, body: dict) -> dict:
    """What a listing returns besides markets: /series returns series, each with its own ticker, title,
    category, tags, and frequency, the parameters /markets and /events take."""
    if endpoint == "/series":
        return {"series": [{k: s.get(k) for k in SERIES_FIELDS} for s in (body or {}).get("series") or []]}
    return {}


def paging(endpoint: str, body) -> dict:
    """How the next page is asked for: the answer's fields other than its lists, its `cursor` among them, which the
    next request passes as `cursor`."""
    return {k: v for k, v in body.items() if not isinstance(v, list)} if isinstance(body, dict) else {}


def event(record: dict) -> dict | None:
    """The event a market belongs to: its ticker and title, with the series' tags."""
    m = record.get("market") or {}
    e = record.get("event") or {}
    ticker = e.get("event_ticker") or m.get("event_ticker")
    if not ticker:
        return None
    return {"event": ticker, "title": e.get("title"), "tags": list((record.get("series") or {}).get("tags") or [])}


# The price Kalshi displays (its market pages, such as https://kalshi.com/nba/championship-odds): "Where the bid and
# ask are close the page shows the midpoint; otherwise it shows the last trade." Kalshi gives no number for close;
# this is Polymarket's documented ten cents.
CLOSE = 0.10
TOLERANCE = 1e-9
# The lifecycle status of a market that trades (https://docs.kalshi.com/api-reference/market/get-market).
OPEN = ("active",)


def _last(payload: dict, record: dict) -> float | None:
    """The last traded YES price: the book's read of the market, else the record's. A market that has never traded
    has none."""
    m = payload.get("market") or record.get("market") or {}
    try:
        price = float(m["last_price_dollars"]) if m.get("last_price_dollars") not in (None, "") else None
        volume = float(m.get("volume_fp") or m.get("volume") or 0)
    except (TypeError, ValueError):
        return None
    return price if price is not None and (price > 0 or volume > 0) else None


def displayed(bid: float | None, ask: float | None, payload: dict, record: dict) -> dict:
    """The midpoint where the best bid and ask are within CLOSE, else the last trade."""
    if bid is not None and ask is not None and ask - bid <= CLOSE + TOLERANCE:
        return {"price": (bid + ask) / 2, "rule": "midpoint"}
    return {"price": _last(payload, record), "rule": "last trade"}


def is_open(record: dict, asof: str | None, past: bool) -> bool | None:
    """Whether the market trades on the as-of date: its status on a record read that day; for a past as-of, whether
    it had opened by then and its close time had not passed."""
    m = record.get("market") or {}
    if not past:
        return m["status"] in OPEN if m.get("status") else None
    if asof is None:
        return None
    opened, closed = ny_date(m.get("open_time")), ny_date(m.get("close_time"))
    return (opened is None or opened <= asof) and (closed is None or closed >= asof)


def levels(payload: dict) -> dict:
    """YES bids from yes_dollars and YES asks from no_dollars, best first."""
    book = (payload or {}).get("orderbook_fp") or {}
    yes = [(float(p), float(c)) for p, c in book.get("yes_dollars") or []]
    no = [(float(q), float(c)) for q, c in book.get("no_dollars") or []]
    return {"bids": sorted(yes, key=lambda x: -x[0]),
            "asks": sorted(((round(1.0 - q, 10), c) for q, c in no), key=lambda x: x[0])}


def daily(payload: dict) -> dict[str, float]:
    """The daily price: the mid of each daily candle's closing YES bid and ask, dated by the
    New York day the candle ends."""
    out: dict[str, float] = {}
    for c in (payload or {}).get("candlesticks") or []:
        bid = (c.get("yes_bid") or {}).get("close_dollars")
        ask = (c.get("yes_ask") or {}).get("close_dollars")
        if bid is None or ask is None:
            continue
        day = datetime.fromtimestamp(c["end_period_ts"] - 1, NY).date().isoformat()
        out[day] = (float(bid) + float(ask)) / 2
    return out


class Kalshi:
    venue = "kalshi"

    def __init__(self, http: Http | None = None):
        self.http = http or Http(interval=1.0 / READS_PER_SECOND)

    def search(self, endpoint: str, params: dict) -> dict:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"Kalshi's search and listing endpoints are {', '.join(ENDPOINTS)}")
        url = BASE + endpoint + query(params)
        status, body = self.http.call("GET", url)
        markets = list(self._markets_in(endpoint, body)) if status == 200 else []
        return {"request": {"method": "GET", "url": url}, "status": status, "response": body, "markets": markets,
                "retrieved_at": now()}

    @staticmethod
    def _markets_in(endpoint: str, body: Any) -> Iterator[tuple[str, dict]]:
        if endpoint == "/markets":
            for m in (body or {}).get("markets") or []:
                yield m["ticker"], {"market": m}
        elif endpoint == "/events":
            for event in (body or {}).get("events") or []:
                context = {k: v for k, v in event.items() if k != "markets"}
                for m in event.get("markets") or []:
                    yield m["ticker"], {"market": m, "event": context}

    def market(self, ticker: str) -> dict:
        """The record: the market, its event, and its series."""
        urls = [f"{BASE}/markets/{ticker}"]
        status, body = self.http.call("GET", urls[0])
        if status != 200 or not (body or {}).get("market"):
            raise LookupError(f"Kalshi answered {status} for market {ticker}")
        market = body["market"]
        urls.append(f"{BASE}/events/{market['event_ticker']}")
        status, body = self.http.call("GET", urls[1])
        event = {k: v for k, v in ((body or {}).get("event") or {}).items() if k != "markets"} if status == 200 else {}
        series = {}
        if event.get("series_ticker"):
            urls.append(f"{BASE}/series/{event['series_ticker']}")
            status, body = self.http.call("GET", urls[2])
            series = (body or {}).get("series") or {} if status == 200 else {}
        return {"payload": {"market": market, "event": event, "series": series},
                "request": {"method": "GET", "urls": urls}, "retrieved_at": now()}

    def book(self, record: dict) -> dict:
        """The book, with the market read at the same moment for its last trade, which the displayed price takes
        where the book is wide."""
        ticker = record["market"]["ticker"]
        url, market_url = f"{BASE}/markets/{ticker}/orderbook", f"{BASE}/markets/{ticker}"
        status, body = self.http.call("GET", url)
        if status != 200:
            return {"payload": {"status": status, "error": body}, "request": {"method": "GET", "url": url},
                    "retrieved_at": now()}
        status, market = self.http.call("GET", market_url)
        payload = {**body, **({"market": {k: market["market"].get(k) for k in LAST}}
                              if status == 200 and (market or {}).get("market") else {})}
        return {"payload": payload, "request": {"method": "GET", "urls": [url, market_url]}, "retrieved_at": now()}

    def books(self, tickers: list[str]) -> dict[str, dict]:
        """The order books of many markets, by ticker: a hundred to a call."""
        out: dict[str, dict] = {}
        for i in range(0, len(tickers), BOOKS_PER_CALL):
            chunk = tickers[i:i + BOOKS_PER_CALL]
            status, body = self.http.call("GET", f"{BASE}/markets/orderbooks" + query({"tickers": chunk}))
            if status == 200:
                out.update({b["ticker"]: {"orderbook_fp": b.get("orderbook_fp")}
                            for b in (body or {}).get("orderbooks") or []})
            else:
                for ticker in chunk:
                    status, one = self.http.call("GET", f"{BASE}/markets/{ticker}/orderbook")
                    if status == 200:
                        out[ticker] = one
        return out

    def history(self, record: dict) -> dict:
        market = record["market"]
        series = (record.get("event") or {}).get("series_ticker") or (record.get("series") or {}).get("ticker")
        opened = market.get("open_time") or market.get("created_time")
        start = int(datetime.fromisoformat(opened.replace("Z", "+00:00")).timestamp()) if opened \
            else int(time.time()) - 400 * 86400  # without an open time, a little more than a year
        url = f"{BASE}/series/{series}/markets/{market['ticker']}/candlesticks" + query(
            {"start_ts": start, "end_ts": int(datetime.now(timezone.utc).timestamp()), "period_interval": 1440})
        status, body = self.http.call("GET", url)
        return {"payload": body if status == 200 else {"status": status, "error": body, "candlesticks": []},
                "request": {"method": "GET", "url": url}, "retrieved_at": now()}
