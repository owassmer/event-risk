"""Impact (MATH.md, sections 1 to 3) on hand-built structure against hand-computed
numbers: each meaning of a stated figure converted to its gap, the levels the pricing identity places, the vector, the
dollars, and each holding's share. Most tests replace g
with a fixture that reads stated values, and may fix a channel's orthogonalization coefficients, so the arithmetic runs
from its own inputs; the rest of the store is its own, over synthetic market data in Massive's shapes."""

from __future__ import annotations

import numpy as np
import pytest

from breakdown import RETRIEVED, World
from harness.derive import Derivation, Registry, channels, checks, default_registry, impact, markets, point, \
    portfolio, propagate
from harness.derive.impact import convert
from harness.ledger import check
from harness.store import StoreError
from massive_data import CHANNELS, closes_from, put_bars, put_constituents, put_market, put_reference, random_returns

# ---------------------------------------------------------------- g and the coefficients as fixtures


def _fx_g_inputs(ctx, subject):
    ids = {c["stated"]["proxy_ticker"]: c["id"] for c in ctx.objects("Channel")}
    return {"g": {ids[t]: v for t, v in getattr(ctx._store, "fixture", {}).get("g", {}).items() if t in ids}}


FX_G = Derivation("g", 1, "Portfolio", _fx_g_inputs, lambda m, load: {"g": m["g"]}, fields=("g",),
                  subject_of=lambda store, entity: "portfolio")


def _fx_orth_inputs(ctx, cid):
    """The store's own orthogonalization, its coefficients fixed where the fixture states them by proxy."""
    tickers = {c["id"]: c["stated"]["proxy_ticker"] for c in ctx.objects("Channel")}
    ids = {t: i for i, t in tickers.items()}
    fixed = getattr(ctx._store, "fixture", {}).get("coefficients", {}).get(tickers[cid])
    return {**channels._orthogonalize_inputs(ctx, cid),
            "fixture": None if fixed is None else {ids[t]: b for t, b in fixed.items()}}


def _fx_orth_compute(m, load):
    out = channels._orthogonalize_compute(m, load)
    return out if m["fixture"] is None else {**out, "coefficients": m["fixture"]}


FX_ORTH = Derivation("orthogonalize", 4, "Channel", _fx_orth_inputs, _fx_orth_compute,
                     fields=channels.ORTHOGONALIZE.fields, pick=channels.ORTHOGONALIZE.pick)


def fixture_registry() -> Registry:
    real = portfolio.DERIVATIONS + channels.DERIVATIONS + markets.DERIVATIONS + propagate.DERIVATIONS \
        + checks.DERIVATIONS + point.DERIVATIONS + impact.DERIVATIONS
    return Registry([d for d in real if d.name not in ("g", "orthogonalize")] + [FX_G, FX_ORTH])


def world(tmp_path, *, registry=None, g=None, coefficients=None, levels=None, answers=None) -> World:
    """The channels over synthetic history, each proxy closing at `levels` where given."""
    w = World(tmp_path / "scratch-impact", answers, registry=registry or fixture_registry())
    w.store.load_initial(CHANNELS)
    w.store.fixture = {"g": g or {}, "coefficients": coefficients or {}}
    put_market(w.store, np.random.default_rng(31))
    for ticker, last in (levels or {}).items():
        closes = closes_from(random_returns(np.random.default_rng(len(ticker)), 0.01))
        top = closes[max(closes)]
        put_bars(w.store, ticker, {d: c * last / top for d, c in closes.items()})
    return w


def page(w, name: str, text: str | None = None) -> str:
    sid = w.store.put_page(f"https://example.org/{name}", text or f"The {name} page.", retrieved_at=RETRIEVED)
    w.store.edit(sid, "dated", "2026-11-01", sid)
    return sid


def metric(w, mid, c, *, traded=True, tracks=None):
    w.store.add("Metric", {"id": mid, "name": f"the {mid.replace('_', ' ')}", "unit": "USD", "traded": traded, "resolution_source": f"{mid}'s publisher",
                           "current_value": {"value": c, "source_id": page(w, f"{mid}-level")}})
    for ticker, conversion in (tracks or {}).items():
        w.store.link(f"metric:{mid}", "tracks", channel(w, ticker), {"conversion": conversion})
    return f"metric:{mid}"


def channel(w, proxy):
    return next(c["id"] for c in w.store.query("Channel") if c["stated"]["proxy_ticker"] == proxy)


