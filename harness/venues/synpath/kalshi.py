"""Kalshi Trade API v2.

Public read endpoints, no credentials needed.

Two facts about this venue shape the whole adapter:

**One book, two views.** A Kalshi binary market has a single order book. A bid
on NO at 0.88 *is* an ask on YES at 0.12 — the same resting order seen from the
other side. `/orderbook` returns two arrays, `yes_dollars` and `no_dollars`,
and both are **bids**. The YES ask side is the NO bid side reflected through
the market's face value. This adapter does that reflection explicitly and marks
the result `derived=True`, rather than pretending two books exist.

**Zero and one are placeholders, not prices.** An absent bid prints as 0, an
absent ask as 1, and a market that has never traded prints last = 0. All three
are read as `None` here. Passing them through is how a library ends up
reporting that a live market is worth nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any

from . import ids
from .base import MAX_PAGE_LIMIT, HttpClient, RateLimiter, check_status, page_limit
from .errors import BadRequest, ExchangeError
from .types import (
    Event, FeeSchedule, Market, MarketStats, OrderBook, Outcome,
    OrderLevel, Page, Quote, Series, iso,
)

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


SEARCH_URL = "https://api.elections.kalshi.com"
"""Where Kalshi's text search lives.

Not part of the documented Trade API, which has no search at all: its `/events`
endpoint accepts a `query` parameter and silently ignores it, returning the
unfiltered first page. This is the endpoint kalshi.com's own search box calls,
on a different host -- `external-api.kalshi.com` answers 404 for it.

Being undocumented, it can change or start requiring credentials without
notice. When it fails, `search_markets` raises rather than falling back to
scanning the catalog: a scan for a term that matches nothing walks ~13,000
events, and turning a fast failure into a hundred-second one is not a
kindness.
"""


SEARCH_LIMITER = RateLimiter(2.0, burst=2)
"""Deliberately slower than the trade API's limiter. This host publishes no
limits and is not part of the documented surface, so it gets the conservative
end of the guess."""


VENUE = "kalshi"


LIMITER = RateLimiter(5.0, burst=5)
"""Kalshi rate-limits its public reads without documenting it in a header.
Measured: an unpaced burst starts returning 429 around 8 req/s, 5.2 req/s ran
clean, and a 429 clears in about a second. Module-level, so every client in the
process shares one budget — which is what the venue actually counts."""


MARKET_BATCH = 200
"""Tickers per `/markets` call. The ceiling is URI length, not a count (500 is
fine, 800 returns 414), so this sits well under it rather than relying on a
limit that depends on how long tickers happen to be."""


_SETTLED = {"settled", "determined", "finalized"}


_OPEN = {"active", "open"}


_UNOPENED = {"unopened", "initialized"}


EVENT_BATCH = 100
"""Event tickers per `/events?tickers=` call. A full page of search results is
at most `MAX_PAGE_LIMIT` rows, so one batch covers it; 100 tickers measured at
under 1,900 characters of URL."""


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value: Any) -> int | None:
    """Kalshi timestamps: ISO 8601 strings, or unix seconds on some endpoints."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value * 1000)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def quoted(value: Any, *, face_value: float = 1.0) -> float | None:
    """A price, or `None` when Kalshi's placeholder means the side is empty.

    Real resting orders sit strictly inside (0, face_value). A 0 means nobody
    is bidding, a face-value ask means nobody is offering, and a 0 ask has been
    observed on an otherwise empty book. None of the three is a price.
    """
    price = to_float(value)
    if price is None or price <= 0 or price >= face_value:
        return None
    return price


def status_of(market: dict[str, Any]) -> tuple[str, bool]:
    """(normalized status, accepting orders)."""
    native = str(market.get("status") or "").lower()
    if native in _SETTLED:
        return "settled", False
    if native == "closed":
        return "closed", False
    if native in _UNOPENED:
        return "unopened", False
    if native in _OPEN:
        return "open", True
    return "unopened", False


def tick_size_of(market: dict[str, Any]) -> float | None:
    """The minimum price increment, read from the venue's own price ladder.

    `price_ranges` describes the ladder as ranges with a step. A market can in
    principle use a finer step near the extremes, so the smallest step present
    is the one an order has to satisfy everywhere.
    """
    steps = [to_float(r.get("step")) for r in (market.get("price_ranges") or [])]
    valid = [s for s in steps if s]
    return min(valid) if valid else None


