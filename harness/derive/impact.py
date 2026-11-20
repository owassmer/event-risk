"""Impact: what an event's worlds do to the portfolio (MATH.md 1.1, 1.3 to 1.7).

The worlds. An event's worlds are its outcomes left whole: its Yes and No sides, or the ways the analyst gives them
(1.1). Each has its probability, read from its structure (1.2), and the weight today's prices put on it: its
probability, or the weight the analyst states on the event or a way (`priced_in`), a figure or a market read live, the
worlds beneath it sharing it as our probabilities divide them (1.5). One world is the baseline, which the analyst names
on the event.

Which shock applies in a world: a consequence on the event gives its shock to every world on the other side from the
baseline, and one on a way to that way and the ways under it, the one nearest above a world applying there (1.3).
The three steps after the probabilities then read them (worlds.py does the arithmetic):
- each factor's compounded move against the baseline: a shock states its whole move or its residual, in points of its
  yield for a factor quoted as one; each factor whose whole move is not stated takes its mean given every condition
  stated, ln(1 + x_U) = cov(U, S) cov(S, S)^-1 ln(1 + x_S) for whole moves, over a named episode's covariance where
  the world has one; a factor stated at -1 stands at zero (1.3). The factors' compounded residuals are the world's z_w;
- each stated name's whole move against the baseline (1.4): the shock where it states the whole move, and where it
  states the residual, the name's response compounded with it, 1 + s = (1 + r)(1 + a);
- where each price stands against today (1.5): every price the store places is placed once, L_base = c / sum q (1 + s).
  The factors' placed moves, each less its regression on the earlier factors' residuals, are the world's u~.

Each holding is then valued in each world (1.6). A price left unstated stands against the baseline at its response,
exp(b . z_w); a name quoted in another currency at its local price times its rate, the rate the level of the factor
whose proxy it is, or else the currency's own response; a fund the look-through expands at its response, with what each
stated company it carries does beyond its response on the dollars it carries of it. A fund valued by its terms, a
multiple of one reference or its contracts, is worth what its terms give in each world, and is not placed: where it
stands at zero, its worlds come to more than its value today at the weights, and the difference is its expected value.
A statement on a fund valued from where it stands unstated sets each world's value from there: a whole move from the
baseline's, times 1 + s, and a residual from that world's, times 1 + a. A holding the analyst values world by world is
worth what its stated values give there (valuation.py). Read with other events (1.9), each name the rows move stands in
each joint world at the move they give it (`joint_prices`, `joint_responses`; together.py).

A world's P&L is the holdings' changes in value summed (1.1), read in three parts: each factor's dollar beta times its
placed move, g . u~_w; what each stated company does beyond its response, on every dollar of exposure to it; and the
curve, what compounding and a holding's terms add beyond those (1.6). Each name's share is its exposure times its placed
move, and each fund's remainder its contribution less what the companies and the funds it holds contribute, so the
names' shares and the remainders come to the holdings', and the holdings' to the world's P&L (1.7). The row's figures
are read from the worlds (worlds.row). A price at zero in every world today's prices carry cannot be placed: the event
then has no figures, and check() lists it (1.10).
"""

from __future__ import annotations

import math

from harness import schema
from harness.derive import channels, distribution, episode, option_funds, outcomes, structure, valuation, worlds
from harness.derive.channels import ordered_channels, unpriced_names
from harness.derive.engine import Derivation, Inputs, Refused, Unavailable
from harness.derive.markets import probability
from harness.derive.outcomes import root_of, tree as outcome_tree
from harness.derive.portfolio import held_name, ticker_of
from harness.derive.structure import node

TOL = 1e-9


# ---------------------------------------------------------------- what the impact reads of a price


def price_today(ctx: Inputs, target: str, proxies: dict[str, str], kinds: dict) -> dict:
    """The price a consequence moves, today (MATH.md 1.5): a factor's, its proxy's; a name's, that of the security it is
    priced through, by the look-through, or its own listing where the look-through books none. Each is the price Metric
    the store derives, with its basis."""
    if target in proxies:
        ticker = proxies[target]
    else:
        info = kinds.get(target) or {}
        ticker = info.get("security") or info.get("beta") or ticker_of(target)
    found = ctx.value("proxy_price", schema.proxy_metric_id(ticker))
    return {"c": found["output"].get("level"), "basis": found["basis"]}


def quoted_yields(ctx: Inputs) -> dict[str, dict]:
    """Each factor quoted as the yield its proxy pays (MATH.md 1.3; ONTOLOGY.md 2.1): its yield today and its proxy's duration."""
    return {c["id"]: {"yield": c["stated"]["yield"], "duration": c["stated"]["duration"]} for c in ordered_channels(ctx)
            if c["stated"].get("yield") is not None}


# ---------------------------------------------------------------- the weight today's prices put on the event


def _market_weight(ctx: Inputs, pid: str, mid: str) -> dict:
    """The weight a market that asks the event gives it (1.5): its probability as the proposition reads it, at its
    latest priced reading, with that reading's time (markets.probability)."""
    link = next((x for x in ctx.links(pid, "asks") if x["to"] == mid), None)
    if link is None:  # the store refuses removing it while priced_in names it
        raise Unavailable(f"{pid}'s priced_in names {mid}, which does not ask it")
    return {"market": mid, "link": link["id"], **probability(ctx, link["id"])}


def _weight(ctx: Inputs, pid: str, stated) -> dict | None:
    if stated is None:
        return None
    if schema.weight_market(stated):
        return _market_weight(ctx, pid, schema.weight_market(stated))
    return {"q": stated["value"], "stated": True}


# ---------------------------------------------------------------- consequences


def stated_move(link: dict) -> dict:
    """What a consequence states of its price's move (MATH.md 1.3): the figure, `shock`, and the place it is measured
    from, `against`: the baseline, or what the price follows."""
    s = link["stated"]["shock"]
    return {"shock": s["value"], schema.AGAINST: schema.against(s)}


def consequences(ctx: Inputs, pid: str) -> list[dict]:
    return sorted(ctx.links(pid, "consequence"), key=lambda link: link["to"])


def _statements(ctx: Inputs, pid: str) -> tuple[list[dict], list[tuple[str, dict]], list[dict]]:
    """An event's ways, at any depth, and what it and each way state of the prices in its worlds: each consequence,
    with the proposition it is on, and each value stated for a holding (`worth`). An event that states none and whose
    portfolio holds no contract has nothing to value."""
    way_nodes = outcome_tree(ctx, pid)
    ways = [w["id"] for w in way_nodes]
    stated_on = [(on, link) for on in (pid, *ways) for link in consequences(ctx, on)]  # the event's, and each way's
    worth_on = [link for on in (pid, *ways) for link in ctx.links(on, "worth")]
    held_markets = any(h["to"].startswith("market:") for h in ctx.links("portfolio", "holds"))
    if not stated_on and not worth_on and not held_markets:  # an event with no valuation waits on none of its exposures
        raise Unavailable(f"{pid} has no consequence: its impact comes from the shocks it states on the prices it "
                          f"moves")
    if ctx.object(pid)["stated"].get("baseline") is None:  # the store refuses a consequence until it names one
        raise Unavailable(f"{pid} names no baseline, the world its other worlds are measured from")
    return way_nodes, stated_on, worth_on


def _valued(pid: str, stated_on: list, worth_on: list, contracts: list, baseline: str, leaves: list[str]) -> None:
    """An event values something in its worlds, and its baseline is one of them."""
    if not stated_on and not worth_on and not contracts:
        raise Unavailable(f"{pid} has no consequence or held contract affected by its outcome")
    if baseline not in leaves:  # a side or a way that has since been given ways
        raise Unavailable(f"{pid}'s baseline names {baseline}, which has ways: the baseline is one world, a side left "
                          f"whole or a way left whole")


def _impact_inputs(ctx: Inputs, pid: str) -> dict:
    point = node(ctx, pid)
    p = point["output"].get("p")
    stated = ctx.object(pid)["stated"]
    way_nodes, stated_on, worth_on = _statements(ctx, pid)
    ways = [w["id"] for w in way_nodes]
    tree = ctx.value("outcomes", pid)
    found_tree = tree["output"]
    if found_tree.get("why") or p is None:  # its structure cannot hold, or leaves a world's probability open
        raise Unavailable(f"{pid}'s row, and the level of each price in its worlds, wait on the probabilities of its "
                          f"worlds. {found_tree.get('why') or 'Its statements leave them open.'}")
    nodes, leaves = found_tree["nodes"], found_tree["worlds"]
    receipt: dict = {}
    contracts, contract_states = valuation.contract_inputs(ctx, pid, found_tree, receipt=receipt)
    _valued(pid, stated_on, worth_on, contracts, stated["baseline"], leaves)
    shape = lambda n: {"side": nodes[n]["side"], "p": nodes[n]["p"], "parent": nodes[n]["parent"],  # noqa: E731
                       "title": nodes[n].get("title")}
    m = {"event": pid, "p": p, "point_basis": point["basis"], "baseline": stated["baseline"],
         "outcomes": tree["basis"],
         "worlds": [{"id": n, **shape(n)} for n in leaves],
         "tree": {n: {**shape(n), "ways": nodes[n]["ways"]} for n in nodes if nodes[n]["ways"]},
         "priced_in": {n: got for n, got in ((("yes" if x == pid else x), _weight(ctx, x, ctx.object(x)["stated"]
                                                                                     .get("priced_in")))
                                              for x in (pid, *ways)) if got is not None},
         **_reads(ctx, pid, stated, ways, stated_on, worth_on, found_tree),
         "contracts": contracts, "contract_states": contract_states}
    return _without(m, receipt.get("waiting"))


