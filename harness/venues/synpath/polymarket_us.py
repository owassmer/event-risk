"""Polymarket US: the CFTC-regulated exchange, through its public gateway.

Public reads, no credentials.

It shares a brand with Polymarket and nothing else. This is a conventional
exchange -- one order book per market, fiat settlement, a matching engine with
a state machine -- rather than the on-chain CLOB the `polymarket` adapter
talks to. Four facts shape the adapter:

**One book, two views.** The venue lists one instrument per market, the YES
side; "to trade against an outcome, you sell YES". The NO view offered here is
the YES book reflected through the face value, the same transform the Kalshi
adapter makes, and it is marked `derived=True` rather than passed off as a
second book. The YES view is the venue's own and is not derived.

**Every data endpoint keys on the slug.** Book, best bid/offer, settlement and
price history are addressed by slug and nothing else, so the slug is the
market id here and the venue's numeric id rides along in `info`.

**The catalog carries no volume, no last trade and no sizes.** A market
payload gives best bid and best ask and nothing more about the book. Those
appear as `None` rather than zero, and `refresh_quotes` reads the book -- one
request -- for the last trade, the sizes, shares traded, open interest and the
market's live state.

**Sort parameters are accepted and ignored.** `orderBy=volume24hr` answers 200
with the same rows in the same order as no sort at all; only `orderBy=id`
changes anything. So the listing walks the catalog in id order, which keeps
offset paging stable against insertions, and `sort` orders the page here
after it is read: `newest` from the catalog's own `createdAt`, and `volume`
and `liquidity` from one best-bid/offer read per market on the page, because
the catalog carries neither figure.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from . import ids
from .base import HttpClient, RateLimiter, check_status, page_limit
from .errors import BadRequest
from .types import (
    Event, Market, MarketStats, OrderBook, Outcome,
    OrderLevel, Page, Quote, iso,
)

GATEWAY_URL = "https://gateway.polymarket.us/v1"


SITE_URL = "https://polymarket.us"


VENUE = "polymarket_us"


LIMITER = RateLimiter(15.0, burst=15)
"""The venue documents 20 requests per second per IP for the public gateway,
answering 429 with no Retry-After beyond that. Set below the ceiling because
the limit is per IP and this budget is per process: two processes on one
machine each at 20/s would be at 40/s. Shared process-wide, as the others are."""


SEARCH_PAGE = 20
"""Results per search page when the caller gives no limit."""


_SETTLED_STATUSES = {"MARKET_STATUS_RESOLVED", "MARKET_STATUS_SETTLED"}


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def amount(value: Any) -> float | None:
    """A gateway money field, `{"value": "0.1060", "currency": "USD"}`, as a float."""
    if isinstance(value, dict):
        return to_float(value.get("value"))
    return to_float(value)


def parse_ts(value: Any) -> int | None:
    """ISO 8601, which this venue writes with nine fractional digits.

    `datetime.fromisoformat` takes at most six, so the fraction is trimmed
    first -- only the run of digits directly after the dot, never anything
    from the timezone suffix behind it. A timestamp that arrives without a
    zone is read as UTC, the same rule `types.ms` applies: reading it as the
    machine's local time put every book timestamp an hour off on a machine in
    London, and nothing in the payload's own numbers would have said so.
    Unix seconds also appear, on price history.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 1e11 else value * 1000)
    text = str(value).replace("Z", "+00:00")
    match = _FRACTION.match(text)
    if match:
        head, digits, rest = match.groups()
        text = f"{head}.{digits[:6]}{rest}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


_FRACTION = re.compile(r"^([^.]*)\.(\d+)(.*)$")


def quoted(value: Any, *, face_value: float = 1.0) -> float | None:
    """A price, or `None` when the venue's placeholder means the side is empty.

    A resolved market prints its sides at 1 and 0 and a best bid or ask of
    `null`; a live book rests strictly inside (0, face_value). None of the
    edge values is a price anyone can trade at.
    """
    price = amount(value)
    if price is None or price <= 0 or price >= face_value:
        return None
    return price


def status_of(market: dict[str, Any]) -> tuple[str, str | None, bool]:
    """(normalized status, native status, accepting orders).

    The venue's `status` is an enum (`MARKET_STATUS_OPEN`,
    `MARKET_STATUS_RESOLVED`, ...) and `closed` a flag; a resolved market is
    still `active: true` and `closed: true` at once, so the enum is read
    first and the flags only where it says nothing.
    """
    native = str(market.get("status") or market.get("ep3Status") or "").upper() or None
    if native in _SETTLED_STATUSES:
        return "settled", native, False
    if market.get("closed"):
        return "closed", native, False
    if market.get("active"):
        accepting = native is None or native.endswith("OPEN")
        return "open", native, accepting
    return "unopened", native, False


