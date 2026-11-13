"""The analyst's runtime: Fable 5.2 through the Anthropic Python SDK's tool runner, with the
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
are answered as not run, so the conversation stays one the API accepts, and what the turn wrote stays. A response
that ends short of its answer, declined or cut off at a limit (SHORT), is made again once as the same request; the trace
keeps it, and the conversation leaves it as it leaves the turns a rewind replaced. A refusal met again leaves it too, its
partial output incomplete, and the turn ends with the person's side, as a stopped one does. A file
the person drops is kept beside the store, in files/, and reaches the analyst as a document or an image; the
trace names it, and a conversation continued from the trace attaches it again. Every tool result stays in the
analyst's context for the whole conversation, within Fable 5.2's context window: nothing it has read is taken away
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
from typing import Any, Callable, NamedTuple

from harness import claude, record
from harness.derive import default_registry
from harness.derive.engine import digest
from harness.store import SCHEMA_VERSION, STORE_FILE, Store, store_dir
from harness.tools import SPECS, Tools, input_schema
from harness.trace import now
from harness.world import World

MODEL = "claude-fable-5-2"
MAX_TOKENS = 128_000  # Fable 5.2's most output (https://platform.claude.com/docs/en/about-claude/models/overview)
# Its context window is 1M tokens, the default for the model, with no beta header and at standard pricing
# (https://platform.claude.com/docs/en/build-with-claude/context-windows): every tool result stays in it.
# Fable 5.2 thinks adaptively, and returns its thinking's text only with display "summarized"; its default, "omitted",
# returns empty thinking blocks (https://platform.claude.com/docs/en/build-with-claude/thinking).
THINKING = {"type": "adaptive", "display": "summarized"}
# Effort is the primary control for how much Fable 5.2 reasons, and its default, high, applies to a request that
# omits it: less than an analysis built over many tool calls needs
# (https://platform.claude.com/docs/en/build-with-claude/effort). xhigh is for long-horizon agentic work.
EFFORT = "xhigh"
NOT_RUN = "Not run: the person stopped the turn before this call."
NOT_FINISHED = "Not run: the reply was cut off before this call ran."
# The ways a response ends short of its answer, as the Messages API reports them in stop_reason
# (https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons): the model declined ("refusal"), its
# safety classifiers' answer, given as a response and not as an error, which the same request does not always meet
# again; it reached max_tokens, which is Fable 5.2's most output already; or it filled the model's context window. A
# cut response cannot be continued in its own words, since Claude 4.6 and later reject a prefilled assistant turn
# (https://platform.claude.com/docs/en/build-with-claude/thinking#response-prefill-and-forced-tool-use), and a message
# asking it to go on would be one the person did not write. So each is made again, once, as the same request. A
# response that stopped, for any reason, never has its tool calls run: a call cut off by a limit has an input the
# model did not finish, and the SDK's runner holds that a stopped turn's tool uses are not executed.
SHORT = ("refusal", "max_tokens", "model_context_window_exceeded")
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


# How long what the analyst has read stays cached between requests: an hour, where the default is five minutes. A member
# reads a reply before writing the next, and a turn can wait minutes on one call, so a five-minute cache lapses and the
# whole conversation is written again; a write for an hour costs twice
# the base input price where one for five minutes costs 1.25 times, and a read keeps either alive. Both breakpoints
# take the same time, as the API requires (https://docs.claude.com/en/docs/build-with-claude/prompt-caching.md,
# "1-hour cache duration": `{"type": "ephemeral", "ttl": "1h"}`; a longer entry must come before a shorter one).
CACHE = {"type": "ephemeral", "ttl": "1h"}


def system_prompt(knowledge: str | Path = KNOWLEDGE, text: str | None = None) -> list[dict]:
    """system.md followed by the four documents, and a conversation's own text after them where it has one (a mode's),
    the last carrying the cache breakpoint. The same for the whole conversation: what changes from one message to the
    next, today's date among it, comes with the message (today)."""
    blocks = [{"type": "text", "text": (Path(knowledge) / d).read_text(encoding="utf-8")} for d in DOCUMENTS]
    if text:
        blocks.append({"type": "text", "text": text})
    blocks[-1]["cache_control"] = dict(CACHE)
    return blocks


