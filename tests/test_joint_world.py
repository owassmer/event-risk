"""A joint world, named by its outcomes and stated (MATH.md 1.9, ONTOLOGY.md 2): what a statement on one leaves standing, (d); that it is
1.3 wherever 1.3 decides; that a statement agreeing with the rows changes nothing; the episode its worlds name; one
outcome, one name; and what check() lists of it."""

from __future__ import annotations

import math
import re

import numpy as np
import pytest

from breakdown import stated
from harness import schema
from harness.derive import worlds
from harness.derive.outcomes import named_by
from harness.derive.together import read
from harness.ledger import check
from harness.store import StoreError
from test_impact import channel, event, world
from test_worth import holding

FIXTURE = dict(coefficients={"SPY": {}, "USO": {"SPY": 0.5}, "GLD": {"SPY": 0.2}},
               variances={"SPY": 0.04, "USO": 0.09, "GLD": 0.0225})


def covariance(w, e):
    """The factors and their covariance as the store holds them, L diag(var) L' from the manifest's coefficients and
    variances (MATH.md 1.3, 3.2)."""
    m = w.store.get(w.store.get(e)["derived"]["full_impact"]["basis"])["inputs"]
    ids = m["channels"]
    lower = np.eye(len(ids))
    for i, c in enumerate(ids):
        for j, beta in m["coefficients"][c].items():
            lower[i, ids.index(j)] = beta
    return ids, lower @ np.diag([m["variances"][c] for c in ids]) @ lower.T


def fill(ids, cov, given: dict[str, float]) -> dict[str, float]:
    """MATH.md 1.3 by hand: every factor not given moves as it ordinarily would with those given, ln(1 + x_U) =
    cov(U, S) cov(S, S)^-1 ln(1 + x_S)."""
    s = [ids.index(c) for c in given]
    u = [i for i in range(len(ids)) if i not in s]
    x = np.zeros(len(ids))
    x[s] = [given[c] for c in given]
    x[u] = cov[np.ix_(u, s)] @ np.linalg.solve(cov[np.ix_(s, s)], x[s])
    return dict(zip(ids, x))


def two_events(tmp_path):
    """A at 0.30 states gold +5% and a company's residual -15% (its beta 1.5 to the market); B at 0.40 states the market
    -10%; nothing is stated between them, so they are independent and the rows compound."""
    w = world(tmp_path, **FIXTURE)
    holding(w, "COMPANY", 100, 10_000)
    w.store.fixture["betas"] = {"COMPANY": {"SPY": 1.5}}
    a, b = event(w, "A", .3), event(w, "B", .4)
    spy, uso, gld = channel(w, "SPY"), channel(w, "USO"), channel(w, "GLD")
    w.link(a, "consequence", gld, shock=0.05)
    w.store.link(a, "consequence", "name:COMPANY", {"shock": stated({"value": -0.15, "against": schema.FOLLOWS})})
    w.link(b, "consequence", spy, shock=-0.1)
    return w, a, b, spy, uso, gld


def by_outcomes(view, a, b):
    return {(s["outcomes"][a], s["outcomes"][b]): s for s in view["worlds"]}


def test_a_stated_joint_world_keeps_what_the_rows_state_and_moves_the_rest_with_what_it_changes(tmp_path):
    """(d), by hand. The rows put the joint world where both happen at A's moves compounded with B's. Stating the market
    there at -20%: the market takes the statement; gold, which A's row states, keeps where the rows put it; oil, which
    no row states, moves from where the rows put it as it ordinarily would with the market's change, gold held; and the
    company keeps the residual A's row states, moving with the market at its beta from where the rows put it. Every
    other joint world stands where the rows put it, and each price is placed once at the joint weights (1.5)."""
    w, a, b, spy, uso, gld = two_events(tmp_path)
    ids, cov = covariance(w, a)
    xa, xb = fill(ids, cov, {gld: math.log(1.05)}), fill(ids, cov, {spy: math.log(0.9)})
    rows = {c: xa[c] + xb[c] for c in ids}
    moved = fill(ids, cov, {spy: math.log(0.8) - rows[spy], gld: 0.0})
    w.link(f"joint:{a}=yes|{b}=yes", "consequence", spy, shock=-0.2)
    view = by_outcomes(read(w.store, [a, b]), a, b)
    both = view["yes", "yes"]
    assert both["factors"][spy] == pytest.approx(-0.2)
    assert both["factors"][gld] == pytest.approx(math.expm1(rows[gld]))
    assert both["factors"][uso] == pytest.approx(math.expm1(rows[uso] + moved[uso]))
    assert view["yes", "no"]["factors"][uso] == pytest.approx(math.expm1(xa[uso]))  # the rows', as they stand
    assert both["set"][spy]["on"] == f"joint:{a}=yes|{b}=yes" and uso not in both["set"]  # oil moves with them
    whole, residual = {"rows": [a], "against": "baseline"}, {"rows": [a], "against": "what_it_follows"}
    assert both["set"][gld] == whole and both["set"]["name:COMPANY"] == residual  # what each row's shock is against
    assert view["no", "no"]["set"] == "nothing" and view["yes", "no"]["set"] == {gld: whole, "name:COMPANY": residual}
    level = {("yes", "yes"): 0.85 * 0.8 ** 1.5, ("yes", "no"): 0.85 * math.exp(1.5 * xa[spy]),
             ("no", "yes"): 0.9 ** 1.5, ("no", "no"): 1.0}
    q = {("yes", "yes"): .12, ("yes", "no"): .18, ("no", "yes"): .28, ("no", "no"): .42}
    scale = sum(q[k] * level[k] for k in q)
    assert {k: s["A"] for k, s in view.items()} == pytest.approx({k: 10_000 * (level[k] / scale - 1) for k in q})


