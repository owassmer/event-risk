"""Kalshi, read through our copy of Synpath's Kalshi read paths (harness.venues.synpath.kalshi, from 0.11.0), with what
the store needs besides. No read needs a
key. Each fact below was checked against a live answer.

How Kalshi lists: a series is a recurring family (the Fed's decision), an event one occurrence of it (the December
meeting), and its markets that event's outcomes or strikes, each named by its ticker (`KXFED-26DEC-T4.25`, its event
`KXFED-26DEC`, its series `KXFED`). A family is an event, `family:kalshi:{event_ticker}`, or the series that holds
events, `family:kalshi:series:{series_ticker}`: a ladder of deadlines can be one event a year within a series
(KXRECSSNBER-26, KXRECSSNBER-27), and an event and its series can share a ticker (KXNBERRECESSQ). An event names its
series (`series_ticker`); a search's hit names the series by title (`series_title`), which our copy of Synpath's search
keeps on the event. A series' events are `GET /events?series_ticker=&status=open&limit=200` (paged by `cursor`;
https://docs.kalshi.com/api-reference/events/get-events), with `with_nested_markets=true` to read them whole.

A family is titled by its event's `title`. Its `sub_title`, the label on the event's card on kalshi.com, is not read:
it repeats the title's scope, tags a category or a source, or gives a date, often the family's furthest deadline,
while each market states what it carries more precisely, in its own name in the family, its close, and its rules.

Search is the endpoint kalshi.com's own search box calls, `GET api.elections.kalshi.com/v1/search/series?query=`
(undocumented: Synpath checks its answer's shape before trusting it, and raises rather than guessing), most relevant
first, settled events among them; the events it names are then read whole through the documented
`GET /events?tickers=…&with_nested_markets=true`, a hundred to a request and answered in Kalshi's own order (Synpath
re-indexes them into the search's), and the markets of an event settled before Kalshi's historical cutoff
(`GET /historical/cutoff`) from `GET /historical/markets`. Synpath's reads keep to five a second, the rate it measured
Kalshi to allow, and its search's to two.

A market's record is its market (`GET /markets/{ticker}`, else `/historical/markets/{ticker}`), its event
(`GET /events/{event_ticker}`), and its series (`GET /series/{series_ticker}`). Its question is `title`; its rules
`rules_primary` and `rules_secondary`; its close `close_time`; a strike market states its strike as data,
`strike_type` (`greater`, `greater_or_equal`, `less`, `less_or_equal`, `between`, `custom`, `functional`,
`structured`) with `floor_strike`, `cap_strike`, `custom_strike`, or `functional_strike`. Every market Kalshi lists is
`binary` (its `market_type`; read across every open event, 12,000 settled ones, and the archive's
latest thousand markets), so the listing carries no market type. A market names no source of its own: its event's
`settlement_sources`, or else its series', are "the official sources used for the determination of markets within"
them (https://docs.kalshi.com/api-reference/events/get-event,
https://docs.kalshi.com/api-reference/market/get-series-list), and they are its `resolution_source`, as Synpath carries
a venue's settlement sources (a market inherits its event's).

Fees (https://kalshi.com/docs/kalshi-fee-schedule.pdf) are a series' `fee_type` and `fee_multiplier`, as Synpath reads
them, with an event's override layered on top: "Event fees are an override layered on top of the parent series' fee
structure" (https://docs.kalshi.com/api-reference/events/get-event-fee-changes). Two lists say when each changed, read
with the market's record and with a family read whole: `GET /series/fee_changes?series_ticker=&show_historical=true`
(https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes), `{series_fee_change_arr: [{id, series_ticker,
fee_type, fee_multiplier, scheduled_ts}]}`, and `GET /events/fee_changes?event_ticker=&show_historical=true`, `{cursor,
event_fee_changes: [{id, event_ticker, series_ticker, fee_type_override, fee_multiplier_override, scheduled_ts}]}`, an
override of null clearing the one before it. Read live on 2026-11-11: 107 series changes, from 2025-10-04 to
2026-09-03, and no event's override, current or past. The lists are kept with when they were read (`read_at`). The fee
in effect at a moment (`fees`): the series' record states its fee when it was read, so it is the fee at any moment no
change falls between; otherwise the series' latest change on or before the moment, and none stated where its only
changes come after it; with the event's latest override on or before the moment over it. A record read today is read
at the moment it was read; at a past as-of, at the end of that day in New York.

Books: `GET /markets/orderbooks?tickers=…&tickers=…`, the key repeated (comma-joined tickers are refused), a hundred a
request
(https://docs.kalshi.com/api-reference/market/get-market-orderbooks), each `{ticker, orderbook_fp: {yes_dollars,
no_dollars}}`: bids only, each level `[price, count]` as strings; a NO bid at q is a YES ask at 1 - q. Synpath reads a
book a market at a time, saying Kalshi has no batch endpoint; Kalshi documents this one. A book is read with the
market's latest trade, `GET /markets/trades?ticker=&limit=1` (https://docs.kalshi.com/api-reference/market/get-trades),
`{trades: [{created_time, yes_price_dollars, count_fp, taker_side}]}`, newest first; a market's own
`last_price_dollars` carries no time.

What Kalshi pays on a position (PAID; harness.venues.paid) is stated on its help page "APY on Kalshi"
(https://help.kalshi.com/en/articles/13823847-apy-on-kalshi): under the heading "Prediction Market
Interest", the line "Rate: 3.50% APY. This rate is variable and can change at any time", accruing daily on cash and on
open positions "based on last traded prices" and paid monthly, to a member whose average balance over the month is at
least $250. The perps' rate stands on the same page under its own heading, "Perpetuals (Perps) Interest", in a line of
the same form. It is paid across the venue, on every market.

History: `GET /series/{series}/markets/{ticker}/candlesticks?start_ts=&end_ts=&period_interval=1440`, or
`/historical/markets/{ticker}/candlesticks` once the market settled before the cutoff
(https://docs.kalshi.com/api-reference/market/get-market-candlesticks): each candle `{end_period_ts, yes_bid:
{close_dollars}, yes_ask: {close_dollars}, price, volume_fp}` (`close` in the historical shape). A daily candle ends at
00:00 New York, and the daily price is the mid of its closing YES bid and ask.

Settlement: a market's `status` runs through `determined`, `disputed`, `amended`, to `finalized`, where the result
stands (https://docs.kalshi.com/api-reference/market/get-market); `result` is "yes", "no", "scalar", or empty. Synpath
reads `determined` as settled; the record waits for `finalized`, reading the venue's own status and result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from harness.venues.synpath import kalshi as sk
from harness.venues.synpath.base import HttpClient, RateLimiter
from harness.venues.synpath.types import Event, Market

from harness.venues.http import VenueError, now
from harness.venues.markets import FAMILIES, NY, family_id, moment, ny_date, number, read

VENUE = "kalshi"
BOOKS_PER_CALL = 100
MARKETS_PER_CALL = 200
HISTORY_DAYS = 4999  # the most daily candles one request answers is 5,000
OPEN = ("active",)  # the lifecycle status of a market that trades
FINAL = ("finalized",)
SETTLEMENT_FIELDS = ("ticker", "status", "result", "settlement_value_dollars", "settlement_ts", "close_time")
# Kalshi's rate on positions: its page, its label, and how it pays it, an annual percentage yield (harness.venues.paid).
PAID = {"url": "https://help.kalshi.com/en/articles/13823847-apy-on-kalshi",
        "words": "Prediction Market Interest, Rate:",
        "label": r"Prediction Market Interest\s+Rate:\s*(?P<rate>\d+(?:\.\d+)?)%\s*APY", "paid": "annual"}
STRIKE = {"strike_type": "type", "floor_strike": "floor", "cap_strike": "cap", "custom_strike": "custom",
          "functional_strike": "functional"}  # Kalshi's strike fields, by the names a listing gives them


# ---------------------------------------------------------------- a market's terms, from its record


def named(found: list) -> str | None:
    """Settlement sources, `{name, url}` as Synpath carries them, as `name (url)` joined by semicolons."""
    names = [f"{str(s['name']).strip()} ({s['url']})" if s.get("url") and s["url"] != s.get("name") else str(s["name"]).strip()
             for s in found or [] if isinstance(s, dict) and str(s.get("name") or "").strip()]
    return "; ".join(names) or None


def sources(event: dict, series: dict) -> str | None:
    """Who decides a market's outcome: the sources Kalshi lists for its event, or else its series."""
    return named(event.get("settlement_sources")) or named(series.get("settlement_sources"))


