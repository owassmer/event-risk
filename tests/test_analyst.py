"""The analyst's documents and runtime: TOOLS.md gives every tool's signature; and a short conversation runs through
the SDK's tool runner against a scripted Messages API, every tool called, its trace the conversation, and
every number in it re-derived from the store with no judge, no keys, and no network."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from conftest import paid, fixture_judge
from harness.analyst import DOCUMENTS, MODEL, Analyst, system_prompt
from harness.derive import default_registry
from harness.ledger import ledger
from harness.store import Store
from harness.tools import SPECS, signature
from harness.trace import Trace
from harness.venues import Venues
from harness.web import Web
from harness.world import World
from massive_data import ASOF, KNOWLEDGE, closes_from, put_bars, put_market, put_reference, random_returns
from test_markets import Opener, adapter, kalshi_route, polymarket_route
from breakdown import stated

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- the documents


def test_tools_md_gives_each_tools_signature_once():
    headings = re.findall(r"^### `([a-z_]+\(.*\))`$", (KNOWLEDGE / "TOOLS.md").read_text(), re.M)
    assert sorted(headings) == sorted(signature(n) for n in SPECS)


def test_the_system_prompt_is_system_md_then_the_four_documents_cached_at_the_end():
    blocks = system_prompt()
    assert [b["text"] for b in blocks] == [(KNOWLEDGE / d).read_text() for d in DOCUMENTS]
    assert DOCUMENTS == ("system.md", "MATH.md", "ONTOLOGY.md", "TOOLS.md", "DATA.md")
    assert [("cache_control" in b) for b in blocks] == [False, False, False, False, True]


# ---------------------------------------------------------------- a scripted conversation


PAGE = ("Published October 30, 2026\n\nThe effective tariff rate is 22 percent, and the Budget Lab expects it to rise. "
        "On October 1 after the close, the White House announced new tariffs.")


def web_route(method, path, query, body):
    """Anthropic's server tools, as the web client calls them: a search, and a fetch of PAGE."""
    assert path == "api.anthropic.com/v1/messages"
    if body["tools"][0]["type"].startswith("web_search"):
        return 200, {"stop_reason": "end_turn", "content": [
            {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {"query": "tariff rate 2027"}},
            {"type": "web_search_tool_result", "tool_use_id": "srv1", "content": [
                {"type": "web_search_result", "url": "https://example.org/rate", "title": "Rate",
                 "page_age": "2026-10-30"}]},
            {"type": "text", "text": "The rate.", "citations": [
                {"type": "web_search_result_location", "url": "https://example.org/rate", "title": "Rate",
                 "cited_text": "The effective tariff rate is 22 percent"}]}]}
    return 200, {"stop_reason": "end_turn", "content": [
        {"type": "web_fetch_tool_result", "content": {"type": "web_fetch_result", "url": "https://example.org/rate",
                                                      "retrieved_at": "2026-11-04T20:00:00Z",
                                                      "content": {"type": "document", "title": "Rate",
                                                                  "source": {"type": "text", "media_type": "text/plain",
                                                                             "data": PAGE}}}},
        {"type": "text", "text": "OK"}]}


class Data:
    """Massive's fetchers over a store of synthetic data: each kind and subject answered from its row."""

    def __init__(self, source: Store):
        self.source = source
        self.calls: list[tuple[str, str]] = []

    def fetcher(self, kind):
        def fetch(subject, covering):
            with self.source.lock:  # one connection, read from the store's reads started ahead too
                self.calls.append((kind, subject))
                r = self.source._one("SELECT id FROM data WHERE kind = ? AND subject = ? ORDER BY rowid DESC LIMIT 1",
                                     kind, subject)
                if r is None:
                    return {"payload": {"status": "NOT_FOUND"}, "request": {"kind": kind, "subject": subject},
                            "retrieved_at": "2026-11-05T02:00:00+00:00", "range": tuple(covering or (ASOF, ASOF))}
                row = self.source._data_row(r["id"])
                return {"payload": self.source.data_payload(r["id"]), "request": row["request"],
                        "retrieved_at": row["retrieved_at"], "range": (row["range_from"], row["range_to"])}
        return fetch

    def fetchers(self):
        return {k: self.fetcher(k) for k in ("ticker_ref", "bars", "hourly", "dividends", "constituents")}


