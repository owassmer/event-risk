"""A name over a past window (MATH.md 1.3, 1.4, 1.6, 3.2; TOOLS.md's window_returns): its move, what holding it earned
less the income it accrued, as a factor's is; its response over the window at its betas as fitted up to the window's
start, exp(b . z) on the factors' residuals over it; and its residual, (1 + move) / (1 + response) - 1, each in the
terms a shock on it is stated in. A listing abroad is measured in its own currency at its local betas, with its rate's
move beside them; a fund valued by its terms gives its move with what its terms are written on; a factor's proxy gives
its move, its residual being its factor's. The closes are the fixtures' own, read back from what the store keeps."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from harness.derive import default_registry
from harness.derive.identity import SEC_FILE
from harness.derive.impact import FOLLOWED, MOVE
from harness.derive.series import NY
from harness.store import Store
from harness.tools import Tools
from harness.trace import Trace
from harness.world import World
from massive_data import ASOF, FETCHED, KNOWLEDGE, closes_from, put_bars, put_home, put_rate, put_reference
from rows_fixture import cash, holding, on_reference
from test_estimation import ABROAD, abroad_store
from test_one_name import SEC, ref, taiwan
from test_one_name import fund as one_name_fund
from test_value_and_exposure import DAYS, store_with

START, END = DAYS[-40], DAYS[-20]


def with_history(store) -> None:
    """Each series read back to 2013, as a client reading the two years before a window's start would find it: the
    fixtures' own prices, which begin two years before the as-of."""
    for row in list(store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends', 'reference', 'home', 'hourly')")):
        store.put_data(row["kind"], row["subject"], store.data_payload(row["id"]), {"url": "test:history"},
                       retrieved_at="2026-11-05T02:00:00+00:00", range=("2013-01-01", ASOF))


def kept(store, kind: str, subject: str) -> dict:
    row = store._one("SELECT id FROM data WHERE kind = ? AND subject = ? ORDER BY id DESC LIMIT 1", kind, subject)
    return store.data_payload(row["id"])


def closes(store, ticker: str) -> dict[str, float]:
    return {datetime.fromtimestamp(r["t"] / 1000, NY).date().isoformat(): r["c"]
            for r in kept(store, "bars", ticker)["results"]}


def funds_store(tmp_path):
    """LEV carries three dollars of an index for each of its own; FND holds A and B half each; B is also held
    directly; IDX, the fund that follows LEV's index, is fitted beside them."""
    return store_with(tmp_path, {"LEV": [on_reference(3.0, [("A", 0.4), ("B", 0.6)], "IDX"), cash(1.0)],
                                 "FND": [holding("A", 0.5), holding("B", 0.5)]},
                      [{"ticker": "LEV", "dollars": 10_000.0}, {"ticker": "FND", "dollars": 10_000.0},
                       {"ticker": "B", "dollars": 5_000.0}],
                      {"LEV": "ETF", "IDX": "ETF", "FND": "ETF", "A": "CS", "B": "CS"}, seed=7)


@pytest.fixture
def funds(tmp_path):
    store = funds_store(tmp_path)
    with_history(store)
    yield store
    store.close()


def window(store, names):
    ok, out = Tools(World(store, None)).call("window_returns", {"start": START, "end": END, "names": names})
    assert ok, out
    return out


def test_a_companys_window_is_its_move_its_response_at_its_betas_then_and_its_residual(funds):
    """A, held only through FND, and B, held directly: each one's move is its closes' (no dividends here); its response
    is exp(b . z) at the betas fitted on its history up to the window's start, whose sessions end there, so the window
    enters none of them; and its residual is what the response leaves of its move."""
    out = window(funds, ["name:A", "name:B"])
    z = {f["factor"]: math.log1p(f[FOLLOWED]) for f in out["factors"]}
    for got, ticker in zip(out["names"], ("A", "B")):
        c = closes(funds, ticker)
        fit = funds.engine.value("fit_before", f"{ticker}@{START}").output
        assert fit["sessions"][-1] == START
        assert got["name"] == f"name:{ticker}" and got["betas"] == fit["betas"]
        assert got[MOVE] == pytest.approx(c[END] / c[START] - 1, rel=1e-12)
        assert got["response"] == pytest.approx(math.expm1(sum(b * z[f] for f, b in fit["betas"].items())), rel=1e-12)
        assert (1 + got[MOVE]) == pytest.approx((1 + got["response"]) * (1 + got[FOLLOWED]), rel=1e-12)


def test_a_fund_the_look_through_expands_is_measured_against_its_fit_a_fund_on_one_reference_by_its_terms(funds):
    """FND responds by its betas, so it has a residual; LEV moves by its multiple of its reference (MATH.md 1.6), so its
    window gives its move with its reference and multiple and no residual; SPY is the market's proxy, its move the
    factor's and its residual the factor's own."""
    out = window(funds, ["name:FND", "name:LEV", "name:SPY"])
    fnd, lev, spy = out["names"]
    assert {"response", FOLLOWED, "betas"} <= set(fnd)
    c = closes(funds, "LEV")
    assert lev == {"name": "name:LEV", MOVE: pytest.approx(c[END] / c[START] - 1, rel=1e-12),
                   "terms": {"reference": "name:IDX", "multiple": 3.0}, "basis": lev["basis"]}
    market = out["factors"][0]
    assert spy == {"name": "name:SPY", MOVE: market[MOVE], "proxy_of": market["factor"], "basis": spy["basis"]}


def test_a_window_over_many_names_stands_aside_once_for_all_their_reads(tmp_path):
    """A call stands aside, holding no lock, for each read it needs that the store does not hold (TOOLS.md 5). The
    two years before a window's start are read for every name's fit, and here the store holds none of them: the call
    starts every name's reads together and stands aside once for them, and each fit reads every series it needs on the
    day at once: four names take three passes, as one does, and each series is read once. Read a name a pass, and a
    fit a series a pass, nine names took more passes than a call makes."""
    store = funds_store(tmp_path)
    try:
        held = {(r["kind"], r["subject"]): store.data_payload(r["id"])
                for r in store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends')")}
        asked: list[tuple[str, str]] = []

        def client(kind):  # a source that answers any span, from the fixture's own prices
            def fetch(subject, covering):
                asked.append((kind, subject))
                return {"payload": held[(kind, subject)], "request": {"url": f"test:{kind}:{subject}"},
                        "retrieved_at": "2026-11-05T02:00:00+00:00", "range": tuple(covering)}
            return fetch
        store.fetchers.update({"bars": client("bars"), "dividends": client("dividends")})
        names = ["name:A", "name:B", "name:FND", "name:IDX"]
        out = window(store, names)
        assert [n["name"] for n in out["names"]] == names and all(FOLLOWED in n for n in out["names"])
        (call_,) = [e for e in Trace(store.trace_path).entries() if e["type"] == "call"]
        assert call_["passes"] <= 3  # its window and the factors', each fit's prices, their dividends
        assert len(asked) == len(set(asked))  # each series read once
        assert {s for k, s in asked if k == "bars"} >= {"SPY", "A", "B", "FND", "IDX"}
    finally:
        store.close()


def test_a_company_held_through_a_fund_is_measured_on_the_security_it_is_priced_through(tmp_path):
    """MATH.md 3.1, 1.4: a company held only through a fund is priced through its listing on its home market, in that
    market's currency, and a shock on it is a shock on that price; so its window reads that listing, in its own
    currency, with its rate's move beside it."""
    store = Store.create(tmp_path / "scratch-home", registry=default_registry())
    store.load_initial(KNOWLEDGE)
    store.put_data("sec_tickers", SEC_FILE, SEC, {"method": "GET", "url": "sec"}, retrieved_at=FETCHED,
                   range=(ASOF, ASOF))
    try:
        rng = np.random.default_rng(3)
        m = rng.normal(0, 0.01, len(DAYS))
        put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
        put_bars(store, "V", closes_from(dict(zip(DAYS, 0.9 * m + rng.normal(0, 0.004, len(DAYS))))))
        ref(store, "V", "ETF")
        taiwan(store)
        one_name_fund(store, "V", [{"constituent_ticker": "2330", "isin": "TW0002330008", "country_of_exchange": "TW",
                                    "weight": 0.5}])
        local = closes_from(dict(zip(DAYS, 0.8 * m + rng.normal(0, 0.01, len(DAYS)))), 1500.0)
        put_home(store, "TW0002330008:TW", local, currency="TWD", mic="XTAI")
        put_rate(store, "C:TWDUSD", {d: 0.031 * (1 + 0.001 * i) for i, d in enumerate(sorted(local))},
                 base="TWD", quote="USD")
        store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "V", "dollars": 1000.0}]})
        with_history(store)
        assert store.engine.value("lookthrough", "portfolio").output["kinds"]["name:TSM"]["security"] == \
            "TW0002330008:TW"
        (got,) = window(store, ["name:TSM"])["names"]
        assert got["name"] == "name:TSM" and got["currency"] == "TWD"
        assert got[MOVE] == pytest.approx(local[END] / local[START] - 1, rel=1e-12)
        assert {"response", FOLLOWED} <= set(got)
    finally:
        store.close()