def market_url(series_ticker: str | None, event_ticker: str | None, ticker: str | None = None) -> str:
    """The kalshi.com page for an event; markets sit on it, there is no per-market page.

    The path is `/markets/<series>/<slug>/<event>` and the slug segment is not
    checked — any value redirects to the canonical one — so the series ticker
    stands in for it.
    """
    series = (series_ticker or (event_ticker or "").split("-")[0]).lower()
    event = (event_ticker or "").lower()
    url = f"https://kalshi.com/markets/{series}/{series}/{event}"
    return f"{url}/{ticker.lower()}" if ticker else url


def normalize_quotes(
    market: dict[str, Any], face_value: float,
) -> tuple[Quote, Quote, float | None]:
    """YES and NO quotes from one market payload, plus the 24h price change.

    The NO sizes are not published separately and do not need to be: in a
    shared book the orders resting on NO's bid are the same orders resting on
    YES's ask, so the sizes mirror along with the prices.
    """
    yes_bid = quoted(market.get("yes_bid_dollars"), face_value=face_value)
    yes_ask = quoted(market.get("yes_ask_dollars"), face_value=face_value)
    no_bid = quoted(market.get("no_bid_dollars"), face_value=face_value)
    no_ask = quoted(market.get("no_ask_dollars"), face_value=face_value)
    yes_bid_size = to_float(market.get("yes_bid_size_fp"))
    yes_ask_size = to_float(market.get("yes_ask_size_fp"))

    last = quoted(market.get("last_price_dollars"), face_value=face_value)
    last_ts = parse_ts(market.get("updated_time"))
    previous = quoted(market.get("previous_price_dollars"), face_value=face_value)
    change = round(last - previous, 6) if last is not None and previous is not None else None

    def mid(bid: float | None, ask: float | None) -> float | None:
        return round((bid + ask) / 2, 6) if bid is not None and ask is not None else None

    yes = Quote(
        bid=yes_bid, bid_size=yes_bid_size, ask=yes_ask, ask_size=yes_ask_size,
        mid=mid(yes_bid, yes_ask),
        last=last, last_timestamp=last_ts if last is not None else None,
        last_datetime=iso(last_ts) if last is not None else None,
    )
    no_last = round(face_value - last, 6) if last is not None else None
    no = Quote(
        bid=no_bid, bid_size=yes_ask_size, ask=no_ask, ask_size=yes_bid_size,
        mid=mid(no_bid, no_ask),
        last=no_last, last_timestamp=last_ts if no_last is not None else None,
        last_datetime=iso(last_ts) if no_last is not None else None,
    )
    return yes, no, change


def normalize_market(market: dict[str, Any], event: dict[str, Any] | None = None) -> Market:
    """One Kalshi market payload as a unified `Market`."""
    event = event or {}
    ticker = market["ticker"]
    face_value = to_float(market.get("notional_value_dollars")) or 1.0
    status, active = status_of(market)
    yes_quote, no_quote, change = normalize_quotes(market, face_value)
    series_id = event.get("series_ticker") or ticker.split("-")[0]
    event_ticker = market.get("event_ticker") or event.get("event_ticker")

    outcome_label = market.get("yes_sub_title") or market.get("subtitle") or None
    yes_label = outcome_label or "Yes"
    no_label = market.get("no_sub_title") or "No"
    yes = Outcome(label=yes_label, quote=yes_quote, price_change_24h=change)
    no = Outcome(label=no_label, quote=no_quote, price_change_24h=-change if change is not None else None)

    liquidity = to_float(market.get("liquidity_dollars"))
    rules = "\n\n".join(
        text for text in (market.get("rules_primary"), market.get("rules_secondary")) if text
    )
    return Market(
        id=ids.qualify(VENUE, ticker),
        venue=VENUE,
        venue_market_id=ticker,
        event_id=ids.qualify(VENUE, event_ticker) if event_ticker else None,
        title=market.get("title") or ticker,
        description=rules or None,
        slug=ticker.lower(),
        yes=yes,
        no=no,
        status=status,
        native_status=market.get("status"),
        active=active,
        market_type=market.get("market_type") or "binary",
        open_timestamp=parse_ts(market.get("open_time")),
        open_datetime=iso(parse_ts(market.get("open_time"))),
        close_timestamp=parse_ts(market.get("close_time")),
        close_datetime=iso(parse_ts(market.get("close_time"))),
        resolution_timestamp=parse_ts(market.get("expected_expiration_time")),
        resolution_datetime=iso(parse_ts(market.get("expected_expiration_time"))),
        tick_size=tick_size_of(market),
        face_value=face_value,
        book_model="shared_complement",
        stats=MarketStats(
            volume_24h=to_float(market.get("volume_24h_fp")),
            volume_total=to_float(market.get("volume_fp")),
            # Kalshi reports 0.0000 here on every open market sampled, so a zero
            # is an absent figure rather than an illiquid book. Storing the zero
            # would mean publishing a number the venue is not actually claiming.
            liquidity=liquidity or None,
            open_interest=to_float(market.get("open_interest_fp")),
            volume_unit="contracts",
            liquidity_unit="collateral",
            as_of=parse_ts(market.get("updated_time")),
        ),
        url=market_url(series_id, event_ticker, ticker),
        category=event.get("category"),
        tags=[t for t in [event.get("category")] if t],
        series_id=series_id,
        outcome_label=outcome_label,
        neg_risk=event.get("mutually_exclusive"),
        # Kalshi publishes these per event, so a market only has them when it
        # was read with its event -- which every path in this adapter does.
        settlement_sources=list(event.get("settlement_sources") or []),
        info=market,
    )


