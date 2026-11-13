"""The estimation (MATH.md 1.2, 1.3, 3.2): the channels' covariance over their common history, a series' response over
the same and the previous session under its prior, a fund's prior from its rows, and the volatility's sessions before
a holding's history, on hand-built series with known answers."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from harness.derive import channels, default_registry, estimate
from harness.store import Store
from massive_data import ASOF, KNOWLEDGE, SESSIONS, closes_from, put_bars, put_hourly, put_reference
from breakdown import stated

DAYS = SESSIONS[1:]


# ---------------------------------------------------------------- a series' response, on its own


def test_a_series_that_is_the_channel_a_session_late_is_fitted_at_one():
    """F = a + a_-1, and H = F_-1: H's whole response to F is one, though none of it is in the same session."""
    rng = np.random.default_rng(0)
    a = rng.normal(size=20002)
    x = (a[1:] + a[:-1])[:, None]  # row t: F on session t + 1
    var = float(np.var(x))
    spans = [(i, i + 1) for i in range(1, len(x))]  # H on session i + 1 is F on session i, row i - 1
    fit = estimate.fit_response(x[:-1, 0], spans, x, estimate.market_prior(np.array([[var]]), var, 1.0),
                                gamma0=np.array([[var]]), market_variance=var, lag_strength=1.0)
    assert fit["session"][:, 0] == pytest.approx([0.0, 1.0], abs=1e-6)  # all of it a session late
    assert fit["full"][0] == pytest.approx(1.0, abs=1e-6)


def test_with_no_returns_a_series_is_its_priors_centre_all_on_the_session():
    prior = estimate.Prior(np.array([1.0]), np.eye(1) * 100.0, {"from": "test"})
    fit = estimate.fit_response(np.zeros(0), [], np.zeros((10, 1)), prior, gamma0=np.eye(1) * 1e-4,
                                market_variance=1e-4, lag_strength=1.0)
    assert fit["session"][:, 0] == pytest.approx([1.0, 0.0], abs=1e-12)
    assert fit["full"] == pytest.approx([1.0], abs=1e-12) and fit["sigma2"] == pytest.approx(1e-4)


def test_the_covariance_of_completed_moves_is_bartletts_and_a_dropped_session_is_no_ones_previous():
    """Omega = Gamma_0 + sum_h (1 - h / (L + 1)) (Gamma_h + Gamma_h'), by hand at L = 2 on four sessions; and with the
    third session left out of the common history, the second and fourth are two sessions apart, not one."""
    x = np.array([[1.0], [-1.0], [2.0], [0.0]])
    c = x[:, 0] - x[:, 0].mean()  # 0.5, -1.5, 1.5, -0.5
    g = [float(np.sum(c[h:] * c[:len(c) - h])) / 4 for h in range(3)]
    _, gamma0, omega = estimate.covariances(x, np.ones(4, dtype=bool), 2)
    assert gamma0[0, 0] == pytest.approx(g[0]) and omega[0, 0] == pytest.approx(g[0] + 2 * (2 / 3 * g[1] + g[2] / 3))
    x = np.array([[1.0], [-1.0], [5.0], [2.0]])
    ok = np.array([True, True, False, True])  # the third session left out: 1, -1 and 2 on sessions 1, 2 and 4
    c = [1 / 3, -5 / 3, 4 / 3]  # less their mean, 2 / 3
    same = sum(v * v for v in c) / 3
    one, two = c[0] * c[1] / 3, c[1] * c[2] / 3  # sessions 1 and 2 are one apart, 2 and 4 two: never 2 and 4 one
    _, gamma0, omega = estimate.covariances(np.where(ok[:, None], x, np.nan), ok, 2)
    assert gamma0[0, 0] == pytest.approx(same) and omega[0, 0] == pytest.approx(same + 2 * (2 / 3 * one + two / 3))
    assert omega[0, 0] == pytest.approx(66 / 81)


# ---------------------------------------------------------------- the store


def market_store(tmp_path, rng, extra: tuple[str, ...] = ()) -> tuple[Store, dict]:
    """The market and semiconductors, and any `extra` proxies, as channels in that order: each later proxy a response
    to the market plus its own move."""
    store = Store.create(tmp_path / "scratch-estimation", registry=default_registry())
    store.load_initial(KNOWLEDGE)
    m = rng.normal(0, 0.01, len(DAYS))
    own = {"SOXX": rng.normal(0, 0.015, len(DAYS))}
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    put_bars(store, "SOXX", closes_from(dict(zip(DAYS, 1.2 * m + own["SOXX"]))))
    store.add("Factor", {"name": "semiconductors", "proxy": "SOXX", "order": 2})
    for i, t in enumerate(extra):
        own[t] = rng.normal(0, 0.008, len(DAYS))
        put_bars(store, t, closes_from(dict(zip(DAYS, -0.3 * m + own[t]))))
        store.add("Factor", {"name": f"the {t} channel", "proxy": t, "order": 3 + i})
    return store, {"market": m, **own}


