"""The judge: Base Labs' mammoth-1, a decision model, through Baseten's System One API.

The judge checks claims about texts: that a market asks exactly a proposition, or that one proposition implies
another. Each check is a `noul`, a typed predicate whose answer is the probability it holds, returned as a typed
field: nothing parses a number out of text. Its state says the situation, the date and the texts it compares, and its
question asks one thing in the words of those texts. Checks over the same evidence share one request (a market's
check forward and converse); each is still its own Judgment, cached on its own key. A question is (kind, statement).

The request, as verified against https://docs.baseten.co/reference/inference-api/system-one and
https://docs.baseten.co/inference/model-apis/decision-models, and a live response:

    POST https://inference.baseten.co/v1/systemone
    Authorization: Bearer $BASETEN_API_KEY
    {"model": "decision-models/baseten-mammoth-27b-latest",
     "state": {"about": <what the evidence is>, "evidence": [<each item's content; a Source as its passages>]},
     "questions": {<kind>: {"type": "noul", "instructions": <statement>}, ...}}
    -> {"model": <the backend model>, "answers": {<kind>: {"type": "noul", "noul": p}, ...},
        "usage": {"input_tokens", "output_tokens"}}

The state may be a structured object; the API renders it as text for the model. Each question's
whole prompt, the state included, must fit the model's context, 16,384 tokens, and the state
50,000 characters. The API rejects a larger request with 400, 413, or 422 rather than truncate
it, so the state's size is checked before asking: evidence over the limit is not sent, and a
rejection is no answer. Neither is cached, so a changed input asks again. 429 (rate limit or
capacity) and server errors are retried with backoff.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

URL = "https://inference.baseten.co/v1/systemone"
MODEL = "decision-models/baseten-mammoth-27b-latest"
STATE_LIMIT = 50_000  # characters of the state

# What the state is, said once for every request: part of the state, and part of every key.
ABOUT = ("The state is what is known on the date it gives: the texts each question compares. A proposition is a "
         "claim about the world as an analyst wrote it. A prediction market is given with its own question, its "
         "resolution rules, its resolution source, its close date, and whether it is still open on that date. A "
         "passage is quoted word for word from a page, with the page's address and date. Each question asks one "
         "thing about these texts. Answer it from the texts and what is generally known on that date.")


class JudgeError(Exception):
    """The judge gave no answer: the evidence is over the limit, the API rejected the request,
    or the service kept erring. Nothing is cached, and the next read asks again."""


@dataclass(frozen=True)
class Answer:
    """One check's answer: the probability the claim holds; None when the response holds no answer for it, with the
    reason; the raw response it came from; and the model that answered."""

    probability: float | None
    raw: str
    model: str
    reason: str | None = None


class Judge(Protocol):
    model: str  # the model asked
    models: tuple[str, ...] | None  # the models whose cached answers this judge accepts; None, any

    def answer_all(self, questions: list[tuple[str, str]], evidence: list[dict]) -> list[Answer]:
        """Each (kind, statement) over the same evidence, in one request."""


def state(evidence: list[dict]) -> dict:
    """The evidence as the judge reads it: what the state is, and each item's content."""
    return {"about": ABOUT, "evidence": [dict(item["content"]) if isinstance(item["content"], dict)
                                         else {"value": item["content"]} for item in evidence]}


def state_size(found: dict) -> int:
    """The state's length in characters, as compact JSON."""
    return len(json.dumps(found, ensure_ascii=False, separators=(",", ":")))


# ---------------------------------------------------------------- mammoth-1 through System One