def normalize_event(event: dict[str, Any]) -> Event:
    markets = event.get("markets") or []
    unified = [normalize_market(m, event) for m in markets]
    closes = [m.close_timestamp for m in unified if m.close_timestamp]
    statuses = {m.status for m in unified}
    for candidate in ("open", "closed", "settled", "unopened"):
        if candidate in statuses:
            status = candidate
            break
    else:
        status = "unopened"
    ticker = event["event_ticker"]
    return Event(
        id=ids.qualify(VENUE, ticker),
        venue=VENUE,
        venue_event_id=ticker,
        title=event.get("title") or ticker,
        description=event.get("sub_title"),
        slug=ticker.lower(),
        markets=unified,
        status=status,  # type: ignore[arg-type]
        category=event.get("category"),
        tags=[t for t in [event.get("category")] if t],
        series_id=event.get("series_ticker"),
        mutually_exclusive=event.get("mutually_exclusive"),
        close_timestamp=max(closes) if closes else None,
        close_datetime=iso(max(closes)) if closes else None,
        settlement_sources=list(event.get("settlement_sources") or []),
        url=market_url(event.get("series_ticker"), ticker),
        info={k: v for k, v in event.items() if k != "markets"},
    )


def normalize_order_book(
    payload: dict[str, Any], *, ticker: str, side: str, face_value: float = 1.0,
    depth: int | None = None,
) -> OrderBook:
    """The shared book, presented from one side.

    `/orderbook` gives `yes_dollars` and `no_dollars`, both bid ladders. For the
    YES view: YES bids are `yes_dollars` as-is, and YES asks are the NO bids
    reflected — a NO bid at 0.88 is a YES ask at `face_value - 0.88`. The NO
    view is the same operation with the arrays swapped.
    """
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    own = book.get(f"{side}_dollars") or book.get(side) or []
    other = book.get(("no" if side == "yes" else "yes") + "_dollars") or []

    def levels(rows: Any) -> list[OrderLevel]:
        out = []
        for row in rows or []:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            price, size = to_float(row[0]), to_float(row[1])
            if price is None or size is None or size <= 0:
                continue
            out.append(OrderLevel(price=price, size=size))
        return out

    bids = sorted(levels(own), key=lambda level: level.price, reverse=True)
    asks = sorted(
        (OrderLevel(price=round(face_value - level.price, 6), size=level.size)
         for level in levels(other)),
        key=lambda level: level.price,
    )
    if depth:
        bids, asks = bids[:depth], asks[:depth]
    return OrderBook(
        market_id=ids.qualify(VENUE, ticker),
        side=side,  # type: ignore[arg-type]
        venue=VENUE,
        bids=bids,
        asks=asks,
        book_model="shared_complement",
        derived=True,
        depth_scope="top_n" if depth else "full",
        info=payload,
    )


