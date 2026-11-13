"""The exchange interface, plus the HTTP plumbing every adapter shares.

Deliberate split, and the whole file is arranged around it:

  * **Pure normalizers** live in each venue module as free functions taking a
    raw payload and returning a unified type. They touch no network, so they
    are testable against a recorded payload and portable to another runtime.
  * **The client** does HTTP, pacing and error mapping, and nothing else.

Adapters compose the two. Nothing in this package reaches across venues — a
cross-venue concern (matching the same question on two exchanges, routing an
order) belongs a layer up, never in an adapter.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .errors import (
    AuthenticationError,
    BadRequest,
    ExchangeError,
    ExchangeNotAvailable,
    MarketNotFound,
    NetworkError,
    RateLimitExceeded,
    RequestTimeout,
)

MARKET_STATUSES: tuple[str, ...] = ("open", "closed", "settled", "all")
"""The status words every venue understands, with one meaning each.

  open    — listed and trading
  closed  — trading stopped, outcome not yet final
  settled — outcome final and paid
  all     — no filter

A venue that cannot filter by one of these raises `NotSupported` for it. What
it must not do is accept the word and quietly answer a different question:
`status="settled"` returning live markets on one venue and settled ones on
another is the kind of disagreement a unified API exists to remove.
"""


MAX_PAGE_LIMIT = 100
"""Rows one catalog call can return, on every venue.

Uniform rather than per-venue. Polymarket's keyset endpoint hard-caps at 100 no
matter what is asked, so a higher ceiling elsewhere would mean `limit=500`
returning 500 rows on one venue and 100 on another, with nothing saying it had
been reduced. Ask for more than this and it is clamped; the cursor is how you
get the rest.
"""


MAX_RETRY_WAIT = 10.0
"""Longest this library will sleep inside one call, in seconds.

