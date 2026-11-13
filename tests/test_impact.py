"""Impact (MATH.md 1.1, 1.3 to 1.7) on hand-built structure against hand-computed numbers: the shock each consequence
states against its event's baseline, the levels and the moves placed against today, the factors filled in compounded
moves, the dollars in each world, and each holding's and name's share. Most tests state the portfolio's dollar beta g
as a fixture, carried by holdings that move one for one with a single factor (a holding of g[c] dollars at a beta of 1
on factor c, or -1 where g[c] is short), so a world's P&L is its holdings summed as in production, and may fix a
channel's orthogonalization coefficients, so the arithmetic runs from its own inputs; the rest of the store is its own,
over synthetic market data in Massive's shapes."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from breakdown import RETRIEVED, WHY, World, stated
from harness import schema
from harness.derive import Derivation, Registry, channels, default_registry, impact
from harness.ledger import check, event_now
from harness.store import StoreError
from massive_data import CHANNELS, SESSIONS, closes_from, factors, put_bars, put_constituents, put_market, put_reference, random_returns

# ---------------------------------------------------------------- g and the coefficients as fixtures


def carrier(proxy: str) -> str:
    """The ticker of the holding that carries the fixture's dollar beta to the factor whose proxy is `proxy`."""
    return f"G{proxy}"


def _fx_betas_inputs(ctx, subject):
    ids = {c["stated"]["proxy"]: c["id"] for c in ctx.objects("Factor")}
    fixture = getattr(ctx._store, "fixture", {})
    g = fixture.get("g", {})
    fixed = {schema.name_id(carrier(t)): {ids[t]: 1.0 if v > 0 else -1.0} for t, v in g.items() if t in ids and v}
    fixed.update({schema.name_id(t): {ids[p]: b for p, b in bs.items()}  # a name's betas, by its ticker
                  for t, bs in fixture.get("betas", {}).items()})
    return {**channels._betas_inputs(ctx, subject), "fixture": fixed}


def _fx_betas_compute(m, load):
    """The store's own betas, each carrier's fixed at one for one with its factor, and each name the fixture's table
    gives at its betas there."""
    out = channels._betas_compute({k: v for k, v in m.items() if k != "fixture"}, load)
    for nid, fixed in m["fixture"].items():
        betas = {c: fixed.get(c, 0.0) for c in out["channels"]}
        out["objects"][nid] = {"type": "Name", "fields": {**(out["objects"].get(nid) or {}).get("fields", {}),
                                                          "betas": betas}}
        for c, beta in betas.items():
            out["links"][f"beta_to:{nid}>{c}"] = {"relation": "beta_to", "from": nid, "to": c, "fields": {"beta": beta}}
    return out


FX_BETAS = Derivation("betas", 1, "Portfolio", _fx_betas_inputs, _fx_betas_compute,
                      subject_of=channels.BETAS.subject_of, produces=channels.BETAS.produces, stage="exposures")


def _fx_orth_inputs(ctx, cid):
    """The store's own orthogonalization, its coefficients fixed where the fixture states them by proxy."""
    tickers = {c["id"]: c["stated"]["proxy"] for c in ctx.objects("Factor")}
    ids = {t: i for i, t in tickers.items()}
    fixed = getattr(ctx._store, "fixture", {}).get("coefficients", {}).get(tickers[cid])
    variance = getattr(ctx._store, "fixture", {}).get("variances", {}).get(tickers[cid])
    return {**channels._orthogonalize_inputs(ctx, cid),
            "fixture": None if fixed is None else {ids[t]: b for t, b in fixed.items()}, "variance": variance}


def _fx_orth_compute(m, load):
    out = channels._orthogonalize_compute(m, load)
    out = out if m["fixture"] is None else {**out, "coefficients": m["fixture"]}
    return out if m["variance"] is None else {**out, "variance": m["variance"]}


FX_ORTH = Derivation("orthogonalize", 4, "Factor", _fx_orth_inputs, _fx_orth_compute,
                     fields=channels.ORTHOGONALIZE.fields, pick=channels.ORTHOGONALIZE.pick)


def fixture_registry() -> Registry:
    """The store's own derivations, every one, with the betas and the orthogonalization fixed as the fixture says."""
    real = default_registry()._by_name.values()
    return Registry([d for d in real if d.name not in ("betas", "orthogonalize")] + [FX_BETAS, FX_ORTH])


def market(store) -> None:
    """The ten proxies' synthetic history every world begins from (put_market at seed 31)."""
    put_market(store, np.random.default_rng(31))


def world(tmp_path, *, registry=None, g=None, coefficients=None, variances=None, levels=None, answers=None,
          channels=()) -> World:
    """The market and the factors whose proxies `g`, `coefficients`, `levels` or `channels` name, of the ten, over
    synthetic history for all ten, each proxy closing at `levels` where given; all ten where `channels` is CHANNELS."""
    w = World(tmp_path / "scratch-impact", answers, registry=registry or fixture_registry(), base=market)
    named = {*(g or {}), *(coefficients or {}), *(levels or {})}
    w.store.load_initial(channels if channels is CHANNELS else factors(tmp_path / "factors", named | set(channels)))
    w.store.fixture = {"g": g or {}, "coefficients": coefficients or {}, "variances": variances or {}}
    for proxy, dollars in (g or {}).items():  # the holdings that carry g
        if dollars:
            put_reference(w.store, carrier(proxy), "CS")
            put_bars(w.store, carrier(proxy), closes_from(random_returns(np.random.default_rng(len(proxy)), 0.01)))
            w.store.link("portfolio", "holds", schema.name_id(carrier(proxy)), {"dollars": abs(dollars)})
    for ticker, last in (levels or {}).items():
        closes = closes_from(random_returns(np.random.default_rng(sum(map(ord, ticker))), 0.01))
        top = closes[max(closes)]
        put_bars(w.store, ticker, {d: c * last / top for d, c in closes.items()})
    return w


def page(w, name: str, text: str | None = None) -> str:
    sid = w.store.put_page(f"https://example.org/{name}", text or f"The {name} page.", retrieved_at=RETRIEVED)
    w.store.edit(sid, "dated", "2026-11-01", sid)
    return sid


def metric(w, mid, c, *, kind="price"):
    """A metric at level c, a price unless `kind` says otherwise."""
    w.store.add("Metric", {"id": mid, "name": f"the {mid.replace('_', ' ')}", "unit": "USD", "kind": kind,
                           "resolution_source": f"{mid}'s publisher",
                           "level": {"value": c, "source_id": page(w, f"{mid}-level")}})
    return f"metric:{mid}"


def channel(w, proxy):
    return next(c["id"] for c in w.store.query("Factor") if c["stated"]["proxy"] == proxy)


def quoted(w, proxy, y, duration):
    """The factor whose proxy is `proxy`, added again quoted as the yield its proxy pays, `y` with its duration, each
    read from a page (ONTOLOGY.md 2.1): the way a factor comes to be quoted as a yield, since that is fixed when it is added."""
    old = channel(w, proxy)
    stated_ = w.store.stated_of(old)
    w.store.remove(old)
    sid = page(w, f"{proxy}-yield", f"The fund yields {y} percent, with a duration of {duration} years.")
    return w.store.add("Factor", {"name": stated_["name"], "proxy": proxy, "order": stated_["order"],
                                  "yield": {"value": y, "source_id": sid},
                                  "duration": {"value": duration, "source_id": sid}})["id"]


def derived(w, oid):
    return {k: v.get("value") for k, v in w.store.get(oid)["derived"].items()}


def output(w, pid, name="impact"):
    return w.store.engine.value(name, pid).output


def event(w, title="E", p=0.25, baseline="no", **fields):
    """A watched event at a stated chance p, its shocks stated against its No side unless named otherwise."""
    e = w.prop(title, chance={"value": p}, baseline=baseline, **fields)
    w.link("portfolio", "watches", e)
    return e


def placed(s: float, q: float) -> tuple[float, float]:
    """A price with one shock s against the No side, at weight q on the Yes side: its moves against today in the two
    worlds, (1 + s) / (1 + q s) - 1 and 1 / (1 + q s) - 1 (1.5)."""
    return (1 + s) / (1 + q * s) - 1, 1 / (1 + q * s) - 1


# ---------------------------------------------------------------- a shock, and where its worlds stand


def test_a_price_at_70_shocked_20_percent_stands_at_80_and_66_67_and_the_dollars_are_the_same_placing(tmp_path):
    """MATH.md 1.5's level, through the store: oil, its proxy at 70, a shock of +20% at p = 0.25. Its levels are 80.00 and
    66.67, and oil's placed moves give the dollars: D = $10,000 x 0.2 / 1.05."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}}, levels={"USO": 70.0})
    oil = channel(w, "USO")
    e = event(w)
    lid = w.link(e, "consequence", oil, shock=0.2)
    levels = derived(w, lid)["levels"]
    assert {k: round(v, 2) for k, v in levels.items()} == {"yes": 80.00, "no": 66.67}
    out = output(w, e)
    s = out["consequences"][lid]
    assert s["factor"] == oil and s["target"] == oil and s["c"] == pytest.approx(70.0)
    assert s["worlds"] == {"yes": {"raw": pytest.approx(0.2), "compounded": pytest.approx(math.log(1.2)),
                                   "implied": pytest.approx(0.0), "own_part": pytest.approx(math.log(1.2))}}
    # it applies in the Yes world, against the baseline; its component in z is its compounded move (1.3)
    assert out["full_impact"] == pytest.approx(10_000 * 0.2 / 1.05)
    # pricing-in applied once: the levels and the dollars are one calculation
    assert out["yes"] == pytest.approx(10_000 * (levels["yes"] / 70 - 1))
    assert out["no"] == pytest.approx(10_000 * (levels["no"] / 70 - 1))
    assert out["worlds"]["yes"] == {"side": "yes", "p": 0.25, "q": 0.25, "A": pytest.approx(out["yes"])}
    assert out["expected"] == pytest.approx(0.0, abs=1e-9)
    assert out["volatility"] == pytest.approx(math.sqrt(0.25 * 0.75) * abs(out["full_impact"]))
    assert out["worst_outcome"]["pnl"] == pytest.approx(out["no"]) and out["worst_outcome"]["world"] == "no"


def test_a_shock_on_a_price_is_never_below_minus_1_and_minus_1_is_worth_nothing(tmp_path):
    """A shock is a fraction of the price in the baseline (1.3): -1 is worth nothing there, oil's proxy at 0 if the
    event happens and 70 / 0.75 = 93.33 if not at p = 0.25; below -1 is refused, on the link and on an edit, on a
    factor and on a name, and the refusal says the shock is a fraction. A shock on a factor quoted as a yield is in
    points of the yield, and any number of points leaves its price above zero. A consequence runs to a factor or a
    name, never to a metric."""
    w = world(tmp_path, g={"USO": 10_000.0, "TLT": 5_000.0}, coefficients={"USO": {}, "TLT": {}},
              levels={"USO": 70.0})
    oil, tlt = channel(w, "USO"), quoted(w, "TLT", 4.2, 17.0)
    e = event(w)
    with pytest.raises(StoreError, match="is a fraction of its price at the place it is measured from, and no price "
                                         "is below zero"):
        w.link(e, "consequence", oil, shock=-1.01)
    with pytest.raises(StoreError, match="is a fraction of its price at the place.*-1 is worth nothing"):
        w.link(e, "consequence", oil, shock=-5.0)  # a percent stated out of habit
    with pytest.raises(StoreError, match="no price is below zero"):  # under either reference
        w.store.link(e, "consequence", oil, {"shock": stated({"value": -1.01, "against": schema.FOLLOWS})})
    lid = w.link(e, "consequence", oil, shock=-1.0)
    assert {k: round(v, 2) for k, v in derived(w, lid)["levels"].items()} == {"yes": 0.0, "no": 93.33}
    with pytest.raises(StoreError, match="no price is below zero"):
        w.store.edit(lid, "shock", stated(-1.5))
    w.link(e, "consequence", tlt, shock=-150.0)  # 150 points lower: its price higher, not below zero
    with pytest.raises(StoreError, match="no price is below zero"):
        w.link(e, "consequence", "name:GUSO", shock=-1.5)
    with pytest.raises(StoreError, match="consequence runs to Factor or Name"):
        w.link(e, "consequence", metric(w, "an_index", 30.0, kind="level"), shock=-0.15)


def test_a_shock_needs_its_events_baseline(tmp_path):
    """A shock needs its event's baseline (1.1, 1.3): a consequence on an event that names none is refused, and so is
    clearing the baseline while shocks are stated in the worlds measured from it. Naming the Yes side instead puts the shock on the No
    world: oil's proxy at 70 then stands at 70 / (0.25 + 0.75 x 1.2) = 60.87 if the event happens and 73.04 if not."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}}, levels={"USO": 70.0})
    x = channel(w, "USO")
    e = w.prop("E", chance={"value": 0.25})
    w.link("portfolio", "watches", e)
    with pytest.raises(StoreError, match="names none"):
        w.link(e, "consequence", x, shock=0.2)
    w.store.edit(e, "baseline", "no")
    lid = w.link(e, "consequence", x, shock=0.2)
    with pytest.raises(StoreError, match="measured from its baseline"):
        w.store.edit(e, "baseline", None)
    w.store.edit(e, "baseline", "yes")
    assert {k: round(v, 2) for k, v in derived(w, lid)["levels"].items()} == {"yes": 60.87, "no": 73.04}
    out = output(w, e)
    assert out["full_impact"] == pytest.approx(-10_000 * 0.2 / 1.15)  # the opposite sign to the shock
    assert set(out["consequences"][lid]["worlds"]) == {"no"}


