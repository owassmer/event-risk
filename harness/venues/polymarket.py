"""Polymarket, read through our copy of Synpath's Polymarket read paths (harness.venues.synpath.polymarket, from
0.11.0), with what the store needs besides. No read
needs a key. Each fact below was checked against a live answer.

How Polymarket lists: an event (Gamma's `/events/{id}`) groups one or more markets, each a binary question named by
its Gamma `id`, with its `groupItemTitle` its name among its event's markets. A family is an event,
`family:polymarket:{event id}`, or the series that holds events, `family:polymarket:series:{series id}`. A ladder can
be one event, or one event per rung within a series ("by end of 2026", "by June 30, 2027"), as Polymarket lists it.
An event names its series (`series`, a list of `{id, title, ...}`); a series (`GET /series/{id}`, read live: `{id,
title, slug, recurrence, events: [{id, title, closed, active, ...}], ...}`) names its events, which are read whole by id
as a search's are.

Search is Polymarket's own two rankings, the documented one's exact matches first. `GET /public-search?q=&page=
&limit_per_type=[&events_status=active]` (https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles)
answers strict matches only, for most texts a handful of events; the site's own search box calls `GET /search-v2?q=
&page=&limit_per_type=&type=events[&events_status=active]`, read from its JavaScript bundle and undocumented, as
Kalshi's site search is, which ranks some hundred events a text, related ones among them, and can rank a strict
match below its first ten: measured on 25 texts, it left 41 of the documented search's 123 events out of its first ten
("housing prices" lost all ten, "Japan yen" its two). Both answer `{"events": [...], "pagination": {"hasMore",
"totalResults"}}`, most relevant first, at most fifty a page whatever `limit_per_type` asks; `events_status=active`
for events open now, and without it closed and resolved events too. So the families a search returns are
`/public-search`'s, in its order, then `/search-v2`'s others, in its order, up to the limit; the two are asked side by
side. `/search-v2`'s events are slim (`id`, `title`, `slug`, dates, and markets with their question and quotes, no
token ids, rules or fees), so every event ranked is read whole after them, in one request, `GET
/events?id=&id=...&limit=`, which answers each in full, closed ones among them, in an order of its own.

A market's record is the list form `GET /markets?id={id}&closed=any`, whose market carries `events`, the events it
belongs to, of which the guide reads the first ("Read the event ID from the market's `events` array",
https://docs.polymarket.com/market-data/market-details); the list form answers open markets only unless `closed=any`
(https://docs.polymarket.com/market-data/discover-markets). Synpath reads a single market through `GET /markets/{id}`,
which carries no event. Its tags come from `GET /markets/{id}/tags`. Its question is `question`, its rules
`description`; who decides it is its `resolutionSource`, else its event's, as Synpath carries a venue's settlement
sources; its close is `endDate`, else its event's. Its YES token is the `clobTokenIds` entry at the index whose
`outcomes` entry is "Yes" (both JSON-encoded strings); a market whose outcomes are not Yes and No has no YES side here.
Fees are the market's `feesEnabled` and `feeSchedule` (https://docs.polymarket.com/trading/fees), as Synpath reads them;
a market with `feesEnabled` false charges none.

What Polymarket pays on a position (PAID; harness.venues.paid) is its Holding Reward, stated in its documentation
(https://docs.polymarket.com/concepts/positions-tokens.md): "Polymarket pays a 4.00% annualized
Holding Reward based on your total position value in eligible markets. Your total position value is randomly sampled
once each hour, and the reward is distributed daily. The rate is variable". A market is eligible where its record says
`holdingRewardsEnabled`.

Books: `POST https://clob.polymarket.com/books` with `[{"token_id"}]`, up to 500 a request, each `{asset_id, bids,
asks, last_trade_price, timestamp, hash, ...}`, levels `{price, size}` as decimal strings, worst first, which Synpath
sorts best first. A token with no orders standing has no book, and the batch leaves it out (a closed market's among
them): its book is read as one with no levels, no quotes on either side. A book's `last_trade_price` is not the token's
own last trade: read against the trade record, it is the market's latest match on either token, in some books its
YES price and in others its NO price (https://docs.polymarket.com/market-data/prices-order-books). A book is read with
the market's latest trade from its trade record, `GET https://data-api.polymarket.com/v2/trades?condition=&limit=1`
(https://data-api.polymarket.com/v2/openapi.json; https://docs.polymarket.com/migrate/data-api-v1-to-v2), `{data:
[{token_id, outcome, side, price, size, timestamp}]}`, newest first, every match on either token: one on the NO token
at q is a trade on the market at 1 - q in YES terms, YES and NO together paying one dollar.

History: `GET https://data-api.polymarket.com/v2/prices-history?token_id=&interval=max&bucket_seconds=10800`
(https://docs.polymarket.com/api-reference/markets/get-a-tokens-price-history), no key, `{data: [{timestamp, price,
resolution_seconds}], pagination: {next_cursor}}`, oldest first, epoch seconds. The 3-hour and 12-hour grains are
kept back to 2022 and the finer ones for weeks; a market's whole life at 3 hours is one page. Read live (Nov 12,
2026), the 3-hour series holds a point every 3 hours from July 2026, and before that only the points at 00:00 and
12:00 UTC, on open and settled markets alike. The docs say nothing
of what `price` is. Read live (Nov 12, 2026) it is the token's book midpoint, the mean of its best bid and ask, at the
point's `timestamp`: the series' last point, an exact tick, equals the book's midpoint read beside it, on spreads of
1 cent and of 94 cents, where the midpoint and the last trade differ; each 3-hour and 12-hour point equals the
1-minute point at its bucket's start; and none follows the trades. So the history holds no bid or ask. The daily
price is the last point inside each New York day, as Kalshi's and Polymarket US's are dated: the midpoint at 22:00 or
23:00 New York where the series holds a point then, at 19:00 or 20:00 on a day it holds only the 12-hour points, and
otherwise its latest point that day. Synpath reads the CLOB's `/prices-history` in pieces of fourteen days; this one reads a market's
whole life in one or a few pages.

Settlement (https://docs.polymarket.com/api-reference/markets/get-market-by-id): `closed`; the UMA oracle's
`umaResolutionStatus`, "resolved" once it has; and `outcomePrices`, each outcome's settled price: "1" and "0", or "0.5"
each where the market resolves 50/50.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from harness.venues.synpath import polymarket as sp
from harness.venues.synpath.base import HttpClient, RateLimiter
from harness.venues.synpath.types import Event, Market

from harness.venues.http import VenueError, now
from harness.venues.markets import FAMILIES, NY, family_id, ny_date, number, read

VENUE = "polymarket"
BOOKS_PER_CALL = 500
SEARCH = "/search-v2"  # the site's own search
EXACT = "/public-search"  # the documented search, strict matches
HISTORY = "/v2/prices-history"
GRAIN = 10800  # the finest grain the history keeps for a market's whole life, in seconds
TRADES = "/v2/trades"
SETTLEMENT_FIELDS = ("id", "closed", "umaResolutionStatus", "outcomes", "outcomePrices", "closedTime")
EVENT_FIELDS = ("id", "title", "slug", "category", "endDate", "resolutionSource")  # what a record keeps of its event


def _event_kept(e: dict) -> dict:
    """What a record keeps of its event: EVENT_FIELDS, and the series it belongs to by id and title."""
    series = [{"id": str(s["id"]), "title": s.get("title")} for s in e.get("series") or []
              if isinstance(s, dict) and s.get("id") is not None]
    return {**{k: e.get(k) for k in EVENT_FIELDS}, **({"series": series} if series else {})}


def within(info: dict) -> dict | None:
    """The family an event belongs to: its series, by its id and title, where the event names one (the first, where it
    names several)."""
    series = [s for s in info.get("series") or [] if isinstance(s, dict) and s.get("id") is not None]
    if not series:
        return None
    return {"id": family_id(VENUE, f"series:{series[0]['id']}"),
            **({"title": series[0]["title"]} if series[0].get("title") else {})}
# Polymarket's Holding Reward: its page, its label, and how it pays it, an annual rate paid daily on the value held.
PAID = {"url": "https://docs.polymarket.com/concepts/positions-tokens.md", "words": "annualized Holding Reward",
        "label": r"(?P<rate>\d+(?:\.\d+)?)%\s*annualized\s+Holding Reward", "paid": "daily"}


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        found = json.loads(value) if isinstance(value, str) else []
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def yes_token(record: dict) -> str | None:
    m = record.get("market") or {}
    tokens, outcomes = _list(m.get("clobTokenIds")), _list(m.get("outcomes"))
    if "Yes" in outcomes and len(tokens) == len(outcomes):
        return str(tokens[outcomes.index("Yes")])
    return None


# ---------------------------------------------------------------- a market's terms, from its record


def family(record: dict) -> dict | None:
    """The family a market belongs to: its event's id and title."""
    e = record.get("event") or {}
    if e.get("id") is None:
        return None
    above = within(e)
    return {"id": family_id(VENUE, str(e["id"])), "title": e.get("title"), **({"within": above} if above else {})}


