"""The past windows a shock is sized from (MATH.md 1.3, ONTOLOGY.md 2, 2.2, MATH.md 3.2, TOOLS.md 5): part of its basis, beside its note or its page,
each the window an episode names. The store measures the price over each as window_returns gives it, in the terms the
shock is stated in: both figures, with the sessions it ran between and its Computation, which the page shows beside the
shock (engine.value("shock_windows", link)). It is a field of no link, so neither get nor a statement's return carries
it, and the analyst reads the same figures with window_returns. The shock stands as stated. A window that ends after
the as-of, or over which the price has no move, is refused with why, and nothing is written."""
from __future__ import annotations

import pytest

from harness import schema
from harness.derive.impact import FOLLOWED, MOVE
from harness.ledger import event_now
from harness.store import StoreError
from harness.tools import Tools
from harness.web import Web
from harness.world import World as Harness
from massive_data import SESSIONS, put_bars
from test_impact import channel, event, quoted, world
from test_name_window import END, START, closes, funds  # noqa: F401  (funds, a fixture)
from massive_data import put_reference
from test_value_and_exposure import DAYS

W1 = {"start": SESSIONS[-60], "end": SESSIONS[-40]}
W2 = {"start": SESSIONS[-120], "end": SESSIONS[-100]}


def tools_of(w) -> Tools:
    return Tools(Harness(w.store, w.venues, Web(key="test")))


def returns(w, window: dict) -> dict:
    """Each factor over the window as the analyst reads it, by its id."""
    ok, got = tools_of(w).call("window_returns", window)
    assert ok, got
    return {f["factor"]: f for f in got["factors"]}


def shock(value: float, windows: list[dict], against: str | None = None) -> dict:
    return {"shock": {"value": value, "windows": windows, "note": "Sized from the windows it names.",
                      **({"against": against} if against else {})}}


def windows_of(store, lid: str) -> list[dict]:
    """What the price did over each window the link's shock is sized from, as the page reads it."""
    got = store.engine.value("shock_windows", lid)
    assert got.output is not None, got.cause
    return got.output["windows"]


def keys(x) -> set[str]:
    """Every key at any depth."""
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in keys(v)}
    return {k for v in x for k in keys(v)} if isinstance(x, list) else set()


@pytest.fixture
def four(tmp_path):
    """The market, oil, long Treasuries quoted as a yield of 4.2 at a duration of 17, and gold, each held."""
    w = world(tmp_path, g={"SPY": 10_000.0, "USO": 5_000.0, "TLT": 5_000.0, "GLD": 3_000.0},
              coefficients={"TLT": {"SPY": -0.2}, "USO": {"SPY": 0.6, "TLT": 0.1}, "GLD": {"SPY": 0.1, "USO": 0.3}},
              levels={"TLT": 90.0})
    quoted(w, "TLT", 4.2, 17.0)
    yield w
    w.store.close()


def test_each_window_reads_as_window_returns_gives_it_in_the_terms_the_shock_is_stated_in(four):
    """The market's whole move sized from two windows, oil's residual from one, long Treasuries' yield from one: each
    window reads with both figures as window_returns gives them, in points of the yield for Treasuries, the two sessions
    it ran between and the Computation. Neither the statement's return, nor get of the link, nor the event as it now
    stands carries it. Each shock stands as stated."""
    w = four
    spy, uso, tlt = (channel(w, t) for t in ("SPY", "USO", "TLT"))
    e = event(w, p=0.3)
    ok, made = tools_of(w).call("link", {"from_id": e, "relation": "consequence", "to_id": spy,
                                         "fields": shock(-0.12, [W1, W2])})
    assert ok, made
    market = made["id"]
    assert "windows" not in made["derived"] and "windows" not in keys(made["events"])  # the page's
    assert made["stated"]["shock"]["windows"] == [W1, W2]  # as stated
    oil = w.store.link(e, "consequence", uso, shock(0.05, [W1], schema.FOLLOWS))["id"]
    bonds = w.store.link(e, "consequence", tlt, shock(-0.4, [W2]))["id"]
    one, two = returns(w, W1), returns(w, W2)
    for lid, c, cited in ((market, spy, (W1, W2)), (oil, uso, (W1,)), (bonds, tlt, (W2,))):
        got = windows_of(w.store, lid)
        assert [(x["start"], x["end"]) for x in got] == [(x["start"], x["end"]) for x in cited]
        for x, window in zip(got, cited):
            read = (one if window is W1 else two)[c]
            assert x[MOVE] == pytest.approx(read[MOVE], rel=1e-12)
            assert x[FOLLOWED] == pytest.approx(read[FOLLOWED], rel=1e-12)
            assert x["sessions"] == read["sessions"] == [window["start"], window["end"]]
            assert x["basis"].startswith("cmp:")
    assert w.store.get(bonds)["stated"]["shock"]["windows"] == [W2]
    ok, read = tools_of(w).call("get", {"id": market})
    assert ok and "windows" not in read["derived"] and read["stated"]["shock"]["windows"] == [W1, W2]
    ok, edited = tools_of(w).call("edit", {"id": oil, "field": "shock", "value": shock(0.06, [W1, W2], schema.FOLLOWS)["shock"]})
    assert ok and "windows" not in edited["derived"] and len(windows_of(w.store, oil)) == 2
    assert "windows" not in keys(event_now(w.store, e))


