"""An option's delta from its terms and prices where its market quotes none (MATH.md 3.1; harness.derive.options), on
textbook values: Hull's Options, Futures, and Other Derivatives, the European example of ch. 17 and the American put of
ch. 21; and the bounds, an option worth its exercise payoff at +1 or -1."""

from __future__ import annotations

import math

import pytest

from harness.derive import options, rates

FLAT = [[0.0833, 0.0], [30.0, 0.0]]  # no interest: a curve at none
NONE_PAID = {"paid": 0.0, "price": 1.0}


def test_a_european_call_and_put_at_hulls_example():
    """S 42, K 40, r 10%, volatility 20%, half a year: the call 4.76 and the put 0.81, the call's delta N(d1) 0.7791."""
    call, put = options.european(True, 42, 40, 0.5, 0.1, 0.0, 0.2), options.european(False, 42, 40, 0.5, 0.1, 0.0, 0.2)
    assert call[0] == pytest.approx(4.76, abs=0.005) and put[0] == pytest.approx(0.81, abs=0.005)
    assert call[1] == pytest.approx(0.7791, abs=1e-4) and put[1] == pytest.approx(call[1] - 1.0, abs=1e-12)


def test_an_american_put_at_hulls_tree():
    """S 50, K 50, r 10%, volatility 40%, five months: Hull gives 4.28 for the American put on a fine tree."""
    price, delta = options.american(False, 50, 50, 5 / 12, 0.1, 0.0, 0.4)
    assert price == pytest.approx(4.28, abs=0.005) and -0.5 < delta < -0.35


def test_the_delta_is_read_at_the_volatility_its_price_implies():
    """A price made at 30% gives back 30%, and the delta there, for each style."""
    at, expiry = "2026-11-10T21:00:00+00:00", "2027-05-21"
    t = options.life(expiry, at)
    r = rates.rate([[0.5, 0.04], [1.0, 0.04]], t)
    for style, model in (("european", options.european), ("american", options.american)):
        for call in (True, False):
            price, delta = model(call, 100.0, 105.0, t, r, 0.02, 0.3)
            got = options.delta(right="call" if call else "put", style=style, strike=105.0, expiry=expiry, at=at,
                                price=price, underlying_price=100.0, rate=[[0.5, 0.04], [1.0, 0.04]],
                                dividend_yield={"paid": 2.0, "price": 100.0})
            assert got["volatility"] == pytest.approx(0.3, abs=1e-6) and got["delta"] == pytest.approx(delta, abs=1e-6)
            assert got["bound"] is None


def test_an_american_option_worth_its_exercise_payoff_is_one_signed_by_its_right():
    """A call struck at 40 on a share at 121.56, quoted at 81.175, below the 81.56 that exercising it pays: +1; a put
    at or below what it pays: -1."""
    common = {"style": "american", "expiry": "2027-01-15", "at": "2026-11-10T21:00:00+00:00", "rate": FLAT,
              "dividend_yield": NONE_PAID}
    call = options.delta(right="call", strike=40.0, price=81.175, underlying_price=121.56, **common)
    put = options.delta(right="put", strike=150.0, price=28.0, underlying_price=121.56, **common)
    assert (call["delta"], call["bound"]) == (1.0, "exercise") and (put["delta"], put["bound"]) == (-1.0, "exercise")


def test_a_european_call_at_its_floor_takes_the_floors_delta_and_a_price_no_volatility_gives_none():
    """A European call on a payer of 2% a year, priced at no more than S e^(-qT) - K e^(-rT): its delta e^(-qT). A call
    priced above its underlying, and a contract whose style no source states, give none."""
    at, expiry = "2026-11-10T21:00:00+00:00", "2027-11-10"
    t = options.life(expiry, at)
    floor = 100.0 * math.exp(-0.02 * t) - 50.0
    got = options.delta(right="call", style="european", strike=50.0, expiry=expiry, at=at, price=floor,
                        underlying_price=100.0, rate=FLAT, dividend_yield={"paid": 2.0, "price": 100.0})
    assert got["bound"] == "no volatility" and got["delta"] == pytest.approx(math.exp(-0.02 * t), abs=1e-12)
    assert options.delta(right="call", style="european", strike=50.0, expiry=expiry, at=at, price=101.0,
                         underlying_price=100.0, rate=FLAT, dividend_yield=NONE_PAID) is None
    assert options.delta(right="call", style=None, strike=50.0, expiry=expiry, at=at, price=55.0,
                         underlying_price=100.0, rate=FLAT, dividend_yield=NONE_PAID) is None


@pytest.mark.parametrize("style", ["european", "american"])
def test_a_deep_call_marked_above_what_the_highest_volatility_gives_but_within_its_terms_takes_the_delta_there(style):
    """A call at strike 6.59 on an underlying at 752.69 with eleven days to run, marked at 746.04: a few cents above
    its value at a volatility of ten (a thousand percent a year), far below its ceiling, the underlying discounted at its
    dividend yield. Its price does not contradict its terms, so it has a delta, near e^(-qT), as at every volatility. A
    mark above the ceiling still gives none (FNOV's call, read live)."""
    at, expiry = "2026-11-09T21:00:00+00:00", "2026-11-20"
    t, q = options.life(expiry, at), 0.012
    kw = dict(right="call", style=style, strike=6.59, expiry=expiry, at=at, underlying_price=752.69,
              rate=[[1 / 12, 0.039], [30.0, 0.043]], dividend_yield={"paid": q * 752.69, "price": 752.69})
    top = (options.american if style == "american" else options.european)(
        True, 752.69, 6.59, t, rates.rate(kw["rate"], t), q, options.VOLATILITY[1])[0]
    got = options.delta(price=top + 0.07, **kw)
    assert got["bound"] == "highest volatility" and got["volatility"] == options.VOLATILITY[1]
    assert got["delta"] == pytest.approx(math.exp(-q * t), abs=2e-3)
    ceiling = 752.69 if style == "american" else 752.69 * math.exp(-q * t)
    assert options.delta(price=ceiling + 0.01, **kw) is None