def _without(m: dict, waiting: dict | None) -> dict:
    """An event's manifest without the held contracts whose payoff waits (together._valued): they are no holding of
    its worlds, and are named with why (`waiting`)."""
    if not waiting:
        return m
    before = m.get("waiting") or {"holdings": []}
    waiting = {**waiting, "holdings": sorted({*before["holdings"], *waiting["holdings"]})}
    return {**m, "holds": {h: n for h, n in m["holds"].items() if h not in waiting["holdings"]}, "waiting": waiting}


def _reads(ctx: Inputs, pid: str, stated: dict, ways: list[str], stated_on: list, worth_on: list,
           tree: dict) -> dict:
    """What valuing an event's worlds reads beyond their probabilities and the weights today's prices put on them
    (MATH.md 1.3 to 1.7): the factors and how each is built from the ones before it, the portfolio's holdings, dollar
    betas, look-through, betas and funds' rows, each consequence with today's price of what it moves, each value stated
    for a holding, each world's episode, and the contracts the funds hold."""
    nodes, leaves = tree["nodes"], tree["worlds"]
    channels = [c["id"] for c in ordered_channels(ctx)]
    lookthrough = ctx.value("lookthrough", "portfolio", ref=True)
    betas = ctx.value("betas", "portfolio", ref=True)
    holds = {h["id"]: held_name(h) for h in ctx.links("portfolio", "holds")}  # cash as its currency moves
    proxies = {c["id"]: c["stated"]["proxy"] for c in ordered_channels(ctx)}
    kinds = ctx.payload(lookthrough).get("kinds") or {}
    found = []
    for on, link in stated_on:
        got = price_today(ctx, link["to"], proxies, kinds)
        found.append({"link": link["id"], "on": on, "target": link["to"], **stated_move(link),
                      "c": got["c"], "basis": got["basis"]})
    fits = {c: ctx.value("orthogonalize", c)["output"] for c in channels}
    exposure = ctx.value("g", "portfolio")["output"]
    # a holding valued by what it is worth is valued by that, and not by its contracts; a fund a shock moves is valued
    # from where it stands unstated, its contracts included (MATH.md 1.6)
    whole_names: set[str] = set()
    for target in {link["to"] for link in worth_on}:
        coverage = {"event": pid, "worlds": [{"id": n, **nodes[n]} for n in leaves], "tree": nodes,
                    "worth": [{"on": link["from"], "target": target, "world": link["stated"].get("world")}
                              for link in worth_on if link["to"] == target]}
        try:
            valuation._worth_worlds(coverage)
        except Unavailable:
            continue
        whole_names.add(target)
    fund_rows = ctx.value("fund_rows", "portfolio", ref=True)  # each fund's rows, which its contracts are read from
    option_terms = option_funds.inputs(ctx, ctx.payload(lookthrough), ctx.payload(fund_rows)["rows"],
                                       distribution.event_date(ctx, pid), exclude=whole_names)
    option_remainders = valuation.remainder_inputs(ctx, option_terms)
    return {"channels": channels,
            "coefficients": {c: fits[c]["coefficients"] for c in channels},
            "variances": {c: fits[c]["variance"] for c in channels},
            "g": exposure["g"], "holds": holds,
            "values": {hid: h["dollars"] for hid, h in exposure["by_holding"].items()},
            "lookthrough": lookthrough, "fund_rows": fund_rows, "betas": betas,
            "consequences": found, "yields": quoted_yields(ctx),
            "asof": ctx.object("portfolio")["stated"]["asof"],
            "worth": valuation.worth_inputs(ctx, pid, ways, kinds),
            "episodes": episode.for_worlds(ctx, pid, tree),
            "option_funds": option_terms, "option_remainders": option_remainders,
            "proxies": proxies}


# ---------------------------------------------------------------- the worlds, placed and valued


def _applies(m: dict) -> dict[str, dict]:
    """Which shock applies in each world (1.3). A consequence on a way gives its shock to that way and the ways under
    it, in place of its parent's on whatever it sets; a consequence on the event gives its shock to every world on the
    other side from the baseline; the baseline has none of its own. So in each world each factor and each name is set
    by the consequence nearest above the world that sets it. In each world, what each factor and name is set by: the
    consequence, its shock, the proposition it is on, and whether that is the world's own (`own`), on a way or the
    event above it (`parent`)."""
    side = {w["id"]: w["side"] for w in m["worlds"]}
    parent = {w["id"]: w.get("parent") for w in m["worlds"]}
    parent.update({n: t.get("parent") for n, t in (m.get("tree") or {}).items()})
    on: dict[str, list[dict]] = {}
    for c in m["consequences"]:
        on.setdefault(c.get("on") or m["event"], []).append(c)
    base = m["baseline"]
    out: dict[str, dict] = {w: {} for w in side}
    for w in side:
        if w == base:
            continue
        path, x = [], w
        while x is not None and x not in ("yes", "no"):  # the world's own ways, nearest first
            path.append(x)
            x = parent.get(x)
        if side[w] != side[base]:
            path.append(m["event"])
        for above in path:
            for c in on.get(above, []):
                if c["target"] in out[w]:
                    continue
                own = above == w or (above == m["event"] and w in ("yes", "no"))
                out[w][c["target"]] = {"link": c["link"], "shock": c["shock"],
                                       schema.AGAINST: c.get(schema.AGAINST, schema.BASELINE),
                                       "on": above, "by": "own" if own else "parent"}
    return out


class Responses:
    """How a price responds to the factors (MATH.md 1.6): a name's betas, its local ones where it is quoted in another
    currency (`local_b`); a response at betas b to the factors' compounded moves (`at`); and a name's price at a set of
    factor moves against where it stands with none (`level`), its local response times its currency's rate, the level of
    the factor whose proxy the rate is, in the direction the proxy quotes it, or else the currency's own response. A
    price moving from one set of factor moves to another moves by the ratio of its levels at them (`departure`)."""

    def __init__(self, ids: list[str], coef: dict, fitted: dict):
        self.ids = ids
        self.fitted = fitted or {}
        fits = self.fitted.get("fits") or {}
        self.currencies = self.fitted.get("currencies") or {}
        self.columns = {c: worlds.orthogonal(ids, coef, {k: float(k == c) for k in ids}) for c in ids}
        self.abroad = {n: f["currency"] for n, f in fits.items() if (f or {}).get("currency")}
        self.rate_by: dict[str, str] = {}  # the factor whose proxy is each currency's rate, or its betas
        for fx in self.abroad.values():
            cur = fx["currency"]
            if cur not in self.rate_by:
                by = (self.currencies.get(cur) or {}).get("by") or fx.get("by")
                self.rate_by[cur] = by if by in ids else "betas"

    def b_of(self, nid: str) -> dict:
        return ((self.fitted.get("objects") or {}).get(nid) or {}).get("fields", {}).get("betas") or {}

    def local_b(self, n: str) -> dict:
        b = self.b_of(n)
        if n not in self.abroad:
            return b
        cb = (self.currencies.get(self.abroad[n]["currency"]) or {}).get("b") or {}
        return {c: b.get(c, 0.0) - cb.get(c, 0.0) for c in self.ids}

    def inverse(self, n: str) -> bool:
        """Whether the proxy of n's currency quotes its rate the other way, units of the currency per dollar."""
        cur = self.abroad[n]["currency"]
        return ((self.currencies.get(cur) or {}).get("direction") or self.abroad[n].get("direction")) == "inverse"

    def at(self, b: dict, logs: dict[str, float]) -> float:
        """A response at betas b, one plus its move: compounded on the factors' moves, exp(sum of b_raw ln(1 + x)),
        with b_raw = T'b its betas on the raw factors; a factor worth nothing there has no compounded move, and enters
        at its simple move, times (1 + b_raw x)."""
        ids = self.ids
        x = {c: math.expm1(logs[c]) for c in ids}
        raw = {c: sum(b.get(k, 0.0) * self.columns[c][k] for k in ids) for c in ids}
        live = sum(raw[c] * logs[c] for c in ids if math.isfinite(logs[c]))
        dead = math.prod(1.0 + raw[c] * x[c] for c in ids if not math.isfinite(logs[c]))
        return math.exp(live) * dead

    def rate(self, n: str, logs: dict[str, float]) -> float:
        """The rate of name n's currency at the factors' compounded moves `logs`, against where it stands with none: the
        level of the factor whose proxy it is, in the direction the proxy quotes it, or else its own response; 1 for a
        name quoted in dollars."""
        if n not in self.abroad:
            return 1.0
        cur = self.abroad[n]["currency"]
        by = self.rate_by[cur]
        if by in self.ids:
            return math.exp(-logs[by] if self.inverse(n) else logs[by])
        return self.at((self.currencies.get(cur) or {}).get("b") or {}, logs)

    def level(self, n: str, logs: dict[str, float]) -> float:
        """Name n's price in dollars at the factors' compounded moves `logs`, against where it stands with none: its
        local response times its currency's rate."""
        return self.at(self.local_b(n), logs) * self.rate(n, logs)

    def departure(self, n: str, logs: dict[str, float], ref: dict[str, float]) -> float:
        """What name n does when the factors move from `ref` to `logs`, each a set of compounded moves: the ratio of its
        levels at them, so that it moves with the factors at its betas from where it stood."""
        return self.level(n, logs) / self.level(n, ref)


