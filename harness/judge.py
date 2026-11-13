"""The judge: Base Labs' mammoth-1, a decision model, through Baseten's System One API.

The judge checks claims about texts: that a market asks exactly a proposition, or that one proposition implies
another. Each check is a `noul`, a typed predicate whose answer is the probability it holds, returned as a typed
field: nothing parses a number out of text. Its state is plain text: what the texts are, the situation in sentences of
fact (today's date, and that the market is open and has not resolved), and each text under plain labels; its question
compares what the texts say. Checks over the same evidence share one request (a market's check forward and converse);
each is still its own Judgment, cached on its own key, the hash of its question and the state. A question is (kind,
statement).

The request, as verified against https://docs.baseten.co/reference/inference-api/system-one and
https://docs.baseten.co/inference/model-apis/decision-models, and a live response:

    POST https://inference.baseten.co/v1/systemone
    Authorization: Bearer $BASETEN_API_KEY
    {"model": "decision-models/baseten-mammoth-27b-latest",
     "state": <the texts and the situation, as plain text (state)>,
     "questions": {<kind>: {"type": "noul", "instructions": <statement>}, ...}}
    -> {"model": <the alias asked>, "answers": {<kind>: {"type": "noul", "noul": p}, ...},
        "usage": {"input_tokens", "output_tokens"}}

The response's body names the model only by the alias asked. Its headers, which neither page documents, name the
deployment that answered: observed live, X-Baseten-Model-Id and X-Baseten-Model-Version-Id, short opaque ids, the
same on every request across the replicas that answered, beside per-request ids, timings, and rate limits. A
Judgment's model is the alias with that version, <alias>@<version id>, so answers from different deployments are told
apart; where the header is missing it is the alias alone. A cached answer is reused whichever deployment gave it.

The state may be plain text, a structured object, or a conversation; it is given as plain text, the form the
documentation shows for a noul, a yes/no question about the state. Each question's
whole prompt, the state included, must fit the model's context, 16,384 tokens, and the state
50,000 characters. The API rejects a larger request with 400, 413, or 422 rather than truncate
it, so the state's size is checked before asking: evidence over the limit is not sent, and a
rejection is no answer. Neither is cached, so a changed input asks again. 429 (rate limit or
capacity) and server errors are retried with backoff, and so is a request the network fails or a connection dropped
or cut short while the answer is read (harness.venues.http.TRANSIENT); one that fails on every try is no answer, and
the next read asks again.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol

from harness.venues.http import TRANSIENT

URL = "https://inference.baseten.co/v1/systemone"
MODEL = "decision-models/baseten-mammoth-27b-latest"
STATE_LIMIT = 50_000  # characters of the state

# What the texts are, said first in every state: part of the state, and so of every key.
ABOUT_MARKET = ("The texts below are a proposition, as an analyst wrote it, and a prediction market, as its venue words "
                "it.")
ABOUT_PAIR = "The texts below are two propositions, as an analyst wrote them."


class JudgeError(Exception):
    """The judge gave no answer just now: the API rejected the request or the service kept erring. Nothing is cached,
    and the next read asks again."""


class TooLong(Exception):
    """The texts a check compares are more than the judge reads at once: no judge here answers it, whenever asked."""


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


def plain_date(day: str) -> str:
    """A date as words: 2026-11-08 is November 8, 2026."""
    d = date.fromisoformat(str(day)[:10])
    return f"{d:%B} {d.day}, {d.year}"


def _situation(day: str, market: dict | None) -> str:
    """Today's date, and where a market is read, whether it is open."""
    out = f"Today is {plain_date(day)}."
    if market is not None and "still open" in market:
        out += " The market is open and has not resolved." if market["still open"] else " The market is closed."
    return out


def proposition_text(c: dict, heading: str) -> str:
    """The one rendering of a proposition that the judge reads: under plain labels, when it is decided as its author would say it (true if it happens at any time in
    its window, true if it holds on its window's end, or decided at the scheduled decision then), and a level on a metric as its threshold and the
    metric it is on: its name, unit, and publisher; or, for a proposition that occurs, the occurrence it names."""
    lines = [heading, f"Title: {c['proposition']}", f"Text: {c.get('says')}"]
    start, end, judged = c.get("window starts"), c.get("window ends"), c.get("judged")
    if end and judged == "at any time in its window":
        lines.append(f"It is true if this happens at any time from {plain_date(start)} to {plain_date(end)}." if start
                     else f"It is true if this happens at any time through {plain_date(end)}.")
    elif end and judged == "at the scheduled decision on its window's end":
        lines.append(f"It is decided at the scheduled decision on {plain_date(end)}.")
    elif end:
        lines.append(f"It is true if this holds on {plain_date(end)}.")
    if c.get("level"):
        lines.append(f"Threshold: {c['level']}")
    of = c.get("of")
    if isinstance(of, dict) and of.get("occurrence"):  # what happens, as the proposition's metric names it
        lines.append(f"Occurrence: {of['metric']}" + (f", reported by {of['published by']}" if of.get("published by")
                                                       else ""))
    elif isinstance(of, dict) and of.get("metric"):
        lines.append(f"Metric: {of['metric']}" + (f" ({of['unit']})" if of.get("unit") else "")
                     + (f", published by {of['published by']}" if of.get("published by") else ""))
    if c.get("resolved by"):
        lines.append(f"Resolution source: {c['resolved by']}")
    return "\n".join(lines)


