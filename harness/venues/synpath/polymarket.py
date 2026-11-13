"""Polymarket: Gamma for the catalog, CLOB for books, Data API for trades.

Public reads, no credentials.

Three things this adapter does that a thin wrapper does not:

**Prices come from the book, not the catalog.** Gamma's `bestBid`, `bestAsk`
and `outcomePrices` are cached summaries that lag during fast trading — on an
in-play match Gamma has read 39c while the CLOB book asked 52c at the same
instant, and the site itself shows the book. Catalog calls carry Gamma's
numbers because pulling a book per market would cost one request each;
`fetch_order_book` and `refresh_quotes` read the real book.

**The CLOB returns bids ascending and asks descending.** Best bid is the *last*
bid, best ask the *last* ask. Reading `bids[0]` gives the worst price on the
book, which is a quiet way to mis-price everything.

**Each instrument owns its book.** Unlike Kalshi, YES and NO are separate CLOB
tokens with independently addressable order books, so nothing here is derived.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import ids
from .base import RateLimiter
from .errors import BadRequest
from .types import (
    BookSide, Event, FeeSchedule, Market, MarketStats, OrderBook, Outcome,
    OrderLevel, Quote, iso,
)

GAMMA_URL = "https://gamma-api.polymarket.com"


CLOB_URL = "https://clob.polymarket.com"


DATA_URL = "https://data-api.polymarket.com"


VENUE = "polymarket"


LIMITER = RateLimiter(20.0, burst=20)
"""Gamma took 30 requests at 43/s without complaint, so this is headroom rather
than a measured ceiling: present so an undocumented limit throttles us instead
of failing us. Shared process-wide, as Kalshi's is."""


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value: Any) -> int | None:
    """Gamma sends ISO strings; the CLOB sends epoch milliseconds as a string;
    the Data API sends epoch seconds as a number. All three appear here."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Seconds or milliseconds, told apart by magnitude: 10^11 seconds is
        # the year 5138, so anything larger is already milliseconds.
        return int(value if value > 1e11 else value * 1000)
    text = str(value)
    if text.isdigit():
        return parse_ts(int(text))
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def json_list(value: Any) -> list[Any]:
    """Gamma returns several array fields as JSON-encoded strings."""
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def quoted(value: Any) -> float | None:
    """A price, or `None` when the book is empty on that side.

    Polymarket reports an empty side as 0 or 1 the way Kalshi does. A resting
    order sits strictly inside.
    """
    price = to_float(value)
    if price is None or price <= 0 or price >= 1:
        return None
    return price


def status_of(market: dict[str, Any]) -> tuple[str, str | None, bool]:
    """(normalized status, native status, accepting orders)."""
    accepting = bool(market.get("acceptingOrders"))
    if market.get("closed"):
        statuses = [str(s).lower() for s in json_list(market.get("umaResolutionStatuses"))]
        resolved = market.get("umaResolutionStatus") == "resolved" or "resolved" in statuses
        return ("settled" if resolved else "closed"), "closed", False
    if market.get("active"):
        return "open", "active", accepting
    return "unopened", "inactive", False


def fee_schedule_of(market: dict[str, Any]) -> FeeSchedule | None:
    """Polymarket's published per-market fee, when fees are switched on.

    The venue charges `C * rate * (P * (1 - P)) ** exponent` to the taker
    at match time (its fee docs, and the V2 client's own arithmetic), so it
    is normalized to `quadratic_theta`. Makers are never charged: with
    `takerOnly` the maker rate is a known zero, not an unknown. `rebateRate`
    is the share of fees paid back to makers daily, not a per-trade figure,
    and stays in `info`.
    """
    if not market.get("feesEnabled"):
        return None
    schedule = market.get("feeSchedule") or {}
    rate = to_float(schedule.get("rate"))
    if rate is None:
        return None
    taker_only = bool(schedule.get("takerOnly"))
    return FeeSchedule(
        venue=VENUE,
        scope="market",
        scope_id=str(market.get("id") or ""),
        fee_type="quadratic_theta",
        taker_rate=rate,
        maker_rate=0.0 if taker_only else rate,
        exponent=to_float(schedule.get("exponent")),
        info={"feeType": market.get("feeType"), "feeSchedule": schedule},
    )


def settlement_sources_of(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Gamma's `resolutionSource`, in the shape every venue reports here.

    It is one free-text field rather than a list, and it is often a bare URL
    and often empty. Normalized into `{"name", "url"}` so a caller reads one
    shape across venues; an empty field stays an empty list rather than
    becoming a source named "".
    """
    source = (raw.get("resolutionSource") or "").strip()
    if not source:
        return []
    return [{"name": source, "url": source if source.startswith("http") else None}]


def normalize_market(market: dict[str, Any], event: dict[str, Any] | None = None) -> Market:
    """One Gamma market payload as a unified `Market`."""
    event = event or {}
    market_id = str(market["id"])
    status, native, accepting = status_of(market)
    labels = [str(o) for o in json_list(market.get("outcomes"))] or ["Yes", "No"]
    token_ids = [str(t) for t in json_list(market.get("clobTokenIds"))]
    prices = [to_float(p) for p in json_list(market.get("outcomePrices"))]

    best_bid = quoted(market.get("bestBid"))
    best_ask = quoted(market.get("bestAsk"))
    last = quoted(market.get("lastTradePrice"))
    last_ts = parse_ts(market.get("updatedAt"))
    change = to_float(market.get("oneDayPriceChange"))

    if len(labels) != 2:
        raise BadRequest(f"polymarket: market {market_id} has {len(labels)} outcomes; a Synpath market is binary")

    def outcome(index: int) -> Outcome:
        token = token_ids[index] if index < len(token_ids) else None
        # Gamma publishes one summary bid/ask pair, for the first outcome only.
        # Giving the complement a mirrored copy would be inventing a quote the
        # venue never sent, so the other side carries only its own price until
        # someone asks for its book.
        if index == 0:
            bid, ask = best_bid, best_ask
            outcome_last = last
        else:
            bid = ask = None
            outcome_last = round(1 - last, 6) if last is not None else None
        mid = round((bid + ask) / 2, 6) if bid is not None and ask is not None else None
        # `outcomePrices` is Gamma's own mark for the outcome — a price, but not
        # one of the four the book defines, so it is kept in `info` rather than
        # promoted into a field it would misrepresent.
        return Outcome(
            label=labels[index],
            quote=Quote(
                bid=bid, ask=ask, mid=mid,
                last=outcome_last,
                last_timestamp=last_ts if outcome_last is not None else None,
                last_datetime=iso(last_ts) if outcome_last is not None else None,
            ),
            venue_token_id=token,
            price_change_24h=change if index == 0 else (-change if change is not None else None),
            info={"gamma_outcome_price": prices[index] if index < len(prices) else None},
        )

    event_slug = event.get("slug") or ""
    slug = market.get("slug") or ""
    url = (
        f"https://polymarket.com/event/{event_slug}/{slug}" if event_slug and slug
        else f"https://polymarket.com/market/{slug}" if slug else None
    )
    tags = [
        str(tag.get("slug") or tag.get("label") or "").lower()
        for tag in (event.get("tags") or []) if isinstance(tag, dict)
    ]
    return Market(
        id=ids.qualify(VENUE, market_id),
        venue=VENUE,
        venue_market_id=market_id,
        event_id=ids.qualify(VENUE, str(event["id"])) if event.get("id") is not None else None,
        title=market.get("question") or market_id,
        description=market.get("description"),
        slug=slug or None,
        yes=outcome(0),
        no=outcome(1),
        status=status,  # type: ignore[arg-type]
        native_status=native,
        active=accepting,
        open_timestamp=parse_ts(market.get("startDate")),
        open_datetime=iso(parse_ts(market.get("startDate"))),
        close_timestamp=parse_ts(market.get("endDate")),
        close_datetime=iso(parse_ts(market.get("endDate"))),
        resolution_timestamp=parse_ts(market.get("endDate")),
        resolution_datetime=iso(parse_ts(market.get("endDate"))),
        tick_size=to_float(market.get("orderPriceMinTickSize")),
        face_value=1.0,
        book_model="native_per_outcome",
        stats=MarketStats(
            volume_24h=to_float(market.get("volume24hr")),
            volume_total=to_float(market.get("volumeNum")) or to_float(market.get("volume")),
            liquidity=to_float(market.get("liquidityNum")) or to_float(market.get("liquidity")),
            # Gamma publishes no open interest for a market.
            open_interest=None,
            # Both figures are USDC notional here, which is *not* the unit
            # Kalshi reports volume in. Comparing the two raw numbers across
            # venues is a category error, so each says which it is.
            volume_unit="collateral",
            liquidity_unit="collateral",
            as_of=parse_ts(market.get("updatedAt")),
        ),
        url=url,
        image_url=market.get("image") or market.get("icon"),
        category=(tags or [None])[0],
        tags=tags,
        series_id=None,
        outcome_label=market.get("groupItemTitle") or None,
        neg_risk=market.get("negRisk"),
        settlement_sources=settlement_sources_of(market) or settlement_sources_of(event),
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
    tags = [
        str(tag.get("slug") or tag.get("label") or "").lower()
        for tag in (event.get("tags") or []) if isinstance(tag, dict)
    ]
    event_id = str(event["id"])
    return Event(
        id=ids.qualify(VENUE, event_id),
        venue=VENUE,
        venue_event_id=event_id,
        title=event.get("title") or event_id,
        description=event.get("description"),
        slug=event.get("slug"),
        markets=markets,
        status=status,  # type: ignore[arg-type]
        native_status="closed" if event.get("closed") else "active" if event.get("active") else None,
        category=(tags or [None])[0],
        tags=tags,
        mutually_exclusive=(
            bool(event["negRisk"]) if event.get("negRisk") is not None else None
        ),
        close_timestamp=parse_ts(event.get("endDate")),
        close_datetime=iso(parse_ts(event.get("endDate"))),
        url=f"https://polymarket.com/event/{event.get('slug')}" if event.get("slug") else None,
        image_url=event.get("image") or event.get("icon"),
        settlement_sources=settlement_sources_of(event),
        info={k: v for k, v in event.items() if k != "markets"},
    )


def normalize_order_book(
    payload: dict[str, Any], *, market_id: str = "", side: BookSide = "yes", depth: int | None = None,
) -> OrderBook:
    """A CLOB book for one token, sorted best-first, labelled with the market
    and side the token belongs to.

    The venue sends bids ascending and asks descending — worst price first on
    both sides. They are re-sorted here so `bids[0]` and `asks[0]` mean what
    every other order book in the world means.
    """
    def levels(rows: Any) -> list[OrderLevel]:
        out = []
        for row in rows or []:
            price, size = to_float(row.get("price")), to_float(row.get("size"))
            if price is None or size is None or size <= 0:
                continue
            out.append(OrderLevel(price=price, size=size))
        return out

    bids = sorted(levels(payload.get("bids")), key=lambda level: level.price, reverse=True)
    asks = sorted(levels(payload.get("asks")), key=lambda level: level.price)
    if depth:
        bids, asks = bids[:depth], asks[:depth]
    timestamp = parse_ts(payload.get("timestamp"))
    return OrderBook(
        market_id=market_id or str(payload.get("market") or ""),
        side=side,
        venue=VENUE,
        bids=bids,
        asks=asks,
        timestamp=timestamp,
        datetime=iso(timestamp),
        book_model="native_per_outcome",
        derived=False,
        depth_scope="top_n" if depth else "full",
        info=payload,
    )