class Moves:
    """Steps 2 and 3 for an event's worlds (MATH.md 1.3, 1.4), which no probability or weight enters: which shock applies
    in each world; every factor's compounded move against the baseline, set or filled (`logs`, `x`), and its compounded
    residual (`z`); each name a shock sets, its whole move against the baseline (`whole`); each currency's rate against the
    baseline (`rate_base`); and any name's move against the baseline (`against`), by its stated shock or its response.
    The impact places them at the weights; a chance that follows a holding's price places them at its own."""

    def __init__(self, m: dict, fitted: dict):
        self.W = [w["id"] for w in m["worlds"]]
        self.ids, self.coef = m["channels"], m["coefficients"]
        self.references = m.get("joint_references") or {}  # where the rows put each joint world the analyst states
        ids, W = self.ids, self.W
        cov = worlds.covariance(ids, self.coef, m["variances"])
        self.applies = applies = _applies(m)
        self.r = r = Responses(ids, self.coef, fitted)
        self.fitted, self.currencies, self.abroad, self.columns = r.fitted, r.currencies, r.abroad, r.columns
        currencies = r.currencies
        # step 2: each factor's compounded move against the baseline in each world, set or filled: a shock on a price is
        # a fraction of it, ln(1 + x); one on a factor quoted as a yield is in points, ln(1 + x) = -duration * shock / 100
        yields = m.get("yields") or {}
        self.at_zero: set[str] = set()  # a factor stated at -1
        self.logs: dict[str, dict[str, float]] = {}
        for w in W:
            stated, owned = {}, {}
            for t, a in applies[w].items():
                if t.startswith("name:"):
                    continue
                if a[schema.AGAINST] == schema.FOLLOWS and (t in yields or a["shock"] > -1.0):
                    # against what it follows: its move beyond the factors before it, in its compounded terms, a
                    # condition on its residual; at -1 it stands at zero wherever they put it, as at -1 against the
                    # baseline
                    owned[t] = (-yields[t]["duration"] * a["shock"] / 100.0 if t in yields
                                else math.log1p(a["shock"]))
                    continue
                if t in yields:
                    stated[t] = -yields[t]["duration"] * a["shock"] / 100.0
                    continue
                x = a["shock"]
                stated[t] = math.log1p(x) if x > -1.0 else -math.inf
                if x <= -1.0:
                    self.at_zero.add(t)
            self.logs[w] = worlds.fill(ids, (m.get("episodes") or {}).get(w, {}).get("covariance", cov), stated,
                                       owned, self.coef)
        logs = self.logs
        self.x = {w: {c: math.expm1(logs[w][c]) for c in ids} for w in W}  # each factor's move against the baseline
        # each world's z_w (1.3): every factor's compounded residual, its compounded move less its regression on the
        # earlier factors' residuals; a factor at zero there has no compounded move, and no residual after it has one
        self.z = {w: worlds.orthogonal(ids, self.coef, logs[w]) if all(math.isfinite(v) for v in logs[w].values())
                  else {c: None for c in ids} for w in W}
        # step 3: each name a shock sets, its whole move against the baseline, in its own currency
        shocked = sorted({t for w in W for t in applies[w] if t.startswith("name:")})
        self.whole = {n: {w: self._stated_whole(applies[w].get(n), n, w) for w in W} for n in shocked}
        # each currency's rate against the baseline: its proxy's level where a factor's proxy is its rate, in the
        # direction the proxy quotes it, or else its compounded response to the factors (1.6)
        self.rate_base: dict[str, dict[str, float]] = {}
        self.rate_by = r.rate_by  # the factor whose proxy is each currency's rate, or its betas
        for n, fx in self.abroad.items():
            cur = fx["currency"]
            if cur in self.rate_base:
                continue
            by = self.rate_by[cur]
            if by in ids:
                inverse = r.inverse(n)
                x_by = self.x
                self.rate_base[cur] = {w: (1.0 / (1.0 + x_by[w][by]) if inverse and 1.0 + x_by[w][by] > TOL else
                                           (1.0 + x_by[w][by] if not inverse else 0.0)) for w in W}
            else:
                self.rate_base[cur] = {w: self.response((currencies.get(cur) or {}).get("b") or {}, w) for w in W}

    def _stated_whole(self, a: dict | None, n: str, w: str) -> float:
        """A name's move against the baseline in one world, in its own currency (MATH.md 1.4): the shock against the
        baseline where one sets it; against what it follows, its response there compounded with the shock,
        (1 + r)(1 + a); and its response where none does."""
        if a is not None and a[schema.AGAINST] == schema.BASELINE:
            return a["shock"]
        r = self.response(self.local_b(n), w)
        return r * (1.0 + a["shock"]) - 1.0 if a is not None else r - 1.0

    def b_of(self, nid: str) -> dict:
        return self.r.b_of(nid)

    def local_b(self, n: str) -> dict:
        return self.r.local_b(n)

    def response(self, b: dict, w: str) -> float:
        """A response at betas b, one plus its move against the baseline in world w (Responses.at)."""
        return self.r.at(b, self.logs[w])

    def departure(self, n: str, w: str) -> float:
        """What a name does in a joint world the analyst states beyond where the rows put it (MATH.md 1.9): it moves with
        the factors at its betas, its currency's rate with it, from where the rows put them (`references`) to where the
        joint world has them (Responses.departure)."""
        ref = self.references.get(w)
        return 1.0 if ref is None else self.r.departure(n, self.logs[w], ref)

    def against(self, n: str, *, responding: bool = False) -> dict[str, float]:
        """A name's price in each world against its price in the baseline (1.6): the whole move a shock settles where
        one sets it (`whole`), else its response, exp(b . z_w); for a name quoted in another currency, times its rate
        against the baseline. `responding` gives the response alone, which what a stated name does beyond its response
        is measured from."""
        if n in self.whole and not responding:
            local = {w: 1.0 + self.whole[n][w] for w in self.W}
        else:
            b = self.local_b(n)
            local = {w: self.response(b, w) for w in self.W}
        if n in self.abroad:
            local = {w: local[w] * self.rate_base[self.abroad[n]["currency"]][w] for w in self.W}
        return local


