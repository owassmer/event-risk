"""The store's versions and what each write touched (harness.touches).

A missed touch is a value that stays as it was when what it read has changed, with no error. So the first test here
holds the property itself, on a store holding something of every kind: every read whose answer a write changes read a
key that write touched after the read's stamp. The rest hold the mechanism's parts: which keys each kind of write
touches, a version per commit that changed something and none for one that did not, a rollback touching nothing, steps
inside a transaction, a rewind and an opening touching every key, the day's turn, a read that lands what it answers,
and a minute's readings as one version.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from breakdown import stated
from conftest import ASOF, RETRIEVED, BookFetcher, StubVenues, fixture_judge, registry
from harness import touches
from harness.store import Store, StoreError

PROPOSITION = {"title": "T", "text": "The US effective tariff rate reaches 25% by 2027-03-31",
               "comparison": "occurs", "window_start": ASOF, "window_end": "2027-03-31", "kind": "hitting"}
MARKETS = {"market:kalshi:M1": {"question": "Will the US raise tariffs by March 31?"},
           "market:kalshi:M3": {"question": "Effective tariff rate above 20% on March 31?"}}
BOOKS = {"market:kalshi:M1": {"bid": 0.28, "ask": 0.33}, "market:kalshi:M3": {"bid": 0.30, "ask": 0.90}}


def trade(subject, covering):
    """A pair's latest trade, as its client reads it."""
    return {"payload": {"status": "OK", "price": 97000.0, "time": RETRIEVED}, "request": {"endpoint": "/trade"},
            "retrieved_at": RETRIEVED, "range": covering}


def services():
    return {"judge": fixture_judge(), "venues": StubVenues(MARKETS),
            "fetchers": {"book": BookFetcher(BOOKS), "last_trade": trade}}


def made(tmp_path: Path) -> tuple[Path, dict]:
    """A store holding something of every kind a read answers from, closed: objects of every mutable type, a link of
    each relation the analyst states, chances given propositions and a market, a kept market, a Source cited and one
    held, a search, readings, Judgments, and a checkpoint before the last of them."""
    d = tmp_path / "base"
    store = Store.create(d, registry=registry(), **services())
    ids: dict[str, str] = {}
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "QQQ", "dollars": 40000.0},
                                                       {"ticker": "NVDA", "dollars": 15000.0}]})
    ids["t"] = store.add("Proposition", {**PROPOSITION, "baseline": "no"})["id"]
    ids["r"] = store.add("Proposition", {**PROPOSITION, "title": "R"})["id"]
    ids["u"] = store.add("Proposition", {**PROPOSITION, "title": "U"})["id"]
    ids["implies"] = store.link(ids["t"], "implies", ids["r"])["id"]
    ids["asks"] = store.link(ids["r"], "asks", "market:kalshi:M1", {"side": "yes"})["id"]
    ids["watches"] = store.link("portfolio", "watches", ids["t"])["id"]
    store.edit(ids["t"], "chances", [stated({"given": [ids["r"]], "given_not": [], "value": 0.6})])
    ids["page"] = store.put_page("https://example.org/rate", "The effective rate is 22 percent.",
                                 retrieved_at=RETRIEVED, dated="2026-10-30")
    ids["held"] = store.put_page("https://example.org/other", "Stocks fell 12 percent.", retrieved_at=RETRIEVED)
    store.add("Metric", {"id": "rate_pct", "name": "the US effective tariff rate", "unit": "pct", "kind": "level",
                         "level": {"value": 22, "source_id": ids["page"]}, "resolution_source": "Yale Budget Lab"})
    ids["evidence"] = store.link(ids["t"], "evidence", ids["page"], {"passages": ["22 percent"]})["id"]
    ids["factor"] = store.add("Factor", {"name": "SEMI", "proxy": "SOXX", "order": 6})["id"]
    ids["shock"] = store.link(ids["t"], "consequence", ids["factor"], {"shock": stated(-0.05)})["id"]
    ids["note"] = store.note("why T", [ids["t"]])["id"]
    for subject, price in (("SPY", 760.0), ("SOXX", 300.0)):
        store.put_data("snapshot", subject, {"status": "OK", "price": price}, {"endpoint": "/snapshot"},
                       retrieved_at=RETRIEVED, range=(ASOF, ASOF))
    store.record_search("search_markets", {}, [{"id": "market:kalshi:M3"}], proposition_id=ids["t"],
                        query="tariffs")
    store.put_judgment("asks", "a question", "h-one", [{"ref": None, "content": "x"}], 0.9, "m1", None)
    store.checkpoint("s:1")
    ids["way"] = store.link(ids["t"], "way", ids["u"], {"side": "yes"})["id"]
    store.close()
    return d, ids