def test_a_statement_that_agrees_with_the_rows_changes_nothing(tmp_path):
    """Restating the market on the joint world exactly where the rows put it: every factor and every dollar of every
    joint world reads as the rows alone give them."""
    w, a, b, spy, uso, gld = two_events(tmp_path)
    alone = by_outcomes(read(w.store, [a, b]), a, b)
    w.link(f"joint:{a}=yes|{b}=yes", "consequence", spy, shock=(1 + alone["yes", "yes"]["factors"][spy]) - 1)
    again = by_outcomes(read(w.store, [a, b]), a, b)
    assert "link" in again["yes", "yes"]["set"][spy]
    for k, s in alone.items():
        assert again[k]["factors"] == pytest.approx(s["factors"], abs=1e-12)
        assert again[k]["A"] == pytest.approx(s["A"], abs=1e-8)


def test_a_statement_that_changes_no_factor_asks_for_no_episode(tmp_path):
    """Restating a whole move at the value the rows already give it changes no factor, so nothing moves with it and no
    episode is asked for, even where the worlds the joint world lies in name different past episodes: the events read
    together read every factor and every dollar as the rows alone give them, where before the statement made them
    wait (MATH.md 1.9). A statement that does change the factor there waits on one episode, as before."""
    from massive_data import SESSIONS
    w, a, b, spy, uso, gld = two_events(tmp_path)
    for e, (start, end) in ((a, (SESSIONS[-80], SESSIONS[-30])), (b, (SESSIONS[-120], SESSIONS[-60]))):
        w.store.edit(e, "episode", {"value": {"start": start, "end": end}, "note": "how it moved then"})
    alone = by_outcomes(read(w.store, [a, b]), a, b)
    joint = f"joint:{a}=yes|{b}=yes"
    lid = w.link(joint, "consequence", spy, shock=alone["yes", "yes"]["factors"][spy])
    view = read(w.store, [a, b])
    assert "why" not in view, view.get("why")
    again = by_outcomes(view, a, b)
    assert again["yes", "yes"]["set"][spy] == {"link": lid, "on": joint}
    for k, s in alone.items():
        assert again[k]["factors"] == pytest.approx(s["factors"], abs=1e-12)
        assert again[k]["A"] == pytest.approx(s["A"], abs=1e-8)
    w.store.edit(lid, "shock", stated(alone["yes", "yes"]["factors"][spy] - 0.05))
    assert "different episodes" in read(w.store, [a, b])["why"]


def test_where_1_3_decides_a_joint_world_is_1_3(tmp_path):
    """A joint world in which B is at its baseline lies in A's world alone, so a statement on it is one on that world of
    A, and 1.3 decides what it leaves standing (a way takes what is stated above it, and its unstated factors fill from
    every condition together). A states gold +5% on the event, which reaches both its ways; the joint world of its first
    way with B at its baseline states the market at -4% and oil's residual at +3%. It stands as that way does where
    the same is stated on the way itself: every factor to the float."""
    from test_ways import way
    factors = {}
    for on_way in (False, True):
        w = world(tmp_path / str(on_way), **FIXTURE)
        a, b = event(w, "A", .4), event(w, "B", .3)
        first, second = way(w, a, "first", side="yes", weight=0.6), way(w, a, "second", side="yes")
        spy, uso, gld = channel(w, "SPY"), channel(w, "USO"), channel(w, "GLD")
        w.link(a, "consequence", gld, shock=0.05)
        w.link(second, "consequence", spy, shock=-0.1)
        w.link(b, "consequence", spy, shock=0.02)
        at = first if on_way else f"joint:{a}={first}|{b}=no"
        w.link(at, "consequence", spy, shock=-0.04)
        w.store.link(at, "consequence", uso, {"shock": stated({"value": 0.03, "against": schema.FOLLOWS})})
        if on_way:
            factors[on_way] = w.store.engine.value("impact", a).output["against_baseline"][first]
        else:
            factors[on_way] = by_outcomes(read(w.store, [a, b]), a, b)[first, "no"]["factors"]
    assert factors[False] == pytest.approx(factors[True], abs=1e-12)


