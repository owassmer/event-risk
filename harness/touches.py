"""What a store's reads read, and what each of its versions changed, named by the same keys.

A version of a store is one commit that changed something its reads can answer, numbered from the store's first
(Store.version). Each read of the store records the keys of what it read, where the read is made (read), into the
recording open in its context, with the state it answered from (recording); each commit touches the keys of every row
it changed, logged by triggers on the writer's connection as the rows change (triggers), so no write can change a row
without touching its keys. A value worked out from reads made at version e is what the store answers at a later version
while none of the keys it read was touched after e (Store.touched_at).

The keys, each a string:

- `id(x)`: the row an id names: an object, a link, a kept market, a Source or a page held, a search, a reading, a
  Judgment. Any change to that row touches it, its making and its removal among them, so a read that found nothing
  under an id is touched when something comes to be named by it. A Computation is read by its content, and has none.
- `links(x, relation, direction)`: the links with `x` at one end, `out` from it or `in` to it, of one relation, or of
  any (`*`).
- `objects(type)` and `relation(name)`: which objects of a type exist (a kept market is a `Market`), and which links
  of a relation: touched where one is made or removed. A read of every one of them records this and the id of each it
  reads, whose own changes touch that.
- `rows(name)`: any change to an object of a type or a link of a relation, for a read that finds them by a field's
  value.
- `ends(kind)`: the links with an end of one kind of id (`name`, `metric`, `market`, ...), for a read that finds links
  by the kind of what they join.
- `table(name)`: any row of a table, for a read that finds rows by a field's value whatever their type or relation.
- `given(x)`: the chances given `x`, true or false: touched where a proposition's chances name `x`, before or after
  the change.
- `data(kind, subject)`: the readings of one kind for one subject; `data_of(kind)`: the subjects read of a kind.
- `judgment(hash)`: the judge's answer to one question.
- `meta(key)`: one of the store's own settings, the as-of among them.
- `TODAY`: New York's date, read by a value whose as-of can still be today (harness.derive.portfolio.is_today).
- `EVERYTHING`: every key at once, which a rewind touches. A store opened holds every key as touched at the version it
  opens at, so nothing worked out before it was opened is taken as current.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Callable, Iterator
from zoneinfo import ZoneInfo

SEP = "\x1f"  # between a key's parts: no id, kind, subject or hash holds it
ANY = "*"  # a key's relation where the read takes links of every relation
TODAY = "today"
EVERYTHING = "*"


def _key(*parts: str) -> str:
    return SEP.join(parts)


def id(x: str) -> str:  # noqa: A001 - the key of an id, read as touches.id
    return _key("id", x)


def links(x: str, relation: str | None, direction: str) -> str:
    return _key("links", x, relation or ANY, direction)


def objects(type_name: str) -> str:
    return _key("objects", type_name)


def relation(name: str) -> str:
    return _key("relation", name)


def rows(name: str) -> str:
    return _key("rows", name)


def ends(kind: str) -> str:
    """The links with an end of one kind of id: what comes before an id's first colon, or the whole of one with none."""
    return _key("ends", kind)


def table(name: str) -> str:
    return _key("table", name)


def given(x: str) -> str:
    return _key("given", x)


def data(kind: str, subject: str) -> str:
    return _key("data", kind, subject)


def data_of(kind: str) -> str:
    return _key("data_of", kind)


def judgment(h: str) -> str:
    return _key("judgment", h)


def meta(key: str) -> str:
    return _key("meta", key)


NY = ZoneInfo("America/New_York")


def today() -> str:
    """New York's date now, which `TODAY` names: the date the as-of is today on."""
    return datetime.now(NY).date().isoformat()


def words(key: str) -> str:
    """A key as a reader of a log reads it."""
    return " ".join(key.split(SEP))


# ---------------------------------------------------------------------------------------------------- what a read read

# The recordings open in this context, innermost last, each the keys its reads read with the stamp of each, and how it
# stamps a read. A context carried into another thread (contextvars.copy_context) carries them, so a read made there
# for the same value is recorded with it.
_open: ContextVar[tuple[tuple[dict[str, tuple[int, int]], Callable[[], tuple[int, int]]], ...]] = ContextVar(
    "recordings", default=())
_answering: ContextVar[frozenset[str]] = ContextVar("answering", default=frozenset())


@contextmanager
def recording(stamp: Callable[[], tuple[int, int]]) -> Iterator[dict[str, tuple[int, int]]]:
    """Every key read in this context while the block runs, wherever in the store the read is made, each with the
    stamp of the state its read answered from, `stamp()` as it answers (Store.stamp: its version, and its step within
    a transaction). A key read more than once keeps its earliest stamp. A recording opened inside another keeps its
    own: what the inner one read is not added to the outer one."""
    reads: dict[str, tuple[int, int]] = {}
    token = _open.set(_open.get() + ((reads, stamp),))
    try:
        yield reads
    finally:
        _open.reset(token)


def read(*keys: str) -> None:
    """A read of the store, by the keys of what it reads, as it answers: recorded with its stamp where a recording
    is open in this context, unless it is part of a read that answers by those keys (answering)."""
    stack = _open.get()
    if not stack:
        return
    quiet = _answering.get()
    reads, stamp = stack[-1]
    keys = [k for k in keys if k not in reads and k not in quiet]
    if keys:
        at = stamp()
        for k in keys:
            reads[k] = at


@contextmanager
def answering(*keys: str) -> Iterator[None]:
    """One read that answers by `keys`, landing what it answers where the store holds none: what it reads of them on
    its way is part of it, and it is recorded once, as it answers, its own landing included."""
    token = _answering.set(_answering.get() | frozenset(keys))
    try:
        yield
    finally:
        _answering.reset(token)
        read(*keys)


