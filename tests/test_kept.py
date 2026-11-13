"""What the cache keeps: a Computation is kept while it is how its value was last worked out, or while the
conversation, the record, or another kept Computation names it. One that a later Computation of its value has replaced
and that nothing names is removed when the read that replaced it ends, with what it alone read."""

from __future__ import annotations

import json
import sqlite3

from conftest import count
from harness.derive import Derivation, Registry
from harness.store import Store


def _words_inputs(ctx, nid):
    return {"text": ctx.object(nid)["stated"]["text"]}


def _twice_inputs(ctx, nid):
    return {"words": ctx.value("words", nid, ref=True)}


def _named_inputs(ctx, nid):
    return {"words": ctx.value("words", nid, ref=True)["basis"]}  # a Computation named by its id alone, as some do


WORDS = Derivation("words", 1, "Note", _words_inputs, lambda m, load: {"n": len(m["text"].split())})
TWICE = Derivation("twice", 1, "Note", _twice_inputs, lambda m, load: {"twice": 2 * load(m["words"])["n"]})
NAMED = Derivation("named", 1, "Note", _named_inputs, lambda m, load: {"of": m["words"]})


def toy(make_store, name="kept-test"):
    return make_store(name, registry_=Registry([WORDS, TWICE, NAMED]))


def read(store, nid):
    """A read as the page and a call make one: under the store's lock, ending when it lets go."""
    with store.lock:
        v = store.engine.value("twice", nid)
        return v.output["twice"], v.basis, store.engine.value("words", nid).basis


def ids(store):
    return {r[0] for r in store.db.execute("SELECT id FROM computation")}


