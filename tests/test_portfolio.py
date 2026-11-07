"""The portfolio: holdings, look-through, the orthogonalization, the
betas, g, and the proxy Metrics, on synthetic data in Massive's shapes."""

from __future__ import annotations

import json

from datetime import datetime

import numpy as np
import pytest

from harness.derive import default_registry, series
from harness.derive.series import WINDOW
from harness.store import Store, StoreError
from massive_data import (ASOF, PROXIES, SESSIONS, closes_from, load_channels, put_bars,
                          put_constituents, put_hourly, put_market, put_reference, random_returns)



@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-portfolio", registry=default_registry())
    yield s
    s.close()


def hold(store, *holdings, total=None):
    fields = {"asof": ASOF, "holdings": [dict(h) for h in holdings]}
    if total is not None:
        fields["total"] = total
    return store.add("Portfolio", fields)


def lookthrough(store) -> dict:
    v = store.engine.value("lookthrough", "portfolio")
    assert v.output is not None, v.reason
    return v.output


def e(store, ticker) -> float:
    return store.get(f"name:{ticker}")["derived"]["e"]["value"]


def link_dollars(out, fund, ticker) -> float:
    return out["links"][f"looks_through_to:name:{fund}>name:{ticker}"]["fields"]["dollars"]


def names(store, *pairs):
    for ticker, kind in pairs:
        put_reference(store, ticker, kind)


# ---------------------------------------------------------------- holdings


def test_holdings_give_the_other_two_amounts_at_the_as_of_close_and_v(store):
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    put_bars(store, "NVDA", {"2026-11-03": 190.0, ASOF: 200.0})
    put_bars(store, "XOM", {"2026-11-03": 110.0, ASOF: 100.0})
    hold(store, {"ticker": "NVDA", "quantity": 10}, {"ticker": "XOM", "dollars": 3000.0},
         {"ticker": "CASH", "weight": 0.1}, total=10000.0)
    assert store.get("portfolio")["derived"]["V"]["value"] == pytest.approx(2000 + 3000 + 1000)
    amounts = {n["object"]["id"]: store.get(n["link"])["derived"] for n in store.neighbors("portfolio", "holds")}
    assert {k: v["value"] for k, v in amounts["name:NVDA"].items()} == {"dollars": 2000.0, "weight": pytest.approx(1 / 3)}
    assert amounts["name:XOM"]["quantity"]["value"] == pytest.approx(30.0)
    assert amounts["name:CASH"]["quantity"]["value"] == pytest.approx(1000.0)
    assert store.get("name:NVDA")["derived"]["prices"]["value"] == {
        "price": 200.0, "time": None, "session": ASOF, "from": "close", "previous_close": 190.0,
        "previous_session": "2026-11-03", "session_change": 10.0, "session_move": pytest.approx(200 / 190 - 1)}  # no snapshot here: the latest close


def test_a_bar_of_the_as_of_session_fetched_before_it_ended_is_not_a_close(store):
    names(store, ("NVDA", "CS"))
    put_bars(store, "NVDA", {"2026-11-02": 180.0, "2026-11-03": 190.0, ASOF: 200.0}, fetched="2026-11-04T18:00:00+00:00")
    hold(store, {"ticker": "NVDA", "quantity": 10})
    got = store.get("name:NVDA")["derived"]["prices"]["value"]
    assert (got["price"], got["session"], got["previous_close"]) == (190.0, "2026-11-03", 180.0)


def snapshot(store, ticker, *, trade=None, minute=None, prev=None, status="OK"):
    """A stock's snapshot in Massive's shape (DATA.md), read on the as-of date."""
    t = {"ticker": ticker}
    if trade:
        t["lastTrade"] = {"p": trade[0], "t": int(datetime.fromisoformat(trade[1]).timestamp() * 1e9), "s": 100}
    if minute:
        t["min"] = {"c": minute[0], "t": int(datetime.fromisoformat(minute[1]).timestamp() * 1000)}
    if prev:
        t["prevDay"] = {"c": prev}
    payload = {"status": status, "ticker": t} if status == "OK" else {"status": status, "http_status": 403}
    return store.put_data("snapshot", ticker, payload, {"method": "GET", "url": f"https://api.massive.com/v2/snapshot/"
                                                       f"locale/us/markets/stocks/tickers/{ticker}"},
                          retrieved_at="2026-11-04T19:47:00+00:00", range=(ASOF, ASOF))