def strike(m: dict) -> dict | None:
    """The strike a market states as data, by Kalshi's fields; None for a market that states none."""
    found = {name: m[field] for field, name in STRIKE.items() if m.get(field) not in (None, "", {})}
    return found if found.get("type") else None


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _latest(changes: list, moment: datetime) -> dict | None:
    """The latest change scheduled on or before the moment."""
    dated = [(_moment(c.get("scheduled_ts")), c) for c in changes or [] if isinstance(c, dict)]
    before = [(t, c) for t, c in dated if t is not None and t <= moment]
    return max(before, key=lambda tc: tc[0])[1] if before else None


# Kalshi's taker fee on C contracts at a price P is TAKER x C x P x (1 - P), times its series' (or its event's) fee
# multiplier, rounded up to the cent over the order (https://kalshi.com/docs/kalshi-fee-schedule.pdf, "fees = round
# up(0.07 x C x P x (1-P))"; its rounding over an order's fills, https://docs.kalshi.com/getting_started/fee_rounding).
# Read live on 2026-11-12: KXFED's, KXINXY's and KXHIGHNY's series state a multiplier of 1. A "flat" fee type states
# no formula here, so its fee is not stated.
TAKER = 0.07
QUADRATIC = ("quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees")


def fees(record: dict, at: str | None = None) -> dict | None:
    """The fee in effect on the market at a moment, ISO 8601, now where none is given: its series' type and multiplier,
    as Synpath reads them, rounded up to the cent, with its event's override over them where one is in effect (`override`
    names it). The series' fee is its record's where no change of it falls between the moment and when the record was
    read, else its latest change on or before the moment; None where Kalshi states it only from a later change."""
    series, changes = record.get("series") or {}, record.get("fee_changes") or {}
    moment = _moment(at) or datetime.now(timezone.utc)
    read = _moment(changes.get("read_at")) or moment
    stated = changes.get("series") or []
    between = [c for c in stated if isinstance(c, dict) and _moment(c.get("scheduled_ts")) is not None
               and min(moment, read) < _moment(c["scheduled_ts"]) <= max(moment, read)]
    if between:
        latest = _latest(stated, moment)
        base = {"fee_type": latest.get("fee_type"), "fee_multiplier": latest.get("fee_multiplier")} if latest else {}
    else:
        base = {"fee_type": series.get("fee_type"), "fee_multiplier": series.get("fee_multiplier")}
    override = _latest(changes.get("event") or [], moment) or {}
    fee_type = override.get("fee_type_override") or base.get("fee_type")
    multiplier = override.get("fee_multiplier_override")
    multiplier = base.get("fee_multiplier") if multiplier is None else multiplier
    if not fee_type:
        return None
    found = sk.normalize_series({**series, "fee_type": fee_type, "fee_multiplier": multiplier}).fee
    out = found.model_dump(exclude={"info", "venue", "scope_id"}, exclude_none=True)
    if fee_type in QUADRATIC:  # the taker's rate, in the one form every venue's fee is given in
        out["taker_rate"] = TAKER * (1.0 if multiplier is None else float(multiplier))
    overridden = override.get("fee_type_override") is not None or override.get("fee_multiplier_override") is not None
    return {**out, "override": "event"} if overridden else out