def young_chip(store, rng, series: dict, ticker: str = "YOUNG", returns: int = 3) -> dict:
    days = DAYS[-returns:] if returns else []
    if returns:
        r = 0.9 * series["market"][-returns:] + 1.5 * series["SOXX"][-returns:] + rng.normal(0, 0.03, returns)
        put_bars(store, ticker, closes_from(dict(zip(days, r))))
    else:
        put_bars(store, ticker, {ASOF: 50.0})
    put_reference(store, ticker, "CS", "3674")
    return {"listing": ticker, "type": "CS", "industry": {"scheme": "SIC", "code": "3674"},
            "first_session": days[0] if days else ASOF, "last_session": ASOF, "traded": 1.0}


def fits(store) -> dict:
    return store.engine.value("betas", "portfolio").output["fits"]


def test_a_holding_that_prices_only_the_news_responds_by_a_half_where_its_session_alone_says_three_quarters(tmp_path):
    """The market's returns F_t = 0.5 F_t-1 + e_t, and a holding H_t = e_t, the news alone: nothing in H is late, yet
    H = F_t - 0.5 F_t-1, so its response to a completed move of the market is 0.5. Fitted over the same and the previous
    session it is that; on the same session alone it is cov(e, F) / var(F) = 1 - 0.5^2 = 0.75."""
    rng = np.random.default_rng(40)
    e = rng.normal(0, 0.01, len(DAYS) + 50)
    f = np.zeros_like(e)
    for t in range(1, len(e)):
        f[t] = 0.5 * f[t - 1] + e[t]
    f, e = f[50:], e[50:]
    store = Store.create(tmp_path / "scratch-news", registry=default_registry())
    store.load_initial(KNOWLEDGE)
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, f))))
    put_bars(store, "H", closes_from(dict(zip(DAYS, e))))
    put_reference(store, "H", "CS")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "H", "dollars": 1000.0}]})
    fit = fits(store)["name:H"]
    assert fit["full"][0] == pytest.approx(0.5, abs=1e-3)
    assert np.array(fit["session"])[:, 0] == pytest.approx([1.0, -0.5], abs=1e-3)
    mdl = channels.model(store.engine.value("factors", "market").output)
    same = dataclasses.replace(mdl, settings=dataclasses.replace(mdl.settings, lag_strength=math.inf))
    news = dict(zip(DAYS, e))
    found = [(i - 1, i, float(news[d])) for i, d in enumerate(mdl.sessions) if i]  # H's returns, session by session
    alone = same.fit(found, same.market_prior())  # the previous session removed: the same session alone
    assert alone["full"][0] == pytest.approx(0.75, abs=0.06)
    store.close()


@pytest.mark.parametrize("length", [20, 60, 252, 504])
def test_a_response_of_two_to_a_factor_is_recovered_on_the_sessions_both_have_whatever_the_factors_history(
        tmp_path, length):
    """A holding with two years of history that moves 2 times a factor's proxy, which has `length` returns: the
    channels and the holding's fit read the sessions every proxy has, so its response is 2 whatever the proxy's
    history, where integrating the factor's missing history pulled it toward none (0.16 at 20 returns)."""
    rng = np.random.default_rng(41)
    m, g = rng.normal(0, 0.01, len(DAYS)), rng.normal(0, 0.01, len(DAYS))
    store = Store.create(tmp_path / f"scratch-two-{length}", registry=default_registry())
    store.load_initial(KNOWLEDGE)
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    put_bars(store, "G", closes_from(dict(zip(DAYS[-length:], g[-length:]))))
    store.add("Factor", {"name": "the factor", "proxy": "G", "order": 2})
    put_bars(store, "H", closes_from(dict(zip(DAYS, 2.0 * g + rng.normal(0, 0.001, len(DAYS))))))
    put_reference(store, "H", "CS")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "H", "dollars": 1000.0}]})
    fit = fits(store)["name:H"]
    assert fit["full"] == pytest.approx([0.0, 2.0], abs=0.1 if length < 100 else 0.02)
    cid = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "G")
    window = store.engine.value("orthogonalize", cid).output
    assert window["n"] == length and window["window"][0] == DAYS[-length]  # the common history, by count and start
    assert fit["n"] == length - 1  # each return with the session before it in the common history
    store.close()


def test_a_fund_with_no_history_holding_a_share_quoted_in_won_keeps_the_wons_response_of_one(tmp_path):
    """A fund with no history of its own that holds one listing in won, beside a channel whose proxy is the won's rate:
    it leans on its row at the listing's response in dollars, its own local response plus the won's, 1 on the won's
    channel exactly, so it keeps the won's 1.0 where the local response alone would give it none."""
    store, _ = abroad_store(tmp_path, np.random.default_rng(42), channel=("C:KRWUSD", "KRW", "USD"), young_fund=True)
    found = fits(store)
    fund, share = found["name:FUNDX"], found[f"name:{ABROAD}"]
    assert fund["n"] == 0 and fund["prior"] == {"from": "rows"}
    assert fund["full"] == pytest.approx(share["full"], abs=1e-12)  # its exposure, 1.0, times the share's response
    assert np.array(share["full"]) - np.array(share["local"]["full"]) == pytest.approx([0.0, 1.0], abs=1e-12)
    assert fund["full"][1] == pytest.approx(1.0, abs=0.1) and abs(share["local"]["full"][1]) < 0.1
    store.close()