def test_the_co_movement_a_joint_world_moves_with_is_the_episode_its_worlds_name(tmp_path):
    """Where one of the worlds a joint world lies in names a past episode, what it changes moves the other factors with
    that episode's co-movement, as in that world (1.3). Where two name different episodes, its figures wait, saying
    which, and check() lists it, until the joint world names its own."""
    from massive_data import SESSIONS
    from harness.derive.episode import subject
    w, a, b, spy, uso, gld = two_events(tmp_path)
    first, second = (SESSIONS[-80], SESSIONS[-30]), (SESSIONS[-120], SESSIONS[-60])
    w.store.edit(a, "episode", {"value": {"start": first[0], "end": first[1]}, "note": "how it moved then"})
    joint = f"joint:{a}=yes|{b}=yes"
    w.link(joint, "consequence", spy, shock=-0.2)
    alone = by_outcomes(read(w.store, [a, b]), a, b)["yes", "yes"]
    ids, cov = covariance(w, a)
    held = np.array(w.store.engine.value("episode_covariance", subject(*first)).output["covariance"])
    xa, xb = fill(ids, held, {gld: math.log(1.05)}), fill(ids, cov, {spy: math.log(0.9)})  # A's world, its episode
    rows = {c: xa[c] + xb[c] for c in ids}
    moved = fill(ids, held, {spy: math.log(0.8) - rows[spy], gld: 0.0})
    assert alone["factors"][uso] == pytest.approx(math.expm1(rows[uso] + moved[uso]))
    assert alone["factors"][uso] != pytest.approx(math.expm1(rows[uso] + fill(ids, cov, {spy: math.log(0.8) - rows[spy],
                                                                                         gld: 0.0})[uso]))
    w.store.edit(b, "episode", {"value": {"start": second[0], "end": second[1]}, "note": "how it moved then"})
    view = read(w.store, [a, b])
    assert "different episodes" in view["why"] and f"state it as {joint}'s episode" in view["why"]
    assert [x["id"] for x in check(w.store)["joint_episodes_differ"]] == [joint]
    w.store.edit(joint, "episode", {"value": {"start": first[0], "end": first[1]}, "note": "this one holds"})
    assert "why" not in read(w.store, [a, b]) and check(w.store)["joint_episodes_differ"] == []
    other = np.array(w.store.engine.value("episode_covariance", subject(*second)).output["covariance"])
    xb2 = fill(ids, other, {spy: math.log(0.9)})  # B's world now fills with its own episode
    rows2 = {c: xa[c] + xb2[c] for c in ids}
    moved2 = fill(ids, held, {spy: math.log(0.8) - rows2[spy], gld: 0.0})  # and the joint world with the one it names
    got = by_outcomes(read(w.store, [a, b]), a, b)["yes", "yes"]["factors"]
    assert got[uso] == pytest.approx(math.expm1(rows2[uso] + moved2[uso]))
    assert got[gld] == pytest.approx(math.expm1(rows2[gld]))


def test_a_band_is_an_outcome_and_the_narrowest_statement_stands(tmp_path):
    """A joint world is named by outcomes, a band among them, with nothing yet stated of it (the research session's row
    15, accepted and ignored before): naming it makes it a world, and the statement applies. A statement on a side holds
    every joint world beneath it; where one on a joint world within it states the same price, that one stands."""
    from test_ladder import band, priced, rung
    w = world(tmp_path, **FIXTURE)
    r25, r3 = priced(w, rung(w, "above 2.5", ">", 2.5), 0.80), priced(w, rung(w, "above 3", ">", 3.0), 0.45)
    r35 = priced(w, rung(w, "above 3.5", ">", 3.5), 0.12)
    b2 = band(w, "(3, 3.5]", (">", 3.0), ("<=", 3.5))
    w.link("portfolio", "watches", r3)
    w.store.edit(r3, "baseline", "no")
    other = event(w, "Other", .3)
    spy, uso = channel(w, "SPY"), channel(w, "USO")
    w.link(r3, "consequence", spy, shock=-0.05)
    w.link(other, "consequence", spy, shock=0.01)
    with pytest.raises(StoreError) as refused:  # its Yes side's bands, named by nothing yet, are outcomes all the same
        w.link(f"joint:{r3}=maybe|{other}=yes", "consequence", uso, shock=0.10)
    assert str(refused.value) == f"maybe is not an outcome of {r3}. Its outcomes are: yes, no, {b2}, {r35}"
    w.link(f"joint:{r3}=yes|{other}=yes", "consequence", uso, shock=0.10)
    w.link(f"joint:{r3}={b2}|{other}=yes", "consequence", uso, shock=0.20)
    view = read(w.store, [r3, other])
    assert "why" not in view
    got = {(s["outcomes"][r3], s["outcomes"][other]): s for s in view["worlds"]}
    assert set(got) == {(b2, "yes"), (r35, "yes"), (b2, "no"), (r35, "no"), ("no", "yes"), ("no", "no")}
    assert got[b2, "yes"]["factors"][uso] == pytest.approx(0.20)  # the narrower statement stands
    assert got[r35, "yes"]["factors"][uso] == pytest.approx(0.10)  # the side's, beneath it


