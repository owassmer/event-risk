"""Observations shared across stores: one cache per machine, beside the stores' root (`observations/`), under the
clients' seam (ONTOLOGY.md 2). What a source answered about a range that has closed does not change, so it is read once on
a machine and serves every store: two years of the market are read once, not once per store. What it answered about
today serves another store while a store that held it would not read it again (harness.readings.due): the same
answer that day, which a second store reading again would only wait for. Each store still keeps, as its own data,
what its computations read, with when it was read, so a saved store and the example open with no keys and no cache,
and a store reads a shared answer again on the schedule its retrieval sets, as if it had read it itself.

What it holds:
- `answers`: a client's answer, as it returned it (its payload, request, retrieval time and range), for a kind and
  subject over a range that closed before the day it was read (its last day past in New York): a past as-of's
  reference, a fund's holdings by a past date, a filed report, a past window's prices. A failed read, or a malformed
  request of ours, is never an answer, and nothing of it is kept. OpenFIGI's mappings, which carry no date, are kept
  here too, by subject, in the form their client keeps them (its `parse`), and served whatever range is asked: one
  that maps its subject to listings, until what it names no longer resolves (a US listing Massive no longer has, or
  holds as another security), when the look-through reads it again (harness.derive.portfolio); one that maps it to
  nothing, only on the day it was read, so a security mapped since is found the next day.
- `today`: an answer about a range that ends today, of a kind the store reads again on a schedule (harness.readings),
  served for exactly that range while it is not due again: one read once on the as-of, all day; a session's closes,
  until its exchange's session has closed and settled; any other, for its kind's interval. It is never served for a
  closed range: an answer read before a day's close is not what that day turned out to be. Answers about earlier
  days are dropped when the file is opened. Latest marks and books, which are about the moment, are not kept.
- `closes`: every US exchange listing's close on each session read, as Massive's grouped daily bars give them
  (`market_days`, one request a session), as traded that day. They are read only for the sessions that establish
  which fund's returns match an index's values (harness.venues.trackers), which reads every listing's close on each.

The file is SQLite, opened by each process that reads it, with a busy wait so two processes read and write it side
by side, one writer at a time. The closes are kept by session, each session's appended after the last, and read by
session. The payloads are kept compressed. Nothing in it is any store's: deleting it costs only reads.

Many subjects are looked up together, one query a chunk (`CHUNK`), and one subject is a chunk of one, so both read by
one rule. A query a subject cost little alone (5,365 listings' sectors in 0.07 s) and a great deal beside a thread
computing in Python: each query hands back the interpreter's lock and waits to take it again, so on a first read beside
the look-through the same lookups took 4.5 s (measured, Nov 12, 2026).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "observations" / "observations.sqlite"
NY = ZoneInfo("America/New_York")
# The kinds an answer is shared for: those whose answer about a closed range is the source's whole answer about it.
# A mark, a book, and a market's record are about the moment they are read.
SHARED = ("ticker_ref", "ticker_on", "listings", "company_listings", "cusip_listing", "ticker_events", "bars",
          "dividends", "splits", "constituents", "holdings_history", "holdings_processed", "nport", "news", "company", "home", "hourly", "rates", "figi",
          "figi_cusip", "figi_ticker", "figi_class", "figi_listing", "figi_isin", "isin_listings", "figi_index",
          "sec_tickers",
          "sec_funds", "bills",
          "money_market", "ncen", "tracker", "closed_at",
          "nport_report", "ncen_report", "listing_sector", "listing_symbol", "profile", "company_record",
          "sec_filings", "sec_document", "option", "pair", "yield_curve", "option_at", "option_contract",
          "minutes", "option_listings")
CHUNK = 500  # the subjects one lookup asks at once

# The kinds whose answer is about no date: OpenFIGI maps an identifier as it stands, and Coinbase Exchange lists a pair
# or does not, with no as-of to ask either for, so the answer is kept and served by subject, whatever range a store
# asks it under.
UNDATED = ("figi", "figi_cusip", "figi_ticker", "figi_class", "figi_listing", "figi_isin", "isin_listings",
           "figi_index", "pair",
           "listing_sector", "listing_symbol", "profile")  # the last three name their month in their subject

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    kind TEXT NOT NULL, subject TEXT NOT NULL, range_from TEXT NOT NULL, range_to TEXT NOT NULL,
    retrieved_at TEXT NOT NULL, request TEXT NOT NULL, payload BLOB NOT NULL,
    PRIMARY KEY (kind, subject, range_from, range_to)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS closes (
    day TEXT NOT NULL, ticker TEXT NOT NULL, c REAL NOT NULL, PRIMARY KEY (day, ticker)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS today (
    kind TEXT NOT NULL, subject TEXT NOT NULL, range_from TEXT NOT NULL, range_to TEXT NOT NULL,
    retrieved_at TEXT NOT NULL, request TEXT NOT NULL, payload BLOB NOT NULL,
    PRIMARY KEY (kind, subject, range_from, range_to)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS market_days (
    day TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL, request TEXT NOT NULL, listings INTEGER NOT NULL);
"""


