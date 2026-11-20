"""The forecast record: each reply's rows as they stood, and each watched proposition's outcome once the markets in
its breakdown determine it. It is the history forecasts are scored against, and nothing the store derives reads it.

A reply's record. When a reply ends, stopped or not, the store records each watched proposition's row as the ledger
gives it, each field with the Computation or Judgment behind it (its basis, which the store keeps while a record names
it); a copy of what the analyst had written, as a checkpoint holds it, each page it cites kept once by its hash, with
each claim's check as it stood (ledger.link_states: the fields' test of a claim between two propositions on one metric
and resolution source, the judge's reading of its two texts, or, where the judge has not yet read it, the question its
check would ask); whether the reply was stopped; and the versions it was made with: each derivation's, the
analyst model and its effort, the judge's alias and the deployment that last answered, and a digest of what the analyst
reads. A record is never changed or removed, and a rewind leaves it where it is: the turns a rewind replaces were said,
and the trace keeps them too. Each row is worked out on its own, in the ledger's order, so a row that cannot be worked
out leaves the others as they stand: it is left out of the record's rows, and the copy still holds what the analyst had
written of the event. The store's trace then keeps a record_error entry naming the proposition and the record it is
missing from; where the claims' checks cannot be read, the record keeps none and its entry says so (`left_out`); a
record that cannot be made at all is not made, and its entry names no proposition. Each entry has the session, the
turn, the error, and the lines it arose at, and no conversation or page reads it; the same line goes to standard error,
where the one running the page sees it. A reply ends whatever its record does.

An outcome. A market settles at 1 when it resolves YES and 0 when it resolves NO; the venue's record says which, and
the caller reads it, for every market any record names, kept or since released (`markets`). A forecast is of a
version: a proposition under one definition, its stated fields as they stood, a null optional field and an absent one
being the same question. Each record keeps its own copy of what the analyst had written, and a version's outcome is
determined through that copy's conditions, never the store's statements now, so a forecast whose event was later edited
or removed is still decided on the question it asked. Those conditions are what structure.statements reads with
`chances=False`: its links, the fields that place propositions on one metric and resolution source on a line, a
ladder's rungs among them whether or not they are linked, and each deadline reading's placing of its rungs, without
reaching through plain numeric chances. The settled markets in it, each a book at its result through its asks link
(one minus the result under the complement), whether or not its price was taken, since a settlement says whether the
outcomes the market asks happened, are read through that structure as one set of probabilities (oneform.decided): where
the proposition's probability can only be 1 it is true, and where it can only be 0 false. A claim that stood disputed
when the record was made (its fields contradict it, or the judge's reading of its two texts is one it does not allow),
or a text claim not yet checked that no judge has read when the outcome is determined, carries no settlement here
(Frozen.disputed); where a disputed or unread link belongs to a group (`way`, `and`, `or`), the group's joint
exhaustiveness is dropped while each sound sibling keeps its own containment claim. Where the structure and the
settlements cannot hold together, one of the record's claims is false, and that record decides nothing. A version
decided the same way by every record that decides it is recorded once, with what each read; one the records decide
differently, through statements one had and another did not, stays open, for the resolver. A store made before the
record is its own state now, its one version.

When an outcome became known. A forecast counts while its outcome was not yet publicly known under its rules, and when
it became known is one fact on the outcome record (`known`), from the evidence that decided it, the same whichever
venue settled it: the earliest moment a prefix of dated settlements decided the outcome (at each venue's settlement
time, or when the store read the settlement where the venue gives none), bounded by the time the definition fixes where
it fixes one (`_definition_known_at`).

The history (`history`). Every version is scored on its own question for the time it stood, across replies and rewinds
in causal order: each priced forecast stands until the next reply (an open probability, a removal, or a redefinition
included), a rewind of its turn, or the moment its outcome became known, whichever comes first; a version watched again
or restored by a rewind stands again in a new spell, each spell reported whole (`spells`). The forecast standing when
the outcome became known is reported apart; a version that had none standing then carries where it ended, if it did.
"""

from __future__ import annotations

import json
import math
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from harness import credentials, ledger, schema
from harness.derive import checks, oneform, structure
from harness.derive import evidence as ev
from harness.derive.engine import Inputs, Unavailable, Waiting, digest
from harness.derive.structure import market
from harness.ledger import link_states, row, stands_disputed, watched_events
from harness.schema import DEFINITION
from harness.store import SCHEMA_VERSION, Store
from harness.trace import Trace, now

NEW_YORK = ZoneInfo("America/New_York")
RESOLUTION_KIND = "resolution"


# ---------------------------------------------------------------- a reply's record


def versions(store: Store, analyst: dict | None = None) -> dict:
    """What a reply was made with: the schema its copy of the analyst's statements is in, each derivation's version,
    the analyst's model, effort, and a digest of what it reads, the judge it asks, and the deployment that last
    answered. The copy is kept as written, so its figures are in the units of the schema it names; a record that names
    none was written at schema 12 or earlier."""
    judge = store.engine.judge
    last = store._one("SELECT model FROM judgment ORDER BY rowid DESC LIMIT 1")
    return {"schema": SCHEMA_VERSION, "derivations": store.engine.registry.versions(), "analyst": analyst or {},
            "judge": {"asked": getattr(judge, "model", None), "last_answered": last["model"] if last else None}}