ABSENT = ("prop:99", "link:999", "src:99", "market:kalshi:ZZ", "note:99", "factor:99", "cash:EUR", "search:99")
RELATIONS = (None, "implies", "asks", "watches", "evidence", "consequence", "way", "about", "cites", "holds", "worth")


def reads(store: Store, ids: dict) -> dict:
    """Each read a value can make of the store whose answer rests on the store's rows alone, by what it reads. Reads
    whose answer rests on a derived value as well (a name's existence, a proxy's price as a Metric) carry that value's
    own record (the engine's), and stay out."""
    objects = [r[0] for r in store.db.execute("SELECT id FROM object")]
    links = [r[0] for r in store.db.execute("SELECT id FROM link")]
    held = list(store._pending)
    kept = [r[0] for r in store.db.execute("SELECT id FROM source")] + held
    markets = [r[0] for r in store.db.execute("SELECT id FROM market")] + ["market:kalshi:M3"]
    # what is made next, of every kind but a Computation, which is read by its content and never by its id
    coming = [f"{prefix}:{n + 1}" for prefix, n in store.db.execute("SELECT prefix, n FROM seq") if prefix != "cmp"]
    things = sorted(set(objects + links + kept + markets + coming + ["portfolio", "cash:USD", "cash:EUR", "name:SOXX",
                                                                     "name:XOM", *ABSENT]))
    out = {}
    for x in things:
        out[f"describe {x}"] = lambda x=x: store.describe(x) if not x.startswith("name:") else None
        if not x.startswith("name:"):  # whether a name exists rests on the look-through's products as well
            out[f"stated_of {x}"] = lambda x=x: store.stated_of(x)
            out[f"type_of {x}"] = lambda x=x: store.type_of(x)
            for field in ("title", "chances", "level", "shock", "passages", "dated"):
                out[f"stated_at {x} {field}"] = lambda x=x, f=field: store.stated_at(x, f)
        out[f"link_by_id {x}"] = lambda x=x: store.link_by_id(x)
        out[f"given_at {x}"] = lambda x=x: store.given_at(x)
        out[f"source_row {x}"] = lambda x=x: store.source_row(x)
        out[f"watched {x}"] = lambda x=x: store.watched(x)
        out[f"given_by {x}"] = lambda x=x: store.given_by(x)
        out[f"citations_of {x}"] = lambda x=x: store.citations_of(x)
        out[f"read_links {x}"] = lambda x=x: store.read_links(x)
        for rel in RELATIONS:
            for d in ("out", "in", "both"):
                out[f"links_of {x} {rel} {d}"] = lambda x=x, rel=rel, d=d: store.links_of(x, rel, d)
    for kind in ("Portfolio", "Proposition", "Factor", "Note", "Market", *(r for r in RELATIONS if r)):
        out[f"ids_of {kind}"] = lambda k=kind: store.ids_of(k)
    for kind in ("Portfolio", "Proposition", "Factor", "Note", "Market", "Source"):
        out[f"query {kind}"] = lambda k=kind: store.query(k)
    for kind, subject in (("snapshot", "SPY"), ("snapshot", "SOXX"), ("snapshot", "XOM")):  # no client: none fetched
        out[f"data {kind} {subject}"] = lambda k=kind, s=subject: store.data(k, s, required=False)
        out[f"readings {kind} {subject}"] = lambda k=kind, s=subject: store.readings(k, s)
    out["latest last_trade BTC-USD"] = lambda: store.latest("last_trade", "BTC-USD", (ASOF, ASOF))
    for mid in ("market:kalshi:M1", "market:kalshi:M3"):  # a book is read where it is held, nothing fetched
        out[f"latest book {mid}"] = lambda m=mid: store.latest("book", m, None)
        out[f"readings book {mid}"] = lambda m=mid: store.readings("book", m)
    out["data_many snapshot"] = lambda: store.data_many("snapshot", ["SPY", "SOXX", "XOM"], required=False)
    out["data_subjects snapshot"] = lambda: store.data_subjects("snapshot", None)
    for h in ("h-one", "h-two"):
        out[f"judgment {h}"] = lambda h=h: store.judgment_by_hash(h)
    out["meta asof"] = lambda: store.meta("asof")
    out["horizon_end"] = lambda: store.horizon_end()
    return out


