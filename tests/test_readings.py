"""Live readings: a holding given in dollars keeps the shares it was given at, and a reading taken again is kept only
when what a value reads of it differs."""

from __future__ import annotations

import pytest

from harness import readings, touches
from harness import store as store_module
from harness.derive import default_registry
from harness.store import Store
from massive_data import ASOF, put_bars, put_reference
from test_portfolio import snapshot


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-readings", registry=default_registry())
    yield s
    s.close()


def at(monkeypatch, moment: str) -> None:
    """The store's clock, for the moment a holding is given."""
    monkeypatch.setattr(store_module, "_now", lambda: moment)


def mark(store, price: float, retrieved: str) -> str:
    """A snapshot of NVDA, its last trade at `price`, read at `retrieved`."""
    payload = {"status": "OK", "ticker": {"ticker": "NVDA", "lastTrade": {"p": price, "t": 1793821617000000000}}}
    return store.put_data("snapshot", "NVDA", payload, {"method": "GET", "url": "https://api.massive.com/v2/snapshot/"
                          "locale/us/markets/stocks/tickers/NVDA"}, retrieved_at=retrieved, range=(ASOF, ASOF))


def nvda(store) -> dict:
    store.engine.reset()
    row = next(r for r in store.engine.value("holdings", "portfolio").output["holds"].values()
               if r["name"] == "name:NVDA")
    return {k: row[k] for k in ("price", "quantity", "dollars")}


def test_a_holding_given_in_dollars_keeps_its_shares_and_moves_with_the_price_until_given_again(store, monkeypatch):
    """Given in dollars at a mark of 180, it holds 50 shares; when the mark is 190 they are worth 9,500. Its figure
    edited when the mark is 190 gives it again: 10,000 at 190, then worth 10,000 x 200 / 190 at 200."""
    put_reference(store, "NVDA", "CS")
    put_bars(store, "NVDA", {"2026-11-03": 200.0, ASOF: 181.0}, fetched="2026-11-04T19:47:00+00:00")
    mark(store, 180.0, "2026-11-04T19:47:00+00:00")
    at(monkeypatch, "2026-11-04T19:47:30+00:00")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "NVDA", "dollars": 9000.0}]})
    assert nvda(store) == {"price": 180.0, "quantity": pytest.approx(50.0), "dollars": 9000.0}
    mark(store, 190.0, "2026-11-04T19:48:00+00:00")
    assert nvda(store) == {"price": 190.0, "quantity": pytest.approx(50.0), "dollars": pytest.approx(9500.0)}
    assert store.get("portfolio")["derived"]["value"]["value"] == pytest.approx(9500.0)
    at(monkeypatch, "2026-11-04T19:49:00+00:00")
    store.edit(store.links_of("portfolio", "holds")[0]["id"], "dollars", 10000.0)
    assert nvda(store) == {"price": 190.0, "quantity": pytest.approx(10000 / 190), "dollars": 10000.0}
    mark(store, 200.0, "2026-11-04T19:50:00+00:00")
    assert nvda(store) == {"price": 200.0, "quantity": pytest.approx(10000 / 190),
                           "dollars": pytest.approx(10000 * 200 / 190)}


def test_a_reading_is_kept_only_when_its_mark_differs_and_a_failed_read_keeps_the_last(store, monkeypatch):
    """The same mark with a new payload stamp is not kept; a new price is; a refusal, a payload with no mark, and a
    read that raises keep the last; once the as-of has passed, nothing is read."""
    with store.transaction():
        store._set_meta("asof", ASOF)
    monkeypatch.setattr(touches, "today", lambda: ASOF)
    first = snapshot(store, "NVDA", trade=(180.0, "2026-11-04T14:46:57-05:00"))
    answers = []

    def read(subject, covering):
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return {"payload": got, "request": {"method": "GET", "url": "https://api.massive.com/v2/snapshot/NVDA"},
                "retrieved_at": "2026-11-04T19:48:00+00:00", "range": covering}

    store.fetchers["snapshot"] = read
    same = {**store.data_payload(first), "updated": 1}  # what a price reads of it is unchanged
    moved = {"status": "OK", "ticker": {"ticker": "NVDA", "lastTrade": {"p": 181.0, "t": 1793821617000000000}}}
    for answer, kept in [(same, []), (moved, [("snapshot", "NVDA")]), ({"status": "NOT_AUTHORIZED"}, []),
                         (RuntimeError("timed out"), []), (moved, [])]:
        answers.append(answer)
        assert readings.refresh(store) == kept
    assert store.data_payload(store.latest("snapshot", "NVDA", (ASOF, ASOF))["id"]) == moved
    monkeypatch.setattr(touches, "today", lambda: "2026-11-05")
    answers.append(moved)
    assert readings.refresh(store) == [] and answers == [moved]  # the as-of has passed: nothing is read