def test_a_name_with_no_history_at_the_window_fails_the_call_with_why(funds):
    """A successful result is the true state (TOOLS.md 5): a name listed after the window began has no move over it and no
    fit before it, and the call says so, naming it."""
    young = {d: 50.0 for d in DAYS[-10:]}
    put_reference(funds, "NEW", "CS")
    put_bars(funds, "NEW", young)
    ok, got = Tools(World(funds, None)).call("window_returns", {"start": START, "end": END, "names": ["name:NEW"]})
    assert not ok and "NEW" in got["error"]
    ok, got = Tools(World(funds, None)).call("window_returns", {"start": START, "end": END, "names": ["NEW"]})
    assert not ok and got["error"] == "NEW is not a company or a fund: each is named name:{TICKER}"
    # a window the factors' proxies have no close over fails with that reason, whichever names are given with it
    ok, got = Tools(World(funds, None)).call("window_returns", {"start": "2019-01-02", "end": "2019-02-01",
                                                                "names": ["name:ZZZ", "name:A"]})
    assert not ok and got["error"].startswith("no factor's proxy has a close at both ends of the window")


def test_a_listing_abroad_is_measured_in_its_own_currency_at_its_local_betas_with_its_rates_move_beside(tmp_path):
    """A listing abroad (MATH.md 1.4): its shock is on its price in its own currency, so its window's move is its
    local closes', its response is exp(b . z) at its local betas as fitted up to the window's start, and its rate's
    move in dollars stands beside them, from the rate struck with each close. With the won a factor, the response in
    the factors' residuals is the response in their proxies' compounded moves at its raw local coefficients, as the
    proxies' moves are L times the residuals: two factors, the same figure."""
    store, _ = abroad_store(tmp_path, np.random.default_rng(7), channel=("C:KRWUSD", "KRW", "USD"))
    try:
        with_history(store)
        out = window(store, [f"name:{ABROAD}"])
        (got,) = out["names"]
        local = {r["session"]: r["c"] for r in kept(store, "home", ABROAD)["results"]}
        rate = {datetime.fromtimestamp(r["t"] / 1000, NY).date().isoformat(): r["c"]
                for r in kept(store, "hourly", "C:KRWUSD")["results"]}
        assert got[MOVE] == pytest.approx(local[END] / local[START] - 1, rel=1e-12)
        assert got["currency"] == "KRW" and got["rate_move"] == pytest.approx(rate[END] / rate[START] - 1, rel=1e-12)
        fit = store.engine.value("fit_before", f"{ABROAD}@{START}").output
        assert got["betas"] == fit["local_betas"] != fit["betas"]
        f = {x["factor"]: x for x in out["factors"]}
        assert len(f) == 2
        assert got["response"] == pytest.approx(
            math.expm1(sum(b * math.log1p(f[c][FOLLOWED]) for c, b in fit["local_betas"].items())), rel=1e-12)
        assert got["response"] == pytest.approx(
            math.expm1(sum(b * math.log1p(f[c][MOVE]) for c, b in zip(fit["raw_betas"], fit["local_full"]))), rel=1e-12)
        assert (1 + got[MOVE]) == pytest.approx((1 + got["response"]) * (1 + got[FOLLOWED]), rel=1e-12)
    finally:
        store.close()