def answers(store: Store, every: dict) -> dict:
    """Each read's answer, and the keys it read with their stamps, as a value's gathering records them."""
    out = {}
    for name, read in every.items():
        with touches.recording(store.stamp) as keys:
            try:
                got = ("answer", read())
            except Exception as e:  # a refusal is an answer too: an id that names nothing, a market not kept
                got = ("refused", type(e).__name__, str(e))
        out[name] = (got, dict(keys))
    return out


WRITES = {
    "add a proposition": lambda s, i: s.add("Proposition", {**PROPOSITION, "title": "V"}),
    "add a proposition given others": lambda s, i: s.add("Proposition", {
        **PROPOSITION, "title": "V", "chances": [stated({"given": [i["u"]], "given_not": ["market:kalshi:M1"],
                                                        "value": 0.5})]}),
    "edit a title": lambda s, i: s.edit(i["u"], "title", "U again"),
    "edit chances to be given another": lambda s, i: s.edit(i["t"], "chances", [
        stated({"given": [i["u"]], "given_not": [], "value": 0.4})]),
    "edit chances to be given none": lambda s, i: s.edit(i["t"], "chances", [
        stated({"given": [], "given_not": [], "value": 0.2})]),
    "link an implication": lambda s, i: s.link(i["u"], "implies", i["t"]),
    "link a market, kept with it": lambda s, i: s.link(i["u"], "asks", "market:kalshi:M3", {"side": "no"}),
    "link a page held as evidence, kept as a Source": lambda s, i: s.link(i["u"], "evidence", i["held"],
                                                                          {"passages": ["fell 12 percent"]}),
    "link a shock on a name": lambda s, i: s.link(i["t"], "consequence", "name:NVDA", {"shock": stated(-0.08)}),
    "edit a shock": lambda s, i: s.edit(i["shock"], "shock", stated(-0.06)),
    "edit a page's date": lambda s, i: s.edit(i["page"], "dated", "2026-10-31", i["page"]),
    "cite another page, the first released": lambda s, i: s.edit("metric:rate_pct", "level", 23.0, i["held"]),
    "remove an implication": lambda s, i: s.remove(i["implies"]),
    "remove a market's link, the market released": lambda s, i: s.remove(i["asks"]),
    "remove a proposition": lambda s, i: s.remove(i["u"]),
    "remove a note": lambda s, i: s.remove(i["note"]),
    "write a note": lambda s, i: s.note("about U", [i["u"]]),
    "fetch a page": lambda s, i: s.put_page("https://example.org/new", "New text.", retrieved_at=RETRIEVED),
    "keep a reading of a subject read before": lambda s, i: s.put_data(
        "snapshot", "SPY", {"status": "OK", "price": 761.0}, {"endpoint": "/snapshot"},
        retrieved_at="2026-11-04T15:01:00+00:00", range=(ASOF, ASOF)),
    "keep a reading of a subject not read before": lambda s, i: s.put_data(
        "snapshot", "XOM", {"status": "OK", "price": 120.0}, {"endpoint": "/snapshot"},
        retrieved_at=RETRIEVED, range=(ASOF, ASOF)),
    "a first read through a client": lambda s, i: s.data("last_trade", "BTC-USD", covering=(ASOF, ASOF)),
    "the judge answers": lambda s, i: s.put_judgment("asks", "another", "h-two", [{"ref": None, "content": "y"}],
                                                     0.7, "m1", None),
    "record a search": lambda s, i: s.record_search("search_web", {}, [], proposition_id=i["u"], query="rate"),
    "add a factor": lambda s, i: s.add("Factor", {"name": "OIL", "proxy": "USO", "order": 7}),
    "edit a holding": lambda s, i: s.edit(next(link["id"] for link in s.links_of("portfolio", "holds")
                                               if link["to"] == "name:NVDA"), "dollars", 16000.0),
    "rewind to a checkpoint": lambda s, i: s.rewind("s:1"),
    "hold cash": lambda s, i: _held_cash(s),
    "watch a proposition": lambda s, i: s.link("portfolio", "watches", i["r"]),
    "stop watching one": lambda s, i: s.remove(i["watches"]),
    "edit the horizon": lambda s, i: s.edit("portfolio", "horizon_days", 200),
    "edit an evidence link's passages": lambda s, i: s.edit(i["evidence"], "passages", ["effective rate"]),
}


