"""Live readings. While a store's as-of is today in New York, the store reads again what can change while the day is
open, and keeps each reading that differs from the one before (ONTOLOGY.md 2; MATH.md 3.1, 3.3; TOOLS.md 5): each latest
mark it uses (a stock's snapshot, a pair's last trade or latest rate, a listing abroad's latest price), the book of each
market it keeps, and on its kind's schedule, every other answer it holds about the as-of day: a market's record and its
price history, a company's news and records, a fund's holdings, a ticker's reference, a session's closes, a currency's
rates. Every value that reads one recomputes through its input hashes, as after any write.

Whether an answer is read again depends on its kind and its range, never on whether it was empty: a book with no
orders, a day with no news, a report not yet filed, are read again as a full answer of their kind is, and a book that
gets quotes, news that arrives, a report filed later in the day, come in at the next read due. An answer whose range
closed before the as-of is read once (a past session's closes, a report filed by a past date): the store reads it
again only when the as-of is today and the answer is about today. A session's closes are read again once the session
has closed and its closing prices are published (SETTLE after the close its exchange's calendar gives,
harness.sessions), and never after; a listing abroad on an exchange no calendar is kept for is read on its kind's
schedule. A session's closes are read again only where a live figure reads them, a name the store holds or a channel's
proxy (LIVE): the series a population reads (a comparable's) responds only when a session closes, and is read once for
the as-of. Every other kind is read again AGAIN after the read before it; a kind whose answer does not change within a
day (an identifier's mappings, the Treasury's bills, a fund's holdings, whether a fund files as a money market fund, a
fund's report) is read once on the as-of. When each was last read is held in memory, so a read that finds what was held already is not read again until
it is due; after a restart, each is due by the time its kept answer was retrieved.

A reading differs when what is read from it differs: a mark's price, its time, and where it came from; a book's TOP
best levels on each side, which its price and depth read and the page's ladder shows, and the market's last trade, its
price and its time, read with the book; a market record's listing and whether it is open; a price history's daily
prices; any other answer's payload, but for the request's own id a source stamps on each answer. Nothing reads
deeper, so a change below them alone is not kept; a reading kept keeps its whole payload. A snapshot's payload changes
on every call through its own timestamps, so the payloads are not what is compared. A read that fails, or a refusal,
or a mark the answer does not give, leaves the last reading in place, so a value never turns unavailable because one
reading failed; a market its venue no longer lists keeps its last record. A book read that finds no orders on either
side is a reading like any other: no quotes, so no price, and the interval from 0 to 1; the quotes of the reading
before, since withdrawn, no longer stand. Whether a market is open is its status, read from its record, never
inferred from an empty book. A store whose as-of has passed is read no more, and its last readings stand.

The reads go side by side, outside the store's lock, and only what is kept is written, under it, the round's readings
in one transaction, one version of the store; a tool call holds the lock for its whole run, so a reading lands between
calls, never inside one.
"""

from __future__ import annotations

import logging
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import partial
from typing import Any, Callable

from harness import sessions, touches
from harness.derive import series
from harness.derive.engine import canonical
from harness.derive.markets import TOP, priced
from harness.derive.series import NY, snapshot_mark, trade_mark
from harness.store import PREFETCH_WORKERS, Store, StoreError
from harness.venues import FORMS, Venues, split
from harness.venues.http import now

log = logging.getLogger(__name__)

INTERVAL = 60.0  # seconds from one reading to the next
MINUTE, HOUR = 60, 3600
NYSE = "XNYS"


def home_mark(payload: dict | None) -> dict | None:
    """A listing abroad's latest price, in its currency, at its own time; None where the answer gives none."""
    p = payload or {}
    if p.get("status") != "OK" or p.get("price") is None or not p.get("time"):
        return None
    return {"price": float(p["price"]), "time": p["time"], "currency": p.get("currency")}