def compute(m: dict, load, *, weights: dict[str, float] | None = None) -> dict:
    """The event's worlds, placed and valued, and its row (MATH.md 1.1, 1.3 to 1.7); `weights` places them at given
    weights in place of the manifest's (placed)."""
    ws = m["worlds"]
    W = [w["id"] for w in ws]
    try:
        q = weights or worlds.weights(ws, m.get("tree"), {n: v["q"] for n, v in (m.get("priced_in") or {}).items()})
    except worlds.Unholdable as e:  # stated weights that cannot hold together: check() lists them (1.10)
        return {"weights_cannot_hold": {"why": str(e)}, "priced_in": m.get("priced_in"), "why": str(e), "p": m["p"],
                "baseline": m["baseline"]}  # each stated weight's reading stands whether or not they hold together
    looked, fitted = load(m["lookthrough"]), load(m["betas"])
    unpriced = unpriced_names(fitted)
    mv = Moves(m, fitted)  # steps 2 and 3: every move against the baseline, before any weight
    ids, coef, applies, logs, x, z, at_zero = mv.ids, mv.coef, mv.applies, mv.logs, mv.x, mv.z, mv.at_zero
    response, b_of, local_b, abroad, whole = mv.response, mv.b_of, mv.local_b, mv.abroad, mv.whole
    rate_base, rate_by = mv.rate_base, mv.rate_by
    shocked = sorted(whole)

    unplaceable: dict[str, dict] = {}

    def place(price: str, ratios: dict[str, float]) -> dict[str, float] | None:
        try:
            return worlds.place(ratios, q)
        except worlds.Unplaceable:
            unplaceable[price] = {"price": price, "levels": ratios, "weights": q}
            return None

    # step 4 for the factors: each price placed against today once, a factor quoted as a yield by the price it gives
    moves: dict[str, dict[str, float]] = {}
    for c in ids:
        got = place(c, {w: math.exp(logs[w][c]) for w in W})
        moves[c] = got or {w: 0.0 for w in W}
    ut = {w: worlds.orthogonal(ids, coef, {c: moves[c][w] for c in ids}) for w in W}

    def along(b: dict, w: str) -> float:  # a move at betas b on the placed factors
        return sum(b.get(c, 0.0) * ut[w][c] for c in ids)

    # step 4 for the names: each one's move against the baseline, placed; a currency's rate placed likewise (1.6)
    named = sorted(set(shocked) | {item["target"] for item in m.get("worth") or []} | set(m.get("joint_prices") or {}))
    rate: dict[str, dict[str, float]] = {}  # each currency's rate against today, placed
    for cur, base in rate_base.items():
        got = place(f"rate:{cur}", base)
        rate[cur] = {w: 1.0 + v for w, v in (got or {w: 0.0 for w in W}).items()}

    def against_baseline(n: str, *, responding: bool = False) -> dict[str, float]:
        """A name's move against the baseline in each world (mv.against); with several events read together (1.9), the
        rows' compounded move in each joint world they set, `joint_prices`, or its response's, `joint_responses`, and in
        a joint world the analyst states, its move there as in a way."""
        joint = ((m.get("joint_responses") if responding else m.get("joint_prices")) or {}).get(n)
        if joint is None:
            return mv.against(n, responding=responding)
        own = mv.against(n, responding=responding) if any(w not in joint for w in W) else {}
        moving = m.get("joint_moving") or {}  # a stated joint world's names that move with what it changes
        return {w: (joint[w] * (mv.departure(n, w) if responding or n in moving.get(w, ()) else 1.0))
                if w in joint else own[w] for w in W}

    def own_price(n: str, *, responding: bool = False) -> dict[str, float]:
        """A name's price in each world against today: its move against the baseline (against_baseline), placed once
        (1.5)."""
        got = place(n, against_baseline(n, responding=responding))
        return {w: 1.0 + v for w, v in (got or {w: 0.0 for w in W}).items()}

    # a fund on one reference reads its multiple of its reference's move where the reference has a price, whose betas
    # are fitted beside the portfolio's names (channels.read_beside); one whose reference has none moves by its own price
    multiples = valuation.on_one_reference(looked, b_of)
    R: dict[str, dict[str, float]] = {}
    normal: dict[str, dict[str, float]] = {}
    components = valuation.fund_components(looked, load(m["fund_rows"])["rows"])
    worth_values, worth_terms, evaluating = {}, {}, set()
    worth_targets = {item["target"] for item in m.get("worth") or []}
    option_specs = m.get("option_funds") or {}
    remainder_ratios = {}
    # each stated name, the statement that sets it in each world it applies in
    against_of = {n: {w: applies[w][n] for w in W if n in applies[w]} for n in shocked}
    joint_prices = m.get("joint_prices") or {}
    base_world = m["baseline"]

    def from_unstated(n: str) -> bool:
        """Whether a stated price is valued from where it stands unstated (MATH.md 1.4, 1.6): a fund the look-through
        expands, on one reference, or of contracts, whatever its worlds state. Every other stated price is its whole
        moves settled from its response (Moves.whole), placed once (own_price)."""
        return n in option_specs or n in multiples or n in components
    unstated: dict[str, dict[str, float]] = {}  # a stated fund valued from where it stands unstated: its worlds there

    def responding(n):
        if n not in normal:
            normal[n] = own_price(n, responding=True)
        return normal[n]

    def as_unstated(n):
        """A price where the store puts it with no statement of its own (MATH.md 1.6): a fund's by its contracts, its
        multiple of its reference, or its response with the companies it holds; any other's by its response."""
        if n in option_specs:
            prices, terms = option_book.value(n)
            if terms["full"]:
                return {w: max(prices[w], 0.0) for w in W}
            remainder = valuation.remainder_fit(m["option_remainders"][n], load)
            terms["remainder"] = remainder
            rem = place(f"remainder:{n}", {w: response(remainder["betas"], w) for w in W})
            rem = rem or {w: 0.0 for w in W}
            remainder_ratios["remainder:" + n] = {w: 1 + rem[w] for w in W}
            return {w: max(prices[w] + (1 - terms["sleeve_today"]) * (1 + rem[w]), 0.0) for w in W}
        if n in multiples:
            spec = multiples[n]
            ref = completed(spec["reference"])
            extra = [(weight, completed(child), responding(child)) for child, weight in
                     valuation.multiple_extras(components, n, spec["reference"], spec["multiple"]).items()
                     if child not in unpriced]
            return {w: max(1 + spec["multiple"] * (ref[w] - 1)
                           + sum(weight * (price[w] - before[w]) for weight, price, before in extra), 0.0) for w in W}
        if n in components:
            children = [(weight, completed(child), responding(child))
                        for child, weight in components[n].items() if child not in unpriced]
            base = responding(n)
            return {w: max(base[w] + sum(weight * (price[w] - before[w]) for weight, price, before in children), 0.0)
                    for w in W}
        return responding(n) if n in named else own_price(n)

    def completed(n):
        """A security's price in each world against today, worked out once and read wherever it is held, paid or
        referred to (1.6): a holding the analyst values world by world, at its stated values; a stated price valued
        from where it stands unstated (from_unstated), at that value times what its statement gives in each world,
        then placed once unless its terms value it; any other stated price, at its whole moves, placed once
        (own_price); and a price left unstated, where it stands unstated (as_unstated)."""
        if n in R:
            return R[n]
        if n in evaluating:
            raise Unavailable("the holding considerations and fund contracts contain a circular valuation")
        evaluating.add(n)
        if n in worth_targets:
            wm = {**m, "worth": [i for i in m["worth"] if i["target"] == n]}
            vals, terms = valuation.worth_values(wm, load, completed,
                                                {w: {c: moves[c][w] for c in ids} for w in W})
            worth_values.update(vals)
            worth_terms.update(terms)
            current = next(i["c"] for i in m["worth"] if i["target"] == n)
            result = {w: vals[n][w] / current for w in W}
        elif n in named and (not from_unstated(n) or n in joint_prices):  # read together (1.9), a fund the rows give
            result = own_price(n)  # a joint move stands at it, each row having settled what its statements do to it
        else:
            result = as_unstated(n)
            if n in named:  # each world from where the fund stands unstated, in its own currency: against the
                unstated[n] = result  # baseline, the baseline's level times 1 + s; against what it follows, that world's
                by = against_of[n]  # times 1 + a; and its rate there, where it is quoted in another
                fx = rate[abroad[n]["currency"]] if n in abroad else {w: 1.0 for w in W}
                own = {w: result[w] / fx[w] if fx[w] > TOL else 0.0 for w in W}
                scaled = {w: fx[w] * (own[w] if w not in by else
                                      own[base_world] * (1.0 + by[w]["shock"])
                                      if by[w][schema.AGAINST] == schema.BASELINE else own[w] * (1.0 + by[w]["shock"]))
                          for w in W}
                if not (n in option_specs or n in multiples):  # a price the store places: placed once (1.5)
                    total = sum(q[w] * scaled[w] for w in W)
                    if total <= TOL:
                        unplaceable[n] = {"price": n, "levels": scaled, "weights": q}
                    result = {w: scaled[w] / total if total > TOL else 0.0 for w in W}
                else:  # valued by its terms: each world at what they give, as stated there, and no level placed
                    result = scaled
        evaluating.remove(n)
        R[n] = result
        responding(n)
        return result

    def factor_reference(ticker):
        # A listed security's completed price includes the companies and contracts it holds.
        # An index's own values, with no security identity, can coincide with a factor proxy.
        if not ticker.startswith("I:"):
            return None
        return next(({w: 1 + moves[c][w] for w in W} for c, t in (m.get("proxies") or {}).items()
                     if t == ticker), None)

    option_book = option_funds.prepare(option_specs, load, q, W, completed, factor_reference)
    names = sorted(n for n, v in looked["e"].items() if v and n not in unpriced)
    priced_ = sorted({*names, *worth_targets, *option_specs,
                      *(n for n in m["holds"].values() if n.startswith("name:") and n not in unpriced)})
    for n in priced_:
        completed(n)
    option_terms = option_book.terms
    # what each stated name does beyond its response, d~ (`own`): its placed move less its response's placed move (1.6)
    own = {n: (lambda resp: {w: R[n][w] - resp[w] for w in W})(responding(n)) for n in named if n in R}
    # a fund scaled from where it stands unstated has its move against the baseline from its worlds as valued (1.4)
    base = m["baseline"]
    for n in unstated:
        rate_of = rate[abroad[n]["currency"]] if n in abroad else None
        at = lambda w: R[n][w] / rate_of[w] if rate_of else R[n][w]  # noqa: E731  in its own currency
        whole[n] = {w: at(w) / at(base) - 1.0 if at(base) > TOL else -1.0 for w in W}
    # each stated name's move against what it follows (1.4): its move against the baseline over where what it follows
    # puts it there, 1 + s = (1 + r)(1 + a); for a company its response, and for a fund scaled from where it stands
    # unstated, those worlds
    def followed(n: str, w: str) -> float:
        if n in unstated:
            u = unstated[n]
            return u[w] / u[base] if u[base] > TOL else 0.0
        return response(local_b(n), w)
    d = {n: {w: (1.0 + whole[n][w]) / followed(n, w) - 1.0 if followed(n, w) > TOL else None for w in W}
         for n in shocked}

    by_holding, shares, remainders = _shares(m, looked, W, R, own, named, completed, along, b_of, unpriced)
    if unplaceable:
        return {"unplaceable": list(unplaceable.values()), "at_zero": sorted(at_zero), "p": m["p"], "baseline": m["baseline"]}
    reach = _reach(m, looked, named)
    g = m["g"]
    # each world's P&L is the holdings' changes in value there, summed (1.1, 1.6); its parts for the inspector are the
    # holdings at their betas on the placed factors, g . u~, what each stated company does beyond its response on every
    # dollar of exposure to it, and the curve, what compounding and a holding's terms add beyond those
    A = {w: sum(h["worlds"][w] for h in by_holding) for w in W}
    figures = worlds.row(ws, A)
    tree = worlds.tree(ws, m.get("tree") or {}, A, q)
    value = sum(m["values"].values())  # V, what the portfolio is worth: the sum of the holdings' values
    side_mean = lambda vals: {s: worlds.side_mean(ws, vals, s) for s in ("yes", "no")}  # noqa: E731
    u_e = {c: (lambda sm: sm["yes"] - sm["no"])(side_mean({w: ut[w][c] for w in W})) for c in ids}
    contributions = {c: g.get(c, 0.0) * u_e[c] for c in ids}
    own_moves = {n: {**own[n], "E": (lambda sm: sm["yes"] - sm["no"])(side_mean(own[n]))} for n in own}  # d~
    own_terms = {n: reach[n] * own_moves[n]["E"] for n in own}
    for holding in by_holding:
        if holding.get("contract"):
            name = holding["name"]
            own_terms[name] = own_terms.get(name, 0.0) + holding["full_impact"]
    parts = _parts(W, ids, g, ut, by_holding, A)
    # a name a consequence runs to that no shock sets in any world, as where a nearer one replaces it everywhere, moves
    # by its response in each: its levels are placed from that
    unset = {c["target"] for c in m["consequences"] if c["target"].startswith("name:")} - set(whole)
    levels = _levels(m, {**whole, **{n: {w: response(local_b(n), w) - 1.0 for w in W} for n in unset}}, q, W, moves,
                     {n: R[n] for n in unstated})
    shown = _shown(m, applies, x, logs, z, whole, d, lambda n, w: response(local_b(n), w) - 1.0, W, abroad, rate, rate_by,
                   levels)
    setting = {w: {t: {**{k: a[k] for k in ("link", "on", "by", "shock")}, schema.AGAINST: a[schema.AGAINST]}
                   for t, a in applies[w].items()} for w in W}
    return {"worlds": {w["id"]: {"side": w["side"], "p": w["p"], "q": q[w["id"]], "A": A[w["id"]],
                                 **({"parent": w["parent"]} if w.get("parent") else {}),
                                 **({"title": w["title"]} if w.get("title") else {})} for w in ws},
            "outcomes": tree, "setting": setting,
            "episodes": {w: {k: e[k] for k in ("on", "window", "sessions")} for w, e in (m.get("episodes") or {}).items()},
            "baseline": m["baseline"], **figures,
            "V": value, "share_yes": figures["yes"] / value if value else None,
            "share_no": figures["no"] / value if value else None,
            "p": m["p"], "priced_in": m.get("priced_in"),
            "u_E": u_e, "against_baseline": x, "own_against_baseline": z,
            "placed": {w: {c: moves[c][w] for c in ids} for w in W},
            "components": ut, "contributions": contributions, "own": own_terms, "own_moves": own_moves, "parts": parts,
            "contributors": shares, "by_holding": by_holding, "remainders": remainders,
            "consequences": shown, "consequence_set": sorted({t for w in W for t in applies[w]
                                                             if not t.startswith("name:")}),
            "names_set": named, "whole": whole, "d": d, "at_zero": sorted(at_zero), "reach": reach,
            "name_ratios": R, "unstated": unstated, "reference_ratios": {}, "rate_base": rate_base,
            "holding_ratios": {h["holding"]: {w: 1 + h["worlds"][w] / m["values"][h["holding"]]
                                                if m["values"][h["holding"]] else 1.0 for w in W} for h in by_holding},
            "valuation_terms": {"worth": worth_terms, "options": option_terms, "contracts": m.get("contracts", [])},
            "remainder_ratios": remainder_ratios, **({"waiting": m["waiting"]} if m.get("waiting") else {}),
            "anchor_ratios": {anchor["state_key"]: {w: valuation.raw_response(anchor["raw_betas"],
                                                         {c: moves[c][w] for c in ids}) for w in W}
                for n, terms in worth_terms.items() for world, claim in terms.items()
                if (anchor := claim.get("anchor"))},
            "responding_ratios": normal,
            "worth": {n: _worth_shown(n, values, worth_terms[n], next(i["c"] for i in m["worth"] if i["target"] == n),
                                      ws, q)
                      for n, values in worth_values.items()}}


