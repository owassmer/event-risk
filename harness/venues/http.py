"""JSON over HTTP for the venue clients: throttling waited out, server errors retried,
and a minimum interval between requests where a venue sets a rate.

Every read names its client: USER_AGENT, this package and its version. A source's edge can throttle or refuse a
generic library's own User-Agent: Polymarket US's throttles httpx's (harness.venues.markets), and the SEC's refuses one
that names a code host or a programming language's library (harness.venues.sec).
"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
import ssl
import threading
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

import httpx
import truststore

try:
    USER_AGENT = f"event-risk/{version('event-risk')}"
except PackageNotFoundError:  # run from a checkout that is not installed
    USER_AGENT = "event-risk"
TIMEOUT = 30.0  # seconds


def client(timeout: float = TIMEOUT) -> httpx.Client:
    """An HTTP client that names this one."""
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


# What a retry clears: the network failing (URLError, a timeout), and a connection dropped or cut short while the answer
# is read (http.client's RemoteDisconnected, a ConnectionResetError; IncompleteRead), neither of them a URLError: read
# live, Massive closes a connection without answering about once in a thousand requests, and the next read answers.
TRANSIENT = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)


CONNECTIONS = 64  # connections the process keeps open, across its sources: more than its reads run at once


class _Answer:
    """An answer as urllib's urlopen gives one: its status, its headers, and its body read once."""

    def __init__(self, response: httpx.Response):
        self.status, self.headers, self._body = response.status_code, response.headers, response.content

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class Pooled:
    """urllib's `urlopen(request, timeout=)` over connections kept open between requests, one pool for the process: a
    request to a source already met reuses a connection to it, where urlopen opens one for every request and closes it
    with the answer. Read live (2026-11-12), one of Massive's references took 103 ms on a new connection and 37 ms on
    one kept open, and a portfolio's first read makes a thousand such requests. Its contract is urlopen's, so each
    client's handling stands as written: an answer of 400 or more raises urllib.error.HTTPError with its code, headers
    and body; a timeout raises TimeoutError, and the network failing urllib.error.URLError (both TRANSIENT). The
    request's own headers are sent as it states them, and a body sent compressed is read decompressed. Every source is
    verified against the machine's own trust store, through truststore, as the Anthropic SDK's HTTP client verifies its
    own (https://truststore.readthedocs.io): some sources' chains end at a root some OpenSSL bundles lack (the ECB's, at
    Sectigo Public Server Authentication Root E46, which macOS's lacks)."""

    def __init__(self, connections: int = CONNECTIONS, *, transport: httpx.BaseTransport | None = None):
        self._limits = httpx.Limits(max_connections=connections, max_keepalive_connections=connections)
        self._transport = transport  # a test's own, in place of the network
        self._http: httpx.Client | None = None
        self._made = threading.Lock()

    def _client(self) -> httpx.Client:
        with self._made:
            if self._http is None:
                self._http = httpx.Client(limits=self._limits, follow_redirects=True, transport=self._transport,
                                          verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                                          headers={"User-Agent": USER_AGENT})
            return self._http

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> _Answer:
        try:
            response = self._client().request(request.get_method(), request.full_url,
                                              headers=dict(request.header_items()), content=request.data,
                                              timeout=TIMEOUT if timeout is None else timeout)
        except httpx.TimeoutException as e:
            raise TimeoutError(f"{request.full_url.split('?')[0]}: {e}") from None
        except httpx.HTTPError as e:
            raise urllib.error.URLError(str(e) or type(e).__name__) from None
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, response.reason_phrase,
                                         response.headers, io.BytesIO(response.content))  # type: ignore[arg-type]
        return _Answer(response)


_pooled: Pooled | None = None
_pooled_made = threading.Lock()


def pooled() -> Pooled:
    """The process's one pool of connections, for every client that reads by urlopen's contract."""
    global _pooled
    with _pooled_made:
        if _pooled is None:
            _pooled = Pooled()
        return _pooled


_reading = threading.local()


@contextmanager
def for_later():
    """Marks the reads this thread makes while it lasts as started for a derivation other than the one reading now
    (harness.store's later reads): at a source's pace (Pace), each gives way to a read a call waits on now."""
    before = getattr(_reading, "later", False)
    _reading.later = True
    try:
        yield
    finally:
        _reading.later = before


def carried(fn: Callable) -> Callable:
    """`fn`, to run on another thread with the mark this thread's reads carry (for_later), as a client's own pool runs
    the requests of one read."""
    later = getattr(_reading, "later", False)

    def run(*args: Any, **kwargs: Any) -> Any:
        if not later:
            return fn(*args, **kwargs)
        with for_later():
            return fn(*args, **kwargs)
    return run


class Pace:
    """A source's pace: its requests one at a time, each at least `interval` after the one before, and a read started
    for later (for_later) taking its turn only while no read a call waits on now is waiting for one. What a call waits on
    goes first; what is read ahead takes the turns left over, so a source's pace is spent on the answer being waited
    for before the reads started for what comes after it."""

    def __init__(self, interval: float):
        self.interval = interval
        self._turn = threading.Condition()
        self._last = 0.0
        self._taken = False
        self._waiting = 0  # reads a call waits on, waiting for their turn

    def take(self) -> None:
        """Wait for this request's turn, and its interval after the request before."""
        later = getattr(_reading, "later", False)
        with self._turn:
            if not later:
                self._waiting += 1
            try:
                while self._taken or (later and self._waiting):
                    self._turn.wait()
                self._taken = True
            finally:
                if not later:
                    self._waiting -= 1
        try:
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        finally:
            with self._turn:
                self._last = time.monotonic()
                self._taken = False
                self._turn.notify_all()


class VenueError(Exception):
    """A venue did not answer: the network failed, or the server kept erring."""


class VenueRefused(VenueError):
    """A venue refused the request itself: asking again would not change its answer."""


class Cooldown:
    """A source that did not answer is not asked again for a while: each request in that while fails at once, so a
    read that would wait on it, held under the store's lock, does not wait on it twice in a minute."""

    def __init__(self, name: str, seconds: float = 60.0):
        self.name, self.seconds = name, seconds
        self._until = 0.0

    def check(self) -> None:
        if time.monotonic() < self._until:
            raise VenueError(f"{self.name} did not answer a moment ago; it is asked again shortly")

    def failed(self) -> None:
        self._until = time.monotonic() + self.seconds


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
        self._open = opener or pooled()
        self.timeout = timeout
        self.retries = retries
        self.throttled = throttled
        self.pause = pause
        self.interval = interval  # seconds between requests: Kalshi reads twenty a second
        self.headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
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
            except TRANSIENT as e:
                if failed < self.retries:
                    failed += 1
                    time.sleep(self.pause * 2 ** failed)
                    continue
                raise VenueError(f"{method} {url.split('?')[0]}: {e}") from None