def _held_cash(store: Store) -> None:
    """A holding of cash, made as the store's own insert makes it."""
    with store.transaction():
        store._insert_link("holds", "portfolio", "cash:EUR", {"cash": 1000.0}, "analyst", None)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return made(tmp_path_factory.mktemp("touches"))


@pytest.mark.parametrize("write", list(WRITES))
def test_every_read_a_write_changes_read_a_key_the_write_touched_after_it(base, tmp_path, write):
    """On its own copy of the store, each kind of write: every read whose answer it changed read some key that the
    write's version touched after the read's stamp, so a value worked out from that read is no longer taken as
    current. A write that changed any answer is a version."""
    d, ids = base
    copy = tmp_path / "copy"
    shutil.copytree(d, copy)
    store = Store.open(copy, registry=registry(), **services())
    try:
        every = reads(store, ids)
        before = answers(store, every)
        version = store.version
        WRITES[write](store, ids)
        after = answers(store, every)
        changed = [name for name in before if before[name][0] != after[name][0]]
        assert changed, f"{write} changed no read: the matrix does not see it"
        assert store.version > version
        missed = [name for name in changed
                  if not any(store.touched_at(k) > at[0] for k, at in before[name][1].items())]
        assert not missed, f"{write} changed these reads, touching nothing they read: {missed}"
    finally:
        store.close()


# ---------------------------------------------------------------------------------------------- the mechanism's parts

def opened(base, tmp_path) -> tuple[Store, dict]:
    d, ids = base
    copy = tmp_path / "copy"
    shutil.copytree(d, copy)
    return Store.open(copy, registry=registry(), **services()), ids


def touched_now(store: Store) -> set[str]:
    """The keys the latest version touched: each key's entry holds its last touch, and nothing has run since."""
    return {k for k, v in store._touched.items() if v == store.version}


def touched_since(store: Store, version: int) -> set[str]:
    """The keys every version after `version` touched: a statement's own, and those of what its return read and
    landed (a reading, the judge's answer)."""
    return {k for k, v in store._touched.items() if v > version}


