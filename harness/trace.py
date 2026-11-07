"""The trace of a store's conversations, `stores/{name}/trace.jsonl`: the conversation itself.

Each line is one event: a session's start, a person's message, the analyst's reply with its thinking, text,
tool calls, and token usage, each tool call with its input and its result or error, and which call answered
which tool_use. A reader replays a conversation by reading it; every number in it re-derives from the store's
data and judgment cache, with no keys and no judge.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from harness import credentials


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Trace:
    """The append-only trace of a store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, entry: dict) -> None:
        """One line. A configured secret never enters it."""
        where = credentials.find(entry, names=False)
        if where:
            raise ValueError(f"the trace is written without credentials; one is at {where}")
        line = json.dumps(entry, ensure_ascii=False, allow_nan=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def next_call(self) -> int:
        return 1 + max((e["call"] for e in self.entries() if e.get("type") == "call"), default=0)
