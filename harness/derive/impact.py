"""Impact: what a consequence sets, and what a watched event does to the portfolio (MATH.md, sections 1 to 3).

Consequences. A consequence links an event E to a metric X that tracks a channel or a name, with one figure the analyst
states and what it means: the gap, E[X given E] - E[X given not E] at the end of E's window; or, for a traded metric,
X's level then if E happens, or if it does not. A metric no market prices states the gap. For a traded metric at its
current level c and E's point p, the pricing identity converts a level to the gap, gap = (L_yes - c) / (1 - p) or
gap = (c - L_no) / p, and places both worlds, L_yes = c + (1 - p) gap and L_no = c - p gap. A level the identity cannot
move, the level if it happens at p = 1 or if it does not at p = 0, gives no gap, and the reason says to state the gap.

The vector. The gap, through the metric's tracks link, is the raw full swing of what it tracks: divided by c for
price, multiplied by -duration / 100 for yield(duration). A channel's raw swing enters the orthogonalized basis in
channel order: its component is the raw swing minus its coefficients on the earlier channels times their components,
the part they imply. A channel no consequence sets has component 0, so it moves with the earlier channels by its
coefficients on them: each channel's raw move is its component plus its coefficients times the earlier components. A
consequence on a name's price sets the name's whole move: its d_i is the move less b_i . u_E. Then D = g . u_E + sum_i e_i d_i, pnl_yes = (1 - p) D, pnl_no = -p D,
emove = 2 p (1 - p) |D|, worst is the more negative of pnl_yes and pnl_no.

Each holding moves with its own betas (channels.moves_with), and a company's own shock reaches every dollar of it,
through a fund as directly. Each name's share of D is what is held of it directly at its own betas, e_i,direct
(b_i . u_E); what each fund holds of it at the fund's, e_i,f (b_f . u_E), the fund's move split across its holdings by
weight; and its own shock on every dollar of it, e_i d_i. A fund's residual and its cash are its holdings by the same
split. Each holding's share is its dollars at the betas they move with, and each company's own shock on the dollars
it holds of that company, which add to it; and by name, the dollars it reaches at each name times the move of those
dollars. Each share comes to
(1 - p) times itself if the event happens and -p times itself if it does not, by the same identity, so the names' and
the holdings' dollars in each world sum to the row's.
"""

from __future__ import annotations

from harness import schema
from harness.derive.channels import moves_with, ordered_channels
from harness.derive.engine import Derivation, Inputs, Unavailable
from harness.derive.portfolio import pseudo, ticker_of

TOL = 1e-9


# ---------------------------------------------------------------- what the impact reads of a metric


def tracked(ctx: Inputs, mid: str, priced: set[str] = frozenset()) -> list[dict]:
    """The channels and names a metric tracks, with each conversion: its stated tracks links; for a price metric, the
    channels whose proxy it is, and the name whose price it is (`priced`: the portfolio's names whose e is not
    zero)."""
    out = [{"target": link["to"], "conversion": link["stated"]["conversion"]} for link in ctx.links(mid, "tracks")]
    have = {x["target"] for x in out}
    m = schema.PROXY_METRIC_RE.match(mid)
    if m:
        out += [{"target": c["id"], "conversion": "price"} for c in ordered_channels(ctx)
                if c["stated"]["proxy_ticker"] == m.group(1) and c["id"] not in have]
        nid = schema.name_id(m.group(1))
        if nid in priced and nid not in have:
            out.append({"target": nid, "conversion": "price"})
    return sorted(out, key=lambda x: x["target"])


def level(ctx: Inputs, mid: str) -> dict:
    """A metric's current level, its date, and whether a market prices it: a price the store derives from its latest
    mark, or the level the analyst stated from a page."""
    if schema.PROXY_METRIC_RE.match(mid):
        found = ctx.value("proxy_price", mid)
        out = found["output"]
        return {"c": out.get("current_value"), "asof": out.get("asof"), "traded": True, "basis": found["basis"]}
    stated = ctx.object(mid)["stated"]
    return {"c": stated.get("current_value"), "asof": stated.get("asof"), "traded": bool(stated.get("traded"))}


def _no_level(mid: str) -> str:
    return (f"{mid} has no current level, which its conversion starts from: state it from a page you fetch, "
            f"edit(\"{mid}\", \"current_value\", value, source_id), with its asof")


def _point(ctx: Inputs, pid: str) -> dict:
    """The event's point with its basis, or the reason it has none."""
    try:
        found = ctx.value("point", pid)
        return {"p": found["output"]["p"], "basis": found["basis"]}
    except Unavailable as e:
        return {"p": None, "reason": str(e)}


