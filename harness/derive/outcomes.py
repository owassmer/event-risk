"""An event's outcomes (MATH.md 1.1, 1.2): its sides and its ways, a tree, each with its probability.

An event's Yes side is the worlds in which it is true, and its No side the rest. A side is one world, or the ways the
analyst gives it, each a `way` link from the event stating its side; a way can have ways of its own, by `way` links
from it, which lie on its side. The ways under one parent exclude one another and together make it up. A watched
level's side can also be the bands its fields place on it (MATH.md 1.2): once something is stated of one of them as of a
way, or one is linked as a way, the side is all its bands, one set of ways, and a way within a band is linked from it.
The worlds are the outcomes with nothing under them, and one of them is the baseline the analyst names.

This is the one account of an event's outcomes (`sides`, `tree`, `event_of`): the row, the structure's ways, the joint
view and the store's checks on what is stated of a world each read it.

The event and its ways are propositions of one structure, and their probabilities are its reading (harness.derive.
structure): a way's chance given its parent is its share of it. Where the structure's statements cannot hold together,
or leave the event's probability or a way's open, the worlds have no probabilities, and `why` says which.
"""

from __future__ import annotations

import math

from harness.derive import structure
from harness.derive.engine import Derivation, Inputs


def parent_link(read_links, pid: str) -> dict | None:
    """The way link that makes pid one way its parent can turn out, or None where pid is no way."""
    found = [link for link in read_links(pid) if link["relation"] == "way" and link["to"] == pid]
    return found[0] if found else None


def root_of(read_links, pid: str) -> str:
    """The event at the top of pid's tree: pid itself where it is no way."""
    seen = {pid}
    while True:
        link = parent_link(read_links, pid)
        if link is None or link["from"] in seen:
            return pid
        pid = link["from"]
        seen.add(pid)


def sides(ctx: Inputs, root: str) -> dict[str, dict]:
    """What makes up each side of root (MATH.md 1.1, 1.2): `by` its linked ways ("links"), by a ladder's bands ("bands"),
    or nothing, a side left whole (None), with those ways in order, each with the link that names it, where one does.
    A watched level's side is its bands where one of them is linked as a way, or where nothing is linked on it and
    something is stated of one of them as of a way (structure.bands); the bands are then all its ways, linked or not.
    Otherwise the ways linked on it make it up."""
    linked: dict[str, list[dict]] = {"yes": [], "no": []}
    for link in ctx.links(root, "way", "out"):
        if link["stated"].get("side") in linked:
            linked[link["stated"]["side"]].append(link)
    possible = structure.possible_bands(ctx, root) if ctx.watched(root) else {}
    said = structure.bands(ctx, root) if possible else {}
    out = {}
    for side in ("yes", "no"):
        bands, ls = possible.get(side) or [], linked[side]
        if bands and (any(link["to"] in bands for link in ls) or (not ls and side in said)):
            by_band = {link["to"]: link["id"] for link in ls if link["to"] in bands}
            out[side] = {"by": "bands", "ways": [{"id": b, "link": by_band.get(b)} for b in bands]}
        elif ls:
            out[side] = {"by": "links", "ways": [{"id": link["to"], "link": link["id"]} for link in ls]}
        else:
            out[side] = {"by": None, "ways": []}
    return out


def tree(ctx: Inputs, root: str, made: dict[str, dict] | None = None) -> list[dict]:
    """Every way in root's tree, parents before their ways and each parent's ways in the order they were linked: its
    id, its parent (a side, "yes" or "no", for a way of the event itself), and the link, None for a band no link
    names. Each side is as `sides` makes it up (`made`, where given): a side made of bands holds all of them, and a way
    a link names on it that is no band is no outcome of root."""
    made = made or sides(ctx, root)
    banded = {s: {w["id"] for w in m["ways"]} for s, m in made.items() if m["by"] == "bands"}
    out, todo, seen = [], [root], {root}

    def add(pid: str, parent: str, link: str | None) -> None:
        seen.add(pid)
        out.append({"id": pid, "parent": parent, "link": link})
        todo.append(pid)
    while todo:
        parent = todo.pop(0)
        for link in ctx.links(parent, "way", "both"):
            if link["relation"] != "way" or link["from"] != parent or link["to"] in seen:
                continue
            side = link["stated"].get("side") if parent == root else parent
            if parent == root and side in banded and link["to"] not in banded[side]:
                continue
            add(link["to"], side, link["id"])
        if parent == root:
            for side in ("yes", "no"):
                for w in made[side]["ways"] if side in banded else ():
                    if w["id"] not in seen:
                        add(w["id"], side, w["link"])
    return out


