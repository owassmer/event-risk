"""A ladder (MATH.md 1.2, "A ladder"; 2.1): propositions on one metric and resolution source that differ only in their
date, or only in their threshold, which the fields place on one line; a band between two rungs; and a chance between
two rungs that names a reading of the ladder. Figures by hand."""

from __future__ import annotations

import pytest

from breakdown import World
from harness.store import StoreError


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "scratch-ladder")
    yield world
    world.store.close()


MARCH = "2027-03-31"


def rung(w, title: str, comparison: str, value: float, **more) -> str:
    """A level on March 31 on the tariff metric."""
    return w.prop(title, comparison, value, kind="level_at_date", end=MARCH, **more)


def band(w, title: str, lo: tuple[str, float], hi: tuple[str, float]) -> str:
    return rung(w, title, lo[0], lo[1], upper_comparison=hi[0], upper_value=hi[1])


# ---------------------------------------------------------------- a band


def test_a_band_states_both_its_ends_on_a_level_on_a_date(w):
    """A band runs from its lower end to its upper, on a level on a date or a decision on it: one end alone, a lower
    end that is not one, an upper end at or below the lower, and a band on reaching a level at any time are refused."""
    with pytest.raises(StoreError, match="states its upper end whole"):
        rung(w, "half", ">", 2.5, upper_comparison="<=")
    with pytest.raises(StoreError, match="comparison < is not a lower end"):
        rung(w, "upside down", "<", 2.5, upper_comparison="<=", upper_value=3.0)
    with pytest.raises(StoreError, match="its upper end its upper_value, above it: value is 3 and upper_value 2.5"):
        band(w, "empty", (">", 3.0), ("<=", 2.5))
    with pytest.raises(StoreError, match="a level between two thresholds on a date or at a scheduled decision"):
        w.prop("reached", ">", 2.5, kind="hitting", upper_comparison="<=", upper_value=3.0)
    assert w.store.stated_of(band(w, "(2.5, 3]", (">", 2.5), ("<=", 3.0)))["upper_value"] == 3.0


def test_the_fields_decide_a_band_within_the_rung_below_it_and_not_within_the_rung_above(w):
    """2.5 < x <= 3 lies within x > 2.5 and within x <= 3; linked as implying x > 3, the fields contradict it, with the
    values compared."""
    from harness.ledger import check
    b = band(w, "(2.5, 3]", (">", 2.5), ("<=", 3.0))
    below, above = rung(w, "above 2.5", ">", 2.5), rung(w, "above 3", ">", 3.0)
    w.link(b, "implies", below)
    wrong = w.link(b, "implies", above)
    [listed] = check(w.store)["fields_contradict"]
    assert listed["link"] == wrong and listed["within"]["upper_value"] == 3.0 and listed["of"]["value"] == 3.0


# ---------------------------------------------------------------- a ladder is one line


def priced(w, pid: str, q: float) -> str:
    """A market on pid whose bid and ask meet at q: it holds pid at its price."""
    w.link(pid, "asks", w.market(pid, f"{pid}?", q, q))
    return pid


def test_rungs_and_bands_the_fields_place_on_one_line_read_together_with_no_link(w):
    """MATH.md 1.2's illustration: rungs above 2.5 at 0.80, above 3 at 0.45 and above 3.5 at 0.12 on one date, and the
    bands between them, with no link among any of them. The fields place every one on the line of the rate's level on
    March 31: the bands are 0.80 - 0.45 = 0.35 and 0.45 - 0.12 = 0.33, the open band below 2.5 is 0.20, and the open
    band above 3.5 is the top rung's 0.12; each is one structure."""
    r25 = priced(w, rung(w, "above 2.5", ">", 2.5), 0.80)
    r3 = priced(w, rung(w, "above 3", ">", 3.0), 0.45)
    r35 = priced(w, rung(w, "above 3.5", ">", 3.5), 0.12)
    low = rung(w, "2.5 or below", "<=", 2.5)
    b1, b2 = band(w, "(2.5, 3]", (">", 2.5), ("<=", 3.0)), band(w, "(3, 3.5]", (">", 3.0), ("<=", 3.5))
    assert [round(w.point(x), 10) for x in (low, b1, b2, r35)] == [0.20, 0.35, 0.33, 0.12]
    from harness.derive.structure import store_component
    assert store_component(w.store, b1) == sorted([r25, r3, r35, low, b1, b2], key=lambda p: int(p.split(":")[1]))