def test_one_outcome_has_one_name_and_check_lists_a_name_that_no_longer_holds(tmp_path):
    """A joint world is named by one outcome of each of two events or more the portfolio watches, kept from its first
    statement under its events in the order they were made; a name that names none is refused, saying why. A joint
    world holding the one in which each event it names is at its baseline takes no shock. When its tree changes so that
    its name no longer holds, the statement stands and check() lists it; a shock on a composite standing from before is
    listed with the name it is stated on."""
    from test_ways import way
    w = world(tmp_path, **FIXTURE)
    a, b = event(w, "A", .3), event(w, "B", .4)
    first = way(w, a, "first", side="yes", weight=0.5)
    way(w, a, "second", side="yes")
    spy = channel(w, "SPY")
    for jid, why in ((f"joint:{a}=yes", f"joint:{a}=yes names one event's world; it is {a} yes"),
                     (f"joint:{a}=yes|{a}=no", "names an event twice"),
                     (f"joint:{a}=yes|prop:99=yes", "prop:99, which is not an event the portfolio watches"),
                     (f"joint:{a}=maybe|{b}=yes", f"maybe is not an outcome of {a}. Its outcomes are: yes, no, {first}"),
                     (f"joint:{a}=no|{b}=no", f"A shock cannot be stated on joint:{a}=no|{b}=no: it includes the joint world the "
                                              "shock is measured against, where each of its events is at its baseline. "
                                              "State it on a joint world that excludes that one.")):
        with pytest.raises(StoreError, match=re.escape(why)):
            w.link(jid, "consequence", spy, shock=-0.1)
    lid = w.link(f"joint:{b}=yes|{a}={first}", "consequence", spy, shock=-0.1)
    joint = f"joint:{a}={first}|{b}=yes"
    assert w.store.get(lid)["from"] == joint and w.store.get(joint)["type"] == "JointWorld"
    assert check(w.store)["joint_names_no_outcome"] == []
    w.store.remove(w.store.links_of(first, "way", "in")[0]["id"])  # the first way is no outcome of A now
    assert [x["id"] for x in check(w.store)["joint_names_no_outcome"]] == [joint]
    w.store.remove(lid)  # its last statement goes, and the joint world with it
    assert w.store.type_of(joint) is None
    both = w.prop("both")
    w.link(both, "and", a)
    w.link(both, "and", b)
    with w.store.transaction():  # a shock on a composite, as a store made before a joint world had its name holds it
        old = w.store._insert_link("consequence", both, spy, {"shock": stated({"value": -0.1})}, "analyst", None)
    assert check(w.store)["stated_on_composite"] == [{"link": old, "on": both, "joint": f"joint:{a}=yes|{b}=yes"}]


def test_a_store_of_schema_14_is_brought_to_keep_joint_worlds_with_no_row_changed(tmp_path):
    """A store made before a joint world was kept declares its object table with the five types it held. Opened, it is
    brought to schema 15 once: the declaration takes JointWorld, edited in place, every row as it was; a second
    connection to the file then keeps one too."""
    import json
    import sqlite3
    from harness.store import JOINT_SCHEMA, SCHEMA_VERSION, Store
    w = world(tmp_path, **FIXTURE)
    a, b = event(w, "A", .3), event(w, "B", .4)
    path, rows = w.store.dir, w.store._all("SELECT id, type, stated FROM object ORDER BY rowid")
    w.store.close()
    db = sqlite3.connect(path / "store.sqlite", isolation_level=None)
    sql = db.execute("SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'object'").fetchone()[0]
    version = db.execute("PRAGMA schema_version").fetchone()[0]
    db.execute("BEGIN")
    db.execute("PRAGMA writable_schema = ON")
    db.execute("UPDATE sqlite_schema SET sql = ? WHERE type = 'table' AND name = 'object'",
               (sql.replace(", 'JointWorld'", ""),))
    db.execute(f"PRAGMA schema_version = {version + 1}")
    db.execute("PRAGMA writable_schema = OFF")
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (JOINT_SCHEMA,))
    db.execute("COMMIT")
    db.close()
    store = Store.open(path, registry=w.store.engine.registry, judge=w.judge, venues=w.venues)
    assert store.meta("schema_version") == SCHEMA_VERSION
    assert json.loads(store.meta("schema_converted"))["from"] == JOINT_SCHEMA
    assert [dict(r) for r in store._all("SELECT id, type, stated FROM object ORDER BY rowid")] == [dict(r) for r in rows]
    other = sqlite3.connect(path / "store.sqlite")
    other.execute("INSERT INTO object (id, type, stated, stated_at, origin, created_call, created_at) VALUES "
                  "(?, 'JointWorld', '{}', '{}', 'analyst', NULL, '2026-11-04T00:00:00+00:00')", (f"joint:{a}=yes|{b}=yes",))
    other.commit()
    other.close()
    store.close()


