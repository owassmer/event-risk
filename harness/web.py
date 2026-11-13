"""The web as the analyst reaches it: search_web and fetch_source, each one call to the Messages API with a
server-side tool.

A search is one Messages call with server-side web search, `web_search_20250305` run by a small model
(https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool): the tool takes `max_uses`,
`allowed_domains` or `blocked_domains` (not both; bare domains with an optional path, no scheme), and
`user_location` (`{type: "approximate", city, region, country, timezone}`). It returns the results as data (URL,
title, page age, and the passages quoted from each), never a synthesis. Each search costs about a cent on the key.

A result's `page_age` is "when the site was last updated" (the tool's documentation, above), given as a date ("April
30, 2025" in its example) or as an age (read live, 1,291 of 1,622 results: "N days ago", the
rest none). Each result's `date` is that date, ISO 8601: a stated date read in its format, and an age taken back from
the moment the search answered, in UTC, a month or a year back by the calendar (`dated`). A page whose age the search
does not give has none.

A fetch is one Messages call with server-side web fetch, `web_fetch_20260309`, at most 100,000 tokens of content
(https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool), which returns the page's full text,
a PDF as base64 that `pdftotext` reads. A page the fetcher cannot reach answers an error code such as
`url_not_accessible`, and the page is then downloaded directly. The code reads no date out of a page: the date it
speaks as of is the analyst's to state, citing the page.
"""

from __future__ import annotations

import base64
import calendar
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from harness import claude
from harness.venues.http import TRANSIENT

API = claude.API
MODEL = "claude-haiku-5-5"  # the small model that runs each search and fetch
SEARCH_TOOL = "web_search_20250305"
FETCH_TOOL = "web_fetch_20260309"
FETCH_MAX_CONTENT_TOKENS = 100_000
SEARCH_PARAMS = ("max_uses", "allowed_domains", "blocked_domains", "user_location")
WRAP_AT = 8000
MAX_DIRECT_BYTES = 25 * 1024 * 1024

SEARCH_SYSTEM = (
    "You are the retrieval step of a web search tool used by another AI agent. That agent reads only (a) the list "
    "of search results and (b) the passages you cite. It never sees your own words.\n\n"
    "1. Search for the query. If the results are clearly off-target, refine the query and search again.\n"
    "2. Then go through the results that bear on the query, most relevant first. For each, write one short sentence "
    "grounded in that result, with citations, so that its most relevant passages are quoted. Cite several passages "
    "from a source when several are relevant. Prefer primary and official sources over secondary ones.\n\n"
    "Do not answer the question, add analysis, or use knowledge from outside the results."
)


class WebError(Exception):
    """The search or the fetch failed. Its message is what the analyst reads: what happened, in plain words."""


# A search's own error codes (https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool), in words.
SEARCH_ERRORS = {"too_many_requests": "too many searches at once; try again shortly",
                 "invalid_input": "the query was not accepted", "max_uses_exceeded": "it ran out of its max_uses",
                 "query_too_long": "the query is too long", "unavailable": "search was unavailable just now"}


def _why(error: Exception) -> str:
    """Why a direct download failed, as the analyst reads it."""
    if isinstance(error, urllib.error.HTTPError):
        return {401: "the site requires a login", 403: "the site refused the request", 404: "the site has no such page",
                410: "the site has no such page", 429: "the site is limiting requests; try again later"}.get(
            error.code, "the site's server failed" if error.code >= 500 else "the site refused the request")
    if isinstance(error, TimeoutError) or "timed out" in str(error):
        return "the site did not answer in time"
    if isinstance(error, urllib.error.URLError):
        return "the site could not be reached"
    if isinstance(error, WebError):
        return str(error)
    return "the site's answer could not be read"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


AGE = re.compile(r"^(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$", re.IGNORECASE)
DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y")


def dated(page_age: str | None, moment: datetime) -> str | None:
    """The date a search result's page_age gives, ISO 8601: a date in its stated format, or an age taken back from the
    moment the search answered; None for a page whose age is not given, or given in a form not read here."""
    if not isinstance(page_age, str) or not page_age.strip():
        return None
    text = " ".join(page_age.split())
    found = AGE.match(text)
    if found:
        n, unit = int(found.group(1)), found.group(2).lower()
        if unit in ("month", "year"):
            months = n * (12 if unit == "year" else 1)
            year, month = divmod(moment.year * 12 + moment.month - 1 - months, 12)
            day = min(moment.day, calendar.monthrange(year, month + 1)[1])
            return date(year, month + 1, day).isoformat()
        return (moment - timedelta(**{f"{unit}s": n})).date().isoformat()
    for form in DATE_FORMATS:
        try:
            return datetime.strptime(text, form).date().isoformat()
        except ValueError:
            continue
    return None