def test_a_rung_on_another_date_or_another_metric_is_on_another_line(w):
    """Above 3 on March 31 and above 3 on June 30 are on two lines; so is a level on the market's price: neither is
    tied to the March ladder by its fields."""
    from harness.derive.structure import store_component
    r3 = rung(w, "above 3", ">", 3.0)
    june = w.prop("above 3 in June", ">", 3.0, kind="level_at_date", end="2027-06-30")
    r25 = rung(w, "above 2.5", ">", 2.5)
    assert store_component(w.store, r3) == [r3, r25] and store_component(w.store, june) == [june]


def deadline(w, title: str, end: str) -> str:
    """The rate at 25% or more at any time from the as-of through `end`."""
    return w.prop(title, ">=", 25.0, kind="hitting", end=end)


def test_deadlines_the_fields_place_on_one_line_order_themselves_and_a_date_between_lies_between(w):
    """By t1 at 0.20 and by t2 at 0.50, on the line of the date the rate first reaches 25% from the as-of: a deadline
    between them, with nothing stated of it, is left open between them, 0.20 to 0.50; one past t2 lies at 0.50 or
    above. No link is made among them."""
    t1 = priced(w, deadline(w, "by t1", "2027-01-31"), 0.20)
    t2 = priced(w, deadline(w, "by t2", "2027-04-30"), 0.50)
    t, later = deadline(w, "by t", "2027-03-01"), deadline(w, "by t3", "2027-06-30")
    o = w.store.get(t)["derived"]["left_open"]["value"]
    assert (round(o["lo"], 10), round(o["hi"], 10)) == (0.20, 0.50)
    assert round(w.store.get(later)["derived"]["left_open"]["value"]["lo"], 10) == 0.50
    assert w.point(t1) == 0.20 and w.point(t2) == 0.50


# ---------------------------------------------------------------- a reading between two rungs


T1, T, T2 = "2027-01-01", "2027-03-02", "2027-04-01"  # t lies 60 of the 90 days from t1 to t2: theta = 2/3


def readable(w, reading, t1=0.20, t2=0.50, **more):
    """By t1 at 0.20 and by t2 at 0.50, each a book whose bid and ask meet, and by t, 60 of the 90 days between them,
    whose chance given by t2 and not by t1 names the reading."""
    r1, r2 = priced(w, deadline(w, "by t1", T1), t1), priced(w, deadline(w, "by t2", T2), t2)
    t = deadline(w, "by t", T)
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": reading, **more})
    return t, r1, r2


def test_spec_1_2s_illustration_even_through_time_and_at_a_constant_hazard(w, tmp_path):
    """By t1 at 0.20 and by t2 at 0.50, for a date two thirds of the way between them: even through time, by t is
    0.20 + 2/3 x 0.30 = 0.400; at a constant hazard, 1 - 0.8 x (0.5 / 0.8)^(2/3) = 0.4152."""
    t, _, _ = readable(w, "even")
    assert w.point(t) == pytest.approx(0.40, abs=1e-9)
    other = World(tmp_path / "scratch-ladder-hazard")
    t, _, _ = readable(other, "hazard")
    assert other.point(t) == pytest.approx(1 - 0.8 * (0.5 / 0.8) ** (2 / 3), abs=1e-9)
    assert round(other.point(t), 3) == 0.415
    [c] = other.store.get(t)["derived"]["probability"] and _shown(other, t)
    assert c["reading"] == {"kind": "hazard", "theta": pytest.approx(2 / 3)}
    assert c["value"] == pytest.approx((other.point(t) - 0.20) / 0.30, abs=1e-9)  # the share s now
    other.store.close()


def _shown(w, pid):
    from harness.derive.structure import subject
    return w.store.engine.value("structure", subject(w.store, pid)).output["props"][pid]["chances"]


