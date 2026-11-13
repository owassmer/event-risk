"""The estimation (MATH.md 1.2, 1.3, 3.2): the channels' covariance over completed moves, and each series' response to
them. The store's derivations read it (harness.derive.channels); nothing here reads the store.

The common history. The factor covariance and the sensitivity fits read the market sessions of the window for which
every channel's proxy has a return: nothing is filled in for a session a proxy lacks, so a proxy with a shorter history
shortens the common history for every channel, and the channel's orthogonalization says how many sessions it holds and
from when. Each lag is a market session: a session the common history leaves out contributes nothing, and never makes
the session before it count as the one before the next.

The channels' covariance. An event's shock is a completed move, so the channels' covariance is that of their completed
moves: the sample covariance of the proxies' returns over the common history, Gamma_0, with their covariances at lags
of up to L market sessions added at Bartlett's declining weights (Newey and West, Econometrica 1987),
Omega = Gamma_0 + sum_{h=1..L} (1 - h / (L + 1)) (Gamma_h + Gamma_h'), Gamma_h = sum_t c_t c_{t-h}' / N over the
sessions t and t - h both in the common history, c the returns less their mean there, N its sessions: positive
semi-definite by construction, and in no order of the channels. The fill of 1.3 reads it (impact), and so does the
orthogonalization, Omega's LDL' in channel order (ldl): channel 1 is its proxy's return, and channel k's own part is its
proxy's return less its coefficients on the earlier channels' own parts.

Each series' response. A holding, a currency, or a name a fund reaches is fitted on its own returns over the spans of
the window's sessions it has (an untraded stretch one span) for which the channels' returns of each session of the span
and of the session before each exist: y = alpha n + s' X0 + b1' (X1 - X0) + e, Var(e) = n sigma^2, n the span's
sessions, X0 the channels' raw proxy returns summed over the span's sessions and X1 over the sessions before each. Its
coefficients on the same session, b0 = s - b1, and on the session before, b1, are free in sign; their sum s is its full
response, the beta g, D, and d_i read, in the proxies' raw basis (L' s in the orthogonalized one). The previous session
carries delayed responses, where a market closes before New York's or a price lags, and what a factor's own
continuation does to the series apart from the news: so s is the fitted model's cumulative response, which equals the
completed-move ratio Cov_LR(y, F) Omega^-1 only as L grows, and the two are scored together where L is chosen.

The priors, in the unit-information metric at the market's variance sigma_m^2, the first channel's same-session
variance over the common history: the full response s ~ N(centre, (sigma_m^2 / kappa) Omega^-1) and the previous
session's coefficient b1 ~ N(0, (sigma_m^2 / lambda) Gamma_0^-1), the intercept flat (Kass and Wasserman, JASA 1995).
Each strength counts in sessions of a series as variable as the market, so a series noisier than the market leans
more, by its own precision (Vasicek, J. Finance 1973), and one kappa holds whatever channels the analyst adds. A listed
share's centre is the market's sensitivity, 1 on the first channel and 0 on the others (raw and orthogonalized alike,
since the first row of the LDL' factor is (1, 0, ..., 0)); a currency or crypto pair's is no response, a pair being no
listed share (`no_response_prior`); a fund whose rows are read is centred on what its rows say (`rows_prior`): each
row at the response of what it refers to, a share no series prices at the listed share's prior, and what the rows leave
undetermined at no response of its own, only its uncertainty, so that the fund's own returns determine it.
lambda = 0 frees the lag, lambda = inf removes it; kappa = 0 is no pull. Own risk sigma^2 has one session's prior at the
market's variance, estimated with the coefficients in turn until neither moves.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class Settings:
    """The estimator's choices: each Computation that reads one records it, so changing it recomputes what reads it.
    Each was measured through this module on cases drawn by a stated rule, at 248 dates over twenty years, each choice
    judged on the half of the cases not used to make it, by how well the fits predicted completed moves 5, 21 and 63
    sessions ahead. A strength is in sessions of a series as variable as the market."""
    # kappa: the full response's pull toward the market's sensitivity (a pair's toward none). Four sessions predicted
    # better than none (by 6 to 40% for moderately traded listings) and than 64 or more, on both halves, the region
    # from 4 to 16 flat within 1%.
    kappa: float = 4.0
    # lambda: the previous session's coefficient's pull toward none, for a series whose closes are New York's. 256
    # sessions predicted better than the lag left free (by 0.5 to 3%) and than the lag removed (by 0.3 to 0.4% for
    # heavily traded listings); 16 cost liquid US listings 0.7 to 1% of their loss.
    lag_strength: float = 256.0
    # lambda for a series most of whose closes are struck before New York's close on the session each belongs to (a
    # listing on an exchange that closes earlier). On a fresh draw of 600 listings abroad (831 fits; two with errors in
    # their home prices left out), 16 predicted completed moves better than 256 for the 662 fits struck early, by 1.2,
    # 1.7 and 2.2% of the loss at 5, 21 and 63 sessions, in both halves of the period; for the 77 that close with New
    # York, 16 was worse by 2.0% at 21 sessions, so the lighter strength goes only to the series struck early.
    lag_strength_early: float = 16.0
    # kappa_fund: a young fund's pull toward what its rows say, its spread about their sum. On a fresh draw of 182
    # first-year equity fund fits, no pull was measurably worse than 16 (by 10% at 5 sessions, 11 to 13% at 21),
    # and 16 and 64 were indistinguishable. On 415 first-year fund-as-ofs read through production's rows, 64
    # against 16 differed measurably only for funds not of equity at 63 sessions (better by 0.65% of the loss), and
    # that rested on the 80 fund-as-ofs whose options, named only in their rows' names, production read as holdings at
    # their market value: without them (eval's 6774e3a), 64 against 16 is not measurable in either direction for any
    # class at any horizon. 64 is unmeasured against 16, and is read again once those rows are read as what they are.
    kappa_fund: float = 16.0
    # kappa_undetermined: what each dollar of a young fund's exposure its rows leave undetermined (a bond, a rate or
    # credit derivative, a commodity or coin with no series, a swap whose underlying is not identified) adds to the
    # uncertainty of the rows' centre, in place of a response: its square times this many sessions' worth of the
    # unit-information form. One session is nothing beside a fund's own returns, so a fund made of such rows stands on
    # its own history, and one with a small part of them keeps nearly all of its pull. Reasoned: on 415 first-year
    # fund-as-ofs, strengths from none to 4 were indistinguishable for funds mostly and partly undetermined alike,
    # once the one fund in each group that carried its differences was set aside, so the evidence does not decide it.
    kappa_undetermined: float = 1.0
    # L: the market sessions of lagged co-movement the channels' covariance adds, at Bartlett's weights. Five was the
    # one length measurably worse than no other construction: the same session and two missed co-movement across
    # asset classes, and ten added noise.
    memory: int = 5
    # own_lean: how many sessions' worth a holding's own variability leans toward the market's, in the portfolio's
    # volatility (MATH.md 3.2), as its fit's own risk does (one session at the market's variance); its co-movement with
    # the holdings before it leans toward none as its fit's response does, at kappa. Stronger leans toward the market's
    # variance, 16 and 64, were worse on held-out years for holdings in their first year (evaluation's 47o): a new
    # listing's own variability is far above the market's.
    own_lean: float = 1.0

    def as_dict(self) -> dict:
        return asdict(self)


SETTINGS = Settings()  # read at each computation, so eval measures another setting by replacing it
EPS = 1e-12


def sym(m: np.ndarray) -> np.ndarray:
    return (m + m.T) / 2.0


def inv_pd(m: np.ndarray) -> np.ndarray:
    """The inverse of a symmetric positive definite matrix, through its Cholesky factor."""
    c = np.linalg.cholesky(sym(m))
    ci = np.linalg.solve(c, np.eye(len(m)))
    return ci.T @ ci


def ldl(omega: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Omega = L diag(d) L', L unit lower triangular: row k holds channel k's coefficients on the earlier channels'
    own parts, and d[k] is channel k's own part's variance."""
    c = np.linalg.cholesky(sym(omega))
    d = np.diag(c) ** 2
    return c / np.diag(c), d


