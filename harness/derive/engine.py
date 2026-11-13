"""Derivations on read: the registry, manifests and input hashes, and the
cache of Computations and Judgments.

A derivation reads through `Inputs`, which returns each item with the ids that name it,
and returns its manifest: everything its compute reads. The manifest's hash is the
Computation's input hash. A Computation with that hash is the derived value; a new hash
computes and stores a new one. Nothing is invalidated: a changed input changes the hash.
The store keeps a Computation while it is the latest of its value, or while the conversation,
the record, or a kept Computation names it (Store._settle), so a structure that
returns to an earlier state is answered by the rows still kept, and computed again from the
data and Judgments otherwise. Judgments are asked while inputs are gathered,
so every answer a Computation used is in its manifest. A Judgment is keyed on its kind,
statement, and the content of its evidence, never on store ids, so the same question is
the same Judgment in any store; the model that answered is beside the key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from harness import schema, touches
from harness.judge import Judge, JudgeError, TooLong, state


log = logging.getLogger("harness.derive")  # a value that waits on a read that failed, for us: the analyst never sees it


class Unavailable(Exception):
    """The value does not exist: what it is derived from says there is none (an untraded metric's levels, a market
    with no price, a proposition nothing gives a point), or what it needs is data no client here reads, or a question
    no judge here answers. The value is absent: its fields are simply not there, with no reason given. The message is
    for the code's own record."""


class Refused(Unavailable):
    """The value cannot exist with the structure as the analyst stated it: a write that would make it so is refused,
    with this reason and the objects it names (TOOLS.md 5), by the derivation's own computation, so the store decides it
    by the same rule the value is derived by."""

    def __init__(self, message: str, named: list[str] | tuple[str, ...] = ()):
        super().__init__(message)
        self.named = tuple(named)


class Waiting(Exception):
    """The value waits: a read it needs has not landed, past the client's retries, or its inputs do not yet give it (a
    dividend whose rate is not yet read; a judge that did not answer). It is not stored, it is read again, and it
    carries no reason for the analyst; its cause is logged. A tool's call that needs it fails, with nothing written,
    and can be made again (TOOLS.md 5)."""


def canonical(obj: Any) -> str:
    """The one serialization every hash is taken over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def computation_hash(name: str, version: int, subject: str, manifest: dict) -> str:
    return digest({"derivation": name, "version": version, "subject": subject, "inputs": manifest})


def judgment_hash(kind: str, statement: str, evidence: list[dict], version: int) -> str:
    """A Judgment's key: its question and the state the judge reads, the evidence as plain text (judge.state), so
    that any change to what the judge is told asks again on its own."""
    return digest({"kind": kind, "statement": statement, "version": version, "state": state(evidence)})


def _always(store, entity_id: str) -> bool:
    return True


def _itself(store, entity_id: str) -> str:
    return entity_id


@dataclass(frozen=True)
class Derivation:
    """One derivation: what the store computes for one kind of object or link.

    `on` is the object type or relation whose instances carry its fields. `applies`
    says whether it holds for an instance; `subject_of` maps an instance to the subject
    it is computed for (a proposition maps to its component, for example); `inputs`
    reads the store through `Inputs` and returns the manifest; `compute` is a pure
    function of the manifest and of the immutable payloads it names; `pick` takes an
    instance's fields from a subject's output. `extra` gives fields to instances of other
    kinds from the same output, each as (kind, fields, pick): the holdings give the
    Portfolio V and each holds link its amounts. A producer names in `produces` the
    derived objects and links its output carries under "objects" and "links". A derivation with a
    `stage` is reported to the store when it computes and when it is done: the stages of a long read.
    """

    name: str
    version: int
    on: str
    inputs: Callable[["Inputs", str], dict]
    compute: Callable[[dict, Callable[[dict], Any]], dict]
    fields: tuple[str, ...] = ()
    applies: Callable[[Any, str], bool] = _always
    subject_of: Callable[[Any, str], str] = _itself
    pick: Callable[[dict, str], dict] | None = None
    produces: tuple[str, ...] = ()
    extra: tuple[tuple[str, tuple[str, ...], Callable[[dict, str], dict]], ...] = ()
    stage: str | None = None

    def gives(self, kind: str) -> tuple[tuple[str, ...], Callable[[dict, str], dict] | None] | None:
        """The fields this derivation gives an instance of `kind`, and how they are picked."""
        if kind == self.on:
            return self.fields, self.pick
        for other, fields, pick in self.extra:
            if other == kind:
                return fields, pick
        return None


# Producers give Names and the links that follow from holdings and betas. A factor's
# proxy Metric follows from its stated proxy, so the store derives it itself.
PRODUCIBLE = ("Name", "looks_through_to", "beta_to")


def _declared(on: str) -> tuple[str, ...]:
    """The fields a derivation on `on` may give: the declared derived fields, and for a
    Metric also its stated names, which a proxy Metric derives."""
    if on in schema.OBJECTS:
        t = schema.OBJECTS[on]
        if on == "Metric":
            return t.derived + tuple(f.name for f in t.stated)
        return t.derived
    return schema.RELATIONS[on].derived


class Registry:
    """The derivations of the store, each checked against the declaration."""

    def __init__(self, derivations=()):
        self._by_name: dict[str, Derivation] = {}
        for d in derivations:
            self.register(d)

    def register(self, d: Derivation) -> None:
        if d.name in self._by_name:
            raise ValueError(f"derivation {d.name} is registered")
        for kind, fields in [(d.on, d.fields)] + [(k, f) for k, f, _ in d.extra]:
            if kind not in schema.OBJECTS and kind not in schema.RELATIONS:
                raise ValueError(f"derivation {d.name} is on {kind!r}, which the declaration does not have")
            undeclared = [f for f in fields if f not in _declared(kind)]
            if undeclared:
                raise ValueError(f"derivation {d.name} gives {kind} fields the declaration does not derive: "
                                 f"{undeclared}")
        if any(p not in PRODUCIBLE for p in d.produces):
            raise ValueError(f"derivation {d.name} produces {d.produces}; producible: {PRODUCIBLE}")
        self._by_name[d.name] = d

    def get(self, name: str) -> Derivation:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"no derivation {name}") from None

    def on(self, kind: str) -> list[Derivation]:
        """The derivations that give fields to instances of `kind`."""
        return [d for d in self._by_name.values() if d.gives(kind) is not None]

    def producers(self) -> list[Derivation]:
        return [d for d in self._by_name.values() if d.produces]

    def versions(self) -> dict[str, int]:
        """Each derivation's version, by name."""
        return {name: d.version for name, d in sorted(self._by_name.items())}