def test_a_replaced_computation_goes_when_the_read_that_replaced_it_ends_and_what_it_alone_read_goes_with_it(
        make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    assert read(store, note)[0] == 6
    first = ids(store)
    store.edit(note, "text", "one two")
    with store.lock:  # inside the read, the one it replaced is still there for it
        v = store.engine.value("twice", note)
        assert v.output["twice"] == 4 and first <= ids(store)
    later = ids(store)
    assert not first & later and len(later) == 2  # replaced, with the words it alone read
    assert {r["derivation"] for r in map(store.computation, later)} == {"words", "twice"}
    assert count(store, "computation_read") == 1  # the one kept reads the one kept


def test_what_the_conversation_names_is_kept_with_everything_it_read(make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    _, twice, words = read(store, note)
    with store.lock:  # a call's result names it before the lock is let go
        store.name({"derived": {"twice": {"value": 6, "basis": twice}}})
    store.edit(note, "text", "one")
    assert read(store, note)[0] == 2
    kept = ids(store)
    assert {twice, words} <= kept and len(kept) == 4
    assert store.computation(twice)["output"] == {"twice": 6}


def test_a_computation_a_kept_one_names_by_its_id_alone_is_kept(make_store):
    """A manifest names a Computation however it writes its id, with its content's hash beside it or by the id alone:
    every one it writes is kept while it is kept, as every id the trace and the record write is."""
    store = toy(make_store)
    note = store.note("one two three")["id"]
    with store.lock:
        named = store.engine.value("named", note)
        words = named.output["of"]
        store.name({"derived": {"named": {"basis": named.basis}}})  # the conversation names the one that names it
    store.edit(note, "text", "one")
    read(store, note)  # the words of the new text replace the old
    kept = ids(store)
    assert {named.basis, words} <= kept
    assert store.computation(words)["output"] == {"n": 3}


def test_what_the_trace_names_is_kept_however_it_came_there(make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    _, twice, words = read(store, note)
    with open(store.trace_path, "a") as f:  # the analyst's reply quoting it
        f.write(json.dumps({"type": "assistant", "text": f"The figure is worked out in {twice}."}) + "\n")
        f.write(f'{{"type": "assistant", "text": "a line not yet whole names {words}')
    store.edit(note, "text", "one")
    read(store, note)
    kept = ids(store)
    assert twice in kept and words in kept  # the whole line's id, and what it read; the half line counts for nothing
    store2 = Store.open(store.dir, registry=Registry([WORDS, TWICE]))
    try:
        assert twice in store2._named and words not in store2._named
    finally:
        store2.close()


def test_what_the_record_names_is_kept_and_its_figures_open_to_what_produced_them(make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    _, twice, words = read(store, note)
    store.put_record(session="s", turn=1, stopped=False, rows=[{"id": note, "basis": {"twice": twice}}],
                     written={}, texts={}, versions={})
    for text in ("one", "one two", "three four five six"):
        store.edit(note, "text", text)
        read(store, note)
    kept = ids(store)
    assert {twice, words} <= kept and len(kept) == 4
    rec = store.records()[0]["rows"][0]["basis"]["twice"]
    assert store.engine.load({"basis": store.computation(rec)["inputs"]["words"]["basis"], "sha256": "x"}) == {"n": 3}


def test_a_computation_found_again_by_its_inputs_is_current_and_kept(make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    _, twice, _ = read(store, note)
    with store.lock:
        store.name({"basis": twice})
    store.edit(note, "text", "one")
    read(store, note)
    store.edit(note, "text", "one two three")
    assert read(store, note)[1] == twice  # its inputs again: the one kept answers
    store.edit(note, "text", "a b c d")
    read(store, note)
    assert twice in ids(store) and len(ids(store)) == 4  # the named pair, and the latest pair


def test_a_rewind_computes_again_what_the_cache_no_longer_holds(make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    read(store, note)
    store.checkpoint("turn:1")
    store.edit(note, "text", "one")
    assert read(store, note)[0] == 2
    store.rewind("turn:1")
    value, twice, _ = read(store, note)
    assert value == 6 and store.computation(twice)["output"] == {"twice": 6}


def test_the_current_computation_of_a_value_follows_it(make_store):
    store = toy(make_store)
    note = store.note("one two three")["id"]
    with store.lock:
        first = store.current_computation("twice", note)
    assert first["type"] == "Computation" and first["output"] == {"twice": 6}
    store.edit(note, "text", "one")
    with store.lock:
        now = store.current_computation("twice", note)
    assert now["output"] == {"twice": 2} and now["id"] != first["id"]
    assert first["id"] not in ids(store)


def test_a_store_made_before_the_rule_is_brought_under_it_at_its_first_open(make_store, tmp_path):
    store = toy(make_store, "kept-before")
    note = store.note("one two three")["id"]
    _, twice, words = read(store, note)
    store.put_record(session="s", turn=1, stopped=False, rows=[{"basis": twice}], written={}, texts={}, versions={})
    for text in ("one", "one two", "a b c d e"):
        store.edit(note, "text", text)
        read(store, note)
    latest = ids(store) - {twice, words}
    assert len(latest) == 2
    directory = store.dir
    store.close()
    # as a store made before the rule stood: no index of what each read, a trigger that refused every removal, and
    # every Computation it ever made
    db = sqlite3.connect(directory / "store.sqlite")
    db.execute("DROP TABLE computation_read")
    db.execute("DROP INDEX computation_value")
    db.execute("CREATE TRIGGER computation_kept BEFORE DELETE ON computation "
               "BEGIN SELECT RAISE(ABORT, 'the cache keeps every Computation'); END")
    seq = db.execute("SELECT n FROM seq WHERE prefix = 'cmp'").fetchone()[0]
    for k in range(5):  # earlier Computations of the note's words, which a page's minutes replaced and nothing names
        db.execute("INSERT INTO computation (rowid, id, derivation, version, subject, input_hash, inputs, output, "
                   "created_at) VALUES (?, ?, 'words', 1, ?, ?, '{}', ?, '2026-11-12T09:00:00+00:00')",
                   (-10 + k, f"cmp:{seq + 1 + k}", note, f"h{k}", json.dumps({"n": "x" * 100000})))
    db.execute("UPDATE seq SET n = ? WHERE prefix = 'cmp'", (seq + 5,))
    db.commit()
    db.close()
    big = (directory / "store.sqlite").stat().st_size
    opened = Store.open(directory, registry=Registry([WORDS, TWICE]))
    try:
        kept = ids(opened)
        # the latest pair, and the pair the record names; the five replaced ones are gone
        assert kept == {twice, words} | latest
        assert opened._one("SELECT 1 FROM sqlite_master WHERE name = 'computation_kept'") is None
        assert opened._one("SELECT count(*) AS n FROM computation_read")["n"] == 2
    finally:
        opened.close()
    assert (directory / "store.sqlite").stat().st_size < big  # the file gives back what they held


def _full(store):
    """The database's file can grow no further, as on a full disk: SQLite answers SQLITE_FULL."""
    pages = store.db.execute("PRAGMA page_count").fetchone()[0]
    store.db.execute(f"PRAGMA max_page_count = {pages}")


def test_a_write_the_file_cannot_hold_says_since_when_and_leaves_no_transaction_open(make_store):
    """A failed refresh keeps the last observation at its recorded time. A write SQLite cannot make, the disk
    full, fails the call, rolls back whole, and leaves the store saying since when and why it cannot be written; the
    next write that commits says it can again."""
    import pytest
    store = toy(make_store)
    assert store.unwritable is None
    _full(store)
    with pytest.raises(sqlite3.OperationalError):
        store.put_data("bars", "AAA", {"x": "y" * 200000}, {"url": "u"}, retrieved_at="2026-11-12T15:00:00+00:00")
    assert store.unwritable["why"] == "full" and store.unwritable["since"]
    assert not store.db.in_transaction and count(store, "data") == 0
    since = store.unwritable["since"]
    with pytest.raises(sqlite3.OperationalError):  # a second failure keeps the first's time
        store.put_data("bars", "BBB", {"x": "y" * 200000}, {"url": "u"}, retrieved_at="2026-11-12T15:01:00+00:00")
    assert store.unwritable["since"] == since
    store.db.execute("PRAGMA max_page_count = 1073741823")
    store.note("written again")
    assert store.unwritable is None


def test_a_write_a_rule_refuses_is_no_failure_to_write():
    from harness.store import unwritable
    assert unwritable(sqlite3.IntegrityError("a Computation is immutable")) is None
    assert unwritable(sqlite3.OperationalError("database or disk is full")) == "full"
    assert unwritable(sqlite3.OperationalError("disk I/O error")) == "io"
    assert unwritable(ValueError("disk I/O error")) is None


def test_a_commit_that_wrote_nothing_does_not_say_the_file_can_be_written(make_store):
    import pytest
    store = toy(make_store)
    _full(store)
    with pytest.raises(sqlite3.OperationalError):
        store.put_data("bars", "AAA", {"x": "y" * 200000}, {"url": "u"}, retrieved_at="2026-11-12T15:00:00+00:00")
    with store.transaction():  # a read inside a transaction: nothing written
        store.db.execute("SELECT count(*) FROM computation").fetchone()
    assert store.unwritable and store.unwritable["why"] == "full"