def reply(store: Store, *, session: str | None, turn: int | None, stopped: bool, analyst: dict | None = None,
          system: list[dict] | None = None) -> str | None:
    """Record each watched proposition's row as it stands at a reply's end, each worked out on its own, with a copy of
    what the analyst had written and the versions, `system` being what the analyst reads. Returns the record's id, or
    None where the record could not be made; a row left out, and a record not made, are each said in the trace and on
    standard error. It never raises."""
    left: list[tuple[str | None, Exception]] = []
    rid = None
    try:
        made = dict(analyst or {})
        if system is not None:
            made["knowledge"] = digest([b["text"] for b in system])
        with store.lock:
            rows = []
            for pid in watched_events(store) if store.type_of("portfolio") == "Portfolio" else []:
                try:
                    rows.append(row(store, pid))
                except Exception as e:
                    left.append((pid, e))
            written, texts = store.written()
            try:  # each claim's check as it stands, with any unread claim's question kept for outcome time
                written["checks"] = link_states(store, questions=True)
            except Exception as e:
                left.append((None, e))
            rid = store.put_record(session=session, turn=turn, stopped=stopped, rows=rows, written=written,
                                   texts=texts, versions=versions(store, made))
    except Exception as e:
        _failed(store, session, turn, e)
    for pid, e in left:
        _failed(store, session, turn, e, proposition=pid, record=rid, checks_left_out=pid is None)
    return rid