@dataclass(frozen=True)
class Value:
    """A derived value: its output and its basis; or none, absent, or waiting on a read, with its cause for the
    log. With what it was worked out from (Engine._check):

    - `reads`: each key of the store its gathering read (harness.touches), with the stamp of the state its read
      answered from, (version, step);
    - `deps`: each value it read, by derivation and subject, with what it read of it (Engine._seen): its output, or
      its Computation where it read what that one read (Inputs.read_of);
    - `waited`: whether any part of it met a read that waited, whether or not it was caught;
    - `verified`: the stamp of the store at which it was last found current, every key and value it read as it read
      them."""
    output: dict | None
    basis: str | None
    cause: str | None = None
    waiting: bool = False
    refused: tuple[str, ...] | None = None  # where the structure itself leaves no value: the objects the reason names
    reads: dict = field(default_factory=dict, compare=False)
    deps: tuple = field(default=(), compare=False)
    waited: bool = False
    verified: tuple[int, int] = (-1, -1)


def _ref(kind: str, subject: str, covering: tuple[str, str] | None, row: dict | None) -> dict:
    """A data read as a manifest names it: the row read, by id and hash, or none with what was asked."""
    if row is None:
        return {"data": None, "kind": kind, "subject": subject, "covering": list(covering) if covering else None}
    return {"data": row["id"], "kind": kind, "subject": subject, "range": [row["range_from"], row["range_to"]],
            "retrieved_at": row["retrieved_at"], "sha256": row["sha256"]}


class Inputs:
    """What a derivation reads. Each method returns the item with the ids that name it,
    for the derivation to put in its manifest."""

    def __init__(self, engine: "Engine"):
        self._engine = engine
        self._store = engine.store
        # each value read while gathering, by derivation and subject, with what was read of it (Value.deps): every
        # value the engine gives this thread while this gathering is innermost (Engine.value), whoever asked for it
        self.deps: dict[tuple[str, str], tuple] = {}
        self.waited = False  # whether any part of it met a read that waited (Value.waited)

    def _met(self, key: tuple[str, str], v: "Value", how: str = "output") -> None:
        """A value read: what this one is worked out from, as it was read, the first reading of it standing."""
        self.deps.setdefault((key, how), Engine._seen(v, how))
        self.waited = self.waited or v.waited or v.waiting

    @contextmanager
    def _waits(self):
        """A read that waits, here or in a value it reads: the value met a wait, whether or not it catches it."""
        try:
            yield
        except Waiting:
            self.waited = True
            raise

    def object(self, oid: str) -> dict:
        described = self._store.describe(oid)
        if described is None:
            raise Unavailable(f"{oid} is not in the store")
        return described

    def links(self, oid: str, relation: str | None = None, direction: str = "out") -> list[dict]:
        return self._store.links_of(oid, relation, direction)

    def stated_at(self, oid: str, field: str) -> str | None:
        """When a stated field of an object or a link was last written (ONTOLOGY.md 2); a derivation that reads it puts it in
        its manifest."""
        return self._store.stated_at(oid, field)

    def link(self, lid: str) -> dict:
        found = self._store.link_by_id(lid)
        if found is None:
            raise Unavailable(f"{lid} is not in the store")
        return found

    def objects(self, type_name: str) -> list[dict]:
        """The stated objects of a type, in the order they were created."""
        return [self.object(oid) for oid in self._store.ids_of(type_name) if self._store.describe(oid)]

    def value(self, name: str, subject: str, *, ref: bool = False) -> dict:
        """An upstream derived value with its basis. With `ref`, the manifest names the
        output by its Computation and hash, and compute loads it."""
        with self._waits():
            v = self._engine.value(name, subject)  # read as this value's own (Engine.value: Inputs._met)
        if v.waiting:
            raise Waiting(v.cause)
        if v.output is None:
            raise Unavailable(v.cause)
        if ref:
            return {"derivation": name, "subject": subject, "basis": v.basis, "sha256": digest(v.output)}
        return {"derivation": name, "subject": subject, "basis": v.basis, "output": v.output}

    def read_of(self, ref: dict) -> dict:
        """What an upstream value read: the manifest of the Computation `ref` names (`value(..., ref=True)`), for a
        derivation that reads what another read. It is pinned by that Computation, which the manifest that holds `ref`
        names, so the store keeps it while this one is kept. What it reads is that Computation itself: a value worked
        out again to the same output from other reads is another account, and this one reads it again."""
        self._met((ref["derivation"], ref["subject"]), Value(None, ref["basis"]), "manifest")
        return self._store.computation(ref["basis"])["inputs"]

    def data(self, kind: str, subject: str, *, covering: tuple[str, str] | None = None,
             required: bool = True, at: str | None = None) -> dict | None:
        """The newest data of `kind` for `subject` covering the range; with `at`, the reading the store held at
        that moment, or the first where `at` is empty. The store records what it read, found or not, absent or
        waiting, as of its own landing where it lands what it reads (harness.touches.answering)."""
        with self._waits():
            row = self._store.data(kind, subject, covering=covering, required=required, at=at)
        return _ref(kind, subject, covering, row)

    def readings(self, kind: str, subject: str) -> list[dict]:
        """Every reading of `kind` the store keeps for `subject`, oldest first, each as `data` names one."""
        rows = self._store.readings(kind, subject)
        return [_ref(kind, subject, None, row) for row in rows]

    def data_many(self, kind: str, subjects, *, covering: tuple[str, str] | None = None,
                  required: bool = True) -> dict[str, dict]:
        """`data` for many subjects of one kind over one range, keyed by subject in the order given: each subject's
        answer is exactly what `data` gives it, and what the store holds is found at once (Store.data_many). Every
        subject asked is recorded as read as of the batch's landing (harness.touches.answering)."""
        subjects = list(dict.fromkeys(subjects))
        with self._waits():
            rows = self._store.data_many(kind, subjects, covering=covering, required=required)
        return {s: _ref(kind, s, covering, rows.get(s)) for s in subjects}

    def prefetch(self, kind: str, subjects, covering: tuple[str, str] | None = None, *, stage: str | None = None,
                 about: str | None = None, wait: bool = True, later: bool = False) -> None:
        """Fetch many subjects' data side by side before reading them one by one with `data`; it reads nothing
        into the manifest, which records what `data` then reads. A `stage` names the work, reported as it goes.
        With `wait` False the reads are started and it returns, for a read that what is being read now does not
        need: `data` waits for it where it is asked (Store.prefetch); `later`, for reads another derivation will ask
        for, which the reads asked for now never wait behind."""
        with self._waits():
            self._store.prefetch(kind, list(subjects), covering, wait=wait, later=later,
                                 stage=None if stage is None else {"stage": stage, **({"about": about} if about else {})})

    def read_again(self, kind: str, subjects, covering: tuple[str, str] | None = None) -> None:
        """Read these subjects' data from their source again, whatever is held, for a read whose answer no longer
        resolves to what it names; `data` then reads the newest."""
        with self._waits():
            self._store.read_again(kind, list(subjects), covering)

    def payload(self, ref: dict) -> Any:
        """The payload a manifest item names, for an inputs function whose next read depends
        on it. The item is in the manifest, so what was read is pinned by its hash. Fetched data is kept parsed
        (Engine.read): read-only, like everything a derivation is given."""
        return self._engine.read(ref, keep=True)

    def source(self, sid: str) -> dict:
        s = self._store.source_row(sid)
        if s is None:
            raise Unavailable(f"{sid} is not in the store")
        return {"source": sid, "url": s["url"], "dated": s["dated"], "sha256": s["sha256"]}

    def ask(self, kind: str, statement: str, evidence: list[dict], *, version: int = 1) -> dict:
        with self._waits():  # a question not yet answered waits; once answered, its answer stands
            return self._engine.ask(kind, statement, evidence, version=version)

    def ask_all(self, questions: list[tuple], evidence: list[dict], *, version: int = 1) -> list[dict]:
        with self._waits():
            return self._engine.ask_all(questions, evidence, version=version)

    def watched(self, pid: str) -> bool:
        return self._store.watched(pid)

    def given_by(self, pid: str) -> list[tuple[str, int]]:
        """Each chance given pid: the proposition it is the chance of, and its place in that one's chances."""
        return self._store.given_by(pid)

    def given_at(self, lid: str) -> str | None:
        """When a holding was given: when its link was made, or its figure last edited."""
        return self._store.given_at(lid)