def fees(m: dict) -> dict | None:
    """The market's fee schedule, as Synpath reads it; none where Polymarket charges none on the market
    (`feesEnabled` false), and None only for a record that does not say."""
    if m.get("feesEnabled") is False:
        return {"fee_type": "none", "taker_rate": 0.0}
    found = sp.fee_schedule_of(m)
    return found.model_dump(exclude={"info", "venue", "scope_id"}, exclude_none=True) if found else None


def listing(record: dict, at: str | None = None) -> dict:
    """A market's terms, as its record states them now: Polymarket states no fee's history."""
    m, e = record.get("market") or {}, record.get("event") or {}
    sources = sp.settlement_sources_of(m) or sp.settlement_sources_of(e)
    return {
        "question": m.get("question"),
        "rules": m.get("description"),
        "resolution_source": "; ".join(s["name"] for s in sources) or None,
        "close_time": ny_date(m.get("endDate") or e.get("endDate")),  # the market's endDate, else its event's
        "strike": None,  # Polymarket states no strike as data
        # nor the date a question is based on: its endDate is "when the market is eligible for resolution"
        # (https://docs.polymarket.com/concepts/resolution.md), its close
        "strike_date": None,
        "family": family(record),
        "fees": fees(m),
    }


def paid_on(record: dict) -> bool:
    """Whether Polymarket pays its Holding Reward on a position in this market: where its record says it does."""
    return (record.get("market") or {}).get("holdingRewardsEnabled") is True