def _failed(store: Store, session: str | None, turn: int | None, error: Exception, *, proposition: str | None = None,
            record: str | None = None, checks_left_out: bool = False) -> None:
    """A row left out of a reply's record, its claims' checks left out of it, or a record not made, in the store's
    trace and on standard error; neither write can raise."""
    try:
        said = f"{type(error).__name__}: {error}"
        where = [f"{f.filename}:{f.lineno} in {f.name}" for f in traceback.extract_tb(error.__traceback__)[-6:]]
        if credentials.find([said, where], names=False):  # an error's text can quote a secret; its name cannot
            said, where = type(error).__name__, []
    except Exception:
        said, where = type(error).__name__, []
    try:
        row_of = {"proposition": proposition, "record": record} if proposition else \
            {"left_out": "checks", "record": record} if checks_left_out else {}
        Trace(store.trace_path).append({"type": "record_error", "session": session, "turn": turn, "at": now(),
                                        **row_of, "error": said, "where": where})
    except Exception:
        pass
    try:
        what = f"left out {proposition}" if proposition else "left out its claims' checks" if checks_left_out \
            else "failed"
        print(f"the forecast record for {session} turn {turn} {what}: {said}", file=sys.stderr, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- outcomes, from each record as it was made


def _group_key(link: dict) -> tuple | None:
    """The group whose members claim joint exhaustiveness together: a parent's side for `way`, or a composite for `and`
    and `or`."""
    rel = link["relation"]
    if rel == "way":
        return ("way", link["from"], link["stated"].get("side") or "yes")
    if rel in ("and", "or"):
        return (rel, link["from"])
    return None


class Frozen:
    """A record's copy of what the analyst had written, read as the store's structure reads a store
    (structure.statements): each object's stated fields, each link by its two ends, the propositions whose chances are
    given an object, and each proposition's line, its fields read as of the copy's as-of. A price the store derives is
    read from its listing (`price_source`); one that cannot be read now places its propositions on no line. A store with
    no record yet is read as it stands, its one version."""

    def __init__(self, written: dict, price_source=None, judged=None, checks=None, ask=None):
        self.asof = written.get("asof")
        self._price_source = price_source or (lambda mid: None)
        self._judged = judged or (lambda frozen, link: None)
        self._ask = ask or (lambda question: None)
        self._checks = written.get("checks") if checks is None else checks
        self._disputed: set[str] | None = None
        self._eff: tuple[dict[str, list[dict]], dict[str, list[dict]]] | None = None
        self.objects = {o["id"]: o for o in written.get("object") or []}
        self._links: list[dict] = []
        self._out: dict[str, list[dict]] = defaultdict(list)
        self._in: dict[str, list[dict]] = defaultdict(list)
        for r in written.get("link") or []:
            link = {"id": r["id"], "relation": r["relation"], "from": r["from_id"], "to": r["to_id"],
                    "stated": r.get("stated") or {}}
            self._links.append(link)
            self._out[link["from"]].append(link)
            self._in[link["to"]].append(link)
        self.markets = [m["id"] for m in written.get("market") or []]

    def out(self, oid: str) -> list[dict]:
        return list(self._out.get(oid, []))

    def both(self, oid: str) -> list[dict]:
        return self.out(oid) + list(self._in.get(oid, []))

    def stated(self, oid: str) -> dict:
        return (self.objects.get(oid) or {}).get("stated") or {}

    def _resolved(self) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        """The copy's sound links: each disputed or unread claim left out, and where one belonged to a `way`, `and`,
        or `or` group, the group's joint exhaustiveness dropped while its sound members keep their individual
        containment claims."""
        if self._eff is not None:
            return self._eff
        bad = self.disputed()
        broken = {gk for link in self._links if (gk := _group_key(link)) is not None and link["id"] in bad}
        eff_out: dict[str, list[dict]] = defaultdict(list)
        eff_in: dict[str, list[dict]] = defaultdict(list)
        for link in self._links:
            if link["id"] in bad:
                continue
            gk = _group_key(link)
            if gk is not None and gk in broken:
                rel = link["relation"]
                if rel == "way":
                    no = link["stated"].get("side") == "no"
                    kept = {"id": link["id"], "relation": "implies", "from": link["to"], "to": link["from"],
                            "stated": {}, **({"to_true": False} if no else {})}
                elif rel == "and":
                    kept = {"id": link["id"], "relation": "implies", "from": link["from"], "to": link["to"],
                            "stated": {}}
                else:
                    kept = {"id": link["id"], "relation": "implies", "from": link["to"], "to": link["from"],
                            "stated": {}}
            else:
                kept = link
            eff_out[kept["from"]].append(kept)
            eff_in[kept["to"]].append(kept)
        self._eff = (eff_out, eff_in)
        return self._eff

    # what structure.statements reads of the copy, as InStore reads it of the store

    def breakdown(self, oid: str) -> list[dict]:
        eff_out, eff_in = self._resolved()
        return [link for link in eff_out.get(oid, []) + eff_in.get(oid, []) if link["relation"] in schema.BREAKDOWN]

    def asks_in(self, mid: str) -> list[dict]:
        _, eff_in = self._resolved()
        return [link for link in eff_in.get(mid, []) if link["relation"] == "asks"]

    def links(self, oid: str, direction: str) -> list[dict]:
        eff_out, eff_in = self._resolved()
        return list(eff_out.get(oid, [])) + (list(eff_in.get(oid, [])) if direction == "both" else [])

    def disputed(self) -> set[str]:
        """The claims that cannot carry a settlement: those that stood disputed when the record was made
        (ledger.stands_disputed: their fields contradict them, or the judge's reading of their two texts is one they do
        not allow), and any text claim left unread then that still has no reading when the outcome is determined. Where
        an unread claim kept its question, the judge is asked over it now (`_ask`), and where it kept none, read as a
        record without kept checks (`_judged`). Any checked relation absent from the kept checks is tested by its
        comparable fields (checks.claimed) or read through `_judged`."""
        if self._disputed is not None:
            return self._disputed
        checks_ = self._checks() if callable(self._checks) else self._checks
        kept = checks_ or {}
        found: set[str] = set()
        readings: dict[str, dict | None] = {}
        seen_ids: set[str] = set()

        def reading(pid: str) -> dict | None:
            if pid not in readings:
                stated = self.stated(pid)
                try:
                    terms = checks.metric_terms(self.stated, self._price_source, stated.get("metric"))
                except (Waiting, Unavailable):
                    readings[pid] = None
                else:
                    readings[pid] = checks.read_stated(stated, terms, self.asof)
            return readings[pid]

        for link in self._links:
            lid = link["id"]
            seen_ids.add(lid)
            if lid in kept:
                state = kept[lid]
                if stands_disputed(state):
                    found.add(lid)
                elif state.get("state") == "not yet checked":
                    q = state.get("question")
                    answers = self._ask(q) if isinstance(q, dict) and "questions" in q else self._judged(self, link)
                    if answers is None or any(checks.disputed(a) for a in answers):
                        found.add(lid)
                continue
            if link["relation"] not in ledger.CHECKED:
                continue
            ends = (link["from"], link["to"])
            tested = None if any(market(x) or x not in self.objects for x in ends) else \
                checks.claimed(link["relation"], reading(ends[0]), reading(ends[1]), link["stated"].get("side"))
            if tested is not None:
                if not tested["holds"]:
                    found.add(lid)
                continue
            if link["relation"] in ("and", "or", "then"):
                continue
            answers = self._judged(self, link)
            if answers is None or any(checks.disputed(a) for a in answers):
                found.add(lid)
        for lid, state in kept.items():
            if lid not in seen_ids and stands_disputed(state):
                found.add(lid)
        self._disputed = found
        return found

    def chances(self, oid: str) -> list[dict]:
        return self.stated(oid).get("chances") or []

    def given_by(self, oid: str) -> list[str]:
        return [p for p in self.propositions()
                if any(oid in (*c.get("given", ()), *c.get("given_not", ())) for c in self.chances(p))]

    def propositions(self) -> list[str]:
        return [oid for oid in self.objects if oid.startswith("prop:")]

    def placed(self) -> dict[str, tuple[tuple, list]]:
        out = {}
        for pid in self.propositions():
            stated = self.stated(pid)
            try:
                terms = checks.metric_terms(self.stated, self._price_source, stated.get("metric"))
            except (Waiting, Unavailable):  # a listing the store cannot read now: on no line
                continue
            found = checks.line_of(checks.read_stated(stated, terms, self.asof))
            if found is not None:
                out[pid] = found
        return out

    def watched(self) -> list[str]:
        return [link["to"] for link in self.out("portfolio") if link["relation"] == "watches"]

    def definition(self, pid: str) -> dict:
        """The proposition's question as stated, the fields its outcome turns on; a null optional field and an absent
        one are the same question."""
        return {k: v for k, v in self.stated(pid).items() if k in DEFINITION and v is not None}

    def notes_about(self, oid: str) -> list[dict]:
        return [{"id": link["from"], "text": self.stated(link["from"]).get("text")}
                for link in self._in.get(oid, []) if link["relation"] == "about"]

    def named_markets(self) -> list[str]:
        """Every market the copy names: those it keeps, and those named in its links, chances, or priced-in weights."""
        found = list(self.markets)
        for link in self._links:
            for end in (link["from"], link["to"]):
                if market(end):
                    found.append(end)
        for pid in self.propositions():
            stated = self.stated(pid)
            for c in stated.get("chances") or []:
                for x in (*c.get("given", ()), *c.get("given_not", ()), *(c.get("through") or ())):
                    if market(x):
                        found.append(x)
                if isinstance(c.get("follow"), str) and market(c["follow"]):
                    found.append(c["follow"])
            pi = (stated.get("priced_in") or {}).get("value")
            if isinstance(pi, dict) and isinstance(pi.get("market"), str) and market(pi["market"]):
                found.append(pi["market"])
        return list(dict.fromkeys(found))


def _ask_question(store: Store) -> Callable[[dict | None], list[float] | None]:
    """Ask a saved check question against the store's Judgment cache and judge; None where no answer is available."""
    def ask(question: dict | None) -> list[float] | None:
        if not isinstance(question, dict) or "questions" not in question:
            return None
        try:
            got = store.engine.ask_all([tuple(q) for q in question["questions"]], question["evidence"],
                                       version=question.get("version", 1))
        except (Unavailable, Waiting):
            return None
        return [a["answer"] for a in got]
    return ask


def replies(store: Store) -> list[dict]:
    """What the store forecast and when: each reply's record, oldest first, with its copy read as `Frozen`; a store
    made before the record, or before its first reply ended, is its own state now, its one version."""
    def priced(mid: str) -> str | None:
        return checks.price_source(Inputs(store.engine), mid)

    ask = _ask_question(store)
    found = store.records()
    if found:
        held = _judgments(store)
        return [{"record": r["id"], "at": r["created_at"], "session": r["session"], "turn": r["turn"],
                 "rows": r["rows"],
                 "frozen": Frozen(r["written"], priced, _judged_by(store, held, r["created_at"], ask), ask=ask)}
                for r in found]
    written, _ = store.written()
    return [{"record": None, "at": None, "session": None, "turn": None, "rows": None,
             "frozen": Frozen(written, priced, checks=lambda: link_states(store, questions=True), ask=ask)}]


def _judgments(store: Store) -> list[dict]:
    """Every Judgment the store holds, with the ordered refs its question read (`refs`) and their serialized content."""
    out = []
    for r in store._all("SELECT kind, statement, inputs, answer, created_at FROM judgment ORDER BY rowid"):
        items = [x for x in json.loads(r["inputs"] or "[]") if isinstance(x, dict)]
        refs = [x.get("ref") for x in items[1:]]
        parts = {x.get("ref"): x.get("content") or {} for x in items}
        out.append({"kind": r["kind"], "statement": r["statement"], "refs": refs, "parts": parts,
                    "at": r["created_at"], "answer": None if r["answer"] is None else float(r["answer"])})
    return out


def _frozen_prop_content(store: Store, frozen: Frozen, pid: str) -> dict:
    """A proposition's evidence as the judge read it (evidence.stated_proposition), built from the record's copy."""
    s = frozen.stated(pid)
    mid = s.get("metric")
    of = None
    if mid and ((s.get("value") is not None and s.get("comparison") in ev.RELATIONS)
                or s.get("comparison") == "occurs"):
        if schema.PROXY_METRIC_RE.match(mid):
            try:
                of = ev.metric(Inputs(store.engine), mid)
            except (Unavailable, Waiting):
                of = {"metric": mid.split(":", 1)[1]}
        else:
            m = frozen.stated(mid)
            raw = {"metric": m.get("name") or m.get("id") or mid.split(":", 1)[1], "unit": m.get("unit"),
                   "published by": m.get("resolution_source"),
                   "occurrence": True if m.get("kind") == "occurrence" else None}
            of = {k: v for k, v in raw.items() if v is not None}
    return ev.stated_proposition(s, of)


def _claim_question(link: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """The questions and ordered [first_ref, second_ref] a text claim puts to the judge (checks.py)."""
    rel = link["relation"]
    side = (link["stated"] or {}).get("side")
    a, b = link["from"], link["to"]
    if rel == "asks":
        return checks.market_questions(side or "yes"), [a, b]
    if rel == "way":
        return [checks.EXCLUSION_QUESTION if side == "no" else checks.IMPLICATION_QUESTION], [b, a]
    ma, mb = market(a), market(b)
    if ma and mb:
        return [checks.MARKETS_QUESTION], [a, b]
    if mb:
        return [checks.market_questions("yes")[0]], [a, b]
    if ma:
        return [checks.market_questions("yes")[1]], [b, a]
    return [checks.IMPLICATION_QUESTION], [a, b]


def _judged_by(store: Store, held: list[dict], at: str | None,
               ask: Callable[[dict | None], list[float] | None]):
    """For a record that kept no checks: the judge's reading of a claim as the store held it when the record was made,
    matched on the ordered ends and each proposition's whole evidence (evidence.stated_proposition), or asked now over
    that frozen evidence where no reading was held then."""
    def judged(frozen: Frozen, link: dict) -> list[float] | None:
        asked, refs = _claim_question(link)
        props = {p: _frozen_prop_content(store, frozen, p) for p in refs if not market(p)}
        out: dict[str, float] = {}
        for j in held:
            if j["answer"] is None or (j["kind"], j["statement"]) not in asked or (at is not None and j["at"] > at):
                continue
            if j["refs"] != refs or not all(j["parts"].get(p) == content for p, content in props.items()):
                continue
            out[j["kind"]] = j["answer"]  # in rowid order: the latest by the record's time
        if len(out) == len(asked):
            return [out[k] for k, _ in asked]
        ctx = Inputs(store.engine)
        items = [{"ref": "portfolio" if frozen.asof else None, "content": {"date": frozen.asof}}]
        for ref in refs:
            if market(ref):
                try:
                    items.append(ev.market(ctx, ref))
                except (Unavailable, Waiting):
                    return None
            else:
                items.append({"ref": ref, "content": props[ref]})
        return ask({"questions": asked, "evidence": items, "version": 1})
    return judged


def _result_of(val: Any) -> int | None:
    if isinstance(val, dict):
        val = val.get("result")
    return int(val) if not isinstance(val, bool) and val in (0, 1) else None


def _breakdown(frozen: Frozen, pid: str, settled: dict[str, Any]) -> dict:
    """The record's structure of pid as oneform reads it, reaching only through what it passes as conditions
    (structure.statements with `chances=False`): breakdown links, field-ordered lines, and deadline-reading rungs; and
    each settled market in it as a book at its result."""
    found = structure.statements(frozen, pid, chances=False)
    props, nodes = found["props"], found["nodes"]
    books = []
    for p in props:
        for link in frozen.links(p, "out"):
            res = _result_of(settled.get(link["to"])) if link["relation"] == "asks" else None
            if res is not None:
                value = float(1 - res if link["stated"].get("side") == "no" else res)
                books.append({"link": link["id"], "on": p, "bid": value, "ask": value, "price": value, "depth": 0.0,
                              "market": link["to"], "result": res})
    for mid in nodes:  # a market no proposition asks, standing as itself: its Yes is it true
        res = _result_of(settled.get(mid))
        if res is not None:
            books.append({"link": mid, "on": mid, "bid": float(res), "ask": float(res), "price": float(res),
                          "depth": 0.0, "market": mid, "result": res})
    return {"props": [*props, *nodes], "links": found["links"], "lines": found["lines"], "books": books}


def determine(frozen: Frozen, settled: dict[str, Any]) -> list[dict]:
    """What the settled markets ({market id: 1 or 0}) determine of each proposition a record watched, through that
    record's own structure (_breakdown): its outcome with the statements and settled markets that set it (`links`, the
    links, a reading's placing of its rungs, `{rung}.rungs`, and a proposition's fields, `{proposition}.fields`), or,
    where the structure and the settlements cannot hold together anywhere in it, each set of them that cannot; a
    proposition they leave open is not listed. Each comes with its definition."""
    out = []
    for pid in frozen.watched():
        stated = frozen.definition(pid)
        m = _breakdown(frozen, pid, settled)
        if not m["books"]:
            continue
        found = oneform.decided(m)
        base = {"proposition": pid, "definition": digest(stated), "stated": stated}
        if "cannot_hold" in found:
            out.append({**base, "cannot_hold": [{"statements": [t.removesuffix(":book") for t in
                                                                 found["cannot_hold"]]}]})
            continue
        got = found[pid]
        if got["lo"] >= 1.0 - oneform.TOL:
            outcome, rests = 1, got["lo_rests"]
        elif got["hi"] <= oneform.TOL:
            outcome, rests = 0, got["hi_rests"]
        else:
            continue
        src = sorted({t.removesuffix(":book") for t in rests})
        by_link = {b["link"]: b for b in m["books"]}
        markets = [{"market": by_link[lid]["market"], **({} if market(lid) else {"link": lid}),
                    "result": by_link[lid]["result"]} for lid in src if lid in by_link]
        out.append({**base, "outcome": outcome, "links": [t for t in src if not market(t)], "markets": markets})
    return out


def record_outcomes(store: Store, settled: dict[str, Any]) -> dict[str, Any]:
    """Record the outcome of each version of each proposition the store ever watched: a version is a proposition under
    one definition, and its outcome is what the settled markets determine through each record's own structure, as that
    record had it (determine). A version determined the same way by every record that determines it is recorded once,
    with what each of those records read; one the records determine differently, through statements one record had and
    another did not, is left open, for the resolver; one whose structure and settlements cannot hold together in a
    record is listed with each set that cannot. Returns those recorded now, those recorded before, those the records
    disagree on, and those whose structure and settlements cannot hold together."""
    found: dict[str, list] = {"recorded": [], "already": [], "disagree": [], "cannot_hold": []}
    with store.lock:
        vs = replies(store)
        frozen_by_rec = {v["record"]: v["frozen"] for v in vs}
        by_version: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for v in vs:
            for d in determine(v["frozen"], settled):
                by_version[(d["proposition"], d["definition"])].append({**d, "record": v["record"]})
        for (pid, definition), ds in by_version.items():
            for d in ds:
                if "cannot_hold" in d:
                    found["cannot_hold"].append(d)
            decided = [d for d in ds if "outcome" in d]
            if not decided:
                continue
            if len({d["outcome"] for d in decided}) > 1:
                found["disagree"].append({"proposition": pid, "definition": definition, "stated": decided[0]["stated"],
                                          "by": [{k: d[k] for k in ("record", "outcome", "links", "markets")}
                                                 for d in decided]})
                continue
            first = decided[0]
            frozens = [frozen_by_rec[d["record"]] for d in decided if d["record"] in frozen_by_rec]
            when = known(store, first["stated"], first["outcome"], first["markets"],
                         frozens=frozens, pid=pid, settled=settled)
            oid = store.put_outcome(pid, definition, first["stated"], first["outcome"],
                                    {"links": first["links"], "markets": first["markets"],
                                     "by": [{k: d[k] for k in ("record", "links", "markets")} for d in decided],
                                     **when})
            found["recorded" if oid else "already"].append({**first, "records": [d["record"] for d in decided],
                                                           **({"id": oid} if oid else {})})
    return found


def markets(store: Store) -> list[str]:
    """Every market any version of the store's forecasts names, kept or since released: the markets whose settlement
    the record reads."""
    found: list[str] = []
    for v in replies(store):
        found += v["frozen"].named_markets()
    found += store.ids_of("Market")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------- every version, scored for the time it stood


def _definition_known_at(stated: dict, outcome: int) -> datetime | None:
    """The time an outcome counts as known from its definition alone, kept in one place until the resolver's dated
    evidence gives the announcement's own time: the start of `window_end` in New York for a level or scheduled
    decision on that date, and the start of the day after `window_end` when a window of hitting closes unmet."""
    end = stated.get("window_end")
    if not end:
        return None
    if stated.get("kind") in ("level_at_date", "scheduled_decision"):
        return _day_start(end)
    if not outcome:
        return _day_start((date.fromisoformat(end[:10]) + timedelta(days=1)).isoformat())
    return None


def _settlement_at(store: Store, mid: str, settled: dict[str, Any] | None = None) -> datetime | None:
    """When a settled market's outcome was known from its settlement: the venue's settlement time, or, where the venue
    gives none, when the store read the settlement."""
    entry = (settled or {}).get(mid)
    if isinstance(entry, dict) and entry.get("at") is not None:
        at = _instant(entry["at"])
        if at is not None:
            return at
    from harness.settlements import KIND
    row = store.latest(KIND, mid, None)
    if row is None:
        return None
    said = (store.data_payload(row["id"]) or {}).get("at")
    return _instant(said) or _instant(row["retrieved_at"])


def _decided_at(store: Store, frozen: Frozen, pid: str, outcome: int,
                settled: dict[str, Any]) -> tuple[datetime, str] | None:
    """The earliest moment a prefix of dated settled markets decides `pid == outcome` through `frozen`'s breakdown,
    with the market whose settlement completed that prefix."""
    m = _breakdown(frozen, pid, settled)
    if not m["books"]:
        return None
    times = {b["market"]: _settlement_at(store, b["market"], settled) for b in m["books"]}
    for t in sorted({dt for dt in times.values() if dt is not None}):
        prefix = [b for b in m["books"] if times[b["market"]] is not None and times[b["market"]] <= t]
        sub = oneform.decided({**m, "books": prefix})
        if "cannot_hold" in sub:
            continue
        got = sub[pid]
        rests = got["lo_rests"] if outcome == 1 and got["lo"] >= 1.0 - oneform.TOL else \
            got["hi_rests"] if outcome == 0 and got["hi"] <= oneform.TOL else None
        if rests is None:
            continue
        by_link = {b["link"]: b["market"] for b in prefix}
        resting = sorted({by_link[s.removesuffix(":book")] for s in rests if s.removesuffix(":book") in by_link})
        at_t = [mid for mid in resting if times[mid] == t]
        return t, (at_t[0] if at_t else resting[0])
    return None


def resolution_known(store: Store, pid: str, definition: str, outcome: int) -> tuple[datetime, str] | None:
    """When dated resolution evidence kept for `(pid, definition)` establishes that `outcome` became publicly known,
    with the source it came from; None where no matching dated resolution is held."""
    row = store.latest(RESOLUTION_KIND, f"{pid}:{definition}", None)
    if row is None:
        return None
    payload = store.data_payload(row["id"])
    if not isinstance(payload, dict) or payload.get("status") != "resolved":
        return None
    if _result_of(payload.get("outcome")) != outcome:
        return None
    dt = _instant(payload.get("known_at"))
    if dt is None:
        return None
    return dt, str(payload.get("known_from") or "resolver")


def record_resolutions(store: Store, resolutions: list[dict[str, Any]]) -> dict[str, list]:
    """Keep dated resolution readings (`RESOLUTION_KIND`) and record any newly resolved outcome in `outcome`."""
    out: dict[str, list] = {"recorded": [], "refined_timing": [], "already": [], "inconclusive": [], "open": []}
    day = datetime.now(timezone.utc).date().isoformat()
    with store.lock:
        existing = {(o["proposition"], o["definition"]): o for o in store.outcomes()}
        for r in resolutions:
            pid, defn = r["proposition"], r["definition"]
            status = r.get("status") or "inconclusive"
            retrieved = r.get("retrieved_at") or now()
            payload = {k: r.get(k) for k in ("status", "outcome", "known_at", "known_from", "intraday",
                                             "p_true", "p_false", "model", "evidence", "reason")}
            store.put_data(RESOLUTION_KIND, f"{pid}:{defn}", payload, r.get("request") or {"resolver": True},
                           retrieved_at=retrieved, range=(day, day))
            if status == "open":
                out["open"].append({"proposition": pid, "definition": defn})
                continue
            if status != "resolved" or _result_of(r.get("outcome")) is None:
                out["inconclusive"].append({"proposition": pid, "definition": defn, "reason": r.get("reason")})
                continue
            outcome = int(r["outcome"])
            prev = existing.get((pid, defn))
            if prev is not None:
                if prev["outcome"] == outcome and r.get("known_at"):
                    out["refined_timing"].append({"proposition": pid, "definition": defn,
                                                  "known_at": r["known_at"], "known_from": r.get("known_from")})
                else:
                    out["already"].append({"proposition": pid, "definition": defn})
                continue
            stated = r["stated"]
            when = known(store, stated, outcome, [], pid=pid, definition=defn)
            det = {"resolver": {"model": r.get("model"), "p_true": r.get("p_true"), "p_false": r.get("p_false"),
                                "evidence": r.get("evidence") or []},
                   **when}
            oid = store.put_outcome(pid, defn, stated, outcome, det)
            out["recorded" if oid else "already"].append({"proposition": pid, "definition": defn, "outcome": outcome,
                                                          **({"id": oid} if oid else {}), **when})
    return out


def known(store: Store, stated: dict, outcome: int, markets: list[dict], *,
          frozens: list[Frozen] | tuple[Frozen, ...] = (), pid: str | None = None,
          settled: dict[str, Any] | None = None, definition: str | None = None) -> dict:
    """When an outcome became publicly known under its proposition's rules: the earliest deciding
    prefix of dated settlements across the records that decided it, bounded by dated resolution evidence where held and
    otherwise by the time the definition fixes (`_definition_known_at`). `known_at`, with what gave it (`known_from`):
    the dated source, the definition, or a market."""
    found: list[tuple[datetime, str]] = []
    res = resolution_known(store, pid, definition or digest(stated), outcome) if pid is not None else None
    def_at = _definition_known_at(stated, outcome)
    if res is not None:
        ev_dt, ev_src = res
        kind = stated.get("kind")
        if kind in ("level_at_date", "scheduled_decision"):
            floor = _day_start(stated["window_end"]) if stated.get("window_end") else ev_dt
            found.append((max(ev_dt, floor), ev_src))
        elif not outcome and def_at is not None:  # an unmet hitting window is known once its window closes
            found.append((def_at, "definition"))
        else:
            found.append((ev_dt, ev_src))
    elif def_at is not None:
        found.append((def_at, "definition"))
    if frozens and pid is not None and settled is not None:
        for fz in frozens:
            dec = _decided_at(store, fz, pid, outcome, settled)
            if dec is not None:
                found.append(dec)
    elif markets:
        dated = [(_settlement_at(store, m["market"]), m["market"]) for m in markets]
        if all(dt is not None for dt, _ in dated):
            found.append(max((dt, mid) for dt, mid in dated if dt is not None))
    if not found:
        return {}
    at, source = min(found)
    return {"known_at": at.astimezone(timezone.utc).isoformat(timespec="seconds"), "known_from": source}


def _day_start(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day[:10]), datetime.min.time(), NEW_YORK)


def _instant(text: Any) -> datetime | None:
    """A venue's time or the store's, as an instant: an ISO time, with a space for its T or an hour alone for its
    offset as some venues write it, or a count of seconds since 1970."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return datetime.fromtimestamp(text, timezone.utc)
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.strip().replace(" ", "T", 1).replace("Z", "+00:00")
    if len(t) >= 3 and t[-3] in "+-" and t[-2:].isdigit():
        t += ":00"
    try:
        found = datetime.fromisoformat(t)
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _causal_events(store: Store, vs: list[dict]) -> list[tuple[str, dict, dict | None]]:
    """Each reply and rewind of `store` in causal order, carrying the active reply standing in the store from that
    moment onward (`curr`)."""
    if not vs or (len(vs) == 1 and vs[0]["record"] is None):
        return [("reply", v, v) for v in vs]
    rewinds = [e for e in Trace(store.trace_path).entries()
               if e.get("type") == "rewind" and e.get("store") in (None, store.dir.name)
               and isinstance(e.get("turn"), int) and e.get("at")]
    out: list[tuple[str, dict, dict | None]] = []
    stack: list[dict] = []
    i, j = 0, 0
    while i < len(vs) or j < len(rewinds):
        if j >= len(rewinds):
            take_reply = True
        elif i >= len(vs):
            take_reply = False
        else:
            v, r = vs[i], rewinds[j]
            tv, tr = _instant(v["at"]), _instant(r["at"])
            if tv is not None and tr is not None and tv != tr:
                take_reply = tv < tr
            else:  # same-second tie: a rewind follows the turn(s) it replaces and precedes the turn that retries it
                s, t = r.get("session"), r["turn"]
                on_stack = any(x["session"] == s and isinstance(x.get("turn"), int) and x["turn"] >= t for x in stack)
                take_reply = (not on_stack) or (v["session"] == s and isinstance(v.get("turn"), int)
                                                and isinstance(stack[-1].get("turn"), int)
                                                and v["turn"] > stack[-1]["turn"])
        if take_reply:
            v = vs[i]
            i += 1
            stack.append(v)
            out.append(("reply", v, v))
        else:
            r = rewinds[j]
            j += 1
            stack = [x for x in stack if not (x["session"] == r.get("session")
                                              and isinstance(x.get("turn"), int) and x["turn"] >= r["turn"])]
            out.append(("rewind", r, stack[-1] if stack else None))
    return out


def history(store: Store) -> list[dict]:
    """Each version of each proposition the store watched, a proposition under one definition: the forecast each reply
    made of it, with when it was made and its rule; how the version ended, if it did: revised to another definition,
    no longer watched, or rewound, with where the analyst said why (the reply's session and turn, whose words the trace
    keeps, and its notes about it); and, where its outcome is known, each forecast scored for the spells it stood (the
    squared error and the log loss), and the forecast standing when the outcome became known, apart."""
    vs = replies(store)
    outcomes = {(o["proposition"], o["definition"]): o for o in store.outcomes()}
    out: dict[tuple[str, str], dict] = {}
    spells_of: dict[int, list[dict]] = {}
    by_rec: dict[tuple[str | None, str, str], dict] = {}
    active: dict[tuple[str, str], dict | None] = {}

    for kind, event, curr in _causal_events(store, vs):
        at = event.get("at")
        if kind == "reply":
            fz = event["frozen"]
            rows = {r["id"]: r for r in event["rows"] or []}
            for pid in fz.watched():
                defn = fz.definition(pid)
                definition = digest(defn)
                key = (pid, definition)
                entry = out.setdefault(key, {"proposition": pid, "definition": definition, "stated": defn,
                                             "forecasts": [], "ended": None})
                r = rows.get(pid) or {}
                forecast = {"record": event["record"], "at": at, "p": r.get("probability"),
                            "lo": (r.get("range") or [None, None])[0], "hi": (r.get("range") or [None, None])[1],
                            "rule": (r.get("point") or {}).get("rule")}
                entry["forecasts"].append(forecast)
                spells_of[id(forecast)] = []
                by_rec[(event["record"], pid, definition)] = forecast

        curr_watched: dict[str, str] = {}
        if curr is not None:
            cfz = curr["frozen"]
            for pid in cfz.watched():
                curr_watched[pid] = digest(cfz.definition(pid))

        for key, entry in out.items():
            pid, definition = key
            prev = active.get(key)
            if curr_watched.get(pid) == definition:
                entry["ended"] = None
                now_f = by_rec.get((curr["record"], pid, definition)) if curr is not None else None
                if now_f is not None and now_f["p"] is not None:
                    if prev is not now_f:
                        if prev is not None:
                            spells_of[id(prev)][-1]["until"] = at
                        spells_of[id(now_f)].append({"from": at, "until": None})
                        active[key] = now_f
                else:  # left open: ends the standing forecast without starting a new spell
                    if prev is not None:
                        spells_of[id(prev)][-1]["until"] = at
                        active[key] = None
            else:
                if prev is not None:
                    spells_of[id(prev)][-1]["until"] = at
                    active[key] = None
                if entry["ended"] is None:
                    if kind == "reply" and curr is not None:
                        now_fz = curr["frozen"]
                        why = {"record": curr["record"], "at": at, "session": curr["session"], "turn": curr["turn"],
                               "notes": now_fz.notes_about(pid)}
                        if pid not in curr_watched:
                            entry["ended"] = {"how": "no longer watched", **why}
                        else:
                            entry["ended"] = {"how": "redefined", "to": curr_watched[pid], **why}
                    elif kind == "rewind":
                        entry["ended"] = {"how": "rewound", "at": at, "session": event.get("session"),
                                          "turn": event.get("turn"),
                                          **({"reason": event["reason"]} if event.get("reason") else {})}

    for key, entry in out.items():
        o = outcomes.get(key)
        entry["outcome"] = None
        if not o:
            continue
        how = o["determined_by"]
        res = resolution_known(store, key[0], key[1], o["outcome"])
        if res is not None:
            when = known(store, o["stated"], o["outcome"], how.get("markets") or [], pid=key[0], definition=key[1])
            if how.get("known_from") not in (None, "definition") and how.get("known_at"):
                prev_dt, cur_dt = _instant(how["known_at"]), _instant(when.get("known_at"))
                if prev_dt is not None and (cur_dt is None or prev_dt < cur_dt):
                    when = {"known_at": how["known_at"], "known_from": how["known_from"]}
        else:
            when = {k: how[k] for k in ("known_at", "known_from") if k in how} or \
                known(store, o["stated"], o["outcome"], how.get("markets") or [], pid=key[0], definition=key[1])
        strict_cutoff = "known_at" not in when
        at = when.get("known_at") or o["created_at"]  # where no evidence dates it, when the store recorded it
        entry["outcome"] = {"outcome": o["outcome"], "at": o["created_at"], "id": o["id"], "known_at": at,
                            "known_from": when.get("known_from", "recorded")}
        known_at = _instant(at)

        def past_outcome(ts: str | None) -> bool:
            if ts is None or known_at is None:
                return False
            dt = _instant(ts)
            if dt is None:
                return False
            return dt > known_at if strict_cutoff else dt >= known_at

        last = None
        for f in entry["forecasts"]:
            kept = []
            for s in spells_of[id(f)]:
                if past_outcome(s["from"]):
                    continue
                end = s["until"]
                if end is None or past_outcome(end):
                    end = at
                    last = f
                kept.append({"from": s["from"], "until": end})
            if kept:
                f["until"] = kept[-1]["until"]
                f["spells"] = kept
                f["brier"] = (f["p"] - o["outcome"]) ** 2
                f["log_loss"] = -math.log(max(f["p"] if o["outcome"] else 1.0 - f["p"], 1e-12))
        entry["last_before_outcome"] = last
    return sorted(out.values(), key=lambda e: (e["proposition"], e["forecasts"][0]["at"] or ""))