RETRY = 60.0  # seconds a value that waits is kept for the page before it is read again: the keyless sources' cooldown
# Characters of fetched payload kept parsed for the inputs functions, which run again on every pass of a read (a read
# that lands touches what read it) and read a fund's holdings and report each time: a broad fund's are a
# megabyte or two of text, about 2.7 times that parsed, so this keeps a portfolio of a couple of dozen broad funds
# whole, about 170 MB at most. What is read once, a computation's histories, is parsed where it is read.
PARSED_TEXT = 64_000_000
APART = 2  # the computations made at once without the store's lock: the first read's fits, one store's at a time


class HeldRead:
    """A page's read under Engine.last_where_waiting: whether a value it would work out under the store's lock is read
    as last worked out too (`inline`), and whether it held one (`unworked`); whether a value it held or found waiting
    is being worked out apart; and each
    value it read as last worked out, or computed from one, which no call ever reads (Value.waited): read once within
    the read, and kept for the page's next read while nothing it rests on can have changed (Engine._page)."""

    def __init__(self, inline: bool):
        self.inline = inline
        self.later = False
        self.values: dict[tuple[str, str], "Value"] = {}  # what it read as last worked out, or from it: once a read
        self.apart: set[tuple[str, str]] = set()  # the values whose read met a computation under way, at any depth
        # the values it held, or read from one held, that a read working out inline values would work out: this read's,
        # and those the page's last read held, which this one reads again from it
        self.unworked: set[tuple[str, str]] = set()
        self.kept_unworked: set[tuple[str, str]] = set()