def test_each_kind_of_write_touches_the_keys_of_what_it_changed(base, tmp_path):
    """The vocabulary as the triggers write it: an object by its id, its type's rows, and, made or removed, which
    objects of its type exist; the chances it is given, before and after; a link at each of its ends, by its relation
    and by any, by its ends' kinds, and, made or removed, which links of its relation exist; a re-pointed end at both
    the end it left and the one it reached; a market kept or released; a page held, kept as a Source and dated; a
    reading; the judge's answer; a setting."""
    store, i = opened(base, tmp_path)
    try:
        u = i["u"]
        store.edit(u, "title", "U again")
        assert touched_now(store) == {touches.id(u), touches.rows("Proposition"), touches.table("object")}
        at = store.version
        store.edit(i["t"], "chances", [stated({"given": [u], "given_not": ["market:kalshi:M1"], "value": 0.5})])
        assert {touches.given(u), touches.given("market:kalshi:M1"), touches.given(i["r"])} <= touched_since(store, at)
        at = store.version
        v = store.add("Proposition", {**PROPOSITION, "title": "V"})["id"]
        assert {touches.id(v), touches.objects("Proposition"), touches.rows("Proposition")} <= touched_since(store, at)
        at = store.version
        lid = store.link(v, "implies", u)["id"]
        assert {touches.id(lid), touches.relation("implies"), touches.rows("implies"), touches.table("link"),
                touches.links(v, "implies", "out"), touches.links(v, None, "out"), touches.links(u, "implies", "in"),
                touches.links(u, None, "in"), touches.ends("prop")} <= touched_since(store, at)
        at = store.version
        store.remove(lid)
        assert {touches.id(lid), touches.relation("implies"), touches.links(u, "implies", "in")} <= \
            touched_since(store, at)
        at = store.version
        store.edit("metric:rate_pct", "level", 23.0, i["held"])  # the citation re-pointed, the first page released
        assert {touches.links(i["page"], "cites", "in"), touches.links(i["held"], "cites", "in"),
                touches.id(i["held"]), touches.table("source"), touches.table("held")} <= touched_since(store, at)
        at = store.version
        store.link(u, "asks", "market:kalshi:M3", {"side": "no"})
        assert {touches.id("market:kalshi:M3"), touches.objects("Market"), touches.ends("market")} <= \
            touched_since(store, at)
        store.put_data("snapshot", "XOM", {"status": "OK", "price": 120.0}, {"endpoint": "/snapshot"},
                       retrieved_at=RETRIEVED, range=(ASOF, ASOF))
        did = store.latest("snapshot", "XOM", None)["id"]
        assert touched_now(store) == {touches.id(did), touches.data("snapshot", "XOM"), touches.data_of("snapshot"),
                                      touches.table("data")}
        jid = store.put_judgment("asks", "q", "h-three", [{"ref": None, "content": "z"}], 0.5, "m1", None)
        assert touched_now(store) == {touches.id(jid), touches.judgment("h-three"), touches.table("judgment")}
        store.edit("portfolio", "horizon_days", 200)
        assert touches.objects("Portfolio") not in touched_now(store)  # an edit changes no membership
    finally:
        store.close()


def test_a_version_is_a_commit_that_touched_something_and_is_kept_with_the_store(base, tmp_path):
    """Every commit that touched a key is the next version, its count in the store's own file, carried on when the
    store opens again; one transaction inside another is one version; a commit that touched nothing (a Computation, a
    record, a transaction that wrote nothing) is none."""
    store, i = opened(base, tmp_path)
    try:
        v = store.version
        assert v > 0 and int(store.meta("version")) == v
        with store.transaction():
            store.edit(i["u"], "title", "one")
            with store.transaction():
                store.edit(i["u"], "title", "two")
        assert store.version == v + 1
        with store.transaction():
            pass
        store.put_computation("d", 1, "s", "h", {}, {"x": 1})
        store.put_record(session=None, turn=None, stopped=False, rows=[], written={}, texts={}, versions={})
        assert store.version == v + 1 == int(store.meta("version"))
        store.close()
        again = Store.open(tmp_path / "copy", registry=registry(), **services())
        assert again.version == v + 1
        assert again.touched_at("never read before") == v + 1  # opened: nothing worked out before counts as current
        again.edit(i["u"], "title", "three")
        assert again.version == v + 2 and again.touched_at(touches.id(i["u"])) == v + 2
        again.close()
    finally:
        store.close()


