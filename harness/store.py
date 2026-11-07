"""The store.

One SQLite file per store, `stores/{name}/store.sqlite`, with `trace.jsonl` beside it. It holds the analyst's objects
and links with their stated fields; the citation of each stated fact, and of each stated assumption cited from a page,
to its page (`sourced_by`); the pages kept as Sources while they are cited or linked as evidence, each held again as a
fetched page, under its id, when its last use goes; the markets kept while a link or a note uses them; the data
fetched to derive values, kept whatever is later released; the record of each search; and the cache of Computations
and Judgments.

Every write is one transaction, and a refused call writes nothing. Every derived value
is computed on read by the engine and returned with its basis; no derived value, derived
object, or derived link is a row.

Checkpoints. Every change to what the analyst writes (objects, links, kept markets
and pages, search records, and the as-of) leaves in the `undo` table the statement that
reverses it, written by triggers, the technique of https://www.sqlite.org/undoredo.html. A
checkpoint is a position in that log, taken at each message the analyst is sent; rewinding
to it runs the statements after it in reverse. Fetched data, the cache of Computations and
Judgments, and the id sequences are not in the log: they are kept, so a restored state reads
what was fetched and asked before, and an id names one thing for the store's whole life.

One store is used from more than one thread (the analyst's and the page's); `lock` is held for
each tool call and each read the page makes, so each sees the store between calls.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from harness import credentials, schema
from harness.derive import logic
from harness.derive.engine import Engine, Registry, Unavailable, canonical, text_hash
from harness.derive.portfolio import pseudo
from harness.venues import NAMES as VENUE_NAMES

SCHEMA_VERSION = "6"  # a store of another schema is not opened: its stated fields are not this schema's
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

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE seq (prefix TEXT PRIMARY KEY, n INTEGER NOT NULL);

CREATE TABLE object (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('Portfolio', 'Proposition', 'Metric', 'Channel', 'Note')),
    stated TEXT NOT NULL CHECK (json_valid(stated)),
    origin TEXT NOT NULL CHECK (origin IN ('initial', 'analyst')),
    created_call INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX object_by_type ON object(type);
CREATE UNIQUE INDEX one_portfolio ON object(type) WHERE type = 'Portfolio';
CREATE UNIQUE INDEX channel_name ON object(stated ->> '$.name') WHERE type = 'Channel';
CREATE UNIQUE INDEX channel_order ON object(stated ->> '$.order') WHERE type = 'Channel';
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
    origin TEXT NOT NULL CHECK (origin IN ('initial', 'analyst')),
    created_call INTEGER,
    created_at TEXT NOT NULL,
    CHECK ((relation = 'sourced_by') = (field IS NOT NULL))
);
CREATE UNIQUE INDEX one_link ON link(relation, from_id, to_id) WHERE relation <> 'sourced_by';
CREATE UNIQUE INDEX one_citation ON link(from_id, field) WHERE relation = 'sourced_by';
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
    tool TEXT NOT NULL CHECK (tool IN ('search_markets', 'search_web', 'search_company')),
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

CREATE TABLE computation (
    id TEXT PRIMARY KEY,
    derivation TEXT NOT NULL,
    version INTEGER NOT NULL,
    subject TEXT,
    input_hash TEXT NOT NULL,
    inputs TEXT NOT NULL CHECK (json_valid(inputs)),
    output TEXT NOT NULL CHECK (json_valid(output)),
    created_at TEXT NOT NULL,
    UNIQUE (derivation, input_hash)
);
CREATE TRIGGER computation_immutable BEFORE UPDATE ON computation
BEGIN SELECT RAISE(ABORT, 'a Computation is immutable'); END;
CREATE TRIGGER computation_kept BEFORE DELETE ON computation
BEGIN SELECT RAISE(ABORT, 'the cache keeps every Computation'); END;

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
""".replace("{DATA}", DATA)