class Engine:
    """Computes derived values on read and keeps the cache.

    A value is kept while nothing it read has changed (ONTOLOGY.md 2): each key of the store its gathering read, with the state
    its read answered from, untouched since (Store.touched_at, harness.touches), and each value it read giving what it
    gave (_check). A write costs what it changes: what read nothing it touched stands, and a value worked out again to
    the same output disturbs nothing that read it. A value worked out inside a transaction stands at its commit where
    nothing touched its reads after it read them, and goes with a rollback (committed, rolled_back).

    A value that waits is kept for RETRY seconds for the page, which shows its placeholder meanwhile; a tool's call is
    strict (`strict`): it reads such a value again, and where it still waits, the call fails, nothing written.

    A derivation reported as a stage of a long read (the exposures' fits) is computed apart, holding no lock, so that
    no call and no render of the page waits on a computation it does not need (TOOLS.md 5). Its manifest is read, and
    every payload it names loaded, under the store's lock; its compute, a pure function of them, then runs on a
    thread of its own that reads nothing of the store; the next read of the value under the lock keeps what it gave
    as the Computation of those inputs' hash. A call that needs it meanwhile stands aside for it, holding no lock, as
    for a read under way (Store.standing_aside); the page reads it as waiting, its placeholder shown, and reads it
    again on its next render; a second reader of the same inputs finds the one under way, so nothing is computed
    twice; and a value is kept only by a reader holding the lock, for the inputs it read itself. A read inside an open transaction computes as before, holding the lock: the state
    it computes from exists only in that transaction, which no other thread may enter."""

    def __init__(self, store, registry: Registry, judge: Judge | None = None):
        self.store = store
        self.registry = registry
        self.judge = judge
        self._memo: dict[tuple[str, str], Value] = {}
        # the producers' products, with what they were computed from (a Value's record): every producer's under
        # None, and those of some producers alone by what they give
        self._products: dict[frozenset | None, tuple[dict, Value]] = {}
        # the values worked out or found current inside the open transaction, by key (committed, rolled_back)
        self._in_tx: set[tuple] = set()
        self._hashes: OrderedDict[str, str] = OrderedDict()  # a Computation's output's hash, by its id (_output_hash)
        self._local = threading.local()  # each thread's own read: the values it is computing, further up its read
        self._producing = False
        self._apart: ThreadPoolExecutor | None = None
        self._running: dict[tuple[str, str], Future] = {}  # the computations made apart, by derivation and inputs' hash
        self._apart_of: dict[tuple[str, str], tuple] = {}  # what each was made of: its derivation, subject and manifest
        self._running_lock = threading.Lock()
        self._expires: dict[tuple[str, str], float] = {}  # when each value that waits is read again
        # the values the page last read as last worked out, or from them, with the state they were read at: the store's
        # stamp, how many Computations were made since, and whether its read held inline values
        self._page: tuple[tuple | None, dict, set] = (None, {}, set())
        self._made = 0
        self._calls = threading.local()  # a tool's call is strict on its own thread: the page's reads stay as they are
        # fetched data kept parsed, by its id and the hash of its content (read), least recently read first
        self._parsed: OrderedDict[tuple[str, str], tuple[Any, int]] = OrderedDict()
        self._parsed_size = 0
        self._parsed_lock = threading.Lock()

    @property
    def _stack(self) -> list[tuple[str, str]]:
        """The values this thread's read is computing: another thread's are its own read's, never a cycle in this one."""
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        return stack

    def close(self) -> None:
        if self._apart is not None:
            self._apart.shutdown(wait=False, cancel_futures=True)

    def reset(self) -> None:
        self._clear()

    def _clear(self) -> None:
        self._memo.clear()
        self._expires.clear()
        self._products.clear()
        self._in_tx.clear()
        self._page = (None, {}, set())

    @property
    def _gathering(self) -> list:
        """The gatherings open on this thread, innermost last: each value the engine gives the thread is read by the
        innermost (Inputs._met). A check of what a value read opens none (_aside), so what it finds is no one's read."""
        open_ = getattr(self._local, "gathering", None)
        if open_ is None:
            open_ = self._local.gathering = []
        return open_

    def _depend(self, key: tuple, v: Value) -> None:
        open_ = self._gathering
        if open_ and open_[-1] is not None:
            open_[-1]._met(key, v)

    @contextmanager
    def _aside(self):
        """A check of what a value read, read as no gathering's: the values it reads and the keys their reads record
        belong to no value being gathered on this thread."""
        self._gathering.append(None)
        try:
            with touches.recording(self.store.stamp):
                yield
        finally:
            self._gathering.pop()

    @staticmethod
    def _seen(v: Value, how: str = "output") -> tuple:
        """What a reader read of a value: its Computation, by id; or, where it has none, that it was absent, refused or
        waiting, with its cause. `how` is what of it the reader read: its output, or what its Computation read."""
        if v.basis is not None:
            return ("basis", v.basis)
        return ("none", v.cause, v.waiting, v.refused)

    def _same(self, then: tuple, v: Value, how: str) -> bool:
        """Whether a value gives a reader what it gave it when it was read (_seen): the same Computation; or, for a
        reader of its output, another whose output is the same, worked out again from reads that changed and coming to
        it again, which disturbs no one that read it."""
        now = self._seen(v, how)
        if now == then:
            return True
        if how != "output" or then[0] != "basis" or now[0] != "basis":
            return False
        a, b = self._output_hash(then[1]), self._output_hash(now[1])
        return a is not None and a == b

    def _output_hash(self, cid: str) -> str | None:
        """The hash of a Computation's output, by its id: a Computation is never changed, so it is kept once read.
        None for what is no Computation the store holds."""
        if not str(cid).startswith("cmp:"):
            return None
        found = self._hashes.get(cid)
        if found is None:
            try:
                found = text_hash(self.store.computation_text(cid))
            except Exception:  # noqa: BLE001 - no longer kept: no other Computation is it
                return None
            self._hashes[cid] = found
            if len(self._hashes) > 4096:
                self._hashes.popitem(last=False)
        return found

    def _fresh(self, v: Value, stamp: tuple[int, int]) -> bool:
        """Whether every key the value read answers now as it did (ONTOLOGY.md 2: a derived field is computed again when
        anything it read changes): touched at no version after the one it was read at, or after the one the value was
        last found current at; and, inside a transaction, at no step after the one it was read or found current at."""
        version, step = stamp
        at, at_step = v.verified
        touched_at = self.store.touched_at
        log = self.store.transaction_touched() if step else None
        every = log.get(touches.EVERYTHING, 0) if log else 0
        since = at_step if at == version else 0
        for k, (e, s) in v.reads.items():
            if touched_at(k) > max(e, at):
                return False
            if log and max(log.get(k, 0), every) > max(s, since):
                return False
        return True

    def _check(self, key: tuple, v: Value) -> Value | None:
        """The value found current at the store's state now, or None where it is not (ONTOLOGY.md 2): every key it read
        answers as it did (_fresh), and every value it read gives what it gave (_same), each of those found current or
        worked out again first. So a value worked out again to the same output disturbs none that read it. Found
        current, it is marked so at this state, where its next check ends at once. A value that waits is current at
        the state it was worked out at alone, and read again at the next: what it waits on can land unnamed. A value
        it read that is held as last worked out, or was worked out around a wait, is no state's value, and vouches for
        none: the page's read, which holds such values while the analyst works, never finds what read them current."""
        stamp = self.store.stamp()
        if v.verified == stamp:
            return v
        if v.waiting and v.verified != (-1, -1):
            return None  # a value that waits may wait on what no key it read names, a read started ahead: any version
        if not self._fresh(v, stamp):
            return None
        if v.deps:
            with self._aside():
                for (dkey, how), then in v.deps:
                    if dkey[0] == "products":
                        found = self._products_for(dkey[1], exact=True)
                        u = found[1] if found is not None else None
                    else:
                        u = self.value(*dkey)
                    if u is None or (u.waited and not u.waiting) or not self._same(then, u, how):
                        return None
            if self.store.stamp() != stamp:  # a value it read was worked out again and landed what it read
                stamp = self.store.stamp()
                if not self._fresh(v, stamp):
                    return None
        if self.store.in_transaction():
            self._in_tx.add(key)
        return replace(v, verified=stamp)

    def committed(self, touched: dict[str, int], before: int, version: int | None) -> None:
        """The open transaction committed (Store._transaction), from version `before`, as `version` where it touched
        anything, each key at the step it last touched it (`touched`): each value worked out or found current inside it
        stands where no key it read was touched at a later step than it read it, and then reads as of that version; any
        other is worked out again when next read."""
        held, self._in_tx = self._in_tx, set()
        every = touched.get(touches.EVERYTHING, 0)
        for key in held:
            v = self._held(key)
            if v is None:
                continue
            since = v.verified[1] if v.verified[0] == before else 0
            if any(max(touched.get(k, 0), every) > max(s, since) for k, (_, s) in v.reads.items()):
                self._drop(key)
                continue
            reads = {k: ((version, 0) if s and version else (e, 0)) for k, (e, s) in v.reads.items()}
            self._keep(key, replace(v, reads=reads, verified=(-1, -1)))

    def rolled_back(self) -> None:
        """The open transaction rolled back (Store._rolled_back): each value worked out from what it wrote goes, and
        each found current inside it is checked again when next read; every other stands, the store being as it was."""
        held, self._in_tx = self._in_tx, set()
        for key in held:
            v = self._held(key)
            if v is None:
                continue
            if any(s for _, s in v.reads.values()):
                self._drop(key)
            else:
                self._keep(key, replace(v, verified=(-1, -1)))
        self._page = (None, {}, set())

    def _held(self, key: tuple) -> Value | None:
        if key[0] == "products":
            found = self._products.get(key[1])
            return found[1] if found is not None else None
        return self._memo.get(key)

    def _keep(self, key: tuple, v: Value) -> None:
        if key[0] == "products":
            found = self._products.get(key[1])
            if found is not None:
                self._products[key[1]] = (found[0], v)
        else:
            self._memo[key] = v

    def _drop(self, key: tuple) -> None:
        if key[0] == "products":
            self._products.pop(key[1], None)
        else:
            self._memo.pop(key, None)
            self._expires.pop(key, None)

    @contextmanager
    def strict(self):
        """A tool's call: every value it reads completes, or the call fails. A value that waited before the call is
        read again, once in the call, and one that still waits raises Waiting out of the read."""
        if not self._strict:
            self._calls.read = set()  # the values that waited and were read again in this call
        self._calls.depth = self._strict + 1
        try:
            yield
        finally:
            self._calls.depth -= 1

    @contextmanager
    def held_only(self):
        """The page's reads from what the store holds alone (render.cache_only), on this thread. What a read here
        waits for is what a call would read: so each value the page read or computed that met a wait, at any depth
        (Value.waited), is dropped when the page's read ends, and the next read with the judge and the clients works
        out what this one could not. Every other value read only what the store holds and is what any reader would
        compute (_current): it is kept."""
        saved = getattr(self._local, "held", None)
        self._local.held = here = set()
        try:
            yield
        finally:
            self._local.held = saved
            for key in here:
                if key[0] == "products":
                    found = self._products.get(key[1])
                    if found is not None and found[1].waited:
                        del self._products[key[1]]
                    continue
                v = self._memo.get(key)
                if v is not None and v.waited:
                    del self._memo[key]
                    self._expires.pop(key, None)

    def _held_here(self, key) -> None:
        here = getattr(self._local, "held", None)
        if here is not None:
            here.add(key)

    @contextmanager
    def placeholders(self):
        """The page's reads on this thread: a value computed apart that is under way is read as waiting, its
        placeholder shown, never awaited. Every other reader that is not a call standing aside has the value."""
        saved = getattr(self._local, "placeholders", False)
        self._local.placeholders = True
        try:
            yield
        finally:
            self._local.placeholders = saved

    @contextmanager
    def last_where_waiting(self, *, inline: bool = True):
        """The page's reads on this thread: a value the read cannot give at the store's newest state without waiting,
        on a read left to the analyst's turn or on a computation made apart that is under way, gives the Computation
        that last worked it out (Store.last_computation); with `inline`, so does one it would work out here, under the
        store's lock (render.cache_only, while the analyst works, and a view's first read between replies, which says
        so, `unworked`, and is read again working them out). So a figure shown stays until its new value is ready on the
        page, and a placeholder stands only for a value never worked out. Each figure so read is complete as it was last
        worked out; values read together can come from different earlier moments. A held value carries `waited`, so the
        engine's memo never keeps it (held_only), and it is never given to a call or inside a transaction: what the
        analyst is returned is the value at the newest state, or the call fails: a successful result is the true state.
        The page's next read takes it again while nothing it rests on has changed (_page). The read it yields says
        whether a value it held or found waiting is being worked out apart (`later`), so the page reads again once it is
        ready."""
        saved = getattr(self._local, "last", None)
        self._local.last = read = HeldRead(inline)
        stamp = self._page_stamp(inline)
        if self._page[0] == stamp:  # nothing it rests on has changed: read as the page's last read held it
            read.values.update(self._page[1])
            read.kept_unworked = self._page[2]
        try:
            yield read
        finally:
            self._local.last = saved
            if self._page_stamp(inline) == stamp and not self.store.in_transaction():
                kept = {k: v for k, v in read.values.items() if k not in read.apart}
                self._page = (stamp, kept, (read.unworked | read.kept_unworked) & kept.keys())

    def page_read(self) -> HeldRead | None:
        """The page's read under way on this thread (last_where_waiting), or None: what it met worked out apart."""
        return getattr(self._local, "last", None)

    def _page_stamp(self, inline: bool) -> tuple:
        """What a value the page read as last worked out rests on: no version of the store and no Computation made since
        (a store's one engine makes each of its Computations), so none it held has a Computation at the newest state."""
        return self.store.stamp(), self._made, inline

    def _last(self, d: Derivation, subject: str, *, inline: bool = False) -> Value | None:
        """The value as last worked out, where this read is the page's under `last_where_waiting` (for a value it would
        work out here, one with `inline`), outside a call and a transaction, and its last Computation is of the
        derivation's current version, whose output has its shape."""
        read = getattr(self._local, "last", None)
        if read is None or (inline and not read.inline) or self._strict or self.store.in_transaction():
            return None
        found = getattr(self.store, "last_computation", None)
        last = found(d.name, subject) if found is not None else None
        if last is not None and last.get("id") is None:  # last read absent: none, as it last was
            return Value(None, None, last.get("absent"), waited=True)
        if last is None or last.get("version") != d.version:
            return None
        return Value(last["output"], last["id"], f"{d.name}({subject}) as last worked out", waited=True)

    def _later(self) -> None:
        read = getattr(self._local, "last", None)
        if read is not None:
            read.later = True

    @property
    def _strict(self) -> int:
        return getattr(self._calls, "depth", 0)

    def pending(self, name: str, subject: str) -> bool:
        """Whether the value waits on a read: the page shows its placeholder."""
        v = self._memo.get((name, subject))
        return bool(v and v.waiting)

    # Whether a payload kept parsed is checked against its hash each time it is handed out again: the tests run with it,
    # so that a derivation that changes what it reads fails where it is read next, naming it.
    check_reads = False

    def read(self, ref: dict, keep: bool = False) -> Any:
        """The payload a manifest item names, as `load` gives it; fetched data from what is kept parsed where it is,
        and, with `keep`, kept once parsed; a Computation's output always kept once parsed, since every value above it
        reads it again. Fetched data is kept by its id and the hash of its content: a transaction rolled back gives its
        ids again, to whatever is read next, so an id alone does not name a content. A Computation's output by its
        content's hash alone, which its ref carries: two Computations of one content give one output. What it gives is
        shared by every reader, so it is read-only: nothing a derivation reads is changed by it (check_reads)."""
        did, sha = ref.get("data"), ref.get("sha256")
        cid = None if did else ref.get("basis")
        if not (did or cid) or not sha:
            return self.load(ref)
        keep = keep or bool(cid)
        key = (did or "output", sha)
        with self._parsed_lock:
            held = self._parsed.get(key)
            if held is not None:
                self._parsed.move_to_end(key)
        if held is not None:
            if self.check_reads and text_hash(canonical(held[0])) != sha:
                raise RuntimeError(f"{did or cid} was changed after it was read: what a derivation reads is read-only")
            return held[0]
        if cid:  # a Computation is never changed: its id names its output
            text = self.store.computation_text(cid)
        else:
            text = self.store.data_text(did)
            if text_hash(text) != sha:
                raise RuntimeError(f"{did} is not the content its manifest names")
        got = json.loads(text)
        if keep and len(text) <= PARSED_TEXT:
            with self._parsed_lock:
                if key not in self._parsed:
                    self._parsed[key] = (got, len(text))
                    self._parsed_size += len(text)
                while self._parsed_size > PARSED_TEXT:
                    _, (_, n) = self._parsed.popitem(last=False)
                    self._parsed_size -= n
        return got

    def load(self, ref: dict) -> Any:
        """The immutable payload a manifest item names: fetched data, a page's text, or a
        Computation's output."""
        if ref.get("data"):
            return self.store.data_payload(ref["data"])
        if ref.get("source"):
            return self.store.source_row(ref["source"])["text"]
        if ref.get("basis") and "sha256" in ref:
            return self.store.computation_output(ref["basis"])
        raise ValueError(f"nothing to load for {ref}")

    def value(self, name: str, subject: str) -> Value:
        """The derived value, computed on read: kept while nothing it read has changed (_check), and otherwise from
        the cache where its inputs' hash has a Computation. A value that waits is kept for the page for RETRY seconds;
        a strict read reads it again, and raises Waiting where it still waits. The value is read by the gathering
        innermost on this thread, as what that value is worked out from (_depend)."""
        key = (name, subject)
        read = None if self._strict else getattr(self._local, "last", None)
        if read is not None and key in read.values:  # read once in this page's read: no reader keeps it after
            if key in read.kept_unworked:
                read.unworked.add(key)
            self._depend(key, read.values[key])
            return read.values[key]
        self._held_here(key)
        if self._running and not self.store.in_transaction():
            self._sweep()
        held = self._memo.get(key)
        if held is not None:
            held = self._check(key, held)
            if held is None:
                self._drop(key)
            else:
                self._memo[key] = held
        again = key not in self._calls.read if self._strict else time.monotonic() >= self._expires.get(key, 0.0)
        if held is not None and held.waiting and again:
            self._drop(key)  # read again
            held = None
        if held is None:
            if key in self._stack:
                chain = " -> ".join(f"{n}({s})" for n, s in self._stack + [key])
                raise RuntimeError(f"derivation cycle: {chain}")
            held = self._compute(key)
            if read is not None and held.waited and not held.waiting:
                read.values[key] = held
        self._depend(key, held)
        if held.waiting and self._strict and not self._stack:
            raise Waiting(held.cause)
        return held

    def _compute(self, key: tuple[str, str]) -> Value:
        """The value worked out from what its gathering reads now: each key of the store it reads, with the state its
        read answered from (harness.touches.recording), and each value it reads, with what it read of it (Inputs.deps).
        Kept where, found current at its end (_check), what it read still answers as it did."""
        name, subject = key
        d = self.registry.get(name)
        computed = apart = False
        self._stack.append(key)
        ctx = Inputs(self)
        self._gathering.append(ctx)
        try:
            with touches.recording(self.store.stamp) as reads:
                try:
                    manifest = d.inputs(ctx, subject)
                except Waiting as e:  # the page: the value as last worked out, where it has one
                    manifest = None
                    v = self._last(d, subject) or Value(None, None, str(e), waiting=True)
                except Unavailable as e:
                    manifest = None
                    v = Value(None, None, str(e))
                    self._answered(d, subject, ctx, None, v.cause)
            if manifest is not None:
                h = computation_hash(d.name, d.version, subject, manifest)
                row = self.store.computation_by_hash(d.name, h)
                if row is not None:
                    v = Value(row["output"], row["id"])
                    self._answered(d, subject, ctx, row["id"])
                    with self._running_lock:
                        self._running.pop((d.name, h), None)  # one kept from it before: nothing left to take
                        self._apart_of.pop((d.name, h), None)
                else:
                    v, computed, apart = self._output(d, subject, h, manifest, answer=not ctx.waited)
                    if v.output is None and not v.waiting and not v.waited:  # absent or refused at these inputs
                        self._answered(d, subject, ctx, None, v.cause)
        finally:
            self._gathering.pop()
            self._stack.pop()
        v = replace(v, reads=dict(reads), deps=tuple(ctx.deps.items()), waited=ctx.waited or v.waiting or v.waited,
                    verified=(-1, -1))
        # what it read is what the store holds: kept; otherwise read again when next asked. A value that met a wait
        # and computed around it is not kept: nothing that lands need say when the wait it met is over
        kept = None if v.waited and not v.waiting else self._check(key, v)
        if kept is not None:
            v = self._memo[key] = kept
            self._held_here(key)
        if self._strict:
            self._calls.read.add(key)
        if v.waiting:
            # a value whose computation is under way is read again at once: it waits on nothing the sources decide
            self._expires[key] = time.monotonic() + (0.0 if apart else RETRY)
            if not self._stack:  # logged once, where the read began: the analyst never sees it
                log.warning("waiting: %s(%s): %s", name, subject, v.cause)
        if computed and d.stage:
            self._report(d, subject, "done", v.basis)
        return v

    def _answered(self, d: Derivation, subject: str, ctx: Inputs, cid: str | None, cause: str | None = None) -> None:
        """The answer the engine gives as the value (Store.answered): a Computation taken again by its inputs'
        hash, or none, the value absent or refused, with why. A value whose inputs met a wait, as one worked out from a
        figure the page holds, gives no answer: it is no state's value."""
        found = getattr(self.store, "answered", None)
        if found is not None and not ctx.waited:
            found(d.name, subject, cid, cause)

    def _output(self, d: Derivation, subject: str, h: str, manifest: dict, *,
                answer: bool = True) -> tuple[Value, bool, bool]:
        """The value of inputs no Computation answers yet: (the value, whether a Computation was kept, whether it
        waits on a computation under way). With `answer` false, inputs that met a wait: a Computation kept is kept by
        its content and is not the value's answer (Store.put_computation). A derivation reported as a stage is computed
        apart, outside a transaction; one under way is waited for (a call standing aside), read as waiting (the page), or, by a reader that can do
        neither (one inside a transaction, which holds the lock it cannot give up), awaited holding it: the
        computation needs no lock to finish."""
        key = (d.name, h)
        with self._running_lock:
            under_way = self._running.get(key)
        page = getattr(self._local, "placeholders", False)
        if (under_way is None and d.stage and not self.store.in_transaction()
                and (page or self.store.stands_aside())):
            under_way = self._start(d, subject, h, manifest)
        if under_way is not None:
            if not under_way.done():
                self.store.stand_aside_for(under_way, f"{d.name} is being computed")  # a call standing aside
                if page and not self.store.in_transaction():
                    self._later()
                    read = getattr(self._local, "last", None)
                    if read is not None:  # it and every value above it on this read: read again until it is made
                        read.apart.update(self._stack)
                    held = self._last(d, subject)  # the page: the value as last worked out while this one is made
                    if held is not None:
                        return held, False, False
                    return Value(None, None, f"{d.name} is being computed", waiting=True), False, True
                under_way.result()  # a reader that must have it waits for it: the computation takes no lock
            return self._land(d, subject, h, manifest, under_way, answer=answer)
        held = self._last(d, subject, inline=True)  # as last worked out, not worked out here under the lock
        if held is not None:
            self._local.last.unworked.update(self._stack)  # it and every value above it on this read
            return held, False, False
        if d.stage:
            self._report(d, subject, "computing")
        try:
            output = d.compute(manifest, self.read)
        except Waiting as e:  # what its inputs give does not yet give the value
            return Value(None, None, str(e), waiting=True), False, False
        except Unavailable as e:  # what its inputs give says there is none
            return Value(None, None, str(e), refused=e.named if isinstance(e, Refused) else None), False, False
        text = self._check_output(d, output)
        cid = self.store.put_computation(d.name, d.version, subject, h, manifest, output, text=text, answer=answer)
        self._made += 1
        return Value(json.loads(text), cid), True, False

    def _start(self, d: Derivation, subject: str, h: str, manifest: dict) -> Future:
        """The computation of these inputs, started apart: every payload the manifest names loaded here, under the
        store's lock, so that the computation reads nothing of the store, its connection least of all."""
        loads: dict[str, Any] = {}

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                if item.get("data") or item.get("source") or (item.get("basis") and "sha256" in item):
                    named = canonical(item)
                    if named not in loads:
                        loads[named] = self.read(item)
                for x in item.values():
                    walk(x)
            elif isinstance(item, list):
                for x in item:
                    walk(x)

        walk(manifest)

        def load(ref: dict) -> Any:
            try:
                return loads[canonical(ref)]
            except KeyError:
                raise RuntimeError(f"{d.name} read {ref}, which its manifest does not name") from None

        def compute() -> tuple:
            try:
                output = d.compute(manifest, load)
            except Waiting as e:
                return ("waiting", str(e), None)
            except Unavailable as e:
                return ("absent", str(e), e.named if isinstance(e, Refused) else None)
            text = self._check_output(d, output)
            return ("output", (text, json.loads(text)), None)

        if self._apart is None:
            self._apart = ThreadPoolExecutor(max_workers=APART, thread_name_prefix="compute")
        self._report(d, subject, "computing")
        future = self._apart.submit(compute)
        self._sweep()
        with self._running_lock:
            # one of the same value at another state, still under way, is of a state the store has left: no reader
            # takes it, and what it was made of is let go (it finishes, and what it gives is dropped)
            for k, made in list(self._apart_of.items()):
                if made[0].name == d.name and made[1] == subject:
                    self._running.pop(k, None)
                    self._apart_of.pop(k, None)
            self._running[(d.name, h)] = future
            self._apart_of[(d.name, h)] = (d, subject, manifest)
        return future

    def _sweep(self) -> None:
        """Each computation made apart that has finished and that no reader has taken, kept by its content and as no
        value's answer (Store.put_computation): a reader whose inputs hash to it finds it so, and the value's next
        answer replaces it. So no finished computation is held here, and none is made again because another one
        started while it waited to be taken. A reader in a transaction keeps none: its write could yet be rolled
        back, and a computation stays here until a reader outside one keeps it."""
        if self.store.in_transaction():
            return
        with self._running_lock:
            done = [k for k, f in self._running.items() if f.done()]
            finished = [(k, self._running.pop(k), self._apart_of.pop(k, None)) for k in done]
        for (name, h), future, made in finished:
            if made is None or future.exception() is not None:  # an error is raised to the reader that computes again
                continue
            state, got, _ = future.result()
            if state == "output" and self.store.computation_by_hash(name, h) is None:
                d, subject, manifest = made
                text, parsed = got
                self.store.put_computation(name, d.version, subject, h, manifest, parsed, text=text, answer=False)

    def _land(self, d: Derivation, subject: str, h: str, manifest: dict, done: Future, *,
              answer: bool = True) -> tuple[Value, bool, bool]:
        """What a computation made apart gave, kept by the reader that takes it, holding the store's lock, as the
        Computation of the inputs it read itself, whose hash is the one the computation was made for."""
        if not self.store.in_transaction():  # kept for good: a transaction's write could yet be rolled back
            with self._running_lock:
                if self._running.get((d.name, h)) is done:
                    del self._running[(d.name, h)]
                    self._apart_of.pop((d.name, h), None)
        state, got, named = done.result()  # an error in the computation is raised here, as computing inline raises it
        if state == "waiting":
            return Value(None, None, got, waiting=True), False, False
        if state == "absent":
            return Value(None, None, got, refused=named), False, False
        text, parsed = got
        cid = self.store.put_computation(d.name, d.version, subject, h, manifest, parsed, text=text, answer=answer)
        self._made += 1
        return Value(parsed, cid), True, False

    def _report(self, d: Derivation, subject: str, state: str, basis: str | None = None) -> None:
        report = getattr(self.store, "report", None)
        if report is not None:
            report({"stage": d.stage, "derivation": d.name, "subject": subject, "state": state,
                    **({"basis": basis} if basis else {})})

    def computing(self, name: str, subject: str) -> bool:
        """Whether the value is being computed now, further up this read."""
        return (name, subject) in self._stack

    def _check_output(self, d: Derivation, output: dict) -> str:
        """The output checked against what the derivation declares, as its canonical text: what the store keeps and
        what every reader is given parsed, so that a value computed in this process and one read back from the store
        are one form, its keys in one order, and a computation that runs over one reads it the same either way."""
        if not isinstance(output, dict):
            raise TypeError(f"derivation {d.name} returned {type(output).__name__}, not an object")
        text = canonical(output)
        for oid, rec in (output.get("objects") or {}).items():
            kind = rec.get("type")
            if kind not in d.produces:
                raise ValueError(f"derivation {d.name} produced a {kind}, which it does not declare")
            if not schema.NAME_ID_RE.match(oid):
                raise ValueError(f"derivation {d.name} produced {oid}, which is not a {kind} id")
            bad = [f for f in rec.get("fields", {}) if f not in schema.OBJECTS["Name"].derived]
            if bad:
                raise ValueError(f"derivation {d.name} gives {oid} undeclared fields {bad}")
        for lid, rec in (output.get("links") or {}).items():
            rel = rec.get("relation")
            if rel not in d.produces:
                raise ValueError(f"derivation {d.name} produced a {rel} link, which it does not declare")
            if lid != schema.derived_link_id(rel, rec["from"], rec["to"]):
                raise ValueError(f"derivation {d.name} produced {lid}; its id is {rel}:{rec['from']}>{rec['to']}")
            bad = [f for f in rec.get("fields", {}) if f not in schema.RELATIONS[rel].derived]
            if bad:
                raise ValueError(f"derivation {d.name} gives {lid} undeclared fields {bad}")
        return text

    def derived(self, entity_id: str, kind: str, *, fields=None, exclude=()) -> dict[str, dict]:
        """The derived fields of an object or link, each with its basis. A field that does not exist is not there; a
        field that waits on a read is there as waiting, for the page's placeholder, and a strict read raises Waiting
        instead. A receipt may select or exclude fields without evaluating derivations that give none of them;
        dependency reads and the validation of any computation remain unchanged."""
        out: dict[str, dict] = {}
        selected, excluded = set(fields) if fields is not None else None, set(exclude)
        # A stated Metric's fields are its own; only a proxy Metric's are derived.
        allowed = None
        if kind == "Metric" and not schema.PROXY_METRIC_RE.match(entity_id):
            allowed = set(schema.OBJECTS["Metric"].derived)
        for d in self.registry.on(kind):
            given, pick = d.gives(kind)
            fields = [f for f in given if (allowed is None or f in allowed)
                      and (selected is None or f in selected) and f not in excluded]
            if not fields or not d.applies(self.store, entity_id):
                continue
            v = self.value(d.name, d.subject_of(self.store, entity_id))
            if v.waiting:
                for f in fields:
                    out[f] = {"value": None, "waiting": True}
                continue
            if v.output is None:
                continue
            picked = pick(v.output, entity_id) if pick else v.output
            for f in fields:
                if f in picked:
                    out[f] = {"value": picked[f], "basis": v.basis}
        if any(kind in d.produces for d in self.registry.producers()):  # only a kind a producer gives reads them
            produced = self.produced((kind,))  # the producers that give it: a Name's b is the betas'
            obj = produced["objects"].get(entity_id)
            if obj:
                for f, field_value in obj["fields"].items():
                    if (selected is None or f in selected) and f not in excluded:
                        out.setdefault(f, field_value)
            link = produced["links"].get(entity_id)
            if link:
                for f, val in link.get("fields", {}).items():
                    if (selected is None or f in selected) and f not in excluded:
                        out.setdefault(f, {"value": val, "basis": link["basis"]})
        return out

    def produced(self, gives: tuple[str, ...] | None = None) -> dict:
        """Every derived object and link the producers give now, each with its basis; with `gives`, those of the
        producers that give one of these kinds of object or link alone, so that a read of which Names exist, or of
        the links that can end at one object, computes no producer that gives neither: none but the betas reads every
        name's prices. Where every producer's products are gathered already, they serve: what a read finds in them is
        what the producers it names give. The products are a value of their own (_produce), kept as values are, and
        read by the gathering innermost on this thread as what its value is worked out from: a store's read that
        finds which Names exist, or a Name's derived links, reads them."""
        found = self._products_for(None if gives is None else frozenset(gives))
        if found is None:
            # A producer reads the store while the products are being gathered: it sees
            # the stated structure, never products in the making.
            return {"objects": {}, "links": {}}
        products, v, k = found
        self._depend(("products", k), v)
        return products

    def _products_for(self, key: frozenset | None, *, exact: bool = False) -> tuple[dict, Value, frozenset | None] | None:
        """The products (produced) found current or gathered again, with their value and the key they are kept under:
        every producer's, which serve any read, or, `exact`, those of `key` alone; None while they are being gathered."""
        for k in ((key,) if exact or key is None else (None, key)):
            held = self._products.get(k)
            if held is None:
                continue
            v = self._check(("products", k), held[1])
            if v is None:
                self._products.pop(k, None)
                continue
            self._products[k] = (held[0], v)
            self._held_here(("products", k))
            return held[0], v, k
        if self._producing:
            return None
        self._producing = True
        try:
            products, v = self._produce(key)
        finally:
            self._producing = False
        return products, v, key

    def _produce(self, gives: frozenset | None = None) -> tuple[dict, Value]:
        """The producers' products gathered, as a value of their own: what they read of the store (which instances
        each producer runs over) and the producers' values, each with what was read of it; their basis names that, so
        products gathered again from the same values are the same."""
        objects: dict[str, dict] = {}
        links: dict[str, dict] = {}
        record = Inputs(self)  # what the products are worked out from: the producers' values
        self._gathering.append(record)
        try:
            with touches.recording(self.store.stamp) as reads:
                for d in self.registry.producers():
                    if gives is not None and not gives.intersection(d.produces):
                        continue
                    subjects: list[str] = []
                    for entity in self.store.ids_of(d.on):
                        if d.applies(self.store, entity):
                            s = d.subject_of(self.store, entity)
                            if s not in subjects:
                                subjects.append(s)
                    for s in subjects:
                        v = self.value(d.name, s)  # read as the products' own (Engine.value: Inputs._met)
                        if v.output is None:  # a product absent, or waiting: read where it is used
                            continue
                        for oid, rec in (v.output.get("objects") or {}).items():
                            # Each field carries the basis of the Computation that produced it.
                            merged = objects.setdefault(oid, {"type": rec["type"], "fields": {}})
                            for f, val in rec.get("fields", {}).items():
                                merged["fields"][f] = {"value": val, "basis": v.basis}
                        for lid, rec in (v.output.get("links") or {}).items():
                            links[lid] = {**rec, "fields": dict(rec.get("fields", {})), "basis": v.basis}
        finally:
            self._gathering.pop()
        found = {"objects": objects, "links": links}
        deps = tuple(record.deps.items())
        basis = "products:" + digest(sorted(repr(item) for item in deps))
        # a producer that waits leaves the products to be gathered again once what it waits on lands, as a value
        # that waits is
        v = Value(None, basis, waiting=record.waited, reads=dict(reads), deps=deps, waited=record.waited)
        kept = self._check(("products", gives), v)
        if kept is not None:
            self._products[gives] = (found, kept)
            self._held_here(("products", gives))
        return found, kept or v

    def ask(self, kind: str, statement: str, evidence: list[dict], *, version: int = 1) -> dict:
        """One question's answer."""
        return self.ask_all([(kind, statement)], evidence, version=version)[0]

    def ask_all(self, questions: list[tuple], evidence: list[dict], *, version: int = 1) -> list[dict]:
        """The answers to questions over one evidence: each from the cache, and those not cached
        from the judge in one request. Each answer is its own Judgment on its own key.

        Evidence is a list of items {"ref": id or None, "content": ...}; a key is taken over the
        kind, the statement, and the state the judge reads from the contents alone, and the refs are
        kept in the Judgment's inputs. With no judge here, a question not yet answered has no answer, and the check
        that asks it is absent. A judge that does not answer, or leaves a question unanswered, leaves the check
        waiting: what it did answer is kept, and the next read asks the rest again.

        Each question is one read that lands what it answers (harness.touches.answering): a value whose gathering asks
        it reads its Judgment as of the answer kept, so the answer it was given leaves it current.
        """
        for item in evidence:
            if not isinstance(item, dict) or "content" not in item:
                raise TypeError("evidence items are {'ref': id, 'content': ...}")
        keys = [touches.judgment(judgment_hash(kind, statement, evidence, version)) for kind, statement in questions]
        with touches.answering(*keys):
            return self._ask_all(questions, evidence, version)

    def _ask_all(self, questions: list[tuple], evidence: list[dict], version: int) -> list[dict]:
        models = self.judge.models if self.judge is not None else None
        out: list[dict | None] = []
        missing = []
        for q in questions:
            kind, statement = q
            h = judgment_hash(kind, statement, evidence, version)
            row = self.store.judgment_by_hash(h, models)
            out.append({"judgment": row["id"], "answer": row["answer"]} if row is not None else None)
            if row is None:
                missing.append((len(out) - 1, kind, statement, h, q))
        if not missing:
            return out
        if self.judge is None:
            raise Unavailable("no judge here answers this check")
        try:
            answers = self.judge.answer_all([q for _, _, _, _, q in missing], evidence)
        except TooLong as e:  # no judge here answers it: the check is absent
            raise Unavailable(str(e)) from None
        except JudgeError as e:
            raise Waiting(str(e)) from None
        if len(answers) != len(missing):
            raise ValueError(f"the judge answered {len(answers)} of {len(missing)} questions")
        kept = [{"ref": item.get("ref"), "content": item["content"]} for item in evidence]
        unanswered = []
        for (i, kind, statement, h, q), a in zip(missing, answers):
            if a.probability is None:
                unanswered.append(a.reason or f"the judge left {kind} unanswered")
                continue
            p = _checked(a.probability)
            jid = self.store.put_judgment(kind, statement, h, kept, p, a.model, a.raw)
            out[i] = {"judgment": jid, "answer": p}
        if unanswered:  # what was answered is kept; the check waits for the rest
            raise Waiting("; ".join(unanswered))
        return out


def _checked(p: Any) -> float:
    """A noul's answer is a probability."""
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
        raise ValueError(f"the judge answered {p!r}, not a probability")
    return float(p)
