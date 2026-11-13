"""Several events read as one economic state (MATH.md 1.9).

The probability model preserves the direction of stated dependencies. In particular,
two causes are not made dependent merely because a third proposition depends on both.
Completed single-event price ratios are combined before the existing valuation is read.
"""
from __future__ import annotations

import copy
import itertools
import math
from datetime import date

import numpy as np

from harness import schema
from harness.derive import distribution, impact, markets, oneform, outcomes, portfolio, structure, valuation, worlds
from harness.derive.engine import Derivation, Refused, Unavailable
from harness.store import StoreError

TOL = 2e-8


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
    """Multiplicative event effects whose conditional mean ratios reproduce each row."""
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
    A, b = [np.ones(len(cells))], [1.0]
    for i, e in enumerate(events):
        for w in e["worlds"]:
            A.append(np.array([c[i] == w for c in cells], float))
            b.append(outputs[i]["worlds"][w]["q"])
    return project(p, A, b)


def _world_case(event, outcome, node):
    return {event: node["side"] == "yes", **({outcome: True} if outcome not in ("yes", "no") else {})}


def model_inputs(ctx, seeds):
    """The existing closed probability structures, without requesting any valuation."""
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


def _contract_states(ctx, component, atoms, p, matching, ids):
    return _states(_held(ctx, component), atoms, p, matching, ids)


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


