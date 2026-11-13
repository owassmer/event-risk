"""The Treasury's curve read as the price today of a dollar paid on a later date (MATH.md 1.6, 3.1): what a payment a world
makes on a date is worth today, what the money in a market's contract forgoes until its close, and the rate an option's
delta is worked out at.

`curve` is one day's Treasury par yield curve as the store keeps it (harness.venues.treasury, `Treasury.yield_curve`):
`[[years, yield], ...]`, each tenor in years, a month as n/12, and each yield a decimal, with whichever tenors the
Treasury quoted that day. Each point is a bond-equivalent par yield: the annual coupon, paid half-yearly, of a security
with that time to maturity that the market prices at par. The Treasury's Interest Rates - Frequently Asked Questions
(https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions):
"the yields at any point on the par yield curve are consistent with a semiannual coupon security with that amount of
time remaining to maturity", on actual days in a year of 365 or 366; and "Treasury does not create or publish daily
zero-coupon curve rates". Its methodology fits the curve to the most recently auctioned bills, notes and bonds, the
bills entering at their bond-equivalent yields
(https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology).

So a dollar paid within half a year, with no coupon before it, is discounted as a bill is: `1 / (1 + y t)`. Beyond half a
year the par yields are read on a grid of half-years, linear between the tenors the day quotes and flat beyond them, and
the discount factors are the ones under which each half-year's par security is worth par:
`(y_T / 2) * sum over k = 1 .. 2T of P(k / 2) + P(T) = 1`. Between two points of the grid the logarithm of the discount
factor is linear: the rate forward over each half-year is constant. At half a year both give `1 / (1 + y / 2)`, which is
MATH.md 1.6's `35.00 / 1.02` at 4%.

Years are of 365 days, as harness.derive.options counts an option's life.
"""

from __future__ import annotations

import math

import numpy as np


def _par(curve: list, years: float) -> float:
    """The par yield at `years`: linear between the tenors quoted, flat beyond them."""
    points = sorted((float(t), float(y)) for t, y in curve)
    if not points:
        raise ValueError("a yield curve quotes at least one tenor")
    return float(np.interp(years, [t for t, _ in points], [y for _, y in points]))


def _grid(curve: list, n: int) -> list[float]:
    """The discount factors at half a year, a year, ... n half-years, each half-year's par security worth par."""
    out: list[float] = []
    paid = 0.0  # the sum of the discount factors of the coupons before the last
    for k in range(1, n + 1):
        half = _par(curve, k / 2) / 2
        p = (1.0 - half * paid) / (1.0 + half)
        out.append(p)
        paid += p
    return out


def discount(curve: list, years: float, basis: str = "par") -> float:
    """The price today of a unit of a currency paid `years` from now, at its government's curve: one where it is paid
    now. A curve of par yields (`basis` "par", the Treasury's, and every curve whose source states bond yields) is read
    as above; one of spot rates (`basis` "spot", continuously compounded, as the ECB states the euro's;
    harness.venues.curves) as `exp(-z t)`, `z` linear between the tenors given and flat beyond them."""
    if years <= 0.0:
        return 1.0
    if basis == "spot":
        return math.exp(-_par(curve, years) * years)
    if basis != "par":
        raise ValueError(f"a yield curve's basis is par or spot, not {basis}")
    if years <= 0.5:
        return 1.0 / (1.0 + _par(curve, years) * years)
    n = math.ceil(2.0 * years)
    grid = _grid(curve, n)
    w = 2.0 * years - (n - 1)  # how far through its half-year the date falls
    return math.exp((1.0 - w) * math.log(grid[n - 2]) + w * math.log(grid[n - 1]))


def rate(curve: list, years: float, basis: str = "par") -> float:
    """The continuously compounded rate to `years`, `-ln(discount) / years`; for no time at all, its limit there, the
    shortest point of the curve."""
    if years <= 0.0:
        return _par(curve, 0.0)
    return -math.log(discount(curve, years, basis)) / years