def by_pair(crypto: Callable, currency: Callable) -> Callable:
    """A pair's read, by its kind: a currency pair's (`C:`) from Yahoo Finance, a crypto pair's from Coinbase; each
    in its own client's form (`parse`, by subject), so a kept answer in another is read again (harness.observations)."""
    def read(subject: str, covering: Any) -> dict:
        return (currency if subject.startswith("C:") else crypto)(subject, covering)
    read.parse = lambda subject: getattr(currency if subject.startswith("C:") else crypto,  # type: ignore[attr-defined]
                                         "parse", None)
    return read


_clients: dict | None = None
_clients_lock = threading.Lock()


def clients() -> dict:
    """This process's data clients, one set for every store it opens, each reading through this machine's shared
    observations (harness.observations), so what one store read about a closed range serves the next: Massive's where
    MASSIVE_API_KEY is set, with which fund's holdings are an index's companies (harness.venues.trackers), read only
    where a held fund's derivative is exposed to an index; Coinbase Exchange's, Yahoo Finance's, the ECB's, OpenFIGI's,
    the SEC's and TreasuryDirect's, which need no key, OpenFIGI's reading OPENFIGI_API_KEY where it is set. Returns
    `{"fetchers"}`."""
    global _clients
    from harness.observations import shared
    from harness.venues.coinbase import Coinbase
    from harness.venues.ecb import ECB
    from harness.venues.fixings import Fixings, with_fixings
    from harness.venues.massive import Massive
    from harness.venues.openfigi import OpenFIGI
    from harness.venues.sec import SEC
    from harness.venues.trackers import Trackers
    from harness.venues.treasury import Treasury
    from harness.venues.curves import Curves, with_curves
    from harness.venues.funds import MutualFunds
    from harness.venues.yahoo import Yahoo

    with _clients_lock:
        if _clients is not None:
            return _clients
        observations = shared()
        yahoo, sec, ecb = Yahoo(), SEC(archive=observations), ECB()
        crypto, currency = Coinbase().fetchers(), yahoo.fetchers()
        # a currency's rates: Yahoo's hourly where they reach, and its daily fixings before them
        currency["hourly"] = with_fixings(currency["hourly"], Fixings(ecb))
        base = {**currency, **crypto, **ecb.fetchers(),
                **OpenFIGI(key=os.environ.get("OPENFIGI_API_KEY") or None).fetchers(),
                **sec.fetchers(), **Treasury().fetchers(),
                **{kind: by_pair(crypto[kind], currency[kind]) for kind in ("hourly", "last_trade")}}
        # each currency's government yield curve: the Treasury's for the dollar, its own government's for another
        base["yield_curve"] = with_curves(base["yield_curve"], Curves())
        key = os.environ.get("MASSIVE_API_KEY")
        if key:
            massive = Massive(key)
            base.update({**massive.fetchers(), **Trackers(massive, observations).fetchers()})
            # a mutual fund's share class, which no listing's reference gives: its own reference, prices and dividends
            base.update(MutualFunds(massive, sec, yahoo).fetchers(base))
        _clients = {"fetchers": {kind: observations.shared(kind, f) for kind, f in base.items()}}
        return _clients


def open_store(name: str, *, root: str | Path = ROOT / "stores", knowledge: str | Path = KNOWLEDGE,
               judge: Any = "default", fetchers: dict | None = None, trace: str | Path | None = None) -> Store:
    """stores/{name}: opened when it exists, otherwise created with the initial contents. The judge
    is mammoth-1 when BASETEN_API_KEY is set, Massive's fetchers are there when MASSIVE_API_KEY is, and Coinbase
    Exchange's, for a crypto pair's prices, Yahoo Finance's, for a currency pair's rates and a listing abroad's prices,
    the ECB's, for the rates a dividend in another currency is added back at, and OpenFIGI's and the SEC's, for the
    identifiers that confirm a fund's companies and a closed-end fund's holdings and which mutual funds are money market
    funds, and TreasuryDirect's, for which holdings are Treasury bills, need no key; without them the store
    answers from its cache and its data. Every store a process opens reads through one set of clients (clients)."""
    from harness.judge import default_judge

    d = store_dir(name, root)
    if fetchers is None:
        fetchers = dict(clients()["fetchers"])
    kw = {"registry": default_registry(), "judge": default_judge() if judge == "default" else judge,
          "fetchers": fetchers, **({"trace": trace} if trace is not None else {})}
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


