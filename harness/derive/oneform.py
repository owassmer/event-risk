"""One set of probabilities for a structure (MATH.md 1.2): pure arithmetic over plain inputs.

A structure is the propositions the analyst relates by links and chances. They divide what can happen into cases, in
each of which every proposition is true or false, and one set of probabilities over those cases gives each proposition
its probability, the sum over the cases where it is true. Each statement is a condition on that set:

- a link says which cases can happen: `implies`, `and`, `or`, `then` (the composite implies each part), and `way` (the
  ways under one parent, or one side of an event, exclude one another and make it up);
- a taken book puts its proposition's probability between its bid and its ask, and gives a price to stay near;
- a chance of E given some propositions true and others false is the share of their probability that E carries:
  P(E and cond) = c * P(cond); stated on its own, it is E's probability.

The set is held as the probabilities of the cases of small overlapping groups of propositions, the cliques of a junction
tree built from the statements' scopes, which agree on what they share: for a tree of groups that agreement is all one
set over every case needs, so what follows is exactly what one set over every case gives, and its size grows with how
far the statements tie propositions together, not with their number.

What is read, in order (`read`):
1. whether the links and chances hold together. Where they cannot, each smallest set of them that cannot is listed
   (a deletion filter), and the structure's probabilities wait. A chance given some propositions is said of the cases
   where they hold, so statements that leave those cases no probability cannot hold with it.
2. each proposition's range the markets imply: the least and most it can be with the links kept and each proposition
   within the bid and ask of every book taken on it.
3. where the taken books cannot hold with the links and chances, how far each is stretched: the deepest book's stretch
   is made least first, then the next deepest's with the deeper held, and so on; books of equal depth share it by the
   least sum of squared stretches, each in its half-spread. A book quoted on one side only has a depth of 0.
4. the reading: the set that keeps every link and chance, puts each proposition within its (stretched) books, and lies
   nearest each proposition's deepest priced book's price, the sum of squared distances, each in that book's
   half-spread, least. A book whose bid and ask meet holds its proposition at its price by its bounds.
5. each proposition's range with the reading held: its probability where it closes, and otherwise the range the
   statements leave, with the chances that would settle it.

Each end of each range opens to the statements its linear program rests on: the rows its optimum's duals do not give 0.

HiGHS (highspy) solves every linear and convex quadratic program: https://ergo-code.github.io/HiGHS/dev/interfaces/python/
Its quadratic solver is held at its default tolerances, which solve where 1e-10 returned false infeasibility; the reading
is then made exact on the constraints that bind (`_exact`), and so is each share of a stretch between books of one depth.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import highspy
import numpy as np

INF = highspy.kHighsInf
TOL = 1e-9  # a probability, a stretch or a dual closer to 0 than this is 0
CLOSED = 1e-7  # a range narrower than this is one probability
HELD = 1e-10  # the slack a held reading keeps, for the linear programs' own tolerance
LIMIT = 2.0  # seconds: a program the solver has not finished by then has failed, and a stalled quadratic one is
# solved by linear programs instead (_nearest)


class Unfinished(Exception):
    """The solver did not finish a program within its time limit: the structure's figures could not be read."""


# ---------------------------------------------------------------- the statements, as conditions on the cases


@dataclass
class Condition:
    """A statement about which cases can happen: over `scope`, the assignments `bad` returns true for carry nothing."""
    scope: tuple[str, ...]
    bad: object  # (assignment dict) -> bool
    tags: tuple[str, ...]


def _links(links: list[dict]) -> tuple[list[Condition], list[str]]:
    """The links as conditions on the cases, and the auxiliary propositions a group of ways is chained through, so that
    no group of cases grows with the number of ways."""
    out: list[Condition] = []
    aux: list[str] = []

    def implies(a, b, tags, fa=True, tb=True):  # where a is fa, b is tb: a market's No side stands as false
        out.append(Condition((a, b), lambda w, a=a, b=b, fa=fa, tb=tb: w[a] == fa and w[b] != tb, tuple(tags)))

    groups: dict[tuple[str, str], list[dict]] = {}
    for link in links:
        rel = link["relation"]
        if rel == "implies":
            implies(link["from"], link["to"], [link["id"]], link.get("from_true", True), link.get("to_true", True))
        elif rel == "then":  # the composite is true where the first happens and the second follows: it implies both
            implies(link["from"], link["to"], [link["id"]])
        elif rel in ("and", "or"):
            groups.setdefault((rel, link["from"]), []).append(link)
        elif rel == "way":
            groups.setdefault(("way", f"{link['from']}\x00{link.get('side') or 'yes'}"), []).append(link)
    for (rel, key), group in groups.items():
        ids = [g["id"] for g in group]
        if rel == "and":  # each part implied by the composite, and the composite where every part is
            e, parts = key, [g["to"] for g in group]
            for g in group:
                implies(e, g["to"], [g["id"]])
            out.append(Condition((e, *parts), lambda w, e=e, ps=tuple(parts): all(w[p] for p in ps) and not w[e],
                                 tuple(ids)))
        elif rel == "or":  # each part implies the composite, and the composite holds only where a part does
            e, parts = key, [g["to"] for g in group]
            for g in group:
                implies(g["to"], e, [g["id"]])
            out.append(Condition((e, *parts), lambda w, e=e, ps=tuple(parts): w[e] and not any(w[p] for p in ps),
                                 tuple(ids)))
        else:  # ways: each lies within the side, they exclude one another, and together make it up
            parent, side = key.split("\x00")
            pos = side != "no"
            ways = [g["to"] for g in group]
            for g in group:
                out.append(Condition((g["to"], parent), lambda w, x=g["to"], p=parent, pos=pos: w[x] and w[p] != pos,
                                     (g["id"],)))
            if len(ways) == 1:
                out.append(Condition((ways[0], parent), lambda w, x=ways[0], p=parent, pos=pos: (w[p] == pos) != w[x],
                                     tuple(ids)))
                continue
            acc = ways[0]
            for k in range(1, len(ways)):
                last = k == len(ways) - 1
                nxt = parent if last else f"~{parent}|{side}|{k}"
                if not last:
                    aux.append(nxt)
                x = ways[k]
                if last:
                    def bad(w, a=acc, x=x, p=parent, pos=pos):
                        return (w[p] == pos) != (w[a] or w[x]) or (w[a] and w[x])
                    out.append(Condition((acc, x, parent), bad, tuple(ids)))
                else:
                    def bad(w, a=acc, x=x, u=nxt):
                        return w[u] != (w[a] or w[x]) or (w[a] and w[x])
                    out.append(Condition((acc, x, nxt), bad, (group[k]["id"],)))
                acc = nxt
    return out, aux


