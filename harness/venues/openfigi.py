"""OpenFIGI: the listings of a share class, by its ISIN, public and with no key; how a fund's holding listed abroad
is found listed in the US, on an exchange or over the counter.

`POST https://api.openfigi.com/v3/mapping` with a list of jobs `[{"idType": "ID_ISIN", "idValue": isin}]`
(https://www.openfigi.com/api/documentation) answers one result per job, in order: `{"data": [...]}`, every listing of
the security on every exchange, each `{figi, name, ticker, exchCode, compositeFIGI, shareClassFIGI, securityType,
securityType2, marketSector, securityDescription}`; or `{"warning": "No identifier found."}`; or `{"error": ...}`.
An ISIN names one share class, so every listing it maps to is that share class's, under one `shareClassFIGI`, and a
depositary receipt, a security of its own with an ISIN of its own, is not among them. `exchCode` "US" is the US
composite, the listing across every US venue, with `compositeFIGI` its FIGI and `ticker` its US ticker, on an exchange
or over the counter; the other US codes are its venues'. What the look-through reads is kept: the US composites, and
every listing's composite FIGI, by which a holding of the same share class on any exchange is known:
`{"status": "OK", "isin", "listings": [...], "composites": [...]}`, `NOT_FOUND` where OpenFIGI has none for the ISIN.
A US ticker's share class (`TICKER` with `exchCode` US) and a share
class's listings (`ID_BB_GLOBAL_SHARE_CLASS_LEVEL`) are mapped the same way, `{"ticker", "share_classes"}` and
`{"share_class", "composites"}`: how a company held through a US listing is known by its share classes' listings
abroad. A holding a fund names only by its CUSIP, as Form N-PORT
often does, is mapped by it (`ID_CUSIP`, or `ID_CINS` for the CUSIP form of an issuer outside the US and Canada, whose
first character is a letter; the same page), and kept as `{"cusip", "listings", "composites"}`. Each kept answer
carries `parse`, the form this client keeps answers in (PARSE), so one kept in another form is read again, not served
(harness.observations).

Without a key a request takes at most 10 jobs and 25 requests are answered a minute, from the first request; with
one, sent as the header `X-OPENFIGI-APIKEY`, 100 jobs and 250 requests a minute (the same page, "Rate Limits"; each
window 60 seconds from its first request, and nothing said of requests at once). A request past them is answered 429
with `retry-after`, the seconds to wait (`ratelimit-reset` the same on every answer), and one with more jobs than its
limit 413. So identifiers are mapped as many to a request as the key allows, and a 429 is waited out; without a key
one request at a time, 250 ISINs in the first moments and 250 a minute after; with one, PARALLEL at once, the chunks
of one mapping and the mappings of several kinds side by side, since one request takes some 0.3 to 0.9 seconds, so
that one at a time used a fraction of the key's window. A chunk OpenFIGI does not answer leaves only its own
identifiers to be read again. The key is optional: where OPENFIGI_API_KEY is not set,
the client reads as it does without one (harness.analyst.clients). Where OpenFIGI does not answer, it is not asked again
for a minute (harness.venues.http.Cooldown), and the rows it would confirm wait (harness.derive.identity).
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from typing import Any, Callable

from harness.venues.http import TRANSIENT, Cooldown, VenueError, VenueRefused, now, pooled

URL = "https://api.openfigi.com/v3/mapping"
JOBS = 10  # the most a request takes without a key
KEYED_JOBS = 100  # with one
INTERVAL = 0.0  # seconds between requests: OpenFIGI's window allows 25 at once, and a 429 says how long to wait
PARALLEL = 3  # requests at once with a key: within its 250 a minute at OpenFIGI's pace, a 429 waited out past it
US = "US"  # the US composite's exchange code
PARSE = 2  # the form answers are kept in: 2, an ISIN's mapping without its listings' own FIGIs and exchanges
# A kind's own form where it differs: a share class's mapping keeps its US listings from 3, as an ISIN's does.
FORMS = {"figi_class": 3}
# A job whose identifier is not in its type's form is answered `{"error": "Invalid idValue format."}`: it names
# nothing, an answer kept as NOT_FOUND (read live: a fund's own code for a swap, 594MSTZJ1, where a CUSIP goes).
# Any other job error is OpenFIGI not answering it just now, asked again at the next read.
INVALID = "Invalid idValue"


def _by_isin(isin: str, data: list[dict]) -> dict:
    return {"isin": isin, "listings": [d for d in data if d.get("exchCode") == US],
            "composites": sorted({d["compositeFIGI"] for d in data if d.get("compositeFIGI")})}


def _sectors(data: list[dict]) -> list[dict]:
    """What each listing OpenFIGI gives says the security is: its market sector and its types, each distinct once."""
    found = {(d.get("marketSector"), d.get("securityType"), d.get("securityType2")) for d in data}
    return [{"marketSector": a, "securityType": b, "securityType2": c} for a, b, c in sorted(found, key=str)]


def _by_cusip(cusip: str, data: list[dict]) -> dict:
    return {"cusip": cusip, **{k: v for k, v in _by_isin(cusip, data).items() if k != "isin"},
            "sectors": _sectors(data)}


def _digits(text: str) -> str:
    return "".join(str(int(c, 36)) for c in text)


def isin_valid(value: str) -> bool:
    """An ISIN: two letters, nine characters, and its check digit (ISO 6166, the Luhn sum of its digits)."""
    v = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", v):
        return False
    total = 0
    for k, c in enumerate(reversed(_digits(v[:-1]))):
        d = int(c) * (2 if k % 2 == 0 else 1)
        total += d // 10 + d % 10
    return (10 - total % 10) % 10 == int(v[-1])


def cusip_valid(value: str) -> bool:
    """A CUSIP: eight characters and its check digit (the sum of the doubled-alternate digits, modulo 10)."""
    v = str(value or "").strip().upper()
    if not re.fullmatch(r"[0-9A-Z*@#]{8}[0-9]", v):
        return False
    total = 0
    for k, c in enumerate(v[:-1]):
        n = int(c, 36) if c.isalnum() else {"*": 36, "@": 37, "#": 38}[c]
        n = n * 2 if k % 2 == 1 else n
        total += n // 10 + n % 10
    return (10 - total % 10) % 10 == int(v[-1])


FIGI = re.compile(r"BBG[0-9A-Z]{9}")


def _index_job(identifier: str) -> dict:
    """An identifier a fund's report gives an index by, asked of OpenFIGI in the form it has: a FIGI (with or without
    Bloomberg's " Index" after it), an ISIN, a CUSIP, or else a Bloomberg index ticker, the identifier with " Index"
    after it, as OpenFIGI keeps an index's ticker ("SPX Index"). A FIGI is tried first: one beginning "BB" can pass an
    ISIN's check digit as Barbados's (read live, Direxion's Russell 2000, "BBG000HS1999 Index", asked as an ISIN, found
    nothing)."""
    v = identifier.strip()
    bare = re.sub(r"\s+index$", "", v, flags=re.I)
    if FIGI.fullmatch(bare.upper()):
        return {"idType": "ID_BB_GLOBAL", "idValue": bare.upper()}
    if isin_valid(bare):
        return {"idType": "ID_ISIN", "idValue": bare.upper()}
    if cusip_valid(bare):
        return {"idType": "ID_CUSIP", "idValue": bare.upper()}
    return {"idType": "TICKER", "idValue": f"{bare} Index"}


def _by_index(identifier: str, data: list[dict]) -> dict:
    """An index identifier as OpenFIGI reads it: the index it names (marketSector `Index`), or the securities it names
    instead."""
    index = next((d for d in data if d.get("marketSector") == "Index"), None)
    return {"identifier": identifier,
            "index": {k: index.get(k) for k in ("figi", "name", "ticker", "securityType")} if index else None,
            "securities": [{k: d.get(k) for k in ("figi", "name", "ticker", "exchCode", "marketSector",
                                                  "securityType")} for d in data if d.get("marketSector") != "Index"]}


def _cusip_job(cusip: str) -> dict:
    """A CUSIP, or a CINS, the CUSIP form for an issuer outside the US and Canada, whose first character is a letter."""
    return {"idType": "ID_CINS" if cusip[:1].isalpha() else "ID_CUSIP", "idValue": cusip}


def _by_ticker(ticker: str, data: list[dict]) -> dict:
    return {"ticker": ticker, "share_classes": sorted({d["shareClassFIGI"] for d in data if d.get("shareClassFIGI")})}


def _by_class(share_class: str, data: list[dict]) -> dict:
    return {"share_class": share_class, "listings": [d for d in data if d.get("exchCode") == US],
            "composites": sorted({d["compositeFIGI"] for d in data if d.get("compositeFIGI")})}


def local_tickers(symbol: str) -> list[str]:
    """The forms a listing's ticker on its exchange can take at OpenFIGI, from its symbol as Yahoo writes it, its
    exchange's suffix taken off: as written; a Hong Kong number without its leading zeros (`0700.HK` is `700`); a class
    after a dash joined to it, as OpenFIGI keeps a Nordic one (`ERIC-B.ST` is `ERICB`), or after a slash, as it keeps
    a London or Toronto one (`BT-A.L` is `BT/A`). Read live, 2026-11-12: `700` at XHKG, `ERICB` at XSTO found; `0700`,
    `ERIC B` found nothing."""
    base = symbol.rsplit(".", 1)[0]
    found = [base, base.lstrip("0") if base.isdigit() else base, base.replace("-", ""), base.replace("-", "/")]
    return [t for t in dict.fromkeys(found) if t]


def _by_listing(subject: str, data: list[dict]) -> dict:
    symbol, _, mic = subject.rpartition("@")
    return {"symbol": symbol, "mic": mic, "ticker": (data[0].get("ticker") if data else None),
            "share_classes": sorted({d["shareClassFIGI"] for d in data if d.get("shareClassFIGI")}),
            "composites": sorted({d["compositeFIGI"] for d in data if d.get("compositeFIGI")})}


# What is mapped, by the data kind it is kept as: the job for a subject, and what is kept of its answer.
KINDS = {
    "figi": (lambda isin: {"idType": "ID_ISIN", "idValue": isin}, _by_isin),
    "figi_cusip": (_cusip_job, _by_cusip),
    "figi_ticker": (lambda ticker: {"idType": "TICKER", "idValue": ticker.replace(".", "/"), "exchCode": US},
                    _by_ticker),
    "figi_class": (lambda figi: {"idType": "ID_BB_GLOBAL_SHARE_CLASS_LEVEL", "idValue": figi}, _by_class),
    # a listing abroad by its symbol and its exchange's MIC, `{symbol}@{MIC}`: one job for each form its ticker can
    # take there (local_tickers), the first that names a security kept (OpenFIGI, mapping, `micCode`)
    "figi_listing": (lambda subject: [{"idType": "TICKER", "idValue": t, "micCode": subject.rpartition("@")[2]}
                                      for t in local_tickers(subject.rpartition("@")[0])], _by_listing),
    "figi_index": (_index_job, _by_index),
}


class OpenFIGI:
    def __init__(self, *, key: str | None = None, opener: Callable | None = None, timeout: float = 30,
                 interval: float = INTERVAL, sleep: Callable[[float], None] = time.sleep):
        self._key = key
        self.jobs = KEYED_JOBS if key else JOBS
        self._open = opener or pooled()
        self.timeout = timeout
        self.interval = interval
        self._sleep = sleep
        self._last = 0.0
        self._slots = threading.Semaphore(PARALLEL if key else 1)  # requests at once: one without a key
        self.cooldown = Cooldown("OpenFIGI")

    def _post(self, jobs: list[dict]) -> list[dict]:
        self.cooldown.check()
        try:
            with self._slots:
                return self._answer(jobs)
        except VenueError:
            self.cooldown.failed()
            raise

    def _answer(self, jobs: list[dict]) -> list[dict]:
        body = json.dumps(jobs).encode()
        request = urllib.request.Request(URL, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "event-risk",
            **({"X-OPENFIGI-APIKEY": self._key} if self._key else {})})
        failed = 0
        while True:
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    try:
                        after = float(e.headers.get("retry-after") or e.headers.get("ratelimit-reset") or 10)
                    except (TypeError, ValueError):
                        after = 10.0
                    self._sleep(min(max(after, 1.0), 60.0))
                    continue
                if e.code >= 500 and failed < 3:
                    failed += 1
                    self._sleep(2.0 * failed)
                    continue
                error = VenueRefused if e.code == 400 else VenueError  # a 400 refuses a request of ours as malformed
                raise error(f"OpenFIGI answered {e.code}") from None
            except TRANSIENT as e:
                if failed < 3:
                    failed += 1
                    self._sleep(2.0 * failed)
                    continue
                raise VenueError(f"OpenFIGI: {e}") from None

    def _map(self, kind: str, subjects: list[str], day: str) -> dict[str, dict]:
        """Each subject's mapping, as many to a request as the key allows, by subject, as the store keeps each (KINDS).
        A subject asks one job, or, for a listing by its symbol, one for each form its ticker can take there, the
        first that names a security answering it."""
        job, shape = KINDS[kind]
        asked = {x: (lambda j: j if isinstance(j, list) else [j])(job(x)) for x in subjects}
        chunks, chunk, size = [], [], 0
        for x in subjects:  # as many subjects to a request as their jobs fit within the key's limit
            if chunk and size + len(asked[x]) > self.jobs:
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append(x)
            size += len(asked[x])
        if chunk:
            chunks.append(chunk)
        form = FORMS.get(kind, PARSE)

        def one(chunk: list[str]) -> dict[str, dict]:
            jobs = [j for x in chunk for j in asked[x]]
            retrieved = now()
            answers = iter(self._post(jobs))
            found: dict[str, dict] = {}
            for subject in chunk:
                mine = [next(answers) for _ in asked[subject]]
                hit = next((a for a in mine if isinstance(a, dict) and a.get("data")), None)
                errors = [a.get("error") for a in mine if isinstance(a, dict) and a.get("error")]
                if hit is None and any(not str(e).startswith(INVALID) for e in errors):
                    continue  # a job OpenFIGI could not answer is not kept, and the next read asks again
                data = (hit or {}).get("data") or []
                error = errors[0] if errors and hit is None else None
                payload = {"status": "OK" if data else "NOT_FOUND", **shape(subject, data), "parse": form,
                           **({"error": error} if error else {})}
                found[subject] = {"payload": payload, "retrieved_at": retrieved, "range": (day, day),
                                  "request": {"method": "POST", "url": URL, "body": [j for j in asked[subject]]}}
            return found

        if len(chunks) <= 1:
            return one(chunks[0]) if chunks else {}
        out: dict[str, dict] = {}
        failed: VenueError | None = None
        with ThreadPoolExecutor(max_workers=min(PARALLEL, len(chunks))) as pool:  # the slots bound them at once
            for got in [pool.submit(one, c) for c in chunks]:
                try:
                    out.update(got.result())
                except VenueError as e:  # its identifiers are read again where they are asked
                    failed = e
        if failed is not None and not out:
            raise failed
        return out

    def isins(self, isins: list[str], day: str) -> dict[str, dict]:
        """Each ISIN's US listings, and every listing's composite FIGI."""
        return self._map("figi", isins, day)

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        """`figi` by ISIN, `figi_cusip` by CUSIP, `figi_ticker` by US ticker, `figi_class` by share-class FIGI,
        `figi_listing` by a listing abroad's symbol and its exchange's MIC, and `figi_index` by an index's identifier
        as a fund's report gives it; each one's `many` maps many subjects at once, as the store's prefetch asks."""
        def fetcher(kind: str) -> Callable[[str, Any], dict]:
            def one(subject: str, covering: Any) -> dict:
                found = self._map(kind, [subject], covering[1]).get(subject)
                if found is None:
                    raise VenueError(f"OpenFIGI could not map {subject} just now")
                return found

            one.many = lambda subjects, covering: self._map(kind, list(subjects), covering[1])  # type: ignore
            one.parse = FORMS.get(kind, PARSE)  # type: ignore[attr-defined]
            return one

        return {kind: fetcher(kind) for kind in KINDS}
