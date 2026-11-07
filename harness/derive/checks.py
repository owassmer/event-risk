"""The judge's checks: claims the analyst made about texts.

| Check | The claim | Evidence |
|---|---|---|
| market, forward | if the proposition is true, the market resolves YES (NO under the complement) | the date; the proposition; the market; the passages linked to the proposition |
| market, converse | if the market resolves YES (NO under the complement), the proposition is true | the same |
| implication | if A is true, B is true, where the fields do not decide it | the date; A and B; the passages linked to either |

Each asks one plain question in the words of the texts it compares; the answer is the probability the claim holds. An
answer below one half disputes the claim. The claim stands, because it is the analyst's, and check() lists the dispute
until the analyst changes the claim, which asks again, or writes a note about the link.

What the fields decide. A threshold implies every weaker threshold on the same metric, resolution source, and window,
strict and non-strict kept distinct (x >= 25 implies x >= 20 and x > 20, not x > 25); a level on a date implies hitting
it over any window containing the date; hitting within a window implies hitting within any window containing it, so
hitting by an earlier date implies hitting by a later one. Where they decide that A implies B, the implication holds by
arithmetic and the judge is not asked.
"""

from __future__ import annotations

from harness import schema
from harness.derive import evidence as ev
from harness.derive.engine import Derivation, Inputs

DISPUTED = 0.5  # an answer below one half reads the claim as false
POINT_KINDS = ("level_at_date", "scheduled_decision")  # a level read on one date


def quote(text: str) -> str:
    return f"\u201c{' '.join(str(text).split())}\u201d"


def market_questions(says: str, question: str, transform: str) -> list[tuple[str, str]]:
    side = "No" if transform == "complement" else "Yes"
    return [("market_forward", f"If {quote(says)} is true, does the market {quote(question)} resolve {side}?"),
            ("market_converse", f"If the market {quote(question)} resolves {side}, is {quote(says)} true?")]


def implication_question(a: str, b: str) -> tuple[str, str]:
    return "implication", f"If {quote(a)} is true, is {quote(b)} true?"


def _answer(found: dict) -> dict:
    return {"check": found["answer"], "judgment": found["judgment"],
            **({"reason": found["reason"]} if found.get("reason") else {})}


# ---------------------------------------------------------------- the market check


def _market_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    pid, mid = link["from"], link["to"]
    transform = link["stated"].get("transform") or "identity"
    says = ctx.object(pid)["stated"]["text"]
    question = ctx.value("market_listing", mid)["output"].get("question") or ""
    found = [ev.date(ctx), ev.proposition(ctx, pid), ev.market(ctx, mid)] + ev.passages(ctx, pid)
    forward, converse = ctx.ask_all(market_questions(says, question, transform), found)
    return {"link": lid, "transform": transform, "forward": forward, "converse": converse}


def _market_compute(m: dict, load) -> dict:
    out = {"forward": m["forward"]["answer"], "converse": m["converse"]["answer"],
           "judgments": {"forward": m["forward"]["judgment"], "converse": m["converse"]["judgment"]}}
    unanswered = {f: m[f]["reason"] for f in ("forward", "converse") if m[f]["reason"]}
    if unanswered:
        out["unavailable"] = unanswered
    return out


MARKET = Derivation("market_check", 1, "quoted_by", _market_inputs, _market_compute, fields=("forward", "converse"))


# ---------------------------------------------------------------- the implication check


def _norm(text: str | None) -> str | None:
    return " ".join(text.split()).casefold() if isinstance(text, str) and text.strip() else None


def _metric_source(ctx: Inputs, mid: str | None) -> str | None:
    if not mid:
        return None
    if schema.PROXY_METRIC_RE.match(mid):
        return ctx.value("proxy_price", mid)["output"].get("resolution_source")
    return ctx.object(mid)["stated"].get("resolution_source")