def test_by_the_calendar_the_share_of_the_weight_on_the_dates_between_that_falls_by_t(w):
    """Dates on January 15, February 15, March 15 and March 25, after t1 through t2: two of the four fall by March 2,
    so s = 0.5 and by t is 0.20 + 0.5 x 0.30 = 0.35; with March 15 weighted 2, s = 2 / 5 and by t is 0.32. A date on
    t1 itself is in by t1 already, and counts nowhere."""
    days = [{"date": "2027-01-15"}, {"date": "2027-02-15"}, {"date": "2027-03-15"}, {"date": "2027-03-25"}]
    t, r1, r2 = readable(w, {"calendar": [{"date": T1}, *days]})
    assert w.point(t) == pytest.approx(0.35, abs=1e-9)
    days[2]["weight"] = 2.0
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": {"calendar": days}})
    assert w.point(t) == pytest.approx(0.32, abs=1e-9)


def test_before_the_first_rung_the_reading_runs_from_the_as_of(w):
    """By t1, January 1, at 0.20, 58 days from the as-of, November 4; by December 3, 29 days in: even through time
    the chance given by t1 is 29 / 58 = 0.5, and by t is 0.10; at a constant hazard it is (1 - 0.8^0.5) / 0.2, and by
    t is 1 - 0.8^0.5 = 0.105573."""
    r1 = priced(w, deadline(w, "by t1", T1), 0.20)
    t = deadline(w, "by t", "2026-12-03")
    w.chances(t, {"given": [r1], "reading": "even"})
    assert w.point(t) == pytest.approx(0.10, abs=1e-9)
    w.chances(t, {"given": [r1], "reading": "hazard"})
    assert w.point(t) == pytest.approx(1 - 0.8 ** 0.5, abs=1e-9)


def test_a_reading_stays_current_with_the_rungs_prices(w):
    """By t2's book moves to 0.60: even through time, by t is 0.20 + 2/3 x 0.40 = 0.4667, read again with no new
    statement."""
    t, _, r2 = readable(w, "even")
    [mid] = [x["to"] for x in w.store.links_of(r2, "asks")]
    w.set_book(mid, 0.60, 0.60)
    w.reread(mid)
    assert w.point(t) == pytest.approx(0.20 + 2 / 3 * 0.40, abs=1e-9)


def test_at_a_constant_hazard_the_rungs_and_the_share_hold_together(w):
    """By t1 at 0.18 to 0.22, by t2 at 0.48 to 0.52, theta 2/3, and a market on by t at 0.41 to 0.43, each book within
    its quotes. The share reads the rungs as the structure gives them, and the rungs read by t's market through it, so
    they are read until they hold still: s = 0.71794, by t 0.418634, by t1 0.201541 and by t2 0.503924, by hand (the
    nearest set in half-spreads, solved apart, with s recomputed until it stopped moving)."""
    r1 = deadline(w, "by t1", T1)
    w.link(r1, "asks", w.market("t1", "t1?", 0.18, 0.22))
    r2 = deadline(w, "by t2", T2)
    w.link(r2, "asks", w.market("t2", "t2?", 0.48, 0.52))
    t = deadline(w, "by t", T)
    w.link(t, "asks", w.market("t", "t?", 0.41, 0.43))
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": "hazard"})
    [c] = _shown(w, t)
    assert c["value"] == pytest.approx(0.71794, abs=2e-5) and w.point(t) == pytest.approx(0.418634, abs=2e-5)
    assert w.point(r1) == pytest.approx(0.201541, abs=2e-5) and w.point(r2) == pytest.approx(0.503924, abs=2e-5)


def test_a_reading_is_refused_where_its_deadlines_are_not_one_ladder_in_its_order(w):
    """A reading is the chance of a deadline given a later one and not an earlier one on one ladder: given the earlier
    one, given a deadline on another threshold, with a figure beside it, or a calendar with no date between, it is
    refused."""
    r1, r2 = deadline(w, "by t1", T1), deadline(w, "by t2", T2)
    t, other = deadline(w, "by t", T), w.prop("by t2 at 30", ">=", 30.0, kind="hitting", end=T2)
    with pytest.raises(StoreError, match="prop:3 is by 2027-03-02: the rung it is given, by 2027-01-01, comes "
                                         "before it, and the one it is given not, by 2027-04-01, after it"):
        w.chances(t, {"given": [r1], "given_not": [r2], "reading": "even"})
    with pytest.raises(StoreError, match="prop:3 is reached at >= 25 and prop:4 at >= 30"):
        w.chances(t, {"given": [other], "given_not": [r1], "reading": "even"})
    with pytest.raises(StoreError, match="its value is the reading's share"):
        w.chances(t, {"given": [r2], "given_not": [r1], "reading": "even", "value": 0.5})
    with pytest.raises(StoreError, match="names no date after 2027-01-01 through 2027-04-01"):
        w.chances(t, {"given": [r2], "given_not": [r1], "reading": {"calendar": [{"date": "2027-05-01"}]}})