def test_as_of_today_each_price_is_the_latest_mark_and_given_dollars_and_weights_are_kept(store):
    """Bars read during the as-of session: the as-of is today, and each price is the stock's last trade with its
    time. Dollars and weights the person gave are kept, their quantities implied at the marks; a quantity given is
    valued at its mark. Returns read completed sessions only."""
    names(store, ("NVDA", "CS"), ("XOM", "CS"), ("AAPL", "CS"))
    during = "2026-11-04T19:47:00+00:00"  # 14:47 in New York, the session not yet complete
    put_bars(store, "NVDA", {"2026-11-03": 200.0, ASOF: 181.0}, fetched=during)
    put_bars(store, "XOM", {"2026-11-03": 110.0, ASOF: 108.0}, fetched=during)
    put_bars(store, "AAPL", {"2026-11-03": 250.0, ASOF: 240.0}, fetched=during)
    snapshot(store, "NVDA", trade=(180.0, "2026-11-04T14:46:57-05:00"), minute=(180.5, "2026-11-04T14:46:00-05:00"))
    snapshot(store, "XOM", minute=(105.0, "2026-11-04T14:46:00-05:00"))  # no trade on this plan: the minute bar
    snapshot(store, "AAPL", status="NOT_AUTHORIZED")  # no mark: the latest completed close
    hold(store, {"ticker": "NVDA", "dollars": 9000.0}, {"ticker": "XOM", "weight": 0.21},
         {"ticker": "AAPL", "quantity": 10}, total=10000.0)
    nvda = store.get("name:NVDA")["derived"]["prices"]["value"]
    assert (nvda["price"], nvda["time"], nvda["from"]) == (180.0, "2026-11-04T14:46:57-05:00", "last trade")
    assert nvda["session_move"] == pytest.approx(180 / 200 - 1)  # against the last completed close
    xom = store.get("name:XOM")["derived"]["prices"]["value"]
    assert (xom["price"], xom["from"]) == (105.0, "latest minute bar")
    aapl = store.get("name:AAPL")["derived"]["prices"]["value"]
    assert (aapl["price"], aapl["session"], aapl["from"]) == (250.0, "2026-11-03", "close")
    amounts = {n["object"]["id"]: {k: v["value"] for k, v in store.get(n["link"])["derived"].items()}
               for n in store.neighbors("portfolio", "holds")}
    assert amounts["name:NVDA"]["quantity"] == pytest.approx(9000 / 180)  # the dollars given, kept
    assert amounts["name:XOM"]["dollars"] == pytest.approx(2100.0) and amounts["name:XOM"]["quantity"] == \
        pytest.approx(2100 / 105)
    assert amounts["name:AAPL"]["dollars"] == pytest.approx(2500.0)  # ten shares at the latest close
    assert store.get("portfolio")["derived"]["V"]["value"] == pytest.approx(9000 + 2100 + 2500)


# ---------------------------------------------------------------- look-through