def proposition_reading(stated: dict, metric_source: str | None, asof: str | None) -> dict | None:
    """What the arithmetic reads of a proposition: its metric, resolution source, relation, value, and window. A
    proposition that `occurs` names no level, and the fields decide nothing for it."""
    if stated.get("relation") == "occurs" or not stated.get("metric") or stated.get("value") is None:
        return None
    point = stated["kind"] in POINT_KINDS
    end = stated["window_end"]
    return {"metric": stated["metric"], "source": _norm(stated.get("resolution_source") or metric_source),
            "relation": stated["relation"], "value": stated["value"], "kind": "point" if point else "hitting",
            "start": end if point else (stated.get("window_start") or asof), "end": end}


def _equal(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _holds(x: float, relation: str, v: float) -> bool:
    return {">=": x >= v or _equal(x, v), ">": x > v and not _equal(x, v), "<=": x <= v or _equal(x, v),
            "<": x < v and not _equal(x, v), "==": _equal(x, v)}[relation]


def within(a_rel: str, a: float, b_rel: str, b: float) -> bool:
    """Every value satisfying `a_rel a` satisfies `b_rel b`: x >= 25 is within x >= 20 and x > 20, not x > 25."""
    if a_rel == "==":
        return _holds(a, b_rel, b)
    up = (">=", ">")
    if b_rel == "==" or (a_rel in up) != (b_rel in up):
        return False
    same = _equal(a, b)
    if a_rel in up:
        if b_rel == ">=":
            return a > b or same
        return (a > b and not same) or (same and a_rel == ">")
    if b_rel == "<=":
        return a < b or same
    return (a < b and not same) or (same and a_rel == "<")


def _covers(a: dict, b: dict) -> bool:
    """a's occurrence in time is within b's: a level on a date within a hitting window containing it, a hitting
    window within a window containing it."""
    if a["kind"] == "point":
        if b["kind"] == "point":
            return a["end"] == b["end"]
        return b["start"] is not None and b["start"] <= a["end"] <= b["end"]
    if b["kind"] == "point":
        return False
    return a["start"] is not None and b["start"] is not None and b["start"] <= a["start"] and a["end"] <= b["end"]


def entails(a: dict | None, b: dict | None) -> bool:
    """Whether the fields decide that a implies b."""
    if not a or not b or a["metric"] != b["metric"] or not a["source"] or a["source"] != b["source"]:
        return False
    return within(a["relation"], a["value"], b["relation"], b["value"]) and _covers(a, b)


def reading(ctx: Inputs, pid: str, asof: str | None) -> dict | None:
    """What the field arithmetic reads of a proposition in the store."""
    stated = ctx.object(pid)["stated"]
    return proposition_reading(stated, _metric_source(ctx, stated.get("metric")), asof)


def _implication_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    a, b = link["from"], link["to"]
    day = ev.asof(ctx)
    ra, rb = reading(ctx, a, day), reading(ctx, b, day)
    if entails(ra, rb):
        return {"link": lid, "readings": {"from": ra, "to": rb}, "by_fields": True}
    found = [ev.date(ctx), ev.proposition(ctx, a), ev.proposition(ctx, b)] + ev.passages(ctx, a) + ev.passages(ctx, b)
    question = implication_question(ctx.object(a)["stated"]["text"], ctx.object(b)["stated"]["text"])
    return {"link": lid, "readings": {"from": ra, "to": rb}, "by_fields": False,
            "judged": ctx.ask_all([question], found)[0]}


def _implication_compute(m: dict, load) -> dict:
    if m["by_fields"]:
        return {"by_fields": True, "check": None}
    out = {"by_fields": False, **_answer(m["judged"])}
    if out.get("reason"):
        out["unavailable"] = {"check": out.pop("reason")}
    return out


IMPLICATION = Derivation("implication_check", 1, "implies", _implication_inputs, _implication_compute,
                         fields=("by_fields", "check"))


def disputed(answer: float | None) -> bool:
    return answer is not None and answer < DISPUTED


DERIVATIONS = (MARKET, IMPLICATION)
