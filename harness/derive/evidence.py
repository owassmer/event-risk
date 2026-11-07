"""The evidence the judge reads: the one serializer.

Every check's evidence is built here, in plain form: the date the store speaks as of; propositions as the analyst
wrote them; markets with their question, rules, resolution source, close date, and whether they are still open on
that date; and passages as quoted from their pages, with the page's address and date. A Note is never serialized, so
the judge reads the texts and not the analyst's argument; nor is a search or its results, which no function here
reads. An item is {"ref": the id it was read from, "content": what the judge reads}. A Judgment's key is taken over
the content alone, which names no store id, so the same check is the same Judgment in any store.
"""

from __future__ import annotations

from harness import schema
from harness.derive.engine import Inputs, Unavailable

SERIALIZED = ("Proposition", "Market", "Metric", "Source")
RELATIONS = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "==": "exactly"}
WHEN = {"hitting": "at any time in the window", "level_at_date": "on the window's last day",
        "scheduled_decision": "at the scheduled decision in the window"}


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


def metric(ctx: Inputs, mid: str) -> dict:
    """A metric as the judge reads it: its name, unit, current level with its date, and who publishes it. A traded
    price is its level as the store first read it on the as-of, so a check is not asked again as the price moves
    within the day."""
    if schema.PROXY_METRIC_RE.match(mid):
        found = ctx.value("proxy_price", mid)["output"]
        level, dated = found.get("first_value", found.get("current_value")), found.get("first_asof", found.get("asof"))
    else:
        obj = ctx.object(mid)
        if obj["type"] != "Metric":
            _refuse(mid, obj["type"])
        found = obj["stated"]
        level, dated = found.get("current_value"), found.get("asof")
    out = {"metric": found.get("id") or mid.split(":", 1)[1], "unit": found.get("unit"),
           "level": level, "level as of": dated, "published by": found.get("resolution_source")}
    return {k: v for k, v in out.items() if v is not None}


def proposition_content(ctx: Inputs, pid: str) -> dict:
    """A proposition as the analyst wrote it: its title and text, its window, when it is read, its level on its
    metric, and its resolution source."""
    found = ctx.object(pid)
    if found["type"] != "Proposition":
        _refuse(pid, found["type"])
    s = found["stated"]
    start = s.get("window_start") or asof(ctx)
    out = {"proposition": s["title"], "says": s["text"],
           "window": f"{start} to {s['window_end']}" if start else f"until {s['window_end']}",
           "read": WHEN[s["kind"]]}
    if s.get("metric") and s.get("value") is not None and s.get("relation") in RELATIONS:
        out["level"] = f"{RELATIONS[s['relation']]} {s['value']:g}"
        out["of"] = metric(ctx, s["metric"])
    if s.get("resolution_source"):
        out["resolved by"] = s["resolution_source"]
    return out


def proposition(ctx: Inputs, pid: str) -> dict:
    return {"ref": pid, "content": proposition_content(ctx, pid)}


def market_content(ctx: Inputs, mid: str) -> dict:
    """A market's own words: its question, rules, resolution source, close date, and whether it is still open on the
    as-of date."""
    listing = ctx.value("market_listing", mid)["output"]
    out = {"market": listing.get("question"), "rules": listing.get("rules"),
           "resolved by": listing.get("resolution_source"), "closes": listing.get("close_time")}
    try:
        opened = ctx.value("market_price", mid)["output"].get("open")
    except Unavailable:
        opened = None
    if opened is not None:
        out["still open"] = opened
    return {k: v for k, v in out.items() if v is not None}


def market(ctx: Inputs, mid: str) -> dict:
    return {"ref": mid, "content": market_content(ctx, mid)}


def page(ctx: Inputs, sid: str, passages: list[str]) -> dict:
    """Passages as the judge reads them: the words quoted from the page, with its address and the date it speaks as
    of. The whole page stays in the store."""
    s = ctx.source(sid)
    content = {"page": s["url"], "dated": s["dated"]}
    content["passages" if len(passages) != 1 else "passage"] = passages if len(passages) != 1 else passages[0]
    return {"ref": sid, "content": content}


def passages(ctx: Inputs, oid: str) -> list[dict]:
    """The passages linked to a proposition as evidence, one item per evidence link."""
    return [page(ctx, link["to"], link["stated"].get("passages") or []) for link in ctx.links(oid, "evidence")]