def test_where_two_statements_overlap_and_neither_lies_within_the_other_the_joint_world_waits(tmp_path):
    """The statement nearest above a joint world stands (1.3): one lying within the other. A joint world of A and B and
    one of A and C each state oil, and where all three happen neither lies within the other, whatever the number of
    joint worlds each holds: there the view waits, naming the joint world where both hold, and check() lists the two.
    Stated there, it stands where both hold, and each of the two stands in the rest of what it holds."""
    from test_ways import way
    w = world(tmp_path, **FIXTURE)
    a, b, c = event(w, "A", .3), event(w, "B", .4), event(w, "C", .5)
    first, second = way(w, c, "first", side="yes", weight=0.6), way(w, c, "second", side="yes")
    spy, uso = channel(w, "SPY"), channel(w, "USO")
    w.link(a, "consequence", spy, shock=-0.05)
    w.link(b, "consequence", spy, shock=0.02)
    w.link(c, "consequence", spy, shock=-0.03)
    ab, ac, all3 = f"joint:{a}=yes|{b}=yes", f"joint:{a}=yes|{c}=yes", f"joint:{a}=yes|{b}=yes|{c}=yes"
    w.link(ab, "consequence", uso, shock=0.10)
    w.link(ac, "consequence", uso, shock=0.05)
    assert "why" not in read(w.store, [a, b]) and "why" not in read(w.store, [a, c])  # each alone in its view
    view = read(w.store, [a, b, c])
    assert f"{ab} and {ac} both set {uso} in " in view["why"] and "neither is within the other" in view["why"]
    assert f"State {uso} on {all3}, where both apply." in view["why"]
    assert check(w.store)["joint_statements_overlap"] == [{"joint_worlds": [ab, ac], "prices": [uso], "state_on": all3}]
    w.link(all3, "consequence", uso, shock=0.07)
    assert check(w.store)["joint_statements_overlap"] == []
    got = {tuple(s["outcomes"][e] for e in (a, b, c)): s["factors"][uso] for s in read(w.store, [a, b, c])["worlds"]}
    assert got["yes", "yes", first] == pytest.approx(0.07) and got["yes", "yes", second] == pytest.approx(0.07)
    assert got["yes", "yes", "no"] == pytest.approx(0.10) and got["yes", "no", first] == pytest.approx(0.05)


def test_a_narrower_joint_world_naming_an_episode_settles_the_worlds_it_holds(tmp_path):
    """Where the worlds a joint world lies in name different episodes, the episode a joint world within it names is the
    co-movement of the joint worlds it holds, so neither the view nor check() waits on them."""
    from massive_data import SESSIONS
    from test_ways import way
    w, a, b, spy, uso, gld = two_events(tmp_path)
    first, second = way(w, a, "first", side="yes", weight=0.6), way(w, a, "second", side="yes")
    w.store.edit(first, "episode", {"value": {"start": SESSIONS[-80], "end": SESSIONS[-30]}, "note": "then"})
    w.store.edit(b, "episode", {"value": {"start": SESSIONS[-120], "end": SESSIONS[-60]}, "note": "then"})
    broad, narrow = f"joint:{a}=yes|{b}=yes", f"joint:{a}={first}|{b}=yes"
    w.link(broad, "consequence", spy, shock=-0.2)
    assert [x["worlds"] for x in check(w.store)["joint_episodes_differ"]] == [{a: first, b: "yes"}]
    w.store.edit(narrow, "episode", {"value": {"start": SESSIONS[-80], "end": SESSIONS[-30]}, "note": "this one"})
    assert check(w.store)["joint_episodes_differ"] == [] and "why" not in read(w.store, [a, b])


def _foreign_pair(tmp_path):
    """A listing in won held for $10,000; E at 0.5 states the market -5%; F at 0.4 states the won -20% against its not
    happening (test_impact._abroad_store)."""
    from test_impact import _abroad_store
    store, e, _, abroad = _abroad_store(tmp_path, "scratch-joint", lambda f, a: [{"ticker": a, "dollars": 10000.0}],
                                        lambda f, a: {})
    f = store.add("Proposition", {"title": "F", "text": "F happens", "comparison": "occurs", "kind": "hitting",
                                  "window_end": "2027-03-31", "chances": [stated(0.4)], "baseline": "no"})["id"]
    store.link("portfolio", "watches", f)
    won = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "C:KRWUSD")
    spy = next(c["id"] for c in store.query("Factor") if c["stated"]["proxy"] == "SPY")
    store.link(e, "consequence", spy, {"shock": stated(-0.05)})
    store.link(f, "consequence", won, {"shock": stated(-0.2)})
    return store, e, f, f"name:{abroad}", won


def test_a_listing_abroad_stated_whole_on_a_joint_world_moves_with_its_currency_there(tmp_path):
    """A shock on a listing quoted in another currency moves its price in that currency, and its dollar price moves by
    that and the rate together (1.4, 1.6). Stated whole on the joint world where both happen, +20% against the joint
    world where neither does: there the rows put the won where F's -20% and what E's market move fills compound, and the
    listing's dollar price is 1.2 times the won's level; with the won stated -10% there as well, 1.2 x 0.9 = 1.08. Each
    is read from the P&L there and in the joint world where neither happens, which nothing moves, so no fitted beta
    enters."""
    store, e, f, name, won = _foreign_pair(tmp_path)
    joint = f"joint:{e}=yes|{f}=yes"
    store.link(joint, "consequence", name, {"shock": stated(0.2)})

    def ratio():
        got = {(s["outcomes"][e], s["outcomes"][f]): s for s in read(store, [e, f])["worlds"]}
        return (1 + got["yes", "yes"]["A"] / 10_000) / (1 + got["no", "no"]["A"] / 10_000), got["yes", "yes"]["factors"]
    level, factors = ratio()
    assert factors[won] < -0.2 and level == pytest.approx(1.2 * (1 + factors[won]))
    store.link(joint, "consequence", won, {"shock": stated(-0.1)})
    level, factors = ratio()
    assert factors[won] == pytest.approx(-0.1) and level == pytest.approx(1.2 * 0.9)
    store.close()