def test_a_shock_of_0_gives_no_impact(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = channel(w, "USO")
    e = event(w)
    w.link(e, "consequence", x, shock=0.0)
    out = output(w, e)
    assert out["full_impact"] == 0 and out["yes"] == 0 and out["volatility"] == 0


def test_a_consequence_is_stated_with_its_basis_a_page_or_its_reasoning(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    x = channel(w, "USO")
    e = event(w)
    with pytest.raises(StoreError, match="shock is stated with its basis"):
        w.store.link(e, "consequence", x, {"shock": 0.2})
    assert w.store.links_of(e, "consequence") == []
    lid = w.store.link(e, "consequence", x, {"shock": {"value": 0.2, "note": "A guess at the size."}})["id"]
    guess = w.store.stated_of(lid)["shock"]["note"]
    sid = page(w, "episode", "In the last episode, X rose by a fifth over the quarter.")
    w.store.edit(lid, "shock", {"value": 0.2, "source_id": sid, "passages": ["X rose by a fifth"]})
    assert w.store.citations_of(lid) == {"shock": sid}
    assert w.store.type_of(guess) is None  # the reasoning written with the figure it no longer rests on goes with it
    w.store.remove(lid)
    assert w.store.source_row(sid)["kept"] is False  # with its last use gone, the page is held, not kept


def test_a_factor_quoted_as_a_yield_and_a_metric_that_quotes_it_each_read_back_in_its_own_unit(tmp_path):
    """Long Treasuries quoted as the yield their proxy pays, 4.2 at a duration of 17, the proxy at 90; a price metric,
    a futures price at 120, quoting the factor; +0.5 points at p = 0.25. The factor's compounded move is -0.085, its
    price placed once, L_base = 1 / (0.25 e^-0.085 + 0.75): the proxy stands at 90 e^-0.085 L_base and 90 L_base, the
    yield it shows is 4.2 - 100 ln(1 + m) / 17, 4.5789 and 4.0789, 0.5 apart as stated, and the futures price moves
    percent for percent with the proxy, 120 e^-0.085 L_base and 120 L_base (MATH.md 1.3, 1.5; ONTOLOGY.md 2.2). What the
    event now does shows each in its own unit."""
    w = world(tmp_path, g={"TLT": 50_000.0}, coefficients={"TLT": {}}, levels={"TLT": 90.0})
    tlt = quoted(w, "TLT", 4.2, 17.0)
    futures = metric(w, "bond_futures", 120.0)
    q = w.store.link(futures, "quotes", tlt)["id"]
    e = event(w)
    lid = w.link(e, "consequence", tlt, shock=0.5)
    base = 1 / (0.25 * math.exp(-0.085) + 0.75)
    m = {"yes": math.exp(-0.085) * base - 1, "no": base - 1}
    got = output(w, e)["consequences"][lid]
    assert got["levels"] == {"yes": pytest.approx(90 * (1 + m["yes"])), "no": pytest.approx(90 * (1 + m["no"]))}
    assert got["yield"] == 4.2 and {k: round(v, 4) for k, v in got["yields"].items()} == {"yes": 4.5789, "no": 4.0789}
    assert got["yields"]["yes"] - got["yields"]["no"] == pytest.approx(0.5)
    assert derived(w, q)["levels"] == {e: {"yes": pytest.approx(120 * (1 + m["yes"])),
                                           "no": pytest.approx(120 * (1 + m["no"]))}}
    now = event_now(w.store, e)["levels"][tlt]
    assert now["today"] == pytest.approx(90.0) and now["yield"] == 4.2 and now["yields"]["yes"] == pytest.approx(
        got["yields"]["yes"], abs=1e-6)
    assert now["quoted"][futures]["worlds"]["yes"] == pytest.approx(120 * (1 + m["yes"]), abs=1e-6)
    # its move against the baseline reads in the points of yield the shock is stated in, not as its proxy's price
    (moved,) = [f for f in event_now(w.store, e)["factors"] if f["factor"] == tlt]
    assert moved["yield"] == 4.2
    assert moved["worlds"]["yes"]["move"] == pytest.approx(0.5) and moved["worlds"]["yes"]["by"] == "own"
    assert "no" not in moved["worlds"]  # the baseline, where nothing moves, has no entry
    w.store.edit(futures, "level", 150.0, page(w, "futures-2"))  # its level moves its levels, and not the dollars
    assert derived(w, q)["levels"][e]["no"] == pytest.approx(150 * (1 + m["no"]))


def test_a_factor_quoted_as_a_yield_is_read_in_that_yield_in_its_exposure(tmp_path):
    """MATH.md 3.2; TOOLS.md 5: long Treasuries quoted as a yield of 4.2 at a duration of 17, with a dollar beta of
    $50,000 to the factor. The portfolio gains or loses -50,000 * 17 / 100 = -$8,500 for each percentage point the yield
    rises, beside its dollar beta; its largest month is the yield's move over that month in points, -100 ln(1 + M) / 17
    of its own part's largest move M, given as a size like every factor's; and that month applied stays |g| * |M|. The
    market, quoted as its proxy's price, reads as before."""
    from harness.ledger import channel_reach, exposure, factor
    w = world(tmp_path, g={"SPY": 20_000.0, "TLT": 50_000.0}, coefficients={"TLT": {}}, levels={"TLT": 90.0})
    tlt, spy = quoted(w, "TLT", 4.2, 17.0), channel(w, "SPY")
    month = {c: derived(w, c)["orthogonalization"]["largest_month_sessions"] for c in (tlt, spy)}
    reach = {c["factor"]: c for c in channel_reach(w.store)["factors"]}
    m = month[tlt]["move"]
    assert reach[tlt]["dollar_beta"] == 50_000.0 and reach[tlt]["yield"] == 4.2
    assert reach[tlt]["per_point"] == pytest.approx(-8_500.0)
    assert "carry" not in month[tlt]  # the month's move is net of the income the proxy paid (series.window_move)
    assert reach[tlt]["largest_month"] == pytest.approx(abs(-100.0 * math.log1p(m) / 17.0))
    assert reach[tlt]["largest_month_applied"] == pytest.approx(50_000.0 * abs(m))
    assert "per_point" not in reach[spy] and "yield" not in reach[spy]
    assert reach[spy]["largest_month"] == pytest.approx(abs(month[spy]["move"]))
    assert {c["factor"]: c for c in exposure(w.store)["factors"]} == reach
    added = factor(w.store, tlt)  # what adding it returns: the month's move in points, of its own sign, with its dates
    assert added["per_point"] == pytest.approx(-8_500.0)
    assert added["largest_month"] == {"move": pytest.approx(-100.0 * math.log1p(m) / 17.0),
                                      "start": month[tlt]["start"], "end": month[tlt]["end"]}
    assert "per_point" not in factor(w.store, spy) and factor(w.store, spy)["largest_month"]["move"] == month[spy]["move"]


def test_a_past_windows_residuals_stated_back_against_what_each_factor_follows_reproduce_its_moves(tmp_path):
    """MATH.md 1.3, 3.2: a factor's residual is one quantity in window_returns and in a statement. A past window's
    residuals, each its compounded move less its coefficients on the earlier factors' residuals, stated back against
    what each factor follows on every factor of an event's Yes world, give each factor the window's whole move there: a
    fraction of its proxy's price, or, for a factor quoted as a yield, the yield's move in points."""
    from harness.derive.impact import FOLLOWED
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = world(tmp_path, g={"SPY": 10_000.0, "USO": 5_000.0, "TLT": 5_000.0},
              coefficients={"TLT": {"SPY": -0.2}, "USO": {"SPY": 0.6, "TLT": 0.1}}, levels={"TLT": 90.0})
    tlt = quoted(w, "TLT", 4.2, 17.0)
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))
    month = derived(w, channel(w, "SPY"))["orthogonalization"]["largest_month_sessions"]
    ok, window = tools.call("window_returns", {"start": month["start"], "end": month["end"]})
    assert ok, window
    e = event(w)
    for f in window["factors"]:
        quoted_ = "yield" in f
        w.store.link(e, "consequence", f["factor"],
                     {"shock": {"value": f[FOLLOWED], "against": schema.FOLLOWS, "note": "As then."}})
    moved = {f["factor"]: f["worlds"]["yes"] for f in event_now(w.store, e)["factors"]}
    for f in window["factors"]:
        if f["move"] is None:  # a proxy with no close at an end of the window
            continue
        assert moved[f["factor"]]["stated"] == {"value": f[FOLLOWED], "against": schema.FOLLOWS}
        assert moved[f["factor"]]["move"] == pytest.approx(f["move"], abs=1e-6)
    assert [f["factor"] for f in window["factors"] if "yield" in f] == [tlt]


def test_a_windows_move_is_what_its_proxy_earned_less_the_income_it_accrued(tmp_path):
    """MATH.md 3.2: a window's move is what holding the proxy earned over it, less the income it accrued over it. A bond
    fund quoted as a yield at a duration of 17 pays 0.90 a month; its closes are its clean price plus the income it has
    accrued since its last ex-date, falling by the distribution on each, as real closes do. Over three sessions that
    hold an ex-date its yield moves by its clean price alone, -100 ln(C_end / C_start) / 17, to within what compounding
    each session on its close leaves (well under a thousandth of a point): the closes' ratio would read the
    distribution as a rise in its yield, and what holding earned alone would read the days' income as a fall."""
    from datetime import date
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = world(tmp_path, g={"SPY": 10_000.0, "TLT": 5_000.0}, coefficients={"TLT": {}}, levels={"TLT": 90.0})
    clean = closes_from(random_returns(np.random.default_rng(sum(map(ord, "TLT"))), 0.01))  # world()'s TLT path
    clean = {d: c * 90.0 / clean[max(clean)] for d, c in clean.items()}
    days = sorted(clean)
    ex = days[30::21]  # a distribution of 0.90 every 21 sessions, each on a session's close
    day = lambda d: date.fromisoformat(d).toordinal()  # noqa: E731

    def accrued(d):  # accrued and not yet gone ex: since the one before, toward the next, by calendar day; before
        nxt = next((x for x in ex if x > d), None)  # the first, over its schedule's period, 365 / 12 days
        if nxt is None:
            return 0.0
        i = ex.index(nxt)
        begun = day(ex[i - 1]) if i > 0 else day(nxt) - 365 / 12
        return 0.90 * max(day(d) - begun, 0.0) / (day(nxt) - begun)
    dirty = {d: c + accrued(d) for d, c in clean.items() if d < ex[-1]}
    put_bars(w.store, "TLT", dirty, dividends={x: 0.90 for x in ex})
    tlt = quoted(w, "TLT", 4.2, 17.0)
    on = ex[len(ex) // 2]
    start, end = days[days.index(on) - 2], days[days.index(on) + 1]
    ok, window = Tools(Harness(w.store, w.venues, Web(key="test"))).call("window_returns", {"start": start, "end": end})
    assert ok, window
    [f] = [f for f in window["factors"] if f["factor"] == tlt]
    expected = -100.0 * math.log(clean[end] / clean[start]) / 17.0
    assert f["move"] == pytest.approx(expected, abs=1e-3)
    assert abs(-100.0 * math.log(dirty[end] / dirty[start]) / 17.0 - expected) > 0.05  # the closes' ratio


def test_a_window_before_the_history_reads_every_proxy_at_once(tmp_path, monkeypatch):
    """MATH.md 3.2; TOOLS.md 5: a window the store holds none of is read for every factor's proxy at once, so a call
    stands aside once for all of them: every proxy's prices and dividends are asked for in its first attempt, each once,
    and the call answers on its second. Each read here is held until the call first stands aside, as a read from a
    source takes time. Read one proxy at a time, each read of each proxy was an attempt of its own, and a store with a
    dozen factors ran past the call's attempts."""
    import threading
    from datetime import date, timedelta
    import harness.tools
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    from massive_data import ms
    proxies = ["SPY", "UUP", "USO", "TLT", "EEM", "GLD"]
    w = world(tmp_path, g={t: 1_000.0 for t in proxies}, coefficients={t: {} for t in proxies})
    asked: list[tuple[str, str]] = []
    aside, real_wait = threading.Event(), harness.tools.wait

    def wait(fs, *a, **kw):  # the call stands aside, holding no lock
        aside.set()
        return real_wait(fs, *a, **kw)
    monkeypatch.setattr(harness.tools, "wait", wait)

    def days(covering):
        d, last = date.fromisoformat(covering[0]), date.fromisoformat(covering[1])
        while d <= last:
            if d.weekday() < 5:
                yield d.isoformat()
            d += timedelta(days=1)

    def bars(subject, covering):
        asked.append(("bars", subject))
        aside.wait(10)
        results = [{"t": ms(d), "o": c, "h": c, "l": c, "c": c, "v": 1000.0}
                   for d in days(covering) for c in [100.0 + date.fromisoformat(d).toordinal() % 7]]
        return {"payload": {"ticker": subject, "adjusted": True, "status": "OK", "results": results},
                "request": {"kind": "bars", "subject": subject}, "retrieved_at": "2026-11-05T03:00:00+00:00",
                "range": covering}

    def dividends(subject, covering):
        asked.append(("dividends", subject))
        aside.wait(10)
        return {"payload": {"status": "OK", "results": []}, "request": {"kind": "dividends", "subject": subject},
                "retrieved_at": "2026-11-05T03:00:00+00:00", "range": covering}
    w.store.fetchers.update(bars=bars, dividends=dividends)
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))
    ok, window = tools.call("window_returns", {"start": "2023-03-01", "end": "2023-03-31"})
    assert ok, window
    [entry] = [e for e in tools.trace.entries() if e.get("tool") == "window_returns"]
    assert entry["passes"] == 2
    assert sorted(asked) == sorted((k, t) for k in ("bars", "dividends") for t in proxies)
    assert all(f["move"] is not None for f in window["factors"])


def test_a_shock_against_what_a_factor_follows_leaves_the_factors_before_it_and_moves_those_after_by_their_coefficients(
        tmp_path):
    """MATH.md 1.3: a shock against what a later factor follows says nothing of the factors before it, whose residuals it
    does not move under the ordinary covariance, and a factor after it moves by its coefficient on it: semiconductors at
    -10% against what they follow leave the market where it is and move a later factor at a coefficient of 0.5 on them
    by e^(0.5 ln 0.9) - 1. With the market then stated at -5% against the baseline, semiconductors' move against the
    baseline is the two compounded, and their residual stays as stated. A company stated against what it follows
    moves by its response there compounded with the shock (1.4)."""
    from harness.derive.impact import FOLLOWED
    from harness.ledger import MOVES_WITH
    w = world(tmp_path, g={"SPY": 10_000.0, "UUP": 5_000.0, "USO": 5_000.0},
              coefficients={"UUP": {"SPY": 1.4}, "USO": {"SPY": 0.0, "UUP": 0.5}})
    semis, oil, spy = channel(w, "UUP"), channel(w, "USO"), channel(w, "SPY")  # a factor beyond the market, and one after
    e = event(w)
    own = w.store.link(e, "consequence", semis, {"shock": {"value": -0.1, "against": schema.FOLLOWS,
                                                         "note": "Beyond the market."}})["id"]
    moved = {f["factor"]: f["worlds"].get("yes") for f in event_now(w.store, e)["factors"]}
    assert moved.get(spy) is None  # the market does not move, and is not listed
    assert {k: v for k, v in moved[semis].items() if k != "against_today"} == {
        "move": pytest.approx(-0.10), "by": "own", "on": e, "consequence": own,
        "stated": {"value": -0.1, "against": schema.FOLLOWS}}
    assert moved[oil]["by"] == MOVES_WITH
    assert moved[oil]["move"] == pytest.approx(math.exp(0.5 * math.log(0.9)) - 1, abs=1e-6)  # given to six places
    w.store.link(e, "consequence", spy, {"shock": {"value": -0.05, "note": "The market falls."}})
    moved = {f["factor"]: f["worlds"]["yes"] for f in event_now(w.store, e)["factors"]}
    assert moved[semis]["move"] == pytest.approx(math.exp(math.log(0.9) + 1.4 * math.log(0.95)) - 1, abs=1e-6)
    assert w.store.get(own)["derived"]["moves"]["value"]["yes"][FOLLOWED] == pytest.approx(-0.10)  # as stated


def test_a_company_stated_against_what_it_follows_moves_by_its_response_compounded_with_the_shock(tmp_path):
    """MATH.md 1.4: NVDA at -10% against what it follows, in a world that moves the market -10%: its whole move is its
    response there compounded with the shock, 1 + s = (1 + r)(1 + a); the same world as stating that whole move against
    the baseline, to the cent; and when the market is restated, it moves with its response, where a shock against the
    baseline stays where it was stated. At 0 it stands where its response puts it, as if unstated."""
    from harness.derive.impact import FOLLOWED
    w = holding_world(tmp_path)
    e = event(w, p=0.3)
    spy = w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    named = w.store.link(e, "consequence", "name:NVDA", {"shock": stated({"value": -0.1, "against": schema.FOLLOWS})})
    out = output(w, e)
    r = out["consequences"][named["id"]]["worlds"]["yes"]["betas"]
    assert out["whole"]["name:NVDA"]["yes"] == pytest.approx((1 + r) * 0.9 - 1)
    assert w.store.get(named["id"])["derived"]["moves"]["value"]["yes"][FOLLOWED] == pytest.approx(-0.10)
    w.store.edit(named["id"], "shock", stated({"value": (1 + r) * 0.9 - 1}))  # the same whole move, against the baseline
    whole = output(w, e)
    assert (whole["yes"], whole["no"]) == (pytest.approx(out["yes"], abs=1e-6), pytest.approx(out["no"], abs=1e-6))
    w.store.edit(spy, "shock", stated(-0.2))  # the market restated: the shock against the baseline keeps its end point
    assert output(w, e)["whole"]["name:NVDA"]["yes"] == pytest.approx((1 + r) * 0.9 - 1)
    w.store.edit(named["id"], "shock", stated({"value": -0.1, "against": schema.FOLLOWS}))
    moved = output(w, e)  # against what it follows, it moves with its response
    r2 = moved["consequences"][named["id"]]["worlds"]["yes"]["betas"]
    assert r2 < r and moved["whole"]["name:NVDA"]["yes"] == pytest.approx((1 + r2) * 0.9 - 1)
    w.store.edit(named["id"], "shock", stated({"value": 0.0, "against": schema.FOLLOWS}))
    at_zero = output(w, e)
    w.store.remove(named["id"])
    unstated = output(w, e)
    assert (at_zero["yes"], at_zero["no"]) == (pytest.approx(unstated["yes"], abs=1e-6),
                                               pytest.approx(unstated["no"], abs=1e-6))


def test_a_consequence_stating_a_shock_against_what_a_factor_follows_is_read_by_every_reader_of_the_row(tmp_path):
    """MATH.md 1.3; ONTOLOGY.md 2.2; TOOLS.md 9: a consequence on a factor states one figure, `shock`, and the place it
    is measured from, the baseline or what the factor follows, with its basis. The row carries it as stated, and every
    reader of an event's row reads it: the row itself, ledger(), the record of a reply's rows, the ledger panel, the
    event's own view and the download."""
    from harness import record
    from harness.ledger import ledger_read, row
    from harness.render import Reader, event_view, ledger_markdown, ledger_panel
    w = world(tmp_path, g={"SPY": 10_000.0, "UUP": 5_000.0}, coefficients={"UUP": {"SPY": 1.4}})
    semis, spy = channel(w, "UUP"), channel(w, "SPY")
    e = event(w, title="An event with a residual")
    own = w.store.link(e, "consequence", semis, {"shock": {"value": -0.1, "against": schema.FOLLOWS,
                                                         "note": "Beyond the market."}})["id"]
    whole = w.store.link(e, "consequence", spy, {"shock": {"value": -0.05, "note": "The market falls."}})["id"]
    got = {c["link"]: c for c in row(w.store, e)["consequences"]}
    assert got[own]["shock"] == w.store.get(own)["stated"]["shock"] and got[own]["shock"]["against"] == schema.FOLLOWS
    assert got[own]["shock"]["value"] == -0.1 and got[own]["moves"] is not None
    assert got[whole]["shock"] == w.store.get(whole)["stated"]["shock"] and "against" not in got[whole]["shock"]
    [read] = ledger_read(w.store)["rows"]
    assert read["id"] == e and read["full_impact"] is not None
    assert record.reply(w.store, session="s", turn=1, stopped=False) is not None
    [kept] = w.store.records()[-1]["rows"]
    assert {c["link"]: c["shock"] for c in kept["consequences"]}[own] == {"value": -0.1, "against": schema.FOLLOWS,
                                                                         "note": got[own]["shock"]["note"]}
    assert "An event with a residual" in ledger_panel(Reader(w.store))
    assert "An event with a residual" in ledger_markdown(w.store)
    assert "An event with a residual" in event_view(Reader(w.store), e)["html"]