# ---------------------------------------------------------------- consequence: the stated figure


def convert(figure: float, meaning: str, c: float | None, p: float | None) -> dict:
    """The stated figure as the gap, or the reason it gives none."""
    if meaning == "gap":
        return {"gap": figure}
    if c is None:
        return {"reason": "a level converts to the gap from the metric's current level, and it has none"}
    if p is None:
        return {"reason": "a level converts to the gap at the event's point, and it has none"}
    if meaning == "level_if_happens":
        if p >= 1.0 - TOL:
            return {"reason": "at a point of 1 the level if it happens is today's level whatever the gap: state the "
                              "gap"}
        return {"gap": (figure - c) / (1.0 - p)}
    if p <= TOL:
        return {"reason": "at a point of 0 the level if it does not happen is today's level whatever the gap: state "
                          "the gap"}
    return {"gap": (c - figure) / p}


def _consequence_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    pid, mid = link["from"], link["to"]
    found = level(ctx, mid)
    point = _point(ctx, pid) if found["traded"] or link["stated"]["meaning"] != "gap" else {"p": None}
    return {"link": lid, "event": pid, "metric": mid, "figure": link["stated"]["value"]["value"],
            "meaning": link["stated"]["meaning"], **found, "point": point}


def _consequence_compute(m: dict, load) -> dict:
    c, p = m["c"], m["point"]["p"]
    out = {"metric": m["metric"], "figure": m["figure"], "meaning": m["meaning"], "c": c, "p": p,
           "traded": m["traded"], **convert(m["figure"], m["meaning"], c, p)}
    if "reason" in out:
        reason = out.pop("reason")
        return {**out, "unavailable": dict.fromkeys(("gap", "L_yes", "L_no"), reason)}
    gap = out["gap"]
    if not m["traded"]:
        why = f"{m['metric']} is not traded: a metric no market prices shows its gap alone"
        return {**out, "unavailable": {"L_yes": why, "L_no": why}}
    if c is None or p is None:
        why = _no_level(m["metric"]) if c is None else f"the event has no point: {m['point'].get('reason')}"
        return {**out, "unavailable": {"L_yes": why, "L_no": why}}
    out.update(L_yes=c + (1.0 - p) * gap, L_no=c - p * gap)
    return out


CONSEQUENCE = Derivation("consequence", 3, "consequence", _consequence_inputs, _consequence_compute,
                         fields=("gap", "L_yes", "L_no"))


def to_swing(gap: float, conversion: str, c: float | None) -> float:
    """A metric's gap as the raw full swing of what it tracks: a return for price, -duration/100 per point for
    yield."""
    if conversion == "price":
        if not c:
            raise ValueError("a price at 0 has no return")
        return gap / c
    duration = float(conversion[len("yield("):-1])
    return gap * -duration / 100.0


# ---------------------------------------------------------------- impact on a watched proposition


def consequences(ctx: Inputs, pid: str) -> list[dict]:
    return sorted(ctx.links(pid, "consequence"), key=lambda link: link["to"])


def _impact_inputs(ctx: Inputs, pid: str) -> dict:
    point = ctx.value("point", pid)
    channels = [c["id"] for c in ordered_channels(ctx)]
    lookthrough = ctx.value("lookthrough", "portfolio", ref=True)
    # each name's price tracks it when its e is not zero: e lists the names the portfolio holds dollars in, and an
    # expanded fund, whose e is 0, is not among them
    priced = {n for n, v in ctx.payload(lookthrough)["e"].items() if v and not pseudo(ticker_of(n))}
    found = []
    for link in consequences(ctx, pid):
        mid, sets = link["to"], tracked(ctx, link["to"], priced)
        if not sets:
            found.append({"link": link["id"], "metric": mid, "sets": [],
                          "note": f"{mid} tracks no channel and no name, so its gap sets none"})
            continue
        got = ctx.value("consequence", link["id"])
        missing = got["output"].get("unavailable") or {}
        if "gap" in missing:
            raise Unavailable(f"{link['id']} gives no gap: {missing['gap']}")
        c = got["output"]["c"]
        if c is None and any(s["conversion"] == "price" for s in sets):
            raise Unavailable(_no_level(mid))
        found.append({"link": link["id"], "metric": mid, "gap": got["output"]["gap"], "c": c, "basis": got["basis"],
                      "sets": [{**s, "coefficients": ctx.value("orthogonalize", s["target"])["output"]
                                ["coefficients"]} if not s["target"].startswith("name:") else s for s in sets]})
    if not found:
        raise Unavailable(f"{pid} has no consequence: its swing comes from the figures of the metrics it moves")
    if not any(c["sets"] for c in found):
        raise Unavailable("no consequence of the event sets a channel or a name: " +
                          "; ".join(c["note"] for c in found if not c["sets"]))
    holds = {h["id"]: h["to"] for h in ctx.links("portfolio", "holds")}
    coefficients = {c: ctx.value("orthogonalize", c)["output"]["coefficients"] for c in channels}
    return {"event": pid, "p": point["output"]["p"], "point_basis": point["basis"], "channels": channels,
            "coefficients": coefficients,
            "g": ctx.value("g", "portfolio")["output"]["g"], "holds": holds,
            "lookthrough": lookthrough, "betas": ctx.value("betas", "portfolio", ref=True), "consequences": found}