def family(record: dict) -> dict | None:
    """The family a market belongs to: its event's id and title."""
    m, e = record.get("market") or {}, record.get("event") or {}
    ticker = e.get("event_ticker") or m.get("event_ticker")
    if not ticker:
        return None
    above = within({**e, "series": record.get("series") or {}})
    return {"id": family_id(VENUE, ticker), "title": e.get("title"), **({"within": above} if above else {})}


def listing(record: dict, at: str | None = None) -> dict:
    """A market's terms, its fee the one in effect at `at` (fees). `strike_date` is the date its event is based on, as
    the event states it where it uses a date strike: "The specific date this event is based on. Only filled when the
    event uses a date strike (mutually exclusive with strike_period)" (https://docs.kalshi.com/api-reference/events/
    get-event.md). It is a moment, and can fall a day past the question's own words ("before Jan 1" carries Jan 1, 10:00
    ET). The other times a market states are not that date: `close_time` is when trading stops, set well past the event
    where it may be rescheduled; `expected_expiration_time` is when the outcome is expected to be known;
    `occurrence_datetime` is when the underlying event was recorded as happening (getting_started/market_lifecycle.md,
    api-reference/market/get-market.md). A period strike (`strike_period`, "Q1 2027") is in the question's words."""
    m, e, s = record.get("market") or {}, record.get("event") or {}, record.get("series") or {}
    return {
        "question": m.get("title") or e.get("title"),
        "rules": "\n\n".join(r for r in (m.get("rules_primary"), m.get("rules_secondary")) if r),
        "resolution_source": sources(e, s),
        "close_time": ny_date(m.get("close_time")),
        "strike": strike(m),
        "strike_date": moment(e.get("strike_date")),
        "family": family(record),
        "fees": fees(record, at) if s else None,
    }