def test_a_fund_quoted_in_won_with_no_history_holding_a_share_in_won_responds_to_the_won_once(tmp_path):
    """A fund quoted in won, with no history of its own, holding one listing in won whose local price moves with
    nothing, beside a channel whose proxy is the won's rate. The fund is fitted on its translated local return and the
    won's response added, so its rows' prior is stated in that basis, the rows' response in dollars less the won's:
    its response is its listing's, 1 on the won, where the won counted twice made it 2 (MATH.md 3.2)."""
    from massive_data import put_home, put_rate
    from rows_fixture import holding, rows_registry
    rng = np.random.default_rng(45)
    store = Store.create(tmp_path / "scratch-quoted", registry=rows_registry())
    store.load_initial(KNOWLEDGE)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    level, levels = 0.00075, {SESSIONS[0]: 0.00075}
    for d, x, o in zip(DAYS, m, rng.normal(0, 0.004, len(DAYS))):
        level *= 1.0 + 0.5 * x + o
        levels[d] = level
    put_rate(store, "C:KRWUSD", levels, base="KRW", quote="USD")
    store.add("Factor", {"name": "the won", "proxy": "C:KRWUSD", "order": 2})
    put_home(store, ABROAD, closes_from(dict(zip(DAYS, rng.normal(0, 0.01, len(DAYS)))), 70000.0))
    put_reference(store, ABROAD, "CS")
    fund = "KR7FUNDX0001:KR"
    put_home(store, fund, {ASOF: 25000.0})
    put_reference(store, fund, "ETF")
    store.fixture_rows = {fund: [holding(ABROAD, 1.0)]}
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": fund, "dollars": 10000.0}]})
    found = fits(store)
    fitted, share = found[f"name:{fund}"], found[f"name:{ABROAD}"]
    assert fitted["n"] == 0 and fitted["currency"]["currency"] == "KRW" and fitted["rows"]["quoted"] == "KRW"
    assert fitted["full"] == pytest.approx(share["full"], abs=1e-12)  # its listing's response in dollars, once
    assert np.array(fitted["local"]["full"]) == pytest.approx(share["local"]["full"], abs=1e-12)
    assert fitted["full"][1] - fitted["local"]["full"][1] == pytest.approx(1.0, abs=1e-12)  # the won, once
    assert fitted["full"][1] == pytest.approx(1.0, abs=0.15)  # its listing's own, local response none but noise
    store.close()



def test_a_young_fund_booked_whole_leans_on_its_rows_bonds_and_cash_at_none_shares_at_the_market(tmp_path):
    """Two funds with no history of their own, beside the market and semiconductors, none of whose rows reaches a
    company, so each is booked whole: one holding bonds and dollar cash has no response its rows determine, its bonds
    left to its own history, of which it has none; one holding 60% in shares that resolve to no company and 40% in
    bonds rests on its shares, 0.6 on the market and none on semiconductors."""
    from rows_fixture import cash, holding, rows_registry
    rng = np.random.default_rng(44)
    store = Store.create(tmp_path / "scratch-whole", registry=rows_registry())
    store.load_initial(KNOWLEDGE)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    put_bars(store, "SOXX", closes_from(dict(zip(DAYS, 1.2 * m + rng.normal(0, 0.015, len(DAYS))))))
    store.add("Factor", {"name": "semiconductors", "proxy": "SOXX", "order": 2})

    def bond(weight: float) -> dict:
        return {"constituent_name": "A bond", "weight": weight, "asset_class": "Fixed Income", "kind": "debt",
                "exposure": weight, "payoff": "holding"}

    for fund in ("BONDS", "MIXED"):
        put_reference(store, fund, "ETF")
        put_bars(store, fund, {ASOF: 25.0})
    for ticker in ("ZZA", "ZZB"):
        put_reference(store, ticker, None)  # its source names no such listing: a share no company resolves for
    store.fixture_rows = {"BONDS": [bond(0.7), bond(0.25), cash(0.05)],
                          "MIXED": [holding("ZZA", 0.35), holding("ZZB", 0.25), bond(0.4)]}
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "BONDS", "dollars": 1000.0},
                                                       {"ticker": "MIXED", "dollars": 1000.0}]})
    found = fits(store)
    for fund, centre, bonds in (("BONDS", [0.0, 0.0], 0.95), ("MIXED", [0.6, 0.0], 0.4)):
        fit = found[f"name:{fund}"]
        assert fit["n"] == 0 and fit["prior"] == {"from": "rows", "undetermined": pytest.approx(bonds)}
        assert fit["rows"]["centre"] == pytest.approx(centre, abs=1e-12) and fit["full"] == pytest.approx(centre)
    looked = store.engine.value("lookthrough", "portfolio").output["funds"]
    assert not looked["BONDS"]["expanded"] and not looked["MIXED"]["expanded"]  # both booked whole
    store.close()

def _refit(store, ticker: str, prior):
    """A series refitted by production's Model.fit on what the betas' Computation read, under `prior`."""
    import json
    from harness.derive import estimate, history
    row = store._one("SELECT inputs FROM computation WHERE derivation = 'betas' ORDER BY rowid DESC LIMIT 1")
    m = json.loads(row["inputs"])
    mdl = estimate.Model.load(store.engine.load(m["factors"]))
    item = m["series"].get(ticker) or m["underlyings"][ticker]
    return mdl, mdl.fit(history.returns(item, store.engine.load, mdl.sessions)["own"], prior)