def label(record: dict) -> str | None:
    """The market's own name among its family's markets, which the family's title completes."""
    return (record.get("market") or {}).get("groupItemTitle") or None


def event(record: dict) -> dict | None:
    """The event a market belongs to: its id and title."""
    e = record.get("event") or {}
    return {"event": str(e["id"]), "title": e.get("title")} if e.get("id") is not None else None


def last_trade(payload: dict, record: dict) -> dict | None:
    """The market's latest trade, in YES terms, with its time: a trade on the YES token at its price, one on the NO
    token at one minus its price; None for a market with no YES side, one that has not traded, or a reading that
    holds no trade."""
    rows = ((payload or {}).get("trade") or {}).get("data") or []
    t, token = (rows[0] if rows and isinstance(rows[0], dict) else {}), yes_token(record)
    price = number(t.get("price"))
    if token is None or price is None or not t.get("timestamp"):
        return None
    when = datetime.fromtimestamp(t["timestamp"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"price": price if str(t.get("token_id")) == token else round(1.0 - price, 6), "time": when}


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


def settlement(market: dict) -> dict:
    """What a market read now says of YES: settled once closed and resolved; its result 1 or 0 where YES's price
    settled there, and None where it settled anywhere else, as a 50/50 resolution does."""
    m = market.get("market") if isinstance(market.get("market"), dict) else market
    if not (m.get("closed") and m.get("umaResolutionStatus") == "resolved"):
        return {"settled": False, "result": None}
    outcomes, prices = _list(m.get("outcomes")), _list(m.get("outcomePrices"))
    if "Yes" not in outcomes or len(prices) != len(outcomes):
        return {"settled": True, "result": None}
    price = number(prices[outcomes.index("Yes")])
    if price is None:
        return {"settled": True, "result": None}
    return {"settled": True, "result": int(price) if price in (0.0, 1.0) else None, "at": m.get("closedTime")}


def levels(payload: dict) -> dict:
    """The YES token's book, best first on each side, as Synpath sorts the CLOB's levels."""
    payload = payload or {}
    book = sp.normalize_order_book(payload["book"] if isinstance(payload.get("book"), dict) else payload, side="yes")
    return {"bids": [(b.price, b.size) for b in book.bids], "asks": [(a.price, a.size) for a in book.asks]}


def daily(payload: dict) -> dict[str, float]:
    """The daily price: the last point inside each New York day, each point the token's midpoint at its timestamp."""
    out: dict[str, float] = {}
    for point in sorted((payload or {}).get("data") or [], key=lambda p: p["timestamp"]):
        out[datetime.fromtimestamp(point["timestamp"], NY).date().isoformat()] = float(point["price"])
    return out


def daily_quotes(payload: dict) -> dict[str, dict]:
    """None: the token's price history is its midpoint, with no bid or ask (the module's docstring)."""
    return {}


def record_of(market: Market, found: Event) -> dict:
    """A market met in a search, as its record: the venue's own payloads, as Synpath carries them."""
    return {"market": market.info, "event": _event_kept(found.info),
            "tags": [t for t in found.info.get("tags") or [] if isinstance(t, dict)]}


def records_of(got: dict) -> list[tuple[str, dict]]:
    """Each market of a family read whole, by its id, as its record."""
    e = got.get("event") or {}
    event, tags = _event_kept(e), [t for t in e.get("tags") or [] if isinstance(t, dict)]
    return [(f"market:{VENUE}:{m['id']}", {"market": m, "event": event, "tags": tags})
            for m in got.get("markets") or [] if isinstance(m, dict) and m.get("id") is not None]


def family_of(got: dict) -> dict:
    """A family's own terms: its title, and its close date, its markets' latest (an event's own end date can be
    earlier than its markets')."""
    e = got.get("event") or {}
    closes = [m.get("endDate") for m in got.get("markets") or [] if isinstance(m, dict) and m.get("endDate")]
    return {"title": e.get("title"), "closes": ny_date(max(closes, key=_moment)) if closes else ny_date(e.get("endDate"))}


def _moment(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def family_close(found: Event) -> str | None:
    """A family met in a search, its close date: its markets' latest, else its event's end date."""
    closes = [m.close_timestamp for m in found.markets if m.close_timestamp]
    return ny_date(max(closes)) if closes else ny_date(found.close_datetime)


# ---------------------------------------------------------------- the adapter


class Polymarket:
    """Polymarket, read through Synpath's client: the site's search with its events read whole, a market's record with
    its event, books five hundred to a request by token, a market's daily history, its settlement, and a family read
    whole."""

    venue = VENUE

    def __init__(self, *, client: Any = None, limiter: RateLimiter | None = sp.LIMITER, timeout: float = 30.0):
        self.gamma = HttpClient(sp.GAMMA_URL, limiter=limiter, timeout=timeout, client=client, venue=sp.VENUE)
        self.clob = HttpClient(sp.CLOB_URL, limiter=limiter, timeout=timeout, client=client, venue=sp.VENUE)
        self.data = HttpClient(sp.DATA_URL, limiter=limiter, timeout=timeout, client=client, venue=sp.VENUE)

    def find(self, text: str, *, past: bool, limit: int = FAMILIES) -> list[Event]:
        """The families Polymarket's searches find for the text, the documented search's exact matches first and then
        the site search's others, each read whole with its markets: those open now, or for a past as-of every status."""
        status = {} if past else {"events_status": "active"}
        asks = {EXACT: {"q": text, "page": 1, "limit_per_type": limit, **status},
                SEARCH: {"q": text, "page": 1, "limit_per_type": limit, "type": "events", **status}}
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {path: pool.submit(read, "Polymarket search", self.gamma.get, path, params)
                    for path, params in asks.items()}
            answers = [jobs[path].result() for path in (EXACT, SEARCH)]
        ranked = [str(e["id"]) for body in answers for e in (body or {}).get("events") or []
                  if isinstance(e, dict) and e.get("id")]
        ranked = list(dict.fromkeys(ranked))[:limit]
        if not ranked:
            return []
        whole = read("Polymarket events", self.gamma.get, "/events",
                     [("id", i) for i in ranked] + [("limit", len(ranked))])
        by_id = {str(e["id"]): e for e in whole or [] if isinstance(e, dict) and e.get("id") is not None}
        found = [sp.normalize_event(by_id[i]) for i in ranked if i in by_id]
        if not past:
            found = [e.model_copy(update={"markets": [m for m in e.markets if m.status == "open"]}) for e in found]
        return [e for e in found if e.markets]

    def _listed(self, market_id: str) -> dict:
        body = read(f"Polymarket market {market_id}", self.gamma.get, "/markets", {"id": market_id, "closed": "any"})
        found = next((m for m in body if isinstance(m, dict) and str(m.get("id")) == str(market_id)), None) \
            if isinstance(body, list) else None
        if found is None:
            raise LookupError(f"Polymarket lists no market {market_id}")
        return found

    def market(self, market_id: str) -> dict:
        """The record: the market, its event, and its tags."""
        m = self._listed(market_id)
        events = [e for e in m.pop("events", None) or [] if isinstance(e, dict)]
        try:
            tags = read("Polymarket tags", self.gamma.get, f"/markets/{market_id}/tags")
        except LookupError:
            tags = []
        return {"payload": {"market": m, **({"event": _event_kept(events[0])} if events else {}),
                            "tags": tags if isinstance(tags, list) else []},
                "request": {"method": "GET", "base": self.gamma.base_url,
                            "paths": [f"/markets?id={market_id}&closed=any", f"/markets/{market_id}/tags"]},
                "retrieved_at": now()}

    def family(self, event_id: str) -> dict:
        """An event read whole, with every market in it."""
        body = read(f"Polymarket event {event_id}", self.gamma.get, f"/events/{event_id}")
        if not isinstance(body, dict) or body.get("id") is None:
            raise LookupError(f"Polymarket lists no event {event_id}")
        markets = [m for m in body.get("markets") or [] if isinstance(m, dict)]
        return {"event": {k: v for k, v in body.items() if k != "markets"}, "markets": markets}

    def _series(self, series_id: str) -> dict:
        body = read(f"Polymarket series {series_id}", self.gamma.get, f"/series/{series_id}")
        if not isinstance(body, dict) or body.get("id") is None:
            raise LookupError(f"Polymarket lists no series {series_id}")
        return body

    def holding(self, series_id: str) -> dict:
        """The series' title, and how many open events it holds."""
        found = self._series(series_id)
        return {"title": found.get("title"), "open_families": sum(
            1 for e in found.get("events") or [] if isinstance(e, dict) and not e.get("closed"))}

    def series(self, series_id: str, *, past: bool) -> dict:
        """A series read whole: its events (those open now, or for a past as-of every one), each read whole by id with
        its markets, a hundred to a request."""
        found = self._series(series_id)
        ids = [str(e["id"]) for e in found.get("events") or [] if isinstance(e, dict) and e.get("id") is not None
               and (past or not e.get("closed"))]
        whole: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            got = read("Polymarket events", self.gamma.get, "/events", [("id", x) for x in chunk] + [("limit", len(chunk))])
            whole.update({str(e["id"]): e for e in got or [] if isinstance(e, dict) and e.get("id") is not None})
        families = [{"event": {k: v for k, v in whole[i].items() if k != "markets"},
                     "markets": [m for m in whole[i].get("markets") or [] if isinstance(m, dict)]}
                    for i in ids if i in whole]
        return {"series": {k: v for k, v in found.items() if k != "events"}, "title": found.get("title"),
                "families": families}

    def books(self, records: dict[str, dict], *, last: bool = True) -> dict[str, dict]:
        """Each market's YES book, by market id, five hundred to a request; with `last`, each with the market's
        latest trade from its trade record (a search's rows read the books alone). A market with no YES side has
        none; one the venue leaves out has a book with no levels."""
        tokens = {yes_token(r): mid for mid, r in records.items()}
        tokens.pop(None, None)
        names = list(tokens)
        out: dict[str, dict] = {}
        for i in range(0, len(names), BOOKS_PER_CALL):
            chunk = names[i:i + BOOKS_PER_CALL]
            for b in read("Polymarket books", self.clob.post, "/books", [{"token_id": t} for t in chunk]) or []:
                if isinstance(b, dict) and str(b.get("asset_id")) in tokens:
                    out[tokens[str(b["asset_id"])]] = {"book": b}
            for t in chunk:  # a token the batch leaves out has no orders standing
                out.setdefault(tokens[t], {"book": {"asset_id": t, "bids": [], "asks": []}})
        if last:
            for mid, found in out.items():
                condition = (records[mid].get("market") or {}).get("conditionId")
                if condition:
                    found["trade"] = read("Polymarket trades", self.data.get, TRADES, {"condition": condition, "limit": 1})
        return out

    def book(self, record: dict) -> dict:
        token = yes_token(record)
        if token is None:
            raise LookupError("this market has no outcome named Yes")
        mid = str((record.get("market") or {}).get("id"))
        found = self.books({mid: record}).get(mid)
        if found is None:
            raise LookupError(f"Polymarket gave no book for market {mid}")
        return {"payload": found, "request": {"method": "POST", "base": self.clob.base_url, "path": "/books",
                                              "body": [{"token_id": token}],
                                              "trade": {"base": self.data.base_url, "path": TRADES}},
                "retrieved_at": now()}

    def history(self, record: dict) -> dict:
        """The YES token's price history, the whole of it, a page at a time."""
        token = yes_token(record)
        if token is None:
            raise LookupError("this market has no outcome named Yes")
        params: dict[str, Any] = {"token_id": token, "interval": "max", "bucket_seconds": GRAIN}
        points: list = []
        while True:
            body = read("Polymarket history", self.data.get, HISTORY, params) or {}
            points += body.get("data") or []
            cursor = (body.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break
            params = {**params, "cursor": cursor}
        return {"payload": {"data": points}, "request": {"method": "GET", "base": self.data.base_url, "path": HISTORY,
                                                          "params": {"token_id": token, "interval": "max",
                                                                     "bucket_seconds": GRAIN}},
                "retrieved_at": now()}

    def settlement(self, record: dict) -> dict:
        """Whether the market has settled, and how: the market read now."""
        mid = str((record.get("market") or {}).get("id"))
        try:
            m = self._listed(mid)
        except LookupError:
            raise VenueError(f"Polymarket gave no market {mid}") from None
        return {"payload": {**{k: m.get(k) for k in SETTLEMENT_FIELDS}, **settlement(m)},
                "request": {"method": "GET", "base": self.gamma.base_url, "path": f"/markets?id={mid}&closed=any"},
                "retrieved_at": now()}
