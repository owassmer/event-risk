"""An option's worlds, their pricing weights, and the same contract at a later date (MATH.md 1.6–1.8)."""

from __future__ import annotations

import math

import pytest

from harness.derive import options


def worlds(**changes):
    args = dict(kind="call", style="european", strike=100.0, years=0.5, rate=0.0,
                dividend_yield=0.0, underlying_today=100.0,
                value_today=5.6371977797016655,
                levels={"yes": 89.5, "no": 104.5}, weights={"yes": 0.3, "no": 0.7})
    return options.worlds_value(**(args | changes))


def test_calibrated_worlds_match_the_current_quote_at_pricing_weights():
    """The independent two-world calculation gives 17.3171%, not the quote's ordinary 20% implied volatility."""
    got = worlds()
    assert got["volatility"] == pytest.approx(0.173171, abs=5e-7)
    assert got["calibrated"] and got["bound"] is None
    assert 0.3 * got["values"]["yes"] + 0.7 * got["values"]["no"] == pytest.approx(5.6371977797016655, abs=1e-8)
    assert got["pricing_gap"] == pytest.approx(0.0, abs=1e-8)
    # A distinct forecast probability must retain the option's expected gain or loss.
    expected = 0.6 * got["values"]["yes"] + 0.4 * got["values"]["no"] - 5.6371977797016655
    assert expected == pytest.approx(-1.92329146, abs=1e-7)


def test_call_and_put_worlds_preserve_put_call_parity():
    """At the same strike, date, and quoted surface, the two calibrations share a volatility and parity per world."""
    s, k, t, r, q = 100.0, 105.0, 0.75, 0.04, 0.02
    call_today = options.european(True, s, k, t, r, q, 0.3)[0]
    put_today = call_today - s * math.exp(-q * t) + k * math.exp(-r * t)
    terms = dict(strike=k, years=t, rate=r, dividend_yield=q)
    call = worlds(value_today=call_today, **terms)
    put = worlds(kind="put", value_today=put_today, **terms)
    assert call["calibrated"] and put["calibrated"]
    assert call["volatility"] == pytest.approx(put["volatility"], abs=1e-9)
    for world, level in (("yes", 89.5), ("no", 104.5)):
        assert call["values"][world] - put["values"][world] == pytest.approx(
            level * math.exp(-q * t) - k * math.exp(-r * t), abs=1e-9)


def test_zero_volatility_boundary_keeps_the_gap_and_exact_payoffs():
    got = worlds(levels={"yes": 50.0, "no": 150.0}, weights={"yes": 0.5, "no": 0.5})
    assert got["values"] == {"yes": 0.0, "no": 50.0}
    assert got["volatility"] == 0.0 and got["bound"] == "no volatility"
    assert not got["calibrated"]
    assert got["pricing_gap"] == pytest.approx(25.0 - 5.6371977797016655)


def test_high_volatility_boundary_is_valued_without_claiming_a_calibration():
    got = worlds(years=1e-4, value_today=80.0)
    assert got["bound"] == "highest volatility"
    assert got["volatility"] == options.VOLATILITY[1]
    assert not got["calibrated"] and got["pricing_gap"] < 0.0
    assert all(v >= 0.0 for v in got["values"].values())


@pytest.mark.parametrize("kind", ["call", "put"])
def test_american_worlds_use_the_same_early_exercise_model(kind):
    levels, weights = {"a": 95.0, "b": 105.0}, {"a": 0.5, "b": 0.5}
    call = kind == "call"
    expected = {w: options.american(call, level, 100.0, 0.5, 0.04, 0.02, 0.3)[0]
                for w, level in levels.items()}
    got = worlds(kind=kind, style="american", rate=0.04, dividend_yield=0.02,
                 levels=levels, weights=weights, value_today=sum(weights[w] * expected[w] for w in levels))
    assert got["model"] == "binomial" and got["calibrated"]
    assert got["volatility"] == pytest.approx(0.3, abs=1e-7)
    assert got["values"] == pytest.approx(expected, abs=1e-7)
    for w, level in levels.items():
        assert got["values"][w] >= max((level - 100.0) if call else (100.0 - level), 0.0)


def test_expiry_is_a_payoff_not_a_second_terminal_contract():
    got = worlds(years=0.0, value_today=0.0,
                 levels={"below": 80.0, "above": 120.0}, weights={"below": 0.5, "above": 0.5})
    assert got["values"] == {"below": 0.0, "above": 20.0}
    assert got["bound"] == "exercise" and got["volatility"] == 0.0
    # A payment is made at expiry. Its later present value uses the expiry level, not a later underlying price.
    payment = options.value(kind="call", style="european", strike=100.0, years=0.0,
                            rate=0.04, dividend_yield=0.0, underlying=120.0, volatility=0.9)
    discounted_payment = math.exp(-0.04 * 0.5) * payment
    assert discounted_payment == pytest.approx(19.603973466135105)


def test_clock_advances_without_changing_the_calibrated_pricing_input():
    got = worlds()
    later = options.value(kind="call", style="european", strike=100.0, years=0.1, rate=0.0,
                          dividend_yield=0.0, underlying=104.5, volatility=got["volatility"])
    assert 4.5 < later < got["values"]["no"]


@pytest.mark.parametrize("style", ["european", "american"])
def test_zero_underlying_and_zero_strike_are_valued_at_their_contract_boundaries(style):
    common = dict(style=style, years=0.5, rate=0.04, dividend_yield=0.02, volatility=0.2)
    assert options.value(kind="call", strike=100.0, underlying=0.0, **common) == 0.0
    zero_put = options.value(kind="put", strike=100.0, underlying=0.0, **common)
    assert zero_put == pytest.approx(100.0 if style == "american" else 100.0 * math.exp(-0.02))
    zero_call = options.value(kind="call", strike=0.0, underlying=100.0, **common)
    assert zero_call == pytest.approx(100.0 if style == "american" else 100.0 * math.exp(-0.01))
    assert options.value(kind="put", strike=0.0, underlying=100.0, **common) == 0.0


@pytest.mark.parametrize("changes", [
    {"weights": {"yes": 0.3}},
    {"weights": {"yes": 0.3, "no": 0.3}},
    {"weights": {"yes": -0.3, "no": 1.3}},
    {"weights": {"yes": float("nan"), "no": 0.7}},
    {"levels": {"yes": -1.0, "no": 104.5}},
    {"value_today": float("nan")},
    {"style": None},
    {"value_today": 101.0},
])
def test_missing_or_incompatible_inputs_are_not_silently_completed(changes):
    with pytest.raises(ValueError):
        worlds(**changes)
