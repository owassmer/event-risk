"""Polymarket: Gamma's search and listings, the CLOB's books, and the data API's price history, each
verified against its documentation page and a live response.

Search and listings, `GET https://gamma-api.polymarket.com{endpoint}` with the analyst's parameters unchanged:
- `/public-search` (https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles) answers
  `{events: [{..., tags, markets: [...]}], pagination: {hasMore, totalResults}}`; a market from it carries its
  event's tags.
- `/markets` (https://docs.polymarket.com/api-reference/markets/list-markets) answers a list of markets; an offset
  past a few thousand is refused, 422 "offset too large, use /markets/keyset".
- `/markets/keyset` (https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination) answers
  `{markets, next_cursor}`, `limit` at most 100, `after_cursor` the cursor; `next_cursor` only on a full page.
- `/events` (https://docs.polymarket.com/api-reference/events/list-events) answers a list of events, each with its
  markets; `/events/keyset` answers `{events, next_cursor}`.

A market's record is `GET /markets/{id}` with `GET /markets/{id}/tags`
(https://docs.polymarket.com/api-reference/markets/get-market-tags-by-id): the market object itself carries no
tags. Its question is `question`, its rules `description`, its resolution source `resolutionSource` (often empty,
the source named in the rules), and its close time `endDate`, else its event's. Its YES token is the
`clobTokenIds` entry at the index whose `outcomes` entry is "Yes"; both are JSON-encoded strings.

Books: `POST https://clob.polymarket.com/books` with `[{"token_id": ...}]`, up to 500 tokens a call
(https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body). Each book is
`{asset_id, bids, asks, tick_size, min_order_size, last_trade_price, ...}`, levels `{price, size}` as decimal
strings. The documentation says bids descend and asks ascend; the books the venue returns list each side worst price
first, so levels are sorted by price. A closed market has no book: the batch answers 200 and leaves it out.

History: `GET https://data-api.polymarket.com/v2/prices-history?token_id=&interval=max&bucket_seconds=43200`
(https://docs.polymarket.com/api-reference/markets/get-a-tokens-price-history), no key, answering
`{data: [{timestamp, price, resolution_seconds}], pagination: {next_cursor}}`, oldest first, `timestamp` in epoch
seconds, ending with the latest tick; the daily price is the last point inside each UTC day.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from harness.venues.http import Http, now, query

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
ENDPOINTS = ("/public-search", "/markets", "/events", "/markets/keyset", "/events/keyset")
BOOKS_PER_CALL = 500
NY = ZoneInfo("America/New_York")


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        found = json.loads(value) if isinstance(value, str) else []
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def ny_date(iso: str | None) -> str | None:
    """The New York date of an ISO time."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(NY).date().isoformat()
    except ValueError:
        return None


def yes_token(record: dict) -> str | None:
    m = record.get("market") or {}
    tokens, outcomes = _list(m.get("clobTokenIds")), _list(m.get("outcomes"))
    if "Yes" in outcomes and len(tokens) == len(outcomes):
        return str(tokens[outcomes.index("Yes")])
    return None


def listing(record: dict) -> dict:
    m = record.get("market") or {}
    event = record.get("event") or {}
    return {
        "question": m.get("question"),
        "rules": m.get("description"),
        "resolution_source": m.get("resolutionSource") or event.get("resolutionSource") or None,
        "close_time": ny_date(m.get("endDate") or event.get("endDate")),  # the market's endDate, else its event's
        "tags": [t["label"] for t in record.get("tags") or [] if isinstance(t, dict) and t.get("label")],
        "categories": [c for c in (m.get("category"), event.get("category")) if c],
        "series": [],
        "yes_token": yes_token(record),
    }


def lists(endpoint: str, body: dict) -> dict:
    """What a listing returns besides markets: nothing here."""
    return {}


def paging(endpoint: str, body) -> dict:
    """How the next page is asked for: /public-search's `pagination` ({hasMore, totalResults}, with `page` the
    parameter), the keyset endpoints' `next_cursor`; the plain lists page by `offset` and return none."""
    return {k: v for k, v in body.items() if not isinstance(v, list)} if isinstance(body, dict) else {}


def event(record: dict) -> dict | None:
    """The event a market belongs to, as search returned it: its id, title, and tags."""
    e = record.get("event")
    if not e:
        return None
    return {"event": e.get("id"), "title": e.get("title"),
            "tags": [t["label"] for t in record.get("tags") or [] if isinstance(t, dict) and t.get("label")]}


# The price Polymarket displays (https://docs.polymarket.com/concepts/prices-orderbook): "The displayed price is the
# midpoint of the bid-ask spread. If the spread is wider than $0.10, the last traded price is shown instead."
SPREAD = 0.10
TOLERANCE = 1e-9  # 0.40 - 0.30 is 0.10000000000000003 in floating point, and not wider than ten cents


