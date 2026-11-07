"""Derivations on read: the registry, manifests and input hashes, and the
cache of Computations and Judgments.

A derivation reads through `Inputs`, which returns each item with the ids that name it,
and returns its manifest: everything its compute reads. The manifest's hash is the
Computation's input hash. A Computation with that hash is the derived value; a new hash
computes and stores a new one. Nothing is invalidated: a changed input changes the hash,
and the cache keeps every row, so a structure that returns to an earlier state is
answered by the rows that state produced. Judgments are asked while inputs are gathered,
so every answer a Computation used is in its manifest. A Judgment is keyed on its kind,
statement, and the content of its evidence, never on store ids, so the same question is
the same Judgment in any store; the model that answered is beside the key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from harness import schema
from harness.judge import ABOUT, Judge, JudgeError


class Unavailable(Exception):
    """An input a derivation needs is not here: data no client here fetches, or a question
    no judge here answers. The derived value is null with this reason, and the next read
    tries again."""


def canonical(obj: Any) -> str:
    """The one serialization every hash is taken over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def computation_hash(name: str, version: int, subject: str, manifest: dict) -> str:
    return digest({"derivation": name, "version": version, "subject": subject, "inputs": manifest})


# What the judge is told the evidence is, said once per request: part of what it is asked, so part
# of every key, and a change to it asks again on its own.
FRAMING = text_hash(ABOUT)


def judgment_hash(kind: str, statement: str, evidence: list[dict], version: int) -> str:
    return digest({"kind": kind, "statement": statement, "version": version, "framing": FRAMING,
                   "evidence": [item["content"] for item in evidence]})


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
    missing: Callable[[dict, str], dict] | None = None  # an instance's fields with no value, each with its reason

    def gives(self, kind: str) -> tuple[tuple[str, ...], Callable[[dict, str], dict] | None] | None:
        """The fields this derivation gives an instance of `kind`, and how they are picked."""
        if kind == self.on:
            return self.fields, self.pick
        for other, fields, pick in self.extra:
            if other == kind:
                return fields, pick
        return None


# Producers give Names and the links that follow from holdings and betas. A channel's
# proxy Metric and its tracks link follow from the channel's stated proxy_ticker, so the
# store derives them itself.
PRODUCIBLE = ("Name", "looks_through_to", "loads_on")


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


@dataclass(frozen=True)
class Value:
    output: dict | None
    basis: str | None
    reason: str | None = None


class Inputs:
    """What a derivation reads. Each method returns the item with the ids that name it,
    for the derivation to put in its manifest."""

    def __init__(self, engine: "Engine"):
        self._engine = engine
        self._store = engine.store

    def object(self, oid: str) -> dict:
        described = self._store.describe(oid)
        if described is None:
            raise Unavailable(f"{oid} is not in the store")
        return described

    def links(self, oid: str, relation: str | None = None, direction: str = "out") -> list[dict]:
        return self._store.links_of(oid, relation, direction)

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
        v = self._engine.value(name, subject)
        if v.output is None:
            raise Unavailable(v.reason)
        if ref:
            return {"derivation": name, "subject": subject, "basis": v.basis, "sha256": digest(v.output)}
        return {"derivation": name, "subject": subject, "basis": v.basis, "output": v.output}

    def data(self, kind: str, subject: str, *, covering: tuple[str, str] | None = None,
             required: bool = True, at: str | None = None) -> dict | None:
        """The newest data of `kind` for `subject` covering the range; with `at`, the reading the store held at
        that moment, or the first where `at` is empty."""
        row = self._store.data(kind, subject, covering=covering, required=required, at=at)
        if row is None:
            return {"data": None, "kind": kind, "subject": subject,
                    "covering": list(covering) if covering else None}
        return {"data": row["id"], "kind": kind, "subject": subject,
                "range": [row["range_from"], row["range_to"]], "retrieved_at": row["retrieved_at"],
                "sha256": row["sha256"]}

    def prefetch(self, kind: str, subjects, covering: tuple[str, str] | None = None, *, stage: str | None = None,
                 about: str | None = None) -> None:
        """Fetch many subjects' data side by side before reading them one by one with `data`; it reads nothing
        into the manifest, which records what `data` then reads. A `stage` names the work, reported as it goes."""
        self._store.prefetch(kind, list(subjects), covering,
                             stage=None if stage is None else {"stage": stage, **({"about": about} if about else {})})

    def payload(self, ref: dict) -> Any:
        """The payload a manifest item names, for an inputs function whose next read depends
        on it. The item is in the manifest, so what was read is pinned by its hash."""
        return self._engine.load(ref)

    def source(self, sid: str) -> dict:
        s = self._store.source_row(sid)
        if s is None:
            raise Unavailable(f"{sid} is not in the store")
        return {"source": sid, "url": s["url"], "dated": s["dated"], "sha256": s["sha256"]}

    def ask(self, kind: str, statement: str, evidence: list[dict], *, version: int = 1) -> dict:
        return self._engine.ask(kind, statement, evidence, version=version)

    def ask_all(self, questions: list[tuple], evidence: list[dict], *, version: int = 1) -> list[dict]:
        return self._engine.ask_all(questions, evidence, version=version)

    def watched(self, pid: str) -> bool:
        return self._store.watched(pid)

    def given_at(self, lid: str) -> str | None:
        """When a holding was given: when its link was made, or its figure last edited."""
        return self._store.given_at(lid)