def _worth_shown(n: str, values: dict[str, float], terms: dict, c: float, ws: list[dict], q: dict) -> dict:
    """A holding valued by what it is worth in each world (MATH.md 1.6): its value there (`levels`), the statements that
    give each, its price today, each side's mean of its values with each world's share (valuation.value_sides), and the
    weight its price implies from them."""
    sides = valuation.value_sides(values, ws, q)
    return {"levels": values, "considerations": {w: t["link"] for w, t in terms.items()}, "c": c, "sides": sides,
            "implied": valuation.implied_from_values(values, c, ws, q, sides)}


def _parts(W: list[str], ids: list[str], g: dict, ut: dict, by_holding: list[dict], A: dict) -> dict[str, dict]:
    """Each world's P&L in its three parts (MATH.md 1.6, "What moved it"), for the inspector: each factor's dollar beta
    times its placed move there, g[c] * u~_w[c]; what each stated company does beyond its response on every dollar of
    exposure to it, as the holdings carry it; and what is left, curve_w. Read from the holdings, so they add to the world's P&L."""
    out = {}
    for w in W:
        factors = {c: g.get(c, 0.0) * ut[w][c] for c in ids}
        own: dict[str, float] = {}
        for h in by_holding:
            for o in h["own"]:
                own[o["name"]] = own.get(o["name"], 0.0) + o["worlds"][w]
        out[w] = {"factors": factors, "own": own, "curve": A[w] - sum(factors.values()) - sum(own.values())}
    return out


def baseline_parts(parts: dict[str, dict], base: str, set_in: dict[str, set[str]]) -> dict[str, dict]:
    """Each world's P&L against the baseline in three parts (MATH.md 1.3, 1.6), from the parts of what moved it in each
    world (_parts) less the baseline's: what comes through the prices a statement sets there, each factor it sets at
    its dollar beta times its placed move and what each stated company does beyond its response (`stated`); what comes
    through the factors moving with them (`moving`), the largest of them named (`largest_moving`); and what compounding
    and a holding's terms add (`rest`). The three add to the world's P&L against the baseline. `set_in` gives the
    factors a statement sets in each world."""
    b = parts[base]
    out = {}
    for w, p in parts.items():
        if w == base:
            continue
        fs = {c: v - b["factors"].get(c, 0.0) for c, v in p["factors"].items()}
        moving = {c: v for c, v in fs.items() if c not in set_in.get(w, ())}
        stated = math.fsum(v for c, v in fs.items() if c in set_in.get(w, ())) + \
            math.fsum(p["own"].values()) - math.fsum(b["own"].values())
        top = max(moving, key=lambda c: abs(moving[c])) if moving else None
        out[w] = {"stated": stated, "moving": math.fsum(moving.values()),
                  **({"largest_moving": {"factor": top, "dollars": moving[top]}}
                     if top is not None and round(moving[top], 2) != 0.0 else {}),
                  "rest": p["curve"] - b["curve"]}
    return out


def _reach(m: dict, looked: dict, named: list[str]) -> dict[str, float]:
    """The dollars on which what each stated name does beyond its response reaches the full impact: its exposure, less
    what it has through a fund whose own price a shock sets (whose move already holds it), and for such a fund, its
    holding."""
    held: dict[str, list[str]] = {}  # each name's holdings: a company can be held by several of its securities
    for hid, nid in m["holds"].items():
        held.setdefault(nid, []).append(hid)
    funds = looked.get("funds") or {}
    set_funds = {n for n in named if n in held and (funds.get(ticker_of(n)) or {}).get("expanded")}
    through: dict[str, float] = {}
    for n in set_funds:
        for hid in held[n]:
            for x in (looked.get("paths") or {}).get(hid, []):
                through[x["name"]] = through.get(x["name"], 0.0) + x["dollars"]
    out = {n: looked["e"].get(n, 0.0) - through.get(n, 0.0) for n in named}
    for n in set_funds:
        out[n] = out.get(n, 0.0) + sum(m["values"].get(hid, 0.0) for hid in held[n])
    return out


def _levels(m: dict, whole: dict, q: dict, W: list[str], moves: dict, valued: dict | None = None) -> dict[str, dict]:
    """The level in each world of each price a consequence sets, where it has a price today (MATH.md 1.5; TOOLS.md 5): a
    factor's, its proxy's price at the factor's placed move; with, for a factor quoted as a yield, the yield its placed
    price implies, y - 100 ln(1 + m_w) / duration; a name's, its own price, in its own currency, placed from its whole
    move in each world, stated or by its response, or, for a fund valued from where it stands unstated (`valued`), at
    its worlds as valued. So the levels shown and the dollars are one calculation."""
    yields = m.get("yields") or {}
    out: dict[str, dict] = {}
    for c in m["consequences"]:
        t = c["target"]
        if t in out:
            continue
        got: dict = {}
        if t.startswith("name:"):
            if t in (valued or {}):  # valued from where it stands unstated: at its worlds as valued (1.6)
                placed_ = {w: valued[t][w] - 1.0 for w in W}
            else:
                try:
                    placed_ = worlds.place({w: 1.0 + whole[t][w] for w in W}, q)
                except worlds.Unplaceable:
                    placed_ = None
            if placed_ is not None and c["c"] is not None:
                got["levels"] = {w: c["c"] * (1.0 + placed_[w]) for w in W}
        elif t in moves:
            if c["c"] is not None:
                got["levels"] = {w: c["c"] * (1.0 + moves[t][w]) for w in W}
            if t in yields and all(1.0 + moves[t][w] > 0.0 for w in W):
                y, dur = yields[t]["yield"], yields[t]["duration"]
                got.update({"yield": y, "yields": {w: y + worlds.points(moves[t][w], dur) for w in W}})
        out[t] = got
    return out


