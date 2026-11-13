"""The runtime through the tool runner against a scripted Messages API: checkpoints and rewind,
edit and retry, stop, dropped files, effort, and the credential. Every test runs with no keys and no network."""

from __future__ import annotations

import base64
import json

import numpy as np
import pytest

from conftest import fixture_judge, masked, paid
from harness import claude
from harness.analyst import (NOT_RUN, Analyst, FileRefused, history,
                             keep_file, live)
from harness.derive import default_registry
from harness.ledger import check, ledger
from harness.store import Store, StoreError
from harness.trace import Trace
from harness.venues import Venues
from harness.web import Web
from harness.world import World
from massive_data import ASOF, KNOWLEDGE, closes_from, put_bars, put_market, put_reference, random_returns
from harness.analyst import MODEL, system_prompt
from test_analyst import TURN_1, Data, Script, page_id, say, use, web_route
from test_markets import Opener, adapter, kalshi_route, polymarket_route

FIRST = "I hold NVDA $60,000 and $40,000 in cash. What could hit it?"
# Today's date, as it leads each message the person sends: the world's clock at the fixture's as-of
DATE = {"type": "text", "text": "Today is Wednesday, 2026-11-04, in New York."}


def spoken(message: dict) -> str | None:
    """The person's words in a message they sent, which begins with today's date; None for a message of results."""
    content = message["content"]
    return content[-1]["text"] if isinstance(content, list) and content and content[0] == DATE else None


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A store and its world over synthetic Massive data, stub venues, and the fixture judge; `start(replies)`
    gives an analyst answering from the script."""
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
    judge = fixture_judge()
    store = Store.create(tmp_path / "scratch-runtime", initial=KNOWLEDGE, registry=default_registry(), judge=judge,
                         fetchers=data.fetchers())
    (pm, pm_seen), (ks, _) = adapter("polymarket", polymarket_route), adapter("kalshi", kalshi_route)
    web = Opener(web_route)
    w = World(store, Venues({"polymarket": pm, "kalshi": ks}, paid()), Web(key="test", opener=web), today=lambda: ASOF)
    out = {"store": store, "world": w, "judge": judge, "data": data, "venue": pm_seen, "web": web}

    def start(replies, credential=None, **kw):
        script = Script(store, replies)
        transport = httpx2.Client(transport=httpx2.MockTransport(script))
        client = claude.client(credential, http_client=transport) if credential else \
            anthropic.Anthropic(api_key="sk-test", http_client=transport)
        out["script"] = script
        return Analyst(w, client=client, credential=credential, **kw)

    out["start"] = start
    yield out
    store.close()
    source.close()


def reached(world: dict) -> tuple[int, ...]:
    """How often the judge, the data, the venue, and the web have been reached."""
    return len(world["judge"].calls), len(world["data"].calls), len(world["venue"].seen), len(world["web"].seen)


WRITTEN = ("object", "link", "market", "source", "search")


def state(store: Store) -> dict:
    """What the analyst has written, row for row, and every value the store derives from it: the ledger, check,
    and every object, link, kept market, and kept page as get returns it."""
    rows = {t: [tuple(r) for r in store.db.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in WRITTEN}
    ids = [r[0] for t in ("object", "link", "market", "source") for r in rows[t]]
    got = {i: store.get(i) for i in ids}
    return json.loads(json.dumps({"rows": rows, "asof": store.meta("asof"), "ledger": ledger(store),
                                  "check": check(store), "get": got}, default=str))


def quoted(s):
    return s.links_of("prop:1", "asks")[0]["id"]


def evidence(s):
    return s.links_of("prop:1", "evidence")[0]["id"]


U = {"title": "U", "text": "NVDA closes at or below $80 on March 31, 2027", "metric": "metric:price:NVDA",
     "comparison": "<=", "value": 80.0, "window_end": "2027-03-31", "kind": "level_at_date"}

# The second turn changes every kind of thing the analyst writes: a market released, an evidence link removed, a
# proposition's stated field and a metric's sourced level edited, a proposition added and watched, a note removed and
# another written, a consequence added, and a search recorded.
CHANGES = [
    lambda s: [use("remove", id=quoted(s)), use("remove", id=evidence(s)),
               use("edit", id="prop:1", field="value", value=30.0),
               use("edit", id="metric:us_effective_tariff_rate_pct", field="level", value=23.0,
                   source_id=page_id(s)),
               use("add", type="Proposition", fields=U), use("remove", id="note:1")],
    lambda s: [use("link", from_id="portfolio", relation="watches", to_id="prop:3"),
               use("link", from_id="prop:3", relation="consequence", to_id="name:NVDA",
                   fields={"value": -30.0}),
               use("note", text="A deeper fall in NVDA.", about_ids=["prop:3"]),
               use("search_markets", text="nvidia",
                   proposition_id="prop:3"),
               use("ledger")],
    lambda s: [say("I changed the tariff event and added a deeper NVDA fall.")],
]


def test_a_rewind_restores_what_the_analyst_wrote_and_reads_every_value_again_with_no_fetch_and_no_judge(world):
    store = world["store"]
    analyst = world["start"](TURN_1 + CHANGES)
    analyst.say(FIRST)
    before = state(store)
    analyst.say("Change it.")
    after = state(store)
    assert after != before
    assert store.type_of("market:polymarket:516710") is None  # released with its last link
    assert store.db.execute("SELECT count(*) FROM data WHERE subject = 'market:polymarket:516710'").fetchone()[0] > 0
    seen = reached(world)
    analyst.rewind(2, "edit")
    # every row as before the message, and every derived value, computed again where the cache no longer held it
    assert masked(state(store)) == masked(before)
    assert reached(world) == seen  # read again from the store's data and cache: no fetch, no judge, no venue
    # the conversation rewound with the store: turn 2's message and everything after it are gone from what the model
    # sees, and the trace keeps the replaced turn beside the rewind
    assert analyst.turn == 1 and analyst.messages[-1]["role"] == "assistant"
    entries = Trace(store.trace_path).entries()
    assert [e["turn"] for e in entries if e["type"] == "user"] == [1, 2]
    assert entries[-1] == {**entries[-1], "type": "rewind", "turn": 2, "reason": "edit"}
    assert [e["turn"] for e in live(entries, analyst.session) if e["type"] == "user"] == [1]
    assert history(entries, analyst.session, store.dir)[0] == analyst.messages
    assert store.checkpoints() == [analyst.checkpoint_key(1), analyst.checkpoint_key(2)]


def test_a_rewind_restores_a_citation_moved_between_pages_a_released_page_and_a_row_changed_twice(make_store):
    from conftest import snapshot
    store = make_store()
    one = store.put_page("https://example.org/one", "The rate is 22 percent.", retrieved_at="2026-11-04T15:00:00+00:00")
    two = store.put_page("https://example.org/two", "The rate is 23 percent.", retrieved_at="2026-11-04T16:00:00+00:00")
    store.add("Metric", {"id": "rate_pct", "name": "the rate", "unit": "percent", "kind": "level",
                         "level": {"value": 22.0, "source_id": one}})
    store.edit(one, "dated", "2026-10-30", one)
    store.checkpoint("s:1")
    before = snapshot(store)["tables"]
    store.edit("metric:rate_pct", "level", 23.0, two)  # the citation moves to page two; page one goes
    store.edit("metric:rate_pct", "level", 24.0, two)  # and the same row changes again
    store.edit(two, "dated", "2026-11-01", two)
    note = store.note("a note added and taken back in one turn", ["metric:rate_pct"])
    store.remove(note["id"])
    assert store.get(one)["kept"] is False and store.get("metric:rate_pct")["citations"] == {"level": two}
    store.rewind("s:1")
    after = snapshot(store)["tables"]
    for t in WRITTEN:
        assert after[t] == before[t], t
    # the as-of and the store's settings as they were; its count of versions goes on, the rewind a version of its own
    settings = lambda rows: [r for r in rows if r[0] != "version"]  # noqa: E731
    assert settings(after["meta"]) == settings(before["meta"])
    assert int(store.meta("version")) == store.version > int(dict(before["meta"])["version"])
    assert store.get(one)["text"] == "The rate is 22 percent." and store.get(one)["dated"] == "2026-10-30"
    assert store.get(one)["kept"] is True and one not in store._pending  # kept again, so not held
    assert store.get("metric:rate_pct")["citations"] == {"level": one}
    assert store.get(two)["kept"] is False  # a fetched page is never rewound: the one the checkpoint did not keep is held
    store.edit("metric:rate_pct", "level", 23.0, two)  # and citable under its id
    assert store.get(two)["kept"] is True


def test_rewinding_to_the_first_message_leaves_the_initial_contents_and_no_as_of(world):
    store = world["store"]
    channels = [tuple(r) for r in store.db.execute("SELECT * FROM object ORDER BY rowid")]
    analyst = world["start"](TURN_1)
    analyst.say(FIRST)
    assert store.meta("asof") == ASOF
    analyst.rewind(1, "edit")
    assert [tuple(r) for r in store.db.execute("SELECT * FROM object ORDER BY rowid")] == channels
    assert store.meta("asof") is None and store.db.execute("SELECT count(*) FROM link").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM data").fetchone()[0] > 0  # what was fetched stays
    assert analyst.messages == [] and analyst.turn == 0
    with pytest.raises(StoreError, match="no checkpoint"):
        store.rewind("nothing:1")


def test_an_edit_rewinds_and_sends_the_new_message_and_a_retry_sends_the_same_one_again(world):
    store = world["store"]
    again = [lambda s: [say("Once more.")]]
    analyst = world["start"](TURN_1 + CHANGES + again + again + again)
    analyst.say(FIRST)
    before = state(store)
    analyst.say("Change it.")
    analyst.say("And then?")
    assert store.checkpoints() == [analyst.checkpoint_key(t) for t in (1, 2, 3)]
    out = analyst.edit(2, "Tell me about the tariff event.")
    assert store.checkpoints() == [analyst.checkpoint_key(t) for t in (1, 2)]  # turn 3's went with its turn
    assert out["text"] == "Once more." and masked(state(store)) == masked(before)  # the edited turn wrote nothing
    sent = world["script"].requests[-1]["messages"]
    assert sent[-1]["role"] == "assistant" or spoken(sent[-1]) == "Tell me about the tariff event."
    assert [spoken(m) for m in sent if m["role"] == "user" and spoken(m)] == [FIRST, "Tell me about the tariff event."]
    analyst.retry(2)
    sent = world["script"].requests[-1]["messages"]
    assert [spoken(m) for m in sent if m["role"] == "user" and spoken(m)] == [FIRST, "Tell me about the tariff event."]
    entries = Trace(store.trace_path).entries()
    assert [(e["type"], e.get("reason")) for e in entries if e["type"] == "rewind"] == [("rewind", "edit"),
                                                                                     ("rewind", "retry")]
    assert [e["text"] for e in entries if e["type"] == "user"] == \
        [FIRST, "Change it.", "And then?", "Tell me about the tariff event.", "Tell me about the tariff event."]
    continued = Analyst(analyst.world, client=analyst.client, session=analyst.session)
    assert continued.messages == analyst.messages and continued.turn == 2


# ---------------------------------------------------------------- stop


def test_stop_ends_a_reply_that_has_gone_quiet_while_the_model_thinks(world):
    """Over a real socket: the reply starts, then nothing comes, as while the model thinks. Stop ends the turn at
    once; closing the stream alone leaves a read waiting on its socket until bytes arrive."""
    import socket
    import threading
    import time
    import anthropic

    quiet = threading.Event()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer():
        conn, _ = server.accept()
        conn.recv(65536)
        start = {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                                       "model": "m", "content": [], "stop_reason": None,
                                                       "usage": {"input_tokens": 1, "output_tokens": 1}}}
        conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n")
        chunk = f"event: message_start\ndata: {json.dumps(start)}\n\n".encode()
        conn.sendall(b"%x\r\n%s\r\n" % (len(chunk), chunk))
        quiet.wait(30)  # the model thinking: nothing more for as long as the test lasts
        conn.close()

    threading.Thread(target=answer, daemon=True).start()
    client = anthropic.Anthropic(api_key="sk-test", base_url=f"http://127.0.0.1:{server.getsockname()[1]}",
                                 max_retries=0)
    started = threading.Event()
    analyst = Analyst(world["world"], client=client,
                      on_event=lambda e: started.set() if e["type"] == "reply_start" else None)
    out = {}
    turn = threading.Thread(target=lambda: out.update(analyst.say("What could hit this?")), daemon=True)
    turn.start()
    assert started.wait(10)
    time.sleep(0.5)  # the read is waiting on the quiet socket
    t0 = time.monotonic()
    analyst.stop()
    turn.join(5)
    quiet.set()
    server.close()
    assert not turn.is_alive() and out["stopped"] is True and time.monotonic() - t0 < 5
    assert [e["type"] for e in Trace(world["store"].trace_path).entries()][-1] == "stop"


V = {"title": "V", "text": "A ruling by March 31, 2027", "comparison": "occurs", "window_start": ASOF,
     "window_end": "2027-03-31", "kind": "hitting"}


def test_stop_ends_the_turn_at_its_current_call_and_the_next_message_joins_the_calls_results(world):
    store = world["store"]
    batch = [lambda s: [use("add", type="Proposition", fields=V), use("query", type="Proposition"),
                        use("check")]]
    analyst = world["start"](batch + [lambda s: [say("Going on from the proposition.")]])
    events = []

    def on_event(e):
        events.append(e)
        if e["type"] == "call" and e["tool"] == "add":
            analyst.stop()

    analyst.on_event = on_event
    out = analyst.say("Add the event.")
    assert out["stopped"] and [(c["tool"], c["ok"]) for c in out["calls"]] == [("add", True)]
    assert store.type_of("prop:1") == "Proposition"  # what the turn wrote stays
    assert len(world["script"].requests) == 1  # no request after the stop
    results = analyst.messages[-1]["content"]
    assert [b.get("is_error", False) for b in results] == [False, True, True]
    assert all(NOT_RUN in json.dumps(b["content"]) for b in results[1:])
    entries = Trace(store.trace_path).entries()
    assert [e["type"] for e in entries][-2:] == ["results", "stop"]
    assert history(entries, analyst.session, store.dir)[0] == analyst.messages
    out = analyst.say("Go on.")
    assert out["text"] == "Going on from the proposition." and not out["stopped"]
    last = world["script"].requests[-1]["messages"][-1]  # the person's side: the results, then their message
    assert [b["type"] for b in last["content"]] == ["tool_result"] * 3 + ["text", "text"]
    assert last["content"][-2:] == [DATE, {"type": "text", "text": "Go on."}]
    continued = Analyst(analyst.world, client=analyst.client, session=analyst.session)
    assert continued.messages == analyst.messages


def test_stop_while_a_reply_streams_keeps_nothing_of_it_but_the_trace_shows_what_was_said(world):
    store = world["store"]
    analyst = world["start"]([lambda s: [say("The largest exposure is to the market as a whole.")],
                              lambda s: [say("Here it is again.")]])

    def on_event(e):
        if e["type"] == "delta" and e["kind"] == "text":
            analyst.stop()

    analyst.on_event = on_event
    out = analyst.say("What matters most?")
    assert out["stopped"] and out["text"] == ""
    entries = Trace(store.trace_path).entries()
    assert entries[-1]["type"] == "stop" and entries[-1]["partial"] == "The larges"
    assert not any(e["type"] == "assistant" for e in entries)
    assert analyst.messages == [{"role": "user", "content": [DATE, {"type": "text", "text": "What matters most?"}]}]
    analyst.on_event = lambda e: None
    out = analyst.say("Go on.")
    assert out["text"] == "Here it is again."
    assert world["script"].requests[-1]["messages"][0]["content"] == [
        DATE, {"type": "text", "text": "What matters most?"}, DATE, {"type": "text", "text": "Go on."}]
    # an edit of the stopped turn takes its message back out of the one it joined
    analyst.rewind(2, "edit")
    assert analyst.messages == [{"role": "user", "content": [DATE, {"type": "text", "text": "What matters most?"}]}]


# ---------------------------------------------------------------- files


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==")
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
CSV = "ticker,dollars\nNVDA,60000\nCASH,40000\n".encode()


def test_a_dropped_file_is_kept_beside_the_store_and_reaches_the_analyst_as_a_document_or_an_image(world):
    store = world["store"]
    analyst = world["start"]([lambda s: [say("I read the three files.")]])
    files = [keep_file(store.dir, "my holdings.csv", CSV), keep_file(store.dir, "Statement (Oct).pdf", PDF),
             keep_file(store.dir, "screen.png", PNG)]
    assert [f["path"] for f in files] == ["files/1-my-holdings.csv", "files/2-Statement-Oct.pdf", "files/3-screen.png"]
    assert (store.dir / files[1]["path"]).read_bytes() == PDF and files[2]["media_type"] == "image/png"
    analyst.say("What could hit this?", files)
    date, *content = world["script"].requests[0]["messages"][0]["content"]  # today's date first, then the files
    assert date == DATE
    assert content[0] == {"type": "document", "title": "my holdings.csv",
                          "source": {"type": "text", "media_type": "text/plain", "data": CSV.decode()}}
    assert content[1]["type"] == "document" and content[1]["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(content[1]["source"]["data"]) == PDF
    assert content[2] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                      "data": base64.b64encode(PNG).decode()}}
    assert content[3] == {"type": "text", "text": "What could hit this?"}
    # the trace names each file, never its bytes, and a conversation continued from it attaches them again
    text = store.trace_path.read_text()
    assert content[1]["source"]["data"] not in text and content[2]["source"]["data"] not in text
    [user] = [e for e in Trace(store.trace_path).entries() if e["type"] == "user"]
    assert user["files"] == files
    continued = Analyst(analyst.world, client=analyst.client, session=analyst.session)
    assert continued.messages == analyst.messages


def test_an_edited_message_keeps_the_files_it_was_sent_with_that_the_edit_keeps(world, monkeypatch):
    """Through the page: a message sent with a file, edited, reaches the analyst with that file; an edit that removes
    it sends none."""
    import time
    from harness import server
    store = world["store"]
    analyst = world["start"]([lambda s: [say("Read.")]] * 3)
    monkeypatch.setattr(claude, "credential", lambda: claude.Credential(claude.KEY, "sk-test"))
    page = server.Page(root=store.dir.parent)
    page.store, page.analyst, page.session = store, analyst, analyst.session

    def done():
        for _ in range(400):
            if not page.busy and not page.waiting:
                return
            time.sleep(0.01)
        raise AssertionError("the reply did not end")

    def sent():  # the person's side of the last request: each block's type, title or text
        content = world["script"].requests[-1]["messages"][-1]["content"]
        return [(b["type"], b.get("title") or b.get("text")) for b in content] if isinstance(content, list) else content

    page.send("What could hit this?", [{"name": "holdings.csv", "data": base64.b64encode(CSV).decode()}])
    done()
    [kept] = analyst.message(1)["files"]
    assert sent() == [("text", DATE["text"]), ("document", "holdings.csv"), ("text", "What could hit this?")]
    page.edit(1, "These are my holdings. What could hit this?", [kept["path"]])
    done()
    assert sent() == [("text", DATE["text"]), ("document", "holdings.csv"),
                      ("text", "These are my holdings. What could hit this?")]
    assert analyst.message(1)["files"] == [kept]  # the trace's edited message names the same kept file
    page.edit(1, "Never mind the file.", [])
    done()
    assert sent() == [("text", DATE["text"]), ("text", "Never mind the file.")] and analyst.message(1)["files"] == []
    with pytest.raises(StoreError):
        page.edit(1, " ", [])  # an edit with neither words nor a file is no message


@pytest.mark.parametrize("name, data, reason", [
    ("book.xlsx", b"PK\x03\x04", "can't be read: drop a PDF, an image"),
    ("holdings.csv", b"\xff\xfeN\x00V\x00", "not text in UTF-8"),
    ("empty.txt", b"", "is empty"),
])
def test_a_file_the_analyst_cannot_read_is_refused_and_not_kept(tmp_path, name, data, reason):
    with pytest.raises(FileRefused, match=reason):
        keep_file(tmp_path, name, data)
    assert not (tmp_path / "files").exists() or not any((tmp_path / "files").iterdir())


def test_every_request_runs_on_fable_5_2_at_xhigh_effort(world):
    """Every analyst request selects Fable 5.2 and explicitly requests xhigh effort."""
    analyst = world["start"](TURN_1)
    analyst.say(FIRST)
    script = world["script"]
    assert len(script.requests) == 6
    assert all(body["model"] == "claude-fable-5-2" for body in script.requests)
    assert all(body["output_config"] == {"effort": "xhigh"} for body in script.requests)


# ---------------------------------------------------------------- a response that ends short of its answer


OCCURS = {"id": "ceasefire", "name": "a ceasefire is announced", "kind": "occurrence",
          "resolution_source": "the parties' statements"}
REFUSED = {"type": "refusal", "category": "reasoning_extraction", "explanation": "The request asks for the model's reasoning."}


def cut(*content):
    """A response cut off at max_tokens: its last call's input unfinished, whatever the stream gave of it."""
    return lambda s: {"content": list(content), "stop_reason": "max_tokens"}


