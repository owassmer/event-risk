"""A market's fields: its terms, its price, bid, ask, and depth, and whether it is open.

A kept market's fields derive from its data rows: its record, fetched when it entered the store, and its book and
history, fetched on its first read. A search result gets the same computation over what the search fetched, and
nothing of it is kept. A market's terms are its question and rules, in prose; the code reads none of them.
close_time is the venue's own end date for the market, as each venue's `listing` reads it.

`bid` and `ask` are the best standing quotes on each side of the YES book; a side with none has no quote, and bounds
the market's interval only at its edge, 0 below and 1 above. `price` is their midpoint where both sides quote, and a
market without quotes on both sides has no price. `depth` is the smaller side's cumulative cost over the top TOP
levels: how much money stands at the quotes. `last_trade` is the market's latest trade, its YES price and its time,
read with its book: history, not a quote. `open` is whether the market trades on the as-of date. What a large order
pays, walking its side of the book, is never the price: it is not what the market believes.

For an as-of before the market was read, the price is the history price at the as-of, its bid and ask that price
less and plus WIDEN, and depth is unknown.

`risk_neutral` is the range of risk-neutral probabilities a market's price allows (MATH.md 1.2), in YES terms. Each side
of a contract is paid for in full until the close, and the venue pays on a position's marked value, so a position is a
security paying the venue's rate on its own price while money elsewhere earns the Treasury's. Its price then drifts at
the difference under the risk-neutral measure, and at the close it is the payoff: `q = pi * G_v / G_T`, where `G_T` is
what a dollar grows to at the Treasury's rate from the as-of to the close (one over the curve's discount factor,
harness.derive.rates) and `G_v` what it grows to at the venue's rate as the venue pays it (harness.venues.paid). With
`k = G_T / G_v - 1`, a price `q` allows from `1 - (1 - q) * (1 + k)` to `q * (1 + k)`, within 0 and 1; where the venue
pays more than the Treasury, `k` is below zero and the two ends change places, so the range is given low end first. It
rests on three things: the venue's rate as it states it at the as-of, held to the close, though each states its rate
as variable; the payment on the position's marked value, compounded as the venue says; and, on Kalshi, a member whose
average balance reaches the venue's line for being paid. Nothing reads it: it is a reference beside the price, which the
store takes as it stands. Where an input is missing it says what it waits on: a market with no price allows nothing.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from harness.derive import rates
from harness.derive.engine import Derivation, Inputs, Unavailable, Waiting
from harness.derive.portfolio import asof_of, read_date
from harness.venues import FORMS, NAMES, split
from harness.venues.markets import NY

TOP = 5  # the levels of a book its depth counts


# ---------------------------------------------------------------- the price


def depth(book: dict) -> float:
    """The smaller side's cost over the top TOP levels: YES up the asks costs price x size, NO against the YES bids
    costs (1 - price) x size."""
    return min(sum(p * s for p, s in book["asks"][:TOP]), sum((1.0 - p) * s for p, s in book["bids"][:TOP]))


def order_cost(book: dict | None, side: str, quantity: float, fees: dict | None) -> dict:
    """What buying `quantity` contracts of a market's `side` costs at its book (MATH.md 1.9, 3.3): its side walked from its
    best price, Yes up the asks and No against the Yes bids at one less each, every fill at its own price; and the fee
    a taker pays, as the venue states it for the market at the as-of. Each venue's client gives that fee in one form
    (harness.venues.kalshi, polymarket, polymarket_us): on C contracts at a price P, `taker_rate` x C x (P x (1 - P)) **
    `exponent` (1 where none is given), rounded up to the cent over the order where `rounding` is "up_to_cent", and at
    least `min_fee` where one is charged; a market that charges none has a rate of 0. `cost` includes the fees.

    Raises ValueError, in the book's words, where its side cannot fill the quantity; Unavailable where the fee in
    effect at the as-of is not stated, or where the book is not known level by level (at a past as-of, its day's closing
    quotes alone)."""
    if side not in ("yes", "no"):
        raise ValueError("a contract is bought on its yes side or its no side")
    if not book or not isinstance(book.get("asks"), list) or not isinstance(book.get("bids"), list):
        raise Unavailable("the market's book is not known level by level at the as-of")
    if not fees or not isinstance(fees.get("taker_rate"), (int, float)):
        raise Unavailable("the fee the market charges at the as-of is not stated")
    levels = sorted(book["asks"]) if side == "yes" else sorted((1.0 - p, n) for p, n in book["bids"])
    rate, power = float(fees["taker_rate"]), float(fees.get("exponent") or 1.0)
    left, cost, fee, fills = float(quantity), 0.0, 0.0, []
    for price, size in levels:
        take = min(left, float(size))
        if take <= 0:
            continue
        cost += take * price
        fee += rate * take * (price * (1.0 - price)) ** power
        fills.append({"price": price, "quantity": take})
        left -= take
        if left <= 1e-9:
            break
    if left > 1e-9:
        raise ValueError(f"the book offers {quantity - left:g} contracts on {side}; the order asks for {quantity:g}")
    if fees.get("rounding") == "up_to_cent":
        fee = math.ceil(round(fee * 100.0, 9)) / 100.0
    if fee and fees.get("min_fee"):
        fee = max(fee, float(fees["min_fee"]))
    return {"cost": cost + fee, "fees": fee, "fills": fills}


def priced(venue: str, book: dict, payload: dict | None, record: dict | None) -> dict:
    """The best bid and ask, the price where both sides quote, the depth, and the last trade."""
    bid = book["bids"][0][0] if book["bids"] else None
    ask = book["asks"][0][0] if book["asks"] else None
    price = (bid + ask) / 2 if bid is not None and ask is not None else None
    return {"bid": bid, "ask": ask, "price": price, "depth": depth(book),
            "last_trade": FORMS[venue].last_trade(payload or {}, record or {})}


def as_of_end(asof: str) -> datetime:
    """The end of the as-of day in New York."""
    return datetime.combine(date.fromisoformat(asof) + timedelta(days=1), datetime.min.time(), NY)


def past_price(venue: str, history: dict | None, asof: str, reading: tuple[str, dict] | None = None) -> dict:
    """A market as it stood at the end of a past as-of: of a reading of its book the store kept by then (`reading`, its
    moment and payload) and the day's closing quotes its venue's history gives, the later. A reading is its book whole,
    its depth with it; a day's closing quotes give its bid and ask, the price their midpoint, its depth unknown. Where
    the venue's history is its midpoint alone, the market has no quote, and so no price (3.3: the midpoint of its bid and
    ask): the day's price stands as its history gives it (`history_price`), and the market bounds nothing. `quoted_at`
    is the moment of what it rests on."""
    forms, end = FORMS[venue], as_of_end(asof)
    quotes = {d: q for d, q in forms.daily_quotes(history or {}).items() if d <= asof}
    day = max(quotes) if quotes else None
    closing = quotes[day] if day else None
    if reading and (closing is None or datetime.fromisoformat(reading[0]) > datetime.fromisoformat(closing["at"])):
        book = forms.levels(reading[1])
        return {**priced(venue, book, reading[1], None), "book": {"bids": book["bids"], "asks": book["asks"]},
                "past": True, "price_date": None, "quoted_at": reading[0], "from": "reading"}
    if closing:
        bid, ask = closing["bid"], closing["ask"]
        return {"bid": bid, "ask": ask, "price": (bid + ask) / 2 if bid is not None and ask is not None else None,
                "depth": None, "book": None, "past": True, "price_date": day, "quoted_at": closing["at"],
                "last_trade": None, "from": "history"}
    prices = {d: p for d, p in forms.daily(history or {}).items() if d <= asof}
    last = max(prices) if prices else None
    return {"bid": None, "ask": None, "price": None, "depth": None, "book": None, "past": True,
            "history_price": prices[last] if last else None, "price_date": last, "quoted_at": None,
            "last_trade": None, "from": "history" if last else None}


def price_of_reading(ctx: Inputs, mid: str, ref: dict) -> float | None:
    """The price one reading of a market's book gives: the midpoint of its best bid and ask, where both sides
    quote."""
    venue = split(mid)[0]
    payload = ctx.payload(ref)
    return priced(venue, FORMS[venue].levels(payload), payload, None)["price"]


# ---------------------------------------------------------------- derivations on a kept market


def _record(ctx: Inputs, mid: str) -> dict:
    return ctx.data("market", mid)


def terms_at(asof: str | None, retrieved_at: str) -> str:
    """The moment a market's terms are read at: the moment its record was read, or for an as-of before that day, the
    end of the as-of day in New York."""
    if asof is not None and asof < read_date(retrieved_at):
        end = datetime.combine(date.fromisoformat(asof) + timedelta(days=1), datetime.min.time(), NY)
        return end.astimezone(timezone.utc).isoformat(timespec="seconds")
    return retrieved_at


def _listing_inputs(ctx: Inputs, mid: str) -> dict:
    record = _record(ctx, mid)
    return {"market": mid, "record": record, "at": terms_at(_asof_or_none(ctx), record["retrieved_at"])}


def listing_of(venue: str, record: dict, at: str | None = None) -> dict:
    """A record's listing, each term as the venue states it: its question and rules; its resolution source, the
    sources its venue specifies for determining its outcome; its close date; its strike where the venue states one as data; its
    strike date, the moment its question is based on, where the venue states one as data; its family; and its fees, those
    in effect at `at`."""
    return FORMS[venue].listing(record, at)


def _listing_compute(m: dict, load) -> dict:
    return listing_of(split(m["market"])[0], load(m["record"]), m["at"])


LISTING = Derivation("market_listing", 10, "Market", _listing_inputs, _listing_compute,
                     fields=("question", "rules", "resolution_source", "close_time", "strike", "strike_date", "family",
                             "fees"))


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
        end = as_of_end(asof)
        held = [r for r in ctx.readings("book", mid) if datetime.fromisoformat(r["retrieved_at"]) <= end]
        return {"market": mid, "asof": asof, "past": True, "record": record, "history": ctx.data("history", mid),
                "reading": held[-1] if held else None}
    return {"market": mid, "asof": asof, "past": False, "record": record, "book": ctx.data("book", mid)}


def _price_compute(m: dict, load) -> dict:
    venue = split(m["market"])[0]
    forms = FORMS[venue]
    record = load(m["record"])
    opened = is_open(venue, record, m["asof"], m["past"])
    if m["past"]:
        reading = (m["reading"]["retrieved_at"], load(m["reading"])) if m.get("reading") else None
        return {**past_price(venue, load(m["history"]), m["asof"], reading), "open": opened}
    payload = load(m["book"])
    book = forms.levels(payload)
    # The moment its quotes stand at: when the book it rests on was read. A book read again and found the same is not
    # kept again (harness.readings), so this is when its quotes were first seen standing as they stand.
    return {**priced(venue, book, payload, record), "book": {"bids": book["bids"], "asks": book["asks"]},
            "open": opened, "quoted_at": (m.get("book") or {}).get("retrieved_at")}


PRICE = Derivation("market_price", 6, "Market", _price_inputs, _price_compute,
                   fields=("book", "bid", "ask", "price", "depth", "open", "last_trade", "quoted_at", "price_date",
                           "history_price"))


def history_to(venue: str, payload: dict | None, day: str | None) -> dict[str, float]:
    """A market's history as it stood at the end of `day`, the as-of: its price on each day to it, as its venue's
    history dates them (each form's `daily`). A market with no history has none."""
    return {d: p for d, p in FORMS[venue].daily(payload or {}).items() if day is None or d <= day}


def _history_inputs(ctx: Inputs, mid: str) -> dict:
    return {"market": mid, "history": ctx.data("history", mid), "asof": _asof_or_none(ctx)}


def _history_compute(m: dict, load) -> dict:
    return {"history": history_to(split(m["market"])[0], load(m["history"]), m["asof"])}


HISTORY = Derivation("market_history", 3, "Market", _history_inputs, _history_compute, fields=("history",))


def settlement_at(venue: str, record: dict, asof: str | None, past: bool) -> dict:
    """A market's settlement as its venue reports it (ONTOLOGY.md 2.1; MATH.md 3.3), read from its record by its venue's
    own fields (each form's `settlement`, the reading harness.settlements keeps for the forecast record): whether it has
    settled; its result, 1 where Yes won and 0 where No did, and None where it settled anywhere else, as a void or an
    even split does; and when, as a UTC time to the second, whichever form its venue writes it in. A record read after
    the as-of's day stands as that day ended (MATH.md 3.3): a market settled after it, or at a time its venue does not
    give, has not settled at the as-of."""
    found = FORMS[venue].settlement(record)
    at = _moment(found["at"]) if found.get("settled") and found.get("at") else None
    if found.get("settled") and past and (at is None or at > as_of_end(asof)):
        return {"settled": False, "result": None}
    return {**found, "at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} if at else found


def _settlement_inputs(ctx: Inputs, mid: str) -> dict:
    record = _record(ctx, mid)
    asof = _asof_or_none(ctx)
    return {"market": mid, "record": record, "asof": asof,
            "past": asof is not None and asof < read_date(record["retrieved_at"])}


def _settlement_compute(m: dict, load) -> dict:
    return {"settlement": settlement_at(split(m["market"])[0], load(m["record"]), m["asof"], m["past"])}


SETTLEMENT = Derivation("market_settlement", 1, "Market", _settlement_inputs, _settlement_compute,
                        fields=("settlement",))


# ---------------------------------------------------------------- the range a price allows


def growth(paid: dict | None, days: int) -> float:
    """What a dollar held in a position grows to over `days` by what the venue pays on its value: an annual
    percentage yield compounded over the years, or an annual rate paid each day on the value held."""
    rate = float((paid or {}).get("rate") or 0.0)
    if rate == 0.0 or days <= 0:
        return 1.0
    if paid["paid"] == "annual":
        return (1.0 + rate) ** (days / 365.0)
    if paid["paid"] == "daily":
        return (1.0 + rate / 365.0) ** days
    raise ValueError(f"a venue's rate is paid annual or daily, not {paid['paid']!r}")


def allows(q: float, k: float) -> list[float]:
    """The range of risk-neutral probabilities a price allows, low end first, within 0 and 1."""
    ends = (1.0 - (1.0 - q) * (1.0 + k), q * (1.0 + k))
    return [round(min(1.0, max(0.0, x)), 6) for x in sorted(ends)]


def curve_on(payloads: list[dict | None], day: str) -> dict | None:
    """The Treasury's curve on the latest day on or before `day`, of the years' payloads read: {date, curve}."""
    days = [c for p in payloads for c in (p or {}).get("results") or [] if c.get("date") and c["date"] <= day]
    return max(days, key=lambda c: c["date"]) if days else None


def paid_by(venue: str, record: dict, reading: dict | None, read_at: str | None, waits: str | None) -> dict | str:
    """What the venue pays on a position in this market, as the range reads it: its reading of the venue's page with
    when it was read; none where the venue pays nothing on this market, with the page that says so; or, where the
    reading is not to be had, what the range waits on."""
    spec = FORMS[venue].PAID
    if not spec.get("label") or not FORMS[venue].paid_on(record):
        return {"rate": 0.0, "url": spec["url"]}
    if reading is None:
        return waits or f"{NAMES[venue]}'s rate on a position, read from {spec['url']}"
    return {**{k: reading.get(k) for k in ("rate", "paid", "stated", "url")}, "read_at": read_at}


def risk_neutral(price: float | None, close: str | None, asof: str, curve: dict | None, paid: dict | str) -> dict | None:
    """The range a price allows, with what it was worked out from; what it waits on where an input is missing; None for
    a market with no price."""
    if price is None:
        return None
    waits = [w for w in ((None if close else "the market's close date"),
                         (None if curve else f"the Treasury's yield curve on or before {asof}"),
                         (paid if isinstance(paid, str) else None)) if w]
    if waits:
        return {"waits_on": waits}
    days = max(0, (date.fromisoformat(close) - date.fromisoformat(asof)).days)
    g_t = 1.0 / rates.discount(curve["curve"], days / 365.0)
    g_v = growth(paid, days)
    k = g_t / g_v - 1.0
    low, high = allows(price, k)
    return {"low": low, "high": high, "k": round(k, 6), "days": days,
            "treasury": {"curve_date": curve["date"], "growth": round(g_t, 6)},
            "venue": {**paid, "growth": round(g_v, 6)}}


def rate_reading(fetch: Callable[[], dict], kept: Callable[[], list[dict]], venue: str, asof: str,
                 past: bool) -> tuple[dict | None, str | None]:
    """The venue's rate as the range reads it, by `fetch` (the reading for the as-of, read where the store has none)
    and `kept` (every reading kept, oldest first): today, the reading for the as-of, and where that read fails, the
    last reading kept; for a past as-of, the latest kept reading taken by the as-of's end. With what it waits on where
    there is none; nothing for a venue that pays nothing on a position."""
    if not FORMS[venue].PAID.get("label"):
        return None, None
    if not past:
        try:
            return fetch(), None
        except (Waiting, Unavailable) as e:
            held = kept()
            return (held[-1], None) if held else (None, str(e.__cause__ or e))
    held = [r for r in kept() if read_date(r["retrieved_at"]) <= asof]
    return (held[-1], None) if held else \
        (None, f"{NAMES[venue]}'s rate on a position as it stood on {asof}, which no reading kept here gives")


def _curve(ctx: Inputs, year: int, asof: str) -> dict:
    """A year's Treasury curves as the range reads them: none where the read fails, which the range then waits on."""
    try:
        return ctx.data("yield_curve", str(year), covering=(asof, asof), required=False)
    except Waiting:
        return {"data": None}


def _range_inputs(ctx: Inputs, mid: str) -> dict:
    venue = split(mid)[0]
    asof = asof_of(ctx)
    record = _record(ctx, mid)
    past = asof < read_date(record["retrieved_at"])
    year = int(asof[:4])
    reading, waits = rate_reading(lambda: ctx.data("venue_rate", venue, covering=(asof, asof)),
                                  lambda: ctx.readings("venue_rate", venue), venue, asof, past)
    return {"market": mid, "asof": asof, "record": record, "price": ctx.value("market_price", mid, ref=True),
            "listing": ctx.value("market_listing", mid, ref=True),
            "curves": [_curve(ctx, y, asof) for y in (year - 1, year)],
            "rate": reading, "waits": waits}


def _range_compute(m: dict, load) -> dict:
    venue = split(m["market"])[0]
    curve = curve_on([load(c) if c.get("data") else None for c in m["curves"]], m["asof"])
    rate = m["rate"]
    paid = paid_by(venue, load(m["record"]), load(rate) if rate else None, rate["retrieved_at"] if rate else None,
                   m["waits"])
    return {"risk_neutral": risk_neutral(load(m["price"])["price"], load(m["listing"])["close_time"], m["asof"],
                                         curve, paid)}


RANGE = Derivation("market_range", 1, "Market", _range_inputs, _range_compute, fields=("risk_neutral",))




# ---------------------------------------------------------------- a market as the proposition that asks it reads it


def transformed(price: dict, side: str) -> dict:
    """The proposition's bid, ask, and price from its market's (MATH.md 1.2): the same through the market's Yes side, and
    through its No side one minus each, bid and ask exchanged."""
    bid, ask, p = price.get("bid"), price.get("ask"), price.get("price")
    if side != "no":
        return {"bid": bid, "ask": ask, "price": p}
    flip = lambda x: None if x is None else 1.0 - x  # noqa: E731
    return {"bid": flip(ask), "ask": flip(bid), "price": flip(p)}


def _quoted_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    return {"link": lid, "side": link["stated"].get("side") or "yes",
            "market": ctx.value("market_price", link["to"])}


def _quoted_compute(m: dict, load) -> dict:
    found = m["market"]["output"]
    return {**transformed(found, m["side"]), "depth": found.get("depth"), "past": bool(found.get("past"))}


QUOTED = Derivation("quoted", 1, "asks", _quoted_inputs, _quoted_compute, fields=("bid", "ask", "price"))


def taken(link: dict) -> bool:
    """Whether a market's price is taken as the probability of the proposition that asks it (MATH.md 1.2): linking takes
    it, and the link says where the analyst judges otherwise, with its basis."""
    return (link["stated"].get("taken") or {}).get("value", True) is not False


def _moment(at: str) -> datetime:
    return datetime.fromisoformat(str(at).replace("Z", "+00:00"))


def _priced(ctx: Inputs, mid: str, ref: dict, side: str) -> float | None:
    """The probability one reading of a market's book gives the proposition: its Yes price, or one minus it through
    its No side; None where the reading has no price."""
    if not ref or not ref.get("data"):
        return None
    price = price_of_reading(ctx, mid, ref)
    return None if price is None else (1.0 - price if side == "no" else price)


def probability(ctx: Inputs, lid: str, at: str | None = None) -> dict:
    """The probability a market gives the proposition that asks it through `lid` (MATH.md 1.2, 1.5): its price, or one
    minus it through its No side, at the reading the store holds now, or with `at`, a moment, at the reading it held
    then; where that reading has no price, at the last reading before it that had one, or else the first after it.
    `{q, at, reading}`, `at` the time of the reading it rests on. A market read at a past as-of is its price then."""
    link = ctx.link(lid)
    side, mid = link["stated"].get("side") or "yes", link["to"]
    quoted = ctx.value("quoted", lid)
    if quoted["output"].get("past"):
        if quoted["output"].get("price") is None:
            raise Unavailable(f"{mid} had no price at the as-of")
        return {"q": quoted["output"]["price"], "at": None, "quoted": quoted["basis"]}
    ref = ctx.data("book", mid, at=at)
    q = _priced(ctx, mid, ref, side)
    if q is not None:
        return {"q": q, "at": ref["retrieved_at"], "reading": ref}
    readings = ctx.readings("book", mid)
    here = max([i for i, r in enumerate(readings) if at is None or _moment(r["retrieved_at"]) <= _moment(at)],
               default=0)
    for i in [*range(here, -1, -1), *range(here + 1, len(readings))]:
        q = _priced(ctx, mid, readings[i], side)
        if q is not None:
            return {"q": q, "at": readings[i]["retrieved_at"], "reading": readings[i]}
    raise Unavailable(f"{mid} has had no price at any reading the store keeps")


DERIVATIONS = (LISTING, PRICE, HISTORY, SETTLEMENT, RANGE, QUOTED)