def test_a_metric_that_quotes_a_factor_with_no_level_says_its_levels_wait_on_one(tmp_path):
    """A metric stands without a level (ONTOLOGY.md 2.1). One that quotes a factor is that factor's price in its own unit, so
    its level in each world is its level today moved as the factor's price moves there (2.2): with no level stated,
    the quotes link says that is what its levels wait on, and once a level is stated they follow."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}}, levels={"USO": 70.0})
    oil = channel(w, "USO")
    w.store.add("Metric", {"id": "crude_spot", "name": "the crude spot price", "unit": "USD", "kind": "price"})
    q = w.store.link("metric:crude_spot", "quotes", oil)["id"]
    e = event(w)
    w.link(e, "consequence", oil, shock=-0.2)
    got = w.store.engine.value("quotes", q)
    assert got.output is None and got.cause == ("metric:crude_spot states no level: its level in each world is its "
                                                f"level today, moved as {oil}'s price moves there")
    assert "levels" not in derived(w, q)
    w.store.edit("metric:crude_spot", "level", 80.0, page(w, "crude-level"))
    placed_ = output(w, e)["placed"]
    assert derived(w, q)["levels"] == {e: {x: pytest.approx(80.0 * (1 + placed_[x][oil])) for x in ("yes", "no")}}


def test_a_yield_factor_that_follows_the_stated_ones_moves_in_points_of_its_yield(tmp_path):
    """Long Treasuries quoted as a yield at a duration of 17, left unstated while the market is shocked -10%: the move
    it takes with the market, x on its proxy's price, reads as -100 ln(1 + x) / 17 points of its yield (1.3)."""
    w = world(tmp_path, g={"SPY": 10_000.0, "TLT": 50_000.0}, levels={"TLT": 90.0})
    tlt = quoted(w, "TLT", 4.2, 17.0)
    e = event(w)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    x = output(w, e)["against_baseline"]["yes"][tlt]
    assert x != 0.0
    (moved,) = [f for f in event_now(w.store, e)["factors"] if f["factor"] == tlt]
    placed_ = output(w, e)["placed"]["yes"][tlt]
    assert moved["worlds"]["yes"] == {"move": pytest.approx(-100 * math.log1p(x) / 17, abs=1e-6),
                                      "against_today": pytest.approx(-100 * math.log1p(placed_) / 17, abs=1e-6),
                                      "by": "moves with the stated ones"}  # it moves only with the market


def test_a_factors_quoting_is_fixed_when_it_is_added_and_a_metric_quotes_one_factors_price(tmp_path):
    """Whether a factor is quoted as a yield decides the unit of every shock on it, so it is fixed when the factor is
    added (MATH.md 1.3; ONTOLOGY.md 2.1): a yield and a duration are stated together, each from a page, and each is
    edited with its new value; neither is added to a factor quoted as its proxy's price, nor cleared. A metric quotes
    one factor, as its price in the metric's own unit; a yield is no factor's price (ONTOLOGY.md 2.2)."""
    w = world(tmp_path, g={"TLT": 50_000.0, "USO": 10_000.0}, coefficients={"TLT": {}, "USO": {}})
    oil = channel(w, "USO")
    sid = page(w, "yields", "The fund yields 4.4 percent, with a duration of 16.5 years.")
    with pytest.raises(StoreError, match="its proxy's duration"):
        w.store.add("Factor", {"name": "long bonds", "proxy": "IEF", "order": 9,
                               "yield": {"value": 4.4, "source_id": sid}})
    with pytest.raises(StoreError, match="from when it is added"):
        w.store.edit(oil, "yield", 4.4, sid)
    tlt = quoted(w, "TLT", 4.2, 17.0)
    w.store.edit(tlt, "duration", 16.5, sid)  # a new value, read from a page
    assert w.store.stated_of(tlt)["duration"] == 16.5 and w.store.citations_of(tlt)["duration"] == sid
    with pytest.raises(StoreError, match="edited with its new value"):
        w.store.edit(tlt, "yield", None)
    with pytest.raises(StoreError, match="a positive number of years"):
        w.store.edit(tlt, "duration", 0.0, sid)
    rate = metric(w, "ten_year_yield", 4.1, kind="yield")
    with pytest.raises(StoreError, match="a yield moves against its bond's price"):
        w.store.link(rate, "quotes", tlt)
    spot = metric(w, "crude_spot", 70.0)
    w.store.link(spot, "quotes", oil)
    with pytest.raises(StoreError, match="a metric is the price of one factor"):
        w.store.link(spot, "quotes", tlt)


def test_a_yield_moves_its_factor_by_minus_duration_over_100_and_its_price_is_placed_once(tmp_path):
    """A shock of +0.5 points on a factor quoted as a yield at a duration of 17, at p = 0.25: the factor's compounded
    move is -0.085 if the event happens. Its price is placed once: e^-0.085 L_base - 1 = -6.2387% and L_base - 1 =
    +2.0796%, with L_base = 1 / (0.25 e^-0.085 + 0.75); D = $50,000 x (e^-0.085 - 1) L_base."""
    w = world(tmp_path, g={"TLT": 50_000.0}, coefficients={"TLT": {}})
    tlt = quoted(w, "TLT", 4.2, 17.0)
    e = event(w)
    lid = w.link(e, "consequence", tlt, shock=0.5)
    out = output(w, e)
    base = 1 / (0.25 * math.exp(-0.085) + 0.75)
    assert out["consequences"][lid]["worlds"]["yes"]["raw"] == pytest.approx(math.expm1(-0.085))
    assert out["placed"]["yes"][tlt] == pytest.approx(math.exp(-0.085) * base - 1)
    assert out["placed"]["no"][tlt] == pytest.approx(base - 1)
    assert round(100 * out["placed"]["yes"][tlt], 4) == -6.2387 and round(100 * out["placed"]["no"][tlt], 4) == 2.0796
    assert out["full_impact"] == pytest.approx(50_000 * math.expm1(-0.085) * base)

def test_a_set_factor_less_what_the_earlier_ones_imply_and_the_unset_ones_filled_in_compounded_moves(tmp_path):
    """EEM set at -10% with a coefficient of 1.4 on SPY, SPY and USO unset; SPY's part has variance 1e-4 and EEM's own
    2e-4, so var(EEM) = 3.96e-4, cov(SPY, EEM) = 1.4e-4 and cov(USO, EEM) = 0.7e-4 (USO's coefficient is 0.5 on SPY).
    Compounded (1.3): ln(1 + x_SPY) = (1.4 / 3.96) ln 0.9 and ln(1 + x_USO) = (0.7 / 3.96) ln 0.9, all of USO's what
    SPY implies. Each is placed at p = 0.2 (1.5). Each holding carrying a factor's component moves by its response,
    exp(z_c), placed once (1.6), and the full impact is those moves at their dollars: g on the placed components'
    difference, and what compounding adds."""
    w = world(tmp_path, g={"SPY": 80_000.0, "EEM": 30_000.0, "USO": 5_000.0},
              coefficients={"SPY": {}, "EEM": {"SPY": 1.4}, "USO": {"SPY": 0.5}},
              variances={"SPY": 1e-4, "EEM": 2e-4, "USO": 3e-4}, levels={"SPY": 760.0, "EEM": 300.0})
    e = event(w, p=0.2)
    w.link(e, "consequence", channel(w, "EEM"), shock=-0.1)
    out = output(w, e)
    spy, eem, oil = channel(w, "SPY"), channel(w, "EEM"), channel(w, "USO")
    x = {spy: math.expm1(1.4 / 3.96 * math.log(0.9)), eem: -0.10, oil: math.expm1(0.7 / 3.96 * math.log(0.9))}
    three = lambda d: {c: d[c] for c in (spy, eem, oil)}  # noqa: E731  the factors the case states
    assert three(out["against_baseline"]["yes"]) == pytest.approx(x)
    assert three(out["against_baseline"]["no"]) == {spy: 0.0, eem: 0.0, oil: 0.0}
    m = {c: placed(x[c], 0.2) for c in x}  # (yes, no)
    u = {c: (m[spy][i], m[eem][i] - 1.4 * m[spy][i], m[oil][i] - 0.5 * m[spy][i]) for i, c in ((0, "yes"), (1, "no"))}
    assert three(out["components"]["yes"]) == pytest.approx(dict(zip((spy, eem, oil), u["yes"])))
    assert three(out["components"]["no"]) == pytest.approx(dict(zip((spy, eem, oil), u["no"])))
    assert out["consequence_set"] == [eem]
    g = (80_000.0, 30_000.0, 5_000.0)
    z = (math.log1p(x[spy]), math.log(0.9) - 1.4 * math.log1p(x[spy]), math.log1p(x[oil]) - 0.5 * math.log1p(x[spy]))
    held = [placed(math.expm1(zc), 0.2) for zc in z]  # each carrier's price: its response, placed once
    full = sum(gc * (a - b) for gc, (a, b) in zip(g, held))
    assert out["full_impact"] == pytest.approx(full)
    at_betas = sum(gc * (a - b) for gc, a, b in zip(g, u["yes"], u["no"]))
    curve = sum(h["curve"]["full_impact"] for h in out["by_holding"] if h.get("curve"))
    assert at_betas + curve == pytest.approx(full)  # the receipt: g on the placed components, and compounding
    assert out["u_E"][oil] == pytest.approx(m[oil][0] - m[oil][1] - 0.5 * (m[spy][0] - m[spy][1]))


def test_a_factor_stated_at_minus_1_is_worth_nothing_and_fills_nothing(tmp_path):
    w = world(tmp_path, g={"SPY": 80_000.0, "USO": 5_000.0}, coefficients={"SPY": {}, "USO": {"SPY": 0.5}},
              variances={"SPY": 1e-4, "USO": 3e-4})
    e = event(w, p=0.2)
    w.link(e, "consequence", channel(w, "USO"), shock=-1.0)
    out = output(w, e)
    assert out["at_zero"] == [channel(w, "USO")]
    assert out["against_baseline"]["yes"][channel(w, "SPY")] == 0.0  # nothing is filled from it


