"""A past episode supplies a world's factor co-movement (MATH.md 1.3, 1.8).

The episode changes the covariance used to complete unstated factor movements. Its
realized return is not a shock, its length is not a forecast horizon, and its
covariance is not substituted for the ordinary variation around a world.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from harness.derive import estimate, history, series
from harness.derive.engine import Derivation, Inputs, Refused, Unavailable
from harness.derive.portfolio import closes, dividend_inputs


def subject(start: str, end: str) -> str:
    return f"episode:{start}:{end}"


def _inputs(ctx: Inputs, key: str) -> dict:
    _, start, end = key.split(":")
    # The last close before the episode is needed for its first completed return.
    begin = (date.fromisoformat(start) - timedelta(days=14)).isoformat()
    window = (begin, end)
    channels = history.ordered_channels(ctx)
    if not channels:
        raise Unavailable("a past episode needs the factors whose co-movement it supplies")
    items = []
    for channel in channels:
        ticker = channel["stated"]["proxy"]
        kind = "hourly" if series.is_pair(ticker) else "home" if series.is_abroad(ticker) else "bars"
        ref = ctx.data(kind, ticker, covering=window)
        item = {"ticker": ticker, "kind": "pair" if kind == "hourly" else "abroad" if kind == "home" else "listing",
                "ref": ref}
        if kind == "bars":
            item.update(dividend_inputs(ctx, ticker, window))
        elif kind == "home":
            currency = (ctx.payload(ref) or {}).get("currency")
            if currency and currency != series.PRICE_CURRENCY:
                item.update(currency=currency, rate=ctx.data("hourly", series.dollar_pair(currency), covering=window))
        items.append(item)
    return {"window": [start, end], "channels": [c["id"] for c in channels], "series": items,
            "settings": estimate.SETTINGS.as_dict()}


def _compute(m: dict, load) -> dict:
    """The factors' covariance over the episode's sessions, by production's estimator (MATH.md 1.3, 3.2). An episode that
    a factor's proxy has no prices for, or over which the proxies share fewer than two sessions' returns, has no
    co-movement to give, and a statement naming it is refused with why."""
    start, end = m["window"]
    for item in m["series"]:
        found = sorted(closes(item, load))
        if not any(d <= start for d in found):
            begins = f"its prices begin {found[0]}" if found else "it has no prices then"
            raise Refused(f"the episode {start} to {end} reads how every factor's proxy moved over it, and "
                          f"{item['ticker']} has no close on or before {start}: {begins}")
    calendar = closes(m["series"][0], load)
    before = [d for d in calendar if d <= start]
    sessions = [max(before)] + sorted(d for d in calendar if start < d <= end)
    x = np.full((len(sessions) - 1, len(m["channels"])), np.nan)
    for j, item in enumerate(m["series"]):
        for a, b, r in history.returns(item, load, sessions)["dollar"]:
            if b == a + 1:
                x[a, j] = r
    ok = estimate.common(x)
    if int(ok.sum()) < 2:
        raise Refused(f"the episode {start} to {end} has {int(ok.sum())} session on which every factor's proxy has a "
                      f"return, and a co-movement is read over two or more")
    memory = m["settings"]["memory"]
    _, _, covariance = estimate.covariances(x, ok, memory)
    return {"window": m["window"], "channels": m["channels"], "sessions": int(ok.sum()),
            "covariance": covariance.tolist(), "memory": memory,
            "first": sessions[int(np.flatnonzero(ok)[0]) + 1],
            "last": sessions[int(np.flatnonzero(ok)[-1]) + 1]}


def for_worlds(ctx: Inputs, event: str, tree: dict) -> dict:
    """Each terminal world's nearest episode, inherited from its ways and event.

    `tree` is the outcomes derivation's output. The references retain both the
    stated date window and the computation of the exact covariance it supplies.
    """
    nodes = tree["nodes"]
    stated = {event: ctx.object(event)["stated"]}
    for node in nodes:
        if node.startswith("prop:"):
            stated[node] = ctx.object(node)["stated"]
    out = {}
    for world in tree["worlds"]:
        at = world
        found = None
        while at is not None:
            if at in stated and stated[at].get("episode"):
                found = (at, stated[at]["episode"])
                break
            at = nodes.get(at, {}).get("parent")
        if found is None and stated[event].get("episode"):
            found = (event, stated[event]["episode"])
        if found:
            owner, window = found
            # The schema stores the value of a basis-bearing field, as it does
            # priced_in; accept the explicit value wrapper in pure fixtures too.
            window = window.get("value", window)
            got = ctx.value("episode_covariance", subject(window["start"], window["end"]))
            out[world] = {"on": owner, "basis": got["basis"], **got["output"]}
    return out


EPISODE = Derivation("episode_covariance", 1, "Proposition", _inputs, _compute,
                     applies=lambda store, entity: False)
DERIVATIONS = (EPISODE,)