# ---------------------------------------------------------------- deadlines on one occurrence


def occurrence(w, mid: str = "offensive") -> str:
    """Something that happens, as a metric: no unit and no level (ONTOLOGY.md 2.1)."""
    w.store.add("Metric", {"id": mid, "name": "a military offensive begins", "kind": "occurrence",
                           "resolution_source": "official confirmation"})
    return f"metric:{mid}"


def by(w, title: str, end: str, on: str, kind: str = "hitting") -> str:
    """That the occurrence happens at any time from the as-of through `end`, or at the decision on `end`."""
    return w.prop(title, "occurs", kind=kind, end=end, metric=on)


def test_deadlines_on_one_occurrence_are_a_ladder_the_fields_order_and_a_reading_reads(w, tmp_path):
    """MATH.md 1.2's ladder, on an occurrence (2.1): by t1 at 0.20 and by t2 at 0.50, each a book whose bid and ask meet,
    and by t, 60 of the 90 days between them, three propositions that the offensive occurs, with no link among them.
    With nothing stated of by t, it is left open between its rungs, 0.20 to 0.50. Even through time it is
    0.20 + 2/3 x 0.30 = 0.400; at a constant hazard, 1 - 0.8 x (0.5 / 0.8)^(2/3) = 0.4152, the illustration's figures
    on deadlines of a threshold."""
    on = occurrence(w)
    r1, r2 = priced(w, by(w, "by t1", T1, on), 0.20), priced(w, by(w, "by t2", T2, on), 0.50)
    t = by(w, "by t", T, on)
    o = w.store.get(t)["derived"]["left_open"]["value"]
    assert (round(o["lo"], 10), round(o["hi"], 10)) == (0.20, 0.50)
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": "even"})
    assert w.point(t) == pytest.approx(0.40, abs=1e-9)
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": "hazard"})
    assert w.point(t) == pytest.approx(1 - 0.8 * (0.5 / 0.8) ** (2 / 3), abs=1e-9)
    [c] = _shown(w, t)
    assert c["reading"] == {"kind": "hazard", "theta": pytest.approx(2 / 3)}


def test_before_the_first_rung_of_an_occurrence_the_reading_runs_from_the_as_of(w):
    """By t1, January 1, at 0.20, 58 days from the as-of, November 4; by December 3, 29 days in, on the same
    occurrence: even through time by t is 0.5 x 0.20 = 0.10; at a constant hazard 1 - 0.8^0.5 = 0.105573. Past the last
    rung, a stated chance given not by t1 carries it: by June 30 is 0.20 + 0.25 x 0.80 = 0.40."""
    on = occurrence(w)
    r1 = priced(w, by(w, "by t1", T1, on), 0.20)
    t = by(w, "by t", "2026-12-03", on)
    w.chances(t, {"given": [r1], "reading": "even"})
    assert w.point(t) == pytest.approx(0.10, abs=1e-9)
    w.chances(t, {"given": [r1], "reading": "hazard"})
    assert w.point(t) == pytest.approx(1 - 0.8 ** 0.5, abs=1e-9)
    past = by(w, "by June 30", "2027-06-30", on)
    w.chances(past, {"given_not": [r1], "value": 0.25})
    assert w.point(past) == pytest.approx(0.40, abs=1e-9)


def test_the_fields_decide_an_implication_between_deadlines_on_one_occurrence(w):
    """On an occurrence, which has no threshold, A implies B where A's time lies within B's (MATH.md 1.2): by t1 implies
    by t2, and so does the occurrence at the decision on t, a date within t2's window; the fields decide each and the
    judge is not asked. A link the other way, by t2 implies by t1, is one the fields contradict, and check() lists it
    with the readings compared. Deadlines on two occurrences are compared by no field."""
    from harness.ledger import check
    on, other = occurrence(w), occurrence(w, "blockade")
    r1, r2 = by(w, "by t1", T1, on), by(w, "by t2", T2, on)
    decision = by(w, "at the decision on t", T, on, kind="scheduled_decision")
    asked = len(w.judge.asked) if hasattr(w.judge, "asked") else None
    ok = [w.link(r1, "implies", r2), w.link(decision, "implies", r2)]
    for lid in ok:
        assert w.store.get(lid)["derived"]["terms"]["value"]["holds"] is True
    if asked is not None:
        assert len(w.judge.asked) == asked
    wrong = w.link(r2, "implies", r1)
    assert w.store.get(wrong)["derived"]["terms"]["value"]["holds"] is False
    assert [x["link"] for x in check(w.store)["fields_contradict"]] == [wrong]
    elsewhere = w.link(by(w, "blockade by t1", T1, other), "implies", r2)
    assert "terms" not in w.store.get(elsewhere)["derived"]


