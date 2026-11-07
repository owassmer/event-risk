"""The judge: mammoth-1 through System One against a fake opener, checks over one evidence in one request,
the size limit, a check left unanswered, and the cache. Nothing touches the network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from breakdown import RETRIEVED, World
from harness.derive.checks import market_questions
from harness.judge import ABOUT, MODEL, STATE_LIMIT, FixtureJudge, JudgeError, SystemOne, default_judge
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
    {"ref": "prop:1", "content": {"proposition": "R", "says": "The US raises tariffs", "window": "2026-11-04 to 2027-03-31",
                                  "read": "at any time in the window"}},
    {"ref": "market:polymarket:M1", "content": {"market": M1Q, "rules": "Resolves Yes...", "closes": "2027-03-31",
                                                "still open": True}},
    {"ref": "src:4", "content": {"page": "https://example.org/r", "dated": "2026-11-01",
                                 "passage": "the rate is set to rise"}},
]
FIT = market_questions("The US raises tariffs", M1Q, "identity")


# ---------------------------------------------------------------- the request


def test_the_request_exactly_as_sent_and_the_key_only_in_its_header(monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    opener = Opener((200, system_one(market_forward=0.96, market_converse=0.94)))
    answers = SystemOne(KEY, opener=opener, pause=0).answer_all(FIT, EVIDENCE)
    url, headers, body, data = opener.sent[0]
    assert url == "https://inference.baseten.co/v1/systemone" and headers["Authorization"] == f"Bearer {KEY}"
    assert body == {
        "model": "decision-models/baseten-mammoth-27b-latest",
        "state": {"about": ABOUT, "evidence": [e["content"] for e in EVIDENCE]},  # a page as the passage quoted
        "questions": {"market_forward": {"type": "noul", "instructions": FIT[0][1]},
                      "market_converse": {"type": "noul", "instructions": FIT[1][1]}}}
    assert FIT[0][1] == "If \u201cThe US raises tariffs\u201d is true, does the market \u201cWill the US raise tariffs by " \
                        "March 31?\u201d resolve Yes?"
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
    with pytest.raises(JudgeError, match="more than the judge reads at once"):
        SystemOne(KEY, opener=rejected, pause=0).answer_all(FIT, EVIDENCE)


def test_evidence_over_the_limit_is_not_sent():
    opener = Opener((200, system_one(implication=0.5)))
    long = [*EVIDENCE[:3], {**EVIDENCE[3], "content": {**EVIDENCE[3]["content"], "passage": "x" * STATE_LIMIT}}]
    with pytest.raises(JudgeError, match="more than the judge reads at once"):
        SystemOne(KEY, opener=opener, pause=0).answer_all([("implication", "If A, B?")], long)
    assert opener.sent == []


# ---------------------------------------------------------------- through the store


def judged_world(tmp_path, monkeypatch, *answers):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    opener = Opener(*answers)
    w.store.engine.judge = SystemOne(KEY, opener=opener, pause=0)
    r = w.prop("R")
    link = w.link(r, "quoted_by", w.market("M1", M1Q, 0.28, 0.33))
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


def test_oversized_evidence_is_unavailable_with_its_reason_and_nothing_is_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    opener = Opener((200, system_one(market_forward=0.9, market_converse=0.9)))
    w.store.engine.judge = SystemOne(KEY, opener=opener, pause=0)
    r = w.prop("R")
    page = w.store.put_page("https://example.org/long", "y" * STATE_LIMIT, retrieved_at=RETRIEVED)
    w.link(r, "evidence", page, passages=["y" * STATE_LIMIT])  # a passage as long as the page
    link = w.store.link(r, "quoted_by", w.market("M1", M1Q, 0.28, 0.33))
    forward = link["derived"]["forward"]
    assert forward["value"] is None and "more than the judge reads at once" in forward["reason"]
    assert opener.sent == [] and w.store.db.execute("SELECT count(*) FROM judgment").fetchone()[0] == 0
    assert w.interval(r) == (0.28, 0.33)  # the check waits; the bound does not


def test_a_question_left_unanswered_is_unavailable_and_asked_again(tmp_path, monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    w = World(tmp_path / "scratch-judge")
    opener = Opener((200, system_one(market_converse=0.94)), (200, system_one(market_forward=0.9)))
    w.store.engine.judge = SystemOne(KEY, opener=opener, pause=0)
    first = w.store.link(w.prop("R"), "quoted_by", w.market("M1", M1Q, 0.28, 0.33))
    forward = first["derived"]["forward"]
    assert forward["value"] is None and "no answer to this check" in forward["reason"]
    assert first["derived"]["converse"]["value"] == 0.94
    assert [tuple(x) for x in w.store.db.execute("SELECT kind FROM judgment")] == [("market_converse",)]
    assert w.derived(first["id"], "forward") == 0.9 and len(opener.sent) == 2  # the next read asks again
    assert list(opener.sent[1][2]["questions"]) == ["market_forward"]  # alone: the converse is cached


def test_which_judge_answers(monkeypatch):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert default_judge() is None  # no key: the cache alone answers
    monkeypatch.setenv("BASETEN_API_KEY", KEY)
    judge = default_judge()
    assert isinstance(judge, SystemOne) and judge.model == MODEL == "decision-models/baseten-mammoth-27b-latest"
    assert FixtureJudge().model == "fixture"


def test_the_framing_is_part_of_every_key(monkeypatch):
    from harness.derive import engine
    before = engine.judgment_hash("implication", "If A, B?", EVIDENCE, 1)
    monkeypatch.setattr(engine, "FRAMING", engine.text_hash(ABOUT + " Another sentence."))
    assert engine.judgment_hash("implication", "If A, B?", EVIDENCE, 1) != before  # asked again, with no version bumped