def test_a_young_funds_rows_say_what_they_determine_and_its_own_history_the_rest(tmp_path):
    """Four funds with 60 returns of their own (MATH.md 3.2). A coin fund, one row nothing identifies: its rows say no
    response for it, only their uncertainty, a dollar of it at one session's worth beside the fund's spread, so it is
    pulled with under one session's weight and keeps its own fit of about 2.5 nearly whole, where a centre of none at
    full strength pulled it far down. The same coin fund whose row names its pair: the pair's own response, fitted once
    on its series leaning on no response, as a pair held directly does. A fund of 60% in two companies and 40% in a
    commodity: its centre is the companies' own responses alone, nothing invented for the commodity, which only widens
    it. A fund with a swap at twice its value on an index whose values are read: twice the index's own response."""
    from harness.derive import estimate
    from massive_data import put_rate
    from rows_fixture import cash, holding, on_index, rows_registry
    rng = np.random.default_rng(43)
    store = Store.create(tmp_path / "scratch-identified", registry=rows_registry())
    store.load_initial(KNOWLEDGE)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    aaa, bbb = 1.1 * m + rng.normal(0, 0.01, len(DAYS)), 0.8 * m + rng.normal(0, 0.01, len(DAYS))
    for ticker, r in (("AAA", aaa), ("BBB", bbb)):
        put_bars(store, ticker, closes_from(dict(zip(DAYS, r))))
        put_reference(store, ticker, "CS")
    index = 1.3 * m + rng.normal(0, 0.005, len(DAYS))
    put_bars(store, "I:TESTIX", closes_from(dict(zip(DAYS, index))))
    coin = 2.5 * m + rng.normal(0, 0.03, len(DAYS))
    put_rate(store, "X:TSTUSD", closes_from(dict(zip(DAYS, coin)), 1000.0), base="TST", quote="USD")
    young = slice(len(DAYS) - 60, None)
    commodity = 0.2 * m + rng.normal(0, 0.015, len(DAYS))
    funds = {"COIN": coin + rng.normal(0, 0.002, len(DAYS)), "PAIRF": coin + rng.normal(0, 0.002, len(DAYS)),
             "MIX": 0.35 * aaa + 0.25 * bbb + 0.4 * commodity,
             "IDXF": 2.0 * index + rng.normal(0, 0.002, len(DAYS))}
    for fund, r in funds.items():
        put_reference(store, fund, "ETF")
        put_bars(store, fund, closes_from(dict(zip(DAYS[young], r[young]))))
    unnamed = {"constituent_name": "A coin", "weight": 1.0, "kind": "other", "exposure": 1.0, "payoff": "holding"}
    gold = {"constituent_name": "Gold", "weight": 0.4, "kind": "commodity", "exposure": 0.4, "payoff": "holding"}
    store.fixture_rows = {"COIN": [unnamed], "PAIRF": [{**unnamed, "to": {"pair": "X:TSTUSD"}}],
                          "MIX": [holding("AAA", 0.35), holding("BBB", 0.25), gold],
                          "IDXF": [on_index(2.0, values="I:TESTIX"), cash(1.0)]}
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": f, "dollars": 1000.0} for f in funds]})
    found = fits(store)
    betas = store.engine.value("betas", "portfolio").output
    zero = estimate.Prior(np.zeros(1), np.zeros((1, 1)), {})

    # the coin nothing identifies: no response, a dollar of it at one session's worth, the fund's spread beside it
    fit = found["name:COIN"]
    assert fit["n"] == 60 and fit["prior"] == {"from": "rows", "undetermined": 1.0}
    assert fit["rows"]["centre"] == [0.0] and fit["rows"]["undetermined"] == 1.0
    mdl, own = _refit(store, "COIN", zero)
    prior = mdl.rows_prior(np.zeros(1), np.zeros((1, 1)), undetermined=1.0)
    assert prior.precision == pytest.approx(mdl.omega / mdl.market_variance / (1.0 + 1.0 / 16.0), rel=1e-12)
    _, expected = _refit(store, "COIN", prior)
    assert fit["full"] == pytest.approx(expected["full"].tolist(), abs=1e-12)
    _, pulled = _refit(store, "COIN", mdl.rows_prior(np.zeros(1), np.zeros((1, 1))))  # none, at full strength
    assert own["full"][0] - fit["full"][0] < 0.25 * (own["full"][0] - pulled["full"][0])

    # the same coin, its row naming its pair: the pair's own response, fitted once, leaning on no response
    fit, pair = found["name:PAIRF"], betas["underlyings"]["X:TSTUSD"]
    assert pair["prior"] == {"from": "none"} and pair["n"] == found["name:AAA"]["n"]
    assert fit["prior"] == {"from": "rows"} and fit["rows"]["underlyings"] == {"X:TSTUSD": 1.0}
    assert fit["rows"]["centre"] == pytest.approx(pair["full"], abs=1e-12)
    assert pair["full"][0] == pytest.approx(2.5, abs=0.3)

    # 60% in companies, 40% in a commodity: the companies' own responses alone, the commodity's dollars widening it
    fit = found["name:MIX"]
    assert fit["prior"] == {"from": "rows", "undetermined": pytest.approx(0.4)}
    _, a_fit = _refit(store, "AAA", mdl.market_prior())
    _, b_fit = _refit(store, "BBB", mdl.market_prior())
    centre = 0.35 * a_fit["full"] + 0.25 * b_fit["full"]
    assert fit["rows"]["centre"] == pytest.approx(centre.tolist(), abs=1e-12)
    cov = 0.35 ** 2 * a_fit["cov"] + 0.25 ** 2 * b_fit["cov"]
    spread = cov + mdl.market_variance * (0.4 ** 2 + 1.0 / 16.0) * np.linalg.inv(mdl.omega)
    assert mdl.rows_prior(centre, cov, undetermined=0.4).precision == pytest.approx(np.linalg.inv(spread), rel=1e-9)
    _, expected = _refit(store, "MIX", mdl.rows_prior(centre, cov, undetermined=0.4))
    assert fit["full"] == pytest.approx(expected["full"].tolist(), abs=1e-12)

    # an index whose values are read: its own response, fitted once, twice over
    fit = found["name:IDXF"]
    response = betas["underlyings"]["I:TESTIX"]
    assert response["n"] == found["name:AAA"]["n"] and response["full"][0] == pytest.approx(1.3, abs=0.05)
    assert fit["prior"] == {"from": "rows"} and fit["rows"]["underlyings"] == {"I:TESTIX": 2.0}
    assert fit["rows"]["centre"] == pytest.approx([2.0 * response["full"][0]], abs=1e-12)
    store.close()


