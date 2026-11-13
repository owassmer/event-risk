"""The judge: mammoth-1 through System One against a fake opener, checks over one evidence in one request,
the size limit, a check left unanswered, and the cache. Nothing touches the network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from breakdown import World
from harness.derive.checks import market_questions
from harness.judge import MODEL, STATE_LIMIT, FixtureJudge, JudgeError, SystemOne, TooLong, default_judge, state
from harness.tools import Tools

TAIL = " could not be read just now. Nothing was written, and the call can be made again."  # the judge is the store's own: no reading is named
from harness.world import World as Place
from test_propagation import M1Q

KEY = "bt-test-0123456789abcdefghij"


class Response(io.BytesIO):
    def __init__(self, status: int, body: dict):
        super().__init__(json.dumps(body).encode())
        self.status = status


class Opener:
    """Answers each POST from a list of (status, body); records the URL, headers, and body sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[tuple[str, dict, dict, bytes]] = []

    def __call__(self, request, timeout=None):
        self.sent.append((request.full_url, dict(request.header_items()), json.loads(request.data), request.data))
        status, body = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))
        return Response(status, body)


def system_one(**answers) -> dict:
    return {"model": "baseten-mammoth-27b", "answers": {k: {"type": "noul", "noul": v} for k, v in answers.items()},
            "usage": {"input_tokens": 431, "output_tokens": len(answers)}}


EVIDENCE = [
    {"ref": "portfolio", "content": {"date": "2026-11-04"}},
    {"ref": "prop:1", "content": {"proposition": "R", "says": "The US raises tariffs", "judged": "at any time in its window",
                                  "window ends": "2027-03-31",
                                  "resolved by": "the Federal Register"}},
    {"ref": "market:polymarket:M1", "content": {"venue": "Polymarket", "market": M1Q, "rules": "Resolves Yes...",
                                                "closes": "2027-03-31", "still open": True}},
]
FIT = market_questions("yes")


# ---------------------------------------------------------------- the request