# What the analyst writes, by table: its columns, and those an update changes. Each change leaves its reverse in
# the undo log, from which checkpoints restore.
WRITTEN = {
    "object": (("id", "type", "stated", "origin", "created_call", "created_at"),
               ("stated", "origin", "created_call", "created_at")),
    "link": (("id", "relation", "from_id", "to_id", "field", "stated", "origin", "created_call", "created_at"),
             ("to_id", "stated", "origin", "created_call", "created_at")),
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

TABLES = ("meta", "seq", "object", "link", "market", "source", "data", "search", "computation", "judgment",
          "undo", "checkpoint")


def store_dir(name: str, root: str | Path = "stores") -> Path:
    """stores/{name}/: one store, one portfolio."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise StoreError(f"{name!r} is not a store name: letters, digits, dots, dashes, and underscores")
    return Path(root) / name


MUTABLE_OBJECT_TYPES = ("Portfolio", "Proposition", "Metric", "Channel", "Note")
# Requests side by side when the store fetches many series at once. Massive allows a paid plan's asset class any
# number of requests and its free tier five a minute, and its client waits out a 429 either way
# (https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis).
PREFETCH_WORKERS = 8


# What each kind of fetched data is, as a reason names it.
DATA_WORDS = {"ticker_ref": "the reference for {}", "listings": "the US listings of {}",
              "bars": "the daily prices of {}", "hourly": "the hourly prices of {}",
              "last_trade": "the latest trade of {}", "dividends": "the dividends of {}",
              "constituents": "the holdings of {}", "snapshot": "the latest price of {}", "market": "the terms of {}",
              "book": "the book of {}", "history": "the price history of {}", "news": "the news about {}"}


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


def _named(d: dict) -> set[str]:
    """The propositions a derivation names: those it reads, and each chance's of and givens."""
    return set(d["reads"]) | {x for c in d["chances"] for x in [c["of"], *c["given"], *c["given_not"]] if x}


def _check(owner: str, f: schema.Field, value: Any) -> Any:
    try:
        return schema.check_value(f, value)
    except schema.Invalid as e:
        raise StoreError(f"{owner}.{e}") from None


class Store:
    """One store. Open it with `Store.create` or `Store.open`."""

    def __init__(self, directory: Path, *, registry: Registry | None = None, judge=None, venues=None,
                 fetchers: dict | None = None):
        self.dir = Path(directory)
        self.path = self.dir / STORE_FILE
        self.trace_path = self.dir / TRACE_FILE
        self.db = sqlite3.connect(self.path, autocommit=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.lock = threading.RLock()
        self.progress: Callable[[dict], None] | None = None  # told each stage of a long read as it happens
        self.venues = venues
        self.fetchers = dict(fetchers or {})
        self.revision = 0
        self._depth = 0
        self._pending: dict[str, dict] = {}  # the pages fetched and held, not kept: each a dict replaced, never changed
        self._pending_at_begin: dict[str, dict] = {}
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
            store._set_meta("schema_version", SCHEMA_VERSION)
            store._set_meta("name", d.name)
            store._set_meta("created_at", _now())
        store.trace_path.touch()
        if initial is not None:
            try:
                store.load_initial(initial)
            except BaseException:
                store.close()
                for leftover in (store.path, store.trace_path, d / f"{STORE_FILE}-wal", d / f"{STORE_FILE}-shm"):
                    leftover.unlink(missing_ok=True)
                raise
        return store

    @classmethod
    def open(cls, directory: str | Path, **kw) -> "Store":
        d = Path(directory)
        if not (d / STORE_FILE).exists():
            raise StoreError(f"{d} holds no store")
        store = cls(d, **kw)
        version = store.meta("schema_version")
        if version != SCHEMA_VERSION:  # an earlier schema's stated fields are not this one's, and are not migrated
            store.close()
            raise StoreError(f"{d} is a store of schema {version}; this code reads schema {SCHEMA_VERSION}")
        store._admit_company_searches()
        return store

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
                del self._pending[sid]
            self.db.execute("DELETE FROM undo WHERE n > ?", (mark["undo"],))  # with what undoing them logged
            self.db.execute("DELETE FROM checkpoint WHERE rowid > ?", (mark["rowid"],))
            self._touch()  # every value is read again: the engine's memo is of the revision before
        return {"checkpoint": key, "undone": len(steps)}

    def meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", key)
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------ transactions

    @contextmanager
    def transaction(self):
        """One transaction; a call inside another joins it. An exception rolls it back whole."""
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
        self._depth = 1
        self._pending_at_begin = dict(self._pending)  # the held pages as they were, should the transaction roll back
        try:
            yield
        except BaseException:
            self._depth = 0
            self.db.execute("ROLLBACK")
            self._pending = self._pending_at_begin
            self._touch()
            self.engine.reset()
            raise
        self._depth = 0
        self.db.execute("COMMIT")

    def _touch(self) -> None:
        self.revision += 1

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
        return self._one("SELECT * FROM object WHERE id = ?", oid)

    def _link_row(self, lid: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM link WHERE id = ?", lid)

    def _source(self, sid: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM source WHERE id = ?", sid)

    def _market_kept(self, mid: str) -> bool:
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
            if prefix in ("prop", "channel", "note"):
                return "object" if self._object_row(oid) else None
            if prefix == "src":
                if self._source(oid):
                    return "source"
                return "page" if oid in self._pending else None
            table = {"jdg": "judgment", "cmp": "computation", "link": "link", "search": "search",
                     "data": "data"}[prefix]
            return table if self._one(f"SELECT 1 FROM {table} WHERE id = ?", oid) else None
        if schema.PROXY_METRIC_RE.match(oid):
            return "proxy_metric" if self._proxy_exists(oid) else None
        if schema.METRIC_ID_RE.match(oid):
            return "object" if self._object_row(oid) else None
        if schema.MARKET_ID_RE.match(oid):
            return "market" if self._market_kept(oid) else None
        if schema.NAME_ID_RE.match(oid):
            return "name" if self._name_exists(oid) else None
        if schema.DERIVED_LINK_RE.match(oid):
            return "derived_link" if self._derived_link(oid) else None
        return None

    _KIND_TYPE = {"market": "Market", "name": "Name", "proxy_metric": "Metric", "source": "Source",
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
        for r in self._all("SELECT relation, to_id FROM link WHERE to_id LIKE 'name:%'"):
            reasons.setdefault(r["to_id"], set()).add("held" if r["relation"] == "holds" else "named")
        if produced:
            products = self.engine.produced()
            for oid, rec in products["objects"].items():
                if rec["type"] == "Name":
                    reasons.setdefault(oid, set())
            for rec in products["links"].values():
                if rec["relation"] == "looks_through_to":
                    reasons.setdefault(rec["to"], set()).add("looked through")
        return {k: sorted(v) for k, v in sorted(reasons.items())}

    def _looked_through(self, nid: str) -> bool:
        return any(rec["relation"] == "looks_through_to" and rec["to"] == nid
                   for rec in self.engine.produced()["links"].values())

    def _portfolio_name(self, nid: str) -> bool:
        """Held, or looked through to."""
        if self._one("SELECT 1 FROM link WHERE relation = 'holds' AND to_id = ?", nid):
            return True
        return self._looked_through(nid)

    def _name_exists(self, nid: str) -> bool:
        if self._one("SELECT 1 FROM link WHERE to_id = ? LIMIT 1", nid):
            return True
        rec = self.engine.produced()["objects"].get(nid)
        return bool(rec and rec["type"] == "Name")

    # Proxy Metrics: a channel's proxy price, kept while a proposition or a link names it.

    def _proxy_channels(self, ticker: str) -> list[str]:
        return [r["id"] for r in self._all(
            "SELECT id FROM object WHERE type = 'Channel' AND stated ->> '$.proxy_ticker' = ? ORDER BY rowid", ticker)]

    def _proxy_named(self, mid: str) -> bool:
        if self._one("SELECT 1 FROM object WHERE type = 'Proposition' AND stated ->> '$.metric' = ? "
                     "LIMIT 1", mid):
            return True
        return self._one("SELECT 1 FROM link WHERE from_id = ? OR to_id = ? LIMIT 1", mid, mid) is not None

    def _priced_name(self, ticker: str) -> str | None:
        """The portfolio's Name whose price metric:price:{ticker} is: a name held or looked through to, not cash or
        a fund's residual."""
        nid = schema.name_id(ticker)
        return nid if not pseudo(ticker) and self._portfolio_name(nid) else None

    def _exposed(self) -> set[str]:
        """The names the portfolio holds dollars in, e not zero: a fund the store looks through has e 0.
        None are known until the look-through has a value."""
        try:
            self.engine.registry.get("lookthrough")
        except KeyError:  # a registry without the look-through derives no e
            return set()
        found = self.engine.value("lookthrough", "portfolio").output if self._kind("portfolio") else None
        return {n for n, dollars in ((found or {}).get("e") or {}).items() if dollars}

    def _tracked_name(self, ticker: str) -> str | None:
        """The name metric:price:{ticker} tracks: the portfolio's name of that ticker, when its e is not zero."""
        nid = self._priced_name(ticker)
        return nid if nid is not None and nid in self._exposed() else None

    def _portfolio_names(self) -> list[str]:
        return [n for n, why in self._name_reasons().items() if not pseudo(n.split(":", 1)[1])
                and ("held" in why or "looked through" in why)]

    def _proxy_exists(self, mid: str) -> bool:
        m = schema.PROXY_METRIC_RE.match(mid)
        return bool(m) and (bool(self._proxy_channels(m.group(1))) or self._proxy_named(mid)
                            or self._priced_name(m.group(1)) is not None)

    def _proxy_metrics(self) -> list[str]:
        ids = {schema.proxy_metric_id(r["t"]) for r in self._all(
            "SELECT stated ->> '$.proxy_ticker' AS t FROM object WHERE type = 'Channel'")}
        ids.update(schema.proxy_metric_id(n.split(":", 1)[1]) for n in self._portfolio_names())  # each name's price
        for r in self._all("SELECT stated ->> '$.metric' AS m FROM object WHERE type = 'Proposition'"):
            if r["m"] and schema.PROXY_METRIC_RE.match(r["m"]):
                ids.add(r["m"])
        for r in self._all("SELECT from_id, to_id FROM link WHERE from_id LIKE 'metric:price:%' "
                           "OR to_id LIKE 'metric:price:%'"):
            for x in (r["from_id"], r["to_id"]):
                if schema.PROXY_METRIC_RE.match(x):
                    ids.add(x)
        return sorted(ids)

    # Derived links: the proxy's tracks link follows from the channel; the rest from producers.

    def _structural_tracks(self) -> dict[str, dict]:
        out = {}
        for r in self._all("SELECT id, stated ->> '$.proxy_ticker' AS t FROM object WHERE type = 'Channel' "
                           "ORDER BY rowid"):
            mid = schema.proxy_metric_id(r["t"])
            out[schema.derived_link_id("tracks", mid, r["id"])] = {
                "relation": "tracks", "from": mid, "to": r["id"], "conversion": "price", "fields": {}}
        exposed = self._exposed()
        for nid in self._portfolio_names():  # each name's price tracks the name, when its e is not zero
            if nid not in exposed:
                continue
            mid = schema.proxy_metric_id(nid.split(":", 1)[1])
            out[schema.derived_link_id("tracks", mid, nid)] = {
                "relation": "tracks", "from": mid, "to": nid, "conversion": "price", "fields": {}}
        return out

    def _derived_links(self) -> dict[str, dict]:
        links = self._structural_tracks()
        links.update(self.engine.produced()["links"])
        return links

    def _derived_link(self, lid: str) -> dict | None:
        m = schema.DERIVED_LINK_RE.match(lid)
        if not m:
            return None
        if m.group(1) == "tracks":
            return self._structural_tracks().get(lid)
        return self.engine.produced()["links"].get(lid)

    # ------------------------------------------------------------------ reads for the engine

    def _link_dict(self, r: sqlite3.Row) -> dict:
        d = {"id": r["id"], "relation": r["relation"], "from": r["from_id"], "to": r["to_id"],
             "stated": json.loads(r["stated"])}
        if r["field"] is not None:
            d["field"] = r["field"]
        return d

    def links_of(self, oid: str, relation: str | None = None, direction: str = "out") -> list[dict]:
        """Stated links of an object; citations only when `relation` is sourced_by."""
        where = {"out": "from_id = ?", "in": "to_id = ?", "both": "(from_id = ? OR to_id = ?)"}[direction]
        args: list[Any] = [oid] if direction != "both" else [oid, oid]
        if relation is None:
            where += " AND relation <> 'sourced_by'"
        else:
            where += " AND relation = ?"
            args.append(relation)
        return [self._link_dict(r) for r in self._all(f"SELECT * FROM link WHERE {where} ORDER BY rowid", *args)]

    def link_by_id(self, lid: str) -> dict | None:
        r = self._link_row(lid)
        return self._link_dict(r) if r else None

    def citations_of(self, oid: str) -> dict[str, str]:
        return {r["field"]: r["to_id"] for r in self._all(
            "SELECT field, to_id FROM link WHERE relation = 'sourced_by' AND from_id = ? ORDER BY field", oid)}

    def watched(self, pid: str) -> bool:
        return self._one("SELECT 1 FROM link WHERE relation = 'watches' AND to_id = ?", pid) is not None

    def read_by_derivation(self, pid: str) -> bool:
        """Whether a proposition's derivation reads pid."""
        return self._one("SELECT 1 FROM object o, json_each(o.stated, '$.derivation.reads') r WHERE o.type = "
                         "'Proposition' AND r.value = ? LIMIT 1", pid) is not None

    def ids_of(self, kind: str) -> list[str]:
        """The instances a producer on `kind` runs over: stated objects, kept markets, the
        Names held or named, or the stated links of a relation."""
        if kind in MUTABLE_OBJECT_TYPES:
            ids = [r["id"] for r in self._all("SELECT id FROM object WHERE type = ? ORDER BY rowid", kind)]
            return ids + (self._proxy_metrics() if kind == "Metric" else [])
        if kind == "Market":
            return [r["id"] for r in self._all("SELECT id FROM market ORDER BY rowid")]
        if kind == "Name":
            return list(self._name_reasons(produced=False))
        if kind in schema.RELATIONS:
            return [r["id"] for r in self._all("SELECT id FROM link WHERE relation = ? ORDER BY rowid", kind)]
        return []

    def data(self, kind: str, subject: str, *, covering: tuple[str, str] | None = None,
             required: bool = True, at: str | None = None) -> dict | None:
        """The newest data of `kind` for `subject` covering the range, fetched by the
        configured client when the store has none; with `at`, the reading the store held at that moment (see
        _find_data)."""
        row = self._find_data(kind, subject, covering, at)
        if row is not None:
            return row
        span = f" over {covering[0]} to {covering[1]}" if covering else ""
        if schema.MARKET_ID_RE.match(subject) and not self._market_kept(subject):
            raise Unavailable(f"{subject} is not in the store: a market's book and history are read once a link or a "
                              f"note names it")
        what = DATA_WORDS.get(kind, "the data for {}").format(subject) + span
        fetcher = self.fetchers.get(kind)
        if fetcher is None:
            if required:
                self.engine.wanting()
                raise Unavailable(f"the store does not have {what}, and cannot read it here")
            return None
        try:
            got = fetcher(subject, covering)
        except Unavailable:
            raise
        except Exception as e:  # a client's failure is the value's reason, not the read's
            raise Unavailable(f"{what} could not be read just now; the next read tries again") from e
        did = self.put_data(kind, subject, got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                            range=got.get("range"))
        return self._data_row(did)

    def prefetch(self, kind: str, subjects: Iterable[str], covering: tuple[str, str] | None = None,
                 workers: int = PREFETCH_WORKERS, stage: dict | None = None) -> int:
        """Fetch, side by side, the data of `kind` the store lacks for these subjects, and keep it as data rows,
        written in the order asked on this thread. What a derivation then reads is found as if fetched one by one;
        a fetch that fails here is fetched again as it is read, which records why. Returns how many were kept.
        With `stage`, each fetch is reported as it completes, `done` of `of`."""
        fetcher = self.fetchers.get(kind)
        todo = [s for s in dict.fromkeys(subjects)
                if not schema.MARKET_ID_RE.match(s) and self._find_data(kind, s, covering) is None]
        if fetcher is None or not todo:
            return 0
        if stage is not None:
            self.report({**stage, "kind": kind, "done": 0, "of": len(todo)})

        def fetch(subject: str):
            try:
                return fetcher(subject, covering)
            except Exception:
                return None

        got: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
            futures = {pool.submit(fetch, s): s for s in todo}
            for future in as_completed(futures):
                got[futures[future]] = future.result()
                if stage is not None:
                    self.report({**stage, "kind": kind, "subject": futures[future], "done": len(got),
                                 "of": len(todo)})
        kept = 0
        for subject in todo:  # written in the order asked
            g = got[subject]
            if g is None:
                continue
            try:
                self.put_data(kind, subject, g["payload"], g["request"], retrieved_at=g["retrieved_at"],
                              range=g.get("range"))
                kept += 1
            except StoreError:
                continue
        return kept

    def _find_data(self, kind: str, subject: str, covering, at: str | None = None) -> dict | None:
        """The newest data of `kind` for `subject` covering the range. With `at`, a moment, the reading the store
        held then: the newest retrieved at or before it, or, where none was yet, the first, the reading that first
        served it; `at` empty is before every reading, so it gives the first."""
        found: list[dict] = []
        for r in self._all("SELECT id FROM data WHERE kind = ? AND subject = ? ORDER BY rowid DESC", kind, subject):
            row = self._data_row(r["id"])
            if covering is not None:
                lo, hi = covering
                if row["range_from"] is None or row["range_to"] is None \
                        or row["range_from"] > lo or row["range_to"] < hi:
                    continue
            if at is None:
                return row
            if at and datetime.fromisoformat(row["retrieved_at"]) <= datetime.fromisoformat(at):
                return row
            found.append(row)
        return found[-1] if found else None

    def latest(self, kind: str, subject: str, covering) -> dict | None:
        """The newest data of `kind` for `subject` covering the range, or None, fetching nothing: what a reading
        taken again is compared with."""
        return self._find_data(kind, subject, covering)

    def data_subjects(self, kind: str, covering) -> list[str]:
        """The subjects the store holds data of `kind` for, covering the range, in the order first read."""
        lo, hi = covering
        return [r["subject"] for r in self._all(
            "SELECT subject FROM data WHERE kind = ? AND range_from <= ? AND range_to >= ? GROUP BY subject "
            "ORDER BY min(rowid)", kind, lo, hi)]

    def given_at(self, lid: str) -> str | None:
        """When a holding was given: its link's created_at, which an edit to its figure sets again."""
        r = self._link_row(lid)
        return r["created_at"] if r is not None else None

    def _data_row(self, did: str) -> dict:
        r = self._one("SELECT id, kind, subject, range_from, range_to, request, retrieved_at, sha256, market_id "
                      "FROM data WHERE id = ?", did)
        d = dict(r)
        d["request"] = json.loads(d["request"])
        return d

    def data_payload(self, did: str) -> Any:
        return json.loads(self._one("SELECT payload FROM data WHERE id = ?", did)["payload"])

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
        return {"id": r["id"], "output": json.loads(r["output"])} if r else None

    def computation_output(self, cid: str) -> Any:
        return json.loads(self._one("SELECT output FROM computation WHERE id = ?", cid)["output"])

    def put_computation(self, name: str, version: int, subject: str, h: str, manifest: dict, output: dict) -> str:
        with self.transaction():
            cid = self._next_id("cmp")
            self.db.execute(
                "INSERT INTO computation (id, derivation, version, subject, input_hash, inputs, output, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, name, version, subject, h, canonical(manifest), canonical(output), _now()),
            )
        return cid

    def judgment_by_hash(self, h: str, models: tuple[str, ...] | None = None) -> dict | None:
        """The Judgment answered under this key by one of the models, the latest; any model when
        none is named (a store replayed without its judge)."""
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
        """A fetched page, held and not kept, citable under its id."""
        self._pending[sid] = {k: page[k] for k in ("url", "retrieved_at", "dated", "text", "sha256")}

    def put_data(self, kind: str, subject: str, payload: Any, request: dict, *, retrieved_at,
                 range: tuple[str, str] | None = None) -> str:
        """Data fetched to derive values. A market's data is kept with the market."""
        if not isinstance(kind, str) or not kind or not isinstance(subject, str) or not subject:
            raise StoreError("data has a kind and a subject")
        if not isinstance(request, dict):
            raise StoreError("data records its request as an object: the endpoint and its parameters")
        self._refuse_credentials(request)
        self._refuse_credentials(payload, names=False)
        try:
            payload_text = canonical(payload)
            request_text = canonical(request)
        except (TypeError, ValueError) as e:
            raise StoreError(f"data is JSON: {e}") from None
        retrieved = _check("data", schema.Field("retrieved_at", schema.DATETIME, required=True), retrieved_at)
        if range is not None:
            lo, hi = (_check("data", schema.Field("range", schema.DATE, required=True), x) for x in range)
        else:
            lo = hi = None
        market_id = subject if schema.MARKET_ID_RE.match(subject) else None
        with self.transaction():
            if market_id and not self._market_kept(market_id):
                raise StoreError(f"{market_id} is not kept; a market's data is kept with the market")
            did = self._insert_data(kind, subject, payload_text, request_text, retrieved, lo, hi, market_id)
        return did

    def _insert_data(self, kind, subject, payload_text, request_text, retrieved, lo, hi, market_id) -> str:
        did = self._next_id("data")
        self.db.execute(
            "INSERT INTO data (id, kind, subject, range_from, range_to, request, retrieved_at, payload, sha256, "
            "market_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, kind, subject, lo, hi, request_text, retrieved, payload_text, text_hash(payload_text), market_id,
             _now()),
        )
        self._touch()
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
        """The record of one search: the venue, endpoint, and parameters (or the query and
        parameters) with what it returned. Results are kept on the search and nowhere else."""
        if tool == "search_markets":
            if venue not in schema.VENUES or not isinstance(endpoint, str) or not endpoint or query is not None:
                raise StoreError(f"a market search records its venue ({', '.join(schema.VENUES)}) and endpoint")
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
            self._touch()
        return sid

    # ------------------------------------------------------------------ add

    def add(self, type: str, fields: dict, *, call: int | None = None) -> dict:
        """Create an object of an analyst type from its stated fields."""
        oid = self._add(type, fields, origin="analyst", call=call)
        return self.get(oid)

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
        with self.transaction():
            oid = self._new_object_id(t, values)
            self._check_references(t, values)
            if type_name == "Portfolio":
                self._fix_asof(values["asof"])
            if type_name == "Channel":
                self._check_channel_unique(oid, values)
            if type_name == "Proposition":
                values = self._assumptions(oid, values)
            for sid in cites.values():
                self._citable(sid)
            self._insert_object(oid, type_name, values, origin, call)
            for field, sid in cites.items():
                self._cite(oid, field, sid, origin, call)
            if type_name == "Proposition":
                self._cite_basis(oid, "Proposition", values, call)
            for ticker, amount in holdings:
                self._insert_link("holds", "portfolio", schema.name_id(ticker), amount, origin, call)
            if type_name == "Channel":
                self._check_consequence_channels(new_metric=schema.proxy_metric_id(values["proxy_ticker"]))
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
            r = self._one(f"SELECT id FROM object WHERE type = 'Channel' AND stated ->> '$.{key}' = ? AND id <> ?",
                          values[key], oid)
            if r:
                raise StoreError(f"{r['id']} has {key} {values[key]}; each channel's {key} is its own", [r["id"]])

    def _holdings(self, raw: Any, values: dict) -> list[tuple[str, dict]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise StoreError("holdings is a list of {ticker, and one of quantity, dollars, or weight}")
        out, seen = [], set()
        holds = schema.RELATIONS["holds"]
        for h in raw:
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
            "INSERT INTO object (id, type, stated, origin, created_call, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (oid, type_name, canonical(values), origin, call, _now()),
        )
        self._touch()

    # ------------------------------------------------------------------ citations

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
        page = self._pending.pop(sid, None)
        if page is None:
            raise StoreError(f"{sid} is not a fetched page; fetch_source(url) fetches one")
        self.db.execute(
            "INSERT INTO source (id, url, retrieved_at, dated, text, sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, page["url"], page["retrieved_at"], page["dated"], page["text"], page["sha256"], _now()),
        )
        self._touch()

    def _cite(self, oid: str, field: str, sid: str, origin: str, call: int | None) -> str | None:
        """Cite `sid` for oid.field; returns the page it cited before, if another."""
        self._keep_page(sid)
        existing = self._one("SELECT id, to_id FROM link WHERE relation = 'sourced_by' AND from_id = ? AND field = ?",
                             oid, field)
        if existing is None:
            self._insert_link("sourced_by", oid, sid, {}, origin, call, field=field)
            return None
        if existing["to_id"] == sid:
            return None
        self.db.execute("UPDATE link SET to_id = ? WHERE id = ?", (sid, existing["id"]))
        self._touch()
        return existing["to_id"]

    # ------------------------------------------------------------------ link and note

    def link(self, from_id: str, relation: str, to_id: str, fields: dict | None = None, *,
             call: int | None = None) -> dict:
        """Create a link by the link table; returns it with its derived fields."""
        lid = self._link(from_id, relation, to_id, fields if fields is not None else {}, origin="analyst", call=call)
        return self.get(lid)

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
        with self.transaction():
            ft = self.type_of(from_id) if isinstance(from_id, str) else None
            if ft is None:
                raise StoreError(f"{from_id} is not in the store")
            if ft not in r.sources:
                raise StoreError(f"{relation} runs from {_or(r.sources)}; {from_id} is a {ft}")
            tt, entering = self._resolve_target(r, to_id, prefetched)
            if r.targets != ("*",) and tt not in r.targets:
                raise StoreError(f"{relation} runs to {_or(r.targets)}; {to_id} is a {tt}")
            existing = self._one("SELECT id FROM link WHERE relation = ? AND from_id = ? AND to_id = ?",
                                 relation, from_id, to_id)
            if existing:
                raise StoreError(f"{existing['id']} links {from_id} {relation} {to_id}; edit it instead",
                                 [existing["id"]])
            if relation == "holds" and "weight" in values:
                self._require_total()
            if relation == "then":
                self._check_then_step(from_id, values["step"])
            if relation == "evidence":
                self._check_passages(to_id, values["passages"])
            if relation == "consequence":
                self._check_meaning(to_id, values["meaning"])
            if entering == "market":
                self._enter_market(to_id, prefetched, call)
            elif entering == "page":
                self._keep_page(to_id)
            lid = self._insert_link(relation, from_id, to_id, values, origin, call)
            if relation == "consequence":
                self._cite_basis(lid, "consequence", values, call)
            if relation == "watches":
                self._check_horizon()
            if relation == "consequence":
                self._check_consequence_channels(new_metric=to_id)
            if relation == "tracks":
                self._check_consequence_channels(new_metric=from_id)
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
                values[f.name] = f.default
            if f.required and values.get(f.name) is None:
                raise StoreError(f"{r.name} requires {f.name}" + (f": {f.doc}" if f.doc else ""))
        try:
            schema.check_link(r.name, values)
        except schema.Invalid as e:
            raise StoreError(f"{r.name}: {e}") from None
        return values

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
        self._touch()
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
            if r.name == "holds":
                return "Name", None
            if self._name_exists(to_id):
                return "Name", None
            raise StoreError(f"{to_id} is not in the store: a Name is held, looked through to, or named by a link")
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
            raise StoreError(f"{to_id} is not in the store")
        return t, None

    def _insert_link(self, relation: str, from_id: str, to_id: str, values: dict, origin: str,
                     call: int | None, field: str | None = None) -> str:
        lid = self._next_id("link")
        self.db.execute(
            "INSERT INTO link (id, relation, from_id, to_id, field, stated, origin, created_call, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, relation, from_id, to_id, field, canonical(values), origin, call, _now()),
        )
        self._touch()
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
        return self.get(nid)

    # ------------------------------------------------------------------ edit

    def edit(self, id: str, field: str, value: Any, source_id: str | None = None, *,
             call: int | None = None) -> dict:
        """Change one stated field; a fact about the world takes a source_id."""
        with self.transaction():
            kind = self._kind(id)
            if kind == "object":
                self._edit_object(id, field, value, source_id, call)
            elif kind == "link":
                self._edit_link(id, field, value, source_id)
            elif kind in ("source", "page"):
                self._edit_dated(id, field, value, source_id)
            else:
                raise StoreError(self._not_editable(id, kind))
        return self.get(id)

    def _not_editable(self, oid: str, kind: str | None) -> str:
        return {
            None: f"{oid} is not in the store",
            "name": f"{oid} is a Name; it follows from holdings and look-through: edit the holds link instead",
            "proxy_metric": f"{oid} is a price the store derives: a channel's proxy, or a name of the portfolio",
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
            self._pending[sid] = {**self._pending[sid], "dated": dated}
        self._touch()

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
                raise StoreError(f"{t.name}.{field} is a stated assumption: its basis rides in its value, "
                                 f"{schema.BASIS_SHAPE}, or is a note about {oid}")
            if source_id is not None:
                raise StoreError(f"{t.name}.{field} is not a fact about the world; it takes no source")
            v = _check(t.name, f, value)
            if v is None and f.default is not None:
                v = list(f.default) if isinstance(f.default, tuple) else f.default
            if v is None:
                new.pop(field, None)
            else:
                new[field] = v
        self._check_object(t.name, new)
        if f.kind == schema.REF:
            self._check_references(t, new)
        if t.name == "Proposition" and field in ("derivation", "chance"):
            new = self._assumptions(oid, new)
        if t.name == "Metric" and field == "traded" and not new.get("traded"):
            self._refuse_untraded(oid)
        if t.name == "Channel" and field in ("name", "order"):
            self._check_channel_unique(oid, new)
        if t.name == "Portfolio" and field == "total" and new.get("total") is None:
            self._refuse_weights_without_total()
        self.db.execute("UPDATE object SET stated = ? WHERE id = ?", (canonical(new), oid))
        self._touch()
        if f.cited:
            if value is None:
                old = self._one("SELECT id, to_id FROM link WHERE relation = 'sourced_by' AND from_id = ? "
                                "AND field = ?", oid, field)
                if old:
                    self.db.execute("DELETE FROM link WHERE id = ?", (old["id"],))
                    self._touch()
                    old_source = old["to_id"]
            else:
                old_source = self._cite(oid, field, source_id, "analyst", call)
            if old_source:
                self._release({old_source})
        if t.name == "Proposition" and field in ("derivation", "chance"):
            self._cite_basis(oid, "Proposition", new, call)
        if (t.name, field) in (("Proposition", "window_end"), ("Portfolio", "horizon_days")):
            self._check_horizon()
        if t.name == "Channel" and field == "proxy_ticker":
            self._check_consequence_channels(new_metric=schema.proxy_metric_id(new["proxy_ticker"]))

    def _edit_link(self, lid: str, field: str, value: Any, source_id: Any) -> None:
        row = self._link_row(lid)
        r = schema.RELATIONS[row["relation"]]
        if r.name == "sourced_by":
            raise StoreError(f"{lid} cites the page for {row['from_id']}.{row['field']}: edit that field with "
                             f"another source")
        f = r.field(field) if isinstance(field, str) else None
        if f is None:
            if field in r.derived:
                raise StoreError(f"{r.name}.{field} is derived; the store computes it")
            have = f"the stated fields {', '.join(x.name for x in r.stated)}" if r.stated else "no stated fields"
            raise StoreError(f"{r.name} has {have}; {field!r} is not one")
        if source_id is not None:
            raise StoreError(f"{r.name}.{field} is a stated assumption: its basis rides in its value, "
                             f"{schema.BASIS_SHAPE}, or is a note about {lid}" if f.kind in schema.BASED else
                             f"{r.name}.{field} is not a fact about the world; it takes no source")
        v = _check(r.name, f, value)
        if r.name == "holds":
            if v is None:
                raise StoreError("a holding gives exactly one of quantity, dollars, or weight")
            new = {field: v}
            if field == "weight":
                self._require_total()
        else:
            new = dict(json.loads(row["stated"]))
            if v is None:
                if f.required:
                    raise StoreError(f"{r.name} requires {field}")
                new.pop(field, None)
                if f.default is not None:
                    new[field] = f.default
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
        if r.name == "consequence":
            self._check_meaning(row["to_id"], new["meaning"])
        if r.name == "holds":  # a holding's figure edited is the holding given again, at this moment
            self.db.execute("UPDATE link SET stated = ?, created_at = ? WHERE id = ?", (canonical(new), _now(), lid))
        else:
            self.db.execute("UPDATE link SET stated = ? WHERE id = ?", (canonical(new), lid))
        self._touch()
        if r.name == "consequence":
            self._cite_basis(lid, "consequence", new, None)

    # ------------------------------------------------------------------ rules across objects

    def read_links(self, oid: str) -> list[dict]:
        """A proposition's links, both directions: what the breakdown's logic reads."""
        return self.links_of(oid, direction="both")

    def _assumptions(self, oid: str, values: dict) -> dict:
        """A proposition's derivation, its chances' omitted `of` the proposition itself, naming propositions in the
        store, fitting one rule of the point by the links as they stand, and reading in no cycle."""
        d = values.get("derivation")
        if d is None:
            return values
        d = {**d, "chances": [{**c, "of": c["of"] or oid} for c in d["chances"]]}
        for x in sorted(_named(d)):
            if x != oid and self.type_of(x) != "Proposition":
                raise StoreError(f"Proposition.derivation names {x}, which is not a proposition in the store")
        try:
            logic.match(self.read_links, oid, d)
        except logic.NoRule as e:
            raise StoreError(str(e)) from None
        stack, seen = [(r, [oid, r]) for r in d["reads"]], set()
        while stack:
            x, path = stack.pop()
            if x == oid:
                raise StoreError(f"a point cannot read itself: {' reads '.join(path)}", path[1:-1])
            if x in seen:
                continue
            seen.add(x)
            stack += [(r, path + [r]) for r in ((self.stated_of(x) or {}).get("derivation") or {}).get("reads", [])]
        return {**values, "derivation": d}

    @staticmethod
    def _basis_cites(kind: str, values: dict) -> dict[str, dict]:
        """Each stated assumption cited from a page, by the field it states: a proposition's chance, each chance of
        its derivation, a consequence's figure."""
        out = {}
        if kind == "Proposition":
            if (values.get("chance") or {}).get("source_id"):
                out["chance"] = values["chance"]
            for i, c in enumerate((values.get("derivation") or {}).get("chances") or []):
                if c.get("source_id"):
                    out[f"derivation.chances[{i}]"] = c
        elif (values.get("value") or {}).get("source_id"):
            out["value"] = values["value"]
        return out

    def _cite_basis(self, owner: str, kind: str, values: dict, call: int | None) -> None:
        """Each assumption cited from a page cites it, its passages in the page word for word; a page whose last
        citation goes is released."""
        cites = self._basis_cites(kind, values)
        for c in cites.values():
            self._citable(c["source_id"])
            self._check_passages(c["source_id"], c["passages"])
        fields = ("chance", "derivation") if kind == "Proposition" else ("value",)
        dropped = set()
        for r in self._all("SELECT id, field, to_id FROM link WHERE relation = 'sourced_by' AND from_id = ?", owner):
            if r["field"].split(".")[0] in fields and r["field"] not in cites:
                self.db.execute("DELETE FROM link WHERE id = ?", (r["id"],))
                self._touch()
                dropped.add(r["to_id"])
        for key, c in cites.items():
            old = self._cite(owner, key, c["source_id"], "analyst", call)
            if old:
                dropped.add(old)
        self._release(dropped - set(self.citations_of(owner).values()))

    def _traded(self, mid: str) -> bool:
        if schema.PROXY_METRIC_RE.match(mid):
            return True
        return bool((self.stated_of(mid) or {}).get("traded"))

    def _check_meaning(self, mid: str, meaning: str) -> None:
        """A level places a world by the pricing identity, which a traded metric's current price anchors; a metric no
        market prices states the gap."""
        if meaning != "gap" and not self._traded(mid):
            raise StoreError(f"{mid} is not traded: a metric no market prices states the gap, meaning \"gap\"")

    def _refuse_untraded(self, mid: str) -> None:
        levels = [r["id"] for r in self._all("SELECT id FROM link WHERE relation = 'consequence' AND to_id = ? AND "
                                             "stated ->> '$.meaning' <> 'gap' ORDER BY rowid", mid)]
        if levels:
            raise StoreError(f"{mid} has consequences stating its level, which only a traded metric's do: state "
                             f"their gaps first", levels)

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
                raise StoreError(f"{r['to_id']} ends {end}, past the horizon {limit} "
                                 f"(asof {p['asof']} plus {p['horizon_days']} days)", [r["to_id"]])

    def _tracked_by(self, mid: str) -> list[str]:
        """The channels and names a metric tracks: its stated tracks links, and for a price metric the channels
        whose proxy it is and the name whose price it is, when that name's e is not zero."""
        targets = {r["to_id"] for r in self._all("SELECT to_id FROM link WHERE relation = 'tracks' AND from_id = ?",
                                                 mid)}
        m = schema.PROXY_METRIC_RE.match(mid)
        if m:
            targets.update(self._proxy_channels(m.group(1)))
            named = self._tracked_name(m.group(1))
            if named:
                targets.add(named)
        return sorted(targets)

    def _check_consequence_channels(self, new_metric: str | None = None) -> None:
        """An event's consequences set each channel, and each name, through one metric."""
        events: dict[str, list[str]] = {}
        for r in self._all("SELECT from_id, to_id FROM link WHERE relation = 'consequence' ORDER BY rowid"):
            events.setdefault(r["from_id"], []).append(r["to_id"])  # a consequence links the event to its metric
        for event, metrics in events.items():
            setter: dict[str, str] = {}
            for m in sorted(set(metrics), key=lambda x: (x == new_metric, x)):
                for c in self._tracked_by(m):
                    if c in setter and setter[c] != m:
                        what = f"name {c}" if c.startswith("name:") else \
                            f"channel {self.stated_of(c).get('name', c)} ({c})"
                        raise StoreError(f"{event} would set {what} through {m}, and {setter[c]} already sets it: an "
                                         f"event's consequences set each channel and each name through one metric",
                                         [setter[c]])
                    setter[c] = m

    # ------------------------------------------------------------------ remove

    def remove(self, id: str, *, call: int | None = None) -> dict:
        """Remove an object or link the analyst created or received as initial contents,
        with its links; refuse what is derived, a market, and what would strand a stated
        fact or reference. Returns the record of what went."""
        with self.transaction():
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
                    f"kept while a channel's proxy or the portfolio's name is its ticker, or a proposition or link "
                    f"names it", of)
        if kind == "derived_link":
            return f"{oid} is derived; it follows from structure and data", []
        if kind in ("computation", "judgment"):
            return (f"{oid} follows from structure: once what asked for it goes, nothing reads it as a number, and it "
                    f"stays to answer the same question again", [])
        if kind == "market":
            return (f"{oid} comes from its venue and is kept while a link or a note uses it; remove its links: "
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
                f"SELECT id FROM link WHERE (relation = 'about' AND to_id IN ({marks})) OR (relation = 'sourced_by' "
                f"AND from_id IN ({marks})) ORDER BY rowid", *ids, *ids)]
            self.db.execute(f"DELETE FROM link WHERE id IN ({marks})", ids)
            self._touch()
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
        if row["relation"] == "sourced_by":
            fact = f"{row['from_id']}.{row['field']}"
            raise StoreError(f"{lid} cites the page for {fact}, and a stated fact keeps its source: edit the field "
                             f"with another source, or remove {row['from_id']}", [fact])
        deleted = self._delete_links([lid])
        released = self._release({row["from_id"], row["to_id"]} | {d["to"] for d in deleted})
        return {"removed": deleted[0], "cascaded": deleted[1:], "released": released}

    def _references_to(self, oid: str) -> list[str]:
        refs = [f"{r['id']}.derivation" for r in self._all(
            "SELECT id, stated FROM object WHERE type = 'Proposition' AND stated ->> '$.derivation' IS NOT NULL "
            "ORDER BY rowid") if r["id"] != oid and oid in _named(json.loads(r["stated"])["derivation"])]
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
        if refs:
            raise StoreError(f"{oid} is named by {', '.join(refs)}; edit or remove those first", refs)
        links = [r["id"] for r in self._all("SELECT id FROM link WHERE from_id = ? OR to_id = ? ORDER BY rowid",
                                            oid, oid)]
        searches = [r["id"] for r in self._all("SELECT id FROM search WHERE proposition_id = ?", oid)]
        deleted = self._delete_links(links + self._about(searches))
        self.db.execute("DELETE FROM search WHERE proposition_id = ?", (oid,))
        self.db.execute("DELETE FROM object WHERE id = ?", (oid,))
        self._touch()
        ends = {d["to"] for d in deleted} | {d["from"] for d in deleted}
        released = self._release(ends - {oid})
        record = {"removed": {"id": oid, "type": row["type"], "stated": json.loads(row["stated"])},
                  "cascaded": deleted, "released": released}
        if searches:
            record["searches"] = searches
        return record

    def _remove_source(self, sid: str) -> dict:
        row = self._source(sid)
        cites = [f"{r['from_id']}.{r['field']}" for r in self._all(
            "SELECT from_id, field FROM link WHERE relation = 'sourced_by' AND to_id = ? ORDER BY rowid", sid)]
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
        self._touch()

    def _release(self, ids: Iterable[str]) -> list[dict]:
        """Markets and pages that lost their last use are not kept. A market's fetched data stays, as all
        fetched data does, so a checkpoint that kept the market reads it again; a page is again a fetched page,
        held under its id and citable."""
        released: list[dict] = []
        for x in sorted(i for i in ids if isinstance(i, str)):
            if schema.MARKET_ID_RE.match(x) and self._market_kept(x):
                if self._one("SELECT 1 FROM link WHERE to_id = ? LIMIT 1", x) is None:
                    self.db.execute("DELETE FROM market WHERE id = ?", (x,))
                    self._touch()
                    released.append({"id": x, "type": "Market"})
            elif x.startswith("src:"):
                s = self._source(x)
                if s is None:
                    continue
                if self._one("SELECT 1 FROM link WHERE to_id = ? AND relation IN ('sourced_by', 'evidence') LIMIT 1",
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

    def get(self, id: str, text: str | None = None) -> dict:
        """The object or link with its stated fields, derived fields (each with its basis),
        and links; a page, given text, as the passages that contain it (`find`)."""
        kind = self._kind(id)
        if kind is None:
            raise StoreError(f"{id} is not in the store")
        if text is not None and str(text).strip():
            if kind not in ("source", "page"):
                raise StoreError(f"text finds words in a page, and {id} is not a page: get({id}) returns it whole")
            s = self.source_row(id)
            return {"id": id, "type": "Source", "kept": kind == "source", "url": s["url"], "dated": s["dated"],
                    "text": text, **find(s["text"], str(text))}
        return getattr(self, f"_get_{kind}")(id)

    def _get_object(self, oid: str) -> dict:
        row = self._object_row(oid)
        out = {"id": oid, "type": row["type"], "origin": row["origin"], "stated": json.loads(row["stated"])}
        citations = self.citations_of(oid)
        if citations:
            out["citations"] = citations
        out["derived"] = self.engine.derived(oid, row["type"])
        out["links"] = self._links_view(oid)
        if row["type"] == "Proposition":
            out["searches"] = self._searches_of(oid)
        if row["type"] == "Channel":
            out["proxy_metric"] = schema.proxy_metric_id(out["stated"]["proxy_ticker"])
        return out

    def _get_link(self, lid: str) -> dict:
        r = self._link_row(lid)
        out = {"id": lid, "type": "link", "relation": r["relation"], "from": r["from_id"], "to": r["to_id"],
               "origin": r["origin"], "stated": json.loads(r["stated"])}
        if r["field"] is not None:
            out["field"] = r["field"]
        out["derived"] = self.engine.derived(lid, r["relation"])
        return out

    def _get_derived_link(self, lid: str) -> dict:
        rec = self._derived_link(lid)
        out = {"id": lid, "type": "link", "relation": rec["relation"], "from": rec["from"], "to": rec["to"],
               "derived_link": True}
        if "conversion" in rec:
            out["conversion"] = rec["conversion"]
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
            "SELECT from_id, field FROM link WHERE relation = 'sourced_by' AND to_id = ? ORDER BY rowid", sid)]
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

    def _get_proxy_metric(self, mid: str) -> dict:
        ticker = mid.split(":", 2)[2]
        out = {"id": mid, "type": "Metric", "proxy_of": self._proxy_channels(ticker), "ticker": ticker}
        named = self._priced_name(ticker)
        if named:
            out["price_of"] = named
        return {**out, "stated": {}, "derived": self.engine.derived(mid, "Metric"), "links": self._links_view(mid)}

    def _get_computation(self, cid: str) -> dict:
        r = self._one("SELECT * FROM computation WHERE id = ?", cid)
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
        """A proposition's searches, each once, in the order they ran: those recorded on it, those that
        returned a market it is quoted_by, and those that returned a page it links as evidence, a market usually
        being found before the proposition it bears on is written. Each says what makes it the proposition's:
        `recorded`, the `quoted_by` markets it returned, the `evidence` pages it returned. When the link goes,
        so does what it attributed."""
        found: dict[str, dict] = {}

        def attribute(rows, key: str | None, value) -> None:
            for r in rows:
                item = found.setdefault(r["id"], {"row": r, "recorded": False, "quoted_by": [], "evidence": []})
                if key is None:
                    item["recorded"] = True
                elif value(r) not in item[key]:
                    item[key].append(value(r))

        attribute(self._all("SELECT * FROM search WHERE proposition_id = ?", pid), None, None)
        for link in self.links_of(pid, "quoted_by"):
            attribute(self._all("SELECT DISTINCT s.* FROM search s, json_each(s.results) j WHERE s.tool = "
                                "'search_markets' AND j.value = ?", link["to"]), "quoted_by", lambda r, m=link["to"]: m)
        for link in self.links_of(pid, "evidence"):
            url = (self.source_row(link["to"]) or {}).get("url")
            if url:
                attribute(self._all("SELECT DISTINCT s.* FROM search s, json_each(s.results) j WHERE s.tool IN "
                                    "('search_web', 'search_company') AND CASE WHEN j.type = 'object' "
                                    "THEN j.value ->> '$.url' END = ?", url), "evidence",
                          lambda r, sid=link["to"]: sid)
        out = []
        for item in sorted(found.values(), key=lambda x: _seq_key(x["row"]["id"])):
            d = self._search_dict(item["row"])
            d["recorded"] = item["recorded"]
            d.update({k: item[k] for k in ("quoted_by", "evidence") if item[k]})
            out.append(d)
        return out

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
        for lid, rec in self._derived_links().items():
            if oid not in (rec["from"], rec["to"]):
                continue
            out = rec["from"] == oid
            item = {"id": lid, "relation": rec["relation"], "direction": "out" if out else "in",
                    "other": rec["to"] if out else rec["from"], "derived_link": True}
            if "conversion" in rec:
                item["conversion"] = rec["conversion"]
            if rec.get("fields"):
                item["derived"] = {f: {"value": v, "basis": rec.get("basis")} for f, v in rec["fields"].items()}
            items.append(item)
        return items

    # ------------------------------------------------------------------ query and neighbors

    def _instances(self, type_name: str) -> list[tuple[str, dict]]:
        if type_name in MUTABLE_OBJECT_TYPES:
            rows = [(r["id"], json.loads(r["stated"])) for r in self._all(
                "SELECT id, stated FROM object WHERE type = ? ORDER BY rowid", type_name)]
            if type_name == "Metric":
                rows += [(m, {}) for m in self._proxy_metrics()]
            return rows
        if type_name == "Name":
            return [(n, {"ticker": n.split(":", 1)[1]}) for n in self._name_reasons()]
        if type_name == "Market":
            return [(r["id"], {"venue": r["venue"], "id": r["venue_id"]}) for r in self._all(
                "SELECT id, venue, venue_id FROM market ORDER BY rowid")]
        if type_name == "Source":
            return [(r["id"], self.stated_of(r["id"])) for r in self._all("SELECT id FROM source ORDER BY rowid")]
        if type_name == "Computation":
            return [(r["id"], {"derivation": r["derivation"], "subject": r["subject"], "input_hash": r["input_hash"]})
                    for r in self._all("SELECT id, derivation, subject, input_hash FROM computation ORDER BY rowid")]
        if type_name == "Judgment":
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
                  for lid, rec in self._derived_links().items() if id in (rec["from"], rec["to"])]
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
                self._add("Channel", c, origin="initial", call=None)
                count += 1
        return {"channels": count}
