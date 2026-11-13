"""An option's delta where its market quotes none (MATH.md 3.1): the delta its contract terms, its price then, and its
underlying's price then give. One calculation serves a listed contract quoted no delta, a contract no market lists
(FLEX), and any contract at a past as-of; the rows (harness.derive.rows) call it, and book the row's exposure from it as
from a quoted delta. The position's direction stays out of it: this is the delta of one contract's unit of the
underlying, held.

The method is the one quoted deltas are made by. Massive's greeks (https://massive.com/blog/greeks-and-implied-volatility)
invert a pricing model at the option's price, the bid and ask midpoint, for its implied volatility, by Brent's method,
and read the delta there, with a continuous dividend yield from the payouts of the year before and a risk-free rate.
Here the model fits the contract's exercise terms: a European contract's price and delta are Black, Scholes and
Merton's closed forms (Hull, Options, Futures, and Other Derivatives, ch. 17); an American contract's, which may be
exercised early, Cox, Ross and Rubinstein's binomial tree (J. Financial Economics 7, 1979), as Massive's own, its delta
the first step's (Hull, ch. 21), the trees of STEPS and STEPS + 1 steps averaged, which cancels the tree's alternation
between odd and even counts.

The bounds. An American contract priced at or below its exercise payoff, what exercising it now pays, is worth that
payoff: its delta is +1 for a call and -1 for a put (MATH.md 3.1), whatever a model would say. Beyond that, no volatility
makes a contract worth less than it is worth at none, its underlying growing at the rate less its dividend yield: an
American contract the best of exercising at any moment to expiry, a European one its payoff at expiry discounted; a
price at or below that takes that value's delta, e^(-qu) or -e^(-qu) for the moment u it is best exercised at, none for
a contract out of the money. A price above what any volatility gives contradicts its terms, and gives no delta, as a
missing term does: more than the underlying for an American call (its dividend-discounted price for a European one), or
more than the strike for an American put (discounted at the rate for a European one). Between those and what the
highest volatility searched (VOLATILITY) gives, a contract takes the delta at that volatility: a deep in-the-money
contract's value climbs from its value at none toward that ceiling so slowly that a mark a few cents off its model,
struck at another moment or on another dividend assumption, reads as a volatility of thousands of percent, while its
delta is near e^(-qu) at every volatility (FNOV's call at strike 6.59, 99% of the fund, marked at 746.04 against 745.97
at a volatility of ten, read on ETF Global's holdings of 2026-11-09).

The rate is the continuously compounded rate to the option's remaining life that the Treasury's par yield curve then
gives (harness.derive.rates, which reads the curve as the price today of a dollar paid on each date). The dividend yield is what the payer paid over the 365 days to the
moment over its price then: the company an option is on, or, for an index, the fund whose holdings are its companies,
since the index's level falls as they pay. The remaining life runs to 16:00 in New York on the expiry date, in years of
365 days.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np

from harness.derive import rates

STEPS = 400  # the tree's steps, and one more, averaged: its delta stood within 1.5e-4 of a 4000-step tree on cases tried
VOLATILITY = (1e-4, 10.0)  # the implied volatilities searched: from none to a thousand percent a year
NY = ZoneInfo("America/New_York")
YEAR = 365.0 * 86400.0


def upper_value(*, kind, style, strike, years, rate, dividend_yield, underlying):
    """The infinite-volatility price limit, including negative rates or dividend carry."""
    discount, carry = math.exp(-rate * years), math.exp(-dividend_yield * years)
    if kind == "call":
        return underlying * (max(1.0, carry) if style == "american" else carry)
    return (np.zeros_like(underlying) + strike) * (max(1.0, discount) if style == "american" else discount)


def life(expiry: str, at: str) -> float:
    """The years from `at` to 16:00 in New York on the expiry date, none where that has passed."""
    end = datetime.combine(datetime.fromisoformat(expiry).date(), time(16, 0), NY)
    start = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=NY)
    return max((end - start).total_seconds() / YEAR, 0.0)


def _n(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def european(call: bool, s: float, k: float, t: float, r: float, q: float, vol: float) -> tuple[float, float]:
    """A European contract's price and delta (Black, Scholes and Merton, with a continuous dividend yield)."""
    if t <= 0.0 or vol <= 0.0 or s == 0.0 or k == 0.0:
        return _zero(call, False, s, k, t, r, q)
    root = vol * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + vol * vol / 2.0) * t) / root
    d2 = d1 - root
    fwd, disc = s * math.exp(-q * t), k * math.exp(-r * t)
    if call:
        return fwd * _n(d1) - disc * _n(d2), math.exp(-q * t) * _n(d1)
    return disc * _n(-d2) - fwd * _n(-d1), -math.exp(-q * t) * _n(-d1)