def event_of(ctx: Inputs, pid: str) -> str:
    """The event pid is an outcome of: the event at the top of its tree of ways, pid itself where it is no way; and for
    a ladder's band that is no event itself, the first watched level whose side it is one of the bands of, whether or
    not that side is its bands yet, so that the first statement on a band finds its event (MATH.md 1.2)."""
    root = root_of(lambda oid: ctx.links(oid, "way", "both"), pid)
    if ctx.watched(root):
        return root
    for event in (link["to"] for link in ctx.links("portfolio", "watches")):
        if any(root in ids for ids in structure.possible_bands(ctx, event).values()):
            return event
    return root


# ---------------------------------------------------------------- a joint world, named by its outcomes


def made(pid: str) -> tuple:
    """The order objects were made in, by their ids: propositions by their number."""
    head, _, n = pid.partition(":")
    return (0, int(n), "") if head == "prop" and n.isdigit() else (1, 0, pid)


def joint_id(named: dict[str, str]) -> str:
    """A joint world's id (ONTOLOGY.md 2): its events in the order they were made, each with its outcome."""
    return "joint:" + "|".join(f"{e}={named[e]}" for e in sorted(named, key=made))


def named_by(jid: str) -> dict[str, str]:
    """The outcome of each event a joint world's id names, as written."""
    return dict(part.split("=", 1) for part in jid.removeprefix("joint:").split("|"))


def joint_world(ctx: Inputs, jid: str) -> tuple[str, dict[str, str]]:
    """A joint world named by its outcomes: its id, in the order its events were made, and the outcome of each event it
    names. Each is an event the portfolio watches, at least two, and each outcome one of that event's: yes, no, or one
    of its ways, a band of a side among them (sides). Raises ValueError, saying why, where the id names none."""
    named = named_by(jid)
    pairs = jid.removeprefix("joint:").split("|")
    if len(named) < len(pairs):
        raise ValueError(f"{jid} names an event twice: a joint world is one outcome of each event it names")
    if len(named) == 1:
        [(e, o)] = named.items()
        raise ValueError(f"{jid} names one event's world; it is {o if o not in ('yes', 'no') else e + ' ' + o}")
    for e, o in named.items():
        if not ctx.watched(e):
            raise ValueError(f"{jid} names {e}, which is not an event the portfolio watches")
        if o in ("yes", "no"):
            continue
        made_up = sides(ctx, e)
        ways = [w["id"] for w in tree(ctx, e, made_up)]
        bands = [b for ids in structure.possible_bands(ctx, e).values() for b in ids]
        if o not in ways and o not in bands:
            raise ValueError(f"{o} is not an outcome of {e}. Its outcomes are: "
                             + ", ".join(["yes", "no", *ways, *(b for b in bands if b not in ways)]))
    return joint_id(named), named


def holds(nodes: dict[str, dict], outcome: str, world: str) -> bool:
    """Whether an event's outcome holds another of its outcomes, a world among them: a side holds the ways on it, a way
    those under it, and each outcome itself."""
    x = world
    while x is not None:
        if x == outcome:
            return True
        x = nodes[x]["parent"] if x in nodes else None
    return False


def within(nodes_of, inner: dict[str, str], outer: dict[str, str]) -> bool:
    """Whether the joint world named `inner` lies within the one named `outer` (MATH.md 1.3, 1.9): it names every event
    `outer` names, each at an outcome `outer`'s holds. `nodes_of(event)` is that event's outcomes with their parents."""
    return all(e in inner and holds(nodes_of(e), o, inner[e]) for e, o in outer.items())


def meet(nodes_of, a: dict[str, str], b: dict[str, str]) -> dict[str, str] | None:
    """The joint world in which two named joint worlds both hold, by its outcomes: every event either names, each at the
    narrower of the two outcomes where both name it; None where they share no world, an event they both name at
    outcomes neither of which holds the other."""
    out = dict(a)
    for e, o in b.items():
        if e not in out or holds(nodes_of(e), out[e], o):
            out[e] = o
        elif not holds(nodes_of(e), o, out[e]):
            return None
    return out


def nearest(nodes_of, named: list[tuple[str, dict[str, str]]]) -> tuple[str, list[str]]:
    """Of the joint worlds that each state something of one joint world beneath them, by id with their outcomes, the one
    nearest above it (MATH.md 1.3): the one lying within every other. Returns its id and no others; or, where two lie
    within none of the rest and neither within the other, no id and those two, which say different things of one joint
    world with neither nearer."""
    least = [i for i, n in named if not any(j != i and within(nodes_of, m, n) for j, m in named)]
    return (least[0], []) if len(least) == 1 else ("", least[:2])