def test_a_price_at_zero_in_every_world_today_carries_leaves_the_event_without_figures(tmp_path):
    """An event at a probability of 1 has no weight on its No side, its baseline: a shock of -1 leaves oil at zero
    in the one world today's prices carry, which today's price contradicts (1.10)."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}})
    e = event(w, p=1.0)
    w.link(e, "consequence", channel(w, "USO"), shock=-1.0)
    out = output(w, e)
    assert "full_impact" not in out and channel(w, "USO") in [u["price"] for u in out["unplaceable"]]
    assert w.store.get(e)["derived"].get("full_impact", {}).get("value") is None
    assert (e, channel(w, "USO")) in [(x["id"], x["price"]) for x in check(w.store)["price_at_zero_where_priced"]]


def test_an_event_with_no_consequence_has_no_impact_and_what_it_now_does_says_why(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    e = event(w)
    assert "full_impact" not in w.store.get(e)["derived"]
    now = event_now(w.store, e)
    assert now["figures"] is None and "has no consequence" in now["why"]
    from harness.ledger import ledger_read
    [row] = ledger_read(w.store)["rows"]  # its row in ledger() says the same, with its probability and outcomes
    assert row["figures"] is None and row["why"] == now["why"] and row["probability"] == 0.25
    assert [o["id"] for o in row["outcomes"]] == ["yes", "no"]


# ---------------------------------------------------------------- each world against the baseline, whatever the probability


def open_event(w, title="E") -> tuple[str, str]:
    """A watched event whose statements leave its probability open: a chance given a proposition nothing settles. The
    event, and that proposition."""
    driver = w.prop(f"{title}'s driver")
    e = w.prop(title, baseline="no")
    w.chances(e, {"given": [driver], "value": 0.5})
    w.link("portfolio", "watches", e)
    return e, driver


def deal(w, ticker="DEAL", price=25.0, dollars=2_500.0):
    """A company held at a price, its shares the dollars over it."""
    put_reference(w.store, ticker, "CS")
    prices = closes_from(random_returns(np.random.default_rng(9), .01))
    last = prices[max(prices)]
    put_bars(w.store, ticker, {d: c * price / last for d, c in prices.items()})
    w.store.link("portfolio", "holds", schema.name_id(ticker), {"dollars": dollars})
    return schema.name_id(ticker)


def worth(w, on, n, value, side=None):
    return w.store.link(on, "worth", n, {**({"world": side} if side else {}),
                                         "value": {"value": value, "note": "The terms."}})["id"]


def test_what_each_world_does_against_the_baseline_reads_no_probability(tmp_path):
    """MATH.md 1.3, 1.4, 1.6; TOOLS.md 5: what a world does against its baseline reads no probability, so it is there
    while the statements leave the event's open. Oil set at +20% moves the market by its ordinary co-movement,
    ln(1 + x_SPY) = (0.5e-4 / 3.25e-4) ln 1.2; each carrier moves by its response, its factor's own part compounded;
    and a holding valued by what a deal pays moves by its shares times its stated values' difference, 100 x (35 - 20).
    Each holding is today's dollars at the world's move against the baseline. The row and every level wait on the
    probability, and the event says so."""
    from harness.ledger import ledger_read
    w = world(tmp_path, g={"SPY": 20_000.0, "USO": 10_000.0}, coefficients={"SPY": {}, "USO": {"SPY": 0.5}},
              variances={"SPY": 1e-4, "USO": 3e-4})
    company = deal(w)
    e, _ = open_event(w)
    oil, spy = channel(w, "USO"), channel(w, "SPY")
    w.link(e, "consequence", oil, shock=0.2)
    worth(w, e, company, {"amount": 35.0}, "yes")
    worth(w, e, company, {"amount": 20.0}, "no")
    assert output(w, e) is None  # the impact, its row and its levels, waits
    x_spy = math.expm1(0.5e-4 / 3.25e-4 * math.log(1.2))
    z_oil = math.log(1.2) - 0.5 * math.log1p(x_spy)
    now = event_now(w.store, e)
    assert "levels" not in now and "row" not in now and "figures" not in now
    assert now["why"].startswith(f"{e}'s row, and the level of each price in its worlds, wait on the probabilities of "
                                 f"its worlds. {e}'s probability is left open")
    moved = {f["factor"]: f["worlds"] for f in now["factors"]}
    assert moved[oil]["yes"]["move"] == pytest.approx(0.2) and moved[oil]["yes"]["by"] == "own"
    assert moved[spy]["yes"] == {"move": pytest.approx(x_spy, abs=1e-6), "by": "moves with the stated ones"}
    assert all("no" not in f for f in moved.values())  # the baseline, where nothing moves
    held = {h["ticker"]: h for h in now["holdings"]}
    expected = {carrier("SPY"): 20_000.0 * x_spy, carrier("USO"): 10_000.0 * math.expm1(z_oil), "DEAL": 1_500.0}
    assert {t: h["against_baseline"]["yes"] for t, h in held.items()} == pytest.approx(expected, abs=0.005)
    assert all(h["against_baseline"]["no"] == 0.0 for h in held.values())
    assert held["DEAL"]["value"] == 2_500.0 and "worlds" not in held["DEAL"] and "full_impact" not in held["DEAL"]
    worlds_ = {o["id"]: o for o in now["outcomes"]}
    assert worlds_["yes"]["against_baseline"] == pytest.approx(sum(expected.values()), abs=0.01)
    assert worlds_["no"]["against_baseline"] == 0.0 and worlds_["no"]["baseline"]
    assert "p" not in worlds_["yes"]  # its probability is open
    [row] = ledger_read(w.store)["rows"]  # ledger() gives the same, with the range its statements leave
    assert "figures" not in row and row["why"] == now["why"] and row["left_open"]["hi"] > row["left_open"]["lo"]
    assert [(o["id"], o.get("against_baseline")) for o in row["outcomes"]] == \
        [(o["id"], o.get("against_baseline")) for o in now["outcomes"]]


def test_what_a_world_does_against_the_baseline_is_the_impact_with_todays_price_at_the_baseline(tmp_path):
    """One valuation (MATH.md 1.5, 1.6): what each world does against the baseline is the event's own impact placed with
    all the weight on the baseline (impact.placed), each holding's value there less its value in the baseline, on an
    event with two ways, a name and a deal's values stated in them. It reads no probability: settling the event's
    leaves it the same Computation."""
    w = world(tmp_path, g={"SPY": 20_000.0, "USO": 10_000.0}, coefficients={"SPY": {}, "USO": {"SPY": 0.5}},
              variances={"SPY": 1e-4, "USO": 3e-4})
    company = deal(w)
    e, driver = open_event(w)
    mild, severe = w.prop("mild"), w.prop("severe")
    w.link(e, "way", mild, side="yes")
    w.link(e, "way", severe, side="yes")
    w.chances(mild, {"given": [e], "value": 0.7})
    w.link(mild, "consequence", channel(w, "USO"), shock=0.1)
    w.link(severe, "consequence", channel(w, "USO"), shock=0.4)
    w.link(severe, "consequence", channel(w, "SPY"), shock=-0.08)
    worth(w, mild, company, {"amount": 30.0})
    worth(w, severe, company, {"amount": 40.0})
    worth(w, e, company, {"amount": 20.0}, "no")
    before = w.store.engine.value("against_baseline", e)
    assert output(w, e) is None and before.output is not None
    w.chances(driver, {"value": 0.4})
    w.chances(e, {"given_not": [driver], "value": 0.1})  # 0.5 x 0.4 + 0.1 x 0.6
    found = w.store.engine.value("impact", e)
    assert found.output["p"] == pytest.approx(0.26)
    after = w.store.engine.value("against_baseline", e)
    assert after.basis == before.basis  # the same Computation
    W = list(found.output["worlds"])
    at_baseline = impact.placed(w.store, found.basis, {x: float(x == "no") for x in W})
    got = {h["holding"]: h["against_baseline"] for h in after.output["by_holding"]}
    for h in at_baseline["by_holding"]:
        assert got[h["holding"]] == pytest.approx({x: h["worlds"][x] - h["worlds"]["no"] for x in W}, abs=1e-9)
    assert after.output["A"] == pytest.approx({x: at_baseline["worlds"][x]["A"] - at_baseline["worlds"]["no"]["A"]
                                               for x in W}, abs=1e-9)
    shares = 2_500.0 / 25.0
    deal_ = next(h for h in after.output["by_holding"] if h["name"] == company)["against_baseline"]
    assert deal_ == pytest.approx({mild: shares * 10.0, severe: shares * 20.0, "no": 0.0})


def test_a_held_contract_counts_where_the_links_fix_what_it_pays_and_waits_where_they_do_not(tmp_path):
    """MATH.md 1.9; TOOLS.md 5: with the event's probability open, a contract on a market that asks the event pays its
    quantity in the event's Yes world and nothing in its No, whatever the probabilities, so it counts against the
    baseline at what that payment is worth today. One on a market that asks a broader proposition pays in the No world
    as often as that proposition holds where the event does not, which the statements leave open: it waits, by its
    market, with why."""
    w = world(tmp_path, g={"SPY": 10_000.0}, coefficients={"SPY": {}})
    e, _ = open_event(w)
    broader = w.prop("broader")
    w.link(e, "implies", broader)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    on_e, on_broader = w.market("e", "E?", .29, .31), w.market("broad", "Broader?", .39, .41)
    w.link(e, "asks", on_e, side="yes", taken=False)
    w.link(broader, "asks", on_broader, side="yes", taken=False)
    for year, rows in ((2025, []), (2026, [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", str(year), {"results": rows}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    w.store.link("portfolio", "holds", on_e, {"side": "yes", "quantity": 10})
    w.store.link("portfolio", "holds", on_broader, {"side": "yes", "quantity": 10})
    got = w.store.engine.value("against_baseline", e).output
    [held] = [h for h in got["by_holding"] if h.get("contract")]
    assert held["contract"]["market"] == on_e and held["contract"]["probabilities"] == {"yes": 1.0, "no": 0.0}
    assert held["against_baseline"] == pytest.approx({"yes": 10 * held["contract"]["discount"], "no": 0.0})
    now = event_now(w.store, e)
    assert [h.get("market") for h in now["holdings"]] == [None, on_e] and "ticker" not in now["holdings"][1]
    assert now["waiting"]["contracts"] == [on_broader]
    head, _, left = now["waiting"]["why"].partition(": ")
    assert head == ("what each pays in some of these worlds is the share of the world where its proposition holds, "
                    "which waits on the probabilities its statements leave open")
    assert e in left.split(", ") and broader in left.split(", ")


def test_a_held_contract_whose_payoff_rests_on_a_probability_left_open_waits_in_the_row(tmp_path):
    """MATH.md 1.6, 1.9 (missing inputs are resolved at their source, not replaced with invented values): the
    event at a stated 0.3 implies a broader proposition nothing settles. A contract on the event pays in its Yes world
    and not in its No. One on the broader proposition pays in the No world as often as that proposition holds there,
    which the statements leave open: the row leaves it out and names it, where it was read at an assumed half. Once its
    chance given not the event is stated, 0.2, it pays 0.2 of its quantity there."""
    from harness.ledger import ledger_read
    w = world(tmp_path, g={"SPY": 10_000.0}, coefficients={"SPY": {}})
    e = event(w, p=0.3)
    broader = w.prop("broader")
    w.link(e, "implies", broader)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    on_e, on_broader = w.market("e", "E?", .29, .31), w.market("broad", "Broader?", .39, .41)
    w.link(e, "asks", on_e, side="yes", taken=False)
    w.link(broader, "asks", on_broader, side="yes", taken=False)
    for year, rows in ((2025, []), (2026, [{"date": "2026-11-04", "curve": [[.5, .04]]}])):
        w.store.put_data("yield_curve", str(year), {"results": rows}, {"url": "test"},
                         retrieved_at="2026-11-04T15:00:00+00:00", range=("2026-11-04", "2026-11-04"))
    w.store.link("portfolio", "holds", on_e, {"side": "yes", "quantity": 10})
    held_broader = w.store.link("portfolio", "holds", on_broader, {"side": "yes", "quantity": 10})["id"]
    out = output(w, e)
    held = {h["contract"]["market"]: h["contract"] for h in out["by_holding"] if h.get("contract")}
    assert set(held) == {on_e} and held[on_e]["probabilities"] == {"yes": 1.0, "no": 0.0}
    assert out["waiting"]["holdings"] == [held_broader] and out["waiting"]["why"].endswith(f": {broader}")
    assert event_now(w.store, e)["waiting"]["contracts"] == [on_broader]
    assert ledger_read(w.store)["rows"][0]["waiting"]["contracts"] == [on_broader]
    w.chances(broader, {"given_not": [e], "value": 0.2})
    out = output(w, e)
    held = {h["contract"]["market"]: h["contract"] for h in out["by_holding"] if h.get("contract")}
    assert "waiting" not in out and held[on_broader]["probabilities"] == pytest.approx({"yes": 1.0, "no": 0.2})


def test_the_full_impact_in_ledger_is_its_factors_own_parts_and_compounding_in_the_order_stated(tmp_path):
    """MATH.md 1.6; TOOLS.md 9: ledger() gives the full impact in its three parts: each factor's dollar beta times its
    move between the sides, in factor order; each stated company's own part, its exposure after look-through times its
    own part's move between the sides, in the order its shock was stated (XOM's before NVDA's); and what compounding
    adds, the rest, the three adding to the full impact."""
    from harness.ledger import ledger_read
    w = holding_world(tmp_path)
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(e, "consequence", "name:XOM", shock=0.05)
    w.link(e, "consequence", "name:NVDA", shock=-0.2)
    out = output(w, e)
    [row] = ledger_read(w.store)["rows"]
    parts = row["full_impact_parts"]
    assert [f["factor"] for f in parts["factors"]] == [channel(w, "SPY"), channel(w, "USO")]
    for f in parts["factors"]:
        assert f["dollars"] == pytest.approx(f["dollar_beta"] * out["u_E"][f["factor"]], abs=0.005)
    assert [o["name"] for o in parts["companies"]] == ["name:XOM", "name:NVDA"]
    for o in parts["companies"]:
        assert o["exposure"] == pytest.approx(out["reach"][o["name"]], abs=0.005)
        assert o["beyond_response"] == pytest.approx(out["own_moves"][o["name"]]["E"], abs=1e-6)
        assert o["dollars"] == pytest.approx(out["reach"][o["name"]] * out["own_moves"][o["name"]]["E"], abs=0.005)
    total = sum(x["dollars"] for x in (*parts["factors"], *parts["companies"])) + parts["compounding"]
    assert total == pytest.approx(row["full_impact"], abs=0.005 * (len(parts["factors"]) + 3))
    curve = sum(h["curve"]["full_impact"] for h in out["by_holding"] if h.get("curve"))
    assert parts["compounding"] == pytest.approx(curve, abs=0.005)


def test_a_derived_probabilitys_cases_each_read_where_the_statements_settle_them(tmp_path):
    """MATH.md 1.2: a probability its chances fix, as total probability over what they are given. A chance of 0.3 given A
    and given not A fixes E at 0.3 whatever A's probability, which nothing settles: each case is read as its
    statements leave it, its range from 0 to 1, with its share by its chance, and no market under it."""
    from harness.ledger import ledger_read
    w = world(tmp_path, g={"USO": 10_000.0})
    a = w.prop("A")
    e = w.prop("E", baseline="no")
    w.chances(e, {"given": [a], "value": 0.3}, {"given_not": [a], "value": 0.3})
    w.link("portfolio", "watches", e)
    w.link(e, "consequence", channel(w, "USO"), shock=0.1)
    [row] = ledger_read(w.store)["rows"]
    assert row["probability"] == pytest.approx(0.3) and row["rule"] == "derived"
    assert row["derivation"] == [
        {"given": [a], "given_not": [], "range": [0.0, 1.0], "markets": [], "share": 0.3, "chance": f"{e}.chances[0]"},
        {"given": [], "given_not": [a], "range": [0.0, 1.0], "markets": [], "share": 0.3, "chance": f"{e}.chances[1]"}]


def test_a_stated_weight_places_the_worlds_and_the_row_carries_an_expected_value(tmp_path):
    """Today's prices carry the event at 0.40 where the analyst holds 0.25 (1.5): oil's proxy at 70, +20%, stands at
    70 / (0.4 x 1.2 + 0.6) = 64.81 if not and 77.78 if so; Yes = (1 - q) D, No = -q D, and mu = (p - q) D."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}}, levels={"USO": 70.0})
    x = channel(w, "USO")
    e = event(w, priced_in={"value": 0.40})
    lid = w.link(e, "consequence", x, shock=0.2)
    assert {k: round(v, 2) for k, v in derived(w, lid)["levels"].items()} == {"yes": 77.78, "no": 64.81}
    out = output(w, e)
    D = 10_000 * 0.2 / 1.08
    assert out["full_impact"] == pytest.approx(D)
    assert (out["yes"], out["no"]) == pytest.approx((0.6 * D, -0.4 * D))
    assert out["expected"] == pytest.approx((0.25 - 0.40) * D)
    assert out["worlds"]["yes"]["q"] == pytest.approx(0.40) and out["worlds"]["yes"]["p"] == 0.25


def test_a_weight_read_from_a_market_follows_it_and_takes_its_last_priced_reading(tmp_path):
    """The weight as a market that asks the event, read live (1.5): 0.35 at 0.30 bid and 0.40 ask. A market the event
    is not linked to is refused, and so is removing the link the weight reads. Where the book later has no price, the
    weight is the last reading that had one, with that reading's time."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}})
    x = channel(w, "USO")
    e = event(w)
    m = w.market("e", "E?", 0.30, 0.40)
    with pytest.raises(StoreError, match="not linked to it"):
        w.store.edit(e, "priced_in", {"value": {"market": m}, "note": WHY})
    link = w.link(e, "asks", m)
    with pytest.raises(StoreError, match=r"is a weight from 0 to 1, or a market that asks the proposition"):
        w.store.edit(e, "priced_in", stated({"market": m}))  # a market's weight takes one form, its value wrapped
    w.store.edit(e, "priced_in", {"value": {"market": m}, "note": WHY})
    w.link(e, "consequence", x, shock=0.2)
    out = output(w, e)
    assert out["priced_in"]["yes"]["q"] == pytest.approx(0.35) and out["worlds"]["yes"]["q"] == pytest.approx(0.35)
    assert out["full_impact"] == pytest.approx(10_000 * 0.2 / (1 + 0.35 * 0.2))
    with pytest.raises(StoreError, match="priced_in reads"):
        w.store.remove(link)
    w.set_book(m, 0.30, None)
    w.store.put_data("book", m, w.books[m], {"endpoint": "/books"}, retrieved_at="2026-11-04T16:00:00+00:00")
    out = output(w, e)
    assert out["priced_in"]["yes"]["q"] == pytest.approx(0.35)
    assert out["priced_in"]["yes"]["at"] != "2026-11-04T16:00:00+00:00"


def test_a_kept_impact_placed_again_at_other_weights(tmp_path):
    """impact.placed: the Computation's own manifest at given weights, as a stated weight would place it."""
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}})
    x = channel(w, "USO")
    e = event(w)
    w.link(e, "consequence", x, shock=0.2)
    cid = w.store.get(e)["derived"]["worlds"]["basis"]
    again = impact.placed(w.store, cid, {"yes": 0.40, "no": 0.60})
    assert again["computation"] == cid and again["version"] == impact.IMPACT.version
    assert again["full_impact"] == pytest.approx(10_000 * 0.2 / 1.08)
    assert again["expected"] == pytest.approx((0.25 - 0.40) * again["full_impact"])
    with pytest.raises(ValueError, match="sum to 1"):
        impact.placed(w.store, cid, {"yes": 0.5, "no": 0.6})


# ---------------------------------------------------------------- the worked row


SECTION_10 = {"QQQ": (40_000.0, {"SPY": 1.15, "SOXX": 0.25}), "NVDA": (15_000.0, {"SPY": 1.50, "SOXX": 1.10}),
              "TSM": (10_000.0, {"SPY": 1.20, "SOXX": 1.00}), "XOM": (10_000.0, {"SPY": 0.55}),
              "TLT": (10_000.0, {"SPY": -0.20}), "IBIT": (5_000.0, {"SPY": 0.90, "SOXX": 0.30}), "CASH": (10_000.0, {})}