def use(name, **input):
    return {"type": "tool_use", "id": f"toolu_{name}_{abs(hash(json.dumps(input, sort_keys=True))) % 10**6}",
            "name": name, "input": input}


def say(text):
    return {"type": "text", "text": text}


class Script:
    """The Messages API: each request answered by the script's next reply, written from the store as it
    stands, as the model writes its calls from what it has read. A reply is its content, which stops to call tools
    where it holds a call and ends its turn otherwise; or {"content", "stop_reason", "stop_details"}, a response that
    stops as it says, sent with its stop_details on the message_delta event as the API streams them."""

    def __init__(self, store, replies):
        self.store, self.replies, self.requests, self.headers = store, list(replies), [], []

    def __call__(self, request):
        import httpx2
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        content = self.replies.pop(0)(self.store)
        stop, details = None, None
        if isinstance(content, dict):
            content, stop, details = content["content"], content["stop_reason"], content.get("stop_details")
        stop = stop or ("tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn")
        message = {"id": f"msg_{len(self.requests)}", "type": "message", "role": "assistant", "model": body["model"],
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": 1000, "output_tokens": 1, "cache_read_input_tokens": 900}}
        events = [("message_start", {"type": "message_start", "message": message})]
        for i, b in enumerate(content):  # each block streamed as the API streams it
            start = {"text": {"type": "text", "text": ""}, "thinking": {"type": "thinking", "thinking": "", "signature": ""},
                     "tool_use": {**b, "input": {}}}[b["type"]]
            events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": start}))
            if b["type"] == "text":
                deltas = [{"type": "text_delta", "text": b["text"][:10]}, {"type": "text_delta", "text": b["text"][10:]}]
            elif b["type"] == "thinking":
                deltas = [{"type": "thinking_delta", "thinking": b["thinking"]},
                          {"type": "signature_delta", "signature": b["signature"]}]
            else:
                deltas = [{"type": "input_json_delta", "partial_json": json.dumps(b["input"])}]
            events += [("content_block_delta", {"type": "content_block_delta", "index": i, "delta": d}) for d in deltas]
            events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        events.append(("message_delta", {"type": "message_delta", "delta": {
            "stop_reason": stop, "stop_sequence": None, **({"stop_details": details} if details else {})},
                                         "usage": {"output_tokens": 50}}))
        events.append(("message_stop", {"type": "message_stop"}))
        sse = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
        return httpx2.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})


def page_id(store):
    return next(sid for sid, p in store._pending.items() if p["url"] == "https://example.org/rate") \
        if any(p["url"] == "https://example.org/rate" for p in store._pending.values()) else \
        store._one("SELECT id FROM source WHERE url = 'https://example.org/rate'")["id"]


T = {"title": "T", "text": "The US effective tariff rate reaches 25% by March 31, 2027",
     "metric": "metric:us_effective_tariff_rate_pct", "comparison": ">=", "value": 25.0,
     "window_start": ASOF, "window_end": "2027-03-31", "kind": "hitting", "baseline": "no"}
N = {"title": "N", "text": "NVDA closes at or below $100 on March 31, 2027", "metric": "metric:price:NVDA",
     "comparison": "<=", "value": 100.0, "window_end": "2027-03-31", "kind": "level_at_date"}

