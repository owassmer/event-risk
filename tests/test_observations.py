"""This machine's shared observations (harness.observations): an answer about a closed range is read once and serves
every store, one about today while a store holding it would not read it again; the market's closes kept by session;
and which fund's returns match an index's values (harness.venues.trackers.tracking)."""

from __future__ import annotations

import numpy as np

from harness.observations import Observations, today
from harness.venues.http import now
from harness.venues.trackers import tracking


def test_an_answer_about_a_closed_range_is_read_once_and_one_about_today_while_it_is_not_due(tmp_path):
    obs = Observations(tmp_path / "observations.sqlite")
    calls = []

    def fetch(subject, covering):
        calls.append(covering)
        return {"payload": {"status": "OK", "results": [len(calls)]}, "request": {"method": "GET", "url": "u"},
                "retrieved_at": now(), "range": covering}

    read = obs.shared("bars", fetch)
    past = ("2024-11-01", "2026-11-06")
    assert read("SPY", past)["payload"]["results"] == [1]
    assert read("SPY", past)["payload"]["results"] == [1] and len(calls) == 1  # kept: no second read
    reference = obs.shared("ticker_ref", fetch)  # read once on the as-of: the same answer all day
    assert reference("SPY", (today(), today()))["payload"]["results"] == [2]
    assert reference("SPY", (today(), today()))["payload"]["results"] == [2] and len(calls) == 2
    mark = obs.shared("snapshot", fetch)  # a latest mark is about the moment, and read every time
    assert mark("SPY", (today(), today()))["payload"]["results"] == [3]
    assert mark("SPY", (today(), today()))["payload"]["results"] == [4]
    mapped = obs.shared("figi", fetch)
    assert mapped("US0378331005", ("2026-11-06", "2026-11-06"))["payload"]["results"] == [5]
    assert mapped("US0378331005", (today(), today()))["range"] == (today(), today())  # a mapping is about no date
    assert len(calls) == 5


def test_many_subjects_are_served_as_each_alone_would_be_with_one_query_a_chunk(tmp_path):
    """A many-read serves exactly what a read of each subject alone serves, by one rule, and asks the file once a chunk
    of subjects, not once a subject: each query hands back the interpreter's lock, which a thread computing beside it
    holds for its whole switch interval."""
    from datetime import datetime, timedelta, timezone

    obs = Observations(tmp_path / "observations.sqlite")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    asked: list[list[str]] = []

    def answer(subject, covering, *, status="OK", parse=2, at=None):
        return {"payload": {"status": status, "parse": parse, "subject": subject}, "request": {"url": subject},
                "retrieved_at": at or now(), "range": covering}

    def fetch(subject, covering):
        asked.append([subject])
        return answer(subject, covering)

    def many(subjects, covering):
        asked.append(list(subjects))
        return {s: answer(s, covering) for s in subjects}

    fetch.many = many
    fetch.parse = 2
    month = (today(), today())
    obs.keep("listing_sector", "OK@m", month, answer("OK@m", month, at=yesterday))  # names its listing: served
    obs.keep("listing_sector", "NONE@m", month, answer("NONE@m", month, status="NOT_FOUND"))  # names none, today
    obs.keep("listing_sector", "OLD@m", month, answer("OLD@m", month, status="NOT_FOUND", at=yesterday))  # read again
    obs.keep("listing_sector", "FORM@m", month, answer("FORM@m", month, parse=1))  # another form: read again
    many_kept = [f"S{i}@m" for i in range(1100)]
    for s in many_kept:
        obs.keep("listing_sector", s, month, answer(s, month, at=yesterday))
    sectors = obs.shared("listing_sector", fetch)
    alone = {s: sectors(s, month) for s in ("OK@m", "NONE@m")}
    assert asked == []  # each served, as kept

    queries: list[str] = []
    obs._db().set_trace_callback(lambda sql: queries.append(sql) if sql.startswith("SELECT") else None)
    got = sectors.many(["OK@m", "NONE@m", "OLD@m", "FORM@m", "NEW@m", *many_kept], month)
    obs._db().set_trace_callback(None)
    assert len(queries) == 3  # 1,105 subjects asked in three chunks of at most 500
    assert asked == [["OLD@m", "FORM@m", "NEW@m"]]  # read from the source: what alone would read, in one request
    assert {s: got[s] for s in alone} == alone
    assert all(got[s]["retrieved_at"] == yesterday for s in many_kept)

    bars = obs.shared("bars", fetch)  # a closed range: served over exactly the range asked
    past, other = ("2024-11-01", "2026-11-06"), ("2024-11-01", "2026-11-05")
    obs.keep("bars", "SPY", past, answer("SPY", past))
    asked.clear()
    assert bars.many(["SPY", "QQQ"], past)["SPY"] == bars("SPY", past) and asked == [["QQQ"]]
    asked.clear()
    bars.many(["SPY"], other)
    assert asked == [["SPY"]]

    refs = obs.shared("ticker_ref", fetch)  # about today: served the same day, while not due
    day = (today(), today())
    obs.keep("ticker_ref", "SPY", day, answer("SPY", day))
    asked.clear()
    assert refs.many(["SPY", "QQQ"], day)["SPY"] == refs("SPY", day) and asked == [["QQQ"]]