def test_a_young_names_prior_reads_no_order_of_the_channels(tmp_path):
    """The raw full response of a young name leaning on the market's sensitivity, and so the dollars at it, are the
    same whichever order the channels after the market stand in."""
    rng = np.random.default_rng(12)
    store, found = market_store(tmp_path, rng, extra=("TLT",))
    young_chip(store, rng, found, returns=20)
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "YOUNG", "dollars": 100.0}]})
    first = fits(store)["name:YOUNG"]["full"]
    channels = {c["stated"]["proxy"]: c["id"] for c in store.query("Factor")}
    store.edit(channels["SOXX"], "order", 4)  # semiconductors after TLT
    second = fits(store)["name:YOUNG"]["full"]
    # raw responses on (SPY, SOXX, TLT) before, and on (SPY, TLT, SOXX) after
    assert [second[0], second[2], second[1]] == pytest.approx(first, abs=1e-9)
    store.close()


def test_the_fill_reads_no_order_of_channels_2_to_k_with_a_young_proxy(tmp_path):
    """A consequence on semiconductors moves every other channel by its expected move given that one, from the
    channels' long-run covariance, so each channel's raw move, D, and each holding's dollars are the same whichever
    order channels 2 to K stand in, a proxy with thirty returns among them."""
    rng = np.random.default_rng(13)
    store, found = market_store(tmp_path, rng, extra=("TLT",))
    r = -0.5 * found["market"][-30:] + rng.normal(0, 0.01, 30)
    put_bars(store, "NEWPX", closes_from(dict(zip(DAYS[-30:], r))))
    store.add("Factor", {"name": "a young proxy", "proxy": "NEWPX", "order": 4})
    for i, t in enumerate(("CHIP0", "CHIP1")):
        put_bars(store, t, closes_from(dict(zip(DAYS, (0.8 + i) * found["market"] + found["SOXX"]
                                                      + rng.normal(0, 0.01, len(DAYS))))))
        put_reference(store, t, "CS")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "CHIP0", "dollars": 600.0},
                                                       {"ticker": "CHIP1", "dollars": 400.0}]})
    event = store.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                      "window_end": "2027-03-31", "chances": [stated(0.3)], "baseline": "no"})["id"]
    store.link("portfolio", "watches", event)
    semi = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "SOXX")
    store.link(event, "consequence", semi, {"shock": stated(-0.1)})

    def read() -> tuple[dict, float, dict]:
        out = store.engine.value("impact", event).output
        proxies = {c["id"]: c["stated"]["proxy"] for c in store.query("Factor")}
        return ({proxies[c]: v for c, v in out["against_baseline"]["yes"].items()}, out["full_impact"],
                {h["holding"]: h["full_impact"] for h in out["by_holding"]})

    raw, d, holdings = read()
    channels = {c["stated"]["proxy"]: c["id"] for c in store.query("Factor")}
    store.edit(channels["SOXX"], "order", 5)
    store.edit(channels["NEWPX"], "order", 2)
    raw2, d2, holdings2 = read()
    assert raw2 == pytest.approx(raw, abs=1e-12) and raw["SOXX"] == pytest.approx(-0.1)
    assert d2 == pytest.approx(d, abs=1e-9) and holdings2 == pytest.approx(holdings, abs=1e-9)
    assert raw["NEWPX"] != 0.0  # the young proxy moves with the market, so with semiconductors
    store.close()