# Each kind of latest mark, and what a price reads of it: read again on every round.
MARKS = {"snapshot": snapshot_mark, "last_trade": trade_mark, "home_mark": home_mark}
# Each kind of answer about the as-of day read again on its schedule, by the seconds after the read before it; None,
# read once on the as-of. Each is set by how often its source can change and what reads it. News arrives at any
# moment, and a company's record (a current report, results, a rating) can change within the day. A fund's holdings
# are processed once a day (ETF Global), and a fund's Form N-PORT report is filed once a quarter (made public sixty days
# after the quarter's end): a day's wait for either is nothing against its own age, and asked hourly for every fund
# held, the SEC's feed would be most of a live store's background reads. A ticker's reference, the listings, the
# identifiers' mappings, a ticker's events, a listing's splits and dividends, the Treasury's bills and whether a fund
# is a money market fund change nothing a value reads within a day, nor does which fund's returns match an index's
# values over the sessions to the as-of. A session's closes (CLOSES) are read again once the session has closed, by its
# exchange's calendar, and hourly only where no calendar is kept.
AGAIN: dict[str, int | None] = {
    "news": 15 * MINUTE, "company": HOUR, "constituents": None, "holdings_history": None, "holdings_processed": None, "nport": None, "rates": HOUR, "hourly": HOUR,
    "bars": HOUR, "home": HOUR,
    "ticker_ref": None, "listings": None, "company_listings": None, "cusip_listing": None, "ticker_events": None,
    "dividends": None, "splits": None, "ticker_on": None, "sec_tickers": None, "sec_funds": None, "figi": None, "figi_cusip": None,
    "figi_ticker": None, "figi_class": None, "figi_listing": None, "figi_isin": None, "isin_listings": None,
    "figi_index": None,
    "bills": None,
    "money_market": None, "ncen": None, "listing_sector": None, "listing_symbol": None, "profile": None,
    "company_record": HOUR, "sec_filings": HOUR, "sec_document": None,
    "tracker": None, "closed_at": None,
    "option": None, "pair": None, "yield_curve": None, "option_at": None, "option_contract": None,
    "minutes": None, "option_listings": None,
    "venue_rate": HOUR,  # what a venue pays on a position: variable, as each venue states it
}
# The kinds of a listing's closes read again only for what a live figure reads: a name the store holds, directly or
# through its funds, or a channel's proxy. A currency's rates (`hourly`) are read for every name priced in it, and
# stay on their schedule.
LIVE = ("bars", "home")
# The kinds whose answer about the as-of is a session's closes, read again once the session has closed: a US listing's
# daily bars, a listing abroad's on its exchange, and a pair's hourly rates, whose close for a session is its rate at
# the session's close in New York (harness.derive.series); each with the exchange whose calendar says when.
CLOSES: dict[str, Callable[[Any], str | None]] = {
    "bars": lambda payload: NYSE, "hourly": lambda payload: NYSE,
    "home": lambda payload: (payload or {}).get("mic"),
}
SETTLE = sessions.SETTLE  # seconds after a session's close its closes are read again, its closing prices published
RECORD, HISTORY = 10 * MINUTE, HOUR  # a kept market's record, and its price history
VOLATILE = ("request_id",)  # what a source stamps on each answer, which is no part of what it says

_read: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # each store's reads, by what was read, and when
_read_lock = threading.Lock()
# Each store's marks and books, by kind and subject: when each was last read and found to be the reading the store holds,
# whether it was unchanged or kept. A read that fails, or a reading the store could not keep, leaves it where it was.
_confirmed: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def is_live(store: Store) -> bool:
    """Whether the store's as-of is today in New York, by the clock the store's versions read (harness.touches.today), so
    what can change while the day is open is read again."""
    asof = store.meta("asof")
    return bool(asof) and asof == touches.today()


def reading(kind: str, subject: str, payload: Any, record: dict | None = None) -> Any:
    """What is read from a reading: a mark, or a book's TOP best levels on each side with the market's last trade,
    none on a side with no orders; None for a refusal or an answer that is not a book or a mark, which is never kept
    in place of the last."""
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
    return {"bids": book["bids"][:TOP], "asks": book["asks"][:TOP], "last_trade": shown["last_trade"]}


def said(kind: str, subject: str, payload: Any, asof: str) -> Any:
    """What an answer of another kind says, which a reading of it is compared by: a market's record, its listing and
    whether it is open; a market's price history, its daily prices; any other answer, its payload without the id its
    source stamps on each answer."""
    if kind == "market":
        form = FORMS[split(subject)[0]]
        try:
            return canonical({"listing": form.listing(payload), "open": form.is_open(payload, asof, False)})
        except Exception:
            return None
    if kind == "history":
        try:
            return canonical(FORMS[split(subject)[0]].daily(payload))
        except Exception:
            return None
    if isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if k not in VOLATILE}
    return canonical(payload)


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def due(kind: str, retrieved: datetime, payload: Any, asof: str, at: datetime) -> bool:
    """Whether an answer about the as-of day, last read at `retrieved`, is read again at `at`: a session's closes once
    the session has closed and its closing prices are published, if they were read before, and on an exchange whose
    calendar holds no session that day, never; any other answer when its kind's interval has passed."""
    if kind in CLOSES:
        mic = CLOSES[kind](payload)
        day = date.fromisoformat(asof)
        if mic and sessions.calendar(mic, day) is not None:
            closed = sessions.close_of(mic, day)
            if closed is None:
                return False  # no session on the as-of: nothing more comes
            settled = closed + timedelta(seconds=SETTLE)
            return retrieved < settled <= at
    again = {"market": RECORD, "history": HISTORY}.get(kind, AGAIN.get(kind))
    return again is not None and at >= retrieved + timedelta(seconds=again)


