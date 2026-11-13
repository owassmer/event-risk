"""The store.

One SQLite file per store, `stores/{name}/store.sqlite`, with `trace.jsonl` beside it. It holds the analyst's objects
and links with their stated fields; the citation of each stated fact, and of each stated assumption cited from a page,
to its page (`cites`); the pages kept as Sources while they are cited or linked as evidence, each held again as a
fetched page, under its id, when its last use goes; the markets kept while a link, a note or a chance names them; the data
fetched to derive values, kept whatever is later released; the record of each search; and the cache of Computations
and Judgments.

What the cache keeps. A Judgment is kept, keyed by its question and the state the judge read. A Computation
is kept while it is how its value was last worked out (its value's answer: the one the engine last gave as that
value, `answer`), or while the
conversation, the record, or another kept Computation names it: the conversation is every `cmp:N` in the store's
trace, a tool's result, a reply or a note; the record, every one in a reply's record; and a kept Computation, every one
written in its manifest, at any depth (`computation_read`). One that a later Computation of its value has replaced
and that nothing names is removed when the read that replaced it ends, at the outermost release of `lock`, so no read
loses a Computation it holds; what it alone named goes with it. A removed id is never given again.

Every write is one transaction, and a refused call writes nothing. Every derived value
is computed on read by the engine and returned with its basis; no derived value, derived
object, or derived link is a row.

Checkpoints. Every change to what the analyst writes (objects, links, kept markets
and pages, search records, and the as-of) leaves in the `undo` table the statement that
reverses it, written by triggers, the technique of https://www.sqlite.org/undoredo.html. A
checkpoint is a position in that log, taken at each message the analyst is sent; rewinding
to it runs the statements after it in reverse. Fetched data, the cache, and the id sequences
are not in the log: a restored state reads what was fetched and asked before, computes again
what the cache no longer holds, and an id names one thing for the store's whole life.

Versions. Each commit that changes a row a read can answer from is the store's next version, its count kept in the
file (`meta.version`), and each key of what it changed is touched at it (harness.touches): triggers on this connection
log the keys as the rows change, and the commit folds them into the version, so no write path can change a row without
touching its keys, and a write made outside a transaction of the store's own is refused. Each read records the keys of
what it read, where it reads, with the state it answered from (stamp). A value worked out from reads is what the store
answers while none of the keys it read has been touched since (touched_at; inside a transaction, touched_in_transaction).
A rewind, opening the store, and a commit made to its file by any other connection since this one's last, touch
every key; New York's date turning is a version once a commit records it.

One store is used from more than one thread (the analyst's and the page's); `lock` is held for
each tool call and each read the page makes, so each sees the store between calls.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from bisect import bisect_right
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from harness import credentials, schema, touches
from harness.derive.engine import Engine, Registry, Unavailable, Waiting, canonical, text_hash
from harness.derive.portfolio import pseudo, unpriced
from harness.venues import NAMES as VENUE_NAMES
from harness.venues.http import for_later

log = logging.getLogger("harness.store")

SCHEMA_VERSION = "14"  # a store of another schema is not opened: its stated fields are not this schema's
# The schemas a store is brought from to this one on opening, each a step to the next (Store._converted): 12 stated
# what an event does to a price as a percent of it; 13 stated a factor's move beyond the factors before it as a field of
# its own, `own_part`, where 14 states it as a `shock` measured against what the factor follows. A store of any other
# schema is not opened.
PERCENT_SCHEMA = "12"
OWN_PART_SCHEMA = "13"
STORE_FILE = "store.sqlite"
TRACE_FILE = "trace.jsonl"

DATA = """
CREATE TABLE data (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    range_from TEXT,
    range_to TEXT,
    request TEXT NOT NULL CHECK (json_valid(request)),
    retrieved_at TEXT NOT NULL,
    payload TEXT NOT NULL CHECK (json_valid(payload)),
    sha256 TEXT NOT NULL,
    market_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX data_lookup ON data(kind, subject);
CREATE INDEX data_market ON data(market_id) WHERE market_id IS NOT NULL;
CREATE TRIGGER data_immutable BEFORE UPDATE ON data
BEGIN SELECT RAISE(ABORT, 'fetched data is immutable'); END;
CREATE TRIGGER data_kept BEFORE DELETE ON data
BEGIN SELECT RAISE(ABORT, 'fetched data is kept'); END;
"""

# Each Computation by its derivation and subject; the Computations each one's manifest names, by which a kept
# Computation keeps what it read; and each value's answer, the one the engine last gave as the value, by its derivation
# and subject (none where the subject is none): a Computation, or none, the value absent or refused, with why.
ANSWER_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS answer (derivation TEXT NOT NULL, subject TEXT NOT NULL, id TEXT, cause TEXT, "
    "version INTEGER, PRIMARY KEY (derivation, subject)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS answer_by_id ON answer(id)",
)
# The cache's table: each Computation by its id, found by its derivation and its inputs' hash. One content can stand
# under more than one id: a value worked out while the write turn was held (Store._pend) is kept under the id it was
# given, whatever else holds its content, so every id anything names is a row of the file.
COMPUTATION_STATEMENTS = (
    "CREATE TABLE computation (id TEXT PRIMARY KEY, derivation TEXT NOT NULL, version INTEGER NOT NULL, subject TEXT, "
    "input_hash TEXT NOT NULL, inputs TEXT NOT NULL CHECK (json_valid(inputs)), "
    "output TEXT NOT NULL CHECK (json_valid(output)), created_at TEXT NOT NULL)",
    "CREATE TRIGGER computation_immutable BEFORE UPDATE ON computation "
    "BEGIN SELECT RAISE(ABORT, 'a Computation is immutable'); END",
    "CREATE INDEX IF NOT EXISTS computation_hash ON computation(derivation, input_hash)",
)
COMPUTATION = "".join(f"{s};\n" for s in COMPUTATION_STATEMENTS)
READ_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS computation_value ON computation(derivation, subject)",
    "CREATE TABLE IF NOT EXISTS computation_read (id TEXT NOT NULL, read TEXT NOT NULL, PRIMARY KEY (id, read)) "
    "WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS computation_read_by_read ON computation_read(read)",
) + ANSWER_STATEMENTS
READ = "".join(f"{s};\n" for s in READ_STATEMENTS)

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE seq (prefix TEXT PRIMARY KEY, n INTEGER NOT NULL);

CREATE TABLE object (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('Portfolio', 'Proposition', 'Metric', 'Factor', 'Note')),
    stated TEXT NOT NULL CHECK (json_valid(stated)),
    stated_at TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stated_at)),
    origin TEXT NOT NULL CHECK (origin IN ('initial', 'analyst')),
    created_call INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX object_by_type ON object(type);
CREATE UNIQUE INDEX one_portfolio ON object(type) WHERE type = 'Portfolio';
CREATE UNIQUE INDEX channel_name ON object(stated ->> '$.name') WHERE type = 'Factor';
CREATE UNIQUE INDEX channel_order ON object(stated ->> '$.order') WHERE type = 'Factor';
CREATE INDEX metric_reference ON object(stated ->> '$.metric') WHERE type = 'Proposition';
CREATE TRIGGER object_keeps_identity BEFORE UPDATE OF id, type ON object
BEGIN SELECT RAISE(ABORT, 'an object keeps its id and its type'); END;

CREATE TABLE link (
    id TEXT PRIMARY KEY,
    relation TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    field TEXT,
    stated TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stated)),
    stated_at TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stated_at)),
    origin TEXT NOT NULL CHECK (origin IN ('initial', 'analyst')),
    created_call INTEGER,
    created_at TEXT NOT NULL,
    CHECK ((relation = 'cites') = (field IS NOT NULL))
);
CREATE UNIQUE INDEX one_link ON link(relation, from_id, to_id) WHERE relation NOT IN ('cites', 'holds', 'worth');
CREATE UNIQUE INDEX one_worth ON link(from_id, to_id, coalesce(stated ->> '$.world', '')) WHERE relation = 'worth';
CREATE UNIQUE INDEX one_holding ON link(from_id, to_id, stated ->> '$.ticker') WHERE relation = 'holds';
CREATE UNIQUE INDEX one_citation ON link(from_id, field) WHERE relation = 'cites';
CREATE INDEX link_from ON link(from_id);
CREATE INDEX link_to ON link(to_id);
CREATE TRIGGER link_keeps_identity BEFORE UPDATE OF id, relation, from_id, field ON link
BEGIN SELECT RAISE(ABORT, 'a link keeps its id, relation, and origin end'); END;

CREATE TABLE market (
    id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    created_call INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (venue, venue_id)
);
CREATE TRIGGER market_immutable BEFORE UPDATE ON market
BEGIN SELECT RAISE(ABORT, 'a market keeps its venue and id'); END;

CREATE TABLE source (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    retrieved_at TEXT,
    dated TEXT,
    text TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX source_url ON source(url);
CREATE TRIGGER source_immutable BEFORE UPDATE OF id, url, retrieved_at, text, sha256, created_at ON source
BEGIN SELECT RAISE(ABORT, 'a Source is as it was fetched; only its dated is stated'); END;

{DATA}
CREATE TABLE search (
    id TEXT PRIMARY KEY,
    proposition_id TEXT REFERENCES object(id) ON DELETE CASCADE,
    tool TEXT NOT NULL CHECK (tool IN ('search_markets', 'search_web', 'search_company', 'ask_forecaster')),
    venue TEXT,
    endpoint TEXT,
    query TEXT,
    params TEXT NOT NULL CHECK (json_valid(params)),
    results TEXT NOT NULL CHECK (json_valid(results)),
    created_call INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX search_by_proposition ON search(proposition_id);
CREATE TRIGGER search_immutable BEFORE UPDATE ON search
BEGIN SELECT RAISE(ABORT, 'a search record is immutable'); END;

{COMPUTATION}
{READ}
CREATE TABLE judgment (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    statement TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    inputs TEXT NOT NULL CHECK (json_valid(inputs)),
    answer TEXT NOT NULL CHECK (json_valid(answer)),  -- a noul's probability
    model TEXT NOT NULL,
    raw TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (input_hash, model)
);
CREATE TRIGGER judgment_immutable BEFORE UPDATE ON judgment
BEGIN SELECT RAISE(ABORT, 'a Judgment is immutable'); END;
CREATE TRIGGER judgment_kept BEFORE DELETE ON judgment
BEGIN SELECT RAISE(ABORT, 'the cache keeps every Judgment'); END;
""".replace("{DATA}", DATA).replace("{COMPUTATION}", COMPUTATION).replace("{READ}", READ)

# What the analyst writes, by table: its columns, and those an update changes. Each change leaves its reverse in
# the undo log, from which checkpoints restore.
WRITTEN = {
    "object": (("id", "type", "stated", "stated_at", "origin", "created_call", "created_at"),
               ("stated", "stated_at", "origin", "created_call", "created_at")),
    "link": (("id", "relation", "from_id", "to_id", "field", "stated", "stated_at", "origin", "created_call",
              "created_at"),
             ("to_id", "stated", "stated_at", "origin", "created_call", "created_at")),
    "market": (("id", "venue", "venue_id", "created_call", "created_at"), ()),
    "source": (("id", "url", "retrieved_at", "dated", "text", "sha256", "created_at"), ("dated",)),
    "search": (("id", "proposition_id", "tool", "venue", "endpoint", "query", "params", "results", "created_call",
                "created_at"), ()),
}


def _undo_ddl() -> str:
    """The undo log, the checkpoints, and the triggers that write each change's reverse."""
    out = ["CREATE TABLE undo (n INTEGER PRIMARY KEY, statement TEXT NOT NULL);",
           "CREATE TABLE checkpoint (id TEXT PRIMARY KEY, undo INTEGER NOT NULL, created_at TEXT NOT NULL);"]
    log = "INSERT INTO undo (statement) VALUES"
    for t, (columns, updated) in WRITTEN.items():
        values = " || ', ' || ".join(f"quote(old.{c})" for c in columns)
        out.append(f"CREATE TRIGGER {t}_undo_insert AFTER INSERT ON {t} BEGIN "
                   f"{log} ('DELETE FROM {t} WHERE rowid = ' || new.rowid); END;")
        out.append(f"CREATE TRIGGER {t}_undo_delete AFTER DELETE ON {t} BEGIN "
                   f"{log} ('INSERT INTO {t} (rowid, {', '.join(columns)}) VALUES (' || old.rowid || ', ' || "
                   f"{values} || ')'); END;")
        if updated:
            sets = " || ', ' || ".join(f"'{c} = ' || quote(old.{c})" for c in updated)
            out.append(f"CREATE TRIGGER {t}_undo_update AFTER UPDATE ON {t} BEGIN "
                       f"{log} ('UPDATE {t} SET ' || {sets} || ' WHERE rowid = ' || old.rowid); END;")
    asof = "WHEN new.key = 'asof'"
    out.append(f"CREATE TRIGGER asof_undo_insert AFTER INSERT ON meta {asof} BEGIN "
               f"{log} ('DELETE FROM meta WHERE key = ''asof'''); END;")
    out.append(f"CREATE TRIGGER asof_undo_update AFTER UPDATE ON meta {asof} BEGIN "
               f"{log} ('UPDATE meta SET value = ' || quote(old.value) || ' WHERE key = ''asof'''); END;")
    return "\n".join(out) + "\n"


DDL += _undo_ddl()

# The forecast record: each reply's rows as they stood and a copy of what the analyst had written, each cited page's
# text kept once by its hash, and each watched proposition's outcome. No rewind touches them, nothing changes or removes
# a row of them, and nothing the store derives reads them.
RECORD_DDL = """
CREATE TABLE IF NOT EXISTS record (
    id TEXT PRIMARY KEY,
    session TEXT,
    turn INTEGER,
    stopped INTEGER NOT NULL CHECK (stopped IN (0, 1)),
    asof TEXT,
    rows TEXT NOT NULL CHECK (json_valid(rows)),
    written TEXT NOT NULL CHECK (json_valid(written)),
    versions TEXT NOT NULL CHECK (json_valid(versions)),
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS record_immutable BEFORE UPDATE ON record
BEGIN SELECT RAISE(ABORT, 'a record is immutable'); END;
CREATE TRIGGER IF NOT EXISTS record_kept BEFORE DELETE ON record
BEGIN SELECT RAISE(ABORT, 'the record keeps every reply'); END;

CREATE TABLE IF NOT EXISTS record_text (
    sha256 TEXT PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS record_text_immutable BEFORE UPDATE ON record_text
BEGIN SELECT RAISE(ABORT, 'a recorded page is immutable'); END;
CREATE TRIGGER IF NOT EXISTS record_text_kept BEFORE DELETE ON record_text
BEGIN SELECT RAISE(ABORT, 'the record keeps every page it cites'); END;

CREATE TABLE IF NOT EXISTS outcome (
    id TEXT PRIMARY KEY,
    proposition TEXT NOT NULL,
    definition TEXT NOT NULL,
    stated TEXT NOT NULL CHECK (json_valid(stated)),
    outcome INTEGER NOT NULL CHECK (outcome IN (0, 1)),
    determined_by TEXT NOT NULL CHECK (json_valid(determined_by)),
    created_at TEXT NOT NULL,
    UNIQUE (proposition, definition)
);
CREATE TRIGGER IF NOT EXISTS outcome_immutable BEFORE UPDATE ON outcome
BEGIN SELECT RAISE(ABORT, 'an outcome is immutable'); END;
CREATE TRIGGER IF NOT EXISTS outcome_kept BEFORE DELETE ON outcome
BEGIN SELECT RAISE(ABORT, 'the record keeps every outcome'); END;
"""
DDL += RECORD_DDL

TABLES = ("meta", "seq", "object", "link", "market", "source", "data", "search", "computation", "judgment",
          "undo", "checkpoint")

COMPUTATION_COLUMNS = ("id", "derivation", "version", "subject", "input_hash", "inputs", "output", "created_at")
CMP_RE = re.compile(rb"cmp:\d+")  # a Computation's id, wherever the trace or the record writes it


def read_by(manifest: Any) -> set[str]:
    """The Computations a manifest names: every Computation's id written in it, at any depth, whether it names one as a
    `basis` with its content's hash beside it or by the id alone, as the trace and the record are read (CMP_RE)."""
    return {m.decode() for m in CMP_RE.findall(json.dumps(manifest).encode())}


def unwritable(error: BaseException) -> str | None:
    """Why a write failed where the store's file cannot be written: "full", the disk or the database's limit full
    (SQLITE_FULL), or "io", the file not written (any SQLITE_IOERR); None for any other failure, a write refused by a
    rule among them (https://www.sqlite.org/rescode.html)."""
    if not isinstance(error, sqlite3.Error):
        return None
    name = getattr(error, "sqlite_errorname", "") or ""
    if name == "SQLITE_FULL" or "database or disk is full" in str(error):
        return "full"
    if name.startswith("SQLITE_IOERR") or "disk I/O error" in str(error):
        return "io"
    return None


def _on_the_turn(subject: str, covering: Any = None) -> dict:
    """What a read the page makes gives where the store does not hold the answer: it waits for the analyst's turn."""
    raise Waiting("read on the analyst's turn, not the page's")


_on_the_turn.waits = False  # a read under way is not waited for: the page shows its placeholder (Store.data)


class StoreLock:
    """The store's lock: re-entrant, held for each tool call and each read the page makes. Its holder's outermost
    release first removes what that read replaced in the cache (Store._settle), so every Computation a read holds
    stays while it holds it. A thread that meters what it does (meter) is told how long it waited for the lock and how
    long it held it, over each of its outermost holds."""

    def __init__(self, release: Callable[[], None]):
        self._lock = threading.RLock()
        self._held = threading.local()
        self._settle = release

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        asked = time.monotonic()
        got = self._lock.acquire(blocking, timeout)
        if got:
            held = self._held
            depth = getattr(held, "depth", 0)
            if depth == 0:
                held.since = time.monotonic()
                meter = getattr(held, "meter", None)
                if meter is not None:
                    meter["waited"] += held.since - asked
            held.depth = depth + 1
        return got

    def release(self) -> None:
        held = self._held
        if held.depth == 1:
            try:
                self._settle()
            except Exception:  # what it could not remove now is removed at a later release
                log.warning("the cache's replaced Computations stay until a later read", exc_info=True)
            meter = getattr(held, "meter", None)
            if meter is not None:
                meter["held"] += time.monotonic() - held.since
        held.depth -= 1
        self._lock.release()

    def __enter__(self) -> "StoreLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    @contextmanager
    def meter(self):
        """What this thread waits for the lock and holds it while the block runs, in seconds: {"waited", "held"}."""
        meter = {"waited": 0.0, "held": 0.0}
        saved = getattr(self._held, "meter", None)
        self._held.meter = meter
        try:
            yield meter
        finally:
            self._held.meter = saved


def store_dir(name: str, root: str | Path = "stores") -> Path:
    """stores/{name}/: one store, one portfolio."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise StoreError(f"{name!r} is not a store name: letters, digits, dots, dashes, and underscores")
    return Path(root) / name


MUTABLE_OBJECT_TYPES = ("Portfolio", "Proposition", "Metric", "Factor", "Note")
# Requests side by side when the store fetches many series at once. Massive allows a paid plan's asset class any
# number of requests and its free tier five a minute, and its client waits out a 429 either way
# (https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis).
PREFETCH_WORKERS = 8
# Computation ids reserved at a time for values worked out while another thread holds the write turn (_reserve_ids):
# enough for every figure a few renders work out during one long statement, and gaps of at most this many between
# one opening of the store and the next.
ID_BLOCK = 256
KEEPER_JOIN = 10.0  # seconds a close waits for the keeper's write under way to finish
FIND_CHUNK = 500  # subjects found in one query by data_many, well within SQLite's bound on a query's parameters
# Reads started ahead of the derivation that asks for them, side by side across kinds and subjects; each client keeps
# its own source's pace (harness.venues.*: the SEC's ten a second, OpenFIGI's one request at a time, Yahoo's interval),
# so this bounds only how many wait on their sources at once.
AHEAD_WORKERS = 16
# The kinds a read may be started ahead for: what a source answers about a range, the same whenever it is read. A
# reading taken again (a mark, a book, an option's quote) is never started ahead, so an answer read early can never
# stand as newer than one read since.
AHEAD_KINDS = frozenset(("ticker_ref", "ticker_on", "ticker_events", "listings", "company_listings", "cusip_listing",
                         "bars", "dividends", "splits", "constituents", "holdings_history", "holdings_processed", "nport", "home", "hourly", "rates", "figi",
                         "figi_cusip", "figi_ticker", "figi_class", "figi_listing", "figi_isin", "isin_listings",
                         "figi_index",
                         "sec_tickers", "sec_funds", "bills",
                         "money_market", "ncen", "listing_sector", "listing_symbol", "profile", "tracker", "closed_at", "pair", "yield_curve", "news", "option_at",
                         "option_contract",
                         "minutes", "option_listings"))
# What says which Names exist and which funds reach them: the look-through's products. The betas give the same Names
# their b, from every name's prices, which no read of which Names exist needs (Engine.produced).
LOOKED = ("looks_through_to",)
# The derivations that read what the portfolio owns, each waiting on its own chain of reads (Store._standing_aside).
OWN_READS = ("holdings", "lookthrough")


# What each kind of fetched data is, as a reason names it.
DATA_WORDS = {"ticker_ref": "the reference for {}", "listings": "the US listings of {}",
              "bars": "the daily prices of {}", "hourly": "the hourly prices of {}",
              "last_trade": "the latest trade of {}", "dividends": "the dividends of {}", "splits": "the splits of {}",
              "rates": "the reference rates of {}", "settlement": "the settlement of {}", "figi": "the US listings of the share class {}",
              "figi_ticker": "the share class of the US listing {}", "figi_class": "the listings of the share class {}",
              "figi_listing": "the share class of the listing {}", "figi_isin": "the ISIN of the share class {} names",
              "isin_listings": "where the funds' holdings say {} trades",
              "sec_tickers": "the SEC's list of listed companies' tickers",
              "sec_funds": "the SEC's list of the funds' share classes", "company_listings": "the listings of the company with CIK {}",
              "constituents": "the holdings of {}", "holdings_history": "the holdings of {} on each day",
              "holdings_processed": "the latest day {} published funds' holdings", "nport": "the Form N-PORT holdings of {}",
              "cusip_listing": "the listing of {}", "ticker_on": "the reference for {}",
              "ticker_events": "the ticker changes of {}",
              "home": "the prices of {} on its home market", "home_mark": "the latest price of {} on its home market",
              "figi_cusip": "the US listings of the security with CUSIP {}", "snapshot": "the latest price of {}", "market": "the terms of {}",
              "book": "the book of {}", "history": "the price history of {}", "news": "the news about {}",
              "figi_index": "the identity of the index {}",
              "tracker": "the funds whose returns match the index {}",
              "closed_at": "the listings that closed at the price of {}",
              "option": "the market's quote of the option {}",
              "option_at": "the option's price standing at {}", "option_contract": "the option's terms {}",
              "minutes": "the minute bars of {}", "option_listings": "the listed contracts {} names",
              "pair": "whether Coinbase Exchange lists the pair {}",
              "yield_curve": "the Treasury's par yield curve over {}",
              "venue_rate": "the rate {} pays on a position, from its page",
              "ncen": "the Form N-CEN census of {}",
              "listing_sector": "the sector and industry of the listing {}",
              "listing_symbol": "the symbol of the listing {}", "profile": "the company profile of {}",
              "company_record": "the results and ratings of {}", "sec_filings": "the SEC filings of CIK {}",
              "sec_document": "the SEC filing {}", "bills": "the Treasury's record of the bills it has sold",
              "money_market": "whether {} is a money market fund", "company": "the company record of {}"}


def reading_words(kind: str, subject: str, covering: tuple[str, str] | None = None) -> str:
    """What a read is, in the words of what it reads (DATA_WORDS): its subject as its reader names it, a month's reading
    without its month, and its range, on a day or from one day to another."""
    shown = re.sub(r"@\d{4}-\d{2}$", "", subject)
    if kind == "closed_at" and shown.count("|") == 2:  # `{day}|{low}|{high}`: the price a listing closed at that day
        day, low, high = shown.split("|")
        return f"the listings that closed at {(float(low) + float(high)) / 2:.4g} on {day}"
    span = "" if not covering else f" on {covering[0]}" if covering[0] == covering[1] else \
        f" from {covering[0]} to {covering[1]}"
    return DATA_WORDS.get(kind, "the data for {}").format(shown) + span


class StandAside(Waiting):
    """A read a call needs is under way, and the call stands aside, holding no lock, until it lands (Store.standing_aside).
    Its write, if it was writing, rolls back whole, what it read kept."""


class _Failed:
    """A read started ahead that failed, with why: its caller reads the source's answer as data() reads it."""

    def __init__(self, error: Exception):
        self.error = error



def _chance_markets(stated: dict | None) -> set[str]:
    """The markets a proposition's chances are given, each standing as the proposition its question and rules define."""
    return {x for c in (stated or {}).get("chances") or [] for x in (*c.get("given", ()), *c.get("given_not", ()))
            if schema.MARKET_ID_RE.match(x)}


def _absent(oid) -> str:
    """Why an id names nothing here: a ticker given where an id belongs is told what the ids are (ONTOLOGY.md 2)."""
    if isinstance(oid, str) and oid and ":" not in oid:
        return f"{oid} is not an id: a company or a fund is name:{oid}, and a factor is factor:N"
    return f"{oid} is not in the store"

class StoreError(Exception):
    """A call the store refuses. It wrote nothing."""

    def __init__(self, message: str, dependents: Iterable[str] = ()):
        super().__init__(message)
        self.message = message
        self.dependents = list(dependents)

    def as_dict(self) -> dict:
        return {"error": self.message, "dependents": self.dependents}


def _seq_key(sid: str) -> int:
    """A sequential id's number, its order of creation: search:12 after search:9."""
    return int(sid.rsplit(":", 1)[1])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _moment() -> str:
    """When a stated field is written, to the microsecond: a book read in the same second is before or after it."""
    return datetime.now(timezone.utc).isoformat()


def _stamped(stated_at: str | None, after: dict, written: Iterable[str]) -> str:
    """The moment each stated field was last written (ONTOLOGY.md 2): the fields `written` now, and of the rest those still
    stated in `after`. A proposition's chance that follows a market keeps its own moment, `chances:follow`, the moment
    its market's probability then is read at (MATH.md 1.2), so stating another of its chances leaves it as it was."""
    kept = lambda k: k in after if k != FOLLOW_STAMP else _following(after) is not None  # noqa: E731
    times = {k: v for k, v in json.loads(stated_at or "{}").items() if kept(k)}
    now = _moment()
    times.update({field: now for field in written if kept(field)})
    return canonical(times)


FOLLOW_STAMP = "chances:follow"


def _following(stated: dict) -> tuple | None:
    """A proposition's chance that follows a market, as what makes it that chance: its figure, its market, and how far
    it follows; None where none does."""
    for c in stated.get("chances") or []:
        if c.get("follow"):
            return (c["value"], c["follow"], c["responsiveness"])
    return None


def _written(old: dict, new: dict, field: str) -> list[str]:
    """The stamps an edit of one field writes: the field, and a following chance's own where it is stated anew."""
    out = [field]
    if field == "chances" and _following(new) is not None and _following(new) != _following(old):
        out.append(FOLLOW_STAMP)
    return out


def _or(names: Iterable[str]) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


# A fetched page's text is markdown; its words are what it shows: an image's markup shows none, a link's its text,
# emphasis its words. Format only, as HTML is to text.
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
_EMPHASIS = re.compile(r"\*\*|__")


def words(text: str) -> str:
    """The words of a text in order, single-spaced, without markdown's image, link, and emphasis markup."""
    return " ".join(_EMPHASIS.sub("", _LINK.sub(r"\1", _IMAGE.sub(" ", text))).split())


PASSAGE = 1000  # the most characters a passage holds: its paragraph, or as much of a longer one around what it finds
PASSAGES = 10  # the passages a search of a page returns, in page order
_SAME = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                       "\u2014": "-"})


def _fold(text: str) -> str:
    """A text as a search reads it, character for character: lower case, and curly quotes and dashes as plain."""
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text).translate(_SAME)


_FRONT = re.compile(r"\A---\n.*?\n---(?:\n|\Z)", re.S)  # a fetched page's front matter: its URL, title, descriptions


def body(page: str) -> str:
    """A fetched page's body: its text after the front matter it opens with, where it has one (its URL, its title and
    the site's descriptions of it, which the fetch puts first), as passages are found in it (find)."""
    front = _FRONT.match(page)
    return page[front.end():].lstrip() if front else page


