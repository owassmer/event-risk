"""The horizon distribution of an event's holdings (MATH.md 1.8).

One joint state is sampled for each draw. Factors, companies, currencies and fund
remainders share it, and every occurrence of a company sees the same price. The
ordinary state covariance uses production's completed-move estimator. Probabilities
mix the conditional distributions; they do not change their ordinary width.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from threading import Lock

import numpy as np

from harness.derive import estimate, history, series
from harness.derive.engine import Derivation, Inputs, Unavailable
from harness.derive.portfolio import ticker_of

SAMPLES = 16384  # the draws: a seeded draw of this many holds the chart's spread to about 1% of the portfolio's
# volatility on an event that changes nothing; every draw values every name, so more costs memory with each name
SEED = 61873
QUANTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)

# The ordinary covariance is identical across events that use the same observed
# state. Keep only completed numerical results, keyed by every array and setting;
# each stored Computation still records its own complete source references.
_COVARIANCE_CACHE_SIZE = 4
# Each event's state, with its factorization and loadings: events differ in it only by what one adds (an option fund's
# remainder, which differs by which of its contracts outlive the event, or an anchor), and none changes with a minute's
# marks, so one is kept for every event a ledger holds. About 1.3 MB each for a portfolio of 300 names.
_STATES_KEPT = 32
_COVARIANCE_CACHE: OrderedDict = OrderedDict()
_COVARIANCE_PENDING: dict[str, Future] = {}
_STATE_CACHE: OrderedDict = OrderedDict()
_STATE_PENDING: dict[str, Future] = {}
_COVARIANCE_LOCK = Lock()
# A name's residual is the portfolio's, the same in every event's state: its own returns less what its fitted response
# to the factors gives (estimate.residuals). Kept by what it follows from, so an event whose state differs from
# another's only in its own remainder or anchor reads every other name's residual once.
_RESIDUALS_KEPT = 4096
_RESIDUALS: OrderedDict = OrderedDict()


def _remember(cache, running, key, compute, kept: int = _COVARIANCE_CACHE_SIZE):
    """Bounded exact reuse, including requests already computing the same key."""
    with _COVARIANCE_LOCK:
        if key in cache:
            value = cache.pop(key)
            cache[key] = value
            return value
        pending = running.get(key)
        owner = pending is None
        if owner:
            pending = running[key] = Future()
    if owner:
        try:
            value = compute()
        except BaseException as error:
            with _COVARIANCE_LOCK:
                running.pop(key)
                pending.set_exception(error)
            raise
        with _COVARIANCE_LOCK:
            cache[key] = value
            while len(cache) > kept:
                cache.popitem(last=False)
            running.pop(key)
            pending.set_result(value)
    else:
        value = pending.result()
    return value


def completed_covariance(values: np.ndarray, ok: np.ndarray, memory: int) -> np.ndarray:
    """Production's Bartlett covariance, corrected for its finite-window centring.

    The whole state enters the estimator together: lagged factor/residual and
    residual/residual cross-covariances are retained. No variance is subtracted
    from an ordinary draw to approximate this calculation.
    """
    if int(ok.sum()) < 2:
        raise ValueError("a covariance needs at least two shared sessions")
    covariance = estimate.covariances(values, ok, memory)[2]
    kept = estimate.centring(ok, memory)
    if kept <= 0:
        raise ValueError("the shared sessions do not identify a centred covariance")
    return estimate.sym(covariance / kept)


def state_covariance(factors: np.ndarray, residuals: dict[str, np.ndarray], *, memory: int,
                     own_lean: float, kappa: float) -> tuple[list[str], np.ndarray, list[dict]]:
    """Reuse an exact covariance only when all histories, names and settings match."""
    x = np.ascontiguousarray(factors, dtype=float)
    ys = {n: np.ascontiguousarray(v, dtype=float) for n, v in sorted(residuals.items())}
    digest = sha256(json.dumps([x.shape, [(n, y.shape) for n, y in ys.items()],
                               memory, own_lean, kappa], separators=(",", ":")).encode())
    digest.update(x.tobytes())
    for y in ys.values():
        digest.update(y.tobytes())
    key = digest.hexdigest()
    value = _remember(_COVARIANCE_CACHE, _COVARIANCE_PENDING, key,
                      lambda: _state_covariance(x, ys, memory=memory, own_lean=own_lean, kappa=kappa))
    # Callers append dated anchor columns; never let that mutate the shared result.
    return value[0].copy(), value[1].copy(), deepcopy(value[2])


def _state_covariance(factors: np.ndarray, residuals: dict[str, np.ndarray], *, memory: int,
                      own_lean: float, kappa: float) -> tuple[list[str], np.ndarray, list[dict]]:
    """A covariance for the state, independent of the portfolio's allocations.

    With common histories this is exactly the completed covariance of all columns.
    A younger history joins by its conditional covariance over the sessions shared
    with the existing state. Its conditional response leans toward zero, using the
    production response strength, and its unexplained covariance has the production
    one-session market prior. The block construction is positive semidefinite and
    preserves the existing state's covariance and full history.

    `estimate.figure` estimates one portfolio's scalar variance, with its own parts
    grouped and weighted in dollars. That scalar does not identify a multivariate
    distribution. This extension retains the same estimator and priors while
    defining one state reusable for every holding allocation; it does not rescale
    the state to force agreement with that separate scalar estimate.
    """
    x = np.asarray(factors, dtype=float)
    base = estimate.common(x)
    columns: list[str] = []
    matrix = x.copy()
    covariance = completed_covariance(x, base, memory)
    market = max(float(covariance[0, 0]), 0.0)
    groups: dict[bytes, list[str]] = {}
    for name, values in sorted(residuals.items()):
        mask = base & np.isfinite(values)
        groups.setdefault(mask.tobytes(), []).append(name)
    # Longer histories enter first. A subsequent young listing never shortens them.
    ordered = sorted(groups.values(), key=lambda names: (-int((base & np.isfinite(residuals[names[0]])).sum()), names))
    details = []
    for names in ordered:
        y = np.column_stack([residuals[n] for n in names])
        mask = estimate.common(matrix) & estimate.common(y) & base
        n = int(mask.sum())
        if np.array_equal(mask, base) and np.all(np.isfinite(matrix[base])):
            # No missing history or estimated extension: one direct joint reading.
            covariance = completed_covariance(np.column_stack([matrix, y]), base, memory)
            method = "joint completed covariance"
        else:
            old = matrix.shape[1]
            count = len(names)
            b = np.zeros((count, old))
            own = np.eye(count) * market
            if n >= 2:
                sample = completed_covariance(np.column_stack([matrix, y]), mask, memory)
                xx, xy, yy = sample[:old, :old], sample[:old, old:], sample[old:, old:]
                plain = xy.T @ np.linalg.pinv(xx, hermitian=True)
                error = estimate.sym(yy - plain @ xy)
                effective = estimate.freedom(mask, memory)
                # As in the production response prior, a noisier residual leans
                # further. Each row has its own precision in market-session units.
                relative = np.maximum(np.diag(error), 0.0) / market if market > 0 else np.zeros(count)
                lean = effective / (effective + kappa * relative) if effective > 0 else np.zeros(count)
                b = plain * lean[:, None]
                own = estimate.sym(yy - b @ xy - xy.T @ b.T + b @ xx @ b.T)
                own = (effective * own + own_lean * market * np.eye(count)) / (effective + own_lean) \
                    if effective + own_lean > 0 else own
            cross = covariance @ b.T
            covariance = np.block([[covariance, cross], [cross.T, b @ covariance @ b.T + own]])
            method = "conditional history extension" if n >= 2 else "market prior"
        matrix = np.column_stack([matrix, y])
        columns.extend(names)
        details.append({"names": names, "sessions": n, "method": method})
    return columns, estimate.sym(covariance), details


def factorization(covariance: np.ndarray) -> np.ndarray:
    """A factorization including exact zero directions, with only rounding removed."""
    eig, vectors = np.linalg.eigh(estimate.sym(covariance))
    scale = max(float(np.max(np.abs(eig), initial=0.0)), 1e-20)
    if float(np.min(eig, initial=0.0)) < -1e-8 * scale:
        raise ValueError("the joint state covariance is not positive semidefinite")
    return vectors * np.sqrt(np.maximum(eig, 0.0))[None, :]


def append_dated_residual(current: np.ndarray, historical: np.ndarray, factors: int) -> np.ndarray:
    """Carry a dated residual's measured conditional law onto today's factor state.

    Used when an unaffected company's pre-event residual history no longer overlaps
    the current window. Its covariance with the common factors and its variance
    beyond them come from that history. Nothing is regressed on today's deal price.
    """
    old = np.asarray(historical, dtype=float)
    b = old[-1, :factors] @ np.linalg.pinv(old[:factors, :factors], hermitian=True)
    own = float(old[-1, -1] - b @ old[:factors, -1])
    if own < -1e-8 * max(float(old[-1, -1]), 1e-20):
        raise ValueError("the dated residual covariance is not positive semidefinite")
    cross = current[:, :factors] @ b
    variance = float(b @ current[:factors, :factors] @ b) + max(own, 0.0)
    return np.block([[current, cross[:, None]], [cross[None, :], np.array([[variance]])]])


def weighted_quantiles(values: np.ndarray, weights: np.ndarray, probabilities=QUANTILES) -> dict:
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    cumulative = np.cumsum(w)
    cumulative /= cumulative[-1]
    return {str(p): float(v[min(int(np.searchsorted(cumulative, p)), len(v) - 1)]) for p in probabilities}


def summarize(samples: dict[str, np.ndarray], worlds: dict, *, centres: dict | None = None,
              sample_weights: dict[str, np.ndarray] | None = None) -> dict:
    """The mixture's actual sampled distribution and its variance identity."""
    conditional = {w: np.asarray((sample_weights or {}).get(w, np.ones(len(v))), dtype=float)
                   for w, v in samples.items()}
    conditional = {w: v / v.sum() for w, v in conditional.items()}
    means = {w: float(conditional[w] @ v) for w, v in samples.items()}
    within = {w: float(conditional[w] @ (v - means[w]) ** 2) for w, v in samples.items()}
    p = {w: float(worlds[w]["p"]) for w in samples}
    total_probability = sum(p.values())
    if not math.isclose(total_probability, 1.0, abs_tol=1e-8):
        raise ValueError("the worlds' probabilities must sum to one")
    mean = sum(p[w] * means[w] for w in samples)
    between = sum(p[w] * (means[w] - mean) ** 2 for w in samples)
    ordinary = sum(p[w] * within[w] for w in samples)
    positive = [w for w in samples if p[w] > 0]
    values = np.concatenate([samples[w] for w in positive])
    weights = np.concatenate([conditional[w] * p[w] for w in positive])
    order = np.argsort(values, kind="stable")
    sorted_values, cumulative = values[order], np.cumsum(weights[order])
    # All discrete mass is represented at its right-continuous CDF value. The
    # display samples this empirical CDF, not a Gaussian fitted to two moments.
    unique, counts = np.unique(sorted_values, return_counts=True)
    at = np.cumsum(counts) - 1
    positions = np.unique(np.linspace(0, len(unique) - 1, min(161, len(unique))).astype(int))
    cdf = [{"pnl": float(unique[i]), "probability": float(cumulative[at[i]])} for i in positions]
    low, high = float(np.min(values)), float(np.max(values))
    if high == low:
        edges = np.array([low - 0.5, high + 0.5])
    else:
        edges = np.linspace(low, high, 61)
    mass = np.histogram(values, bins=edges, weights=weights)[0]
    out = {}
    for w, v in samples.items():
        out[w] = {"p": p[w], "side": worlds[w].get("side"), "mean": means[w],
                  "variance": within[w], "centre": (centres or {}).get(w, worlds[w].get("A")),
                  "quantiles": weighted_quantiles(v, conditional[w]),
                  "mass": np.histogram(v, bins=edges, weights=conditional[w] * p[w])[0].tolist()}
    # 1.8's parts of the whole: the variance between the outcomes' means, and the outcomes' own variances at their
    # probabilities; each outcome's own variance is its `variance`
    return {"worlds": out, "mean": mean,
            "variance": {"between": between, "within": ordinary, "total": between + ordinary},
            "quantiles": weighted_quantiles(values, weights), "cdf": cdf,
            "bins": {"edges": edges.tolist(), "mass": mass.tolist()},
            "sample_count": len(next(iter(samples.values())))}


