"""Holding-price following through the store, at observed marks and endogenous world prices."""
from datetime import datetime

import pytest

from harness import store as store_module
from harness.derive.structure import followed, subject
from test_impact import event, world
from test_worth import holding, worth


STAMP = "2026-11-05T02:00:30+00:00"


def mark(w, price, minute=0):
    at = f"2026-11-05T02:{minute:02}:00+00:00"
    w.store.put_data("snapshot", "DEAL", {"status": "OK", "ticker": {"ticker": "DEAL", "lastTrade": {
        "p": price, "t": int(datetime.fromisoformat(at).timestamp() * 1e9), "s": 100}}},
        {"url": "test:snapshot"}, retrieved_at=at, range=("2026-11-04", "2026-11-04"))


def setup(tmp_path, monkeypatch, *, yes=40, no=20, price=28, chance=.3, response=.5):
    w = world(tmp_path)
    holding(w, "DEAL", price, 2800)
    mark(w, price)
    e = event(w, p=chance)
    y = worth(w, e, "name:DEAL", "yes", {"amount": yes})
    worth(w, e, "name:DEAL", "no", {"amount": no})
    monkeypatch.setattr(store_module, "_moment", lambda: STAMP)
    w.chances(e, {"value": chance, "follow": "name:DEAL", "responsiveness": response})
    return w, e, y


def node(w, e):
    result = w.store.engine.value("structure", subject(w.store, e))
    assert result.output is not None, result.cause
    return result.output["props"][e]


def follow(w, e):
    return next(c["follow"] for c in node(w, e)["chances"] if c.get("follow"))


def test_followed_holding_moves_at_new_mark_and_keeps_original_observed_mark(tmp_path, monkeypatch):
    w, e, _ = setup(tmp_path, monkeypatch)
    assert node(w, e)["p"] == pytest.approx(.3)
    mark(w, 30, 1)
    assert node(w, e)["p"] == pytest.approx(followed(.3, .5, .4, .5))
    f = follow(w, e)
    assert f["name"] == "name:DEAL"
    assert (f["q_then"], f["q_now"]) == pytest.approx((.4, .5))
    assert f["then_at"] == "2026-11-04T21:00:00-05:00"
    assert f["now_at"] == "2026-11-04T21:01:00-05:00"
    mark(w, 22, 2)
    assert node(w, e)["p"] == pytest.approx(followed(.3, .5, .4, .1))


def test_restated_consideration_recomputes_both_weights_against_same_original_price(tmp_path, monkeypatch):
    w, e, y = setup(tmp_path, monkeypatch, response=1)
    mark(w, 30, 1)
    before = node(w, e)["p"]
    w.store.edit(y, "value", {"value": {"amount": 50}, "note": "increased offer"})
    assert node(w, e)["p"] == pytest.approx(followed(.3, 1, 8 / 30, 10 / 30))
    assert node(w, e)["p"] != pytest.approx(before)
    assert follow(w, e)["stated_at"] == STAMP


def test_received_shares_solve_the_same_probability_that_places_the_acquirers_price(tmp_path, monkeypatch):
    w, e, y = setup(tmp_path, monkeypatch, price=30, yes=40, no=25, response=1)
    holding(w, "BUYER", 100, 1000)
    w.store.link(e, "consequence", "name:BUYER", {"shock": {"value": -0.2, "note": "dilution"}})
    w.store.edit(y, "value", {"value": {"cash": 5, "date": "2026-11-04",
                                            "shares": {"name": "name:BUYER", "quantity": .4}}, "note": "terms"})
    mark(w, 32, 1)
    def expected(p):
        spread = 5 + 40 * .8 / (1 - .2 * p) - 25
        return followed(.3, 1, 5 / spread, 7 / spread)
    lo, hi = .3, .9
    for _ in range(60):
        mid = (lo + hi) / 2
        if expected(mid) > mid:
            lo = mid
        else:
            hi = mid
    want = (lo + hi) / 2
    got = node(w, e)["p"]
    assert got == pytest.approx(want, abs=2e-10)
    assert got == pytest.approx(expected(got), abs=2e-10)


def test_ways_use_their_stated_shares_and_leave_unknown_shares_open(tmp_path, monkeypatch):
    w, e, _ = setup(tmp_path, monkeypatch, price=28, response=1)
    first, second = w.prop("First"), w.prop("Second")
    w.link(e, "way", first, side="yes")
    w.link(e, "way", second, side="yes")
    w.store.link(first, "worth", "name:DEAL", {"value": {"value": {"amount": 50}, "note": "first offer"}})
    mark(w, 30, 1)
    got = node(w, e)
    assert got.get("p") is None and got["open"]["lo"] == 0 and got["open"]["hi"] == 1
    assert follow(w, e)["q_now"] is None
    w.chances(first, {"given": [e], "value": .6})
    assert node(w, e)["p"] == pytest.approx(followed(.3, 1, 8 / 26, 10 / 26))


def test_identical_fixed_values_do_not_need_an_invented_split_between_ways(tmp_path, monkeypatch):
    w, e, _ = setup(tmp_path, monkeypatch)
    w.link(e, "way", w.prop("First"), side="yes")
    w.link(e, "way", w.prop("Second"), side="yes")
    mark(w, 30, 1)
    assert node(w, e)["p"] == pytest.approx(followed(.3, .5, .4, .5))