TURN_1 = [
    lambda s: [{"type": "thinking", "thinking": "The person holds NVDA and cash; the store prices them.",
                "signature": "c2lnbmF0dXJl"},
               say("I'll add the portfolio."), use("add", type="Portfolio", fields={
        "asof": ASOF, "holdings": [{"ticker": "NVDA", "dollars": 60000.0}, {"cash": 40000.0}]})],
    lambda s: [use("exposure"), use("search_web", text="US effective tariff rate 2027"),
               use("fetch_source", url="https://example.org/rate")],
    lambda s: [use("edit", id=page_id(s), field="dated", value="2026-10-30", source_id=page_id(s)),
               use("add", type="Metric", fields={
                   "id": "us_effective_tariff_rate_pct", "name": "the US effective tariff rate", "unit": "percent", "resolution_source": "Yale Budget Lab",
                   "kind": "level", "level": {"value": 22.0, "source_id": page_id(s)},
                   "asof": "2026-10-30"})],
    lambda s: [use("add", type="Proposition", fields=T), use("add", type="Proposition", fields=N),
               use("link", from_id="prop:1", relation="asks", to_id="market:polymarket:516710"),
               use("link", from_id="prop:1", relation="evidence", to_id=page_id(s),
                   fields={"passages": ["The effective tariff rate is 22 percent"]}),
               use("link", from_id="prop:1", relation="consequence", to_id="name:NVDA",
                   fields={"shock": stated(-0.2)}),
               use("note", text="The tariff rate is the largest risk to the semiconductor holding.",
                   about_ids=["prop:1"]),
               use("link", from_id="portfolio", relation="watches", to_id="prop:1"),
               use("search_markets", text="tariff",
                   proposition_id="prop:1")],
    lambda s: [use("ledger"), use("check"), use("window_returns", start="2026-10-01", end="2026-10-02"),
               use("query", type="Proposition", where={"title": "N"}),
               use("neighbors", id="prop:1", relation="asks")],
    lambda s: [say("The tariff rate reaching 25% is the event that matters most here.")],
]
TURN_2 = [
    lambda s: [use("get", id="prop:1"), use("remove", id="note:2"), use("get", id="prop:99")],
    lambda s: [say("The chance comes from the market quoting the rate above 20%.")],
]


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    from harness.credentials import SECRET_ENV
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    import anthropic
    import httpx2

    rng = np.random.default_rng(13)
    source = Store.create(tmp_path / "massive-data")
    put_market(source, rng)
    put_reference(source, "NVDA", "CS", sic="3674", name="NVIDIA Corp")
    put_bars(source, "NVDA", closes_from(random_returns(rng, 0.02)))
    data = Data(source)
    store = Store.create(tmp_path / "scratch-conversation", initial=KNOWLEDGE, registry=default_registry(),
                         judge=fixture_judge(), fetchers=data.fetchers())
    (pm, pm_seen), (ks, ks_seen) = adapter("polymarket", polymarket_route), adapter("kalshi", kalshi_route)
    web = Opener(web_route)
    world = World(store, Venues({"polymarket": pm, "kalshi": ks}, paid()), Web(key="test", opener=web), today=lambda: ASOF)
    script = Script(store, TURN_1 + TURN_2)
    client = anthropic.Anthropic(api_key="sk-test", http_client=httpx2.Client(transport=httpx2.MockTransport(script)))
    events = []
    analyst = Analyst(world, client=client, on_event=events.append)
    first = analyst.say("I hold NVDA $60,000 and $40,000 in cash. What could hit it?")
    second = analyst.say("Why that chance?")
    seen = {"polymarket": pm_seen.seen, "kalshi": ks_seen.seen, "web": web.seen,
            "massive": data.calls}
    yield {"store": store, "analyst": analyst, "script": script, "turns": [first, second], "seen": seen,
           "tmp": tmp_path, "events": events}
    store.close()
    source.close()


