"""The breakdown's logic: which propositions imply which, and the rules of the point.

Implication follows the analyst's links: A `implies` B; a composite of `and` or `then` implies each of its
components; each component of `or` implies its composite. It is transitive, so a proposition implies whatever a
chain of these reaches.

A derivation names the propositions the point reads and states the conditional chances its rule needs. Which rule it
is follows from what it reads, what each chance is of and given, and the links between the propositions: `match`
finds the one row of the table it fits, or says why it fits none. `evaluate` is the row's arithmetic, each input at
its own point.

| Row | E reads | Point | Stated |
|---|---|---|---|
| narrower | B, which E implies | P(E given B) * p_B | P(E given B) |
| broader | A, which implies E | p_A + P(E given not A) * (1 - p_A) | P(E given not A) |
| between | A and B, A implying E and E implying B | p_A + P(E given B and not A) * (p_B - p_A) | P(E given B and not A) |
| driver | D, which neither implies E nor is implied by it | p_D * P(E given D) + (1 - p_D) * P(E given not D) | both |
| part | B, where E = B and C, or B then C | p_B * P(C given B) | P(C given B), C following B for then |
| either | B and C, where E = B or C | p_B + p_C - p_B * P(C given B) | P(C given B) |
| other | B, where E = B or C | p_B + (1 - p_B) * P(C given not B) | P(C given not B) |
"""

from __future__ import annotations

from collections import deque
from typing import Callable

ReadLinks = Callable[[str], list[dict]]  # a proposition's links, both directions

ROWS = {
    "narrower": "P(E given B) × p_B, for B, which E implies",
    "broader": "p_A + P(E given not A) × (1 − p_A), for A, which implies E",
    "between": "p_A + P(E given B and not A) × (p_B − p_A), for A implying E and E implying B",
    "driver": "p_D × P(E given D) + (1 − p_D) × P(E given not D), for D, which neither implies E nor is implied by it",
    "part": "p_B × P(C given B), for E = B and C, or E = B then C",
    "either": "p_B + p_C − p_B × P(C given B), for E = B or C",
    "other": "p_B + (1 − p_B) × P(C given not B), for E = B or C",
}


class NoRule(ValueError):
    """A derivation that fits no row of the table, with the reason."""


# ---------------------------------------------------------------- implication


def successors(read: ReadLinks, x: str) -> list[str]:
    """What x implies by one link: the B of x implies B; the components of x = B and C, or B then C; the composite of
    an or x is a component of."""
    out = []
    for link in read(x):
        rel = link["relation"]
        if link["from"] == x and rel in ("implies", "and", "then"):
            out.append(link["to"])
        elif link["to"] == x and rel == "or":
            out.append(link["from"])
    return out


def implies(read: ReadLinks, a: str, b: str) -> bool:
    """Whether a chain of the analyst's links says every world where a is true is one where b is."""
    if a == b:
        return False
    seen, queue = {a}, deque([a])
    while queue:
        for nxt in successors(read, queue.popleft()):
            if nxt == b:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def composition(read: ReadLinks, e: str) -> dict:
    """E's components by relation: and, or, and then by step."""
    out: dict = {"and": [], "or": [], "then": {}}
    for link in read(e):
        if link["from"] != e:
            continue
        if link["relation"] in ("and", "or"):
            out[link["relation"]].append(link["to"])
        elif link["relation"] == "then":
            out["then"][link["stated"]["step"]] = link["to"]
    return out


# ---------------------------------------------------------------- the rules


def _shape(c: dict) -> tuple:
    return c["of"], tuple(sorted(c["given"])), tuple(sorted(c["given_not"]))


def _said(e: str, c: dict) -> str:
    """A stated chance in words."""
    cond = [*c["given"], *(f"not {x}" for x in c["given_not"])]
    return f"P({'E' if c['of'] == e else c['of']} given {' and '.join(cond)})"