def derived(w, oid):
    return {k: v.get("value") for k, v in w.store.get(oid)["derived"].items()}


def output(w, pid, name="impact"):
    return w.store.engine.value(name, pid).output


def event(w, title="E", p=0.25, **fields):
    """A watched event at a stated chance p."""
    e = w.prop(title, chance={"value": p}, **fields)
    w.link("portfolio", "watches", e)
    return e


# ---------------------------------------------------------------- the stated figure, as the gap


def test_each_meaning_of_a_figure_converts_to_the_gap_by_the_pricing_identity():
    assert convert(14.0, "gap", 70.0, 0.25) == {"gap": 14.0}
    assert convert(80.5, "level_if_happens", 70.0, 0.25)["gap"] == pytest.approx((80.5 - 70) / 0.75) == pytest.approx(14)
    assert convert(66.5, "level_if_not", 70.0, 0.25)["gap"] == pytest.approx((70 - 66.5) / 0.25) == pytest.approx(14)
    assert "state the gap" in convert(80.0, "level_if_happens", 70.0, 1.0)["reason"]  # the identity cannot move it
    assert "state the gap" in convert(60.0, "level_if_not", 70.0, 0.0)["reason"]
    assert convert(14.0, "gap", None, None) == {"gap": 14.0}  # the gap needs neither


@pytest.mark.parametrize("figure, meaning", [(14.0, "gap"), (80.5, "level_if_happens"), (66.5, "level_if_not")])
def test_x_at_70_each_figure_places_80_5_and_66_5_and_one_swing(tmp_path, figure, meaning):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w)
    lid = w.link(e, "consequence", x, value=figure, meaning=meaning)
    d = derived(w, lid)
    assert d["gap"] == pytest.approx(14) and d["L_yes"] == pytest.approx(80.5) and d["L_no"] == pytest.approx(66.5)
    out = output(w, e)
    oil = channel(w, "USO")
    assert out["consequences"][x]["sets"][0]["raw"] == pytest.approx(0.2)  # 14 / 70
    assert out["D"] == pytest.approx(10_000 * 0.2)
    # Pricing-in applied once: the levels and the dollars are one calculation.
    assert out["pnl_yes"] == pytest.approx(10_000 * (d["L_yes"] - 70) / 70)
    assert out["pnl_no"] == pytest.approx(10_000 * (d["L_no"] - 70) / 70)
    assert out["primary_channel"] == oil and out["emove"] == pytest.approx(2 * 0.25 * 0.75 * 2000)


def test_a_level_converts_at_the_events_point_and_follows_it(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w)
    lid = w.link(e, "consequence", x, value=80.5, meaning="level_if_happens")
    w.store.edit(e, "chance", 0.5)
    assert derived(w, lid)["gap"] == pytest.approx(21) and derived(w, lid)["L_yes"] == pytest.approx(80.5)
    w.store.edit(e, "chance", 1.0)
    got = w.store.get(lid)["derived"]["gap"]
    assert got["value"] is None and "state the gap" in got["reason"]