def _last_read(store: Store, key: tuple, kept: str) -> datetime:
    """When an answer was last read: as held in memory, or as its kept answer was retrieved."""
    with _read_lock:
        held = _read.get(store, {}).get(key)
    return held if held is not None else _moment(kept)


def _mark_read(store: Store, key: tuple, at: datetime) -> None:
    with _read_lock:
        _read.setdefault(store, {})[key] = at


def confirmed(store: Store, kind: str, subject: str, covering: tuple[str, str] | None) -> str | None:
    """When a mark or a book was last read and found to be the reading the store holds: as a round of readings found
    it, or, before one has, when the reading it holds was retrieved. None where it holds none."""
    with _read_lock:
        held = _confirmed.get(store, {}).get((kind, subject))
    if held is not None:
        return held
    row = store.latest(kind, subject, covering)
    return row["retrieved_at"] if row is not None else None


def prices_at(store: Store, holdings: dict | None) -> str | None:
    """The moment as of which every price the holdings are valued at is still that holding's latest (MATH.md 3.1): the
    earliest, among them, of when each one's mark was last read and found to be the reading the store holds, so that a
    read that fails, or a reading the store could not keep, holds the moment at that holding's last good read. A
    mark's own time says when its price was struck, which for a mutual fund's net asset value or a thinly traded
    listing is earlier than the moment it is still the latest; a price from a session's close is the latest from that
    close on. A contract's price is its market's book, and a settled contract's is final. None where no holding is
    priced by a mark or a book: the holdings are priced at a session's close."""
    rows = (holdings or {}).get("holds") or {}
    asof = store.meta("asof")
    to = {link["id"]: link["to"] for link in store.links_of("portfolio", "holds")}
    marked, closes = [], []
    for lid, row in rows.items():
        if row.get("price_from") in (None, "cash", "settlement"):
            continue  # dollar cash, which is priced by nothing, or a contract that has settled
        if row.get("contract"):
            got = confirmed(store, "book", to.get(lid, ""), None)
        elif row.get("price_time") and row.get("security"):
            got = confirmed(store, series.mark_kind(row["security"]), row["security"], (asof, asof))
        elif row.get("session"):  # a price from a close: the latest from that close on
            closes.append(_closed(row.get("security") or "", row["session"]))
            continue
        else:
            continue
        marked.append(got or row.get("price_time"))
    if not marked:
        return None
    return min(marked + closes, key=lambda t: _moment(t))


def _closed(ticker: str, session: str) -> str:
    """When a session's close was struck: New York's close for a US listing; for a listing abroad, whose close comes
    earlier on the same date on its own exchange, the start of that date in New York, the latest moment it is certain
    to have closed before."""
    d = date.fromisoformat(session)
    if series.is_abroad(ticker):
        return datetime.combine(d, datetime.min.time(), NY).isoformat()
    return datetime.combine(d, series.session_close(d), NY).isoformat()


def _jobs(store: Store, asof: str, venues: Venues | None, at: datetime) -> list[tuple]:
    """Each reading to take again: every latest mark the store has read on the as-of, and the book of every market it
    keeps; each with the call that takes it, and a market's record, which its last trade reads. And each other answer
    about the as-of day that is due: its kind, subject, the call, and the key its reads are held by."""
    jobs = []
    for kind in MARKS:
        fetch = store.fetchers.get(kind)
        if fetch is not None:
            jobs += [(kind, s, partial(fetch, s, (asof, asof)), None) for s in store.data_subjects(kind, (asof, asof))]
    if venues is not None:
        by_venue: dict[str, dict[str, dict]] = {}
        for mid in store.ids_of("Market"):
            held, record = store.latest("book", mid, None), store.latest("market", mid, None)
            if record is None or split(mid)[0] not in venues.clients:
                continue
            payload = store.data_payload(record["id"])
            if held is not None:
                by_venue.setdefault(split(mid)[0], {})[mid] = payload
            key = ("market", mid, None, None)
            if due("market", _last_read(store, key, record["retrieved_at"]), payload, asof, at):
                venue, vid = split(mid)
                jobs.append(("market", mid, partial(venues.market, venue, vid), key))
            history = store.latest("history", mid, None)
            key = ("history", mid, None, None)
            if history is not None and store.fetchers.get("history") is not None and \
                    due("history", _last_read(store, key, history["retrieved_at"]), None, asof, at):
                jobs.append(("history", mid, partial(store.fetchers["history"], mid, None), key))
        for venue, records in by_venue.items():  # a venue's books read together
            jobs.append(("books", venue, partial(_books, venues.client(venue), records), records))
    live = _live_subjects(store)
    for kind in AGAIN:
        fetch = store.fetchers.get(kind)
        if fetch is None:
            continue
        for subject in store.data_subjects(kind, (asof, asof)):
            if kind in LIVE and subject not in live:
                continue
            row = store.latest(kind, subject, (asof, asof))
            if row is None or row["range_to"] != asof:  # an answer about an earlier day, whose range has closed
                continue
            key = (kind, subject, row["range_from"], row["range_to"])
            payload = store.data_payload(row["id"]) if kind in CLOSES else None
            if due(kind, _last_read(store, key, row["retrieved_at"]), payload, asof, at):
                jobs.append((kind, subject, partial(fetch, subject, (row["range_from"], row["range_to"])), key))
    return jobs