def paid_on(record: dict) -> bool:
    """Whether Kalshi pays its rate on a position in this market: it pays it across the venue."""
    return True


def within(info: dict) -> dict | None:
    """The family an event belongs to: its series, by its ticker and title, where the event names one."""
    ticker = info.get("series_ticker")
    if not ticker:
        return None
    title = info.get("series_title") or (info.get("series") or {}).get("title")
    return {"id": family_id(VENUE, f"series:{ticker}"), **({"title": title} if title else {})}


def label(record: dict) -> str | None:
    """The market's own name among its family's markets, which the family's title completes."""
    return (record.get("market") or {}).get("yes_sub_title") or None


def event(record: dict) -> dict | None:
    """The event a market belongs to: its ticker and title."""
    found = family(record)
    return {"event": found["id"].split(":", 2)[2], "title": found.get("title")} if found else None


def last_trade(payload: dict, record: dict) -> dict | None:
    """The market's latest trade, its YES price and its time, as its book's reading holds it; None for a market that
    has not traded, or a reading that holds no trade."""
    trades = ((payload or {}).get("trade") or {}).get("trades") or []
    t = trades[0] if trades and isinstance(trades[0], dict) else {}
    price = number(t.get("yes_price_dollars"))
    return {"price": price, "time": t.get("created_time")} if price is not None and t.get("created_time") else None


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


def settlement(market: dict) -> dict:
    """What a market read now says of YES: settled once finalized; its result 1 for "yes" and 0 for "no", and None for
    a scalar settlement or none."""
    m = market.get("market") if isinstance(market.get("market"), dict) else market
    if m.get("status") not in FINAL:
        return {"settled": False, "result": None}
    return {"settled": True, "result": {"yes": 1, "no": 0}.get(m.get("result")), "at": m.get("settlement_ts")}


def levels(payload: dict) -> dict:
    """YES bids and asks, best first, as Synpath reads the shared book: the asks the NO bids reflected."""
    book = sk.normalize_order_book(payload or {}, ticker="", side="yes")
    return {"bids": [(b.price, b.size) for b in book.bids], "asks": [(a.price, a.size) for a in book.asks]}


