"""Several events read as one set of worlds (MATH.md 1.9).

Probabilities. The events and what is stated between them form one structure, and each joint world's probability is
read from it, every event and way keeping the probability it has alone (probabilities). Events depend on one another
only as their statements say: each proposition is read given what its statements tie it to, so two events each read
given a third are independent given it, and two that a third is read given stay independent of each other. Each
probability assumed this way is marked (`assumptions`).

What today's prices carry. Each joint world's weight is its joint probability times one factor for each event's world
in it, set so that every event's worlds carry the weights its row has (price_weights).

What each joint world does. A joint world the analyst states stands as stated, against the joint world in which every
event is at its baseline. Every other stands where the rows put it: each price the store places moves so that, in
each world of an event, its average over what the other events do there, at their joint probabilities, stands from its
average at the event's baseline by the move the row gives it (joint_levels), and a name's response compounds the same
way, so a company no row states has no move beyond its response. A holding the store does not place is valued as in
any world: a fund by its terms there, times what each row's statement does to it, and a stated value at the
corresponding world's. The joint worlds are then valued once, as one event's (impact.compute), and each event's
average over them stands beside its row.
"""
from __future__ import annotations

import copy
import itertools
import math
from datetime import date

import numpy as np

from harness import schema
from harness.derive import (distribution, episode, impact, markets, oneform, option_funds, outcomes, portfolio,
                            structure, valuation, worlds)
from harness.derive.engine import Derivation, Refused, Unavailable
from harness.store import StoreError

TOL = 2e-8
EPISODE = "an episode"  # what two joint worlds that each name an episode state, where neither is within the other


def project(prior, A, b):
    """Relative-entropy projection on exact linear statements, retaining structural zeros.

    The dual Newton solve uses only the independent equations; its residual is checked,
    so inconsistent statements never become a least-squares compromise.
    """
    r = np.asarray(prior, float)
    A, b = np.asarray(A, float), np.asarray(b, float)
    live = r > 0
    # Exact boundary statements remove their impossible cases before a dual solve;
    # a probability of zero is not a tiny positive world with an invented tail.
    for row, target in zip(A, b):
        centred = row - target
        if np.all((centred == 0) | (centred == 1)):
            live &= centred == 0
        elif np.all((centred == 0) | (centred == -1)):
            live &= centred == 0
    if not np.any(live):
        raise ValueError("the statements leave no possible world")
    a = A[:, live]
    U, s, V = np.linalg.svd(a, full_matrices=False)
    keep = s > 1e-11 * max(s[0], 1)
    if np.max(np.abs(b - U[:, keep] @ (U[:, keep].T @ b)), initial=0) > TOL:
        raise ValueError("the probabilities cannot all hold together")
    a, target = V[keep], (U[:, keep].T @ b) / s[keep]
    z = np.zeros(len(target))
    log_r = np.log(r[live])
    for _ in range(240):
        x = np.exp(np.clip(log_r + a.T @ z, -745, 700))
        grad = a @ x - target
        if np.max(np.abs(A[:, live] @ x - b), initial=0) < 2e-12:
            result = np.zeros(len(r))
            result[live] = x
            return result
        h = (a * x) @ a.T
        step = np.linalg.lstsq(h, grad, rcond=1e-13)[0]
        objective = float(x.sum() - target @ z)
        scale = 1.0
        while scale > 1e-12:
            trial = z - scale * step
            value = np.exp(np.clip(log_r + a.T @ trial, -745, 700)).sum() - target @ trial
            if value <= objective - 1e-4 * scale * float(grad @ step) + 1e-14:
                z = trial
                break
            scale *= .5
        else:
            break
    if np.max(np.abs(A[:, live] @ x - b), initial=0) > TOL:
        raise ValueError("the probabilities cannot all hold together")
    result = np.zeros(len(r))
    result[live] = x
    return result


def _multiply(factors):
    scope = sorted({v for variables, _ in factors for v in variables})
    product = np.ones((2,) * len(scope))
    for variables, values in factors:
        ordered = sorted(variables)
        if list(variables) != ordered:
            values = np.transpose(values, [variables.index(v) for v in ordered])
        product *= values.reshape(tuple(2 if v in variables else 1 for v in scope))
    return scope, product


def _marginal(factors, keep, *, support=False):
    """Exact variable elimination; complexity follows the scopes, not the component's size."""
    factors = [(list(scope), value) for scope, value in factors]
    remove = {v for scope, _ in factors for v in scope} - set(keep)
    while remove:
        node = min(remove, key=lambda v: (len({u for scope, _ in factors if v in scope for u in scope}), v))
        bucket = [factor for factor in factors if node in factor[0]]
        factors = [factor for factor in factors if node not in factor[0]]
        scope, values = _multiply(bucket)
        axis = scope.index(node)
        values = values.max(axis=axis) if support else values.sum(axis=axis)
        scope.remove(node)
        factors.append((scope, values))
        remove.remove(node)
    return _multiply(factors)


def _factorized(model, points, keep, order, parents, allowed):
    """Calibrate directed local tables, then eliminate everything the view does not ask for.

    A composite observation that revises its ancestors, or a dependency cycle, is
    left to the general exact calibration below; it is never replaced by a new
    independence assumption merely to fit a directed table.
    """
    factors, logical, earlier = [], [], set()
    for node in order:
        given = sorted(parents[node])
        if not set(given) <= earlier:
            raise ValueError("the directed structure has a cycle")
        if given:
            parent_scope, parent_values = _marginal(factors, given)
            if parent_scope != given:
                raise ValueError("the parents have no directed reading")
        else:
            parent_values = np.asarray(1.)
        scope = sorted([node, *given])
        atoms, indices, prior, parent_cases = [], [], [], []
        for values in itertools.product((False, True), repeat=len(given)):
            case = dict(zip(given, values))
            mass = float(parent_values[tuple(map(int, values))])
            options = [case | {node: v} for v in (False, True) if allowed(case | {node: v})]
            if not options and mass > TOL:
                raise ValueError("the observation changes its parents' distribution")
            parent_cases.append((case, mass))
            for atom in options:
                atoms.append(atom)
                indices.append(tuple(int(atom[v]) for v in scope))
                prior.append(mass / len(options))
        A, b = [np.ones(len(atoms))], [1.]
        for case, mass in parent_cases:
            A.append(np.array([all(atom[k] == v for k, v in case.items()) for atom in atoms], float))
            b.append(mass)
        if points.get(node) is not None:
            A.append(np.array([atom[node] for atom in atoms], float))
            b.append(points[node])
        for chance in model["chances"]:
            if chance["of"] != node:
                continue
            A.append(np.array([(float(atom[node]) - chance["value"])
                if all(atom[p] for p in chance["given"]) and all(not atom[p] for p in chance["given_not"])
                else 0. for atom in atoms]))
            b.append(0.)
        calibrated = project(prior, A, b)
        table, possible = np.zeros((2,) * len(scope)), np.zeros((2,) * len(scope))
        for atom, index, probability in zip(atoms, indices, calibrated):
            parent_index = tuple(int(atom[v]) for v in given)
            mass = float(parent_values[parent_index])
            table[index] = probability / mass if mass > 0 else .5
            possible[index] = 1.
        factors.append((scope, table))
        logical.append((scope, possible))
        earlier.add(node)
    scope, values = _marginal(factors, keep)
    support_scope, possible = _marginal(logical, keep, support=True)
    assert scope == support_scope
    atoms, p = [], []
    for index in itertools.product((0, 1), repeat=len(scope)):
        if possible[index] > 0:
            atoms.append(dict(zip(scope, map(bool, index))))
            p.append(float(values[index]))
    return atoms, np.asarray(p)


