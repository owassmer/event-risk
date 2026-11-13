"""A value worked out while another thread holds the store's write turn: its Computation is pending, read by
its id and its hash at once, and written by the keeper when the turn is free, in the one store file. Whatever the file
or the trace names is kept before it is named, so every name resolves, whatever becomes of the process; each answer is
written at the version it belongs to, never over a later one's."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from harness import render
from harness.derive.engine import canonical, text_hash
from harness.store import ID_BLOCK, Store, StoreError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "pending")
    yield s
    s.close()


class Writer:
    """Another thread holding the write turn, as a statement does, running what it is given while it holds it."""

    def __init__(self, store: Store):
        self.store = store
        self.jobs: list = []
        self.done = threading.Event()
        self.ready = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait()

    def _run(self) -> None:
        with self.store.lock:
            self.ready.set()
            while not self.done.is_set():
                self.wake.wait(0.05)
                self.wake.clear()
                while self.jobs:
                    job, out = self.jobs.pop(0)
                    try:
                        out["value"] = job()
                    except BaseException as e:  # noqa: BLE001
                        out["error"] = e
                    out["event"].set()

    def run(self, job):
        out = {"event": threading.Event()}
        self.jobs.append((job, out))
        self.wake.set()
        out["event"].wait(10)
        if "error" in out:
            raise out["error"]
        return out.get("value")

    def let_go(self) -> None:
        self.done.set()
        self.thread.join(10)


@contextmanager
def turn_held(store: Store):
    w = Writer(store)
    try:
        yield w
    finally:
        w.let_go()


def in_file(store: Store, cid: str) -> bool:
    db = store._own_db()
    return db.execute("SELECT 1 FROM computation WHERE id = ?", (cid,)).fetchone() is not None


def kept_soon(store: Store, cid: str) -> bool:
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if in_file(store, cid):
            return True
        time.sleep(0.02)
    return False


def ref(store: Store, cid: str) -> dict:
    return {"basis": cid, "sha256": text_hash(store.computation_text(cid))}


def test_a_value_worked_out_while_the_turn_is_held_is_read_at_once_and_the_keeper_writes_it_when_it_is_free(store):
    with turn_held(store):
        start = time.monotonic()
        cid = store.put_computation("d", 1, "s", "h1", {"a": 1}, {"x": 1})
        assert time.monotonic() - start < 0.5  # it did not wait for the statement
        assert cid.startswith("cmp:") and not in_file(store, cid)
        assert store.computation_by_hash("d", "h1") == {"id": cid, "output": {"x": 1}}
        assert store.computation(cid)["inputs"] == {"a": 1} and store.computation_output(cid) == {"x": 1}
        assert store.computation_of(cid) == {"derivation": "d", "subject": "s"}
        assert store.last_computation("d", "s")["id"] == cid  # the value's answer, pending with it
        assert store.get(cid)["output"] == {"x": 1} and store.type_of(cid) == "Computation"
        assert store.engine.read(ref(store, cid)) == {"x": 1}  # a manifest naming it loads it
    assert kept_soon(store, cid)
    row = store._own_db().execute("SELECT id, version FROM answer WHERE derivation = 'd' AND subject = 's'").fetchone()
    assert tuple(row) == (cid, store.version)
    assert store.computation(cid)["output"] == {"x": 1} and not store._pending_cmp


def test_a_chip_and_the_inspector_open_a_pending_working_by_its_value(store):
    with turn_held(store):
        cid = store.put_computation("impact", 1, "prop:1", "h2", {}, {"row": {}})
        reader = render.Reader(store, pending=True)
        assert render.working(reader, cid) == "raw:value:impact:prop:1"  # the figure's link, by its value
        chip = render.chips(store, [cid])[cid]
        assert chip.get("exists", True) and chip["label"]  # a chip in the conversation finds it
        assert store.get(cid)["inputs"] == {}  # the inspector's step reads it whole


def test_a_record_and_a_calls_result_keep_the_pending_computations_they_name_before_naming_them(store):
    with turn_held(store) as writer:
        a = store.put_computation("impact", 1, "prop:1", "ha", {}, {"v": 1})
        c = store.put_computation("distribution", 1, "prop:1", "hc", {"impact": ref(store, a)}, {"v": 2})
        assert not in_file(store, a) and not in_file(store, c)
        writer.run(lambda: store.put_record(session="s", turn=1, stopped=False, rows=[{"basis": c}], written={},
                                            texts={}, versions={}))
        assert in_file(store, c) and in_file(store, a)  # in the record's own write, what it read first
        named = store.put_computation("factors", 1, "portfolio", "hn", {}, {"v": 3})
        writer.run(lambda: store.name({"basis": named}))
        assert in_file(store, named)


def test_the_writers_computation_that_reads_a_pending_one_writes_it_in_its_transaction_and_keeps_it_on_a_rollback(store):
    with turn_held(store) as writer:
        a = store.put_computation("impact", 1, "prop:1", "ha", {}, {"v": 1})

        def write(fail: bool):
            with store.transaction():
                store.put_computation("distribution", 1, "prop:1", "hk" + str(fail), {"impact": ref(store, a)}, {"v": 2})
                if fail:
                    raise RuntimeError("the statement fails")

        with pytest.raises(RuntimeError):
            writer.run(lambda: write(True))
        # its Computations are kept by their content after the rollback (_keep_fetched): what one reads with it
        assert in_file(store, a)
        writer.run(lambda: write(False))
        assert in_file(store, a)


def test_an_answer_pending_at_a_version_is_never_written_over_a_later_ones(store):
    with turn_held(store) as writer:
        old = store.put_computation("impact", 1, "prop:1", "h-old", {}, {"v": "old"})  # answered at this version

        def later():  # a statement makes the next version, and the value is worked out there
            store.note("a later version")
            return store.put_computation("impact", 1, "prop:1", "h-new", {}, {"v": "new"})

        new = writer.run(later)
    assert kept_soon(store, old)
    row = store._own_db().execute("SELECT id, version FROM answer WHERE derivation = 'impact'").fetchone()
    assert row["id"] == new and row["version"] == store.version


def test_a_record_naming_a_pending_computation_whose_content_the_file_came_to_hold_reads_it_after_reopening(tmp_path):
    """The evaluation worker's case: a value worked out while the writer holds the turn is pending under its id; the
    writer keeps the same content under its own id inside its statement first; a record then names the pending one.
    The record is kept for good and scored against, so the id it names is a row of the file after any
    reopening: each content can stand under more than one id."""
    store = Store.create(tmp_path / "same")
    with turn_held(store) as writer:
        pending = store.put_computation("impact", 1, "prop:1", "same", {}, {"v": 1})

        def theirs():  # the same content kept under the writer's own id, inside its statement, first
            with store._transaction():
                store.db.execute("INSERT INTO computation (id, derivation, version, subject, input_hash, inputs, "
                                 "output, created_at) VALUES ('cmp:900000', 'impact', 1, 'prop:1', 'same', '{}', "
                                 "?, '2026-11-13T00:00:00Z')", (canonical({"v": 1}),))
            return store.put_record(session="s", turn=1, stopped=False, rows=[{"basis": pending}], written={},
                                    texts={}, versions={})
        writer.run(theirs)
        assert in_file(store, pending) and in_file(store, "cmp:900000")  # one content, under both ids
    store.close()
    again = Store.open(tmp_path / "same")
    try:
        named = again.records()[0]["rows"][0]["basis"]
        assert named == pending and again.computation(named)["output"] == {"v": 1}
    finally:
        again.close()


def test_a_store_whose_cache_held_each_content_under_one_id_is_rebuilt_keeping_every_computation(tmp_path):
    d = tmp_path / "older"
    s = Store.create(d)
    first = s.put_computation("d", 1, "s", "h1", {}, {"x": 1})
    second = s.put_computation("d", 1, "t", "h2", {}, {"x": 2})
    with s.lock, s._transaction():  # the table as an earlier store made it, each content under one id
        s.db.execute("ALTER TABLE computation RENAME TO c_new")
        s.db.execute("DROP TRIGGER IF EXISTS computation_immutable")
        s.db.execute("CREATE TABLE computation (id TEXT PRIMARY KEY, derivation TEXT NOT NULL, version INTEGER NOT "
                     "NULL, subject TEXT, input_hash TEXT NOT NULL, inputs TEXT NOT NULL CHECK (json_valid(inputs)), "
                     "output TEXT NOT NULL CHECK (json_valid(output)), created_at TEXT NOT NULL, "
                     "UNIQUE (derivation, input_hash))")
        s.db.execute("INSERT INTO computation SELECT * FROM c_new ORDER BY rowid")
        s.db.execute("DROP TABLE c_new")
    s.close()
    again = Store.open(d)
    try:
        sql = again._one("SELECT sql FROM sqlite_master WHERE name = 'computation'")["sql"]
        assert "UNIQUE" not in sql
        assert [r["id"] for r in again._all("SELECT id FROM computation ORDER BY rowid")] == [first, second]
        assert again.computation_by_hash("d", "h2") == {"id": second, "output": {"x": 2}}
        with pytest.raises(Exception, match="immutable"):
            with again.lock, again._transaction():
                again.db.execute("UPDATE computation SET output = '{}' WHERE id = ?", (first,))
    finally:
        again.close()


def test_retention_keeps_what_a_pending_computation_reads(store):
    kept = store.put_computation("impact", 1, "prop:1", "hb", {}, {"v": 1})  # the value's answer
    with turn_held(store) as writer:
        reader = store.put_computation("distribution", 1, "prop:1", "hr", {"impact": ref(store, kept)}, {"v": 3})
        writer.run(lambda: store.put_computation("impact", 1, "prop:1", "hb2", {}, {"v": 2}))  # kept is replaced
    # the writer's hold ended, and retention ran then: the pending one that reads it kept it
    assert kept_soon(store, reader) and in_file(store, kept)


def test_pending_ids_come_from_a_block_the_file_reserved_and_the_writers_after_it(store):
    first, end = store._ids
    assert end - first == ID_BLOCK
    with turn_held(store):
        cid = store.put_computation("d", 1, "s", "hp", {}, {"x": 1})
    assert first <= int(cid.split(":")[1]) < end
    assert kept_soon(store, cid)  # the keeper's write, before the writer's own below
    with store.lock:  # the writer's own, holding the turn: written at once in its transaction, as before
        mine = store.put_computation("d", 1, "s", "hw", {}, {"x": 2})
    assert in_file(store, mine) and int(mine.split(":")[1]) >= end


def answer_of(store: Store, derivation: str) -> tuple:
    return tuple(store._own_db().execute("SELECT id, cause, version FROM answer WHERE derivation = ?",
                                         (derivation,)).fetchone())


def test_an_answer_pending_at_a_version_leaves_one_the_file_holds_there_and_answers_one_read_as_waiting(store):
    """Two answers at one version are of one state. The keeper writing an answer pending at the version the file's
    answer is at leaves the file's where it names a Computation, which a reader may have been given, and replaces it
    where it names none: the value was read as waiting there, and the pending one is what it waited for. The keeper
    is not woken here, so each order is the one the test makes."""
    wake = store._keeper_wake.set
    store._keeper_wake.set = lambda: None
    with turn_held(store):
        early = store.put_computation("d", 1, "s", "h1", {}, {"x": 1})
    with store.lock:  # the writer's own, after it, at the same version
        mine = store.put_computation("d", 1, "s", "h2", {}, {"x": 2})
    version = store.version
    with store.lock:
        store._flush_pending()
    assert answer_of(store, "d") == (mine, None, version) and in_file(store, mine) and in_file(store, early)
    with store.lock, store._transaction():
        store._set_answer("w", "s", None, "waiting on a read")
    with turn_held(store):
        landed = store.put_computation("w", 1, "s", "h3", {}, {"x": 3})
    with store.lock:
        store._flush_pending()
    assert answer_of(store, "w") == (landed, None, store.version)
    store._keeper_wake.set = wake


def test_a_process_that_ends_with_values_pending_loses_cache_nothing_names_and_never_gives_their_ids_again(tmp_path):
    d = tmp_path / "ended"
    Store.create(d).close()
    script = f"""