def _inputs(ctx: Inputs, pid: str) -> dict:
    from harness.derive import impact, valuation
    portfolio = ctx.object("portfolio")["stated"]
    asof = portfolio["asof"]
    end = (date.fromisoformat(asof) + timedelta(days=portfolio["horizon_days"])).isoformat()
    centre = ctx.value("impact", pid, ref=True)
    fitted = ctx.value("betas", "portfolio", ref=True)
    fitted_output = ctx.payload(fitted)
    fits = fitted_output.get("fits") or {}
    look = ctx.value("lookthrough", "portfolio")["output"]
    kinds = look.get("kinds") or {}
    names = {n: (kinds.get(n) or {}).get("beta") or ticker_of(n)
             for n, fit in fits.items() if "session" in fit}
    for ticker, fit in (fitted_output.get("underlyings") or {}).items():
        if "session" in fit:
            names.setdefault(f"name:{ticker}", ticker)
    found = history.series_inputs_many(ctx, names.values(), asof, required=lambda t: False)
    valued = ctx.read_of(centre)  # what the event's worlds are valued on: the manifest its centre was worked out from
    contract_receipt = {}
    contracts, contract_states = valuation.contract_inputs(
        ctx, pid, ctx.value("outcomes", pid)["output"], through=end, receipt=contract_receipt)
    valued = impact._without({**valued, "contracts": contracts, "contract_states": contract_states},
                             contract_receipt.get("waiting"))
    return {"event": pid, "asof": asof, "horizon_end": end, "sample_count": SAMPLES, "seed": SEED,
            "impact": centre, "valuation": valued, "contract_receipt": contract_receipt,
            "factors": ctx.value("factors", "market", ref=True), "betas": fitted,
            "series": {n: found[t] for n, t in names.items()}}


