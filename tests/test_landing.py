"""A read landing in the store (ONTOLOGY.md 2; TOOLS.md 5, "a successful result is the true state"): what the store
holds of a subject is its newest observation by when it was retrieved, however its reads land; a read that lands while a
write's transaction is open is kept through that write's rollback, being what the world said; a read made ready on the
thread that fetched it lands with nothing but its insert left to do; and a read carrying a credential is refused
wherever it is made ready. Every test runs with no keys and no network."""

from __future__ import annotations

import threading

import pytest

from harness.store import Store, StoreError

EARLY, LATE = "2026-11-11T14:00:00+00:00", "2026-11-11T14:01:00+00:00"


def answer(value: float, at: str, covering: tuple[str, str] | None = None) -> dict:
    """A client's answer, as a fetcher returns it."""
    return {"payload": {"status": "OK", "price": value}, "request": {"url": "https://example.test/last"},
            "retrieved_at": at, **({"range": covering} if covering else {})}


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-landing")
    yield s
    s.close()


def land(store: Store, kind: str, subject: str, got: dict) -> str:
    return store.put_data(kind, subject, got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                          range=got.get("range"))


def test_the_newest_observation_is_the_newest_retrieved_whatever_order_the_reads_land_in(store):
    land(store, "last", "AAA", answer(102.0, LATE))  # the later reading lands first
    land(store, "last", "AAA", answer(101.0, EARLY))  # an earlier one, started first and slower, lands after it
    assert store.data_payload(store.latest("last", "AAA", None)["id"])["price"] == 102.0
    # the reading held at a moment is the newest retrieved by then, or, before every reading, the first retrieved
    held = store._find_data("last", "AAA", None, at="2026-11-11T14:00:30+00:00")
    assert store.data_payload(held["id"])["price"] == 101.0
    assert store.data_payload(store._find_data("last", "AAA", None, at="")["id"])["price"] == 101.0


