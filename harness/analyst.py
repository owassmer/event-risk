"""The analyst's runtime: Opus 5.5 through the Anthropic Python SDK's tool runner, with the
sixteen tools registered as functions and the system prompt, system.md, followed in context by MATH.md,
ONTOLOGY.md, TOOLS.md, and DATA.md. One session per conversation, over one persistent store; every tool
message and every tool call with its result is written to stores/{name}/trace.jsonl: a reader replays a
conversation by reading it, and every number in it re-derives from the store's data and judgment cache, with
no keys and no judge. A conversation continues from its trace.

Each reply streams. `on_event` receives, in order: the person's message ("user"); each reply's start
("reply_start"), each content block's start, deltas, and stop ("block_start", "delta", "block_stop"), and
the whole reply ("assistant"); each tool call's start and end with the id of the tool_use block that asked
for it ("call_start", "call"); a stop ("stop"); and the answer ("answer"). The page is drawn from these.

The store checkpoints what the analyst has written at each message it is sent; editing a message or retrying
a reply rewinds the store and the conversation together to just before it, and the trace records the rewind
and keeps the turns it replaced. Stop ends a turn at its current call: the calls not yet run
are answered as not run, so the conversation stays one the API accepts, and what the turn wrote stays. A file
the person drops is kept beside the store, in files/, and reaches the analyst as a document or an image; the
trace names it, and a conversation continued from the trace attaches it again. Every tool result stays in the
analyst's context for the whole conversation, within Opus 5.5's context window: nothing it has read is taken away
while it works.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import threading
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any, Callable

from harness import claude
from harness.derive import default_registry
from harness.derive.engine import digest
from harness.store import STORE_FILE, Store, store_dir
from harness.tools import SPECS, Tools, input_schema
from harness.trace import now
from harness.world import World

MODEL = "claude-opus-5-5"
MAX_TOKENS = 128_000  # Opus 5.5's most output (https://platform.claude.com/docs/en/about-claude/models/overview)
# Its context window is 1M tokens, the default for the model, with no beta header and at standard pricing
# (https://platform.claude.com/docs/en/build-with-claude/context-windows): every tool result stays in it.
# Opus 5.5 thinks adaptively, and returns its thinking's text only with display "summarized"; its default, "omitted",
# returns empty thinking blocks (https://platform.claude.com/docs/en/build-with-claude/thinking).
THINKING = {"type": "adaptive", "display": "summarized"}
# Effort is the primary control for how much Opus 5.5 reasons, and its default, medium, applies to a request that
# omits it: less than an analysis built over many tool calls needs
# (https://platform.claude.com/docs/en/build-with-claude/effort). xhigh is for long-horizon agentic work.
EFFORT = "xhigh"
NOT_RUN = "Not run: the person stopped the turn before this call."
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "knowledge"
DOCUMENTS = ("system.md", "MATH.md", "ONTOLOGY.md", "TOOLS.md", "DATA.md")


def load_env(path: str | Path = ROOT / ".env") -> None:
    """The keys in .env, into the environment where they are not already set. Nothing is printed."""
    p = Path(path)
    for line in p.read_text().splitlines() if p.exists() else []:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("'\""))


def system_prompt(knowledge: str | Path = KNOWLEDGE) -> list[dict]:
    """system.md followed by the four documents, the last carrying the cache breakpoint."""
    blocks = [{"type": "text", "text": (Path(knowledge) / d).read_text(encoding="utf-8")} for d in DOCUMENTS]
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def open_store(name: str, *, root: str | Path = ROOT / "stores", knowledge: str | Path = KNOWLEDGE,
               judge: Any = "default", fetchers: dict | None = None) -> Store:
    """stores/{name}: opened when it exists, otherwise created with the initial contents. The judge
    is mammoth-1 when BASETEN_API_KEY is set, Massive's fetchers are there when MASSIVE_API_KEY is, and Coinbase
    Exchange's, for a crypto pair's prices, need no key; without them the store answers from its cache and its data."""
    from harness.judge import default_judge
    from harness.venues.coinbase import Coinbase
    from harness.venues.massive import Massive

    d = store_dir(name, root)
    if fetchers is None:
        key = os.environ.get("MASSIVE_API_KEY")
        fetchers = {**(Massive(key).fetchers() if key else {}), **Coinbase().fetchers()}
    kw = {"registry": default_registry(), "judge": default_judge() if judge == "default" else judge,
          "fetchers": fetchers}
    if (d / STORE_FILE).exists():
        return Store.open(d, **kw)
    return Store.create(d, initial=knowledge, **kw)


# ---------------------------------------------------------------- files the person drops

# What the analyst reads, by extension: a PDF as a document, text as a plain-text document, and the four image
# formats (https://platform.claude.com/docs/en/build-with-claude/pdf-support,
# https://platform.claude.com/docs/en/build-with-claude/citations, https://platform.claude.com/docs/en/build-with-claude/vision).
READABLE = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".txt": "text/plain", ".csv": "text/csv",
            ".tsv": "text/tab-separated-values", ".md": "text/markdown", ".json": "application/json"}
FILES = "files"


class FileRefused(ValueError):
    """A file the analyst cannot read; the message says which files it can."""


def keep_file(directory: str | Path, name: str, data: bytes) -> dict:
    """A dropped file, kept beside the store in files/, and what the trace says of it."""
    suffix = Path(name).suffix.lower()
    media = READABLE.get(suffix)
    if media is None:
        raise FileRefused(f"{name} can't be read: drop a PDF, an image (PNG, JPEG, GIF, WebP), or text (CSV, TXT)")
    if not data:
        raise FileRefused(f"{name} is empty")
    if not (media == "application/pdf" or media.startswith("image/")):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileRefused(f"{name} is not text in UTF-8") from None
    folder = Path(directory) / FILES
    folder.mkdir(parents=True, exist_ok=True)
    n = 1 + max((int(m.group(1)) for f in folder.iterdir() if (m := re.match(r"(\d+)-", f.name))), default=0)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.")[:60] or "file"
    path = folder / f"{n}-{stem}{suffix}"
    path.write_bytes(data)
    return {"name": name, "path": f"{FILES}/{path.name}", "media_type": media, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def file_block(directory: str | Path, f: dict) -> dict:
    """A kept file as the analyst reads it: a document or an image."""
    data = (Path(directory) / f["path"]).read_bytes()
    media = f["media_type"]
    if media.startswith("image/"):
        return {"type": "image", "source": {"type": "base64", "media_type": media,
                                            "data": base64.b64encode(data).decode("ascii")}}
    if media == "application/pdf":
        return {"type": "document", "title": f["name"],
                "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode("ascii")}}
    return {"type": "document", "title": f["name"],
            "source": {"type": "text", "media_type": "text/plain", "data": data.decode("utf-8")}}


def user_content(directory: str | Path, text: str, files: list[dict] | None) -> str | list[dict]:
    if not files:
        return text
    return [file_block(directory, f) for f in files] + [{"type": "text", "text": text}]


def _append_user(messages: list[dict], content: str | list[dict]) -> tuple[int, int | None]:
    """The person's message, appended; after a stopped turn the conversation already ends with the person's side
    (their message, or the results of the calls made), and the message joins it. Returns where it starts: the
    message's index, and the blocks before it there when it joined one."""
    if messages and messages[-1]["role"] == "user":
        last = messages[-1]
        before = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
        added = content if isinstance(content, list) else [{"type": "text", "text": content}]
        messages[-1] = {"role": "user", "content": before + added}
        return len(messages) - 1, len(before)
    messages.append({"role": "user", "content": content})
    return len(messages) - 1, None