def pack(a: np.ndarray) -> list:
    """An array as data, a value that is not a number as null."""
    return np.where(np.isnan(a), None, a).tolist() if np.issubdtype(np.asarray(a).dtype, np.floating) \
        else np.asarray(a).tolist()


# ---------------------------------------------------------------- the channels' covariance


def common(x: np.ndarray) -> np.ndarray:
    """The sessions of the common history: those on which every channel's proxy has a return."""
    return ~np.isnan(x).any(axis=1)


def covariances(x: np.ndarray, ok: np.ndarray, memory: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The channels' mean, their same-session covariance Gamma_0, and their covariance of completed moves Omega, over
    the sessions `ok` of `x` (sessions by proxies): each lag a market session, a session outside `ok` contributing
    nothing, so Omega is the Bartlett-weighted sum of the biased autocovariances of the returns less their mean, each
    left out at none, and positive semi-definite."""
    n = int(ok.sum())
    mean = x[ok].mean(axis=0)
    c = np.where(ok[:, None], x - mean, 0.0)
    gamma0 = c.T @ c / n
    omega = gamma0.copy()
    for h in range(1, memory + 1):
        g = c[h:].T @ c[:-h] / n
        omega = omega + (1.0 - h / (memory + 1.0)) * (g + g.T)
    return mean, sym(gamma0), sym(omega)


def _quadratic(ok: np.ndarray, memory: int) -> np.ndarray:
    """The matrix M with which `covariances` estimates a variance over the sessions `ok`, x' M x / n: C W C, C the
    centring on the mean of those n sessions and W the Bartlett weights, 1 - h / (memory + 1) for two sessions h rows
    apart within the memory."""
    rows = np.flatnonzero(ok)
    gap = np.abs(rows[:, None] - rows[None, :])
    w = np.clip(1.0 - gap / (memory + 1.0), 0.0, None)
    return w - w.mean(axis=0)[None, :] - w.mean(axis=1)[:, None] + w.mean()


def centring(ok: np.ndarray, memory: int) -> float:
    """What share of a variance `covariances` keeps, in expectation, over the sessions `ok` of moves independent of
    one another: tr(M) / n (_quadratic). Centring on the mean of n sessions takes 1/n of each session's variance and
    makes every two covary by -1/n of it, so it keeps (1 - 1/n) - (2 / n^2) * sum over the lags h of
    (1 - h / (memory + 1)) times the pairs of sessions h apart: at no memory 1 - 1/n, Bessel's correction; at five,
    0.988 over 504 sessions, 0.58 over twelve, and 0.15 over three, where every window of six sessions holds nearly
    all of a series whose centred moves sum to none. None for fewer than two sessions, which say nothing of a variance
    once centred."""
    n = int(ok.sum())
    if n < 2:
        return 0.0
    return float(np.trace(_quadratic(ok, memory))) / n


def freedom(ok: np.ndarray, memory: int) -> float:
    """The degrees of freedom of what `covariances` estimates over the sessions `ok`, for moves independent of one
    another: tr(M)^2 / tr(M^2) (_quadratic), so that the estimate varies as a variance on that many does. n - 1 at no
    memory; at five, about a quarter of it over a full window, since six sessions' moves summed overlap, and nearly
    all of it over three sessions, where there is little left to overlap. None for fewer than two sessions."""
    if int(ok.sum()) < 2:
        return 0.0
    m = _quadratic(ok, memory)
    square = float(np.sum(m * m))
    return float(np.trace(m)) ** 2 / square if square > EPS else 0.0


def joined(before: float, total: np.ndarray, part: np.ndarray, memory: int, prior: float, lean: float,
           kappa: float, *, tol: float = 1e-10, max_iter: int = 200) -> dict:
    """The variance of completed moves of a total once a part joins it (MATH.md 3.2): `total`, the total's moves so far,
    and `part`, the part's, each a session's move, NaN where it has none; `before`, the total's variance over the
    window it has. The part moves with the total by `beta` and by its own variability beyond that, so the total
    becomes (1 + beta) times itself plus the part's own, its variance (1 + beta)^2 before + own.

    The part is fitted on the total as a series' response is fitted (fit_response), over the sessions both have and
    in their covariance of completed moves, taken as the sessions say it: `covariances` divided by what its centring
    keeps (`centring`), weighed by the degrees of freedom it has (`freedom`). Its co-movement leans toward none as a
    response does, `kappa` sessions of a series as variable as the market, `prior` being that variance on the part's
    dollars; its own risk leans `lean` sessions toward `prior`; the two are estimated in turn until neither moves,
    the risk counting what the co-movement's uncertainty takes of the residuals, so that a fit on few sessions is not
    read as closer than they show. The part adds its own risk and the uncertainty of its co-movement on the total:
    own = sigma^2 + v before, beta its co-movement's posterior mean, v its variance. A part with no session both
    have, or one, is its prior alone."""
    both = ~np.isnan(total) & ~np.isnan(part)
    n = int(both.sum())
    dof = freedom(both, memory)
    if dof <= EPS or prior <= EPS:
        return {"variance": before + prior, "beta": 0.0, "own": prior, "sessions": n}
    _, _, m = covariances(np.column_stack([total, part]), both, memory)
    m = m / centring(both, memory)
    a, c, d = float(m[0, 0]), float(m[0, 1]), float(m[1, 1])  # the total's variance, their covariance, the part's
    # the co-movement's prior, in the unit-information form at the total's variance over its window (`before`), as a
    # response's prior is at the channels' over theirs: kappa sessions of a series as variable as the market
    pinned = kappa * before / prior

    def fit(sigma2: float) -> tuple[float, float, float]:
        """beta, and the co-movement's variance on the total over these sessions and over its window: where the total
        neither moved over them nor has a variance, there is nothing to fit the part on, and nothing it multiplies."""
        precision = dof * a / sigma2 + pinned  # what the sessions say of the co-movement, and its prior
        if precision <= 0.0:
            return 0.0, 0.0, 0.0
        return (dof * c / sigma2) / precision, a / precision, before / precision

    sigma2 = prior
    for _ in range(max_iter):
        beta, on_sessions, _ = fit(sigma2)
        residual = d - 2.0 * beta * c + beta * beta * a  # what the part does beyond beta times the total
        new = (lean * prior + dof * (max(residual, 0.0) + on_sessions)) / (lean + dof)
        done = abs(new - sigma2) <= tol * max(sigma2, EPS)
        sigma2 = new
        if done:
            break
    beta, _, on_window = fit(sigma2)
    own = sigma2 + on_window
    return {"variance": (1.0 + beta) ** 2 * before + own, "beta": beta, "own": own, "sessions": n}


def residuals(x: np.ndarray, ok: np.ndarray, session: np.ndarray, spans: list[tuple[int, int, float]]) -> np.ndarray:
    """A holding's own part on each row of the channels' returns `x` (MATH.md 3.2): its return less what its session
    coefficients give, r - b0 . x_t - b1 . x_{t-1}, on each span (i, i + 1] of its returns (history.returns) that
    covers one session, row i, where that row and the one before are in the common history `ok`; none (NaN) on every
    other row. `session` is its fit's two rows of coefficients, a listing abroad's in dollars (its local response
    and its currency's). Its intercept is a constant the estimator's centring removes."""
    rows = x.shape[0]
    e = np.full(rows, np.nan)
    b = np.asarray(session, dtype=float)
    for i, j, r in spans:
        if j == i + 1 and 1 <= i < rows and ok[i] and ok[i - 1]:
            e[i] = r - b[0] @ x[i] - b[1] @ x[i - 1]
    return e


def figure(x: np.ndarray, ok: np.ndarray, omega: np.ndarray, holdings: dict, *, memory: int, lean: float,
           kappa: float) -> dict:
    """The portfolio's variance of completed moves per session (MATH.md 3.2), from the channels' returns `x` over the
    common history `ok`, their covariance of completed moves `omega`, and the holdings, each `(dollars, session, e)`:
    its dollars, its fit's session coefficients (2 by the channels), and its residuals on the rows (`residuals`).

    The channels' part is s' omega s, s the holdings' full responses in dollars. The holdings join the total, s . x_t,
    by the row their residuals begin, those beginning together as one part, the sum of their dollars times their
    residuals, each as `joined` takes it: its own variability leaning `lean` sessions toward the market's (each
    holding's own at the market's variance), its co-movement with those before it toward none as its fit's response
    leans, `kappa` sessions of a series as variable as the market; a holding with no residual row joins last, its
    prior alone. Every covariance of completed moves the figure reads, `omega`'s over the common history `ok` as each
    part's over its own sessions, is taken as the sessions say it, divided by what its centring keeps (`centring`):
    the figure reads a variance's level, where the fill and the orthogonalization read only ratios. The variance is the sum of three terms: the channels' part; the holdings' own parts taken together
    by the same rule with nothing under them; and what the two share, the rest. Returns them, the square root of the
    whole, and each part: the row it begins on (`start`, the number of rows where none), its holdings, its sessions,
    its co-movement with the total before it (`beta`, `with`), and its own variability beyond that (`own`)."""
    rows, k = x.shape
    s = np.zeros(k)
    for dollars, session, _ in holdings.values():
        s += dollars * np.asarray(session, dtype=float).sum(axis=0)
    omega = omega / centring(ok, memory)
    channels = float(s @ omega @ s)
    total = np.where(ok, np.nan_to_num(x) @ s, np.nan)  # the total's moves so far: the channels' part
    variance, owns, own_variance = channels, np.zeros(rows), 0.0
    starts = {h: (int(np.flatnonzero(~np.isnan(e))[0]) if (~np.isnan(e)).any() else rows)
              for h, (_, _, e) in holdings.items()}
    prior = float(omega[0, 0])  # the market's variance of completed moves, a session's worth of each holding's own
    parts = []
    for start in sorted(set(starts.values())):
        members = sorted(h for h in holdings if starts[h] == start)
        part = sum(holdings[h][0] * holdings[h][2] for h in members)
        weight = prior * sum(holdings[h][0] ** 2 for h in members)
        got = joined(variance, total, part, memory, weight, lean, kappa)
        parts.append({"start": start, "holdings": members, "sessions": got["sessions"], "beta": got["beta"],
                      "with": got["variance"] - variance - got["own"], "own": got["own"]})
        variance, total = got["variance"], total + part
        own_variance = joined(own_variance, owns, part, memory, weight, lean, kappa)["variance"]
        owns = owns + part
    return {"variance": {"channels": channels, "own": own_variance, "shared": variance - channels - own_variance},
            "daily_sd": float(np.sqrt(variance)), "parts": parts}


# ---------------------------------------------------------------- a series' response


@dataclass
class Prior:
    """A series' prior on its full response s, in the proxies' raw basis: its centre, its precision (zero where nothing
    pulls it), and what it rests on."""
    centre: np.ndarray
    precision: np.ndarray
    source: dict


def market_prior(omega: np.ndarray, market_variance: float, kappa: float) -> Prior:
    """The market's sensitivity, 1 on the first channel and 0 on the others, at kappa sessions of the unit-information
    form: what a series leans on where nothing else speaks for it."""
    centre = np.zeros(len(omega))
    centre[0] = 1.0
    return Prior(centre, kappa * omega / market_variance, {"from": "market"})


def no_response_prior(omega: np.ndarray, market_variance: float, kappa: float) -> Prior:
    """No response to any channel, at kappa sessions of the unit-information form: what a currency or crypto pair
    leans on, the market's sensitivity being what a listed share has, and a pair not being one."""
    return Prior(np.zeros(len(omega)), kappa * omega / market_variance, {"from": "none"})


def rows_prior(centre: np.ndarray, centre_cov: np.ndarray, omega: np.ndarray, market_variance: float,
               kappa_fund: float, *, shares: float = 0.0, kappa: float = 1.0, undetermined: float = 0.0,
               kappa_undetermined: float = 1.0) -> Prior:
    """A fund's prior from its rows: what its rows say its full response is, each row's response by the fund's exposure
    to it, summed (`centre`, the shares no series prices among them at the market's sensitivity), with the uncertainty
    of that sum: the responses' own (`centre_cov`); the listed share's prior, kappa sessions of the unit-information
    form, on the exposure to `shares` no series prices; and the exposure the rows leave `undetermined`, which says no
    response, only its uncertainty, kappa_undetermined sessions' worth on its square. The fund's spread about that sum
    is kappa_fund sessions of the same form. A strength of none on a part the rows have is no pull at all."""
    source = {"from": "rows", **({"undetermined": undetermined} if undetermined else {})}
    widths = [(1.0, kappa_fund), (shares * shares, kappa), (undetermined * undetermined, kappa_undetermined)]
    if any(weight and strength <= 0.0 for weight, strength in widths):
        return Prior(centre, np.zeros_like(omega), source)
    width = sum(weight / strength for weight, strength in widths if weight)  # in sessions' worth, inverted
    spread = sym(centre_cov) + market_variance * width * inv_pd(omega)
    if not np.all(np.isfinite(spread)):  # a response no history determines, among the rows: the sum pulls nothing
        return Prior(centre, np.zeros_like(omega), source)
    return Prior(centre, inv_pd(spread), source)


def fit_response(y: np.ndarray, spans: list[tuple[int, int]], x: np.ndarray, prior: Prior, *, gamma0: np.ndarray,
                 market_variance: float, lag_strength: float, omega: np.ndarray | None = None, max_iter: int = 200,
                 tol: float = 1e-12) -> dict:
    """A series' posterior: its returns `y`, each over its span (i, j] of closes of the window's sessions, the
    channels' returns on session t being row t - 1 of `x`, under `prior` on its full response and the lag's prior at
    `lag_strength` sessions; its own risk with one session's prior at the market's variance. Returns its intercept,
    full response s with its covariance, previous session's coefficient b1, session coefficients (b0, b1), own risk,
    how many returns it had, and how many directions of its coefficients they leave undetermined.

    A part whose prior has a strength of none (the intercept always, the full response where its prior pulls nothing,
    the lag where it is free) is determined by the returns alone, and they determine it only where they span it: a
    full response no prior pins, with fewer returns than one more than the channels, is not determined. There the fit
    is the limit of its prior's strength going to none: the returns' posterior in every direction they determine, and
    the prior's centre, at no certainty, in each direction they leave open, the directions measured in the form the
    prior's precision takes at any strength, `omega` (the unit-information form every prior here takes; `gamma0` where
    not given), and the lag's own, `gamma0`."""
    k = x.shape[1]
    lagged = not math.isinf(lag_strength)
    width = 1 + k * (2 if lagged else 1)
    rows = []
    for i, j in spans:
        x0 = x[i:j].sum(axis=0)  # sessions i + 1 .. j
        x1 = x[i - 1:j - 1].sum(axis=0)  # the session before each
        rows.append(np.concatenate([[j - i], x0] + ([x1 - x0] if lagged else [])))
    n = len(rows)
    centre = np.concatenate([[0.0], prior.centre] + ([np.zeros(k)] if lagged else []))
    if not n:  # no returns of its own: its prior's centre
        session = np.vstack([prior.centre, np.zeros(k)])
        cov = inv_pd(prior.precision) if np.all(np.linalg.eigvalsh(sym(prior.precision)) > EPS) else \
            np.full((k, k), np.inf)
        return {"alpha": 0.0, "full": prior.centre.copy(), "lag": np.zeros(k), "session": session, "cov": cov,
                "sigma2": market_variance, "n": 0}
    z = np.array(rows)
    y = np.asarray(y, dtype=float)
    w = 1.0 / z[:, 0]
    zz, zy = (z * w[:, None]).T @ z, (z * w[:, None]).T @ y
    precision = np.zeros((width, width))
    precision[1:1 + k, 1:1 + k] = prior.precision
    if lagged:
        precision[1 + k:, 1 + k:] = lag_strength * gamma0 / market_variance
    open_ = _open(z, precision, k, width)  # the directions no prior pins and the returns leave open, if any
    metric = np.zeros((width, width))  # how far from the prior's centre a direction lies, in the prior's own form
    metric[1:1 + k, 1:1 + k] = gamma0 if omega is None else omega
    if lagged:
        metric[1 + k:, 1 + k:] = gamma0

    def posterior(s2: float) -> tuple[np.ndarray, np.ndarray]:
        a, b = zz / s2 + precision, zy / s2 + precision @ centre
        if open_ is None:
            v = inv_pd(a)
            return v @ b, v
        # on the directions the returns determine, A's inverse there (A + QQ' is A's inverse plus QQ', A Q being
        # none); along the open ones, the solution nearest the prior's centre in its own form
        v = inv_pd(a + open_ @ open_.T) - open_ @ open_.T
        theta = v @ b
        theta -= open_ @ np.linalg.solve(open_.T @ metric @ open_, open_.T @ metric @ (theta - centre))
        return theta, v

    sigma2 = market_variance
    for _ in range(max_iter):
        theta, v = posterior(sigma2)
        e = y - z @ theta
        rss = float(np.sum(w * e * e)) + float(np.trace(zz @ v))  # what the residuals are given the coefficients
        new = (market_variance + rss) / (1.0 + n)
        done = abs(new - sigma2) <= tol * max(sigma2, EPS)
        sigma2 = new
        if done:
            break
    theta, v = posterior(sigma2)
    full = theta[1:1 + k]
    lag = theta[1 + k:] if lagged else np.zeros(k)
    undetermined = 0 if open_ is None else open_.shape[1]
    cov = np.full((k, k), np.inf) if open_ is not None and np.any(open_[1:1 + k]) else sym(v[1:1 + k, 1:1 + k])
    return {"alpha": float(theta[0]), "full": full, "lag": lag, "session": np.vstack([full - lag, lag]),
            "cov": cov, "sigma2": float(sigma2), "n": n, "undetermined": undetermined}


def _open(z: np.ndarray, precision: np.ndarray, k: int, width: int) -> np.ndarray | None:
    """The directions of a fit's coefficients that neither its priors nor its returns determine, as orthonormal
    columns, or None: the coefficients of each part whose prior has a strength of none (the intercept; the full
    response; the lag), wherever the returns' rows over them fall short of spanning them."""
    parts = [range(0, 1), range(1, 1 + k)] + ([range(1 + k, width)] if width > 1 + k else [])
    free = [i for part in parts if not np.any(precision[np.ix_(part, part)]) for i in part]
    if not free:
        return None
    rank = int(np.linalg.matrix_rank(z[:, free]))
    if rank == len(free):
        return None
    _, _, vt = np.linalg.svd(z[:, free], full_matrices=True)
    out = np.zeros((width, len(free) - rank))
    out[free] = vt[rank:].T
    return out


@dataclass
class Model:
    """The channels as the factors' Computation keeps them: `sessions`, the window's closes, the first the base; row t
    of `x` the proxies' returns on sessions[t + 1], none where a proxy has none; the common history's mean, Gamma_0 and
    Omega; Omega's LDL' in channel order; and the settings it was estimated at."""
    sessions: list[str]
    channels: list[str]
    x: np.ndarray
    mean: np.ndarray
    gamma0: np.ndarray
    omega: np.ndarray
    lower: np.ndarray
    d: np.ndarray
    settings: Settings

    @classmethod
    def load(cls, out: dict) -> "Model":
        k = len(out["channels"])
        return cls(out["sessions"], out["channels"], np.array(out["x"], dtype=float).reshape(-1, k),
                   np.array(out["mean"]), np.array(out["gamma0"]), np.array(out["omega"]), np.array(out["lower"]),
                   np.array(out["d"]), Settings(**out["settings"]))

    @property
    def k(self) -> int:
        return len(self.channels)

    @property
    def ok(self) -> np.ndarray:
        return common(self.x)

    @property
    def market_variance(self) -> float:
        return float(self.gamma0[0, 0])

    def market_prior(self) -> Prior:
        return market_prior(self.omega, self.market_variance, self.settings.kappa)

    def no_response_prior(self) -> Prior:
        return no_response_prior(self.omega, self.market_variance, self.settings.kappa)

    def rows_prior(self, centre: np.ndarray, centre_cov: np.ndarray, *, shares: float = 0.0,
                   undetermined: float = 0.0) -> Prior:
        s = self.settings
        return rows_prior(centre, centre_cov, self.omega, self.market_variance, s.kappa_fund, shares=shares,
                          kappa=s.kappa, undetermined=undetermined, kappa_undetermined=s.kappa_undetermined)

    def rows(self, found: list[tuple[int, int, float]]) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """A series' returns over spans (i, j] of the sessions that the fit reads: those whose sessions, and the
        session before each, are all in the common history."""
        ok = self.ok
        kept = [(i, j, r) for i, j, r in found if i >= 1 and ok[i - 1:j].all()]
        return np.array([r for _, _, r in kept], dtype=float), [(i, j) for i, j, _ in kept]

    def fit(self, found: list[tuple[int, int, float]], prior: Prior, *, early: bool = False) -> dict:
        """A series' fit on the common history, its lag at the strength for a series whose closes are New York's, or
        `early`, struck before it."""
        y, spans = self.rows(found)
        lag = self.settings.lag_strength_early if early else self.settings.lag_strength
        return fit_response(y, spans, self.x, prior, gamma0=self.gamma0, market_variance=self.market_variance,
                            lag_strength=lag, omega=self.omega)

    def orthogonal(self, full: np.ndarray) -> np.ndarray:
        """A full response on the proxies' raw returns as one on the orthogonalized channels: L' s, since the proxies'
        returns are L times the channels' own parts."""
        return self.lower.T @ full

    def expected(self) -> np.ndarray:
        """Each session's channel returns, where a proxy has none, at its expectation given the returns the session
        has, by the same-session covariance over the common history: what a series rebuilt on a session before a
        young proxy's history moves with. The first channel's proxy, whose closes are the sessions, has every one."""
        out = self.x.copy()
        for t in np.flatnonzero(np.isnan(out).any(axis=1)):
            miss = np.isnan(out[t])
            have = ~miss
            given = np.linalg.solve(self.gamma0[np.ix_(have, have)], out[t, have] - self.mean[have])
            out[t, miss] = self.mean[miss] + self.gamma0[np.ix_(miss, have)] @ given
        return out
