"""A proposition's definition written whole (ONTOLOGY.md 2.1; TOOLS.md 5's edit): its title, text and the fields code
compares, given together as add gives them, so that a claim becomes another kind of claim in one statement. One field
at a time, an occurrence cannot become a threshold on a level: its metric must be an occurrence while it occurs, and a
threshold needs its metric and its value. Written whole, it keeps its chances, its links and its searches."""
from __future__ import annotations

import pytest

from breakdown import TARIFF, World
from harness import schema
from harness.store import StoreError
from harness.tools import Tools


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path / "scratch-definition")
    yield world
    world.store.close()


def test_an_occurrence_becomes_a_threshold_in_one_statement_and_keeps_what_it_rests_on(w):
    p = w.prop("Traffic recovers", "occurs", end="2027-03-31", chance={"value": 0.4})
    q = w.prop("Something broader", "occurs", end="2027-03-31", chance={"value": 0.6})
    link = w.link(p, "implies", q)
    together = r"its definition's fields change together with edit\(prop:1, \"definition\""
    with pytest.raises(StoreError, match=together):
        w.store.edit(p, "metric", TARIFF)  # one field at a time: an occurrence names an occurrence
    with pytest.raises(StoreError, match="names its metric and its value; its definition's fields change together"):
        w.store.edit(p, "comparison", ">")
    before = w.store.stated_of(p)
    w.store.edit(p, schema.DEFINITION_FIELD, {"title": "The rate above 25 on March 31",
                                              "text": "The effective tariff rate stands above 25 on March 31, 2027.",
                                              "metric": TARIFF, "comparison": ">", "value": 25.0,
                                              "window_end": "2027-03-31", "kind": "level_at_date"})
    after = w.store.stated_of(p)
    assert {k: after.get(k) for k in schema.DEFINITION} == {
        "title": "The rate above 25 on March 31", "text": "The effective tariff rate stands above 25 on March 31, 2027.",
        "metric": TARIFF, "comparison": ">", "value": 25.0, "upper_comparison": None, "upper_value": None,
        "window_start": None, "window_end": "2027-03-31", "kind": "level_at_date", "resolution_source": None}
    assert before.get("window_start") and "window_start" not in after  # written whole: a field left out is cleared
    assert after["chances"] == before["chances"]  # what it states of how likely it is stays
    assert [x["id"] for x in w.store.links_of(p, "implies")] == [link]
    assert w.point(p) == pytest.approx(0.4)


def test_a_definition_is_checked_as_add_checks_it_and_names_only_its_own_fields(w):
    p = w.prop("Traffic recovers", "occurs", end="2027-03-31", chance={"value": 0.4})
    whole = {"title": "T", "text": "T happens.", "comparison": "occurs", "window_end": "2027-03-31", "kind": "hitting"}
    with pytest.raises(StoreError, match="chances is not part of a proposition's definition"):
        w.store.edit(p, schema.DEFINITION_FIELD, {**whole, "chances": []})
    with pytest.raises(StoreError, match="Proposition requires window_end|window_end"):
        w.store.edit(p, schema.DEFINITION_FIELD, {k: v for k, v in whole.items() if k != "window_end"})
    with pytest.raises(StoreError, match="names its metric and its value"):
        w.store.edit(p, schema.DEFINITION_FIELD, {**whole, "comparison": ">"})
    with pytest.raises(StoreError, match="takes no source"):
        w.store.edit(p, schema.DEFINITION_FIELD, whole, "src:1")
    with pytest.raises(StoreError, match="a definition is a proposition's"):
        w.store.edit("metric:us_effective_tariff_rate_pct", schema.DEFINITION_FIELD, whole)
    assert w.store.stated_of(p)["title"] == "Traffic recovers"  # nothing refused was written


def test_the_tool_returns_its_structure_as_a_change_of_its_chances_does(w):
    p = w.prop("Traffic recovers", "occurs", end="2027-03-31", chance={"value": 0.4})
    ok, out = Tools(w).call("edit", {"id": p, "field": "definition", "value": {
        "title": "Traffic recovers by March", "text": "Traffic recovers by March 31, 2027.", "comparison": "occurs",
        "window_end": "2027-03-31", "kind": "hitting"}})
    assert ok, out
    assert out["stated"]["title"] == "Traffic recovers by March"
    assert out["propositions"][p]["probability"] == pytest.approx(0.4)
