"""The judge's checks: claims the analyst made about texts.

| Check | The claim | Evidence |
|---|---|---|
| market, forward | if the proposition is true, the market resolves YES (NO under the complement) | the date; the proposition; the market |
| market, converse | if the market resolves YES (NO under the complement), the proposition is true | the same |
| implication | if A is true, B is true, where the fields do not decide it; a market at an end is its Yes, asked as the market check asks it, or for two markets whether the second resolves Yes wherever the first does | the date; A and B |
| way | a way's outcomes lie within its parent's, or within those where the parent is false for a way of an event's No side, where the fields do not decide it | the date; the way and its parent |

Each asks one question comparing what the two texts say, in every outcome; the answer is the probability the claim
holds. Passages are not read: news linked to a proposition defines neither text. An answer below one half disputes
the claim. The claim stands, because it is the analyst's, and check() lists what differs between its two texts until
the analyst changes the claim, which asks again.

What the fields decide (MATH.md 1.2). A proposition's fields count its outcomes as stated, so two propositions on one
metric and resolution source are compared by their fields both ways: A implies B when every value A's threshold allows,
B's allows, strict and non-strict kept distinct (x >= 25 implies x >= 20 and x > 20, not x > 25), and A's time lies
within B's (a level on a date, or a decision on it, within that date and within any window of hitting containing it; a
window of hitting within any window of hitting containing it). A claim the fields do not give is one they contradict,
listed by check() with the values compared. The judge is asked about neither, and reads every other implication. The
containment a composite claims, and the place a way claims, are tested the same way: a way on an event's No side claims
that it and the event exclude each other, which the fields give where both are levels on one date whose thresholds
share no value, and contradict for any other pair they compare, since any other pair can both be true. No market's
strike is compared: what it measures, its texts say.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from harness import schema
from harness.derive import evidence as ev
from harness.derive.channels import price_fields
from harness.derive.engine import Derivation, Inputs, Unavailable

DISPUTED = 0.5  # an answer below one half reads the claim as false
POINT_KINDS = ("level_at_date", "scheduled_decision")  # a level read on one date


def market_questions(side: str) -> list[tuple[str, str]]:
    """Whether the market's resolution follows the proposition, and the proposition the market's: YES, or NO under the
    complement."""
    side = "No" if side == "no" else "Yes"
    return [("market_forward", f"Does the market resolve {side}, under its rules, in every outcome in which the "
                               f"proposition is true?"),
            ("market_converse", f"Is the proposition true in every outcome in which the market resolves {side} under "
                                f"its rules?")]


IMPLICATION_QUESTION = ("implication", "Is the second proposition true in every outcome in which the first proposition "
                                       "is true?")
EXCLUSION_QUESTION = ("exclusion", "Is the second proposition false in every outcome in which the first proposition is "
                                   "true?")
MARKETS_QUESTION = ("market_implication", "Does the second market resolve Yes, under its rules, in every outcome in which "
                                          "the first market resolves Yes under its rules?")


# ---------------------------------------------------------------- the market check


def market_question(ctx: Inputs, pid: str, mid: str, side: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """What the market check asks, and over which evidence: the date, the proposition, the market, as of now."""
    return market_questions(side), [ev.date(ctx), ev.proposition(ctx, pid), ev.market(ctx, mid)]


def implication_question(ctx: Inputs, a: str, b: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """What the implication check asks, and over which evidence: the date, A, and B. A market stands as its Yes (MATH.md
    1.2), and its texts are read in the venue's words: from a proposition to a market, the claim is the
    market check's forward question, and from a market to a proposition its converse, each over the date, the
    proposition and the market, so that one claim has one reading whichever link states it; between two markets,
    whether the second resolves Yes wherever the first does."""
    ma, mb = (schema.MARKET_ID_RE.match(x) is not None for x in (a, b))
    if ma and mb:
        return [MARKETS_QUESTION], [ev.date(ctx), ev.market(ctx, a), ev.market(ctx, b)]
    if mb:
        return [market_questions("yes")[0]], [ev.date(ctx), ev.proposition(ctx, a), ev.market(ctx, b)]
    if ma:
        return [market_questions("yes")[1]], [ev.date(ctx), ev.proposition(ctx, b), ev.market(ctx, a)]
    return [IMPLICATION_QUESTION], [ev.date(ctx), ev.proposition(ctx, a), ev.proposition(ctx, b)]


def _market_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    pid, mid = link["from"], link["to"]
    side = link["stated"].get("side") or "yes"
    forward, converse = ctx.ask_all(*market_question(ctx, pid, mid, side))
    return {"link": lid, "side": side, "forward": forward, "converse": converse}


def _market_compute(m: dict, load) -> dict:
    return {"forward": m["forward"]["answer"], "converse": m["converse"]["answer"],
            "judgments": {"forward": m["forward"]["judgment"], "converse": m["converse"]["judgment"]}}


MARKET = Derivation("market_check", 3, "asks", _market_inputs, _market_compute, fields=("forward", "converse"))


# ---------------------------------------------------------------- the implication check


def _norm(text: str | None) -> str | None:
    return " ".join(text.split()).casefold() if isinstance(text, str) and text.strip() else None


def metric_terms(stated_of: Callable[[str], dict], price_source: Callable[[str], str | None],
                 mid: str | None) -> tuple[str | None, str | None]:
    """What a proposition on a metric is read from when it states no source, and what the metric is. The source is the
    metric's own. A price the store derives is fixed by its metric: a crypto pair's is Coinbase Exchange's trade, and a
    listed ticker's, which names no publisher, is the listing's own (`price_source`). A stated metric that names no
    source is one quantity, the metric itself. So two propositions on one metric that state no source read one
    quantity, and the fields compare them. The metric is a price, a yield, another level, or an occurrence, something
    that happens; a price the store derives is a price. `stated_of(mid)` gives a metric's stated fields, from the store
    or from a record's copy of it."""
    if not mid:
        return None, None
    if schema.PROXY_METRIC_RE.match(mid):
        return price_source(mid) or mid, "price"
    stated = stated_of(mid)
    return stated.get("resolution_source") or mid, stated.get("kind")