def find(page: str, sought: str) -> dict:
    """Where a text is in a page's body, in the page's words as the passage check reads them: each paragraph the text
    is in, or, in a paragraph longer than PASSAGE, as much of it around the text, cut at words, finds close together
    sharing one passage, each a part of the page that quotes word for word; with where it sits, its place in the
    body's words and the heading above it. Case, spacing, line breaks, the markup of links, images, and emphasis, and
    curly or plain quotes and dashes do not matter to the search; what it returns is the page's own words. The front
    matter a fetched page opens with, its URL, title, and the site's descriptions of it, is not searched: it repeats
    the title, and a site's description can be cut off mid-word. Its finds are counted, as front_matter, where it has
    any."""
    front = _FRONT.match(page)
    head_matter = page[:front.end()] if front else ""
    page = page[front.end():] if front else page
    raw = re.split(r"\n\s*\n", page)
    blocks, heads, head = [], [], None
    for b in raw:
        w = words(b)
        if not w:
            continue
        if b.lstrip().startswith("#"):
            head = w.lstrip("# ").strip() or head
        blocks.append(w)
        heads.append(head)
    starts, at = [], 0
    for b in blocks:
        starts.append(at)
        at += len(b) + 1
    whole = " ".join(blocks)
    target = _fold(words(sought))
    hay = _fold(whole)
    hits, i = [], hay.find(target) if target else -1
    while i >= 0:
        hits.append(i)
        i = hay.find(target, i + len(target))
    spans: list[list[int]] = []
    for hit in hits:
        end = hit + len(target)
        first, last = bisect_right(starts, hit) - 1, bisect_right(starts, end - 1) - 1
        lo, hi = starts[first], starts[last] + len(blocks[last])
        if hi - lo > PASSAGE:
            pad = max(0, (PASSAGE - (end - hit)) // 2)
            cut_lo, cut_hi = max(lo, hit - pad), min(hi, end + pad)
            if cut_lo > lo:
                cut_lo = whole.find(" ", cut_lo) + 1 or cut_lo  # at a word
                cut_lo = min(cut_lo, hit)
            if cut_hi < hi:
                space = whole.rfind(" ", end, cut_hi)
                cut_hi = space if space > end else cut_hi
            lo, hi = cut_lo, cut_hi
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi, heads[first]])
    in_front = _fold(words(head_matter)).count(target) if target and head_matter else 0
    return {"length": len(whole), "found": len(hits),
            "passages": [{"at": lo, "under": under, "passage": whole[lo:hi]} for lo, hi, under in spans[:PASSAGES]],
            **({"front_matter": in_front} if in_front else {})}


def _check(owner: str, f: schema.Field, value: Any) -> Any:
    try:
        return schema.check_value(f, value)
    except schema.Invalid as e:
        raise StoreError(f"{owner}.{e}") from None


# The pages fetched and held, not yet kept: each citable under its id, and kept as a Source once cited. They are
# fetched data, so they outlast a restart and are never rewound: no undo is logged for them.
HELD_DDL = """CREATE TABLE IF NOT EXISTS held (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    retrieved_at TEXT,
    dated TEXT,
    text TEXT NOT NULL,
    sha256 TEXT NOT NULL
);"""