def _shown(m: dict, applies: dict, x: dict, logs: dict, z: dict, whole: dict, d: dict, responds, W: list[str],
           abroad: dict, rate: dict, rate_by: dict, levels: dict) -> dict[str, dict]:
    """What each consequence sets, by its link, in each world it applies in, against the baseline (MATH.md 1.3, 1.4): a
    factor's move as stated (`raw`), its compounded move, ln(1 + x) (`compounded`), the part of it the earlier
    factors' residuals give (`implied`), and its compounded residual in z_w (`own_part`), so that the compounded move
    is the last two summed; a name's whole move, its response's move (`betas`), and its residual (`d`), with its
    currency's rate against today where it is quoted in another; and the price's level today and in each world."""
    out: dict[str, dict] = {}
    for c in m["consequences"]:
        t = c["target"]
        at = [w for w in W if applies[w].get(t, {}).get("link") == c["link"]]
        if t.startswith("name:"):
            cur = abroad.get(t)
            moved = {"name": t, "worlds": {w: {"whole": whole[t][w], "betas": responds(t, w), "d": d[t][w]} for w in at},
                     **({"currency": cur["currency"], "rate": {w: rate[cur["currency"]][w] - 1.0 for w in W},
                         "rate_by": rate_by[cur["currency"]]} if cur else {})}
        else:
            def factor_move(w: str) -> dict:
                compounded = logs[w][t] if math.isfinite(logs[w][t]) else None
                component = z[w][t]
                return {"raw": x[w][t], "compounded": compounded, "own_part": component,
                        "implied": compounded - component if None not in (compounded, component) else None}
            moved = {"factor": t, "worlds": {w: factor_move(w) for w in at}}
        out[c["link"]] = {"link": c["link"], "on": c.get("on"), "target": t, "shock": c["shock"],
                          schema.AGAINST: c.get(schema.AGAINST, schema.BASELINE), "c": c["c"],
                          **moved, **levels.get(t, {})}
    return out


def _shares(m: dict, looked: dict, W: list[str], R: dict, own: dict, named: list[str], completed, along, b_of,
            unpriced: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """Each holding's change in value in each world, each name's share, and each fund's remainder (MATH.md 1.6, 1.7).

    A holding moves by its security's price (`R`, as `completed` values it): a fund the look-through expands by its
    response, placed once, with what each stated company it carries does beyond its response on the dollars it carries
    of it, or at its own statement where one sets it. Each fund on a path contributes its dollars there at its own
    price, and its remainder is that less what the names and the funds it holds at the next level contribute. A name's
    share is its exposure times its placed move, R - 1, in each world. Each share keeps its parts: its dollar beta times
    the placed factors' moves, what it does beyond its response (`own`), and the curve, what compounding and its terms
    add beyond them; a share is kept wherever it shows anything (_shows). Each figure is given at each side, and, where
    a way has ways of its own, at that way (`outcomes`), as the outcome tree reads the P&L there (worlds.outcome_mean),
    so that the parts add to the P&L at every outcome."""
    funds = looked.get("funds") or {}
    reaches = looked.get("reaches") or {}
    held_set = {nid for nid in m["holds"].values() if nid in named and (funds.get(ticker_of(nid)) or {}).get("expanded")}

    parents = m.get("tree") or {}
    nested = [n for n in parents if n not in ("yes", "no")]  # the ways that have ways of their own

    def sides(vals: dict[str, float]) -> dict:  # each figure at each outcome, as the tree reads the P&L there
        ys, ns = (worlds.side_mean(m["worlds"], vals, s) for s in ("yes", "no"))
        return {"full_impact": ys - ns, "yes": ys, "no": ns, "worlds": vals,
                **({"outcomes": {n: worlds.outcome_mean(m["worlds"], parents, vals, n) for n in nested}}
                   if nested else {})}

    by_holding: list[dict] = []
    remainders: dict[str, dict] = {}
    contracts = {c["holding"]: c for c in m.get("contracts", [])}
    contract_shares = []
    for hid, nid in sorted(m["holds"].items()):
        value = m["values"].get(hid, 0.0)
        if hid in contracts:
            contract = contracts[hid]
            changes = {w: contract["quantity"] * contract["discount"] * contract["probabilities"][w] - value for w in W}
            zeros = {w: 0.0 for w in W}
            receipt = {"name": nid, "dollars": contract["quantity"],
                       "d": sides(changes)["full_impact"] / contract["quantity"] if contract["quantity"] else 0,
                       **sides(changes)}
            by_holding.append({"holding": hid, "name": nid, **sides(changes), "contract": contract,
                               "at_betas": {"dollars": value, "move": 0.0, **sides(zeros)},
                               "own": [receipt], "names": [receipt], "remainders": []})
            contract_shares.append({"name": nid, "exposure": value, **sides(changes),
                                    "at_betas": 0.0, "own": sides(changes)["full_impact"]})
            continue
        reaching = [x for x in (looked.get("paths") or {}).get(hid, []) if x["name"] not in unpriced]
        top = next((f for f in reaches.get(hid, []) if not f["funds"]), None)  # the held fund, where it is expanded
        if top is not None:
            price = completed(nid)
        else:
            price = R.get(nid) or {w: 1.0 for w in W}
        change = {w: value * (price[w] - 1.0) for w in W}
        at_betas = {w: value * along(b_of(nid), w) for w in W}
        # what the holding carries of stated names beyond their responses: its own where a shock sets its price, or, for
        # a fund the look-through expands, that of each stated company on the exposure through it
        if nid in own and (top is None or nid in held_set):
            own_of = {nid: value}
        elif top is not None:
            own_of = {}
            for x in reaching:
                if x["name"] in own:
                    own_of[x["name"]] = own_of.get(x["name"], 0.0) + x["dollars"]
        else:
            own_of = {}
        own_h = [{"name": n, "dollars": dollars, "d": sides(own[n])["full_impact"], **sides({w: dollars * own[n][w] for w in W})}
                 for n, dollars in own_of.items()]
        own_h = sorted((s for s in own_h if _shows(s)), key=lambda s: (-abs(s["full_impact"]), s["name"]))
        curve = {w: change[w] - at_betas[w] - sum(s["worlds"][w] for s in own_h) for w in W}
        reached: dict[str, float] = {}
        for x in reaching:
            reached[x["name"]] = reached.get(x["name"], 0.0) + x["dollars"]
        shown_names = []
        for n, dollars in reached.items():
            if n not in R:
                continue
            s = {"name": n, "dollars": dollars, **sides({w: dollars * (R[n][w] - 1.0) for w in W})}
            s["move"] = s["full_impact"] / dollars if dollars else 0.0
            if _shows(s):
                shown_names.append(s)
        shown_names.sort(key=lambda s: (-_size(s), s["name"]))
        rest = []
        for f in reaches.get(hid, []):
            path = [*f["funds"], f["fund"]]
            fid = schema.name_id(f["fund"])
            fp = price if not f["funds"] else completed(fid)
            contribution = {w: f["dollars"] * (fp[w] - 1.0) for w in W}
            names_next = {w: sum(x["dollars"] * (R[x["name"]][w] - 1.0) for x in reaching
                                 if x["funds"] == path and x["name"] in R) for w in W}
            funds_next = {w: 0.0 for w in W}
            for y in reaches[hid]:
                if y["funds"] != path:
                    continue
                yp = completed(schema.name_id(y["fund"]))
                for w in W:
                    funds_next[w] += y["dollars"] * (yp[w] - 1.0)
            r = {w: contribution[w] - names_next[w] - funds_next[w] for w in W}
            entry = {"fund": fid, "dollars": f["dollars"], "move": sides({w: fp[w] - 1.0 for w in W})["full_impact"], **sides(r),
                     **({"set": True} if not f["funds"] and nid in held_set else {})}
            rest.append(entry)
            total = remainders.setdefault(fid, {"fund": fid, "dollars": 0.0,
                                                "worlds": {w: 0.0 for w in W}})
            total["dollars"] += f["dollars"]
            for w in W:
                total["worlds"][w] += r[w]
            if entry.get("set"):
                total["set"] = True
        by_holding.append({"holding": hid, "name": nid, **sides(change),
                           "at_betas": {"dollars": value, "move": sides({w: along(b_of(nid), w) for w in W})["full_impact"],
                                        **sides(at_betas)},
                           **({"set": {"move": sides({w: price[w] - 1.0 for w in W})["full_impact"]}}
                              if nid in held_set else {}),
                           "own": own_h,
                           **({"curve": sides(curve)} if any(abs(v) > TOL for v in curve.values()) else {}),
                           "names": shown_names,
                           "remainders": sorted(rest, key=lambda s: (-abs(s["full_impact"]), s["fund"]))})
    shares = contract_shares
    for n in sorted(R):
        dollars = looked["e"].get(n, 0.0)
        if not dollars:
            continue
        at = {w: dollars * along(b_of(n), w) for w in W}
        o = {w: dollars * own[n][w] for w in W} if n in own else {w: 0.0 for w in W}
        whole = {w: dollars * (R[n][w] - 1.0) for w in W}
        curve = {w: whole[w] - at[w] - o[w] for w in W}
        s = {"name": n, "exposure": dollars, **sides(whole), "at_betas": sides(at)["full_impact"],
             "own": sides(o)["full_impact"],
             **({"curve": sides(curve)} if any(abs(v) > TOL for v in curve.values()) else {})}
        if _shows(s):
            shares.append(s)
    shares.sort(key=lambda s: (-abs(s["full_impact"]), s["name"]))
    funds_out = [{**r, **sides(r["worlds"])} for r in remainders.values()]
    funds_out = [r for r in funds_out if _shows(r)]
    funds_out.sort(key=lambda s: (-abs(s["full_impact"]), s["fund"]))
    return by_holding, shares, funds_out


def _shows(share: dict) -> bool:
    """Whether a share has anything to show: its value in any world."""
    return any(abs(v) > 0.0 for v in share["worlds"].values())


def _size(share: dict) -> float:
    """How much a share carries: the largest of its full impact and its value in any world, by which a holding's names
    are ranked."""
    return max(abs(share["full_impact"]), *(abs(v) for v in share["worlds"].values()))


IMPACT_FIELDS = ("u_E", "full_impact", "contributors", "by_holding", "yes", "no", "expected", "volatility", "worst_outcome",
                 "worlds")


IMPACT = Derivation("impact", 48, "Proposition", _impact_inputs, compute, fields=IMPACT_FIELDS,
                    applies=lambda store, pid: store.watched(pid))

# The first version of the impact whose manifest holds what the analyst states of a price as a fraction of it: a shock,
# an own part, and a worth's return since. A manifest made before it holds each as a percent.
FRACTION = 38
# The first version whose manifest holds each shock with the place it is measured from, `against`; one made before it
# holds a factor's own part beyond the factors before it as `own_part`, which is its shock against what it follows.
REFERENCE = 39


def as_references(m: dict) -> dict:
    """An impact's manifest made before REFERENCE, in the terms of one made since: an own part as a shock against what
    the factor follows, and every other shock against the baseline."""
    def one(c: dict) -> dict:
        if "own_part" in c:
            out = {k: v for k, v in c.items() if k != "own_part"}
            return {**out, "shock": c["own_part"], schema.AGAINST: schema.FOLLOWS}
        return {**c, schema.AGAINST: c.get(schema.AGAINST, schema.BASELINE)}
    return {**m, "consequences": [one(c) for c in m.get("consequences") or []]}


def as_fractions(m: dict) -> dict:
    """An impact's manifest made before FRACTION, in the terms of one made since: each shock and own part on a price, and
    each worth's return since, a percent there, as a fraction; a shock or an own part on a factor quoted as a yield
    stays in points of that yield."""
    yields = m.get("yields") or {}
    consequences = [{**c, **{k: schema.from_percent(c[k]) for k in ("shock", "own_part")
                             if k in c and c["target"] not in yields}} for c in m.get("consequences") or []]
    worth = [{**i, "value": {**i["value"], "return": schema.from_percent(i["value"]["return"])}}
             if "return" in i["value"] else i for i in m.get("worth") or []]
    return {**m, "consequences": consequences, "worth": worth}


def placed(store, cid: str, weights: dict[str, float]) -> dict:
    """A kept impact placed again at given weights (MATH.md 1.5): the Computation's own manifest, every reading it named,
    computed again with each world's weight `weights` gives it, nothing read from the store as it stands now. For a
    score that reads the weights today's prices put on the worlds at a past moment; marked with the version of the
    code that placed it. A manifest made before FRACTION is read in its own terms (as_fractions)."""
    found = store.computation(cid)
    if found["derivation"] != "impact":
        raise ValueError(f"{cid} is a Computation of {found['derivation']}, not of an event's impact")
    have = {w["id"] for w in found["inputs"]["worlds"]}
    if set(weights) != have or abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"weights name each of {sorted(have)} once and sum to 1")
    m = found["inputs"] if found["version"] >= FRACTION else as_fractions(found["inputs"])
    m = m if found["version"] >= REFERENCE else as_references(m)
    out = compute(m, store.engine.load, weights=weights)
    return {**out, "computation": cid, "version": IMPACT.version, "weights": dict(weights)}