def user_content(directory: str | Path, text: str, files: list[dict] | None,
                 context: list[str] | None = None) -> str | list[dict]:
    """A message as the analyst reads it: what the harness says with it first (today's date), then the files the
    person dropped, then their words."""
    blocks = [{"type": "text", "text": c} for c in context or []] + [file_block(directory, f) for f in files or []]
    if not blocks:
        return text
    return blocks + [{"type": "text", "text": text}]


# The media type of an image a tool's result names by its path, by its extension.
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def result_content(result: Any, directory: str | Path, ok: bool = True) -> str | list[dict]:
    """A call's result as the analyst reads it: its text, and, where the call did what it was asked (`ok`), each image
    it names (`images`, each with a `path` in the conversation's directory), as an image after it. A failed call's result
    is its text alone: the Messages API refuses a whole request whose `is_error` tool result holds anything else, with
    400 "all content must be type `text` if `is_error` is true", read live, though
    https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls does not say so."""
    text = json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
    named = result.get("images") if isinstance(result, dict) and ok else None
    if not named:
        return text
    blocks: list[dict] = [{"type": "text", "text": text}]
    root = Path(directory).resolve()
    for image in named:
        path = (root / str(image.get("path", ""))).resolve() if isinstance(image, dict) else None
        if path is None or root not in path.parents or path.suffix.lower() not in IMAGES or not path.is_file():
            continue
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": IMAGES[path.suffix.lower()],
                                                   "data": base64.b64encode(path.read_bytes()).decode("ascii")}})
    return blocks


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
    """A conversation's entries as it stands: each rewind drops the turns it replaced, and each response that ended
    short of its answer and was made again, or was declined, leaves it; all stay in the trace."""
    out: list[dict] = []
    for e in entries:
        if e.get("session") != session:
            continue
        if e["type"] == "rewind":
            out = [x for x in out if not (isinstance(x.get("turn"), int) and x["turn"] >= e["turn"])]
            continue
        if e["type"] == "dropped":  # the response it follows, which ran no call, leaves the conversation
            i = next(i for i in range(len(out) - 1, -1, -1) if out[i]["type"] == "assistant")
            del out[i]
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


def _shut(stream: Any) -> bool:
    """Shut down the socket a reply streams over, so a read waiting on it returns at once; whether there was one."""
    try:
        network = stream.response.extensions["network_stream"]
        socket.socket.shutdown(network.get_extra_info("socket"), socket.SHUT_RDWR)
    except Exception:  # a stream with no socket of its own, such as a test's
        return False
    return True


class Stopped(Exception):
    """The person stopped the turn."""


class Extra(NamedTuple):
    """A tool a conversation has beyond the sixteen: what it does and returns, its parameters' schema, and the function
    of the call's input to (True, result) or (False, {"error", ...})."""
    description: str
    input_schema: dict
    run: Callable[[dict], tuple[bool, Any]]