class State:
    """An event's joint state, read only and shared by every reader of it (_state): what it is (`about`), its covariance
    over completed moves, that covariance's factorization, taken once (factorization; a period's is it times the root
    of the period's sessions), and each price's loading on it. A loading is a price's response to the factors and its
    own part's column, with its coefficient, none for a factor's proxy: `rows` maps each price drawn to its loading's
    row, so a company held under two ids is one row and one draw. Each row's log-variance per session is its loading
    through the covariance."""

    def __init__(self, about: dict, covariance: np.ndarray, linear: dict, fm: dict):
        k = len(about["channels"])
        self.about, self.fm, self.k = about, fm, k
        self.covariance = covariance
        self.root = factorization(covariance)
        distinct: dict[int, int] = {}
        loadings: list[np.ndarray] = []
        self.rows: dict[str, int] = {}
        for n, a in linear.items():
            if id(a) not in distinct:
                distinct[id(a)] = len(loadings)
                loadings.append(np.asarray(a, dtype=float))
            self.rows[n] = distinct[id(a)]
        a = np.array(loadings) if loadings else np.zeros((0, len(covariance)))
        self.response = np.ascontiguousarray(a[:, :k])
        own = [np.flatnonzero(row[k:]) for row in a]
        if any(len(o) > 1 for o in own):
            raise ValueError("a price's loading has one own part at most")
        self.owned = np.array([i for i, o in enumerate(own) if len(o)], dtype=int)
        self.own = np.array([k + int(own[i][0]) for i in self.owned], dtype=int)
        self.coefficient = np.array([a[i, j] for i, j in zip(self.owned, self.own)], dtype=float)
        self.log_variance = np.maximum(np.einsum("ij,jk,ik->i", a, covariance, a), 0.0) if len(a) else np.zeros(0)
        for x in (self.covariance, self.root, self.response, self.owned, self.own, self.coefficient,
                  self.log_variance):
            x.setflags(write=False)

    def log_moves(self, changes: np.ndarray) -> np.ndarray:
        """Each row's compounded move at each draw, rows first: its response to the factors' changes, and its own part's
        change times its coefficient."""
        out = self.response @ changes[:, :self.k].T
        if len(self.owned):
            out[self.owned] += self.coefficient[:, None] * changes[:, self.own].T
        return out


