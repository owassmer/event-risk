"""Polymarket US, read through our copy of Synpath's Polymarket US read paths (harness.venues.synpath.polymarket_us,
from 0.11.0), with what the store needs besides. No
read needs a key. Each fact below was checked against a live answer.

Polymarket US is a CFTC-regulated exchange and clearing house trading in US dollars, for US residents, with its own
central limit order book (https://docs.polymarket.us/getting-started/what-is-polymarket-us): a book apart from
Polymarket's, traded by a population Polymarket's international book is closed to. Its public gateway is
`https://gateway.polymarket.us/v1` (https://docs.polymarket.us/api-reference/introduction).

How it lists: an event (`GET /events/slug/{slug}`) groups one or more markets, each a binary market named by its slug,
the YES side the one instrument it trades. A family is an event, `family:polymarket_us:{event slug}`, titled by the
event's `title`, or the series that holds events, `family:polymarket_us:series:{series slug}`. An event names its
series by slug (`seriesSlug`); the series is `GET /series?slug=` (https://docs.polymarket.us/api-reference/series/overview,
read live: `{series: [{id, slug, title}]}`), and its events `GET /events?seriesId=&closed=false&limit=` (read live:
`{events: [...]}`, each with its markets). A league states its season's series from above (`activeSeriesId`,
https://docs.polymarket.us/api-reference/sports/get-league-by-slug), and no series names a league, so a series is the
widest family read here. A market's `question` is its event's, the same on every market of the event; its own words are its
`title` ("By December 31, 2026", "50+ bps Increase"), so a market's question here is its `title`, which its family's
title completes. Its rules are its `description`, which names the sources that settle it in prose ("Outcome sourced
from …"): the venue states no source as data (https://docs.polymarket.us/learn/markets/market-resolution), so its
`resolution_source` is empty and the rules carry it. Its close is its `endDate`. Its strike is stated as data only on a
market that settles against a reference price, in `assetPriceTerms`
(https://docs.polymarket.us/api-reference/market/overview): a range's bounds `rangeLower` and `rangeUpper`, inclusive,
the open side of a tail null; an up-or-down market paying where the close is at or above `priceToBeat`; an above-or-below
threshold or a one-touch target `strike`. Its fee is the market's `feeCoefficient`, the taker's θ in θ × C × p × (1 − p)
(https://docs.polymarket.us/fees). It pays nothing on a position (PAID): its incentive programs pay for deposits,
referrals, trading, and liquidity (https://docs.polymarket.us/incentives/overview).

A market read alone (`GET /market/slug/{slug}`) names no event; its event is the one the venue's own search finds for
the market's `question`, its event's title, that lists the market's slug among its markets. Where the search finds
none, the market keeps its family's title, its own `question`, and its family has no id to read it whole by.

Search is the venue's own, `GET /search?query=&limit=&page=` (https://docs.polymarket.us/api-reference/search/search),
events with their markets. Books: `GET /markets/{slug}/book` (https://docs.polymarket.us/api-reference/markets/get-market-book),
`{marketData: {bids, offers, state, stats, transactTime}}`, both ladders best first, each level `{px: {value}, qty}`, on
the YES instrument; `stats` carries the last trade, `lastTradePx` and `lastTradeSetTime`, in YES terms. One market to a
request: the venue has no batch read of books. Its edge answers httpx's default User-Agent with 429 (`Retry-After: 10`)
after five book reads in a row; named as this client (harness.venues.markets.USER_AGENT), the reads go at Synpath's
pace, within the documented 25 a second (https://docs.polymarket.us/api-reference/rate-limits).

History: `GET /price-history?symbol={slug}&fixedInterval=INTERVAL_ALL&fidelity=1440`
(https://docs.polymarket.us/api-reference/price-history/get-price-history), `{history: [{timestamp, longPrice,
shortPrice}]}`, the YES display price "normally derived from the best ask" and the NO "from one minus the best bid":
each sample a bid and an ask, one a day from the market's start; the daily price is their midpoint, dated by the New
York day the sample closes.

Settlement: a market's `status` is `MARKET_STATUS_RESOLVED` or `MARKET_STATUS_SETTLED` once its outcome is determined,
and resolutions are final (https://docs.polymarket.us/learn/markets/market-resolution); `GET
/markets/{slug}/settlement` (https://docs.polymarket.us/api-reference/markets/get-market-settlement) answers `{slug,
settlement}`, YES's settled price, 1 or 0. A resolved market's record carries that price as `settlement`, so the record
the store keeps says how the market settled, as Kalshi's and Polymarket's records do.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from harness.venues.synpath import polymarket_us as su
from harness.venues.synpath.base import HttpClient, RateLimiter
from harness.venues.synpath.types import Event, Market

from harness.venues.http import VenueError, now
from harness.venues.markets import FAMILIES, NY, family_id, ny_date, number, read

VENUE = "polymarket_us"
EVENT_FIELDS = ("id", "slug", "title", "category", "endDate", "seriesSlug")  # what a record keeps of its event
OPEN = ("MARKET_STATUS_OPEN",)
FINAL = ("MARKET_STATUS_RESOLVED", "MARKET_STATUS_SETTLED")
SEARCH_PAGE = 20  # the events one search page holds when a market's event is looked for
PAID = {"url": "https://docs.polymarket.us/incentives/overview", "label": None, "paid": None}  # nothing on a position


def _amount(value: Any) -> float | None:
    return number(value.get("value") if isinstance(value, dict) else value)


# ---------------------------------------------------------------- a market's terms, from its record


def strike(m: dict) -> dict | None:
    """The threshold the venue states as data, on a market that settles against a reference price: a range's bounds,
    inclusive; an up-or-down market's price to beat, paid at or above it; or an above-or-below threshold or a one-touch
    target, which the rules say how to compare."""
    t = m.get("assetPriceTerms") or {}
    kind = t.get("marketType")
    lower, upper = _amount(t.get("rangeLower")), _amount(t.get("rangeUpper"))
    if kind == "ASSET_PRICE_MARKET_TYPE_RANGE":
        if lower is not None and upper is not None:
            return {"type": "between", "floor": lower, "cap": upper}
        if upper is not None:
            return {"type": "less_or_equal", "cap": upper}
        if lower is not None:
            return {"type": "greater_or_equal", "floor": lower}
    if kind == "ASSET_PRICE_MARKET_TYPE_UP_DOWN" and _amount(t.get("priceToBeat")) is not None:
        return {"type": "greater_or_equal", "floor": _amount(t.get("priceToBeat"))}
    if kind in ("ASSET_PRICE_MARKET_TYPE_ABOVE_BELOW", "ASSET_PRICE_MARKET_TYPE_ONE_TOUCH") \
            and _amount(t.get("strike")) is not None:
        return {"type": kind.removeprefix("ASSET_PRICE_MARKET_TYPE_").lower(), "strike": _amount(t.get("strike")),
                **({"direction": t["direction"].removeprefix("ASSET_PRICE_DIRECTION_").lower()}
                   if t.get("direction") and not t["direction"].endswith("UNSPECIFIED") else {})}
    return None


def family(record: dict) -> dict | None:
    """The family a market belongs to: its event's id and title. A market's own `question` is its event's title, the
    same on every market of the event, so a market whose event the search did not find keeps its family's title,
    with no id to read it whole by."""
    e, m = record.get("event") or {}, record.get("market") or {}
    if e.get("slug"):
        above = within(e)
        return {"id": family_id(VENUE, str(e["slug"])), "title": e.get("title") or m.get("question"),
                **({"within": above} if above else {})}
    return {"id": None, "title": m["question"]} if m.get("question") else None


def within(info: dict) -> dict | None:
    """The family an event belongs to: its series, by its slug, and its title where it was read."""
    slug = info.get("seriesSlug")
    if not slug:
        return None
    return {"id": family_id(VENUE, f"series:{slug}"), **({"title": info["series_title"]} if info.get("series_title") else {})}


def fees(m: dict) -> dict | None:
    """The market's fee: its taker coefficient, θ in θ × C × p × (1 − p)."""
    theta = number(m.get("feeCoefficient"))
    return {"fee_type": "quadratic_theta", "taker_rate": theta} if theta is not None else None