def admits(links: list[dict], lines: list[dict]):
    """Which assignments of truth to propositions the links and the lines allow, as the one form reads them
    (oneform._links, oneform._lines): each condition there says which cases cannot happen, over its propositions and
    the auxiliary ones a group of ways or a line's cuts are chained through. An assignment is allowed where some truth of
    those auxiliary propositions breaks none of the conditions it assigns; a partial one, where it breaks none it assigns
    whole, so each is exact once every proposition is assigned."""
    conditions, aux = oneform._links(links)
    on_lines, line_aux = oneform._lines(lines)
    conditions, aux = conditions + on_lines, set(aux) | set(line_aux)
    plain = [c for c in conditions if not aux & set(c.scope)]
    root = {x: x for x in aux}

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for c in conditions:
        hidden = [v for v in c.scope if v in aux]
        for x in hidden[1:]:
            root[find(x)] = find(hidden[0])
    chained: dict[str, list] = {}
    for c in conditions:
        hidden = [v for v in c.scope if v in aux]
        if hidden:
            chained.setdefault(find(hidden[0]), []).append(c)
    hidden_of = {g: sorted({v for c in cs for v in c.scope if v in aux}) for g, cs in chained.items()}

    def allowed(a: dict) -> bool:
        if any(all(v in a for v in c.scope) and c.bad(a) for c in plain):
            return False
        for g, cs in chained.items():
            known = [c for c in cs if all(v in a or v in aux for v in c.scope)]
            if known and not any(all(not c.bad({**a, **dict(zip(hidden_of[g], truth))}) for c in known)
                                 for truth in itertools.product((False, True), repeat=len(hidden_of[g]))):
                return False
        return True

    return allowed


def probabilities(model: dict, points: dict[str, float | None], *, keep=None):
    """A joint reading from conditional statements and links, then observations.

    Build conditional tables in dependency order while retaining the distribution of
    earlier propositions. Only a further statement requiring a different joint reading
    revises that distribution. This is a directed construction, not maximum entropy
    over the whole structure (which would correlate the inputs of a collider).
    """
    props, links, chances = model["props"], model["links"], model["chances"]
    parents = {p: set() for p in props}
    for c in chances:
        parents[c["of"]].update((*c["given"], *c["given_not"]))
    for l in links:
        a, b = l["from"], l["to"]
        if l["relation"] in ("implies", "and", "or", "then"):
            parents[a].add(b)
        elif l["relation"] == "way":
            parents[b].add(a)
    order, left = [], set(props)
    while left:
        ready = [p for p in props if p in left and not (parents[p] & left)]
        # Mutually stated conditionals form one connected block. A stable order within
        # it supplies only the initial reference; the exact statements decide its joint.
        p = ready[0] if ready else next(p for p in props if p in left)
        order.append(p)
        left.remove(p)

    allowed = admits(links, model.get("lines") or [])

    assumed = [{"of": node, "given": sorted(parents[node]), "reading": "independent beyond these statements"}
               for node in order]
    # Ways and ladders already prune to their terminal outcomes in the general
    # construction. The directed path avoids expanding an ordinary sparse DAG.
    if keep is not None and set(keep) != set(props) and not model.get("lines") and not any(l["relation"] == "way" for l in links):
        try:
            atoms, p = _factorized(model, points, keep, order, parents, allowed)
            return atoms, p, assumed
        except ValueError:
            pass

    def equations(atoms, known, *, new=None):
        A, b = [np.ones(len(atoms))], [1.0]
        for p in known:
            if points.get(p) is not None:
                A.append(np.array([a[p] for a in atoms], float))
                b.append(points[p])
        for c in chances:
            scope = {c["of"], *c["given"], *c["given_not"]}
            if not scope <= set(known):
                continue
            A.append(np.array([(float(a[c["of"]]) - c["value"])
                               if all(a[p] for p in c["given"]) and all(not a[p] for p in c["given_not"])
                               else 0.0 for a in atoms]))
            b.append(0.0)
        return A, b

    atoms, p = [{}], np.ones(1)
    seen = []
    for node in order:
        extended, origin, prior = [], [], []
        for i, a in enumerate(atoms):
            options = [a | {node: v} for v in (False, True) if allowed(a | {node: v})]
            for option in options:
                extended.append(option)
                origin.append(i)
                prior.append(p[i] / len(options))
        if not extended:
            raise ValueError("the links leave no possible world")
        seen.append(node)
        A, b = equations(extended, seen)
        # A conditional table has one entry per parent configuration, not one
        # fitted parameter per history. This both preserves the direction of
        # independence and avoids a quadratic matrix of prefix constraints.
        parent_order = sorted(parents[node] & set(seen[:-1]))
        table_groups, keys = {}, []
        choices = {i: tuple(a[node] for a, j in zip(extended, origin) if j == i) for i in range(len(atoms))}
        for i, old in enumerate(atoms):
            key = (tuple(old[x] for x in parent_order), choices[i])
            table_groups.setdefault(key, []).append(i)
        for key in table_groups:
            keys.extend((key, value) for value in key[1])
        M, reference = np.zeros((len(extended), len(keys))), []
        fixed_b, fixed = list(b), []
        masses = {key: float(sum(p[i] for i in origins)) for key, origins in table_groups.items()}
        for col, (key, value) in enumerate(keys):
            mass = masses[key]
            reference.append(mass / len(key[1]))
            for j, (a, i) in enumerate(zip(extended, origin)):
                if i in table_groups[key] and a[node] == value and mass > 0:
                    M[j, col] = p[i] / mass
        fixed = list(np.asarray(A) @ M)
        for key in table_groups:
            fixed.append(np.array([k == key for k, _ in keys], float))
            fixed_b.append(masses[key])
        try:
            p = M @ project(reference, fixed, fixed_b)
        except ValueError:
            # A price on a composite, or a reciprocal conditional, is evidence about
            # the earlier joint distribution; it is the reason independence gives way.
            p = project(prior, A, b)
        atoms = extended
    if keep is not None:
        kept = sorted(keep)
        grouped = {}
        for atom, probability in zip(atoms, p):
            key = tuple(atom[k] for k in kept)
            grouped[key] = grouped.get(key, 0.) + probability
        atoms, p = [dict(zip(kept, key)) for key in grouped], np.array(list(grouped.values()))
    return atoms, p, assumed


def joint_levels(events, cells, p, targets):
    """A price's level in each joint world against the joint world in which every event is at its baseline (MATH.md
    1.9): one factor for each event's world, multiplied, set so that in each world w of each event its mean over the
    joint worlds within w, at their probabilities, stands at the row's ratio `targets` gives it from its mean within
    the event's baseline. A world where a row puts the price at zero holds it at zero."""
    keys = [(i, w) for i, e in enumerate(events) for w in e["worlds"] if w != e["baseline"]]
    F = np.array([[float(c[i] == w) for i, w in keys] for c in cells])
    ratios = np.array([targets[i][w] for i, w in keys])
    zeros = ratios <= 0
    zero_rows = np.any(F[:, zeros] > 0, axis=1) if np.any(zeros) else np.zeros(len(cells), bool)
    free = ~zeros
    z = np.log(np.maximum(ratios[free], 1e-300))
    groups = []
    for (i, w), target in zip(keys, ratios):
        a = np.array([c[i] == w for c in cells])
        b = np.array([c[i] == events[i]["baseline"] for c in cells])
        pa, pb = p[a].sum(), p[b].sum()
        if pa <= TOL or pb <= TOL:
            continue
        groups.append((p * a / pa, p * b / pb, target))

    def evaluate(z):
        value = np.exp(np.clip(F[:, free] @ z, -700, 700))
        value[zero_rows] = 0
        residual, jac = [], []
        for a, b, target in groups:
            av, bv = a @ value, b @ value
            residual.append(av - target * bv)
            jac.append(((a - target * b) * value) @ F[:, free])
        return value, np.asarray(residual), np.asarray(jac)

    for _ in range(100):
        value, r, J = evaluate(z)
        if np.max(np.abs(r), initial=0) < 1e-10:
            return value
        step = np.linalg.lstsq(J, r, rcond=1e-12)[0]
        scale = 1.0
        while scale > 1e-10:
            trial = z - scale * step
            if np.linalg.norm(evaluate(trial)[1]) < np.linalg.norm(r):
                z = trial
                break
            scale /= 2
        else:
            break
    raise ValueError("the events' price moves cannot hold together")