def _live_subjects(store: Store) -> set[str]:
    """The listings a live figure reads the closes of: each name the store holds or names, and each channel's proxy."""
    names = {nid.split(":", 1)[1] for nid in store.ids_of("Name") if ":" in nid}
    proxies = {store.stated_of(cid).get("proxy") for cid in store.ids_of("Factor")}
    return names | (proxies - {None})


def _books(client: Any, records: dict[str, dict]) -> dict[str, dict]:
    """The books of every kept market at one venue, read together, each as a reading of its own."""
    found, when = client.books(records), now()
    return {mid: {"payload": payload, "request": {"method": "books", "markets": sorted(records)},
                  "retrieved_at": when} for mid, payload in found.items()}


def _take(call: Callable[[], dict]) -> dict | None:
    try:
        return call()
    except Exception:  # the last reading stays
        return None


def refresh(store: Store, venues: Venues | None = None, *, workers: int = PREFETCH_WORKERS,
            at: datetime | None = None) -> list[tuple[str, str]]:
    """One round of readings, while the as-of is today: each due taken again side by side, and each that differs from
    the one before kept, the round's together as one version of the store, so that every value reads them as one
    moment. New York's date turned since the store's last version is a version of its own, whatever the as-of
    (Store.record_the_day). The books and markets are read through `venues`, else the store's own. `at` is the
    moment the round is taken at, now unless given. Returns the kind and subject of each reading kept."""
    at = at or datetime.now(timezone.utc)
    with store.lock:
        store.record_the_day()
        if not is_live(store):
            return []
        asof = store.meta("asof")
        jobs = _jobs(store, asof, venues if venues is not None else store.venues, at)
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        got = list(pool.map(lambda job: _take(job[2]), jobs))
    kept, found = [], []  # what was kept, and each mark and book found to be the reading the store holds, and when
    read = []  # each answer read on its schedule: not due again until its schedule says, once the round has landed
    with store.lock, store.transaction():
        if store.meta("asof") != asof:
            return []
        each = []
        for (kind, subject, _, record), g in zip(jobs, got):
            if kind == "books":  # one venue's books, each a reading of its market
                each += [("book", mid, record[mid], (g or {}).get(mid)) for mid in record]
            elif kind in MARKS:
                each.append((kind, subject, record, g))
            else:
                if g is not None:
                    read.append(record)
                each.append((kind, subject, record, g))
        for kind, subject, record, g in each:
            if g is None:
                continue
            if kind in MARKS or kind == "book":
                new = reading(kind, subject, g["payload"], record)
                if new is None:
                    continue
                last = store.latest(kind, subject, (asof, asof) if kind in MARKS else None)
                if last is not None and reading(kind, subject, store.data_payload(last["id"]), record) == new:
                    found.append(((kind, subject), g["retrieved_at"]))
                    continue
            else:
                new = said(kind, subject, g["payload"], asof)
                if new is None:
                    continue
                covering = (record[2], record[3]) if record[2] is not None else None
                last = store.latest(kind, subject, covering)
                if last is not None and said(kind, subject, store.data_payload(last["id"]), asof) == new:
                    continue
            try:
                store.put_data(kind, subject, g["payload"], g["request"], retrieved_at=g["retrieved_at"],
                               range=g.get("range"))
            except StoreError as e:  # a reading the store refuses (a credential in it) leaves the last reading, as
                # a failed read does, and the rest of the round lands; a credential in a client's answer is a defect
                # of the client, logged by what was read and where in it, never with the payload
                log.warning("reading %s of %s refused, its last reading kept: %s", kind, subject, e.message)
                continue
            kept.append((kind, subject))
            if kind in MARKS or kind == "book":
                found.append(((kind, subject), g["retrieved_at"]))
    for record in read:
        _mark_read(store, record, at)
    with _read_lock:  # the round's writes are in: a write the file could not take raised, and confirmed nothing
        _confirmed.setdefault(store, {}).update(found)
    return kept