def test_a_metric_no_market_prices_states_the_gap_and_shows_it_alone(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    rate = metric(w, "rate_pct", 22.0, traded=False)
    e = event(w)
    with pytest.raises(StoreError, match="not traded: a metric no market prices states the gap"):
        w.link(e, "consequence", rate, value=25.0, meaning="level_if_happens")
    lid = w.link(e, "consequence", rate, value=3.0, meaning="gap")
    got = w.store.get(lid)["derived"]
    assert got["gap"]["value"] == 3.0 and got["L_yes"]["value"] is None and "gap alone" in got["L_yes"]["reason"]
    y = metric(w, "y_level", 50.0)
    w.link(e, "consequence", y, value=55.0, meaning="level_if_happens")
    with pytest.raises(StoreError, match="stating its level"):
        w.store.edit(y, "traded", False)  # a level it states needs a market's price
    with pytest.raises(StoreError, match="meaning is one of gap, level_if_happens, level_if_not"):
        w.link(e, "consequence", metric(w, "z_level", 1.0), value=1.0, meaning="the level")


def test_a_gap_of_0_gives_no_impact(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w)
    w.link(e, "consequence", x, value=0.0, meaning="gap")
    out = output(w, e)
    assert out["D"] == 0 and out["pnl_yes"] == 0 and out["emove"] == 0


def test_a_consequence_without_a_basis_is_listed_until_a_page_or_a_note_gives_it_one(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w)
    w.store.note("A guess for the chance.", [e])
    lid = w.link(e, "consequence", x, value=14.0, meaning="gap")
    assert [(a["id"], a["field"]) for a in check(w.store)["assumptions_without_basis"]] == [(lid, "value")]
    sid = page(w, "episode", "In the last episode, X rose by 14 dollars over the quarter.")
    w.store.edit(lid, "value", {"value": 14.0, "source_id": sid, "passages": ["X rose by 14 dollars"]})
    assert check(w.store)["assumptions_without_basis"] == [] and w.store.citations_of(lid) == {"value": sid}
    w.store.remove(lid)
    assert w.store.source_row(sid)["kept"] is False  # with its last use gone, the page is held, not kept


def test_the_yield_conversion_multiplies_by_minus_duration_over_100(tmp_path):
    w = world(tmp_path, g={"TLT": 50_000.0})
    y = metric(w, "ten_year_yield", 4.2, traded=False, tracks={"TLT": "yield(17)"})
    e = event(w)
    w.link(e, "consequence", y, value=0.5, meaning="gap")
    out = output(w, e)
    assert out["consequences"][y]["sets"][0]["raw"] == pytest.approx(0.5 * -17 / 100)


def test_a_set_channel_less_what_the_earlier_set_channels_imply_and_an_unset_channel_at_0(tmp_path):
    w = world(tmp_path, g={"SPY": 80_000.0, "EEM": 30_000.0, "USO": 5_000.0},
              coefficients={"EEM": {"SPY": 1.4}, "USO": {"SPY": 0.5}}, levels={"SPY": 760.0, "EEM": 300.0})
    e = event(w, p=0.2)
    w.link(e, "consequence", "metric:price:SPY", value=-38.0, meaning="gap")
    w.link(e, "consequence", "metric:price:EEM", value=-30.0, meaning="gap")
    out = output(w, e)
    spy, eem, oil = channel(w, "SPY"), channel(w, "EEM"), channel(w, "USO")
    assert out["u_E"][spy] == pytest.approx(-0.05) and out["u_E"][eem] == pytest.approx(-0.10 + 1.4 * 0.05)
    assert out["u_E"][oil] == 0.0  # it moves with the earlier channels by its coefficients, and its component is 0
    assert out["D"] == pytest.approx(80_000 * -0.05 + 30_000 * (-0.10 + 0.07))


def test_a_second_metric_setting_a_channel_is_refused_naming_the_first(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w)
    w.link(e, "consequence", x, value=1.0, meaning="gap")
    with pytest.raises(StoreError, match="already sets it"):
        w.link(e, "consequence", "metric:price:USO", value=1.0, meaning="gap")


def test_an_event_with_no_consequence_has_no_swing_and_check_lists_it(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    e = event(w)
    got = w.store.get(e)["derived"]["D"]
    assert got["value"] is None and "has no consequence" in got["reason"]
    assert [x["id"] for x in check(w.store)["watched_without_consequence"]] == [e]


def test_editing_a_metrics_level_recomputes_the_swing(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w)
    w.link(e, "consequence", x, value=14.0, meaning="gap")
    assert output(w, e)["D"] == pytest.approx(2000)
    w.store.edit(x, "current_value", 140.0, page(w, "x-level-2"))
    assert output(w, e)["D"] == pytest.approx(1000)


# ---------------------------------------------------------------- the worked row


def test_the_worked_row_built_through_the_tools(tmp_path):
    """The worked row, every step a tool call: the semiconductors channel the analyst adds, with SOXX as its proxy; T's
    breakdown and its three markets; T reads R, which M1 asks at 0.305, with P(T given R) = 0.62; SPY at 760 ends at
    740 if T happens and SOXX at 300 at 285; g $88.5k on the S&P 500 and $38k on semiconductors, whose coefficient on
    the S&P 500 is 1.4. Its numbers at the page's rounding."""
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = world(tmp_path, g={"SPY": 88_500.0, "SOXX": 38_000.0}, coefficients={"SOXX": {"SPY": 1.4}},
              levels={"SPY": 760.0, "SOXX": 300.0}, answers={("implication", "T", "R"): 0.97})
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))

    def call(name, **input):
        ok, out = tools.call(name, input)
        assert ok, out
        return out

    def prop(title, relation="occurs", value=None, kind="hitting"):
        fields = {"title": title, "text": f"{title}: the proposition", "relation": relation,
                  "window_end": "2027-03-31", "kind": kind}
        if value is not None:
            fields.update(metric="metric:us_effective_tariff_rate_pct", value=value)
        if kind == "hitting":
            fields["window_start"] = "2026-11-04"
        return call("add", type="Proposition", fields=fields)["id"]

    assert "SOXX" not in {c["stated"]["proxy_ticker"] for c in w.store.query("Channel")}
    call("add", type="Channel", fields={"name": "semiconductors", "proxy_ticker": "SOXX", "order": 11})
    t, l, r = prop("T", ">=", 25), prop("L", ">=", 25, "level_at_date"), prop("R")
    a30, a20 = prop("A30", ">", 30, "level_at_date"), prop("A20", ">", 20, "level_at_date")
    m1 = w.market("1", "Will the US raise tariffs by March 31?", 0.28, 0.33)
    w.set_book(m1, 0.28, 0.33, size=14_000 / 0.33)  # $14k on the ask side
    m2 = w.market("2", "Will the US effective tariff rate be above 30% on March 31, 2027?", 0.10, 0.15)
    m3 = w.market("3", "Will the US effective tariff rate be above 20% on March 31, 2027?", 0.86, 0.90)
    call("link", from_id="portfolio", relation="watches", to_id=t)
    call("link", from_id=l, relation="implies", to_id=t)
    call("link", from_id=t, relation="implies", to_id=r)
    call("link", from_id=r, relation="quoted_by", to_id=m1)
    call("link", from_id=a30, relation="quoted_by", to_id=m2)
    call("link", from_id=a20, relation="quoted_by", to_id=m3)
    call("link", from_id=a30, relation="implies", to_id=l)
    call("link", from_id=l, relation="implies", to_id=a20)
    call("edit", id=t, field="derivation", value={"reads": [r], "chances": [{"given": [r], "value": 0.62}]})
    call("note", text="Given a raise, most paths take the rate past 25%.", about_ids=[t])
    call("link", from_id=t, relation="consequence", to_id="metric:price:SPY",
         fields={"value": 740.0, "meaning": "level_if_happens"})
    call("link", from_id=t, relation="consequence", to_id="metric:price:SOXX",
         fields={"value": 285.0, "meaning": "level_if_happens"})
    [row] = call("ledger")["rows"]
    assert row["p"] == pytest.approx(0.62 * 0.305) and (row["lo"], row["hi"]) == pytest.approx((0.10, 0.33))
    assert row["coverage"] == pytest.approx(0.77) and row["point"]["row"] == "narrower"
    soxx, spy = row["consequences"]  # by metric id
    assert round(spy["gap"], 2) == -24.66 and round(spy["L_no"], 2) == 764.66 and round(soxx["gap"], 2) == -18.50
    mkt, semi = channel(w, "SPY"), channel(w, "SOXX")
    assert round(100 * row["u_E"][mkt], 2) == -3.25 and round(100 * row["u_E"][semi], 2) == -1.62
    assert round(row["D"]) == -3489 and {c["channel"]: round(c["dollars"]) for c in row["contributions"]
                                          if c["dollars"]} == {mkt: -2872, semi: -617}
    assert (round(row["pnl_yes"]), round(row["pnl_no"]), round(row["emove"]), round(row["worst"])) == \
        (-2829, 660, 1070, -2829)
    assert row["primary_channel"]["name"] == "S&P 500"
    from harness.render import chance, money
    assert (chance(row["p"]), chance(row["lo"]), chance(row["hi"])) == ("19%", "10%", "33%")
    assert (money(row["pnl_yes"]), money(row["pnl_no"], sign=True), money(row["D"])) == \
        ("\u2212$2,828.95", "+$659.70", "\u2212$3,488.65")  # every dollar figure exact to the cent
    assert [g for k, g in call("check").items() if k != "assumptions_without_basis" and g] == []