def listing(record: dict, at: str | None = None) -> dict:
    """A market's terms, as its record states them now: Polymarket US states no fee's history."""
    m = record.get("market") or {}
    return {
        "question": m.get("title") or m.get("question"),
        "rules": m.get("description"),
        "resolution_source": None,  # named in the rules' prose only
        "close_time": ny_date(m.get("endDate")),
        "strike": strike(m),
        # The market's `windowEnd`, "End of the measurement window and scheduled settlement instant. Set on every
        # automated crypto family" (https://docs.polymarket.us/api-reference/markets/get-market-by-slug.md), would be the
        # date its question is based on; the gateway's market read does not serve it, on its crypto families' markets
        # (an hourly up-or-down among them) or any other, as read live.
        "strike_date": None,
        "family": family(record),
        "fees": fees(m),
    }


def paid_on(record: dict) -> bool:
    """Whether Polymarket US pays anything on a position in this market: it pays nothing on any."""
    return False


def label(record: dict) -> str | None:
    """The market's own name among its family's markets, which the family's title completes."""
    return (record.get("market") or {}).get("title") or None


def event(record: dict) -> dict | None:
    """The event a market belongs to: its slug and title, the title alone where the search did not find the event."""
    found = family(record)
    return {"event": found["id"].split(":", 2)[2] if found["id"] else None, "title": found["title"]} if found else None


