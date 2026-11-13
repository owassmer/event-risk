"""The Treasury's par curve read as the price today of a dollar paid later (harness.derive.rates), on figures worked by
hand: a bill's bond-equivalent yield within half a year, par securities paying half-yearly beyond it."""

from __future__ import annotations

import math

import pytest

from harness.derive.rates import discount, rate

FLAT = [[1 / 12, 0.04], [0.5, 0.04], [1.0, 0.04], [2.0, 0.04], [10.0, 0.04], [30.0, 0.04]]
# One day's curve as the Treasury quotes it, a decimal at each tenor in years; the 20-year left out, as on a day it was
# not quoted.
DAY = [[1 / 12, 0.0431], [2 / 12, 0.0428], [3 / 12, 0.0425], [4 / 12, 0.0421], [0.5, 0.0412], [1.0, 0.0398],
       [2.0, 0.0381], [3.0, 0.0377], [5.0, 0.0383], [7.0, 0.0397], [10.0, 0.0415], [30.0, 0.0461]]


def par(curve, years):
    """The par yield the curve quotes at `years`, linear between tenors, as the bootstrap reads it."""
    pts = sorted(curve)
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if t0 <= years <= t1:
            return y0 + (y1 - y0) * (years - t0) / (t1 - t0)
    return pts[0][1] if years < pts[0][0] else pts[-1][1]


def test_a_payment_in_six_months_at_four_percent_is_spec_1_6s_illustration():
    """$35.00 paid in six months, the Treasury's six-month yield at 4%: 35.00 / 1.02 = $34.31 today (MATH.md 1.6)."""
    assert 35.00 * discount(FLAT, 0.5) == pytest.approx(35.00 / 1.02, abs=1e-12)
    assert round(35.00 * discount(FLAT, 0.5), 2) == 34.31


def test_within_half_a_year_a_dollar_is_discounted_as_a_bill_is_at_the_interpolated_yield():
    """A quarter of a year between the one-month bill at 3.6% and the six-month at 4.2%: a yield of
    3.6% + 0.4 x 0.6% = 3.84%, and a price of 1 / (1 + 0.0384 x 0.25)."""
    curve = [[1 / 12, 0.036], [0.5, 0.042], [2.0, 0.04]]
    assert discount(curve, 0.25) == pytest.approx(1 / (1 + 0.0384 * 0.25), abs=1e-14)
    assert discount(curve, 1 / 365) == pytest.approx(1 / (1 + 0.036 / 365), abs=1e-14)  # before the shortest: flat
    assert discount(curve, 0.0) == 1.0 and discount(curve, -1.0) == 1.0


def test_beyond_half_a_year_the_par_securities_are_bootstrapped_by_hand():
    """Six months at 4% and a year at 5%: P(0.5) = 1 / 1.02; the year's par security pays 2.5 cents at six months and
    1.025 at a year, so P(1) = (1 - 0.025 / 1.02) / 1.025 = 0.951697752...; nine months is their geometric mean."""
    curve = [[0.5, 0.04], [1.0, 0.05]]
    p_half, p_year = 1 / 1.02, (1 - 0.025 / 1.02) / 1.025
    assert discount(curve, 1.0) == pytest.approx(p_year, abs=1e-15) and p_year == pytest.approx(0.951697752, abs=1e-9)
    assert discount(curve, 0.75) == pytest.approx(math.sqrt(p_half * p_year), abs=1e-15)
    # Eighteen months, the year's 5% held flat beyond it: P(1.5) = (1 - 0.025 (P(0.5) + P(1))) / 1.025.
    assert discount(curve, 1.5) == pytest.approx((1 - 0.025 * (p_half + p_year)) / 1.025, abs=1e-15)


def test_on_a_flat_curve_a_dollar_is_discounted_at_the_yield_compounded_half_yearly():
    """Where every par yield is y, every half-year's forward is y too: P(T) = (1 + y / 2) ^ (-2T)."""
    for years in (0.5, 1.0, 2.0, 7.5, 10.0, 30.0):
        assert discount(FLAT, years) == pytest.approx(1.02 ** (-2 * years), rel=1e-12)
    assert rate(FLAT, 3.0) == pytest.approx(2 * math.log(1.02), rel=1e-12)


def test_each_half_years_par_security_is_worth_par_on_a_day_with_a_tenor_missing():
    """On a day's curve, the twenty-year left unquoted: at every half-year to thirty years, the security paying the
    par yield quoted there, half each half-year, is worth exactly par at the discount factors."""
    for n in range(1, 61):
        t, y = n / 2, par(DAY, n / 2)
        worth = sum(y / 2 * discount(DAY, k / 2) for k in range(1, n + 1)) + discount(DAY, t)
        assert worth == pytest.approx(1.0, abs=1e-12), t


def test_the_rate_is_continuous_and_at_no_time_it_is_the_shortest_yield():
    assert rate(DAY, 0.25) == pytest.approx(math.log(1 + par(DAY, 0.25) * 0.25) / 0.25, rel=1e-12)
    assert rate(DAY, 1e-9) == pytest.approx(0.0431, rel=1e-6) and rate(DAY, 0.0) == 0.0431
    assert rate(DAY, 2.0) == pytest.approx(-math.log(discount(DAY, 2.0)) / 2.0, rel=1e-15)
