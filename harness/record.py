"""The forecast record: each reply's rows as they stood, and each watched proposition's outcome once the markets in
its breakdown determine it. It is the history forecasts are scored against, and nothing the store derives reads it.

A reply's record. When a reply ends, stopped or not, the store records each watched proposition's row as the ledger
gives it, each field with the Computation or Judgment behind it (its basis, which the store keeps while a record names
it); a copy of what the analyst had written, as a checkpoint holds it, each page it cites kept once by its hash;
whether the reply was stopped; and the versions it was made with: each derivation's, the analyst model and its effort,
the judge's alias and the deployment that last answered, and a digest of what the analyst reads. A record is never
changed or removed, and a rewind leaves it where it is: the turns a rewind replaces were said, and the trace keeps them
too. Each row is worked out on its own, in the ledger's order, so a row that cannot be worked out leaves
the others as they stand: it is left out of the record's rows, and the copy still holds what the analyst had written
of the event. The store's trace then keeps a record_error entry naming the proposition and the record it is missing
from; a record that cannot be made at all is not made, and its entry names no proposition. Each entry has the
session, the turn, the error, and the lines it arose at, and no conversation or page reads it; the same line goes to
standard error, where the one running the page sees it. A reply ends whatever its record does.

An outcome. A market settles at 1 when it resolves YES and 0 when it resolves NO; the venue's record says which, and
the caller reads it, for every market any record names, kept or since released (`markets`). A forecast is of a
version: a proposition under one definition, its stated fields as they stood. Each record keeps its own copy of what
the analyst had written, and a version's outcome is determined through that copy's links, never the store's links
now, so a forecast whose event was later edited or removed is still decided on the question it asked. The settled
markets in a record's breakdown of the proposition, each a book at its result through its asks link (one minus the
result under the complement), whether or not its price was taken, since a settlement says whether the outcomes the
market asks happened, are read through the breakdown's links as one set of probabilities (oneform.decided): where the
proposition's probability can only be 1 it is true, and where it can only be 0 false. Where the links and the
settlements cannot hold together, one of the record's claims is false, and that record decides nothing. A version
decided the same way by every record that decides it is recorded once, with what each read; one the records decide
differently, through links one had and another did not, stays open, for the resolver. A store made before the record is
its own state now, its one version.

The history (`history`). Every version is scored on its own question for the time it stood: each reply's forecast of
it, until the next reply or the outcome; the last forecast before the outcome is reported apart; and a version that
ended before its outcome, redefined or no longer watched, carries where the analyst said why: the reply's session and
turn, whose words the trace keeps, and its notes about the proposition.
"""

from __future__ import annotations

import math
import sys
import traceback
from collections import defaultdict
from typing import Any

from harness import credentials
from harness.derive.engine import digest
from harness.derive import oneform
from harness.derive.structure import breakdown_links, market, reach, standing
from harness.ledger import row, watched_events
from harness.store import SCHEMA_VERSION, Store
from harness.trace import Trace, now

DEFINITION = ("title", "text", "metric", "comparison", "value", "window_start", "window_end", "kind",
              "resolution_source")


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
    left: list[tuple[str, Exception]] = []
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
            rid = store.put_record(session=session, turn=turn, stopped=stopped, rows=rows, written=written,
                                   texts=texts, versions=versions(store, made))
    except Exception as e:
        _failed(store, session, turn, e)
    for pid, e in left:
        _failed(store, session, turn, e, proposition=pid, record=rid)
    return rid