def test_the_request_exactly_as_sent_and_the_key_only_in_its_header(monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    opener = Opener((200, system_one(market_forward=0.96, market_converse=0.94)))
    answers = SystemOne(KEY, opener=opener, pause=0).answer_all(FIT, EVIDENCE)
    url, headers, body, data = opener.sent[0]
    assert url == "https://inference.baseten.co/v1/systemone" and headers["Authorization"] == f"Bearer {KEY}"
    assert body == {
        "model": "decision-models/baseten-mammoth-27b-latest",
        "state": state(EVIDENCE),
        "questions": {"market_forward": {"type": "noul", "instructions": FIT[0][1]},
                      "market_converse": {"type": "noul", "instructions": FIT[1][1]}}}
    assert body["state"] == (
        "The texts below are a proposition, as an analyst wrote it, and a prediction market, as its venue words it."
        "\n\nToday is November 4, 2026. The market is open and has not resolved."
        "\n\nThe proposition, as an analyst wrote it:\nTitle: R\nText: The US raises tariffs"
        "\nIt is true if this happens at any time through March 31, 2027.\nResolution source: the Federal Register"
        "\n\nThe prediction market, as Polymarket words it:\nQuestion: Will the US raise tariffs by March 31?"
        "\nRules: Resolves Yes...\nIt closes on March 31, 2027.")
    assert FIT[0][1] == "Does the market resolve Yes, under its rules, in every outcome in which the proposition is true?"
    assert KEY.encode() not in data
    assert [(a.probability, a.model) for a in answers] == [(0.96, "baseten-mammoth-27b"), (0.94, "baseten-mammoth-27b")]
    assert json.loads(answers[0].raw) == opener.answers[0][1]  # raw is the response body


def test_a_check_the_response_does_not_answer_is_no_answer_and_each_kind_is_named_once():
    opener = Opener((200, {"model": "m", "answers": {"market_forward": {"type": "noul", "noul": 0.9},
                                                     "market_converse": {"type": "score", "score": 1}}}))
    found = SystemOne(KEY, opener=opener, pause=0).answer_all(FIT, EVIDENCE)
    assert [a.probability for a in found] == [0.9, None] and "no answer to this check" in found[1].reason
    with pytest.raises(ValueError, match="names each question once"):
        SystemOne(KEY, opener=opener, pause=0).answer_all([FIT[0], FIT[0]], EVIDENCE)


def test_429_is_retried_and_a_rejection_is_no_answer_with_the_apis_message():
    opener = Opener((429, {"error": "capacity"}), (200, system_one(implication=0.4)))
    assert SystemOne(KEY, opener=opener, pause=0).answer_all([("implication", "If A, B?")], EVIDENCE)[0].probability == 0.4
    assert len(opener.sent) == 2
    rejected = Opener((422, {"error": "Prompt exceeds the context limit", "error_type": "Validation"}))
    with pytest.raises(TooLong, match="more than the judge reads at once"):
        SystemOne(KEY, opener=rejected, pause=0).answer_all(FIT, EVIDENCE)


def test_a_connection_dropped_while_the_answer_is_read_is_asked_again_and_one_never_answered_is_no_answer():
    import http.client

    class Dropping(Opener):
        def __init__(self, drops, *answers):
            super().__init__(*answers)
            self.drops = list(drops)

        def __call__(self, request, timeout=None):
            if self.drops:
                self.sent.append((request.full_url, {}, {}, b""))
                raise self.drops.pop(0)
            return super().__call__(request, timeout)

    dropped = [http.client.RemoteDisconnected("closed"), ConnectionResetError("reset"),
               http.client.IncompleteRead(b"{")]
    opener = Dropping(dropped, (200, system_one(implication=0.4)))
    assert SystemOne(KEY, opener=opener, pause=0, retries=3).answer_all([("implication", "If A, B?")],
                                                                         EVIDENCE)[0].probability == 0.4
    assert len(opener.sent) == 4
    never = Dropping([ConnectionResetError("reset")] * 4, (200, system_one(implication=0.4)))
    with pytest.raises(JudgeError, match="could not be reached"):
        SystemOne(KEY, opener=never, pause=0, retries=3).answer_all([("implication", "If A, B?")], EVIDENCE)


def test_evidence_over_the_limit_is_not_sent():
    opener = Opener((200, system_one(implication=0.5)))
    long = [EVIDENCE[0], {**EVIDENCE[1], "content": {**EVIDENCE[1]["content"], "says": "x" * STATE_LIMIT}}, EVIDENCE[2]]
    with pytest.raises(TooLong, match="more than the judge reads at once"):
        SystemOne(KEY, opener=opener, pause=0).answer_all([("implication", "If A, B?")], long)
    assert opener.sent == []


# ---------------------------------------------------------------- through the store


def judged_world(tmp_path, monkeypatch, *answers):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    opener = Opener(*answers)
    w.store.engine.judge = SystemOne(KEY, opener=opener, pause=0)
    r = w.prop("R")
    link = w.link(r, "asks", w.market("M1", M1Q, 0.28, 0.33))
    return w, opener, r, link


def test_a_market_check_is_one_request_and_two_judgments_and_a_second_read_asks_nothing(tmp_path, monkeypatch):
    answer = system_one(market_forward=0.96, market_converse=0.94)
    w, opener, r, link = judged_world(tmp_path, monkeypatch, (200, answer))
    assert len(opener.sent) == 1 and list(opener.sent[0][2]["questions"]) == ["market_forward", "market_converse"]
    rows = w.store.db.execute("SELECT kind, answer, model, raw FROM judgment ORDER BY rowid").fetchall()
    assert [(k, json.loads(a), m) for k, a, m, _ in rows] == [("market_forward", 0.96, "baseten-mammoth-27b"),
                                                 ("market_converse", 0.94, "baseten-mammoth-27b")]
    assert all(json.loads(raw) == answer for *_, raw in rows)
    assert (w.derived(link, "forward"), w.derived(link, "converse")) == (0.96, 0.94)
    w.store.engine.reset()
    assert w.derived(link, "forward") == 0.96 and len(opener.sent) == 1  # from the cache
    w.store.close()
    stored = b"".join(f.read_bytes() for f in w.store.dir.iterdir() if f.is_file())
    assert KEY.encode() not in stored  # the key is in no recorded byte


def test_oversized_evidence_is_no_check_and_nothing_is_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    opener = Opener((200, system_one(market_forward=0.9, market_converse=0.9)))
    w.store.engine.judge = SystemOne(KEY, opener=opener, pause=0)
    r = w.prop("R")
    link = w.store.link(r, "asks", w.market("M1", M1Q, 0.28, 0.33, rules="y" * STATE_LIMIT))  # rules that long
    assert "forward" not in link["derived"] and "converse" not in link["derived"]  # no judge here reads it
    assert opener.sent == [] and w.store.db.execute("SELECT count(*) FROM judgment").fetchone()[0] == 0
    assert w.interval(r) == (0.28, 0.33)  # the bound stands


def test_a_question_left_unanswered_fails_the_call_keeps_what_was_answered_and_is_asked_again(tmp_path, monkeypatch):
    """The judge's reading is a read the link's result needs: a question it leaves unanswered fails the call with
    nothing written and no reason, what it did answer is kept, and the call made again asks only the rest (TOOLS.md 5)."""
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    opener = Opener((200, system_one(market_converse=0.94)), (200, system_one(market_forward=0.9)))
    w.store.engine.judge = SystemOne(KEY, opener=opener, pause=0)
    tools = Tools(Place(w.store, w.venues))
    r, m = w.prop("R"), w.market("M1", M1Q, 0.28, 0.33)
    ok, got = tools.call("link", {"from_id": r, "relation": "asks", "to_id": m})
    assert not ok and got == {"error": "A reading this call needs" + TAIL, "dependents": []}
    assert w.store.links_of(r, "asks") == [] and not w.store._market_kept(m)  # nothing written
    assert [tuple(x) for x in w.store.db.execute("SELECT kind FROM judgment")] == [("market_converse",)]  # kept
    ok, got = tools.call("link", {"from_id": r, "relation": "asks", "to_id": m})
    held = w.store.get(got["id"])["derived"]  # the reading is the store's: the analyst's return carries none of it
    assert ok and (held["forward"]["value"], held["converse"]["value"]) == (0.9, 0.94)
    assert len(opener.sent) == 2 and list(opener.sent[1][2]["questions"]) == ["market_forward"]  # the rest alone


def test_a_check_the_judge_could_not_answer_waits_with_no_reason(tmp_path, monkeypatch):
    """Read outside a call, as the page reads, a check the judge did not answer is there as waiting and nothing more;
    read by a call, it fails the call."""
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    w.store.engine.judge = SystemOne(KEY, opener=Opener((500, {"error": "down"})), pause=0, retries=1)
    r = w.prop("R")
    link = w.store.link(r, "asks", w.market("M1", M1Q, 0.28, 0.33))
    assert link["derived"]["forward"] == {"value": None, "waiting": True}
    assert w.interval(r) == (0.28, 0.33)  # the bound does not wait on the check
    ok, got = Tools(Place(w.store, w.venues)).call("get", {"id": link["id"]})
    assert not ok and got == {"error": "A reading this call needs" + TAIL, "dependents": []}


def test_which_judge_answers(monkeypatch):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert default_judge() is None  # no key: the cache alone answers
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    judge = default_judge()
    assert isinstance(judge, SystemOne) and judge.model == MODEL == "decision-models/baseten-mammoth-27b-latest"
    assert FixtureJudge().model == "fixture"


def test_the_framing_is_part_of_every_key(monkeypatch):
    from harness import judge
    from harness.derive import engine
    before = engine.judgment_hash("implication", "If A, B?", EVIDENCE, 1)
    monkeypatch.setattr(judge, "ABOUT_MARKET", judge.ABOUT_MARKET + " Another sentence.")
    assert engine.judgment_hash("implication", "If A, B?", EVIDENCE, 1) != before  # asked again, with no version bumped


@pytest.mark.parametrize("kind, start, said", [
    ("hitting", "2026-11-04", "It is true if this happens at any time from November 4, 2026 to March 31, 2027."),
    ("hitting", None, "It is true if this happens at any time through March 31, 2027."),
    ("level_at_date", None, "It is true if this holds on March 31, 2027."),
    ("scheduled_decision", None, "It is decided at the scheduled decision on March 31, 2027.")])
def test_the_judge_reads_when_a_proposition_is_judged(kind, start, said):
    """A level reached at any time in a window, a level on a date, and a decision then are three propositions on the
    same dates: the judge reads which, from the one rendering of a proposition."""
    from harness.derive.evidence import JUDGED
    from harness.judge import proposition_text
    content = {"proposition": "T", "says": "The rate reaches 25%", "judged": JUDGED[kind],
               **({"window starts": start} if start else {}), "window ends": "2027-03-31"}
    assert said in proposition_text(content, "The proposition:").split("\n")