def test_an_occurrence_is_named_by_propositions_that_occur_and_has_no_unit_or_level(w):
    """An occurrence is something that happens (ONTOLOGY.md 2.1): a proposition on it occurs, a threshold is measured on a
    level, it has no unit or level, a level has its unit, and no occurrence is the price a factor quotes."""
    on = occurrence(w)
    with pytest.raises(StoreError, match="metric:offensive is something that happens: a proposition on it is true "
                                         "when it happens, with comparison occurs"):
        w.prop("above 25 on it", ">=", 25.0, metric=on)
    with pytest.raises(StoreError, match="comparison occurs is true when something happens, and the metric it names "
                                         "is that occurrence: metric:us_effective_tariff_rate_pct is a level"):
        by(w, "the rate occurs", T, "metric:us_effective_tariff_rate_pct")
    with pytest.raises(StoreError, match="has no unit"):
        w.store.add("Metric", {"id": "with_unit", "name": "x", "kind": "occurrence", "unit": "events"})
    with pytest.raises(StoreError, match="a metric of kind level is a level, stated in its unit"):
        w.store.add("Metric", {"id": "no_unit", "name": "x", "kind": "level"})
    with pytest.raises(StoreError, match="is something that happens, and a metric that quotes a factor"):
        w.store._check_quotes(on, "factor:1")  # what linking it to quote a factor checks, before the factor


def test_a_deadline_reading_places_its_rungs_by_their_dates_and_that_order_is_in_the_range(w):
    """MATH.md 1.2: a deadline reading states that the proposition and its rungs concern the same occurrence, counted from
    the same start, by their dates, so the store places them by those dates, whether or not the analyst links them or
    they share a metric: here three deadlines that name no metric. By t1 at 0.20 and by t2 at 0.50, by t 60 of the 90
    days between: even through time 0.400; at a constant hazard 1 - 0.8 x (0.5 / 0.8)^(2/3); before the first rung,
    from the as-of. The order the reading states is in the range the markets imply, 0.20 to 0.50, the same whether the
    analyst states the implications or they follow from the reading; only the share is the analyst's."""
    r1, r2, t = (w.prop(f"by {d}", "occurs", end=d) for d in (T1, T2, T))
    priced(w, r1, 0.20), priced(w, r2, 0.50)
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": "even"})
    assert w.point(t) == pytest.approx(0.40, abs=1e-9)
    assert w.interval(t) == (pytest.approx(0.20), pytest.approx(0.50))
    w.chances(t, {"given": [r2], "given_not": [r1], "reading": "hazard"})
    assert w.point(t) == pytest.approx(1 - 0.8 * (0.5 / 0.8) ** (2 / 3), abs=1e-9)
    w.link(r1, "implies", t), w.link(t, "implies", r2)  # stated as well: the same range and probability
    assert w.interval(t) == (pytest.approx(0.20), pytest.approx(0.50))
    assert w.point(t) == pytest.approx(1 - 0.8 * (0.5 / 0.8) ** (2 / 3), abs=1e-9)
    first = w.prop("by December 3", "occurs", end="2026-12-03")
    w.chances(first, {"given": [r1], "reading": "even"})
    assert w.point(first) == pytest.approx(0.5 * 0.20, abs=1e-9)
    assert w.interval(first) == (pytest.approx(0.0), pytest.approx(0.20))