def test_a_fund_matches_an_index_by_how_closely_it_tracks_the_index_on_a_typical_session():
    rng = np.random.default_rng(7)
    index = rng.normal(0, 0.01, 63)
    noise = lambda sd: rng.normal(0, sd, 63)  # noqa: E731
    fund = index + noise(0.0003)
    fund[30] -= 0.004  # its distribution's session, which the typical session does not read
    twin, other = index + noise(0.0004), index * 0.9 + noise(0.004)
    found = tracking(index, {"A": fund, "B": twin, "C": other})
    assert [m["listing"] for m in found["matches"]] == ["A", "B"] and found["compared"] == 3
    assert found["matches"][0]["tracking"] < 0.06 and found["nearest"][-1]["listing"] == "C"
    assert next(n for n in found["nearest"] if n["listing"] == "C")["tracking"] > 0.3  # another basket
    alone = tracking(index, {"C": other})  # where no fund holds the index, none matches it
    assert alone["matches"] == [] and alone["nearest"][0]["listing"] == "C"
    assert tracking(index, {})["matches"] == []


def test_the_markets_closes_are_kept_by_session_and_read_by_session(tmp_path):
    obs = Observations(tmp_path / "observations.sqlite")
    for day, closes in (("2026-11-02", {"X": 100.0, "Y": 10.0}), ("2026-11-03", {"X": 102.0})):
        obs.keep_day(day, [{"T": t, "c": c} for t, c in closes.items()] + [{"T": "Z"}], {"url": "g"},
                     "2026-11-05T00:00:00+00:00")
    assert set(obs.market_days()) == {"2026-11-02", "2026-11-03"}
    assert obs.day_closes(["2026-11-02", "2026-11-03"]) == {"X": {"2026-11-02": 100.0, "2026-11-03": 102.0},
                                                             "Y": {"2026-11-02": 10.0}}


def test_the_listings_that_closed_at_a_price_are_read_from_the_sessions_closes(tmp_path, monkeypatch):
    """closed_at: every listing whose close on the row's session lay between the two prices, closest to the middle
    first; a day the exchange did not trade reads the session before it; and today's session, once closed, is read
    from the source and not kept, since a session's closes are kept once its day has passed."""
    import harness.venues.trackers as trackers
    from harness.venues.trackers import Trackers

    obs = Observations(tmp_path / "observations.sqlite")
    obs.keep_day("2026-11-06", [{"T": "META", "c": 641.39}, {"T": "OTHR", "c": 641.40}, {"T": "LOW", "c": 12.5}],
                 {"url": "g"}, "2026-11-08T00:00:00+00:00")

    class Source:
        asked: list[str] = []

        def url(self, path, params=None):
            return "https://massive.test" + path

        def get(self, url):
            self.asked.append(url)
            return 200, {"status": "OK", "results": [{"T": "META", "c": 650.0}, {"T": "NOW", "c": 650.004}]}

    source = Source()
    found = Trackers(source, obs).closed_at("2026-11-08|641.3899|641.4001", "2026-11-10")["payload"]
    assert found["session"] == "2026-11-06"  # a Sunday's row stands at Friday's close
    assert [x["ticker"] for x in found["listings"]] == ["META", "OTHR"] and not source.asked
    found = Trackers(source, obs).closed_at("2026-11-06|641.389999|641.390001", "2026-11-10")["payload"]
    assert [x["ticker"] for x in found["listings"]] == ["META"]
    monkeypatch.setattr(trackers, "today", lambda: "2026-11-09")
    found = Trackers(source, obs).closed_at("2026-11-09|649.999|650.0045", "2026-11-09")["payload"]
    assert found["session"] == "2026-11-09" and [x["ticker"] for x in found["listings"]] == ["META", "NOW"]
    assert source.asked and "2026-11-09" not in obs.market_days()  # read now, kept nowhere


