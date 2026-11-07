"""Live readings. While a store's as-of is today in New York, the store reads again each latest mark it uses, a
stock's snapshot and a crypto pair's last trade, and the book of each market it keeps, and keeps each reading that
differs from the one before (SPEC 2, 3.1, 3.3). Every value that reads one recomputes through its input hashes, as
after any write.

A reading differs when what is read from it differs: a mark's price, its time, and where it came from; a book's TOP
best levels on each side, which its depth reads and the page's ladder shows, and the price the venue displays.
Nothing reads deeper, so a change below them alone is not kept; a reading kept keeps its whole payload. A snapshot's
payload changes on every call through its own timestamps, so the payloads are not what is compared. A read that
fails, or that gives no mark or no book, leaves the last reading in place, so a value never turns unavailable
because one reading failed. A book with no level on either side is no book: a market that has closed answers so, and
its last book stands. A store whose as-of has passed is read no more, and its last readings stand.

The reads go side by side, outside the store's lock, and only what is kept is written, under it; a tool call holds
the lock for its whole run, so a reading lands between calls, never inside one.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from typing import Any, Callable

from harness.derive.markets import TOP, priced
from harness.derive.series import NY, snapshot_mark, trade_mark
from harness.store import PREFETCH_WORKERS, Store
from harness.venues import FORMS, Venues, split

INTERVAL = 60.0  # seconds from one reading to the next
MARKS = {"snapshot": snapshot_mark, "last_trade": trade_mark}  # each kind of latest mark, and what a price reads of it


def today() -> str:
    """The New York date now."""
    return datetime.now(NY).date().isoformat()


def is_live(store: Store) -> bool:
    """Whether the store's as-of is today, so its marks and books are read again."""
    asof = store.meta("asof")
    return bool(asof) and asof == today()


def reading(kind: str, subject: str, payload: Any, record: dict | None = None) -> Any:
    """What is read from a reading: a mark, or a book's TOP best levels on each side with the price its venue
    displays; None where the read gave neither, which is never kept in place of the last."""
    if kind in MARKS:
        return MARKS[kind](payload)
    if not isinstance(payload, dict) or "error" in payload:  # a venue's refusal, kept by each client as {status, error}
        return None
    venue = split(subject)[0]
    try:
        book = FORMS[venue].levels(payload)
        shown = priced(venue, book, payload, record)
    except Exception:  # a payload that is not a book
        return None
    if not book["bids"] and not book["asks"]:  # no book: a closed market's answer
        return None
    return {"bids": book["bids"][:TOP], "asks": book["asks"][:TOP], "price": shown["price"]}


def _jobs(store: Store, asof: str, venues: Venues | None) -> list[tuple[str, str, Callable[[], dict], dict | None]]:
    """Each reading to take again: every latest mark the store has read on the as-of, and the book of every market it
    keeps; each with the call that takes it, and a market's record, which its displayed price reads."""
    jobs = []
    for kind in MARKS:
        fetch = store.fetchers.get(kind)
        if fetch is not None:
            jobs += [(kind, s, partial(fetch, s, (asof, asof)), None) for s in store.data_subjects(kind, (asof, asof))]
    if venues is not None:
        for mid in store.ids_of("Market"):
            held, record = store.latest("book", mid, None), store.latest("market", mid, None)
            if held is None or record is None:
                continue
            found = store.data_payload(record["id"])
            jobs.append(("book", mid, partial(venues.client(split(mid)[0]).book, found), found))
    return jobs


def _take(call: Callable[[], dict]) -> dict | None:
    try:
        return call()
    except Exception:  # the last reading stays
        return None


def refresh(store: Store, venues: Venues | None = None, *, workers: int = PREFETCH_WORKERS) -> list[tuple[str, str]]:
    """One round of readings, while the as-of is today: each taken again side by side, and each that differs from the
    one before kept. The books are read through `venues`, else the store's own. Returns the kind and subject of each
    reading kept."""
    with store.lock:
        if not is_live(store):
            return []
        asof = store.meta("asof")
        jobs = _jobs(store, asof, venues if venues is not None else store.venues)
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        got = list(pool.map(lambda job: _take(job[2]), jobs))
    kept = []
    with store.lock:
        if store.meta("asof") != asof:
            return []
        for (kind, subject, _, record), g in zip(jobs, got):
            new = reading(kind, subject, g["payload"], record) if g is not None else None
            if new is None:
                continue
            last = store.latest(kind, subject, (asof, asof) if kind in MARKS else None)
            if last is not None and reading(kind, subject, store.data_payload(last["id"]), record) == new:
                continue
            store.put_data(kind, subject, g["payload"], g["request"], retrieved_at=g["retrieved_at"],
                           range=g.get("range"))
            kept.append((kind, subject))
    return kept