def test_equal_world_values_leave_the_following_probability_open(tmp_path, monkeypatch):
    w, e, _ = setup(tmp_path, monkeypatch, yes=28, no=28)
    assert node(w, e).get("p") is None
    assert "same value" in follow(w, e)["why"]


def test_outside_values_cannot_be_clipped_into_a_fictitious_probability(tmp_path, monkeypatch):
    w, e, _ = setup(tmp_path, monkeypatch)
    mark(w, 45, 1)
    result = w.store.engine.value("structure", subject(w.store, e))
    assert result.output is None and "outside" in result.cause


def test_unaffected_anchor_uses_the_same_placed_prices_as_the_final_impact(tmp_path, monkeypatch):
    from test_impact import channel
    from harness.derive import series
    w, e, _ = setup(tmp_path, monkeypatch, response=1)
    for row in list(w.store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends')")):
        w.store.put_data(row["kind"], row["subject"], w.store.data_payload(row["id"]), {"url": "test:anchor-history"},
                         retrieved_at="2026-11-05T02:00:00+00:00",
                         range=(series.lookback("2026-11-03"), "2026-11-04"))
    no = next(l for l in w.store.links_of(e, "worth") if l["stated"]["world"] == "no")
    w.store.edit(no["id"], "value", {"value": {"return": -0.2, "date": "2026-11-03"}, "note": "unaffected price"})
    w.store.link(e, "consequence", channel(w, "SPY"), {"shock": {"value": 0.2, "note": "world shock"}})
    mark(w, 30, 1)
    p = node(w, e)["p"]
    result = w.store.engine.value("impact", e)
    assert result.output is not None, result.cause
    v = result.output["worth"]["name:DEAL"]["levels"]
    q_then, q_now = (28 - v["no"]) / (v["yes"] - v["no"]), (30 - v["no"]) / (v["yes"] - v["no"])
    assert p == pytest.approx(followed(.3, 1, q_then, q_now), abs=2e-10)
    assert follow(w, e)["q_now"] == pytest.approx(result.output["worth"]["name:DEAL"]["implied"])


def test_later_curve_reading_revalues_cash_at_both_price_observation_times(tmp_path, monkeypatch):
    w, e, y = setup(tmp_path, monkeypatch, response=1)
    for year in (2025, 2026):
        w.store.put_data("yield_curve", str(year), {"results": [] if year == 2025 else [
            {"date": "2026-11-04", "curve": [[1, .05]]}]}, {"url": "test:curve"},
            retrieved_at="2026-11-05T02:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    w.store.edit(y, "value", {"value": {"cash": 40, "date": "2027-11-04"}, "note": "cash terms"})
    assert node(w, e)["p"] == pytest.approx(.3)
    mark(w, 30, 1)
    before = node(w, e)["p"]
    w.store.put_data("yield_curve", "2026", {"results": [{"date": "2026-11-04", "curve": [[1, .10]]}]},
        {"url": "test:later-curve"}, retrieved_at="2026-11-05T02:02:00+00:00", range=("2026-11-04", "2026-11-04"))
    value = 40 / 1.05 ** 2  # one-year Treasury par yield, paid semiannually
    expected = followed(.3, 1, (28 - 20) / (value - 20), (30 - 20) / (value - 20))
    assert node(w, e)["p"] == pytest.approx(expected)
    assert node(w, e)["p"] != pytest.approx(before)
    assert follow(w, e)["then_at"] == "2026-11-04T21:00:00-05:00"


def test_the_followed_consideration_stays_until_the_chance_is_restated(tmp_path, monkeypatch):
    from harness.store import StoreError
    w, e, y = setup(tmp_path, monkeypatch)
    before = w.store.link_by_id(y)
    with pytest.raises(StoreError, match="follows"):
        w.store.remove(y)
    assert w.store.link_by_id(y) == before
    w.chances(e, {"value": .3})
    w.store.remove(y)
    assert w.store.link_by_id(y) is None


def test_at_a_responsiveness_of_0_the_chance_stands_whatever_the_price_implies(tmp_path, monkeypatch):
    """MATH.md 1.2: a responsiveness of 0 keeps the stated level, so a price outside the stated values leaves it standing,
    and check() lists the values that contradict the price (1.10)."""
    from harness.ledger import check
    w, e, _ = setup(tmp_path, monkeypatch, response=0)
    mark(w, 45, 1)  # above both stated values
    assert node(w, e)["p"] == pytest.approx(.3)
    [listed] = check(w.store)["values_contradict_price"]
    assert listed["id"] == e and listed["name"] == "name:DEAL" and listed["implied"] == pytest.approx((45 - 20) / 20)


def test_values_of_cash_alone_read_nothing_of_the_portfolios_exposures(tmp_path, monkeypatch):
    """The following chance reads what the holding's values are made of in each world, which no probability enters
    (worth_follow.unplaced); amounts and cash in dollars move with no world's prices, so the structure that follows
    them reads no betas, factors or shocks, and the impact is never read at a candidate probability."""
    from harness.derive import worth_follow
    w, e, _ = setup(tmp_path, monkeypatch)
    got = w.store.engine.value("unplaced", worth_follow.subject(e, "name:DEAL"))
    held = w.store.computation(got.basis)["inputs"]
    assert not {"betas", "channels", "consequences"} & set(held)
    assert got.output["names"] == {} and got.output["factors"] == {}
    assert got.output["values"]["yes"]["fixed"] == 40 and got.output["values"]["no"]["fixed"] == 20