class Store:
    """One store. Open it with `Store.create` or `Store.open`."""

    def __init__(self, directory: Path, *, registry: Registry | None = None, judge=None, venues=None,
                 fetchers: dict | None = None, trace: str | Path | None = None):
        self.dir = Path(directory)
        self.path = self.dir / STORE_FILE
        # the conversation's trace: the store's own, beside it, or one a conversation over several stores keeps
        self.trace_path = Path(trace) if trace is not None else self.dir / TRACE_FILE
        self.db = sqlite3.connect(self.path, autocommit=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.execute("PRAGMA temp_store = MEMORY")  # the log of what the open transaction touched
        for statement in touches.triggers(set()):  # the log and the mark of a transaction of the store's own
            self.db.execute(statement)
        # The store's versions (harness.touches): the last one committed, each key's last touch by version, every
        # key's since (a rewind; opening the store), and New York's date as the last version found it.
        self.version = 0
        self._touched: dict[str, int] = {}
        self._everything_at = 0
        self._day = touches.today()
        self._step_at = (-1, 0)  # the open transaction's step, as of the connection's count of its changes
        self._tx_touched: tuple[int, dict[str, int]] = (-1, {})  # its touches by key, as of that count
        self._seen = 0  # the file's data_version as this connection last saw it (_fold)
        self.lock = StoreLock(self._settle)
        # each thread's own connection to the file, read only, for the reads that take no lock (last_computation)
        self._own = threading.local()
        self._owns: list[sqlite3.Connection] = []
        self._owns_lock = threading.Lock()
        # what the cache keeps: the Computations a read replaced, removed when it ends where nothing keeps
        # them; every Computation the conversation and the record name; and how far the trace has been read for them
        self._replaced: set[str] = set()
        self._named: set[str] = set()
        self._trace_at = 0
        self._settling = False
        self._closed = False
        self.unwritable: dict | None = None  # since when, and why, the store's file cannot be written (_failed_write)
        self.progress: Callable[[dict], None] | None = None  # told each stage of a long read as it happens
        self.venues = venues
        self.fetchers = dict(fetchers or {})
        # Reads started ahead (prefetch with wait=False): each a client's fetch alone, on its own pool, by kind and
        # subject with the range it covers; its answer is written by the caller that asks for it (_take_ahead).
        self._ahead = ThreadPoolExecutor(max_workers=AHEAD_WORKERS, thread_name_prefix="read-ahead")
        # and those another derivation will read (`later`), on a pool of their own, so that none waits behind them
        self._later = ThreadPoolExecutor(max_workers=AHEAD_WORKERS, thread_name_prefix="read-later")
        self._ahead_lock = threading.Lock()
        self._aside = threading.local()  # a call standing aside while its reads land (standing_aside), per thread
        self._unread = threading.local()  # the reads a thread's call could not obtain (missing)
        self._page = threading.local()  # a read the page makes (as_page), per thread
        self._ahead_reads: dict[tuple[str, str], list[tuple[tuple[str, str] | None, Future]]] = {}
        self._depth = 0
        self._pending: dict[str, dict] = {}  # the pages fetched and held, not kept: each a dict replaced, never changed
        self._pending_at_begin: dict[str, dict] = {}
        self._fetched: list[tuple[str, tuple]] = []  # data read and Judgments answered inside the open transaction
        self._estimable: list[str | None] = []  # the open transaction's writes the channels' estimability is decided for
        self._shocked_names: list[str] = []  # the names the open transaction's consequences run to, each to have a price
        self._answers: dict[str, dict] = {}  # each reading's status and currency, by its id: fetched data never changes
        # each name's answer with the keys it read, until the store next changes (stamp) or a write rolls back
        self._unpriced_at: tuple[tuple[int, int], dict[str, tuple[bool, tuple[str, ...]]]] = ((-1, -1), {})
        # Worked-out values made while another thread holds the write turn: each Computation pending, with
        # its id, by id and by its derivation and inputs' hash, and each value's answer pending with the version it
        # belongs to; written by the keeper when the turn is free (_flush_pending). Every read by id or by hash answers
        # from here at once. A pending id is taken from a block of ids reserved in the file (_reserve_ids), so no id
        # the writer takes inside its transactions is one of them.
        self._pending_lock = threading.Lock()
        self._pending_cmp: dict[str, dict] = {}  # cid -> {"row": computation row, "reads": ids its manifest names}
        self._pending_hash: dict[tuple[str, str], str] = {}  # (derivation, input hash) -> cid
        self._pending_answer: dict[tuple[str, str], tuple[str | None, str | None, int]] = {}  # -> (cid, cause, version)
        self._ids = [0, 0]  # the reserved block: the next id's number, and the number past its end
        self._tx_answers: set[tuple[str, str]] = set()  # the answers the open transaction wrote, stamped at its commit
        self._tx_kept: set[str] = set()  # the pending Computations the open transaction wrote
        self._keeper: threading.Thread | None = None
        self._keeper_wake = threading.Event()
        self._stopping = False  # the store is closing: the keeper ends, and the close writes what is still pending
        self.engine = Engine(self, registry if registry is not None else Registry(), judge)

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def create(cls, directory: str | Path, *, initial: str | Path | None = None, **kw) -> "Store":
        """A new store in `directory` (stores/{name}/), with, when given, the initial contents in
        `initial` (channels.yaml)."""
        d = Path(directory)
        if (d / STORE_FILE).exists():
            raise StoreError(f"{d} holds a store")
        d.mkdir(parents=True, exist_ok=True)
        store = cls(d, **kw)
        store.db.execute("PRAGMA journal_mode = WAL")
        with store.transaction():
            store.db.executescript(DDL)
            store.db.execute(HELD_DDL)
            store._set_meta("schema_version", SCHEMA_VERSION)
            store._set_meta("name", d.name)
            store._set_meta("created_at", _now())
        store._watch()
        store._reserve_ids()  # after _watch: its commit is the store's own, and no version
        store.trace_path.touch()
        if initial is not None:
            try:
                store.load_initial(initial)
            except BaseException:
                store.close()
                own = (store.trace_path,) if store.trace_path.parent == d else ()  # a trace it shares stays
                for leftover in (store.path, *own, d / f"{STORE_FILE}-wal", d / f"{STORE_FILE}-shm"):
                    leftover.unlink(missing_ok=True)
                raise
        return store

    @classmethod
    def open(cls, directory: str | Path, **kw) -> "Store":
        d = Path(directory)
        if not (d / STORE_FILE).exists():
            raise StoreError(f"{d} holds no store")
        store = cls(d, **kw)
        if store._one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'") is None:
            store.close()
            raise StoreError(f"{d} holds no store: its {STORE_FILE} has none of a store's tables")
        version = store.meta("schema_version")
        if version in (PERCENT_SCHEMA, OWN_PART_SCHEMA):
            try:
                store._converted(version)
            except BaseException:
                store.close()
                raise
        elif version != SCHEMA_VERSION:  # an earlier schema's stated fields are not this one's, and are not migrated
            store.close()
            raise StoreError(f"{d} is a store of schema {version}; this code reads schema {SCHEMA_VERSION}")
        store._admit_company_searches()
        store._admit_record()
        store._admit_worth()
        store._read_held()
        store._read_named()
        store._admit_answer()
        store._admit_read()
        store._admit_computation_ids()
        store._admit_answer_version()
        store._watch()
        store._reserve_ids()  # after _watch: its commit is the store's own, and no version
        return store

    def _converted(self, version: str) -> None:
        """A store of a schema before this one, brought to it once, on opening, a step at a time, in one transaction:
        - from 12, each shock and own part on a name or on a factor quoted as its proxy's price, and each worth's return
          since, stated as a percent of the price, becomes the fraction it was written as (schema.from_percent), the
          form every move the store returns takes; a figure on a factor quoted as the yield its proxy pays stays in
          points of that yield, which is fixed when the factor is added;
        - from 13, a factor's own part, `own_part`, becomes its `shock` measured against what it follows
          (schema.FOLLOWS), the same figure with its basis: what it tells the store is the same (MATH.md 1.3).
        Each step rewrites the links as they stand and each reverse the undo log holds, so that a rewind restores a
        figure in the terms the store now reads. Nothing else changes: when each figure was stated, its basis, and what
        the record and the trace keep as they were written. The conversion is no statement: it leaves nothing in the
        undo log for a rewind to take back. `schema_converted` in meta says from which schema, to which, and when. Where
        a reverse cannot be read exactly, nothing is converted and the store is not opened."""
        scratch = {t: f"undo_{t}" for t in ("object", "link")}

        def row_of(statement: str) -> tuple[str, dict] | None:
            """The row a reverse in the undo log writes to an object or a link, read by SQLite itself into a scratch
            table of the same columns: its table, and the columns it sets (an update sets no `relation` or `type`)."""
            for t, name in scratch.items():
                insert, update = f"INSERT INTO {t} (", f"UPDATE {t} SET "
                if statement.startswith(insert):
                    self.db.execute(f"INSERT INTO temp.{name} (" + statement[len(insert):])
                elif statement.startswith(update):
                    self.db.execute(f"INSERT INTO temp.{name} (rowid) VALUES (?)",
                                    (int(statement.rsplit("WHERE rowid = ", 1)[1]),))
                    self.db.execute(f"UPDATE temp.{name} SET " + statement[len(update):])
                else:
                    continue
                row = dict(self.db.execute(f"SELECT * FROM temp.{name}").fetchone())
                self.db.execute(f"DELETE FROM temp.{name}")
                return t, row
            return None

        def fractions(stated: dict, target: str | None) -> dict | None:
            """12 to 13: a link's stated fields with each figure on a price a fraction, or None where they hold no
            figure stated as a percent of a price. `shock` and `own_part` are a consequence's fields alone, and a
            `value` holding a `return` a worth's (schema.RELATIONS), so a reverse that names no relation is read by its
            fields."""
            if any(stated.get(k) is not None for k in ("shock", "own_part")):
                if target in yields:
                    return None
                return {**stated, **{k: {**stated[k], "value": schema.from_percent(stated[k]["value"])}
                                     for k in ("shock", "own_part") if stated.get(k) is not None}}
            outer = stated.get("value")
            value = outer.get("value") if isinstance(outer, dict) else None
            if isinstance(value, dict) and value.get("return") is not None:
                return {**stated, "value": {**outer, "value": {**value, "return": schema.from_percent(value["return"])}}}
            return None

        def references(stated: dict, target: str | None) -> dict | None:
            """13 to 14: a consequence's own part as its shock against what the price follows, or None where it states
            none. `own_part` is a consequence's field alone, so a reverse is read by its fields."""
            if stated.get("own_part") is None:
                return None
            out = {k: v for k, v in stated.items() if k != "own_part"}
            return {**out, "shock": {**stated["own_part"], schema.AGAINST: schema.FOLLOWS}}

        steps = {PERCENT_SCHEMA: (OWN_PART_SCHEMA, fractions), OWN_PART_SCHEMA: (SCHEMA_VERSION, references)}
        first = version
        with self.transaction():
            for t, name in scratch.items():
                self.db.execute(f"CREATE TEMP TABLE {name} (rowid INTEGER PRIMARY KEY, {', '.join(WRITTEN[t][0])})")
            try:
                while version in steps:
                    to, converted = steps[version]
                    logged = self.db.execute("SELECT n, statement FROM undo ORDER BY n").fetchall()
                    reverses = {r["n"]: (r["statement"], row_of(r["statement"])) for r in logged}
                    # the factors quoted as a yield: those the store holds, and those removed since, whose rows the
                    # undo log holds whole
                    yields = {r["id"] for r in self.db.execute(
                        "SELECT id FROM object WHERE type = 'Factor' AND stated ->> '$.yield' IS NOT NULL")}
                    yields |= {row["id"] for _, got in reverses.values() if got and got[0] == "object"
                               for row in (got[1],) if row.get("type") == "Factor"
                               and json.loads(row["stated"] or "{}").get("yield") is not None}
                    mark = self.db.execute("SELECT coalesce(max(n), 0) FROM undo").fetchone()[0]
                    for r in self.db.execute("SELECT id, to_id, stated FROM link WHERE relation IN ('consequence', "
                                             "'worth')").fetchall():
                        new = converted(json.loads(r["stated"]), r["to_id"])
                        if new is not None:
                            self.db.execute("UPDATE link SET stated = ? WHERE id = ?", (canonical(new), r["id"]))
                    self.db.execute("DELETE FROM undo WHERE n > ?", (mark,))  # the conversion is no statement
                    for n, (statement, got) in reverses.items():
                        if not got or got[0] != "link" or got[1].get("relation") not in (None, "consequence", "worth") \
                                or got[1].get("stated") is None:
                            continue
                        new = converted(json.loads(got[1]["stated"]), got[1].get("to_id"))
                        if new is None:
                            continue
                        old = "'" + got[1]["stated"].replace("'", "''") + "'"  # as the trigger quoted it (_undo_ddl)
                        if statement.count(old) != 1:
                            raise StoreError(f"{self.dir} is a store of schema {first}, and its undo log's entry {n} "
                                             f"cannot be read exactly to bring it to schema {SCHEMA_VERSION}")
                        self.db.execute("UPDATE undo SET statement = ? WHERE n = ?",
                                        (statement.replace(old, "'" + canonical(new).replace("'", "''") + "'"), n))
                    version = to
                self._set_meta("schema_version", SCHEMA_VERSION)
                self._set_meta("schema_converted", json.dumps({"from": first, "to": SCHEMA_VERSION, "at": _now()}))
            finally:
                for name in scratch.values():
                    self.db.execute(f"DROP TABLE IF EXISTS temp.{name}")

    def _watch(self) -> None:
        """From here on, each row this connection changes in a table a read answers from logs its keys, and each
        commit that changed one is a version (harness.touches.triggers, _transaction). The store opens at the version
        its file holds, every key touched at it: nothing worked out before it was opened is taken as current."""
        tables = {r["name"] for r in self._all("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for statement in touches.triggers(tables & set(touches.WATCHED)):
            self.db.execute(statement)
        row = self._one("SELECT value FROM meta WHERE key = 'version'")
        self.version = int(row["value"]) if row else 0
        self._touched.clear()
        self._everything_at = self.version
        self._day = touches.today()
        self._seen = self._data_version()

    def _data_version(self) -> int:
        """A count that changes for this connection whenever another connection commits to the file
        (https://www.sqlite.org/pragma.html#pragma_data_version)."""
        return self.db.execute("PRAGMA data_version").fetchone()[0]

    def _read_named(self) -> None:
        """Every Computation the record names, and the conversation as far as its trace holds whole lines."""
        for r in self._all("SELECT rows, written FROM record"):
            self._named.update(m.decode() for c in (r["rows"], r["written"]) for m in CMP_RE.findall(c.encode()))
        self._named_now()

    def _admit_answer(self) -> None:
        """A store made before the store kept each value's answer gains it: each value's answer, its latest Computation,
        as retention read the latest then. Before _admit_read, whose collection keeps each value's answer."""
        if self._one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'answer'") is not None:
            return
        with self.transaction():
            for statement in ANSWER_STATEMENTS:
                self.db.execute(statement)
            self.db.execute("INSERT INTO answer (derivation, subject, id) SELECT c.derivation, coalesce(c.subject, ''), "
                            "c.id FROM computation c JOIN (SELECT max(rowid) AS n FROM computation "
                            "GROUP BY derivation, subject) l ON c.rowid = l.n")

    def _admit_read(self) -> None:
        """A store made before the cache was kept by its rule gains the index of what each Computation
        read, and is brought under the rule once, whole (_collect): what the rule does not keep is removed, and the
        file gives back the space it held."""
        if self._one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'computation_read'") is not None:
            return
        with self.transaction():
            self.db.execute("DROP TRIGGER IF EXISTS computation_kept")
            for statement in READ_STATEMENTS:
                self.db.execute(statement)
            removed = self._collect()
        if removed:
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _admit_computation_ids(self) -> None:
        """A store whose cache held each content under one id alone (`UNIQUE (derivation, input_hash)`) is rebuilt
        without that rule, every Computation kept with its id, in the order it was kept: a value worked out while the
        write turn was held is kept under the id it was given, whatever else holds its content (COMPUTATION). SQLite
        drops a table's constraint only by making the table again (https://www.sqlite.org/lang_altertable.html)."""
        sql = self._one("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'computation'")["sql"]
        if "UNIQUE" not in sql:
            return
        with self.transaction():
            self.db.execute("ALTER TABLE computation RENAME TO computation_held")
            self.db.execute("DROP TRIGGER IF EXISTS computation_immutable")
            for statement in COMPUTATION_STATEMENTS:
                self.db.execute(statement)
            columns = ", ".join(COMPUTATION_COLUMNS)
            self.db.execute(f"INSERT INTO computation ({columns}) SELECT {columns} FROM computation_held ORDER BY rowid")
            self.db.execute("DROP TABLE computation_held")
            for statement in READ_STATEMENTS:
                self.db.execute(statement)

    def _admit_answer_version(self) -> None:
        """A store made before each answer carried the version it belongs to gains the column: an answer it held reads
        as of no version, so any answer given since stands over it."""
        if any(r["name"] == "version" for r in self._all("PRAGMA table_info(answer)")):
            return
        with self.transaction():
            self.db.execute("ALTER TABLE answer ADD COLUMN version INTEGER")

    def _reserve_ids(self) -> None:
        """A block of Computation ids reserved in the file (`seq`) for what is worked out while another thread holds the
        write turn (put_computation), in a write of its own that commits: the writer's ids, taken inside its
        transactions, come after it, and none is given twice, whatever becomes of a transaction or of this process. A
        store whose file cannot be written takes its ids past every one its file holds, and keeps nothing."""
        try:
            with self.lock, self._transaction():
                end = self.db.execute(
                    "INSERT INTO seq (prefix, n) VALUES ('cmp', ?) ON CONFLICT (prefix) DO UPDATE SET n = n + ? "
                    "RETURNING n", (ID_BLOCK, ID_BLOCK)).fetchone()[0]
        except sqlite3.OperationalError:
            row = self._one("SELECT n FROM seq WHERE prefix = 'cmp'")
            end = (row["n"] if row else 0) + ID_BLOCK
        with self._pending_lock:
            self._ids = [end - ID_BLOCK + 1, end + 1]

    def _admit_worth(self) -> None:
        """Allow one stated value per outcome without changing existing links or their identities."""
        if self._one("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'one_worth'") is None:
            with self.transaction():
                self.db.execute("DROP INDEX one_link")
                self.db.execute("CREATE UNIQUE INDEX one_link ON link(relation, from_id, to_id) "
                                "WHERE relation NOT IN ('cites', 'holds', 'worth')")
                self.db.execute("CREATE UNIQUE INDEX one_worth ON link(from_id, to_id, "
                                "coalesce(stated ->> '$.world', '')) WHERE relation = 'worth'")

    def _admit_record(self) -> None:
        """A store made before the forecast record gains its tables, empty."""
        if self._one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'outcome'") is None:
            with self.lock:
                self.db.executescript(RECORD_DDL)

    def _read_held(self) -> None:
        """The pages the store held when it was last open, held again under their ids. A store made before pages were
        kept across a restart gains the table that keeps them."""
        self.db.execute(HELD_DDL)
        self._pending = {r["id"]: {k: r[k] for k in ("url", "retrieved_at", "dated", "text", "sha256")}
                         for r in self._all("SELECT * FROM held ORDER BY rowid")}

    def _admit_company_searches(self) -> None:
        """A store made before search_company recorded only market and web searches; its search table's CHECK is
        widened to admit a company search, by SQLite's procedure for relaxing a constraint in place
        (https://www.sqlite.org/lang_altertable.html, section 7). Every row it holds still satisfies it."""
        old, new = "'search_web')", "'search_web', 'search_company')"
        found = self._one("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'search'")
        if found is None or "'search_company'" in found["sql"] or old not in found["sql"]:
            return
        with self.lock:
            version = self._one("PRAGMA schema_version")[0]
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("PRAGMA writable_schema = ON")
                self.db.execute("UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE type = 'table' AND "
                                "name = 'search'", (old, new))
                self.db.execute(f"PRAGMA schema_version = {version + 1}")
                self.db.execute("PRAGMA writable_schema = OFF")
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        """The store closed: what is pending written first, where the write turn is free and the file can be written;
        otherwise it goes with the process, as cache nothing the file holds names."""
        if not self._closed:
            self._stopping = True  # the keeper ends after the write it is making, if it is making one
            self._keeper_wake.set()
            keeper = self._keeper
            if keeper is not None and keeper is not threading.current_thread():
                keeper.join(KEEPER_JOIN)
            if self.lock.acquire(blocking=False):
                try:
                    self._flush_pending(reserve=False)
                except Exception:
                    log.warning("what was pending when the store closed is not kept", exc_info=True)
                finally:
                    self.lock.release()
        self._closed = True
        self._replaced.clear()
        self._ahead.shutdown(wait=False, cancel_futures=True)
        self._later.shutdown(wait=False, cancel_futures=True)
        self.engine.close()
        with self._owns_lock:
            for own in self._owns:
                own.close()
            self._owns.clear()
        self.db.close()

    def report(self, event: dict) -> None:
        """A stage of a long read, to whoever follows the store's work; it never interrupts the read."""
        if self.progress is not None:
            try:
                self.progress(event)
            except Exception:
                pass

    # ------------------------------------------------------------------ checkpoints

    def checkpoint(self, key: str) -> None:
        """Mark what the analyst has written now, as `key`: the message about to be sent."""
        with self.transaction():
            mark = self._one("SELECT coalesce(max(n), 0) AS n FROM undo")["n"]
            self.db.execute("INSERT INTO checkpoint (id, undo, created_at) VALUES (?, ?, ?) ON CONFLICT (id) DO "
                            "UPDATE SET undo = excluded.undo, created_at = excluded.created_at", (key, mark, _now()))

    def checkpoints(self) -> list[str]:
        return [r["id"] for r in self._all("SELECT id FROM checkpoint ORDER BY rowid")]

    def rewind(self, key: str) -> dict:
        """Restore what the analyst had written at checkpoint `key`, and drop the checkpoints after it. Fetched
        data and the cache stay."""
        with self.transaction():
            mark = self._one("SELECT rowid, undo FROM checkpoint WHERE id = ?", key)
            if mark is None:
                raise StoreError(f"there is no checkpoint {key}")
            steps = self._all("SELECT statement FROM undo WHERE n > ? ORDER BY n DESC", mark["undo"])
            kept = {r["id"]: dict(r)
                    for r in self._all("SELECT id, url, retrieved_at, dated, text, sha256 FROM source")}
            for s in steps:
                self.db.execute(s["statement"])
            for sid, row in kept.items():  # a fetched page is never rewound: one the checkpoint did not keep is held
                if self._source(sid) is None:
                    self._hold(sid, row)
            for sid in [sid for sid in self._pending if self._source(sid) is not None]:
                self._unhold(sid)
            self.db.execute("DELETE FROM undo WHERE n > ?", (mark["undo"],))  # with what undoing them logged
            self.db.execute("DELETE FROM checkpoint WHERE rowid > ?", (mark["rowid"],))
            # every key: what the undone statements changed touched its own keys, and a rewind, rare and wholesale,
            # leaves no value worked out before it standing
            self.db.execute("INSERT INTO temp.touch (key) VALUES (?)", (touches.EVERYTHING,))
        return {"checkpoint": key, "undone": len(steps)}

    def meta(self, key: str) -> str | None:
        touches.read(touches.meta(key))
        row = self._one("SELECT value FROM meta WHERE key = ?", key)
        return row["value"] if row else None

    def settings(self, **values: str) -> None:
        """Settings the store states of itself, beside its as-of and its schema, written in one transaction of its own:
        what a reader of the store takes it to be (a research store's `mode`; a copy's `copied_from`, `copied_at`,
        `copied_through_record`)."""
        with self.transaction():
            for key, value in values.items():
                self._set_meta(key, str(value))

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------ transactions

    @contextmanager
    def transaction(self):
        """One transaction; a call inside another joins it. An exception rolls it back whole, but for what it read of
        the world and asked the judge: data fetched and Judgments answered are kept, being keyed by their content and
        written by no one (a tool's write and the reads its result needs are one transaction, TOOLS.md 5)."""
        with self.lock:
            with self._transaction():
                yield

    @contextmanager
    def _transaction(self):
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("INSERT INTO temp.writing (open) VALUES (1)")  # a transaction of the store's own: its changes log
        self._depth = 1
        changes = self.db.total_changes  # what it writes, counted: a commit that wrote says the file can be written
        self._pending_at_begin = dict(self._pending)  # the held pages as they were, should the transaction roll back
        self._tx_answers, self._tx_kept = set(), set()
        self._fetched = []
        self._estimable = []
        self._shocked_names = []
        try:
            yield
            if self._estimable:  # decided at the commit, once the call's other reads are done (TOOLS.md 5)
                self._check_estimable(next((c for c in self._estimable if c), None))
            if self._shocked_names:
                self._check_named(self._shocked_names)
        except BaseException as e:
            self._depth = 0
            self._tx_answers, self._tx_kept = set(), set()  # what it wrote of the pending set stays pending
            self._rolled_back()
            self._failed_write(e)
            self._keep_fetched()
            raise
        self._depth = 0
        self._fetched = []
        wrote = self.db.total_changes > changes
        before = self.version
        try:
            touched = dict(self.transaction_touched(open_=True))  # read before the version folds the log away
            made = self._fold()
            if self._tx_answers:  # each answer it gave belongs to the version it commits as, or to the one it read
                at = made[0] + 1 if made else self.version
                self.db.executemany("UPDATE answer SET version = ? WHERE derivation = ? AND subject = ?",
                                    [(at, d, s) for d, s in sorted(self._tx_answers)])
            self.db.execute("COMMIT")
        except BaseException as e:  # a COMMIT that fails leaves the transaction open: it is rolled back here
            self._tx_answers, self._tx_kept = set(), set()
            self._rolled_back()
            self._failed_write(e)
            raise
        self._tx_answers = set()
        kept, self._tx_kept = self._tx_kept, set()
        if kept:
            self._forget_pending(kept)
        self._made(made)
        self._tx_touched = (-1, {})
        self.engine.committed(touched, before, self.version if made else None)
        if wrote:  # a write that commits: the store can be written again
            self.unwritable = None

    def _fold(self) -> tuple[int, list[str], str] | None:
        """What the open transaction touched, made the version its commit is: each key its changed rows logged, and
        New York's date where it has turned since the last version found it. The version is one more than the file's
        own count, written in the same commit. None where it touched nothing, and its commit is no version."""
        keys = [r[0] for r in self.db.execute("SELECT DISTINCT key FROM temp.touch")]
        self.db.execute("DELETE FROM temp.touch")
        self.db.execute("DELETE FROM temp.writing")
        day = touches.today()
        if day != self._day:
            keys.append(touches.TODAY)
        # A commit made to the file by any other connection since this one's last holds changes no touch here
        # records, whatever wrote them: every key is touched. Read inside the write's lock, so none can come between.
        seen = self._data_version()
        if seen != self._seen:
            keys.append(touches.EVERYTHING)
            self._seen = seen
        if not keys:
            return None
        row = self._one("SELECT value FROM meta WHERE key = 'version'")
        on_file = int(row["value"]) if row else 0
        self._set_meta("version", str(on_file + 1))
        return on_file, keys, day

    def _made(self, made: tuple[int, list[str], str] | None) -> None:
        """A version committed. Each key it touched is touched at it, before the version itself is, so a reader that
        finds the version finds its touches."""
        if made is None:
            return
        on_file, keys, day = made
        version = on_file + 1
        for key in keys:
            self._touched[key] = version
        if touches.EVERYTHING in keys:
            self._everything_at = version
        if touches.TODAY in keys:
            self._day = day
        self.version = version

    def touched_at(self, key: str) -> int:
        """The last version whose commit touched `key` (harness.touches), and never one before a version that touched
        every key. A value worked out from reads made at version e is what the store answers at version v, at or
        after e, while every key it read was touched at or before e. New York's date, turned since the last version
        found it, is touched after every version committed, until a commit records it."""
        if key == touches.TODAY and touches.today() != self._day:
            return self.version + 1
        return max(self._touched.get(key, 0), self._everything_at)

    def step(self) -> int:
        """Where the open transaction stands in what it has changed: the place of its latest touch in its log, 0
        outside a transaction or before it has changed anything. A value worked out at one step is what its commit
        answers where no key it read is touched at a later step (touched_in_transaction). The log is read again only
        once the connection has changed a row since, as every touch does."""
        if not self._depth:
            return 0
        changes = self.db.total_changes
        if self._step_at[0] != changes:
            self._step_at = (changes, self._one("SELECT coalesce(max(n), 0) AS n FROM temp.touch")["n"])
        return self._step_at[1]

    def stamp(self) -> tuple[int, int]:
        """The state a read answers from: the version, and the step within the open transaction (harness.touches)."""
        return self.version, self.step()

    def transaction_touched(self, *, open_: bool = False) -> dict[str, int]:
        """Each key the open transaction has touched, with the step it last touched it at (touched_in_transaction),
        `EVERYTHING` among them where it rewound; empty outside a transaction. Read again only once the connection
        has changed a row since, as every touch does. `open_` reads it while the transaction is being committed."""
        if not (self._depth or open_):
            return {}
        changes = self.db.total_changes
        if self._tx_touched[0] != changes:
            self._tx_touched = (changes, dict(self.db.execute("SELECT key, max(n) FROM temp.touch GROUP BY key")))
        return self._tx_touched[1]

    def touched_in_transaction(self, key: str) -> int:
        """The step of the open transaction at which `key` was last touched, by a change of its own or by a rewind,
        which touches every key; 0 where it has not been."""
        return self._one("SELECT coalesce(max(n), 0) AS n FROM temp.touch WHERE key IN (?, ?)", key,
                         touches.EVERYTHING)["n"]

    def record_the_day(self) -> bool:
        """Where New York's date has turned since the last version found it, a version that touches it (TODAY), so the
        values that read it are worked out again once and are then current; False where it has not turned."""
        if touches.today() == self._day:
            return False
        with self.transaction():
            pass
        return True

    def _rolled_back(self) -> None:
        """The open transaction undone, where SQLite has not already undone it (as it may on a full disk or an I/O
        error), with what this side held of it."""
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")
        self._pending = self._pending_at_begin
        self._answers.clear()  # an id the transaction gave a reading can be given another's again
        self._unpriced_at = ((-1, -1), {})  # its stamps can be reached again by writes that differ
        self._step_at = (-1, 0)
        self._tx_touched = (-1, {})
        self.engine.rolled_back()

    def _failed_write(self, error: BaseException) -> None:
        """A write the store could not make (a failed refresh keeps the last observation at its
        recorded time): `unwritable` says since when and why, "full" for a full disk and "io" for any other failure to
        write the file, until a write commits again. What the page shows reads it (server.Page.state)."""
        why = unwritable(error)
        if why and self.unwritable is None:
            self.unwritable = {"why": why, "since": _now(), "error": f"{type(error).__name__}: {error}"}

    def _keep_fetched(self) -> None:
        """Write again, after a rollback, the data the transaction read, the Judgments it was answered, and the
        Computations it made: what the world and the judge said stands whatever became of the call that asked, and a
        Computation is keyed by its content, so a call that stood aside finds again, when it is made again,
        what its first attempt worked out, in place of working it out again. Each Computation keeps its id, which the
        Computations made after it name in their manifests; it is no value's answer until a read gives it as one, since
        the write it was made in never stood. A market's data goes with a market the rollback did not keep."""
        fetched, self._fetched = self._fetched, []
        if not fetched:
            return
        with self.transaction():
            for kind, args in fetched:
                if kind == "data":
                    market_id = args[7]  # (kind, subject, payload, request, retrieved, lo, hi, market_id, sha)
                    if market_id and not self._market_kept(market_id):
                        continue
                    self._insert_data(*args)
                elif kind == "computation":
                    cid, name, h = args[0], args[1], args[4]
                    if self._one("SELECT 1 FROM computation WHERE derivation = ? AND input_hash = ?", name, h) is None:
                        self._insert_computation(args)
                        self.db.execute("INSERT INTO seq (prefix, n) VALUES ('cmp', ?) ON CONFLICT (prefix) DO "
                                        "UPDATE SET n = max(n, excluded.n)", (int(cid.split(":")[1]),))
                else:
                    if self.judgment_by_hash(args[2], (args[4],)) is None:
                        self._insert_judgment(*args)
            self._fetched = []

    def _next_id(self, prefix: str) -> str:
        assert self._depth, "ids are allocated inside a transaction"
        n = self.db.execute(
            "INSERT INTO seq (prefix, n) VALUES (?, 1) ON CONFLICT (prefix) DO UPDATE SET n = n + 1 RETURNING n",
            (prefix,),
        ).fetchone()[0]
        return f"{prefix}:{n}"

    def _one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    # ------------------------------------------------------------------ what an id names

    def _object_row(self, oid: str) -> sqlite3.Row | None:
        touches.read(touches.id(oid))
        return self._one("SELECT * FROM object WHERE id = ?", oid)

    def _link_row(self, lid: str) -> sqlite3.Row | None:
        touches.read(touches.id(lid))
        return self._one("SELECT * FROM link WHERE id = ?", lid)

    def _source(self, sid: str) -> sqlite3.Row | None:
        touches.read(touches.id(sid))  # kept, or a page held under its id
        return self._one("SELECT * FROM source WHERE id = ?", sid)

    def _market_kept(self, mid: str) -> bool:
        touches.read(touches.id(mid))
        return self._one("SELECT 1 FROM market WHERE id = ?", mid) is not None

    def _kind(self, oid: Any) -> str | None:
        """What an id names: object, link, market, source, page (fetched, not kept), name,
        proxy_metric, derived_link, computation, judgment, search, or data; None if nothing."""
        if not isinstance(oid, str) or not oid:
            return None
        if oid == "portfolio":
            return "object" if self._object_row(oid) else None
        m = schema.SEQ_ID_RE.match(oid)
        if m:
            prefix = m.group(1)
            if prefix in ("prop", "factor", "note"):
                return "object" if self._object_row(oid) else None
            if prefix == "src":
                if self._source(oid):
                    return "source"
                return "page" if oid in self._pending else None
            table = {"jdg": "judgment", "cmp": "computation", "link": "link", "search": "search",
                     "data": "data"}[prefix]
            if table != "computation":  # a Computation is read by its content, which never changes (harness.touches)
                touches.read(touches.id(oid))
            elif self._pending_row(oid) is not None:  # worked out while the write turn was held, and pending
                return table
            return table if self._one(f"SELECT 1 FROM {table} WHERE id = ?", oid) else None
        if schema.PROXY_METRIC_RE.match(oid):
            return "proxy_metric" if self._proxy_exists(oid) else None
        if schema.METRIC_ID_RE.match(oid):
            return "object" if self._object_row(oid) else None
        if schema.MARKET_ID_RE.match(oid):
            return "market" if self._market_kept(oid) else None
        if schema.NAME_ID_RE.match(oid):
            return "name" if self._name_exists(oid) else None
        if schema.CASH_ID_RE.match(oid):  # cash in a currency, while the portfolio holds it
            touches.read(touches.links(oid, "holds", "in"))
            return "cash" if self._one("SELECT 1 FROM link WHERE relation = 'holds' AND to_id = ?", oid) else None
        if schema.DERIVED_LINK_RE.match(oid):
            return "derived_link" if self._derived_link(oid) else None
        return None

    _KIND_TYPE = {"market": "Market", "name": "Name", "cash": "Cash", "proxy_metric": "Metric", "source": "Source",
                  "page": "Source", "computation": "Computation", "judgment": "Judgment", "link": "link",
                  "derived_link": "link", "search": "search", "data": "data"}

    def type_of(self, oid: str) -> str | None:
        kind = self._kind(oid)
        if kind == "object":
            return self._object_row(oid)["type"]
        return self._KIND_TYPE.get(kind)

    def stated_of(self, oid: str) -> dict:
        kind = self._kind(oid)
        if kind == "object":
            return json.loads(self._object_row(oid)["stated"])
        if kind == "link":
            return json.loads(self._link_row(oid)["stated"])
        if kind == "market":
            row = self._one("SELECT venue, venue_id FROM market WHERE id = ?", oid)
            return {"venue": row["venue"], "id": row["venue_id"]}
        if kind == "name":
            return {"ticker": oid.split(":", 1)[1]}
        if kind in ("source", "page"):
            s = self.source_row(oid)
            return {k: s[k] for k in ("url", "retrieved_at", "dated") if s.get(k) is not None}
        return {}

    def stated_at(self, oid: str, field: str) -> str | None:
        """When a stated field of an object or a link was last written (ONTOLOGY.md 2), to the microsecond; None where it is
        not stated."""
        kind = self._kind(oid)
        row = self._object_row(oid) if kind == "object" else self._link_row(oid) if kind == "link" else None
        return json.loads(row["stated_at"]).get(field) if row is not None else None

    def describe(self, oid: str) -> dict | None:
        """An object as a derivation reads it: its id, type, and stated fields. A Name is its
        ticker and a proxy Metric its channel's proxy, whatever derives them."""
        m = schema.NAME_ID_RE.match(oid)
        if m:
            return {"id": oid, "type": "Name", "stated": {"ticker": m.group(1)}}
        if schema.PROXY_METRIC_RE.match(oid):
            return {"id": oid, "type": "Metric", "stated": {}}
        t = self.type_of(oid)
        if t is None:
            return None
        return {"id": oid, "type": t, "stated": self.stated_of(oid)}

    # Names: a ticker is a Name while it is held, looked through to, or named by a link.

    def _name_reasons(self, *, produced: bool = True) -> dict[str, list[str]]:
        reasons: dict[str, set[str]] = {}
        touches.read(touches.ends("name"))
        for r in self._all("SELECT relation, to_id FROM link WHERE to_id LIKE 'name:%'"):
            reasons.setdefault(r["to_id"], set()).add("held" if r["relation"] == "holds" else "named")
        for nid in self._consideration_names():
            reasons.setdefault(nid, set()).add("named")
        if produced:
            products = self.engine.produced(LOOKED)
            for oid, rec in products["objects"].items():
                if rec["type"] == "Name":
                    reasons.setdefault(oid, set())
            for rec in products["links"].values():
                if rec["relation"] == "looks_through_to":
                    reasons.setdefault(rec["to"], set()).add("looked through")
        return {k: sorted(v) for k, v in sorted(reasons.items())}

    def _looked_through(self, nid: str) -> bool:
        return any(rec["relation"] == "looks_through_to" and rec["to"] == nid
                   for rec in self.engine.produced(LOOKED)["links"].values())

    def _portfolio_name(self, nid: str) -> bool:
        """Held, or looked through to."""
        touches.read(touches.links(nid, "holds", "in"))
        if self._one("SELECT 1 FROM link WHERE relation = 'holds' AND to_id = ?", nid):
            return True
        return self._looked_through(nid)

    def _name_exists(self, nid: str) -> bool:
        touches.read(touches.links(nid, None, "in"))
        if self._one("SELECT 1 FROM link WHERE to_id = ? LIMIT 1", nid):
            return True
        if nid in self._consideration_names():
            return True
        rec = self.engine.produced(LOOKED)["objects"].get(nid)
        return bool(rec and rec["type"] == "Name")

    def _consideration_names(self) -> set[str]:
        """Securities and currencies explicitly named by a holding's consideration."""
        from harness.derive.series import dollar_pair
        out = set()
        touches.read(touches.rows("worth"))
        for row in self._all("SELECT stated FROM link WHERE relation='worth'"):
            value = json.loads(row["stated"])["value"]["value"]
            if value.get("shares"):
                out.add(value["shares"]["name"])
            if "cash" in value and value.get("currency", "USD") != "USD":
                out.add(schema.name_id(dollar_pair(value["currency"])))
        return out

    # Proxy Metrics: a channel's proxy price, kept while a proposition or a link names it.

    def _proxy_channels(self, ticker: str) -> list[str]:
        touches.read(touches.rows("Factor"))
        return [r["id"] for r in self._all(
            "SELECT id FROM object WHERE type = 'Factor' AND stated ->> '$.proxy' = ? ORDER BY rowid", ticker)]

    def _proxy_named(self, mid: str) -> bool:
        touches.read(touches.rows("Proposition"), touches.links(mid, None, "out"), touches.links(mid, None, "in"))
        if self._one("SELECT 1 FROM object WHERE type = 'Proposition' AND stated ->> '$.metric' = ? "
                     "LIMIT 1", mid):
            return True
        return self._one("SELECT 1 FROM link WHERE from_id = ? OR to_id = ? LIMIT 1", mid, mid) is not None

    def _priced_name(self, ticker: str) -> str | None:
        """The portfolio's Name whose price metric:price:{ticker} is: a name held or looked through to, not cash,
        and one that has a price."""
        nid = schema.name_id(ticker)
        return nid if not pseudo(ticker) and self._portfolio_name(nid) and not self._no_price(nid) else None

    def _no_price(self, nid: str) -> bool:
        """Whether a name has no price, by what its sources answered in the newest readings the store holds
        (portfolio.unpriced, the one definition every reader takes): read from the readings alone, so that whether a
        name's price is a metric never waits on the estimation. A name not yet read has its price to come. Held until
        the store next changes (a reading taken, a write, a rollback), each with the keys its reads recorded, which a
        read that finds it held records again."""
        at = self.stamp()
        if self._unpriced_at[0] != at:
            self._unpriced_at = (at, {})
        found = self._unpriced_at[1]
        if nid not in found:
            with touches.recording(self.stamp) as reads:
                answer = unpriced(nid.split(":", 1)[1], self._answer)
            found[nid] = (answer, tuple(reads))
        answer, keys = found[nid]
        touches.read(*keys)  # what it read, as the read that found it read it
        return answer

    def _answer(self, kind: str, subject: str) -> dict | None:
        """The newest reading of `kind` the store holds for `subject`, as portfolio.unpriced reads it: its `status` and
        `currency`, read once for each reading, which is never changed, where the whole payload, a price history,
        would otherwise be read to learn them. None where it holds none."""
        row = self._find_data(kind, subject, None)
        if row is None:
            return None
        if row["id"] not in self._answers:
            r = self._one("SELECT payload ->> '$.status' AS status, payload ->> '$.currency' AS currency FROM data "
                          "WHERE id = ?", row["id"])
            self._answers[row["id"]] = {"status": r["status"], "currency": r["currency"]}
        return self._answers[row["id"]]

    def _portfolio_names(self) -> list[str]:
        return [n for n, why in self._name_reasons().items() if not pseudo(n.split(":", 1)[1])
                and ("held" in why or "looked through" in why)]

    def _proxy_exists(self, mid: str) -> bool:
        m = schema.PROXY_METRIC_RE.match(mid)
        return bool(m) and (bool(self._proxy_channels(m.group(1))) or self._proxy_named(mid)
                            or self._priced_name(m.group(1)) is not None)

    def _proxy_metrics(self) -> list[str]:
        touches.read(touches.rows("Factor"), touches.rows("Proposition"), touches.ends("metric"))
        ids = {schema.proxy_metric_id(r["t"]) for r in self._all(
            "SELECT stated ->> '$.proxy' AS t FROM object WHERE type = 'Factor'")}
        ids.update(schema.proxy_metric_id(n.split(":", 1)[1]) for n in self._portfolio_names()
                   if not self._no_price(n))  # each name's price, where it has one
        for r in self._all("SELECT stated ->> '$.metric' AS m FROM object WHERE type = 'Proposition'"):
            if r["m"] and schema.PROXY_METRIC_RE.match(r["m"]):
                ids.add(r["m"])
        for r in self._all("SELECT from_id, to_id FROM link WHERE from_id LIKE 'metric:price:%' "
                           "OR to_id LIKE 'metric:price:%'"):
            for x in (r["from_id"], r["to_id"]):
                if schema.PROXY_METRIC_RE.match(x):
                    ids.add(x)
        return sorted(ids)

    # Derived links: each from the producer that gives it.

    def _derived_links(self, at: str | None = None) -> dict[str, dict]:
        """The derived links; with `at`, those that can end at that id, by its type and each relation's ends
        (_produced_relations), so that a read about one object computes no producer whose links never reach it."""
        gives = None if at is None else self._produced_relations(at)
        links = dict(self.engine.produced(gives)["links"]) if gives != () else {}
        return links if at is None else {lid: rec for lid, rec in links.items() if at in (rec["from"], rec["to"])}

    def _produced_relations(self, oid: str) -> tuple[str, ...]:
        """The relations a producer gives that can end at this id: a Name's look-through and its betas' beta_to, a
        Channel's beta_to, and none for any other type."""
        t = self.type_of(oid)
        return tuple(sorted(r for d in self.engine.registry.producers() for r in d.produces
                            if r in schema.RELATIONS and t in schema.RELATIONS[r].sources + schema.RELATIONS[r].targets))

    def _derived_link(self, lid: str) -> dict | None:
        m = schema.DERIVED_LINK_RE.match(lid)
        if not m:
            return None
        return self.engine.produced((m.group(1),))["links"].get(lid)

    # ------------------------------------------------------------------ reads for the engine

    def _link_dict(self, r: sqlite3.Row) -> dict:
        d = {"id": r["id"], "relation": r["relation"], "from": r["from_id"], "to": r["to_id"],
             "stated": json.loads(r["stated"])}
        if r["field"] is not None:
            d["field"] = r["field"]
        return d

    def links_of(self, oid: str, relation: str | tuple[str, ...] | None = None, direction: str = "out") -> list[dict]:
        """Stated links of an object, of one relation, of several, or of every relation but cites; citations only when
        `relation` is cites. A read of several relations reads each, and what changes links of another touches none of
        them (harness.touches)."""
        where = {"out": "from_id = ?", "in": "to_id = ?", "both": "(from_id = ? OR to_id = ?)"}[direction]
        args: list[Any] = [oid] if direction != "both" else [oid, oid]
        relations = (relation,) if isinstance(relation, str) or relation is None else tuple(relation)
        touches.read(*(touches.links(oid, r, d) for d in (("out", "in") if direction == "both" else (direction,))
                       for r in relations))
        if relation is None:
            where += " AND relation <> 'cites'"
        else:
            where += f" AND relation IN ({', '.join('?' for _ in relations)})"
            args.extend(relations)
        return [self._link_dict(r) for r in self._all(f"SELECT * FROM link WHERE {where} ORDER BY rowid", *args)]

    def link_by_id(self, lid: str) -> dict | None:
        r = self._link_row(lid)
        return self._link_dict(r) if r else None

    def citations_of(self, oid: str) -> dict[str, str]:
        touches.read(touches.links(oid, "cites", "out"))
        return {r["field"]: r["to_id"] for r in self._all(
            "SELECT field, to_id FROM link WHERE relation = 'cites' AND from_id = ? ORDER BY field", oid)}

    def watched(self, pid: str) -> bool:
        touches.read(touches.links(pid, "watches", "in"))
        return self._one("SELECT 1 FROM link WHERE relation = 'watches' AND to_id = ?", pid) is not None

    def given_by(self, pid: str) -> list[tuple[str, int]]:
        """Each chance given pid, true or false: the proposition it is the chance of, and its place in that one's
        chances."""
        out = []
        touches.read(touches.given(pid))
        for r in self._all("SELECT id, stated FROM object WHERE type = 'Proposition' AND instr(stated, ?) ORDER BY "
                           "rowid", json.dumps(pid)):
            for i, c in enumerate(json.loads(r["stated"]).get("chances") or []):
                if pid in (*c["given"], *c["given_not"]):
                    out.append((r["id"], i))
        return out

    def ids_of(self, kind: str) -> list[str]:
        """The instances a producer on `kind` runs over: stated objects, kept markets, the
        Names held or named, or the stated links of a relation."""
        if kind in MUTABLE_OBJECT_TYPES:
            touches.read(touches.objects(kind))
            ids = [r["id"] for r in self._all("SELECT id FROM object WHERE type = ? ORDER BY rowid", kind)]
            return ids + (self._proxy_metrics() if kind == "Metric" else [])
        if kind == "Market":
            touches.read(touches.objects("Market"))
            return [r["id"] for r in self._all("SELECT id FROM market ORDER BY rowid")]
        if kind == "Name":
            return list(self._name_reasons(produced=False))
        if kind in schema.RELATIONS:
            touches.read(touches.relation(kind))
            return [r["id"] for r in self._all("SELECT id FROM link WHERE relation = ? ORDER BY rowid", kind)]
        return []

    @staticmethod
    def _covers(have: tuple[str, str] | None, want: tuple[str, str] | None) -> bool:
        """Whether a read over `have` gives `want`, by the rule held data is found by (_find_data)."""
        return want is None or (have is not None and have[0] <= want[0] and have[1] >= want[1])

    def _start_ahead(self, kind: str, subjects: list[str], covering: tuple[str, str] | None,
                     stage: dict | None, later: bool = False) -> None:
        """Start each subject's read where none that covers it is under way: on the read-ahead pool, or, `later`, on
        the pool of reads another derivation will ask for."""
        pool = self._later if later else self._ahead
        fetcher = self._fetcher(kind)
        if fetcher is None or kind not in AHEAD_KINDS or self.reading_as_page():
            return

        def fetch(subject: str):  # the client's fetch alone, made ready to keep: no store, no lock (a caller may wait
            try:                  # on it holding it); a read for later gives way at its source's pace (http.Pace)
                with for_later() if later else nullcontext():
                    return Store.ready(fetcher(subject, covering))
            except Exception as e:
                return _Failed(e)  # read again where it is asked, or, standing aside, read as the source answered

        many = getattr(fetcher, "many", None)  # a client that answers many subjects in one request: one read for all
        started = 0
        batch: dict[str, Future] = {}
        with self._ahead_lock:
            for subject in subjects:
                held = self._ahead_reads.setdefault((kind, subject), [])
                if any(self._covers(have, covering) for have, _ in held):
                    continue
                if many is not None:
                    batch[subject] = Future()
                    held.append((covering, batch[subject]))
                else:
                    held.append((covering, pool.submit(fetch, subject)))
                started += 1
        if batch:
            def read_many() -> None:  # each subject's read runs from here, unless its caller took it over first
                live = [s for s, f in batch.items() if f.set_running_or_notify_cancel()]
                try:
                    with for_later() if later else nullcontext():
                        got = {s: Store.ready(g) if isinstance(g, dict) else g
                               for s, g in dict(many(live, covering) if live else {}).items()}
                except Exception as e:
                    got = {s: _Failed(e) for s in live}
                for s in live:
                    batch[s].set_result(got.get(s))

            pool.submit(read_many)
        if started and stage is not None:
            self.report({**stage, "kind": kind, "done": 0, "of": started})
            if self._standing_aside():  # each read reported as it lands, the call not waiting on it here
                landed = {"n": 0}

                def report(_: Future) -> None:
                    with self._ahead_lock:
                        landed["n"] += 1
                        n = landed["n"]
                    self.report({**stage, "kind": kind, "done": n, "of": started})

                with self._ahead_lock:
                    mine = [f for s in subjects for have, f in self._ahead_reads.get((kind, s)) or []
                            if self._covers(have, covering)]
                for f in mine:
                    f.add_done_callback(report)

    def _take_ahead(self, kind: str, subject: str, covering: tuple[str, str] | None,
                    wait: bool = True) -> dict | None:
        """The answer of a read started ahead that covers the range, waited for where it is under way, and taken
        from the pool's keeping: None where none was started, where it is still queued (the caller reads it now,
        rather than wait behind others), or where it failed. Not `wait`, a read still under way is left to land."""
        with self._ahead_lock:
            held = self._ahead_reads.get((kind, subject)) or []
            found = next((f for have, f in held if self._covers(have, covering)), None)
            if found is None or not (wait or found.done()):
                return None
            self._ahead_reads[(kind, subject)] = [(h, f) for h, f in held if f is not found]
        if found.cancel():  # still queued: the caller, which needs it now, reads it itself
            return None
        got = found.result()
        return None if isinstance(got, _Failed) else got

    @contextmanager
    def as_page(self):
        """The reads made on this thread are the page's (render.cache_only): the store reads none of its sources for
        them and starts none ahead, so what it does not hold waits for the analyst's turn (Waiting), and nothing stands
        aside for them. It holds on this thread alone, so a read the page makes inside a call, as a stage the call
        works out renders the portfolio panel on the call's thread (server.Page._progress), reads as the page reads,
        and every other thread, a call's or the minute's readings, reads with the store's sources."""
        saved = getattr(self._page, "on", False), getattr(self._aside, "on", False)
        self._page.on, self._aside.on = True, False
        try:
            yield
        finally:
            self._page.on, self._aside.on = saved

    def reading_as_page(self) -> bool:
        return bool(getattr(self._page, "on", False))

    def _fetcher(self, kind: str) -> Callable | None:
        """The source a read of `kind` on this thread goes to: the store's client, or, for the page's reads (as_page),
        the analyst's turn, which a read reaches by waiting for it."""
        fetcher = self.fetchers.get(kind)
        return _on_the_turn if fetcher is not None and self.reading_as_page() else fetcher

    @contextmanager
    def standing_aside(self, failed: set, met: dict | None = None):
        """A call made so that it never waits on a read holding the store's lock (TOOLS.md 5): a read it needs that the
        store does not hold, of a kind read ahead, is started, or found under way, and the call stands aside
        (StandAside), its write rolled back; it yields the reads it stood aside for, which its caller waits for
        holding no lock before making the call again, each landed read then kept as the call reads it. So a call is
        made again for as long as each attempt meets a read it has not met, or one still under way. A read that
        failed is started once more; failing again, the call fails, as one whose read failed past its retries does,
        or is refused with its source's answer, which it gives again wherever the call asks for that read. `failed`
        holds the reads that failed once, and `met` each read the call stood aside for with the read it waited on, or
        its source's answer, both across its attempts: a read met again once what it waited on has landed and been
        kept is one the store did not keep as answering it, a defect of ours (_aside_read). A
        reading taken again (a mark, a book) is read as before, under the lock: it is short, and an answer read early
        could stand as newer than one read since; so are the reads of what the portfolio owns (_standing_aside)."""
        self._aside.on, self._aside.waits, self._aside.failed = True, [], failed
        self._aside.met = {} if met is None else met
        try:
            yield self._aside.waits
        finally:
            self._aside.on = False

    def _aside_read(self, kind: str, subject: str, covering: tuple[str, str] | None, what: str) -> dict:
        """data() for a call standing aside: the read kept where it has landed; started, or found under way, and the
        call stood aside where it has not; where it failed, read once more, the call standing aside again."""
        def under_way() -> Future | None:
            with self._ahead_lock:
                return next((f for have, f in self._ahead_reads.get((kind, subject)) or []
                             if self._covers(have, covering)), None)

        key = (kind, subject, covering)
        found = under_way()
        waited = self._aside.met.get(key)
        if isinstance(waited, _Failed):  # it failed past one more try: its source's answer, wherever the call asks
            self._failed_answer(waited, subject, covering, what)
        if waited is not None and found is not waited:  # it landed, and what was kept does not answer it
            raise RuntimeError(f"{what}: read again by the call that waited for it to land, the store holding "
                               f"nothing that answers it")
        if found is None:
            self._start_ahead(kind, [subject], covering, None, later=True)
            found = under_way()
            if found is None:  # nothing here starts it: read as before
                return self._aside_now(key, kind, subject, covering, what)
        if not found.done():
            self._aside.met[key] = found
            self._aside.waits.append(found)
            raise StandAside(f"{what} is being read")
        with self._ahead_lock:
            held = self._ahead_reads.get((kind, subject)) or []
            self._ahead_reads[(kind, subject)] = [(h, f) for h, f in held if f is not found]
        got = None if found.cancelled() else found.result()
        if got is None:  # a read many subjects share that did not answer this one: read on its own, as before
            return self._aside_now(key, kind, subject, covering, what)
        if isinstance(got, _Failed):
            if isinstance(got.error, LookupError) or key in self._aside.failed:
                self._aside.met[key] = got
                self._failed_answer(got, subject, covering, what)
            self._aside.failed.add(key)  # once more, standing aside again
            self._start_ahead(kind, [subject], covering, None, later=True)
            again = under_way()
            if again is None:  # nothing here starts it: read once more within the call, as before
                return self._aside_now(key, kind, subject, covering, what)
            self._aside.met[key] = again
            self._aside.waits.append(again)
            raise StandAside(f"{what} is being read again")
        return self._put_ahead(kind, subject, got)

    def _aside_now(self, key: tuple, kind: str, subject: str, covering: tuple[str, str] | None, what: str) -> dict:
        """A read a call standing aside makes itself, nothing here starting it apart: kept; where it fails, its
        source's answer, kept for the call to give again wherever it asks for it (_failed_answer)."""
        try:
            got = self._read(self._fetcher(kind), subject, covering, what)
        except (Waiting, Unavailable, StoreError) as e:
            self._aside.met[key] = _Failed(e)
            raise
        return self._put_ahead(kind, subject, got)

    def _failed_answer(self, failed: "_Failed", subject: str, covering: tuple[str, str] | None, what: str) -> None:
        """Raises what the source answered a read that failed, as data() reads it: a refusal, or a read that failed."""
        def answered(*_):
            raise failed.error
        self._read(answered, subject, covering, what)

    def _standing_aside(self) -> bool:
        """Whether a read here stands aside: in a call standing aside, but for the reads of what the portfolio owns,
        its holdings priced and its funds looked through. Those read in many steps, each read started as soon as what
        it depends on is read and asked for at once, so that standing aside at each would compute them again from
        their start for every step; and they are read by the first read itself or a change to the holdings, which
        no other call waits on. They wait on their own reads, as they always have."""
        return getattr(self._aside, "on", False) and not any(
            self.engine.computing(name, "portfolio") for name in OWN_READS)

    def stood_aside(self) -> bool:
        """Whether the call being made on this thread has stood aside for a read under way."""
        return bool(getattr(self._aside, "on", False) and self._aside.waits)

    def stands_aside(self) -> bool:
        """Whether the call being made on this thread stands aside for what it needs that is under way."""
        return bool(getattr(self._aside, "on", False))

    def stand_aside_for(self, under_way: Future, what: str) -> None:
        """A call standing aside stands aside for a computation under way (Engine), as for a read: it raises
        StandAside, the computation among what its caller waits for holding no lock. Any other reader returns."""
        if getattr(self._aside, "on", False):
            self._aside.waits.append(under_way)
            raise StandAside(what)

    def in_transaction(self) -> bool:
        """Whether a transaction is open: asked by the thread holding the lock, whose transaction it is."""
        return bool(self._depth)

    def _put_ahead(self, kind: str, subject: str, got: dict) -> dict:
        did = self.put_data(kind, subject, got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                            range=got.get("range"), kept=got.get("kept"))
        return self._data_row(did)

    def data(self, kind: str, subject: str, *, covering: tuple[str, str] | None = None,
             required: bool = True, at: str | None = None) -> dict | None:
        """The newest data of `kind` for `subject` covering the range, fetched by the configured client when the store
        has none; with `at`, the reading the store held at that moment (see _find_data). One read of its key, landing
        what it answers where the store holds none (harness.touches.answering).

        What the client's read comes to (the seam with the clients, TOOLS.md 5): the source's answer, an answer of nothing
        included, is kept as data; a source that answers that the thing asked for does not exist (LookupError) refuses
        a writing call, with its reason and nothing written, and outside one the value is absent; a read that fails
        past the client's retries leaves the value waiting (Waiting), logged, and the next read tries again. With no
        client here for the kind, data the store does not hold is absent."""
        with touches.answering(touches.data(kind, subject)):
            return self._data(kind, subject, covering, required, at)

    def _data(self, kind: str, subject: str, covering: tuple[str, str] | None, required: bool,
              at: str | None) -> dict | None:
        row = self._find_data(kind, subject, covering, at)
        if row is not None:
            return row
        if schema.MARKET_ID_RE.match(subject) and not self._market_kept(subject):
            raise Unavailable(f"{subject} is not in the store: a market's book and history are read once a link or a "
                              f"note names it")
        what = reading_words(kind, subject, covering)
        fetcher = self._fetcher(kind)
        if fetcher is None:
            if required:
                raise Unavailable(f"no client here reads {what}")
            return None
        if at is None and kind in AHEAD_KINDS and self._standing_aside():
            return self._aside_read(kind, subject, covering, what)
        # a read started ahead: taken where it has landed; waited for where it is under way, but by a reader that does
        # not wait (the page's, reading from what the store holds while the analyst works)
        ahead = self._take_ahead(kind, subject, covering, wait=getattr(fetcher, "waits", True)) if at is None else None
        if ahead is not None:
            return self._put_ahead(kind, subject, ahead)
        got = self._read(fetcher, subject, covering, what)
        did = self.put_data(kind, subject, got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                            range=got.get("range"))
        return self._data_row(did)

    def missing(self, since: bool = False) -> list[str]:
        """The reads this thread's call could not obtain, in the words of what each reads (reading_words), each once in
        the order they failed: those that failed past their clients' retries. `since` starts the record anew for a
        call."""
        if since:
            self._unread.names = []
            return []
        return list(dict.fromkeys(getattr(self._unread, "names", None) or []))

    def _missed(self, what: str) -> None:
        names = getattr(self._unread, "names", None)
        if names is not None:
            names.append(what)

    def _read(self, fetcher, subject: str, covering, what: str) -> dict:
        """One client's read, as the seam has it: its answer; a refusal or an absence where the source says the thing
        does not exist; Waiting where it failed past its retries."""
        try:
            return fetcher(subject, covering)
        except (Waiting, Unavailable, StoreError):
            raise
        except LookupError as e:
            if self._depth:  # inside a writing call: the call is refused, with the source's answer
                raise StoreError(str(e.args[0]) if e.args else str(e)) from None
            raise Unavailable(f"{what}: {e}") from None
        except Exception as e:  # a read that failed past its client's retries: the value waits, and the cause is logged
            self._missed(what)
            raise Waiting(f"{what} could not be read: {e}") from e

    def fetch_data(self, kind: str, subject: str, *, covering: tuple[str, str] | None = None,
                   required: bool = True) -> dict | None:
        """As data(), for a caller that holds no lock: what the store holds is read under its lock, a fetch waits
        outside it, so the page's readings and renders go on, and what it fetched is written under the lock."""
        with self.lock:
            row = self._find_data(kind, subject, covering)
            if row is not None:
                return row
            fetcher = self._fetcher(kind)
            what = reading_words(kind, subject, covering)
            if fetcher is None:
                if required:
                    raise Unavailable(f"no client here reads {what}")
                return None
        got = self._take_ahead(kind, subject, covering) or self._read(fetcher, subject, covering, what)
        got = got if "kept" in got else Store.ready(got)  # made ready here, holding no lock
        with self.lock:
            row = self._find_data(kind, subject, covering)  # read meanwhile, by another thread
            if row is not None:
                return row
            did = self.put_data(kind, subject, got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                                range=got.get("range"), kept=got["kept"])
            return self._data_row(did)

    def prefetch(self, kind: str, subjects: Iterable[str], covering: tuple[str, str] | None = None,
                 workers: int = PREFETCH_WORKERS, stage: dict | None = None, wait: bool = True,
                 later: bool = False) -> int:
        """Fetch, side by side, the data of `kind` the store lacks for these subjects, and keep it as data rows,
        written in the order asked on this thread. What a derivation then reads is found as if fetched one by one;
        a fetch that fails here is fetched again as it is read, which records why. Returns how many were kept.
        With `stage`, each fetch is reported as it completes, `done` of `of`.

        With `wait` False, the reads are started and the call returns: a read that does not depend on what is being
        read now starts when its subjects are known, and whatever asks for it later (data, or a prefetch that waits)
        waits for it there and keeps it, as it keeps its own reads; one nothing asks for stays with the pool, and the
        machine's observations hold its answer (harness.observations). `later` marks reads another derivation will
        ask for, which wait behind none of those the derivation reading now asks for."""
        fetcher = self._fetcher(kind)
        asked = [s for s in dict.fromkeys(subjects) if not schema.MARKET_ID_RE.match(s)]
        held = self._find_many(kind, asked, covering)  # what the store holds, in one query, not one a subject
        todo = [s for s in asked if s not in held]
        if fetcher is None or not todo or not getattr(fetcher, "waits", True):  # the last: a reader that never waits
            return 0
        if not wait or (kind in AHEAD_KINDS and self._standing_aside()):  # standing aside: data() stands aside
            self._start_ahead(kind, todo, covering, stage, later or bool(wait))
            return 0
        if stage is not None:
            self.report({**stage, "kind": kind, "done": 0, "of": len(todo)})

        def fetch(subject: str):  # on a pool thread: read, or taken from a read ahead, and made ready to keep
            try:
                got = self._take_ahead(kind, subject, covering) or fetcher(subject, covering)
                return got if "kept" in got else Store.ready(got)
            except Exception:
                return None

        got: dict[str, Any] = {}
        many = getattr(fetcher, "many", None)  # a client that answers many subjects in one request
        if many is not None:
            for s in todo:  # those read ahead, waited for; the rest in one request
                ahead = self._take_ahead(kind, s, covering)
                if ahead is not None:
                    got[s] = ahead
            rest = [s for s in todo if s not in got]
            try:
                more = {s: Store.ready(g) if isinstance(g, dict) else g
                        for s, g in dict(many(rest, covering) if rest else {}).items()}
            except Exception:
                more = {}
            got = {s: got.get(s) or more.get(s) for s in todo}
            if stage is not None:
                self.report({**stage, "kind": kind, "done": len(todo), "of": len(todo)})
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
                futures = {pool.submit(fetch, s): s for s in todo}
                for future in as_completed(futures):
                    got[futures[future]] = future.result()
                    if stage is not None:
                        self.report({**stage, "kind": kind, "subject": futures[future], "done": len(got),
                                     "of": len(todo)})
        kept = 0
        with self.transaction():  # landed together: one commit for the reads it made side by side
            for subject in todo:  # written in the order asked
                g = got[subject]
                if g is None:
                    continue
                try:
                    self.put_data(kind, subject, g["payload"], g["request"], retrieved_at=g["retrieved_at"],
                                  range=g.get("range"), kept=g.get("kept"))
                    kept += 1
                except StoreError:
                    continue
        return kept

    def read_again(self, kind: str, subjects: Iterable[str], covering: tuple[str, str] | None = None) -> int:
        """Read these subjects' data of `kind` from their source again, whatever the store and this machine hold, and
        keep each answer as the newest data: an answer kept with no date (an identifier's mapping) whose subject no
        longer resolves to what it names. A read that fails keeps what was held. Returns how many were kept."""
        fetcher = self._fetcher(kind)
        todo = list(dict.fromkeys(subjects))
        if fetcher is None or not todo:
            return 0
        again = getattr(fetcher, "again", None) or getattr(fetcher, "many", None)
        try:
            got = dict(again(todo, covering)) if again is not None else {s: fetcher(s, covering) for s in todo}
        except Exception:
            return 0
        kept = 0
        for subject in todo:
            g = got.get(subject)
            if g is None:
                continue
            self.put_data(kind, subject, g["payload"], g["request"], retrieved_at=g["retrieved_at"],
                          range=g.get("range"))
            kept += 1
        return kept

    def _find_data(self, kind: str, subject: str, covering, at: str | None = None) -> dict | None:
        """The newest data of `kind` for `subject` covering the range, newest by when it was retrieved, whatever order
        the reads landed in (a read started earlier can land after one started later: the newer observation stands).
        With `at`, a moment, the reading the store held then: the newest retrieved at or before it, or, where none was
        yet, the first retrieved, the reading that first served it; `at` empty is before every reading, so it gives the
        first. One query: the covering range tested, and only the row found read."""
        touches.read(touches.data(kind, subject))
        sql = "SELECT id FROM data WHERE kind = ? AND subject = ?"
        args: list[Any] = [kind, subject]
        if covering is not None:
            sql += " AND range_from IS NOT NULL AND range_to IS NOT NULL AND range_from <= ? AND range_to >= ?"
            args += list(covering)
        newest = " ORDER BY julianday(retrieved_at) DESC, rowid DESC LIMIT 1"
        if at is None:
            r = self._one(sql + newest, *args)
        else:
            r = self._one(sql + " AND julianday(retrieved_at) <= julianday(?)" + newest, *args, at) if at else None
            r = r or self._one(sql + " ORDER BY julianday(retrieved_at), rowid LIMIT 1", *args)
        return self._data_row(r["id"]) if r is not None else None

    def data_many(self, kind: str, subjects: Iterable[str], *, covering: tuple[str, str] | None = None,
                  required: bool = True, at: str | None = None) -> dict[str, dict | None]:
        """data() for each of many subjects of one kind and range, in the order given: for each, exactly what
        data(kind, subject, covering=, required=, at=) returns, and the same raises at the same subject. What the store
        holds is found for all of them in one query (_find_many), each request parsed once, where data() asks once a
        subject; a subject it holds nothing for goes through data() itself, one by one in the order given, so its
        fetch, a read under way, and a call standing aside are as they are for one read. A call standing aside asks
        for all of them at once, so every one of their reads is started together and it stands aside for all of them
        (_aside_many): its next attempt finds them landed, where one read an attempt would run a call that needs
        hundreds (a fund's companies' prices) out of attempts."""
        order = list(dict.fromkeys(subjects))
        with touches.answering(*(touches.data(kind, s) for s in order)):  # one read, its landings its own
            return self._data_many(kind, order, covering, required, at)

    def _data_many(self, kind: str, order: list[str], covering: tuple[str, str] | None, required: bool,
                   at: str | None) -> dict[str, dict | None]:
        held = self._find_many(kind, order, covering, at)
        absent = [s for s in order if s not in held]
        if absent and at is None and kind in AHEAD_KINDS and self._fetcher(kind) is not None \
                and self._standing_aside():
            self._aside_many(kind, absent, covering)
        return {s: held[s] if s in held else self.data(kind, s, covering=covering, required=required, at=at)
                for s in order}

    def _aside_many(self, kind: str, subjects: list[str], covering: tuple[str, str] | None) -> None:
        """For a call standing aside that asks many subjects of one kind: each one's read started where none is under
        way, and every one still under way among what the call waits for, so that the call stands aside once for all
        of them; data() then stands aside at the first, as for one read, and takes those that have landed."""
        asked = [s for s in subjects if not schema.MARKET_ID_RE.match(s)]
        self._start_ahead(kind, asked, covering, None, later=True)
        with self._ahead_lock:
            pending = [f for s in asked for have, f in self._ahead_reads.get((kind, s)) or []
                       if self._covers(have, covering) and not f.done()]
        self._aside.waits.extend(pending)

    def _find_many(self, kind: str, subjects: list[str], covering, at: str | None = None) -> dict[str, dict]:
        """_find_data for many subjects, by subject, those the store holds a row for: one query a chunk, each subject's
        row chosen as _find_data chooses it (the newest retrieved, the rowid breaking a tie; with `at`, the newest
        retrieved at or before it, else the first retrieved)."""
        found: dict[str, dict] = {}
        touches.read(*(touches.data(kind, s) for s in subjects))
        for i in range(0, len(subjects), FIND_CHUNK):
            chunk = subjects[i:i + FIND_CHUNK]
            where = f"kind = ? AND subject IN ({','.join('?' * len(chunk))})"
            args: list[Any] = [kind, *chunk]
            if covering is not None:
                where += " AND range_from IS NOT NULL AND range_to IS NOT NULL AND range_from <= ? AND range_to >= ?"
                args += list(covering)
            if at is None:
                order, head = "julianday(retrieved_at) DESC, rowid DESC", []
            else:  # held by then, newest first; else none held by then, so the first retrieved
                by = "(julianday(retrieved_at) <= julianday(?))" if at else "0"
                order = (f"{by} DESC, CASE WHEN {by} THEN -julianday(retrieved_at) ELSE julianday(retrieved_at) END, "
                         f"CASE WHEN {by} THEN -rowid ELSE rowid END")
                head = [at] * 3 if at else []
            rows = self._all(
                "SELECT id, kind, subject, range_from, range_to, request, retrieved_at, sha256, market_id FROM ("
                "SELECT id, kind, subject, range_from, range_to, request, retrieved_at, sha256, market_id, "
                f"ROW_NUMBER() OVER (PARTITION BY subject ORDER BY {order}) AS n FROM data WHERE {where}) WHERE n = 1",
                *head, *args)
            for r in rows:
                d = dict(r)
                d["request"] = json.loads(d["request"])
                found[d["subject"]] = d
        return found

    def latest(self, kind: str, subject: str, covering) -> dict | None:
        """The newest data of `kind` for `subject` covering the range, or None, fetching nothing: what a reading
        taken again is compared with."""
        return self._find_data(kind, subject, covering)

    def data_subjects(self, kind: str, covering) -> list[str]:
        """The subjects the store holds data of `kind` for, covering the range (any range, with None), in the order
        first read."""
        touches.read(touches.data_of(kind))
        if covering is None:
            return [r["subject"] for r in self._all(
                "SELECT subject FROM data WHERE kind = ? GROUP BY subject ORDER BY min(rowid)", kind)]
        lo, hi = covering
        return [r["subject"] for r in self._all(
            "SELECT subject FROM data WHERE kind = ? AND range_from <= ? AND range_to >= ? GROUP BY subject "
            "ORDER BY min(rowid)", kind, lo, hi)]

    def given_at(self, lid: str) -> str | None:
        """When a holding was given: its link's created_at, which an edit to its figure sets again."""
        r = self._link_row(lid)
        return r["created_at"] if r is not None else None

    def readings(self, kind: str, subject: str) -> list[dict]:
        """Every reading of `kind` the store keeps for `subject`, oldest first by when it was retrieved: a book read
        again is kept when it differs from the one before (ONTOLOGY.md 2)."""
        touches.read(touches.data(kind, subject))
        return [self._data_row(r["id"]) for r in self._all(
            "SELECT id FROM data WHERE kind = ? AND subject = ? ORDER BY julianday(retrieved_at), rowid", kind, subject)]

    def _data_row(self, did: str) -> dict:
        r = self._one("SELECT id, kind, subject, range_from, range_to, request, retrieved_at, sha256, market_id "
                      "FROM data WHERE id = ?", did)
        d = dict(r)
        d["request"] = json.loads(d["request"])
        return d

    def data_payload(self, did: str) -> Any:
        return json.loads(self.data_text(did))

    def data_text(self, did: str) -> str:
        """A data row's payload as it is kept, its canonical JSON: what its sha256 is the hash of."""
        return self._one("SELECT payload FROM data WHERE id = ?", did)["payload"]

    def source_row(self, sid: str) -> dict | None:
        r = self._source(sid)
        if r is not None:
            return {**dict(r), "kept": True}
        page = self._pending.get(sid)
        if page is not None:
            return {**page, "id": sid, "kept": False}
        return None

    def computation_by_hash(self, name: str, h: str) -> dict | None:
        r = self._one("SELECT id, output FROM computation WHERE derivation = ? AND input_hash = ?", name, h)
        if r is not None:
            return {"id": r["id"], "output": json.loads(r["output"])}
        with self._pending_lock:
            cid = self._pending_hash.get((name, h))
            held = self._pending_cmp.get(cid) if cid is not None else None
        return {"id": cid, "output": json.loads(held["row"][6])} if held is not None else None

    def computation_output(self, cid: str) -> Any:
        return json.loads(self.computation_text(cid))

    def computation_text(self, cid: str) -> str:
        """A Computation's output as it is kept, its text."""
        held = self._pending_row(cid)
        if held is not None:
            return held[6]
        return self._one("SELECT output FROM computation WHERE id = ?", cid)["output"]

    def computation(self, cid: str) -> dict:
        """A Computation whole: its derivation and version, its subject, its manifest, and its output. A Computation
        is never changed, and its id is never given to another, so an id names one Computation, or none once the
        cache no longer keeps it. One worked out while another thread held the write turn is read from the
        pending set until the keeper writes it, by the same id."""
        r = self._computation_row(cid)
        if r is None:
            raise StoreError(f"{cid} is not in the store")
        return {**{k: r[k] for k in ("id", "derivation", "version", "subject", "input_hash", "created_at")},
                "inputs": json.loads(r["inputs"]), "output": json.loads(r["output"])}

    def _computation_row(self, cid: str) -> dict | None:
        """A Computation's row by its id, pending or kept, as the table holds its columns."""
        held = self._pending_row(cid)
        if held is not None:
            return dict(zip(COMPUTATION_COLUMNS, held))
        r = self._one("SELECT id, derivation, version, subject, input_hash, inputs, output, created_at FROM computation "
                      "WHERE id = ?", cid)
        return dict(r) if r is not None else None

    def _pending_row(self, cid: str) -> tuple | None:
        with self._pending_lock:
            held = self._pending_cmp.get(cid)
        return held["row"] if held is not None else None

    def put_computation(self, name: str, version: int, subject: str, h: str, manifest: dict, output: dict, *,
                        text: str | None = None, answer: bool = True) -> str:
        """A Computation kept, with the Computations its manifest names: its value's answer, each other way the value
        was worked out now replaced, and removed when this read ends unless something keeps it. With
        `answer` False, a Computation worked out around a wait (from an input held at an earlier moment) is kept by its
        content, so a figure read from it opens to it, and is no answer: the value's answer stays as it was, and this
        one goes once the value answers again. `text` is the output's canonical text where the caller has made it
        already.

        Where another thread holds the write turn (a statement under way), it is not waited for: the Computation is
        pending (_pend), read by its id and its hash at once, and written by the keeper when the turn is free; its
        answer with it, at the version it was worked out at."""
        text = text if text is not None else canonical(output)
        if not self.lock.acquire(blocking=False):
            cid = self._pend(name, version, subject, h, manifest, text, answer)
            if cid is not None:
                return cid
            self.lock.acquire()  # no id left to give without the turn: waited for, once a block is spent
        try:
            with self._transaction():
                with self._pending_lock:
                    held = self._pending_hash.get((name, h))
                if held is not None:  # worked out already while the turn was held: that one, kept here
                    cid = held
                    self._keep_named({cid})
                else:
                    cid = self._next_id("cmp")
                    row = (cid, name, version, subject, h, canonical(manifest), text, _now())
                    self._insert_computation(row, read_by(manifest))
                    self._fetched.append(("computation", row))  # kept through a rollback, as what was read is
                if answer:
                    self._set_answer(name, subject, cid, None)
                    self._replace_others(name, subject, cid)
        finally:
            self.lock.release()
        return cid

    def _pend(self, name: str, version: int, subject: str, h: str, manifest: dict, text: str,
              answer: bool) -> str | None:
        """A Computation worked out while another thread holds the write turn, pending with an id from the reserved
        block, and its answer, where it is one, at the version the store stood at: None where the block is spent."""
        at = self.version
        with self._pending_lock:
            cid = self._pending_hash.get((name, h))
            if cid is None:
                if self._ids[0] >= self._ids[1]:
                    return None
                cid = f"cmp:{self._ids[0]}"
                self._ids[0] += 1
                row = (cid, name, version, subject, h, canonical(manifest), text, _now())
                self._pending_cmp[cid] = {"row": row, "reads": read_by(manifest)}
                self._pending_hash[(name, h)] = cid
            if answer:
                self._pend_answer(name, subject, cid, None, at)
        self._wake_keeper()
        return cid

    def _pend_answer(self, derivation: str, subject: str | None, cid: str | None, cause: str | None, at: int) -> None:
        """An answer pending, where none pending belongs to a later version (held: the pending set's lock)."""
        key = (derivation, subject or "")
        held = self._pending_answer.get(key)
        if held is None or held[2] <= at:
            self._pending_answer[key] = (cid, cause, at, subject)

    def _keep_named(self, ids: Iterable[str]) -> None:
        """Each pending Computation among `ids` written to the file, each pending one its manifest names before it, so
        that nothing the file holds names a Computation it does not: inside the open transaction, where one
        is, and leaving the pending set when it commits; otherwise in a write of its own. Each is kept under the id it
        was given, though the file came to hold its content under another meanwhile (COMPUTATION). The caller holds the
        write turn."""
        with self._pending_lock:
            todo = [c for c in ids if c in self._pending_cmp and c not in self._tx_kept]
        if not todo:
            return
        if not self._depth:
            with self._transaction():
                self._keep_named(todo)
            return
        order: list[str] = []  # each after the pending ones it read
        seen: set[str] = set()

        def visit(cid: str) -> None:
            if cid in seen:
                return
            seen.add(cid)
            with self._pending_lock:
                held = self._pending_cmp.get(cid)
            if held is None:
                return
            for read in sorted(held["reads"]):
                visit(read)
            order.append(cid)

        for cid in todo:
            visit(cid)
        for cid in order:
            if cid in self._tx_kept:
                continue
            with self._pending_lock:
                held = self._pending_cmp.get(cid)
            if held is None:
                continue
            self._tx_kept.add(cid)
            if self._one("SELECT 1 FROM computation WHERE id = ?", cid) is None:
                self._insert_computation(held["row"], held["reads"])

    def _forget_pending(self, cids: Iterable[str]) -> None:
        """Pending Computations now in the file, which reads them there from here."""
        with self._pending_lock:
            for cid in cids:
                held = self._pending_cmp.pop(cid, None)
                if held is not None:
                    key = (held["row"][1], held["row"][4])
                    if self._pending_hash.get(key) == cid:
                        del self._pending_hash[key]

    def _kept_by_pending(self) -> set[str]:
        """The kept Computations the pending set keeps: each one a pending Computation read, and each a pending answer
        names."""
        with self._pending_lock:
            out = {r for held in self._pending_cmp.values() for r in held["reads"]}
            out |= {a[0] for a in self._pending_answer.values() if a[0]}
        return out

    def _wake_keeper(self) -> None:
        """The keeper told there is something pending: started on the first."""
        self._keeper_wake.set()
        if self._keeper is not None and self._keeper.is_alive():
            return
        with self._pending_lock:
            if self._keeper is None or not self._keeper.is_alive():
                self._keeper = threading.Thread(target=self._keep_pending, name=f"keeper:{self.dir.name}",
                                                daemon=True)
                self._keeper.start()

    def _keep_pending(self) -> None:
        """The keeper: whenever something is pending, it takes the write turn once it is free and writes the pending
        set (_flush_pending). It is the one that waits behind a statement; nothing that worked a value out waits."""
        while not (self._closed or self._stopping):
            self._keeper_wake.wait()
            self._keeper_wake.clear()
            if self._closed or self._stopping:
                return
            try:
                with self.lock:
                    if self._closed or self._stopping:
                        return
                    self._flush_pending()
            except Exception:
                if self._closed or self._stopping:
                    return
                log.warning("what is pending stays pending until the keeper's next write", exc_info=True)
                time.sleep(1.0)
                self._keeper_wake.set()

    def _flush_pending(self, *, reserve: bool = True) -> None:
        """The pending set written in one short transaction, holding the write turn: every Computation, each before
        what reads it, and every answer that belongs to no version earlier than the one the file holds. What was made
        pending meanwhile stays for the next write. A new block of ids is reserved once half the block is spent."""
        with self._pending_lock:
            cids = list(self._pending_cmp)
            answers = dict(self._pending_answer)
            low = self._ids[1] - self._ids[0] < ID_BLOCK // 2
        if cids or answers:
            written: list[tuple[str, str | None, str | None]] = []
            with self._transaction():
                self._keep_named(cids)
                for (derivation, _), (cid, cause, at, subject) in sorted(answers.items(), key=lambda kv: kv[0]):
                    if cid is not None and self._computation_row(cid) is None:
                        continue  # its Computation is no longer kept: it answers nothing
                    if self._set_answer(derivation, subject, cid, cause, at):
                        written.append((derivation, subject, cid))
            with self._pending_lock:
                for key, value in answers.items():
                    if self._pending_answer.get(key) == value:
                        del self._pending_answer[key]
            for derivation, subject, cid in written:
                self._replace_others(derivation, subject, cid)
        if low and reserve and not self._closed:
            self._reserve_ids()

    def _replace_others(self, derivation: str, subject: str | None, cid: str | None) -> None:
        """A value's answer replaces each other way the value was worked out, its earlier answer and what was worked out
        around a wait alike: each goes where nothing keeps it (_settle)."""
        self._replaced.update(r["id"] for r in self._all(
            "SELECT id FROM computation WHERE derivation = ? AND subject IS ? AND id IS NOT ?", derivation, subject,
            cid))

    def current_computation(self, derivation: str, subject: str) -> dict | None:
        """The Computation a read of this value gives now, whole as `get` gives it, or None where there is none (a value
        absent, or waiting; under render.cache_only, one not yet computed). The page opens a value's working by it, so
        the working it shows follows the value as the value moves."""
        v = self.engine.value(derivation, subject)
        return self.get(v.basis) if v.basis and str(v.basis).startswith("cmp:") else None

    def answered(self, derivation: str, subject: str | None, cid: str | None, cause: str | None = None) -> None:
        """The engine gave this answer as a value: a Computation it took again by its inputs' hash, or none, the value
        absent or refused, with why (a value still waiting on a read has given no answer). Written only where it
        differs from the value's answer the store holds; the Computation the value answered with before is replaced,
        and goes where nothing keeps it (_settle). An answer is what the value is in the store as it stands, so one given
        inside a write rolls back with it: a refused statement's figures were never the store's (_keep_fetched keeps
        its Computations, by their content, and not its answers)."""
        if not self.lock.acquire(blocking=False):  # another thread holds the write turn: pending, as a Computation is
            with self._pending_lock:
                self._pend_answer(derivation, subject, cid, cause, self.version)
            self._wake_keeper()
            return
        try:
            held = self._one("SELECT id, cause FROM answer WHERE derivation = ? AND subject = ?", derivation,
                             subject or "")
            if held is not None and held["id"] == cid and (cid is not None or held["cause"] == cause):
                return
            with self._transaction():
                self._set_answer(derivation, subject, cid, cause)
            self._replace_others(derivation, subject, cid)
        finally:
            self.lock.release()

    def _set_answer(self, derivation: str, subject: str | None, cid: str | None, cause: str | None,
                    at: int | None = None) -> bool:
        """A value's answer written, inside the open transaction: one the transaction gives belongs to the version it
        commits as, and is stamped there (_transaction); one pending, given `at`, the version it was worked out at,
        stands where the answer held belongs to an earlier version, or to the same one and names no Computation (the
        value read as waiting or absent there, which the pending one now answers). An answer held at the same version
        that names a Computation is of the same state, and stays: replacing it would remove what a reader was given. A
        Computation it names that is pending is written first. Whether it was written."""
        if cid is not None:
            self._keep_named({cid})
        if at is None:
            self._tx_answers.add((derivation, subject or ""))
        return self.db.execute(
            "INSERT INTO answer (derivation, subject, id, cause, version) VALUES (?, ?, ?, ?, ?) ON CONFLICT "
            "(derivation, subject) DO UPDATE SET id = excluded.id, cause = excluded.cause, version = excluded.version "
            "WHERE excluded.version IS NULL OR answer.version IS NULL OR answer.version < excluded.version "
            "OR (answer.version = excluded.version AND answer.id IS NULL)",
            (derivation, subject or "", cid, cause, at)).rowcount > 0

    def last_computation(self, derivation: str, subject: str) -> dict | None:
        """The value's answer: the Computation the engine last gave as a value, by its derivation and subject,
        whatever the store's statements and data are now, with its id, the derivation's version it was worked out by,
        its output, and when it was made; where the engine last read the value absent or refused, `id` none and why,
        `absent`; and None where the value was never worked out. It is the one the cache keeps for the value, so the
        page can show the value last worked out while the new one is worked out. It is read on the calling thread's own
        connection, in one read, with no lock: it reads what the store last committed, never a call's writes not yet
        committed, and it neither waits on a call holding the store's lock nor holds up the store's writes (SQLite's
        write-ahead log, which lets readers and a writer proceed together on separate connections:
        https://www.sqlite.org/wal.html)."""
        with self._pending_lock:  # an answer pending is the latest the value was given
            pending = self._pending_answer.get((derivation, subject or ""))
        own = self._own_db()
        own.execute("BEGIN")
        try:
            a = {"id": pending[0], "cause": pending[1]} if pending is not None else own.execute(
                "SELECT id, cause FROM answer WHERE derivation = ? AND subject = ?", (derivation, subject or "")).fetchone()
            cid = a["id"] if a is not None else None
            held = self._pending_row(cid) if cid else None
            r = dict(zip(COMPUTATION_COLUMNS, held)) if held is not None else own.execute(
                "SELECT id, version, output, created_at FROM computation WHERE id = ?", (cid,)).fetchone() if cid else None
        finally:
            own.execute("COMMIT")
        if a is None:
            return None
        if r is None:
            return {"id": None, "derivation": derivation, "subject": subject, "absent": a["cause"]}
        return {"id": cid, "derivation": derivation, "subject": subject, "version": r["version"],
                "output": json.loads(r["output"]), "created_at": r["created_at"]}

    def _own_db(self) -> sqlite3.Connection:
        """The calling thread's own connection to the store's file, read only, opened on its first read."""
        own = getattr(self._own, "db", None)
        if own is None:
            own = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, autocommit=True,
                                  check_same_thread=False)
            own.row_factory = sqlite3.Row
            own.execute("PRAGMA busy_timeout = 5000")
            self._own.db = own
            with self._owns_lock:
                self._owns.append(own)
        return own

    def computation_of(self, cid: str) -> dict | None:
        """Which value a Computation is the working of: its derivation and its subject, read without its inputs and
        output, or None where the store does not keep it. The page links a current figure's working by them, so the
        link opens the value's working as it is when it is opened (current_computation)."""
        held = self._pending_row(cid)
        if held is not None:
            return {"derivation": held[1], "subject": held[3]}
        r = self._one("SELECT derivation, subject FROM computation WHERE id = ?", cid)
        return {"derivation": r["derivation"], "subject": r["subject"]} if r else None

    # ------------------------------------------------------------------ what the cache keeps

    def name(self, result: Any) -> None:
        """The Computations a tool's result names, which the conversation now names: kept from here, before the
        trace has the result."""
        found = {m.decode() for m in CMP_RE.findall(json.dumps(result, default=str).encode())}
        self._named.update(found)
        with self.lock:  # a pending one it names is kept before the trace names it
            self._keep_named(found)

    def _named_now(self) -> set[str]:
        """Every Computation the conversation and the record name: the trace read on to its last whole line."""
        try:
            with open(self.trace_path, "rb") as f:
                f.seek(self._trace_at)
                chunk = f.read()
        except FileNotFoundError:
            return self._named
        end = chunk.rfind(b"\n") + 1
        if end:
            self._named.update(m.decode() for m in CMP_RE.findall(chunk[:end]))
            self._trace_at += end
        return self._named

    def _settle(self) -> None:
        """At the end of a read (StoreLock.release): each Computation it replaced is removed where nothing keeps it, one
        that is not its value's answer, that the conversation and the record do not name, and that no kept
        Computation read; and with it, whatever it read that nothing else keeps."""
        if not self._replaced or self._settling or self._closed:
            return
        self._settling = True
        replaced, self._replaced = self._replaced, set()
        try:
            named = self._named_now() | self._kept_by_pending()
            todo = sorted(replaced)
            with self._transaction():
                while todo:
                    cid = todo.pop()
                    if cid in named:
                        continue
                    if self._one("SELECT 1 FROM computation WHERE id = ?", cid) is None:
                        continue
                    if self._one("SELECT 1 FROM answer WHERE id = ? LIMIT 1", cid) is not None or \
                            self._one("SELECT 1 FROM computation_read WHERE read = ? LIMIT 1", cid) is not None:
                        continue
                    todo += [r["read"] for r in self._all("SELECT read FROM computation_read WHERE id = ?", cid)]
                    self.db.execute("DELETE FROM computation_read WHERE id = ?", (cid,))
                    self.db.execute("DELETE FROM computation WHERE id = ?", (cid,))
        except BaseException:
            self._replaced |= replaced  # removed at a later read's end
            raise
        finally:
            self._settling = False

    def _collect(self) -> int:
        """The cache brought under its rule whole, inside the caller's transaction: each value's answer (`answer`),
        those the conversation and the record name, and every one a kept one read, at any depth, are kept, with the
        index of what each read; the rest are removed. Returns how many were removed."""
        todo = [r["id"] for r in self._all("SELECT id FROM answer WHERE id IS NOT NULL")] + \
            sorted(self._named_now() | self._kept_by_pending())
        reads: dict[str, set[str]] = {}
        while todo:
            cid = todo.pop()
            if cid in reads:
                continue
            row = self._one("SELECT inputs FROM computation WHERE id = ?", cid)
            if row is None:
                continue
            reads[cid] = read_by(json.loads(row["inputs"]))
            todo += sorted(reads[cid])
        self.db.execute("CREATE TEMP TABLE kept_now (id TEXT PRIMARY KEY)")
        try:
            self.db.executemany("INSERT INTO kept_now (id) VALUES (?)", [(c,) for c in reads])
            removed = self.db.execute("DELETE FROM computation WHERE id NOT IN (SELECT id FROM kept_now)").rowcount
        finally:
            self.db.execute("DROP TABLE kept_now")
        self.db.execute("DELETE FROM computation_read")
        self.db.executemany("INSERT INTO computation_read (id, read) VALUES (?, ?)",
                            [(c, r) for c, rs in reads.items() for r in sorted(rs)])
        return removed

    def _insert_computation(self, row: tuple, reads: set[str] | None = None) -> None:
        """A Computation's row, with the Computations its manifest names (computation_read), each pending one of which
        is written first (_keep_named)."""
        reads = read_by(json.loads(row[5])) if reads is None else reads
        self._keep_named(reads)
        self.db.execute("INSERT INTO computation (id, derivation, version, subject, input_hash, inputs, output, "
                        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
        self.db.executemany("INSERT OR IGNORE INTO computation_read (id, read) VALUES (?, ?)",
                            [(row[0], read) for read in sorted(reads)])

    def judgment_by_hash(self, h: str, models: tuple[str, ...] | None = None) -> dict | None:
        """The Judgment answered under this key by one of the models, the latest; any model when
        none is named (a store replayed without its judge)."""
        touches.read(touches.judgment(h))
        if models:
            marks = ", ".join("?" for _ in models)
            r = self._one(f"SELECT id, answer FROM judgment WHERE input_hash = ? AND model IN ({marks}) "
                          f"ORDER BY rowid DESC LIMIT 1", h, *models)
        else:
            r = self._one("SELECT id, answer FROM judgment WHERE input_hash = ? ORDER BY rowid DESC LIMIT 1", h)
        return {"id": r["id"], "answer": json.loads(r["answer"])} if r else None

    def put_judgment(self, kind: str, statement: str, h: str, evidence: list, answer: float, model: str,
                     raw: str | None) -> str:
        with self.transaction():
            jid = self._insert_judgment(kind, statement, h, evidence, answer, model, raw)
            self._fetched.append(("judgment", (kind, statement, h, evidence, answer, model, raw)))
        return jid

    def _insert_judgment(self, kind, statement, h, evidence, answer, model, raw) -> str:
        self._refuse_credentials([evidence, raw], names=False)
        jid = self._next_id("jdg")
        self.db.execute(
            "INSERT INTO judgment (id, kind, statement, input_hash, inputs, answer, model, raw, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (jid, kind, statement, h, canonical(evidence), canonical(answer), model, raw, _now()),
        )
        if self.meta("judge_model") is None:
            self._set_meta("judge_model", model)
        return jid

    # ------------------------------------------------------------------ the forecast record

    def written(self) -> tuple[dict, dict[str, str]]:
        """A copy of what the analyst has written, as a checkpoint holds it: each table's rows with their stated fields,
        and the as-of; each kept page by its hash, with the texts by hash beside the copy."""
        out: dict[str, Any] = {"asof": self.meta("asof")}
        texts: dict[str, str] = {}
        for t, (columns, _) in WRITTEN.items():
            rows = []
            for r in self._all(f"SELECT {', '.join(columns)} FROM {t} ORDER BY rowid"):
                row = dict(r)
                for c in ("stated", "params", "results"):
                    if isinstance(row.get(c), str):
                        row[c] = json.loads(row[c])
                if t == "source":
                    texts[row["sha256"]] = row.pop("text")
                rows.append(row)
            out[t] = rows
        return out, texts

    def put_record(self, *, session: str | None, turn: int | None, stopped: bool, rows: list, written: dict,
                   texts: dict[str, str], versions: dict) -> str:
        """One reply's record, kept unchanged."""
        named = {m.decode() for c in (canonical(rows), canonical(written)) for m in CMP_RE.findall(c.encode())}
        with self.transaction():
            self._keep_named(named)  # each pending Computation it names kept first, in the same write
            rid = self._next_id("rec")
            for sha, text in texts.items():
                self.db.execute("INSERT INTO record_text (sha256, text) VALUES (?, ?) ON CONFLICT (sha256) DO NOTHING",
                                (sha, text))
            self.db.execute(
                "INSERT INTO record (id, session, turn, stopped, asof, rows, written, versions, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, session, turn, int(bool(stopped)), self.meta("asof"), canonical(rows), canonical(written),
                 canonical(versions), _now()))
        # what the record names, it keeps
        self._named.update(named)
        return rid

    def records(self) -> list[dict]:
        """Every reply's record, in the order they were made."""
        out = []
        for r in self._all("SELECT * FROM record ORDER BY rowid"):
            row = dict(r)
            for c in ("rows", "written", "versions"):
                row[c] = json.loads(row[c])
            row["stopped"] = bool(row["stopped"])
            out.append(row)
        return out

    def put_outcome(self, proposition: str, definition: str, stated: dict, outcome: int,
                    determined_by: dict) -> str | None:
        """A watched proposition's outcome, once for each definition of it; None where it is already recorded."""
        with self.transaction():
            if self._one("SELECT 1 FROM outcome WHERE proposition = ? AND definition = ?", proposition, definition):
                return None
            oid = self._next_id("out")
            self.db.execute(
                "INSERT INTO outcome (id, proposition, definition, stated, outcome, determined_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (oid, proposition, definition, canonical(stated), int(outcome), canonical(determined_by), _now()))
        return oid

    def outcomes(self) -> list[dict]:
        out = []
        for r in self._all("SELECT * FROM outcome ORDER BY rowid"):
            row = dict(r)
            row["stated"], row["determined_by"] = json.loads(row["stated"]), json.loads(row["determined_by"])
            out.append(row)
        return out

    # ------------------------------------------------------------------ fetched pages, data, searches

    def put_page(self, url: str, text: str, *, retrieved_at, dated=None) -> str:
        """A page fetched with fetch_source. It is held, not kept: it becomes a Source when it
        is cited for a stated fact or linked as evidence, and a page never used is not kept."""
        if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
            raise StoreError(f"{url!r} is not an http or https URL, so there is no page to keep")
        if not isinstance(text, str) or not text.strip():
            raise StoreError(f"the page at {url} came back with no text to keep")
        retrieved = _check("page", schema.Field("retrieved_at", schema.DATETIME, required=True), retrieved_at)
        dated = _check("page", schema.Field("dated", schema.DATE), dated)
        self._refuse_credentials(url)
        self._refuse_credentials(text, names=False)
        held = self.held_page(url)
        if held is not None:
            return held
        with self.transaction():
            sid = self._next_id("src")
            self._hold(sid, {"url": url, "retrieved_at": retrieved, "dated": dated, "text": text,
                             "sha256": text_hash(text)})
        return sid

    def held_page(self, url: str) -> str | None:
        """The id of the page the store holds at `url`, kept or only fetched; None where it holds none."""
        row = self._one("SELECT id FROM source WHERE url = ?", url)
        if row:
            return row["id"]
        return next((sid for sid, page in self._pending.items() if page["url"] == url), None)

    def _hold(self, sid: str, page: dict) -> None:
        """A fetched page, held and not kept, citable under its id, and kept on disk so that it outlasts a restart."""
        self._pending[sid] = {k: page[k] for k in ("url", "retrieved_at", "dated", "text", "sha256")}
        self.db.execute("INSERT OR REPLACE INTO held (id, url, retrieved_at, dated, text, sha256) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (sid, *(self._pending[sid][k] for k in
                                                       ("url", "retrieved_at", "dated", "text", "sha256"))))

    def _unhold(self, sid: str) -> dict | None:
        """A held page, no longer held: it is kept as a Source."""
        self.db.execute("DELETE FROM held WHERE id = ?", (sid,))
        return self._pending.pop(sid, None)

    @staticmethod
    def _texts(payload: Any, request: Any) -> tuple[str, str, str]:
        """What a read is kept as: its payload's and its request's canonical texts and the payload's hash, each checked
        to carry no credential. Pure: made where the read was fetched, holding no lock (ready), or as it lands."""
        if not isinstance(request, dict):
            raise StoreError("data records its request as an object: the endpoint and its parameters")
        Store._refuse_credentials(request)
        try:
            payload_text = canonical(payload)
            request_text = canonical(request)
        except (TypeError, ValueError) as e:
            raise StoreError(f"data is JSON: {e}") from None
        if credentials.in_text(payload_text):  # a payload's text searched once; walked only to say where
            Store._refuse_credentials(payload, names=False)
            raise StoreError("the store keeps every request and payload without credentials; one is in the payload")
        return payload_text, request_text, text_hash(payload_text)

    @staticmethod
    def ready(got: dict) -> dict:
        """A client's answer made ready to keep on the thread that fetched it, holding no lock (a read lands
        with only its insert under the lock): `kept`, its texts and hash (_texts), or the refusal they met, which the
        read meets again where it lands (put_data)."""
        try:
            kept: Any = Store._texts(got.get("payload"), got.get("request"))
        except StoreError as e:
            kept = e
        return {**got, "kept": kept}

    def put_data(self, kind: str, subject: str, payload: Any, request: dict, *, retrieved_at,
                 range: tuple[str, str] | None = None, kept: Any = None) -> str:
        """Data fetched to derive values. A market's data is kept with the market. `kept`, what ready made of the read
        where it was fetched, spares the work of making it here, under the lock."""
        if not isinstance(kind, str) or not kind or not isinstance(subject, str) or not subject:
            raise StoreError("data has a kind and a subject")
        if isinstance(kept, StoreError):
            raise kept
        payload_text, request_text, sha = kept if kept is not None else self._texts(payload, request)
        retrieved = _check("data", schema.Field("retrieved_at", schema.DATETIME, required=True), retrieved_at)
        if range is not None:
            lo, hi = (_check("data", schema.Field("range", schema.DATE, required=True), x) for x in range)
        else:
            lo = hi = None
        market_id = subject if schema.MARKET_ID_RE.match(subject) else None
        with self.transaction():
            if market_id and not self._market_kept(market_id):
                raise StoreError(f"{market_id} is not kept; a market's data is kept with the market")
            did = self._insert_data(kind, subject, payload_text, request_text, retrieved, lo, hi, market_id, sha)
            self._fetched.append(("data", (kind, subject, payload_text, request_text, retrieved, lo, hi, market_id,
                                           sha)))
        return did

    def _insert_data(self, kind, subject, payload_text, request_text, retrieved, lo, hi, market_id,
                     sha: str | None = None) -> str:
        did = self._next_id("data")
        self.db.execute(
            "INSERT INTO data (id, kind, subject, range_from, range_to, request, retrieved_at, payload, sha256, "
            "market_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, kind, subject, lo, hi, request_text, retrieved, payload_text, sha or text_hash(payload_text),
             market_id, _now()),
        )
        return did

    @staticmethod
    def _refuse_credentials(obj: Any, *, names: bool = True) -> None:
        """A recorded value carries no credential: requests and URLs are checked for
        credential names and Authorization values, payloads and texts for configured secrets."""
        where = credentials.find(obj, names=names)
        if where:
            raise StoreError(f"the store keeps every request and payload without credentials; one is at {where}")

    def record_search(self, tool: str, params: dict, results: list, *, proposition_id: str | None = None,
                      venue: str | None = None, endpoint: str | None = None, query: str | None = None,
                      call: int | None = None) -> str:
        """The record of one search: its query (a market search's or a web search's text, a company search's
        ticker), its parameters, and, for a company search, its kind, with what it returned. Results are kept on
        the search and nowhere else."""
        if tool == "search_markets":
            if not isinstance(query, str) or not query.strip() or venue is not None or endpoint is not None:
                raise StoreError("a market search records its text, as its query")
        elif tool == "search_web":
            if not isinstance(query, str) or not query.strip() or venue is not None or endpoint is not None:
                raise StoreError("a web search records its query")
        elif tool == "search_company":
            if not isinstance(query, str) or not query.strip() or not isinstance(endpoint, str) or not endpoint \
                    or venue is not None:
                raise StoreError("a company search records its ticker, as its query, and its kind, as its endpoint")
        else:
            raise StoreError("a search is search_markets, search_web, or search_company")
        if not isinstance(params, dict) or not isinstance(results, list):
            raise StoreError("a search records its parameters as an object and its results as a list")
        self._refuse_credentials(params)
        self._refuse_credentials(results, names=False)
        try:
            params_text, results_text = canonical(params), canonical(results)
        except (TypeError, ValueError) as e:
            raise StoreError(f"a search record is JSON: {e}") from None
        with self.transaction():
            if proposition_id is not None and self.type_of(proposition_id) != "Proposition":
                raise StoreError(f"{proposition_id} is not a proposition in the store")
            sid = self._next_id("search")
            self.db.execute(
                "INSERT INTO search (id, proposition_id, tool, venue, endpoint, query, params, results, created_call, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, proposition_id, tool, venue, endpoint, query, params_text, results_text, call, _now()),
            )
        return sid

    # ------------------------------------------------------------------ add

    def add(self, type: str, fields: dict, *, call: int | None = None, derived: bool = True) -> dict:
        """Create an object of an analyst type from its stated fields; returned as `get` returns it, or, not
        `derived`, as its id, type, and stated fields alone, for a caller whose answer reads none of what the store
        derives for it."""
        oid = self._add(type, fields, origin="analyst", call=call)
        return self.get(oid, exclude_derived=("distribution",)) if derived else self.describe(oid)

    def _add(self, type_name: str, fields: dict, *, origin: str, call: int | None) -> str:
        t = schema.OBJECTS.get(type_name)
        if t is None:
            raise StoreError(f"there is no type {type_name!r}; add creates {_or(schema.ADDABLE)}")
        if t.creator != schema.ANALYST:
            raise StoreError(schema.NOT_ADDED[type_name])
        if not isinstance(fields, dict):
            raise StoreError("fields is an object of stated fields")
        values, cites = self._stated_values(t, fields)
        holdings = self._holdings(fields.get("holdings"), values) if type_name == "Portfolio" else []
        named = self._prefetch_named(values.get("chances")) if type_name == "Proposition" else {}
        with self.transaction():
            self._enter_named(named, call)
            oid = self._new_object_id(t, values)
            self._check_references(t, values)
            if type_name == "Portfolio":
                self._fix_asof(values["asof"])
            if type_name == "Factor":
                self._check_channel_unique(oid, values)
            if type_name == "Proposition":
                self._check_occurrence(values)
                self._check_chances(oid, values)
            for sid in cites.values():
                self._citable(sid)
            values = self._noted(oid, type_name, values, {}, call)
            self._insert_object(oid, type_name, values, origin, call)
            for field, sid in cites.items():
                self._cite(oid, field, sid, origin, call)
            if type_name == "Proposition":
                self._check_priced_in(oid, values)
                self._check_follow(oid, values)
                self._check_baseline(oid, values.get("baseline"))
                self._cite_basis(oid, "Proposition", values, call)
                self._tie_notes(oid, "Proposition", values, {}, call)
            for ticker, amount in holdings:  # each to its company's name, the security as given (MATH.md 3.1)
                if ticker.startswith(("market:", "cash:")):
                    self._link("portfolio", "holds", ticker, amount, origin=origin, call=call)
                else:
                    self._insert_link("holds", "portfolio", self._held_name(ticker, values.get("asof")),
                                      {"ticker": ticker, **amount}, origin, call)
            if type_name == "Portfolio":  # its as-of is what makes the channels' window
                self._estimable.append(None)
            if type_name == "Factor":
                self._estimable.append(oid)
        return oid

    def _stated_values(self, t: schema.ObjectType, given: dict) -> tuple[dict, dict]:
        values: dict[str, Any] = {}
        cites: dict[str, str] = {}
        for key, raw in given.items():
            if key in t.add_params:
                continue
            f = t.field(key)
            if f is None:
                if key in t.derived:
                    raise StoreError(f"{t.name}.{key} is derived; the store computes it")
                raise StoreError(f"{t.name} has no field {key!r}; its stated fields are "
                                 f"{', '.join(x.name for x in t.stated)}")
            if f.cited:
                if raw is None:
                    continue
                if not (isinstance(raw, dict) and set(raw) == {"value", "source_id"}):
                    raise StoreError(f"{t.name}.{key} is a fact about the world: give it as "
                                     f"{{\"value\": ..., \"source_id\": \"src:N\"}}")
                if raw["value"] is None:
                    raise StoreError(f"{t.name}.{key} is given with a source but no value")
                values[key] = _check(t.name, f, raw["value"])
                cites[key] = raw["source_id"]
            else:
                if isinstance(raw, dict) and "source_id" in raw and f.kind not in (schema.MAPPING,) + schema.BASED:
                    raise StoreError(f"{t.name}.{key} is not a fact about the world; it takes no source")
                v = _check(t.name, f, raw)
                if v is not None:
                    values[key] = v
        for f in t.stated:
            if values.get(f.name) is None and f.default is not None:
                values[f.name] = list(f.default) if isinstance(f.default, tuple) else f.default
            if f.required and values.get(f.name) is None:
                raise StoreError(f"{t.name} requires {f.name}")
        self._check_object(t.name, values)
        return values, cites

    @staticmethod
    def _check_object(type_name: str, values: dict) -> None:
        try:
            schema.check_object(type_name, values)
        except schema.Invalid as e:
            raise StoreError(f"{type_name}: {e}") from None

    def _new_object_id(self, t: schema.ObjectType, values: dict) -> str:
        if t.name == "Portfolio":
            if self._object_row("portfolio"):
                raise StoreError("this store has its portfolio, and a store holds one: edit it, or remove it first",
                                 ["portfolio"])
            return "portfolio"
        if t.name == "Metric":
            mid = f"metric:{values['id']}"
            if self._object_row(mid):
                raise StoreError(f"{mid} exists; a metric's id is its own", [mid])
            return mid
        return self._next_id(t.prefix)

    def _check_occurrence(self, values: dict) -> None:
        """A proposition's comparison and its metric agree (ONTOLOGY.md 2.1): one that occurs is true when something happens,
        and the metric it names is that occurrence; a threshold is measured on a level."""
        mid = values.get("metric")
        if mid is None:
            return
        kind = "price" if schema.PROXY_METRIC_RE.match(mid) else (self.stated_of(mid) or {}).get("kind")
        if values.get("comparison") == "occurs" and kind != "occurrence":
            raise StoreError(f"comparison occurs is true when something happens, and the metric it names is that "
                             f"occurrence: {mid} is a {kind}, which a threshold is measured on", [mid])
        if values.get("comparison") != "occurs" and kind == "occurrence":
            raise StoreError(f"{mid} is something that happens: a proposition on it is true when it happens, with "
                             f"comparison occurs, and a threshold is measured on a level", [mid])

    def _check_references(self, t: schema.ObjectType, values: dict) -> None:
        for f in t.stated:
            if f.kind == schema.REF and values.get(f.name) is not None:
                ref = values[f.name]
                if self.type_of(ref) not in f.ref:
                    hint = ""
                    if "Metric" in f.ref and self._object_row(f"metric:{ref}"):
                        hint = f"; the metric is metric:{ref}"
                    raise StoreError(f"{t.name}.{f.name} names {ref}, which is not a {_or(f.ref)} in the store{hint}")

    def _fix_asof(self, asof: str) -> None:
        fixed = self.meta("asof")
        if fixed is not None and fixed != asof:
            raise StoreError(f"this store speaks as of {fixed}; a different as-of is a different store")
        self._set_meta("asof", asof)

    def _check_channel_unique(self, oid: str, values: dict) -> None:
        for key in ("name", "order"):
            r = self._one(f"SELECT id FROM object WHERE type = 'Factor' AND stated ->> '$.{key}' = ? AND id <> ?",
                          values[key], oid)
            if r:
                raise StoreError(f"{r['id']} has {key} {values[key]}; each factor's {key} is its own", [r["id"]])
        r = self._one("SELECT id FROM object WHERE type = 'Factor' AND stated ->> '$.proxy' = ? AND id <> ?",
                      values["proxy"], oid)
        if r:  # TOOLS.md 5: two channels on one proxy are one series, and cannot be told apart
            raise StoreError(f"{values['proxy']} is the proxy of {r['id']}, and one instrument carries one factor",
                             [r["id"]])
        nid = schema.name_id(values["proxy"])  # the price of a factor's proxy is the factor's own (1.4)
        stated = [r["id"] for r in self._all("SELECT id FROM link WHERE relation IN ('consequence', 'worth') AND "
                                             "to_id = ? ORDER BY rowid", nid)]
        if stated:
            raise StoreError(f"{', '.join(stated)} state{'s' if len(stated) == 1 else ''} what {nid}'s price does in a "
                             f"world; as a factor's proxy its price is the factor's own, so what it does is stated on "
                             f"the factor: remove {'it' if len(stated) == 1 else 'them'}, and state it on the factor",
                             stated)

    def _held_name(self, ticker: str, asof: str | None) -> str:
        """The name of the company a held ticker is a security of (harness.derive.identity.name_of); the ticker's own
        where no source here names one, so that what is held is priced, or refused, by its own reads (MATH.md 3.1)."""
        from harness.derive.identity import name_of
        try:
            return name_of(self, ticker, asof or self.meta("asof"))["name"] if asof or self.meta("asof") \
                else schema.name_id(ticker)
        except LookupError:
            return schema.name_id(ticker)

    def _holding(self, to_id: Any, values: dict) -> dict:
        """A holding the analyst links: its security, `ticker` where given and else its name's, must be one of the
        company's whose name it links to, as a holding given with the portfolio is (MATH.md 3.1)."""
        if isinstance(to_id, str) and schema.MARKET_ID_RE.match(to_id):
            if values.get("side") not in ("yes", "no") or "quantity" not in values or "ticker" in values:
                raise StoreError("a contract held names its side, yes or no, and its quantity")
            return values
        if isinstance(to_id, str) and to_id.startswith("cash:"):
            if not schema.CASH_ID_RE.match(to_id):
                raise StoreError(f"{to_id} is not cash in a currency: cash:{{CURRENCY}}, by its three-letter code, as "
                                 f"cash:USD")
            if set(values) != {"cash"}:
                raise StoreError("cash held gives its amount in its currency, {\"cash\": ...}")
            if values["cash"] < 0:
                raise StoreError("cash held is an amount of at least zero")
            return values
        if values.get("cash") is not None:
            raise StoreError("cash is held as cash:{CURRENCY}, its amount as cash; a name is held by a ticker")
        if values.get("side") is not None:
            raise StoreError("side belongs to a market contract; a share holding names its ticker")
        if not isinstance(to_id, str) or not schema.NAME_ID_RE.match(to_id):
            return values
        ticker = values.get("ticker") or to_id.split(":", 1)[1]
        named = self._held_name(ticker, None)
        if named != to_id:
            raise StoreError(f"{ticker} is a security of the company whose name is {named}: hold it as "
                             f"link(\"portfolio\", \"holds\", \"{named}\", {{\"ticker\": \"{ticker}\", ...}})", [named])
        return {**values, "ticker": ticker}

    def _held_tickers(self, nid: str) -> list[str]:
        """The securities the member holds a name by, in the order its holdings were given."""
        return [json.loads(r["stated"]).get("ticker") or nid.split(":", 1)[1] for r in self._all(
            "SELECT stated FROM link WHERE relation = 'holds' AND to_id = ? ORDER BY rowid", nid)]

    def _check_pricing(self, nid: str, held: list[str]) -> None:
        """A change to the holdings that would price a name through another security while a consequence states a shock
        on its price is refused, naming the consequence (MATH.md 3.1): the shock is a fraction of that price, in its
        currency, and would read differently."""
        shocks = [r["id"] for r in self._all(
            "SELECT id FROM link WHERE relation = 'consequence' AND to_id = ? ORDER BY rowid", nid)]
        if not shocks:
            return
        from harness.derive.portfolio import pricing_security
        out = self.engine.value("lookthrough", "portfolio").output or {}
        before = ((out.get("kinds") or {}).get(nid) or {}).get("security")
        if before is None:
            return
        members = set((out.get("securities") or {}).get(nid) or {}) | set(held)
        after = pricing_security(nid.split(":", 1)[1], held, members)
        if after != before:
            raise StoreError(f"{nid} is priced through {before}, and this change would price it through {after}, while "
                             f"{', '.join(shocks)} states a shock on its price: restate or remove "
                             f"{'it' if len(shocks) == 1 else 'them'} first", shocks)

    def _holdings(self, raw: Any, values: dict) -> list[tuple[str, dict]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise StoreError("holdings is a list of {ticker, and one of quantity, dollars, or weight}, cash "
                             "{cash, currency}, or contracts {market, side, quantity}")
        out, seen = [], set()
        holds = schema.RELATIONS["holds"]
        for h in raw:
            if isinstance(h, dict) and "cash" in h:  # cash, an amount in its currency, the dollar's where none is given
                if set(h) - {"cash", "currency"} or not isinstance(h.get("currency", "USD"), str):
                    raise StoreError("cash held is {\"cash\": its amount, \"currency\": its three-letter code}")
                cash = schema.cash_id(h.get("currency", "USD").strip())
                amount = self._holding(cash, self._link_values(holds, {"cash": h["cash"]}))
                if cash in seen:
                    raise StoreError(f"cash in {cash[5:]} is held twice; give its amount once")
                seen.add(cash)
                out.append((cash, amount))
                continue
            if isinstance(h, dict) and "market" in h:
                if set(h) - {"market", "side", "quantity"} or not isinstance(h["market"], str) or not schema.MARKET_ID_RE.match(h["market"]):
                    raise StoreError("a contract holding gives market:venue:id, side and quantity")
                amount = self._link_values(holds, {k: h[k] for k in ("side", "quantity") if k in h})
                self._holding(h["market"], amount)
                key = (h["market"], amount["side"])
                if key in seen:
                    raise StoreError("the same side of a contract is held twice; give its total quantity once")
                seen.add(key)
                out.append((h["market"], amount))
                continue
            if not isinstance(h, dict) or not isinstance(h.get("ticker"), str):
                raise StoreError("each holding is {ticker, and one of quantity, dollars, or weight}")
            extra = set(h) - {"ticker", "quantity", "dollars", "weight"}
            if extra:
                raise StoreError(f"a holding has a ticker and one of quantity, dollars, or weight; not {sorted(extra)}")
            ticker = h["ticker"].strip().upper()
            if not schema.TICKER_RE.match(ticker):
                raise StoreError(f"{h['ticker']!r} is not a ticker")
            if ticker in seen:
                raise StoreError(f"{ticker} is held twice; give each ticker once")
            seen.add(ticker)
            amount = {k: _check("holds", holds.field(k), h[k]) for k in ("quantity", "dollars", "weight")
                      if h.get(k) is not None}
            try:
                schema.check_link("holds", amount)
            except schema.Invalid as e:
                raise StoreError(f"{ticker}: {e}") from None
            out.append((ticker, amount))
        if any("weight" in a for _, a in out) and values.get("total") is None:
            raise StoreError("Portfolio requires total when holdings are given as weights")
        return out

    def _insert_object(self, oid: str, type_name: str, values: dict, origin: str, call: int | None) -> None:
        self.db.execute(
            "INSERT INTO object (id, type, stated, stated_at, origin, created_call, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (oid, type_name, canonical(values), _stamped(None, values, [*values, FOLLOW_STAMP]), origin, call, _now()),
        )

    def _citable(self, sid: Any) -> None:
        if not isinstance(sid, str):
            raise StoreError("a source_id names a fetched page, src:N")
        if self._source(sid) is not None:
            return
        if sid not in self._pending:
            raise StoreError(f"{sid} is not a fetched page; fetch_source(url) fetches one")

    def _keep_page(self, sid: str) -> None:
        if self._source(sid):
            return
        page = self._unhold(sid)
        if page is None:
            raise StoreError(f"{sid} is not a fetched page; fetch_source(url) fetches one")
        self.db.execute(
            "INSERT INTO source (id, url, retrieved_at, dated, text, sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, page["url"], page["retrieved_at"], page["dated"], page["text"], page["sha256"], _now()),
        )

    def _cite(self, oid: str, field: str, sid: str, origin: str, call: int | None) -> str | None:
        """Cite `sid` for oid.field; returns the page it cited before, if another."""
        self._keep_page(sid)
        existing = self._one("SELECT id, to_id FROM link WHERE relation = 'cites' AND from_id = ? AND field = ?",
                             oid, field)
        if existing is None:
            self._insert_link("cites", oid, sid, {}, origin, call, field=field)
            return None
        if existing["to_id"] == sid:
            return None
        self.db.execute("UPDATE link SET to_id = ? WHERE id = ?", (sid, existing["id"]))
        return existing["to_id"]

    # ------------------------------------------------------------------ link and note

    def link(self, from_id: str, relation: str, to_id: str, fields: dict | None = None, *,
             call: int | None = None) -> dict:
        """Create a link by the link table; returns it with its derived fields."""
        with self.transaction():
            lid = self._link(from_id, relation, to_id, fields if fields is not None else {}, origin="analyst",
                             call=call)
        return self.get(lid, exclude_derived=("distribution",))

    def _link(self, from_id: str, relation: str, to_id: str, fields: dict, *, origin: str,
              call: int | None) -> str:
        r = schema.RELATIONS.get(relation)
        if r is None:
            raise StoreError(f"there is no relation {relation!r}; link creates {_or(schema.LINKABLE)}")
        if r.creator not in (schema.ANALYST, schema.NOTE):
            raise StoreError(schema.NOT_LINKED[relation])
        if not isinstance(fields, dict):
            raise StoreError("fields is an object of the link's stated fields")
        values = self._link_values(r, fields)
        if from_id == to_id:
            raise StoreError(f"a link joins two different objects; {from_id} is both ends")
        prefetched = self._prefetch_market(to_id)
        source = self._prefetch_named([{"given": [from_id]}]) if relation == "implies" else {}  # a market at its source
        with self.transaction():
            self._enter_named(source, call)
            ft = self.type_of(from_id) if isinstance(from_id, str) else None
            if ft is None:
                raise StoreError(_absent(from_id))
            if ft not in r.sources:
                raise StoreError(f"{relation} runs from {_or(r.sources)}; {from_id} is a {ft}")
            if relation == "holds":
                values = self._holding(to_id, values)
            tt, entering = self._resolve_target(r, to_id, prefetched)
            if r.targets != ("*",) and tt not in r.targets:
                raise StoreError(f"{relation} runs to {_or(r.targets)}; {to_id} is a {tt}")
            existing = self._one("SELECT id FROM link WHERE relation = ? AND from_id = ? AND to_id = ?"
                                 + (" AND stated ->> '$.side' = ?" if relation == "holds" and tt == "Market" else
                                    " AND coalesce(stated ->> '$.ticker', substr(to_id, 6)) = ?" if relation == "holds"
                                    and tt != "Cash"
                                    else " AND coalesce(stated ->> '$.world', '') = ?" if relation == "worth" else ""),
                                 relation, from_id, to_id, *([values["side"]] if relation == "holds" and tt == "Market" else
                                                          [values["ticker"]] if relation == "holds" and tt != "Cash" else
                                                          [values.get("world", "")] if relation == "worth" else []))
            if existing:
                raise StoreError(f"{existing['id']} links {from_id} {relation} {to_id}; edit it instead",
                                 [existing["id"]])
            if relation == "holds" and "weight" in values:
                self._require_total()
            if relation == "holds" and tt == "Name":
                self._check_pricing(to_id, self._held_tickers(to_id) + [values["ticker"]])
            if relation == "then":
                self._check_then_step(from_id, values["step"])
            if relation == "evidence":
                self._check_passages(to_id, values["passages"])
            if relation == "way":
                self._check_way(from_id, to_id, values)
            if relation in ("consequence", "worth") and tt == "Name":
                self._check_company_id(to_id)
                self._check_not_proxy(to_id, relation)
            if relation == "worth":
                self._check_worth(from_id, to_id, values)
            if relation == "watches" and self._way_parent(to_id):
                parent = self._way_parent(to_id)
                raise StoreError(f"{to_id} is a way of {parent['from_id']} ({parent['id']}), read in that event's row "
                                 f"against that event's baseline", [parent["id"]])
            if entering == "market":
                self._enter_market(to_id, prefetched, call)
            elif entering == "page":
                self._keep_page(to_id)
            values = self._noted(None, relation, values, {}, call)
            lid = self._insert_link(relation, from_id, to_id, values, origin, call)
            if self._based_fields(relation):
                self._cite_basis(lid, relation, values, call)
                self._tie_notes(lid, relation, values, {}, call)
            if relation == "consequence":
                self._check_shocked(from_id)
            if relation == "watches":
                self._check_horizon()
            if relation == "consequence":
                self._check_shocks(to_id)
                if tt == "Name":
                    self._shocked_names.append(to_id)
            if relation == "worth":
                self._shocked_names.append(to_id)
                shares = values["value"]["value"].get("shares")
                if shares:
                    self._shocked_names.append(shares["name"])
            if relation in ("consequence", "worth", "way") and (tt == "Name" or relation == "way"):
                self._check_valued_once(from_id)
            if relation == "quotes":
                self._check_quotes(from_id, to_id)
        return lid

    def _link_values(self, r: schema.Relation, fields: dict) -> dict:
        values: dict[str, Any] = {}
        for key, raw in fields.items():
            f = r.field(key)
            if f is None:
                if key in r.derived:
                    raise StoreError(f"{r.name}.{key} is derived; the store computes it")
                have = f"the stated fields {', '.join(x.name for x in r.stated)}" if r.stated else "no stated fields"
                raise StoreError(f"{r.name} has {have}; {key!r} is not one")
            if isinstance(raw, dict) and "source_id" in raw and f.kind not in schema.BASED:
                raise StoreError(f"{r.name}.{key} is not a fact about the world; it takes no source")
            v = _check(r.name, f, raw)
            if v is not None:
                values[key] = v
        for f in r.stated:
            if values.get(f.name) is None and f.default is not None:
                values[f.name] = _check(r.name, f, f.default)
            if f.required and values.get(f.name) is None:
                detail = (": 1 for the part that comes first, 2 for the part that follows"
                          if (r.name, f.name) == ("then", "step") else "")
                raise StoreError(f"{r.name} requires {f.name}{detail}")
        try:
            schema.check_link(r.name, values)
        except schema.Invalid as e:
            raise StoreError(f"{r.name}: {e}") from None
        return values

    def _prefetch_named(self, chances: Any) -> dict[str, dict]:
        """The markets a proposition's chances name that the store does not keep, each brought from its venue before
        the write begins (_prefetch_market). What is not a list of chances is left to the field's own check."""
        named: dict[str, dict] = {}
        for c in chances if isinstance(chances, list) else []:
            for x in ((c.get("given") or []) + (c.get("given_not") or []) if isinstance(c, dict) else []):
                if isinstance(x, str) and schema.MARKET_ID_RE.match(x) and x not in named:
                    got = self._prefetch_market(x)
                    if got is not None:
                        named[x] = got
        return named

    def _enter_named(self, named: dict[str, dict], call: int | None) -> None:
        """Each market a statement names, kept from here (MATH.md 3.3): it is read again every minute while it is named."""
        for mid, got in named.items():
            if not self._market_kept(mid):
                self._enter_market(mid, got, call)

    def _prefetch_market(self, oid: Any) -> dict | None:
        """A market a link or note names that the store does not keep, brought from its
        venue before the write begins."""
        m = schema.MARKET_ID_RE.match(oid) if isinstance(oid, str) else None
        if not m or self._market_kept(oid):
            return None
        venue, vid = m.group(1), m.group(2)
        if venue not in schema.VENUES:
            raise StoreError(f"{oid}: the venues are {', '.join(schema.VENUES)}")
        if self._find_data("market", oid, None) is not None:
            return {"held": True}  # entered before: what was fetched then is what it reads
        shown = VENUE_NAMES[venue]
        if self.venues is None:
            raise StoreError(f"{oid} is not in the store, and its venue cannot be reached here")
        try:
            got = self.venues.market(venue, vid)
        except LookupError:
            raise StoreError(f"{shown} lists no market {vid}") from None
        except Exception:
            raise StoreError(f"{shown} did not answer for {vid} just now; try again shortly") from None
        return got

    def _enter_market(self, mid: str, got: dict, call: int | None) -> None:
        venue, vid = schema.MARKET_ID_RE.match(mid).groups()
        self.db.execute("INSERT INTO market (id, venue, venue_id, created_call, created_at) VALUES (?, ?, ?, ?, ?)",
                        (mid, venue, vid, call, _now()))
        if got.get("held"):
            return
        request = got.get("request", {})
        self._refuse_credentials(request)
        self._refuse_credentials(got.get("payload"), names=False)
        retrieved = _check("market", schema.Field("retrieved_at", schema.DATETIME, required=True),
                           got.get("retrieved_at"))
        self._insert_data("market", mid, canonical(got.get("payload")), canonical(request), retrieved, None, None, mid)

    def _resolve_target(self, r: schema.Relation, to_id: Any, prefetched: dict | None) -> tuple[str, str | None]:
        """The type of a link's target, and what bringing it in takes: nothing, entering a
        market, or keeping a page."""
        if not isinstance(to_id, str):
            raise StoreError("a link's target is an id")
        if schema.NAME_ID_RE.match(to_id):
            if r.name in ("holds", "consequence", "worth"):  # a holding brings its name in, and so does a shock an event states
                return "Name", None
            if self._name_exists(to_id):
                return "Name", None
            raise StoreError(f"{to_id} is not in the store: a Name is one the portfolio holds or reaches through a "
                             f"fund, or one an event's consequence names")
        if schema.CASH_ID_RE.match(to_id) and r.name == "holds":  # cash held in a currency (ONTOLOGY.md 2.2)
            return "Cash", None
        if schema.MARKET_ID_RE.match(to_id) and not self._market_kept(to_id):
            if prefetched is None:
                raise StoreError(f"{to_id} is not in the store")
            return "Market", "market"
        if to_id.startswith("src:"):
            if self._source(to_id) is not None:
                return "Source", None
            if to_id in self._pending:
                if r.name == "evidence":
                    return "Source", "page"
                raise StoreError(f"{to_id} is a fetched page that is not kept; a page is kept once it is cited "
                                 f"for a stated fact or linked as evidence")
        t = self.type_of(to_id)
        if t is None:
            raise StoreError(_absent(to_id))
        return t, None

    def _insert_link(self, relation: str, from_id: str, to_id: str, values: dict, origin: str,
                     call: int | None, field: str | None = None) -> str:
        lid = self._next_id("link")
        self.db.execute(
            "INSERT INTO link (id, relation, from_id, to_id, field, stated, stated_at, origin, created_call, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, relation, from_id, to_id, field, canonical(values), _stamped(None, values, values), origin, call,
             _now()),
        )
        return lid

    def note(self, text: str, about_ids: Iterable[str] = (), *, call: int | None = None) -> dict:
        """Create a Note about the given objects and links."""
        text = _check("Note", schema.OBJECTS["Note"].field("text"), text)
        if isinstance(about_ids, str) or not all(isinstance(x, str) for x in about_ids):
            raise StoreError("about_ids is a list of ids")
        about = list(dict.fromkeys(about_ids))
        prefetched = {x: self._prefetch_market(x) for x in about}
        r = schema.RELATIONS["about"]
        with self.transaction():
            nid = self._next_id("note")
            self._insert_object(nid, "Note", {"text": text}, "analyst", call)
            for x in about:
                _, entering = self._resolve_target(r, x, prefetched.get(x))
                if entering == "market":
                    self._enter_market(x, prefetched[x], call)
                self._insert_link("about", nid, x, {}, "analyst", call)
        return self.get(nid, exclude_derived=("distribution",))

    # ------------------------------------------------------------------ edit

    def edit(self, id: str, field: str, value: Any, source_id: str | None = None, *,
             call: int | None = None, derived: bool = True) -> dict:
        """Change one stated field; a fact about the world takes a source_id. Returned as `get` returns it, or, not
        `derived`, as its id, type, and stated fields alone, as `add` returns it."""
        named = self._prefetch_named(value) if field == "chances" and self.type_of(id) == "Proposition" else {}
        with self.transaction():
            self._enter_named(named, call)
            kind = self._kind(id)
            if kind == "object" and field == schema.DEFINITION_FIELD:
                self._edit_definition(id, value, source_id)
            elif kind == "object":
                self._edit_object(id, field, value, source_id, call)
            elif kind == "link":
                self._edit_link(id, field, value, source_id, call)
            elif kind in ("source", "page"):
                self._edit_dated(id, field, value, source_id)
            else:
                raise StoreError(self._not_editable(id, kind))
        return self.get(id, exclude_derived=("distribution",)) if derived else self.describe(id)

    def _not_editable(self, oid: str, kind: str | None) -> str:
        return {
            None: _absent(oid),
            "name": f"{oid} is a Name; it follows from holdings and look-through: edit the holds link instead",
            "proxy_metric": f"{oid} is a price the store derives: a factor's proxy, or a name of the portfolio",
            "derived_link": f"{oid} is derived; change what it follows from",
            "market": f"{oid}'s fields come from its venue",
            "computation": f"{oid} is how a value was worked out; it follows from structure",
            "judgment": f"{oid} is the judge's answer to a check; it follows from the claim it checks",
            "search": f"{oid} is a record of a search",
            "data": f"{oid} is fetched data",
        }[kind]

    def _edit_dated(self, sid: str, field: str, value: Any, source_id: Any) -> None:
        """A page is as it was fetched, but for the date it speaks as of: the page states it, and
        the analyst writes it, citing the page itself."""
        row = self.source_row(sid)
        if field != "dated":
            raise StoreError(f"{sid} is a fetched page: its url, text, and retrieval are as fetched; the field the "
                             f"analyst states is dated, the date the page speaks as of")
        if source_id is None:
            raise StoreError(f"Source.dated is the date the page speaks as of, as the page states it; it cites the "
                             f"page itself: edit({sid}, 'dated', value, {sid})")
        if source_id != sid:
            raise StoreError(f"Source.dated cites the page itself, {sid}, not {source_id}")
        dated = _check("Source", schema.OBJECTS["Source"].field("dated"), value)
        if row["kept"]:
            self.db.execute("UPDATE source SET dated = ? WHERE id = ?", (dated, sid))
        else:
            self._hold(sid, {**self._pending[sid], "dated": dated})

    def _edit_definition(self, oid: str, value: Any, source_id: Any) -> None:
        """A proposition's definition written whole (ONTOLOGY.md 2.1, TOOLS.md 5): what would make it true, its title, its text and
        the fields code compares, given together as add gives them, so that a claim becomes another kind of claim in
        one statement, as an occurrence becomes a threshold on a level. Each field is checked as add checks it, and
        the whole as one; a field it leaves out is cleared. Its chances, baseline, priced_in and episode, its links,
        notes and searches stay as they are, and what reads its fields reads them anew."""
        row = self._object_row(oid)
        if row["type"] != "Proposition":
            raise StoreError(f"{oid} is a {row['type']}; a definition is a proposition's: edit {oid}'s fields "
                             f"one by one")
        if source_id is not None:
            raise StoreError("a proposition's definition is not a fact about the world; it takes no source")
        if not isinstance(value, dict):
            raise StoreError(f"a proposition's definition is an object of its fields: {', '.join(schema.DEFINITION)}")
        other = [k for k in value if k not in schema.DEFINITION]
        if other:
            raise StoreError(f"{_or(other)} {'is' if len(other) == 1 else 'are'} not part of a proposition's "
                             f"definition, which is {', '.join(schema.DEFINITION)}: edit "
                             f"{'it' if len(other) == 1 else 'each'} on its own")
        t = schema.OBJECTS["Proposition"]
        given, _ = self._stated_values(t, value)
        stated = json.loads(row["stated"])
        new = {k: v for k, v in stated.items() if k not in schema.DEFINITION}
        new.update({k: v for k, v in given.items() if k in schema.DEFINITION})
        self._check_object("Proposition", new)
        self._check_references(t, new)
        self._check_occurrence(new)
        self._check_chances(oid, new)
        written = [k for k in schema.DEFINITION if stated.get(k) != new.get(k)]
        self.db.execute("UPDATE object SET stated = ?, stated_at = ? WHERE id = ?",
                        (canonical(new), _stamped(row["stated_at"], new, written), oid))
        self._check_horizon()

    def _edit_object(self, oid: str, field: str, value: Any, source_id: Any, call: int | None) -> None:
        row = self._object_row(oid)
        t = schema.OBJECTS[row["type"]]
        f = t.field(field) if isinstance(field, str) else None
        if f is None:
            if field in t.derived:
                raise StoreError(f"{t.name}.{field} is derived; the store computes it")
            if field in t.add_params:
                raise StoreError("holdings are the holds links: edit, link, or remove those")
            raise StoreError(f"{t.name} has no field {field!r}; its stated fields are "
                             f"{', '.join(x.name for x in t.stated)}")
        if not f.editable:
            raise StoreError(f"{t.name}.{field} is fixed: "
                             + ("a different as-of is a different store" if field == "asof" and t.name == "Portfolio"
                                else "it is the object's identity; add a new one and move its references"))
        stated = json.loads(row["stated"])
        new = dict(stated)
        old_source = None
        if f.cited:
            if value is None:
                if source_id is not None:
                    raise StoreError(f"{t.name}.{field} is cleared without a source")
                if f.required:
                    raise StoreError(f"{t.name} requires {field}")
                new.pop(field, None)
            else:
                if source_id is None:
                    raise StoreError(f"{t.name}.{field} is a fact about the world; it takes a source_id")
                self._citable(source_id)
                new[field] = _check(t.name, f, value)
        else:
            if source_id is not None and f.kind in schema.BASED:
                raise StoreError(f"{t.name}.{field} is a stated assumption, and its basis is stated in its value: "
                                 f"{schema.BASIS_SHAPE}")
            if source_id is not None:
                raise StoreError(f"{t.name}.{field} is not a fact about the world; it takes no source")
            v = _check(t.name, f, value)
            if v is None and f.default is not None:
                v = list(f.default) if isinstance(f.default, tuple) else f.default
            if v is None:
                new.pop(field, None)
            else:
                new[field] = v
        if t.name == "Factor" and field in ("yield", "duration"):
            self._check_quoting(oid, field, stated, new)
        try:
            self._check_object(t.name, new)
            if f.kind == schema.REF:
                self._check_references(t, new)
            if t.name == "Proposition" and field in ("metric", "comparison"):
                self._check_occurrence(new)
        except StoreError as e:
            if t.name != "Proposition" or field not in schema.DEFINITION:
                raise
            raise StoreError(f"{e.message}; its definition's fields change together with edit({oid}, "
                             f"\"{schema.DEFINITION_FIELD}\", {{...}})", e.dependents) from None
        if t.name == "Proposition" and field in ("chances", "episode"):
            self._check_chances(oid, new)
        if t.name == "Proposition" and field == "baseline":
            self._check_baseline(oid, new.get("baseline"))
        if t.name == "Proposition" and field == "priced_in":
            self._check_priced_in(oid, new)
        if t.name == "Proposition" and field == "chances":
            self._check_follow(oid, new)
        if t.name == "Factor" and field in ("name", "order", "proxy"):
            self._check_channel_unique(oid, new)
        if t.name == "Portfolio" and field == "total" and new.get("total") is None:
            self._refuse_weights_without_total()
        if f.kind in schema.BASED:
            new = self._noted(oid, t.name, new, stated, call)
        self.db.execute("UPDATE object SET stated = ?, stated_at = ? WHERE id = ?",
                        (canonical(new), _stamped(row["stated_at"], new, _written(stated, new, field)), oid))
        if f.cited:
            if value is None:
                old = self._one("SELECT id, to_id FROM link WHERE relation = 'cites' AND from_id = ? "
                                "AND field = ?", oid, field)
                if old:
                    self.db.execute("DELETE FROM link WHERE id = ?", (old["id"],))
                    old_source = old["to_id"]
            else:
                old_source = self._cite(oid, field, source_id, "analyst", call)
            if old_source:
                self._release({old_source})
        if f.kind in schema.BASED:
            self._cite_basis(oid, t.name, new, call)
            self._tie_notes(oid, t.name, new, stated, call)
        if (t.name, field) in (("Proposition", "window_end"), ("Portfolio", "horizon_days")):
            self._check_horizon()
        if t.name == "Factor" and field == "proxy":
            self._estimable.append(oid)
        if t.name == "Proposition" and field == "chances":  # a market no chance names any longer is not kept
            self._release(_chance_markets(stated) - _chance_markets(new))

    def _check_estimable(self, cid: str | None) -> None:
        """A channel, or the portfolio, is written only where the channels can still be estimated (TOOLS.md 5): the
        factors' own computation, read at the write's commit, after the reads the call's result needs, so the first
        read's stages come in their order, refuses a proxy that would leave the common history too short or the
        channels' covariance singular, or that is another channel's rate quoted the other way round, with its reason;
        nothing is written. A channel's write names the other channels concerned; the portfolio's, whose as-of sets
        the window, every one, since a channel added before it was written with no window to read."""
        if "factors" not in self.engine.registry.versions() or self._kind("portfolio") is None:
            return
        found = self.engine.value("factors", "market")
        if found.refused is None:
            return
        if cid is None:
            raise StoreError(f"at the as-of {self.stated_of('portfolio')['asof']}, {found.cause}",
                             list(found.refused))
        proxy = self.stated_of(cid)["proxy"]
        raise StoreError(f"{proxy} as the proxy of {cid}: {found.cause}", [n for n in found.refused if n != cid])

    def _edit_link(self, lid: str, field: str, value: Any, source_id: Any, call: int | None) -> None:
        row = self._link_row(lid)
        r = schema.RELATIONS[row["relation"]]
        if r.name == "cites":
            raise StoreError(f"{lid} cites the page for {row['from_id']}.{row['field']}: edit that field with "
                             f"another source")
        f = r.field(field) if isinstance(field, str) else None
        if f is None:
            if field in r.derived:
                raise StoreError(f"{r.name}.{field} is derived; the store computes it")
            have = f"the stated fields {', '.join(x.name for x in r.stated)}" if r.stated else "no stated fields"
            raise StoreError(f"{r.name} has {have}; {field!r} is not one")
        if source_id is not None:
            raise StoreError(f"{r.name}.{field} is a stated assumption, and its basis is stated in its value: "
                             f"{schema.BASIS_SHAPE}" if f.kind in schema.BASED else
                             f"{r.name}.{field} is not a fact about the world; it takes no source")
        v = _check(r.name, f, value)
        if r.name == "holds":
            if v is None:
                raise StoreError("a holding gives exactly one of quantity, dollars, or weight")
            before = json.loads(row["stated"])
            new = {k: before[k] for k in ("ticker", "side") if k in before}
            if field == "side":
                new.update(quantity=before["quantity"], side=v)
            else:
                new[field] = v
            new = self._holding(row["to_id"], new)
            if field == "weight":
                self._require_total()
        else:
            new = dict(json.loads(row["stated"]))
            if v is None:
                if f.required:
                    raise StoreError(f"{r.name} requires {field}")
                new.pop(field, None)
                if f.default is not None:
                    new[field] = _check(r.name, f, f.default)
            else:
                new[field] = v
        try:
            schema.check_link(r.name, new)
        except schema.Invalid as e:
            raise StoreError(f"{r.name}: {e}") from None
        if r.name == "then":
            self._check_then_step(row["from_id"], new["step"], editing=lid)
        if r.name == "evidence":
            self._check_passages(row["to_id"], new["passages"])
        if r.name == "way":
            self._check_way_side(row["from_id"], row["to_id"], new)
        if r.name == "worth":
            self._check_worth(row["from_id"], row["to_id"], new, editing=lid)
        old = json.loads(row["stated"])
        if f.kind in schema.BASED:
            new = self._noted(lid, r.name, new, old, call)
        at = _stamped(row["stated_at"], new, [field])
        if r.name == "holds":  # a holding's figure edited is the holding given again, at this moment
            self.db.execute("UPDATE link SET stated = ?, stated_at = ?, created_at = ? WHERE id = ?",
                            (canonical(new), at, _now(), lid))
        else:
            self.db.execute("UPDATE link SET stated = ?, stated_at = ? WHERE id = ?", (canonical(new), at, lid))
        if r.name == "consequence":
            self._check_shocks(row["to_id"])
        if f.kind in schema.BASED:
            self._cite_basis(lid, r.name, new, call)
            self._tie_notes(lid, r.name, new, old, call)

    # ------------------------------------------------------------------ rules across objects

    def read_links(self, oid: str) -> list[dict]:
        """A proposition's links of the breakdown (schema.BREAKDOWN), both directions: what the breakdown's logic
        reads, and nothing a note or a page's evidence adds."""
        return self.links_of(oid, schema.BREAKDOWN, "both")

    def _check_chances(self, pid: str, values: dict) -> None:
        """Each chance is given propositions in the store, other than the one it is the chance of (MATH.md 1.2)."""
        ep = (values.get("episode") or {}).get("value")
        if ep and self.meta("asof") and ep["end"] > self.meta("asof"):
            raise StoreError("an episode ends on or before the portfolio's as-of")
        if ep and "episode_covariance" in self.engine.registry.versions() and self._kind("portfolio") is not None:
            from harness.derive.episode import subject
            found = self.engine.value("episode_covariance", subject(ep["start"], ep["end"]))
            if found.refused is not None:  # no co-movement is observed over the window: the statement fails with why
                raise StoreError(found.cause, [pid])
        for i, c in enumerate(values.get("chances") or []):
            for x in (*c["given"], *c["given_not"]):
                if x == pid:
                    raise StoreError(f"{pid}.chances[{i}] is given {pid} itself: a chance of a proposition is given "
                                     f"others")
                if schema.MARKET_ID_RE.match(x):  # a market stands as the proposition its question and rules define
                    asked = self._one("SELECT id FROM link WHERE relation = 'asks' AND from_id = ? AND to_id = ?", pid, x)
                    if asked is not None:
                        raise StoreError(f"{pid}.chances[{i}] is given {x}, which asks {pid} itself ({asked['id']}): "
                                         f"a chance of a proposition is given others", [asked["id"]])
                    if self.type_of(x) != "Market":
                        raise StoreError(f"{pid}.chances[{i}] is given {x}, which its venue does not list")
                elif self.type_of(x) != "Proposition":
                    raise StoreError(f"{pid}.chances[{i}] is given {x}, which is not a proposition in the store")
            if "reading" in c:
                self._check_reading(pid, f"{pid}.chances[{i}]", c, values)

    def _check_worth(self, pid: str, nid: str, values: dict, *, editing: str | None = None) -> None:
        """A value is on one outcome; its terms carry no inference about which outcome it describes."""
        way = self._way_parent(pid) or self._joint_baselines(pid) or any(pid in self._ways_below(r["to_id"]) for r in self._all(
            "SELECT to_id FROM link WHERE relation='watches' AND from_id='portfolio'"))
        if not way and values.get("world") not in ("yes", "no"):
            raise StoreError("worth on an event names its world, yes or no; on a way the way is the world")
        if way and values.get("world") is not None:
            raise StoreError("worth on a way takes that way as its world, with no world field")
        duplicate = self._one("SELECT id FROM link WHERE relation='worth' AND from_id=? AND to_id=? "
                              "AND coalesce(stated ->> '$.world', '')=? AND id<>?", pid, nid,
                              values.get("world", ""), editing or "")
        if duplicate:
            raise StoreError(f"{duplicate['id']} already gives this holding's value in that world; edit it instead",
                             [duplicate["id"]])
        value = values["value"]["value"]
        asof = self.meta("asof")
        if "return" in value and asof and value["date"] >= asof:
            raise StoreError("a value stated as the holding's price on an earlier date takes a date before the as-of")
        if "cash" in value and asof and value["date"] < asof:
            raise StoreError("cash a world pays is paid on or after the as-of")
        if value.get("shares"):
            other = value["shares"]["name"]
            if other == nid:
                raise StoreError(f"the shares a value pays are another name's, and {nid} is the holding itself")
            self._check_company_id(other)

    def _check_reading(self, pid: str, key: str, c: dict, values: dict) -> None:
        """A chance that names a reading of a ladder (MATH.md 1.2) is the chance of a deadline given a later rung, and
        given not an earlier one where it lies between two, or the first rung alone for a date before it. The reading
        states that the proposition and its rungs concern the same occurrence or threshold, counted from the same
        start, by their dates, and the store places them by those dates (structure._rungs). Where a pair it places
        side by side is on one metric and resolution source, the fields decide: the two are on one line, the same
        threshold reached from the same start, or the reading is refused with what differs. Each proposition is a
        deadline, reached at any time by its date; a market a rung, at the day its question counts through. Each
        refusal says the fact of the statement. A calendar names a date on which it can happen there."""
        from datetime import date as day
        from harness.derive import checks
        from harness.derive.engine import Inputs
        if len(c["given"]) != 1 or len(c["given_not"]) > 1:
            raise StoreError(f"{key} is a reading given {', '.join(c['given']) or 'nothing'} and given not "
                             f"{', '.join(c['given_not']) or 'nothing'}: a reading is given its later rung, and given "
                             f"not its earlier rung or nothing")
        ctx, asof = Inputs(self.engine), self.meta("asof")
        stated = {**(self.stated_of(pid) or {}), **values}
        later, earlier = c["given"][0], (c["given_not"] or [None])[0]
        through = c.get("through") or {}  # a market standing as a rung, at the day its question counts through
        rungs = [x for x in (earlier, later) if x]

        def fields(x: str) -> dict:
            return stated if x == pid else (self.stated_of(x) or {})

        for x in (pid, *(r for r in rungs if r not in through)):  # each proposition a deadline
            f = fields(x)
            if f.get("kind") != "hitting":
                what = "a decision on" if f.get("kind") == "scheduled_decision" else "a level on"
                raise StoreError(f"{key}: {x} is {what} {f.get('window_end')}, not reached at any time by it (`kind` "
                                 f"`{f.get('kind')}`): a reading's rungs, and the date between them, are deadlines",
                                 rungs)

        def reading(x: str) -> dict | None:
            return checks.stated_reading(ctx, fields(x), asof) if x == pid else checks.reading(ctx, x, asof)

        for a, b in ((earlier, pid), (pid, later)):  # each pair, the earlier first
            if a is None or a in through or b in through:
                continue
            ra, rb = reading(a), reading(b)
            if checks.comparable(ra, rb):  # one metric and resolution source: the fields decide
                la, lb = checks.line_of(ra), checks.line_of(rb)
                if la is not None and lb is not None and la[0] == lb[0]:
                    continue
                raise StoreError(f"{key}: {self._apart_on(a, ra, b, rb)}", rungs)
        end = lambda x: (day.fromisoformat(through[x]).toordinal() if x in through  # noqa: E731
                         else day.fromisoformat(fields(x)["window_end"]).toordinal())  # each deadline's date
        t = end(pid)
        start = stated.get("window_start") or asof
        t2 = end(later)
        t1 = end(earlier) if earlier else day.fromisoformat(start).toordinal()
        if not t1 < t < t2:
            on = lambda n: day.fromordinal(n).isoformat()  # noqa: E731
            given = f"the rung it is given, by {on(t2)}, comes {'after' if t2 > t else 'before'} it"
            given_not = (f", and the one it is given not, by {on(t1)}, {'before' if t1 < t else 'after'} it" if earlier
                         else f", and its ladder starts on {on(t1)}")
            raise StoreError(f"{key}: {pid} is by {on(t)}: {given}{given_not}", rungs)
        r = c["reading"]
        if isinstance(r, dict) and not any(t1 < day.fromisoformat(d["date"]).toordinal() <= t2 for d in r["calendar"]):
            raise StoreError(f"{key}'s calendar names no date after {day.fromordinal(t1).isoformat()} through "
                             f"{day.fromordinal(t2).isoformat()}, the span its share is read over")

    @staticmethod
    def _apart_on(a: str, ra: dict, b: str, rb: dict) -> str:
        """Two deadlines on one metric and resolution source that the fields put on two lines, by what differs: what
        each is reached at, or where each counts from (checks.line_of)."""
        if (ra["relation"], ra["value"]) != (rb["relation"], rb["value"]):
            at = lambda r: f"{r['relation']} {r['value']:g}" if r["value"] is not None else "its occurrence"  # noqa: E731
            return f"{a} is reached at {at(ra)} and {b} at {at(rb)}"
        return f"{a} counts from {ra['start']} and {b} from {rb['start']}"

    @staticmethod
    def _basis_cites(kind: str, values: dict) -> dict[str, dict]:
        """Each stated assumption cited from a page, by the field it states: each of a proposition's chances, the weight
        it states, a consequence's shock, a market not taken."""
        return {k: v for k, v in schema.assumptions(kind, values).items() if v.get("source_id")}

    @staticmethod
    def _based_fields(kind: str) -> set[str]:
        t = schema.OBJECTS.get(kind) or schema.RELATIONS.get(kind)
        return {f.name for f in (t.stated if t else ()) if f.kind in schema.BASED}

    def _cite_basis(self, owner: str, kind: str, values: dict, call: int | None) -> None:
        """Each assumption cited from a page cites it, its passages in the page word for word; a page whose last
        citation goes is released."""
        cites = self._basis_cites(kind, values)
        for c in cites.values():
            self._citable(c["source_id"])
            self._check_passages(c["source_id"], c["passages"])
        fields = self._based_fields(kind)
        dropped = set()
        for r in self._all("SELECT id, field, to_id FROM link WHERE relation = 'cites' AND from_id = ?", owner):
            if schema.field_of(r["field"]) in fields and r["field"] not in cites:
                self.db.execute("DELETE FROM link WHERE id = ?", (r["id"],))
                dropped.add(r["to_id"])
        for key, c in cites.items():
            old = self._cite(owner, key, c["source_id"], "analyst", call)
            if old:
                dropped.add(old)
        self._release(dropped - set(self.citations_of(owner).values()))

    @staticmethod
    def _named_notes(kind: str, values: dict) -> set[str]:
        """The Notes an object's or a link's stated assumptions rest on."""
        return {v["note"] for v in schema.assumptions(kind, values).values() if v.get("note")}

    def _noted(self, owner: str | None, kind: str, values: dict, old: dict, call: int | None) -> dict:
        """The stated values with each assumption's reasoning written as a Note and named by its id (ONTOLOGY.md 2;
        TOOLS.md 5). A note given as an id is the one that field already rests on, given again to keep it; any other
        note is the reasoning in words, a new Note. Before the statement is written, so a refusal leaves neither."""
        if not self._based_fields(kind):
            return values
        values = json.loads(canonical(values))
        kept: dict[str, set[str]] = {}
        for key, v in schema.assumptions(kind, old).items():
            if v.get("note"):
                kept.setdefault(schema.field_of(key), set()).add(v["note"])
        for key, v in schema.assumptions(kind, values).items():
            note = v.get("note")
            if note is None:
                continue
            if schema.NOTE_ID_RE.match(note.strip()):
                if note.strip() not in kept.get(schema.field_of(key), set()):
                    raise StoreError(f"{owner or kind}.{key}.note is your reasoning in words, or the id of a note "
                                     f"{schema.field_of(key)} already rests on, to keep it; {note.strip()} is not "
                                     f"one")
                v["note"] = note.strip()
                continue
            text = _check("Note", schema.OBJECTS["Note"].field("text"), note)
            nid = self._next_id("note")
            self._insert_object(nid, "Note", {"text": text}, "analyst", call)
            v["note"] = nid
        return values

    def _tie_notes(self, owner: str, kind: str, values: dict, old: dict, call: int | None) -> None:
        """Each Note a statement rests on is a note about what was stated; a Note written as the basis of a statement
        that no longer names it goes with that statement."""
        named = self._named_notes(kind, values)
        for nid in sorted(named):
            if not self._one("SELECT 1 FROM link WHERE relation = 'about' AND from_id = ? AND to_id = ?", nid, owner):
                self._insert_link("about", nid, owner, {}, "analyst", call)
        self._drop_notes(self._named_notes(kind, old) - named)

    def _drop_notes(self, nids: Iterable[str]) -> list[str]:
        """Notes a statement rested on, gone with it, with their links; those still in the store, in id order."""
        gone = []
        for nid in sorted(set(nids), key=lambda n: int(n.split(":")[1])):
            if self._object_row(nid) is None or self._resting_on(nid):
                continue
            self._delete_links([r["id"] for r in self._all("SELECT id FROM link WHERE from_id = ? OR to_id = ? "
                                                             "ORDER BY rowid", nid, nid)])
            self.db.execute("DELETE FROM object WHERE id = ?", (nid,))
            gone.append(nid)
        return gone

    def _resting_on(self, nid: str) -> list[str]:
        """Each stated assumption whose basis is this note, as owner.field."""
        quoted = json.dumps(nid)
        out = []
        for r in self._all("SELECT id, type AS kind, stated FROM object WHERE instr(stated, ?) ORDER BY rowid", quoted):
            out += [f"{r['id']}.{k}" for k, v in schema.assumptions(r["kind"], json.loads(r["stated"])).items()
                    if v.get("note") == nid]
        for r in self._all("SELECT id, relation AS kind, stated FROM link WHERE instr(stated, ?) ORDER BY rowid",
                           quoted):
            out += [f"{r['id']}.{k}" for k, v in schema.assumptions(r["kind"], json.loads(r["stated"])).items()
                    if v.get("note") == nid]
        return out

    def _check_passages(self, sid: str, passages: list[str]) -> None:
        """Each passage is in its page word for word: the same words in the same order, however the
        page's text breaks its lines or marks up its links and emphasis."""
        text = words(self.source_row(sid)["text"])
        for passage in passages:
            if words(passage) not in text:
                raise StoreError(f"this passage is not in {sid} word for word: {passage!r}; quote the page's own "
                                 f"words, which get({sid}, text) finds and get({sid}) returns whole")

    def _check_then_step(self, composite: str, step: int, *, editing: str | None = None) -> None:
        """A composite A = B then C has one then link of each step."""
        found = self._one("SELECT id FROM link WHERE relation = 'then' AND from_id = ? AND stated ->> '$.step' = ? "
                          "AND id IS NOT ?", composite, step, editing)
        if found:
            part = "B, the part that comes first" if step == 1 else "C, the part that follows it"
            raise StoreError(f"{composite} has its step-{step} then link, {found['id']}, naming {part}; a composite "
                             f"has one link of each step: edit or remove {found['id']} first", [found["id"]])

    def _require_total(self) -> None:
        """A holding given as a weight is a weight of the Portfolio's total."""
        row = self._object_row("portfolio")
        if row is None or json.loads(row["stated"]).get("total") is None:
            raise StoreError("Portfolio requires total when holdings are given as weights")

    def _refuse_weights_without_total(self) -> None:
        if self._one("SELECT 1 FROM link WHERE relation = 'holds' AND stated ->> '$.weight' IS NOT NULL"):
            raise StoreError("Portfolio requires total when holdings are given as weights")

    def horizon_end(self) -> str | None:
        """The end of the one period the ledger looks over: asof plus horizon_days."""
        row = self._object_row("portfolio")
        if row is None:
            return None
        p = json.loads(row["stated"])
        return (date.fromisoformat(p["asof"]) + timedelta(days=p["horizon_days"])).isoformat()

    def _check_horizon(self) -> None:
        """A watched proposition's window ends within asof + horizon_days."""
        limit = self.horizon_end()
        if limit is None:
            return
        p = self.stated_of("portfolio")
        for r in self._all("SELECT to_id FROM link WHERE relation = 'watches' AND from_id = 'portfolio' "
                           "ORDER BY rowid"):
            end = self.stated_of(r["to_id"])["window_end"]
            if end > limit:
                raise StoreError(f"{r['to_id']}'s window ends {end}, after the horizon's end, {limit}, the as-of plus "
                                 f"horizon_days: an event of this ledger is decided by the horizon's end. Its window "
                                 f"can end by then, or the portfolio can state a longer horizon_days", [r["to_id"]])

    # ------------------------------------------------------------------ an event's ways

    def _way_parent(self, pid: str) -> dict | None:
        """The way link that makes pid one way its parent can turn out, or None."""
        return self._one("SELECT id, from_id, stated FROM link WHERE relation = 'way' AND to_id = ?", pid)

    def _event_of(self, pid: str) -> str:
        """The event at the top of pid's tree of ways: pid itself where it is no way, or, for a ladder's band that is
        a world of a watched level and no event itself, the first such level (structure.bands)."""
        seen = {pid}
        while (up := self._way_parent(pid)) is not None and up["from_id"] not in seen:
            pid = up["from_id"]
            seen.add(pid)
        if not self.watched(pid):
            held = self._banded_by(pid)
            if held:
                return held[0]
        return pid

    def _bands(self, pid: str, *, possible: bool = False) -> dict[str, list[str]]:
        """The worlds a ladder's bands give pid, by side, where pid is a watched level (structure.bands); `possible`, the
        bands that make each side up, whether or not anything is stated of them yet (structure.possible_bands)."""
        from harness.derive.engine import Inputs
        from harness.derive.structure import bands, possible_bands
        if not self.watched(pid) or "structure" not in self.engine.registry.versions():
            return {}
        return (possible_bands if possible else bands)(Inputs(self.engine), pid)

    def _banded_by(self, pid: str) -> list[str]:
        """The watched levels pid can be a world of as one of a ladder's bands: a statement on a band is checked against
        its level's baseline before the side it lies on is its bands."""
        watched = [r["to_id"] for r in self._all("SELECT to_id FROM link WHERE relation = 'watches' ORDER BY rowid")]
        return [e for e in watched if pid in {b for ids in self._bands(e, possible=True).values() for b in ids}]

    def _ways_below(self, pid: str) -> list[str]:
        """Every way under pid, at any depth, a ladder's bands among them where they are a watched level's worlds on
        a side it links no way on."""
        out, todo = [], [pid]
        sided = {r["side"] for r in self._all("SELECT stated ->> '$.side' AS side FROM link WHERE relation = 'way' AND "
                                              "from_id = ?", pid)}
        out += [b for side, ids in self._bands(pid).items() if side not in sided for b in ids]
        todo += list(out)
        while todo:
            for r in self._all("SELECT to_id FROM link WHERE relation = 'way' AND from_id = ? ORDER BY rowid",
                               todo.pop()):
                if r["to_id"] not in out and r["to_id"] != pid:
                    out.append(r["to_id"])
                    todo.append(r["to_id"])
        return out

    def _check_way(self, parent: str, way: str, values: dict) -> None:
        """A way has one parent, lies under no way of its own, and is no event the portfolio watches; it states its
        side where its parent is an event, and lies on its parent's side where its parent is a way (MATH.md 1.1;
        ONTOLOGY.md 2.2)."""
        had = self._way_parent(way)
        if had is not None:
            raise StoreError(f"{way} is a way of {had['from_id']} ({had['id']}), and a way has one parent",
                             [had["id"]])
        if parent == way or parent in self._ways_below(way):
            raise StoreError(f"{parent} lies under {way}, and a way lies under its parent")
        if self.watched(way):
            raise StoreError(f"{way} is an event the portfolio watches; a way of {parent} is read in {parent}'s row, "
                             f"against {parent}'s baseline")
        if (self.stated_of(way) or {}).get("baseline") is not None:
            raise StoreError(f"{way} names a baseline, and a way's worlds are measured from its event's: clear it "
                             f"first", [f"{way}.baseline"])
        sided = [r["id"] for r in self._all("SELECT id FROM link WHERE relation = 'way' AND from_id = ? AND "
                                            "stated ->> '$.side' IS NOT NULL ORDER BY rowid", way)]
        if sided:
            raise StoreError(f"{way}'s ways state their sides, as an event's do, and a way's ways lie on its side: "
                             f"clear their sides first", sided)
        self._check_way_side(parent, way, values)

    def _check_way_side(self, parent: str, way: str, values: dict) -> None:
        if self._way_parent(parent) is not None and values.get("side") is not None:
            raise StoreError(f"{parent} is a way, and its ways lie on its side; side is stated on a way of an event")
        if self._way_parent(parent) is None and values.get("side") is None:
            raise StoreError(f"a way of an event states its side, yes or no: the side of {parent} that {way} is a way "
                             f"of")

    def _check_shocked(self, pid: str) -> None:
        """A shock is stated against its event's baseline, which has none of its own (MATH.md 1.1, 1.3): the event names
        its baseline, and a way that holds it states no shock."""
        event = self._event_of(pid)
        base = (self.stated_of(event) or {}).get("baseline")
        if base is None and self._joint_baselines(pid):
            return
        if base is None:
            raise StoreError(f"a shock is stated against its event's baseline, and {event} names none: "
                             f"edit({event}, baseline, ...) names it")
        if pid != event and (base == pid or base in self._ways_below(pid)):
            raise StoreError(f"{pid} holds {event}'s baseline, {base}, and a shock says how another world stands "
                             f"against the baseline", [f"{event}.baseline"])

    def _joint_baselines(self, pid: str, seen: frozenset[str] = frozenset()) -> bool:
        """A conjunction's shock compares its joint world with its events' baselines together."""
        if pid in seen:
            return False
        parts = self._all("SELECT to_id FROM link WHERE from_id=? AND relation IN ('and','then') ORDER BY rowid", pid)
        if len(parts) != 2:
            return False
        for part in parts:
            root = self._event_of(part["to_id"])
            if (self.stated_of(root) or {}).get("baseline") is None and not self._joint_baselines(root, seen | {pid}):
                return False
        return True

    def _check_baseline(self, pid: str, base: str | None) -> None:
        """An event's baseline names one of its worlds: a side with no ways, yes or no, or a way with nothing under it,
        held by no way that states a shock. It is not cleared while shocks are stated in the worlds measured from it
        (MATH.md 1.1)."""
        parent = self._way_parent(pid)
        if parent is not None and base is not None:
            raise StoreError(f"{pid} is a way of {parent['from_id']}, and its event names the baseline")
        tree = self._ways_below(pid)
        if base is None:
            shocks = [r["id"] for r in self._all("SELECT id, from_id FROM link WHERE relation = 'consequence' ORDER BY "
                                                 "rowid") if r["from_id"] in (pid, *tree)]
            if shocks:
                raise StoreError(f"{pid}'s other worlds are measured from its baseline, and it states shocks in them: "
                                 f"{', '.join(shocks)}; remove them, or name another world", shocks)
            return
        if base in ("yes", "no"):
            sided = [r["id"] for r in self._all("SELECT id FROM link WHERE relation = 'way' AND from_id = ? AND "
                                                "stated ->> '$.side' = ? ORDER BY rowid", pid, base)] or \
                self._bands(pid).get(base, [])
            if sided:
                raise StoreError(f"{pid}'s {base} side has ways: the baseline is one world, a side left whole or a "
                                 f"way left whole", sided)
            return
        if base not in tree and base not in {b for ids in self._bands(pid, possible=True).values() for b in ids}:
            raise StoreError(f"{base} is not a way of {pid}: the baseline is one of {pid}'s worlds, yes or no for a "
                             f"side left whole, or a way left whole")
        below = self._ways_below(base)
        if below:
            raise StoreError(f"{base} has ways of its own: the baseline is one world, a way left whole", below)
        holding = [r["id"] for r in self._all("SELECT id, from_id FROM link WHERE relation = 'consequence' ORDER BY "
                                              "rowid") if r["from_id"] in tree and
                   (r["from_id"] == base or base in self._ways_below(r["from_id"]))]
        if holding:
            raise StoreError(f"{base} would be the baseline under shocks stated on it or on a way that holds it "
                             f"({', '.join(holding)}), and a shock says how another world stands against the baseline",
                             holding)

    @contextmanager
    def _keeping_baselines(self):
        """A removal that would leave an event's baseline naming a way no longer under it is refused, naming the
        baseline (ONTOLOGY.md 2.3)."""
        yield
        for r in self._all("SELECT id, stated ->> '$.baseline' AS base FROM object WHERE type = 'Proposition' AND "
                           "stated ->> '$.baseline' LIKE 'prop:%' ORDER BY rowid"):
            if r["base"] not in self._ways_below(r["id"]):
                raise StoreError(f"{r['id']}'s baseline names {r['base']}, which this would leave no way of "
                                 f"{r['id']}: name another world first", [f"{r['id']}.baseline"])

    def _check_follow(self, pid: str, values: dict) -> None:
        """A chance that follows a market follows one the proposition asks (MATH.md 1.2)."""
        mid = next((c["follow"] for c in values.get("chances") or [] if c.get("follow")), None)
        if mid and mid.startswith("name:"):
            worlds = {pid, *self._ways_below(pid)}
            if not any(r["from_id"] in worlds for r in self._all(
                    "SELECT from_id FROM link WHERE relation='worth' AND to_id=?", mid)):
                raise StoreError(f"a chance follows {mid}'s price through its values in the event's worlds; "
                                 "state those values with worth first")
            return
        if mid and not self._one("SELECT id FROM link WHERE relation = 'asks' AND from_id = ? AND to_id = ?",
                                 pid, mid):
            raise StoreError(f"a chance follows a market that asks {pid}, and {mid} is not linked to it: "
                             f"link({pid}, asks, {mid}) first")

    def _check_priced_in(self, pid: str, values: dict) -> None:
        """A weight read from a market is that of a market that asks the proposition (MATH.md 1.5)."""
        mid = schema.weight_market(values.get("priced_in"))
        if mid and not self._one("SELECT id FROM link WHERE relation = 'asks' AND from_id = ? AND to_id = ?",
                                 pid, mid):
            raise StoreError(f"priced_in reads the probability of a market that asks {pid}, and {mid} is not "
                             f"linked to it: link({pid}, asks, {mid}) first")

    def _check_shocks(self, target: str) -> None:
        """A shock is a fraction of the price at the place it is measured from, and a price is never below zero: no
        shock on a name, or on a factor quoted as its proxy's price, is below -1, measured against the baseline or
        against what the price follows (MATH.md 1.3). A shock on a factor quoted as the yield its proxy pays is in points of
        that yield, and every such shock leaves the price above zero."""
        if self.type_of(target) == "Factor" and self.stated_of(target).get("yield") is not None:
            return
        rows = [(r["id"], json.loads(r["stated"])) for r in self._all(
            "SELECT id, stated FROM link WHERE relation = 'consequence' AND to_id = ? ORDER BY rowid", target)]
        below = [lid for lid, s in rows if (s.get("shock") or {}).get("value", 0.0) < -1.0]
        if below:
            raise StoreError(f"a shock on {target} is a fraction of its price at the place it is measured from, and no "
                             f"price is below zero: {', '.join(below)} would put it there; -1 is worth nothing", below)

    def _check_valued_once(self, pid: str) -> None:
        """A holding an event values by what it is worth in its worlds is not also moved by a shock there (MATH.md 1.6,
        ONTOLOGY.md 2.2): within one event's tree of ways, a name a `worth` values takes no `consequence`, and the one refused names
        the other."""
        root = self._event_of(pid)
        tree = [root, *self._ways_below(root)]
        marks = ",".join("?" * len(tree))
        rows = self._all(f"SELECT id, relation, to_id FROM link WHERE relation IN ('consequence', 'worth') AND "
                         f"from_id IN ({marks}) ORDER BY rowid", *tree)
        by = {}
        for r in rows:
            by.setdefault(r["to_id"], {}).setdefault(r["relation"], []).append(r["id"])
        for name, got in by.items():
            if len(got) == 2:
                raise StoreError(f"{name} is valued in {root}'s worlds by what it is worth there "
                                 f"({', '.join(got['worth'])}), and a shock moves its price there too "
                                 f"({', '.join(got['consequence'])}): its worth is its value in each world, so state "
                                 f"what it is worth there, or a shock, one of the two", got["worth"] + got["consequence"])

    def _check_quotes(self, mid: str, cid: str) -> None:
        """A metric that quotes a factor is that factor's price in the metric's own unit, and moves percent for percent
        with it (ONTOLOGY.md 2.2): a price or another level, and the price of one factor."""
        if (self.stated_of(mid) or {}).get("kind") == "occurrence":
            raise StoreError(f"{mid} is something that happens, and a metric that quotes a factor is the factor's price "
                             f"in its own unit")
        if (self.stated_of(mid) or {}).get("kind") == "yield":
            raise StoreError(f"{mid} is a yield, and a metric that quotes a factor is the factor's price in its own "
                             f"unit, moving percent for percent with it; a yield moves against its bond's price, and a "
                             f"factor quoted as the yield its proxy pays carries that yield itself")
        other = self._one("SELECT id, to_id FROM link WHERE relation = 'quotes' AND from_id = ? AND to_id <> ?", mid, cid)
        if other:
            raise StoreError(f"{mid} quotes {other['to_id']} ({other['id']}): a metric is the price of one factor",
                             [other["id"]])

    def _check_quoting(self, oid: str, field: str, old: dict, new: dict) -> None:
        """Whether a factor is quoted as the yield its proxy pays is fixed when it is added (MATH.md 1.3; ONTOLOGY.md
        2.1): every shock on it is stated in that yield's points or as a fraction of its proxy's price, so its yield and
        its proxy's duration are each edited as they change, and neither is added or cleared."""
        if old.get("yield") is None:
            raise StoreError(f"{oid} is quoted as its proxy's price, and its shocks are fractions of it: a factor is "
                             f"quoted as the yield its proxy pays from when it is added, with that yield and its "
                             f"proxy's duration")
        if new.get(field) is None:
            raise StoreError(f"{oid} is quoted as the yield its proxy pays, and its shocks are in points of that yield: "
                             f"its {field} is edited with its new value and the page it was read from")

    def _check_company_id(self, nid: str) -> None:
        """A consequence names a company by its one id (MATH.md 3.1): a company is one name whatever securities list it,
        and its id is the listing chosen by one rule (harness.derive.identity.name_of). A name the store holds is that
        id already; one written on another listing of a company is refused, naming the company's own. A ticker no
        source lists is left to the price a shock is placed from, which refuses it with its own reason (_check_named)."""
        asof = self.meta("asof")
        if not asof or self._name_exists(nid):
            return
        from harness.derive.identity import name_of
        try:
            own = name_of(self, nid.split(":", 1)[1], asof)["name"]
        except LookupError:
            return
        if own == nid:  # the rule's id; the company's in this store where the portfolio already owns it under another,
            # kept since a link named it (harness.derive.portfolio.as_companies)
            looked = self.engine.value("lookthrough", "portfolio").output if self.type_of("portfolio") else None
            ticker = nid.split(":", 1)[1]
            own = next((other for other, held in sorted(((looked or {}).get("securities") or {}).items())
                        if other != nid and ticker in held), nid)
        if own != nid:
            raise StoreError(f"{nid} is a listing of the company whose name is {own}: a company is one name, and a "
                             f"shock on its price is stated on {own}", [own])

    def _check_not_proxy(self, nid: str, relation: str) -> None:
        """The price of a factor's proxy is the factor's own (MATH.md 1.4, 3.2): a shock on it, or what it is in a world,
        is stated on the factor, so one price has one value in each world. Refused, naming the factor."""
        if not nid.startswith("name:"):
            return
        r = self._one("SELECT id, stated ->> '$.name' AS name FROM object WHERE type = 'Factor' AND "
                      "stated ->> '$.proxy' = ?", nid.split(":", 1)[1])
        if r is None:
            return
        what = "a shock on that price" if relation == "consequence" else "what that price is in a world"
        raise StoreError(f"{nid} is the price of {r['id']}, {r['name']}, whose proxy it is: {what} is stated on "
                         f"{r['id']}", [r["id"]])

    def _check_named(self, names: list[str]) -> None:
        """A name a consequence runs to has a price (MATH.md 1.4, 1.5): a shock on it is placed from that price, and its
        own part is its move beyond its response, measured on that price's history. The betas say which names their
        sources answered with no price; read at the write's commit, once the call's other reads are done."""
        if "betas" not in self.engine.registry.versions() or self._kind("portfolio") is None:
            return
        fits = (self.engine.value("betas", "portfolio").output or {}).get("fits") or {}
        none = [n for n in dict.fromkeys(names) if (fits.get(n) or {}).get("rule") == "no price"]
        if none:
            uses = [r["id"] for n in none for r in self._all(
                "SELECT id FROM link WHERE relation = 'consequence' AND to_id = ? ORDER BY rowid", n)]
            raise StoreError(f"{', '.join(none)} {'has' if len(none) == 1 else 'have'} no price: {'its' if len(none) == 1 else 'their'} "
                             f"sources hold none, and a shock on a price is placed from it", uses)

    # ------------------------------------------------------------------ remove

    def remove(self, id: str, *, call: int | None = None) -> dict:
        """Remove an object or link the analyst created or received as initial contents,
        with its links; refuse what is derived, a market, and what would strand a stated
        fact or reference, or a chance the propositions it is given, naming it. Returns the record of what went."""
        with self.transaction(), self._keeping_baselines():
            kind = self._kind(id)
            if kind == "object":
                return self._remove_object(id)
            if kind == "link":
                return self._remove_link(id)
            if kind == "source":
                return self._remove_source(id)
            raise StoreError(*self._not_removable(id, kind))

    def _not_removable(self, oid: str, kind: str | None) -> tuple[str, list[str]]:
        uses = [r["id"] for r in self._all("SELECT id FROM link WHERE to_id = ? ORDER BY rowid", oid)]
        if kind is None:
            return f"{oid} is not in the store", []
        if kind == "name":
            return (f"{oid} is derived: a Name follows from the holdings and the funds' constituents, and is kept "
                    f"while a link names it; remove the link that holds or names it", uses)
        if kind == "proxy_metric":
            ticker = oid.split(":", 2)[2]
            of = self._proxy_channels(ticker) + [n for n in [self._priced_name(ticker)] if n]
            return (f"{oid} is derived: it is the price of {', '.join(of) or 'nothing the store holds now'}, and is "
                    f"kept while a factor's proxy or the portfolio's name is its ticker, or a proposition or link "
                    f"names it", of)
        if kind == "derived_link":
            return f"{oid} is derived; it follows from structure and data", []
        if kind in ("computation", "judgment"):
            return (f"{oid} follows from structure: once what asked for it goes, nothing reads it as a number, and it "
                    f"stays to answer the same question again", [])
        if kind == "market":  # named at either end of a link, or by a chance (ONTOLOGY.md 2; MATH.md 3.3)
            uses = [r["id"] for r in self._all("SELECT id FROM link WHERE to_id = ? OR from_id = ? ORDER BY rowid", oid, oid)]
            uses += list(dict.fromkeys(p for p, _ in self.given_by(oid)))
            return (f"{oid} comes from its venue and is kept while a link, a note or a chance names it; what names it: "
                    f"{', '.join(uses)}", uses)
        if kind == "page":
            return f"{oid} is a fetched page that is not kept; there is nothing to remove", []
        if kind == "search":
            return f"{oid} is the record of a search", []
        return f"{oid} is fetched data, kept with what it derives", []

    def _delete_links(self, ids: Iterable[str]) -> list[dict]:
        """Delete links with the about links that name them; the snapshots of every link deleted."""
        ids = list(ids)
        snapshots: list[dict] = []
        while ids:
            rows = [self._link_row(i) for i in ids]
            rows = [r for r in rows if r is not None]
            snapshots.extend(self._link_dict(r) for r in rows)
            marks = ",".join("?" * len(ids))
            about = [r["id"] for r in self._all(
                f"SELECT id FROM link WHERE (relation = 'about' AND to_id IN ({marks})) OR (relation = 'cites' "
                f"AND from_id IN ({marks})) ORDER BY rowid", *ids, *ids)]
            self.db.execute(f"DELETE FROM link WHERE id IN ({marks})", ids)
            ids = [a for a in about if a not in {s["id"] for s in snapshots}]
        return snapshots

    def _about(self, ids: list[str]) -> list[str]:
        """The about links that name any of these ids."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return [r["id"] for r in self._all(
            f"SELECT id FROM link WHERE relation = 'about' AND to_id IN ({marks}) ORDER BY rowid", *ids)]

    def _remove_link(self, lid: str) -> dict:
        row = self._link_row(lid)
        if row["relation"] == "holds" and row["to_id"].startswith("name:"):
            given = json.loads(row["stated"]).get("ticker") or row["to_id"].split(":", 1)[1]
            after = self._held_tickers(row["to_id"])
            after.remove(given) if given in after else None
            self._check_pricing(row["to_id"], after)
        if row["relation"] == "cites":
            fact = f"{row['from_id']}.{row['field']}"
            raise StoreError(f"{lid} cites the page for {fact}, and a stated fact keeps its source: edit the field "
                             f"with another source, or remove {row['from_id']}", [fact])
        if row["relation"] == "asks" and \
                schema.weight_market((self.stated_of(row["from_id"]) or {}).get("priced_in")) == row["to_id"]:
            raise StoreError(f"{row['from_id']}'s priced_in reads {row['to_id']} through {lid}: edit priced_in first",
                             [f"{row['from_id']}.priced_in"])
        if row["relation"] == "asks":
            following = [i for i, c in enumerate((self.stated_of(row["from_id"]) or {}).get("chances") or [])
                         if c.get("follow") == row["to_id"]]
            if following:
                raise StoreError(f"{row['from_id']}'s chance follows {row['to_id']} through {lid}: restate the chance "
                                 f"first", [f"{row['from_id']}.chances[{i}]" for i in following])
        if row["relation"] == "worth":
            resting = []
            for prop in self._all("SELECT id, stated FROM object WHERE type='Proposition'"):
                if row["from_id"] not in {prop["id"], *self._ways_below(prop["id"])}:
                    continue
                resting += [f"{prop['id']}.chances[{i}]"
                            for i, c in enumerate(json.loads(prop["stated"]).get("chances") or [])
                            if c.get("follow") == row["to_id"]]
            if resting:
                raise StoreError(f"{', '.join(resting)} follows {row['to_id']}'s price through its world values: "
                                 "restate the chance before removing a value", resting)
        if row["relation"] == "about":
            resting = [r for r in self._resting_on(row["from_id"]) if r.startswith(f"{row['to_id']}.")]
            if resting:
                raise StoreError(f"{row['from_id']} is the basis of {', '.join(resting)}, and stays a note about "
                                 f"{row['to_id']} while it is: restate {'them' if len(resting) > 1 else 'it'} with "
                                 f"another basis first", resting)
        deleted = self._delete_links([lid])
        released = self._release({row["from_id"], row["to_id"]} | {d["to"] for d in deleted})
        return {"removed": deleted[0], "cascaded": deleted[1:], "released": released,
                **self._went_with(deleted)}

    def _went_with(self, links: list[dict], stated: dict | None = None, kind: str | None = None) -> dict:
        """The Notes removed statements rested on, gone with them."""
        named = {n for d in links for n in self._named_notes(d["relation"], d["stated"])}
        if stated is not None:
            named |= self._named_notes(kind, stated)
        gone = self._drop_notes(named)
        return {"notes": gone} if gone else {}

    def _references_to(self, oid: str) -> list[str]:
        if self._kind(oid) == "object" and self._object_row(oid)["type"] == "Note":
            return self._resting_on(oid)
        refs = [f"{r['id']}.baseline" for r in self._all(
            "SELECT id FROM object WHERE type = 'Proposition' AND stated ->> '$.baseline' = ? ORDER BY rowid", oid)]
        refs += [f"{pid}.chances[{i}]" for pid, i in self.given_by(oid) if pid != oid]
        for t in schema.OBJECTS.values():
            for f in t.stated:
                if f.kind == schema.REF:
                    refs += [f"{r['id']}.{f.name}" for r in self._all(
                        f"SELECT id FROM object WHERE type = ? AND stated ->> '$.{f.name}' = ? ORDER BY rowid",
                        t.name, oid)]
        return refs

    def _remove_object(self, oid: str) -> dict:
        row = self._object_row(oid)
        refs = self._references_to(oid)
        if refs and row["type"] == "Note":
            raise StoreError(f"{oid} is the basis of {', '.join(refs)}: restate {'them' if len(refs) > 1 else 'it'} "
                             f"with another basis, or remove {'them' if len(refs) > 1 else 'it'}, first", refs)
        if refs:
            raise StoreError(f"{oid} is named by {', '.join(refs)}; edit or remove those first", refs)
        links = [r["id"] for r in self._all("SELECT id FROM link WHERE from_id = ? OR to_id = ? ORDER BY rowid",
                                            oid, oid)]
        searches = [r["id"] for r in self._all("SELECT id FROM search WHERE proposition_id = ?", oid)]
        deleted = self._delete_links(links + self._about(searches))
        self.db.execute("DELETE FROM search WHERE proposition_id = ?", (oid,))
        self.db.execute("DELETE FROM object WHERE id = ?", (oid,))
        ends = {d["to"] for d in deleted} | {d["from"] for d in deleted}
        released = self._release((ends | _chance_markets(json.loads(row["stated"]))) - {oid})
        record = {"removed": {"id": oid, "type": row["type"], "stated": json.loads(row["stated"])},
                  "cascaded": deleted, "released": released,
                  **self._went_with(deleted, json.loads(row["stated"]), row["type"])}
        if searches:
            record["searches"] = searches
        return record

    def _remove_source(self, sid: str) -> dict:
        row = self._source(sid)
        cites = [f"{r['from_id']}.{r['field']}" for r in self._all(
            "SELECT from_id, field FROM link WHERE relation = 'cites' AND to_id = ? ORDER BY rowid", sid)]
        if cites:
            raise StoreError(f"{sid} is cited for {', '.join(cites)}, and a stated fact keeps its source: edit "
                             f"those fields with another source first", cites)
        uses = [r["id"] for r in self._all("SELECT id FROM link WHERE to_id = ? ORDER BY rowid", sid)]
        deleted = self._delete_links(uses)
        self._unkeep(sid, row)
        return {"removed": {"id": sid, "type": "Source", "url": row["url"], "kept": False}, "cascaded": deleted,
                "released": []}

    def _unkeep(self, sid: str, row: sqlite3.Row) -> None:
        """A kept page whose last use goes: it is again a fetched page, held under the same id."""
        self.db.execute("DELETE FROM source WHERE id = ?", (sid,))
        self._hold(sid, dict(row))

    def _release(self, ids: Iterable[str]) -> list[dict]:
        """Markets and pages that lost their last use are not kept. A market's fetched data stays, as all
        fetched data does, so a checkpoint that kept the market reads it again; a page is again a fetched page,
        held under its id and citable."""
        released: list[dict] = []
        for x in sorted(i for i in ids if isinstance(i, str)):
            if schema.MARKET_ID_RE.match(x) and self._market_kept(x):  # kept while a link or a chance names it
                if self._one("SELECT 1 FROM link WHERE to_id = ? OR from_id = ? LIMIT 1", x, x) is None \
                        and not self.given_by(x):
                    self.db.execute("DELETE FROM market WHERE id = ?", (x,))
                    released.append({"id": x, "type": "Market"})
            elif x.startswith("src:"):
                s = self._source(x)
                if s is None:
                    continue
                if self._one("SELECT 1 FROM link WHERE to_id = ? AND relation IN ('cites', 'evidence') LIMIT 1",
                             x) is None:
                    about = [r["id"] for r in self._all("SELECT id FROM link WHERE to_id = ? ORDER BY rowid", x)]
                    cascaded = self._delete_links(about)
                    self._unkeep(x, s)
                    rec = {"id": x, "type": "Source", "url": s["url"], "kept": False}
                    if cascaded:
                        rec["cascaded"] = cascaded
                    released.append(rec)
        return released

    # ------------------------------------------------------------------ get

    def get(self, id: str, text: str | None = None, *, derived_fields=None, exclude_derived=()) -> dict:
        """The object or link with its stated fields, derived fields (each with its basis),
        and links; a page, given text, as the passages that contain it (`find`)."""
        kind = self._kind(id)
        if kind is None:
            raise StoreError(_absent(id))
        if text is not None and str(text).strip():
            if kind not in ("source", "page"):
                raise StoreError(f"text finds words in a page, and {id} is not a page: get({id}) returns it whole")
            s = self.source_row(id)
            return {"id": id, "type": "Source", "kept": kind == "source", "url": s["url"], "dated": s["dated"],
                    "text": text, **find(s["text"], str(text))}
        if kind in ("object", "link"):
            return getattr(self, f"_get_{kind}")(id, derived_fields=derived_fields, exclude_derived=exclude_derived)
        return getattr(self, f"_get_{kind}")(id)

    def _get_object(self, oid: str, *, derived_fields=None, exclude_derived=()) -> dict:
        row = self._object_row(oid)
        out = {"id": oid, "type": row["type"], "origin": row["origin"], "stated": json.loads(row["stated"])}
        citations = self.citations_of(oid)
        if citations:
            out["citations"] = citations
        out["derived"] = self.engine.derived(oid, row["type"], fields=derived_fields, exclude=exclude_derived)
        out["links"] = self._links_view(oid)
        if row["type"] == "Proposition":
            out["searches"] = self._searches_shown(oid)
        if row["type"] == "Factor":
            out["proxy_metric"] = schema.proxy_metric_id(out["stated"]["proxy"])
        return out

    def _get_link(self, lid: str, *, derived_fields=None, exclude_derived=()) -> dict:
        r = self._link_row(lid)
        out = {"id": lid, "type": "link", "relation": r["relation"], "from": r["from_id"], "to": r["to_id"],
               "origin": r["origin"], "stated": json.loads(r["stated"])}
        if r["field"] is not None:
            out["field"] = r["field"]
        out["derived"] = self.engine.derived(lid, r["relation"], fields=derived_fields, exclude=exclude_derived)
        return out

    def _get_derived_link(self, lid: str) -> dict:
        rec = self._derived_link(lid)
        out = {"id": lid, "type": "link", "relation": rec["relation"], "from": rec["from"], "to": rec["to"],
               "derived_link": True}
        out["derived"] = {f: {"value": v, "basis": rec.get("basis")} for f, v in rec.get("fields", {}).items()}
        return out

    def _get_market(self, mid: str) -> dict:
        r = self._one("SELECT * FROM market WHERE id = ?", mid)
        derived = self.engine.derived(mid, "Market")  # first: a first read fetches the book and history
        data = [{"id": d["id"], "kind": d["kind"], "retrieved_at": d["retrieved_at"]}
                for d in self._all("SELECT id, kind, retrieved_at FROM data WHERE market_id = ? ORDER BY rowid", mid)]
        return {"id": mid, "type": "Market", "stated": {"venue": r["venue"], "id": r["venue_id"]}, "data": data,
                "derived": derived, "links": self._links_view(mid)}

    def _get_source(self, sid: str) -> dict:
        s = self.source_row(sid)
        out = {"id": sid, "type": "Source", "kept": True}
        for k in ("url", "retrieved_at", "dated", "text"):
            if s.get(k) is not None:
                out[k] = s[k]
        cited = [f"{r['from_id']}.{r['field']}" for r in self._all(
            "SELECT from_id, field FROM link WHERE relation = 'cites' AND to_id = ? ORDER BY rowid", sid)]
        if cited:
            out["cited_for"] = cited
        out["links"] = self._links_view(sid, with_derived=False)
        return out

    def _get_page(self, sid: str) -> dict:
        s = self.source_row(sid)
        return {"id": sid, "type": "Source", "kept": False, "url": s["url"], "retrieved_at": s["retrieved_at"],
                "dated": s["dated"], "text": s["text"]}

    def _get_name(self, nid: str) -> dict:
        reasons = self._name_reasons().get(nid, [])
        return {"id": nid, "type": "Name", "stated": {"ticker": nid.split(":", 1)[1]}, "because": reasons,
                "derived": self.engine.derived(nid, "Name"), "links": self._links_view(nid)}

    def _get_cash(self, cid: str) -> dict:
        """Cash in a currency the portfolio holds: its currency, and its holding."""
        return {"id": cid, "type": "Cash", "stated": {"currency": cid.split(":", 1)[1]},
                "links": self._links_view(cid)}

    def _get_proxy_metric(self, mid: str) -> dict:
        ticker = mid.split(":", 2)[2]
        out = {"id": mid, "type": "Metric", "proxy_of": self._proxy_channels(ticker), "ticker": ticker}
        named = self._priced_name(ticker)
        if named:
            out["price_of"] = named
        return {**out, "stated": {}, "derived": self.engine.derived(mid, "Metric"), "links": self._links_view(mid)}

    def _get_computation(self, cid: str) -> dict:
        r = self._computation_row(cid)
        return {"id": cid, "type": "Computation", "derivation": r["derivation"], "version": r["version"],
                "subject": r["subject"], "input_hash": r["input_hash"], "inputs": json.loads(r["inputs"]),
                "output": json.loads(r["output"]), "created_at": r["created_at"]}

    def _get_judgment(self, jid: str) -> dict:
        r = self._one("SELECT * FROM judgment WHERE id = ?", jid)
        return {"id": jid, "type": "Judgment", "kind": r["kind"], "statement": r["statement"],
                "input_hash": r["input_hash"], "inputs": json.loads(r["inputs"]), "answer": json.loads(r["answer"]),
                "model": r["model"], "raw": r["raw"], "created_at": r["created_at"]}

    def _get_search(self, sid: str) -> dict:
        return self._search_dict(self._one("SELECT * FROM search WHERE id = ?", sid))

    def _get_data(self, did: str) -> dict:
        d = self._data_row(did)
        return {"id": did, "type": "data", "kind": d["kind"], "subject": d["subject"],
                "range": [d["range_from"], d["range_to"]], "request": d["request"],
                "retrieved_at": d["retrieved_at"], "payload": self.data_payload(did)}

    @staticmethod
    def _search_dict(r: sqlite3.Row) -> dict:
        out = {"id": r["id"], "type": "search", "tool": r["tool"], "proposition": r["proposition_id"]}
        for k in ("venue", "endpoint", "query"):
            if r[k] is not None:
                out[k] = r[k]
        out["params"] = json.loads(r["params"])
        out["results"] = json.loads(r["results"])
        out["created_call"] = r["created_call"]
        return out

    def _searches_of(self, pid: str) -> list[dict]:
        """A proposition's searches, each once, in the order they ran, each whole (_search_dict): those recorded on it,
        and those that returned a market or a page it uses, a market usually being found before the proposition it
        bears on is written. What it uses is every market and page it names: a market it asks, links by `implies`
        either way, gives a chance given or not, follows, or reads its weight from; a page it holds as evidence. Each
        search says which of its results the proposition uses (`uses`). When the statement goes, so does what it
        attributed."""
        found: dict[str, dict] = {}

        def attribute(rows, used: str | None) -> None:
            for r in rows:
                item = found.setdefault(r["id"], {"row": r, "uses": []})
                if used is not None and used not in item["uses"]:
                    item["uses"].append(used)

        attribute(self._all("SELECT * FROM search WHERE proposition_id = ?", pid), None)
        stated = self.stated_of(pid) or {}
        markets = [link["to"] for link in self.links_of(pid, "asks")]
        markets += [x for link in self.links_of(pid, "implies", "both")
                    for x in (link["from"], link["to"]) if schema.MARKET_ID_RE.match(x)]
        markets += sorted(_chance_markets(stated))
        markets += [c["follow"] for c in stated.get("chances") or []
                    if schema.MARKET_ID_RE.match(str(c.get("follow") or ""))]
        weight = schema.weight_market(stated.get("priced_in"))
        markets += [weight] if weight else []
        for m in dict.fromkeys(markets):
            attribute(self._all("SELECT DISTINCT s.* FROM search s, json_each(s.results) j WHERE s.tool = "
                                "'search_markets' AND j.value = ?", m), m)
        for link in self.links_of(pid, "evidence"):
            url = (self.source_row(link["to"]) or {}).get("url")
            if url:
                attribute(self._all("SELECT DISTINCT s.* FROM search s, json_each(s.results) j WHERE s.tool IN "
                                    "('search_web', 'search_company') AND CASE WHEN j.type = 'object' "
                                    "THEN j.value ->> '$.url' END = ?", url), link["to"])
        out = []
        for item in sorted(found.values(), key=lambda x: _seq_key(x["row"]["id"])):
            out.append({**self._search_dict(item["row"]), "uses": item["uses"]})
        return out

    def _searches_shown(self, pid: str) -> list[dict]:
        """A proposition's searches as it shows them (ONTOLOGY.md 2): each by its words, where it ran, how many results it
        returned, and which of them the proposition uses; the results themselves are read by the search's id."""
        return [{"id": d["id"], "tool": d["tool"], **{k: d[k] for k in ("query", "venue", "endpoint") if k in d},
                 "returned": len(d["results"]), "uses": d["uses"]} for d in self._searches_of(pid)]

    def _links_view(self, oid: str, *, with_derived: bool = True) -> list[dict]:
        items = []
        for link in self.links_of(oid, direction="both"):
            out = link["from"] == oid
            item = {"id": link["id"], "relation": link["relation"], "direction": "out" if out else "in",
                    "other": link["to"] if out else link["from"], "stated": link["stated"]}
            if link["relation"] == "about" and out and self._kind(link["to"]) is None:
                item["exists"] = False  # a derived object or link its inputs do not give
            if with_derived:
                derived = self.engine.derived(link["id"], link["relation"])
                if derived:
                    item["derived"] = derived
            items.append(item)
        for lid, rec in self._derived_links(oid).items():
            out = rec["from"] == oid
            item = {"id": lid, "relation": rec["relation"], "direction": "out" if out else "in",
                    "other": rec["to"] if out else rec["from"], "derived_link": True}
            if rec.get("fields"):
                item["derived"] = {f: {"value": v, "basis": rec.get("basis")} for f, v in rec["fields"].items()}
            items.append(item)
        return items

    # ------------------------------------------------------------------ query and neighbors

    def _instances(self, type_name: str) -> list[tuple[str, dict]]:
        """Every object of a type with its stated fields, each read as `ids_of` and `describe` read it."""
        if type_name in MUTABLE_OBJECT_TYPES:
            touches.read(touches.objects(type_name), touches.rows(type_name))
            rows = [(r["id"], json.loads(r["stated"])) for r in self._all(
                "SELECT id, stated FROM object WHERE type = ? ORDER BY rowid", type_name)]
            if type_name == "Metric":
                rows += [(m, {}) for m in self._proxy_metrics()]
            return rows
        if type_name == "Name":
            return [(n, {"ticker": n.split(":", 1)[1]}) for n in self._name_reasons()]
        if type_name == "Market":
            touches.read(touches.objects("Market"))
            return [(r["id"], {"venue": r["venue"], "id": r["venue_id"]}) for r in self._all(
                "SELECT id, venue, venue_id FROM market ORDER BY rowid")]
        if type_name == "Source":
            touches.read(touches.table("source"))
            return [(r["id"], self.stated_of(r["id"])) for r in self._all("SELECT id FROM source ORDER BY rowid")]
        if type_name == "Computation":
            return [(r["id"], {"derivation": r["derivation"], "subject": r["subject"], "input_hash": r["input_hash"]})
                    for r in self._all("SELECT id, derivation, subject, input_hash FROM computation ORDER BY rowid")]
        if type_name == "Judgment":
            touches.read(touches.table("judgment"))
            return [(r["id"], {"kind": r["kind"], "statement": r["statement"], "answer": json.loads(r["answer"]),
                               "model": r["model"]})
                    for r in self._all("SELECT id, kind, statement, answer, model FROM judgment ORDER BY rowid")]
        return []

    @staticmethod
    def _matches(value: Any, cond: Any) -> bool:
        if isinstance(cond, dict) and len(cond) == 1:
            (op, arg), = cond.items()
            try:
                if op == "eq":
                    return value == arg
                if op == "ne":
                    return value != arg
                if op == "lt":
                    return value is not None and value < arg
                if op == "le":
                    return value is not None and value <= arg
                if op == "gt":
                    return value is not None and value > arg
                if op == "ge":
                    return value is not None and value >= arg
                if op == "in":
                    return value in arg
                if op == "contains":
                    return value is not None and arg in value
            except TypeError:
                return False
            raise StoreError(f"a filter is a value or one of eq, ne, lt, le, gt, ge, in, contains; not {op!r}")
        return value == cond

    def query(self, type: str, where: dict | None = None) -> list[dict]:
        """Objects of a type whose fields match the filters; a filter on a derived field
        derives it."""
        t = schema.OBJECTS.get(type)
        if t is None:
            raise StoreError(f"there is no type {type!r}; the types are {', '.join(schema.OBJECTS)}")
        where = where or {}
        if not isinstance(where, dict):
            raise StoreError("where is an object of field filters")
        known = {"id"} | {f.name for f in t.stated} | set(t.derived)
        unknown = [k for k in where if k not in known]
        if unknown:
            raise StoreError(f"{type} has no field {unknown[0]!r}")
        out = []
        for oid, stated in self._instances(type):
            derived = None
            ok = True
            # Every field of a proxy Metric is derived.
            proxy = type == "Metric" and schema.PROXY_METRIC_RE.match(oid) is not None
            for key, cond in where.items():
                if key == "id":
                    value = oid
                elif not proxy and t.field(key) is not None:
                    value = stated.get(key)
                else:
                    if derived is None:
                        derived = self.engine.derived(oid, type)
                    value = derived.get(key, {}).get("value")
                if not self._matches(value, cond):
                    ok = False
                    break
            if ok:
                item = {"id": oid, "type": type, "stated": stated}
                if derived:
                    item["derived"] = {k: derived[k] for k in where if k in derived}
                out.append(item)
        return out

    def neighbors(self, id: str, relation: str | None = None, direction: str = "both") -> list[dict]:
        """The objects linked to an id, by relation and direction."""
        if self._kind(id) is None:
            raise StoreError(f"{id} is not in the store")
        if direction not in ("out", "in", "both"):
            raise StoreError("direction is out, in, or both")
        if relation is not None and relation not in schema.RELATIONS:
            raise StoreError(f"there is no relation {relation!r}")
        found = []
        rows = self._all("SELECT * FROM link WHERE from_id = ? OR to_id = ? ORDER BY rowid", id, id)
        links = [self._link_dict(r) for r in rows]
        links += [{"id": lid, "relation": rec["relation"], "from": rec["from"], "to": rec["to"], "derived_link": True}
                  for lid, rec in self._derived_links(id).items()]
        for link in links:
            out = link["from"] == id
            d = "out" if out else "in"
            if (relation is not None and link["relation"] != relation) or direction not in ("both", d):
                continue
            other = link["to"] if out else link["from"]
            kind = self.type_of(other)
            item = {"link": link["id"], "relation": link["relation"], "direction": d,
                    "object": {"id": other, "type": kind}}
            if kind is None:
                item["exists"] = False
            if "field" in link:
                item["field"] = link["field"]
            if link.get("derived_link"):
                item["derived_link"] = True
            found.append(item)
        return found

    # ------------------------------------------------------------------ initial contents

    def load_initial(self, directory: str | Path) -> dict:
        """Load channels.yaml into this store as its initial contents: the analyst's from then on, written
        through the same rules as its own calls, in one transaction. The store ships no metrics: the
        analyst adds those it uses."""
        p = Path(directory) / "channels.yaml"
        doc = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
        count = 0
        with self.transaction():
            for c in doc.get("channels") or []:
                self._add("Factor", c, origin="initial", call=None)
                count += 1
        return {"channels": count}
