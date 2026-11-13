"""The range of risk-neutral probabilities a market's price allows (MATH.md 1.2; harness.derive.markets.risk_neutral), and
what each venue pays on a position, read from its own page under its own label (harness.venues.paid). The expected
figures are worked out here from the definitions, not from the code's functions: a flat par curve at 4% discounts a
dollar paid within half a year as a bill, `1 / (1 + 0.04 t)`, and on the half-year grid as `1.02 ** (-2 t)`."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import KALSHI_PAGE, POLYMARKET_PAGE, paid
from harness.derive.markets import allows, growth, risk_neutral
from harness.venues import MODULES
from harness.venues.http import VenueError
from harness.venues.paid import stated

FLAT = {"date": "2026-11-10", "curve": [[1 / 12, 0.04], [0.5, 0.04], [1.0, 0.04], [5.0, 0.04], [30.0, 0.04]]}
KALSHI = {"rate": 0.035, "paid": "annual", "stated": "Prediction Market Interest\nRate: 3.50% APY.",
          "url": MODULES["kalshi"].PAID["url"], "read_at": "2026-11-11T15:00:00+00:00"}
POLY = {"rate": 0.04, "paid": "daily", "stated": "…", "url": MODULES["polymarket"].PAID["url"],
        "read_at": "2026-11-11T15:00:00+00:00"}
ASOF = "2026-11-11"


def later(days: int) -> str:
    return (date.fromisoformat(ASOF) + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- each venue's page, read under its label


def test_a_venues_rate_is_the_figure_under_its_own_label_with_the_sentence_it_stands_in():
    kalshi = stated("kalshi", "Kalshi", MODULES["kalshi"].PAID, KALSHI_PAGE)
    assert (kalshi["rate"], kalshi["paid"]) == (0.035, "annual")  # not the perps' 3.25% under their own label
    assert kalshi["stated"] == "Prediction Market Interest\nRate: 3.50% APY."  # its heading and its line
    poly = stated("polymarket", "Polymarket", MODULES["polymarket"].PAID, POLYMARKET_PAGE)
    assert (poly["rate"], poly["paid"]) == (0.04, "daily")  # the emphasis around the figure is markup
    assert poly["stated"].startswith("Polymarket pays a 4.00% annualized Holding Reward based on")


def test_a_page_whose_label_is_missing_or_carries_two_figures_is_a_read_that_fails():
    spec = MODULES["kalshi"].PAID
    with pytest.raises(VenueError, match="states no rate under its label 'Prediction Market Interest, Rate:'"):
        stated("kalshi", "Kalshi", spec, "<h1>Perpetuals (Perps) Interest</h1><p>Rate: 3.50% APY.</p>")
    twice = "<h1>Prediction Market Interest</h1><p>Rate: 3.50% APY.</p><h2>Prediction Market Interest</h2>" \
            "<p>Rate: 3.75% APY.</p>"
    with pytest.raises(VenueError, match="states 2 different rates under its label"):
        stated("kalshi", "Kalshi", spec, twice)
    same = "<h1>Prediction Market Interest</h1><p>Rate: 3.50% APY.</p>" * 2
    assert stated("kalshi", "Kalshi", spec, same)["rate"] == 0.035  # stated twice, one figure


def test_the_paid_client_reads_the_page_and_a_venue_that_pays_nothing_states_no_figure():
    client = paid()
    got = client.read("kalshi", (ASOF, ASOF))
    assert got["payload"]["rate"] == 0.035 and got["range"] == (ASOF, ASOF) and client.asked == [KALSHI["url"]]
    with pytest.raises(LookupError):
        client.read("polymarket_us", (ASOF, ASOF))
    with pytest.raises(VenueError, match="answered 503"):
        paid(status=503).read("kalshi", (ASOF, ASOF))


# ---------------------------------------------------------------- the arithmetic


def test_a_market_that_closes_in_a_week_allows_a_sliver_either_side_of_its_price():
    found = risk_neutral(0.30, later(7), ASOF, FLAT, KALSHI)
    g_t, g_v = 1 + 0.04 * 7 / 365, 1.035 ** (7 / 365)
    k = g_t / g_v - 1
    assert found["k"] == pytest.approx(k, abs=1e-6) and found["days"] == 7
    assert [found["low"], found["high"]] == pytest.approx([1 - 0.70 * (1 + k), 0.30 * (1 + k)], abs=1e-6)
    assert found["treasury"] == {"curve_date": "2026-11-10", "growth": pytest.approx(g_t, abs=1e-6)}
    assert found["venue"]["growth"] == pytest.approx(g_v, abs=1e-6) and found["venue"]["url"] == KALSHI["url"]


def test_a_market_that_closes_in_three_years_allows_a_wide_range_and_one_paid_more_than_the_treasury_turns_it():
    days = (date.fromisoformat(later(1095)) - date.fromisoformat(ASOF)).days
    assert days == 1095  # three years of 365 days: six half-years on the curve's grid
    kalshi = risk_neutral(0.30, later(1095), ASOF, FLAT, KALSHI)
    k = 1.02 ** 6 / 1.035 ** 3 - 1
    assert kalshi["k"] == pytest.approx(k, abs=1e-6)
    assert [kalshi["low"], kalshi["high"]] == pytest.approx([1 - 0.70 * (1 + k), 0.30 * (1 + k)], abs=1e-6)
    assert risk_neutral(0.99, later(1095), ASOF, FLAT, KALSHI)["high"] == 1.0  # within 0 and 1
    poly = risk_neutral(0.98, later(1095), ASOF, FLAT, POLY)
    k = 1.02 ** 6 / (1 + 0.04 / 365) ** 1095 - 1
    assert k < 0  # Polymarket's reward, paid daily, outgrows the Treasury over three years
    assert [poly["low"], poly["high"]] == pytest.approx([0.98 * (1 + k), 1 - 0.02 * (1 + k)], abs=1e-6)
    none = risk_neutral(0.30, later(1095), ASOF, FLAT, {"rate": 0.0, "url": MODULES["polymarket_us"].PAID["url"]})
    assert none["k"] == pytest.approx(1.02 ** 6 - 1, abs=1e-6) and none["venue"]["growth"] == 1.0


def test_the_range_says_what_it_waits_on_and_a_market_with_no_price_allows_nothing():
    assert risk_neutral(None, later(7), ASOF, FLAT, KALSHI) is None
    assert risk_neutral(0.3, None, ASOF, None, "Kalshi's rate on a position, read from its page") == {"waits_on": [
        "the market's close date", f"the Treasury's yield curve on or before {ASOF}",
        "Kalshi's rate on a position, read from its page"]}
    assert risk_neutral(0.3, ASOF, ASOF, FLAT, KALSHI)["k"] == 0.0  # it closes today: nothing forgone
    assert allows(0.5, 0.0) == [0.5, 0.5] and growth(None, 400) == 1.0


# ---------------------------------------------------------------- a kept market, and one read from its venue


def curve_fetcher(world, curve=FLAT, fails=False):
    def fetch(subject, covering):
        if fails:
            raise ConnectionError("the Treasury did not answer")
        return {"payload": {"status": "OK", "results": [curve] if subject == curve["date"][:4] else []},
                "request": {"url": "treasury"}, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "range": tuple(covering)}
    world.store.fetchers["yield_curve"] = fetch


def test_a_kept_market_carries_its_range_with_the_venues_page_and_a_failed_read_keeps_the_last(world):
    from harness.derive.markets import NY
    today = datetime.now(NY).date().isoformat()
    world.store.add("Portfolio", {"asof": today})
    curve_fetcher(world, {**FLAT, "date": today})
    t = world.store.add("Proposition", {"title": "T", "text": "x", "comparison": "occurs", "window_end": "2027-03-31",
                                        "kind": "hitting"})
    world.store.link(t["id"], "asks", "market:kalshi:KXFED-26DEC-T4.50")
    found = world.store.get("market:kalshi:KXFED-26DEC-T4.50")["derived"]["risk_neutral"]["value"]
    assert found["venue"]["rate"] == 0.035 and found["venue"]["stated"] == KALSHI["stated"]
    assert found["low"] <= 0.335 <= found["high"] or found["k"] == 0.0
    assert "holding_reward" not in world.store.get("market:kalshi:KXFED-26DEC-T4.50")["derived"]
    world.venues.paid = paid({})  # the page now answers 404: the last reading kept stands
    world.store.fetchers.update(world.venues.fetchers(world.store))
    assert world.store.get("market:kalshi:KXFED-26DEC-T4.50")["derived"]["risk_neutral"]["value"]["venue"]["rate"] \
        == 0.035
    world.store.link(t["id"], "asks", "market:polymarket:516710")  # not marked eligible: Polymarket pays nothing on it
    poly = world.store.get("market:polymarket:516710")["derived"]["risk_neutral"]["value"]
    assert poly["venue"] == {"rate": 0.0, "url": MODULES["polymarket"].PAID["url"], "growth": 1.0}


def test_a_market_read_from_its_venue_carries_the_same_range_and_waits_on_what_it_cannot_read(world):
    from harness.derive.markets import NY
    today = datetime.now(NY).date().isoformat()
    world.store.add("Portfolio", {"asof": today})
    curve_fetcher(world, fails=True)  # a Treasury that does not answer, and a page that cannot be read: the book stands
    world.venues.paid = paid({})
    world.store.fetchers.update(world.venues.fetchers(world.store))
    read = world.read_market("market:kalshi:KXFED-26DEC-T4.50")
    assert read["price"] == pytest.approx(0.335) and "holding_reward" not in read
    waits = read["risk_neutral"]["waits_on"]
    assert waits == [f"the Treasury's yield curve on or before {today}",
                     f"Kalshi's page {KALSHI['url']} answered 404"]  # the cause, as the venue's read states it
    curve_fetcher(world, {**FLAT, "date": today})
    world.venues.paid = paid()
    world.store.fetchers.update(world.venues.fetchers(world.store))
    found = world.read_market("market:kalshi:KXFED-26DEC-T4.50")["risk_neutral"]
    assert found["venue"]["rate"] == 0.035 and found["treasury"]["curve_date"] == today


def test_a_past_as_of_reads_only_the_rate_kept_by_then(world):
    world.store.add("Portfolio", {"asof": "2026-10-01"})
    curve_fetcher(world, {**FLAT, "date": "2026-09-30"})
    t = world.store.add("Proposition", {"title": "T", "text": "x", "comparison": "occurs", "window_end": "2027-03-31",
                                        "kind": "hitting"})
    world.store.link(t["id"], "asks", "market:kalshi:KXFED-26DEC-T4.50")
    found = world.store.get("market:kalshi:KXFED-26DEC-T4.50")["derived"]["risk_neutral"]["value"]
    assert found == {"waits_on": ["Kalshi's rate on a position as it stood on 2026-10-01, which no reading kept here "
                                  "gives"]} or found is None
    assert not world.venues.paid.asked  # the page today says nothing of the rate then