def _two_rows(tmp_path):
    """The research session's fixture (sandbox/tests/test_proposed.py), its composite as the joint world's id: A's row
    moves the market -10% and leaves a second factor to move with it; B's row states that factor at +5%."""
    w = world(tmp_path, g={"SPY": 10_000, "TLT": 5_000})
    a, b = event(w, "A", .3), event(w, "B", .4)
    spy, tlt = channel(w, "SPY"), channel(w, "TLT")
    w.link(a, "consequence", spy, shock=-0.1)
    w.link(b, "consequence", tlt, shock=0.05)
    return w, a, b, spy, tlt, f"joint:{a}=yes|{b}=yes"


def test_the_research_sessions_invariant_a_statement_agreeing_with_the_rows_changes_nothing(tmp_path):
    """The research analyst's statement of the invariant, on its own fixture: the market restated, on the joint world
    where both happen, exactly where the rows put it there, leaves every joint world's factors and P&L as the rows
    alone give them."""
    w, a, b, spy, tlt, both = _two_rows(tmp_path)
    rows = {tuple(named_by(s["id"]).values()): s for s in read(w.store, [a, b])["worlds"]}
    w.link(both, "consequence", spy, shock=rows[("yes", "yes")]["factors"][spy])
    got = {tuple(named_by(s["id"]).values()): s for s in read(w.store, [a, b])["worlds"]}
    assert "link" in got[("yes", "yes")]["set"][spy]
    for cell, state in got.items():
        assert state["A"] == pytest.approx(rows[cell]["A"], abs=1e-6)
        for c in (spy, tlt):
            assert state["factors"][c] == pytest.approx(rows[cell]["factors"][c], abs=1e-12)


def test_the_research_sessions_invariant_a_price_a_row_states_stays_where_the_rows_put_it(tmp_path):
    """Both happening stated at -20% on the market alone: the factor B's row states stays where the rows put it in
    that joint world, and is not filled again from the market's move."""
    w, a, b, spy, tlt, both = _two_rows(tmp_path)
    rows = next(s for s in read(w.store, [a, b])["worlds"] if set(s["outcomes"].values()) == {"yes"})
    w.link(both, "consequence", spy, shock=-0.2)
    state = next(s for s in read(w.store, [a, b])["worlds"] if set(s["outcomes"].values()) == {"yes"})
    assert state["factors"][spy] == pytest.approx(-0.2)
    assert state["factors"][tlt] == pytest.approx(rows["factors"][tlt], abs=1e-12)


def test_what_is_stated_of_a_joint_world_returns_its_events_read_together(tmp_path):
    """A statement returns what it now implies (TOOLS.md 5): a shock or an episode stated of a joint world, its removal, and
    get of it each give the events it names read together, the joint world among them. A joint world nothing is stated
    of is read too, with nothing stated and no links; an id naming none is refused, saying why. Named in another order,
    it is the one joint world, under its id; when its last statement goes, it is again one nothing is stated of."""
    from massive_data import SESSIONS
    from harness.tools import Tools
    from harness.world import World
    w, a, b, spy, uso, gld = two_events(tmp_path)
    tools = Tools(World(w.store, w.venues))

    def call(name, args):
        ok, out = tools.call(name, args)
        assert ok, out
        return out
    joint = f"joint:{a}=yes|{b}=yes"
    unstated = call("get", {"id": f"joint:{b}=yes|{a}=yes"})  # a joint world of its events, stated of or not
    assert (unstated["id"], unstated["stated"], unstated["links"]) == (joint, {}, [])
    assert unstated["together"]["events"] == [a, b] and call("neighbors", {"id": joint}) == []
    assert w.store.type_of(joint) is None and call("query", {"type": "JointWorld"}) == []
    ok, refused = tools.call("get", {"id": f"joint:{a}=maybe|{b}=yes"})
    assert not ok and refused["error"].startswith(f"maybe is not an outcome of {a}. Its outcomes are: yes, no")
    linked = call("link", {"from_id": f"joint:{b}=yes|{a}=yes", "relation": "consequence", "to_id": spy,
                           "fields": {"shock": {"value": -0.2, "note": "both together"}}})
    assert linked["from"] == joint and linked["together"]["events"] == [a, b]
    stood = {tuple(named_by(s["id"]).values()): s for s in linked["together"]["worlds"]}
    assert stood["yes", "yes"]["set"][spy]["link"] == linked["id"] and stood["yes", "yes"]["factors"][spy] == \
        pytest.approx(-0.2)
    window = {"value": {"start": SESSIONS[-80], "end": SESSIONS[-30]}, "note": "how it moved then"}
    edited = call("edit", {"id": joint, "field": "episode", "value": window})
    assert edited["id"] == joint and edited["together"]["events"] == [a, b]
    got = call("get", {"id": joint})
    assert got["stated"]["episode"]["value"] == window["value"] and got["together"]["worlds"] == \
        call("ledger", {"together": [a, b]})["together"]["worlds"]
    assert call("get", {"id": f"joint:{b}=yes|{a}=yes"})["id"] == joint  # its events in any order, one name
    assert linked["id"] in [n["link"] for n in call("neighbors", {"id": f"joint:{b}=yes|{a}=yes"})]
    removed = call("remove", {"id": linked["id"]})
    assert removed["together"]["events"] == [a, b]
    assert all(s["set"] == "nothing" or all("rows" in v for v in s["set"].values())
               for s in removed["together"]["worlds"])
    assert w.store.type_of(joint) == "JointWorld"  # its episode still names it
    cleared = call("edit", {"id": joint, "field": "episode", "value": None})
    assert cleared["together"]["events"] == [a, b] and w.store.type_of(joint) is None


