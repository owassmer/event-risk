"""An event's worlds (MATH.md 1.1, 1.3 to 1.5): the weights today's prices put on them, the factors' moves against the
baseline with the unstated ones filled in compounded moves, every price placed against today, and the row read from
the worlds. Pure arithmetic over plain numbers: the impact (impact.py) reads the store and calls it.

A world is `{"id", "side", "p"}`: its id ("yes" or "no" for a side with no ways), the side it lies on, and its
probability. Every function keys what it gives by world id.
"""

from __future__ import annotations

import math

import numpy as np

TOL = 1e-12


class Unplaceable(Exception):
    """A price at zero in every world today's prices carry: no scale brings its worlds to today's price (1.10)."""


class Unholdable(Exception):
    """Weights stated for outcomes that no weights summing to 1 give, with the reason."""


def weights(worlds: list[dict], parents: dict[str, dict] | None = None,
            stated: dict[str, float] | None = None) -> dict[str, float]:
    """The weight today's prices put on each world (1.5): its probability where nothing is stated. A weight stated
    for an outcome, the event ("yes", its No side then the rest) or a way, stands. Every other world takes its share
    of what its nearest outcome above it with a stated weight, or the whole at 1, leaves beyond the stated outcomes
    under it, as our probabilities divide that share; where they put none of it on any, evenly. So an outcome with
    no stated weight has its worlds' sum. `parents` are the sides and ways that have ways, each with its `parent`;
    Unholdable where stated weights under an outcome come to more than its own, or, filling every world under it,
    to less."""
    stated = dict(stated or {})
    if not stated:
        return {w["id"]: w["p"] for w in worlds}
    if "yes" in stated:
        stated.setdefault("no", 1.0 - stated["yes"])
    up = {w["id"]: w.get("parent") for w in worlds}
    up.update({n: t.get("parent") for n, t in (parents or {}).items()})
    for side in ("yes", "no"):
        up[side] = "the whole"
    stated["the whole"] = 1.0

    def above(n: str) -> str:
        """The nearest outcome above n with a stated weight."""
        x = up.get(n)
        while x not in stated:
            x = up.get(x) if x in up else "the whole"
        return x

    under: dict[str, dict] = {a: {"stated": [], "worlds": []} for a in stated}
    for n in stated:
        if n != "the whole":
            under[above(n)]["stated"].append(n)
    for w in worlds:
        if w["id"] not in stated:
            under[above(w["id"])]["worlds"].append(w)
    out = {w["id"]: stated[w["id"]] for w in worlds if w["id"] in stated}
    for a, got in under.items():
        left = stated[a] - sum(stated[n] for n in got["stated"])
        if left < -1e-9 or (not got["worlds"] and abs(left) > 1e-9 and got["stated"]):
            raise Unholdable(f"the weights stated under {a} come to {sum(stated[n] for n in got['stated']):.6g}, "
                             f"and {a}'s is {stated[a]:.6g}: the outcomes under one make it up")
        mass = sum(w["p"] for w in got["worlds"])
        for w in got["worlds"]:
            out[w["id"]] = max(left, 0.0) * (w["p"] / mass if mass > TOL else 1.0 / len(got["worlds"]))
    return out


def covariance(ids: list[str], coefficients: dict, variances: dict) -> np.ndarray:
    """The factors' covariance over completed moves, L diag(variance) L', from the orthogonalization (3.2)."""
    at = {c: i for i, c in enumerate(ids)}
    lower = np.eye(len(ids))
    for c in ids:
        for j, beta in coefficients[c].items():
            lower[at[c], at[j]] = beta
    return lower @ np.diag([variances[c] for c in ids]) @ lower.T


def orthogonal(ids: list[str], coefficients: dict, raw: dict[str, float]) -> dict[str, float]:
    """The factors' moves in the orthogonalized basis, in factor order: each move less its regression on the earlier
    factors' components (1.3)."""
    out: dict[str, float] = {}
    for c in ids:
        out[c] = raw[c] - sum(beta * out[j] for j, beta in coefficients[c].items())
    return out