def _impact_compute(m: dict, load) -> dict:
    p, ids, g = m["p"], m["channels"], m["g"]
    raw, coefficients, shown, named, setter = {}, {}, {}, {}, {}
    for c in m["consequences"]:
        if not c["sets"]:
            shown[c["metric"]] = {"link": c["link"], "sets": [], "reason": c["note"]}  # it sets nothing, and says why
            continue
        shown[c["metric"]] = {"link": c["link"], "gap": c["gap"], "c": c["c"]}
        for s in c["sets"]:
            try:
                converted = to_swing(c["gap"], s["conversion"], c["c"])
            except ValueError as e:
                return _unavailable(f"{c['metric']}: {e}")
            setter[s["target"]] = c["metric"]
            if s["target"].startswith("name:"):
                named[s["target"]] = converted  # the name's whole move
            else:
                raw[s["target"]] = converted
                coefficients[s["target"]] = s["coefficients"]
    u_e = {}
    for c in ids:  # a set channel's raw swing less its regression on the earlier components; any other, 0
        u_e[c] = raw[c] - sum(beta * u_e[j] for j, beta in coefficients[c].items()) if c in raw else 0.0
    # each channel's raw move: a set channel's raw swing, and one no consequence sets what the earlier imply
    raw_moves = {c: u_e[c] + sum(beta * u_e[j] for j, beta in m["coefficients"][c].items()) for c in ids}
    looked = load(m["lookthrough"])
    e = looked["e"]
    fitted = load(m["betas"])
    along = lambda b: sum(b.get(c, 0.0) * u_e[c] for c in ids)  # noqa: E731  a move at betas b
    b_of = lambda nid: (fitted["objects"].get(nid) or {}).get("fields", {}).get("b") or {}  # noqa: E731
    d_i = {n: move - along(b_of(n)) for n, move in named.items()}
    for metric, found in shown.items():  # what each set channel and name is, beside its raw swing
        if "reason" in found:
            continue
        found["sets"] = [{"channel": c, "raw": raw[c], "implied": raw[c] - u_e[c], "component": u_e[c]}
                         for c in ids if c in raw and setter.get(c) == metric] + \
                        [{"name": n, "raw": mv, "betas": mv - d_i[n], "d": d_i[n]}
                         for n, mv in sorted(named.items()) if setter.get(n) == metric]
    contributions = {c: g.get(c, 0.0) * u_e[c] for c in ids}
    own = {n: e.get(n, 0.0) * d for n, d in d_i.items()}  # each named name's own term, e_i d_i
    swing = sum(contributions.values()) + sum(own.values())
    pnl_yes, pnl_no = (1.0 - p) * swing, -p * swing
    primary = max(ids, key=lambda c: abs(contributions[c]))
    by_holding, shares = _shares(looked, fitted, along, d_i, p, m["holds"])
    shared = {s["name"] for s in shares}
    moves = {n: along(b_of(n)) + d_i.get(n, 0.0) for n in e if n in shared}  # each name's own move, held directly
    value = sum(e.values())  # V, what the portfolio is worth: the dollars at every name
    return {"u_E": u_e, "raw": raw_moves, "D": swing, "pnl_yes": pnl_yes, "pnl_no": pnl_no,
            "emove": 2.0 * p * (1.0 - p) * abs(swing), "worst": min(pnl_yes, pnl_no), "V": value,
            "share_yes": pnl_yes / value if value else None, "share_no": pnl_no / value if value else None,
            "primary_channel": primary, "contributions": contributions, "own": own, "by_name": shares,
            "moves": moves, "by_holding": by_holding, "p": p, "consequences": shown, "consequence_set": sorted(raw),
            "names_set": sorted(named), "d": d_i}


