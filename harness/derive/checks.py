"""The judge's checks: claims the analyst made about texts.

| Check | The claim | Evidence |
|---|---|---|
| market, forward | if the proposition is true, the market resolves YES (NO under the complement) | the date; the proposition; the market |
| market, converse | if the market resolves YES (NO under the complement), the proposition is true | the same |
| implication | if A is true, B is true, where the fields do not decide it | the date; A and B |

Each asks one question comparing what the two texts say, in every outcome; the answer is the probability the claim
holds. Passages are not read: news linked to a proposition defines neither text. An answer below one half disputes
the claim. The claim stands, because it is the analyst's, and check() lists the dispute
until the analyst changes the claim, which asks again, or writes a note about the link.

What the fields decide (MATH.md 1.4). A proposition's fields count its outcomes as stated, so two propositions on one
metric and resolution source are compared by their fields both ways: A implies B when every value A's threshold allows,
B's allows, strict and non-strict kept distinct (x >= 25 implies x >= 20 and x > 20, not x > 25), and A's time lies
within B's (a level on a date, or a decision on it, within that date and within any window of hitting containing it; a
window of hitting within any window of hitting containing it). A claim the fields do not give is one they contradict,
listed by check() with the values compared. The judge is asked about neither, and reads every other implication. The
containment a composite claims is tested the same way. No market's strike is compared: what it measures, its texts say.
"""

from __future__ import annotations

from datetime import date

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


# ---------------------------------------------------------------- the market check


def market_question(ctx: Inputs, pid: str, mid: str, side: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """What the market check asks, and over which evidence: the date, the proposition, the market, as of now."""
    return market_questions(side), [ev.date(ctx), ev.proposition(ctx, pid), ev.market(ctx, mid)]


def implication_question(ctx: Inputs, a: str, b: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """What the implication check asks, and over which evidence: the date, A, and B."""
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


MARKET = Derivation("market_check", 2, "asks", _market_inputs, _market_compute, fields=("forward", "converse"))


# ---------------------------------------------------------------- the implication check


def _norm(text: str | None) -> str | None:
    return " ".join(text.split()).casefold() if isinstance(text, str) and text.strip() else None


def _metric_source(ctx: Inputs, mid: str | None) -> str | None:
    """What a proposition on a metric is read from when it states no source: the metric's own. A price the store
    derives is fixed by its metric: a crypto pair's is Coinbase Exchange's trade, and a listed ticker's, which names no
    publisher, is the listing's own. A stated metric that names no source is one quantity, the metric itself. So two
    propositions on one metric that state no source read one quantity, and the fields compare them."""
    if not mid:
        return None
    if schema.PROXY_METRIC_RE.match(mid):
        return price_fields(ctx, mid.split(":", 2)[2]).get("resolution_source") or mid
    return ctx.object(mid)["stated"].get("resolution_source") or mid


def _metric_kind(ctx: Inputs, mid: str | None) -> str | None:
    """What a metric is: a price, a yield, another level, or an occurrence, something that happens. A price the store
    derives is a price."""
    if not mid:
        return None
    if schema.PROXY_METRIC_RE.match(mid):
        return "price"
    return ctx.object(mid)["stated"].get("kind")


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


def _holds(x: float, relation: str, v: float) -> bool:
    return {">=": x >= v or _equal(x, v), ">": x > v and not _equal(x, v), "<=": x <= v or _equal(x, v),
            "<": x < v and not _equal(x, v), "==": _equal(x, v)}[relation]


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


def reading(ctx: Inputs, pid: str, asof: str | None) -> dict | None:
    """What the field arithmetic reads of a proposition in the store."""
    return stated_reading(ctx, ctx.object(pid)["stated"], asof)


def stated_reading(ctx: Inputs, stated: dict, asof: str | None) -> dict | None:
    """What the field arithmetic reads of a proposition's stated fields, its metric read from the store."""
    mid = stated.get("metric")
    return proposition_reading(stated, _metric_source(ctx, mid), asof, occurrence=_metric_kind(ctx, mid) == "occurrence")


def _implication_inputs(ctx: Inputs, lid: str) -> dict:
    """A implies B: the fields test it where A and B are on one metric and resolution source, and the judge is not
    asked; otherwise the judge reads it."""
    link = ctx.link(lid)
    a, b = link["from"], link["to"]
    if any(schema.MARKET_ID_RE.match(x) for x in (a, b)):  # a market stands as its question: no fields to test
        raise Unavailable(f"{lid} names a market, which has no fields to test it by")
    day = ev.asof(ctx)
    tested = terms(reading(ctx, a, day), reading(ctx, b, day))
    if tested is not None:
        return {"link": lid, "terms": tested}
    return {"link": lid, "judged": ctx.ask_all(*implication_question(ctx, a, b))[0]}


def _implication_compute(m: dict, load) -> dict:
    if "terms" in m:
        return {"terms": m["terms"]}
    return {"check": m["judged"]["answer"], "judgment": m["judged"]["judgment"]}


IMPLICATION = Derivation("implication_check", 3, "implies", _implication_inputs, _implication_compute,
                         fields=("terms", "check"))


# ---------------------------------------------------------------- the containment a composite claims


def _composite_inputs(ctx: Inputs, lid: str) -> dict:
    """A composite of `and` or `then` claims that its outcomes lie within each component's, and a component of `or`
    within its composite's: the fields test that containment where the two are on one metric and resolution source,
    as they test an implication."""
    link = ctx.link(lid)
    composite, component = link["from"], link["to"]
    day = ev.asof(ctx)
    a, b = reading(ctx, composite, day), reading(ctx, component, day)
    tested = terms(b, a) if link["relation"] == "or" else terms(a, b)
    return {"link": lid, "terms": tested}


def _composite_compute(m: dict, load) -> dict:
    return {"terms": m["terms"]} if m["terms"] is not None else {}


COMPOSITE = Derivation("composite_terms", 1, "and", _composite_inputs, _composite_compute, fields=("terms",),
                       extra=(("or", ("terms",), lambda out, lid: out), ("then", ("terms",), lambda out, lid: out)))


def disputed(answer: float | None) -> bool:
    return answer is not None and answer < DISPUTED


DERIVATIONS = (MARKET, IMPLICATION, COMPOSITE)