def test_two_reads_of_one_subject_landing_together_leave_the_newer_held(store):
    start = threading.Barrier(2)

    def landing(got):
        start.wait(5)
        land(store, "last", "BBB", got)

    threads = [threading.Thread(target=landing, args=(answer(v, at),)) for v, at in ((201.0, LATE), (200.0, EARLY))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert store.data_payload(store.latest("last", "BBB", None)["id"])["price"] == 201.0


def test_held_data_is_found_by_its_covering_range_and_the_newest_of_those_that_cover(store):
    land(store, "bars", "CCC", answer(1.0, EARLY, ("2024-01-01", "2026-11-10")))
    land(store, "bars", "CCC", answer(2.0, LATE, ("2026-01-01", "2026-11-10")))
    assert store.data_payload(store.latest("bars", "CCC", ("2025-01-01", "2026-11-10"))["id"])["price"] == 1.0
    assert store.data_payload(store.latest("bars", "CCC", ("2026-06-01", "2026-11-10"))["id"])["price"] == 2.0
    assert store.latest("bars", "CCC", ("2023-01-01", "2026-11-10")) is None


def test_a_read_that_lands_while_a_write_is_open_is_kept_through_its_rollback(store):
    reads = []

    def fetch(subject, covering):
        reads.append(subject)
        return answer(300.0, LATE)

    store.fetchers["last"] = fetch
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.data("last", "DDD")  # read and landed inside the write
            raise RuntimeError("the write is refused")
    row = store.latest("last", "DDD", None)
    assert row is not None and store.data_payload(row["id"])["price"] == 300.0
    store.data("last", "DDD")  # held: not read again
    assert reads == ["DDD"]


def test_a_read_made_ready_where_it_was_fetched_lands_as_one_made_ready_under_the_lock(store):
    got = answer(400.0, LATE, ("2026-11-01", "2026-11-10"))
    ready = Store.ready(got)
    did = store.put_data("bars", "EEE", got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                         range=got["range"], kept=ready["kept"])
    other = land(store, "bars", "EEE2", got)
    assert store._data_row(did)["sha256"] == store._data_row(other)["sha256"]
    assert store.data_payload(did) == got["payload"]


def test_a_read_carrying_a_credential_is_refused_wherever_it_is_made_ready(store):
    got = {**answer(500.0, LATE), "request": {"url": "https://example.test/last?apiKey=abcdef123456"}}
    ready = Store.ready(got)
    with pytest.raises(StoreError):
        store.put_data("last", "FFF", got["payload"], got["request"], retrieved_at=got["retrieved_at"],
                       kept=ready["kept"])
    with pytest.raises(StoreError):
        land(store, "last", "FFF", got)
    assert store.latest("last", "FFF", None) is None


@pytest.mark.parametrize("secret, payload", [
    ("sk-test-0123456789abcdef", {"results": [{"note": "deep in a list: sk-test-0123456789abcdef"}]}),
    ("sk-test-0123456789abcdef", {"by": {"sk-test-0123456789abcdef": 1}}),            # a key's text
    ('sk"test\\0123456789', {"results": ['a value holding sk"test\\0123456789 escaped']}),  # escaped in JSON
])
def test_a_configured_secret_anywhere_in_a_payload_is_found_by_searching_its_text(store, monkeypatch, secret, payload):
    monkeypatch.setenv("MASSIVE_API_KEY", secret)
    got = {**answer(1.0, LATE), "payload": payload}
    assert isinstance(Store.ready(got)["kept"], StoreError)
    with pytest.raises(StoreError):
        land(store, "last", "GGG", got)
    clean = {**answer(1.0, LATE), "payload": {"results": ["nothing secret here"]}}
    assert not isinstance(Store.ready(clean)["kept"], StoreError)


@pytest.mark.parametrize("name", ["OPENFIGI_API_KEY", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY"])
def test_every_key_a_client_or_the_tooling_reads_is_refused(store, monkeypatch, name):
    monkeypatch.setenv(name, "a1b2c3d4-e5f6-7890-abcd-ef0123456789")
    got = {**answer(1.0, LATE), "payload": {"results": ["signed by a1b2c3d4-e5f6-7890-abcd-ef0123456789"]}}
    assert isinstance(Store.ready(got)["kept"], StoreError)


def test_a_key_kept_as_a_file_is_refused_whole_or_a_line_at_a_time(store, monkeypatch, tmp_path):
    lines = ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj",
             "MZr0Q3ZqGQXwX3T0yKqzZ0x7mT2b9b1j9rKXlW1oZPZq8Hn2B6p0rYH1aQ2bL5c"]
    pem = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----\n"
    key = tmp_path / "kalshi.pem"
    key.write_text(pem)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key))
    for leaked in (pem, lines[1]):
        got = {**answer(1.0, LATE), "payload": {"results": [f"a request quoting {leaked}"]}}
        assert isinstance(Store.ready(got)["kept"], StoreError)
    armour = {**answer(1.0, LATE), "payload": {"results": ["-----BEGIN PRIVATE KEY-----"]}}
    assert not isinstance(Store.ready(armour)["kept"], StoreError)  # the armour names a kind of key, no secret


MIDDLE, LATER = "2026-11-11T14:00:30+00:00", "2026-11-11T14:02:00+00:00"
WINDOW, OTHER = ("2026-01-01", "2026-11-10"), ("2025-01-01", "2025-06-30")