def normalize_series(series: dict[str, Any]) -> Series:
    ticker = series.get("ticker") or ""
    fee = None
    if series.get("fee_type"):
        fee = FeeSchedule(
            venue=VENUE,
            scope="series",
            scope_id=ticker,
            fee_type=str(series["fee_type"]),
            multiplier=to_float(series.get("fee_multiplier")),
            rounding="up_to_cent",
            info=series,
        )
    return Series(
        id=ticker,
        venue=VENUE,
        title=series.get("title"),
        category=series.get("category"),
        tags=[str(t) for t in (series.get("tags") or [])],
        fee=fee,
        settlement_sources=list(series.get("settlement_sources") or []),
        info=series,
    )


def _with_status(items: list, status: str) -> list:
    """Keep what is actually in `status`, judged by the item's own status.

    The venue's filter is applied first to keep pages small, but it is not the
    last word: Kalshi's selects events, so a "settled" page can carry markets
    that are still trading. Everything is checked against the normalized status
    before it is returned.
    """
    if status == "all":
        return items
    return [item for item in items if item.status == status]


def query_fingerprint(**terms: Any) -> str:
    """A short, stable tag for the query a cursor belongs to."""
    material = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()[:8]


def encode_market_cursor(
    page_cursor: str | None, position: int | str | None, fingerprint: str,
) -> str:
    """Pack "which venue page, where in it, and for which query".

    `position` is the last ticker returned for a listing, or a row offset for
    search, where relevance order has no stable key to resume after.

    Kalshi has no market-level catalog endpoint: markets are reached by paging
    events and unpacking them. A page holds a variable number of markets, so a
    cursor that only remembered the page would skip every market the caller's
    `limit` trimmed off the end -- which is what it did, losing 87% of the
    catalog on a walk with `limit=5`.

    The offset lives in the cursor rather than on the adapter, so paging
    survives a restart, works from another process, and cannot be disturbed by
    a concurrent caller. The fingerprint is what makes that safe: an offset
    counts rows of one particular query, so resuming with a different `status`
    would land somewhere arbitrary. Recording which query the offset belongs to
    turns that from silent misalignment into a clear error.
    """
    packed = json.dumps(
        {"c": page_cursor, "o": position, "f": fingerprint}, separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(packed.encode()).decode().rstrip("=")


def decode_market_cursor(
    cursor: str | None, fingerprint: str,
) -> tuple[str | None, int | str | None]:
    """`(page cursor, position in that page)`, checked against this query.

    A cursor this library did not issue is rejected rather than guessed at: the
    alternative is silently restarting the walk, which reads as duplicate data
    rather than as an error.
    """
    if not cursor:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded.encode()))
        position = state["o"]
        page_cursor = state["c"]
        carried = state["f"]
    except Exception:
        raise BadRequest(
            f"kalshi: {cursor!r} is not a cursor this API issued; "
            f"pass the `next_cursor` from a previous page, or omit it to start over"
        ) from None
    position_ok = (
        position is None
        or isinstance(position, str)
        or (isinstance(position, int) and not isinstance(position, bool) and position >= 0)
    )
    if not position_ok or (page_cursor is not None and not isinstance(page_cursor, str)):
        raise BadRequest(f"kalshi: malformed cursor {cursor!r}")
    if carried != fingerprint:
        raise BadRequest(
            "kalshi: this cursor belongs to a different query -- the offset it "
            "carries counts rows of the search and status it was issued for. "
            "Keep those arguments the same while paging, or start over without "
            "a cursor."
        )
    return page_cursor, position


# -- search: synpath.Kalshi's methods, as functions of the clients they read through --