def test_the_worked_row_built_through_the_tools(tmp_path):
    """The worked row, every statement a tool call, on a store that starts as a new one does, with the market alone:
    its portfolio added, with QQQ holding NVIDIA at $3,200 (its other companies left out); each holding's betas fixed
    at the worked row's table and semiconductors built from the S&P 500 with a coefficient of 1.4. T, L, R, A30 and
    A20 with three markets; the chance of T given R, 0.62; T's baseline its No side; the semiconductors factor added
    after the market; shocks of -0.033 and -0.063. Every figure the worked row gives comes out at the rounding it
    shows, each computed by hand from what the row states."""
    from breakdown import RETRIEVED, Venues, key
    from harness.judge import FixtureJudge
    from harness.store import Store
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    from pathlib import Path
    judge = FixtureJudge({("implication", "T", "R"): 0.97}, default=lambda kind, statement, evidence: 0.95, key=key)
    venues, books = Venues(), {}
    store = Store.create(tmp_path / "scratch-section-10", registry=fixture_registry(), judge=judge, venues=venues,
                         fetchers={"book": lambda s, c: {"payload": books[s], "request": {"endpoint": "/books"},
                                                         "retrieved_at": RETRIEVED},
                                   "history": lambda s, c: {"payload": {"data": [], "pagination": {}},
                                                            "request": {"endpoint": "/prices-history"},
                                                            "retrieved_at": RETRIEVED}})
    store.fixture = {"betas": {t: bs for t, (_, bs) in SECTION_10.items() if t != "CASH"},
                     "coefficients": {"SOXX": {"SPY": 1.4}}}
    store.load_initial(Path(__file__).resolve().parents[1] / "knowledge")  # the market, as a new store starts
    put_market(store, np.random.default_rng(31))
    for t, last in {"SPY": 760.0, "SOXX": 300.0, **{t: 100.0 for t in SECTION_10 if t != "CASH"}}.items():
        closes = closes_from(random_returns(np.random.default_rng(sum(map(ord, t))), 0.01))
        put_bars(store, t, {d: c * last / closes[max(closes)] for d, c in closes.items()})
        if t not in ("SPY", "SOXX"):
            put_reference(store, t, "ETF" if t == "QQQ" else "CS")
    put_constituents(store, "QQQ", [("NVDA", 0.08, "Equity")])
    tools = Tools(Harness(store, venues, Web(key="test")))

    def call(name, **input):
        ok, out = tools.call(name, input)
        assert ok, out
        return out

    def market(vid, question, bid, ask, size=1000.0):
        mid = f"market:polymarket:{vid}"
        venues.records[mid] = {"market": {"id": vid, "question": question, "description": "Resolves to the Yale Budget "
                                          "Lab's published average effective tariff rate.", "endDate": "2027-04-01T03:59:00Z",
                                          "closed": False, "clobTokenIds": f'["Y{vid}", "N{vid}"]',
                                          "outcomes": '["Yes", "No"]'}, "tags": []}
        books[mid] = {"bids": [{"price": str(bid), "size": str(size)}], "asks": [{"price": str(ask), "size": str(size)}]}
        return mid

    def prop(title, comparison="occurs", value=None, kind="hitting"):
        fields = {"title": title, "text": f"{title}: the proposition", "comparison": comparison,
                  "window_end": "2027-03-31", "kind": kind}
        if value is not None:
            fields.update(metric="metric:us_effective_tariff_rate_pct", value=value)
        if kind == "hitting":
            fields["window_start"] = "2026-11-04"
        return call("add", type="Proposition", fields=fields)["id"]

    owned = call("add", type="Portfolio", fields={"asof": "2026-11-04", "holdings": [
        {"cash": d} if t == "CASH" else {"ticker": t, "dollars": d} for t, (d, _) in SECTION_10.items()]})
    assert owned["holdings"]
    yale = store.put_page("https://budgetlab.yale.edu/tariffs", "The average effective tariff rate stands at 22%.",
                          retrieved_at=RETRIEVED)
    call("add", type="Metric", fields={"id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate",
                                       "kind": "level", "unit": "pct", "resolution_source": "Yale Budget Lab",
                                       "level": {"value": 22, "source_id": yale}})
    t, l, r = prop("T", ">=", 25), prop("L", ">=", 25, "level_at_date"), prop("R")
    a30, a20 = prop("A30", ">", 30, "level_at_date"), prop("A20", ">", 20, "level_at_date")
    m1 = market("1", "Will the US raise tariffs by March 31?", 0.28, 0.33, size=14_000 / 0.33)  # $14,000 at its ask
    m2 = market("2", "Will the US effective tariff rate be above 30% on March 31, 2027?", 0.10, 0.15)
    m3 = market("3", "Will the US effective tariff rate be above 20% on March 31, 2027?", 0.86, 0.90)
    call("link", from_id="portfolio", relation="watches", to_id=t)
    call("link", from_id=r, relation="asks", to_id=m1)
    call("link", from_id=t, relation="implies", to_id=r)
    call("link", from_id=a30, relation="asks", to_id=m2)
    call("link", from_id=a20, relation="asks", to_id=m3)
    for a, b in ((a30, l), (l, a20), (l, t)):  # each decided by the fields
        call("link", from_id=a, relation="implies", to_id=b)
    call("edit", id=t, field="chances", value=[stated({"given": [r], "value": 0.62})])
    call("edit", id=t, field="baseline", value="no")
    semi = call("add", type="Factor", fields={"name": "semiconductors", "proxy": "SOXX", "order": 2})["id"]
    mkt = channel(SimpleNamespace(store=store), "SPY")
    call("link", from_id=t, relation="consequence", to_id=mkt, fields={"shock": stated(-0.033)})
    call("link", from_id=t, relation="consequence", to_id=semi, fields={"shock": stated(-0.063)})
    ledger_ = call("ledger")
    [row] = ledger_["rows"]  # the ledger's row (TOOLS.md 9), its dollars to the cent
    event = call("get", id=t)  # the event as it now stands, with its distribution
    account = call("get", id=row["basis"]["outcomes"])["output"]  # the whole account, in the Computation behind it
    money = lambda x: round(x, 2)  # noqa: E731
    # what each returns (TOOLS.md 5, 9): the row's fields, and the event's worlds, factors, levels, holdings and distribution
    assert set(row) == {"id", "title", "probability", "range", "rule", "derivation", "yes", "share_yes", "no",
                        "full_impact", "full_impact_parts", "volatility", "worst_outcome", "expected", "contributors",
                        "outcomes", "basis"}
    assert len(row["contributors"]) == 3 and set(row["contributors"][0]) == {
        "name", "company", "full_impact", "yes", "no", "held_directly", "through"}
    assert all(v == round(v, 2) for v in (row["yes"], row["no"], row["full_impact"], row["volatility"]))
    assert row["basis"]["probability"].startswith("cmp:") and account["worlds"]
    assert {"outcomes", "factors", "names", "levels", "holdings", "row", "stated", "links"} <= set(event)
    assert set(event["derived"]) == {"range", "probability", "distribution"}
    assert "probability" not in event["row"] and "by_holding" not in event and "contributors" not in event

    # how likely: the markets imply 10% to 33%; T is 0.62 x 0.305, derived; L is left open
    assert tuple(row["range"]) == pytest.approx((0.10, 0.33))
    assert row["probability"] == pytest.approx(0.1891) and row["rule"] == "derived"
    left = store.get(l)["derived"]["left_open"]["value"]
    assert (round(left["lo"], 4), round(left["hi"], 4)) == (0.125, 0.1891)
    # its derivation: R at its market's price, 0.62 of it by the chance, and none of not R, which T implies
    assert row["derivation"] == [
        {"given": [r], "given_not": [], "probability": 0.305, "markets": [m1], "share": 0.62,
         "chance": f"{t}.chances[0]"},
        {"given": [], "given_not": [r], "probability": 0.695, "markets": [m1], "share": 0.0, "by": "links"}]
    assert sum(c["probability"] * c["share"] for c in row["derivation"]) == pytest.approx(row["probability"])
    # what it does: each price placed at p, and the placed moves between the two sides
    levels = {target: {k: money(v) for k, v in got["worlds"].items()} for target, got in event["levels"].items()}
    assert levels == {mkt: {"yes": 739.53, "no": 764.77}, semi: {"yes": 284.49, "no": 303.62}}
    assert round(100 * account["u_E"][mkt], 4) == -3.3207 and round(100 * account["u_E"][semi], 4) == -1.7269
    # the factors' moves (1.3): the market's compounded move, and semiconductors' own component in z
    shown = store.engine.value("impact", t).output["consequences"].values()
    by_target = {c["target"]: c["worlds"]["yes"] for c in shown}
    assert round(by_target[mkt]["compounded"], 6) == -0.033557 and round(by_target[mkt]["own_part"], 6) == -0.033557
    assert round(by_target[semi]["own_part"], 6) == -0.018092
    assert by_target[semi]["compounded"] == pytest.approx(by_target[semi]["implied"] + by_target[semi]["own_part"])
    held = {h["ticker"]: (h["worlds"]["yes"], h["worlds"]["no"]) for h in event["holdings"]}
    assert held == {"QQQ": (-1379.72, 321.75), "NVDA": (-835.73, 194.89), "TSM": (-464.68, 108.36),
                    "XOM": (-148.80, 34.70), "TLT": (54.54, -12.72), "IBIT": (-142.86, 33.31)}  # the cash it moves not
    # the row
    assert (money(row["yes"]), money(row["no"]), money(row["full_impact"])) == (-2917.26, 680.30, -3597.56)
    assert round(100 * row["share_yes"], 2) == -2.92 and money(row["volatility"]) == 1408.76
    assert money(row["worst_outcome"]["pnl"]) == -2917.26 and row["worst_outcome"]["world"] == "yes"
    assert row["expected"] == 0.0
    # the full impact's three parts, and no company with a part of its own
    assert {c: money(d) for c, d in account["contributions"].items() if d} == {mkt: -2938.84, semi: -656.24}
    assert money(sum(h["curve"]["full_impact"] for h in account["by_holding"] if h.get("curve"))) == -2.48
    assert row["full_impact_parts"] == {  # as ledger() gives them, in factor order, adding to the full impact
        "factors": [{"factor": mkt, "name": "S&P 500", "dollar_beta": 88_500.0, "move": -0.033207, "dollars": -2938.84},
                    {"factor": semi, "name": "semiconductors", "dollar_beta": 38_000.0, "move": -0.017269,
                     "dollars": -656.24}],
        "companies": [], "compounding": -2.48}
    # the ledger by holding: each holding as given, its P&L on each side, summing to the row's
    by_holding = {h["holding"]: h for h in ledger_["by_holding"]}
    assert list(by_holding)[:2] == ["name:QQQ", "name:NVDA"] and by_holding["cash:USD"]["events"][t] == \
        {"yes": 0.0, "no": 0.0}
    assert by_holding["name:NVDA"] == {"holding": "name:NVDA", "value": 15_000.0,
                                       "events": {t: {"yes": -835.73, "no": 194.89}}}
    for side in ("yes", "no"):  # each figure its own value to the cent: half a cent of rounding in each
        assert sum(h["events"][t][side] for h in by_holding.values()) == \
            pytest.approx(row[side], abs=0.005 * len(by_holding) + 1e-9)
    # NVIDIA's share: on the shares held directly, and on the $3,200 inside QQQ
    nvda = row["contributors"][0]
    assert nvda["name"] == "name:NVDA" and nvda["held_directly"] and nvda["through"] == ["name:QQQ"]  # (9)
    assert nvda["full_impact"] == -1250.49
    assert next(c for c in account["contributors"] if c["name"] == "name:NVDA")["exposure"] == pytest.approx(18_200.0)
    [inside] = [n for h in account["by_holding"] if h["name"] == "name:QQQ" for n in h["names"]]
    assert inside["name"] == "name:NVDA" and inside["dollars"] == pytest.approx(3200.0)
    assert money(inside["full_impact"]) == -219.87
    assert next(h for h in event["holdings"] if h["ticker"] == "NVDA")["full_impact"] == -1030.62
    from harness.render import chance, money as shown
    assert (chance(row["probability"]), chance(row["range"][0]), chance(row["range"][1])) == ("18.9%", "10.0%", "33.0%")
    assert (shown(row["yes"]), shown(row["no"], sign=True), shown(row["full_impact"])) == \
        ("\u2212$2,917.26", "+$680.30", "\u2212$3,597.56")
    assert [g for g in call("check").values() if g] == []


# ---------------------------------------------------------------- names and holdings


def holding_world(tmp_path, channels=("USO",)):
    """NVDA held directly, FUND holding NVDA and XOM, and cash; the betas are the store's own, to the market and the
    factors `channels` names."""
    rng = np.random.default_rng(5)
    w = world(tmp_path, registry=default_registry(), channels=channels)
    for ticker in ("NVDA", "XOM"):
        put_reference(w.store, ticker, "CS")
        put_bars(w.store, ticker, closes_from(random_returns(rng, 0.02)))
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("NVDA", 0.6, "Equity"), ("XOM", 0.35, "Equity")])
    put_bars(w.store, "FUND", closes_from(random_returns(rng, 0.015)))
    for ticker, dollars in (("NVDA", 5000.0), ("FUND", 4000.0)):
        w.store.link("portfolio", "holds", f"name:{ticker}", {"dollars": dollars})
    return w


def test_a_company_held_only_below_the_floor_has_its_own_part_on_those_dollars_while_a_consequence_names_it(tmp_path):
    """MATH.md 1.6, 3.1: every dollar of exposure to a company moves by its one price. XYZ is 0.1% of FUND, below the
    look-through's floor, and held nowhere else; a consequence on it, made through the store's link, books its $4 inside
    FUND to it, and its own part reaches those dollars, through FUND's holding; with the consequence removed, nothing
    of XYZ is left in the event or the look-through."""
    rng = np.random.default_rng(5)
    w = world(tmp_path, registry=default_registry())
    for ticker in ("NVDA", "XOM", "XYZ"):
        put_reference(w.store, ticker, "CS")
        put_bars(w.store, ticker, closes_from(random_returns(rng, 0.02)))
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("NVDA", 0.6, "Equity"), ("XOM", 0.35, "Equity"), ("XYZ", 0.001, "Equity")])
    put_bars(w.store, "FUND", closes_from(random_returns(rng, 0.015)))
    w.store.link("portfolio", "holds", "name:FUND", {"dollars": 4000.0})
    e = event(w, p=0.3)
    assert "name:XYZ" not in w.store.engine.value("lookthrough", "portfolio").output["e"]
    shock = w.link(e, "consequence", "name:XYZ", shock=-0.5)
    out = output(w, e)
    assert out["reach"]["name:XYZ"] == pytest.approx(4.0)  # 0.001 of FUND's $4,000
    [held] = [h for h in out["by_holding"] if h["name"] == "name:FUND"]
    [own] = [o for o in held["own"] if o["name"] == "name:XYZ"]
    assert own["dollars"] == pytest.approx(4.0)
    assert own["full_impact"] == pytest.approx(4.0 * own["d"]) and own["full_impact"] < 0
    assert out["own"]["name:XYZ"] == pytest.approx(own["full_impact"])
    _reconciles(out)
    w.store.remove(shock)
    out = output(w, e)
    assert "name:XYZ" not in w.store.engine.value("lookthrough", "portfolio").output["e"]
    assert out is None  # the event states no shock now, so nothing of XYZ, or of anything, is in its figures


def test_what_an_event_does_to_a_set_of_names_is_their_shares_summed_with_each_beside_it(tmp_path):
    """MATH.md 1.7: an industry's or a country's line opens to the events that move its names most. For NVDA and XOM,
    each event gives their shares summed (e_i * m_i,w, by_name), in the full impact, on each side and in each world,
    each name's own beside the sum, from the event's impact Computation; a name the set holds with no share in it is
    left out, and an event the portfolio watches with no figures has no entry."""
    from harness.ledger import moves
    w = holding_world(tmp_path)
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    event(w, "F", p=0.2)  # watched, with no shock: no figures
    [got] = moves(w.store, ["name:NVDA", "name:XOM", "name:CASH:USD"])
    found = w.store.engine.value("impact", e)
    shares = {s["name"]: s for s in found.output["contributors"]}
    assert got["event"] == e and got["basis"] == found.basis and got["probability"] == pytest.approx(0.3)
    assert [n["name"] for n in got["names"]] == ["name:NVDA", "name:XOM"]  # cash has no share
    for k in ("full_impact", "yes", "no"):
        assert got[k] == pytest.approx(shares["name:NVDA"][k] + shares["name:XOM"][k], abs=1e-9)
        assert got[k] == pytest.approx(sum(n[k] for n in got["names"]), abs=1e-9)
    for x in found.output["worlds"]:
        assert got["worlds"][x] == pytest.approx(shares["name:NVDA"]["worlds"][x] + shares["name:XOM"]["worlds"][x],
                                                 abs=1e-9)


def along(w, out, nid, world=None):
    """A name's move at its own betas on the placed factors: b . u_E, or in one world b . u~_w."""
    b = w.store.engine.value("betas", "portfolio").output["objects"][nid]["fields"]["betas"]
    u = out["u_E"] if world is None else out["components"][world]
    return sum(b[c] * u[c] for c in u)


def responds(w, out, nid, q, world="yes"):
    """A name's response moved into its placed full impact by hand (1.6): exp(b . z) - 1 against the baseline in the one
    world that moves, z the compounded factor moves in the orthogonalized basis, placed two worlds apart as
    s / (1 + q s)."""
    b = w.store.engine.value("betas", "portfolio").output["objects"][nid]["fields"]["betas"]
    z = components(w, {c: math.log1p(v) for c, v in out["against_baseline"][world].items()})
    s_r = math.exp(sum(b.get(c, 0.0) * z[c] for c in z)) - 1
    return s_r / (1 + q * s_r)


def _reconciles(out: dict) -> None:
    """In each world the holdings come to the row's, and so do the names' shares with the funds' remainders; the full
    impact is the factors' dollars, the named names' own terms, and what compounding adds to the holdings."""
    for w in out["worlds"]:
        assert sum(h["worlds"][w] for h in out["by_holding"]) == pytest.approx(out["worlds"][w]["A"], abs=1e-9)
        assert sum(s["worlds"][w] for s in out["contributors"]) + sum(r["worlds"][w] for r in out["remainders"]) == \
            pytest.approx(out["worlds"][w]["A"], abs=1e-9)
    curve = sum((h.get("curve") or {}).get("full_impact", 0.0) for h in out["by_holding"])
    assert sum(out["contributions"].values()) + sum(out["own"].values()) + curve == pytest.approx(out["full_impact"], abs=1e-9)
    for n, move in out["own_moves"].items():  # each own part is its dollars times the company's placed own move, d~
        assert out["own"][n] == pytest.approx(out["reach"][n] * move["E"], abs=1e-9)
        for w in out["worlds"]:
            assert out["parts"][w]["own"].get(n, 0.0) == pytest.approx(out["reach"][n] * move[w], abs=1e-9)
    for w, part in out["parts"].items():  # each world's receipt (1.6): the factors at their dollar betas, the own parts,
        # and the rest, adding to its P&L; the factors' part is the holdings at their betas, and the rest their curves
        assert sum(part["factors"].values()) + sum(part["own"].values()) + part["curve"] == \
            pytest.approx(out["worlds"][w]["A"], abs=1e-9)
        assert sum(part["factors"].values()) == pytest.approx(sum(h["at_betas"]["worlds"][w] for h in out["by_holding"]),
                                                              abs=1e-9)
        assert part["curve"] == pytest.approx(sum((h.get("curve") or {}).get("worlds", {}).get(w, 0.0)
                                                  for h in out["by_holding"]), abs=1e-9)


def test_each_holdings_dollars_in_each_world_sum_to_the_rows(tmp_path):
    """NVDA's price -20% against the baseline at p = 0.3: placed, -0.2 / 0.94 apart, on every dollar of it."""
    w = holding_world(tmp_path)
    x = channel(w, "USO")
    e = event(w, p=0.3)
    w.link(e, "consequence", x, shock=-0.1)
    w.link(e, "consequence", "name:NVDA", shock=-0.2)
    out = output(w, e)
    rows = {r["name"]: r for r in out["by_holding"]}
    assert set(rows) == {"name:NVDA", "name:FUND", "name:CASH:USD"}
    assert rows["name:CASH:USD"]["full_impact"] == 0
    assert {n["name"] for n in rows["name:FUND"]["names"]} >= {"name:NVDA", "name:XOM"}  # what it reaches through FUND
    nvda_via_fund = next(n["full_impact"] for n in rows["name:FUND"]["names"] if n["name"] == "name:NVDA")
    move = -0.2 / (1 - 0.3 * 0.2)
    assert nvda_via_fund == pytest.approx(4000 * 0.6 * move)
    fund = rows["name:FUND"]
    assert sum(n["full_impact"] for n in fund["names"]) + sum(r["full_impact"] for r in fund["remainders"]) == pytest.approx(fund["full_impact"])
    assert rows["name:NVDA"]["full_impact"] == pytest.approx(5000 * move)
    _reconciles(out)