def last_trade(payload: dict, record: dict) -> dict | None:
    """The market's last trade, its YES price and its time, as its book's stats carry it; None for a market that has
    not traded."""
    stats = ((payload or {}).get("marketData") or {}).get("stats") or {}
    price, when = _amount(stats.get("lastTradePx")), stats.get("lastTradeSetTime")
    if price is None or not when:
        return None
    moment = su.parse_ts(when)
    return {"price": price, "time": datetime.fromtimestamp(moment / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if moment else when}


def is_open(record: dict, asof: str | None, past: bool) -> bool | None:
    """Whether the market trades on the as-of date: its status on a record read that day; for a past as-of, whether
    it had started by then and had not yet ended."""
    m = record.get("market") or {}
    if not past:
        return m["status"] in OPEN if m.get("status") else None
    if asof is None:
        return None
    started, ended = ny_date(m.get("startDate")), ny_date(m.get("endDate"))
    return (started is None or started <= asof) and (ended is None or ended >= asof)


def settlement(market: dict) -> dict:
    """What a market read now says of YES: settled once resolved; its result 1 or 0 where YES settled there."""
    m = market.get("market") if isinstance(market.get("market"), dict) else market
    if m.get("status") not in FINAL:
        return {"settled": False, "result": None}
    price = number(market.get("settlement"))
    return {"settled": True, "result": int(price) if price in (0.0, 1.0) else None, "at": m.get("updatedAt")}


def levels(payload: dict) -> dict:
    """YES bids and asks, best first, as Synpath reads the book."""
    book = su.normalize_order_book(payload or {}, slug="", side="yes")
    return {"bids": [(b.price, b.size) for b in book.bids], "asks": [(a.price, a.size) for a in book.asks]}


def daily(payload: dict) -> dict[str, float]:
    """The daily price: the midpoint of each day's sample of the best bid and ask, dated by the New York day the
    sample closes."""
    out: dict[str, float] = {}
    for p in sorted((payload or {}).get("history") or [], key=lambda p: p.get("timestamp") or 0):
        ask, short = number(p.get("longPrice")), number(p.get("shortPrice"))
        if ask is None or short is None or not p.get("timestamp"):
            continue
        out[datetime.fromtimestamp(p["timestamp"] - 1, NY).date().isoformat()] = ((1.0 - short) + ask) / 2
    return out


def daily_quotes(payload: dict) -> dict[str, dict]:
    """Each day's sample of the best bid and ask: the YES ask its `longPrice`, the YES bid one less its `shortPrice`
    (the NO price, "derived from one minus the best bid"), dated by the New York day the sample closes, with its
    moment."""
    out: dict[str, dict] = {}
    for p in sorted((payload or {}).get("history") or [], key=lambda p: p.get("timestamp") or 0):
        ask, short = number(p.get("longPrice")), number(p.get("shortPrice"))
        if not p.get("timestamp") or (ask is None and short is None):
            continue
        out[datetime.fromtimestamp(p["timestamp"] - 1, NY).date().isoformat()] = {
            "bid": None if short is None else 1.0 - short, "ask": ask,
            "at": datetime.fromtimestamp(p["timestamp"], timezone.utc).isoformat(timespec="seconds")}
    return out


def _event_kept(e: dict) -> dict:
    return {k: e.get(k) for k in EVENT_FIELDS}


def record_of(market: Market, found: Event) -> dict:
    """A market met in a search, as its record: the venue's own payloads, as Synpath carries them."""
    return {"market": market.info, "event": _event_kept(found.info)}


def records_of(got: dict) -> list[tuple[str, dict]]:
    """Each market of a family read whole, by its id, as its record."""
    event = _event_kept(got.get("event") or {})
    return [(f"market:{VENUE}:{m['slug']}", {"market": m, "event": event})
            for m in got.get("markets") or [] if isinstance(m, dict) and m.get("slug")]


def family_of(got: dict) -> dict:
    """A family's own terms: its title, and its close date, its markets' latest."""
    e = got.get("event") or {}
    closes = [m.get("endDate") for m in got.get("markets") or [] if isinstance(m, dict) and m.get("endDate")]
    return {"title": e.get("title"), "closes": ny_date(max(closes, key=su.parse_ts)) if closes else ny_date(e.get("endDate"))}


def family_close(found: Event) -> str | None:
    """A family met in a search, its close date: its markets' latest, else its event's end date."""
    closes = [m.close_timestamp for m in found.markets if m.close_timestamp]
    return ny_date(max(closes)) if closes else ny_date(found.close_datetime)


# ---------------------------------------------------------------- the adapter


class PolymarketUS:
    """Polymarket US, read through Synpath's client and its search: a market's record with its event, books a market
    to a request with their last trades, a market's daily history, its settlement, a family read whole, and the venue's
    own search."""

    venue = VENUE

    def __init__(self, *, client: Any = None, limiter: RateLimiter | None = su.LIMITER, timeout: float = 30.0):
        self.http = HttpClient(su.GATEWAY_URL, limiter=limiter, timeout=timeout, client=client, venue=su.VENUE)

    def _get(self, what: str, path: str, params: Any = None) -> Any:
        return read(f"Polymarket US {what}", self.http.get, path, params)

    def find(self, text: str, *, past: bool, limit: int = FAMILIES) -> list[Event]:
        """The families Polymarket US's own search finds for the text, most relevant first, each with its markets:
        those open now, or for a past as-of every status."""
        return list(read("Polymarket US search", su.search_events, self.http, text, limit=limit,
                         status="all" if past else "open"))

    def _event_of(self, m: dict) -> dict:
        """The event that lists the market: the one the venue's search finds for the market's question, its event's
        title, among whose markets the market's slug is; {} where the search finds none."""
        for page in (1, 2):
            body = self._get("search", "/search", {"query": m.get("question") or m.get("title"), "limit": SEARCH_PAGE,
                                                  "page": page})
            for e in body.get("events") or []:
                if any(isinstance(x, dict) and x.get("slug") == m.get("slug") for x in e.get("markets") or []):
                    return e
            if len(body.get("events") or []) < SEARCH_PAGE:
                break
        return {}

    def market(self, slug: str) -> dict:
        """The record: the market and its event, and once it has resolved, its settlement price, which the market's own
        record does not carry (`settlement`)."""
        m = (self._get(f"market {slug}", f"/market/slug/{slug}") or {}).get("market")
        if not m:
            raise LookupError(f"Polymarket US lists no market {slug}")
        e = self._event_of(m)
        paths = [f"/market/slug/{slug}", "/search"]
        price = None
        if m.get("status") in FINAL:
            paths.append(f"/markets/{slug}/settlement")
            price = (self._get(f"settlement of {slug}", f"/markets/{slug}/settlement") or {}).get("settlement")
        return {"payload": {"market": m, **({"event": _event_kept(e)} if e else {}),
                            **({"settlement": price} if price is not None else {})},
                "request": {"method": "GET", "base": self.http.base_url, "paths": paths}, "retrieved_at": now()}

    def family(self, slug: str) -> dict:
        """An event read whole, with every market in it."""
        body = (self._get(f"event {slug}", f"/events/slug/{slug}") or {}).get("event")
        if not isinstance(body, dict) or not body.get("slug"):
            raise LookupError(f"Polymarket US lists no event {slug}")
        return {"event": {k: v for k, v in body.items() if k != "markets"},
                "markets": [m for m in body.get("markets") or [] if isinstance(m, dict)]}

    def _series(self, slug: str) -> dict:
        found = [s for s in (self._get(f"series {slug}", "/series", {"slug": slug}) or {}).get("series") or []
                 if isinstance(s, dict) and s.get("slug") == slug]
        if not found:
            raise LookupError(f"Polymarket US lists no series {slug}")
        return found[0]

    def _events(self, series_id: Any, *, past: bool) -> list[dict]:
        out: list[dict] = []
        params: dict[str, Any] = {"seriesId": series_id, "limit": 100, "offset": 0, **({} if past else {"closed": "false"})}
        while True:
            got = [e for e in (self._get("events", "/events", params) or {}).get("events") or [] if isinstance(e, dict)]
            out += got
            if len(got) < params["limit"]:
                return out
            params = {**params, "offset": params["offset"] + params["limit"]}

    def holding(self, slug: str) -> dict:
        """The series' title, and how many open events it holds."""
        found = self._series(slug)
        return {"title": found.get("title"), "open_families": len(self._events(found["id"], past=False))}

    def series(self, slug: str, *, past: bool) -> dict:
        """A series read whole: its events (those open now, or for a past as-of every one), each with its markets."""
        found = self._series(slug)
        families = [{"event": {k: v for k, v in e.items() if k != "markets"},
                     "markets": [m for m in e.get("markets") or [] if isinstance(m, dict)]}
                    for e in self._events(found["id"], past=past)]
        return {"series": found, "title": found.get("title"), "families": families}

    def books(self, records: dict[str, dict], *, last: bool = True) -> dict[str, dict]:
        """Each market's book with its last trade, by market id, a market to a request."""
        out: dict[str, dict] = {}
        for mid, r in records.items():
            slug = (r.get("market") or {}).get("slug")
            if slug:
                out[mid] = self._get(f"book of {slug}", f"/markets/{slug}/book")
        return out

    def book(self, record: dict) -> dict:
        slug = (record.get("market") or {}).get("slug")
        found = self.books({slug: record}).get(slug)
        if found is None:
            raise LookupError(f"Polymarket US gave no book for {slug}")
        return {"payload": found, "request": {"method": "GET", "base": self.http.base_url,
                                              "path": f"/markets/{slug}/book"}, "retrieved_at": now()}

    def history(self, record: dict) -> dict:
        """The market's daily samples of its best bid and ask, from its start."""
        slug = (record.get("market") or {})["slug"]
        params = {"symbol": slug, "fixedInterval": "INTERVAL_ALL", "fidelity": 1440}
        body = self._get(f"history of {slug}", "/price-history", params)
        return {"payload": {"history": (body or {}).get("history") or []},
                "request": {"method": "GET", "base": self.http.base_url, "path": "/price-history", "params": params},
                "retrieved_at": now()}

    def settlement(self, record: dict) -> dict:
        """Whether the market has settled, and how: the market read now, and its settlement once resolved."""
        slug = (record.get("market") or {})["slug"]
        try:
            m = (self._get(f"market {slug}", f"/market/slug/{slug}") or {}).get("market")
        except LookupError:
            m = None
        if not m:
            raise VenueError(f"Polymarket US gave no market {slug}")
        price = None
        if m.get("status") in FINAL:
            price = (self._get(f"settlement of {slug}", f"/markets/{slug}/settlement") or {}).get("settlement")
        found = {"market": m, "settlement": price}
        return {"payload": {"slug": slug, "status": m.get("status"), "settlement": price, **settlement(found)},
                "request": {"method": "GET", "base": self.http.base_url,
                            "paths": [f"/market/slug/{slug}", f"/markets/{slug}/settlement"]},
                "retrieved_at": now()}