# ---------------------------------------------------------------- each world against the baseline, whatever the probabilities


def _against_inputs(ctx: Inputs, pid: str) -> dict:
    """What each world of an event does against its baseline reads (MATH.md 1.3, 1.4, 1.6): its worlds as its links make
    them, what is stated of their prices, and what valuing the holdings reads, with no probability and no weight. A
    held contract is valued by what it pays in each world, as the impact values it (together._valued): where the links
    fix that, whatever the probabilities; one whose payoff depends on probabilities its statements leave open is left
    out, named with why (`waiting`)."""
    stated = ctx.object(pid)["stated"]
    way_nodes, stated_on, worth_on = _statements(ctx, pid)
    ways = [w["id"] for w in way_nodes]
    tree = outcomes.shape(ctx, pid, way_nodes)
    nodes, leaves = tree["nodes"], tree["worlds"]
    markets = sorted(h["id"] for h in ctx.links("portfolio", "holds") if h["to"].startswith("market:"))
    receipt: dict = {}
    try:
        contracts, contract_states = valuation.contract_inputs(ctx, pid, tree, receipt=receipt)
        waiting = receipt.get("waiting")
    except Unavailable as e:  # its structure cannot hold: every contract on a proposition of it waits
        within = set(structure.ctx_component(ctx, pid))
        on = [h["id"] for h in ctx.links("portfolio", "holds") if h["id"] in markets
              and any(a["from"] in within for a in ctx.links(h["to"], "asks", direction="in"))]
        contracts, contract_states, waiting = [], {}, ({"holdings": sorted(on), "why": str(e)} if on else None)
    if not stated_on and not worth_on and not contracts and waiting:
        raise Unavailable(f"{pid}'s held contracts wait: {waiting['why']}")
    _valued(pid, stated_on, worth_on, contracts, stated["baseline"], leaves)
    shape = lambda n: {"side": nodes[n]["side"], "parent": nodes[n]["parent"],  # noqa: E731
                       "title": nodes[n].get("title")}
    m = {"event": pid, "baseline": stated["baseline"],
         "worlds": [{"id": n, **shape(n)} for n in leaves],
         "tree": {n: {**shape(n), "ways": nodes[n]["ways"]} for n in nodes if nodes[n]["ways"]},
         **_reads(ctx, pid, stated, ways, stated_on, worth_on, tree),
         "contracts": contracts, "contract_states": contract_states}
    return _without(m, waiting)


def _against_compute(m: dict, load) -> dict:
    """Each world of an event against its baseline, whatever its probability's state (MATH.md 1.3, 1.4, 1.6): each
    factor's move there and its residual, each stated name's whole move and its residual, each holding's value there
    less its value in the baseline, and the portfolio's, its holdings summed.

    It is the impact's own valuation with today's price standing at the baseline: all the weight on the baseline, so
    that each price's move against today is its move against the baseline (1.5), and each holding is worth today's
    dollars at that move, or what its terms or its stated values give there. Every figure kept is one world's and reads
    no probability: a mean over a side reads the probabilities, so the row and the levels, which are placed, are the
    impact's."""
    base = m["baseline"]
    W = [w["id"] for w in m["worlds"]]
    q = {w: float(w == base) for w in W}
    # the valuation the impact shares reads each side's mean as it goes, here at these same weights; every such mean
    # is left out below
    got = compute({**m, "p": None, "worlds": [{**w, "p": q[w["id"]]} for w in m["worlds"]]}, load, weights=q)
    held = [{"holding": h["holding"], "name": h["name"], "value": m["values"].get(h["holding"], 0.0),
             "against_baseline": {w: h["worlds"][w] - h["worlds"][base] for w in W},
             **({"contract": h["contract"]} if h.get("contract") else {})} for h in got["by_holding"]]
    A = {w: sum(h["against_baseline"][w] for h in held) for w in W}
    leaf = {w["id"]: w for w in m["worlds"]}
    tree: list[dict] = []

    def visit(n: str, parent: str | None) -> None:
        node = leaf.get(n) or m["tree"][n]
        tree.append({"id": n, "parent": parent, "side": node["side"],
                     **({"title": node["title"]} if node.get("title") else {}),
                     **({"ways": list(m["tree"][n]["ways"])} if n in m["tree"] else {"against_baseline": A[n]})})
        for k in (m["tree"][n]["ways"] if n in m["tree"] else []):
            visit(k, n)

    for side in ("yes", "no"):
        if side in leaf or side in m["tree"]:
            visit(side, None)
    placed_ = ("levels", "yield", "yields", "rate", "rate_by")  # what the weights place, against today
    return {"event": m["event"], "baseline": base, "outcomes": tree,
            "worlds": {w: {k: leaf[w][k] for k in ("side", "parent", "title") if leaf[w].get(k)} for w in W},
            **{k: got[k] for k in ("against_baseline", "own_against_baseline", "setting", "episodes", "names_set",
                                   "whole", "d", "at_zero", "parts")},
            "consequences": {lid: {k: v for k, v in c.items() if k not in placed_}
                             for lid, c in got["consequences"].items()},
            "worth": {n: {k: w[k] for k in ("levels", "considerations", "c")} for n, w in (got.get("worth") or {}).items()},
            "by_holding": held, "A": A, **({"waiting": m["waiting"]} if m.get("waiting") else {})}