def search_events(
    http: HttpClient, search: HttpClient, query: str, *, limit: int | None = None, cursor: str | None = None,
    status: str = "open",
) -> Page[Event]:
    """Events matching `query`, most relevant first, with markets nested.

    The same two requests as `search_markets`, paged by event instead of
    by market.
    """
    check_status(status)
    wanted = page_limit(limit) or MAX_PAGE_LIMIT
    fingerprint = query_fingerprint(q=query, status=status, kind="search_events")
    page_cursor, offset = decode_market_cursor(cursor, fingerprint)
    offset = 0 if offset is None else offset
    if not isinstance(offset, int):
        raise BadRequest(f"kalshi: malformed cursor {cursor!r}")

    hits, venue_next = _search_page(search, query, page_cursor)
    tickers = list(dict.fromkeys(
        hit["event_ticker"] for hit in hits if hit.get("event_ticker")
    ))
    window = tickers[offset:offset + wanted]
    archived: dict[str, list[str]] = {}
    for hit in hits:
        for market in hit.get("markets") or []:
            if hit.get("event_ticker") in window and market.get("ticker"):
                archived.setdefault(hit["event_ticker"], []).append(market["ticker"])
    found = _events_by_ticker(http, window, archived=archived if status != "open" else None)
    # event-risk: each hit names its event's series by title, which the event read whole does not; kept on the
    # event's info as `series_title`, so the series an event belongs to is named without reading the series
    titles = {hit.get("event_ticker"): hit.get("series_title") for hit in hits if hit.get("series_title")}
    for ticker, event in found.items():
        if titles.get(ticker) and isinstance(event.info, dict):
            event.info.setdefault("series_title", titles[ticker])
    events = _with_status([found[ticker] for ticker in window if ticker in found], status)
    offset += len(window)

    if offset < len(tickers):
        next_cursor = encode_market_cursor(page_cursor, offset, fingerprint)
    elif venue_next:
        next_cursor = encode_market_cursor(venue_next, 0, fingerprint)
    else:
        next_cursor = None
    return Page(events, next_cursor=next_cursor)


def _search_page(
    search: HttpClient, query: str, cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the venue's search, checked before it is trusted.

    The endpoint is undocumented, so its most likely failure is a change of
    shape that still answers 200. Read with a default, a renamed field
    becomes an empty result with no cursor -- indistinguishable from a
    term that matched nothing, which is the one wrong answer nobody would
    think to question. A genuine no-match still carries `current_page: []`,
    so requiring the field costs nothing on the honest path.
    """
    payload = search.get("/v1/search/series", {"query": query, "cursor": cursor})
    hits = payload.get("current_page") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise ExchangeError(
            "kalshi: the search endpoint answered in a shape this library does "
            "not recognise (no `current_page` list). It is undocumented and may "
            "have changed. Walk fetch_markets() without a query instead."
        )
    return hits, payload.get("next_cursor") or None


def _events_by_ticker(
    http: HttpClient, tickers: list[str], *, archived: dict[str, list[str]] | None = None,
) -> dict[str, Event]:
    """Full events with nested markets, keyed by event ticker.

    Uses `tickers=`, not `event_ticker=`. The latter looks right, answers
    200, and is silently ignored: the venue returns its unfiltered first
    page, which reads as results rather than as an error. The batch also
    comes back in the venue's order rather than the order asked for, so the
    caller re-indexes by ticker to keep relevance order.
    """
    raw_events = _raw_events_by_ticker(http, tickers)
    if archived:
        # An event settled before the historical cutoff comes back with no
        # markets; its markets are on `/historical/markets`, one batch by ticker.
        wanted = [ticker for event, found in raw_events.items() if not found.get("markets")
                  for ticker in archived.get(event, [])]
        for start in range(0, len(wanted), MARKET_BATCH):
            batch = wanted[start:start + MARKET_BATCH]
            payload = http.get("/historical/markets", {"tickers": ",".join(batch), "limit": len(batch)})
            for raw in payload.get("markets") or []:
                event = raw_events.get(raw.get("event_ticker") or "")
                if event is not None:
                    event.setdefault("markets", [])
                    event["markets"] = [*(event["markets"] or []), raw]
    return {ticker: normalize_event(raw) for ticker, raw in raw_events.items()}


def _raw_events_by_ticker(http: HttpClient, tickers: list[str]) -> dict[str, dict[str, Any]]:
    """The venue's event records with nested markets, keyed by ticker."""
    unique = list(dict.fromkeys(tickers))
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(unique), EVENT_BATCH):
        batch = unique[start:start + EVENT_BATCH]
        payload = http.get("/events", {
            "tickers": ",".join(batch),
            "with_nested_markets": "true",
            "limit": len(batch),
        })
        for raw in payload.get("events") or []:
            if raw.get("event_ticker"):
                found[raw["event_ticker"]] = raw
    return found