def price_source(ctx: Inputs, mid: str) -> str | None:
    """The source a price the store derives is read from (metric_terms)."""
    return price_fields(ctx, mid.split(":", 2)[2]).get("resolution_source")


def _metric_terms(ctx: Inputs, mid: str | None) -> tuple[str | None, str | None]:
    return metric_terms(lambda m: ctx.object(m)["stated"], lambda m: price_source(ctx, m), mid)


def _metric_source(ctx: Inputs, mid: str | None) -> str | None:
    return _metric_terms(ctx, mid)[0]


def _metric_kind(ctx: Inputs, mid: str | None) -> str | None:
    return metric_terms(lambda m: ctx.object(m)["stated"], lambda m: None, mid)[1]  # what a metric is reads no price


def proposition_reading(stated: dict, metric_source: str | None, asof: str | None, *,
                        occurrence: bool = False) -> dict | None:
    """What the arithmetic reads of a proposition, as it is stated: its metric, resolution source, relation, value, and
    time. A threshold on a metric is read with its value. A proposition that `occurs` is read where its metric is the
    occurrence (`occurrence`): it has no threshold, so every value of it qualifies and its time alone places it
    (MATH.md 1.2). One that names no occurrence is what its text says, and the fields decide nothing for it."""
    if stated.get("comparison") == "occurs":
        if not (occurrence and stated.get("metric")):
            return None
    elif not stated.get("metric") or stated.get("value") is None:
        return None
    point = stated["kind"] in POINT_KINDS
    end = stated["window_end"]
    return {"metric": stated["metric"], "source": _norm(stated.get("resolution_source") or metric_source),
            "relation": stated["comparison"], "value": stated.get("value") if stated["comparison"] != "occurs" else None,
            "kind": "point" if point else "hitting",
            "start": end if point else (stated.get("window_start") or asof), "end": end,
            **({"upper": stated["upper_comparison"], "upper_value": stated["upper_value"]}
               if stated.get("upper_comparison") else {})}


INF = float("inf")


def interval(r: dict) -> tuple[float, bool, float, bool]:
    """The values a proposition's threshold allows, as (lo, lo closed, hi, hi closed): x >= 25 is [25, inf), x < 25
    (-inf, 25), x == 25 [25, 25], and a band from its lower end to its upper."""
    rel, v = r["relation"], r["value"]
    lo, lo_c, hi, hi_c = -INF, False, INF, False
    if rel in (">", ">=", "=="):
        lo, lo_c = v, rel != ">"
    if rel in ("<", "<=", "=="):
        hi, hi_c = v, rel != "<"
    if r.get("upper"):
        hi, hi_c = r["upper_value"], r["upper"] == "<="
    return lo, lo_c, hi, hi_c


