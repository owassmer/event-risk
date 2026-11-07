"""The calls that bring the world into the store: search_web, fetch_source, search_company, and search_markets. The
tools wrap them.

A search returns what the venue or the web returned and keeps none of it: it is recorded on its search row, and on a
proposition when one is given. A market enters the store only when a link or a note names it; a page is held when
fetched and kept once cited or linked.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from harness.derive.engine import Unavailable, canonical
from harness.derive.markets import is_open, listing_of, past_price, priced, read_date
from harness.schema import MARKET_ID_RE, VENUES
from harness.store import PREFETCH_WORKERS, Store, StoreError
from harness.venues import FORMS, NAMES as VENUES_SHOWN, Venues
from harness.venues.http import VenueError
from harness.venues.massive import COMPANY
from harness.venues.polymarket import NY
from harness.web import Web, WebError, html_text

EXCERPT = 1500  # the characters of a fetched page that fetch_source returns
LISTED = 500  # the characters of each document a company search lists
# A company's record, by kind: what it is, as a sentence names it, and the parameters that date it: its lower bound,
# which the company's listing date sets, since a ticker can have been another company's, and its upper bound, which
# the as-of sets.
RECORD = {"news": ("news", "published.gte", "published.lte"), "earnings": ("earnings", "date.gte", "date.lte"),
          "guidance": ("guidance", "date.gte", "date.lte"),
          "ratings": ("analysts' ratings", "date.gte", "date.lte"),
          "risk_factors": ("risk factors", "filing_date.gte", "filing_date.lte"),
          "annual_report": ("annual report (10-K)", "filing_date.gte", "filing_date.lte"),
          "current_reports": ("current reports (8-K)", "filing_date.gte", "filing_date.lte")}
NONE_FILED = {  # a company with nothing in a kind of its SEC filings: what that is, in plain words
    "risk_factors": "A company that files its annual report on Form 20-F, as a foreign issuer does, has none here.",
    "annual_report": "A company that files its annual report on Form 20-F, as a foreign issuer does, has none.",
    "current_reports": "A company that reports on Form 6-K, as a foreign issuer does, has none."}
# Fields a company search leaves out of a row: the vendor's own record keeping, the company's name and ticker, which
# the search names, and Benzinga's own reading of how much an event matters.
DROPPED = ("benzinga_id", "company_name", "ticker", "last_updated", "importance", "images")
NO_PRICE = {"bid": None, "ask": None, "price": None, "depth": None}


class World:
    """The venues and the web over one store."""

    def __init__(self, store: Store, venues: Venues | None = None, web: Web | None = None, *,
                 today: Callable[[], str] | None = None):
        self.store = store
        self.venues = venues if venues is not None else Venues()
        self.web = web if web is not None else Web()
        self.today = today or (lambda: datetime.now(NY).date().isoformat())  # the New York date now
        # A market a link or a note names enters through the venues; its book and history are
        # fetched on its first read.
        store.venues = self.venues
        store.fetchers.update(self.venues.fetchers(store))

    # ---------------------------------------------------------------- search_markets

    def _asof(self) -> str | None:
        return self.store.meta("asof")

    def _books(self, client, rows: list[tuple[str, dict, dict]]) -> dict[str, Any]:
        tokens = [listing["yes_token"] for _, _, listing in rows if listing["yes_token"]]
        if not tokens:
            return {}
        return client.books(tokens)

    @staticmethod
    def _past_prices(venue: str, client, records: list[dict], asof: str) -> list[dict]:
        """Each market's history price at a past as-of, the histories read side by side, PREFETCH_WORKERS at a
        time, as a portfolio's first read reads its bars."""
        def one(record: dict) -> dict:
            try:
                return past_price(FORMS[venue].daily(client.history(record)["payload"]), asof)
            except (LookupError, VenueError) as e:
                return {**NO_PRICE, "past": True, "reason": _unread(venue, e)}
        if not records:
            return []
        with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            return list(pool.map(one, records))

    def search_markets(self, venue: str, endpoint: str, params: dict | None = None, proposition_id: str | None = None,
                       *, call: int | None = None) -> dict:
        """The venue's own search or listing with the analyst's parameters, unchanged; the markets it
        returned grouped under their events in its order, each with its listing, close time, whether it is open, the
        price the venue displays, its best bid and ask, and its depth. Nothing is kept."""
        params = params if params is not None else {}
        if not isinstance(params, dict):
            raise StoreError("params is an object of the venue's own parameters")
        if proposition_id is not None and self.store.type_of(proposition_id) != "Proposition":
            raise StoreError(f"{proposition_id} is not a proposition in the store")
        try:
            client = self.venues.client(venue)
            found = client.search(endpoint, params)
        except ValueError as e:
            raise StoreError(str(e)) from None
        except VenueError:
            raise StoreError(f"{VENUES_SHOWN[venue]} did not answer just now; try again shortly") from None
        rows = [(vid, record, listing_of(venue, record)) for vid, record in found["markets"]]
        asof = self._asof()
        past = asof is not None and asof < read_date(found["retrieved_at"])  # an asof before the day of the search
        books = {} if past else self._books(client, rows)
        histories = self._past_prices(venue, client, [record for _, record, _ in rows], asof) if past else []
        markets = []
        for i, (vid, record, listing) in enumerate(rows):
            mid = f"market:{venue}:{vid}"
            if past:
                q = histories[i]
            elif listing["yes_token"] in books:
                payload = books[listing["yes_token"]]
                q = priced(venue, FORMS[venue].levels(payload), payload, record)
            else:
                q = {**NO_PRICE, "reason": "no book for this market"}
            markets.append({"id": mid, "question": listing["question"], "close_time": listing["close_time"],
                            "resolution_source": listing["resolution_source"], "tags": listing["tags"],
                            "categories": listing["categories"], "series": listing["series"],
                            "open": is_open(venue, record, asof, past), **_price_fields(q),
                            "kept": self.store.type_of(mid) == "Market", "_event": FORMS[venue].event(record)})
        answered = found["status"] == 200
        paging = FORMS[venue].paging(endpoint, found["response"]) if answered else {}
        lists = FORMS[venue].lists(endpoint, found["response"]) if answered else {}
        search = self.store.record_search("search_markets", params, [m["id"] for m in markets],
                                          proposition_id=proposition_id, venue=venue, endpoint=endpoint, call=call)
        return {"search": search, "venue": venue, "endpoint": endpoint, "params": params,
                "returned": len(markets), "events": grouped(markets), **lists, "paging": paging,
                **({} if answered else {"error": refusal(venue, found["response"])})}

    # ---------------------------------------------------------------- a market the store does not hold

    def read_market(self, mid: str) -> dict:
        """A market read from its venue, as the store would derive it if kept: its question, rules,
        resolution source, close time, the venue's tags, categories, or series, whether it is open, its price, its
        best bid and ask, and its depth.
        Nothing is kept: a market enters the store when a link or a note names it."""
        m = MARKET_ID_RE.match(mid) if isinstance(mid, str) else None
        if not m or m.group(1) not in VENUES:
            raise StoreError(f"{mid} is not in the store")
        venue, vid = m.groups()
        try:
            got = self.venues.market(venue, vid)
        except LookupError:
            raise StoreError(f"{VENUES_SHOWN[venue]} lists no market {vid}") from None
        except (ValueError, VenueError):
            raise StoreError(f"{VENUES_SHOWN[venue]} did not answer for {vid} just now; try again shortly") from None
        record = got["payload"]
        listing = listing_of(venue, record)
        client = self.venues.client(venue)
        asof = self._asof()
        past = asof is not None and asof < read_date(got["retrieved_at"])
        try:
            if past:
                q = past_price(FORMS[venue].daily(client.history(record)["payload"]), asof)
            else:
                payload = client.book(record)["payload"]
                q = priced(venue, FORMS[venue].levels(payload), payload, record)
        except (LookupError, VenueError) as e:
            q = {**NO_PRICE, "reason": _unread(venue, e)}
        return {"id": mid, "type": "Market", "kept": False, "stated": {"venue": venue, "id": vid},
                **{k: listing[k] for k in ("question", "rules", "resolution_source", "close_time", "tags",
                                           "categories", "series")},
                "open": is_open(venue, record, asof, past), **_price_fields(q)}

    # ---------------------------------------------------------------- a company's record

    def search_company(self, ticker: str, kind: str, params: dict | None = None, *, call: int | None = None) -> dict:
        """One kind of a company's own record from Massive, the analyst's parameters passed to its endpoint as given,
        beside the company's identity, its ticker; bounded above by the as-of, so a past as-of reads only what was
        known by then, and below by the company's listing date, so a ticker's earlier company is left out while the
        company's filings under each registrant it has filed as are read; a bound the analyst gives stands where it is
        tighter. A list comes back as compact rows; each document in it (an article's text,
        a 10-K section, an 8-K's items, a risk factor's text) is held as a page, as fetch_source holds one, under its
        own URL, and kept as a Source once cited. The answer is kept as data, so a saved store reads it again with no
        keys, and the search is recorded. A refusal comes back in plain words."""
        if kind not in RECORD:
            raise StoreError(f"kind is one of {', '.join(RECORD)}")
        if not isinstance(ticker, str) or not ticker.strip():
            raise StoreError("ticker names the company, as its listing does")
        if params is not None and not isinstance(params, dict):
            raise StoreError("params is an object of the endpoint's own parameters")
        ticker, given = ticker.strip().upper(), dict(params or {})
        what, since, dated_by = RECORD[kind]
        filed = kind in NONE_FILED
        asof = self.store.meta("asof") or self.today()
        name, cik, listed = self._reference(ticker, asof)
        sent = dict(given)
        names = COMPANY[kind][1]
        if names:
            sent[names] = ticker
        bound = _end_of_day(asof) if kind == "news" else asof
        sent[dated_by] = min(str(sent[dated_by]), bound) if sent.get(dated_by) else bound
        if listed:
            sent[since] = max(str(sent[since]), listed) if sent.get(since) else listed
        subject = canonical({"kind": kind, "ticker": ticker, "cik": cik, "params": sent})
        try:
            row = self.store.data("company", subject, covering=(asof, asof), required=False)
        except Unavailable as e:
            raise StoreError(str(e)) from None
        if row is None:
            raise StoreError(f"{ticker}'s {what} is read with the Massive key, which is not set here")
        payload = self.store.data_payload(row["id"]) or {}
        out: dict[str, Any] = {"ticker": ticker, "kind": kind, "params": sent}
        status = payload.get("status")
        if status == "OK":
            rows = self._record_rows(kind, payload, row["retrieved_at"], cik)
            if not rows and filed:
                company = f"{ticker}, {name} (CIK {cik})" if name and cik else ticker
                out["reason"] = f"Massive holds no {what} for {company}, filed by {asof}. {NONE_FILED[kind]}"
        else:
            rows = []
            said = payload.get("error") or payload.get("message") or status
            out["error"] = (f"The Massive key's plan does not include {what}: {said}" if status == "NOT_AUTHORIZED"
                            else f"Massive refused these parameters: {said}" if status == "INVALID"
                            else f"Massive has no {what} for {ticker}: {said}")
        search = self.store.record_search("search_company", sent, rows, endpoint=kind, query=ticker, call=call)
        return {"search": search, **out, "returned": len(rows), "rows": rows, "more": bool(payload.get("next_url"))}

    def _reference(self, ticker: str, asof: str) -> tuple[str | None, str | None, str | None]:
        """The company's name, its SEC registrant's CIK, and its listing date, from its ticker reference as of the
        as-of. The record is read from the listing date, because a ticker can have been another company's; the CIK
        chooses the company's own listing of a filing made by several registrants. A reference that cannot be read,
        or that has no listing date, gives no lower bound."""
        try:
            row = self.store.data("ticker_ref", ticker, covering=(asof, asof), required=False)
        except Unavailable:
            return None, None, None
        if row is None:
            return None, None, None
        payload = self.store.data_payload(row["id"]) or {}
        found = (payload.get("results") or {}) if payload.get("status") == "OK" else {}
        return found.get("name"), found.get("cik"), found.get("list_date")

    def _record_rows(self, kind: str, payload: dict, retrieved_at: str, cik: str | None) -> list[dict]:
        """A company search's rows: each a compact record, and each document held as a page."""
        results = [r for r in payload.get("results") or [] if isinstance(r, dict)]
        if kind in ("earnings", "guidance", "ratings"):
            return [{k: v for k, v in r.items() if k not in DROPPED and v is not None} for r in results]

        def page(url: str, text: str) -> dict:
            sid = self.store.put_page(url, text, retrieved_at=retrieved_at)
            return {"id": sid, "length": len(text), "excerpt": text[:LISTED]}

        rows: list[dict] = []
        if kind == "news":
            for r in results:
                row = {"published": r.get("published"), "author": r.get("author"), "title": _plain(r.get("title")),
                       "teaser": _plain(r.get("teaser")), "channels": r.get("channels") or [],
                       "tags": r.get("tags") or [], "tickers": r.get("tickers") or [], "url": r.get("url")}
                body = html_text(r["body"]).strip() if r.get("body") else ""
                if body and r.get("url"):  # its headline and teaser say what it holds: its page, without an excerpt
                    row["page"] = {k: v for k, v in page(r["url"], f"# {row['title'] or ''}\n\n{body}").items()
                                   if k != "excerpt"}
                rows.append(row)
            return rows
        text_of = {"risk_factors": lambda r: (r.get("supporting_text") or "").strip(),
                   "annual_report": lambda r: r.get("text") or "",
                   "current_reports": lambda r: r.get("items_text") or ""}[kind]
        category = lambda r: [r.get("primary_category"), r.get("secondary_category"), r.get("tertiary_category")]
        key_of = {"risk_factors": lambda r: (r.get("filing_date"), text_of(r), *category(r)),
                  "annual_report": lambda r: (r.get("filing_date"), r.get("section")),
                  "current_reports": lambda r: (r.get("accession_number"),)}[kind]
        chosen: dict[tuple, dict] = {}  # one record for each filing, or part of one, in the order first listed
        for r in results:
            if not text_of(r).strip() or (kind != "risk_factors" and not r.get("filing_url")):
                continue
            key = key_of(r)
            if key not in chosen or (cik and r.get("cik") == cik and chosen[key].get("cik") != cik):
                chosen[key] = r  # a filing listed under each of its registrants: the company's own listing of it
        filings = payload.get("filing_urls") or {}
        for r in chosen.values():
            if kind == "risk_factors":
                text = text_of(r)
                filing = filings.get(r.get("filing_date")) or ("https://www.sec.gov/cgi-bin/browse-edgar?action="
                                                              f"getcompany&CIK={r.get('cik') or cik}&type=10-K")
                url = f"{filing}#risk-factor-{_short_hash(text)}"
                rows.append({"filing_date": r.get("filing_date"), "category": category(r), "url": url,
                             "page": page(url, text)})
            elif kind == "annual_report":  # each section its own document, under the filing's URL with its name
                url = f"{r['filing_url']}#{r.get('section')}"
                rows.append({"filing_date": r.get("filing_date"), "period_end": r.get("period_end"),
                             "section": r.get("section"), "url": url, "page": page(url, text_of(r))})
            else:
                rows.append({"filing_date": r.get("filing_date"), "form_type": r.get("form_type"),
                             "accession_number": r.get("accession_number"), "url": r["filing_url"],
                             "page": page(r["filing_url"], text_of(r))})
        return rows

    # ---------------------------------------------------------------- the web

    def search_web(self, text: str, params: dict | None = None, proposition_id: str | None = None, *,
                   call: int | None = None) -> dict:
        """One web search: URLs, titles, dates, and the passages quoted from each, as data."""
        if not isinstance(text, str) or not text.strip():
            raise StoreError("the query is empty: a web search searches for its text")
        if proposition_id is not None and self.store.type_of(proposition_id) != "Proposition":
            raise StoreError(f"{proposition_id} is not a proposition in the store")
        try:
            found = self.web.search(text, params or {})
        except WebError as e:
            raise StoreError(str(e)) from None
        search = self.store.record_search("search_web", params or {}, found["results"], proposition_id=proposition_id,
                                          query=text, call=call)
        return {"search": search, "query": text, "params": params or {}, **found}

    def fetch_source(self, url: str) -> dict:
        """The page's full text, held in the store: kept as a Source once it is cited for a stated
        fact or linked as evidence. A page the store holds is not fetched again."""
        if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
            raise StoreError(f"{url!r} is not an http or https URL: a page is fetched by one")
        held = self.store.held_page(url)
        if held is None:
            try:
                page = self.web.fetch(url)
            except WebError as e:
                raise StoreError(str(e)) from None
            held = self.store.put_page(url, page["text"], retrieved_at=page["retrieved_at"])
            title = page.get("title")
        else:
            title = None
        row = self.store.source_row(held)
        return {"id": held, "url": url, "title": title, "dated": row["dated"], "retrieved_at": row["retrieved_at"],
                "kept": row["kept"], "length": len(row["text"]), "excerpt": row["text"][:EXCERPT]}