def sides_of(market: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(YES side, NO side) by the venue's `long` flag, not by position."""
    sides = [s for s in (market.get("marketSides") or []) if isinstance(s, dict)]
    longs = [s for s in sides if s.get("long") is True]
    shorts = [s for s in sides if s.get("long") is False]
    yes = longs[0] if longs else (sides[0] if sides else {})
    no = shorts[0] if shorts else (sides[1] if len(sides) > 1 else {})
    return yes, no


def _labels(market: dict[str, Any], yes: dict[str, Any], no: dict[str, Any]) -> tuple[str, str]:
    """Display labels: the side's own text, else the market's outcomes list."""
    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except ValueError:
            outcomes = []
    outcomes = [str(o) for o in (outcomes or [])]
    yes_label = yes.get("description") or (outcomes[0] if outcomes else "Yes")
    no_label = no.get("description") or (outcomes[1] if len(outcomes) > 1 else "No")
    return str(yes_label), str(no_label)


def _tags_of(*holders: dict[str, Any] | None) -> list[str]:
    tags: list[str] = []
    for holder in holders:
        for tag in (holder or {}).get("tags") or []:
            if isinstance(tag, dict):
                text = str(tag.get("slug") or tag.get("label") or "").lower()
                if text and text not in tags:
                    tags.append(text)
    return tags


def normalize_market(market: dict[str, Any], event: dict[str, Any] | None = None) -> Market:
    """One gateway market payload as a unified `Market`."""
    event = event or {}
    slug = str(market.get("slug") or market.get("id"))
    face_value = 1.0
    status, native, accepting = status_of(market)
    yes_side, no_side = sides_of(market)
    yes_label, no_label = _labels(market, yes_side, no_side)

    # The catalog quotes the YES book's top only. The NO view is the same two
    # resting orders seen from the other side: a YES bid at 0.106 is a NO ask
    # at 0.894. Sizes and the last trade are not in the catalog at all; they
    # come from the book, through `refresh_quotes`.
    bid = quoted(market.get("bestBidQuote"), face_value=face_value)
    ask = quoted(market.get("bestAskQuote"), face_value=face_value)
    no_bid = round(face_value - ask, 6) if ask is not None else None
    no_ask = round(face_value - bid, 6) if bid is not None else None

    def mid(a: float | None, b: float | None) -> float | None:
        return round((a + b) / 2, 6) if a is not None and b is not None else None

    yes = Outcome(
        label=yes_label, quote=Quote(bid=bid, ask=ask, mid=mid(bid, ask)),
        info={k: v for k, v in yes_side.items() if k != "team"},
    )
    no = Outcome(
        label=no_label, quote=Quote(bid=no_bid, ask=no_ask, mid=mid(no_bid, no_ask)),
        info={k: v for k, v in no_side.items() if k != "team"},
    )

    event_slug = event.get("slug")
    tags = _tags_of(event, market)
    question = market.get("question") or market.get("title") or slug
    short = market.get("titleShort") or market.get("title") or None
    return Market(
        id=ids.qualify(VENUE, slug),
        venue=VENUE,
        venue_market_id=slug,
        event_id=ids.qualify(VENUE, str(event_slug)) if event_slug else None,
        title=question,
        description=market.get("description") or None,
        slug=slug,
        yes=yes,
        no=no,
        status=status,  # type: ignore[arg-type]
        native_status=native,
        active=accepting,
        market_type="binary",
        open_timestamp=parse_ts(market.get("startDate")),
        open_datetime=iso(parse_ts(market.get("startDate"))),
        close_timestamp=parse_ts(market.get("endDate")),
        close_datetime=iso(parse_ts(market.get("endDate"))),
        resolution_timestamp=parse_ts(market.get("endDate")),
        resolution_datetime=iso(parse_ts(market.get("endDate"))),
        tick_size=to_float(market.get("orderPriceMinTickSize")),
        face_value=face_value,
        book_model="shared_complement",
        # Nothing about volume, liquidity or open interest is in the catalog
        # payload. Absent is `None`; a zero would claim the venue said so.
        stats=MarketStats(
            volume_unit="contracts",
            # Every liquidity figure this venue publishes is in shares
            # (`bidShares`, `askShares` on the best bid/offer), never dollars.
            liquidity_unit="contracts",
            as_of=parse_ts(market.get("updatedAt")),
        ),
        url=f"{SITE_URL}/event/{event_slug}" if event_slug else f"{SITE_URL}/market/{slug}",
        image_url=market.get("image") or None,
        category=market.get("category") or event.get("category") or (tags or [None])[0],
        tags=tags,
        series_id=event.get("seriesSlug") or None,
        # `question` is the whole market ("National League Champion"); the
        # short title is the row inside it ("Atlanta Braves"). Where the two
        # are the same string there is no separate label to report.
        outcome_label=short if short and short != question else None,
        neg_risk=None,
        settlement_sources=[],
        info=market,
    )


def normalize_event(event: dict[str, Any]) -> Event:
    markets = [normalize_market(m, event) for m in (event.get("markets") or [])]
    statuses = {m.status for m in markets}
    for candidate in ("open", "closed", "settled", "unopened"):
        if candidate in statuses:
            status = candidate
            break
    else:
        status = "unopened"
    slug = str(event.get("slug") or event.get("id"))
    tags = _tags_of(event)
    return Event(
        id=ids.qualify(VENUE, slug),
        venue=VENUE,
        venue_event_id=slug,
        title=event.get("title") or slug,
        description=event.get("description") or None,
        slug=slug,
        markets=markets,
        status=status,  # type: ignore[arg-type]
        native_status="closed" if event.get("closed") else "active" if event.get("active") else None,
        category=event.get("category") or (tags or [None])[0],
        tags=tags,
        series_id=event.get("seriesSlug") or None,
        # The venue publishes nothing about whether an event's markets exclude
        # one another. `None` is that answer; it is not defaulted to False.
        mutually_exclusive=None,
        close_timestamp=parse_ts(event.get("endDate")),
        close_datetime=iso(parse_ts(event.get("endDate"))),
        url=f"{SITE_URL}/event/{slug}",
        image_url=event.get("image") or None,
        settlement_sources=[],
        info={k: v for k, v in event.items() if k != "markets"},
    )


def normalize_order_book(
    payload: dict[str, Any], *, slug: str, side: str, face_value: float = 1.0,
    depth: int | None = None,
) -> OrderBook:
    """The venue's YES book, presented from one side.

    `/markets/{slug}/book` answers `marketData.bids` and `marketData.offers`,
    both best-first, on the YES instrument. The YES view is those two ladders
    as sent. The NO view reflects them: a YES offer at 0.107 is a NO bid at
    `face_value - 0.107`, and the sides swap.
    """
    data = payload.get("marketData") or payload

    def levels(rows: Any) -> list[OrderLevel]:
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            price, size = amount(row.get("px")), to_float(row.get("qty"))
            if price is None or size is None or size <= 0:
                continue
            out.append(OrderLevel(price=price, size=size))
        return out

    yes_bids = levels(data.get("bids"))
    yes_asks = levels(data.get("offers"))
    if side == "yes":
        bids, asks, derived = yes_bids, yes_asks, False
    else:
        bids = [OrderLevel(price=round(face_value - level.price, 6), size=level.size) for level in yes_asks]
        asks = [OrderLevel(price=round(face_value - level.price, 6), size=level.size) for level in yes_bids]
        derived = True
    bids = sorted(bids, key=lambda level: level.price, reverse=True)
    asks = sorted(asks, key=lambda level: level.price)
    if depth:
        bids, asks = bids[:depth], asks[:depth]
    timestamp = parse_ts(data.get("transactTime"))
    return OrderBook(
        market_id=ids.qualify(VENUE, slug),
        side=side,  # type: ignore[arg-type]
        venue=VENUE,
        bids=bids,
        asks=asks,
        timestamp=timestamp,
        datetime=iso(timestamp),
        book_model="shared_complement",
        derived=derived,
        depth_scope="top_n" if depth else "full",
        info=payload,
    )


def _with_status(items: list, status: str) -> list:
    if status == "all":
        return items
    return [item for item in items if item.status == status]


def query_fingerprint(**terms: Any) -> str:
    material = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()[:8]


def encode_cursor(position: int, fingerprint: str) -> str:
    """Pack an offset (or a search page) with the query it counts rows of.

    An offset only means something for one particular query; resuming it
    under a different status or search term lands somewhere arbitrary. The
    fingerprint turns that from silent misalignment into an error.
    """
    packed = json.dumps({"o": position, "f": fingerprint}, separators=(",", ":"))
    return base64.urlsafe_b64encode(packed.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, fingerprint: str) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded.encode()))
        position, carried = state["o"], state["f"]
    except Exception:
        raise BadRequest(
            f"{VENUE}: {cursor!r} is not a cursor this API issued; pass the "
            f"`next_cursor` from a previous page, or omit it to start over"
        ) from None
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise BadRequest(f"{VENUE}: malformed cursor {cursor!r}")
    if carried != fingerprint:
        raise BadRequest(
            f"{VENUE}: this cursor belongs to a different query -- the offset it "
            f"carries counts rows of the search and status it was issued for. Keep "
            f"those arguments the same while paging, or start over without a cursor."
        )
    return position


# -- search: synpath.PolymarketUS's method, as a function of the client it reads through --


def search_events(
    http: HttpClient, query: str, *, limit: int | None = None, cursor: str | None = None,
    status: str = "open",
) -> Page[Event]:
    """Events matching `query`, most relevant first, with markets nested.

    The venue's search is paged by page number; the cursor carries it,
    tagged with the query it belongs to so a cursor from one search cannot
    be replayed into another.
    """
    check_status(status)
    wanted = page_limit(limit) or SEARCH_PAGE
    fingerprint = query_fingerprint(q=query, status=status, kind="search")
    page_no = decode_cursor(cursor, fingerprint) or 1
    payload = http.get("/search", {"query": query, "limit": wanted, "page": page_no})
    rows = payload.get("events") or []
    events = _with_status([normalize_event(raw) for raw in rows], status)
    next_cursor = encode_cursor(page_no + 1, fingerprint) if len(rows) >= wanted else None
    return Page(events, next_cursor=next_cursor)