# ---------------------------------------------------------------------------------------------- what a write touched

def _sql_key(*parts: str) -> str:
    """A key as an SQL expression of its parts, each an SQL expression: the same string `_key` makes of their values."""
    return " || char(31) || ".join(parts)


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _kind_sql(x: str) -> str:
    return f"CASE WHEN instr({x}, ':') THEN substr({x}, 1, instr({x}, ':') - 1) ELSE {x} END"


def _object_keys(row: str, membership: bool) -> list[str]:
    keys = [_sql_key("'id'", f"{row}.id"), _sql_key("'rows'", f"{row}.type"), _sql_key("'table'", "'object'")]
    if membership:
        keys.append(_sql_key("'objects'", f"{row}.type"))
    return keys


def _given_keys(row: str) -> str:
    """Each proposition or market a row's chances are given, true or false, as a SELECT of the `given` keys."""
    return (f"SELECT {_sql_key(_quoted('given'), 'g.value')} FROM json_each({row}.stated, '$.chances') c, "
            f"json_each(c.value, '$.given') g UNION SELECT {_sql_key(_quoted('given'), 'g.value')} FROM "
            f"json_each({row}.stated, '$.chances') c, json_each(c.value, '$.given_not') g")


def _link_keys(row: str, membership: bool) -> list[str]:
    keys = [_sql_key("'id'", f"{row}.id"), _sql_key("'rows'", f"{row}.relation"), _sql_key("'table'", "'link'"),
            _sql_key("'links'", f"{row}.from_id", f"{row}.relation", "'out'"),
            _sql_key("'links'", f"{row}.from_id", _quoted(ANY), "'out'"),
            _sql_key("'links'", f"{row}.to_id", f"{row}.relation", "'in'"),
            _sql_key("'links'", f"{row}.to_id", _quoted(ANY), "'in'"),
            _sql_key("'ends'", _kind_sql(f"{row}.from_id")), _sql_key("'ends'", _kind_sql(f"{row}.to_id"))]
    if membership:
        keys.append(_sql_key("'relation'", f"{row}.relation"))
    return keys


GUARD = "a write to the store is made inside one of its transactions (Store.transaction)"


def triggers(present: set[str]) -> list[str]:
    """The statements that make, on one connection, the triggers that log each changed row's keys into `temp.touch`,
    for the tables `present` among those whose rows a read can answer from. Each refuses a write made outside a
    transaction of the store's own, whose commit folds the log into a version (Store._transaction)."""
    out = [
        "CREATE TEMP TABLE IF NOT EXISTS touch (n INTEGER PRIMARY KEY, key TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS temp.touch_key ON touch(key)",
        "CREATE TEMP TABLE IF NOT EXISTS writing (open INTEGER NOT NULL)",
    ]
    guard = f"SELECT RAISE(ABORT, {_quoted(GUARD)}) WHERE NOT EXISTS (SELECT 1 FROM writing);"

    def trigger(table_name: str, event: str, keys: list[str], *, selects: list[str] = (), when: str = "") -> None:
        name = f"touch_{table_name}_{event.lower()}"
        values = "".join(f"INSERT INTO touch (key) VALUES ({k});" for k in keys)
        selected = "".join(f"INSERT INTO touch (key) {s};" for s in selects)
        out.append(f"CREATE TEMP TRIGGER IF NOT EXISTS {name} AFTER {event} ON main.{table_name} "
                   f"{('WHEN ' + when) if when else ''} BEGIN {guard} {values} {selected} END")

    if "object" in present:
        trigger("object", "INSERT", _object_keys("new", True), selects=[_given_keys("new")])
        trigger("object", "UPDATE", _object_keys("new", False), selects=[_given_keys("old"), _given_keys("new")])
        trigger("object", "DELETE", _object_keys("old", True), selects=[_given_keys("old")])
    if "link" in present:
        trigger("link", "INSERT", _link_keys("new", True))
        trigger("link", "UPDATE", _link_keys("old", False) + _link_keys("new", False))
        trigger("link", "DELETE", _link_keys("old", True))
    if "market" in present:
        for event, row in (("INSERT", "new"), ("DELETE", "old")):
            trigger("market", event, [_sql_key("'id'", f"{row}.id"), _sql_key("'objects'", "'Market'"),
                                      _sql_key("'table'", "'market'")])
    for t in ("source", "held", "search"):
        if t in present:
            for event, row in (("INSERT", "new"), ("UPDATE", "new"), ("DELETE", "old")):
                trigger(t, event, [_sql_key("'id'", f"{row}.id"), _sql_key("'table'", _quoted(t))])
    if "data" in present:
        trigger("data", "INSERT", [_sql_key("'id'", "new.id"), _sql_key("'data'", "new.kind", "new.subject"),
                                   _sql_key("'data_of'", "new.kind"), _sql_key("'table'", "'data'")])
    if "judgment" in present:
        trigger("judgment", "INSERT", [_sql_key("'id'", "new.id"), _sql_key("'judgment'", "new.input_hash"),
                                       _sql_key("'table'", "'judgment'")])
    if "meta" in present:  # the store's own count of its versions is no setting a read answers from
        for event, row in (("INSERT", "new"), ("UPDATE", "new"), ("DELETE", "old")):
            trigger("meta", event, [_sql_key("'meta'", f"{row}.key")], when=f"{row}.key <> 'version'")
    return out


WATCHED = ("object", "link", "market", "source", "held", "search", "data", "judgment", "meta")
