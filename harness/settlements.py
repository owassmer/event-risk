"""Settlements: which markets the store keeps have settled, and how, read from their venues; and the outcomes they
determine, recorded in the forecast record (ONTOLOGY.md 2; harness.record.record_outcomes).

A market settles at 1 when YES wins and at 0 when NO does, each venue saying so in its own structured fields, never in
prose (each client's `settlement`): Polymarket once a market is closed and its UMA resolution is "resolved", by YES's
settled price; Kalshi once its status is finalized, by its result; Polymarket US once its status is resolved or
settled, by its settlement price. A market that settles anywhere else, as a 50/50 or void resolution, a scalar, or a draw does, is settled
with no result, and determines nothing.

When. Settlement comes when a market's question is answered, often weeks or months after the conversation that kept
it, and nothing the store derives reads it, so it is read where it cannot slow the work: when a store is opened with
its keys, and again when a reply ends, each time for every market any record names, kept or since released, not yet
settled, outside the store's lock. A settlement read is kept as data, once, so a saved store keeps what settled and
how, and a market once settled is not read again. The live readings, every minute, read only marks and books: a
settled market's book is empty, and its settlement waits for the next of these reads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from harness import record
from harness.store import Store
from harness.venues import Venues, split
from harness.venues.http import VenueError

KIND = "settlement"


def results(store: Store) -> dict[str, int]:
    """Each kept settlement's result, by market: 1 or 0; a market settled with no result is left out."""
    out: dict[str, int] = {}
    for mid in store.data_subjects(KIND, None):
        row = store.latest(KIND, mid, None)
        found = store.data_payload(row["id"]) if row else None
        if isinstance(found, dict) and found.get("settled") and found.get("result") in (0, 1):
            out[mid] = int(found["result"])
    return out


def _unsettled(store: Store) -> list[tuple[str, dict]]:
    """The markets any version of the store's forecasts names (record.markets), kept or since released, that no
    settlement has been read for, each with its record: a market's record stays as data once it is released."""
    done = set(store.data_subjects(KIND, None))
    out = []
    for mid in record.markets(store):
        held = store.latest("market", mid, None)
        if mid not in done and held is not None:
            out.append((mid, store.data_payload(held["id"])))
    return out


def read(store: Store, venues: Venues | None) -> dict[str, Any]:
    """Read the settlement of every market any record names not yet settled, keep each that has settled, and record the
    outcomes the settled markets determine. Returns the markets read, those settled now, and what record_outcomes
    recorded. A venue that does not answer leaves its markets for the next read."""
    if venues is None:
        return {"read": 0, "settled": {}, "outcomes": None}
    with store.lock:
        todo = _unsettled(store)
    got: list[tuple[str, dict]] = []
    for mid, held in todo:  # outside the store's lock
        venue, _ = split(mid)
        try:
            found = venues.client(venue).settlement(held)
        except (VenueError, LookupError, KeyError, ValueError):
            continue
        if (found.get("payload") or {}).get("settled"):
            got.append((mid, found))
    with store.lock:
        for mid, found in got:
            day = datetime.now(timezone.utc).date().isoformat()
            store.put_data(KIND, mid, found["payload"], found["request"], retrieved_at=found["retrieved_at"],
                           range=(day, day))
        settled = results(store)
        outcomes = record.record_outcomes(store, settled) if settled else None
    return {"read": len(todo), "settled": {mid: f["payload"].get("result") for mid, f in got}, "outcomes": outcomes}