def _lines(lines: list[dict]) -> tuple[list[Condition], list[str]]:
    """Each line as conditions on the cases (MATH.md 1.2, "A ladder"): its members are true where one quantity falls in
    each one's set of values, a level on one date or the date a threshold is first reached, so the fields order the
    rungs and place a band between them. Each end of a member is a cut, "x > v" or "x >= v", and the cuts are chained,
    each implying the weaker one below it; a member that is a cut stands for it, another cut is an auxiliary
    proposition, and each other member is its lower cut and not its upper one. Each condition is tagged with the
    fields of the members it reads."""
    out: list[Condition] = []
    aux: list[str] = []
    for n, line in enumerate(lines):
        members = line["members"]  # pid -> (lo, lo closed, hi, hi closed), an unbounded end None
        cuts: dict[tuple[float, bool], str | None] = {}  # (v, strict): x > v where strict, else x >= v
        ends: dict[str, tuple] = {}
        for pid, (lo, lo_c, hi, hi_c) in members.items():
            low = None if lo is None or lo == -math.inf else (lo, not lo_c)
            high = None if hi is None or hi == math.inf else (hi, hi_c)  # x <= hi: not x > hi; x < hi: not x >= hi
            ends[pid] = (low, high)
            for c in (low, high):
                if c is not None:
                    cuts.setdefault(c, None)
            if low is not None and high is None and cuts.get(low) is None:
                cuts[low] = pid  # a rung stands for its own cut
        order = sorted(cuts)
        for k, c in enumerate(order):
            if cuts[c] is None:
                cuts[c] = f"~line|{n}|{k}"
                aux.append(cuts[c])
        tag = {c: tuple(f"{pid}.fields" for pid, (lo, hi) in ends.items() if c in (lo, hi)) for c in order}
        for a, b in zip(order, order[1:]):  # the stronger cut implies the weaker
            x, y = cuts[b], cuts[a]
            out.append(Condition((x, y), lambda w, x=x, y=y: w[x] and not w[y], tuple(dict.fromkeys(tag[a] + tag[b]))))
        for pid, (low, high) in ends.items():
            if high is None and low is not None and cuts[low] == pid:
                continue
            lv, hv = (cuts[low] if low else None), (cuts[high] if high else None)
            scope = tuple(v for v in (pid, lv, hv) if v is not None)
            out.append(Condition(scope, lambda w, m=pid, l=lv, h=hv: w[m] != ((w[l] if l else True) and
                                                                              not (w[h] if h else False)),
                                 (f"{pid}.fields",)))
    return out, aux


# ---------------------------------------------------------------- the junction tree


@dataclass
class Model:
    props: list[str]
    cliques: list[tuple[str, ...]]
    tree: list[tuple[int, int]]
    offset: list[int]
    n: int
    home: dict[str, int]
    conditions: list[Condition]
    chances: list[dict]
    books: list[dict]
    base: list[tuple[np.ndarray, float, float]] = field(default_factory=list)
    tables: dict[int, np.ndarray] = field(default_factory=dict)  # each clique's cases as truth values, once built