def test_a_holding_listed_within_the_window_has_its_completed_moves_variance(tmp_path, monkeypatch):
    """F = a + b and H = a + b_-1: H responds a half on the session and a half the session after, its full response
    1, and what its fit leaves, half of a's and b's moves less half of the session before's, nearly cancels over a
    completed move. Its variance of completed moves per session is F's, 2 (in units of 1e-4), the channels' part of
    its full response, where its full response on the session alone with the rest as its own would give 3. Listed
    150 sessions before the as-of, it joins the channels over its own sessions (MATH.md 3.2). The lag is left to its
    150 sessions (one session's pull), so that they measure the half."""
    from dataclasses import replace
    from harness.derive import estimate
    monkeypatch.setattr(estimate, "SETTINGS", replace(estimate.SETTINGS, lag_strength=1.0))
    rng = np.random.default_rng(14)
    store = Store.create(tmp_path / "scratch-volatility", registry=default_registry())
    store.load_initial(KNOWLEDGE)
    a, b = rng.normal(0, 0.01, len(DAYS) + 1), rng.normal(0, 0.01, len(DAYS) + 1)
    f, h = a[1:] + b[1:], a[1:] + b[:-1]
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, f))))
    young = 150  # H's own sessions, the last of the window
    put_bars(store, "H", closes_from(dict(zip(DAYS[-young:], h[-young:]))))
    put_reference(store, "H", "CS")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "H", "dollars": 1.0}]})
    fit = fits(store)["name:H"]
    session = np.array(fit["session"])[:, 0]  # a half and a half, as 150 sessions measure them
    assert session == pytest.approx([0.5, 0.5], abs=0.25) and sum(session) == pytest.approx(1.0, abs=0.15)
    out = store.engine.value("volatility", "portfolio").output
    [held] = out["by_holding"].values()
    assert held["own_sessions"] == young  # each session of its 150 returns: it covers each alone
    assert out["variance"]["channels"] == pytest.approx(2e-4, rel=0.15)
    assert out["daily_sd"] ** 2 == pytest.approx(2e-4, rel=0.15)  # not 3e-4
    store.close()


# ---------------------------------------------------------------- a name quoted in another currency

ABROAD = "KR7005930003:KR"


def abroad_store(tmp_path, rng, *, channel: tuple[str, str, str] | None = None, fund: bool = False,
                 young_fund: bool = False) -> tuple:
    """A listing abroad, its price in won, and the won's rate in dollars at 0.5 times the market's move, exactly: held
    directly for $10,000, or through a fund that holds it at 60% beside a US company. With `channel`, a channel whose
    proxy is the won's rate, (pair, base, quote), which then moves by its own news as well; with `young_fund`, a fund
    with no history of its own that holds the listing alone."""
    from massive_data import put_home, put_rate
    from rows_fixture import holding, rows_registry
    store = Store.create(tmp_path / "scratch-abroad",
                         registry=rows_registry() if fund or young_fund else default_registry())
    store.load_initial(KNOWLEDGE)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    level, levels = 0.00075, {SESSIONS[0]: 0.00075}
    own = rng.normal(0, 0.004, len(DAYS)) if channel else np.zeros(len(DAYS))  # a channel's proxy is its own series
    for d, x, o in zip(DAYS, m, own):
        level *= 1.0 + 0.5 * x + o
        levels[d] = level
    put_rate(store, "C:KRWUSD", levels, base="KRW", quote="USD")
    if channel:
        pair, base, quote = channel
        if pair != "C:KRWUSD":
            put_rate(store, pair, {d: 1.0 / v for d, v in levels.items()}, base=base, quote=quote)
        store.add("Factor", {"name": "the won", "proxy": pair, "order": 2})
    local = closes_from(dict(zip(DAYS, 0.8 * m + rng.normal(0, 0.01, len(DAYS)))), 70000.0)
    put_home(store, ABROAD, local)
    put_reference(store, ABROAD, "CS")
    if young_fund:
        put_reference(store, "FUNDX", "ETF")
        put_bars(store, "FUNDX", {ASOF: 25.0})
        store.fixture_rows = {"FUNDX": [holding(ABROAD, 1.0)]}
    elif fund:
        put_reference(store, "FUNDX", "ETF")
        put_bars(store, "FUNDX", closes_from(dict(zip(DAYS, 0.9 * m + rng.normal(0, 0.002, len(DAYS))))))
        put_bars(store, "AAA", closes_from(dict(zip(DAYS, m + rng.normal(0, 0.01, len(DAYS))))))
        put_reference(store, "AAA", "CS")
        store.fixture_rows = {"FUNDX": [holding(ABROAD, 0.6), holding("AAA", 0.4)]}
    held = "FUNDX" if fund or young_fund else ABROAD
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": held, "dollars": 10000.0}]})
    return store, local[ASOF]


@pytest.mark.parametrize("channel", [None, ("C:KRWUSD", "KRW", "USD")])
def test_a_price_on_an_earlier_date_is_fitted_as_every_fit_is_a_listing_abroad_in_two_parts(tmp_path, channel):
    """MATH.md 1.6: the fit a price on an earlier date is carried by is production's own. For a listing abroad that
    is its local return fitted, and its currency's response, fitted once or a factor's whose proxy is its rate: at the
    as-of, the history up to the date is production's, and the full response is the same to the last digit."""
    store, _ = abroad_store(tmp_path, np.random.default_rng(7), channel=channel)
    try:
        production = store.engine.value("betas", "portfolio").output["fits"][f"name:{ABROAD}"]
        before = store.engine.value("fit_before", f"{ABROAD}@{ASOF}").output
        assert before["full"] == pytest.approx(production["full"], abs=1e-12)
        assert np.ravel(before["session"]) == pytest.approx(np.ravel(production["session"]), abs=1e-12)
    finally:
        store.close()