def _state(m: dict, load) -> State:
    centre = m.get("centre") or load(m["impact"])
    # Production references include hashes of immutable payloads. Bare references
    # in pure callers have no such guarantee and are deliberately not cached.
    if not all(isinstance(m[k], dict) and m[k].get("sha256") for k in ("factors", "betas")):
        return State(*_state_uncached(m, load))
    terms = centre.get("valuation_terms") or {}
    remainder = {n: book["remainder"] for n, book in (terms.get("options") or {}).items()
                 if book.get("remainder")}
    anchor_keys = ("state_key", "date", "full", "sessions", "residuals", "state_covariance")
    anchors = {claim["anchor"]["state_key"]: {k: claim["anchor"][k] for k in anchor_keys}
               for claims in (terms.get("worth") or {}).values() for claim in claims.values() if claim.get("anchor")}
    key = sha256(json.dumps([m["factors"], m["betas"], m["series"], remainder, anchors],
                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return _remember(_STATE_CACHE, _STATE_PENDING, key, lambda: State(*_state_uncached(m, load)), _STATES_KEPT)


def _residual(factors: dict | None, n: str, session, item: dict | None, compute) -> np.ndarray:
    """A name's residual over the common history, kept by the factors' Computation, the name, its fitted session
    coefficients and its series' references (each with its hash); computed where nothing names it by its content."""
    if factors is None:
        return compute()
    key = sha256(json.dumps([factors, n, session, item], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with _COVARIANCE_LOCK:
        found = _RESIDUALS.get(key)
        if found is not None:
            _RESIDUALS.move_to_end(key)
            return found
    value = np.asarray(compute(), dtype=float)
    value.setflags(write=False)  # read only, as every reader shares it
    with _COVARIANCE_LOCK:
        _RESIDUALS[key] = value
        while len(_RESIDUALS) > _RESIDUALS_KEPT:
            _RESIDUALS.popitem(last=False)
    return value


def _state_uncached(m: dict, load) -> tuple[dict, np.ndarray, dict, dict]:
    from harness.derive.channels import model
    fm = load(m["factors"])
    mdl = model(fm)
    betas = load(m["betas"])
    residuals, responses = {}, {}
    all_fits = dict(betas.get("fits") or {})
    for ticker, fit in (betas.get("underlyings") or {}).items():
        all_fits.setdefault(f"name:{ticker}", fit)
    kept = isinstance(m["factors"], dict) and bool(m["factors"].get("sha256"))
    for n, fit in sorted(all_fits.items()):
        if "session" not in fit:
            continue
        session = np.asarray(fit["session"], dtype=float).reshape(2, mdl.k)
        item = m["series"].get(n)
        residuals[n] = _residual(m["factors"] if kept else None, n, fit["session"], item,
                                 lambda: estimate.residuals(mdl.x, mdl.ok, session,
                                                            history.returns(item, load, mdl.sessions)["dollar"]
                                                            if item else []))
        responses[n] = np.asarray(fit["full"], dtype=float)
    centre = m.get("centre") or load(m["impact"])
    for fund, terms in ((centre.get("valuation_terms") or {}).get("options") or {}).items():
        remainder = terms.get("remainder") or {}
        if "session" in remainder:
            n = f"remainder:{fund}"
            session = np.asarray(remainder["session"], dtype=float).reshape(2, mdl.k)
            residuals[n] = estimate.residuals(mdl.x, mdl.ok, session, remainder.get("returns") or [])
            responses[n] = np.asarray(remainder["full"], dtype=float)
    anchors = {}
    for claims in ((centre.get("valuation_terms") or {}).get("worth") or {}).values():
        for claim in claims.values():
            if claim.get("anchor"):
                anchor = claim["anchor"]
                anchors[anchor["state_key"]] = anchor
    detached = {}
    for n, anchor in anchors.items():
        old = dict(zip(anchor["sessions"][1:], anchor.get("residuals") or []))
        aligned = np.array([old.get(day, np.nan) for day in mdl.sessions[1:]], dtype=float)
        if int(np.isfinite(aligned[mdl.ok]).sum()) >= 2:
            residuals[n] = aligned
        else:
            detached[n] = anchor
        responses[n] = np.asarray(anchor["full"], dtype=float)
    # A completed residual uses the current and previous factor session. All
    # residuals lack the first row, so that row is excluded for the joint reading.
    x = mdl.x[1:] if residuals else mdl.x
    residuals = {n: e[1:] for n, e in residuals.items()}
    settings = mdl.settings
    names, covariance, cohorts = state_covariance(x, residuals, memory=settings.memory,
                                                 own_lean=settings.own_lean, kappa=settings.kappa)
    for n, anchor in detached.items():
        covariance = append_dated_residual(covariance, np.asarray(anchor["state_covariance"]), mdl.k)
        names.append(n)
        cohorts.append({"names": [n], "sessions": len(anchor["sessions"]) - 1,
                        "method": "dated residual conditional on factors", "through": anchor["date"]})
    dimension = covariance.shape[0]
    linear = {}
    for n, response in responses.items():
        a = np.zeros(dimension)
        a[:mdl.k] = response
        a[mdl.k + names.index(n)] = 1.0
        linear[n] = a
    # The same identified reference has one draw under either of its data ids.
    for ticker, fit in (betas.get("underlyings") or {}).items():
        if f"name:{ticker}" in linear:
            linear[ticker] = linear[f"name:{ticker}"]
    # A factor's proxy and that same asset held directly are one price, not a
    # second regression with an independent residual draw.
    for j, ticker in enumerate(fm["tickers"]):
        a = np.zeros(dimension)
        a[j] = 1.0
        linear[f"name:{ticker}"] = a
        linear[ticker] = a
    return {"channels": mdl.channels, "names": names, "cohorts": cohorts,
            "memory": settings.memory, "sessions": int(estimate.common(x).sum())}, covariance, linear, fm


def _compute(m: dict, load) -> dict:
    from harness.derive import valuation
    centre = m.get("centre") or load(m["impact"])
    if not centre.get("worlds"):
        raise Unavailable("the event has no valued worlds to draw its distribution from")
    st = _state(m, load)
    state, covariance = st.about, st.covariance
    ahead = series.sessions_after(m["asof"], m["horizon_end"])
    factor_ids = state["channels"]
    dimension = len(covariance)
    sample_count = m["sample_count"]
    rng = np.random.default_rng(m["seed"])
    # Common random numbers preserve each world's shape when only its probability
    # changes; antithetic pairs reduce numerical error in its centre.
    half = rng.standard_normal((math.ceil(sample_count / 2), dimension))
    z = np.concatenate([half, -half], axis=0)[:sample_count]
    increments = z @ (st.root.T * math.sqrt(ahead))  # the covariance over `ahead` sessions, factorized once
    log_variances = st.log_variance * ahead  # each row's, over the period
    factor_variances = np.maximum(np.diag(covariance)[:len(factor_ids)] * ahead, 0.0)
    path_moves = {}
    ratios = {k: centre.get(k) or {} for k in ("name_ratios", "reference_ratios", "remainder_ratios",
                                                "anchor_ratios", "responding_ratios")}
    drawn = list(st.rows)
    rows = np.array([st.rows[n] for n in drawn], dtype=int)
    responding = [n for n in ratios["responding_ratios"] if n in st.rows]

    def moved(world: str, changes: np.ndarray, sessions: int) -> tuple[dict, dict, dict]:
        # Each price's draws as ratios to its centre, matched to a mean of exactly 1 (valuation.zero_drift): zero
        # drift holds on the draws, so a world's mean departs from its centre only by what a holding whose value is
        # not in proportion to a price adds.
        ratio = sessions / ahead if ahead else 0.0
        factors = {c: (1.0 + centre["placed"][world][c])
                   * valuation.zero_drift(np.exp(changes[:, j] - 0.5 * factor_variances[j] * ratio))
                   for j, c in enumerate(factor_ids)}
        path_key = (id(changes), sessions)
        if path_key not in path_moves:  # every row's draws at once, rows first, each with its mean 1 (zero_drift)
            moves = np.exp(st.log_moves(changes) - 0.5 * ratio * log_variances[:, None])
            means = moves.mean(axis=1)
            matched = means > 0.0  # as zero_drift: a price whose draws are all zero stays as it is
            moves[matched] /= means[matched, None]
            path_moves[path_key] = moves
            if len(path_moves) > 2:
                path_moves.pop(next(iter(path_moves)))
        name_moves = path_moves[path_key]

        def centred(n: str) -> float:
            for k in ("name_ratios", "reference_ratios", "remainder_ratios", "anchor_ratios"):
                if world in ratios[k].get(n, {}):
                    return float(ratios[k][n][world])
            return 1.0
        drawn_now = name_moves[rows] * np.array([centred(n) for n in drawn])[:, None]
        names = dict(zip(drawn, drawn_now))
        names.update({"response:" + n: ratios["responding_ratios"][n][world] * name_moves[st.rows[n]]
                      for n in responding})
        variances = {n: float(log_variances[st.rows[n]]) * ratio for n in drawn}
        variances.update({c: float(factor_variances[j]) * ratio for j, c in enumerate(factor_ids)})
        variances["__factor_covariance__"] = covariance[:len(factor_ids), :len(factor_ids)] * sessions
        return factors, names, variances

    terms = centre.get("valuation_terms") or {}
    expiries = sorted({s["expiry"] for group in (terms.get("options") or {}).values()
                       for s in (group.get("contracts") or [])
                       if s.get("expiry") and m["asof"] < s["expiry"] < m["horizon_end"]})
    expiry_increments = {}
    # Conditional Brownian bridges retain the same joint path for early expiry
    # and the horizon. Generate dates in chronological order, not independently.
    previous = np.zeros_like(increments)
    previous_time = 0
    for expiry in expiries:
        t = series.sessions_after(m["asof"], expiry)
        fraction = (t - previous_time) / (ahead - previous_time) if ahead > previous_time else 0.0
        bridge_variance = (t - previous_time) * (ahead - t) / (ahead - previous_time) if ahead > previous_time else 0.0
        noise = rng.standard_normal(increments.shape) @ (st.root.T * math.sqrt(bridge_variance))
        previous = previous + fraction * (increments - previous) + noise
        previous_time = t
        expiry_increments[expiry] = (previous.copy(), t)
    samples, sample_weights, holding_means, valuation_receipts = {}, {}, {}, {}
    contracts = {c["holding"]: c for c in m["valuation"].get("contracts") or []
                 if c.get("close") and c["close"][:10] <= m["horizon_end"]}
    for world in centre["worlds"]:
        factors, names, variances = moved(world, increments, ahead)
        expiry_states = {}
        for expiry, (bridge, t) in expiry_increments.items():
            fs, ns, vs = moved(world, bridge, t)
            expiry_states[expiry] = {"factors": fs, "names": ns, "variances": vs}
        receipt = {}
        values = valuation.value_state(m["valuation"], load, centre, world, factors, names, variances,
                                       m["horizon_end"], expiry_states=expiry_states, receipt=receipt)
        if receipt:
            valuation_receipts[world] = receipt
        total = np.zeros(sample_count)
        holding_means[world] = {}
        for hid, value in values.items():
            pnl = np.asarray(value, dtype=float) - m["valuation"]["values"][hid]
            total += pnl
            holding_means[world][hid] = float(pnl.mean())
        atoms = (m["valuation"].get("contract_states") or {}).get(world) or []
        if atoms and contracts:
            shifted, weights = [], []
            for atom in atoms:
                adjustment = sum(c["quantity"] * c["discount"]
                                 * (atom["payoffs"][hid] - c["probabilities"][world])
                                 for hid, c in contracts.items()
                                 if hid in atom["payoffs"] and world in c.get("probabilities", {}))
                shifted.append(total + adjustment)
                weights.append(np.full(sample_count, atom["p"] / sample_count))
            samples[world] = np.concatenate(shifted)
            sample_weights[world] = np.concatenate(weights)
        else:
            samples[world] = total
    return {**summarize(samples, centre["worlds"], sample_weights=sample_weights),
            "sample_count": sample_count, "horizon_end": m["horizon_end"],
            "sessions_ahead": ahead, "state": state,
            "holding_means": holding_means, "valuation_receipts": valuation_receipts,
            "contract_receipt": m.get("contract_receipt") or {},
            "drift": "zero in today's dollars", "seed": m["seed"]}


def receipt(out: dict, pid: str) -> dict:
    """The analyst's compact read; the basis opens all draws' chart and covariance.

    The page reads the complete Computation. A default object read carries the
    forecast's quantities and method without repeating its plotting coordinates.
    """
    keys = ("horizon_end", "sessions_ahead", "sample_count", "mean", "variance", "quantiles", "drift")
    world_keys = ("p", "side", "mean", "variance", "centre", "quantiles")
    summary = {k: out[k] for k in keys}
    summary["worlds"] = {w: {k: row[k] for k in world_keys} for w, row in out["worlds"].items()}
    summary["sessions"] = out["state"]["sessions"]  # the completed moves the state's covariance is measured over
    if out.get("valuation_receipts"):
        summary["valuation_receipts"] = out["valuation_receipts"]
    if out.get("contract_receipt"):
        summary["contract_receipt"] = out["contract_receipt"]
    return {"distribution": summary}


DISTRIBUTION = Derivation("distribution", 3, "Proposition", _inputs, _compute,
                          fields=("distribution",), applies=lambda store, pid: store.watched(pid),
                          pick=receipt, stage="distribution")
DERIVATIONS = (DISTRIBUTION,)