def _cliques(nodes: list[str], scopes: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """The cliques of a triangulation of the scopes' graph, eliminating at each step the node whose neighbourhood needs
    the fewest edges added, ties by fewest neighbours and then by order."""
    order = {v: i for i, v in enumerate(nodes)}
    nb: dict[str, set[str]] = {v: set() for v in nodes}
    for s in scopes:
        for a, b in itertools.permutations(s, 2):
            nb[a].add(b)
    left, found = set(nodes), []
    while left:
        def fill(v):
            ns = [u for u in nb[v] if u in left]
            return sum(1 for a, b in itertools.combinations(ns, 2) if b not in nb[a])
        v = min(left, key=lambda v: (fill(v), len([u for u in nb[v] if u in left]), order[v]))
        ns = [u for u in nb[v] if u in left]
        for a, b in itertools.combinations(ns, 2):
            nb[a].add(b)
            nb[b].add(a)
        found.append(frozenset([v, *ns]))
        left.remove(v)
    kept = [c for c in dict.fromkeys(found) if not any(c < d for d in found)]
    return [tuple(sorted(c, key=order.__getitem__)) for c in kept]


def compile(m: dict) -> Model:
    """The structure's cases, held as its cliques' and agreeing where they meet."""
    props = list(m["props"])
    conditions, aux = _links(m.get("links") or [])
    on_lines, line_aux = _lines(m.get("lines") or [])
    conditions, aux = conditions + on_lines, aux + line_aux
    nodes = props + aux
    chances = list(m.get("chances") or [])
    books = [b for b in (m.get("books") or []) if b["on"] in props]
    scopes = [c.scope for c in conditions] + [(c["of"], *c["given"], *c["given_not"]) for c in chances] + \
        [(v,) for v in nodes]
    cliques = _cliques(nodes, scopes)
    cliques.sort(key=lambda c: (-len(c), c))
    edges = sorted(((len(set(a) & set(b)), i, j) for (i, a), (j, b) in itertools.combinations(enumerate(cliques), 2)
                    if set(a) & set(b)), reverse=True)
    root = list(range(len(cliques)))

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    tree = []
    for _, i, j in edges:
        if find(i) != find(j):
            root[find(i)] = find(j)
            tree.append((i, j))
    offset, n = [], 0
    for c in cliques:
        offset.append(n)
        n += 2 ** len(c)
    home = {v: next(k for k, c in enumerate(cliques) if v in c) for v in nodes}
    model = Model(props, cliques, tree, offset, n, home, conditions, chances, books)
    for k, c in enumerate(cliques):  # each group's cases sum to 1
        r = np.zeros(n)
        r[offset[k]:offset[k] + 2 ** len(c)] = 1.0
        model.base.append((r, 1.0, 1.0))
    for i, j in tree:  # neighbouring groups agree on what they share
        shared = tuple(sorted(set(cliques[i]) & set(cliques[j])))
        for v in itertools.product((False, True), repeat=len(shared)):
            want = dict(zip(shared, v))
            r = _indicator(model, i, want) - _indicator(model, j, want)
            model.base.append((r, 0.0, 0.0))
    return model


def _atoms(model: Model, k: int):
    """Clique k's cases, each as (column, assignment)."""
    c = model.cliques[k]
    for t, v in enumerate(itertools.product((False, True), repeat=len(c))):
        yield model.offset[k] + t, dict(zip(c, v))


def _clique_of(model: Model, scope) -> int:
    scope = set(scope)
    found = [k for k, c in enumerate(model.cliques) if scope <= set(c)]
    if not found:
        raise ValueError(f"no group holds {sorted(scope)} together")
    return min(found, key=lambda k: len(model.cliques[k]))


def _table(model: Model, k: int) -> np.ndarray:
    """Clique k's cases as truth values, a row for each case in the order of its columns and a column for each of its
    propositions."""
    if k not in model.tables:
        c = model.cliques[k]
        model.tables[k] = np.array(list(itertools.product((False, True), repeat=len(c))), dtype=bool).reshape(-1, len(c))
    return model.tables[k]


def _match(model: Model, k: int, want: dict) -> np.ndarray:
    """Which of clique k's cases have each proposition of `want` as it says."""
    table, at = _table(model, k), {v: i for i, v in enumerate(model.cliques[k])}
    found = np.ones(len(table), dtype=bool)
    for x, v in want.items():
        found &= table[:, at[x]] == v
    return found


def _indicator(model: Model, k: int, want: dict) -> np.ndarray:
    r = np.zeros(model.n)
    r[model.offset[k]:model.offset[k] + 2 ** len(model.cliques[k])] = _match(model, k, want)
    return r


def prob(model: Model, want: dict) -> np.ndarray:
    """The row giving the probability of a case of some propositions held in one group: {proposition: True or False}."""
    return _indicator(model, _clique_of(model, want), want)


def _condition_row(model: Model, c: Condition) -> np.ndarray:
    k = _clique_of(model, c.scope)
    r = np.zeros(model.n)
    for col, a in _atoms(model, k):
        if c.bad(a):
            r[col] = 1.0
    return r


def chance_row(model: Model, of: str, given, given_not, value: float) -> np.ndarray:
    """P(of and cond) - value * P(cond) = 0."""
    k = _clique_of(model, (of, *given, *given_not))
    cond = _match(model, k, {**dict.fromkeys(given, True), **dict.fromkeys(given_not, False)})
    is_of = _table(model, k)[:, model.cliques[k].index(of)]
    r = np.zeros(model.n)
    r[model.offset[k]:model.offset[k] + len(cond)] = np.where(cond, is_of.astype(float) - value, 0.0)
    return r


# ---------------------------------------------------------------- the programs


class Rows:
    """A set of rows, each with its bounds and the statements it carries, over the model's cases and any added columns."""

    def __init__(self, n: int):
        self.n, self.rows, self.lo, self.hi, self.tags = n, [], [], [], []

    def add(self, r: np.ndarray, lo: float, hi: float, tags=()):
        self.rows.append(r)
        self.lo.append(lo)
        self.hi.append(hi)
        self.tags.append(tuple(tags))
        return len(self.rows) - 1

    def copy(self) -> "Rows":
        out = Rows(self.n)
        out.rows, out.lo, out.hi, out.tags = list(self.rows), list(self.lo), list(self.hi), list(self.tags)
        return out


class Program:
    """One HiGHS model over a set of rows, solved for many objectives: min c.x (+ x'Qx / 2, Q diagonal, given by its
    entries {column: value}), each column within its bounds."""

    def __init__(self, rows: Rows, col_lo=None, col_hi=None, Q: dict[int, float] | None = None, extra: int = 0):
        n = rows.n + extra
        self.n, self.m, self.tags = n, len(rows.rows), rows.tags
        self.quadratic = Q is not None
        self.h = highspy.Highs()
        self.h.setOptionValue("output_flag", False)
        self.h.setOptionValue("time_limit", LIMIT)
        if Q is None:
            self.h.setOptionValue("primal_feasibility_tolerance", 1e-10)
            self.h.setOptionValue("dual_feasibility_tolerance", 1e-10)
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = n, self.m
        lp.col_cost_ = np.zeros(n)
        lp.col_lower_ = np.asarray(col_lo if col_lo is not None else np.zeros(n), float)
        lp.col_upper_ = np.asarray(col_hi if col_hi is not None else np.ones(n), float)
        lp.row_lower_ = np.asarray(rows.lo, float)
        lp.row_upper_ = np.asarray(rows.hi, float)
        starts, idx, vals = [0], [], []
        for r in rows.rows:  # row by row: a structure has far fewer statements than cases
            nz = np.nonzero(r)[0]
            idx += nz.tolist()
            vals += r[nz].tolist()
            starts.append(len(idx))
        lp.a_matrix_.format_ = highspy.MatrixFormat.kRowwise
        lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = starts, idx, vals
        self.h.passModel(lp)
        if Q is not None:
            hess = highspy.HighsHessian()
            hess.dim_ = n
            hess.format_ = highspy.HessianFormat.kTriangular
            st, ix, vl = [0], [], []
            for j in range(n):
                if Q.get(j):
                    ix.append(j)
                    vl.append(float(Q[j]))
                st.append(len(ix))
            hess.start_, hess.index_, hess.value_ = st, ix, vl
            self.h.passHessian(hess)

    def solve(self, cost=None):
        """(solved, x, objective, row duals); `cost` a vector of the columns' costs. A linear program is unsolved only
        where no set holds its rows; one the solver does not finish raises Unfinished. A quadratic one that does not
        finish is unsolved, and its nearest point is found by linear programs instead (_nearest)."""
        if cost is not None:
            c = np.zeros(self.n)
            c[:len(cost)] = cost
            self.h.changeColsCost(self.n, np.arange(self.n, dtype=np.int32), c)
        self.h.run()
        status = self.h.getModelStatus()
        if status != highspy.HighsModelStatus.kOptimal:
            if self.quadratic or status in (highspy.HighsModelStatus.kInfeasible,
                                            highspy.HighsModelStatus.kUnboundedOrInfeasible):
                return False, None, None, None
            raise Unfinished(str(status))
        sol = self.h.getSolution()
        return True, np.array(sol.col_value), self.h.getInfo().objective_function_value, np.array(sol.row_dual)

    def set_row(self, i: int, lo: float, hi: float):
        self.h.changeRowBounds(i, lo, hi)


def _rests(prog: Program, duals) -> list[str]:
    """The statements a solution's duals do not give 0, each once, in order."""
    out: list[str] = []
    for d, tags in zip(duals, prog.tags):
        if abs(d) > TOL:
            out += [t for t in tags if t not in out]
    return out


def _extent(prog: Program, row: np.ndarray, *, why: bool = True) -> dict | None:
    """The least and the most of a row's quantity, with what each rests on; None where no set holds."""
    out = {}
    for end, sign in (("lo", 1.0), ("hi", -1.0)):
        ok, x, obj, duals = prog.solve(sign * row)
        if not ok:
            return None
        out[end] = sign * obj
        out[f"{end}_x"] = x
        if why:
            out[f"{end}_rests"] = _rests(prog, duals)
    out["lo"], out["hi"] = min(out["lo"], out["hi"]), max(out["lo"], out["hi"])
    return out


def _clip(x: float) -> float:
    return 0.0 if abs(x) < TOL else 1.0 if abs(x - 1.0) < TOL else x


# ---------------------------------------------------------------- the reading


def statements(model: Model) -> Rows:
    """The cases' own rows, every link, and every chance."""
    rows = Rows(model.n)
    for r, lo, hi in model.base:
        rows.add(r, lo, hi)
    for c in model.conditions:
        rows.add(_condition_row(model, c), 0.0, 0.0, c.tags)
    for c in model.chances:
        rows.add(chance_row(model, c["of"], c["given"], c["given_not"], c["value"]), 0.0, 0.0, (c["key"],))
    return rows


def _book_bounds(book: dict) -> tuple[float, float]:
    return (book["bid"] if book.get("bid") is not None else 0.0, book["ask"] if book.get("ask") is not None else 1.0)


def _depth(book: dict) -> float:
    """Depth is the money at a book's quotes, the smaller side's (3.3): a book quoted on one side has none."""
    if book.get("bid") is None or book.get("ask") is None:
        return 0.0
    return float(book.get("depth") or 0.0)


def _half(book: dict) -> float:
    """Half a book's spread; a side with no quote is the interval's boundary there, 0 for the bid and 1 for the ask
    (3.3)."""
    lo, hi = _book_bounds(book)
    return (hi - lo) / 2.0


def with_books(model: Model, rows: Rows, stretch: dict[str, tuple[float, float]] | None = None) -> Rows:
    out = rows.copy()
    for b in model.books:
        lo, hi = _book_bounds(b)
        below, above = (stretch or {}).get(b["link"], (0.0, 0.0))
        out.add(prob(model, {b["on"]: True}), max(0.0, lo - below), min(1.0, hi + above), (f"{b['link']}:book",))
    return out


def feasible(rows: Rows) -> bool:
    return Program(rows).solve(np.zeros(rows.n))[0]


def stretches(model: Model, rows: Rows) -> dict[str, tuple[float, float]]:
    """How far each taken book is stretched past its bid and its ask so that it holds with the rows (MATH.md 1.2): the
    deepest books first, the next deepest with the deeper held where they settled, and so on, books shallower than
    those being settled free to take what is left. Books of equal depth share a stretch by the least sum of squared
    stretches, each in its half-spread, those with no spread first by their least total stretch."""
    books = model.books
    if not books or feasible(with_books(model, rows)):
        return {}
    levels: list[list[int]] = []
    for depth in sorted({_depth(b) for b in books}, reverse=True):
        same = [j for j, b in enumerate(books) if _depth(b) == depth]
        tight = [j for j in same if _half(books[j]) <= TOL]
        if tight:
            levels.append(tight)
        if len(same) > len(tight):
            levels.append([j for j in same if j not in tight])
    settled: dict[int, tuple[float, float]] = {}
    for level in levels:
        k = len(level)
        base = rows.copy()
        for j, (below, above) in settled.items():
            lo, hi = _book_bounds(books[j])
            base.add(prob(model, {books[j]["on"]: True}), max(0.0, lo - below), min(1.0, hi + above))
        plain = base.copy()
        for j in level:
            plain.add(prob(model, {books[j]["on"]: True}), *_book_bounds(books[j]))
        if feasible(plain):  # the books below can take it all
            settled.update(dict.fromkeys(level, (0.0, 0.0)))
            continue
        # columns: the cases, then each of this level's books' stretch below its bid, then above its ask
        r = Rows(model.n + 2 * k)
        for row, lo, hi in zip(base.rows, base.lo, base.hi):
            r.add(np.concatenate([row, np.zeros(2 * k)]), lo, hi)
        for i, j in enumerate(level):
            lo, hi = _book_bounds(books[j])
            p = prob(model, {books[j]["on"]: True})
            e = np.zeros(2 * k)
            e[i] = 1.0
            r.add(np.concatenate([p, e]), lo, INF)  # P >= bid - below
            e = np.zeros(2 * k)
            e[k + i] = -1.0
            r.add(np.concatenate([p, e]), -INF, hi)  # P <= ask + above
        cols = np.concatenate([np.ones(model.n), np.ones(2 * k)])
        if _half(books[level[0]]) <= TOL:  # the least total stretch
            cost = np.concatenate([np.zeros(model.n), np.ones(2 * k)])
            ok, z, _, _ = Program(r, np.zeros(len(cols)), cols).solve(cost)
        else:
            w = np.array([1.0 / _half(books[j]) ** 2 for j in level] * 2)
            Q = {model.n + i: 2.0 * x for i, x in enumerate(w)}
            L = np.zeros((2 * k, len(cols)))
            L[:, model.n:] = np.eye(2 * k)
            ok, z, _, _ = Program(r, np.zeros(len(cols)), cols, Q).solve(np.zeros(len(cols)))
            if not ok:  # the quadratic solver stalled: the same nearest point by linear programs
                z = _nearest(r, np.zeros(len(cols)), cols, L, np.zeros(2 * k), np.sqrt(w))
                ok = z is not None
            if ok:
                z = _exact(r, z, L, np.zeros(2 * k), np.sqrt(w))
        if not ok:
            raise RuntimeError("the books' stretches could not be found")
        for i, j in enumerate(level):
            settled[j] = (_clip(max(0.0, float(z[model.n + i]))), _clip(max(0.0, float(z[model.n + k + i]))))
    return {b["link"]: settled.get(j, (0.0, 0.0)) for j, b in enumerate(books)}


def _nearest(rows: Rows, col_lo, col_hi, L: np.ndarray, t: np.ndarray, w: np.ndarray) -> np.ndarray | None:
    """A solution of the rows whose quantities L z lie nearest t, the sum of (w (L z - t))^2 least, by Wolfe's
    minimum-norm point over the polytope of the scaled quantities, each step's farthest point from the simplex: the
    reading and a stretch share where the quadratic solver stalls. None where no solution holds."""
    prog = Program(rows, col_lo, col_hi)
    k = L.shape[0]

    def vertex(direction: np.ndarray):
        ok, z, _, _ = prog.solve(direction @ (np.diag(w) @ L))
        return (z, w * (L @ z - t)) if ok else (None, None)

    z, x = vertex(np.ones(k))
    if z is None:
        return None
    pts, zs, lam = [x], [z], np.array([1.0])
    for _ in range(500):
        cur = sum(l * p for l, p in zip(lam, pts))
        zv, v = vertex(cur)
        if zv is None or cur @ cur - cur @ v <= 1e-14 * max(1.0, cur @ cur):
            break
        pts.append(v)
        zs.append(zv)
        lam = np.append(lam, 0.0)
        while True:  # the affine minimiser of the points kept, or the farthest step toward it within their hull
            P = np.array(pts)
            G = P @ P.T
            A = np.block([[G, np.ones((len(pts), 1))], [np.ones((1, len(pts))), np.zeros((1, 1))]])
            mu = np.linalg.lstsq(A, np.append(np.zeros(len(pts)), 1.0), rcond=None)[0][:len(pts)]
            if np.all(mu > 1e-12):
                lam = mu
                break
            neg = mu <= 1e-12
            step = min(lam[i] / (lam[i] - mu[i]) for i in range(len(pts)) if neg[i] and lam[i] - mu[i] > 0)
            lam = lam + step * (mu - lam)
            keep = lam > 1e-12
            pts = [p for p, k_ in zip(pts, keep) if k_]
            zs = [q for q, k_ in zip(zs, keep) if k_]
            lam = lam[keep] / lam[keep].sum()
    return sum(l * q for l, q in zip(lam, zs))


def _exact(rows: Rows, z: np.ndarray, L: np.ndarray, t: np.ndarray, w: np.ndarray) -> np.ndarray:
    """A quadratic program's solution made exact on the constraints that bind at the solver's: minimise
    sum (w (L z - t))^2 with every equality and every bound z meets held as equalities, and every column it gives
    nothing held at 0, a least squares in what they leave free. On the columns left free, with A the binding rows and
    P the projection onto what they leave free, z = z0 + P L' W (W L P L' W)^+ W (t - L z0), z0 the least solution of
    the binding rows: worked through a thin factorisation of A, so its cost grows with the binding rows and not with
    the square of the cases. Kept where it holds every row and stays within the solver's tolerance of its objective's
    quantities; otherwise the solver's own."""
    free = z >= 1e-8
    eq, rhs = [], []
    for row, lo, hi in zip(rows.rows, rows.lo, rows.hi):
        v = float(row @ z)
        if lo == hi or abs(v - lo) < 1e-7:
            eq.append(row[free])
            rhs.append(lo)
        elif abs(v - hi) < 1e-7:
            eq.append(row[free])
            rhs.append(hi)
    Lf, W = L[:, free], np.diag(w)
    if eq:
        U, s, Vt = np.linalg.svd(np.array(eq), full_matrices=False)
        rank = int((s > 1e-10 * max(s[0], 1.0)).sum()) if len(s) else 0
        U, s, Vt = U[:, :rank], s[:rank], Vt[:rank]
        z0 = Vt.T @ ((U.T @ np.array(rhs)) / s)
        PLt = Lf.T - Vt.T @ (Vt @ Lf.T)  # P L', what the binding rows leave free of each quantity
    else:
        z0, PLt = np.zeros(int(free.sum())), Lf.T
    G = W @ Lf @ PLt @ W
    zf = z0 + PLt @ W @ np.linalg.lstsq(G, W @ (t - Lf @ z0), rcond=None)[0]
    zs = np.zeros(len(z))
    zs[free] = zf
    holds = np.all(zs > -1e-9) and all(lo - 1e-9 <= float(row @ zs) <= hi + 1e-9
                                        for row, lo, hi in zip(rows.rows, rows.lo, rows.hi))
    return zs if holds and np.all(np.abs(L @ zs - L @ z) < 1e-5) else z


def targets(model: Model) -> dict[str, dict]:
    """Each proposition's price to stay near: its deepest taken book's that has a price and a spread, the first linked
    among books of equal depth. A book whose bid and ask meet holds its proposition by its bounds."""
    out: dict[str, dict] = {}
    for b in model.books:
        if b.get("price") is None or b.get("bid") is None or b.get("ask") is None:
            continue
        have = out.get(b["on"])
        if have is None or _depth(b) > have["depth"]:
            out[b["on"]] = {"link": b["link"], "price": b["price"], "half": _half(b), "depth": _depth(b)}
    return {p: t for p, t in out.items() if t["half"] > TOL}


def reading(model: Model, rows: Rows) -> dict[str, float] | None:
    """Each targeted proposition's probability in the set nearest the prices, in half-spreads; None where no set holds."""
    tg = targets(model)
    if not tg:
        return {}
    names = list(tg)
    k = len(names)
    r = Rows(model.n + k)
    for row, lo, hi in zip(rows.rows, rows.lo, rows.hi):
        r.add(np.concatenate([row, np.zeros(k)]), lo, hi)
    for j, p in enumerate(names):  # y_p = P(p)
        e = np.zeros(k)
        e[j] = -1.0
        r.add(np.concatenate([prob(model, {p: True}), e]), 0.0, 0.0)
    Q: dict[int, float] = {}
    cost = np.zeros(model.n + k)
    for j, p in enumerate(names):
        w = 1.0 / tg[p]["half"] ** 2
        Q[model.n + j] = 2.0 * w
        cost[model.n + j] = -2.0 * w * tg[p]["price"]
    prog = Program(r, np.zeros(model.n + k), np.ones(model.n + k), Q)
    ok, x, _, _ = prog.solve(cost)
    M = np.zeros((k, model.n + k))
    M[:, model.n:] = np.eye(k)
    if not ok:  # the quadratic solver stalled: the same nearest point by linear programs
        x = _nearest(r, np.zeros(model.n + k), np.ones(model.n + k), M,
                     np.array([tg[p]["price"] for p in names]), np.array([1.0 / tg[p]["half"] for p in names]))
        if x is None:
            return None
    x = _exact(r, x, M, np.array([tg[p]["price"] for p in names]), np.array([1.0 / tg[p]["half"] for p in names]))
    return {p: float(x[model.n + j]) for j, p in enumerate(names)}


# ---------------------------------------------------------------- what cannot hold together


def _groups(rows: Rows) -> dict[str, list[int]]:
    """Each statement's rows."""
    out: dict[str, list[int]] = {}
    for i, tags in enumerate(rows.tags):
        for t in tags:
            out.setdefault(t, []).append(i)
    return out


def _filter(rows: Rows, holds, order: list[str]) -> list[str]:
    """A smallest set of statements for which `holds(program)` is false, each one needed (a deletion filter): each
    statement in turn is set aside, and kept where setting it aside makes `holds` true."""
    groups = _groups(rows)
    prog = Program(rows)
    kept: list[str] = []
    for s in order:
        for i in groups[s]:
            prog.set_row(i, -INF, INF)
        if holds(prog):
            kept.append(s)
            for i in groups[s]:
                prog.set_row(i, rows.lo[i], rows.hi[i])
    return kept


def _order(model: Model, rows: Rows) -> list[str]:
    """The statements, chances first, so a set names the links it needs among them."""
    tags = list(_groups(rows))
    chances = [c["key"] for c in model.chances]
    return [t for t in tags if t in chances] + [t for t in tags if t not in chances]


def conflicts(model: Model, rows: Rows) -> list[dict]:
    """Each smallest set of links and chances that cannot hold together, until setting aside one member of each set
    lets the rest hold; and each chance given some propositions that the statements leave no probability, with the
    smallest set of statements that leave it none."""
    out: list[dict] = []
    order = _order(model, rows)
    groups = _groups(rows)
    prog = Program(rows)
    zero = np.zeros(rows.n)
    aside: set[str] = set()
    while not prog.solve(zero)[0]:
        found = _filter(_without(rows, groups, aside), lambda p: p.solve(zero)[0],
                        [s for s in order if s not in aside])
        out.append({"statements": found})
        drop = next((s for s in found if s in {c["key"] for c in model.chances}), found[0])
        aside.add(drop)
        for i in groups[drop]:
            prog.set_row(i, -INF, INF)
    if out:
        return out
    for c in model.chances:  # a chance given some propositions needs their case to be possible
        if not c["given"] and not c["given_not"]:
            continue
        cond = {**dict.fromkeys(c["given"], True), **dict.fromkeys(c["given_not"], False)}
        row = prob(model, cond)
        ok, _, obj, _ = prog.solve(-row)
        if ok and -obj > TOL:
            continue
        found = _filter(rows, lambda p, row=row: (lambda r: r[0] and -r[2] > TOL)(p.solve(-row)), order)
        if c["key"] not in found:
            found = [c["key"], *found]
        entry = {"statements": found, "leaves_no_probability": cond}
        if not any(set(e["statements"]) == set(found) for e in out):
            out.append(entry)
    # each smallest group (1.10): a group that holds a listed group within it is that group's conflict again
    return [e for e in out if not any(set(o["statements"]) < set(e["statements"]) for o in out)]


def _without(rows: Rows, groups: dict[str, list[int]], aside: set[str]) -> Rows:
    out = rows.copy()
    for s in aside:
        for i in groups[s]:
            out.lo[i], out.hi[i] = -INF, INF
    return out


# ---------------------------------------------------------------- the whole reading


def read(m: dict) -> dict:
    """The structure read whole (MATH.md 1.2): for each proposition, the range the markets imply, and its probability or the
    range the statements leave; what cannot hold together; and each stretched book. Unfinished, with how many
    propositions one group of cases ties together, where the solver does not finish a program in time."""
    model = compile(m)
    try:
        return _read(model)
    except Unfinished:
        k = max((len(c) for c in model.cliques), default=0)
        raise Unfinished(f"its links and chances tie {k} propositions into one group of {2 ** k:,} cases, more than "
                         f"can be read. A chance given fewer propositions makes smaller groups.") from None


def _read(model: Model) -> dict:
    base = statements(model)
    props = model.props
    out: dict = {"props": {p: {} for p in props}, "cannot_hold": [], "stretched": []}

    # the range the markets imply: the links and the taken books, before any chance
    links_only = Rows(model.n)
    for r, lo, hi, t in zip(base.rows, base.lo, base.hi, base.tags):
        if not any(tag in {c["key"] for c in model.chances} for tag in t):
            links_only.add(r, lo, hi, t)
    implied_stretch = stretches(model, links_only) if feasible(links_only) else None
    if implied_stretch is not None:
        prog = Program(with_books(model, links_only, implied_stretch))
        for p in props:
            got = _extent(prog, prob(model, {p: True}))
            if got is not None:
                lo, hi = _clip(got["lo"]), _clip(got["hi"])
                out["props"][p]["range"] = {"lo": lo, "hi": hi, "lo_rests": got["lo_rests"], "hi_rests": got["hi_rests"]}
        if _stretched(model, implied_stretch):
            out["implied_stretched"] = _stretched(model, implied_stretch)

    found = conflicts(model, base)
    if found:
        out["cannot_hold"] = found
        return out

    stretch = stretches(model, base)
    out["stretched"] = _stretched(model, stretch)
    rows = with_books(model, base, stretch)
    y = reading(model, rows)
    if y is None:  # the stretched books hold by construction: no set means the solver did not finish
        raise Unfinished("the reading")
    tg = targets(model)
    held = rows.copy()
    for p, v in y.items():
        held.add(prob(model, {p: True}), v - HELD, v + HELD, (f"{tg[p]['link']}:price",))
    prog = Program(held)
    if not prog.solve(np.zeros(held.n))[0]:  # the reading held at its own tolerance
        held = rows.copy()
        for p, v in y.items():
            held.add(prob(model, {p: True}), v - 1e-8, v + 1e-8, (f"{tg[p]['link']}:price",))
        prog = Program(held)
    # a chance stated on its own is its proposition's probability, exactly, wherever the statements hold together
    given_nothing = {c["of"]: c["value"] for c in model.chances if not c["given"] and not c["given_not"]}
    for p in props:
        node = out["props"][p]
        if p in tg:
            got = _extent(prog, prob(model, {p: True}))
            if got is None:  # the reading is held within its own rows: no set means the solver did not finish
                raise Unfinished(p)
            rule = "chance" if p in given_nothing else "market" if abs(y[p] - tg[p]["price"]) < 1e-9 else "read"
            value = given_nothing[p] if rule == "chance" else tg[p]["price"] if rule == "market" else _clip(y[p])
            node.update(p=value, rests=_union(got["lo_rests"], got["hi_rests"]) if got else [], target=tg[p],
                        rule=rule)
            mine = any(c["of"] == p and (c["given"] or c["given_not"]) for c in model.chances)
            if rule != "chance" and mine:  # its chances fix it, with every other book at its reading: derived
                own = [i for i, t in enumerate(held.tags) if t == (f"{tg[p]['link']}:price",)]
                for i in own:
                    prog.set_row(i, -INF, INF)
                alone = _extent(prog, prob(model, {p: True}))
                if alone is not None and alone["hi"] - alone["lo"] <= CLOSED:
                    node.update(rule="derived", rests=_union(alone["lo_rests"], alone["hi_rests"]))
                    node["terms"] = _terms(model, prog, p)
                for i in own:
                    prog.set_row(i, held.lo[i], held.hi[i])
            continue
        got = _extent(prog, prob(model, {p: True}))
        if got is None:
            raise Unfinished(p)
        lo, hi = _clip(got["lo"]), _clip(got["hi"])
        if hi - lo <= CLOSED:
            node.update(p=given_nothing.get(p, _clip((lo + hi) / 2.0)), rests=_union(got["lo_rests"], got["hi_rests"]),
                        rule="chance" if p in given_nothing else "derived")
            node["terms"] = _terms(model, prog, p)
        else:
            node["open"] = {"lo": lo, "hi": hi, "lo_rests": got["lo_rests"], "hi_rests": got["hi_rests"],
                            **_closers(model, held, p, got, out)}
    _no_circle(out["props"])
    return out


def _union(a, b) -> list[str]:
    return list(dict.fromkeys([*a, *b]))


def _stretched(model: Model, stretch: dict) -> list[dict]:
    out = []
    for b in model.books:
        below, above = stretch.get(b["link"], (0.0, 0.0))
        if below > TOL or above > TOL:
            out.append({"link": b["link"], "on": b["on"], "bid": b.get("bid"), "ask": b.get("ask"),
                        "depth": _depth(b), "below": _clip(below), "above": _clip(above)})
    return out


def _terms(model: Model, prog: Program, p: str) -> list[dict] | None:
    """A probability its chances fix, as total probability over what they are given (MATH.md 1.2): each case of those
    propositions that can happen, with its probability and the share of it where p is true, so that the cases' shares
    of their probabilities sum to p. Each is read where the statements settle it: a case's probability, with the
    statements its least and its most rest on (`rests`), or where they leave it open, its range; the share by the
    chance that states it, by a link that leaves it 0 or 1, or read from the case's probability and p's within it;
    and `joint`, the probability of the case and p together, what the case gives p, read from the same program, a
    figure where the statements settle it and its least and most where they leave it open."""
    mine = [c for c in model.chances if c["of"] == p and (c["given"] or c["given_not"])]
    if not mine:
        return None
    over = tuple(dict.fromkeys(g for c in mine for g in (*c["given"], *c["given_not"])))
    try:
        _clique_of(model, (p, *over))
    except ValueError:
        return None
    out = []
    for v in itertools.product((True, False), repeat=len(over)):
        cell = dict(zip(over, v))
        case = _extent(prog, prob(model, cell))
        if case is None:
            return None
        if case["hi"] <= TOL:  # a case that cannot happen
            continue
        stated = next((c for c in mine if set(c["given"]) == {g for g in over if cell[g]}
                       and set(c["given_not"]) == {g for g in over if not cell[g]}), None)
        entry = {"given": [g for g in over if cell[g]], "given_not": [g for g in over if not cell[g]]}
        closed = case["hi"] - case["lo"] <= CLOSED
        if closed:
            entry["p"] = _clip((case["lo"] + case["hi"]) / 2.0)
        else:
            entry["range"] = {"lo": _clip(case["lo"]), "hi": _clip(case["hi"])}
        entry["rests"] = _union(case["lo_rests"], case["hi_rests"])
        joint = _extent(prog, prob(model, {**cell, p: True}), why=False)
        if joint is not None:
            entry["joint"] = (_clip((joint["lo"] + joint["hi"]) / 2.0) if joint["hi"] - joint["lo"] <= CLOSED else
                              {"lo": _clip(joint["lo"]), "hi": _clip(joint["hi"])})
        if stated is not None:
            entry.update(share=_clip(stated["value"]), chance=stated["key"])
        elif _most(prog, prob(model, {**cell, p: True})) <= TOL:
            entry.update(share=0.0, by="links")
        elif _most(prog, prob(model, {**cell, p: False})) <= TOL:
            entry.update(share=1.0, by="links")
        elif closed and joint is not None and joint["hi"] - joint["lo"] <= CLOSED:
            entry["share"] = _clip((joint["lo"] + joint["hi"]) / 2.0 / entry["p"])
        out.append(entry)
    return out


def _closers(model: Model, held: Rows, p: str, got: dict, out: dict) -> dict:
    """What would settle the range p's statements leave, as alternatives, each one way to settle it:
    (a) for each set of propositions p's chances are given, p's chances given each case of them that no chance of p
        states, that can happen, and where no link settles p;
    (b) where p states none, the same for each proposition p is linked to;
    (c) where p's own cases are all settled, the chance of one of the propositions its chances are given, given
        another: how they relate is what is left open.
    Each is kept where stating it closes p at the values the analyst could state (`_allowed`), and of each set the
    fewest of its chances that close p are named: a chance left out is one the statements already fix, which stated
    at another value could only conflict. Where none closes p and what p rests on is open, closing that closes p
    (`with`). A chance of p stated on its own is never named: what would settle p is a chance given what the
    statements tie it to, and where they tie it to nothing that closes it, nothing is named."""
    mine = [c for c in model.chances if c["of"] == p]
    scopes = list(dict.fromkeys(tuple(dict.fromkeys((*c["given"], *c["given_not"]))) for c in mine
                                if c["given"] or c["given_not"]))
    if not scopes:
        scopes = list(dict.fromkeys((q,) for c in model.conditions if p in c.scope for q in c.scope
                                    if q != p and q in model.props))
        low, high = _rungs_about(model, p)
        if low or high:  # a band between rungs lies within the one below and not the one above, by its fields, so
            ask = [{"of": p, "given": [low] if low else [], "given_not": [high] if high else []}]  # its chance given
            leaning = [q for q in (low, high) if q and _left_open(model, held, q)]  # them settles it by arithmetic
            return {"settled_by": [ask], **({"with": leaning} if leaning else {})}
    prog = Program(held)
    at = _allowed(model, prog, p, got)
    ways: list[list[dict]] = []
    for scope in scopes:
        cells = []
        for v in itertools.product((True, False), repeat=len(scope)):
            cell = dict(zip(scope, v))
            if any(set(c["given"]) == {g for g in scope if cell[g]} and
                   set(c["given_not"]) == {g for g in scope if not cell[g]} for c in mine):
                continue
            if _most(prog, prob(model, cell)) <= TOL:  # a case that cannot happen
                continue
            if _most(prog, prob(model, {**cell, p: True})) <= TOL or \
                    _most(prog, prob(model, {**cell, p: False})) <= TOL:  # a link settles p there
                continue
            cells.append({"of": p, "given": [g for g in scope if cell[g]], "given_not": [g for g in scope if not cell[g]]})
        ways += [alt for alt in _fewest(model, held, p, cells, at) if alt not in ways]
    if not ways:  # (c) how the propositions p's chances are given relate
        for scope in scopes:
            for a, b in itertools.permutations(scope, 2):
                ask = [{"of": a, "given": [b], "given_not": []}]
                if ask not in ways and _closes(model, held, p, ask, at):
                    ways.append(ask)
    leaning = sorted({q for s in scopes for q in s if _left_open(model, held, q)})
    if ways:
        return {"settled_by": ways, **({"with": leaning} if leaning else {})}
    if leaning:  # what it rests on is open: closing that closes it
        return {"settled_by": [], "with": leaning}
    return {"settled_by": []}


def _rungs_about(model: Model, p: str) -> tuple[str | None, str | None]:
    """For a proposition a line's fields place between two of its cuts (MATH.md 1.2), the rungs nearest outside it: the
    nearest proposition of the line at or below its lower end, and at or above its upper end, found along the line's
    chain of cuts (_lines), where its own ends are cuts no proposition stands for."""
    def chained(c) -> bool:
        return len(c.scope) == 2 and bool(c.tags) and all(t.endswith(".fields") for t in c.tags)

    def walk(v: str, stronger: bool) -> str | None:
        seen = set()
        while v not in model.props:
            if v in seen:
                return None
            seen.add(v)
            nxt = next((c.scope[0] if stronger else c.scope[1] for c in model.conditions
                        if chained(c) and c.scope[1 if stronger else 0] == v), None)
            if nxt is None:
                return None
            v = nxt
        return v

    member = next((c for c in model.conditions if c.scope and c.scope[0] == p and len(c.scope) == 3
                   and c.tags == (f"{p}.fields",)), None)
    if member is None or (member.scope[1] in model.props and member.scope[2] in model.props):
        return None, None  # no band, or one whose ends are rungs: the links' own search names what settles it
    low, high = walk(member.scope[1], False), walk(member.scope[2], True)
    return (low if low != p else None), (high if high != p else None)


def _no_circle(props: dict) -> None:
    """Propositions that rest on one another in a circle, none of them with a chance that would settle it, have
    nothing reaching them: each names nothing that would settle it, and none points to the others."""
    rests = {p: list(node["open"].get("with") or []) for p, node in props.items() if node.get("open")}
    closable = {p for p, node in props.items() if node.get("open") and node["open"].get("settled_by")}

    def reach(q: str) -> set[str]:
        seen, todo = set(), [q]
        while todo:
            x = todo.pop()
            for y in rests.get(x, []):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        return seen

    for p, on in rests.items():
        kept = [q for q in on if p not in reach(q) or ({q} | reach(q)) & closable]
        if kept != on:
            if kept:
                props[p]["open"]["with"] = kept
            else:
                props[p]["open"].pop("with", None)


def _left_open(model: Model, held: Rows, q: str) -> bool:
    got = _extent(Program(held), prob(model, {q: True}), why=False)
    return got is not None and got["hi"] - got["lo"] > CLOSED


def _most(prog: Program, row: np.ndarray) -> float:
    ok, _, obj, _ = prog.solve(-row)
    return -obj if ok else 0.0


def _allowed(model: Model, prog: Program, p: str, got: dict) -> list[np.ndarray]:
    """Two sets the statements allow, at which a candidate is checked: each a mix of the sets that put p, and each other
    proposition they leave open, lowest and highest, at weights unequal within each mix and different between the two.
    A chance stated at its share in either takes a value no relation among those extreme sets fixes. The sets that put
    p lowest and highest alone are not enough: where they give a proposition p could be given the same probability,
    p's chances given it and given not it are equal at every set between them, ends included, and stating both would
    seem to fix p while that proposition is open. Every case either extreme gives a probability keeps one in a mix, so
    a chance given it can be stated there."""
    ends = [got["lo_x"], got["hi_x"]]
    for q in model.props:
        if q != p:
            e = _extent(prog, prob(model, {q: True}), why=False)
            if e is not None and e["hi"] - e["lo"] > CLOSED:
                ends += [e["lo_x"], e["hi_x"]]
    V = np.array(ends)
    up = np.arange(1.0, len(ends) + 1.0)
    down = up[::-1] ** 2
    return [(up / up.sum()) @ V, (down / down.sum()) @ V]


def _fewest(model: Model, held: Rows, p: str, cells: list[dict], at: list[np.ndarray]) -> list[list[dict]]:
    """The fewest of these chances that close p, each such set one alternative; none where all of them do not."""
    if not cells or not _closes(model, held, p, cells, at):
        return []
    for r in range(1, len(cells) + 1):
        found = [list(s) for s in itertools.combinations(cells, r) if _closes(model, held, p, list(s), at)]
        if found:
            return found
    return [cells]


def _closes(model: Model, held: Rows, p: str, candidates: list[dict], at: list[np.ndarray]) -> bool:
    """Whether stating these chances fixes p, at each of the sets given: each chance at the share it has there."""
    for x in at:
        rows = held.copy()
        for c in candidates:
            cond = {**dict.fromkeys(c["given"], True), **dict.fromkeys(c["given_not"], False)}
            try:
                pc = float(prob(model, cond) @ x)
                share = float(prob(model, {**cond, c["of"]: True}) @ x) / pc if pc > TOL else 0.5
                rows.add(chance_row(model, c["of"], c["given"], c["given_not"], share), 0.0, 0.0)
            except ValueError:  # no group holds them together: a chance the cases held here cannot say
                return False
        got = _extent(Program(rows), prob(model, {p: True}), why=False)
        if got is None or got["hi"] - got["lo"] > CLOSED:
            return False
    return True


# ---------------------------------------------------------------- what settled markets decide (for the record)


def same_worlds(m: dict, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The pairs the links and the fields leave true in the same cases (MATH.md 1.9, 1.10): wherever one is true, so is the
    other. Read from the cases the links and lines allow, with no chance and no book: the probability of a less that
    of b is 0 at its highest and at its lowest, since a case where one holds without the other could carry it all.
    Links that cannot hold among themselves allow no case, and name no pair."""
    model = compile({**m, "chances": [], "books": []})
    rows = Rows(model.n)
    for r, lo, hi in model.base:
        rows.add(r, lo, hi)
    for c in model.conditions:
        rows.add(_condition_row(model, c), 0.0, 0.0, c.tags)
    prog = Program(rows)
    if not prog.solve(np.zeros(rows.n))[0]:
        return []
    out = []
    for a, b in pairs:
        d = prob(model, {a: True}) - prob(model, {b: True})
        if _most(prog, d) <= TOL and _most(prog, -d) <= TOL:
            out.append((a, b))
    return out


def decided(m: dict) -> dict:
    """Each proposition's range through the links alone with each settled market a book at 1 or 0, its bid and ask
    met (the record's outcomes, MATH.md 3.1 of the record): {proposition: {lo, hi, lo_rests, hi_rests}}, or, where the
    links and the settlements cannot hold together, {"cannot_hold": [statements]}."""
    model = compile(m)
    rows = Rows(model.n)
    for r, lo, hi, t in zip(*(lambda b: (b.rows, b.lo, b.hi, b.tags))(statements(model))):
        rows.add(r, lo, hi, t)
    rows = with_books(model, rows)
    prog = Program(rows)
    if not prog.solve(np.zeros(rows.n))[0]:
        return {"cannot_hold": _filter(rows, lambda q: q.solve(np.zeros(rows.n))[0], list(_groups(rows)))}
    out = {}
    for p in model.props:
        got = _extent(prog, prob(model, {p: True}))
        out[p] = {"lo": _clip(got["lo"]), "hi": _clip(got["hi"]), "lo_rests": got["lo_rests"],
                  "hi_rests": got["hi_rests"]}
    return out