def test_each_names_share_comes_to_its_dollars_in_each_world(tmp_path):
    """by_name is each name's exposure times its whole move in each world, its betas' part and its own part; at the
    probability's weights each comes to (1 - p) of its full impact if the event happens and -p of it if not."""
    w = holding_world(tmp_path)
    x = channel(w, "USO")
    e = event(w, p=0.3)
    w.link(e, "consequence", x, shock=-0.1)
    w.link(e, "consequence", "name:NVDA", shock=-0.2)
    out = output(w, e)
    names = {s["name"]: s for s in out["contributors"]}
    for nid, s in names.items():
        assert set(s) - {"curve"} == {"name", "exposure", "full_impact", "at_betas", "own", "yes", "no", "worlds"}
        curve = (s.get("curve") or {}).get("full_impact", 0.0)  # what compounding adds beyond the betas' straight line
        assert s["full_impact"] == pytest.approx(s["exposure"] * along(w, out, nid) + s["own"] + curve)
        assert s["yes"] == pytest.approx(0.7 * s["full_impact"]) and s["no"] == pytest.approx(-0.3 * s["full_impact"])
    nvda = (5000 + 4000 * 0.6) * -0.2 / (1 - 0.3 * 0.2)
    assert names["name:NVDA"]["full_impact"] == pytest.approx(nvda)
    assert [s["name"] for s in out["contributors"]] == sorted(names, key=lambda n: -abs(names[n]["full_impact"]))
    _reconciles(out)


def components(w, moves: dict) -> dict:
    """Moves in the orthogonalized basis, by the store's own coefficients (1.3)."""
    out = {}
    for c in sorted(moves, key=lambda c: w.store.stated_of(c)["order"]):
        coef = w.store.engine.value("orthogonalize", c).output["coefficients"]
        out[c] = moves[c] - sum(beta * out[j] for j, beta in coef.items())
    return out


def test_a_consequence_on_a_names_price_sets_its_whole_move_and_its_residual_is_its_move_against_its_response(tmp_path):
    """NVDA's price -18% against the baseline at p = 0.2 (1.4): its residual there is its move over its response's,
    0.82 / exp(b . z) - 1, with z the world's compounded factor moves in the orthogonalized basis; against today, what it
    does beyond its response is its placed move less its response's placed move, on every dollar of exposure to it
    (1.6)."""
    w = holding_world(tmp_path)
    x = channel(w, "USO")
    e = event(w, p=0.2)
    w.link(e, "consequence", "name:NVDA", shock=-0.18)
    w.link(e, "consequence", x, shock=0.1)
    out = output(w, e)
    b = w.store.engine.value("betas", "portfolio").output["objects"]["name:NVDA"]["fields"]["betas"]
    z = components(w, {c: math.log1p(v) for c, v in out["against_baseline"]["yes"].items()})
    s_r = math.exp(sum(b[c] * z[c] for c in b)) - 1  # the response's move against the baseline
    assert out["d"]["name:NVDA"]["yes"] == pytest.approx(0.82 / (1 + s_r) - 1)
    assert out["d"]["name:NVDA"]["no"] == 0.0  # the baseline has no shock of its own
    move = -0.18 / (1 - 0.2 * 0.18)
    nvda = next(s["full_impact"] for s in out["contributors"] if s["name"] == "name:NVDA")
    assert nvda == pytest.approx((5000 + 4000 * 0.6) * move)  # on every dollar of exposure to it
    assert out["names_set"] == ["name:NVDA"]
    assert out["own"] == {"name:NVDA": pytest.approx((5000 + 4000 * 0.6) * (move - s_r / (1 + 0.2 * s_r)))}
    _reconciles(out)


def test_a_consequence_on_a_held_funds_price_sets_the_holdings_whole_move(tmp_path):
    """FUND, held for $4,000 and looked through, has e 0, and a shock on it sets its price. At p = 0.25,
    FUND's price -10%, NVDA's -20%, and the market's -5% against the baseline: the holding moves by FUND's placed
    price, $4,000 x -0.1 / 0.975, in place of its betas' move and NVDA's own part on the $2,400 through it; NVDA held
    for $5,000 moves $5,000 x -0.2 / 0.95. Yes = 0.75 D and No = -0.25 D. NVDA's share is its whole move on all
    $7,400 of it; FUND's remainder is its move less NVDA's on its $2,400 and XOM at its betas on its $1,400."""
    w = holding_world(tmp_path)
    assert w.store.get("name:FUND")["derived"]["exposure"]["value"] == 0.0
    e = event(w)
    for target, move in (("name:FUND", -0.1), ("name:NVDA", -0.2), (channel(w, "SPY"), -0.05)):
        w.link(e, "consequence", target, shock=move)
    out = output(w, e)
    assert out["names_set"] == ["name:FUND", "name:NVDA"]
    fund_move, nvda_move = -0.1 / (1 - 0.25 * 0.1), -0.2 / (1 - 0.25 * 0.2)
    D = 4000 * fund_move + 5000 * nvda_move
    assert (out["full_impact"], out["yes"], out["no"]) == pytest.approx((D, 0.75 * D, -0.25 * D), abs=1e-9)
    rows = {r["name"]: r for r in out["by_holding"]}
    assert rows["name:FUND"]["full_impact"] == pytest.approx(4000 * fund_move)
    assert rows["name:NVDA"]["full_impact"] == pytest.approx(5000 * nvda_move)
    names = {s["name"]: s for s in out["contributors"]}
    assert names["name:NVDA"]["full_impact"] == pytest.approx(7400 * nvda_move)  # its whole move, through FUND as well
    (fund,) = out["remainders"]
    assert fund["set"] and fund["full_impact"] == pytest.approx(4000 * fund_move - 2400 * nvda_move
                                                      - 1400 * responds(w, out, "name:XOM", 0.25))
    _reconciles(out)


def test_a_companys_share_is_its_whole_move_on_all_of_it_and_a_set_funds_remainder_reconciles(tmp_path):
    """Company A held for $1,000 directly and $1,000 through a $10,000 fund; no factor moves; A's price -10% and the
    fund's +2% against the baseline at p = 0.25. The fund's whole move stands for it: D = $1,000 x -0.1 / 0.975 +
    $10,000 x 0.02 / 1.005; A's share is its move on all $2,000 of it; the fund's remainder is its move less A's
    through it; in each world too."""
    rng = np.random.default_rng(6)
    w = world(tmp_path, registry=default_registry())
    put_reference(w.store, "AAA", "CS")
    put_bars(w.store, "AAA", closes_from(random_returns(rng, 0.02)))
    put_reference(w.store, "FUND", "ETF")
    put_constituents(w.store, "FUND", [("AAA", 0.1, "Equity")])
    put_bars(w.store, "FUND", closes_from(random_returns(rng, 0.015)))
    for ticker, dollars in (("AAA", 1000.0), ("FUND", 10000.0)):
        w.store.link("portfolio", "holds", f"name:{ticker}", {"dollars": dollars})
    e = event(w, p=0.25)
    for ticker, move in (("AAA", -0.1), ("FUND", 0.02)):
        w.link(e, "consequence", f"name:{ticker}", shock=move)
    out = output(w, e)
    assert not any(out["u_E"].values())  # no factor moves
    a_move, f_move = -0.1 / (1 - 0.025), 0.02 / (1 + 0.005)
    assert out["full_impact"] == pytest.approx(1000 * a_move + 10_000 * f_move, abs=1e-9)
    (a,) = out["contributors"]
    assert a["name"] == "name:AAA" and a["exposure"] == pytest.approx(2000.0) and a["full_impact"] == pytest.approx(2000 * a_move)
    (fund,) = out["remainders"]
    assert fund["set"] and fund["full_impact"] == pytest.approx(10_000 * f_move - 1000 * a_move)
    assert (a["yes"], a["no"]) == pytest.approx((0.75 * a["full_impact"], -0.25 * a["full_impact"]))
    assert (fund["yes"], fund["no"]) == pytest.approx((0.75 * fund["full_impact"], -0.25 * fund["full_impact"]))
    _reconciles(out)


def _abroad_store(tmp_path, name, holdings, rows):
    from massive_data import put_home, put_rate
    from rows_fixture import rows_registry
    from test_estimation import ABROAD, DAYS, KNOWLEDGE, SESSIONS
    from harness.store import Store
    rng = np.random.default_rng(7)
    store = Store.create(tmp_path / name, registry=rows_registry())
    store.load_initial(KNOWLEDGE)
    m = rng.normal(0, 0.01, len(DAYS))
    put_bars(store, "SPY", closes_from(dict(zip(DAYS, m))))
    level, levels = 0.00075, {SESSIONS[0]: 0.00075}
    for d, x, o in zip(DAYS, m, rng.normal(0, 0.004, len(DAYS))):
        level *= 1.0 + 0.5 * x + o
        levels[d] = level
    put_rate(store, "C:KRWUSD", levels, base="KRW", quote="USD")
    store.add("Factor", {"name": "the won", "proxy": "C:KRWUSD", "order": 2})
    put_home(store, ABROAD, closes_from(dict(zip(DAYS, 0.8 * m + rng.normal(0, 0.01, len(DAYS)))), 70000.0))
    put_reference(store, ABROAD, "CS")
    put_bars(store, "AAA", closes_from(dict(zip(DAYS, m + rng.normal(0, 0.01, len(DAYS))))))
    put_reference(store, "AAA", "CS")
    fund = "KR7FUNDX0001:KR"
    put_home(store, fund, closes_from(dict(zip(DAYS, 0.9 * m + rng.normal(0, 0.004, len(DAYS)))), 25000.0))
    put_reference(store, fund, "ETF")
    store.fixture_rows = rows(fund, ABROAD)
    store.add("Portfolio", {"asof": "2026-11-04", "holdings": holdings(fund, ABROAD)})
    e = store.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                  "window_end": "2027-03-31", "chances": [stated(0.5)], "baseline": "no"})["id"]
    store.link("portfolio", "watches", e)
    return store, e, fund, ABROAD


def test_a_set_fund_quoted_abroad_is_its_price_times_its_rate_scaled_to_average_to_today(tmp_path):
    """A $10,000 fund quoted in won, looked through, at p = 0.5: its price in won +20% and the won +10% against the
    baseline. Placed, its local price is 1.2 / 1.1 and 1 / 1.1 of today's and the rate 1.1 / 1.05 and 1 / 1.05; their
    product, 1.142857 and 0.865801, averages 1.004329 at the weights, so scaled together they are 1.137931 and 0.862069:
    Yes +$1,379.31, No -$1,379.31, and no expected value (1.6). Its names with its remainder come to each world."""
    from rows_fixture import holding
    store, e, fund, abroad = _abroad_store(tmp_path, "scratch-quoted", lambda f, a: [{"ticker": f, "dollars": 10000.0}],
                                           lambda f, a: {f: [holding(a, 0.6), holding("AAA", 0.4)]})
    won = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "C:KRWUSD")
    for target, move in ((f"name:{fund}", 0.2), (won, 0.1)):
        store.link(e, "consequence", target, {"shock": stated(move)})
    out = store.engine.value("impact", e).output
    assert (round(out["yes"], 2), round(out["no"], 2)) == (1379.31, -1379.31)
    assert out["expected"] == pytest.approx(0.0, abs=1e-9)
    (remainder,) = out["remainders"]
    assert remainder["set"] and remainder["full_impact"] == pytest.approx(remainder["yes"] - remainder["no"])
    assert {n["name"] for n in out["contributors"]} == {f"name:{abroad}", "name:AAA"}
    _reconciles(out)
    store.close()


def test_a_name_quoted_abroad_is_its_local_price_times_its_rate_placed_once(tmp_path):
    """A listing in won held for $10,000, at p = 0.5: its price in won +20% and the won -20% against the baseline. Its
    dollar price moves by the two together, 1.2 x 0.8 = 0.96 against the baseline, and is placed once: 0.96 / 0.98 and
    1 / 0.98, Yes -$204.08 and No +$204.08, no expected value. The holding reads as its dollar betas' move, its own
    part, and what compounding adds; its share, its names, and the row agree in each world (1.6, 1.7)."""
    from harness.ledger import row
    store, e, fund, abroad = _abroad_store(tmp_path, "scratch-abroad", lambda f, a: [{"ticker": a, "dollars": 10000.0}],
                                           lambda f, a: {})
    won = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "C:KRWUSD")
    for target, move in ((f"name:{abroad}", 0.2), (won, -0.2)):
        store.link(e, "consequence", target, {"shock": stated(move)})
    out = store.engine.value("impact", e).output
    cents = lambda *xs: tuple(round(x, 2) + 0.0 for x in xs)  # noqa: E731  each to the cent, -0.00 as 0.00
    assert cents(out["yes"], out["no"], out["full_impact"]) == (-204.08, 204.08, -408.16)
    assert out["expected"] == pytest.approx(0.0, abs=1e-9)
    name = f"name:{abroad}"
    (s,) = out["contributors"]
    assert s["name"] == name and cents(s["yes"], s["no"]) == (-204.08, 204.08)
    (h,) = out["by_holding"]
    (own,) = h["own"]
    assert own["name"] == name
    for w in ("yes", "no"):  # its dollar betas' move, its own part, and what compounding adds
        assert h["at_betas"]["worlds"][w] + own["worlds"][w] + h["curve"]["worlds"][w] == \
            pytest.approx(h["worlds"][w], abs=1e-9)
    _reconciles(out)
    shown = row(store, e)
    (named,) = shown["by_holding"][0]["names"]
    assert cents(named["yes"], named["no"]) == (-204.08, 204.08)
    assert "bound" not in shown and shown["worlds"]["yes"]["q"] == 0.5
    store.close()


def test_adding_a_consequence_asks_no_unchanged_check_again(tmp_path):
    w = world(tmp_path, g={"USO": 10_000.0})
    e = event(w)
    w.link(e, "asks", w.market("e", "E?", 0.24, 0.26))
    w.link(e, "consequence", channel(w, "USO"), shock=0.1)
    check(w.store)
    asked = list(w.judge.calls)
    w.link(e, "consequence", channel(w, "SPY"), shock=0.01)
    check(w.store)
    assert w.judge.calls == asked  # the market check reads the proposition and the market, and neither changed


def test_a_consequence_link_shows_its_levels_and_its_moves_in_each_world_it_applies_in(tmp_path):
    """link, edit, and get on a consequence give the price's levels and, in each world it applies in, its move against
    the baseline and its residual, in the terms a shock on it is stated in, read from the event's impact: a factor's
    residual is its compounded move less what the earlier factors imply, as a move; a name's, its whole move over its
    response's. An unwatched event has no impact, and the fields are not there."""
    from harness.derive.impact import FOLLOWED
    w = holding_world(tmp_path)
    oil = channel(w, "USO")
    e = w.prop("E", chance={"value": 0.3}, baseline="no")
    first = w.store.link(e, "consequence", oil, {"shock": stated(0.1)})
    assert "moves" not in first["derived"]
    w.link("portfolio", "watches", e)
    got = w.store.get(first["id"])["derived"]["moves"]
    out = output(w, e)
    shown = out["consequences"][first["id"]]["worlds"]["yes"]  # the impact's own numbers, its working's logs among them
    assert out["consequences"][first["id"]]["factor"] == oil
    assert shown["implied"] == pytest.approx(shown["compounded"] - shown["own_part"])
    assert got["value"] == {"yes": {"move": pytest.approx(0.10), FOLLOWED: pytest.approx(math.expm1(shown["own_part"]))}}
    inputs = w.store.get(got["basis"])["inputs"]
    assert inputs["impact"]["basis"] == w.store.get(e)["derived"]["full_impact"]["basis"]  # it reads the impact Computation

    named = w.store.link(e, "consequence", "name:NVDA", {"shock": stated(-0.2)})
    out = output(w, e)
    n = named["derived"]["moves"]["value"]["yes"]
    shown = out["consequences"][named["id"]]["worlds"]["yes"]
    assert out["consequences"][named["id"]]["name"] == "name:NVDA" and n["move"] == pytest.approx(-0.2)
    assert n[FOLLOWED] == pytest.approx(out["d"]["name:NVDA"]["yes"])
    assert n[FOLLOWED] == pytest.approx(0.8 / (1 + shown["betas"]) - 1)

    edited = w.store.edit(first["id"], "shock", stated(0.2))
    assert edited["derived"]["moves"]["value"]["yes"]["move"] == pytest.approx(0.20)
    w.store.link(e, "consequence", channel(w, "SPY"), {"shock": stated(0.01)})
    assert output(w, e)["consequence_set"] == sorted([oil, channel(w, "SPY")])