A venue's `Retry-After` is a hint, not an instruction. Honouring a large one
literally would let the venue park a caller's thread for as long as it likes --
and in the server that thread belongs to a pool shared with every other
request, so a handful of rate-limited calls would take the whole service down.
The header's real value is still on the raised `RateLimitExceeded`, so a caller
that wants to wait longer can decide that for itself.
"""


class RateLimiter:
    """Token bucket, shared by every client for one venue in the process.

    Shared on purpose. A venue counts requests per account and per IP, not per
    client object, so six loops each politely under the limit still add up to
    being over it. Making the process rather than the object the thing that
    stays inside the budget is the only version that works.
    """

    def __init__(self, rate_per_second: float, burst: int | None = None):
        self.rate = rate_per_second
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_second)))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def _take_or_wait(self) -> float:
        """Take a token if one is there, else say how long until one is.

        The bookkeeping under the lock, shared by the blocking and the async
        paths so the two cannot drift apart on how a bucket refills.
        """
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            return (1 - self._tokens) / self.rate

    def acquire(self) -> None:
        while True:
            wait = self._take_or_wait()
            if wait == 0.0:
                return
            time.sleep(wait)  # outside the lock, so waiters do not serialise on it

    async def acquire_async(self) -> None:
        """The same budget from an event loop, without blocking it.

        The trading stack is async, and a `time.sleep` inside the loop would
        stall every socket the engine holds for the length of the wait -- the
        exact moment a stop needs to fire. Same bucket, same arithmetic, an
        `await` instead of a block.
        """
        import asyncio

        while True:
            wait = self._take_or_wait()
            if wait == 0.0:
                return
            await asyncio.sleep(wait)


def venue_reason(parsed: Any, text: str) -> str:
    """The venue's own reason, in words, from the error payloads venues send:
    `{"error": {"code", "message", "details"}}` (Kalshi), `{"error": "..."}`
    (Polymarket's CLOB), or a top-level `message` / `detail`. Falls back to
    the raw text, shortened."""
    if isinstance(parsed, dict):
        error = parsed.get("error", parsed.get("errors"))
        if isinstance(error, dict):
            message = error.get("message") or error.get("msg") or error.get("code")
            details = error.get("details") or error.get("detail")
            if message:
                if isinstance(details, str) and details and details != message:
                    return f"{message} ({details})"
                return str(message)
        if isinstance(error, str) and error:
            return error
        for key in ("message", "errorMsg", "error_message", "detail", "msg", "reason"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return value
    text = " ".join((text or "").split())
    return text[:200]


def _map_status(response: httpx.Response, label: str) -> None:
    """Turn a non-2xx response into the typed error a caller can branch on.
    The message is `<venue>: <the venue's reason>`; the raw payload and the
    status stay on the exception as `body` and `status`."""
    code = response.status_code
    if code < 400:
        return
    if code == 429:
        retry = response.headers.get("retry-after")
        raise RateLimitExceeded(
            f"{label}: rate limited",
            retry_after=float(retry) if retry and retry.replace(".", "", 1).isdigit() else None,
        )
    parsed: Any = None
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    reason = venue_reason(parsed, response.text)
    if code == 404:
        what = reason if "not found" in reason.lower() else (f"not found: {reason}" if reason else "not found")
        raise MarketNotFound(f"{label}: {what}", body=parsed, status=code)
    if code in (502, 503, 504):
        raise ExchangeNotAvailable(f"{label}: unavailable ({code})", body=parsed, status=code)
    if 500 <= code:
        raise ExchangeNotAvailable(f"{label}: server error ({code}){': ' + reason if reason else ''}", body=parsed, status=code)
    if code in (401, 403):
        raise AuthenticationError(f"{label}: credentials refused ({code}){': ' + reason if reason else ''}", body=parsed, status=code)
    if code in (400, 409, 422):
        raise BadRequest(f"{label}: {reason or f'rejected ({code})'}", body=parsed, status=code)
    raise ExchangeError(f"{label}: unexpected status {code}{': ' + reason if reason else ''}", body=parsed, status=code)


class HttpClient:
    """Paced, retrying HTTP with venue errors mapped to this library's types."""

    def __init__(
        self,
        base_url: str,
        *,
        limiter: RateLimiter | None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        attempts: int = 4,
        venue: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.limiter = limiter
        self.attempts = attempts
        self.venue = venue
        """Named at the front of every error this client raises, so a caller
        reads `kalshi: insufficient balance`, not a class name and a path."""
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """One call, paced and retried on rate limits only.

        A 429 is retried with doubling backoff because it clears on its own. A
        404 or a 400 is raised immediately: waiting will not make the request
        valid, and quietly retrying it wastes the caller's budget.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        label = self.venue or f"{self.__class__.__name__} {method} {path}"
        delay = 0.5
        last: RateLimitExceeded | None = None
        for attempt in range(self.attempts):
            if self.limiter is not None:
                self.limiter.acquire()
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                raise RequestTimeout(f"{label}: {exc}") from exc
            except httpx.HTTPError as exc:
                raise NetworkError(f"{label}: {exc}") from exc
            try:
                _map_status(response, label)
            except RateLimitExceeded as exc:
                last = exc
                if attempt == self.attempts - 1:
                    break
                time.sleep(min(exc.retry_after or delay, MAX_RETRY_WAIT))
                delay *= 2
                continue
            return response.json()
        assert last is not None
        raise last

    def get(self, path: str, params: Any = None) -> Any:
        return self.request("GET", path, params=_clean(params))

    def post(self, path: str, json: Any = None) -> Any:
        return self.request("POST", path, json=json)

    def close(self) -> None:
        self._client.close()


def check_status(status: str) -> str:
    """Validate a status word against the shared vocabulary.

    Raised rather than passed through, because every venue has its own status
    names and forwarding an unrecognised one means each venue decides for
    itself what it meant.
    """
    if status not in MARKET_STATUSES:
        raise BadRequest(
            f"unknown status {status!r}; expected one of {', '.join(MARKET_STATUSES)}"
        )
    return status


def page_limit(limit: int | None) -> int | None:
    """Clamp a requested page size to what every venue can actually serve."""
    return None if limit is None else max(1, min(limit, MAX_PAGE_LIMIT))


def _clean(params: Any) -> Any:
    """Drop `None` values so an unset optional never becomes the string "None".

    Accepts a list of pairs as well as a mapping, because a repeated parameter
    (`?id=1&id=2`) cannot be expressed as a dict and Gamma's batch lookup wants
    exactly that.
    """
    if params is None:
        return None
    if isinstance(params, dict):
        return {key: value for key, value in params.items() if value is not None}
    return [(key, value) for key, value in params if value is not None]