def declined(*content):
    return lambda s: {"content": list(content), "stop_reason": "refusal", "stop_details": REFUSED}


def metrics(store) -> list:
    """The metrics the analyst added: a price's is the store's own."""
    return [m for m in store.ids_of("Metric") if not m.startswith("metric:price:")]


def test_a_response_cut_off_runs_none_of_its_calls_and_is_made_again_as_the_same_request(world):
    """A call in a response cut off at its limit is not run, so nothing it would write reaches the store; the same
    request goes again, and the turn answers from it. The trace keeps the cut response and the conversation leaves it,
    so the next turn, and a conversation continued from the trace, send what the analyst saw."""
    events = []
    analyst = world["start"]([
        lambda s: [say("I'll note the metric."), use("add", type="Metric", fields=OCCURS)],
        cut(say("Now the second."), use("add", type="Metric", fields={**OCCURS, "id": "second"})),
        lambda s: [use("add", type="Metric", fields={**OCCURS, "id": "third"})],
        lambda s: [say("Both are in.")],
        lambda s: [say("Nothing more.")],
    ], on_event=events.append)
    out = analyst.say(FIRST)
    store, script = world["store"], world["script"]
    assert out["text"] == "Both are in." and not out["stopped"]
    assert metrics(store) == ["metric:ceasefire", "metric:third"]  # the cut call wrote nothing
    assert script.requests[2]["messages"] == script.requests[1]["messages"]  # the same request, again
    entries = analyst.tools.trace.entries()
    replies = [e for e in entries if e["type"] == "assistant"]
    assert [e["stop_reason"] for e in replies] == ["tool_use", "max_tokens", "tool_use", "end_turn"]
    assert all(e["model"] == MODEL for e in replies)
    assert [e["reason"] for e in entries if e["type"] == "dropped"] == ["max_tokens"]
    assert [e for e in events if e["type"] == "dropped"][0]["reason"] == "max_tokens"
    shown = [e for e in live(entries, analyst.session) if e["type"] == "assistant"]
    assert [e["stop_reason"] for e in shown] == ["tool_use", "tool_use", "end_turn"]
    analyst.say("And now?")
    sent = json.dumps(script.requests[4]["messages"])
    assert "Now the second." not in sent and "Both are in." in sent
    continued = Analyst(analyst.world, client=analyst.client, session=analyst.session)
    assert continued.messages == analyst.messages