def test_a_rewind_restores_the_joint_worlds_its_checkpoint_kept(tmp_path):
    """A joint world is kept with what the analyst has written, as a checkpoint holds it: one taken before it was named
    holds none, one after it holds it with its statement, and the joint view reads as each did."""
    w, a, b, spy, uso, gld = two_events(tmp_path)
    joint = f"joint:{a}=yes|{b}=yes"
    before = read(w.store, [a, b])["worlds"]
    w.store.checkpoint("s:0")
    lid = w.link(joint, "consequence", spy, shock=-0.2)
    after = read(w.store, [a, b])["worlds"]
    w.store.checkpoint("s:1")
    w.store.remove(lid)
    assert w.store.type_of(joint) is None
    w.store.rewind("s:1")
    assert w.store.type_of(joint) == "JointWorld" and w.store.links_of(joint, "consequence")[0]["id"] == lid
    assert read(w.store, [a, b])["worlds"] == after
    w.store.rewind("s:0")
    assert w.store.type_of(joint) is None and read(w.store, [a, b])["worlds"] == before


def test_a_reply_standing_on_a_joint_world_is_recorded_with_it(tmp_path):
    """The record a reply leaves copies what the analyst had written, a joint world and its statement among it, and
    the record reads back with every watched proposition's row."""
    from harness import record
    w, a, b, spy, uso, gld = two_events(tmp_path)
    joint = f"joint:{a}=yes|{b}=yes"
    lid = w.link(joint, "consequence", spy, shock=-0.2)
    record.reply(w.store, session="s", turn=1, stopped=False)
    (kept,) = w.store.records()
    written = kept["written"]
    assert next(o for o in written["object"] if o["id"] == joint)["type"] == "JointWorld"
    assert any(link["id"] == lid and link["from_id"] == joint for link in written["link"])
    assert {r["id"] for r in kept["rows"]} == {a, b}
    (read_back,) = record.replies(w.store)  # its copy reads as a Frozen state, the joint world in it
    assert read_back["frozen"].objects[joint]["type"] == "JointWorld" and read_back["frozen"].disputed() == set()


# ---------------------------------------------------------------- each world against the baseline, in three parts


def test_each_world_against_the_baseline_is_what_its_statements_set_what_moves_with_them_and_the_rest(tmp_path):
    """By hand (MATH.md 1.3, 1.5, 1.6). Oil is stated at +20% on an event at 0.3 against its not happening, and the market
    moves with it by their covariance, ln(1 + x_SPY) = (0.5e-4 / 3.25e-4) ln 1.2. $20,000 rides on the market and
    $10,000 on oil's residual. Placed, each price's two worlds stand s / (1 + q s) of today's apart, and a factor's
    residual is its placed move less 0.5 of the market's. The yes world's P&L against the baseline is then oil's dollar
    beta times its residual's difference, what the statement sets; the market's, which moves with it, and the largest;
    and what compounding adds, the oil carrier's own move r / (1 + q r) beyond its residual's. While the probability is
    open, the same parts are read with today's price at the baseline, q = 0."""
    from harness.ledger import event_now
    from test_impact import open_event
    x_spy = math.expm1(0.5e-4 / 3.25e-4 * math.log(1.2))
    r_oil = math.expm1(math.log(1.2) - 0.5 * math.log1p(x_spy))  # the oil carrier's response, 1 to oil's residual
    for q in (0.3, 0.0):
        w = world(tmp_path / str(q), g={"SPY": 20_000.0, "USO": 10_000.0},
                  coefficients={"SPY": {}, "USO": {"SPY": 0.5}}, variances={"SPY": 1e-4, "USO": 3e-4})
        e = event(w, "E", q) if q else open_event(w)[0]
        oil, spy = channel(w, "USO"), channel(w, "SPY")
        w.link(e, "consequence", oil, shock=0.2)
        apart = lambda s: s / (1 + q * s)  # noqa: E731
        stated_, moving = 10_000 * (apart(0.2) - 0.5 * apart(x_spy)), 20_000 * apart(x_spy)
        whole = 20_000 * apart(x_spy) + 10_000 * apart(r_oil)
        now = event_now(w.store, e)
        yes = next(o for o in now["outcomes"] if o["id"] == "yes")["against_baseline_parts"]
        assert yes == {"stated": round(stated_, 2), "moving": round(moving, 2),
                       "largest_moving": {"factor": spy, "name": w.store.stated_of(spy)["name"],
                                          "dollars": round(moving, 2)},
                       "rest": round(whole - stated_ - moving, 2)}
        assert "against_baseline_parts" not in next(o for o in now["outcomes"] if o["id"] == "no")  # the baseline
        if q:
            got = w.store.engine.value("impact", e).output["worlds"]
            assert got["yes"]["A"] - got["no"]["A"] == pytest.approx(whole)