def _failed(store: Store, session: str | None, turn: int | None, error: Exception, *, proposition: str | None = None,
            record: str | None = None) -> None:
    """A row left out of a reply's record, or a record not made, in the store's trace and on standard error; neither
    write can raise."""
    try:
        said = f"{type(error).__name__}: {error}"
        where = [f"{f.filename}:{f.lineno} in {f.name}" for f in traceback.extract_tb(error.__traceback__)[-6:]]
        if credentials.find([said, where], names=False):  # an error's text can quote a secret; its name cannot
            said, where = type(error).__name__, []
    except Exception:
        said, where = type(error).__name__, []
    try:
        row_of = {"proposition": proposition, "record": record} if proposition else {}
        Trace(store.trace_path).append({"type": "record_error", "session": session, "turn": turn, "at": now(),
                                        **row_of, "error": said, "where": where})
    except Exception:
        pass
    try:
        what = f"left out {proposition}" if proposition else "failed"
        print(f"the forecast record for {session} turn {turn} {what}: {said}", file=sys.stderr, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- outcomes, from each record as it was made


class Frozen:
    """A record's copy of what the analyst had written, read as the propagation reads a store: each object's stated
    fields, and each link by its two ends. A store with no record yet is read as it stands, its one version."""

    def __init__(self, written: dict):
        self.objects = {o["id"]: o for o in written.get("object") or []}
        self._out: dict[str, list[dict]] = defaultdict(list)
        self._in: dict[str, list[dict]] = defaultdict(list)
        for row in written.get("link") or []:
            link = {"id": row["id"], "relation": row["relation"], "from": row["from_id"], "to": row["to_id"],
                    "stated": row.get("stated") or {}}
            self._out[link["from"]].append(link)
            self._in[link["to"]].append(link)
        self.markets = [m["id"] for m in written.get("market") or []]

    def out(self, oid: str) -> list[dict]:
        return list(self._out.get(oid, []))

    def both(self, oid: str) -> list[dict]:
        return self.out(oid) + list(self._in.get(oid, []))

    def stated(self, oid: str) -> dict:
        return (self.objects.get(oid) or {}).get("stated") or {}

    def watched(self) -> list[str]:
        return [link["to"] for link in self.out("portfolio") if link["relation"] == "watches"]

    def definition(self, pid: str) -> dict:
        """The proposition's question as stated, the fields its outcome turns on."""
        return {k: v for k, v in self.stated(pid).items() if k in DEFINITION}

    def notes_about(self, oid: str) -> list[dict]:
        return [{"id": link["from"], "text": self.stated(link["from"]).get("text")}
                for link in self._in.get(oid, []) if link["relation"] == "about"]

    def named_markets(self) -> list[str]:
        """Every market the copy names: those it keeps, and those its asks links quote."""
        quoted = [link["to"] for links in self._out.values() for link in links if link["relation"] == "asks"]
        return list(dict.fromkeys(self.markets + quoted))


def replies(store: Store) -> list[dict]:
    """What the store forecast and when: each reply's record, oldest first, with its copy read as `Frozen`; a store
    made before the record, or before its first reply ended, is its own state now, its one version."""
    found = store.records()
    if found:
        return [{"record": r["id"], "at": r["created_at"], "session": r["session"], "turn": r["turn"],
                 "rows": r["rows"], "frozen": Frozen(r["written"])} for r in found]
    written, _ = store.written()
    return [{"record": None, "at": None, "session": None, "turn": None, "rows": None, "frozen": Frozen(written)}]


def _breakdown(frozen: Frozen, pid: str, settled: dict[str, int]) -> dict:
    """The record's breakdown of pid as oneform reads it: the propositions its links relate to pid, the links, and each
    settled market that asks one of them, as a book at its result. A market at an end of a link stands as the
    proposition that asks it, or as itself where none does, as the store's structure reads it (structure.breakdown_links),
    and one that stands as itself is a book at its result where it settled."""
    props, mids = reach(frozen.both, lambda o: [], lambda o: [], pid)
    members = set(props)
    stands = standing(frozen.both, mids, members)
    links = breakdown_links([link for here in (*props, *mids) for link in (frozen.both(here) if market(here)
                                                                          else frozen.out(here))],
                            members, mids, stands)
    books = []
    for p in props:
        for link in frozen.out(p):
            if link["relation"] == "asks" and settled.get(link["to"]) is not None:
                result = int(settled[link["to"]])
                value = float(1 - result if link["stated"].get("side") == "no" else result)
                books.append({"link": link["id"], "on": p, "bid": value, "ask": value, "price": value, "depth": 0.0,
                              "market": link["to"], "result": result})
    nodes = [mid for mid in mids if mid not in stands]
    for mid in nodes:  # a market no proposition asks, standing as itself: its Yes is it true
        if settled.get(mid) is not None:
            result = int(settled[mid])
            books.append({"link": mid, "on": mid, "bid": float(result), "ask": float(result), "price": float(result),
                          "depth": 0.0, "market": mid, "result": result})
    return {"props": [*props, *nodes], "links": links, "books": books}


def determine(frozen: Frozen, settled: dict[str, int]) -> list[dict]:
    """What the settled markets ({market id: 1 or 0}) determine of each proposition a record watched, through that
    record's own links: its outcome with the links and settled markets that set it, or, where the links and the
    settlements cannot hold together anywhere in its breakdown, each set of them that cannot; a proposition they leave
    open is not listed. Each comes with its definition."""
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


def record_outcomes(store: Store, settled: dict[str, int]) -> dict[str, Any]:
    """Record the outcome of each version of each proposition the store ever watched: a version is a proposition under
    one definition, and its outcome is what the settled markets determine through each record's own links, as that
    record had them. A version determined the same way by every record that determines it is recorded once, with what
    each of those records read; one the records determine differently, through links one record had and another did
    not, is left open, for the resolver; one whose links and settlements cannot hold together in a record is listed
    with each set that cannot. Returns those recorded now, those recorded before, those the records disagree on, and
    those whose links and settlements cannot hold together."""
    found: dict[str, list] = {"recorded": [], "already": [], "disagree": [], "cannot_hold": []}
    with store.lock:
        by_version: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for v in replies(store):
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
            oid = store.put_outcome(pid, definition, first["stated"], first["outcome"],
                                    {"links": first["links"], "markets": first["markets"],
                                     "by": [{k: d[k] for k in ("record", "links", "markets")} for d in decided]})
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


def history(store: Store) -> list[dict]:
    """Each version of each proposition the store watched, a proposition under one definition: the forecast each reply
    made of it, with when it was made and its rule, scored where its outcome is known (the squared error and the log
    loss, each for the time the forecast stood, until the next reply or the outcome); the last forecast before the
    outcome, apart; and how the version ended, if it did: revised to another definition, or no longer watched, with
    where the analyst said why (the reply's session and turn, whose words the trace keeps, and its notes about it)."""
    vs = replies(store)
    outcomes = {(o["proposition"], o["definition"]): o for o in store.outcomes()}
    out: dict[tuple[str, str], dict] = {}
    for k, v in enumerate(vs):
        fz = v["frozen"]
        rows = {r["id"]: r for r in v["rows"] or []}
        for pid in fz.watched():
            definition = digest(fz.definition(pid))
            key = (pid, definition)
            entry = out.setdefault(key, {"proposition": pid, "definition": definition, "stated": fz.definition(pid),
                                         "forecasts": [], "ended": None})
            row = rows.get(pid) or {}
            entry["forecasts"].append({"record": v["record"], "at": v["at"], "p": row.get("probability"),
                                       "lo": (row.get("range") or [None, None])[0],
                                       "hi": (row.get("range") or [None, None])[1],
                                       "rule": ((row.get("point") or {}).get("rule"))})
        if k + 1 < len(vs):
            after = vs[k + 1]["frozen"]
            now = set(after.watched())
            for pid in fz.watched():
                key = (pid, digest(fz.definition(pid)))
                if out[key]["ended"]:
                    continue
                why = {"record": vs[k + 1]["record"], "at": vs[k + 1]["at"], "session": vs[k + 1]["session"],
                       "turn": vs[k + 1]["turn"], "notes": after.notes_about(pid)}
                if pid not in now:
                    out[key]["ended"] = {"how": "no longer watched", **why}
                elif digest(after.definition(pid)) != key[1]:
                    out[key]["ended"] = {"how": "redefined", "to": digest(after.definition(pid)), **why}
    for key, entry in out.items():
        o = outcomes.get(key)
        entry["outcome"] = {"outcome": o["outcome"], "at": o["created_at"], "id": o["id"]} if o else None
        if not o:
            continue
        y = o["outcome"]
        stood = [f for f in entry["forecasts"]
                 if f["p"] is not None and (f["at"] is None or f["at"] <= o["created_at"])]
        for f, nxt in zip(stood, stood[1:] + [None]):
            f["until"] = nxt["at"] if nxt else o["created_at"]
            f["brier"] = (f["p"] - y) ** 2
            f["log_loss"] = -math.log(max(f["p"] if y else 1.0 - f["p"], 1e-12))
        entry["last_before_outcome"] = stood[-1] if stood else None
    return sorted(out.values(), key=lambda e: (e["proposition"], e["forecasts"][0]["at"] or ""))
