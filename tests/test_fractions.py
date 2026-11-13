"""A shock as a fraction of the price (MATH.md 1.3; ONTOLOGY.md 2.2), and a store of a schema before this one brought to
it.

Schema 12 stated what an event does to a price as a percent of it: each shock and own part on a name or on a factor
quoted as its proxy's price, and each worth's return since. Schema 13 stated them as fractions, a factor's move beyond
the factors before it as a field of its own, `own_part`. A store of either is brought to this schema on opening
(Store._converted), a step at a time: each percent becomes the fraction it was written as, and each own part the shock
against what the factor follows, in the links as they stand and in the reverses a rewind runs; a figure on a factor
quoted as a yield stays in points of it. Its figures are then those of the same statements made in this schema, before
and after a rewind."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

import pytest

from harness import schema
from harness.derive import impact
from harness.store import OWN_PART_SCHEMA, PERCENT_SCHEMA, SCHEMA_VERSION, Store, StoreError
from test_impact import channel, event, holding_world, quoted, stated


def from_percent_cases():
    return [(-3.3, -0.033), (1.1, 0.011), (20, 0.2), (-100.0, -1.0), (0.04, 0.0004), (0, 0.0)]


@pytest.mark.parametrize("percent, fraction", from_percent_cases())
def test_a_percent_is_the_fraction_it_was_written_as(percent, fraction):
    assert schema.from_percent(percent) == fraction  # exactly: 1.1 / 100 in binary is 0.011000000000000001


def _write(w, k: float) -> dict:
    """One analysis, written in the terms `k` gives a price's figures (100 for percents, 1 for fractions), every figure
    no lower than -1 in either, so that this schema's store accepts both: two watched events with a shock on the
    market, an own part on oil, a shock and an own part on a factor quoted as a yield in its points, a shock on a name;
    a worth's return since and an amount on a proposition no ledger watches; then, past a checkpoint, a shock edited,
    a shock against what a factor follows edited, a worth's return edited, and a consequence removed, each leaving its
    reverse in the undo log."""
    s = w.store
    spy, oil, tlt = channel(w, "SPY"), channel(w, "USO"), quoted(w, "TLT", 4.2, 17.0)
    e, f = event(w, "E", p=0.3), event(w, "F", p=0.4)
    other = w.prop("G", chance={"value": 0.5}, baseline="no")
    ids = {
        "spy": s.link(e, "consequence", spy, {"shock": stated(0.2 * k)})["id"],
        "oil": s.link(e, "consequence", oil, {"shock": stated({"value": 0.05 * k, "against": schema.FOLLOWS})})["id"],
        "tlt": s.link(e, "consequence", tlt, {"shock": stated(0.5)})["id"],  # half a point of its yield
        "tlt_own": s.link(f, "consequence", tlt, {"shock": stated({"value": -0.25, "against": schema.FOLLOWS})})["id"],
        "nvda": s.link(f, "consequence", "name:NVDA", {"shock": stated(-0.005 * k)})["id"],
        "spy_f": s.link(f, "consequence", spy, {"shock": stated(0.08 * k)})["id"],
    }
    with s.transaction():  # values stated for a holding the ledger does not value, read as the store keeps them
        ids["since"] = s._insert_link("worth", other, "name:NVDA", {"world": "yes", "value": {
            "value": {"date": "2026-08-03", "return": 0.12 * k}, "note": "Its price before the event."}}, "analyst", None)
        ids["amount"] = s._insert_link("worth", other, "name:NVDA", {"world": "no", "value": {
            "value": {"amount": 30.0}, "note": "What it pays."}}, "analyst", None)
    s.checkpoint("turn 2")
    s.edit(ids["spy"], "shock", stated(0.3 * k))
    s.edit(ids["oil"], "shock", stated({"value": 0.06 * k, "against": schema.FOLLOWS}))
    with s.transaction():
        row = s._one("SELECT stated FROM link WHERE id = ?", ids["since"])
        changed = json.loads(row["stated"])
        changed["value"]["value"]["return"] = 0.15 * k
        s.db.execute("UPDATE link SET stated = ? WHERE id = ?", (json.dumps(changed, sort_keys=True,
                                                                            separators=(",", ":")), ids["since"]))
    s.remove(ids["spy_f"])
    return {"ids": ids, "events": (e, f)}


# How schemas 12 and 13 wrote a shock against what a factor follows: its own part, a field of its own. A stated
# figure is canonical JSON, its keys sorted, so the figure's text names `against` first; the same text stands in a
# link's stated fields and in each reverse the undo log holds.
FOLLOWED_TEXT = f'"shock":{{"against":"{schema.FOLLOWS}",'


def _older_store(tmp_path, schema_version: str, k: float, name: str) -> tuple:
    """The analysis written as an older schema wrote it, in `k`'s terms (100 for 12's percents), with each shock against
    what a factor follows as its own part, in the links and in the undo log alike; closed, and marked as of that
    schema."""
    w = holding_world(tmp_path / name, channels=("USO", "TLT"))
    made = _write(w, k)
    path, opened = w.store.dir, dict(registry=w.store.engine.registry, judge=w.judge, venues=w.venues,
                                     fetchers=w.store.fetchers)
    w.store.close()
    db = sqlite3.connect(path / "store.sqlite")
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (schema_version,))
    mark = db.execute("SELECT coalesce(max(n), 0) FROM undo").fetchone()[0]  # the file's triggers log this rewrite:
    db.execute("UPDATE link SET stated = replace(stated, ?, ?) WHERE instr(stated, ?) > 0",  # it is no statement, and
               (FOLLOWED_TEXT, '"own_part":{', FOLLOWED_TEXT))  # what they log of it goes
    db.execute("DELETE FROM undo WHERE n > ?", (mark,))
    db.execute("UPDATE undo SET statement = replace(statement, ?, ?)", (FOLLOWED_TEXT, '"own_part":{'))
    db.commit()
    db.close()
    return w, made, path, opened


def _percent_store(tmp_path) -> tuple:
    """The analysis written as schema 12 wrote it."""
    return _older_store(tmp_path, PERCENT_SCHEMA, 100.0, "percent")


def _stated(store) -> dict[str, dict]:
    return {r["id"]: json.loads(r["stated"]) for r in store._all("SELECT id, stated FROM link ORDER BY rowid")}


def _undo(path) -> dict[int, str]:
    db = sqlite3.connect(path / "store.sqlite")
    try:
        return dict(db.execute("SELECT n, statement FROM undo"))
    finally:
        db.close()


def _rows(store, events) -> dict:
    from harness.ledger import row
    keep = ("yes", "no", "full_impact", "volatility", "expected")
    return {e: {k: row(store, e)[k] for k in keep} for e in events}


def test_a_store_of_the_schema_before_this_one_is_brought_to_it_on_opening(tmp_path):
    """Each figure on a price becomes the fraction it was written as, in the links and in the undo log's reverses; a
    shock and an own part on the factor quoted as a yield, the worth's amount, every basis and when each figure was
    stated stay as they were; the conversion logs nothing a rewind would take back, and meta says when it was made."""
    w, made, path, opened = _percent_store(tmp_path)
    ids = made["ids"]
    before = {lid: json.loads(sqlite3.connect(path / "store.sqlite").execute(
        "SELECT stated FROM link WHERE id = ?", (lid,)).fetchone()[0]) for lid in ids.values() if lid != ids["spy_f"]}
    at_before = dict(sqlite3.connect(path / "store.sqlite").execute("SELECT id, stated_at FROM link"))
    undo_before = _undo(path)
    store = Store.open(path, **opened)
    try:
        assert store.meta("schema_version") == SCHEMA_VERSION
        converted = json.loads(store.meta("schema_converted"))
        assert (converted["from"], converted["to"]) == (PERCENT_SCHEMA, SCHEMA_VERSION) and converted["at"]
        now = _stated(store)
        assert now[ids["spy"]]["shock"]["value"] == 0.3 and now[ids["oil"]]["shock"]["value"] == 0.06
        assert now[ids["oil"]]["shock"]["against"] == schema.FOLLOWS and "against" not in now[ids["spy"]]["shock"]
        assert now[ids["nvda"]]["shock"]["value"] == -0.005
        assert now[ids["tlt"]]["shock"]["value"] == 0.5 and now[ids["tlt_own"]]["shock"] == {
            **before[ids["tlt_own"]]["own_part"], "against": schema.FOLLOWS}  # in points of its yield, as stated
        assert now[ids["since"]]["value"]["value"] == {"date": "2026-08-03", "return": 0.15}
        assert now[ids["amount"]] == before[ids["amount"]]
        for lid, was in before.items():  # the basis stays: only the figure changes, and an own part's field
            for k in ("shock", "own_part"):
                if k in was:
                    assert {x: y for x, y in now[lid]["shock"].items() if x not in ("value", "against")} == \
                           {x: y for x, y in was[k].items() if x != "value"}
        assert dict(store._all("SELECT id, stated_at FROM link")) == at_before
        undo = _undo(path)
        assert set(undo) == set(undo_before)  # the conversion is no statement
        changed = {n for n in undo if undo[n] != undo_before[n]}
        owned = {n for n in undo_before if '"own_part":{' in undo_before[n]}
        assert owned and owned <= changed  # each reverse holding an own part: now a shock against what it follows
        for n in owned:
            assert '"own_part":{' not in undo[n] and FOLLOWED_TEXT in undo[n]
        percents = changed - owned
        assert len(percents) == 3  # the shock's edit, the return's edit, and the removed consequence's row
        for n in percents:
            assert ("0.2" in undo[n] or "0.12" in undo[n] or "0.08" in undo[n]) and "20.0" not in undo[n]
        when = store.meta("schema_converted")
    finally:
        store.close()
    again = Store.open(path, **opened)  # converted once: opened again, it is as it was left
    try:
        assert _stated(again) == now and again.meta("schema_converted") == when
    finally:
        again.close()


def test_a_converted_store_reads_as_the_same_analysis_stated_as_fractions_before_and_after_a_rewind(tmp_path):
    """The converted store's rows are those of the same statements made as fractions, to the cent and closer; rewound
    past the edits and the removal, each reverse restores its figure as a fraction, and the rows again agree with the
    store stated as fractions and rewound the same way."""
    _, made, path, opened = _percent_store(tmp_path)
    twin = holding_world(tmp_path / "fraction", channels=("USO", "TLT"))
    twin_made = _write(twin, 1.0)
    store = Store.open(path, **opened)
    try:
        assert made["ids"] == twin_made["ids"] and made["events"] == twin_made["events"]
        assert _stated(store) == _stated(twin.store)
        got, want = _rows(store, made["events"]), _rows(twin.store, made["events"])
        for e in made["events"]:
            assert got[e] == pytest.approx(want[e], abs=1e-9)
        store.rewind("turn 2")
        twin.store.rewind("turn 2")
        assert _stated(store) == _stated(twin.store)
        assert _stated(store)[made["ids"]["spy"]]["shock"]["value"] == 0.2
        assert _stated(store)[made["ids"]["oil"]]["shock"] == {**_stated(store)[made["ids"]["oil"]]["shock"], "value": 0.05,
                                                               "against": schema.FOLLOWS}  # restored across both steps
        assert _stated(store)[made["ids"]["spy_f"]]["shock"]["value"] == 0.08
        assert _stated(store)[made["ids"]["since"]]["value"]["value"]["return"] == 0.12
        got, want = _rows(store, made["events"]), _rows(twin.store, made["events"])
        for e in made["events"]:
            assert got[e] == pytest.approx(want[e], abs=1e-9)
    finally:
        store.close()
        twin.store.close()


def test_a_reverse_the_converter_cannot_read_exactly_leaves_the_store_unopened_and_unchanged(tmp_path):
    """A reverse whose stated figures cannot be told from the rest of its statement is not guessed at: the store is
    not opened, and stays as the schema before this one wrote it."""
    _, _, path, opened = _percent_store(tmp_path)
    figure = json.dumps({"shock": stated(20.0)}, sort_keys=True, separators=(",", ":"))
    db = sqlite3.connect(path / "store.sqlite")
    db.execute("INSERT INTO undo (statement) VALUES (?)",  # the stated figure's text, written twice into one reverse
               (f"UPDATE link SET to_id = 'factor:1', stated = '{figure}', stated_at = '{figure}', origin = 'analyst', "
                f"created_call = NULL, created_at = '2026-11-12T00:00:00+00:00' WHERE rowid = 1",))
    db.commit()
    links = dict(db.execute("SELECT id, stated FROM link"))
    db.close()
    with pytest.raises(StoreError, match=f"cannot be read exactly to bring it to schema {SCHEMA_VERSION}"):
        Store.open(path, **opened)
    db = sqlite3.connect(path / "store.sqlite")
    try:
        assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == PERCENT_SCHEMA
        assert dict(db.execute("SELECT id, stated FROM link")) == links
    finally:
        db.close()


def test_a_kept_impact_made_before_the_fraction_is_placed_in_its_own_terms(tmp_path):
    """impact.placed computes a kept impact again from its own manifest: one made before FRACTION holds a price's
    shock, own part and a worth's return as percents, and places as the same manifest made as fractions would."""
    w = holding_world(tmp_path, channels=("USO", "TLT"))
    made = _write(w, 1.0)
    e = made["events"][0]
    found = w.store.engine.value("impact", e)
    kept = w.store.computation(found.basis)
    yields = kept["inputs"]["yields"]
    percent = lambda x: float(Decimal(repr(x)).scaleb(2))  # noqa: E731  as the analyst wrote a percent
    def as_written(c: dict) -> dict:  # a consequence as a manifest made before both wrote it
        figure = c["shock"] if c["target"] in yields else percent(c["shock"])
        rest = {k: v for k, v in c.items() if k not in ("shock", "against")}
        return {**rest, ("own_part" if c["against"] == schema.FOLLOWS else "shock"): figure}
    old = {**kept["inputs"], "consequences": [as_written(c) for c in kept["inputs"]["consequences"]]}
    assert [c.get("shock", c.get("own_part")) for c in old["consequences"]] == [30.0, 6.0, 0.5]  # the yield's in points
    assert impact.as_references(impact.as_fractions(old))["consequences"] == kept["inputs"]["consequences"]
    with w.store.transaction():
        old_id = w.store.put_computation("impact", impact.FRACTION - 1, e, "made-before-the-fraction", old,
                                         kept["output"], answer=False)
    weights = {x: (0.6 if x == "yes" else 0.4) for x in (wd["id"] for wd in kept["inputs"]["worlds"])}
    then, now = impact.placed(w.store, old_id, weights), impact.placed(w.store, found.basis, weights)
    for k in ("yes", "no", "full_impact", "volatility", "expected"):
        assert then[k] == pytest.approx(now[k], abs=1e-9)


