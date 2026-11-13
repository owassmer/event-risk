"""An event's outcomes (MATH.md 1.1, 1.2): its sides and its ways, a tree, each with its probability.

An event's Yes side is the worlds in which it is true, and its No side the rest. A side is one world, or the ways the
analyst gives it, each a `way` link from the event stating its side; a way can have ways of its own, by `way` links
from it, which lie on its side. The ways under one parent exclude one another and together make it up. The worlds are
the outcomes with nothing under them, and one of them is the baseline the analyst names.

The event and its ways are propositions of one structure, and their probabilities are its reading (harness.derive.
structure): a way's chance given its parent is its share of it. Where the structure's statements cannot hold together,
or leave the event's probability or a way's open, the worlds have no probabilities, and `why` says which.
"""

from __future__ import annotations

import math

from harness.derive import structure
from harness.derive.engine import Derivation, Inputs

SIDES = ("yes", "no")


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


def ways_under(read_links, root: str, bands=None) -> list[dict]:
    """Every way in root's tree, parents before their ways and each parent's ways in the order they were linked: its
    id, its parent (a side, "yes" or "no", for a way of the event itself), and the link. Where a side of the event has
    no way linked and a ladder's bands make it up (structure.bands, read by `bands`), they are that side's ways, in
    order, with no link."""
    out, todo, seen = [], [root], {root}
    while todo:
        parent = todo.pop(0)
        for link in read_links(parent):
            if link["relation"] != "way" or link["from"] != parent or link["to"] in seen:
                continue
            seen.add(link["to"])
            out.append({"id": link["to"], "parent": link["stated"].get("side") if parent == root else parent,
                        "link": link["id"]})
            todo.append(link["to"])
        if parent == root and bands is not None:
            linked = {w["parent"] for w in out}
            for side, ids in bands(root).items():
                if side in linked:
                    continue
                for b in ids:
                    if b not in seen:
                        seen.add(b)
                        out.append({"id": b, "parent": side, "link": None})
                        todo.append(b)
    return out


# ---------------------------------------------------------------- the tree, with its probabilities


def _outcomes_inputs(ctx: Inputs, root: str) -> dict:
    links = lambda oid: ctx.links(oid, "way", "both")  # noqa: E731
    ways = ways_under(links, root, lambda r: structure.bands(ctx, r))
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
    for n in list(nodes):  # the sides first, then each way after its parent (ways_under's order)
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
    its parent, its side and its title, and its worlds, the outcomes left whole. `ways` are its ways (ways_under)."""
    return _outcomes_compute({"event": pid, "ways": ways, "cannot_hold": [],
                              "nodes": {n: {"title": ctx.object(n)["stated"].get("title")}
                                        for n in (pid, *(w["id"] for w in ways))}}, None)


def _subject(store, pid: str) -> str:
    return root_of(store.read_links, pid)


OUTCOMES = Derivation("outcomes", 7, "Proposition", _outcomes_inputs, _outcomes_compute, fields=("outcomes",),
                      subject_of=_subject, pick=lambda out, pid: {"outcomes": {k: out[k] for k in (
                          "nodes", "worlds", "why") if k in out}}
                      if out["event"] == pid else {},
                      applies=lambda store, pid: store.watched(pid))

DERIVATIONS = (OUTCOMES,)