def price_weights(cells, p, events, outputs):
    """The weight today's prices carry on each joint world (MATH.md 1.9): its joint probability times one factor for each
    event's world in it, the projection of the probabilities nearest them in relative entropy that gives every event's
    worlds the weights its row has."""
    A, b = [np.ones(len(cells))], [1.0]
    for i, e in enumerate(events):
        for w in e["worlds"]:
            A.append(np.array([c[i] == w for c in cells], float))
            b.append(outputs[i]["worlds"][w]["q"])
    return project(p, A, b)


def _world_case(event, outcome, node):
    return {event: node["side"] == "yes", **({outcome: True} if outcome not in ("yes", "no") else {})}


def model_inputs(ctx, seeds):
    """The structures the seeds are read in, joined as one (MATH.md 1.9): their propositions, links, books and lines,
    each chance at the figure its structure read, and each proposition's probability as its structure reads it. No
    valuation is read."""
    model = {"props": [], "links": [], "chances": [], "books": [], "lines": []}
    points, seen, resolved = {}, set(), {}
    for pid in seeds:
        first = structure.ctx_component(ctx, pid)[0]
        if first in seen:
            continue
        seen.add(first)
        m = structure._structure_inputs(ctx, first)
        out = ctx.value("structure", first)
        if out["output"]["cannot_hold"]:
            raise Unavailable(structure.cannot_hold_text(out["output"]["cannot_hold"]))
        for k in model:
            model[k] += m.get(k, [])
        points.update({p: n.get("p") for p, n in out["output"]["props"].items()})
        # each chance at its figure as the structure read it: a reading's share, a constant hazard's from the rungs
        # as the structure gives them, and a chance that follows a market or a holding's price, carried forward
        resolved.update({c["key"]: c["value"] for n in out["output"]["props"].values() for c in n.get("chances") or []})
    model["chances"] = [{**c, "value": resolved.get(c["key"], structure._now(c))} for c in model["chances"]]
    model["chances"] = [c for c in model["chances"] if c["value"] is not None]  # a figure the structure leaves open
    return model, points


def _held(ctx, component) -> list[dict]:
    """Each contract the portfolio holds, unsettled, whose market asks a proposition of the structure: its holding, and
    the proposition and side on which it pays."""
    out = []
    for holding in ctx.links("portfolio", "holds"):
        if not holding["to"].startswith("market:"):
            continue
        settlement = ctx.value("market_settlement", holding["to"])["output"].get("settlement") or {}
        if settlement.get("settled"):
            continue
        asks = next((l for l in ctx.links(holding["to"], "asks", direction="in") if l["from"] in component), None)
        if asks is not None:
            out.append({"holding": holding["id"], "on": asks["from"],
                        "pays": asks["stated"].get("side", "yes") == holding["stated"]["side"]})
        elif holding["to"] in component:  # a market that stands as itself in the structure: its Yes is it true
            out.append({"holding": holding["id"], "on": holding["to"], "pays": holding["stated"]["side"] == "yes"})
    return out


def _states(held, atoms, p, matching, ids):
    """In each world, the chance of each combination of the held contracts paying, from the cases' probabilities."""
    if not held:
        return {}
    state = [tuple(int(a[h["on"]] == h["pays"]) for h in held) for a in atoms]
    out = {}
    for wid, mask in zip(ids, matching):
        grouped = {}
        mass = float(p[mask].sum())
        for i, hit in enumerate(mask):
            if not hit:
                continue
            chance = float(p[i] / mass) if mass > TOL else 1.0 / int(mask.sum())
            grouped[state[i]] = grouped.get(state[i], 0.) + chance
        out[wid] = [{"p": chance, "payoffs": {h["holding"]: value for h, value in zip(held, values)}}
                    for values, chance in grouped.items() if chance > 0]
    return out


def _contract_propositions(ctx, component):
    return ({l["from"] for held in ctx.links("portfolio", "holds") if held["to"].startswith("market:")
             for l in ctx.links(held["to"], "asks", direction="in") if l["from"] in component}
            | {held["to"] for held in ctx.links("portfolio", "holds") if held["to"] in component})


def _identified(model, points, pid, seen=frozenset()):
    """A point stated directly or given by complete dependencies under the declared independence.

    A conjunction of identified roots is modelled; an unstated root or missing branch
    of a conditional table is not filled with an arbitrary half probability.
    """
    if points.get(pid) is not None:
        return True
    if pid in seen:
        return False
    parts = [l["to"] for l in model["links"] if l["from"] == pid and l["relation"] in ("and", "or")]
    if parts and all(_identified(model, points, p, seen | {pid}) for p in parts):
        return True
    chances = [c for c in model["chances"] if c["of"] == pid]
    parents = sorted({p for c in chances for p in (*c["given"], *c["given_not"])})
    return bool(chances) and all(_identified(model, points, p, seen | {pid}) for p in parents) and all(
        any(all(case[p] for p in c["given"]) and all(not case[p] for p in c["given_not"]) for c in chances)
        for values in itertools.product((False, True), repeat=len(parents)) for case in [dict(zip(parents, values))])


def _changes(stated: float, rows: float) -> bool:
    """Whether a compounded move a statement gives a factor departs from the one the rows give it: a price at zero in
    both is where the rows have it."""
    if not (math.isfinite(stated) and math.isfinite(rows)):
        return stated != rows
    return abs(stated - rows) > TOL


def contract_states_for_cases(ctx, cases, *, through=None, receipt=None):
    """The shared contract truth states conditional on an event's terminal worlds.

    Without a date, unrelated holdings have no entry: learning the event leaves
    their mark. With the date the worlds are drawn on, unsettled contracts due by then join by their stated
    probability; disconnected structures are independent. No market q invents a p.
    """
    seeds = list(dict.fromkeys(p for case in cases.values() for p in case))
    expiring = []
    if through:
        for held in ctx.links("portfolio", "holds"):
            if not held["to"].startswith("market:"):
                continue
            listing = ctx.value("market_listing", held["to"])["output"]
            close = (listing.get("close_time") or "")[:10]
            settlement = ctx.value("market_settlement", held["to"])["output"].get("settlement") or {}
            if settlement.get("settled"):
                continue
            if not close:
                raise Unavailable(f"{held['to']}'s payment date is needed to say whether it pays by {through}, "
                                  f"the date the outcomes are drawn on")
            if close > through:
                continue
            asks = ctx.links(held["to"], "asks", direction="in")
            if not asks:
                raise Unavailable(f"{held['to']} pays by {through}, the date the outcomes are drawn on, and no "
                                  f"proposition is linked as the one it asks, so what it pays there has no probability")
            expiring.append((held["to"], asks[0]["from"]))
            seeds.append(asks[0]["from"])
    model, points = model_inputs(ctx, seeds)
    for mid, pid in expiring:
        if not _identified(model, points, pid):
            raise Unavailable(f"{mid} pays by {through}, the date the outcomes are drawn on, and {pid}'s "
                              f"probability is left open by its statements")
    keep = set(seeds) | _contract_propositions(ctx, set(model["props"]))
    atoms, p, assumed = probabilities(model, points, keep=keep)
    if receipt is not None:
        receipt["probability_assumptions"] = assumed
    masks = [np.array([all(a[k] == v for k, v in case.items()) for a in atoms]) for case in cases.values()]
    held, waiting = _valued(model, points, _held(ctx, set(model["props"])), atoms, masks)
    if waiting and receipt is not None:
        receipt["waiting"] = waiting
    return _states(held, atoms, p, masks, list(cases))