def test_the_runner_sends_the_system_prompt_and_the_fifteen_tools_and_every_result(conversation):
    script = conversation["script"]
    first = script.requests[0]
    assert first["model"] == MODEL and first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first["stream"] is True and first["max_tokens"] == 128_000
    assert [b["text"] for b in first["system"]] == [b["text"] for b in system_prompt()]
    # today's date leads the person's message (the world's clock, the fixture's as-of), so the system text is the same
    # for the whole conversation
    assert first["messages"][0]["content"][0] == {"type": "text", "text": "Today is Wednesday, 2026-11-04, in New York."}
    assert all(request["system"] == first["system"] for request in script.requests)
    assert sorted(t["name"] for t in first["tools"]) == sorted(SPECS)
    # both breakpoints cached for an hour: a turn can wait on one call, and a member on a reply, past five minutes, and
    # the API refuses a request whose breakpoints differ in how long they keep
    assert first["cache_control"] == first["system"][4]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert next(t for t in first["tools"] if t["name"] == "link")["input_schema"]["required"] == \
        ["from_id", "relation", "to_id"]
    for request in script.requests[1:]:
        last = request["messages"][-1]
        if last["role"] == "user" and last["content"][0]["type"] == "tool_result":
            used = [b["id"] for b in request["messages"][-2]["content"] if b["type"] == "tool_use"]
            assert [b["tool_use_id"] for b in last["content"]] == used  # every call's result goes back
    final = script.requests[-1]["messages"]
    results = [b for m in final if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]
               if b["type"] == "tool_result"]
    assert [b.get("is_error", False) for b in results] == [False] * 21 + [True]  # get("prop:99") is an error
    assert json.loads(results[-1]["content"][0]["text"] if isinstance(results[-1]["content"], list)
                      else results[-1]["content"])["error"] == "prop:99 is not in the store"


def test_every_tool_is_called_and_answers(conversation):
    first, second = conversation["turns"]
    called = [c["tool"] for c in first["calls"] + second["calls"]]
    assert set(called) == set(SPECS) - {"search_company"}
    assert [c["ok"] for c in first["calls"] + second["calls"]] == [True] * 21 + [False]
    assert first["text"].startswith("The tariff rate") and first["requests"] == 6 and second["requests"] == 2
    assert first["usage"]["input_tokens"] == 6000 and second["usage"]["output_tokens"] == 100
    store = conversation["store"]
    # The as-of is past: Polymarket's history is a price a day, no quote, so the market reads the book the store kept on
    # the as-of day (harness.derive.markets.past_price).
    from test_markets import PM_BOOK
    store.put_data("book", "market:polymarket:516710", PM_BOOK, {"method": "books"},
                   retrieved_at=f"{ASOF}T20:00:00+00:00", range=(ASOF, ASOF))
    row = ledger(store)["rows"][0]
    assert row["id"] == "prop:1" and row["markets"][0]["market"] == "market:polymarket:516710"
    returned = [r["id"] for r in store.db.execute("SELECT id, results FROM search WHERE tool = 'search_markets'")
                if "market:polymarket:516710" in json.loads(r["results"])]
    # The web search ran before T existed: it counts for T through the page it returned that T uses, beside the
    # search recorded on T.
    recorded = [r["id"] for r in store.db.execute("SELECT id FROM search WHERE proposition_id = 'prop:1'")]
    assert [q["search"] for q in row["searches"]["queries"]] == returned == recorded
    assert row["searches"]["web_searches"] == 1 and row["searches"]["web_queries"][0]["proposition"] is None
    assert row["searches"]["sources_linked"] == 1 and row["consequences"][0]["target"] == "name:NVDA"
    assert row["consequences"][0]["shock"]["value"] == -0.2 and row["consequences"][0]["levels"] is not None
    assert row["d"] and "name:NVDA" in row["d"]


def test_the_trace_holds_the_conversation_every_call_and_what_the_world_answered(conversation):
    entries = Trace(conversation["store"].trace_path).entries()
    kinds = [e["type"] for e in entries]
    assert kinds[0] == "session" and kinds.count("user") == 2 and kinds.count("assistant") == 8
    assert kinds.count("results") == 6  # one per reply that called tools
    session = entries[0]
    assert session["model"] == MODEL and session["judge"] == "fixture" and "bars" in session["fetchers"]
    calls = [e for e in entries if e["type"] == "call"]
    assert [c["call"] for c in calls] == list(range(1, 23)) and all(c["session"] == session["session"] for c in calls)
    by_tool = {}
    for c in calls:
        by_tool.setdefault(c["tool"], c)  # each tool's first call
    assert not any("world" in c for c in calls)  # the conversation, and no payloads of the world
    assert by_tool["fetch_source"]["result"]["excerpt"].startswith("Published")
    assert calls[-1]["tool"] == "get" and calls[-1]["error"]["error"] == "prop:99 is not in the store"
    assistant = [e for e in entries if e["type"] == "assistant"]
    assert assistant[0]["content"][0] == {"type": "thinking", "signature": "c2lnbmF0dXJl",
                                          "thinking": "The person holds NVDA and cash; the store prices them."}
    assert assistant[0]["content"][2]["name"] == "add" and assistant[0]["usage"]["input_tokens"] == 1000
    added = next(c["call"] for c in calls if c["tool"] == "add" and c["input"]["type"] == "Proposition")
    row = conversation["store"].db.execute("SELECT created_call FROM object WHERE id = 'prop:1'").fetchone()
    assert row[0] == added  # each row the store writes names the call that wrote it