def refusal(venue: str, body) -> str:
    """A venue's refusal of the analyst's parameters, in its own words: what the venue said, never its status code."""
    said = body
    if isinstance(body, dict):
        said = next((body[k] for k in ("error", "message", "detail") if body.get(k)), None) or body
        if isinstance(said, dict):
            said = said.get("message") or said.get("details") or said.get("code") or json.dumps(said)
    return f"{VENUES_SHOWN[venue]} refused these parameters: {said}"


def _unread(venue: str, error: Exception) -> str:
    """Why a market's book or history could not be read: the venue's answer about the market, or its silence."""
    if isinstance(error, VenueError):
        return f"{VENUES_SHOWN[venue]} did not answer just now; try again shortly"
    return str(error)


def grouped(markets: list[dict]) -> list[dict]:
    """Markets under their events, in the order the venue returned them: each event's title and tags once,
    and its markets beneath, without the event's tags again or empty lists; a market with no event, alone."""
    groups: list[dict] = []
    index: dict = {}
    for m in markets:
        event = m.get("_event")
        key = event["event"] if event else ("market", m["id"])
        if key not in index:
            index[key] = len(groups)
            groups.append({**(event or {"event": None, "title": None, "tags": []}), "markets": []})
        group = groups[index[key]]
        item = {k: v for k, v in m.items() if k != "_event" and not (k in ("categories", "series") and v == [])
                and not (k == "tags" and (v == group["tags"] or v == []))}
        group["markets"].append(item)
    return groups


def _end_of_day(day: str) -> str:
    """The end of a New York day, as the UTC time a news endpoint takes."""
    end = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), NY)
    return end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain(text: str | None) -> str | None:
    """A short HTML text, its markup taken out and its entities read: format only."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()) if text else text


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _price_fields(q: dict) -> dict:
    """A market's price as a search or a read shows it: the price the venue displays and the rule that gave it, its
    best bid and ask, and its depth, with the reason where there is none."""
    out = {"price": q.get("price"), "price_rule": q.get("rule"), "bid": q.get("bid"), "ask": q.get("ask"),
           "depth": q.get("depth")}
    if q.get("past"):
        out["price_date"] = q.get("price_date")
    if q.get("reason"):
        out["reason"] = q["reason"]
    return out
