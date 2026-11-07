"""The initial contents in knowledge/: the market, one channel, and nothing else. The store ships no
metrics: the analyst adds each metric it uses, at its current level from a page it fetches."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.store import Store

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"
CHANNELS = ["S&P 500"]  # in plain words
PROXIES = ["SPY"]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    s = Store.create(tmp_path_factory.mktemp("knowledge") / "scratch-knowledge", initial=KNOWLEDGE)
    yield s
    s.close()


def test_the_initial_contents_are_the_market_alone(store):
    channels = sorted(store.query("Channel"), key=lambda c: c["stated"]["order"])
    assert [c["stated"]["name"] for c in channels] == CHANNELS
    assert [c["stated"]["proxy_ticker"] for c in channels] == PROXIES
    assert all(store.get(c["id"])["origin"] == "initial" for c in channels)
    assert all(set(c["stated"]) == {"name", "proxy_ticker", "order"} for c in channels)


def test_the_store_ships_no_metric_and_no_page(store):
    assert store.db.execute("SELECT count(*) FROM object WHERE type = 'Metric'").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM source").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM data").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM judgment").fetchone()[0] == 0
    # The only metrics are the prices the store derives: here, each channel's proxy.
    assert sorted(m["id"] for m in store.query("Metric")) == sorted(f"metric:price:{t}" for t in PROXIES)
    shipped = {p.name for p in KNOWLEDGE.iterdir()}
    assert shipped == {"channels.yaml", "system.md", "MATH.md", "ONTOLOGY.md", "TOOLS.md", "DATA.md"}


def test_each_channel_tracks_its_proxys_price(store):
    for c in store.query("Channel"):
        mid = f"metric:price:{c['stated']['proxy_ticker']}"
        got = store.get(mid)
        assert got["proxy_of"] == [c["id"]]
        assert [(l["relation"], l["other"], l.get("conversion")) for l in got["links"]] == \
            [("tracks", c["id"], "price")]