def test_a_readings_refusals_say_the_fact_of_its_statement(w):
    """MATH.md 1.2: on one metric and resolution source the fields decide, and a pair they put on two lines is refused
    with what differs, whatever link the analyst made; a reading's propositions are deadlines; and a reading is given
    one rung, and given not one or none."""
    r1, r2, t = deadline(w, "by t1", T1), deadline(w, "by t2", T2), deadline(w, "by t", T)
    higher = w.prop("by t1 at 30", ">=", 30.0, kind="hitting", end=T1)
    w.link(higher, "implies", t)  # the fields decide on one metric: the link changes nothing
    with pytest.raises(StoreError, match=r"prop:4 is reached at >= 30 and prop:3 at >= 25"):
        w.chances(t, {"given": [r2], "given_not": [higher], "reading": "even"})
    level = w.prop("at t2", ">=", 25.0, kind="level_at_date", end=T2)
    with pytest.raises(StoreError, match=r"prop:5 is a level on 2027-04-01, not reached at any time by it"):
        w.chances(t, {"given": [level], "given_not": [r1], "reading": "even"})
    with pytest.raises(StoreError, match=r"is a reading given prop:2 and given not prop:1, prop:4"):
        w.chances(t, {"given": [r2], "given_not": [r1, higher], "reading": "even"})

def test_the_judge_reads_the_occurrence_a_proposition_names(w):
    """The judge is given the metric a proposition is measured on, here what happens."""
    from harness.derive.engine import Inputs
    from harness.derive.evidence import proposition_content
    from harness.judge import proposition_text
    t = by(w, "by t", T, occurrence(w))
    text = proposition_text(proposition_content(Inputs(w.store.engine), t), "The proposition")
    assert "Occurrence: a military offensive begins, reported by official confirmation" in text
    assert "Threshold" not in text and "Metric:" not in text


# ---------------------------------------------------------------- bands as worlds of the rung the analyst watches


def ladder_world(tmp_path, watch: str):
    """Evaluation's case: rungs above 2.5 at 0.80, above 3 at 0.45 and above 3.5 at 0.12 on one date, the open band at
    2.5 or below and the bands (2.5, 3] and (3, 3.5]; a $100,000 fund at 100 that moves one for one with the market,
    shocked +4%, 0, -5% and -12% in the four bands, against the band (2.5, 3] as the baseline. The analyst watches one
    rung and links no way: the bands its fields put on each side are its worlds."""
    from test_impact import channel, world
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 100.0})
    rungs = {"2.5": priced(w, rung(w, "above 2.5", ">", 2.5), 0.80), "3": priced(w, rung(w, "above 3", ">", 3.0), 0.45),
             "3.5": priced(w, rung(w, "above 3.5", ">", 3.5), 0.12)}
    low = rung(w, "2.5 or below", "<=", 2.5)
    b1, b2 = band(w, "(2.5, 3]", (">", 2.5), ("<=", 3.0)), band(w, "(3, 3.5]", (">", 3.0), ("<=", 3.5))
    e = rungs[watch]
    w.link("portfolio", "watches", e)
    w.store.edit(e, "baseline", b1)
    mkt = channel(w, "SPY")
    for world_, shock in ((low, 0.04), (b2, -0.05), (rungs["3.5"], -0.12)):
        w.link(world_, "consequence", mkt, shock=shock)
    return w, e, {"low": low, "b1": b1, "b2": b2, "top": rungs["3.5"]}


def test_a_watched_rung_s_worlds_are_the_bands_on_each_side_and_its_row_moves_only_with_which_rung(tmp_path):
    """Watching above 2.5, its Yes side is (2.5, 3], (3, 3.5] and above 3.5, its No side 2.5 or below: Yes -$1,609.35,
    No +$6,437.42, full impact -$8,046.77. Watching above 3: (3, 3.5] and above 3.5 against (2.5, 3] and below, Yes
    -$4,683.93 and No +$3,832.31. In both the worlds and their P&L are the same, so the volatility is ±$4,959.80 and
    the worst outcome -$9,937.57, in the band above 3.5 (evaluation's figures, from the text)."""
    from harness.ledger import row
    w, e, b = ladder_world(tmp_path / "a", "2.5")
    r = row(w.store, e)
    assert (round(r["yes"], 2), round(r["no"], 2), round(r["full_impact"], 2)) == (-1609.35, 6437.42, -8046.77)
    assert round(r["volatility"], 2) == 4959.80 and round(r["worst_outcome"]["pnl"], 2) == -9937.57
    assert r["worst_outcome"]["world"] == b["top"]
    tree = w.store.engine.value("outcomes", e).output["nodes"]
    assert tree["yes"]["ways"] == [b["b1"], b["b2"], b["top"]] and tree["no"]["ways"] == [b["low"]]
    w, e, b = ladder_world(tmp_path / "b", "3")
    r = row(w.store, e)
    assert (round(r["yes"], 2), round(r["no"], 2)) == (-4683.93, 3832.31)
    assert round(r["volatility"], 2) == 4959.80 and round(r["worst_outcome"]["pnl"], 2) == -9937.57
    tree = w.store.engine.value("outcomes", e).output["nodes"]
    assert tree["yes"]["ways"] == [b["b2"], b["top"]] and tree["no"]["ways"] == [b["low"], b["b1"]]