def test_a_price_on_an_earlier_date_of_a_pair_leans_on_no_response_as_every_fit_of_a_pair_does(tmp_path):
    """MATH.md 3.8 (3.2's fits): a currency or crypto pair leans on no response, where a listed share leans on the
    market's. A pair held with six returns, where the prior speaks loudly: its fit on its history up to the as-of is
    production's, to the last digit."""
    store = Store.create(tmp_path / "scratch-pair", registry=default_registry())
    try:
        store.load_initial(KNOWLEDGE)
        rng = np.random.default_rng(5)
        m = rng.normal(0, 0.01, len(DAYS))
        put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
        put_hourly(store, "X:BTCUSD", closes_from(dict(zip(DAYS[-6:], rng.normal(0, 0.03, 6))), 60000.0))
        put_reference(store, "X:BTCUSD", None)
        store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "X:BTCUSD", "dollars": 1000.0}]})
        production = store.engine.value("betas", "portfolio").output["fits"]["name:X:BTCUSD"]
        assert production["prior"] == {"from": "none"}
        before = store.engine.value("fit_before", f"X:BTCUSD@{ASOF}").output
        assert before["full"] == pytest.approx(production["full"], abs=1e-12)
    finally:
        store.close()


def event_on(store, local_price: float, *, market: float | None = None, rate: tuple[str, float] | None = None
             ) -> str:
    """An event at p = 0.25 whose consequences put the listing's price in won 20% lower, and the market or the rate's
    proxy where given."""
    e = store.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                  "window_end": "2027-03-31", "chances": [stated(0.25)], "baseline": "no"})["id"]
    store.link("portfolio", "watches", e)
    factor = {c["stated"]["proxy"]: c["id"] for c in store.query("Factor")}
    store.link(e, "consequence", f"name:{ABROAD}", {"shock": stated(-0.2)})
    if market is not None:
        store.link(e, "consequence", factor["SPY"], {"shock": stated(market)})
    if rate is not None:
        pair, move = rate
        store.link(e, "consequence", factor[pair], {"shock": stated(move)})
    return e


def abroad_worlds(rate_yes: float, q: float = 0.25) -> tuple[float, float]:
    """A listing at -20% in its own currency against the baseline, times its rate, `rate_yes` of the baseline's in the
    Yes world: each placed by 1.5, their product scaled to average to today's (1.6). Its value in each world per unit
    of today's."""
    local = (0.8 / (1 - q * 0.2), 1 / (1 - q * 0.2))
    fx = (rate_yes / (q * rate_yes + 1 - q), 1 / (q * rate_yes + 1 - q))
    product = (local[0] * fx[0], local[1] * fx[1])
    mean = q * product[0] + (1 - q) * product[1]
    return product[0] / mean, product[1] / mean


def test_a_name_quoted_in_won_is_valued_in_each_world_at_its_price_times_the_rate(tmp_path, monkeypatch):
    """$10,000 of a listing abroad at p = 0.25, its price in won -20% against the baseline: its local price placed and
    the won's rate placed, their product scaled to average to today's, so it has no expected value (1.6). The won's
    rate from its response to the market, half the market's -10% compounded, 0.9^0.5 of the baseline's (as its history
    measures it with no pull), or from a channel whose proxy is its rate, stated -5%."""
    from dataclasses import replace
    from harness.derive import estimate
    monkeypatch.setattr(estimate, "SETTINGS", replace(estimate.SETTINGS, kappa=0.0))
    from_betas, local = abroad_store(tmp_path / "betas", np.random.default_rng(20))
    from_channel, local2 = abroad_store(tmp_path / "channel", np.random.default_rng(20),
                                        channel=("C:KRWUSD", "KRW", "USD"))
    for store, e, rate_yes, by in ((from_betas, event_on(from_betas, local, market=-0.10), 0.9 ** 0.5, "betas"),
                                   (from_channel, event_on(from_channel, local2, rate=("C:KRWUSD", -0.05)), 0.95,
                                    "channel")):
        out = store.engine.value("impact", e).output
        yes, no = abroad_worlds(rate_yes)
        assert 10000 + out["yes"] == pytest.approx(10000 * yes, abs=0.01)
        assert 10000 + out["no"] == pytest.approx(10000 * no, abs=0.01)
        assert out["expected"] == pytest.approx(0.0, abs=1e-6)
        (s,) = [c for c in out["consequences"].values() if c.get("name")]
        assert s["currency"] == "KRW" and s["rate"]["yes"] == pytest.approx(rate_yes / (0.25 * rate_yes + 0.75) - 1,
                                                                            abs=1e-6)
        assert s["worlds"]["yes"]["whole"] == pytest.approx(-0.2)  # its shock is on its price in won
        assert s["rate_by"] == "betas" if by == "betas" else s["rate_by"].startswith("factor:")
        assert sum(h["worlds"]["yes"] for h in out["by_holding"]) == pytest.approx(out["yes"], abs=1e-9)
        store.close()