def test_a_sessions_closes_read_before_its_closing_prices_were_published_are_read_again_and_such_a_read_is_not_kept(
        tmp_path, monkeypatch):
    """A session's closes are what a read made once its closing prices are published gives, SETTLE after its close. A
    session this machine holds from a read made during it holds a moment of the session (read live: a read at 1:15 PM
    held EWY at 149.56, its close 147.81), so it is read again, and the read replaces it; and a read made before then is
    answered and not kept."""
    import harness.venues.trackers as trackers
    from harness.venues.trackers import Trackers

    obs = Observations(tmp_path / "observations.sqlite")
    obs.keep_day("2026-11-11", [{"T": "EWY", "c": 149.56}], {"url": "g"}, "2026-11-11T18:15:07+00:00")  # 1:15 PM
    obs.keep_day("2026-11-10", [{"T": "EWY", "c": 139.18}], {"url": "g"}, "2026-11-11T05:02:00+00:00")

    class Source:
        def __init__(self):
            self.asked = []

        def url(self, path, params=None):
            return "https://massive.test" + path

        def get(self, url):
            self.asked.append(url)
            return 200, {"status": "OK", "results": [{"T": "EWY", "c": 147.81}, {"T": "SPY", "c": 761.0}]}

    source = Source()
    found = Trackers(source, obs).closed_at("2026-11-11|147.8099|147.8101", "2026-11-12")["payload"]
    assert [x["ticker"] for x in found["listings"]] == ["EWY"]  # its close, read again
    assert len(source.asked) == 1 and "2026-11-11" in source.asked[0]  # the settled day before it was not
    assert obs.day_closes(["2026-11-11"])["EWY"] == {"2026-11-11": 147.81}  # and the read replaced the moment
    monkeypatch.setattr(trackers, "now", lambda: "2026-11-12T21:05:00+00:00")  # five minutes after the 12th's close
    assert Trackers(source, obs).closes(["2026-11-12"])["EWY"] == {"2026-11-12": 147.81}
    assert "2026-11-12" not in obs.market_days()  # answered, and not kept


def test_a_session_kept_from_a_read_before_its_close_is_dropped_with_the_answers_resting_on_it(tmp_path):
    """A session this machine holds from a read made before its closing prices were published, and each
    listing-at-a-price answer that rests on it, are dropped when the trackers open, so each is read again; a session
    read once settled, and an answer resting on it, stand."""
    from harness.venues.trackers import Trackers

    obs = Observations(tmp_path / "observations.sqlite")
    obs.keep_day("2026-11-11", [{"T": "EWY", "c": 149.56}], {"url": "g"}, "2026-11-11T18:15:07+00:00")
    obs.keep_day("2026-11-10", [{"T": "EWY", "c": 139.18}], {"url": "g"}, "2026-11-11T05:02:00+00:00")
    for session in ("2026-11-11", "2026-11-10"):
        obs.keep("closed_at", f"{session}|1|2", ("2026-08-24", "2026-08-24"),
                 {"payload": {"status": "OK", "session": session, "listings": []}, "request": {"url": "g"},
                  "retrieved_at": "2026-11-12T08:59:55+00:00", "range": ("2026-08-24", "2026-08-24")})
    Trackers(object(), obs)
    assert set(obs.market_days()) == {"2026-11-10"} and obs.day_closes(["2026-11-11"]) == {}
    kept = {s for (s,) in obs._db().execute("SELECT subject FROM answers WHERE kind = 'closed_at'")}
    assert kept == {"2026-11-10|1|2"}