class Engine:
    """Computes derived values on read and keeps the cache."""

    def __init__(self, store, registry: Registry, judge: Judge | None = None):
        self.store = store
        self.registry = registry
        self.judge = judge
        self._memo: dict[tuple[str, str], Value] = {}
        self._produced: dict | None = None
        self._rev = -1
        self._stack: list[tuple[str, str]] = []
        self._producing = False
        self._unsettled: set[tuple[str, str]] = set()  # values that read a check the judge left unanswered
        self._wanting: set[tuple[str, str]] = set()  # values this read could not work out for want of a client

    def reset(self) -> None:
        self._memo.clear()
        self._wanting.clear()
        self._produced = None
        self._rev = -1

    def _sync(self) -> None:
        if self._rev != self.store.revision:
            self._memo.clear()
            self._wanting.clear()
            self._produced = None
            self._rev = self.store.revision

    def wanting(self) -> None:
        """The values being read wait on a judge or a data client this engine does not have: with one, they would be
        worked out. The page shows such a value as a placeholder while the analyst works."""
        self._wanting.update(self._stack)

    def pending(self, name: str, subject: str) -> bool:
        """Whether this revision's read of the value waited on a judge or a data client that is not here."""
        return (name, subject) in self._wanting

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
        self._sync()
        key = (name, subject)
        if key in self._memo:
            if key in self._wanting:
                self._wanting.update(self._stack)
            return self._memo[key]
        if key in self._stack:
            chain = " -> ".join(f"{n}({s})" for n, s in self._stack + [key])
            raise RuntimeError(f"derivation cycle: {chain}")
        d = self.registry.get(name)
        computed = False
        self._stack.append(key)
        try:
            try:
                manifest = d.inputs(Inputs(self), subject)
            except Unavailable as e:
                v = Value(None, None, str(e))
            else:
                h = computation_hash(d.name, d.version, subject, manifest)
                row = self.store.computation_by_hash(d.name, h)
                if row is not None:
                    v = Value(row["output"], row["id"])
                else:
                    if d.stage:
                        self._report(d, subject, "computing")
                    output = d.compute(manifest, self.load)
                    self._check_output(d, output)
                    cid = self.store.put_computation(d.name, d.version, subject, h, manifest, output)
                    v = Value(output, cid)
                    computed = True
        finally:
            self._stack.pop()
        self._sync()
        if key in self._unsettled:  # it read a check left unanswered, which the next read asks again
            self._unsettled.discard(key)
        else:
            self._memo[key] = v
        if computed and d.stage:
            self._report(d, subject, "done", v.basis)
        return v

    def _report(self, d: Derivation, subject: str, state: str, basis: str | None = None) -> None:
        report = getattr(self.store, "report", None)
        if report is not None:
            report({"stage": d.stage, "derivation": d.name, "subject": subject, "state": state,
                    **({"basis": basis} if basis else {})})

    def computing(self, name: str, subject: str) -> bool:
        """Whether the value is being computed now, further up this read."""
        return (name, subject) in self._stack

    def _check_output(self, d: Derivation, output: dict) -> None:
        if not isinstance(output, dict):
            raise TypeError(f"derivation {d.name} returned {type(output).__name__}, not an object")
        canonical(output)
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

    def derived(self, entity_id: str, kind: str) -> dict[str, dict]:
        """The derived fields of an object or link, each with its basis or the reason it is null."""
        out: dict[str, dict] = {}
        # A stated Metric's fields are its own; only a proxy Metric's are derived.
        allowed = None
        if kind == "Metric" and not schema.PROXY_METRIC_RE.match(entity_id):
            allowed = set(schema.OBJECTS["Metric"].derived)
        for d in self.registry.on(kind):
            given, pick = d.gives(kind)
            fields = [f for f in given if allowed is None or f in allowed]
            if not fields or not d.applies(self.store, entity_id):
                continue
            v = self.value(d.name, d.subject_of(self.store, entity_id))
            if v.output is None:
                for f in fields:
                    out[f] = {"value": None, "reason": v.reason}
                continue
            picked = pick(v.output, entity_id) if pick else v.output
            # a field whose input has no answer is null, with the reason
            missing = dict(v.output.get("unavailable") or {})
            if d.missing is not None:
                missing.update(d.missing(v.output, entity_id))
            for f in fields:
                if f in missing:
                    out[f] = {"value": None, "reason": missing[f], "basis": v.basis}
                elif f in picked:
                    out[f] = {"value": picked[f], "basis": v.basis}
        produced = self.produced()
        obj = produced["objects"].get(entity_id)
        if obj:
            for f, field_value in obj["fields"].items():
                out.setdefault(f, field_value)
        link = produced["links"].get(entity_id)
        if link:
            for f, val in link.get("fields", {}).items():
                out.setdefault(f, {"value": val, "basis": link["basis"]})
        return out

    def produced(self) -> dict:
        """Every derived object and link the producers give now, each with its basis."""
        self._sync()
        if self._produced is not None:
            return self._produced
        if self._producing:
            # A producer reads the store while the products are being gathered: it sees
            # the stated structure, never products in the making.
            return {"objects": {}, "links": {}}
        self._producing = True
        try:
            return self._produce()
        finally:
            self._producing = False

    def _produce(self) -> dict:
        objects: dict[str, dict] = {}
        links: dict[str, dict] = {}
        for d in self.registry.producers():
            subjects: list[str] = []
            for entity in self.store.ids_of(d.on):
                if d.applies(self.store, entity):
                    s = d.subject_of(self.store, entity)
                    if s not in subjects:
                        subjects.append(s)
            for s in subjects:
                v = self.value(d.name, s)
                if v.output is None:  # a product not yet derivable: its reason is the value's, read where it is used
                    continue
                for oid, rec in (v.output.get("objects") or {}).items():
                    # Each field carries the basis of the Computation that produced it.
                    merged = objects.setdefault(oid, {"type": rec["type"], "fields": {}})
                    for f, val in rec.get("fields", {}).items():
                        merged["fields"][f] = {"value": val, "basis": v.basis}
                for lid, rec in (v.output.get("links") or {}).items():
                    links[lid] = {**rec, "fields": dict(rec.get("fields", {})), "basis": v.basis}
        self._sync()
        self._produced = {"objects": objects, "links": links}
        return self._produced

    def ask(self, kind: str, statement: str, evidence: list[dict], *, version: int = 1) -> dict:
        """One question's answer. A question the judge does not answer is no answer: the value that
        needed it is unavailable, with the reason."""
        found = self.ask_all([(kind, statement)], evidence, version=version)[0]
        if found["answer"] is None:
            raise Unavailable(found["reason"])
        return found

    def ask_all(self, questions: list[tuple], evidence: list[dict], *, version: int = 1) -> list[dict]:
        """The answers to questions over one evidence: each from the cache, and those not cached
        from the judge in one request. Each answer is its own Judgment on its own key.

        Evidence is a list of items {"ref": id or None, "content": ...}; a key is taken over the
        kind, the statement, the framing, and the content alone, and the refs are kept in the
        Judgment's inputs. A Source is read as the passages its evidence link quotes, in its
        content. Only an answer is cached: a question the response leaves unanswered comes
        back as {"answer": None, "reason"}, and is asked again on the next read.
        """
        for item in evidence:
            if not isinstance(item, dict) or "content" not in item:
                raise TypeError("evidence items are {'ref': id, 'content': ...}")
        models = self.judge.models if self.judge is not None else None
        out: list[dict | None] = []
        missing = []
        for q in questions:
            kind, statement = q
            h = judgment_hash(kind, statement, evidence, version)
            row = self.store.judgment_by_hash(h, models)
            out.append({"judgment": row["id"], "answer": row["answer"], "reason": None} if row is not None else None)
            if row is None:
                missing.append((len(out) - 1, kind, statement, h, q))
        if not missing:
            return out
        if self.judge is None:
            self.wanting()
            raise Unavailable("the judge is not available here, so this check is not yet read")
        try:
            answers = self.judge.answer_all([q for _, _, _, _, q in missing], evidence)
        except JudgeError as e:  # no answer: nothing is cached, and the next read asks again
            self._unsettled.update(self._stack)
            raise Unavailable(str(e)) from None
        if len(answers) != len(missing):
            raise ValueError(f"the judge answered {len(answers)} of {len(missing)} questions")
        kept = [{"ref": item.get("ref"), "content": item["content"]} for item in evidence]
        for (i, kind, statement, h, q), a in zip(missing, answers):
            p = a.probability
            if p is None:
                out[i] = {"judgment": None, "answer": None, "reason": a.reason}
                self._unsettled.update(self._stack)
                continue
            p = _checked(p)
            jid = self.store.put_judgment(kind, statement, h, kept, p, a.model, a.raw)
            out[i] = {"judgment": jid, "answer": p, "reason": None}
        return out


def _checked(p: Any) -> float:
    """A noul's answer is a probability."""
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0.0 <= p <= 1.0:
        raise ValueError(f"the judge answered {p!r}, not a probability")
    return float(p)