def daily(payload: dict) -> dict[str, float]:
    """The daily price: the mid of each daily candle's closing YES bid and ask, dated by the New York day the candle
    ends."""
    out: dict[str, float] = {}
    for c in (payload or {}).get("candlesticks") or []:
        bid = (c.get("yes_bid") or {}).get("close_dollars", (c.get("yes_bid") or {}).get("close"))
        ask = (c.get("yes_ask") or {}).get("close_dollars", (c.get("yes_ask") or {}).get("close"))
        if bid is None or ask is None or not c.get("end_period_ts"):
            continue
        day = datetime.fromtimestamp(c["end_period_ts"] - 1, NY).date().isoformat()
        out[day] = (float(bid) + float(ask)) / 2
    return out


def daily_quotes(payload: dict) -> dict[str, dict]:
    """Each day's closing quotes, as its daily candle gives them: the YES bid and ask at the candle's close, a side
    with no quote none, dated by the New York day the candle ends, with the moment it ends."""
    out: dict[str, dict] = {}
    for c in (payload or {}).get("candlesticks") or []:
        bid = (c.get("yes_bid") or {}).get("close_dollars", (c.get("yes_bid") or {}).get("close"))
        ask = (c.get("yes_ask") or {}).get("close_dollars", (c.get("yes_ask") or {}).get("close"))
        if not c.get("end_period_ts") or (bid is None and ask is None):
            continue
        out[datetime.fromtimestamp(c["end_period_ts"] - 1, NY).date().isoformat()] = {
            "bid": number(bid), "ask": number(ask),
            "at": datetime.fromtimestamp(c["end_period_ts"], timezone.utc).isoformat(timespec="seconds")}
    return out


def record_of(market: Market, family: Event) -> dict:
    """A market met in a search, as its record: the venue's own payloads, as Synpath carries them."""
    return {"market": market.info, "event": family.info}


def records_of(got: dict) -> list[tuple[str, dict]]:
    """Each market of a family read whole, by its id, as its record."""
    event, series = got.get("event") or {}, got.get("series") or {}
    changes = got.get("fee_changes") or {}
    return [(f"market:{VENUE}:{m['ticker']}", {"market": m, "event": event, "series": series, "fee_changes": changes})
            for m in got.get("markets") or [] if isinstance(m, dict) and m.get("ticker")]


def family_of(got: dict) -> dict:
    """A family's own terms: its title, and its close date, its markets' latest."""
    e = got.get("event") or {}
    closes = [m.get("close_time") for m in got.get("markets") or [] if isinstance(m, dict) and m.get("close_time")]
    return {"title": e.get("title"), "closes": ny_date(max(closes)) if closes else None}


def family_close(found: Event) -> str | None:
    """A family met in a search, its close date: its markets' latest, as Synpath reads it."""
    return ny_date(found.close_datetime)


# ---------------------------------------------------------------- the adapter