def test_a_rounds_readings_land_together_as_one_version_and_the_days_turn_is_a_version_at_any_as_of(store,
                                                                                                    monkeypatch):
    """Two marks that moved in one round land in one commit, the store's next version, which touches each reading's
    key, so every value reads them as one moment; a round that keeps nothing is no version. Once New York's date has
    turned, the round makes the turn a version of its own, though the as-of has passed and nothing is read."""
    live(store, monkeypatch)
    store._day = ASOF
    for ticker in ("NVDA", "AAPL"):
        snapshot(store, ticker, trade=(180.0, "2026-11-04T14:46:57-05:00"))
    prices = {"NVDA": 181.0, "AAPL": 231.0}

    def read(subject, covering):
        trade = {"p": prices[subject], "t": 1793821617000000000}
        return {"payload": {"status": "OK", "ticker": {"ticker": subject, "lastTrade": trade}},
                "request": {"method": "GET", "url": f"https://api.massive.com/v2/snapshot/{subject}"},
                "retrieved_at": "2026-11-04T19:48:00+00:00", "range": covering}

    store.fetchers["snapshot"] = read
    v = store.version
    assert sorted(readings.refresh(store)) == [("snapshot", "AAPL"), ("snapshot", "NVDA")]
    assert store.version == v + 1
    assert store.touched_at(touches.data("snapshot", "NVDA")) == store.touched_at(touches.data("snapshot", "AAPL")) \
        == v + 1
    assert readings.refresh(store) == [] and store.version == v + 1  # read again, unchanged: nothing kept
    monkeypatch.setattr(touches, "today", lambda: "2026-11-05")
    assert readings.refresh(store) == [] and store.version == v + 2
    assert store.touched_at(touches.TODAY) == v + 2


def test_a_book_reads_as_its_five_best_levels_and_last_trade_a_refusal_as_none_and_no_orders_as_no_quotes():
    book = {"market": "0x1", "asset_id": "1", "timestamp": "1", "bids": [{"price": "0.51", "size": "20"}],
            "asks": [{"price": "0.53", "size": "40"}]}
    first = readings.reading("book", "market:polymarket:1", book, {})
    assert first == {"bids": [(0.51, 20.0)], "asks": [(0.53, 40.0)], "last_trade": None}
    assert readings.reading("book", "market:polymarket:1", {**book, "timestamp": "2", "hash": "h"}, {}) == first
    bids = book["bids"] + [{"price": f"0.{50 - k}", "size": "1"} for k in range(1, 7)]  # seven bids
    moved = bids[:-1] + [{"price": "0.40", "size": "9"}]  # the seventh changes: nothing reads below the five best
    assert readings.reading("book", "market:polymarket:1", {**book, "bids": bids}, {}) == \
        readings.reading("book", "market:polymarket:1", {**book, "bids": moved}, {})
    assert readings.reading("book", "market:polymarket:1", {**book, "bids": bids}, {}) != first
    assert readings.reading("book", "market:polymarket:1", {"status": 404, "error": "not found"}, {}) is None
    assert readings.reading("book", "market:polymarket:1", {}, {}) == \
        {"bids": [], "asks": [], "last_trade": None}  # no orders: a reading, with no quotes


def live(store, monkeypatch) -> None:
    """The store's as-of is today."""
    with store.transaction():
        store._set_meta("asof", ASOF)
    monkeypatch.setattr(touches, "today", lambda: ASOF)


def source(answers: list, calls: list):
    """A client whose answers come in turn, each read recorded."""
    def read(subject, covering):
        calls.append((subject, covering))
        return {"payload": answers[0], "request": {"method": "GET", "url": f"https://example.test/{subject}"},
                "retrieved_at": "2026-11-04T15:00:00+00:00", "range": covering}
    return read


def moment(text: str):
    from datetime import datetime
    return datetime.fromisoformat(text)