def test_what_an_event_now_does_in_every_world(tmp_path):
    """What link and edit return for a shock (ledger.event_now): each world in the outcome tree, each factor's move
    against the baseline in every world with how it got it, a shock of the world's own or one that follows the set
    factors, and each holding it moves, with its gain or loss there."""
    from harness.ledger import event_now
    w = holding_world(tmp_path)
    e = event(w, p=0.3)
    spy_id = channel(w, "SPY")
    lid = w.link(e, "consequence", spy_id, shock=-0.1)
    now = event_now(w.store, e)
    assert now["baseline"] == "no" and [(o["id"], o["world"]) for o in now["outcomes"]] == [("yes", True), ("no", True)]
    assert [o["title"] for o in now["outcomes"]] == ["Yes", "No"] and now["outcomes"][1]["baseline"]
    (spy,) = [f for f in now["factors"] if f["factor"] == spy_id]
    placed_ = output(w, e)["placed"]
    assert spy["worlds"]["yes"] == {"move": pytest.approx(-0.10), "against_today": pytest.approx(placed_["yes"][spy_id]),
                                    "by": "own", "on": e, "consequence": lid,
                                    "stated": {"value": -0.1, "against": schema.BASELINE}}
    assert "no" not in spy["worlds"]  # the baseline, where nothing is stated and nothing moves
    assert now["levels"][spy_id]["worlds"]["yes"] == pytest.approx(
        w.store.get(lid)["derived"]["levels"]["value"]["yes"], abs=1e-6)
    assert now["row"]["worst_outcome"]["title"] in ("Yes", "No")
    held = {h.get("ticker") or f"cash:{h['cash']}": h for h in now["holdings"]}
    assert set(held) == {"NVDA", "FUND"}  # each holding it moves: the cash, at 0.00 in every world, is not one
    assert sum(h["worlds"]["yes"] for h in now["holdings"]) == pytest.approx(output(w, e)["yes"], abs=0.05)


# ---------------------------------------------------------------- a price is never below zero (MATH.md 1.6)


def _bound_case(holdings: dict, betas: dict, paths: dict, reaches: dict, e: dict, expanded=("LEV",), *, p=0.2,
                shock=-0.5 / 1.1, second=None, multiples=None, named=None, against=schema.BASELINE) -> dict:
    """The impact of one event on hand-built holdings, betas, and look-through: the market shocked `shock`, a fraction
    of its price in the baseline, at probability p (by default -0.5/1.1 at 0.2, so its placed moves are -40% and +10%).
    `second` adds a factor no consequence sets, (its coefficient on the market, each name's beta to it); `multiples` is
    the look-through's funds that pay a multiple of a reference each session; `named` sets names' shocks, as fractions,
    each against the baseline or, by `against`, against what it follows."""
    c, f2 = "factor:1", "factor:2"
    ids = [c] + ([f2] if second else [])
    fitted = {"objects": {n: {"fields": {"betas": {c: x, **({f2: second[1].get(n, 0.0)} if second else {})}}}
                          for n, x in betas.items()}, "fits": {}}
    looked = {"e": e, "funds": {t: {"expanded": True} for t in expanded}, "paths": paths, "reaches": reaches,
              **({"multiples": multiples} if multiples else {})}
    g = {k: sum(v * fitted["objects"][n]["fields"]["betas"][k] for n, v in holdings.values()) for k in ids}
    m = {"event": "prop:1", "p": p, "baseline": "no",
         "worlds": [{"id": "yes", "side": "yes", "p": p}, {"id": "no", "side": "no", "p": 1 - p}], "weight": None,
         "channels": ids, "coefficients": {c: {}, **({f2: {c: second[0]}} if second else {})},
         "variances": {c: 0.04, **({f2: 0.01} if second else {})},
         "g": g, "holds": {h: n for h, (n, _) in holdings.items()},
         "values": {h: v for h, (_, v) in holdings.items()},
         "lookthrough": {"basis": "cmp:look", "sha256": "x"}, "betas": {"basis": "cmp:betas", "sha256": "y"},
         "fund_rows": {"basis": "cmp:rows", "sha256": "z"},
         "consequences": [{"link": "link:9", "target": c, "on": "prop:1", "shock": shock, "c": 100.0,
                           "basis": "cmp:level"}]
         + [{"link": f"link:{10 + i}", "target": f"name:{n}", "on": "prop:1", "shock": x, "against": against,
             "c": 100.0, "basis": "cmp:level"} for i, (n, x) in enumerate((named or {}).items())]}
    return impact._impact_compute(m, lambda ref: {"cmp:look": looked, "cmp:betas": fitted,
                                                  "cmp:rows": {"rows": {}}}[ref["basis"]])


def _held(nid, dollars):  # one holding of a name, held directly, with its dollars as its exposure
    return {"h:1": (nid, dollars)}, {"h:1": [{"funds": [], "name": nid, "dollars": dollars}]}, {}, {nid: dollars}


def test_spec_1_6s_illustration_a_holding_at_beta_2_and_its_receipt():
    """$10,000 of a holding with a beta of 2 to the market, and an event at 0.30 whose Yes side moves the market -40%:
    the holding stands at 0.6^2 = 0.36 of its baseline price if the event happens; placed, the market moves -31.82% and
    +13.64% against today and the holding -55.45% and +23.76%. If the event happens the holding loses $5,544.55: its
    dollar beta of $20,000 times -31.82% is -$6,363.64, and compounding adds +$819.08. If it does not, it gains
    $2,376.24: +$2,727.27, and compounding takes $351.04 (MATH.md 1.6)."""
    holdings, paths, reaches, e = _held("name:H", 10_000.0)
    out = _bound_case(holdings, {"name:H": 2.0}, paths, reaches, e, (), p=0.3, shock=-0.4)
    (h,) = out["by_holding"]
    cents = lambda d: {k: round(v, 2) for k, v in d.items()}  # noqa: E731
    assert cents(h["worlds"]) == {"yes": -5544.55, "no": 2376.24}
    assert cents(h["at_betas"]["worlds"]) == {"yes": -6363.64, "no": 2727.27}
    assert cents(h["curve"]["worlds"]) == {"yes": 819.08, "no": -351.04}


def test_spec_1_4s_illustration_a_companys_residual_is_its_move_over_its_responses():
    """A world moves the market -10%; a company with a beta of 1.8 moves 0.9^1.8 - 1 = -17.28% by its response; the
    analyst states -25% on its price, against the baseline; its residual is 0.75 / 0.8272 - 1 = -9.34%, and -0.0934
    stated against what it follows is the same world (MATH.md 1.4)."""
    holdings, paths, reaches, e = _held("name:CO", 1_000.0)
    out = _bound_case(holdings, {"name:CO": 1.8}, paths, reaches, e, (), shock=-0.1, named={"CO": -0.25})
    s = out["consequences"]["link:10"]
    assert round(100 * s["worlds"]["yes"]["betas"], 2) == -17.28 and round(100 * s["worlds"]["yes"]["d"], 2) == -9.34
    a = 0.75 / 0.9 ** 1.8 - 1
    again = _bound_case(holdings, {"name:CO": 1.8}, paths, reaches, e, (), shock=-0.1, named={"CO": a},
                        against=schema.FOLLOWS)
    assert again["whole"]["name:CO"]["yes"] == pytest.approx(-0.25)
    assert again["yes"] == pytest.approx(out["yes"]) and again["no"] == pytest.approx(out["no"])


def test_a_holdings_two_outcomes_stand_in_one_ratio_whatever_the_probability():
    """What an outcome does is fixed by what is stated about it: at a beta of 2 and the market 40% lower, the holding's
    Yes stands at 0.36 of its No at a probability of 0.05, 0.30 or 0.90, where the straight line put it 78%, 71% or 59%
    below (1.6)."""
    holdings, paths, reaches, e = _held("name:H", 10_000.0)
    for p in (0.05, 0.30, 0.90):
        (h,) = _bound_case(holdings, {"name:H": 2.0}, paths, reaches, e, (), p=p, shock=-0.4)["by_holding"]
        assert (10_000.0 + h["worlds"]["yes"]) / (10_000.0 + h["worlds"]["no"]) == pytest.approx(0.36)


def test_a_factor_added_with_nothing_stated_on_it_changes_no_holdings_figure():
    """A second factor no consequence sets is filled with the market: its compounded move is its coefficient times the
    market's, so its orthogonal part is zero in every world, and a holding with a beta to it stands where it stood
    without it (1.3, 1.6)."""
    holdings, paths, reaches, e = _held("name:H", 10_000.0)
    alone = _bound_case(holdings, {"name:H": 1.6}, paths, reaches, e, (), shock=-0.3)["by_holding"][0]["worlds"]
    added = _bound_case(holdings, {"name:H": 1.6}, paths, reaches, e, (), shock=-0.3,
                        second=(1.2, {"name:H": 0.7}))["by_holding"][0]["worlds"]
    assert added == {"yes": pytest.approx(alone["yes"]), "no": pytest.approx(alone["no"])}


def test_a_short_of_a_fund_that_pays_three_times_its_reference_each_session():
    """$5,000 short of a fund paying 3x an index each session, the index at beta 1, the market 40% lower if the event
    happens at 0.2: the index's placed moves are 0.6 / 0.92 - 1 = -34.78% and 1 / 0.92 - 1 = +8.70%, so the fund stands
    at zero (3 x -34.78% would take it below) and at 1 + 3 x 8.70% = 1.2609. The short gains $5,000 or loses $1,304.35:
    not placed again, its worlds come to $43.48 below its value today, its expected value (1.6)."""
    fund, ref = "name:LEV3", "name:IDX"
    holdings, paths, reaches, e = _held(fund, -5_000.0)
    out = _bound_case(holdings, {fund: 3.0, ref: 1.0}, paths, reaches, e, (), shock=-0.4,
                      multiples={"LEV3": {"reference": ref, "multiple": 3.0}})
    (h,) = out["by_holding"]
    assert h["worlds"] == {"yes": pytest.approx(5_000.0), "no": pytest.approx(-5_000.0 * 3 * (1 / 0.92 - 1))}
    assert out["expected"] == pytest.approx(0.2 * 5_000.0 - 0.8 * 5_000.0 * 3 * (1 / 0.92 - 1))
    assert round(out["expected"], 2) == -43.48


def test_a_fund_valued_by_its_terms_is_worth_what_they_give_in_each_world_a_shock_does_not_touch():
    """MATH.md 1.6: a fund paying twice its reference each session, $10,000 of it, the market 20% lower if the event
    happens at 0.2. Unstated, it stands at its terms' value in each world. Its residual at 0 changes nothing. A residual
    of -10% if the event happens puts that world at 0.9 of its terms' value and leaves No at its terms', so its expected
    value moves by the probability times what was stated. A whole move of -30% if it happens puts that world at 0.7 of
    the No world's terms' value, the baseline's, and No at its terms', not its betas'."""
    fund, ref = "name:LEV2", "name:IDX"
    holdings, paths, reaches, e = _held(fund, 10_000.0)
    terms = {"LEV2": {"reference": ref, "multiple": 2.0}}
    case = lambda **kw: _bound_case(holdings, {fund: 2.0, ref: 1.0}, paths, reaches, e, (), shock=-0.2,  # noqa: E731
                                    multiples=terms, **kw)
    unstated = case()
    u = {w: 1 + v / 10_000.0 for w, v in unstated["by_holding"][0]["worlds"].items()}  # its terms' value to today's
    assert u["yes"] > 0.0 and unstated["expected"] == pytest.approx(0.0, abs=1e-9)  # linear here: no expected value
    at_zero = case(named={"LEV2": 0.0}, against=schema.FOLLOWS)
    assert at_zero["by_holding"][0]["worlds"] == {w: pytest.approx(v) for w, v in
                                                  unstated["by_holding"][0]["worlds"].items()}
    fell = case(named={"LEV2": -0.1}, against=schema.FOLLOWS)
    assert fell["by_holding"][0]["worlds"] == {"yes": pytest.approx(10_000.0 * (u["yes"] * 0.9 - 1)),
                                               "no": pytest.approx(10_000.0 * (u["no"] - 1))}
    assert fell["expected"] == pytest.approx(0.2 * 10_000.0 * u["yes"] * -0.1)
    whole = case(named={"LEV2": -0.3})
    assert whole["by_holding"][0]["worlds"] == {"yes": pytest.approx(10_000.0 * (u["no"] * 0.7 - 1)),
                                                "no": pytest.approx(10_000.0 * (u["no"] - 1))}


def test_a_fund_valued_by_its_terms_is_not_placed_whatever_parts_the_look_through_gives_it():
    """MATH.md 1.6: the 2x fund above, now with look-through parts, a company its rows carry: what values it is still its
    multiple of its reference, so a residual of -10% if the event happens puts Yes at 0.9 of its terms' value and
    leaves No at its terms', with no level placed, and at 0 it stands where it stood unstated."""
    fund, ref, co = "name:LEV2", "name:IDX", "name:CO"
    holdings = {"h:1": (fund, 10_000.0)}
    paths = {"h:1": [{"funds": ["LEV2"], "name": co, "dollars": 2_000.0}]}
    reaches = {"h:1": [{"funds": [], "fund": "LEV2", "dollars": 10_000.0}]}
    e = {fund: 0.0, co: 2_000.0}
    terms = {"LEV2": {"reference": ref, "multiple": 2.0}}
    case = lambda **kw: _bound_case(holdings, {fund: 2.0, ref: 1.0, co: 1.0}, paths, reaches, e, ("LEV2",),  # noqa: E731
                                    shock=-0.2, multiples=terms, **kw)
    unstated = case()
    u = {w: 1 + v / 10_000.0 for w, v in unstated["by_holding"][0]["worlds"].items()}
    at_zero = case(named={"LEV2": 0.0}, against=schema.FOLLOWS)
    assert at_zero["by_holding"][0]["worlds"] == {w: pytest.approx(v) for w, v in
                                                  unstated["by_holding"][0]["worlds"].items()}
    fell = case(named={"LEV2": -0.1}, against=schema.FOLLOWS)
    assert fell["by_holding"][0]["worlds"] == {"yes": pytest.approx(10_000.0 * (u["yes"] * 0.9 - 1)),
                                               "no": pytest.approx(10_000.0 * (u["no"] - 1))}


def test_a_fund_the_look_through_expands_stated_against_what_it_follows_stands_from_where_its_companies_put_it():
    """MATH.md 1.6: a fund carrying a company the event states at -20% against the baseline, the fund stated at 0 against
    what it follows: it stands where it stood unstated, its response with the company's move beyond its response on
    the dollars it carries, so the company's move still reaches it. Stated -5%, it stands 0.95 of there in the Yes
    world, its average at the weights kept, and the company's move is not counted on its dollars a second time."""
    fund, co = "name:FND", "name:CO"
    holdings = {"h:1": (fund, 10_000.0)}
    paths = {"h:1": [{"funds": ["FND"], "name": co, "dollars": 2_000.0}]}
    reaches = {"h:1": [{"funds": [], "fund": "FND", "dollars": 10_000.0}]}
    e = {fund: 0.0, co: 2_000.0}
    betas = {fund: 1.0, co: 1.2}
    unstated = _bound_case(holdings, betas, paths, reaches, e, ("FND",), shock=-0.1, named={"CO": -0.2})
    # both stated against what they follow: CO at the figure that gives it the same -20% whole, its response 0.9^1.2
    # compounded with it, and the fund at 0
    r = 0.9 ** 1.2
    at_zero = _bound_case(holdings, betas, paths, reaches, e, ("FND",), shock=-0.1,
                          named={"CO": 0.8 / r - 1, "FND": 0.0}, against=schema.FOLLOWS)
    assert at_zero["by_holding"][0]["worlds"] == {w: pytest.approx(v, abs=1e-6) for w, v in
                                                  unstated["by_holding"][0]["worlds"].items()}
    fell = _bound_case(holdings, betas, paths, reaches, e, ("FND",), shock=-0.1,
                       named={"CO": 0.8 / r - 1, "FND": -0.05}, against=schema.FOLLOWS)
    before = {w: 1 + v / 10_000.0 for w, v in unstated["by_holding"][0]["worlds"].items()}
    q = {"yes": 0.2, "no": 0.8}
    keep = sum(q[w] * before[w] for w in q) / (q["yes"] * before["yes"] * 0.95 + q["no"] * before["no"])
    assert fell["by_holding"][0]["worlds"] == {"yes": pytest.approx(10_000.0 * (before["yes"] * 0.95 * keep - 1)),
                                               "no": pytest.approx(10_000.0 * (before["no"] * keep - 1))}
    assert "name:CO" not in fell["own"] or fell["reach"]["name:CO"] == pytest.approx(0.0)  # not counted twice
    _reconciles(fell)