class Analyst:
    """One conversation with the analyst over one store."""

    def __init__(self, world: World, *, client: Any = None, model: str = MODEL, effort: str = EFFORT,
                 knowledge: str | Path = KNOWLEDGE, max_tokens: int = MAX_TOKENS, thinking: dict | None = None,
                 on_event: Callable[[dict], None] | None = None, session: str | None = None,
                 credential: claude.Credential | None = None, mode: str | None = None, text: str | None = None,
                 extra: dict[str, Extra] | None = None, directory: str | Path | None = None):
        """A conversation over the world's store. A conversation of a mode of its own names it (`mode`), reads its
        text after the analyst's (`text`), has its tools beside the sixteen (`extra`), and keeps its files and reads
        them in `directory`, where they are kept beside the store otherwise."""
        self.world = world
        self.store = world.store
        self.extra = dict(extra or {})
        self.tools = Tools(world, {name: x.run for name, x in self.extra.items()})
        self.mode = mode
        self.effort = effort
        self.directory = Path(directory) if directory is not None else self.store.dir
        # The key (harness.claude); a client given comes with the credential it carries, if any.
        self.credential = credential if client is not None or credential is not None else claude.credential()
        self.client = client if client is not None else claude.client(self.credential)
        self.model = model
        self.max_tokens = max_tokens
        self.thinking = thinking if thinking is not None else THINKING
        self.system = system_prompt(knowledge, text)
        self.messages: list[dict] = []
        self.starts: dict[int, tuple[int, int | None]] = {}  # where each turn's message starts in messages
        self._calls: list[dict] = []
        self._uses: deque = deque()  # the tool_use blocks of the reply whose tools run now, in order
        self._stopping = threading.Event()
        self._stream: Any = None
        self.turn = 0
        self.usage: dict[str, int] = {}
        self.on_event = on_event or (lambda event: None)
        self.functions = [self._function(name) for name in (*SPECS, *self.extra)]
        if session is not None:  # the conversation continues: its history, from the trace
            self.session = self.tools.session = session
            self.messages, self.turn, self.starts = history(self.tools.trace.entries(), session, self.directory)
            return
        self.session = f"{now().replace(':', '').replace('-', '')}-{secrets.token_hex(3)}"
        self.tools.session = self.session
        judge = self.store.engine.judge
        self.tools.trace.append({"type": "session", "session": self.session, "at": now(),
                                 **({"mode": mode} if mode else {}), "model": model, "effort": effort,
                                 "schema": SCHEMA_VERSION, "store": self.store.dir.name,
                                 "judge": getattr(judge, "model", None), "fetchers": sorted(self.store.fetchers),
                                 "system": digest([b["text"] for b in self.system])})

    def _function(self, name: str):
        from anthropic.lib.tools import BetaFunctionTool, ToolError

        def run(**input: Any) -> str:
            use = next((u for u in self._uses if u[1] == name), (None, name))
            if use[0] is not None:
                self._uses.remove(use)
            if self._stopping.is_set():  # the turn ends at the call before: this one is answered as not run
                raise ToolError(NOT_RUN)
            store = self.store.dir.name  # the store the call runs on, as its trace entry names it
            self.on_event({"type": "call_start", "tool": name, "input": input, "tool_use_id": use[0], "store": store})
            ok, out = self.tools.call(name, input)
            content = result_content(out, self.directory, ok)
            self._calls.append({"call": self.tools.last_call, "tool": name, "ok": ok, "tool_use_id": use[0]})
            self.on_event({"type": "call", "call": self.tools.last_call, "tool": name, "input": input, "ok": ok,
                           "result": out, "tool_use_id": use[0], "store": store})
            if not ok:
                raise ToolError(content)
            return content

        if name in self.extra:
            x = self.extra[name]
            return BetaFunctionTool(run, name=name, description=x.description, input_schema=x.input_schema)
        return BetaFunctionTool(run, name=name, description=SPECS[name]["does"], input_schema=input_schema(name))

    def today(self) -> list[str]:
        """Today's date in New York, said first in each message the person sends, so the system text is the same for
        the whole conversation."""
        day = date.fromisoformat(self.world.today())
        return [f"Today is {day.strftime('%A')}, {day.isoformat()}, in New York."]

    def _open_calls(self) -> list[dict]:
        """The calls this conversation made after its last reply whose results were not sent: a reply that failed while
        its calls ran."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return []
        made = {c["call"] for c in self._calls}
        return [e for e in self.tools.trace.entries() if e.get("type") == "call" and e.get("call") in made]

    def checkpoint_key(self, turn: int) -> str:
        return f"{self.session}:{turn}"

    def checkpoint(self, turn: int) -> None:
        """What the analyst has written, marked at turn `turn`'s message, for a rewind to return to."""
        with self.store.lock:
            self.store.checkpoint(self.checkpoint_key(turn))

    # ---------------------------------------------------------------- the person's side

    def stop(self) -> None:
        """End the turn at its current call. A reply streaming now ends where it is, a quiet one included: closing a
        stream from another thread does not wake a read already waiting on its socket while the model thinks, so the
        socket is shut down, which does, and the reading thread, woken, closes the stream as it leaves. Closing it here
        as well would release the socket's descriptor before that thread had woken, and its read would wait on a
        descriptor no longer the reply's, which another connection can take: it would never wake. A stream with no
        socket of its own is closed here."""
        self._stopping.set()
        stream = self._stream
        if stream is not None and not _shut(stream):
            try:
                stream.close()
            except Exception:
                pass

    def rewind(self, turn: int, reason: str) -> None:
        """Return the store and the conversation to just before turn `turn`'s message."""
        if turn not in self.starts or turn > self.turn:
            raise ValueError(f"turn {turn} is not in this conversation")
        self.rewind_store(turn)
        index, before = self.starts[turn]
        if before is None:
            del self.messages[index:]
        else:
            del self.messages[index + 1:]
            self.messages[index] = {"role": "user", "content": self.messages[index]["content"][:before]}
        self.starts = {t: v for t, v in self.starts.items() if t < turn}
        self.turn = turn - 1
        entry = {"type": "rewind", "session": self.session, "turn": turn, "reason": reason, "at": now(),
                 "store": self.store.dir.name}
        self.tools.trace.append(entry)
        self.on_event(entry)

    def rewind_store(self, turn: int) -> None:
        """The store, as the analyst had written it at turn `turn`'s message."""
        with self.store.lock:
            self.store.rewind(self.checkpoint_key(turn))

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
        self.checkpoint(self.turn)
        entry = {"type": "user", "session": self.session, "turn": self.turn, "at": now(), "text": text,
                 **({"files": files} if files else {}), "context": today, "context_at": "message"}
        trace.append(entry)
        self.on_event(entry)
        answer_open_calls(self.messages, self._open_calls(), self.directory)
        self.starts[self.turn] = _append_user(self.messages, user_content(self.directory, text, files, today))
        self._calls = []
        spent: dict[str, int] = {}
        replies = []
        partial: list[str] = []
        stopped = False
        again: set[int] = set()  # each request made again, by how many messages it was sent with
        declined = False  # the turn ended on a refusal its second try met too
        try:
            while True:
                message = self._request(self.client.beta.messages.tool_runner(
                    model=self.model, max_tokens=self.max_tokens,
                    system=self.system,
                    tools=self.functions, messages=list(self.messages), thinking=self.thinking, thread=False,
                    cache_control=dict(CACHE), stream=True, output_config={"effort": self.effort}),
                spent, replies, partial)
                sent = len(self.messages) - 1  # the request the last response answered
                if message is None or message.stop_reason not in SHORT:
                    break
                made_again = sent not in again
                if made_again or message.stop_reason == "refusal":
                    # it leaves the conversation and stays in the trace: made again, or a refusal's partial output,
                    # which is incomplete and is not sent back (refusals-and-fallback, "What a refusal looks like")
                    self.messages.pop()
                    replies.pop()
                    declined = not made_again
                    entry = {"type": "dropped", "session": self.session, "turn": self.turn, "at": now(),
                             "reason": message.stop_reason}
                    trace.append(entry)
                    self.on_event(entry)
                if not made_again:
                    break
                again.add(sent)
        except Stopped:
            stopped = True
        final = replies[-1] if replies and not declined else None
        answer = "".join(b.text for b in (final.content if final else []) if getattr(b, "type", None) == "text")
        if stopped:
            entry = {"type": "stop", "session": self.session, "turn": self.turn, "at": now(),
                     **({"partial": "".join(partial)} if partial else {})}
            trace.append(entry)
            self.on_event(entry)
        record.reply(self.store, session=self.session, turn=self.turn, stopped=stopped,
                     analyst={"model": self.model, "effort": self.effort}, system=self.system)  # it never raises
        out = {"text": answer, "calls": list(self._calls), "usage": spent, "requests": len(replies),
               "stopped": stopped}
        self.on_event({"type": "answer", "session": self.session, "turn": self.turn, **out})
        return out

    def _request(self, runner: Any, spent: dict, replies: list, partial: list[str]) -> Any:
        """One request of the turn through the tool runner, to the response that ends it: each response streamed and
        traced, with the model that wrote it and why it stopped, and the calls of one that stopped to call tools run
        and answered. Returns the last response, None where none came; raises Stopped where the person stopped it."""
        trace = self.tools.trace
        message = None
        for stream in runner:
            if self._stopping.is_set():
                raise Stopped
            self._stream = stream
            self.on_event({"type": "reply_start", "session": self.session, "turn": self.turn})
            partial.clear()
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
                if self._stopping.is_set():  # stopped: this thread, awake, closes what it read
                    try:
                        stream.close()
                    except Exception:
                        pass
            content = [_block(b) for b in message.content]
            usage = _usage(message)
            for k, v in usage.items():
                spent[k] = spent.get(k, 0) + v
                self.usage[k] = self.usage.get(k, 0) + v
            details = getattr(message, "stop_details", None)  # why it declined, on a refusal
            entry = {"type": "assistant", "session": self.session, "turn": self.turn, "at": now(),
                     "model": message.model, "stop_reason": message.stop_reason,
                     **({"stop_details": _block(details)} if details is not None else {}),
                     "content": content, "usage": usage}
            trace.append(entry)
            self.on_event(entry)
            self.messages.append({"role": "assistant", "content": content})
            partial.clear()
            replies.append(message)
            if message.stop_reason != "tool_use":  # it stopped: the runner ends here, and its calls are not run
                continue
            self._uses = deque((b["id"], b["name"]) for b in content if b.get("type") == "tool_use")
            ran = len(self._calls)
            results = runner.generate_tool_call_response()  # cached: the runner sends these same results
            if results is not None:
                self._record_results(content, [_block(b) for b in results["content"]], ran)
            if self._stopping.is_set():
                raise Stopped
        return message

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