import os, sys, threading
sys.path.insert(0, {str(ROOT)!r})
from harness.store import Store
s = Store.open({str(d)!r})
held = threading.Event()
threading.Thread(target=lambda: (s.lock.acquire(), held.set(), threading.Event().wait()), daemon=True).start()
held.wait()
print(s.put_computation("d", 1, "s", "lost", {{}}, {{"x": 1}}), flush=True)
os._exit(0)
"""
    lost = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60).stdout.strip()
    assert lost.startswith("cmp:")
    again = Store.open(d)
    try:
        assert again._own_db().execute("SELECT 1 FROM computation WHERE id = ?", (lost,)).fetchone() is None
        with pytest.raises(StoreError):
            again.computation(lost)  # it went with the process: nothing the file holds names it
        assert again._ids[0] > int(lost.split(":")[1])  # and its id is never given again
        assert int(again.put_computation("d", 1, "s", "next", {}, {"x": 2}).split(":")[1]) > int(lost.split(":")[1])
    finally:
        again.close()


def test_closing_the_store_writes_what_is_pending_where_the_turn_is_free(tmp_path):
    s = Store.create(tmp_path / "closing")
    s._wake_keeper = lambda: None  # no keeper: what is pending is written by the close alone
    with turn_held(s):
        cid = s.put_computation("d", 1, "s", "hc", {}, {"x": 1})
    assert not in_file(s, cid)
    s.close()
    again = Store.open(tmp_path / "closing")
    try:
        assert again.computation(cid)["output"] == {"x": 1}
    finally:
        again.close()