def _valued(model, points, held, atoms, masks) -> tuple[list[dict], dict | None]:
    """The held contracts whose payoff in each world the statements give, and those that wait. A contract's payoff in a
    world is fixed where its proposition stands on the same side in every case the links allow within it; elsewhere it
    is the share of the world's probability where the proposition holds, which the statements give where every
    proposition of the structure has its probability, read with no dependence beyond what they state (MATH.md 1.9). Where
    one is left open, a contract whose payoff varies within a world waits, with those propositions named."""
    left = [q for q in model["props"] if not _identified(model, points, q)]
    kept, out = [], []
    for h in held:
        fixed = all(len({a[h["on"]] == h["pays"] for a, hit in zip(atoms, mask) if hit}) <= 1 for mask in masks)
        (kept if fixed or not left else out).append(h)
    if not out:
        return kept, None
    return kept, {"holdings": [h["holding"] for h in out],
                  "why": f"what each pays in some of these worlds is the share of the world where its proposition "
                         f"holds, which waits on the probabilities its statements leave open: {', '.join(left)}"}


def contract_states(ctx, pid, tree, *, through=None, receipt=None):
    return contract_states_for_cases(ctx, {w: _world_case(pid, w, tree["nodes"][w]) for w in tree["worlds"]},
                                     through=through, receipt=receipt)


# ---------------------------------------------------------------- events read together, one derivation


def _made(pid: str) -> tuple:
    head, _, n = pid.partition(":")
    return (0, int(n), "") if head == "prop" and n.isdigit() else (1, 0, pid)


def subject(events, considering: dict | None = None) -> str:
    """The joint view's key (MATH.md 1.9; TOOLS.md 5): the set of events, in the order they were made, and a contract
    considered beside them by its market, its side and its quantity. Naming the events in another order reads the same
    view."""
    key = ",".join(sorted(dict.fromkeys(events), key=_made))
    if considering is not None:
        key += "|" + "|".join((considering["market"], considering["side"], format(float(considering["quantity"]), "g")))
    return key


def _parse(key: str) -> tuple[list[str], dict | None]:
    head, *contract = key.split("|")
    considering = {"market": contract[0], "side": contract[1], "quantity": float(contract[2])} if contract else None
    return head.split(","), considering


def _wid(events: list[str], cell: tuple) -> str:
    return "joint:" + "|".join(f"{e}={w}" for e, w in zip(events, cell))


def _together_inputs(ctx, key: str) -> dict:
    """What a joint view reads (MATH.md 1.9): each event's row and what it reads, its outcomes, the one structure its
    events and their statements form with what each proposition's probability is alone, each joint world the analyst
    states, the contracts held on a proposition of that structure, today's prices of what the joint worlds move, the
    state the distribution draws, and the contract considered with its book, its fees and its close."""
    events, considering = _parse(key)
    for pid in events:
        if not ctx.watched(pid):
            raise Unavailable(f"{pid} is not an event the portfolio watches")
    rows = [ctx.value("impact", pid, ref=True) for pid in events]
    manifests = [impact._impact_inputs(ctx, pid) for pid in events]
    trees = [ctx.value("outcomes", pid)["output"] for pid in events]
    related = list(events)
    considered = None
    asof = portfolio.asof_of(ctx)
    if considering is not None:
        mid = considering["market"]
        asks = [{"from": link["from"], "side": link["stated"].get("side", "yes")}
                for link in ctx.links(mid, "asks", direction="in")]
        related += [a["from"] for a in asks]
        book, listing = ctx.value("market_price", mid), ctx.value("market_listing", mid)
        close = (listing["output"].get("close_time") or asof)[:10]
        considered = {**considering, "asks": asks, "book": book["output"].get("book"), "fees": listing["output"].get("fees"),
                      "close": close, "asof": asof, "basis": [book["basis"], listing["basis"]], "curves": {}}
        if close > asof:
            try:  # what only the contract reads: where it is not obtained, the contract says why, and the view stands
                considered["curves"] = valuation.curve_inputs(ctx, asof)
            except Unavailable as e:
                considered["unread"] = str(e)
    model, points = model_inputs(ctx, related)
    props = set(model["props"])
    kinds = ctx.payload(manifests[0]["lookthrough"]).get("kinds") or {}
    stated = []
    for j in ctx.objects("JointWorld"):  # each joint world a statement names, of events all in this view
        named = outcomes.named_by(j["id"])
        if not set(named) <= set(events) or \
                any(o not in trees[events.index(e)]["nodes"] for e, o in named.items()):
            continue  # of other events, or naming an outcome its event no longer has, which check() lists
        cons = [{"link": link["id"], "target": link["to"], **impact.stated_move(link)}
                for link in ctx.links(j["id"], "consequence")]
        worth = valuation.worth_inputs(ctx, j["id"], [], kinds) if ctx.links(j["id"], "worth") else []
        window = (j["stated"].get("episode") or {}).get("value")
        held = None
        if window:
            got = ctx.value("episode_covariance", episode.subject(window["start"], window["end"]))
            held = {"on": j["id"], "basis": got["basis"], **got["output"]}
        if cons or worth or held:
            stated.append({"id": j["id"], "named": named, "consequences": cons, "worth": worth,
                           **({"episode": held} if held else {})})
    proxies = {c["id"]: c["stated"]["proxy"] for c in ctx.objects("Factor")}
    targets = (set(manifests[0]["channels"]) | {i["target"] for mf in manifests for i in mf["worth"]}
               | {i["target"] for st in stated for i in st["worth"]} | {c["target"] for st in stated for c in st["consequences"]})
    prices = {t: impact.price_today(ctx, t, proxies, kinds) for t in sorted(targets)}
    cells = list(itertools.product(*(tree["worlds"] for tree in trees)))
    cases = {_wid(events, cell): {k: v for i, (e, w) in enumerate(zip(events, cell))
                                  for k, v in _world_case(e, w, trees[i]["nodes"][w]).items()} for cell in cells}
    # one date for the view, the latest of its events' dates, when every joint world is known (1.8, 1.9): its draws and
    # the contracts that pay by then read it
    drawn = distribution._inputs(ctx, events[0], on=distribution.event_date(ctx, *events))
    receipt: dict = {}
    paying = contract_states_for_cases(ctx, cases, through=drawn["drawn_on"], receipt=receipt)
    contracts, states = valuation.contract_inputs(ctx, events[0], {}, states=paying)
    # the contracts each fund still holds on that date, each fund valued by its terms as one event's are on its own date:
    # a contract expiring before it is replaced by then, and a fund none of whose contracts outlive it moves by its
    # response (1.6); a holding valued by what it is worth in a world is valued by that
    worth_named = {i["target"] for mf in manifests for i in mf.get("worth") or []} | \
        {i["target"] for st in stated for i in st["worth"]}
    books = option_funds.inputs(ctx, ctx.payload(manifests[0]["lookthrough"]),
                                ctx.payload(manifests[0]["fund_rows"])["rows"], drawn["drawn_on"],
                                exclude=worth_named)
    return {"events": events, "rows": rows, "manifests": manifests, "trees": trees, "model": model, "points": points,
            "stated": stated, "held": _held(ctx, props), "prices": prices, "considering": considered,
            "books": {"option_funds": books, "option_remainders": valuation.remainder_inputs(ctx, books)},
            "distribution": drawn, "paying": {"contracts": contracts, "states": states, "receipt": receipt}}


def _replaced_ratios(n: str, out: dict, row: dict) -> tuple[dict[str, float], dict[str, float]]:
    """A fund an event's row values by its contracts, read with events whose latest date none of them outlives: by then
    they are replaced, and it moves by its response (1.6). In each world of the row, its move against the row's
    baseline: its response there; where the row states its whole move, that move; where it states its residual, that
    residual on its response (1.9). With its response's move beside it."""
    resp, own, unstated = out["responding_ratios"][n], out["name_ratios"][n], (out.get("unstated") or {}).get(n)
    base = row["baseline"]
    if resp[base] <= 0:
        raise ValueError(f"{n} is worth zero in an event's baseline, so its joint move has none")
    moved = {w: resp[w] / resp[base] for w in row["worlds"]}
    rel = dict(moved)
    for w in row["worlds"]:
        a = ((out.get("setting") or {}).get(w) or {}).get(n)
        if a is None or unstated is None:
            continue
        if a[schema.AGAINST] == schema.BASELINE:
            if own[base] <= 0:
                raise ValueError(f"{n} is worth zero in an event's baseline, so its joint move has none")
            rel[w] = own[w] / own[base]
        else:
            rel[w] = moved[w] * (own[w] / unstated[w] if unstated[w] > TOL else 0.0)
    return rel, moved