def _tree(call: bool, s: float, k: float, t: float, r: float, q: float, vol: float, steps: int) -> tuple[float, float]:
    dt = t / steps
    u = math.exp(vol * math.sqrt(dt))
    d = 1.0 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0.0 < p < 1.0:
        raise ValueError("the tree's step is too coarse for this volatility")
    disc = math.exp(-r * dt)
    sign = 1.0 if call else -1.0
    j = np.arange(steps + 1)
    prices = s * u ** (steps - 2 * j)  # the underlying at each node of the last step, highest first
    values = np.maximum(sign * (prices - k), 0.0)
    for n in range(steps - 1, 0, -1):
        prices = prices[1:] * u  # the step before: each node one down-move fewer
        values = np.maximum(disc * (p * values[:-1] + (1.0 - p) * values[1:]), sign * (prices - k))
    up, down = values[0], values[1]  # the first step's two nodes
    price = max(disc * (p * up + (1.0 - p) * down), sign * (s - k))
    return float(price), float((up - down) / (s * u - s * d))


def american(call: bool, s: float, k: float, t: float, r: float, q: float, vol: float) -> tuple[float, float]:
    """An American contract's price and delta: Cox, Ross and Rubinstein's tree of STEPS steps and of one more,
    averaged."""
    if t <= 0.0 or vol <= 0.0 or s == 0.0 or k == 0.0:
        return _zero(call, True, s, k, t, r, q)
    a, b = _tree(call, s, k, t, r, q, vol, STEPS), _tree(call, s, k, t, r, q, vol, STEPS + 1)
    return (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0


def _zero(call: bool, early: bool, s: float, k: float, t: float, r: float, q: float) -> tuple[float, float]:
    """What a contract is worth, and its delta, at no volatility: its underlying grows at r - q, so it is worth the
    best of e^(-r u) times its payoff at u over the moments u it may be exercised, every moment to expiry for an
    American contract, expiry for a European one. The payoff's discounted value, S e^(-q u) - K e^(-r u) for a call,
    has at most one turning point, so the best is at now, at expiry, or there."""
    sign = 1.0 if call else -1.0
    if t <= 0.0:
        payoff = max(sign * (s - k), 0.0)
        return payoff, sign if payoff > 0.0 else 0.0
    moments = [t]
    if early:
        moments.append(0.0)
        if r * q > 0.0 and s > 0.0 and k > 0.0 and r != q:  # where q S e^(-q u) = r K e^(-r u)
            u = math.log(q * s / (r * k)) / (q - r)
            if 0.0 < u < t:
                moments.append(u)
    best, delta = 0.0, 0.0
    for u in moments:
        worth = sign * (s * math.exp(-q * u) - k * math.exp(-r * u))
        if worth > best:
            best, delta = worth, sign * math.exp(-q * u)
    return best, delta


def _brent(f, a: float, b: float, fa: float, fb: float, tol: float = 1e-10, iterations: int = 200) -> float:
    """The root of f between a and b, where f(a) and f(b) differ in sign: Brent's method (Brent, Algorithms for
    Minimization without Derivatives, 1973, ch. 4), bisection safeguarding inverse quadratic and secant steps."""
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa
    c, fc, bisected, d = a, fa, True, 0.0
    for _ in range(iterations):
        if fb == 0.0 or abs(b - a) < tol:
            return b
        if fa != fc and fb != fc:
            s = (a * fb * fc / ((fa - fb) * (fa - fc)) + b * fa * fc / ((fb - fa) * (fb - fc))
                 + c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            s = b - fb * (b - a) / (fb - fa)
        if (not ((3.0 * a + b) / 4.0 < s < b or b < s < (3.0 * a + b) / 4.0)
                or (bisected and abs(s - b) >= abs(b - c) / 2.0)
                or (not bisected and abs(s - b) >= abs(c - d) / 2.0)
                or (bisected and abs(b - c) < tol) or (not bisected and abs(c - d) < tol)):
            s, bisected = (a + b) / 2.0, True
        else:
            bisected = False
        fs = f(s)
        d, c, fc = c, b, fb
        if fa * fs < 0.0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa
    return b


def _lowest(early: bool, rate: float, dividend_yield: float, years: float) -> float:
    """The lowest volatility searched: VOLATILITY's, or, for the tree, the least at which each step's spread exceeds the
    drift, d < e^((r - q) dt) < u."""
    return max(VOLATILITY[0], 2.0 * abs(rate - dividend_yield) * math.sqrt(years / STEPS)) if early else VOLATILITY[0]


def _implied(worth: Callable[[float], float], target: float, low: float, tolerance: float) -> tuple[float, str | None]:
    """The one search for a contract's volatility, which its delta (one world, today's, its quoted price the target) and
    its worlds' values (its worlds at their weights, its value today the target) both make: the volatility at which
    `worth`, its value at a volatility averaged over its worlds, comes to `target`, by Brent's method over VOLATILITY
    from `low`; with its bound where none does: "no volatility" where even none gives at least the target, "highest
    volatility" where the highest searched gives less, "lowest volatility" where the lowest a tree admits already
    gives more by more than `tolerance`."""
    if worth(0.0) >= target:
        return 0.0, "no volatility"
    top = worth(VOLATILITY[1]) - target
    if top < 0.0:
        return VOLATILITY[1], "highest volatility"
    bottom = worth(low) - target
    if bottom >= 0.0:  # a numerical boundary is not an exact calibration: its residual is kept by the caller
        return low, "lowest volatility" if bottom > tolerance else None
    return _brent(lambda v: worth(v) - target, low, VOLATILITY[1], bottom, top), None


def delta(*, right: str | None, style: str | None, strike: float | None, expiry: str | None, at: str | None,
          price: float | None, underlying_price: float | None, rate: list | None, dividend_yield: dict | None
          ) -> dict | None:
    """The delta of a contract held, per unit of its underlying, that its terms (`right`, "call" or "put"; `style`,
    "american" or "european"; `strike`; `expiry`, a date), its `price` and its underlying's (each per unit of the
    underlying, at the moment `at`), the Treasury's par yield curve then (`rate`, as harness.derive.rates reads it) and its
    payer's dividends (`dividend_yield`: `paid` over the 365 days to `at`, at its `price` then) give: `delta`; the
    implied `volatility`, none at a bound; the `model`; and `bound`, "exercise" where the contract is worth its
    exercise payoff (an American one at or below what exercising it now pays, any at expiry), "no volatility" where its
    price is at or below what it is worth at none, "highest volatility" where it is above what the highest volatility
    searched gives and within its terms. None where a term or a price is missing, or the price is above what any
    volatility gives."""
    if right not in ("call", "put") or style not in ("american", "european") or not expiry or not at:
        return None
    if not all(isinstance(x, (int, float)) for x in (strike, price, underlying_price)) or not rate \
            or not isinstance(dividend_yield, dict):
        return None
    s, k, quoted = float(underlying_price), float(strike), float(price)
    paid, payer = dividend_yield.get("paid"), dividend_yield.get("price")
    if s <= 0.0 or k < 0.0 or quoted < 0.0 or not isinstance(paid, (int, float)) \
            or not isinstance(payer, (int, float)) or payer <= 0.0:
        return None
    call, early = right == "call", style == "american"
    t = life(expiry, at)
    r, q = rates.rate(rate, t), float(paid) / float(payer)
    model = american if early else european
    name = "binomial" if early else "black-scholes-merton"
    found = {"model": name, "rate": r, "yield": q, "years": t}
    payoff = (s - k) if call else (k - s)  # what exercising it now pays
    if (t <= 0.0 or early) and payoff > 0.0 and quoted <= payoff:  # worth its exercise payoff
        return {"delta": 1.0 if call else -1.0, "volatility": 0.0, "bound": "exercise", **found}
    if t <= 0.0:  # expiring out of the money
        return {"delta": 0.0, "volatility": 0.0, "bound": "exercise", **found}
    if quoted > upper_value(kind=right, style=style, strike=k, years=t, rate=r, dividend_yield=q, underlying=s):
        return None  # dearer than any volatility makes it: the price contradicts its terms
    vol, bound = _implied(lambda v: model(call, s, k, t, r, q, v)[0], quoted, _lowest(early, r, q, t),
                          1e-9 * max(1.0, quoted))
    return {"delta": model(call, s, k, t, r, q, vol)[1], "volatility": vol, "bound": bound, **found}


def _terms(kind: str, style: str, strike: float, years: float, rate: float, dividend_yield: float) -> None:
    """Validate the pure valuation's terms without supplying a missing term by assumption."""
    if kind not in ("call", "put") or style not in ("european", "american"):
        raise ValueError("an option states its right, call or put, and its exercise style, european or american")
    for name, x in (("strike", strike), ("years", years), ("rate", rate), ("dividend_yield", dividend_yield)):
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError(f"an option's {name} must be a finite number")
    if strike < 0.0 or years < 0.0:
        raise ValueError("an option's strike and remaining life cannot be negative")


def value(*, kind: str, style: str, strike: float, years: float, rate: float,
          dividend_yield: float, underlying: float, volatility: float) -> float:
    """The value of one underlying unit of a contract at the supplied valuation date.

    This is the same pricing calculation as ``delta`` and ``worlds_value``. At zero remaining life it is the
    exercise payoff, with no time value or later holding. The caller values an expired contract at its *expiry's*
    underlying level and books that payment once, discounted from its payment date; a later underlying level is
    not an expiry price. A live contract's value is in dollars at the valuation date, not discounted to today.
    """
    _terms(kind, style, strike, years, rate, dividend_yield)
    for name, x in (("underlying", underlying), ("volatility", volatility)):
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0.0:
            raise ValueError(f"an option's {name} must be a finite nonnegative number")
    model = american if style == "american" else european
    return model(kind == "call", underlying, strike, years, rate, dividend_yield, volatility)[0]


def worlds_value(*, kind: str, style: str, strike: float, years: float, rate: float,
                 dividend_yield: float, underlying_today: float, value_today: float,
                 levels: Mapping[str, float], weights: Mapping[str, float]) -> dict:
    """One contract's unit valued in every world (MATH.md 1.6): at its underlying's level there (`levels`, keyed by
    world), by the model that gives its delta (european or american, at `rate` and `dividend_yield` for `years` of
    life left today), at the one volatility at which its worlds, at the weights today's prices carry (`weights`, not
    the probabilities), average to its value today. Each contract is valued at its own volatility.

    It returns `values`, each world's value of one unit, unsigned (the caller applies the signed units once);
    `volatility`; `bound`; `rate`, `yield`, `years` and `model`; `calibrated`, whether the worlds average to
    `value_today`; and `pricing_gap`, by how much they do not. The bounds: "no volatility" where even none leaves
    the worlds above its value today, valued there, the gap its expected value (MATH.md 1.1); "highest volatility"
    where the highest searched leaves them below, valued there; "lowest volatility" where the lowest an American
    tree admits is already above; and "exercise" at no life left, each world its payoff. A value today above what
    the contract's terms allow at any volatility raises ValueError, as a missing or ill-formed term does. The search is
    the one its delta makes (_implied).

    At a draw at the horizon's end (MATH.md 1.8) the same contract is valued by `value` at this volatility, with the
    life it has left there; a contract that has expired by then is its payoff, paid once.
    """
    _terms(kind, style, strike, years, rate, dividend_yield)
    for name, x in (("underlying_today", underlying_today), ("value_today", value_today)):
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0.0:
            raise ValueError(f"an option's {name} must be a finite nonnegative number")
    if not isinstance(levels, Mapping) or not levels or not isinstance(weights, Mapping) or levels.keys() != weights.keys():
        raise ValueError("an option's world levels and pricing weights must name the same nonempty set of worlds")
    for world in levels:
        for name, x in (("level", levels[world]), ("pricing weight", weights[world])):
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0.0:
                raise ValueError(f"{world}'s {name} must be a finite nonnegative number")
    total = math.fsum(weights.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-10):
        raise ValueError("an option's pricing weights must sum to one")
    # Remove only floating-point summation error, never complete or change supplied weights here.
    q = {world: weight / total for world, weight in weights.items()}
    call, early = kind == "call", style == "american"
    model = american if early else european
    name = "binomial" if early else "black-scholes-merton"
    tolerance = 1e-9 * max(1.0, value_today)

    def at(volatility: float) -> dict[str, float]:
        return {world: model(call, level, strike, years, rate, dividend_yield, volatility)[0]
                for world, level in levels.items()}

    def mean(values: Mapping[str, float]) -> float:
        return math.fsum(q[world] * values[world] for world in levels)

    def result(volatility: float, bound: str | None, values: dict[str, float]) -> dict:
        gap = mean(values) - value_today
        return {"volatility": volatility, "bound": bound, "values": values, "rate": rate,
                "yield": dividend_yield, "years": years, "model": name,
                "calibrated": abs(gap) <= tolerance, "pricing_gap": gap}

    if years == 0.0:
        return result(0.0, "exercise", at(0.0))
    ceiling = upper_value(kind=kind, style=style, strike=strike, years=years, rate=rate,
                          dividend_yield=dividend_yield, underlying=underlying_today)
    if value_today > ceiling + tolerance:
        raise ValueError("the option's price is above what its terms allow at any volatility")

    volatility, bound = _implied(lambda v: mean(at(v)), value_today, _lowest(early, rate, dividend_yield, years),
                                 tolerance)
    return result(volatility, bound, at(volatility))