def test_a_window_the_price_has_no_move_over_is_refused_and_a_residual_not_measured_is_null(four):
    """Oil's proxy begins after the window's start, so oil has no move over it: a shock on oil sized from that window is
    refused with why, as an episode over it is, and nothing is written. Gold's residual is taken against oil's, so it
    is not measured either, while gold's whole move is: a shock on gold's residual from that window stands, its window
    giving the whole move and null under the residual's own key. A window that ends after the as-of is
    refused by its dates."""
    w = four
    uso, gld = channel(w, "USO"), channel(w, "GLD")
    put_bars(w.store, "USO", {d: c for d, c in closes(w.store, "USO").items() if d >= SESSIONS[-50]})  # begins in W1
    e = event(w, p=0.3)
    before = len(w.store.links_of(e, "consequence"))
    with pytest.raises(StoreError, match=rf"^link:\d+\.shock\.windows\[0\]: USO has no close on or before "
                                         rf"{W1['start']}; its first close after it is {SESSIONS[-50]}$"):
        w.store.link(e, "consequence", uso, shock(0.05, [W1]))
    asof = w.store.meta("asof")
    with pytest.raises(StoreError, match=rf"^link:\d+\.shock\.windows\[1\]: a past window ends on or before the as-of, "
                                         rf"{asof}, and this one ends 2099-01-02$"):
        w.store.link(e, "consequence", gld, shock(0.05, [W2, {"start": SESSIONS[-5], "end": "2099-01-02"}]))
    assert len(w.store.links_of(e, "consequence")) == before
    gold = w.store.link(e, "consequence", gld, shock(0.04, [W1], schema.FOLLOWS))["id"]
    (got,) = windows_of(w.store, gold)
    assert got[MOVE] == pytest.approx(returns(w, W1)[gld][MOVE], rel=1e-12) and got[FOLLOWED] is None


def test_a_shocks_windows_are_one_or_more_each_given_once_and_go_with_a_restatement_that_leaves_them_out(four):
    """A list of one or more windows, each two dates with the start first and given once; `window` is not a field of
    a shock, and the refusal names the fields it holds. A shock restated without its windows has none, and its link
    measures none."""
    w = four
    spy = channel(w, "SPY")
    e = event(w, p=0.3)
    for windows, said in (([], r"shock.windows is a list of one or more past windows"),
                          ([W1, W2, dict(W1)], r"shock.windows\[0\] and shock.windows\[2\] are one window"),
                          ([{"start": W1["end"], "end": W1["start"]}],
                           rf"shock.windows\[0\]: a window starts before its end, and this one starts {W1['end']} and "
                           rf"ends {W1['start']}$"),
                          ([W1, {"start": W2["end"], "end": W2["end"]}],  # a window of no length
                           rf"shock.windows\[1\]: a window starts before its end, and this one starts {W2['end']} and "
                           rf"ends {W2['end']}$")):
        with pytest.raises(StoreError, match=said):
            w.store.link(e, "consequence", spy, shock(-0.1, windows))
    with pytest.raises(StoreError, match=r"with `against` and `windows` where it states them; not window$"):
        w.store.link(e, "consequence", spy, {"shock": {"value": -0.1, "window": W1, "note": "one window"}})
    lid = w.store.link(e, "consequence", spy, shock(-0.1, [W1]))["id"]
    assert windows_of(w.store, lid)[0]["start"] == W1["start"]
    w.store.edit(lid, "shock", {"value": -0.1, "note": "Restated on its reasoning alone."})
    assert "windows" not in w.store.get(lid)["stated"]["shock"]
    assert w.store.engine.value("shock_windows", lid).output is None  # it measures none


def test_a_shock_on_a_name_reads_its_window_as_window_returns_reads_the_name(funds):
    """A residual on a company held through a fund, sized from a window: it reads the name's move and its
    residual against its response at the betas fitted up to the window's start, as window_returns gives them, with no
    event watched, since a window reads none. A shock on a name listed after the window began is refused, saying so."""
    e = funds.add("Proposition", {"title": "E", "text": "E happens", "comparison": "occurs", "kind": "hitting",
                                  "window_end": "2027-03-31", "baseline": "no"})["id"]
    window = {"start": START, "end": END}
    a = funds.link(e, "consequence", "name:A", shock(-0.1, [window], schema.FOLLOWS))["id"]
    ok, read = Tools(Harness(funds, None)).call("window_returns", {**window, "names": ["name:A"]})
    assert ok, read
    (name,) = read["names"]
    (got,) = windows_of(funds, a)
    assert got == {**window, "sessions": name["sessions"], MOVE: pytest.approx(name[MOVE], rel=1e-12),
                   FOLLOWED: pytest.approx(name[FOLLOWED], rel=1e-12), "basis": got["basis"]}
    put_reference(funds, "NEW", "CS")
    put_bars(funds, "NEW", {d: 50.0 for d in DAYS[-30:]})  # listed within the window
    with pytest.raises(StoreError, match=rf"^link:\d+\.shock\.windows\[0\]: NEW has no close on or before {START}; its "
                                         rf"first close after it is {DAYS[-30]}$"):
        funds.link(e, "consequence", "name:NEW", shock(-0.1, [window]))


def test_a_proposition_stated_whole_carries_a_shocks_windows_through_the_same_check(tmp_path):
    """A proposition stated whole in one call (TOOLS.md 5): a consequence inside it whose window ends after the as-of is
    refused by the same rule, named by its place in `fields`, and nothing is written."""
    from test_whole_statement import refused, section_10
    w = section_10(tmp_path)
    spy = channel(w, "SPY")
    late = {"start": SESSIONS[-5], "end": "2099-01-02"}
    out = refused(w, "add", type="Proposition", fields={
        **w.prop("T"), "watches": [{"from": "portfolio"}], "baseline": "no",
        "consequence": [{"to": spy, "shock": shock(-0.1, [W1, late])["shock"]}]})
    assert out["error"] == (f"fields.consequence[0].shock.windows[1]: a past window ends on or before the as-of, "
                            f"{w.store.meta('asof')}, and this one ends 2099-01-02. Nothing was written.")
    assert out["dependents"] == ["fields.consequence[0]"]

