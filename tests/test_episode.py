"""A stated past window completes factors by its actual lagged co-movement."""

import numpy as np
import pytest

from harness.derive import estimate
from harness.derive.episode import for_worlds, subject


def test_episode_reads_exact_sessions_and_production_completed_covariance(tmp_path):
    from test_impact import world
    from massive_data import SESSIONS
    from harness.derive.portfolio import closes
    from harness.derive import history
    w = world(tmp_path, g={"SPY": 1000})
    start, end = SESSIONS[-80], SESSIONS[-30]
    found = w.store.engine.value("episode_covariance", subject(start, end))
    assert found.output is not None, found.cause
    held = w.store.computation(found.basis)
    m = held["inputs"]
    load = w.store.engine.load
    sessions = sorted(d for d in closes(m["series"][0], load) if start <= d <= end)
    x = np.full((len(sessions) - 1, len(m["channels"])), np.nan)
    for j, item in enumerate(m["series"]):
        for a, b, r in history.returns(item, load, sessions)["dollar"]:
            if b == a + 1:
                x[a, j] = r
    expected = estimate.covariances(x, estimate.common(x), estimate.SETTINGS.memory)[2]
    assert np.asarray(found.output["covariance"]) == pytest.approx(expected, abs=1e-14)
    assert found.output["sessions"] == len(sessions) - 1
    assert found.output["window"] == [start, end]


def test_nearest_episode_inherits_through_ways_without_changing_ordinary_variation():
    class Context:
        def object(self, pid):
            return {"stated": {"episode": {"start": "2020-01-01", "end": "2020-02-01"}}} if pid == "prop:1" else \
                {"stated": {"episode": {"start": "2021-01-01", "end": "2021-02-01"}}} if pid == "prop:2" else \
                {"stated": {}}

        def value(self, kind, key):
            return {"basis": "cmp:1", "output": {"covariance": [[1.0]], "key": key}}

    tree = {"nodes": {"yes": {"parent": None}, "no": {"parent": None},
                      "prop:2": {"parent": "yes"}, "prop:3": {"parent": "prop:2"}},
            "worlds": ["prop:3", "no"]}
    out = for_worlds(Context(), "prop:1", tree)
    assert out["prop:3"]["on"] == "prop:2"
    assert out["no"]["on"] == "prop:1"
    assert "2021-01-01" in out["prop:3"]["key"]


def test_episode_changes_the_unstated_fill_but_neither_shock_nor_probability(tmp_path):
    from test_impact import world, event, channel
    from breakdown import stated
    from massive_data import SESSIONS
    w = world(tmp_path, g={"SPY": 5000, "USO": 3000})
    e = event(w, p=0.3)
    spy, oil = channel(w, "SPY"), channel(w, "USO")
    w.link(e, "consequence", spy, shock=-0.1)
    ordinary = w.store.engine.value("impact", e).output
    w.store.edit(e, "episode", {"value": {"start": SESSIONS[-75], "end": SESSIONS[-25]},
                                "note": "The named past window supplies this world's factor relationships."})
    changed = w.store.engine.value("impact", e).output
    assert changed["p"] == ordinary["p"] == 0.3
    assert changed["against_baseline"]["yes"][spy] == pytest.approx(-0.1)
    assert changed["against_baseline"]["yes"][oil] != pytest.approx(ordinary["against_baseline"]["yes"][oil])


def test_a_window_a_proxy_has_no_prices_for_is_refused_and_the_event_names_its_episode(tmp_path):
    """MATH.md 1.3; TOOLS.md 5: an episode is a past window over which every factor's proxy moved; one that begins
    before a proxy's prices do has no co-movement to give, and the statement naming it fails with why, leaving the store
    as it was. One that holds is named, with its window and its count of sessions, beside each factor it fills."""
    from test_impact import world, event, channel
    from massive_data import SESSIONS
    from harness.ledger import event_now
    from harness.store import StoreError
    w = world(tmp_path, g={"SPY": 5000, "USO": 3000})
    e = event(w, p=0.3)
    spy, oil = channel(w, "SPY"), channel(w, "USO")
    w.link(e, "consequence", spy, shock=-0.1)
    early = "2019-01-02"  # the store's reads now cover it, and the proxies' prices begin later
    for row in list(w.store._all("SELECT * FROM data WHERE kind IN ('bars', 'dividends')")):
        w.store.put_data(row["kind"], row["subject"], w.store.data_payload(row["id"]), {"url": "test:history"},
                         retrieved_at="2026-11-05T02:00:00+00:00", range=("2018-12-01", SESSIONS[-1]))
    with pytest.raises(StoreError, match=rf"the episode {early} to {SESSIONS[5]} reads how every factor's proxy moved "
                                         rf"over it, and \w+ has no close on or before {early}: its prices begin"):
        w.store.edit(e, "episode", {"value": {"start": early, "end": SESSIONS[5]}, "note": "a window before them"})
    assert "episode" not in w.store.stated_of(e)
    w.store.edit(e, "episode", {"value": {"start": SESSIONS[-75], "end": SESSIONS[-25]}, "note": "the window"})
    now = event_now(w.store, e)
    [filled] = [f for f in now["factors"] if f["factor"] == oil]
    episode = filled["worlds"]["yes"]["episode"]
    assert filled["worlds"]["yes"]["by"] == "moves with the stated ones" and episode["on"] == e
    assert episode["window"] == [SESSIONS[-75], SESSIONS[-25]] and episode["sessions"] == 50
    assert "no" not in filled["worlds"]  # the baseline sets no factor and moves nothing: no episode, and no entry