def test_a_write_rolled_back_touches_nothing_and_is_no_version(base, tmp_path):
    store, i = opened(base, tmp_path)
    try:
        v, before = store.version, dict(store._touched)
        with pytest.raises(StoreError):
            with store.transaction():
                store.edit(i["u"], "title", "gone")
                raise StoreError("refused after it wrote")
        with pytest.raises(StoreError):
            store.link(i["t"], "implies", i["t"])  # refused by the store's own rule
        assert store.version == v and store._touched == before
        assert store.db.execute("SELECT count(*) FROM temp.touch").fetchone()[0] == 0
        assert store.stated_of(i["u"])["title"] == "U"
    finally:
        store.close()


def test_a_write_outside_a_transaction_of_the_stores_own_is_refused(base, tmp_path):
    """No change can commit without its keys becoming a version: the triggers refuse one made outside a transaction."""
    store, i = opened(base, tmp_path)
    try:
        for sql, args in (("UPDATE object SET stated = stated WHERE id = ?", (i["u"],)),
                          ("DELETE FROM link WHERE id = ?", (i["implies"],)),
                          ("INSERT INTO meta (key, value) VALUES ('x', 'y')", ())):
            with pytest.raises(Exception, match="inside one of its transactions"):
                store.db.execute(sql, args)
        with store.transaction():
            store.db.execute("UPDATE object SET stated = stated WHERE id = ?", (i["u"],))
        assert store.touched_at(touches.id(i["u"])) == store.version
    finally:
        store.close()


def test_inside_a_transaction_each_write_touches_at_a_later_step_than_the_reads_before_it(base, tmp_path):
    """A read made after one write and before the next is stamped with the first write's last touch: the next write's
    touches come after it, and a key no write since has touched stays at or before it."""
    store, i = opened(base, tmp_path)
    try:
        with store.transaction():
            assert store.step() == 0 and store.touched_in_transaction(touches.id(i["u"])) == 0
            store.edit(i["u"], "title", "one")
            first = store.step()
            assert first > 0 and store.touched_in_transaction(touches.id(i["u"])) <= first
            with touches.recording(store.stamp) as keys:
                store.describe(i["t"])
            assert keys[touches.id(i["t"])] == (store.version, first)
            store.edit(i["t"], "title", "T again")
            assert store.touched_in_transaction(touches.id(i["t"])) > first
            assert store.touched_in_transaction(touches.id(i["u"])) <= first
            assert store.touched_in_transaction(touches.id(i["r"])) == 0
        assert store.step() == 0  # committed: the log is folded into the version
    finally:
        store.close()


def test_a_rewind_touches_every_key(base, tmp_path):
    store, i = opened(base, tmp_path)
    try:
        store.rewind("s:1")
        assert store.touched_at("a key nothing has read") == store.version
        assert store.touched_at(touches.id(i["way"])) == store.version  # the way the rewind undid, by its own key
        with store.transaction():
            store.rewind("s:1")
            assert store.touched_in_transaction("any key at all") > 0
    finally:
        store.close()


def test_a_commit_after_another_connections_touches_every_key(base, tmp_path):
    """A file another connection committed to since this one last did holds changes no touch here records."""
    store, i = opened(base, tmp_path)
    other = Store.open(tmp_path / "copy", registry=registry(), **services())
    try:
        other.edit(i["u"], "title", "elsewhere")
        on_file = int(other.meta("version"))
        store.edit(i["t"], "title", "here")
        # its first commit after the other's touched every key
        assert store._everything_at == on_file + 1 <= store.version
        assert store.touched_at(touches.id(i["u"])) >= on_file + 1
    finally:
        other.close()
        store.close()


