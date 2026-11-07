"""A market's fields: its terms, its price, bid, ask, and depth, and whether it is open.

A kept market's fields derive from its data rows: its record, fetched when it entered the store, and its book and
history, fetched on its first read. A search result gets the same computation over what the search fetched, and
nothing of it is kept. A market's terms are its question and rules, in prose; the code reads none of them.
close_time is the venue's own end date for the market, as each venue's `listing` reads it.

`bid` and `ask` are the best on each side of the YES book. `price` is the price the venue displays, by each venue's
own convention (each venue's `displayed`, with the documentation it rests on): on Polymarket the midpoint of the best
bid and ask, or the last trade where the spread is wider than ten cents. `depth` is the smaller side's cumulative cost
over the top TOP levels, the part of a book that is standing rather than fleeting. `open` is whether the market trades
on the as-of date. What a large order pays, walking its side of the book, is never the price: it is not what the
market believes.

For an as-of before the market was read, the price is the history price at the as-of, its bid and ask that price
less and plus WIDEN, and depth is unknown.
"""

from __future__ import annotations

from harness.derive.engine import Derivation, Inputs, Unavailable
from harness.derive.portfolio import asof_of, read_date
from harness.venues import FORMS, split

TOP = 5  # the standing part of a book
WIDEN = 0.01  # a cent, the spread of a liquid contract


# ---------------------------------------------------------------- the price


def depth(book: dict) -> float:
    """The smaller side's cost over the top TOP levels: YES up the asks costs price x size, NO against the YES bids
    costs (1 - price) x size."""
    return min(sum(p * s for p, s in book["asks"][:TOP]), sum((1.0 - p) * s for p, s in book["bids"][:TOP]))


def priced(venue: str, book: dict, payload: dict | None, record: dict | None) -> dict:
    """The best bid and ask, the price the venue displays with the rule that gave it, and the depth."""
    bid = book["bids"][0][0] if book["bids"] else None
    ask = book["asks"][0][0] if book["asks"] else None
    shown = FORMS[venue].displayed(bid, ask, payload or {}, record or {})
    return {"bid": bid, "ask": ask, "price": shown["price"], "rule": shown["rule"], "depth": depth(book)}


def past_price(prices: dict[str, float], asof: str) -> dict:
    """The history price at asof, its bid and ask a cent either side; depth is unknown."""
    days = sorted(d for d in prices if d <= asof)
    if not days:
        return {"bid": None, "ask": None, "price": None, "rule": "history", "depth": None, "past": True,
                "price_date": None}
    p = prices[days[-1]]
    return {"bid": max(0.0, p - WIDEN), "ask": min(1.0, p + WIDEN), "price": p, "rule": "history", "depth": None,
            "past": True, "price_date": days[-1]}


# ---------------------------------------------------------------- derivations on a kept market


def _record(ctx: Inputs, mid: str) -> dict:
    return ctx.data("market", mid)


def _listing_inputs(ctx: Inputs, mid: str) -> dict:
    return {"market": mid, "record": _record(ctx, mid)}


def listing_of(venue: str, record: dict) -> dict:
    """A record's listing: its question, rules, resolution source, close time, the venue's tags,
    categories, or series, and its YES token, each as the venue states it."""
    return FORMS[venue].listing(record)


def _listing_compute(m: dict, load) -> dict:
    return listing_of(split(m["market"])[0], load(m["record"]))


LISTING = Derivation("market_listing", 2, "Market", _listing_inputs, _listing_compute,
                     fields=("question", "rules", "resolution_source", "close_time", "tags", "categories", "series",
                             "yes_token"))


def _asof_or_none(ctx: Inputs) -> str | None:
    try:
        return asof_of(ctx)
    except Unavailable:
        return None


def is_open(venue: str, record: dict, asof: str | None, past: bool) -> bool | None:
    """Whether the market trades on the as-of date: the venue's own word on a record read that day, and for a past
    as-of whether the market had not yet closed then."""
    return FORMS[venue].is_open(record, asof, past)


def _price_inputs(ctx: Inputs, mid: str) -> dict:
    record = _record(ctx, mid)
    asof = _asof_or_none(ctx)
    if asof is not None and asof < read_date(record["retrieved_at"]):
        return {"market": mid, "asof": asof, "past": True, "record": record, "history": ctx.data("history", mid)}
    return {"market": mid, "asof": asof, "past": False, "record": record, "book": ctx.data("book", mid)}


def _price_compute(m: dict, load) -> dict:
    venue = split(m["market"])[0]
    forms = FORMS[venue]
    record = load(m["record"])
    opened = is_open(venue, record, m["asof"], m["past"])
    if m["past"]:
        return {**past_price(forms.daily(load(m["history"])), m["asof"]), "book": None, "open": opened}
    payload = load(m["book"])
    book = forms.levels(payload)
    return {**priced(venue, book, payload, record), "book": {"bids": book["bids"], "asks": book["asks"]},
            "open": opened}


PRICE = Derivation("market_price", 1, "Market", _price_inputs, _price_compute,
                   fields=("book", "bid", "ask", "price", "depth", "open"))


def _history_inputs(ctx: Inputs, mid: str) -> dict:
    return {"market": mid, "history": ctx.data("history", mid)}


def _history_compute(m: dict, load) -> dict:
    return {"history": FORMS[split(m["market"])[0]].daily(load(m["history"]))}


HISTORY = Derivation("market_history", 1, "Market", _history_inputs, _history_compute, fields=("history",))


DERIVATIONS = (LISTING, PRICE, HISTORY)