def test_a_reader_replays_the_conversation_from_its_trace_and_every_number_re_derives_with_no_keys(conversation):
    store = conversation["store"]
    entries = Trace(store.trace_path).entries()
    seen = {k: len(v) for k, v in conversation["seen"].items()}
    # Every message and every call, in order: what a reader reads.
    said = [e["text"] for e in entries if e["type"] == "user"]
    assert said == ["I hold NVDA $60,000 and $40,000 in cash. What could hit it?", "Why that chance?"]
    results = {r["tool_use_id"]: r for e in entries if e["type"] == "results" for r in e["results"]}
    uses = [b for e in entries if e["type"] == "assistant" for b in e["content"] if b["type"] == "tool_use"]
    calls = {e["call"]: e for e in entries if e["type"] == "call"}
    assert [calls[results[u["id"]]["call"]]["input"] for u in uses] == [u["input"] for u in uses]
    # The store, opened with no judge, no venues, and no fetchers: every number is the one the conversation saw.
    before = json.loads(json.dumps(ledger(store), default=str))
    again = Store.open(store.dir, registry=default_registry())
    try:
        assert again.engine.judge is None and again.fetchers == {} and again.venues is None
        assert json.loads(json.dumps(ledger(again), default=str)) == before
        from harness.ledger import event_read
        for e in calls.values():
            if e["tool"] == "get" and "result" in e:
                oid = e["input"]["id"]  # a watched event is read as it now stands, as get read it
                found = event_read(again, oid) if again.type_of(oid) == "Proposition" and again.watched(oid) \
                    else again.get(oid)
                assert found["derived"] == json.loads(json.dumps(e["result"]["derived"]))
    finally:
        again.close()
    assert {k: len(v) for k, v in conversation["seen"].items()} == seen  # nothing reached the world


def test_a_conversation_continues_from_its_trace(conversation):
    analyst = conversation["analyst"]
    again = Analyst(analyst.world, client=analyst.client, session=analyst.session)
    assert again.messages == analyst.messages and again.turn == 2
    assert [e["type"] for e in Trace(conversation["store"].trace_path).entries()].count("session") == 1


def test_the_events_stream_each_block_and_tie_each_call_to_its_tool_use(conversation):
    events = conversation["events"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "user" and kinds[1] == "reply_start" and kinds[-1] == "answer"
    deltas = [e for e in events if e["type"] == "delta"]
    assert {d["kind"] for d in deltas} == {"thinking", "text", "input"}
    thought = "".join(d["delta"] for d in deltas if d["kind"] == "thinking")
    assert thought == "The person holds NVDA and cash; the store prices them."
    starts = {e["block"]["id"]: e["block"]["name"] for e in events if e["type"] == "block_start"
              and e["block"]["type"] == "tool_use"}
    calls = [e for e in events if e["type"] == "call"]
    assert len(calls) == 22 and all(starts[c["tool_use_id"]] == c["tool"] for c in calls)
    assert [e["tool_use_id"] for e in events if e["type"] == "call_start"] == [c["tool_use_id"] for c in calls]
    stops = [e["block"] for e in events if e["type"] == "block_stop"]
    assert stops[0]["type"] == "thinking" and stops[2]["type"] == "tool_use" and stops[2]["input"]["type"] == "Portfolio"