class Kalshi:
    """Kalshi, read through Synpath's client and its site search: a market's whole record, books a hundred to a request,
    a market's daily history, its settlement, a family read whole, and the venue's own search."""

    venue = VENUE

    def __init__(self, *, client: Any = None, limiter: RateLimiter | None = sk.LIMITER,
                 search_limiter: RateLimiter | None = sk.SEARCH_LIMITER, timeout: float = 30.0):
        self.http = HttpClient(sk.BASE_URL, limiter=limiter, timeout=timeout, client=client, venue=sk.VENUE)
        self.search = HttpClient(sk.SEARCH_URL, limiter=search_limiter, timeout=timeout, client=client, venue=sk.VENUE)

    def _get(self, what: str, path: str, params: Any = None) -> Any:
        return read(f"Kalshi {what}", self.http.get, path, params)

    def find(self, text: str, *, past: bool, limit: int = FAMILIES) -> list[Event]:
        """The families Kalshi's own search finds for the text, most relevant first, each with its markets: those
        open now, or for a past as-of every status."""
        return list(read("Kalshi search", sk.search_events, self.http, self.search, text, limit=limit,
                         status="all" if past else "open"))

    def _fee_changes(self, series_ticker: str | None, event_ticker: str | None) -> dict:
        """When the series' fees changed, and the event's overrides of them, past ones included."""
        out: dict[str, list] = {}
        if series_ticker:
            out["series"] = self._get("fee changes", "/series/fee_changes", {
                "series_ticker": series_ticker, "show_historical": "true"}).get("series_fee_change_arr") or []
        if event_ticker:
            out["event"] = self._get("fee overrides", "/events/fee_changes", {
                "event_ticker": event_ticker, "show_historical": "true", "limit": 1000}).get("event_fee_changes") or []
        return {**out, "read_at": now()}

    def market(self, ticker: str) -> dict:
        """The record: the market, its event, its series, and when the series' fees and the event's overrides changed."""
        urls = [f"/markets/{ticker}"]
        try:
            m = self._get(f"market {ticker}", f"/markets/{ticker}").get("market")
        except LookupError:
            urls.append(f"/historical/markets/{ticker}")
            m = self._get(f"market {ticker}", f"/historical/markets/{ticker}").get("market")
        if not m:
            raise LookupError(f"Kalshi lists no market {ticker}")
        event = series = {}
        if m.get("event_ticker"):
            urls.append(f"/events/{m['event_ticker']}")
            event = self._get("event", f"/events/{m['event_ticker']}").get("event") or {}
        if event.get("series_ticker"):
            urls.append(f"/series/{event['series_ticker']}")
            try:
                series = self._get("series", f"/series/{event['series_ticker']}").get("series") or {}
            except LookupError:
                series = {}
        event = {k: v for k, v in event.items() if k != "markets"}
        changes = self._fee_changes(event.get("series_ticker") if series else None, m.get("event_ticker"))
        urls += ["/series/fee_changes", "/events/fee_changes"]
        return {"payload": {"market": m, "event": event, "series": series, "fee_changes": changes},
                "request": {"method": "GET", "base": self.http.base_url, "paths": urls}, "retrieved_at": now()}

    def family(self, event_ticker: str) -> dict:
        """An event read whole: its markets, the markets of one settled before Kalshi's historical cutoff from
        `/historical/markets`, and its series, whose sources stand where the event lists none."""
        body = self._get(f"event {event_ticker}", f"/events/{event_ticker}", {"with_nested_markets": "true"})
        event = dict(body.get("event") or {})
        if not event:
            raise LookupError(f"Kalshi lists no event {event_ticker}")
        markets = event.pop("markets", None) or body.get("markets") or []
        if not markets:
            markets = self._get(f"event {event_ticker}", "/historical/markets",
                                {"event_ticker": event_ticker, "limit": 1000}).get("markets") or []
        series = {}
        if event.get("series_ticker"):
            try:
                series = self._get("series", f"/series/{event['series_ticker']}").get("series") or {}
            except LookupError:
                series = {}
        changes = self._fee_changes(event.get("series_ticker") if series else None, event_ticker)
        return {"event": event, "markets": markets, "series": series, "fee_changes": changes}

    def _events(self, ticker: str, *, past: bool, nested: bool) -> list[dict]:
        """A series' events: those open now, or for a past as-of every one, with their markets where `nested`."""
        params: dict[str, Any] = {"series_ticker": ticker, "limit": 200,
                                  **({} if past else {"status": "open"}),
                                  **({"with_nested_markets": "true"} if nested else {})}
        out: list[dict] = []
        while True:
            body = self._get(f"events of {ticker}", "/events", params)
            out += [e for e in body.get("events") or [] if isinstance(e, dict)]
            if not body.get("cursor"):
                return out
            params = {**params, "cursor": body["cursor"]}

    def holding(self, ticker: str) -> dict:
        """How many open events the series holds; its title is the search's, kept on each event (within)."""
        return {"open_families": len(self._events(ticker, past=False, nested=False))}

    def series(self, ticker: str, *, past: bool) -> dict:
        """A series read whole: its record, and its events (those open now, or for a past as-of every one), each with
        its markets and its series, as a family read whole is."""
        found = self._get(f"series {ticker}", f"/series/{ticker}").get("series")
        if not found:
            raise LookupError(f"Kalshi lists no series {ticker}")
        families = [{"event": {k: v for k, v in e.items() if k != "markets"}, "markets": e.get("markets") or [],
                     "series": found} for e in self._events(ticker, past=past, nested=True)]
        return {"series": found, "title": found.get("title"), "families": families}

    def books(self, records: dict[str, dict], *, last: bool = True) -> dict[str, dict]:
        """Each market's book, by market id, a hundred to a request; with `last`, each with the market's latest trade
        (a search's rows read the books alone)."""
        tickers = {(r.get("market") or {}).get("ticker"): mid for mid, r in records.items()}
        tickers.pop(None, None)
        names = list(tickers)
        out: dict[str, dict] = {}
        for i in range(0, len(names), BOOKS_PER_CALL):
            chunk = names[i:i + BOOKS_PER_CALL]
            books = self._get("books", "/markets/orderbooks", [("tickers", t) for t in chunk]).get("orderbooks") or []
            for b in books:
                t = b.get("ticker")
                if t in tickers:
                    out[tickers[t]] = {"orderbook_fp": b.get("orderbook_fp") or {}}
            for t in chunk:  # a market the answer leaves out has no orders standing
                out.setdefault(tickers[t], {"orderbook_fp": {}})
        if last:
            for t, mid in tickers.items():
                if mid in out:
                    out[mid]["trade"] = self._get(f"trades of {t}", "/markets/trades", {"ticker": t, "limit": 1})
        return out

    def book(self, record: dict) -> dict:
        ticker = (record.get("market") or {}).get("ticker")
        found = self.books({ticker: record}).get(ticker)
        if found is None:
            raise LookupError(f"Kalshi gave no book for {ticker}")
        return {"payload": found, "request": {"method": "GET", "base": self.http.base_url,
                                              "paths": ["/markets/orderbooks", "/markets/trades"], "tickers": [ticker]},
                "retrieved_at": now()}

    def history(self, record: dict) -> dict:
        """The market's daily candles from its open to now, from the historical endpoint once it settled before the
        cutoff."""
        m = record.get("market") or {}
        ticker = m["ticker"]
        series = (record.get("event") or {}).get("series_ticker") or ticker.split("-")[0]
        opened = m.get("open_time") or m.get("created_time")
        end = int(datetime.now(timezone.utc).timestamp())
        start = int(datetime.fromisoformat(opened.replace("Z", "+00:00")).timestamp()) if opened else end - 400 * 86400
        start = max(start, end - HISTORY_DAYS * 86400)
        window = {"start_ts": start, "end_ts": end, "period_interval": 1440}
        path = f"/series/{series}/markets/{ticker}/candlesticks"
        try:
            body = self._get(f"history of {ticker}", path, window)
        except LookupError:
            path = f"/historical/markets/{ticker}/candlesticks"
            body = self._get(f"history of {ticker}", path, window)
        return {"payload": body, "request": {"method": "GET", "base": self.http.base_url, "path": path, **window},
                "retrieved_at": now()}

    def settlement(self, record: dict) -> dict:
        """Whether the market has settled, and how: the market read now, its own status and result."""
        ticker = (record.get("market") or {})["ticker"]
        try:
            m = self._get(f"market {ticker}", f"/markets/{ticker}").get("market")
        except LookupError:
            m = self._get(f"market {ticker}", f"/historical/markets/{ticker}").get("market")
        if not m:
            raise VenueError(f"Kalshi gave no market {ticker}")
        return {"payload": {**{k: m.get(k) for k in SETTLEMENT_FIELDS}, **settlement(m)},
                "request": {"method": "GET", "base": self.http.base_url, "path": f"/markets/{ticker}"},
                "retrieved_at": now()}