def test_nested_funds_expand_once_more_with_the_floor_on_the_product(store):
    names(store, ("F1", "ETF"), ("F2", "ETF"), ("F3", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("D", "ADRC"),
          ("E", "CS"))
    put_constituents(store, "F1", [("A", 0.5, "Equity"), ("B", 0.3, "Equity"), ("F2", 0.2, "Equity")])
    put_constituents(store, "F2", [("C", 0.5, "Equity"), ("D", 0.39, "Equity"), ("F3", 0.1, "Equity"),
                                   ("E", 0.01, "Equity")])
    hold(store, {"ticker": "F1", "dollars": 10000.0})
    out = lookthrough(store)
    assert link_dollars(out, "F1", "F2") == pytest.approx(2000.0)
    assert link_dollars(out, "F2", "C") == pytest.approx(1000.0)
    assert link_dollars(out, "F2", "F3") == pytest.approx(200.0)  # a fund at the next depth is booked whole
    assert "looks_through_to:name:F2>name:E" not in out["links"]  # 0.2 * 0.01 is under the floor
    assert out["e"] == pytest.approx({"name:A": 5000.0, "name:B": 3000.0, "name:C": 1000.0, "name:D": 780.0,
                                      "name:F3": 200.0, "name:UNMAPPED:F2": 20.0})
    assert e(store, "F1") == 0.0 and e(store, "F2") == 0.0  # an expanded fund has e = 0
    assert out["funds"]["F2"]["depth"] == 2 and "F3" not in out["funds"]
    assert store.get("name:C")["because"] == ["looked through"]


def test_a_null_weight_and_a_sub_floor_weight_map_to_the_residual(store):
    names(store, ("G", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("D", "CS"))
    put_constituents(store, "G", [("A", 0.6, "Equity"), ("B", None, "Equity"), ("C", 0.002, "Equity"),
                                  ("D", 0.396, "Equity")])
    hold(store, {"ticker": "G", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 600.0, "name:D": 396.0, "name:UNMAPPED:G": 4.0})
    assert out["kinds"]["name:UNMAPPED:G"] == {"kind": "unmapped", "beta": "G"}  # the fund's own beta
    assert set(out["links"]) == {"looks_through_to:name:G>name:A", "looks_through_to:name:G>name:D",
                                 "looks_through_to:name:G>name:UNMAPPED:G"}
    with pytest.raises(StoreError):  # a constituent that maps to the residual is no Name of the portfolio
        store.get("name:B")


def test_cash_constituents_become_the_funds_cash(store):
    names(store, ("H", "ETF"), ("A", "CS"))
    put_constituents(store, "H", [("A", 0.95, "Equity"), ("USD", 0.05, "Cash")])
    hold(store, {"ticker": "H", "dollars": 2000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 1900.0, "name:CASH:H": 100.0})
    assert store.get("name:CASH:H")["derived"]["type"]["value"] == "cash"


def test_levered_both_ways_book_the_residual_to_unmapped_or_to_cash(store):
    names(store, ("L", "ETF"), ("S", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "L", [("A", 0.8, "Equity"), ("B", 0.5, "Equity")])  # 130 long: financed
    put_constituents(store, "S", [("A", 0.9, "Equity"), ("B", -0.2, "Equity")])  # long and short
    hold(store, {"ticker": "L", "dollars": 1000.0}, {"ticker": "S", "dollars": 1000.0})
    out = lookthrough(store)
    assert link_dollars(out, "L", "CASH:L") == pytest.approx(-300.0)  # negative: the financing leg
    assert link_dollars(out, "S", "UNMAPPED:S") == pytest.approx(300.0)  # positive: the fund's own beta
    assert link_dollars(out, "S", "B") == pytest.approx(-200.0)
    assert out["e"]["name:A"] == pytest.approx(800.0 + 900.0)
    assert sum(out["e"].values()) == pytest.approx(2000.0)


def test_weights_summing_outside_the_basket_book_the_fund_whole(store):
    names(store, ("X2", "ETF"), ("INV", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "X2", [("A", 1.2, "Equity"), ("B", 0.9, "Equity")])
    put_constituents(store, "INV", [("A", -1.0, "Equity")])
    hold(store, {"ticker": "X2", "dollars": 1000.0}, {"ticker": "INV", "dollars": 500.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:X2": 1000.0, "name:INV": 500.0})
    assert "2.1000" in out["funds"]["X2"]["reason"] and "-1.0000" in out["funds"]["INV"]["reason"]
    assert out["links"] == {}


def test_a_fund_that_maps_nothing_and_one_without_constituents_are_booked_whole(store):
    names(store, ("BND", "ETF"), ("GOLDT", "ETF"), ("NOTE1", None))
    put_constituents(store, "BND", [(None, 0.6, "Fixed Income"), ("NOTE1", 0.35, "Fixed Income"),
                                    ("USD", 0.05, "Cash")])
    put_constituents(store, "GOLDT", [], status="NOT_AUTHORIZED")
    hold(store, {"ticker": "BND", "dollars": 1000.0}, {"ticker": "GOLDT", "dollars": 500.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:BND": 1000.0, "name:GOLDT": 500.0})
    assert "US-listed share or fund" in out["funds"]["BND"]["reason"]
    assert out["funds"]["GOLDT"]["reason"] == "its holdings cannot be read here"


def test_a_name_held_directly_and_through_a_fund_sums_both(store):
    names(store, ("F", "ETF"), ("A", "CS"), ("B", "CS"))
    put_constituents(store, "F", [("A", 0.5, "Equity"), ("B", 0.5, "Equity")])
    hold(store, {"ticker": "A", "dollars": 1000.0}, {"ticker": "F", "dollars": 2000.0})
    assert e(store, "A") == pytest.approx(2000.0)
    assert store.get("name:A")["because"] == ["held", "looked through"]


def test_sum_of_e_is_v_to_one_cent(store):
    names(store, ("F1", "ETF"), ("F2", "ETF"), ("L", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("TLT", "ETF"),
          ("XOM", "CS"))
    put_constituents(store, "F1", [("A", 0.4123, "Equity"), ("F2", 0.3317, "Equity"), ("B", 0.0019, "Equity"),
                                   ("USD", 0.012, "Cash"), ("FUT", -0.0004, None)])
    put_constituents(store, "F2", [("B", 0.61, "Equity"), ("C", 0.3871, "Equity")])
    put_constituents(store, "L", [("A", 0.731, "Equity"), ("C", 0.5543, "Equity")])
    put_constituents(store, "TLT", [(None, 0.97, "Fixed Income"), ("USD", 0.03, "Cash")])
    hold(store, {"ticker": "F1", "weight": 0.37}, {"ticker": "L", "weight": 0.13}, {"ticker": "TLT", "weight": 0.1},
         {"ticker": "XOM", "dollars": 13333.33}, {"ticker": "CASH", "weight": 0.11}, total=87654.32)
    value = store.get("portfolio")["derived"]["V"]["value"]
    total = sum(n["derived"]["e"]["value"] for n in store.query("Name", {"e": {"ne": None}}))
    assert abs(total - value) < 0.01
    assert abs(lookthrough(store)["sum_e"] - value) < 0.01


# ---------------------------------------------------------------- the channels, the betas, and g


def channel_series(store) -> list[dict]:
    channels = sorted(store.query("Channel"), key=lambda c: c["stated"]["order"])
    return [store.engine.value("orthogonalize", c["id"]).output for c in channels]


def test_each_orthogonalized_channel_is_uncorrelated_in_sample_with_those_before_it(store):
    load_channels(store)
    put_market(store, np.random.default_rng(1))
    hold(store, {"ticker": "CASH", "dollars": 1.0})
    found = channel_series(store)
    days = sorted(found[0]["series"])
    assert len(days) == WINDOW and found[-1]["proxy"] == "IBIT"
    matrix = np.array([[c["series"].get(d, 0.0) for d in days] for c in found])
    for k in range(1, len(found)):
        for j in range(k):
            assert abs(np.corrcoef(matrix[k], matrix[j])[0, 1]) < 1e-9
        assert found[k]["coefficients"]  # each later channel regressed on the earlier ones


def test_betas_recover_known_coefficients_and_g_is_the_sum_of_e_times_b(store):
    load_channels(store)
    rng = np.random.default_rng(2)
    put_market(store, rng)
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    hold(store, {"ticker": "CASH", "dollars": 1.0})
    found = channel_series(store)
    days = sorted(found[0]["series"])
    ids = [c["channel"] for c in found]
    truth = {"NVDA": [1.6, 0.2, -0.4, 0.1, 0.0, 1.2, 0.3, -0.1, 0.5, 0.05],
             "XOM": [0.8, -0.1, 0.2, -0.3, 0.9, 0.0, 0.1, 0.2, 0.3, 0.0]}
    for ticker, b in truth.items():
        r = {d: 0.0002 + sum(bj * c["series"].get(d, 0.0) for bj, c in zip(b, found)) + float(rng.normal(0, 1e-6))
             for d in days}
        put_bars(store, ticker, closes_from(r))
    store.remove("portfolio")
    hold(store, {"ticker": "NVDA", "dollars": 15000.0}, {"ticker": "XOM", "dollars": 10000.0},
         {"ticker": "CASH", "dollars": 5000.0})
    for ticker, b in truth.items():
        got = store.get(f"name:{ticker}")["derived"]["b"]["value"]
        assert [got[c] for c in ids] == pytest.approx(b, abs=1e-3)
    assert store.get("name:CASH")["derived"]["b"]["value"] == dict.fromkeys(ids, 0.0)
    g = store.get("portfolio")["derived"]["g"]["value"]
    expected = {c: sum(e(store, t) * store.get(f"name:{t}")["derived"]["b"]["value"][c] for t in ("NVDA", "XOM", "CASH"))
                for c in ids}
    assert g == pytest.approx(expected)
    loads = store.get(f"loads_on:name:NVDA>{ids[5]}")
    assert loads["derived"]["beta"]["value"] == pytest.approx(1.2, abs=1e-3)


def test_a_name_with_fewer_than_250_returns_moves_with_the_market(store):
    load_channels(store)
    rng = np.random.default_rng(3)
    put_market(store, rng)
    names(store, ("NEW", "CS"), ("YEAR", "CS"))
    put_bars(store, "NEW", closes_from(random_returns(rng, days=SESSIONS[-249:])))  # 249 returns
    put_bars(store, "YEAR", closes_from(random_returns(rng, days=SESSIONS[-250:])))  # 250 returns
    hold(store, {"ticker": "NEW", "dollars": 1.0}, {"ticker": "YEAR", "dollars": 1.0})
    ids = [c["channel"] for c in channel_series(store)]
    assert list(store.get("name:NEW")["derived"]["b"]["value"].values()) == [1.0] + [0.0] * 9
    fits = store.engine.value("betas", "portfolio").output["fits"]
    assert fits["name:NEW"]["n"] == 249 and fits["name:YEAR"]["rule"] == "least squares"
    assert fits["name:YEAR"]["n"] == 250 and len(ids) == 10


def test_a_crypto_pair_set_as_a_proxy_is_read_at_16_00_new_york(store):
    load_channels(store)
    rng = np.random.default_rng(4)
    put_market(store, rng)
    prices = closes_from(random_returns(rng, 0.03))
    put_hourly(store, "X:BTCUSD", prices)
    hold(store, {"ticker": "CASH", "dollars": 1.0})
    crypto = next(c for c in store.query("Channel") if c["stated"]["proxy_ticker"] == "IBIT")
    store.edit(crypto["id"], "proxy_ticker", "X:BTCUSD")
    assert store.engine.value("orthogonalize", crypto["id"]).output["proxy"] == "X:BTCUSD"
    assert store.get(crypto["id"])["derived"]["orthogonalization"]["value"]["n"] == WINDOW
    btc = store.get("metric:price:X:BTCUSD")["derived"]
    assert btc["current_value"]["value"] == pytest.approx(prices[ASOF] * 1.03)  # as of today, its latest hourly bar
    series = store.engine.value("orthogonalize", crypto["id"]).output["series"]
    assert len(series) == WINDOW  # its returns read the 16:00 price of each completed day
    assert btc["resolution_source"]["value"] == ("Coinbase Exchange: the latest trade as of today, else the price at 16:00 "
                                                 "America/New_York")


def test_the_proxy_metric_is_the_proxys_price(store):
    load_channels(store)
    put_market(store, np.random.default_rng(5))
    hold(store, {"ticker": "CASH", "dollars": 1.0})
    spy = store.get("metric:price:SPY")["derived"]
    assert spy["current_value"]["value"] > 0 and spy["asof"]["value"] == ASOF and spy["traded"]["value"] is True
    ibit = store.get("metric:price:IBIT")["derived"]
    assert ibit["resolution_source"]["value"] == "Massive: the latest trade as of today, else the close"


def test_an_eleventh_channel_recomputes_every_beta_and_its_removal_returns_every_value(store):
    load_channels(store)
    rng = np.random.default_rng(6)
    put_market(store, rng)
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    for ticker in ("NVDA", "XOM", "BDRY"):
        put_bars(store, ticker, closes_from(random_returns(rng)))
    hold(store, {"ticker": "NVDA", "dollars": 15000.0}, {"ticker": "XOM", "dollars": 10000.0},
         {"ticker": "CASH", "dollars": 5000.0})
    calls = []
    store.fetchers.update({kind: (lambda subject, covering, kind=kind: calls.append((kind, subject)))
                           for kind in ("bars", "hourly", "dividends", "ticker_ref", "constituents")})

    def snapshot():
        ids = ["portfolio", "name:NVDA", "name:XOM", "name:CASH"] + [c["id"] for c in store.query("Channel")]
        return {i: store.get(i)["derived"] for i in ids}

    before = snapshot()
    rows = store.db.execute("SELECT count(*) FROM computation").fetchone()[0]
    ship = store.add("Channel", {"name": "dry bulk shipping", "proxy_ticker": "BDRY", "order": 11})
    during = snapshot()
    for name in ("name:NVDA", "name:XOM"):
        assert during[name]["b"]["basis"] != before[name]["b"]["basis"]
        assert ship["id"] in during[name]["b"]["value"]
    assert during["portfolio"]["g"]["basis"] != before["portfolio"]["g"]["basis"]
    added = store.db.execute("SELECT count(*) FROM computation").fetchone()[0]
    store.remove(ship["id"])
    assert snapshot() == {k: v for k, v in before.items()}
    assert store.db.execute("SELECT count(*) FROM computation").fetchone()[0] == added > rows
    assert calls == []


def test_a_row_reported_twice_counts_once_and_rows_sharing_a_ticker_stay_distinct(store):
    names(store, ("DUP", "ETF"), ("A", "CS"), ("B", "CS"))
    rows = [("A", 0.5, "Equity"), ("A", 0.5, "Equity"), ("B", 0.3, "Equity"), ("B", 0.2, "Equity")]
    put_constituents(store, "DUP", rows)  # A's row twice; B's two rows differ in weight
    hold(store, {"ticker": "DUP", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["funds"]["DUP"]["weights_sum"] == pytest.approx(1.0)  # not 1.5
    assert out["e"] == pytest.approx({"name:A": 500.0, "name:B": 500.0})


# ---------------------------------------------------------------- the Equity rule


def references_read(store) -> set[str]:
    """The tickers whose reference the look-through read."""
    basis = store.engine.value("lookthrough", "portfolio").basis
    inputs = json.loads(store.db.execute("SELECT inputs FROM computation WHERE id = ?", (basis,)).fetchone()[0])
    return set(inputs["references"])


def test_a_bond_fund_whose_tickers_collide_with_a_listed_company_maps_nothing_and_is_booked_whole(store):
    names(store, ("TLT", "ETF"), ("WIT", "ADRC"))  # WIT is Wipro's ADR, and the when-issued Treasuries' ticker
    put_constituents(store, "TLT", [("WIT", 0.031, "Fixed Income"), ("WIT", 0.027, "Fixed Income"),
                                    ("T 4.75 05/15/55", 0.937, "Fixed Income"), ("USD", 0.005, "Cash")])
    hold(store, {"ticker": "TLT", "dollars": 10000.0})
    out = lookthrough(store)
    assert out["e"] == {"name:TLT": 10000.0} and out["links"] == {}
    assert {k: v for k, v in out["funds"]["TLT"].items() if k != "holds"} == {"expanded": False,
                                   "reason": "none of its holdings is a US-listed share or fund"}
    assert out["kinds"]["name:TLT"] == {"kind": "fund", "beta": "TLT"}  # with its own beta
    assert references_read(store) == {"TLT"}  # a bond's ticker is not looked up
    with pytest.raises(StoreError):
        store.get("name:WIT")


def test_a_coin_fund_whose_ticker_collides_with_a_listed_fund_is_booked_whole(store):
    names(store, ("IBIT", "ETF"), ("BTC", "ETV"))  # BTC is the Grayscale Bitcoin Mini Trust, and bitcoin's ticker
    put_constituents(store, "IBIT", [("BTC", 1.0, "Alternative")])
    put_constituents(store, "BTC", [("BTC", 1.0, "Alternative")])
    hold(store, {"ticker": "IBIT", "dollars": 5000.0})
    out = lookthrough(store)
    assert out["e"] == {"name:IBIT": 5000.0} and out["links"] == {}
    assert set(out["funds"]) == {"IBIT"} and "US-listed share or fund" in out["funds"]["IBIT"]["reason"]
    assert references_read(store) == {"IBIT"}


def test_equity_rows_resolve_and_other_classes_go_to_the_residual(store):
    names(store, ("MIX", "ETF"), ("A", "CS"), ("D", "ADRC"), ("WIT", "ADRC"), ("BTC", "ETV"))
    put_constituents(store, "MIX", [("A", 0.5, "Equity"), ("D", 0.2, "Equity"), ("WIT", 0.1, "Fixed Income"),
                                    ("BTC", 0.15, "Alternative"), ("NQZ6", 0.03, None), ("USD", 0.02, "Cash")])
    hold(store, {"ticker": "MIX", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["e"] == pytest.approx({"name:A": 500.0, "name:D": 200.0, "name:UNMAPPED:MIX": 280.0,
                                      "name:CASH:MIX": 20.0})
    assert references_read(store) == {"MIX", "A", "D"}
    assert out["kinds"]["name:UNMAPPED:MIX"] == {"kind": "unmapped", "beta": "MIX"}


def test_an_equity_classed_fund_constituent_still_expands_at_the_next_depth(store):
    names(store, ("TOP", "ETF"), ("SUB", "ETF"), ("A", "CS"), ("B", "CS"), ("C", "CS"), ("WIT", "ADRC"))
    put_constituents(store, "TOP", [("A", 0.6, "Equity"), ("SUB", 0.4, "Equity")])
    put_constituents(store, "SUB", [("B", 0.5, "Equity"), ("C", 0.3, "Equity"), ("WIT", 0.2, "Fixed Income")])
    hold(store, {"ticker": "TOP", "dollars": 1000.0})
    out = lookthrough(store)
    assert out["funds"]["SUB"]["depth"] == 2 and out["funds"]["SUB"]["expanded"] is True
    assert out["e"] == pytest.approx({"name:A": 600.0, "name:B": 200.0, "name:C": 120.0, "name:UNMAPPED:SUB": 80.0})
    assert e(store, "SUB") == 0.0


# ---------------------------------------------------------------- total returns


def test_a_dividend_on_its_ex_date_is_added_back_to_that_sessions_return():
    sessions = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]
    closes = {"2026-10-28": 100.0, "2026-10-29": 101.0, "2026-10-30": 99.5, "2026-11-02": 99.0, "2026-11-03": 99.2}
    paid = {"2026-10-30": 1.2, "2026-11-01": 0.3}  # 2026-11-01 is a Sunday: it goes ex on Monday
    got = series.returns(closes, sessions, paid)
    assert got == pytest.approx({"2026-10-29": 101.0 / 100.0 - 1, "2026-10-30": (99.5 + 1.2) / 101.0 - 1,
                                 "2026-11-02": (99.0 + 0.3) / 99.5 - 1, "2026-11-03": 99.2 / 99.0 - 1}, abs=1e-15)
    # A dividend that went ex on or before the window's first session has no return in it.
    assert series.returns(closes, sessions, {"2026-10-28": 5.0, "2026-10-01": 5.0}) == series.returns(closes, sessions)


def test_a_ticker_with_no_dividends_has_its_close_to_close_returns():
    sessions = ["2026-10-29", "2026-10-30", "2026-11-02"]
    closes = {"2026-10-29": 50.0, "2026-10-30": 51.0, "2026-11-02": 50.49}
    plain = {"2026-10-30": 51.0 / 50.0 - 1, "2026-11-02": 50.49 / 51.0 - 1}
    assert series.returns(closes, sessions) == series.returns(closes, sessions, {}) == pytest.approx(plain, abs=1e-15)
    assert series.dividends({"status": "OK", "results": []}) == {}


def test_massives_dividends_read_by_ex_date_split_adjusted_one_record_once():
    payload = {"status": "OK", "results": [
        {"id": "E1", "ex_dividend_date": "2026-10-01", "cash_amount": 1.0, "split_adjusted_cash_amount": 0.25},
        {"id": "E1", "ex_dividend_date": "2026-10-01", "cash_amount": 1.0, "split_adjusted_cash_amount": 0.25},
        {"id": "E2", "ex_dividend_date": "2026-10-01", "cash_amount": 0.4, "split_adjusted_cash_amount": 0.1},
        {"id": "E3", "ex_dividend_date": "2026-11-02", "cash_amount": 0.3, "split_adjusted_cash_amount": 0.3}]}
    assert series.dividends(payload) == pytest.approx({"2026-10-01": 0.35, "2026-11-02": 0.3})


def test_the_market_channel_is_the_proxys_total_return(store):
    load_channels(store)
    rng = np.random.default_rng(7)
    put_market(store, rng)
    spy = closes_from(random_returns(rng))
    ex = SESSIONS[-3]
    put_bars(store, "SPY", spy, dividends={ex: 1.75})
    hold(store, {"ticker": "CASH", "dollars": 1.0})
    mkt = channel_series(store)[0]["series"]
    before = SESSIONS[SESSIONS.index(ex) - 1]
    assert mkt[ex] == pytest.approx((spy[ex] + 1.75) / spy[before] - 1, abs=1e-15)
    assert mkt[SESSIONS[-1]] == pytest.approx(spy[SESSIONS[-1]] / spy[SESSIONS[-2]] - 1, abs=1e-15)


def orthogonal(total: list[np.ndarray]) -> list[np.ndarray]:
    """The orthogonalization done by hand: channel 1 is its proxy's return, channel k the residual of proxy
    k on an intercept and channels 1 to k-1."""
    out = [total[0]]
    for y in total[1:]:
        design = np.column_stack([np.ones(len(y))] + out)
        coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
        out.append(y - design @ coefficients)
    return out


def paying(rng, total: dict[str, float], every: int, rate: float) -> tuple[dict[str, float], dict[str, float]]:
    """Closes and dividends whose total return is `total`: every `every` sessions a dividend
    of `rate` of the previous close goes ex, and the close falls by it."""
    days = sorted(total)
    level = 100.0
    closes, dividends = {SESSIONS[SESSIONS.index(days[0]) - 1]: level}, {}
    for i, d in enumerate(days):
        dividend = rate * level if i % every == every - 1 else 0.0
        level = level * (1.0 + total[d]) - dividend
        closes[d] = level
        if dividend:
            dividends[d] = dividend
    return closes, dividends


def test_betas_are_recovered_when_proxies_and_names_pay_dividends(store):
    load_channels(store)
    rng = np.random.default_rng(8)
    days = SESSIONS[-WINDOW:]
    sd = {"SPY": 0.01, "SHY": 0.001, "TLT": 0.008, "UUP": 0.004, "USO": 0.02, "EFA": 0.009, "EEM": 0.012,
          "GLD": 0.009, "HYG": 0.004, "IBIT": 0.03}
    pays = {"SPY": (63, 0.004), "SHY": (21, 0.003), "TLT": (21, 0.003), "HYG": (21, 0.005), "EFA": (126, 0.012), "EEM": (126, 0.01)}
    total, raw, priced = [], [], {}
    for ticker in PROXIES:
        own = rng.normal(0.0, sd[ticker], len(days))
        mixed = own + sum(0.3 * r for r in raw[-2:]) if raw else own
        raw.append(mixed)
        total.append(mixed)
        returns = dict(zip(days, (float(x) for x in mixed)))
        if ticker in pays:
            closes, dividends = paying(rng, returns, *pays[ticker])
            put_bars(store, ticker, closes, dividends=dividends)
            priced[ticker] = closes
        else:
            put_bars(store, ticker, closes_from(returns))
    basis = orthogonal(total)
    truth = {"NVDA": [1.6, 0.2, -0.4, 0.1, 0.0, 1.2, 0.3, -0.1, 0.5, 0.05],
             "XOM": [0.8, -0.1, 0.2, -0.3, 0.9, 0.0, 0.1, 0.2, 0.3, 0.0]}
    names(store, ("NVDA", "CS"), ("XOM", "CS"))
    for ticker, b in truth.items():
        r = 0.0002 + sum(bj * ch for bj, ch in zip(b, basis)) + rng.normal(0, 1e-6, len(days))
        returns = dict(zip(days, (float(x) for x in r)))
        if ticker == "XOM":  # a name pays too
            closes, dividends = paying(rng, returns, 63, 0.009)
            put_bars(store, ticker, closes, dividends=dividends)
        else:
            put_bars(store, ticker, closes_from(returns))
    hold(store, {"ticker": "NVDA", "dollars": 15000.0}, {"ticker": "XOM", "dollars": 10000.0})
    found = channel_series(store)
    for mine, theirs in zip(found, basis):
        assert [mine["series"][d] for d in days] == pytest.approx(list(theirs), abs=1e-12)
    ids = [c["channel"] for c in found]
    for ticker, b in truth.items():
        got = store.get(f"name:{ticker}")["derived"]["b"]["value"]
        assert [got[c] for c in ids] == pytest.approx(b, abs=1e-3)
    # Read as price returns, SHY's payout is a fall of three tenths of a percent each month,
    # three times its daily move, and the FRONT channel would be another series.
    sessions = SESSIONS[-(WINDOW + 1):]
    shy_price = series.returns(priced["SHY"], sessions)
    front_price = orthogonal([total[0], np.array([shy_price[d] for d in days])])[1]
    assert max(abs(found[1]["series"][d] - x) for d, x in zip(days, front_price)) > 2e-3


def test_a_window_return_adds_back_the_dividends_that_go_ex_inside_it_once():
    closes = {"2025-04-01": 100.0, "2025-04-02": 101.0, "2025-04-03": 97.0}
    paid = {"2025-04-01": 9.0, "2025-04-03": 0.5}  # ex on the start day is already out of its close
    assert series.window_return(closes, paid, "2025-04-01", "2025-04-03") == pytest.approx((97.0 + 0.5) / 100.0 - 1)
    assert series.window_return(closes, {}, "2025-04-01", "2025-04-03") == pytest.approx(97.0 / 100.0 - 1)
    assert series.window_return(closes, paid, "2025-03-31", "2025-04-03") is None  # no close at the start
