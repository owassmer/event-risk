"""Live readings: a holding given in dollars keeps the shares it was given at, and a reading taken again is kept only
when what a value reads of it differs."""

from __future__ import annotations

import pytest

from harness import readings
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
    assert store.get("portfolio")["derived"]["V"]["value"] == pytest.approx(9500.0)
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
    monkeypatch.setattr(readings, "today", lambda: ASOF)
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
    monkeypatch.setattr(readings, "today", lambda: "2026-11-05")
    answers.append(moved)
    assert readings.refresh(store) == [] and answers == [moved]  # the as-of has passed: nothing is read


def test_a_book_reads_as_its_five_best_levels_and_displayed_price_and_a_refusal_or_no_book_reads_as_none():
    book = {"market": "0x1", "asset_id": "1", "timestamp": "1", "bids": [{"price": "0.51", "size": "20"}],
            "asks": [{"price": "0.53", "size": "40"}]}
    first = readings.reading("book", "market:polymarket:1", book, {})
    assert first == {"bids": [(0.51, 20.0)], "asks": [(0.53, 40.0)], "price": pytest.approx(0.52)}
    assert readings.reading("book", "market:polymarket:1", {**book, "timestamp": "2", "hash": "h"}, {}) == first
    bids = book["bids"] + [{"price": f"0.{50 - k}", "size": "1"} for k in range(1, 7)]  # seven bids
    moved = bids[:-1] + [{"price": "0.40", "size": "9"}]  # the seventh changes: nothing reads below the five best
    assert readings.reading("book", "market:polymarket:1", {**book, "bids": bids}, {}) == \
        readings.reading("book", "market:polymarket:1", {**book, "bids": moved}, {})
    assert readings.reading("book", "market:polymarket:1", {**book, "bids": bids}, {}) != first
    assert readings.reading("book", "market:polymarket:1", {"status": 404, "error": "not found"}, {}) is None
    assert readings.reading("book", "market:polymarket:1", {}, {}) is None  # a closed market's: no book