def test_many_subjects_read_at_once_answer_exactly_as_each_read_alone(tmp_path):
    """data_many over subjects the store holds (several retrievals of one, landing out of order; another under a range
    that does not cover the window) and ones it does not (fetched as data() fetches them), at every reading moment:
    each subject's row is data()'s, and what the store holds is found in one query, not one a subject."""
    fetched: list[str] = []

    def fetch(subject, covering):
        fetched.append(subject)
        return answer(9.0, LATER, covering)

    def build(name):
        s = Store.create(tmp_path / name, fetchers={"bars": fetch})
        land(s, "bars", "AAA", answer(102.0, LATE, WINDOW))  # the later reading lands first
        land(s, "bars", "AAA", answer(101.0, EARLY, WINDOW))
        land(s, "bars", "AAA", answer(150.0, LATER, OTHER))  # the newest, but not covering the window
        land(s, "bars", "BBB", answer(201.0, EARLY, WINDOW))
        land(s, "bars", "CCC", answer(301.0, LATE, OTHER))
        return s

    subjects = ["CCC", "AAA", "ZZZ", "BBB", "AAA"]
    for at in (None, "", MIDDLE, LATER):
        one, many = build(f"one-{at}"), build(f"many-{at}")
        try:
            alone = {s: one.data("bars", s, covering=WINDOW, at=at) for s in dict.fromkeys(subjects)}
            fetched.clear()
            queries: list[str] = []
            many.db.set_trace_callback(queries.append)
            together = many.data_many("bars", subjects, covering=WINDOW, at=at)
            many.db.set_trace_callback(None)
            assert list(together) == ["CCC", "AAA", "ZZZ", "BBB"]
            for s in together:
                a, b = alone[s], together[s]
                assert (a is None) == (b is None)
                if a is not None:
                    assert one.data_payload(a["id"]) == many.data_payload(b["id"])
                    assert {k: a[k] for k in a if k != "id"} == {k: b[k] for k in b if k != "id"}
            # held: AAA and BBB, in one query; not held, CCC (its range does not cover) and ZZZ, fetched one by one
            assert sum("ROW_NUMBER" in q for q in queries) == 1 and fetched == ["CCC", "ZZZ"]
        finally:
            one.close()
            many.close()


def test_many_subjects_raise_where_one_read_would(tmp_path):
    """A subject no client reads raises as data() raises at it; with required False it is absent."""
    from harness.store import Unavailable
    s = Store.create(tmp_path / "raise")
    try:
        land(s, "bars", "AAA", answer(1.0, EARLY, WINDOW))
        with pytest.raises(Unavailable):
            s.data_many("bars", ["AAA", "NONE"], covering=WINDOW)
        got = s.data_many("bars", ["AAA", "NONE"], covering=WINDOW, required=False)
        assert got["NONE"] is None and s.data_payload(got["AAA"]["id"])["price"] == 1.0
    finally:
        s.close()


def test_a_call_standing_aside_for_many_subjects_waits_for_all_their_reads_at_once(tmp_path):
    """A call standing aside (Store.standing_aside) that asks many subjects the store does not hold starts every one's
    read and stands aside for all of them in one attempt, so its next attempt finds them landed: read one an attempt,
    a call needing a fund's hundreds of companies' prices ran out of attempts (measured cold on INDA, VWO and QLD,
    each attempt standing aside for one home market's prices)."""
    from concurrent.futures import wait

    from harness.store import StandAside

    release, fetched = threading.Event(), []

    def fetch(subject, covering):
        fetched.append(subject)
        release.wait(10)
        return answer(1.0, LATE, covering)

    s = Store.create(tmp_path / "aside", fetchers={"bars": fetch})
    try:
        subjects = ["AAA", "BBB", "CCC", "DDD"]
        with s.standing_aside(set()) as waits:
            with pytest.raises(StandAside):
                s.data_many("bars", subjects, covering=WINDOW)
            stood = list(waits)
        release.set()
        assert len(set(stood)) == len(subjects)  # every subject's read, stood aside for together
        wait(stood, timeout=10)
        with s.standing_aside(set()) as waits:
            got = s.data_many("bars", subjects, covering=WINDOW)
            assert not waits
        assert list(got) == subjects and all(got[x] is not None for x in subjects)
        assert sorted(fetched) == subjects  # each read once
    finally:
        s.close()