def live(entries: list[dict], session: str) -> list[dict]:
    """A conversation's entries as it stands: each rewind drops the turns it replaced, which stay in the trace."""
    out: list[dict] = []
    for e in entries:
        if e.get("session") != session:
            continue
        if e["type"] == "rewind":
            out = [x for x in out if not (isinstance(x.get("turn"), int) and x["turn"] >= e["turn"])]
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------- the session


def _block(block: Any) -> dict:
    return block.model_dump(mode="json", exclude_none=True) if hasattr(block, "model_dump") else dict(block)


def _usage(message: Any) -> dict:
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    return {k: v for k, v in usage.model_dump(mode="json", exclude_none=True).items() if isinstance(v, (int, float))}


def _shut(stream: Any) -> None:
    """Shut down the socket a reply streams over, so a read waiting on it returns at once."""
    try:
        network = stream.response.extensions["network_stream"]
        socket.socket.shutdown(network.get_extra_info("socket"), socket.SHUT_RDWR)
    except Exception:  # a stream with no socket of its own, such as a test's: closing it is enough
        pass


class Stopped(Exception):
    """The person stopped the turn."""


class Analyst:
    """One conversation with the analyst over one store."""

    def __init__(self, world: World, *, client: Any = None, model: str = MODEL, knowledge: str | Path = KNOWLEDGE,
                 max_tokens: int = MAX_TOKENS, thinking: dict | None = None,
                 on_event: Callable[[dict], None] | None = None, session: str | None = None):
        self.world = world
        self.store = world.store
        self.tools = Tools(world)
        self.client = client if client is not None else claude.client()  # on the Claude key (harness.claude)
        self.model = model
        self.max_tokens = max_tokens
        self.thinking = thinking if thinking is not None else THINKING
        self.system = system_prompt(knowledge)
        self.messages: list[dict] = []
        self.starts: dict[int, tuple[int, int | None]] = {}  # where each turn's message starts in messages
        self._calls: list[dict] = []
        self._uses: deque = deque()  # the tool_use blocks of the reply whose tools run now, in order
        self._stopping = threading.Event()
        self._stream: Any = None
        self.turn = 0
        self.on_event = on_event or (lambda event: None)
        self.functions = [self._function(name) for name in SPECS]
        if session is not None:  # the conversation continues: its history, from the trace
            self.session = self.tools.session = session
            self.messages, self.turn, self.starts = history(self.tools.trace.entries(), session, self.store.dir)
            return
        self.session = f"{now().replace(':', '').replace('-', '')}-{secrets.token_hex(3)}"
        self.tools.session = self.session
        judge = self.store.engine.judge
        self.tools.trace.append({"type": "session", "session": self.session, "at": now(), "model": model,
                                 "store": self.store.dir.name, "judge": getattr(judge, "model", None),
                                 "fetchers": sorted(self.store.fetchers),
                                 "system": digest([b["text"] for b in self.system])})

    def _function(self, name: str):
        from anthropic.lib.tools import BetaFunctionTool, ToolError

        def run(**input: Any) -> str:
            use = next((u for u in self._uses if u[1] == name), (None, name))
            if use[0] is not None:
                self._uses.remove(use)
            if self._stopping.is_set():  # the turn ends at the call before: this one is answered as not run
                raise ToolError(NOT_RUN)
            self.on_event({"type": "call_start", "tool": name, "input": input, "tool_use_id": use[0]})
            ok, out = self.tools.call(name, input)
            text = json.dumps(out, ensure_ascii=False, allow_nan=False, default=str)
            self._calls.append({"call": self.tools.last_call, "tool": name, "ok": ok, "tool_use_id": use[0]})
            self.on_event({"type": "call", "call": self.tools.last_call, "tool": name, "input": input, "ok": ok,
                           "result": out, "tool_use_id": use[0]})
            if not ok:
                raise ToolError(text)
            return text

        return BetaFunctionTool(run, name=name, description=SPECS[name]["does"], input_schema=input_schema(name))

    def today(self) -> list[dict]:
        """Today's date in New York, a fact in the analyst's context after its documents."""
        day = date.fromisoformat(self.world.today())
        return [{"type": "text", "text": f"Today is {day.strftime('%A')}, {day.isoformat()}, in New York."}]

    def checkpoint_key(self, turn: int) -> str:
        return f"{self.session}:{turn}"

    # ---------------------------------------------------------------- the person's side

    def stop(self) -> None:
        """End the turn at its current call. A reply streaming now ends where it is, a quiet one included: closing a
        stream from another thread does not wake a read already waiting on its socket while the model thinks, so the
        socket is shut down first, which does."""
        self._stopping.set()
        stream = self._stream
        if stream is not None:
            _shut(stream)
            try:
                stream.close()
            except Exception:
                pass

    def rewind(self, turn: int, reason: str) -> None:
        """Return the store and the conversation to just before turn `turn`'s message."""
        if turn not in self.starts or turn > self.turn:
            raise ValueError(f"turn {turn} is not in this conversation")
        with self.store.lock:
            self.store.rewind(self.checkpoint_key(turn))
        index, before = self.starts[turn]
        if before is None:
            del self.messages[index:]
        else:
            del self.messages[index + 1:]
            self.messages[index] = {"role": "user", "content": self.messages[index]["content"][:before]}
        self.starts = {t: v for t, v in self.starts.items() if t < turn}
        self.turn = turn - 1
        entry = {"type": "rewind", "session": self.session, "turn": turn, "reason": reason, "at": now()}
        self.tools.trace.append(entry)
        self.on_event(entry)

    def message(self, turn: int) -> dict:
        """Turn `turn`'s message as the person sent it: its text and files."""
        found = [e for e in live(self.tools.trace.entries(), self.session) if e["type"] == "user" and e["turn"] == turn]
        if not found:
            raise ValueError(f"turn {turn} is not in this conversation")
        return {"text": found[-1]["text"], "files": found[-1].get("files") or []}

    def edit(self, turn: int, text: str, files: list[dict] | None = None) -> dict:
        """The message of turn `turn`, changed: the store and the conversation rewind to before it, and it goes."""
        self.rewind(turn, "edit")
        return self.say(text, files)

    def retry(self, turn: int) -> dict:
        """Turn `turn`'s reply, again: the store and the conversation rewind to before its message, which goes
        again as it was."""
        sent = self.message(turn)
        self.rewind(turn, "retry")
        return self.say(sent["text"], sent["files"])

    # ---------------------------------------------------------------- a turn

    def say(self, text: str, files: list[dict] | None = None) -> dict:
        """One message from the person, with the files they dropped as kept by `keep_file`; the analyst works
        until it answers or the person stops it. Returns the answer's text, the tool calls it made, the tokens the
        turn spent, and whether it was stopped."""
        self._stopping.clear()
        self.turn += 1
        self.tools.turn = self.turn
        trace = self.tools.trace
        today = self.today()
        with self.store.lock:
            self.store.checkpoint(self.checkpoint_key(self.turn))
        entry = {"type": "user", "session": self.session, "turn": self.turn, "at": now(), "text": text,
                 **({"files": files} if files else {}), "context": [b["text"] for b in today]}
        trace.append(entry)
        self.on_event(entry)
        self.starts[self.turn] = _append_user(self.messages, user_content(self.store.dir, text, files))
        self._calls = []
        spent: dict[str, int] = {}
        replies = []
        partial: list[str] = []
        stopped = False
        try:
            runner = self.client.beta.messages.tool_runner(
                model=self.model, max_tokens=self.max_tokens,
                system=self.system + today,
                tools=self.functions, messages=list(self.messages), thinking=self.thinking, thread=False,
                cache_control={"type": "ephemeral"}, stream=True, output_config={"effort": EFFORT})
            for stream in runner:
                if self._stopping.is_set():
                    raise Stopped
                self._stream = stream
                self.on_event({"type": "reply_start", "session": self.session, "turn": self.turn})
                partial = []
                try:
                    for event in stream:
                        self._stream_event(event, partial)
                        if self._stopping.is_set():
                            raise Stopped
                    message = stream.get_final_message()
                except Exception:
                    if self._stopping.is_set():
                        raise Stopped from None
                    raise
                finally:
                    self._stream = None
                content = [_block(b) for b in message.content]
                usage = _usage(message)
                for k, v in usage.items():
                    spent[k] = spent.get(k, 0) + v
                entry = {"type": "assistant", "session": self.session, "turn": self.turn, "at": now(),
                         "stop_reason": message.stop_reason, "content": content, "usage": usage}
                trace.append(entry)
                self.on_event(entry)
                self.messages.append({"role": "assistant", "content": content})
                partial = []
                replies.append(message)
                self._uses = deque((b["id"], b["name"]) for b in content if b.get("type") == "tool_use")
                ran = len(self._calls)
                results = runner.generate_tool_call_response()  # cached: the runner sends these same results
                if results is not None:
                    self._record_results(content, [_block(b) for b in results["content"]], ran)
                if self._stopping.is_set():
                    raise Stopped
        except Stopped:
            stopped = True
        final = replies[-1] if replies else None
        answer = "".join(b.text for b in (final.content if final else []) if getattr(b, "type", None) == "text")
        if stopped:
            entry = {"type": "stop", "session": self.session, "turn": self.turn, "at": now(),
                     **({"partial": "".join(partial)} if partial else {})}
            trace.append(entry)
            self.on_event(entry)
        out = {"text": answer, "calls": list(self._calls), "usage": spent, "requests": len(replies),
               "stopped": stopped}
        self.on_event({"type": "answer", "session": self.session, "turn": self.turn, **out})
        return out

    def _record_results(self, content: list[dict], blocks: list[dict], ran: int) -> None:
        """A reply's tool results, sent back and traced: each call that ran by its number, any other by its text."""
        self.messages.append({"role": "user", "content": blocks})
        calls = {c["tool_use_id"]: c["call"] for c in self._calls[ran:]}
        self.tools.trace.append({"type": "results", "session": self.session, "turn": self.turn, "results": [
            {"tool_use_id": b["tool_use_id"], "is_error": bool(b.get("is_error")),
             **({"call": calls[b["tool_use_id"]]} if b["tool_use_id"] in calls else {"content": b["content"]})}
            for b in blocks]})

    def _stream_event(self, event: Any, partial: list[str]) -> None:
        """A streamed reply's content blocks as they arrive: each block's start, its deltas, and its stop."""
        kind = getattr(event, "type", None)
        if kind == "content_block_start":
            block = event.content_block
            self.on_event({"type": "block_start", "index": event.index, "block": {
                "type": block.type, **({"id": block.id, "name": block.name} if block.type == "tool_use" else {})}})
        elif kind == "content_block_delta":
            d = event.delta
            delta = {"text_delta": ("text", "text"), "thinking_delta": ("thinking", "thinking"),
                     "input_json_delta": ("input", "partial_json")}.get(d.type)
            if delta:
                if delta[0] == "text":
                    partial.append(d.text)
                self.on_event({"type": "delta", "index": event.index, "kind": delta[0], "delta": getattr(d, delta[1])})
        elif kind == "content_block_stop" and getattr(event, "content_block", None) is not None:
            self.on_event({"type": "block_stop", "index": event.index, "block": _block(event.content_block)})


def history(entries: list[dict], session: str, directory: str | Path) -> tuple[list[dict], int, dict]:
    """A conversation's messages as the model saw them, its last turn, and where each turn's message starts,
    rebuilt from the trace: the turns a rewind replaced are dropped, and each message's files are attached again
    from the store's files/."""
    calls = {e["call"]: e for e in entries if e.get("type") == "call"}
    messages: list[dict] = []
    starts: dict[int, tuple[int, int | None]] = {}
    turn = 0
    for e in live(entries, session):
        if e["type"] == "user":
            starts[e["turn"]] = _append_user(messages, user_content(directory, e["text"], e.get("files")))
            turn = e["turn"]
        elif e["type"] == "assistant":
            messages.append({"role": "assistant", "content": e["content"]})
        elif e["type"] == "results":
            blocks = []
            for r in e["results"]:
                if "call" in r:
                    c = calls[r["call"]]
                    text = json.dumps(c.get("result", c.get("error")), ensure_ascii=False, allow_nan=False, default=str)
                else:
                    text = r["content"]
                blocks.append({"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": text,
                               **({"is_error": True} if r["is_error"] else {})})
            messages.append({"role": "user", "content": blocks})
    return messages, turn, starts