def _shares(looked: dict, fitted: dict, along, d_i: dict, p: float, holds: dict) -> tuple[list[dict], list[dict]]:
    """Each holding's share of D and each name's, from the dollars each holding reaches at each name and the funds
    they pass through: the dollars at the betas they move with (channels.moves_with), and each company's own shock,
    d_i, on every dollar of it."""
    direct: dict[str, float] = {}
    through: dict[str, dict[str, float]] = {}
    by_holding = []
    for hid, reaching in looked.get("paths", {}).items():
        reached: dict[str, list[float]] = {}  # each name's dollars through the holding, and their share of D
        held = at_betas = 0.0
        for x in reaching:
            n, dollars = x["name"], x["dollars"]
            part = dollars * along(moves_with(x["funds"], n, fitted))  # the dollars at the betas they move with
            if x["funds"]:
                fund = schema.name_id(x["funds"][0])
                through.setdefault(n, {})[fund] = through.get(n, {}).get(fund, 0.0) + part
            else:
                direct[n] = direct.get(n, 0.0) + part
            held += dollars
            at_betas += part
            got = reached.setdefault(n, [0.0, 0.0])
            got[0] += dollars
            got[1] += part + dollars * d_i.get(n, 0.0)  # and the company's own shock on every dollar of it
        names = sorted(({"name": n, "D": d, "move": d / dollars}
                        for n, (dollars, d) in reached.items() if dollars and d), key=lambda x: (-abs(x["D"]), x["name"]))
        # The holding's move: its dollars at the betas they move with, then each company's own shock on its dollars
        # at that company, which adds to it.
        own_h = sorted(({"name": n, "dollars": reached[n][0], "d": d_i[n], "D": reached[n][0] * d_i[n]}
                        for n in d_i if reached.get(n, [0.0])[0] and d_i[n]), key=lambda x: (-abs(x["D"]), x["name"]))
        d_h = at_betas + sum(x["D"] for x in own_h)
        by_holding.append({"holding": hid, "name": holds.get(hid), "D": d_h, "if_yes": (1.0 - p) * d_h,
                           "if_no": -p * d_h,
                           "at_betas": {"dollars": held, "move": at_betas / held if held else None, "D": at_betas},
                           "own": own_h, "names": names})
    shares = []
    for nid, dollars in looked["e"].items():
        parts = {"direct": direct.get(nid, 0.0), "through": through.get(nid, {}), "own": dollars * d_i.get(nid, 0.0)}
        share = parts["direct"] + sum(parts["through"].values()) + parts["own"]
        if dollars and share:  # the name's share of D, its parts, and what it comes to in each world
            shares.append({"name": nid, "D": share, "if_yes": (1.0 - p) * share, "if_no": -p * share, "parts": parts})
    shares.sort(key=lambda x: (-abs(x["D"]), x["name"]))
    return by_holding, shares


IMPACT_FIELDS = ("u_E", "primary_channel", "D", "by_name", "by_holding", "pnl_yes", "pnl_no", "emove", "worst")


def _unavailable(reason: str) -> dict:
    return {"unavailable": dict.fromkeys(IMPACT_FIELDS, reason)}


IMPACT = Derivation("impact", 12, "Proposition", _impact_inputs, _impact_compute, fields=IMPACT_FIELDS,
                    applies=lambda store, pid: store.watched(pid))


# ---------------------------------------------------------------- what a consequence sets


def _sets_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    pid = link["from"]
    if not ctx.watched(pid):
        raise Unavailable(f"{pid} is not watched: what its consequences set is part of its impact, which the store "
                          f"computes for an event the portfolio watches")
    return {"link": lid, "metric": link["to"], "impact": ctx.value("impact", pid, ref=True)}


def _sets_compute(m: dict, load) -> dict:
    """What the consequence sets through its metric's tracks link, as the event's impact computed it: a channel's raw
    swing, the part the earlier channels imply, and its component, or a name's whole move, the part its betas give,
    and its d. Nothing is computed here."""
    impact = load(m["impact"])
    if "consequences" not in impact:  # the impact has no value, and says why
        return {"unavailable": {"sets": next(iter(impact["unavailable"].values()))}}
    found = impact["consequences"][m["metric"]]  # one consequence links an event to a metric
    if "reason" in found:
        return {"unavailable": {"sets": found["reason"]}}
    return {"sets": found["sets"]}


SETS = Derivation("sets", 1, "consequence", _sets_inputs, _sets_compute, fields=("sets",))

DERIVATIONS = (CONSEQUENCE, IMPACT, SETS)