# ---------------------------------------------------------------- names and holdings


def holding_world(tmp_path):
    """NVDA held directly, FUND holding NVDA and XOM, and cash; the betas are the store's own."""
    rng = np.random.default_rng(5)
    w = world(tmp_path, registry=default_registry())
    for ticker in ("NVDA", "XOM"):
        put_reference(w.store, ticker, "CS")
        put_bars(w.store, ticker, closes_from(random_returns(rng, 0.02)))
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("NVDA", 0.6, "Equity"), ("XOM", 0.35, "Equity")])
    put_bars(w.store, "FUND", closes_from(random_returns(rng, 0.015)))
    for ticker, dollars in (("NVDA", 5000.0), ("FUND", 4000.0)):
        w.store.link("portfolio", "holds", f"name:{ticker}", {"dollars": dollars})
    return w


def along(w, out, nid):
    """A name's move at its own betas: b . u_E."""
    b = w.store.engine.value("betas", "portfolio").output["objects"][nid]["fields"]["b"]
    return sum(b[c] * out["u_E"][c] for c in out["u_E"])


def test_each_holdings_dollars_in_each_world_sum_to_the_rows(tmp_path):
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w, p=0.3)
    w.link(e, "consequence", x, value=-7.0, meaning="gap")
    c = w.store.get("metric:price:NVDA")["derived"]["current_value"]["value"]
    w.link(e, "consequence", "metric:price:NVDA", value=-0.2 * c, meaning="gap")
    out = output(w, e)
    rows = {r["name"]: r for r in out["by_holding"]}
    assert set(rows) == {"name:NVDA", "name:FUND", "name:CASH"}
    assert sum(r["if_yes"] for r in rows.values()) == pytest.approx(out["pnl_yes"])
    assert sum(r["if_no"] for r in rows.values()) == pytest.approx(out["pnl_no"])
    assert rows["name:CASH"]["D"] == 0
    assert {n["name"] for n in rows["name:FUND"]["names"]} >= {"name:NVDA", "name:XOM"}  # what it reaches through FUND
    nvda_via_fund = next(n["D"] for n in rows["name:FUND"]["names"] if n["name"] == "name:NVDA")
    # the fund's move on its dollars at the name, and the name's own shock on them
    assert nvda_via_fund == pytest.approx(4000 * 0.6 * (along(w, out, "name:FUND") - 0.2 - along(w, out, "name:NVDA")))
    assert rows["name:NVDA"]["D"] == pytest.approx(5000 * -0.2)