def test_an_empty_answer_about_today_is_read_again_on_its_kinds_schedule_and_what_arrives_comes_in(store, monkeypatch):
    """No news, then news arrives: the empty answer is read again as a full one is, once its kind's interval has
    passed, and the store then serves the article; read, it is not read again until it is due. A kind that does not
    change within a day is read once."""
    live(store, monkeypatch)
    answers, calls = [{"status": "OK", "results": [], "request_id": "a"}], []
    store.fetchers["news"] = source(answers, calls)
    store.fetchers["figi"] = source([{"status": "OK", "listings": []}], calls)
    covering = ("2026-10-28", ASOF)
    assert store.data_payload(store.data("news", "NVDA", covering=covering)["id"])["results"] == []
    store.data("figi", "US67066G1040", covering=(ASOF, ASOF))
    calls.clear()
    article = {"title": "NVIDIA reports", "published": "2026-11-04T15:05:00Z"}
    answers[0] = {"status": "OK", "results": [article], "request_id": "b"}
    assert readings.refresh(store, at=moment("2026-11-04T15:10:00+00:00")) == [] and calls == []  # not yet due
    assert readings.refresh(store, at=moment("2026-11-04T15:16:00+00:00")) == [("news", "NVDA")]
    assert calls == [("NVDA", covering)]  # the news, and not the identifier's mapping
    assert store.data_payload(store.data("news", "NVDA", covering=covering)["id"])["results"] == [article]
    answers[0] = {**answers[0], "request_id": "c"}  # the same news, stamped anew
    assert readings.refresh(store, at=moment("2026-11-04T15:20:00+00:00")) == []  # read at 15:16: not due
    assert readings.refresh(store, at=moment("2026-11-04T15:32:00+00:00")) == []  # due, read, the same: not kept
    assert len(calls) == 2


def test_a_sessions_closes_are_read_again_once_it_has_closed_and_then_never(store, monkeypatch):
    """Daily bars read during the session have no close for it: they are read again once its exchange's calendar says
    it has closed and its closes are published, which brings the session's close in; after that, never again. Only a
    listing a live figure reads, a name the store holds or a proxy, is read again: another's (a comparable's) bars are
    read once for the as-of."""
    live(store, monkeypatch)
    monkeypatch.setattr(readings, "_live_subjects", lambda s: {"NVDA"})  # NVDA a name the store holds, AMD none
    before = {"status": "OK", "results": [{"t": 1793764800000, "c": 200.0}]}  # 2026-11-03's bar
    answers, calls = [before], []
    store.fetchers["bars"] = source(answers, calls)
    covering = ("2024-11-01", ASOF)
    store.data("bars", "NVDA", covering=covering)  # read at 15:00Z, 10:00 in New York
    store.data("bars", "AMD", covering=covering)
    calls.clear()
    answers[0] = {"status": "OK", "results": before["results"] + [{"t": 1793851200000, "c": 210.0}]}
    assert readings.refresh(store, at=moment("2026-11-04T20:00:00+00:00")) == [] and calls == []  # still open
    assert readings.refresh(store, at=moment("2026-11-04T21:10:00+00:00")) == [] and calls == []  # closed at 16:00
    assert readings.refresh(store, at=moment("2026-11-04T21:15:00+00:00")) == [("bars", "NVDA")]
    assert store.data_payload(store.data("bars", "NVDA", covering=covering)["id"])["results"][-1]["c"] == 210.0
    store.put_data("bars", "NVDA", answers[0], {"method": "GET", "url": "https://example.test/NVDA"},
                   retrieved_at="2026-11-04T21:16:00+00:00", range=covering)  # as a restart finds it
    readings._read.clear()
    assert readings.refresh(store, at=moment("2026-11-04T23:59:00+00:00")) == [] and len(calls) == 1


def test_a_reading_the_store_refuses_leaves_its_last_and_the_rest_of_the_round_lands(store, monkeypatch, caplog):
    """Two readings due in one round, one carrying a configured secret: it is refused, as any payload carrying one is,
    its last reading stands, and the other lands; the round does not stop at the refusal."""
    live(store, monkeypatch)
    covering = ("2026-10-28", ASOF)
    clean, calls = [{"status": "OK", "results": [], "request_id": "a"}], []
    store.fetchers["news"] = source(clean, calls)
    store.data("news", "NVDA", covering=covering)
    store.data("news", "AAPL", covering=covering)
    monkeypatch.setenv("MASSIVE_API_KEY", "sk-test-0123456789abcdef")

    def read(subject, covering):
        said = "leaked sk-test-0123456789abcdef" if subject == "AAPL" else "fresh"
        return {"payload": {"status": "OK", "results": [{"title": said}]},
                "request": {"method": "GET", "url": f"https://example.test/{subject}"},
                "retrieved_at": "2026-11-04T15:20:00+00:00", "range": covering}
    store.fetchers["news"] = read
    with caplog.at_level("WARNING", logger="harness.readings"):
        assert readings.refresh(store, at=moment("2026-11-04T15:30:00+00:00")) == [("news", "NVDA")]
    # the refusal is logged by what was read, never with the payload or the credential in it
    refused = [r.getMessage() for r in caplog.records if r.name == "harness.readings"]
    assert len(refused) == 1 and "news of AAPL refused" in refused[0]
    assert "sk-test" not in caplog.text and "leaked" not in caplog.text
    assert store.data_payload(store.data("news", "AAPL", covering=covering)["id"])["results"] == []
    assert store.data_payload(store.data("news", "NVDA", covering=covering)["id"])["results"] == [{"title": "fresh"}]


