"""What a derivation reads: fetched data parsed once and kept by its id and the hash of its content, read-only.

An inputs function runs again on every pass of a read; before a value was current only while nothing it read had landed (Engine._current),
each landed read moved the store's revision, and on a portfolio of broad funds the look-through read each fund's
holdings and report some seventy times a read. The engine keeps
what inputs functions parse (Engine.read), so these tests hold it to the three things its correctness rests on: the
same content is parsed once; a payload is named by its content, never its id alone, since a rolled-back call gives
its ids again; and nothing a derivation reads is changed by it, which every test checks (conftest.read_only_payloads)
and the case below breaks on purpose.
"""

from __future__ import annotations

import pytest

from harness.derive import Derivation, Registry
from harness.derive.engine import Engine, Waiting
from harness.derive.portfolio import _valid_rows
from harness.store import Store
from test_store import RETRIEVED, add_proposition, market_store


def _reader(mutate_in: str | None = None) -> Derivation:
    """A derivation whose inputs function reads a fund's holdings and decides its next read from them, as the
    look-through does; `mutate_in` names where it changes what it read: its inputs, or its compute."""

    def inputs(ctx, subject):
        ref = ctx.data("holdings", "name:F")
        held = ctx.payload(ref)
        if mutate_in == "inputs":
            held["results"].append({"ticker": "ZZZ", "weight": 0.5})
        return {"holdings": ref, "first": held["results"][0]["ticker"]}

    def compute(m, load):
        held = load(m["holdings"])
        if mutate_in == "compute":
            held["results"][0]["weight"] = 9.0
        return {"weights": sum(r["weight"] for r in held["results"])}

    return Derivation("reader", 1, "Portfolio", inputs, compute)