def test_each_names_share_comes_to_its_dollars_in_each_world_on_the_rows_footing(tmp_path):
    """by_name is each name's share of D, e_i (b_i . u_E + d_i), with what it comes to if the event happens
    and if it does not by 1.1, as each holding has: all three on one footing, summing to the row's."""
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w, p=0.3)
    w.link(e, "consequence", x, value=-7.0, meaning="gap")
    c = w.store.get("metric:price:NVDA")["derived"]["current_value"]["value"]
    w.link(e, "consequence", "metric:price:NVDA", value=-0.2 * c, meaning="gap")
    out = output(w, e)
    names = {s["name"]: s for s in out["by_name"]}
    for nid, s in names.items():
        assert set(s) == {"name", "D", "if_yes", "if_no", "parts"}
        assert s["D"] == pytest.approx(s["parts"]["direct"] + sum(s["parts"]["through"].values()) + s["parts"]["own"])
        assert s["if_yes"] == pytest.approx(0.7 * s["D"]) and s["if_no"] == pytest.approx(-0.3 * s["D"])
    assert sum(s["D"] for s in names.values()) == pytest.approx(out["D"])
    assert sum(s["if_yes"] for s in names.values()) == pytest.approx(out["pnl_yes"])
    assert sum(s["if_no"] for s in names.values()) == pytest.approx(out["pnl_no"])
    # NVDA held directly at its whole move, -20%, and through the fund at the fund's move with its own shock
    nvda = 5000 * -0.2 + 4000 * 0.6 * (along(w, out, "name:FUND") - 0.2 - along(w, out, "name:NVDA"))
    assert names["name:NVDA"]["D"] == pytest.approx(nvda)
    assert names["name:NVDA"]["if_yes"] == pytest.approx(0.7 * nvda)
    assert [s["name"] for s in out["by_name"]] == sorted(names, key=lambda n: -abs(names[n]["D"]))