def test_a_store_of_schema_13_brings_each_own_part_to_a_shock_against_what_the_factor_follows(tmp_path):
    """Schema 13 stated fractions, a factor's move beyond the factors before it as `own_part`. Opened, each own part
    becomes the shock against what the factor follows, its value and its basis as they were, in the links and in the
    undo log's reverses; every other figure stays; and the store reads as the same analysis made in this schema, before
    and after a rewind across the conversion."""
    _, made, path, opened = _older_store(tmp_path, OWN_PART_SCHEMA, 1.0, "own-part")
    ids = made["ids"]
    assert any('"own_part":{' in t for t in _undo(path).values())  # reverses that hold an own part, to convert
    twin = holding_world(tmp_path / "now", channels=("USO", "TLT"))
    twin_made = _write(twin, 1.0)
    store = Store.open(path, **opened)
    try:
        converted = json.loads(store.meta("schema_converted"))
        assert (converted["from"], converted["to"]) == (OWN_PART_SCHEMA, SCHEMA_VERSION)
        now = _stated(store)
        assert now[ids["oil"]]["shock"] == {"value": 0.06, "against": schema.FOLLOWS, "note": now[ids["oil"]]["shock"]["note"]}
        assert not any("own_part" in v for v in now.values())
        assert not any('"own_part":{' in t for t in _undo(path).values())
        assert made["ids"] == twin_made["ids"] and _stated(store) == _stated(twin.store)
        got, want = _rows(store, made["events"]), _rows(twin.store, made["events"])
        for e in made["events"]:
            assert got[e] == pytest.approx(want[e], abs=1e-9)
        store.rewind("turn 2")
        twin.store.rewind("turn 2")
        assert _stated(store) == _stated(twin.store)
        got, want = _rows(store, made["events"]), _rows(twin.store, made["events"])
        for e in made["events"]:
            assert got[e] == pytest.approx(want[e], abs=1e-9)
    finally:
        store.close()
        twin.store.close()