def today() -> str:
    """The New York date now."""
    return datetime.now(NY).date().isoformat()


def closed(range_: Iterable[str] | None) -> bool:
    """Whether a range has closed: its last day is past in New York."""
    found = list(range_ or ())
    return len(found) == 2 and bool(found[1]) and found[1] < today()


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def read_today(retrieved_at: str) -> bool:
    """Whether an answer was read on today's New York date."""
    return _moment(retrieved_at).astimezone(NY).date().isoformat() == today()


def scheduled(kind: str) -> bool:
    """Whether a kind's answers about today are read again on a schedule (harness.readings.AGAIN), and so served
    across stores while they are not due."""
    from harness.readings import AGAIN  # the schedule is the readings'; imported here, where it is read

    return kind in AGAIN and kind in SHARED


class Observations:
    def __init__(self, path: Path | str = PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write = threading.Lock()  # one writer at a time in a process; SQLite's busy wait across processes
        self._db().executescript(SCHEMA)
        with self._write:  # what no store will be served again: answers about earlier days
            self._db().execute("DELETE FROM today WHERE range_to < ?", (today(),))

    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            self._local.db = db
        return db

    # ---------------------------------------------------------------- answers

    def _rows(self, table: str, kind: str, subjects: list[str], covering: tuple[str, str] | None) -> dict[str, tuple]:
        """Each subject's row of a kind in `table` (retrieved_at, request, payload, range_from, range_to), one query a
        chunk: the row over exactly `covering`, or with `covering` None the one retrieved last."""
        found: dict[str, tuple] = {}
        db = self._db()
        for i in range(0, len(subjects), CHUNK):
            chunk = subjects[i:i + CHUNK]
            asked = f"kind = ? AND subject IN ({','.join('?' * len(chunk))})"
            if covering is not None:
                rows = db.execute(f"SELECT subject, retrieved_at, request, payload, range_from, range_to FROM {table} "
                                  f"WHERE {asked} AND range_from = ? AND range_to = ?",
                                  (kind, *chunk, covering[0], covering[1])).fetchall()
            else:
                rows = db.execute(f"SELECT subject, retrieved_at, request, payload, range_from, range_to FROM (SELECT "
                                  f"*, ROW_NUMBER() OVER (PARTITION BY subject ORDER BY retrieved_at DESC) AS n FROM "
                                  f"{table} WHERE {asked}) WHERE n = 1", (kind, *chunk)).fetchall()
            for r in rows:
                found[r[0]] = r[1:]
        return found

    def answers(self, kind: str, subjects: list[str], covering: tuple[str, str]) -> dict[str, dict]:
        """The answer kept for a kind and each subject over exactly this range, as its client returned it, by subject:
        those kept."""
        return {s: {"payload": json.loads(zlib.decompress(r[2])), "request": json.loads(r[1]), "retrieved_at": r[0],
                    "range": (r[3], r[4])} for s, r in self._rows("answers", kind, subjects, covering).items()}

    def answer(self, kind: str, subject: str, covering: tuple[str, str]) -> dict | None:
        return self.answers(kind, [subject], covering).get(subject)

    def undated_many(self, kind: str, subjects: list[str], covering: tuple[str, str],
                     parse: Callable[[str], Any] = lambda subject: None) -> dict[str, dict]:
        """The latest answer kept for an undated kind's subjects, under the range now asked, in its client's form now
        (`parse`, by subject): one that names what it maps (`status` OK) whatever day it was read, until it is read
        again (`again`); one that names nothing only on the day it was read. By subject: those served."""
        out = {}
        for s, r in self._rows("answers", kind, subjects, None).items():
            payload = json.loads(zlib.decompress(r[2]))
            form = parse(s)
            if form is not None and (not isinstance(payload, dict) or payload.get("parse") != form):
                continue
            if (not isinstance(payload, dict) or payload.get("status") != "OK") and not read_today(r[0]):
                continue
            out[s] = {"payload": payload, "request": json.loads(r[1]), "retrieved_at": r[0], "range": tuple(covering)}
        return out

    def current_many(self, kind: str, subjects: list[str], covering: tuple[str, str]) -> dict[str, dict]:
        """The answer kept about today for exactly this range, while a store holding it would not read it again, by
        subject: those served."""
        from harness.readings import due

        out, at = {}, datetime.now(timezone.utc)
        for s, r in self._rows("today", kind, subjects, covering).items():
            if not read_today(r[0]):
                continue
            payload = json.loads(zlib.decompress(r[2]))
            if due(kind, _moment(r[0]), payload, covering[1], at):
                continue
            out[s] = {"payload": payload, "request": json.loads(r[1]), "retrieved_at": r[0],
                      "range": (covering[0], covering[1])}
        return out

    def current(self, kind: str, subject: str, covering: tuple[str, str]) -> dict | None:
        return self.current_many(kind, [subject], covering).get(subject)

    def keep(self, kind: str, subject: str, covering: tuple[str, str], got: dict) -> None:
        """Keep an answer whose range has closed, under the range it was asked for; an undated kind's whatever its
        range; and one about a range ending today, of a scheduled kind, for the day."""
        if kind in SHARED and kind not in UNDATED and covering[1] == today() and scheduled(kind) \
                and read_today(got["retrieved_at"]):
            with self._write:
                self._db().execute(
                    "INSERT OR REPLACE INTO today VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (kind, subject, covering[0], covering[1], got["retrieved_at"],
                     json.dumps(got["request"], sort_keys=True),
                     zlib.compress(json.dumps(got["payload"], separators=(",", ":")).encode(), 6)))
            return
        if kind not in SHARED or (kind not in UNDATED and (not closed(got.get("range")) or not closed(covering))):
            return
        with self._write:
            self._db().execute(
            "INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, subject, covering[0], covering[1], got["retrieved_at"],
                 json.dumps(got["request"], sort_keys=True),
                 zlib.compress(json.dumps(got["payload"], separators=(",", ":")).encode(), 6)))

    def shared(self, kind: str, fetcher: Callable[[str, Any], dict]) -> Callable[[str, Any], dict]:
        """A fetcher that answers from what this machine has read, for a closed range, or about today while it is not
        due again, in the fetcher's form now where it declares one (`parse`, or a function of the subject giving it: a
        kept answer in another is read again),
        and keeps what it reads; its `again` reads subjects from the source whatever is kept, and keeps what it
        reads."""
        if kind not in SHARED:
            return fetcher
        declared = getattr(fetcher, "parse", None)

        def form(subject: str) -> Any:
            # a fetcher that reads several sources by subject declares each one's form, by subject (analyst.by_pair)
            return declared(subject) if callable(declared) else declared

        def kept(subjects: list[str], covering: Any) -> dict[str, dict]:
            """What this machine keeps of each subject that serves the range asked, by subject."""
            if covering is None:
                return {}
            covering = tuple(covering)
            if kind in UNDATED:
                return self.undated_many(kind, subjects, covering, form)
            if closed(covering):
                found = self.answers(kind, subjects, covering)
            else:
                found = self.current_many(kind, subjects, covering) if covering[1] == today() else {}
            return {s: f for s, f in found.items() if form(s) is None or (f["payload"] or {}).get("parse") == form(s)}

        def read(subject: str, covering: Any) -> dict:
            found = kept([subject], covering).get(subject)
            if found is not None:
                return found
            got = fetcher(subject, covering)
            if covering is not None:
                self.keep(kind, subject, tuple(covering), got)
            return got

        many = getattr(fetcher, "many", None)
        if many is not None:
            def read_many(subjects, covering):
                subjects = list(dict.fromkeys(subjects))
                out = kept(subjects, covering)
                todo = [s for s in subjects if s not in out]
                if todo:
                    got = dict(many(todo, covering))
                    for s, g in got.items():
                        if g is not None and covering is not None:
                            self.keep(kind, s, tuple(covering), g)
                    out.update(got)
                return out
            read.many = read_many  # type: ignore[attr-defined]

        def again(subjects, covering) -> dict:
            subjects = list(dict.fromkeys(subjects))
            got = dict(many(subjects, covering)) if many is not None else {s: fetcher(s, covering) for s in subjects}
            for s, g in got.items():
                if g is not None and covering is not None:
                    self.keep(kind, s, tuple(covering), g)
            return got

        read.again = again  # type: ignore[attr-defined]
        return read

    # ---------------------------------------------------------------- the market's closes, by session

    def market_days(self) -> dict[str, dict]:
        """Each session whose closes are kept, with when and how they were read and how many listings."""
        return {d: {"retrieved_at": r, "request": json.loads(q), "listings": n}
                for d, r, q, n in self._db().execute("SELECT day, retrieved_at, request, listings FROM market_days")}

    def keep_day(self, day: str, rows: list[dict], request: dict, retrieved_at: str) -> None:
        """A session's grouped bars, each listing's close as traded that day, kept whole in one transaction."""
        with self._write:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM closes WHERE day = ?", (day,))
                db.executemany("INSERT INTO closes VALUES (?, ?, ?)",
                               sorted({r["T"]: (day, r["T"], r["c"]) for r in rows
                                       if r.get("T") and isinstance(r.get("c"), (int, float))}.values()))
                db.execute("INSERT OR REPLACE INTO market_days VALUES (?, ?, ?, ?)",
                           (day, retrieved_at, json.dumps(request, sort_keys=True), len(rows)))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def forget_days(self, days: Iterable[str]) -> None:
        """Sessions whose kept closes are not what the session turned out to be (read before its closing prices were
        published): their closes, and every listing-at-a-price answer that rests on one of them (`closed_at`, which
        names its session), are dropped, so the next read of each reads it again."""
        days = set(days)
        if not days:
            return
        with self._write:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                for day in sorted(days):
                    db.execute("DELETE FROM closes WHERE day = ?", (day,))
                    db.execute("DELETE FROM market_days WHERE day = ?", (day,))
                for table in ("answers", "today"):
                    resting = [(k, s, a, b) for k, s, a, b, p in db.execute(
                        f"SELECT kind, subject, range_from, range_to, payload FROM {table} WHERE kind = 'closed_at'")
                        if json.loads(zlib.decompress(p)).get("session") in days]
                    db.executemany(f"DELETE FROM {table} WHERE kind = ? AND subject = ? AND range_from = ? "
                                   f"AND range_to = ?", resting)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def day_closes(self, days: Iterable[str]) -> dict[str, dict[str, float]]:
        """Every listing's close on each of `days`, by listing and session."""
        out: dict[str, dict[str, float]] = {}
        for day in days:
            for t, c in self._db().execute("SELECT ticker, c FROM closes WHERE day = ?", (day,)):
                out.setdefault(t, {})[day] = c
        return out


_shared: Observations | None = None
_lock = threading.Lock()


def shared() -> Observations:
    """This machine's observations, opened once in a process."""
    global _shared
    with _lock:
        if _shared is None:
            _shared = Observations()
        return _shared