def test_a_refusal_is_made_again_once_and_where_it_stands_the_turn_ends_with_the_persons_side(world):
    """A declined response is made again as the same request, once. Declined again, it stands: its partial output
    leaves the conversation, which ends with the person's side as a stopped turn's does, and the trace keeps both
    attempts with why each was declined. The next message joins the person's side and is answered."""
    analyst = world["start"]([
        lambda s: [use("add", type="Metric", fields=OCCURS)],
        declined({"type": "thinking", "thinking": "Looking back over what", "signature": "c2ln"}),
        declined(),
        lambda s: [say("Here is what the store holds.")],
    ])
    out = analyst.say(FIRST)
    script = world["script"]
    assert len(script.requests) == 3 and script.requests[2]["messages"] == script.requests[1]["messages"]
    assert out["text"] == "" and not out["stopped"]
    entries = analyst.tools.trace.entries()
    refused = [e for e in entries if e["type"] == "assistant" and e["stop_reason"] == "refusal"]
    assert len(refused) == 2 and all(e["stop_details"]["category"] == "reasoning_extraction" for e in refused)
    assert [e["reason"] for e in entries if e["type"] == "dropped"] == ["refusal", "refusal"]
    assert analyst.messages[-1]["role"] == "user"  # the call's results: the person's side
    assert metrics(world["store"]) == ["metric:ceasefire"]  # what the turn wrote stays
    second = analyst.say("What does the store hold?")
    assert second["text"] == "Here is what the store holds."
    last = script.requests[3]["messages"]
    assert last[-1]["role"] == "user" and last[-1]["content"][-1] == {"type": "text", "text": "What does the store hold?"}
    assert "Looking back over what" not in json.dumps(last)