def test_a_commit_after_a_raw_write_by_another_connection_touches_every_key(base, tmp_path):
    """Whatever wrote the file in between, a store's own SQL among them: the store's next commit touches every key."""
    import sqlite3
    store, i = opened(base, tmp_path)
    try:
        raw = sqlite3.connect(tmp_path / "copy" / "store.sqlite")
        raw.execute("UPDATE object SET stated = json_set(stated, '$.title', 'raw') WHERE id = ?", (i["u"],))
        raw.commit()
        raw.close()
        v = store.version
        store.edit(i["t"], "title", "here")
        assert store._everything_at == v + 1 and store.touched_at(touches.id(i["u"])) >= v + 1
        store.edit(i["t"], "title", "here again")  # nothing else since: only its own keys
        assert store._everything_at == v + 1
    finally:
        store.close()


def test_the_day_is_read_where_the_as_of_can_still_be_today_and_its_turn_is_a_version(base, tmp_path, monkeypatch):
    """A value that asks whether its as-of is today reads the day where the as-of is today or later; a past as-of
    stays past. Once New York's date turns, the day reads as touched after every version, until a version records
    it: the round each minute does, and so does any commit."""
    from harness.derive.portfolio import is_today
    store, i = opened(base, tmp_path)
    try:
        monkeypatch.setattr(touches, "today", lambda: ASOF)
        store._day = ASOF
        for asof, read in ((ASOF, True), ("2026-11-05", True), ("2026-11-03", False)):
            with touches.recording(store.stamp) as keys:
                is_today(asof)
            assert (touches.TODAY in keys) is read, asof
        assert store.record_the_day() is False
        v = store.version
        assert store.touched_at(touches.TODAY) <= v
        monkeypatch.setattr(touches, "today", lambda: "2026-11-05")
        assert store.touched_at(touches.TODAY) == v + 1  # turned, and not yet recorded
        assert store.record_the_day() is True and store.version == v + 1
        assert store.touched_at(touches.TODAY) == v + 1 and touched_now(store) == {touches.TODAY}
        monkeypatch.setattr(touches, "today", lambda: "2026-11-06")
        store.edit(i["u"], "title", "the next day")  # any commit records it
        assert touches.TODAY in touched_now(store)
    finally:
        store.close()


def test_a_read_that_lands_what_it_answers_is_stamped_after_its_landing(base, tmp_path):
    """A read of what the store does not hold, fetched and landed in the read: the value worked out from it is current
    once made. A separate read of a key that a later read lands again keeps its earlier stamp, and is not."""
    store, i = opened(base, tmp_path)
    try:
        key = touches.data("last_trade", "BTC-USD")
        with touches.recording(store.stamp) as keys:
            store.data("last_trade", "BTC-USD", covering=(ASOF, ASOF))
            store.data_many("last_trade", ["ETH-USD"], covering=(ASOF, ASOF))
        assert keys[key][0] == store.touched_at(key) == store.version - 1
        assert keys[touches.data("last_trade", "ETH-USD")][0] == store.touched_at(
            touches.data("last_trade", "ETH-USD")) == store.version
        with touches.recording(store.stamp) as keys:
            store.data("snapshot", "SPY", required=False)  # found
            store.put_data("snapshot", "SPY", {"status": "OK", "price": 762.0}, {"endpoint": "/snapshot"},
                           retrieved_at="2026-11-04T15:02:00+00:00", range=(ASOF, ASOF))
            store.data("snapshot", "SPY", required=False)  # the newer, read separately
        assert store.touched_at(touches.data("snapshot", "SPY")) > keys[touches.data("snapshot", "SPY")][0]
    finally:
        store.close()


def test_a_names_price_answered_from_the_cache_records_what_it_read(base, tmp_path):
    """Whether a name has a price is kept for the state it was worked out at, with the keys its reads recorded: a
    read answered from it records them again."""
    store, i = opened(base, tmp_path)
    try:
        with touches.recording(store.stamp) as first:
            store._no_price("name:NVDA")
        with touches.recording(store.stamp) as again:
            store._no_price("name:NVDA")
        assert first and dict(again) == dict(first)
    finally:
        store.close()