def match(read: ReadLinks, e: str, d: dict) -> dict:
    """The row of the table this derivation of e fits: {row, reads by role, chances by role}. NoRule otherwise."""
    reads, chances = d["reads"], d["chances"]
    shapes = [_shape(c) for c in chances]
    parts = composition(read, e)
    one = reads[0] if len(reads) == 1 else None
    if e in reads:
        raise NoRule(f"{e}'s derivation reads {e} itself; a point reads other propositions")
    if one and shapes == [(e, (one,), ())]:
        if implies(read, e, one):
            return {"row": "narrower", "roles": {"B": one}, "chances": {"E given B": chances[0]}}
        raise NoRule(f"{e} reads {one} with P(E given {one}), the rule for a proposition E implies, and no link says "
                     f"{e} implies {one}: link it, or for a proposition that neither implies E nor is implied by it "
                     f"state P(E given {one}) and P(E given not {one}) both")
    if one and shapes == [(e, (), (one,))]:
        if implies(read, one, e):
            return {"row": "broader", "roles": {"A": one}, "chances": {"E given not A": chances[0]}}
        raise NoRule(f"{e} reads {one} with P(E given not {one}), the rule for a proposition that implies E, and no "
                     f"link says {one} implies {e}")
    if len(reads) == 2 and len(shapes) == 1 and shapes[0][0] == e and len(shapes[0][1]) == 1 \
            and len(shapes[0][2]) == 1 and {shapes[0][1][0], shapes[0][2][0]} == set(reads):
        b, a = shapes[0][1][0], shapes[0][2][0]
        if implies(read, a, e) and implies(read, e, b):
            return {"row": "between", "roles": {"A": a, "B": b}, "chances": {"E given B and not A": chances[0]}}
        raise NoRule(f"{e} reads {a} and {b} with P(E given {b} and not {a}), the rule for {a} implying {e} and {e} "
                     f"implying {b}; the links say {a} implies {e}: {implies(read, a, e)}, {e} implies {b}: "
                     f"{implies(read, e, b)}")
    if one and sorted(shapes) == sorted([(e, (one,), ()), (e, (), (one,))]):
        if implies(read, one, e) or implies(read, e, one):
            way = f"{one} implies {e}" if implies(read, one, e) else f"{e} implies {one}"
            raise NoRule(f"{e} reads {one} through both of its outcomes, the rule for a proposition that neither "
                         f"implies E nor is implied by it, and the links say {way}: read it by the rule for that")
        given = next(c for c in chances if c["given"])
        given_not = next(c for c in chances if c["given_not"])
        return {"row": "driver", "roles": {"D": one}, "chances": {"E given D": given, "E given not D": given_not}}
    if one and len(shapes) == 1 and shapes[0][0] != e and shapes[0][1] == (one,) and not shapes[0][2]:
        c = shapes[0][0]
        if sorted(parts["and"]) == sorted([one, c]) or parts["then"] == {1: one, 2: c}:
            return {"row": "part", "roles": {"B": one, "C": c}, "chances": {"C given B": chances[0]}}
        if parts["then"] == {1: c, 2: one}:
            raise NoRule(f"{e} = {c} then {one}: the chance a then rule states is of the part that follows given the "
                         f"part that comes first, P({one} given {c}), reading {c}")
        raise NoRule(f"{e} reads {one} with P({c} given {one}), the rule for E = {one} and {c}, or {one} then {c}, "
                     f"and {e} is not linked as either")
    if len(reads) == 2 and len(shapes) == 1 and shapes[0][0] != e and len(shapes[0][1]) == 1 and not shapes[0][2] \
            and {shapes[0][0], shapes[0][1][0]} == set(reads):
        c, b = shapes[0][0], shapes[0][1][0]
        if sorted(parts["or"]) == sorted([b, c]):
            return {"row": "either", "roles": {"B": b, "C": c}, "chances": {"C given B": chances[0]}}
        raise NoRule(f"{e} reads {b} and {c} with P({c} given {b}), the rule for E = {b} or {c}, and {e} is not "
                     f"linked as their or")
    if one and len(shapes) == 1 and shapes[0][0] != e and not shapes[0][1] and shapes[0][2] == (one,):
        c = shapes[0][0]
        if sorted(parts["or"]) == sorted([one, c]):
            return {"row": "other", "roles": {"B": one, "C": c}, "chances": {"C given not B": chances[0]}}
        raise NoRule(f"{e} reads {one} with P({c} given not {one}), the rule for E = {one} or {c}, and {e} is not "
                     f"linked as their or")
    stated = ", ".join(_said(e, c) for c in chances)
    raise NoRule(f"{e}'s derivation reads {', '.join(reads)} and states {stated}, which fits no rule of the point "
                 f"(MATH.md): " + "; ".join(ROWS.values()))


def evaluate(found: dict, p: dict[str, float]) -> float:
    """The row's arithmetic, each read proposition at its own point `p`."""
    r, c = found["roles"], {k: v["value"] for k, v in found["chances"].items()}
    row = found["row"]
    if row == "narrower":
        return c["E given B"] * p[r["B"]]
    if row == "broader":
        return p[r["A"]] + c["E given not A"] * (1.0 - p[r["A"]])
    if row == "between":
        return p[r["A"]] + c["E given B and not A"] * (p[r["B"]] - p[r["A"]])
    if row == "driver":
        return p[r["D"]] * c["E given D"] + (1.0 - p[r["D"]]) * c["E given not D"]
    if row == "part":
        return p[r["B"]] * c["C given B"]
    if row == "either":
        return p[r["B"]] + p[r["C"]] - p[r["B"]] * c["C given B"]
    if row == "other":
        return p[r["B"]] + (1.0 - p[r["B"]]) * c["C given not B"]
    raise ValueError(f"no row {row}")