def test_a_consequence_on_a_names_price_sets_its_whole_move_and_its_d_is_what_its_betas_do_not_give(tmp_path):
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w, p=0.2)
    c = w.store.get("metric:price:NVDA")["derived"]["current_value"]["value"]
    w.link(e, "consequence", "metric:price:NVDA", value=-0.18 * c, meaning="gap")
    w.link(e, "consequence", x, value=7.0, meaning="gap")
    out = output(w, e)
    b = w.store.engine.value("betas", "portfolio").output["objects"]["name:NVDA"]["fields"]["b"]
    assert out["d"]["name:NVDA"] == pytest.approx(-0.18 - sum(b[ch] * out["u_E"][ch] for ch in b))
    nvda = next(s["D"] for s in out["by_name"] if s["name"] == "name:NVDA")
    # the whole move on its dollars held directly; through the fund, the fund's move with the name's own shock
    assert nvda == pytest.approx(5000 * -0.18 + 4000 * 0.6 * (along(w, out, "name:FUND") + out["d"]["name:NVDA"]))
    assert out["names_set"] == ["name:NVDA"]
    # the swing's terms as the inspector shows them: each channel's dollars, and the name's own, e_i d_i
    assert out["own"] == {"name:NVDA": pytest.approx((5000 + 4000 * 0.6) * out["d"]["name:NVDA"])}
    assert sum(out["contributions"].values()) + sum(out["own"].values()) == pytest.approx(out["D"])
    assert out["moves"]["name:NVDA"] == pytest.approx(-0.18)  # each name's move, which its share is its dollars times


def test_an_expanded_funds_price_tracks_nothing(tmp_path):
    w = holding_world(tmp_path)
    assert w.store.get("name:FUND")["derived"]["e"]["value"] == 0.0
    assert not w.store.neighbors("metric:price:FUND", "tracks")
    e = event(w)
    w.link(e, "consequence", "metric:price:FUND", value=-1.0, meaning="gap")
    w.link(e, "consequence", metric(w, "x_level", 70.0, tracks={"USO": "price"}), value=7.0, meaning="gap")
    out = output(w, e)
    assert out["consequences"]["metric:price:FUND"]["sets"] == [] and out["names_set"] == []


def test_adding_a_consequence_asks_no_unchanged_check_again(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    e = event(w)
    w.link(e, "quoted_by", w.market("e", "E?", 0.24, 0.26))
    w.link(e, "consequence", metric(w, "x_level", 70.0, tracks={"USO": "price"}), value=7.0, meaning="gap")
    check(w.store)
    asked = list(w.judge.calls)
    w.link(e, "consequence", metric(w, "y_level", 30.0, traded=False), value=1.0, meaning="gap")
    check(w.store)
    assert w.judge.calls == asked  # the market check reads the proposition and the market, and neither changed


def test_a_consequence_link_shows_what_it_sets_through_its_metrics_tracks_link(tmp_path):
    """link, edit, and get on a consequence give what it sets, read from the event's impact: a channel's raw
    swing, the part the earlier channels imply, and its component; a name's whole move, the part its betas give, and
    its d. An unwatched event has no impact, and the field says so."""
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = w.prop("E", chance={"value": 0.3})
    first = w.store.link(e, "consequence", x, {"value": 7.0, "meaning": "gap"})
    assert first["derived"]["sets"]["value"] is None and "not watched" in first["derived"]["sets"]["reason"]
    w.link("portfolio", "watches", e)
    got = w.store.get(first["id"])["derived"]["sets"]
    out = output(w, e)
    oil = channel(w, "USO")
    assert got["value"] == out["consequences"][x]["sets"]  # the impact's own numbers
    (s,) = got["value"]
    assert s["channel"] == oil and s["raw"] == pytest.approx(7.0 / 70.0)  # the gap over today's level
    assert s["component"] == pytest.approx(out["u_E"][oil]) and s["implied"] == pytest.approx(s["raw"] - s["component"])
    inputs = w.store.get(got["basis"])["inputs"]
    assert inputs["impact"]["basis"] == w.store.get(e)["derived"]["D"]["basis"]  # it reads the impact Computation

    c = w.store.get("metric:price:NVDA")["derived"]["current_value"]["value"]
    named = w.store.link(e, "consequence", "metric:price:NVDA", {"value": -0.2 * c, "meaning": "gap"})
    out = output(w, e)
    (n,) = named["derived"]["sets"]["value"]
    assert n["name"] == "name:NVDA" and n["raw"] == pytest.approx(-0.2) and n["d"] == pytest.approx(out["d"]["name:NVDA"])
    assert n["betas"] == pytest.approx(-0.2 - out["d"]["name:NVDA"])  # the part its betas give

    edited = w.store.edit(first["id"], "value", 14.0)
    assert edited["derived"]["sets"]["value"][0]["raw"] == pytest.approx(14.0 / 70.0)
    y = metric(w, "y_level", 10.0)  # tracks nothing
    nothing = w.store.link(e, "consequence", y, {"value": 1.0, "meaning": "gap"})["derived"]["sets"]
    assert nothing["value"] is None and "tracks no channel and no name" in nothing["reason"]