def fill(ids: list[str], cov: np.ndarray, stated: dict[str, float], owned: dict[str, float] | None = None,
         coefficients: dict | None = None) -> dict[str, float]:
    """Every factor's compounded move against the baseline, ln(1 + x), in one world (1.3): a set factor's as stated,
    and each other's as it ordinarily moves with the set ones, ln(1 + x_U) = cov(U, S) cov(S, S)^-1 ln(1 + x_S). The
    inverse is the pseudo-inverse, so a set factor whose own part has no variance implies nothing for the others. A
    factor stated at -1 (-inf) has no compounded move to fill from: it stands at zero and the others are filled
    from the rest.

    A factor whose own part is set (`owned`, its compounded own part beyond the factors before it, z_c) sets one more
    condition on the same moves: z = T^-1 ln(1 + x), with T the orthogonalization's (`coefficients`). Every factor not
    set whole then takes its mean given all the conditions together, cov A' (A cov A')^-1 b, which with whole moves
    alone is the formula above; so a factor before it moves only as it ordinarily would with what is set, and one
    after it by its coefficients on the own parts there."""
    cov = np.asarray(cov, dtype=float)
    finite = [i for i, c in enumerate(ids) if c in stated and math.isfinite(stated[c])]
    unset = [i for i, c in enumerate(ids) if c not in stated]
    out = dict(stated)
    if owned:
        at = {c: i for i, c in enumerate(ids)}
        lower = np.eye(len(ids))
        for c in ids:
            for j, beta in (coefficients or {}).get(c, {}).items():
                lower[at[c], at[j]] = beta
        inverse = np.linalg.inv(lower)
        rows = [np.eye(len(ids))[i] for i in finite] + [inverse[at[c]] for c in owned]
        values = [stated[ids[i]] for i in finite] + [owned[c] for c in owned]
        A = np.array(rows)
        moved = cov @ A.T @ np.linalg.pinv(A @ cov @ A.T) @ np.array(values)
        out = {**{c: float(moved[i]) for i, c in enumerate(ids)}, **stated}
        return {c: out[c] for c in ids}
    if unset:
        filled = (cov[np.ix_(unset, finite)] @ np.linalg.pinv(cov[np.ix_(finite, finite)])
                  @ np.array([stated[ids[i]] for i in finite])) if finite else np.zeros(len(unset))
        out.update({ids[i]: float(x) for i, x in zip(unset, filled)})
    return {c: out[c] for c in ids}


def place(ratios: dict[str, float], q: dict[str, float]) -> dict[str, float]:
    """A price's move against today in each world (1.5, 1.6). `ratios` are its levels in each world against any one
    level: never below zero there, they are scaled so that at the weights they come to today's price. For a price
    whose shocks against the baseline are s_w, the ratios are 1 + s_w and this is L_base = c / sum q (1 + s); for a
    price that moves in a straight line with the placed prices it changes nothing but where the floor holds. Raises
    Unplaceable where the price is zero in every world today's prices carry."""
    held = {w: max(x, 0.0) for w, x in ratios.items()}
    total = sum(q[w] * held[w] for w in held)
    if total <= TOL:
        raise Unplaceable
    return {w: held[w] / total - 1.0 for w in held}


def points(x: float, duration: float) -> float | None:
    """A factor quoted as the yield its proxy pays, read in that yield (MATH.md 1.3, 1.5, 3.2): its proxy's price move x
    as the yield's move in percentage points, -100 ln(1 + x) / duration; none where the price stands at zero, which
    leaves the yield no level."""
    return -100.0 * math.log1p(x) / duration if x > -1.0 else None


def in_its_terms(stated: dict, x: float) -> float | None:
    """A factor's move in the terms its shocks are stated in (1.3): its proxy's price move as a fraction, or, for a
    factor quoted as a yield (its stated `duration`), the yield's move in points."""
    duration = stated.get("duration")
    return x if duration is None else points(x, duration)