def _result_text(call: dict, directory: str | Path) -> str | list[dict]:
    return result_content(call.get("result", call.get("error")), directory, "result" in call)


def answer_open_calls(messages: list[dict], ran: list[dict] | None = None, directory: str | Path = ".") -> None:
    """Every tool call of the conversation's last reply answered, so the API takes the next message: a reply cut while
    its calls ran (the process stopped, or the reply failed) leaves calls with no result. A call the trace shows ran,
    one of `ran` in the order the runtime runs them, matched by its tool as the runtime matches it, is answered with
    its result; any other as not run."""
    if not messages or messages[-1]["role"] != "assistant":
        return
    uses = [b for b in messages[-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not uses:
        return
    pending = list(ran or [])
    blocks = []
    for use in uses:
        call = next((c for c in pending if c.get("tool") == use.get("name")), None)
        if call is not None:
            pending.remove(call)
            blocks.append({"type": "tool_result", "tool_use_id": use["id"], "content": _result_text(call, directory),
                           **({"is_error": True} if "error" in call else {})})
        else:
            blocks.append({"type": "tool_result", "tool_use_id": use["id"], "content": NOT_FINISHED, "is_error": True})
    messages.append({"role": "user", "content": blocks})


def history(entries: list[dict], session: str, directory: str | Path) -> tuple[list[dict], int, dict]:
    """A conversation's messages as the model saw them, its last turn, and where each turn's message starts,
    rebuilt from the trace: the turns a rewind replaced are dropped, each message's files are attached again from the
    conversation's files/, and today's date leads each message that was sent with it (`context_at`), as it was sent.
    A reply whose calls were cut off before their results were sent has each answered (answer_open_calls), so the
    conversation continues as one the API takes."""
    calls = {e["call"]: e for e in entries if e.get("type") == "call"}
    messages: list[dict] = []
    starts: dict[int, tuple[int, int | None]] = {}
    turn = 0
    ran: list[dict] = []  # the calls made since the last reply, before its results were sent
    for e in live(entries, session):
        if e["type"] == "call":
            ran.append(e)
            continue
        if e["type"] in ("user", "assistant"):
            answer_open_calls(messages, ran, directory)
            ran = []
        if e["type"] == "user":
            context = e.get("context") if e.get("context_at") == "message" else None
            starts[e["turn"]] = _append_user(messages, user_content(directory, e["text"], e.get("files"), context))
            turn = e["turn"]
        elif e["type"] == "assistant":
            messages.append({"role": "assistant", "content": e["content"]})
        elif e["type"] == "results":
            ran = []
            blocks = []
            for r in e["results"]:
                if "call" in r:
                    text = _result_text(calls[r["call"]], directory)
                else:
                    text = r["content"]
                blocks.append({"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": text,
                               **({"is_error": True} if r["is_error"] else {})})
            messages.append({"role": "user", "content": blocks})
    answer_open_calls(messages, ran, directory)
    return messages, turn, starts

