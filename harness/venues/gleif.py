"""GLEIF, the Global Legal Entity Identifier Foundation: the legal entity behind a security, by its ISIN, public and
with no key; how a company held by one security is known as the issuer of another (harness.derive.identity).

An LEI (ISO 17442) names one legal entity. GLEIF keeps every LEI's record and, from the national numbering agencies
that issue ISINs (through the Association of National Numbering Agencies), which LEI is the issuer of each ISIN.
`GET https://api.gleif.org/api/v1/lei-records?filter[isin]={ISIN}` answers the records of the LEIs mapped to that ISIN
(https://documenter.getpostman.com/view/7679680/SVYrrxuU, "Finding LEI Records by ISIN": the `isin` filter used
alone), as JSON:API: `{"data": [...], "meta": {"goldenCopy": {"publishDate"}, "pagination": {...}}, "links"}`, each
record `{"type": "lei-records", "id": LEI, "attributes": {"lei", "entity": {"legalName": {"name"}, "jurisdiction",
"status", ...}, "registration": {"status", ...}, ...}}`. An ISIN with no mapping, and a string that is no ISIN, are
answered 200 with no records. Read live: a depositary receipt's US ISIN is mapped to the company whose shares it
represents (Petrobras's receipts, US71654V4086 and US71654V1017, to 5493000J801JZRCMFE49, TSMC's, Novo Nordisk's,
Toyota's, Alibaba's and Infosys's likewise), and of home shares, Denmark's and Japan's are mapped and Taiwan's,
Brazil's and Korea's not: a company's home shares held in a fund are known by its issuer's LEI as the fund's own Form
N-PORT report states it (harness.venues.sec), which is the other half. The filter takes several ISINs, comma-separated,
and answers their records without saying which ISIN each is for, so each ISIN is asked alone.

What is kept: `{"status": "OK", "isin", "records": [{"lei", "name", "jurisdiction", "entity_status",
"registration_status"}], "published"}`, `NOT_FOUND` with no records where GLEIF maps the ISIN to none, an answer kept as
any is. A mapping is about no date, as OpenFIGI's are (harness.observations.UNDATED).

GLEIF allows each user 60 requests a minute (the same page, "Rate limiting is currently set at 60 requests, per minute,
per user, for all users"): requests go at once while fewer than WINDOW_REQUESTS were made in the last WINDOW seconds,
and wait for the window otherwise, so a portfolio's few asks are not spaced and a long list keeps to the limit. A 429
is waited out (its Retry-After where it gives one, the window otherwise); a 5xx or a connection that fails is asked
again ATTEMPTS times; past them the read fails (VenueError), so the reads that wait on it
wait (harness.derive.identity), never standing as an answer of no LEI.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Callable

from harness.venues.http import TRANSIENT, VenueError, now, pooled

URL = "https://api.gleif.org/api/v1/lei-records"
WINDOW, WINDOW_REQUESTS = 60.0, 60  # GLEIF's limit: 60 requests a minute
ATTEMPTS = 4  # a read asked this many times before it fails
PAUSE = 2.0  # seconds between attempts after a 5xx or a failed connection
TIMEOUT = 20.0
ISIN = re.compile(r"[A-Z]{2}[0-9A-Z]{9}[0-9]")
LEI = re.compile(r"[0-9A-Z]{18}[0-9]{2}")


def valid_lei(value: Any) -> bool:
    """Whether a string is an LEI: twenty characters, the last two its check digits, the whole read as a number (each
    letter its value from 10 for A to 35 for Z) leaving 1 when divided by 97 (ISO 17442, the check of ISO 7064's MOD
    97-10). A fund's report writes N/A, zeros, or a name where it has none."""
    text = str(value or "").strip().upper()
    if not LEI.fullmatch(text):
        return False
    return int("".join(str(int(c, 36)) for c in text)) % 97 == 1


class GLEIF:
    def __init__(self, *, opener: Callable | None = None, timeout: float = TIMEOUT):
        self._open = opener or pooled()
        self.timeout = timeout
        self._sent: deque[float] = deque()  # when each request in the window was made
        self._lock = threading.Lock()

    def _slot(self) -> None:
        """Wait until a request keeps within the limit, and count it."""
        while True:
            with self._lock:
                t = time.monotonic()
                while self._sent and t - self._sent[0] >= WINDOW:
                    self._sent.popleft()
                if len(self._sent) < WINDOW_REQUESTS:
                    self._sent.append(t)
                    return
                wait = WINDOW - (t - self._sent[0])
            time.sleep(max(wait, 0.05))

    def _get(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.api+json", "User-Agent": "event-risk"})
        failed = 0
        while True:
            self._slot()
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429 and failed < ATTEMPTS - 1:
                    failed += 1
                    after = e.headers.get("Retry-After") if e.headers else None
                    time.sleep(float(after) if after and str(after).isdigit() else WINDOW)
                    continue
                if e.code >= 500 and failed < ATTEMPTS - 1:
                    failed += 1
                    time.sleep(PAUSE)
                    continue
                raise VenueError(f"GLEIF answered {e.code}") from None
            except (TRANSIENT + (json.JSONDecodeError,)) as e:
                if failed < ATTEMPTS - 1:
                    failed += 1
                    time.sleep(PAUSE)
                    continue
                raise VenueError(f"GLEIF: {e}") from None

    def lei(self, isin: str, asof: str) -> dict:
        """The legal entities GLEIF maps an ISIN to: `records`, each its LEI, legal name, jurisdiction and the status of
        the entity and of its registration; NOT_FOUND where it maps none. A string that is no ISIN is not asked."""
        isin = str(isin).strip().upper()
        url = URL + "?" + urllib.parse.urlencode({"filter[isin]": isin})
        retrieved = now()
        if not ISIN.fullmatch(isin):
            body: dict = {"data": []}
        else:
            body = self._get(url)
        records = [{"lei": r.get("id"), "name": ((r.get("attributes") or {}).get("entity") or {}).get("legalName",
                                                                                                    {}).get("name"),
                    "jurisdiction": ((r.get("attributes") or {}).get("entity") or {}).get("jurisdiction"),
                    "entity_status": ((r.get("attributes") or {}).get("entity") or {}).get("status"),
                    "registration_status": ((r.get("attributes") or {}).get("registration") or {}).get("status")}
                   for r in body.get("data") or [] if isinstance(r, dict) and r.get("id")]
        payload = {"status": "OK" if records else "NOT_FOUND", "isin": isin, "records": records,
                   "published": ((body.get("meta") or {}).get("goldenCopy") or {}).get("publishDate")}
        return {"payload": payload, "request": {"method": "GET", "url": url}, "retrieved_at": retrieved,
                "range": (asof, asof)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        return {"lei": lambda subject, covering: self.lei(subject, covering[1])}