def test_a_joint_worlds_parts_count_what_it_states_and_what_a_row_states_there_as_set(tmp_path):
    """In a joint world, the prices a statement sets are those the joint world states and those a row states in its
    world there; the rest move with them. Each joint world's three parts add to its P&L against the joint world in
    which every event is at its baseline. The company moves with the market alone."""
    w, a, b, spy, uso, gld = two_events(tmp_path)
    w.link(f"joint:{a}=yes|{b}=yes", "consequence", spy, shock=-0.2)
    view = by_outcomes(read(w.store, [a, b]), a, b)
    base = view["no", "no"]
    assert "against_baseline_parts" not in base
    for k, s in view.items():
        if k == ("no", "no"):
            continue
        parts = s["against_baseline_parts"]
        assert parts["stated"] + parts["moving"] + parts["rest"] == pytest.approx(s["A"] - base["A"], abs=1e-6)
    both = view["yes", "yes"]["against_baseline_parts"]  # the market stated there, gold kept from A's row, and oil,
    assert both["moving"] == 0.0 and "largest_moving" not in both  # which moves with them, carries no dollar
    only_a = view["yes", "no"]["against_baseline_parts"]  # as the rows put it: gold and the company's residual set by
    assert only_a["largest_moving"]["factor"] == spy and only_a["moving"] > 0.0  # A's row, the market moving with them
    assert only_a["stated"] < 0.0


def test_a_composite_the_portfolio_watches_is_its_own_event_and_takes_its_own_shocks(tmp_path):
    """A composite of outcomes of watched events stands for their joint world where it is no outcome of a watched event
    itself: a shock on it is refused with the joint world's id, and one stated on it before its parts were linked is
    listed with that id. Watched, the composite is an event of its own, and its shocks are its row's."""
    w = world(tmp_path, **FIXTURE)
    a, b = event(w, "A", .3), event(w, "B", .4)
    spy = channel(w, "SPY")
    w.link(a, "consequence", spy, shock=-0.1)
    w.link(b, "consequence", spy, shock=0.02)
    joint = f"joint:{a}=yes|{b}=yes"
    early = w.prop("both, before its parts", baseline="no")
    lid = w.link(early, "consequence", spy, shock=-0.2)  # stated while it was no composite
    w.link(early, "and", a)
    w.link(early, "and", b)
    assert check(w.store)["stated_on_composite"] == [{"link": lid, "on": early, "joint": joint}]
    with pytest.raises(StoreError, match=re.escape(f"{early} stands for the joint world {joint}. State the")):
        w.link(early, "consequence", channel(w, "USO"), shock=0.1)
    both = w.prop("both", baseline="no", chance={"value": 0.12})
    w.link(both, "and", a)
    w.link(both, "and", b)
    w.link("portfolio", "watches", both)
    w.link(both, "consequence", spy, shock=-0.3)  # an event of its own: its row
    assert w.store.engine.value("impact", both).output["against_baseline"]["yes"][spy] == pytest.approx(-0.3)
    assert [x["on"] for x in check(w.store)["stated_on_composite"]] == [early]
    then = w.prop("A, then B following it", baseline="no")  # within the joint world where both happen, and not it
    w.store.link(then, "then", a, {"step": 1})
    w.store.link(then, "then", b, {"step": 2})
    w.link(then, "consequence", spy, shock=-0.25)
    assert [x["on"] for x in check(w.store)["stated_on_composite"]] == [early]


def test_a_residual_stated_on_a_joint_world_stands_in_place_of_a_rows_as_a_whole_move_does(tmp_path):
    """A shock on a joint world is measured against the joint world in which each event it names is at its baseline, a
    residual as a whole move is (1.3, 1.4), and the statement on the joint world stands in place of a row's on the same
    price. A's row states the company's residual at -15%; the joint world where both happen states it at +5%. There the
    company is its response to that joint world's factors, at a beta of 1.5 to the market, times 1.05, against the joint
    world where neither happens, where nothing moves it: A's -15% no longer compounds with it."""
    w, a, b, spy, uso, gld = two_events(tmp_path)
    w.store.link(f"joint:{a}=yes|{b}=yes", "consequence", "name:COMPANY",
                 {"shock": stated({"value": 0.05, "against": schema.FOLLOWS})})
    view = by_outcomes(read(w.store, [a, b]), a, b)
    both, neither = view["yes", "yes"], view["no", "no"]
    level = (1 + both["A"] / 10_000) / (1 + neither["A"] / 10_000)
    assert "link" in both["set"]["name:COMPANY"]
    assert level == pytest.approx(1.05 * (1 + both["factors"][spy]) ** 1.5)