def contract_states_for_cases(ctx, cases, *, through=None, receipt=None):
    """The shared contract truth states conditional on an event's terminal worlds.

    Without a horizon, unrelated holdings have no entry: learning the event leaves
    their mark. With a horizon, unsettled contracts due by then join by their stated
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
                raise Unavailable(f"{held['to']}'s payment date is needed to value its contract through the horizon")
            if close > through:
                continue
            asks = ctx.links(held["to"], "asks", direction="in")
            if not asks:
                raise Unavailable(f"{held['to']} pays by the horizon but has no linked proposition with a probability")
            expiring.append((held["to"], asks[0]["from"]))
            seeds.append(asks[0]["from"])
    model, points = model_inputs(ctx, seeds)
    for mid, pid in expiring:
        if not _identified(model, points, pid):
            raise Unavailable(f"{mid} pays by the horizon but {pid}'s probability is not identified")
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


def contract_probabilities(ctx, pid, tree):
    """Conditional means of those same shared payoff states."""
    states = contract_states(ctx, pid, tree)
    out = {}
    for w, found in states.items():
        for outcome in found:
            for hid, value in outcome["payoffs"].items():
                values = out.setdefault(hid, {})
                values[w] = values.get(w, 0.) + outcome["p"] * value
    return out


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


def _joint_world(ctx, pid: str, seen: frozenset = frozenset()) -> bool:
    """A proposition that is worlds of several events at once: a composite (`and`, `then`) of two parts, each a world
    of an event that names its baseline, or itself such a composite. What the analyst states of it is stated against the
    joint world in which every event is at its baseline (1.9)."""
    if pid in seen:
        return False
    parts = [link["to"] for link in ctx.links(pid, ("and", "then"))]
    if len(parts) != 2:
        return False
    links = lambda oid: ctx.links(oid, "way", "both")  # noqa: E731
    for part in parts:
        root = outcomes.root_of(links, part)
        if ctx.object(root)["stated"].get("baseline") is None and not _joint_world(ctx, root, seen | {pid}):
            return False
    return True


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
    in_rows = {n for tree in trees for n in tree["nodes"]} | set(events)
    stated = []
    for pid in model["props"]:
        if pid in in_rows or not _joint_world(ctx, pid):
            continue
        cons = [{"link": link["id"], "target": link["to"], **impact.stated_move(link)}
                for link in ctx.links(pid, "consequence")]
        worth = ([i for i in valuation.worth_inputs(ctx, pid, [], kinds) if i.get("world") in (None, "yes")]
                 if ctx.links(pid, "worth") else [])
        if cons or worth:
            stated.append({"id": pid, "consequences": cons, "worth": worth})
    proxies = {c["id"]: c["stated"]["proxy"] for c in ctx.objects("Factor")}
    targets = (set(manifests[0]["channels"]) | {i["target"] for mf in manifests for i in mf["worth"]}
               | {i["target"] for st in stated for i in st["worth"]} | {c["target"] for st in stated for c in st["consequences"]})
    prices = {t: impact.price_today(ctx, t, proxies, kinds) for t in sorted(targets)}
    cells = list(itertools.product(*(tree["worlds"] for tree in trees)))
    cases = {_wid(events, cell): {k: v for i, (e, w) in enumerate(zip(events, cell))
                                  for k, v in _world_case(e, w, trees[i]["nodes"][w]).items()} for cell in cells}
    drawn = distribution._inputs(ctx, events[0])
    receipt: dict = {}
    horizon = contract_states_for_cases(ctx, cases, through=drawn["horizon_end"], receipt=receipt)
    contracts, states = valuation.contract_inputs(ctx, events[0], {}, states=horizon)
    return {"events": events, "rows": rows, "manifests": manifests, "trees": trees, "model": model, "points": points,
            "stated": stated, "held": _held(ctx, props), "prices": prices, "considering": considered,
            "distribution": drawn, "horizon": {"contracts": contracts, "states": states, "receipt": receipt}}


def _together_compute(inputs: dict, load) -> dict:
    """Several events read as one set of worlds (MATH.md 1.9): each joint world's probability from the one structure, the
    weight today's prices carry on it, what it does, as the analyst states it or as the rows put it, and the portfolio's
    P&L there; beside each event's row, its average over the joint worlds; the distribution at the horizon's end; and a
    contract considered, valued in the same worlds."""
    events, trees, manifests = inputs["events"], inputs["trees"], inputs["manifests"]
    outputs = [load(ref) for ref in inputs["rows"]]
    for pid, out in zip(events, outputs):
        if not out.get("worlds"):
            raise Unavailable(out.get("why") or f"{pid} has no valued worlds")
    model, points, held = inputs["model"], inputs["points"], inputs["held"]
    keep = set(events) | {w for tree in trees for w in tree["worlds"] if w not in ("yes", "no")}
    keep |= {st["id"] for st in inputs["stated"]} | {h["on"] for h in held}
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

    # what the analyst states of joint worlds, each over the joint worlds its proposition is true in; in each, the
    # statement on the fewest joint worlds stands
    scopes = []
    for st in inputs["stated"]:
        truth = np.array([a[st["id"]] for a in atoms])
        hits = [i for i, mask in enumerate(matching) if np.any(mask & truth)]
        if not hits:
            continue
        if any(not np.all(truth[matching[i]]) for i in hits):
            raise Unavailable(f"the events read together leave {st['id']} true in part of a joint world; read with them "
                              f"the events it is stated on")
        if cells.index(baseline) in hits and st["consequences"]:
            raise Unavailable(f"{st['id']} holds the joint world in which every event is at its baseline, which its "
                              f"shocks are stated against")
        scopes.append((set(hits), st))
    joint_consequences: dict[int, dict] = {}
    joint_worth: dict[int, dict] = {}
    for scope, st in sorted(scopes, key=lambda entry: len(entry[0])):
        for i in scope:
            for c in st["consequences"]:
                joint_consequences.setdefault(i, {}).setdefault(c["target"], c)
            for item in st["worth"]:
                joint_worth.setdefault(i, {}).setdefault(item["target"], item)
    stated_by = {ids[i]: sorted({x["link"] for x in (*joint_consequences.get(i, {}).values(),
                                                    *joint_worth.get(i, {}).values())})
                 for i in set(joint_consequences) | set(joint_worth)}

    try:
        q = price_weights(cells, p, rows, outputs)
        m0 = manifests[0]
        factors = list(m0["channels"])
        looked, fitted = load(m0["lookthrough"]), load(m0["betas"])
        special = {"name:" + t for t in looked.get("multiples") or {}}
        special |= {n for mf in manifests for n in mf.get("option_funds") or {}}
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
                if target in out["against_baseline"].get(rows[i]["baseline"], {}):
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

    # the joint worlds as one event's, valued once (1.6): a joint world the analyst states stands as stated, as a way
    # does, its other factors moving with what it sets and its names by their responses or their stated shocks; every
    # other joint world stands where the rows put it
    prices = inputs["prices"]
    rows_set = [j for j in range(len(ids)) if j not in joint_consequences and ids[j] != base]
    joint = copy.deepcopy(m0)
    joint.update(event="together:" + ",".join(events), baseline=base, p=points[events[0]], tree={}, priced_in={},
                 worlds=[{"id": wid, "side": trees[0]["nodes"][c[0]]["side"], "p": float(prob)}
                         for wid, c, prob in zip(ids, cells, p)], consequences=[], episodes={})
    yields = joint.get("yields") or {}
    for target in factors:
        for j in rows_set:
            y, lv = yields.get(target), levels[target][j]
            joint["consequences"].append({"link": "rows:" + target, "on": ids[j], "target": target,
                                          "shock": worlds.points(lv - 1, y["duration"]) if y else lv - 1,
                                          "c": prices[target]["c"], "basis": prices[target]["basis"]})
    # a fund valued by its terms that a row states a shock on: in each joint world the rows set, what its terms give
    # there, times what each row's statement does to it in that row's world, its value there over where it stands
    # unstated (1.6), carried as a residual on it, as the rows compound every other price
    for n in sorted(special):
        factors_ = [(i, out["name_ratios"][n], out["unstated"][n]) for i, out in enumerate(outputs)
                    if n in (out.get("unstated") or {}) and n in (out.get("name_ratios") or {})]
        if not factors_:
            continue
        for j in rows_set:
            f = 1.0
            for i, stated_r, unstated_r in factors_:
                w = cells[j][i]
                f *= stated_r[w] / unstated_r[w] if unstated_r[w] > TOL else 1.0
            joint["consequences"].append({"link": "rows:" + n, "on": ids[j], "target": n, "shock": f - 1.0,
                                          schema.AGAINST: schema.FOLLOWS,
                                          **({"c": prices[n]["c"], "basis": prices[n]["basis"]}
                                             if n in prices else {"c": None, "basis": None})})
    for j, items in joint_consequences.items():
        for target, c in items.items():
            joint["consequences"].append({"link": c["link"], "on": ids[j], "target": target,
                                          "shock": c["shock"], schema.AGAINST: c[schema.AGAINST],
                                          **({"c": prices[target]["c"], "basis": prices[target]["basis"]}
                                             if target in prices else {"c": None, "basis": None})})
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
                entries[target] = {"target": target, "link": "rows:" + target, "asof": m0["asof"],
                                   "value": {"amount": float(prices[target]["c"] * levels[target][j] / denominator)},
                                   "c": prices[target]["c"], "price_basis": prices[target]["basis"]}
        joint["worth"] += [{**item, "on": wid, "world": None} for item in entries.values()]
    joint["joint_prices"] = {n: {ids[j]: float(levels[n][j]) for j in rows_set + [cells.index(baseline)]}
                             for n in names if n not in worth_targets and n not in response_names}
    joint["joint_responses"] = {n: {ids[j]: float((levels if n in response_names else responses)[n][j])
                                    for j in rows_set + [cells.index(baseline)]}
                                for n in names if n not in worth_targets and (n in response_names or n in responses)}
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
    for wid, cell in zip(ids, cells):
        view["worlds"].append({"id": wid, "outcomes": dict(zip(events, cell)), **valued["worlds"][wid],
                               "factors": valued["against_baseline"][wid],
                               "by": {"stated": stated_by[wid]} if wid in stated_by else "rows"})
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
    drawn = {**inputs["distribution"], "centre": valued, "contract_receipt": inputs["horizon"]["receipt"],
             "valuation": {**joint, "contracts": inputs["horizon"]["contracts"],
                           "contract_states": inputs["horizon"]["states"]}}
    view["distribution"] = distribution._compute(drawn, load)
    return view


TOGETHER = Derivation("together", 6, "Proposition", _together_inputs, _together_compute,
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
    out["worlds"] = [{**w, "factors": {c: worlds.in_its_terms(stated.get(c) or {}, x) for c, x in w["factors"].items()}}
                     for w in out["worlds"]]
    return {**out, "basis": found.basis}


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