# ---------------------------------------------------------------- the tree, with its probabilities


def _outcomes_inputs(ctx: Inputs, root: str) -> dict:
    ways = tree(ctx, root)
    found = ctx.value("structure", structure.ctx_component(ctx, root)[0])
    read = found["output"]
    nodes = {}
    for pid in [root, *(w["id"] for w in ways)]:
        got = read["props"][pid]
        nodes[pid] = {"title": ctx.object(pid)["stated"].get("title"),
                      **{k: got[k] for k in ("p", "rule", "open") if k in got}}
    return {"event": root, "ways": ways, "nodes": nodes, "structure": found["basis"],
            "cannot_hold": read["cannot_hold"]}


def why_none(m: dict) -> str | None:
    """Why the event's outcomes have no probabilities, where they have none."""
    root, nodes = m["event"], m["nodes"]
    if m["cannot_hold"]:
        return (f"The statements of {root}'s structure cannot hold together, and its probabilities wait until one is "
                f"restated: {structure.cannot_hold_text(m['cannot_hold'])}.")
    left = [(pid, node["open"]) for pid, node in nodes.items() if node.get("p") is None and node.get("open")]
    if not left:
        return None
    return " ".join(structure.left_open_text(pid, o) for pid, o in left)


def _outcomes_compute(m: dict, load) -> dict:
    root, why = m["event"], why_none(m)
    p = m["nodes"][root].get("p")
    nodes: dict[str, dict] = {
        "yes": {"id": "yes", "parent": None, "side": "yes", "ways": [], "p": p, "rule": m["nodes"][root].get("rule")},
        "no": {"id": "no", "parent": None, "side": "no", "ways": [], "p": None if p is None else 1.0 - p,
               "rule": "complement"}}
    for w in m["ways"]:
        side = nodes[w["parent"] or "yes"]["side"]
        got = m["nodes"][w["id"]]
        nodes[w["id"]] = {"id": w["id"], "parent": w["parent"] or "yes", "side": side, "ways": [],
                          "way_link": w["link"], "p": got.get("p"), "rule": got.get("rule"), "title": got.get("title")}
        nodes[w["parent"] or "yes"]["ways"].append(w["id"])
    _make_up(nodes)
    worlds = [n for n, node in nodes.items() if not node["ways"]]
    return {"nodes": nodes, "worlds": worlds, "event": root, "p": p, **({"why": why} if why else {})}


def _make_up(nodes: dict[str, dict]) -> None:
    """Each outcome's ways make it up (MATH.md 1.1): they divide its probability as the structure's figures for them
    divide it, from the sides down. The structure reads each way's probability from one solution, which holds a way
    link's sum to the solver's tolerance; dividing the parent's by their proportions holds it to the float, so the
    worlds' probabilities, and the weights read from them, sum to one."""
    for n in list(nodes):  # the sides first, then each way after its parent (tree's order)
        node = nodes[n]
        ways = [nodes[w] for w in node["ways"]]
        if node.get("p") is None or not ways or any(w.get("p") is None for w in ways):
            continue
        total = math.fsum(w["p"] for w in ways)
        if total > 0.0:
            for w in ways:
                w["p"] = node["p"] * (w["p"] / total)


def shape(ctx: Inputs, pid: str, ways: list[dict]) -> dict:
    """An event's outcomes as its links make them (MATH.md 1.1), with no probability: its two sides and each way, each with
    its parent, its side and its title, and its worlds, the outcomes left whole. `ways` are its ways (tree)."""
    return _outcomes_compute({"event": pid, "ways": ways, "cannot_hold": [],
                              "nodes": {n: {"title": ctx.object(n)["stated"].get("title")}
                                        for n in (pid, *(w["id"] for w in ways))}}, None)


def _subject(store, pid: str) -> str:
    return root_of(store.read_links, pid)


OUTCOMES = Derivation("outcomes", 9, "Proposition", _outcomes_inputs, _outcomes_compute, fields=("outcomes",),
                      subject_of=_subject, pick=lambda out, pid: {"outcomes": {k: out[k] for k in (
                          "nodes", "worlds", "why") if k in out}}
                      if out["event"] == pid else {},
                      applies=lambda store, pid: store.watched(pid))

DERIVATIONS = (OUTCOMES,)