def test_a_series_a_session_late_keeps_its_response_where_its_closes_are_struck_before_new_yorks(tmp_path):
    """One listing abroad, quoted in dollars, that is the market a session late with its own news as large: its
    closes struck before New York opens on each session they belong to, its lag is shrunk at 16 sessions and its
    full response, 1, stands; the same returns struck at New York's close, at 256, and its response reads about a
    fifth small, the previous session's coefficient pulled toward none (MATH.md 3.2; evaluation's 47e)."""
    from datetime import timezone
    from harness.derive import series
    from massive_data import FETCHED, lookback
    found = {}
    for when in ("early", "at the close"):
        rng = np.random.default_rng(47)
        store = Store.create(tmp_path / when.replace(" ", "-"), registry=default_registry())
        store.load_initial(KNOWLEDGE)
        m = rng.normal(0, 0.01, len(DAYS))
        put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
        late = np.concatenate([[0.0], m[:-1]]) + rng.normal(0, 0.01, len(DAYS))  # the market a session late
        closes = closes_from(dict(zip(DAYS, late)), 100.0)
        moment = (lambda d: f"{d}T06:30:00Z") if when == "early" else \
            (lambda d: series.ny_close(d).astimezone(timezone.utc).isoformat())  # struck as New York's session closes
        payload = {"status": "OK", "listing": ABROAD, "symbol": "SYM", "exchange": "home", "mic": "XKRX",
                   "timezone": "Asia/Seoul", "currency": "USD", "dividends": [], "splits": [],
                   "results": [{"session": d, "moment": moment(d), "c": c} for d, c in sorted(closes.items())]}
        store.put_data("home", ABROAD, payload, {"method": "GET", "url": f"home:{ABROAD}"}, retrieved_at=FETCHED,
                       range=(lookback(ASOF), ASOF))
        put_reference(store, ABROAD, "CS")
        store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": ABROAD, "dollars": 1000.0}]})
        found[when] = fits(store)[f"name:{ABROAD}"]
        store.close()
    early, at_close = found["early"], found["at the close"]
    assert early["lag_strength"] == 16.0 and at_close["lag_strength"] == 256.0
    assert early["full"][0] == pytest.approx(1.0, abs=0.06)
    assert at_close["full"][0] < 0.88 and early["full"][0] - at_close["full"][0] > 0.1


def test_a_rate_quoted_the_other_way_round_is_read_as_its_reciprocal(tmp_path):
    """A channel whose proxy is won per dollar, 5% higher against the baseline: placed, 1.05 / 1.0125 and 1 / 1.0125
    of today's, and the won's rate in dollars is its reciprocal in each world; the product with the listing's local
    price is scaled to average to today's."""
    store, local = abroad_store(tmp_path, np.random.default_rng(21), channel=("C:USDKRW", "USD", "KRW"))
    e = event_on(store, local, rate=("C:USDKRW", 0.05))
    out = store.engine.value("impact", e).output
    proxy = (1.05 / 1.0125, 1 / 1.0125)  # won per dollar, placed
    local_ = (0.8 / 0.95, 1 / 0.95)
    product = (local_[0] / proxy[0], local_[1] / proxy[1])
    mean = 0.25 * product[0] + 0.75 * product[1]
    assert 10000 + out["yes"] == pytest.approx(10000 * product[0] / mean, abs=0.005)
    assert 10000 + out["no"] == pytest.approx(10000 * product[1] / mean, abs=0.005)
    (s,) = [c for c in out["consequences"].values() if c.get("name")]
    assert s["rate_by"] == next(c["id"] for c in store.query("Factor")
                                if c["stated"]["proxy"] == "C:USDKRW")
    store.close()


def test_the_same_name_through_a_fund_is_valued_in_each_world_and_the_funds_remainder_takes_the_rest(tmp_path):
    """Held through a fund at 60%, the listing's $6,000 of exposure moves as the listing does, valued in each world as
    its price times its rate, scaled: each holding's worlds, and the names' with the fund's remainder, sum to the
    row's, and the row has no expected value."""
    store, local = abroad_store(tmp_path, np.random.default_rng(22), channel=("C:KRWUSD", "KRW", "USD"), fund=True)
    e = event_on(store, local, rate=("C:KRWUSD", -0.05))
    out = store.engine.value("impact", e).output
    for w in ("yes", "no"):
        assert sum(h["worlds"][w] for h in out["by_holding"]) == pytest.approx(out["worlds"][w]["A"], abs=1e-9)
        assert sum(n["worlds"][w] for n in out["contributors"]) + sum(r["worlds"][w] for r in out["remainders"]) == \
            pytest.approx(out["worlds"][w]["A"], abs=1e-9)
    assert out["expected"] == pytest.approx(0.0, abs=1e-6)
    named = {n["name"]: n for n in out["contributors"]}[f"name:{ABROAD}"]
    yes, no = abroad_worlds(0.95)
    assert named["worlds"]["yes"] == pytest.approx(6000 * (yes - 1), abs=1e-6)
    assert named["worlds"]["no"] == pytest.approx(6000 * (no - 1), abs=1e-6)
    store.close()