def private_host(hostname: str) -> bool:
    """Hosts Anthropic's fetcher cannot reach: downloaded directly."""
    h = hostname.strip("[]").lower()
    if h == "localhost" or re.search(r"\.(localhost|local|internal|lan|home\.arpa)$", h):
        return True
    try:
        return ipaddress.ip_address(h).is_private or ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def normalize(text: str) -> str:
    """Trailing whitespace dropped; overlong lines wrapped at whitespace."""
    out = []
    for line in re.sub(r"\s+$", "", text).split("\n"):
        while len(line) > WRAP_AT:
            cut = line.rfind(" ", 0, WRAP_AT)
            at = cut + 1 if cut > WRAP_AT // 2 else WRAP_AT
            out.append(line[:at])
            line = line[at:]
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- the client


class Web:
    """The web on the Claude key (harness.claude); `key` gives one directly."""

    def __init__(self, key: str | None = None, *, opener: Callable | None = None, model: str = MODEL,
                 timeout: float = 180, credential: claude.Credential | None = None):
        self.credential = credential if credential is not None else \
            claude.Credential(claude.KEY, key) if key else claude.credential()
        self._open = opener or urllib.request.urlopen
        self.model = model
        self.timeout = timeout

    def _call(self, body: dict) -> dict:
        if self.credential is None:
            raise WebError("the web is not available here")
        request = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST",
                                         headers=claude.headers(self.credential))
        for attempt in range(3):
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                text = e.read()[:500].decode("utf-8", "replace")
                if (e.code in (429, 529) or e.code >= 500) and attempt < 2:
                    after = e.headers.get("retry-after") if e.headers is not None else None
                    try:
                        wait = min(float(after), 30.0)
                    except (TypeError, ValueError):
                        wait = 2.0 * (attempt + 1)
                    time.sleep(wait)
                    continue
                if e.code == 400:  # the request's shape: for a search, the analyst's parameters
                    try:
                        text = json.loads(text).get("error", {}).get("message") or text
                    except (ValueError, AttributeError):
                        pass
                    raise WebError(f"the request was refused: {text}") from None
                if e.code in (401, 403):
                    raise WebError("the web is not available here") from None
                raise WebError("the web is busy just now; try again shortly") from None
            except TRANSIENT:  # the network failed, or the connection dropped or was cut short mid-answer
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise WebError("the web could not be reached just now; try again shortly") from None
        raise WebError("the web is busy just now; try again shortly")

    def _turn(self, base: dict, content: str) -> list[dict]:
        """One server-tool turn to completion, resuming pause_turn; every content block in order."""
        blocks: list[dict] = []
        messages: list[dict] = [{"role": "user", "content": content}]
        base = dict(base)
        for _ in range(5):
            response = self._call({**base, "messages": messages})
            blocks.extend(response.get("content") or [])
            if response.get("stop_reason") != "pause_turn":
                return blocks
            messages = [messages[0], {"role": "assistant", "content": list(blocks)}]
            base.pop("tool_choice", None)
        return blocks

    def search(self, text: str, params: dict | None = None) -> dict:
        """Search results as data: the queries run, and each result's URL, title, date, and cited passages."""
        params = dict(params or {})
        unknown = [k for k in params if k not in SEARCH_PARAMS]
        if unknown:
            raise WebError(f"the search tool's parameters are {', '.join(SEARCH_PARAMS)}; not {', '.join(unknown)}")
        if params.get("allowed_domains") and params.get("blocked_domains"):
            raise WebError("allowed_domains and blocked_domains do not combine")
        tool = {"type": SEARCH_TOOL, "name": "web_search", "max_uses": params.pop("max_uses", 3), **params}
        blocks = self._turn({"model": self.model, "max_tokens": 4096, "thinking": {"type": "disabled"},
                             "system": SEARCH_SYSTEM, "tools": [tool],
                             "tool_choice": {"type": "tool", "name": "web_search"}}, f"Query: {text}")
        answered = datetime.now(timezone.utc)
        queries, by_id, sources, errors = [], {}, {}, []
        for b in blocks:
            if b.get("type") == "server_tool_use" and b.get("name") == "web_search":
                q = str((b.get("input") or {}).get("query", ""))
                queries.append(q)
                by_id[b.get("id")] = q
            elif b.get("type") == "web_search_tool_result":
                q = by_id.get(b.get("tool_use_id"), "")
                content = b.get("content")
                if isinstance(content, list):
                    for r in content:
                        if r.get("type") == "web_search_result" and r.get("url") not in sources:
                            sources[r["url"]] = {"url": r["url"], "title": r.get("title") or "",
                                                 "date": dated(r.get("page_age"), answered), "query": q,
                                                 "passages": []}
                elif isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
                    code = str(content.get("error_code"))
                    errors.append(f'the search "{q}" failed: {SEARCH_ERRORS.get(code, code.replace("_", " "))}')
        for b in blocks:
            for c in b.get("citations") or [] if b.get("type") == "text" else []:
                if c.get("type") != "web_search_result_location" or not c.get("url"):
                    continue
                s = sources.setdefault(c["url"], {"url": c["url"], "title": c.get("title") or "", "date": None,
                                                  "query": "", "passages": []})
                passage = str(c.get("cited_text") or "").strip()
                if passage and passage not in s["passages"]:
                    s["passages"].append(passage)
        return {"queries": queries, "results": list(sources.values()), "errors": errors}

    def fetch(self, url: str) -> dict:
        """The page's full text: through the web fetch tool, else a direct download."""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise WebError(f"{url!r} is not an http or https URL: a page is fetched by one")
        if not private_host(parsed.hostname):  # the web fetch tool reaches public hosts only
            try:
                found = self._anthropic_fetch(url)
            except WebError:
                found = None
            if found is not None:
                return found
        attempt = 0
        while True:  # the direct download is the last resort; its failure is the fetch's
            try:
                return self._direct_fetch(url)
            except Exception as e:
                # a network failure, or a connection dropped or cut short, is read again once; a site's refusal is not
                again = isinstance(e, TRANSIENT) and not isinstance(e, urllib.error.HTTPError) and attempt == 0
                if not again:
                    raise WebError(f"the page could not be read: {_why(e)}") from None
            attempt += 1
            time.sleep(2.0)

    def _anthropic_fetch(self, url: str) -> dict | None:
        """The page through the web fetch tool; None where the tool returns no page (an error code such as
        `url_not_accessible`, or content it gives in a form other than text or a PDF), which is then downloaded."""
        blocks = self._turn({"model": self.model, "max_tokens": 1024, "thinking": {"type": "disabled"},
                             "tools": [{"type": FETCH_TOOL, "name": "web_fetch", "max_uses": 1,
                                        "max_content_tokens": FETCH_MAX_CONTENT_TOKENS, "allowed_callers": ["direct"],
                                        "use_cache": True, "citations": {"enabled": False}}],
                             "tool_choice": {"type": "tool", "name": "web_fetch"}},
                            f"Fetch this exact URL with web_fetch, then reply with only: OK\n\n{url}")
        result = next((b.get("content") for b in blocks if b.get("type") == "web_fetch_tool_result"), None)
        if not result or result.get("type") == "web_fetch_tool_result_error":
            return None
        document = result.get("content") or {}
        source = document.get("source") or {}
        media = str(source.get("media_type") or "text/plain")
        if source.get("type") == "text":
            text = str(source.get("data") or "")
        elif source.get("type") == "base64" and media == "application/pdf":
            text = pdf_text(base64.b64decode(source.get("data") or ""))
        else:
            return None
        return {"url": str(result.get("url") or url), "title": document.get("title"),
                "retrieved_at": result.get("retrieved_at") or _now(), "via": "anthropic", "content_type": media,
                "text": normalize(text)}

    def _direct_fetch(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0 Safari/537.36",
            "accept": "text/html,application/xhtml+xml,application/pdf,text/plain,application/json,*/*;q=0.8"})
        with self._open(request, timeout=60) as response:
            raw = response.read(MAX_DIRECT_BYTES + 1)
            final = response.geturl() if hasattr(response, "geturl") else url
            header = (response.headers.get("content-type") or "") if response.headers else ""
            kind = header.split(";")[0].strip().lower()
        if len(raw) > MAX_DIRECT_BYTES:
            raise WebError("it is larger than 25 MB")
        title = None
        if kind == "application/pdf" or raw[:5] == b"%PDF-":
            text, kind = pdf_text(raw), "application/pdf"
        elif "html" in kind or kind == "":
            html = raw.decode("utf-8", "replace")
            found = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, re.I)
            title = re.sub(r"\s+", " ", found.group(1)).strip() if found else None
            text = html_text(html)
        elif kind.startswith("text/") or re.search(r"json|xml|yaml|javascript|markdown|csv", kind):
            text = raw.decode("utf-8", "replace")
        else:
            raise WebError(f"it is a {kind} file, which holds no text to read")
        return {"url": final, "title": title, "retrieved_at": _now(), "via": "direct",
                "content_type": kind or "text/html", "text": normalize(text)}


def pdf_text(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "doc.pdf")
        with open(path, "wb") as f:
            f.write(data)
        done = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=60)
    if done.returncode != 0:
        raise WebError("its PDF holds no text that can be read")
    return done.stdout.decode("utf-8", "replace")


def html_text(html: str) -> str:
    cleaned = re.sub(r"<(script|style|noscript|svg|template)\b[\s\S]*?</\1>", "", html, flags=re.I)
    try:
        done = subprocess.run(["pandoc", "-f", "html", "-t", "gfm-raw_html", "--wrap=none"], input=cleaned.encode(),
                              capture_output=True, timeout=60)
        if done.returncode == 0:
            return done.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        pass
    text = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.I)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|pre|section|article)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    for a, b in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&")):
        text = text.replace(a, b)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