AGAINST_BASELINE = Derivation("against_baseline", 6, "Proposition", _against_inputs, _against_compute,
                              applies=lambda store, pid: store.watched(pid))


# ---------------------------------------------------------------- a consequence's levels and moves; a metric that quotes a factor


def _consequence_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    pid = root_of(lambda oid: ctx.links(oid, "way", "both"), link["from"])  # the event, for a way's consequence
    if not ctx.watched(pid):
        raise Unavailable(f"{pid} is not watched: its worlds, and so the levels and the moves its consequences give, "
                          f"are its impact's, which the store computes for an event the portfolio watches")
    duration = ctx.object(link["to"])["stated"].get("duration") if link["to"].startswith("factor:") else None
    return {"link": lid, "target": link["to"], "impact": ctx.value("impact", pid, ref=True), "duration": duration}


MOVE = "move"  # a price's move in a world against the baseline, in the terms a shock on it is stated in
FOLLOWED = "against_what_it_follows"  # its move against where what it follows puts it there, in the same terms


def _consequence_compute(m: dict, load) -> dict:
    """The price's level today and in each world, and in each world the consequence applies in its move against the
    baseline and against what it follows, in the terms a shock on it is stated in (MATH.md 1.3, 1.4): a fraction of the
    price, or points of a factor's yield; and its response there, the move it would have left unstated, in the same
    terms (`responses`, read by the page and no field of the link). As the event's impact placed them: nothing is
    computed here."""
    found = (load(m["impact"]).get("consequences") or {}).get(m["link"]) or {}
    terms = {"duration": m["duration"]} if m.get("duration") is not None else {}

    def stated_form(x: dict) -> dict:
        if "factor" in found:
            own = x.get("own_part")
            return {MOVE: worlds.in_its_terms(terms, x["raw"]),
                    FOLLOWED: worlds.in_its_terms(terms, math.expm1(own)) if own is not None else None}
        return {MOVE: x["whole"], FOLLOWED: x.get("d")}

    def response(x: dict) -> float | None:  # the move it would have there left unstated, in the same terms
        if "factor" in found:
            implied = x.get("implied")
            return worlds.in_its_terms(terms, math.expm1(implied)) if implied is not None else None
        return x.get("betas")
    ws = found.get("worlds") or {}
    return {"target": m["target"], "c": found.get("c"), "shock": found.get("shock"),
            schema.AGAINST: found.get(schema.AGAINST), "levels": found.get("levels"),
            "moves": {w: stated_form(x) for w, x in ws.items()},
            # the page's, beside the whole move and the residual: no field of the link, so get does not give it
            "responses": {w: response(x) for w, x in ws.items()},
            **{k: found[k] for k in ("yield", "yields", "currency", "rate", "rate_by") if k in found}}


CONSEQUENCE = Derivation("consequence", 11, "consequence", _consequence_inputs, _consequence_compute,
                         fields=("levels", "moves"))


# ---------------------------------------------------------------- the past windows a shock is sized from


def _windows_inputs(ctx: Inputs, lid: str) -> dict:
    """What measures the price a consequence moves over each past window its shock is sized from (ONTOLOGY.md 2, MATH.md 3.2): the
    factors' window, for a factor (window_returns), and the name's, for a name (name_window), each read as its own
    value, so that the window reads as the analyst read it with window_returns; or why it gives the price no move, a
    window that ends after the as-of among them, which the computation refuses."""
    link = ctx.link(lid)
    windows = (link["stated"].get("shock") or {}).get(schema.WINDOWS)
    if not windows:
        raise Unavailable(f"{lid}'s shock is sized from no past window")
    target = link["to"]
    out = {"link": lid, "target": target, "windows": windows}
    if target.startswith("factor:"):
        out["duration"] = ctx.object(target)["stated"].get("duration")
        subjects = [("window_returns", channels.window_subject(w["start"], w["end"])) for w in windows]
    else:
        subjects = [("name_window", valuation.name_window_subject(w["start"], w["end"], target)) for w in windows]
    measured = []
    for name, subject in subjects:
        try:
            measured.append(ctx.value(name, subject, ref=True))
        except Unavailable as e:  # the window gives the price no move: a read still under way is Waiting, not this
            measured.append({"why": str(e)})
    return {**out, "measured": measured}


def _windows_compute(m: dict, load) -> dict:
    """The price's move over each past window its shock is sized from, and its residual over it, in the terms a shock
    on it is stated in (MATH.md 1.3, 3.2): as window_returns gives the factor its move and residual, in points of its
    yield for a factor quoted as one; as it gives a name its move and its residual against its response at its betas
    fitted up to the window's start. Each names the two sessions it ran between and the Computation it was read from.
    A residual that is not measured, as where an earlier factor's proxy has no move over the window, is None. The
    shock stands as stated: these are what the price did, beside it. A window over which the price has no move, one
    that ends after the as-of among them, is refused with why (Refused), and the statement naming it fails, as one
    naming such an episode does."""
    terms = {"duration": m["duration"]} if m.get("duration") is not None else {}
    out = []
    for i, (w, ref) in enumerate(zip(m["windows"], m["measured"])):
        refuse = f"{m['link']}.shock.windows[{i}]: "
        if "why" in ref:
            raise Refused(refuse + ref["why"], [m["link"]])
        got = load(ref)
        if m["target"].startswith("factor:"):
            c = m["target"]
            move, residual = got["moves"].get(c), got["own_part"].get(c)
            if move is None:
                proxy = got["proxies"].get(c)
                raise Refused(refuse + ((got.get("why") or {}).get(proxy) or
                                        f"{proxy} has no move over the window, {w['start']} to {w['end']}"), [m["link"]])
            figures = {"sessions": got["sessions"][c], MOVE: worlds.in_its_terms(terms, move),
                       FOLLOWED: None if residual is None else worlds.in_its_terms(terms, residual)}
        else:
            figures = {"sessions": got["sessions"], MOVE: got["move"], FOLLOWED: got.get("residual")}
        out.append({"start": w["start"], "end": w["end"], **figures, "basis": ref["basis"]})
    return {"windows": out}


# what the price did over each past window a shock is sized from: what the store checks at the write
# (Store._check_windows) and the page shows beside the shock (ONTOLOGY.md 2), read as engine.value("shock_windows", lid); a
# field of no link, since the analyst reads the same figures with window_returns
SHOCK_WINDOWS = Derivation("shock_windows", 1, "consequence", _windows_inputs, _windows_compute,
                           applies=lambda store, lid: False)


def metric_level(ctx: Inputs, mid: str) -> dict:
    """A metric's level today: the price the store derives for a proxy's or a name's, or the level stated from a page."""
    if schema.PROXY_METRIC_RE.match(mid):
        found = ctx.value("proxy_price", mid)
        return {"c": found["output"].get("level"), "basis": found["basis"]}
    return {"c": ctx.object(mid)["stated"].get("level")}


def _quotes_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    level = metric_level(ctx, link["from"])
    if level["c"] is None:  # a metric is given its level where it has one (ONTOLOGY.md 2.1); one quoting a factor is its price
        raise Unavailable(f"{link['from']} states no level: its level in each world is its level today, moved as "
                          f"{link['to']}'s price moves there")
    impacts = {}
    for w in ctx.links("portfolio", "watches"):
        try:
            impacts[w["to"]] = ctx.value("impact", w["to"], ref=True)
        except Unavailable:  # an event with no figures has no worlds to read the metric in
            continue
    return {"link": lid, "metric": link["from"], "factor": link["to"], **level, "impacts": impacts}


def _quotes_compute(m: dict, load) -> dict:
    """A metric that quotes a factor, in each world of each watched event (ONTOLOGY.md 2.2): its level today at the factor's
    placed move there, as the factor's price moves."""
    levels = {}
    for pid, ref in m["impacts"].items():
        placed_ = load(ref).get("placed") or {}
        if not placed_ or any(m["factor"] not in v for v in placed_.values()):
            continue
        levels[pid] = {w: m["c"] * (1.0 + v[m["factor"]]) for w, v in placed_.items()}
    return {"c": m["c"], "levels": levels}


QUOTES = Derivation("quotes", 1, "quotes", _quotes_inputs, _quotes_compute, fields=("levels",))


def _worth_inputs(ctx: Inputs, lid: str) -> dict:
    link = ctx.link(lid)
    pid = root_of(lambda oid: ctx.links(oid, "way", "both"), link["from"])
    if not ctx.watched(pid):
        raise Unavailable(f"{pid} is not watched")
    return {"link": lid, "target": link["to"], "impact": ctx.value("impact", pid, ref=True)}


def _worth_compute(m: dict, load) -> dict:
    return (load(m["impact"]).get("worth") or {}).get(m["target"]) or {}


WORTH = Derivation("worth", 4, "worth", _worth_inputs, _worth_compute, fields=("levels", "implied"))

DERIVATIONS = (CONSEQUENCE, SHOCK_WINDOWS, QUOTES, WORTH, IMPACT, AGAINST_BASELINE)