def test_every_price_moves_by_its_response_and_is_placed_once_whichever_side_is_held():
    """$20,000 in a fund at beta 3 looked through to A, B, C (2.7 dollars of them per dollar of the fund), $10,000 of A
    held directly, $5,000 of D held short; betas: the fund 3.0, A 1.3, B 1.1, C 0.9, D 2.6; the market 0.6 / 1.1 of its
    baseline level if the event happens, at p = 0.2. Each price stands against the baseline at (0.6 / 1.1)^b, its
    response, and is placed once, L_base = 1 / (0.2 r + 0.8): nothing stops at zero and nothing is rescaled, every
    holding averages to its value today, and a short gains what the same long would lose (MATH.md 1.6). The fund's
    remainder is its own move less its companies' moves at their dollars through it (1.7)."""
    lev, a, b, c, d = (f"name:{t}" for t in ("LEV", "A", "B", "C", "D"))
    out = _bound_case(
        {"h:1": (lev, 20_000.0), "h:2": (a, 10_000.0), "h:3": (d, -5_000.0)},
        {lev: 3.0, a: 1.3, b: 1.1, c: 0.9, d: 2.6},
        {"h:1": [{"funds": ["LEV"], "name": a, "dollars": 24_000.0},
                 {"funds": ["LEV"], "name": b, "dollars": 18_000.0},
                 {"funds": ["LEV"], "name": c, "dollars": 12_000.0}],
         "h:2": [{"funds": [], "name": a, "dollars": 10_000.0}], "h:3": [{"funds": [], "name": d, "dollars": -5_000.0}]},
        {"h:1": [{"funds": [], "fund": "LEV", "dollars": 20_000.0}]},
        {a: 34_000.0, b: 18_000.0, c: 12_000.0, d: -5_000.0, lev: 0.0})
    def move(beta):  # (yes, no): the response against the baseline, placed once
        r = (0.6 / 1.1) ** beta
        base = 1 / (0.2 * r + 0.8)
        return r * base - 1, base - 1
    held = {h["name"]: h for h in out["by_holding"]}
    for nid, dollars, beta in ((lev, 20_000.0, 3.0), (a, 10_000.0, 1.3), (d, -5_000.0, 2.6)):
        yes, no = move(beta)
        assert held[nid]["worlds"] == {"yes": pytest.approx(dollars * yes), "no": pytest.approx(dollars * no)}
        assert 0.2 * held[nid]["worlds"]["yes"] + 0.8 * held[nid]["worlds"]["no"] == pytest.approx(0.0, abs=1e-9)
        assert "scaled" not in held[nid]
    assert out["expected"] == pytest.approx(0.0, abs=1e-9)
    names = {s["name"]: s for s in out["contributors"]}
    for nid, dollars, beta in ((a, 34_000.0, 1.3), (b, 18_000.0, 1.1), (c, 12_000.0, 0.9), (d, -5_000.0, 2.6)):
        assert names[nid]["yes"] == pytest.approx(dollars * move(beta)[0])
    [fund] = out["remainders"]
    through = sum(dollars * move(beta)[0] for dollars, beta in ((24_000.0, 1.3), (18_000.0, 1.1), (12_000.0, 0.9)))
    assert fund["worlds"]["yes"] == pytest.approx(20_000.0 * move(3.0)[0] - through)
    _reconciles(out)


def test_a_company_inside_a_fund_moves_every_exposure_to_it_by_its_response_and_the_funds_remainder_takes_the_rest():
    """A fund at beta 1 holding X at half its dollars, X at beta 2.6, and X held directly; the market 0.6 / 1.1 of its
    baseline level if the event happens, at p = 0.2. X stands at (0.6 / 1.1)^2.6 of its baseline price, placed once, on
    the $5,000 through the fund as on the $1,000 held; the fund moves its own -40% and +10%, and its remainder is that
    less X's (1.6, 1.7)."""
    f, x = "name:F", "name:X"
    out = _bound_case({"h:1": (f, 10_000.0), "h:2": (x, 1_000.0)}, {f: 1.0, x: 2.6},
                      {"h:1": [{"funds": ["F"], "name": x, "dollars": 5_000.0}],
                       "h:2": [{"funds": [], "name": x, "dollars": 1_000.0}]},
                      {"h:1": [{"funds": [], "fund": "F", "dollars": 10_000.0}]}, {x: 6_000.0, f: 0.0},
                      expanded=("F",))
    r = (0.6 / 1.1) ** 2.6
    base = 1 / (0.2 * r + 0.8)
    yes, no = r * base - 1, base - 1  # X's placed move
    held = {h["name"]: h for h in out["by_holding"]}
    assert held[f]["worlds"] == {"yes": pytest.approx(-4_000.0), "no": pytest.approx(1_000.0)}
    assert held[x]["worlds"] == {"yes": pytest.approx(1_000.0 * yes), "no": pytest.approx(1_000.0 * no)}
    [through] = held[f]["names"]
    assert through["worlds"] == {"yes": pytest.approx(5_000.0 * yes), "no": pytest.approx(5_000.0 * no)}
    [name] = out["contributors"]
    assert name["worlds"] == {"yes": pytest.approx(6_000.0 * yes), "no": pytest.approx(6_000.0 * no)}
    [fund] = out["remainders"]
    assert fund["worlds"] == {"yes": pytest.approx(-4_000.0 - 5_000.0 * yes), "no": pytest.approx(1_000.0 - 5_000.0 * no)}
    _reconciles(out)


def test_a_step_between_replies_keeps_each_world_and_the_impact_by_its_computation(tmp_path):
    """ledger.watched: each world's probability, weight and P&L, the impact's Computation named by id,
    and a step dated by every reading under the figures it holds."""
    from harness.ledger import watched
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}})
    x = channel(w, "USO")
    e = event(w)
    w.link(e, "consequence", x, shock=0.2)
    step = watched(w.store)[e]
    out = output(w, e)
    assert step["worlds"] == {k: {"p": v["p"], "q": v["q"], "A": pytest.approx(v["A"])} for k, v in out["worlds"].items()}
    assert step["basis"]["worlds"] == w.store.get(e)["derived"]["worlds"]["basis"]
    assert step["basis"]["worlds"] not in step["computations"]  # the whole portfolio's, kept by its id
    assert step["at"] is not None


def test_editing_the_baseline_or_the_weight_returns_what_the_event_now_does(tmp_path):
    """The baseline can be edited while shocks are stated against it; edit returns the event in every world, as a
    shock's edit does."""
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = world(tmp_path, g={"USO": 10_000.0}, coefficients={"USO": {}})
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))
    x = channel(w, "USO")
    e = event(w)
    w.link(e, "consequence", x, shock=0.2)
    ok, got = tools.call("edit", {"id": e, "field": "baseline", "value": "yes"})
    assert ok and got["events"][0]["baseline"] == "yes"
    (oil,) = [f for f in got["events"][0]["factors"] if f["factor"] == channel(w, "USO")]
    assert oil["worlds"]["no"]["move"] == pytest.approx(0.2) and oil["worlds"]["no"]["by"] == "own"
    assert "yes" not in oil["worlds"]  # the baseline now, where nothing moves
    ok, got = tools.call("edit", {"id": e, "field": "priced_in", "value": stated(0.5)})
    assert ok and {o["id"]: o for o in got["events"][0]["outcomes"]}["yes"]["q"] == pytest.approx(0.5)


def test_a_fund_paying_twice_a_company_each_session_counts_the_companys_own_part_once():
    """$10,000 in a fund paying 2x company C each session, looked through to C at $20,000; C stated -50% and the market
    -10%, at p 0.3. C's placed move, 0.5 / 0.85 - 1 and 1 / 0.85 - 1, holds its own part, so the fund stands at
    1 + 2 x those, -$8,235.29 and +$3,529.41, by its terms: C's own part is not added again (1.6)."""
    fund, c = "name:LEV2", "name:C"
    out = _bound_case({"h:1": (fund, 10_000.0)}, {fund: 2.0, c: 1.0},
                      {"h:1": [{"funds": ["LEV2"], "name": c, "dollars": 20_000.0}]},
                      {"h:1": [{"funds": [], "fund": "LEV2", "dollars": 10_000.0}]}, {c: 20_000.0, fund: 0.0},
                      ("LEV2",), p=0.3, shock=-0.1, named={"C": -0.5},
                      multiples={"LEV2": {"reference": c, "multiple": 2.0}})
    (h,) = out["by_holding"]
    assert {k: round(v, 2) for k, v in h["worlds"].items()} == {"yes": -8235.29, "no": 3529.41}


def test_each_kind_of_shock_through_the_tools_returns_the_event_with_the_level_of_each_price_it_sets(tmp_path):
    """Every kind of shock, stated through link, returns the event as it now stands (TOOLS.md 5), with the level in each
    world of each price a shock sets (1.5). An event at 0.3 whose Yes side has two ways at 0.5 each, its baseline No:
    the market -10% on the event and -20% on the first way, which restates it there; long Treasuries, quoted as a yield
    at 4.2 and a duration of 17, +0.5 points; NVDA, held, -20%; FUND, held, -10%; and AMD, a company the portfolio
    neither holds nor reaches, -30% on the first way alone, so it moves by its response in the second. Each price is
    placed once: the market at 0.8 / 0.955, 0.9 / 0.955 and 1 / 0.955 of today's, 0.955 = 0.15 x 0.8 + 0.15 x 0.9 +
    0.7; the bond fund at e^-0.085 L and L, L = 1 / (0.3 e^-0.085 + 0.7), its yield 4.2 - 100 ln(1 + m) / 17; NVDA at
    0.8 / 0.94; FUND at 0.9 / 0.97. AMD is fitted on its own series though nothing holds it, and moves no holding."""
    from harness.tools import Tools
    from harness.web import Web
    from harness.world import World as Harness
    w = holding_world(tmp_path, channels=("USO", "TLT"))
    put_reference(w.store, "AMD", "CS")
    put_bars(w.store, "AMD", closes_from(random_returns(np.random.default_rng(9), 0.02)))
    tools = Tools(Harness(w.store, w.venues, Web(key="test")))

    def call(name, **input):
        ok, out = tools.call(name, input)
        assert ok, out
        return out

    tlt = quoted(w, "TLT", 4.2, 17.0)
    spy = channel(w, "SPY")
    e = event(w, p=0.3)
    first, second = w.prop("a wide fall"), w.prop("a narrow fall")
    w.link(e, "way", first, side="yes")
    w.link(e, "way", second, side="yes")
    w.chances(first, {"given": [e], "value": 0.5})
    got = call("link", from_id=e, relation="consequence", to_id=spy, fields={"shock": stated(-0.1)})
    assert set(got["events"][0]["levels"]) == {spy}
    call("link", from_id=first, relation="consequence", to_id=spy, fields={"shock": stated(-0.2)})
    call("link", from_id=e, relation="consequence", to_id=tlt, fields={"shock": stated(0.5)})
    call("link", from_id=e, relation="consequence", to_id="name:NVDA", fields={"shock": stated(-0.2)})
    call("link", from_id=e, relation="consequence", to_id="name:FUND", fields={"shock": stated(-0.1)})
    before = output(w, e)["by_holding"]
    now = call("link", from_id=first, relation="consequence", to_id="name:AMD", fields={"shock": stated(-0.3)})["events"][0]
    levels = now["levels"]
    assert set(levels) == {spy, tlt, "name:NVDA", "name:FUND", "name:AMD"}

    def today(ticker):
        return w.store.get(f"metric:price:{ticker}")["derived"]["level"]["value"]

    def at(target, ratios):
        assert levels[target]["today"] == pytest.approx(levels[target]["today"])
        assert levels[target]["worlds"] == {k: pytest.approx(levels[target]["today"] * r, abs=1e-5)
                                            for k, r in ratios.items()}

    assert levels[spy]["today"] == pytest.approx(today("SPY"))
    at(spy, {first: 0.8 / 0.955, second: 0.9 / 0.955, "no": 1 / 0.955})
    base = 1 / (0.3 * math.exp(-0.085) + 0.7)
    m = {first: math.exp(-0.085) * base - 1, second: math.exp(-0.085) * base - 1, "no": base - 1}
    at(tlt, {k: 1 + v for k, v in m.items()})
    assert levels[tlt]["yield"] == 4.2 and levels[tlt]["yields"] == {
        k: pytest.approx(4.2 - 100 * math.log1p(v) / 17, abs=1e-5) for k, v in m.items()}
    assert levels[tlt]["yields"][first] - levels[tlt]["yields"]["no"] == pytest.approx(0.5, abs=1e-5)
    assert levels["name:NVDA"]["today"] == pytest.approx(today("NVDA"))
    at("name:NVDA", {first: 0.8 / 0.94, second: 0.8 / 0.94, "no": 1 / 0.94})
    at("name:FUND", {first: 0.9 / 0.97, second: 0.9 / 0.97, "no": 1 / 0.97})
    out = output(w, e)
    b = w.store.engine.value("betas", "portfolio").output["objects"]["name:AMD"]["fields"]["betas"]
    z = components(w, {c: math.log1p(v) for c, v in out["against_baseline"][second].items()})
    r = math.exp(sum(b.get(c, 0.0) * z[c] for c in z))  # AMD's response in the second way, against the baseline
    amd = 1 / (0.15 * 0.7 + 0.15 * r + 0.7)
    at("name:AMD", {first: 0.7 * amd, second: r * amd, "no": amd})
    names = {n["name"]: n for n in now["names"]}
    assert names["name:AMD"]["worlds"][second]["by"] == "moves with the stated ones"
    amd_first = names["name:AMD"]["worlds"][first]
    assert {k: v for k, v in amd_first.items() if k != "against_today"} == {
        "move": pytest.approx(-0.3), "by": "own", "on": first,
        "consequence": out["setting"][first]["name:AMD"]["link"], "stated": {"value": -0.3, "against": schema.BASELINE}}
    assert out["by_holding"] == before  # a company nothing holds moves no holding
    held = {h["name"]: h for h in out["by_holding"]}
    assert held["name:FUND"]["worlds"] == {k: pytest.approx(4000.0 * (r_ - 1)) for k, r_ in
                                           {first: 0.9 / 0.97, second: 0.9 / 0.97, "no": 1 / 0.97}.items()}


def test_a_shock_on_a_name_its_sources_hold_no_price_for_is_refused(tmp_path):
    """A shock on a price is placed from that price, and a name's own part is its move beyond its response, measured on
    its history (MATH.md 1.4, 1.5): a name whose source answers that it has no prices takes no shock, and the call writes
    nothing."""
    from conftest import snapshot
    from harness.derive.series import lookback
    from massive_data import put_dividends
    w = holding_world(tmp_path)
    w.store.put_data("bars", "ZZZZ", {"status": "NOT_FOUND", "message": "no such ticker"},
                     {"url": "https://api.massive.com/v2/aggs/ticker/ZZZZ"}, retrieved_at="2026-11-04T21:00:00+00:00",
                     range=(lookback("2026-11-04"), "2026-11-04"))
    put_dividends(w.store, "ZZZZ", {})
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    before = snapshot(w.store)
    with pytest.raises(StoreError, match="name:ZZZZ has no price"):
        w.link(e, "consequence", "name:ZZZZ", shock=-0.2)
    assert snapshot(w.store) == before


def test_removing_a_factor_a_shock_runs_to_removes_its_influence(tmp_path):
    """An event states the market -10% and oil +20%; another states the market -10% alone. Removing oil, the factor,
    takes its shock with it (2.3), and the first event's figures are then the second's (removing something
    removes its influence)."""
    w = world(tmp_path, g={"SPY": 50_000.0, "USO": 10_000.0}, coefficients={"SPY": {}, "USO": {"SPY": 0.5}},
              variances={"SPY": 1e-4, "USO": 3e-4})
    both, alone = event(w, "both", p=0.3), event(w, "alone", p=0.3)
    for e in (both, alone):
        w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    w.link(both, "consequence", channel(w, "USO"), shock=0.2)
    assert output(w, both)["full_impact"] != pytest.approx(output(w, alone)["full_impact"])
    w.store.remove(channel(w, "USO"))
    assert w.store.links_of(both, "consequence") and all(c["to"] == channel(w, "SPY")
                                                         for c in w.store.links_of(both, "consequence"))
    for field in ("full_impact", "yes", "no", "volatility"):
        assert output(w, both)[field] == pytest.approx(output(w, alone)[field])


def test_a_shock_on_a_factors_proxy_is_stated_on_the_factor(tmp_path):
    """MATH.md 1.4: the price of a factor's proxy is the factor's own (3.2), so a shock on it is stated on the factor,
    and so is what it is in a world: a consequence or a value on that name is refused, naming the factor, and a factor
    whose proxy a shock or a value is already stated on is refused, naming them, so one price has one value."""
    w = world(tmp_path, g={"SPY": 10_000.0})
    spy = channel(w, "SPY")
    e = event(w)
    with pytest.raises(StoreError, match=rf"^name:SPY is the price of {spy}, S&P 500, whose proxy it is: a shock on "
                                         rf"that price is stated on {spy}$"):
        w.store.link(e, "consequence", "name:SPY", {"shock": stated(-0.2)})
    with pytest.raises(StoreError, match=rf"name:SPY is the price of {spy}, S&P 500, whose proxy it is: what that "
                                         rf"price is in a world is stated on {spy}"):
        w.store.link(e, "worth", "name:SPY", {"world": "yes", "value": {"value": {"amount": 500}, "note": WHY}})
    lid = w.store.link(e, "consequence", "name:XLE", {"shock": stated(-0.1)})["id"]
    with pytest.raises(StoreError, match=rf"{lid} states what name:XLE's price does in a world; as a factor's proxy its "
                                         r"price is the factor's own, so what it does is stated on the factor"):
        w.store.add("Factor", {"name": "energy", "proxy": "XLE", "order": 99})