def _store(make_store, mutate_in=None) -> Store:
    store = make_store(registry_=Registry([_reader(mutate_in)]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    store.put_data("holdings", "name:F", {"results": [{"ticker": "AAA", "weight": 0.6}, {"ticker": "BBB",
                                                                                         "weight": 0.4}]},
                   {"endpoint": "/holdings"}, retrieved_at=RETRIEVED)
    return store


def _move_revision(store: Store, n: int) -> None:
    """Every value read again, as a write, a rollback, or the page's render makes it: each inputs function runs
    again on what the store holds, the data it read unchanged."""
    store.engine.reset()


def test_an_inputs_function_parses_what_it_reads_once_across_passes(make_store, monkeypatch):
    store = _store(make_store)
    texts = []
    real = store.data_text
    monkeypatch.setattr(store, "data_text", lambda did: texts.append(did) or real(did))
    first = store.engine.value("reader", "portfolio").output
    for n in range(5):
        _move_revision(store, n)
        assert store.engine.value("reader", "portfolio").output == first == {"weights": 1.0}
    holdings = store.data("holdings", "name:F")["id"]
    assert texts.count(holdings) == 1  # five passes after the first, each its inputs read again, one parse


def test_a_value_read_by_the_values_above_it_is_parsed_once_across_passes(make_store, monkeypatch):
    """A Computation's output, which a value above it reads in its inputs function to decide what it reads next, as
    the impact reads the look-through's, is kept parsed by its content's hash as fetched data is: read again on every
    pass, it is parsed once."""

    def inputs(ctx, s):
        ref = ctx.value("reader", s, ref=True)
        return {"reader": ref, "weights": ctx.payload(ref)["weights"]}

    above = Derivation("above", 1, "Portfolio", inputs, lambda m, load: {"twice": 2 * m["weights"]})
    store = make_store(registry_=Registry([_reader(), above]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    store.put_data("holdings", "name:F", {"results": [{"ticker": "AAA", "weight": 0.6}]}, {"endpoint": "/holdings"},
                   retrieved_at=RETRIEVED)
    texts = []
    real = store.computation_text
    monkeypatch.setattr(store, "computation_text", lambda cid: texts.append(cid) or real(cid))
    for n in range(5):
        _move_revision(store, n)
        assert store.engine.value("above", "portfolio").output == {"twice": 1.2}
    reader = store.engine.value("reader", "portfolio").basis
    assert texts.count(reader) == 1


@pytest.mark.changes_reads
@pytest.mark.parametrize("where", ["inputs", "compute"])
def test_a_derivation_that_changes_what_it_read_fails_where_it_is_read_next(make_store, where, monkeypatch):
    """With check_reads: changed in the inputs function, it is caught by the compute that reads it next, in the same
    pass; changed in the compute, by the next pass's inputs function."""
    monkeypatch.setattr(Engine, "check_reads", True)
    store = _store(make_store, mutate_in=where)
    holdings = store.data("holdings", "name:F")["id"]
    with pytest.raises(RuntimeError, match=f"{holdings} was changed after it was read"):
        store.engine.value("reader", "portfolio")
        _move_revision(store, 1)
        store.engine.value("reader", "portfolio")


@pytest.mark.changes_reads
def test_without_the_check_a_changed_payload_would_be_read_as_the_store_holds_it(make_store, monkeypatch):
    """The case the check exists for: the change, unchecked, is what every later reader gets, a second state
    beside the store's that the hash no longer describes."""
    monkeypatch.setattr(Engine, "check_reads", False)
    store = _store(make_store, mutate_in="inputs")
    store.engine.value("reader", "portfolio")
    _move_revision(store, 1)
    store.engine.value("reader", "portfolio")
    ref = {"data": store.data("holdings", "name:F")["id"], "sha256": store.data("holdings", "name:F")["sha256"]}
    assert len(store.engine.read(ref)["results"]) == 4  # two rows held, one appended by each pass
    assert len(store.data_payload(ref["data"])["results"]) == 2


def test_an_id_a_rolled_back_call_gives_again_is_read_as_what_it_now_names(make_store):
    store, judge, venues, books = market_store(make_store)
    t = add_proposition(store)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.link(t["id"], "asks", "market:kalshi:M1")  # the market kept, and its data with it
            did = store.put_data("book", "market:kalshi:M1", {"bids": "as read in the call"}, {"endpoint": "/book"},
                                 retrieved_at=RETRIEVED)
            row = store._data_row(did)
            assert store.engine.read({"data": did, "sha256": row["sha256"]}, keep=True) == {
                "bids": "as read in the call"}
            raise RuntimeError("the call fails after its reads")
    # the market was not kept, so its data was not written again: its ids are given to what is read next
    given = []
    while did not in given:
        assert len(given) < 50, "the rolled-back id was never given again"
        given.append(store.put_data("holdings", f"name:F{len(given)}", {"results": [len(given)]},
                                    {"endpoint": "/holdings"}, retrieved_at=RETRIEVED))
    row = store._data_row(did)
    assert store.engine.read({"data": did, "sha256": row["sha256"]}, keep=True) == {"results": [len(given) - 1]}


def test_a_funds_rows_are_told_apart_once_for_the_payload_read_again():
    payload = {"status": "OK", "results": [{"t": "A", "w": 0.5}, {"t": "A", "w": 0.5}, {"t": "B", "w": 0.5}]}
    rows = _valid_rows(payload)
    assert rows == ({"t": "A", "w": 0.5}, {"t": "B", "w": 0.5}) and _valid_rows(payload) is rows
    same_content = {"status": "OK", "results": [{"t": "A", "w": 0.5}]}  # another object: its own rows
    assert _valid_rows(same_content) == ({"t": "A", "w": 0.5},)
    with pytest.raises(AttributeError):
        rows.append({"t": "C"})  # what is kept is shared: no reader extends it


# ---------------------------------------------------------------- data landing: what read it is current no longer


def _landing_store(make_store):
    """Three values over data: `a` reads ('k', 'X'); `b` reads `a`; `c` reads ('k', 'Y'); `z` reads ('k', 'Z'), which
    the store does not hold. Each counts its inputs' runs."""
    runs = {}

    def reader(name, inputs):
        def run(ctx, subject):
            runs[name] = runs.get(name, 0) + 1
            return inputs(ctx)
        return Derivation(name, 1, "Portfolio", run, lambda m, load: {"read": m})

    reg = Registry([reader("a", lambda ctx: {"x": ctx.data("k", "X", required=False)}),
                    reader("b", lambda ctx: {"a": ctx.value("a", "portfolio")}),
                    reader("c", lambda ctx: {"y": ctx.data("k", "Y", required=False)}),
                    reader("z", lambda ctx: {"z": ctx.data("k", "Z", required=False)})])
    store = make_store(registry_=reg)
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    for subject in ("X", "Y"):
        store.put_data("k", subject, {"v": 1}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
    for name in ("a", "b", "c", "z"):
        store.engine.value(name, "portfolio")
    return store, runs


def _again(store, runs) -> dict:
    before = dict(runs)
    for name in ("a", "b", "c", "z"):
        store.engine.value(name, "portfolio")
    return {n: runs[n] - before[n] for n in runs}


def test_data_landing_evicts_the_values_that_read_it_and_those_that_read_them_and_no_other(make_store):
    store, runs = _landing_store(make_store)
    version = store.version
    store.put_data("k", "X", {"v": 2}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
    assert store.version == version + 1  # a landing is one version, touching its kind and subject
    assert _again(store, runs) == {"a": 1, "b": 1, "c": 0, "z": 0}
    assert store.engine.value("b", "portfolio").output["read"]["a"]["output"]["read"]["x"]["data"] == \
        store.data("k", "X")["id"]  # what b reads is a's reading of the newest X


def test_a_value_that_found_nothing_reads_it_when_it_lands(make_store):
    store, runs = _landing_store(make_store)
    assert store.engine.value("z", "portfolio").output["read"]["z"]["data"] is None
    store.put_data("k", "Z", {"v": 3}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
    assert _again(store, runs) == {"a": 0, "b": 0, "c": 0, "z": 1}
    assert store.engine.value("z", "portfolio").output["read"]["z"]["data"] == store.data("k", "Z")["id"]


def test_a_write_reads_again_the_values_that_read_what_it_changed_and_no_other(make_store):
    """ONTOLOGY.md 2: a derived field is computed again when anything it read changes. A write to the portfolio changes
    nothing a, b, c or z read; a value that reads the portfolio, and one that reads that value, are read again."""
    store, runs = _landing_store(make_store)
    store.edit("portfolio", "total", 2000.0)
    assert _again(store, runs) == {"a": 0, "b": 0, "c": 0, "z": 0}
    reads = {"p": 0, "q": 0}

    def counted(name, inputs):
        def run(ctx, subject):
            reads[name] += 1
            return inputs(ctx)
        return Derivation(name, 1, "Portfolio", run, lambda m, load: {"read": m})

    store.engine.registry.register(counted("p", lambda ctx: {"total": ctx.object("portfolio")["stated"]["total"]}))
    store.engine.registry.register(counted("q", lambda ctx: {"p": ctx.value("p", "portfolio")}))
    assert store.engine.value("q", "portfolio").output["read"]["p"]["output"]["read"]["total"] == 2000.0
    store.edit("portfolio", "total", 3000.0)
    assert store.engine.value("q", "portfolio").output["read"]["p"]["output"]["read"]["total"] == 3000.0
    assert reads == {"p": 2, "q": 2} and _again(store, runs) == {"a": 0, "b": 0, "c": 0, "z": 0}


def test_a_waiting_value_is_read_again_when_any_data_lands(make_store):
    """A value that waits may wait on any read: whatever lands, it is read again, not left to its retry."""
    calls = {"n": 0}

    def inputs(ctx, subject):
        calls["n"] += 1
        raise Waiting("what it reads is being read")

    store = make_store(registry_=Registry([Derivation("w", 1, "Portfolio", inputs, lambda m, load: {})]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    assert store.engine.value("w", "portfolio").waiting
    store.engine.value("w", "portfolio")
    assert calls["n"] == 1  # kept for its retry
    store.put_data("k", "Q", {"v": 4}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
    store.engine.value("w", "portfolio")
    assert calls["n"] == 2


def test_a_value_whose_read_landed_while_it_was_computed_is_read_again(make_store):
    """A read that lands from within the value's own computation, after the value read what the store then held: what
    it read may be older than what the store holds, so it is not kept."""
    runs = {"n": 0}
    store_ref = {}

    def inputs(ctx, subject):
        runs["n"] += 1
        found = ctx.data("k", "X", required=False)
        if runs["n"] == 1:  # another reader's read lands meanwhile
            store_ref["s"].put_data("k", "X", {"v": 9}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
        return {"x": found}

    store = make_store(registry_=Registry([Derivation("late", 1, "Portfolio", inputs, lambda m, load: {"read": m})]))
    store_ref["s"] = store
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    first = store.engine.value("late", "portfolio").output["read"]["x"]["data"]
    assert first is None
    second = store.engine.value("late", "portfolio").output["read"]["x"]["data"]
    assert second == store.data("k", "X")["id"] and runs["n"] == 2


# ---------------------------------------------------------------- what the memo answers (Engine._current)


def test_a_reader_of_a_value_not_kept_is_not_kept_either(make_store):
    """U reads X, and X lands while U is computed, so U is not kept; R read U, so R carries what U read, and is
    not kept either: asked again, R reads the X the store holds now."""
    runs = {"u": 0}
    ref = {}

    def u_inputs(ctx, subject):
        runs["u"] += 1
        found = ctx.data("k", "X", required=False)
        if runs["u"] == 1:  # another reader's read of X lands after U read the store
            ref["s"].put_data("k", "X", {"v": 9}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
        return {"x": found}

    store = make_store(registry_=Registry([
        Derivation("u", 1, "Portfolio", u_inputs, lambda m, load: {"read": m}),
        Derivation("r", 1, "Portfolio", lambda ctx, s: {"u": ctx.value("u", "portfolio")},
                   lambda m, load: {"read": m}),
        Derivation("t", 1, "Portfolio", lambda ctx, s: {"r": ctx.value("r", "portfolio")},
                   lambda m, load: {"read": m})]))
    ref["s"] = store
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    x = lambda v: v.output["read"]["r"]["output"]["read"]["u"]["output"]["read"]["x"]["data"]  # noqa: E731
    assert x(store.engine.value("t", "portfolio")) is None  # its first answer rests on U's read before X landed
    assert x(store.engine.value("t", "portfolio")) == store.data("k", "X")["id"]  # at any depth


def test_a_value_computed_now_and_one_read_back_from_the_store_are_one_form(make_store):
    """A Computation's output is kept as its canonical text, its keys in one order, and a value computed in this process
    is handed to its readers in that same form. So a computation that runs over its input's keys gives one figure
    whether that input was computed now or read back: here U's parts come in the order z, a, m, which sum to 0.0 in
    floating point, where the stored order a, m, z sums to 0.1 (evaluation's 47ag: the look-through's cash total
    differed in its last bits between the two paths, and with it every hash that named it)."""
    up = Derivation("u", 1, "Portfolio", lambda ctx, s: {},
                    lambda m, load: {"parts": {"z": 0.1, "a": 1e16, "m": -1e16}})

    def down(version):
        return Derivation("r", version, "Portfolio", lambda ctx, s: {"u": ctx.value("u", "portfolio", ref=True)},
                          lambda m, load: {"total": sum(load(m["u"])["parts"].values())})

    store = make_store(registry_=Registry([up, down(1)]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    assert list(store.engine.value("u", "portfolio").output["parts"]) == ["a", "m", "z"]
    assert store.engine.value("r", "portfolio").output["total"] == 0.1  # U computed in this process
    fresh = Engine(store, Registry([up, down(2)]))  # U found in the store and read back; R computed again from it
    assert fresh.value("r", "portfolio").output["total"] == 0.1


def test_what_the_memo_answers_is_what_an_engine_that_kept_nothing_answers(make_store):
    """The property itself, on random graphs: values reading data and one another, some waiting where what they read
    is absent, data landing between reads and during them, read inline by the very value being computed or by
    another reader. After every step, every value the engine answers is the value an engine that kept nothing
    computes now from the same store (40 graphs, 60 steps each, seeds fixed before any result)."""
    import random

    from harness.derive.engine import Engine

    for seed in range(40):
        rng = random.Random(seed)
        keys = [f"X{i}" for i in range(4)]
        live = {"on": False, "store": None}
        plan = {}

        def make(i):
            reads = rng.sample(keys, rng.randint(0, 2))
            ups = rng.sample(range(i), min(i, rng.randint(0, 2)))
            waits = bool(reads) and rng.random() < 0.2
            lands = rng.random() < 0.4
            plan[i] = (reads, ups, waits, lands)

            def inputs(ctx, subject):
                got = {}
                for n, k in enumerate(reads):
                    got[k] = ctx.data("k", k, required=False)
                    if n == 0 and lands and live["on"] and rng.random() < 0.5:  # a read lands while it is computed
                        live["store"].put_data("k", rng.choice(keys), {"v": rng.random()}, {"endpoint": "/k"},
                                               retrieved_at=RETRIEVED)
                if waits and got[reads[0]]["data"] is None:
                    raise Waiting(f"{reads[0]} is being read")
                for j in ups:
                    got[f"v{j}"] = ctx.value(f"v{j}", "portfolio")
                return got

            return Derivation(f"v{i}", 1, "Portfolio", inputs, lambda m, load: {"read": m})

        registry = Registry([make(i) for i in range(6)])
        store = make_store(f"scratch-property-{seed}", registry_=registry)
        live["store"] = store
        store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})

        def answer(engine, i):
            v = engine.value(f"v{i}", "portfolio")
            return ("waiting" if v.waiting else "absent" if v.output is None else v.output)

        for step in range(60):
            if rng.random() < 0.3:  # a read lands between reads
                store.put_data("k", rng.choice(keys), {"v": rng.random()}, {"endpoint": "/k"},
                               retrieved_at=RETRIEVED)
            else:
                live["on"] = True
                store.engine.value(f"v{rng.randrange(6)}", "portfolio")
                live["on"] = False
            fresh = Engine(store, registry)
            for i in range(6):
                assert answer(store.engine, i) == answer(fresh, i), (seed, step, i, plan[i])


def test_a_value_whose_own_read_lands_what_it_reads_is_kept(make_store):
    """A read the value itself makes, fetched and landed in its own computation, is what the store holds once it
    returns: the value is kept on that pass, and not computed again for a landing it read (each pass of the first read
    lands what it reads)."""
    runs = {"n": 0}

    def inputs(ctx, subject):
        runs["n"] += 1
        return {"x": ctx.data("k", "X")}

    store = make_store(registry_=Registry([Derivation("own", 1, "Portfolio", inputs, lambda m, load: {"read": m})]))
    store.fetchers["k"] = lambda subject, covering: {"payload": {"v": 1}, "request": {"endpoint": "/k"},
                                                     "retrieved_at": RETRIEVED}
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    version = store.version
    first = store.engine.value("own", "portfolio")
    assert store.version == version + 1 and first.output["read"]["x"]["data"] == store.data("k", "X")["id"]
    store.engine.value("own", "portfolio")
    assert runs["n"] == 1


def test_many_subjects_read_at_once_are_each_read_as_alone_and_each_landing_reaches_the_value(make_store):
    """A value that reads subjects of one kind at once (Inputs.data_many) records each as read: it gives what reading
    each alone gives, and is read again when any of them lands, held or not, and not when another subject does."""
    runs = {}

    def reader(name, inputs):
        def run(ctx, subject):
            runs[name] = runs.get(name, 0) + 1
            return inputs(ctx)
        return Derivation(name, 1, "Portfolio", run, lambda m, load: {"read": m})

    reg = Registry([reader("many", lambda ctx: ctx.data_many("k", ["X", "Z", "X"], required=False)),
                    reader("alone", lambda ctx: {s: ctx.data("k", s, required=False) for s in ("X", "Z")})])
    store = make_store(registry_=reg)
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    for subject in ("X", "Y"):
        store.put_data("k", subject, {"v": 1}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)

    def both():
        return [store.engine.value(n, "portfolio").output["read"] for n in ("many", "alone")]

    many, alone = both()
    assert many == alone and list(many) == ["X", "Z"] and many["Z"]["data"] is None
    before = dict(runs)
    store.put_data("k", "Y", {"v": 2}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)  # read by neither
    both()
    assert runs == before
    for subject in ("Z", "X"):  # one the store did not hold, then one it did
        store.put_data("k", subject, {"v": 3}, {"endpoint": "/k"}, retrieved_at=RETRIEVED)
        many, alone = both()
        assert runs["many"] == before["many"] + 1 and many == alone and many[subject]["data"] == \
            store.data("k", subject)["id"]
        before = dict(runs)


def test_many_series_read_at_once_are_each_what_reading_it_alone_gives(make_store):
    """history.series_inputs_many against series_inputs one ticker at a time, over every kind of series: a US listing
    with dividends, one of them paid in another currency with no split-adjusted amount (its rates and its splits
    read beyond the records), one with none, a pair, an index, a listing abroad with its currency's rates, and one the
    store holds nothing for, not required."""
    from harness.derive import history
    from massive_data import ASOF, put_bars, put_dividends, put_home, put_rate, weekdays

    days = weekdays("2026-10-01", ASOF)
    tickers = ["AAA", "BBB", "X:BTCUSD", "I:SPX", "KR7005930003:KR", "CCC", "AAA"]
    order = []  # the order the inputs give them in; an output's keys are kept in the one canonical order

    def inputs(ctx, subject):
        one = {t: history.series_inputs(ctx, t, ASOF, required=t != "CCC") for t in dict.fromkeys(tickers)}
        many = history.series_inputs_many(ctx, tickers, ASOF, required=lambda t: t != "CCC")
        order[:] = list(many)
        return {"one": one, "many": many}

    store = make_store(registry_=Registry([Derivation("both", 1, "Portfolio", inputs, lambda m, load: m)]))
    store.add("Portfolio", {"asof": ASOF, "total": 1000.0})
    flat = {d: 100.0 for d in days}
    put_bars(store, "AAA", flat)
    put_dividends(store, "AAA", {"2026-10-15": 0.5})
    store.put_data("dividends", "AAA", {"status": "OK", "results": [
        {"id": "EAAA1", "ticker": "AAA", "ex_dividend_date": "2026-10-20", "cash_amount": 0.4, "currency": "EUR",
         "frequency": 4, "distribution_type": "recurring"}]}, {"method": "GET", "url": "dividends:AAA"},
        retrieved_at="2026-11-05T03:00:00+00:00", range=("2024-01-01", ASOF))
    store.put_data("splits", "AAA", {"status": "OK", "results": []}, {"method": "GET", "url": "splits:AAA"},
                   retrieved_at="2026-11-05T03:00:00+00:00", range=("2024-01-01", ASOF))
    store.put_data("rates", "EUR", {"status": "OK", "results": []}, {"method": "GET", "url": "rates:EUR"},
                   retrieved_at="2026-11-05T03:00:00+00:00", range=("2024-01-01", ASOF))
    put_bars(store, "BBB", flat)
    put_bars(store, "I:SPX", flat)
    put_rate(store, "X:BTCUSD", {d: 90_000.0 for d in days}, base="BTC", quote="USD")
    put_home(store, "KR7005930003:KR", {d: 70_000.0 for d in days}, currency="KRW")
    put_rate(store, "C:KRWUSD", {d: 0.00072 for d in days}, base="KRW", quote="USD")
    got = store.engine.value("both", "portfolio").output
    assert got["many"] == got["one"] and order == list(dict.fromkeys(tickers))
    assert set(got["many"]["AAA"]) >= {"rates", "splits"} and got["many"]["CCC"]["ref"]["data"] is None
    assert got["many"]["KR7005930003:KR"]["rate"]["data"] is not None


def test_a_read_the_page_makes_inside_a_call_reads_as_the_page_reads_and_no_other_thread_reads_with_its_sources(
        make_store):
    """A stage a call works out renders the page's portfolio panel on the call's own thread (server.Page._progress).
    That read is the page's: what the store does not hold waits for the analyst's turn, nothing is read or started for
    it, and the call does not stand aside for it. While the page reads, every other thread, a call's or the minute's
    readings (harness.readings), reads with the store's own sources."""
    import threading

    from harness import render
    from harness.store import StandAside
    asked = []

    def bars(subject, *a, **k):  # the store's own client: each subject asked, written down
        asked.append(subject)
        raise LookupError(subject)

    store = make_store("page-inside-a-call", fetchers={"bars": bars})
    with store.lock, store.standing_aside(set()) as waits:  # an attempt of a call
        with render.cache_only(store):  # the page's read on its thread
            with pytest.raises(Waiting) as read:
                store.data("bars", "SPY", covering=("2026-11-03", "2026-11-04"))
            assert not isinstance(read.value, StandAside)
        assert waits == [] and not store.stood_aside()
    assert asked == []

    reading, leave = threading.Event(), threading.Event()

    def page():
        with store.lock, render.cache_only(store):
            reading.set()
            leave.wait(5)

    t = threading.Thread(target=page)
    t.start()
    reading.wait(5)
    try:
        assert store.fetchers["bars"] is bars  # what the minute's readings take, outside the lock
    finally:
        leave.set()
        t.join()


def test_the_page_reads_a_value_it_cannot_give_now_as_last_worked_out_and_nothing_else_does(make_store):
    """A figure shown stays until its new value is ready. A value the page's read cannot give at the newest
    state without working it out under the store's lock, or without waiting on the analyst's turn, is read as it was
    last worked out (Engine.last_where_waiting). Nothing else is ever given that value (a successful result
    is the true state): a call's read, and a read outside the page's, give the value at the newest state, exactly as an
    engine that kept nothing gives it, and so does the page's read once the value is worked out; inside a transaction,
    and where the last Computation is of another version of the derivation, the page's read works it out as ever."""
    from harness import render
    gate = {"wait": False}

    def inputs(ctx, subject):
        if gate["wait"]:
            raise Waiting("read on the analyst's turn")
        return {"holdings": ctx.data("holdings", "name:F")}

    def compute(m, load):
        return {"weights": round(sum(r["weight"] for r in load(m["holdings"])["results"]), 6)}

    registry_ = Registry([Derivation("reader", 1, "Portfolio", inputs, compute)])
    store = make_store(registry_=registry_)
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})

    def put(*weights):
        store.put_data("holdings", "name:F", {"results": [{"ticker": f"T{i}", "weight": w}
                                                          for i, w in enumerate(weights)]},
                       {"endpoint": "/holdings"}, retrieved_at=RETRIEVED)

    def page():
        with store.lock, render.cache_only(store):
            return store.engine.value("reader", "portfolio")

    def call():
        with store.lock, store.engine.strict():
            return store.engine.value("reader", "portfolio")

    put(0.6, 0.4)
    first = call()
    assert first.output == {"weights": 1.0}
    put(0.6, 0.2)  # it lands: the value's inputs change, and nothing has worked it out at them yet
    held = page()
    assert (held.output, held.basis, held.waited, held.waiting) == ({"weights": 1.0}, first.basis, True, False)
    now = call()
    fresh = Engine(store, registry_).value("reader", "portfolio")  # an engine that kept nothing
    assert now.output == fresh.output == {"weights": 0.8} and now.basis == fresh.basis != first.basis
    assert store.engine.value("reader", "portfolio").basis == now.basis  # a read outside the page's
    assert page().basis == now.basis  # worked out: the page reads it as it is

    gate["wait"] = True  # a read the page leaves to the analyst's turn
    store.engine.reset()
    assert page().basis == now.basis
    with pytest.raises(Waiting):
        call()
    gate["wait"] = False

    put(0.5)
    with store.lock, store.transaction(), render.cache_only(store):  # inside a transaction: worked out as ever
        assert store.engine.value("reader", "portfolio").output == {"weights": 0.5}
    put(0.3)
    store.put_computation("reader", 2, "portfolio", "another", {}, {"another": "shape"})
    assert page().output == {"weights": 0.3}  # the last is of another version: worked out as ever


def test_between_replies_the_page_never_waits_on_a_value_made_apart_and_reads_it_again_once_it_is_made(make_store):
    """A view opened between replies shows at once. A value computed apart (a fit, a distribution) that is
    being worked out is read as it was last worked out, without the page's read waiting for it while it holds the
    store's lock, and the read says so (`later`); read again once it is made, the page has the value at the newest
    state, the same as a call's read. A value worked out inline is worked out as ever between replies."""
    import threading
    import time

    from harness import render
    go = threading.Event()

    def inputs(ctx, subject):
        return {"holdings": ctx.data("holdings", "name:F")}

    def compute(m, load):
        go.wait(10)
        return {"weights": round(sum(r["weight"] for r in load(m["holdings"])["results"]), 6)}

    store = make_store(registry_=Registry([Derivation("fit", 1, "Portfolio", inputs, compute, stage="fitting")]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})

    def put(*weights):
        store.put_data("holdings", "name:F", {"results": [{"ticker": f"T{i}", "weight": w}
                                                          for i, w in enumerate(weights)]},
                       {"endpoint": "/holdings"}, retrieved_at=RETRIEVED)

    put(0.6, 0.4)
    go.set()
    with store.lock:
        first = store.engine.value("fit", "portfolio")
    go.clear()
    put(0.6, 0.2)
    t = time.monotonic()
    with store.lock, render.reading(store) as read:
        held = store.engine.value("fit", "portfolio")
    assert time.monotonic() - t < 2 and read.later  # at once, saying the value is being made
    assert (held.output, held.basis) == ({"weights": 1.0}, first.basis)
    go.set()
    for _ in range(100):
        with store.lock, render.reading(store) as read:
            got = store.engine.value("fit", "portfolio")
        if not read.later:
            break
        time.sleep(0.05)
    assert got.output == {"weights": 0.8} and not got.waited
    with store.lock, store.engine.strict():
        assert store.engine.value("fit", "portfolio").basis == got.basis


def test_between_replies_a_value_never_worked_out_and_being_made_apart_has_its_place_and_one_not_there_has_none(
        make_store):
    """A figure that is arriving keeps its placeholder, and nothing says a figure is missing. Read
    between replies, a value computed apart that was never worked out and is being made is waiting, and the page's
    reader says so (Reader.waits), so its figure shows its place; once made it is there, and a value nothing is making
    has no place."""
    import threading

    from harness import render
    go = threading.Event()

    def inputs(ctx, subject):
        return {"holdings": ctx.data("holdings", "name:F")}

    def compute(m, load):
        go.wait(10)
        return {"weights": round(sum(r["weight"] for r in load(m["holdings"])["results"]), 6)}

    store = make_store(registry_=Registry([Derivation("fit", 1, "Portfolio", inputs, compute, stage="fitting")]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    store.put_data("holdings", "name:F", {"results": [{"ticker": "T0", "weight": 0.6}]}, {"endpoint": "/holdings"},
                   retrieved_at=RETRIEVED)
    with store.lock, render.reading(store) as read:
        found = store.engine.value("fit", "portfolio")
        reader = render.Reader(store)
        assert found.waiting and read.later and reader.waits("fit", "portfolio")  # arriving: its place
        assert not reader.waits("fit", "another")
    go.set()
    for _ in range(100):
        with store.lock, render.reading(store) as read:
            found = store.engine.value("fit", "portfolio")
            made = not render.Reader(store).waits("fit", "portfolio")
        if not read.later:
            break
        threading.Event().wait(0.05)
    assert found.output == {"weights": 0.6} and made


def test_a_views_first_read_between_replies_holds_what_it_would_work_out_and_says_so_until_a_read_works_it_out(
        make_store):
    """A view opened between replies shows at once. Its first read holds a value it would work out under the
    store's lock as last worked out and says so (`unworked`), also when it takes the value again from the page's last
    read (Engine._page); the read that follows works it out (render.reading without inline), and every read after it
    has the value at the newest state."""
    from harness import render
    registry_ = Registry([Derivation("reader", 1, "Portfolio", lambda ctx, s: {"h": ctx.data("holdings", "name:F")},
                                     lambda m, load: {"n": len(load(m["h"])["results"])})])
    store = make_store(registry_=registry_)
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})

    def put(n):
        store.put_data("holdings", "name:F", {"results": [{"ticker": f"T{i}", "weight": 0.1} for i in range(n)]},
                       {"endpoint": "/holdings"}, retrieved_at=RETRIEVED)

    def first():
        with store.lock, render.reading(store, inline=True) as read:
            return store.engine.value("reader", "portfolio").output, bool(read.unworked)

    put(2)
    with store.lock:
        assert store.engine.value("reader", "portfolio").output == {"n": 2}
    put(3)
    assert first() == ({"n": 2}, True)
    assert first() == ({"n": 2}, True)  # taken again from the page's last read: still said
    with store.lock, render.reading(store) as read:  # the read that works it out
        assert store.engine.value("reader", "portfolio").output == {"n": 3} and not read.unworked
    assert first() == ({"n": 3}, False)


def test_each_value_answers_as_the_engine_last_gave_it_and_one_worked_out_from_a_held_figure_answers_nothing(
        make_store):
    """A Computation is kept while it is how its value was last worked out, and the store keeps each value's
    answer (Store.answered). Between replies the page holds a figure whose read waits on the analyst's turn, and a
    value above it is worked out from that figure and today's other input. That is no state's value as far as the
    engine can tell, so it is kept by its content and is not the value's answer. Where a later read finds it again by
    its inputs' hash, it is that state's value, and its answer. One worked out from a figure of an earlier moment goes
    when the value next answers; and a value read absent answers none, with why."""
    from harness.derive.engine import Unavailable
    gate = {"wait": False, "absent": False}

    def leaf_in(ctx, s):
        found = ctx.data("holdings", "name:F")
        if gate["wait"]:
            raise Waiting("read on the analyst's turn")
        return {"h": found}

    def top_in(ctx, s):
        if gate["absent"]:
            raise Unavailable("none to read")
        return {"leaf": ctx.value("leaf", "portfolio", ref=True), "g": ctx.data("holdings", "name:G")}

    total = lambda payload: sum(r["weight"] for r in payload["results"])  # noqa: E731
    registry_ = Registry([Derivation("leaf", 1, "Portfolio", leaf_in, lambda m, load: {"w": total(load(m["h"]))}),
                          Derivation("top", 1, "Portfolio", top_in,
                                     lambda m, load: {"t": round(load(m["leaf"])["w"] + total(load(m["g"])), 6)})])
    store = make_store(registry_=registry_)
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})

    def put(subject, *weights):
        store.put_data("holdings", subject, {"results": [{"weight": w} for w in weights]}, {"endpoint": "/h"},
                       retrieved_at=RETRIEVED)

    def call():
        with store.lock, store.engine.strict():
            return store.engine.value("top", "portfolio")

    def page():
        with store.lock, store.engine.last_where_waiting(inline=False):
            return store.engine.value("top", "portfolio")

    answer = lambda: store.last_computation("top", "portfolio")["id"]  # noqa: E731
    kept = lambda cid: store._one("SELECT 1 FROM computation WHERE id = ?", cid) is not None  # noqa: E731
    put("name:F", 0.6), put("name:G", 0.1)
    first = call()
    assert first.output == {"t": 0.7} and answer() == first.basis

    put("name:G", 0.3)  # G moves; F's value is read again and waits on the analyst's turn: the page holds it
    gate["wait"] = True
    store.engine.reset()
    held = page()
    assert held.output == {"t": 0.9} and held.waited
    assert answer() == first.basis and kept(held.basis)  # kept by its content, and not the answer
    gate["wait"] = False
    found = call()  # F as it was: the same inputs, taken again by their hash, and so the value's answer
    assert found.basis == held.basis and answer() == held.basis

    gate["wait"] = True  # F and G move while F's value waits: the page's figure mixes an earlier F with today's G
    put("name:F", 0.5), put("name:G", 0.4)
    mixed = page()
    assert mixed.output == {"t": 1.0} and answer() == held.basis
    gate["wait"] = False
    now = call()
    assert now.output == {"t": 0.9} == Engine(store, registry_).value("top", "portfolio").output
    assert answer() == now.basis and not kept(mixed.basis)  # gone with the next answer

    gate["absent"] = True
    store.engine.reset()
    with store.lock:
        assert store.engine.value("top", "portfolio").output is None
    assert store.last_computation("top", "portfolio") == {"id": None, "derivation": "top", "subject": "portfolio",
                                                          "absent": "none to read"}


def test_a_value_made_apart_that_no_reader_took_is_kept_by_its_content_when_another_is_started(make_store):
    """A value computed apart that finishes before any reader takes it is kept by its content, as no
    value's answer, when another computation starts, and leaves the computations under way: a reader whose inputs hash
    to it finds it, and nothing is computed twice. The page reads two values made apart and both finish; reading a third
    starts it, and a call's read of the first takes it by its hash."""
    import threading
    import time

    from harness import render
    go, made = threading.Event(), []

    def compute(m, load):
        go.wait(10)
        made.append(m["of"])
        return {"of": m["of"]}

    store = make_store(registry_=Registry([Derivation("fit", 1, "Portfolio", lambda ctx, s: {"of": s}, compute,
                                                      stage="fitting")]))
    store.add("Portfolio", {"asof": "2026-11-04", "total": 1000.0})
    with store.lock, render.reading(store):
        assert all(store.engine.value("fit", s).waiting for s in ("a", "b"))
    go.set()
    for _ in range(200):
        if len(made) == 2:
            break
        time.sleep(0.01)
    with store.lock, render.reading(store):
        store.engine.value("fit", "c")  # a third, started while the first two wait to be taken
    assert len(store.engine._running) <= 1  # the third alone, if under way: the two finished are kept by content
    with store.lock, store.engine.strict():
        assert store.engine.value("fit", "a").output == {"of": "a"}
    assert sorted(made).count("a") == 1  # taken by its hash, not made again