def last(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def displayed(bid: float | None, ask: float | None, payload: dict, record: dict) -> dict:
    """The midpoint of the best bid and ask, or the book's last trade where the spread is wider than ten cents or a
    side is empty."""
    if bid is not None and ask is not None and ask - bid <= SPREAD + TOLERANCE:
        return {"price": (bid + ask) / 2, "rule": "midpoint"}
    return {"price": last(payload.get("last_trade_price")), "rule": "last trade"}


def is_open(record: dict, asof: str | None, past: bool) -> bool | None:
    """Whether the market trades on the as-of date: `closed` on a record read that day; for a past as-of, whether it
    had started by then and had not yet closed (closedTime, else its end date)."""
    m = record.get("market") or {}
    if not past:
        return (not m["closed"]) if isinstance(m.get("closed"), bool) else None
    started = ny_date(m.get("startDate"))
    ended = (ny_date(m.get("closedTime")) if m.get("closed") else None) or \
        ny_date(m.get("endDate") or (record.get("event") or {}).get("endDate"))
    if asof is None:
        return None
    return (started is None or started <= asof) and (ended is None or ended >= asof)


def levels(payload: dict) -> dict:
    """The YES token's book, best first on each side."""
    bids = [(float(b["price"]), float(b["size"])) for b in (payload or {}).get("bids") or []]
    asks = [(float(a["price"]), float(a["size"])) for a in (payload or {}).get("asks") or []]
    return {"bids": sorted(bids, key=lambda x: -x[0]), "asks": sorted(asks, key=lambda x: x[0])}


def daily(payload: dict) -> dict[str, float]:
    """The daily price: the last point inside each UTC day."""
    out: dict[str, float] = {}
    for point in sorted((payload or {}).get("data") or [], key=lambda p: p["timestamp"]):
        out[datetime.fromtimestamp(point["timestamp"], timezone.utc).date().isoformat()] = float(point["price"])
    return out


class Polymarket:
    venue = "polymarket"

    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def search(self, endpoint: str, params: dict) -> dict:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"Polymarket's search and listing endpoints are {', '.join(ENDPOINTS)}")
        url = GAMMA + endpoint + query(params)
        status, body = self.http.call("GET", url)
        markets = list(self._markets_in(endpoint, body)) if status == 200 else []
        return {"request": {"method": "GET", "url": url}, "status": status, "response": body, "markets": markets,
                "retrieved_at": now()}

    @staticmethod
    def _markets_in(endpoint: str, body: Any) -> Iterator[tuple[str, dict]]:
        def of_event(event: dict) -> Iterator[tuple[str, dict]]:
            context = {k: event.get(k) for k in ("id", "title", "slug", "category", "endDate", "resolutionSource")}
            for m in event.get("markets") or []:
                yield str(m["id"]), {"market": m, "tags": event.get("tags") or [], "event": context}

        if endpoint in ("/public-search", "/events/keyset"):
            for event in (body or {}).get("events") or []:
                yield from of_event(event)
        elif endpoint == "/events":
            for event in body or []:
                yield from of_event(event)
        else:
            for m in ((body or {}).get("markets") if endpoint == "/markets/keyset" else body) or []:
                yield str(m["id"]), {"market": m, "tags": m.get("tags") or []}

    def market(self, venue_id: str) -> dict:
        """The record: the market and its tags."""
        url = f"{GAMMA}/markets/{venue_id}"
        status, market = self.http.call("GET", url)
        if status != 200 or not isinstance(market, dict) or not market.get("id"):
            raise LookupError(f"Polymarket answered {status} for market {venue_id}")
        tags_url = f"{url}/tags"
        status, tags = self.http.call("GET", tags_url)
        return {"payload": {"market": market, "tags": tags if status == 200 and isinstance(tags, list) else []},
                "request": {"method": "GET", "urls": [url, tags_url]}, "retrieved_at": now()}

    def books(self, tokens: list[str]) -> dict[str, dict]:
        """The order books of many tokens, by token."""
        out: dict[str, dict] = {}
        for i in range(0, len(tokens), BOOKS_PER_CALL):
            chunk = [{"token_id": t} for t in tokens[i:i + BOOKS_PER_CALL]]
            status, body = self.http.call("POST", f"{CLOB}/books", chunk)
            if status == 200:
                out.update({str(b.get("asset_id")): b for b in body or []})
        return out

    def book(self, record: dict) -> dict:
        token = yes_token(record)
        if token is None:
            raise LookupError("this market has no outcome named Yes")
        request = [{"token_id": token}]
        status, body = self.http.call("POST", f"{CLOB}/books", request)
        payload = (body or [{}])[0] if status == 200 else {"status": status, "error": body}
        return {"payload": payload, "request": {"method": "POST", "url": f"{CLOB}/books", "body": request},
                "retrieved_at": now()}

    def history(self, record: dict) -> dict:
        token = yes_token(record)
        if token is None:
            raise LookupError("this market has no outcome named Yes")
        return self.token_history(token)

    def token_history(self, token: str) -> dict:
        """A token's whole price history, every page."""
        url = f"{DATA}/v2/prices-history" + query({"token_id": token, "interval": "max", "bucket_seconds": 43200})
        points, urls, cursor = [], [url], None
        while True:
            status, body = self.http.call("GET", url if cursor is None else url + "&cursor=" + cursor)
            if status != 200:
                return {"payload": {"status": status, "error": body, "data": []},
                        "request": {"method": "GET", "url": url}, "retrieved_at": now()}
            points.extend(body.get("data") or [])
            cursor = (body.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
            urls.append(url + "&cursor=" + cursor)
        return {"payload": {"token_id": token, "data": points},
                "request": {"method": "GET", "url": url, **({"pages": len(urls)} if len(urls) > 1 else {})},
                "retrieved_at": now()}
