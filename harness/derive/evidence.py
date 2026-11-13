"""The evidence the judge reads: the one serializer.

Every check's evidence is built here, from the store: the date the store speaks as of; propositions as the analyst
wrote them, each window as the analyst stated it, with no start filled in from the as-of, and a level on a metric with
the metric's name, unit, and publisher, what the proposition is about, and not the metric's level today, which bears on
how likely the threshold is and not on what the texts say; and a market as its venue words it, with the title of its event
where that differs from its question, its question, rules, resolution source, close date, and whether it is open on
that date. A Kalshi market names no source of its own; its rules do, or the settlement sources Kalshi lists for its event
or else its series, which it carries under that name. judge.state words the items as the judge reads them. A Note is never serialized, so the judge
reads the texts and not the analyst's argument; nor are passages, which define neither text, nor a search or its
results, which no function here reads. An item is {"ref": the id it was read from, "content": what it says}. A
Judgment's key is taken over the state the judge reads, which names no store id, so the same check is the same
Judgment in any store.
"""

from __future__ import annotations

from harness import schema
from harness.derive.channels import price_fields
from harness.derive.engine import Inputs, Unavailable
from harness.venues import FORMS, NAMES, split

SERIALIZED = ("Proposition", "Market", "Metric")
RELATIONS = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "==": "exactly"}


class NotSerialized(TypeError):
    """An object the judge never reads."""


def _refuse(oid: str, type_name: str | None) -> None:
    if type_name == "Note":
        raise NotSerialized(f"{oid} is a Note: the analyst's reasons are never serialized, so the judge reads the "
                            f"texts and not the argument")
    raise NotSerialized(f"{oid} is a {type_name or 'thing the store does not have'}; the judge reads "
                        f"{', '.join(SERIALIZED)}")


def asof(ctx: Inputs) -> str | None:
    try:
        return ctx.object("portfolio")["stated"]["asof"]
    except Unavailable:
        return None


def date(ctx: Inputs) -> dict:
    """The date the check is asked as of: the store's as-of."""
    day = asof(ctx)
    return {"ref": "portfolio" if day else None, "content": {"date": day}}


def number(x: float) -> str:
    """A threshold as the analyst stated it, every digit and no exponent: 7000000000, 1.12999, 75878.21."""
    x = float(x)
    return str(int(x)) if x.is_integer() else repr(x)


def metric(ctx: Inputs, mid: str) -> dict:
    """A metric as the judge reads it: its name, unit, and who publishes it. A traded price's are its ticker's, fixed by
    the ticker, so a check reads nothing that moves with the price."""
    found = price_fields(ctx, mid.split(":", 2)[2]) if schema.PROXY_METRIC_RE.match(mid) else None
    if found is None:
        obj = ctx.object(mid)
        if obj["type"] != "Metric":
            _refuse(mid, obj["type"])
        found = obj["stated"]
    out = {"metric": found.get("name") or found.get("id") or mid.split(":", 1)[1], "unit": found.get("unit"),
           "published by": found.get("resolution_source"),
           "occurrence": True if found.get("kind") == "occurrence" else None}  # something that happens (ONTOLOGY.md 2.1)
    return {k: v for k, v in out.items() if v is not None}


JUDGED = {"hitting": "at any time in its window", "level_at_date": "on its window's end",
          "scheduled_decision": "at the scheduled decision on its window's end"}  # a proposition's kind, as it reads


def proposition_content(ctx: Inputs, pid: str) -> dict:
    """A proposition as the analyst wrote it: its title and text, when it is judged (at any time in its window, on its
    window's end, or at the scheduled decision then) with its window as stated, its level on its metric, and
    its resolution source."""
    found = ctx.object(pid)
    if found["type"] != "Proposition":
        _refuse(pid, found["type"])
    s = found["stated"]
    out = {"proposition": s["title"], "says": s["text"], "judged": JUDGED[s["kind"]]}
    if s.get("window_start"):
        out["window starts"] = s["window_start"]
    out["window ends"] = s["window_end"]
    if s.get("metric") and s.get("value") is not None and s.get("comparison") in RELATIONS:
        out["level"] = f"{RELATIONS[s['comparison']]} {number(s['value'])}"
        out["of"] = metric(ctx, s["metric"])
    elif s.get("metric") and s.get("comparison") == "occurs":  # the occurrence it is true on
        out["of"] = metric(ctx, s["metric"])
    if s.get("resolution_source"):
        out["resolved by"] = s["resolution_source"]
    return out


def proposition(ctx: Inputs, pid: str) -> dict:
    return {"ref": pid, "content": proposition_content(ctx, pid)}


def market_content(ctx: Inputs, mid: str) -> dict:
    """A market as its venue words it: the venue; its event's title, where it differs from the question; its question,
    rules, resolution source (for Kalshi, the settlement sources it lists for the market's event, or else its series',
    as market_listing reads them); its close date; and whether it is open on the as-of date."""
    venue = split(mid)[0]
    listing = ctx.value("market_listing", mid)["output"]
    question = listing.get("question")
    event = (FORMS[venue].event(ctx.payload(ctx.data("market", mid))) or {}).get("title")
    out = {"venue": NAMES.get(venue, venue),
           "event": event if event and event.strip() != (question or "").strip() else None,
           "market": question, "rules": listing.get("rules"), "resolved by": listing.get("resolution_source"),
           "closes": listing.get("close_time")}
    try:
        opened = ctx.value("market_price", mid)["output"].get("open")
    except Unavailable:
        opened = None
    if opened is not None:
        out["still open"] = opened
    return {k: v for k, v in out.items() if v is not None}


def market(ctx: Inputs, mid: str) -> dict:
    return {"ref": mid, "content": market_content(ctx, mid)}