class SystemOne:
    """Baseten's System One API: the evidence as state, each question a noul."""

    model = MODEL
    models = None  # one judge: its cached answers are the store's, whichever backend version gave them

    def __init__(self, key: str | None = None, *, opener: Callable | None = None, timeout: float = 120,
                 retries: int = 5, pause: float = 1.0):
        key = key if key is not None else os.environ.get("BASETEN_API_KEY", "")
        if not key:
            raise JudgeError("System One needs BASETEN_API_KEY")
        self._key = key
        self._open = opener or urllib.request.urlopen
        self.timeout, self.retries, self.pause = timeout, retries, pause

    @staticmethod
    def body(questions: list[tuple[str, str]], evidence: list[dict]) -> dict:
        return {"model": MODEL, "state": state(evidence),
                "questions": {kind: {"type": "noul", "instructions": statement} for kind, statement in questions}}

    def answer_all(self, questions: list[tuple[str, str]], evidence: list[dict]) -> list[Answer]:
        kinds = [q[0] for q in questions]
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"a request names each question once: {kinds}")
        body = self.body(questions, evidence)
        size = state_size(body["state"])
        if size > STATE_LIMIT:
            raise JudgeError(f"the texts this check compares run to {size:,} characters, more than the judge reads at "
                             f"once ({STATE_LIMIT:,}): fewer or shorter passages bring them within it")
        status, found, raw = self._post(body)
        if status in (413, 422):  # a request larger than the model's context is rejected, never truncated
            raise JudgeError("the texts this check compares are more than the judge reads at once: fewer or shorter "
                             "passages bring them within it")
        if status != 200:
            raise JudgeError("the judge could not answer this check just now; the next read asks again")
        model = found.get("model") or MODEL
        answers = found.get("answers") or {}
        out = []
        for kind in kinds:
            a = answers.get(kind)
            p = a.get("noul") if isinstance(a, dict) and a.get("type") == "noul" else None
            if isinstance(p, (int, float)) and not isinstance(p, bool):
                out.append(Answer(float(p), raw, model))
            else:
                out.append(Answer(None, raw, model, reason="the judge gave no answer to this check; the next read asks "
                                                           "again"))
        return out

    def _post(self, body: dict) -> tuple[int, Any, str]:
        request = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
            "User-Agent": "event-risk"})
        attempt = 0
        while True:
            try:
                with self._open(request, timeout=self.timeout) as response:
                    text = response.read().decode("utf-8")
                    return response.status, json.loads(text), text
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = {}
                if not (e.code == 429 or e.code >= 500) or attempt >= self.retries:
                    return e.code, parsed, text
                attempt += 1
                after = e.headers.get("Retry-After") if e.headers is not None else None
                try:
                    wait = min(float(after), 60.0)
                except (TypeError, ValueError):
                    wait = min(self.pause * 2 ** attempt, 60.0)
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError):
                if attempt >= self.retries:
                    raise JudgeError("the judge could not be reached just now; the next read asks again") from None
                attempt += 1
                time.sleep(min(self.pause * 2 ** attempt, 60.0))


# ---------------------------------------------------------------- the fixture


class FixtureJudge:
    """Answers from a table, or by a default rule, and names its model `fixture`: a row that names a judge is one that
    judge answered. A table entry of None is a check left unanswered. A key is (kind, statement) unless `key` says
    otherwise, given (kind, statement, evidence)."""

    model = "fixture"
    models = ("fixture",)

    def __init__(self, answers: dict | None = None, *, default: Callable | None = None,
                 key: Callable[..., Any] = lambda kind, statement, evidence: (kind, statement)):
        self.answers = dict(answers or {})
        self.default = default
        self.key = key
        self.calls: list[Any] = []  # one key per check asked
        self.asked: list[list[dict]] = []  # the evidence of each check asked
        self.requests: list[list[Any]] = []  # the keys asked together, one list per request

    def answer_all(self, questions: list[tuple[str, str]], evidence: list[dict]) -> list[Answer]:
        out, keys = [], []
        for kind, statement in questions:
            k = self.key(kind, statement, evidence)
            self.calls.append(k)
            self.asked.append(evidence)
            keys.append(k)
            if k in self.answers:
                p = self.answers[k]
            elif self.default is not None:
                p = self.default(kind, statement, evidence)
            else:
                raise JudgeError(f"the fixture has no answer for {k}")
            raw = json.dumps({"fixture": True, "key": list(k) if isinstance(k, tuple) else k, "answer": p})
            out.append(Answer(None, raw, self.model, reason=f"the fixture leaves {k} unanswered") if p is None
                       else Answer(float(p), raw, self.model))
        self.requests.append(keys)
        return out


def default_judge() -> Judge | None:
    """mammoth-1 when BASETEN_API_KEY is set; otherwise none, and a read answers from the cache alone."""
    return SystemOne() if os.environ.get("BASETEN_API_KEY") else None