def test_a_response_cut_off_again_stands_as_the_turns_answer_so_far(world):
    """Made again and cut off again, a response stands: the same request needs more than the model's most output in
    one response, and the turn ends with what it wrote. Its calls are not run."""
    analyst = world["start"]([
        cut(say("The first part of a long answer")),
        cut(say("The first part of a longer answer"), use("add", type="Metric", fields=OCCURS)),
    ])
    out = analyst.say(FIRST)
    assert len(world["script"].requests) == 2 and out["text"] == "The first part of a longer answer"
    assert metrics(world["store"]) == []
    shown = [e for e in live(analyst.tools.trace.entries(), analyst.session) if e["type"] == "assistant"]
    assert [e["stop_reason"] for e in shown] == ["max_tokens"]


# ---------------------------------------------------------------- the first read's stages


def test_the_first_read_reports_its_stages_as_they_happen(world):
    store = world["store"]
    events = []
    store.progress = events.append
    analyst = world["start"](TURN_1[:1] + [lambda s: [say("Priced.")]])
    analyst.say(FIRST)
    store.get("portfolio")  # read whole, as its panel reads it: its volatility needs the exposures fitted
    stages = [e["stage"] for e in events]
    firsts = [s for i, s in enumerate(stages) if s not in stages[:i]]
    assert firsts == ["holdings", "funds", "industries", "prices", "exposures"]  # pricing, looking through, classifying, reading, fitting
    done = [e["derivation"] for e in events if e.get("state") == "done"]
    assert done.index("holdings") < done.index("lookthrough") < done.index("betas") < done.index("g")
    prices = [e for e in events if e["stage"] == "prices" and e["kind"] == "bars"]
    if prices:  # the market's calendar reads its proxy's bars while the holdings are priced
        assert prices[0]["done"] == 0 and [e["done"] for e in prices[1:]] == list(range(1, len(prices)))
        assert all(e["of"] == prices[0]["of"] for e in prices) and prices[-1]["done"] == prices[-1]["of"]
    for e in events:
        if e.get("state") == "done":
            assert store.computation_output(e["basis"]) is not None  # each part is there to show as it is ready
    events.clear()
    store.get("portfolio")
    assert events == []  # a read the cache answers reports nothing


# ---------------------------------------------------------------- the Claude key


def test_on_the_key_a_request_carries_the_key_alone(monkeypatch):
    key = claude.Credential(claude.KEY, "sk-ant-api03-test")
    h = claude.headers(key)
    assert h["x-api-key"] == "sk-ant-api03-test" and "authorization" not in h and "anthropic-beta" not in h
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    assert claude.credential() == key
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert claude.credential() is None


def test_an_error_line_names_the_key_refused(monkeypatch):
    import anthropic
    import httpx2
    from harness.server import _plain_error
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    limited = anthropic.RateLimitError("limit", response=httpx2.Response(429, request=request),
                                       body={"error": {"message": "This request would exceed your limit."}})
    assert _plain_error(limited) == "The analyst couldn't reply on ANTHROPIC_API_KEY: This request would exceed your limit."
    refused = anthropic.AuthenticationError("no", response=httpx2.Response(401, request=request), body=None)
    assert _plain_error(refused) == "The Claude key was refused. Check ANTHROPIC_API_KEY in .env."