def _equal(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def within(a: dict, b: dict) -> bool:
    """Every value a's threshold allows, b's allows: x >= 25 is within x >= 20 and x > 20, not x > 25; a band within
    each rung below its lower end."""
    alo, alc, ahi, ahc = interval(a)
    blo, blc, bhi, bhc = interval(b)
    same = lambda x, y: x == y if INF in (abs(x), abs(y)) else _equal(x, y)  # noqa: E731  an unbounded end, exactly
    above = (alo > blo and not same(alo, blo)) or (same(alo, blo) and (blc or not alc))
    below = (ahi < bhi and not same(ahi, bhi)) or (same(ahi, bhi) and (bhc or not ahc))
    return above and below


def line_of(r: dict | None) -> tuple[tuple, list] | None:
    """The line the fields place a proposition on, and its set there (MATH.md 1.2, "A ladder"): a level on a date, or a
    decision on it, on the line of that metric's value on that date, its set the values its threshold allows; a
    level reached at any time, on the line of the date that threshold is first reached from the window's start, its set
    the dates through its window's end. None for a proposition the fields place on no line. A set is [lo, lo closed,
    hi, hi closed], an unbounded end None."""
    if r is None or not r["source"]:
        return None
    if r["kind"] == "point" and r["relation"] == "occurs":  # an occurrence on a date is no rung: a ladder of one
        return None  # occurrence is its deadlines, each a window of hitting
    if r["kind"] == "point":
        lo, lo_c, hi, hi_c = interval(r)
        return (("level", r["metric"], r["source"], r["end"]),
                [None if lo == -INF else lo, lo_c, None if hi == INF else hi, hi_c])
    if r.get("upper") or not r["start"]:
        return None
    end = date.fromisoformat(r["end"]).toordinal()
    return (("time", r["metric"], r["source"], r["relation"], r["value"], r["start"]), [None, False, end, True])


def _covers(a: dict, b: dict) -> bool:
    """a's time lies within b's: a level on a date, or a decision on it, within that date and within any window of
    hitting containing it; a window of hitting within any window of hitting containing it."""
    if a["kind"] == "point":
        if b["kind"] == "point":
            return a["end"] == b["end"]
        return b["start"] is not None and b["start"] <= a["end"] <= b["end"]
    if b["kind"] == "point":
        return False
    return a["start"] is not None and b["start"] is not None and b["start"] <= a["start"] and a["end"] <= b["end"]


def comparable(a: dict | None, b: dict | None) -> bool:
    """Whether the data establish comparable terms: two propositions on one metric and resolution source, one quantity
    in its one unit, or one occurrence."""
    return bool(a and b and a["metric"] == b["metric"] and a["source"] and a["source"] == b["source"])


def entails(a: dict | None, b: dict | None) -> bool:
    """Whether the fields give that every outcome of a is one of b: every value a's threshold allows, b's allows, and
    a's time lies within b's."""
    return comparable(a, b) and within(a, b) and _covers(a, b)


def terms(within_: dict | None, of: dict | None) -> dict | None:
    """The claim that every outcome of one proposition is one of another, tested by the fields where they are
    comparable: the two readings and whether the fields give it. None where they are not comparable. A claim the
    fields do not give is one they contradict, since each proposition counts exactly its outcomes as stated."""
    if not comparable(within_, of):
        return None
    return {"within": within_, "of": of, "holds": entails(within_, of)}


def apart(a: dict | None, b: dict | None) -> bool:
    """Whether the fields give that a and b are never both true: levels on one date, or decisions on it, whose
    thresholds share no value. Any other two propositions on one metric and resolution source can both be true."""
    if not comparable(a, b) or not (a["kind"] == b["kind"] == "point" and a["end"] == b["end"]):
        return False
    alo, alc, ahi, ahc = interval(a)
    blo, blc, bhi, bhc = interval(b)
    same = lambda x, y: x == y if INF in (abs(x), abs(y)) else _equal(x, y)  # noqa: E731
    left = (ahi < blo and not same(ahi, blo)) or (same(ahi, blo) and not (ahc and blc))
    right = (bhi < alo and not same(bhi, alo)) or (same(bhi, alo) and not (bhc and alc))
    return left or right


def claimed(relation: str, first: dict | None, second: dict | None, side: str | None = None) -> dict | None:
    """The containment a link of the breakdown claims between its two ends, from its first to its second, tested by
    the fields where they are comparable (terms): an implication's first within its second; a composite of `and` or
    `then` within each part; each part of an `or` within its composite; a way within its parent, or, for a way of an
    event's No side (`side`), within the outcomes where the event is false, which is that the two are never both true
    (apart)."""
    if relation == "way" and side == "no":
        if not comparable(first, second):
            return None
        return {"within": second, "of": first, "side": "no", "holds": apart(first, second)}
    return terms(second, first) if relation in ("or", "way") else terms(first, second)


def reading(ctx: Inputs, pid: str, asof: str | None) -> dict | None:
    """What the field arithmetic reads of a proposition in the store."""
    return stated_reading(ctx, ctx.object(pid)["stated"], asof)


def stated_reading(ctx: Inputs, stated: dict, asof: str | None) -> dict | None:
    """What the field arithmetic reads of a proposition's stated fields, its metric read from the store."""
    mid = stated.get("metric")
    return read_stated(stated, (_metric_source(ctx, mid), _metric_kind(ctx, mid)), asof)


def read_stated(stated: dict, terms: tuple[str | None, str | None], asof: str | None) -> dict | None:
    """What the field arithmetic reads of a proposition's stated fields, its metric's source and kind given
    (metric_terms), from the store or from a record's copy of it."""
    return proposition_reading(stated, terms[0], asof, occurrence=terms[1] == "occurrence")


def _implication_inputs(ctx: Inputs, lid: str) -> dict:
    """A implies B: the fields test it where A and B are propositions on one metric and resolution source, and the
    judge is not asked; otherwise the judge reads it, a market at either end by its texts, whatever its strike."""
    link = ctx.link(lid)
    a, b = link["from"], link["to"]
    if not any(schema.MARKET_ID_RE.match(x) for x in (a, b)):
        day = ev.asof(ctx)
        tested = claimed("implies", reading(ctx, a, day), reading(ctx, b, day))
        if tested is not None:
            return {"link": lid, "terms": tested}
    return {"link": lid, "judged": ctx.ask_all(*implication_question(ctx, a, b))[0]}


def _implication_compute(m: dict, load) -> dict:
    if "terms" in m:
        return {"terms": m["terms"]}
    return {"check": m["judged"]["answer"], "judgment": m["judged"]["judgment"]}


IMPLICATION = Derivation("implication_check", 5, "implies", _implication_inputs, _implication_compute,
                         fields=("terms", "check"))


# ---------------------------------------------------------------- the containment a composite claims


def _composite_inputs(ctx: Inputs, lid: str) -> dict:
    """A composite of `and` or `then` claims that its outcomes lie within each component's, and a component of `or`
    within its composite's: the fields test that containment where the two are on one metric and resolution source,
    as they test an implication."""
    link = ctx.link(lid)
    composite, component = link["from"], link["to"]
    day = ev.asof(ctx)
    tested = claimed(link["relation"], reading(ctx, composite, day), reading(ctx, component, day))
    return {"link": lid, "terms": tested}


def _composite_compute(m: dict, load) -> dict:
    return {"terms": m["terms"]} if m["terms"] is not None else {}


COMPOSITE = Derivation("composite_terms", 1, "and", _composite_inputs, _composite_compute, fields=("terms",),
                       extra=(("or", ("terms",), lambda out, lid: out), ("then", ("terms",), lambda out, lid: out)))


# ---------------------------------------------------------------- the place a way claims


def way_question(ctx: Inputs, parent: str, way: str, side: str | None) -> tuple[list[tuple[str, str]], list[dict]]:
    """What the way check asks, and over which evidence: the date, the way, and its parent. A way of an event's No side
    claims the event is false wherever the way is true; any other way, that its parent is true wherever it is."""
    asked = EXCLUSION_QUESTION if side == "no" else IMPLICATION_QUESTION
    return [asked], [ev.date(ctx), ev.proposition(ctx, way), ev.proposition(ctx, parent)]


def _way_inputs(ctx: Inputs, lid: str) -> dict:
    """A way lies within its parent, or within the outcomes where its event is false for a way of the No side (MATH.md
    1.1): the fields test it where the two are on one metric and resolution source, and otherwise the judge reads
    it."""
    link = ctx.link(lid)
    parent, way, side = link["from"], link["to"], link["stated"].get("side")
    day = ev.asof(ctx)
    tested = claimed("way", reading(ctx, parent, day), reading(ctx, way, day), side)
    if tested is not None:
        return {"link": lid, "terms": tested}
    return {"link": lid, "judged": ctx.ask_all(*way_question(ctx, parent, way, side))[0]}


WAY = Derivation("way_check", 1, "way", _way_inputs, _implication_compute, fields=("terms", "check"))


def disputed(answer: float | None) -> bool:
    return answer is not None and answer < DISPUTED


DERIVATIONS = (MARKET, IMPLICATION, COMPOSITE, WAY)
