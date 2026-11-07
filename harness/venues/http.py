"""JSON over HTTP for the venue clients: throttling waited out, server errors retried,
and a minimum interval between requests where a venue sets a rate."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


class VenueError(Exception):
    """A venue did not answer: the network failed, or the server kept erring."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def query(params: dict | None) -> str:
    """The query string of a venue's own parameters, passed as given: lists repeat their key,
    and booleans are the lowercase words the venues read."""
    if not params:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        for v in value if isinstance(value, (list, tuple)) else [value]:
            pairs.append((key, ("true" if v else "false") if isinstance(v, bool) else str(v)))
    return "?" + urllib.parse.urlencode(pairs)


class Http:
    def __init__(self, *, opener: Callable | None = None, timeout: float = 30, retries: int = 3,
                 throttled: int = 8, pause: float = 1.0, interval: float = 0.0, headers: dict | None = None):
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout
        self.retries = retries
        self.throttled = throttled
        self.pause = pause
        self.interval = interval  # seconds between requests: Kalshi reads twenty a second
        self.headers = {"Accept": "application/json", "User-Agent": "event-risk", **(headers or {})}
        self._last = 0.0
        self._slot = threading.Lock()  # requests from several threads keep the interval between them

    def _wait(self, error: urllib.error.HTTPError, attempt: int) -> float:
        after = error.headers.get("Retry-After") if error.headers is not None else None
        try:
            return min(float(after), 60.0)
        except (TypeError, ValueError):
            return min(self.pause * 2 ** attempt, 60.0)

    def call(self, method: str, url: str, body: Any = None) -> tuple[int, Any]:
        """The status and JSON body of one request. 4xx other than 429 is an answer, returned."""
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data is not None else {}))
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        throttled = failed = 0
        while True:
            with self._slot:  # each request reserves its moment, interval after the one before
                now = time.monotonic()
                at = max(now, self._last + self.interval)
                self._last = at
            if at > now:
                time.sleep(at - now)
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return response.status, json.loads(response.read() or b"null")
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code == 429 and throttled < self.throttled:
                    throttled += 1
                    time.sleep(self._wait(e, throttled))
                    continue
                if e.code >= 500 and failed < self.retries:
                    failed += 1
                    time.sleep(self.pause * 2 ** failed)
                    continue
                if 400 <= e.code < 500:
                    try:
                        return e.code, json.loads(raw)
                    except ValueError:
                        return e.code, {"error": raw[:300].decode("utf-8", "replace")}
                raise VenueError(f"{method} {url.split('?')[0]} answered {e.code}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if failed < self.retries:
                    failed += 1
                    time.sleep(self.pause * 2 ** failed)
                    continue
                raise VenueError(f"{method} {url.split('?')[0]}: {e}") from None