def side_mean(worlds: list[dict], values: dict[str, float], side: str) -> float:
    """The mean of a figure over one side's worlds, each at its probability (1.1); a side with no probability, at
    its worlds' plain mean."""
    on = [w for w in worlds if w["side"] == side]
    mass = sum(w["p"] for w in on)
    if mass > TOL:
        return sum(w["p"] * values[w["id"]] for w in on) / mass
    return sum(values[w["id"]] for w in on) / len(on)


def row(worlds: list[dict], A: dict[str, float]) -> dict:
    """A row's figures, read from its worlds' P&L (1.1): Yes and No, each the mean of its side's worlds at their
    probabilities; the full impact, their difference; the expected value; the volatility, the standard deviation of
    the P&L over the worlds; each side's spread, the standard deviation of its worlds' P&L given the side, 0 for a
    side that is one world; and the worst outcome, the lowest P&L among the worlds that can happen, those with a
    probability above zero, with its world and that world's probability. The variance is
    p (1 - p) D^2 + p spread_yes^2 + (1 - p) spread_no^2."""
    p = {w["id"]: w["p"] for w in worlds}
    mu = sum(p[w] * A[w] for w in p)
    yes, no = side_mean(worlds, A, "yes"), side_mean(worlds, A, "no")
    worst = min((w for w in p if p[w] > TOL), key=lambda w: (A[w], w))
    return {"yes": yes, "no": no, "full_impact": yes - no, "expected": mu,
            "volatility": math.sqrt(max(sum(p[w] * (A[w] - mu) ** 2 for w in p), 0.0)),
            "spread": {s: spread(worlds, A, s) for s in ("yes", "no")},
            "worst_outcome": {"pnl": A[worst], "world": worst, "probability": p[worst]}}


def spread(worlds: list[dict], A: dict[str, float], side: str) -> float:
    """The standard deviation of a side's worlds' P&L given the side (1.1); 0 for a side that is one world, or that
    has no probability."""
    on = [w for w in worlds if w["side"] == side]
    mass = sum(w["p"] for w in on)
    if len(on) < 2 or mass <= TOL:
        return 0.0
    mean = sum(w["p"] * A[w["id"]] for w in on) / mass
    return math.sqrt(max(sum(w["p"] * (A[w["id"]] - mean) ** 2 for w in on) / mass, 0.0))


def tree(worlds: list[dict], parents: dict[str, dict], A: dict[str, float], q: dict[str, float]) -> list[dict]:
    """The outcomes as a tree (1.1), from the event's two sides down, each parent's ways in their order: each outcome
    with its parent, its side, its title, its probability, the weight today's prices put on it, and the portfolio's P&L
    there. A world's are its own; a parent's probability and weight are its worlds' summed, and its P&L the average of
    its worlds' at their probabilities, or their plain mean where it has no probability. `parents` are the sides and
    ways that have ways, each with its `ways` in order."""
    leaf = {w["id"]: w for w in worlds}

    def under(n: str) -> list[str]:
        if n in leaf:
            return [n]
        return [x for k in parents[n]["ways"] for x in under(k)]

    out: list[dict] = []

    def visit(n: str, parent: str | None) -> None:
        ws = under(n)
        mass = sum(leaf[w]["p"] for w in ws)
        node = leaf.get(n) or parents[n]
        a = sum(leaf[w]["p"] * A[w] for w in ws) / mass if mass > TOL else sum(A[w] for w in ws) / len(ws)
        out.append({"id": n, "parent": parent, "side": node["side"], **({"title": node["title"]}
                                                                        if node.get("title") else {}),
                    "p": mass, "q": sum(q[w] for w in ws), "A": a,
                    **({"ways": list(parents[n]["ways"])} if n in parents else {})})
        for k in (parents[n]["ways"] if n in parents else []):
            visit(k, n)

    for side in ("yes", "no"):
        if side in leaf or side in parents:
            visit(side, None)
    return out