def _market(c: dict) -> str:
    """A market as its venue words it, under plain labels: its event, question, rules, resolution source (for Kalshi, the
    settlement sources it lists for the market's event, or else its series'), and close."""
    venue = c.get("venue") or "its venue"
    lines = [f"The prediction market, as {venue} words it:"]
    if c.get("event"):
        lines.append(f"Event: {c['event']}")
    lines.append(f"Question: {c.get('market')}")
    if c.get("rules"):
        lines.append(f"Rules: {c['rules']}")
    if c.get("resolved by"):
        lines.append(f"Resolution source: {c['resolved by']}")
    if c.get("closes"):
        lines.append(f"It closes on {plain_date(c['closes'])}.")
    return "\n".join(lines)


def _other(c: Any) -> str:
    return "\n".join(f"{k}: {v}" for k, v in c.items()) if isinstance(c, dict) else str(c)


def state(evidence: list[dict]) -> str:
    """The evidence as the judge reads it, as plain text: what the texts are; the situation; then each text, the
    proposition before the market, or the first proposition before the second."""
    contents = [item["content"] for item in evidence]
    market = next((c for c in contents if isinstance(c, dict) and "market" in c), None)
    day = next((c["date"] for c in contents if isinstance(c, dict) and c.get("date")), None)
    props = [c for c in contents if isinstance(c, dict) and isinstance(c.get("proposition"), str)]
    headings = iter(["The proposition, as an analyst wrote it:"] if market is not None or len(props) != 2 else
                    ["The first proposition, as an analyst wrote it:", "The second proposition, as an analyst wrote it:"])
    parts = [ABOUT_MARKET if market is not None else ABOUT_PAIR] + ([_situation(day, market)] if day else [])
    for c in contents:
        if isinstance(c, dict) and set(c) == {"date"}:
            continue
        if c is market:
            parts.append(_market(c))
        elif any(c is x for x in props):
            parts.append(proposition_text(c, next(headings, "A proposition, as an analyst wrote it:")))
        else:
            parts.append(_other(c))
    return "\n\n".join(parts)


def state_size(found: str) -> int:
    """The state's length in characters."""
    return len(found)


# ---------------------------------------------------------------- mammoth-1 through System One


class SystemOne:
    """Baseten's System One API: the evidence as state, each question a noul."""

    model = MODEL
    models = None  # one judge: its cached answers are the store's, whichever deployment gave them

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
            raise TooLong(f"the texts this check compares run to {size:,} characters, more than the judge reads at "
                          f"once ({STATE_LIMIT:,})")
        status, found, raw, version = self._post(body)
        if status in (413, 422):  # a request larger than the model's context is rejected, never truncated
            raise TooLong("the texts this check compares are more than the judge reads at once")
        if status != 200:
            raise JudgeError("the judge could not answer this check just now; the next read asks again")
        model = found.get("model") or MODEL
        if version:
            model = f"{model}@{version}"  # the deployment that answered
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

    def _post(self, body: dict) -> tuple[int, Any, str, str | None]:
        """The response's status, its parsed body, its text, and the version of the deployment that answered, from
        its headers, when they name one."""
        request = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
            "User-Agent": "event-risk"})
        attempt = 0
        while True:
            try:
                with self._open(request, timeout=self.timeout) as response:
                    text = response.read().decode("utf-8")
                    headers = getattr(response, "headers", None)
                    version = headers.get("X-Baseten-Model-Version-Id") if headers is not None else None
                    return response.status, json.loads(text), text, (version or "").strip() or None
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = {}
                if not (e.code == 429 or e.code >= 500) or attempt >= self.retries:
                    return e.code, parsed, text, None
                attempt += 1
                after = e.headers.get("Retry-After") if e.headers is not None else None
                try:
                    wait = min(float(after), 60.0)
                except (TypeError, ValueError):
                    wait = min(self.pause * 2 ** attempt, 60.0)
                time.sleep(wait)
            except TRANSIENT:  # the network failing, or the connection dropped or cut short while the answer is read
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