def _together_compute(inputs: dict, load) -> dict:
    """Several events read as one set of worlds (MATH.md 1.9): each joint world's probability from the one structure, the
    weight today's prices carry on it, what it does, as the analyst states it or as the rows put it, and the portfolio's
    P&L there; beside each event's row, its average over the joint worlds; the distribution on the latest of the events'
    dates, when every joint world is known; and a
    contract considered, valued in the same worlds."""
    events, trees, manifests = inputs["events"], inputs["trees"], inputs["manifests"]
    outputs = [load(ref) for ref in inputs["rows"]]
    for pid, out in zip(events, outputs):
        if not out.get("worlds"):
            raise Unavailable(out.get("why") or f"{pid} has no valued worlds")
    model, points, held = inputs["model"], inputs["points"], inputs["held"]
    keep = set(events) | {w for tree in trees for w in tree["worlds"] if w not in ("yes", "no")}
    keep |= {h["on"] for h in held}
    if inputs["considering"]:
        keep |= {a["from"] for a in inputs["considering"]["asks"]}
    try:
        atoms, probabilities_, assumed = probabilities(model, points, keep=keep)
    except ValueError as e:
        raise Unavailable(str(e)) from None
    rows = [{"worlds": list(trees[i]["worlds"]), "baseline": o["baseline"]} for i, o in enumerate(outputs)]
    cells, p, matching = [], [], []
    for cell in itertools.product(*(r["worlds"] for r in rows)):
        cases = [_world_case(e, w, trees[i]["nodes"][w]) for i, (e, w) in enumerate(zip(events, cell))]
        matched = np.array([all(all(a[k] == v for k, v in case.items()) for case in cases) for a in atoms])
        if not matched.any():
            continue
        cells.append(cell)
        p.append(float(probabilities_[matched].sum()))
        matching.append(matched)
    p = np.array(p)
    baseline = tuple(r["baseline"] for r in rows)
    if baseline not in cells:  # the events cover every outcome: they are ways of one event (1.9)
        raise Refused("these events cover every outcome: the joint world in which each is at its baseline, which the "
                      "others are stated against, is empty; read together they are ways of one event", events)
    ids = [_wid(events, c) for c in cells]
    base = ids[cells.index(baseline)]

    # what the analyst states of joint worlds (MATH.md 1.9): a joint world kept by its outcomes holds every joint world of
    # this view beneath them, an outcome holding the worlds under it and the events it does not name turning out as
    # they may; in each, for each price, the statement nearest above it stands, the one lying within every other that
    # states that price there (1.3), and its shock is against the joint world in which the events it names are at
    # their baselines and the others as they are there
    nodes_of = lambda e: trees[events.index(e)]["nodes"]  # noqa: E731
    held_by = {st["id"]: [j for j, cell in enumerate(cells)
                          if all(outcomes.holds(nodes_of(e), o, cell[events.index(e)]) for e, o in st["named"].items())]
               for st in inputs["stated"]}

    def standing(sts: list[dict], j: int, what: str) -> dict:
        """Of the statements that each state `what` of joint world j, the one nearest above it; where two lie within
        neither each other nor the rest, its figures wait, naming the joint world where both apply to state it on."""
        got, apart = outcomes.nearest(nodes_of, [(st["id"], st["named"]) for st in sts])
        if got:
            return next(st for st in sts if st["id"] == got)
        a, b = (next(st for st in sts if st["id"] == x) for x in apart)
        both = outcomes.joint_id(outcomes.meet(nodes_of, a["named"], b["named"]))
        if what is EPISODE:
            raise Unavailable(f"{a['id']} and {b['id']} name different episodes in {ids[j]}, and neither is within the "
                              f"other. State the episode on {both}, where both apply.")
        raise Unavailable(f"{a['id']} and {b['id']} both set {what} in {ids[j]}, and neither is within the other. "
                          f"State {what} on {both}, where both apply.")

    def against(st: dict, j: int) -> int:
        named = {events.index(e) for e in st["named"]}
        cell = tuple(rows[i]["baseline"] if i in named else c for i, c in enumerate(cells[j]))
        if cell not in cells:
            raise Unavailable(f"{st['id']}'s shocks are stated against {_wid(events, cell)}, which cannot happen with "
                              f"what is stated between its events")
        if cell == cells[j]:
            raise Unavailable(f"{st['id']} holds {ids[j]}, in which each event it names is at its baseline, which its "
                              f"shocks are stated against")
        return cells.index(cell)
    setting: dict[int, dict[str, tuple[dict, dict]]] = {}
    joint_worth: dict[int, dict] = {}
    worth_on: dict[int, dict[str, str]] = {}  # the joint world whose statement each value is
    episode_of: dict[int, tuple[dict, str]] = {}
    for j in range(len(cells)):
        here = [st for st in inputs["stated"] if j in held_by[st["id"]]]
        for t in sorted({c["target"] for st in here for c in st["consequences"]}):
            st = standing([s for s in here if any(c["target"] == t for c in s["consequences"])], j, t)
            setting.setdefault(j, {})[t] = (next(c for c in st["consequences"] if c["target"] == t), st)
        for t in sorted({i["target"] for st in here for i in st["worth"]}):
            st = standing([s for s in here if any(i["target"] == t for i in s["worth"])], j, f"{t}'s value")
            joint_worth.setdefault(j, {})[t] = next(i for i in st["worth"] if i["target"] == t)
            worth_on.setdefault(j, {})[t] = st["id"]
        named_episode = [s for s in here if s.get("episode")]
        if named_episode:
            if len({tuple(s["episode"]["window"]) for s in named_episode}) == 1:
                st = named_episode[0]  # one window, whichever names it
            else:
                st = standing(named_episode, j, EPISODE)
            episode_of[j] = (st["episode"], st["id"])
    stated_js = [j for j in setting if setting[j]]
    for j in stated_js:
        for c, st in setting[j].values():
            against(st, j)

    try:
        q = price_weights(cells, p, rows, outputs)
        m0 = manifests[0]
        factors = list(m0["channels"])
        looked, fitted = load(m0["lookthrough"]), load(m0["betas"])
        books = inputs["books"]
        special = {"name:" + t for t in looked.get("multiples") or {}} | set(books["option_funds"])
        # a fund a row values by its contracts, none of which outlive the view's date: replaced by then (1.6)
        replaced = {n for mf in manifests for n in mf.get("option_funds") or {}} - special
        stated_names = {n for out in outputs for n in out["names_set"]}
        response_names = {"name:" + ticker for ticker, fund in (looked.get("funds") or {}).items()
                          if fund.get("expanded")} - stated_names - special
        # a fund valued by its terms takes no joint move: it is worth what its terms give in each joint world, a row's
        # statement on it carried there as what it does to that value (below)
        names = sorted((stated_names - special) | {n for out in outputs for field in ("name_ratios", "reference_ratios")
                                                   for n in out.get(field) or {}
                                                   if n.startswith("name:") and n not in special})
        # each price's move against the all-baseline joint world in each joint world, as the rows put it; and each
        # name's response there, its rows' own responses compounded as its price is, so that a name no row states has
        # no move beyond its response in any joint world, as in a row, and a stated one moves beyond it by its
        # statements alone (1.6, 1.9)
        levels, responses = {}, {}
        for target in factors + names:
            per_row, per_row_response = [], []
            for i, out in enumerate(outputs):
                if target in replaced and target in (manifests[i].get("option_funds") or {}):
                    rel, resp_rel = _replaced_ratios(target, out, rows[i])
                    per_row.append(rel)
                    per_row_response.append(resp_rel)
                elif target in out["against_baseline"].get(rows[i]["baseline"], {}):
                    per_row.append({w: 1 + out["against_baseline"][w][target] for w in rows[i]["worlds"]})
                elif target in (out.get("name_ratios") or {}) or target in (out.get("reference_ratios") or {}):
                    own = (out.get("name_ratios") or {}).get(target) or out["reference_ratios"][target]
                    resp = (out.get("responding_ratios") or {}).get(target) or own  # stating nothing, its response
                    ratio = resp if target in response_names else own
                    normal = ratio[rows[i]["baseline"]]
                    if normal <= 0:
                        raise ValueError(f"{target} is worth zero in an event's baseline, so its joint move has none")
                    per_row.append({w: ratio[w] / normal for w in rows[i]["worlds"]})
                    before = resp[rows[i]["baseline"]]
                    per_row_response.append({w: resp[w] / before for w in rows[i]["worlds"]} if before > 0 else
                                            per_row[-1])
                else:  # a name this row states nothing of follows its response to the row's completed factors
                    b = ((fitted.get("objects") or {}).get(target) or {}).get("fields", {}).get("betas") or {}
                    per_row.append({w: math.exp(sum(b.get(c, 0) * z for c, z in worlds.orthogonal(
                        manifests[i]["channels"], manifests[i]["coefficients"],
                        {c: math.log1p(out["against_baseline"][w][c]) for c in manifests[i]["channels"]}).items()))
                        for w in rows[i]["worlds"]})
                    per_row_response.append(per_row[-1])
            levels[target] = joint_levels(rows, cells, p, per_row)
            if len(per_row_response) == len(rows):
                responses[target] = joint_levels(rows, cells, p, per_row_response)
    except ValueError as e:
        raise Unavailable(str(e)) from None

    # the joint worlds as one event's, valued once (1.6): every joint world stands where the rows put it, and one the
    # analyst states stands there except for what it states (d): a price it states takes its statement; a price a row
    # states in its event's world here keeps what that statement gives it, a whole move as a whole move and a residual
    # as a residual, as a way keeps what is stated above it (1.3); every other factor moves from where the rows put it
    # as it ordinarily would with what the statement changes, with the co-movement of the episode its worlds name; and
    # each name moves with the factors at its betas from where the rows put it
    prices = inputs["prices"]
    rows_set = [j for j in range(len(ids)) if j not in stated_js and ids[j] != base]
    ids_f, coef_f = m0["channels"], m0["coefficients"]
    ordinary = worlds.covariance(ids_f, coef_f, m0["variances"])
    yields_f = m0.get("yields") or {}
    resp = impact.Responses(ids_f, coef_f, fitted)  # how each name moves with the factors, its currency's rate with it

    def as_log(t_: str, shock: float) -> float:  # a shock as its proxy's compounded move (1.3)
        if t_ in yields_f:
            return -yields_f[t_]["duration"] * shock / 100.0
        return math.log1p(shock) if shock > -1.0 else -math.inf

    def ln(x: float) -> float:
        return math.log(x) if x > 0 else -math.inf

    def residuals(logs_: dict[str, float]) -> dict[str, float] | None:
        return worlds.orthogonal(ids_f, coef_f, logs_) if all(math.isfinite(v) for v in logs_.values()) else None
    factor_logs: dict[int, dict[str, float]] = {}  # each stated joint world's factors, worked out as (d) gives them

    def factors_at(j: int) -> dict[str, float]:
        return factor_logs[j] if j in factor_logs else {c: ln(levels[c][j]) for c in factors}

    def kept_by(j: int) -> dict[str, list[tuple[int, str]]]:
        """What each event's row states in its world in joint world j: each price, with each event and what it is
        stated against there."""
        out: dict[str, list[tuple[int, str]]] = {}
        for i, o in enumerate(outputs):
            for target, a in ((o.get("setting") or {}).get(cells[j][i]) or {}).items():
                out.setdefault(target, []).append((i, a[schema.AGAINST]))
        return out
    for j in sorted(stated_js, key=lambda k: -sum(c == rows[i]["baseline"] for i, c in enumerate(cells[k]))):
        here, kept, ref = setting[j], kept_by(j), {c: ln(levels[c][j]) for c in factors}
        ref_z = residuals(ref)
        whole, owned = {}, {}
        for c in factors:
            if c in here:
                cons, st = here[c]
                r = against(st, j)
                if cons[schema.AGAINST] == schema.FOLLOWS:
                    owned[c] = as_log(c, cons["shock"]) + (residuals(factors_at(r)) or {}).get(c, 0.0)
                else:
                    whole[c] = as_log(c, cons["shock"]) + factors_at(r)[c]
            elif c in kept:  # a whole move a row states fixes the price; residuals alone, its residual
                if all(a == schema.FOLLOWS for _, a in kept[c]) and ref_z is not None:
                    owned[c] = ref_z[c]
                else:
                    whole[c] = ref[c]
        # what the statement changes: each factor it states, against where the rows put it here, a whole move against
        # the rows' move and a residual against the rows' residual. A statement that changes no factor leaves them
        # where the rows put them, with no fill and no episode to ask for (1.9)
        moving = any(_changes(whole[c], ref[c]) if c in whole else ref_z is None or _changes(owned[c], ref_z[c])
                     for c in factors if c in here)
        if moving and ref_z is None:
            raise Unavailable(f"the rows put a factor at zero in {ids[j]}, so nothing moves with what a statement changes "
                              f"there: {', '.join(c for c in factors if not math.isfinite(ref[c]))}")
        if moving:
            chosen = episode_of.get(j, (None, None))[0]  # the episode a joint world holding it names, or its worlds'
            if chosen is None:
                named = {}
                for i, mf in enumerate(manifests):
                    got = (mf.get("episodes") or {}).get(cells[j][i])
                    if got:
                        named.setdefault(tuple(got["window"]), (got, events[i], cells[j][i]))
                if len(named) > 1:
                    said = ", and ".join(f"{w} of {e} names the episode {got['window'][0]} to {got['window'][1]}"
                                         for got, e, w in named.values())
                    sts = {st["id"]: st["named"] for _, st in here.values()}  # the nearest joint world holding it
                    holder, apart = outcomes.nearest(nodes_of, list(sts.items()))
                    holder = holder or outcomes.joint_id(outcomes.meet(nodes_of, *(sts[x] for x in apart)))
                    raise Unavailable(f"{ids[j]} lies in worlds that name different episodes: {said}. Its factors move "
                                      f"with the co-movement of one: state it as {holder}'s episode")
                chosen = next(iter(named.values()))[0] if named else None
            cov = np.array(chosen["covariance"]) if chosen else ordinary
            dep = worlds.fill(ids_f, cov, {c: v - ref[c] if math.isfinite(v) else v for c, v in whole.items()},
                              {c: v - ref_z[c] for c, v in owned.items()}, coef_f)
            factor_logs[j] = {c: (whole[c] if c in whole and not math.isfinite(whole[c]) else ref[c] + dep[c])
                              for c in factors}
        else:
            factor_logs[j] = ref
    joint = copy.deepcopy(m0)
    joint.update(option_funds=books["option_funds"], option_remainders=books["option_remainders"])
    joint.update(event="together:" + ",".join(events), baseline=base, p=points[events[0]], tree={}, priced_in={},
                 worlds=[{"id": wid, "side": trees[0]["nodes"][c[0]]["side"], "p": float(prob)}
                         for wid, c, prob in zip(ids, cells, p)], consequences=[], episodes={})
    yields = joint.get("yields") or {}
    for target in factors:
        for j in rows_set + stated_js:
            y = yields.get(target)
            lv = levels[target][j] if j in rows_set else math.exp(factor_logs[j][target])
            stated_c = setting.get(j, {}).get(target)
            joint["consequences"].append({"link": stated_c[0]["link"] if stated_c else "rows:" + target, "on": ids[j],
                                          "target": target,
                                          "shock": worlds.points(lv - 1, y["duration"]) if y else lv - 1,
                                          "c": prices[target]["c"], "basis": prices[target]["basis"]})
    # a fund valued by its terms that a row states a shock on: in each joint world, what its terms give there, times
    # what each row's statement does to it in that row's world, its value there over where it stands unstated (1.6),
    # carried as a residual on it, as the rows compound every other price; a joint world that states it, as stated
    for n in sorted(special):
        factors_ = [(i, out["name_ratios"][n], out["unstated"][n]) for i, out in enumerate(outputs)
                    if n in (out.get("unstated") or {}) and n in (out.get("name_ratios") or {})]
        for j in rows_set + stated_js:
            if n in setting.get(j, {}) or not factors_:
                continue
            f = 1.0
            for i, stated_r, unstated_r in factors_:
                w = cells[j][i]
                f *= stated_r[w] / unstated_r[w] if unstated_r[w] > TOL else 1.0
            joint["consequences"].append({"link": "rows:" + n, "on": ids[j], "target": n, "shock": f - 1.0,
                                          schema.AGAINST: schema.FOLLOWS,
                                          **({"c": prices[n]["c"], "basis": prices[n]["basis"]}
                                             if n in prices else {"c": None, "basis": None})})
    # each name in each stated joint world: where the rows put it, moving with what the statement changes at its
    # betas unless a statement fixes its whole move there; one the joint world states, as stated there
    name_levels: dict[str, dict[str, float]] = {}
    joint_moving: dict[str, list[str]] = {}
    for j in stated_js:
        here, kept, wid = setting[j], kept_by(j), ids[j]
        for n in sorted({t for t in here if t.startswith("name:") and t not in special} | set(names)):
            cons, st = here.get(n, (None, None))
            rows_level = float(levels[n][j]) if n in levels else None
            if cons is not None:  # measured against its reference, the joint world in which the events it names are
                r = against(st, j)  # at their baselines (1.9), a whole move as a residual is: where the name stands there
                if n in levels and r not in factor_logs:
                    at_r = float(levels[n][r])
                elif n in levels:  # a stated joint world: where it stands there, its departure with it
                    at_r = name_levels.get(n, {}).get(ids[r], float(levels[n][r]))
                    if n in joint_moving.get(ids[r], ()):
                        at_r *= resp.departure(n, factor_logs[r], {c: ln(levels[c][r]) for c in factors})
                else:  # a name no row states stands at its response there
                    at_r = resp.level(n, factors_at(r))
                if cons[schema.AGAINST] == schema.BASELINE:
                    # the shock moves its price in its own currency (1.4), and the rate stands where this joint
                    # world's factors put it (1.6)
                    moved = resp.rate(n, factors_at(j)) / resp.rate(n, factors_at(r)) if n in resp.abroad else 1.0
                else:  # its residual: its further move from where it would otherwise stand here, moving with the
                    moved = resp.departure(n, factors_at(j), factors_at(r))  # factors at its betas from there (1.4)
                name_levels.setdefault(n, {})[wid] = (1.0 + cons["shock"]) * at_r * moved
            elif rows_level is not None:
                name_levels.setdefault(n, {})[wid] = rows_level
                if not any(a == schema.BASELINE for _, a in kept.get(n, ())):
                    joint_moving.setdefault(wid, []).append(n)
    resolved = [valuation._worth_worlds(mf) for mf in manifests]
    worth_targets = {t for per in resolved for t in per} | {t for items in joint_worth.values() for t in items}
    joint["worth"] = []
    for j, (cell, wid) in enumerate(zip(cells, ids)):
        entries = {}
        for i, per in enumerate(resolved):
            for target, by_world in per.items():
                item = by_world[cell[i]]
                if target in entries and entries[target]["value"] != item["value"] and \
                        target not in joint_worth.get(j, {}):
                    raise Unavailable(f"{target} has different values stated in {wid}; state its value in that joint "
                                      f"world")
                entries[target] = item
        for target in worth_targets:
            if target in joint_worth.get(j, {}):
                entries[target] = joint_worth[j][target]
            elif target not in entries:
                denominator = float(q @ levels[target])
                if denominator <= 0 or prices[target]["c"] is None:
                    raise Unavailable(f"{target}'s value cannot be placed against today's price")
                level = float(levels[target][j])
                if j in factor_logs:  # where a statement moves the factors, at its betas from where the rows put it
                    ref = {c: ln(levels[c][j]) for c in factors}
                    if all(math.isfinite(v) for v in (*ref.values(), *factor_logs[j].values())):
                        level *= resp.departure(target, factor_logs[j], ref)
                entries[target] = {"target": target, "link": "rows:" + target, "asof": m0["asof"],
                                   "value": {"amount": float(prices[target]["c"] * level / denominator)},
                                   "c": prices[target]["c"], "price_basis": prices[target]["basis"]}
        joint["worth"] += [{**item, "on": wid, "world": None} for item in entries.values()]
    every = rows_set + [cells.index(baseline)]
    joint["joint_prices"] = {n: {**{ids[j]: float(levels[n][j]) if n in levels else resp.level(n, factors_at(j))
                                    for j in every}, **name_levels.get(n, {})}
                             for n in sorted(set(names) | set(name_levels))
                             if n not in worth_targets and n not in response_names}
    joint["joint_responses"] = {n: {**{ids[j]: float((levels if n in response_names else responses)[n][j])
                                       for j in every},
                                    **{ids[j]: float((levels if n in response_names else responses)[n][j])
                                       for j in stated_js}}
                                for n in names if n not in worth_targets and (n in response_names or n in responses)}
    joint["joint_moving"] = joint_moving
    joint["joint_references"] = {ids[j]: {c: ln(levels[c][j]) for c in factors} for j in stated_js}
    joint["joint_cases"] = {wid: {k: v for i, (e, w) in enumerate(zip(events, cell))
                                  for k, v in _world_case(e, w, trees[i]["nodes"][w]).items()}
                            for wid, cell in zip(ids, cells)}
    on = {h["holding"]: h for h in held}
    joint["contracts"] = list({item["holding"]: copy.deepcopy(item)
                               for mf in manifests for item in mf.get("contracts") or []}.values())
    for item in joint["contracts"]:
        h = on.get(item["holding"])
        if h is None:
            continue
        win = np.array([a[h["on"]] == h["pays"] for a in atoms])
        item["probabilities"] = {wid: float(probabilities_[mask & win].sum() / probabilities_[mask].sum())
                                 if probabilities_[mask].sum() > TOL else float(win[mask].mean())
                                 for wid, mask in zip(ids, matching)}
    joint["contract_states"] = _states(held, atoms, probabilities_, matching, ids)
    valued = impact.compute(joint, load, weights=dict(zip(ids, map(float, q))))
    if not valued.get("worlds"):
        raise Unavailable(valued.get("why") or "the joint worlds' prices cannot be placed")
    view = {"events": events, "baseline": base, "worlds": [], "assumptions": assumed,
            **{k: valued[k] for k in ("expected", "volatility", "worst_outcome")}, "events_read": []}
    # in each joint world, the prices a statement fixes: those the joint world's own statements set, each with its link
    # and the joint world it is stated on, and those a row states in its event's world there, with the events and what
    # their shock is against, the baseline (its whole move held) or what it follows (its residual held); every
    # other price moves with them (1.9). Each joint world's P&L against the one in which every event is at its
    # baseline, in three parts (impact.baseline_parts), counts the factors named here as set.
    def fixed(j: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for target, by in kept_by(j).items():  # held as a residual only where every row states one (above)
            out[target] = {"rows": [events[i] for i, _ in by],
                           schema.AGAINST: schema.FOLLOWS if all(a == schema.FOLLOWS for _, a in by) else schema.BASELINE}
        for i, per in enumerate(resolved):
            for target, by_world in per.items():
                if cells[j][i] in by_world:
                    out.setdefault(target, {"rows": []})["rows"].append(events[i])
        for target, (cons, st) in setting.get(j, {}).items():
            out[target] = {"link": cons["link"], "on": st["id"]}
        for target, item in joint_worth.get(j, {}).items():
            out[target] = {"link": item["link"], "on": worth_on[j][target]}
        return dict(sorted(out.items()))
    named = [fixed(j) for j in range(len(ids))]
    split = impact.baseline_parts(valued["parts"], base, {wid: {c for c in factors if c in named[j]}
                                                          for j, wid in enumerate(ids)})
    for wid, cell in zip(ids, cells):
        j = ids.index(wid)
        view["worlds"].append({"id": wid, "outcomes": dict(zip(events, cell)), **valued["worlds"][wid],
                               "factors": valued["against_baseline"][wid],
                               **({"against_baseline_parts": split[wid]} if wid in split else {}),
                               "set": named[j] or "nothing"})
    for i, (pid, out) in enumerate(zip(events, outputs)):
        av = {}
        for side in ("yes", "no"):
            ix = [j for j, c in enumerate(cells) if trees[i]["nodes"][c[i]]["side"] == side]
            mass = sum(p[j] for j in ix)
            av[side] = float(sum(p[j] * valued["worlds"][ids[j]]["A"] for j in ix) / mass) if mass > TOL else None
        av["full_impact"] = av["yes"] - av["no"] if None not in av.values() else None
        view["events_read"].append({"event": pid, "alone": {k: out[k] for k in ("yes", "no", "full_impact")},
                                    "together": av})
    if inputs["considering"]:  # a contract that cannot be valued says why in its own place; the joint view stands
        try:
            view["considering"] = protection(inputs["considering"], atoms, probabilities_, matching, ids, valued, held,
                                             load)
        except Unavailable as e:
            view["considering"] = {k: inputs["considering"][k] for k in ("market", "side", "quantity")} | {"why": str(e)}
    drawn = {**inputs["distribution"], "centre": valued, "contract_receipt": inputs["paying"]["receipt"],
             "valuation": {**joint, "contracts": inputs["paying"]["contracts"],
                           "contract_states": inputs["paying"]["states"]}}
    view["distribution"] = distribution._compute(drawn, load)
    return view


TOGETHER = Derivation("together", 12, "Proposition", _together_inputs, _together_compute,
                      applies=lambda store, key: False)
DERIVATIONS = (TOGETHER,)


def read(store, selected: list[str], considering: dict | None = None) -> dict:
    """The events named read as one set of worlds, with a contract considered beside them (MATH.md 1.9; TOOLS.md 5), as the
    together derivation gives them, with its basis; the distribution as the analyst's summary of it. Each joint
    world's factor moves, against the joint world in which every event is at its baseline, are in the terms a shock
    on each is stated in (1.3), as an event's are: its proxy's price move as a fraction, or, for a factor quoted as
    the yield its proxy pays, that yield's move in points; `factors` names each, broad to narrow, with its yield
    today where it is quoted as one. The Computation keeps them as price moves."""
    if not isinstance(selected, list) or not selected or any(not isinstance(p, str) for p in selected):
        raise StoreError("together names the events as a list of their ids")
    if len(set(selected)) != len(selected):
        raise StoreError("together names each event once")
    for pid in selected:
        if not store.watched(pid):
            raise StoreError(f"{pid} is not an event the portfolio watches")
    if considering is not None:
        _check_contract(considering)
    key = subject(selected, considering)
    found = store.engine.value("together", key)
    if found.output is None:
        if found.refused is not None:
            raise StoreError(found.cause, list(found.refused))
        return {"events": _parse(key)[0], "why": found.cause}
    out = dict(found.output)
    out["distribution"] = distribution.receipt(out["distribution"], None)["distribution"]
    factors = sorted(store.query("Factor"), key=lambda c: (c["stated"]["order"], c["id"]))
    stated = {c["id"]: c["stated"] for c in factors}
    out["factors"] = [{"factor": c["id"], "name": c["stated"]["name"],
                       **({"yield": c["stated"]["yield"]} if c["stated"].get("duration") is not None else {})}
                      for c in factors]
    out["worlds"] = [{**w, "factors": {c: worlds.in_its_terms(stated.get(c) or {}, x) for c, x in w["factors"].items()},
                      **({"against_baseline_parts": _named(w["against_baseline_parts"], stated)}
                         if w.get("against_baseline_parts") else {})}
                     for w in out["worlds"]]
    return {**out, "basis": found.basis}


def _named(parts: dict, factors: dict[str, dict]) -> dict:
    """A joint world's three parts against the baseline, the largest moving factor with its name."""
    top = parts.get("largest_moving")
    if not top:
        return parts
    return {**parts, "largest_moving": {**top, "name": (factors.get(top["factor"]) or {}).get("name", top["factor"])}}


def _check_contract(contract):
    if not isinstance(contract, dict) or set(contract) != {"market", "side", "quantity"} or contract["side"] not in ("yes", "no"):
        raise StoreError("considering states a market, its side (yes or no), and a quantity")
    quantity = contract["quantity"]
    if not isinstance(contract["market"], str) or not contract["market"].startswith("market:"):
        raise StoreError("considering names a market id")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0:
        raise StoreError("a considered contract has a positive quantity")


def protection(contract, atoms, p, matching, ids, valued, held, load):
    """A contract considered beside the events read together (MATH.md 1.9; TOOLS.md 5): in each joint world, what it pays
    there, the chance it pays times its quantity discounted from its close, less what buying it costs at its book (3.3),
    with the portfolio's P&L there, what the two come to, and each combination of it and the contracts held paying."""
    side, quantity = contract["side"], contract["quantity"]
    if contract.get("unread"):
        raise Unavailable(contract["unread"])
    linked = next((a for a in contract["asks"] if a["from"] in atoms[0]), None)
    if linked is None:
        raise Unavailable(f"{contract['market']} asks no proposition of the events read together")
    try:  # what buying it costs at its book, with the fee its venue states (3.3): the markets' one calculation
        bought = markets.order_cost(contract["book"], side, quantity, contract["fees"])
    except (ValueError, Unavailable) as e:
        raise Unavailable(f"{contract['market']}: {e}") from None
    cost, fee, fills = bought["cost"], bought["fees"], bought["fills"]
    years = max(0, (date.fromisoformat(contract["close"]) - date.fromisoformat(contract["asof"])).days) / 365
    pv = valuation.discount_at(contract["curves"], load, contract["asof"], years) if years else 1.0
    winning = np.array([a[linked["from"]] == (linked["side"] == side) for a in atoms])
    on = {h["holding"]: h for h in held}
    holding = [(item, np.array([a[on[item["holding"]]["on"]] == on[item["holding"]]["pays"] for a in atoms]))
               for item in (valued.get("valuation_terms") or {}).get("contracts") or [] if item["holding"] in on]
    out = {}
    for wid, mask in zip(ids, matching):
        mass = p[mask].sum()
        chance = float(p[mask & winning].sum() / mass) if mass > TOL else float(winning[mask].mean())
        payout = quantity * pv * chance
        portfolio_pnl = valued["worlds"][wid]["A"]
        grouped = {}
        for i, hit in enumerate(mask):
            if not hit:
                continue
            key = (bool(winning[i]), *(bool(truth[i]) for _, truth in holding))
            grouped[key] = grouped.get(key, 0.) + (float(p[i] / mass) if mass > TOL else 1.0 / int(mask.sum()))
        outcomes_ = []
        for truth, probability in grouped.items():
            if probability <= 0:
                continue
            value = quantity * pv if truth[0] else 0.
            state = portfolio_pnl + sum(item["quantity"] * item["discount"] * (wins - item["probabilities"][wid])
                                        for (item, _), wins in zip(holding, truth[1:]))
            outcomes_.append({"probability": probability, "payout": value, "pnl": value - cost,
                              "portfolio": state, "combined": state + value - cost})
        out[wid] = {"payout": payout, "pnl": payout - cost, "portfolio": portfolio_pnl,
                    "combined": portfolio_pnl + payout - cost, "pays_probability": chance, "outcomes": outcomes_}
    return {k: contract[k] for k in ("market", "side", "quantity")} | {
        "cost": cost, "fees": fee, "fills": fills, "worlds": out, "discount": pv, "basis": contract["basis"],
        "expected": sum(valued["worlds"][w]["p"] * x["pnl"] for w, x in out.items())}