TRADED = 1793821617000000000  # a last trade at 19:46:57 UTC on the as-of, 2:46 PM in New York
REQUEST = {"method": "GET", "url": "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/"}


def traded(ticker: str, price: float, seconds_later: int = 0) -> dict:
    return {"status": "OK", "ticker": {"ticker": ticker, "lastTrade": {"p": price,
                                                                       "t": TRADED + seconds_later * 10**9}}}


def test_the_prices_are_from_the_last_read_every_holding_landed_through_a_failed_read_and_a_refused_write(store,
                                                                                                        monkeypatch):
    """Under the total, the moment as of which every price shown is still its holding's latest. A mark that has not
    moved is still the latest at the read that found it, though its trade is older; a read that fails, or a reading
    the store cannot keep, holds the moment at that holding's last good read, though another holding has traded since;
    once both land, it moves on."""
    from harness import render
    from harness.store import StoreError
    live(store, monkeypatch)
    for ticker in ("NVDA", "AMD"):
        put_reference(store, ticker, "CS")
        put_bars(store, ticker, {"2026-11-03": 100.0, ASOF: 101.0}, fetched="2026-11-04T19:40:00+00:00")
        store.put_data("snapshot", ticker, traded(ticker, 100.0), {**REQUEST, "url": REQUEST["url"] + ticker},
                       retrieved_at="2026-11-04T19:47:00+00:00", range=(ASOF, ASOF))
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "NVDA", "quantity": 10.0},
                                                       {"ticker": "AMD", "quantity": 10.0}]})
    answers: dict = {}

    def read(subject, covering):
        got = answers[subject]
        if isinstance(got, Exception):
            raise got
        return {"payload": got, "request": {**REQUEST, "url": REQUEST["url"] + subject},
                "retrieved_at": answers["at"], "range": covering}

    store.fetchers["snapshot"] = read

    def when() -> tuple[str | None, str]:
        store.engine.reset()
        held = store.engine.value("holdings", "portfolio").output
        return readings.prices_at(store, held), render._when(store, held)

    assert when()[0] == "2026-11-04T19:47:00+00:00"  # as first read
    answers.update(NVDA=traded("NVDA", 100.0), AMD=traded("AMD", 100.0), at="2026-11-04T19:48:00+00:00")
    assert readings.refresh(store) == []  # neither has moved: still the latest at 2:48 PM
    assert when() == ("2026-11-04T19:48:00+00:00", "Latest prices at 2:48 PM on Nov 4, 2026")
    answers.update(NVDA=RuntimeError("timed out"), AMD=traded("AMD", 102.0, 633), at="2026-11-04T19:58:00+00:00")
    assert readings.refresh(store) == [("snapshot", "AMD")]  # AMD traded at 2:57 PM; NVDA was not read
    assert when() == ("2026-11-04T19:48:00+00:00", "Latest prices at 2:48 PM on Nov 4, 2026")
    real = store.put_data

    def refuses_nvda(kind, subject, *a, **kw):
        if subject == "NVDA":
            raise StoreError("a credential in the answer")
        return real(kind, subject, *a, **kw)

    monkeypatch.setattr(store, "put_data", refuses_nvda)
    answers.update(NVDA=traded("NVDA", 99.0, 700), AMD=traded("AMD", 102.0, 633), at="2026-11-04T19:59:00+00:00")
    assert readings.refresh(store) == []  # NVDA moved, and its reading was not kept: its price is the one before
    assert when()[0] == "2026-11-04T19:48:00+00:00"
    monkeypatch.setattr(store, "put_data", real)
    answers["at"] = "2026-11-04T20:00:00+00:00"
    assert readings.refresh(store) == [("snapshot", "NVDA")]
    assert when() == ("2026-11-04T20:00:00+00:00", "Latest prices at 3:00 PM on Nov 4, 2026")