def test_a_side_stays_whole_until_something_is_stated_of_one_of_its_bands(tmp_path):
    """MATH.md 1.2: the bands can stand as a watched rung's worlds; a side stays whole until the analyst states something of
    one of its bands as it would of a way, and is then its bands, each side on its own. So a band made to read a market
    leaves the event's worlds and its baseline as they were."""
    from test_impact import channel, world
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 100.0})
    r25, r3 = priced(w, rung(w, "above 2.5", ">", 2.5), 0.80), priced(w, rung(w, "above 3", ">", 3.0), 0.45)
    r35 = priced(w, rung(w, "above 3.5", ">", 3.5), 0.12)
    b2 = band(w, "(3, 3.5]", (">", 3.0), ("<=", 3.5))
    band(w, "(2.5, 3]", (">", 2.5), ("<=", 3.0))
    w.link("portfolio", "watches", r3)
    w.store.edit(r3, "baseline", "no")
    mkt = channel(w, "SPY")
    w.link(r3, "consequence", mkt, shock=-0.05)
    tree = w.store.engine.value("outcomes", r3).output["nodes"]
    assert tree["yes"]["ways"] == [] and tree["no"]["ways"] == []  # nothing is stated of a band: two worlds
    assert w.store.engine.value("impact", r3).output["worlds"].keys() == {"yes", "no"}
    w.link(r35, "consequence", mkt, shock=-0.12)  # a shock on the band above 3.5: the Yes side is its bands
    tree = w.store.engine.value("outcomes", r3).output["nodes"]
    assert tree["yes"]["ways"] == [b2, r35] and tree["no"]["ways"] == []
    out = w.store.engine.value("impact", r3).output
    assert out["worlds"].keys() == {b2, r35, "no"} and out["baseline"] == "no"
    assert r25 not in out["worlds"]


def test_a_side_the_bands_do_not_make_up_whole_is_left_whole(w):
    """Above 2.5 watched with only the band (2.5, 3] beside it: nothing makes up its Yes side from 3 upward, so the
    side is one world; the baseline can be yes or no, and no band takes a shock as a world of it."""
    from harness.store import StoreError
    r25 = priced(w, rung(w, "above 2.5", ">", 2.5), 0.80)
    b1 = band(w, "(2.5, 3]", (">", 2.5), ("<=", 3.0))
    w.link("portfolio", "watches", r25)
    tree = w.store.engine.value("outcomes", r25).output["nodes"]
    assert tree["yes"]["ways"] == [] and tree["no"]["ways"] == []
    with pytest.raises(StoreError, match="is not a way of"):
        w.store.edit(r25, "baseline", b1)


def test_a_band_between_rungs_names_its_chance_given_the_rungs_about_it(w):
    """MATH.md 1.2: a band whose ends fall between rungs is open between what the rungs about it allow, and what would
    settle it is its chance given the rung below it and not the one above (rungs above 2.5,
    3 and 3.5 at 0.80, 0.45 and 0.12, a band from 2.7 to 3.2, open from 0 to 0.68)."""
    from harness.derive.structure import subject
    r25 = priced(w, rung(w, "above 2.5", ">", 2.5), 0.80)
    priced(w, rung(w, "above 3", ">", 3.0), 0.45)
    r35 = priced(w, rung(w, "above 3.5", ">", 3.5), 0.12)
    b = band(w, "(2.7, 3.2]", (">", 2.7), ("<=", 3.2))
    node = w.store.engine.value("structure", subject(w.store, b)).output["props"][b]
    assert node.get("p") is None and (round(node["open"]["lo"], 4), round(node["open"]["hi"], 4)) == (0.0, 0.68)
    assert node["open"]["settled_by"] == [[{"of": b, "given": [r25], "given_not": [r35]}]]
