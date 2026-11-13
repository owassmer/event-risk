"""The calls that bring the world into the store: search_web, fetch_source, search_company, and search_markets. The
tools wrap them.

A search returns what the venue or the web returned and keeps none of it: it is recorded on its search row, and on a
proposition when one is given. A market enters the store only when a link or a note names it; a page is held when
fetched and kept once cited or linked.
"""

from __future__ import annotations

import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from harness.derive.engine import Unavailable, Waiting, canonical
from harness.derive.markets import (curve_on, history_to, is_open, listing_of, paid_by, past_price, priced, rate_reading,
                                     read_date, risk_neutral, settlement_at, terms_at)
from harness.schema import FAMILY_ID_RE, MARKET_ID_RE, VENUES
from harness.store import PREFETCH_WORKERS, Store, StoreError, body, find
from harness.venues import FORMS, NAMES as VENUES_SHOWN, Venues
from harness.venues.http import VenueError, VenueRefused
from harness.venues.massive import COMPANY, answered
from harness.venues.markets import FAMILIES, LARGE, NY
from harness.web import Web, WebError, html_text

EXCERPT = 1500  # the characters of a fetched page's body that fetch_source returns
LISTED = 500  # the characters of each document a company search lists
# A company's record, by kind: what it is, as a sentence names it, and the parameters that date it: its lower bound,
# which the company's listing date sets, since a ticker can have been another company's, and its upper bound, which
# the as-of sets.
EARNINGS_LIMIT = 100  # Benzinga's earnings endpoint's own default limit (https://massive.com/docs/rest/partners/benzinga/earnings)
RECORD = {"news": ("news", "published.gte", "published.lte"), "earnings": ("earnings", "date.gte", "date.lte"),
          "guidance": ("guidance", "date.gte", "date.lte"),
          "ratings": ("analysts' ratings", "date.gte", "date.lte"),
          "risk_factors": ("risk factors", "filing_date.gte", "filing_date.lte"),
          "annual_report": ("annual report (10-K)", "filing_date.gte", "filing_date.lte"),
          "current_reports": ("current reports (8-K)", "filing_date.gte", "filing_date.lte")}
NONE_FILED = {  # a company with nothing in a kind of its SEC filings: what else was read, in plain words
    "risk_factors": "A company that files its annual report on Form 20-F or 40-F, as a foreign issuer does, has its "
                    "risk factors in that report: search its annual_report.",
    "annual_report": "The SEC lists no annual report of it on Form 20-F or 40-F within the dates searched either.",
    "current_reports": "The SEC lists no report of it on Form 6-K within the dates searched either."}
# Who published each kind of a US listing's record, the fact the analyst weighs a document by; the service it is read
# through is the code's: Benzinga's news, results, guidance and ratings, and the filings the SEC publishes, read
# through Massive, a foreign issuer's from EDGAR.
SEC = "the SEC"
SOURCE = {"news": "Benzinga", "earnings": "Benzinga", "guidance": "Benzinga", "ratings": "Benzinga",
          "risk_factors": SEC, "annual_report": SEC, "current_reports": SEC}
# A foreign issuer's reports at the SEC, read from EDGAR where Massive carries none (Massive's are a 10-K's sections and
# an 8-K's items): its annual reports, on Form 20-F or, a Canadian issuer's, 40-F; and its reports between them, 6-K.
FOREIGN_FORMS = {"annual_report": ("20-F", "20-F/A", "40-F", "40-F/A"), "current_reports": ("6-K", "6-K/A")}
FOREIGN_LIMIT = {"annual_report": 1, "current_reports": 10}  # the reports read where a search names no limit
# What a company held only abroad has, by kind: its results and analysts' ratings on its own market (Yahoo Finance).
ABROAD = ("earnings", "ratings")
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

    def _past(self, asof: str | None, read_at: str | None = None) -> bool:
        """Whether the store's as-of is before the day the venue is read, so its markets are those open then, priced
        from history, as a kept market's price reads it (harness.derive.markets): the day a record was read where it
        was, and else today, the world's own (`today`)."""
        return asof is not None and asof < (read_date(read_at) if read_at else self.today())

    def _past_prices(self, venue: str, records: dict[str, dict], asof: str) -> dict[str, dict]:
        """Each market's history price at a past as-of, the histories read side by side, PREFETCH_WORKERS at a
        time, as a portfolio's first read reads its bars. A market the venue keeps no history for has no price."""
        client = self.venues.client(venue)

        def one(record: dict) -> dict:
            try:
                return past_price(venue, client.history(record)["payload"], asof)
            except LookupError as e:
                return {**NO_PRICE, "past": True, "reason": str(e)}
        if not records:
            return {}
        with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
            return dict(zip(records, pool.map(one, records.values())))

    def _quotes(self, rows: dict[str, dict], asof: str | None, past: bool, *, last: bool = False) -> dict[str, dict]:
        """Each market's price, bid, ask, and depth, and with `last` its last trade: today from its book, each
        venue's books read together and the venues side by side, with the moment they were read (`quoted_at`); for a
        past as-of from its history at the as-of. A venue that does not answer raises."""
        by_venue: dict[str, dict[str, dict]] = {}
        for mid, record in rows.items():
            by_venue.setdefault(MARKET_ID_RE.match(mid).group(1), {})[mid] = record

        def one(venue: str, records: dict[str, dict]) -> dict[str, dict]:
            if past:
                return self._past_prices(venue, records, asof)
            at = datetime.now(timezone.utc).isoformat()
            books, got = self.venues.client(venue).books(records, last=last), {}
            for mid, record in records.items():
                payload = books.get(mid)
                got[mid] = {**priced(venue, FORMS[venue].levels(payload), payload, record), "quoted_at": at} \
                    if payload is not None else {**NO_PRICE, "reason": "no book for this market"}
            return got
        out: dict[str, dict] = {}
        if by_venue:
            with ThreadPoolExecutor(max_workers=len(by_venue)) as pool:
                for got in pool.map(lambda item: one(*item), by_venue.items()):
                    out.update(got)
        return out

    def search_markets(self, text: str, proposition_id: str, *, call: int | None = None) -> dict:
        """Every venue's own search for the text, side by side, each venue's families in its own order of relevance,
        interleaved rank by rank; each family's markets open on the as-of as compact rows (`compact`), a large family
        as its count and its span, read whole by its id. A venue that does not answer past its retries makes the
        search fail, to be asked again shortly. The search is made for a proposition, the one the markets would bear
        on, and is recorded on it; nothing is kept."""
        if not isinstance(text, str) or not text.strip():
            raise StoreError("text is what you are looking for, in your own words")
        text = text.strip()
        with self.store.lock:
            if self.store.type_of(proposition_id) != "Proposition":
                raise StoreError(f"{proposition_id} is not a proposition in the store")
            asof = self._asof()
        past = self._past(asof)
        try:  # the venues are read outside the store's lock
            found = {}
            for venue, ranked in self.venues.search(text, past=past).items():
                forms, kept_open = FORMS[venue], []
                for family in ranked:  # the venue's first FAMILIES with a market open on the as-of
                    markets = [(f"market:{m.id}", forms.record_of(m, family)) for m in family.markets]
                    opened = [(mid, record) for mid, record in markets if forms.is_open(record, asof, past)]
                    if opened and len(kept_open) < FAMILIES:
                        kept_open.append((family, opened))
                found[venue] = kept_open
            families = [(venue, family, opened) for venue, (family, opened) in interleave(found)]
            rows = {mid: record for _, _, opened in families if len(opened) <= LARGE for mid, record in opened}
            with ThreadPoolExecutor(max_workers=2) as pool:  # the books and the families above, side by side
                held = pool.submit(self._within, [(venue, family.info) for venue, family, _ in families])
                quotes = self._quotes(rows, asof, past)
                within = held.result()
        except VenueError as e:
            raise StoreError(silent(e)) from None
        with self.store.lock:
            kept = sorted(mid for mid in rows if self.store.type_of(mid) == "Market")
        shown = [compact(venue, family, opened, quotes, within) for venue, family, opened in families]
        returned = [row[0] for f in shown for row in f.get("markets", [])] + \
            [f["family"] for f in shown if "markets" not in f]
        with self.store.lock:
            search = self.store.record_search("search_markets", {"text": text}, returned,
                                              proposition_id=proposition_id, query=text, call=call)
        return {"search": search, "text": text, "returned": len(rows), "columns": list(COLUMNS), "families": shown,
                **({"kept": kept} if kept else {}), **({"prices_as_of": asof} if past and rows else {})}

    # ---------------------------------------------------------------- a family and a market the store does not hold

    def read_family(self, fid: str) -> dict:
        """A family read whole from its venue: every market in it open on the as-of as a compact row, priced, and the
        rest by id. Nothing is kept."""
        m = FAMILY_ID_RE.match(fid) if isinstance(fid, str) else None
        if not m or m.group(1) not in VENUES:
            raise StoreError(f"{fid} is not a family any venue lists")
        venue, native = m.groups()
        with self.store.lock:
            asof = self._asof()
        past = self._past(asof)
        forms = FORMS[venue]
        if native.startswith("series:"):
            return self._read_series(fid, venue, native.split(":", 1)[1], asof, past)
        try:  # the venue is read outside the store's lock
            got = self.venues.client(venue).family(native)
            markets = forms.records_of(got)
            opened = {mid: record for mid, record in markets if forms.is_open(record, asof, past)}
            quotes = self._quotes(opened, asof, past)
            within = self._within([(venue, got.get("event") or {}, got.get("series") or {})])
        except LookupError:
            raise StoreError(f"{VENUES_SHOWN[venue]} lists no family {native}") from None
        except VenueError as e:
            raise StoreError(silent(e)) from None
        above = forms.within({**(got.get("event") or {}), **({"series": got["series"]} if got.get("series") else {})})
        closed = [mid for mid, _ in markets if mid not in opened]
        with self.store.lock:
            kept = sorted(mid for mid, _ in markets if self.store.type_of(mid) == "Market")
        whole = forms.family_of(got)
        return {"id": fid, "type": "Family", "kept": False, "venue": venue, "title": whole.get("title"),
                "closes": whole.get("closes"), **({"within": within[above["id"]]} if above else {}),
                "columns": list(COLUMNS),
                "markets": rows_of(venue, list(opened.items()), quotes, many=len(markets) > 1),
                **({"closed": closed} if closed else {}), **({"kept": kept} if kept else {}),
                **({"prices_as_of": asof} if past and opened else {})}

    def _risk_neutral(self, venue: str, record: dict, close: str | None, price: float | None, day: str,
                      past: bool) -> dict | None:
        """The range a market's price allows (harness.derive.markets.risk_neutral), for a market the store does not
        keep: the Treasury's curve and the venue's rate read through the store, as a kept market's are."""
        if price is None:
            return None
        year, curves = int(day[:4]), []
        for y in (year - 1, year):
            try:
                row = self.store.fetch_data("yield_curve", str(y), covering=(day, day), required=False)
            except Waiting:
                row = None
            with self.store.lock:
                curves.append(self.store.data_payload(row["id"]) if row else None)
        def kept() -> list[dict]:
            with self.store.lock:
                return self.store.readings("venue_rate", venue)
        reading, waits = rate_reading(lambda: self.store.fetch_data("venue_rate", venue, covering=(day, day)),
                                      kept, venue, day, past)
        with self.store.lock:
            stated = self.store.data_payload(reading["id"]) if reading else None
        paid = paid_by(venue, record, stated, reading["retrieved_at"] if reading else None, waits)
        return risk_neutral(price, close, day, curve_on(curves, day), paid)

    def _within(self, events: list[tuple]) -> dict[str, dict]:
        """The family each event belongs to, by its id: its title, and how many open families it holds, each venue's
        families above read side by side. A family above that its venue no longer lists is named without its count."""
        above: dict[str, tuple[str, str, dict]] = {}
        for venue, info, *series in events:
            found = FORMS[venue].within({**info, **({"series": series[0]} if series and series[0] else {})})
            if found:
                above.setdefault(found["id"], (venue, found["id"].split(":", 3)[3], found))

        def one(item: tuple[str, str, dict]) -> dict:
            venue, native, found = item
            try:
                held = self.venues.client(venue).holding(native)
            except LookupError:
                held = {}
            title = found.get("title") or held.get("title")
            return {"family": found["id"], **({"title": title} if title else {}),
                    **({"open_families": held["open_families"]} if "open_families" in held else {})}
        if not above:
            return {}
        with ThreadPoolExecutor(max_workers=min(PREFETCH_WORKERS, len(above))) as pool:
            return dict(zip(above, pool.map(one, above.values())))

    def _read_series(self, fid: str, venue: str, native: str, asof: str | None, past: bool) -> dict:
        """A family that holds families, read whole at its level (MATH.md 3.3): each family in it open on the as-of, with
        its markets open then as compact rows, or past LARGE of them their count and span; past LARGE families, each by
        its id, title, close date and how many open markets it holds, each read whole by its id. Nothing is kept."""
        forms = FORMS[venue]
        try:  # the venue is read outside the store's lock
            got = self.venues.client(venue).series(native, past=past)
            held = []
            for family in got["families"]:
                opened = [(mid, r) for mid, r in forms.records_of(family) if forms.is_open(r, asof, past)]
                if opened:
                    held.append((family, opened))
            rows = {mid: r for _, opened in held if len(opened) <= LARGE for mid, r in opened} \
                if len(held) <= LARGE else {}
            quotes = self._quotes(rows, asof, past)
        except LookupError:
            raise StoreError(f"{VENUES_SHOWN[venue]} lists no family {fid.split(':', 2)[2]}") from None
        except VenueError as e:
            raise StoreError(silent(e)) from None
        out = {"id": fid, "type": "Family", "kept": False, "venue": venue, "title": got.get("title"),
               "open_families": len(held)}
        shown = [(forms.family(opened[0][1])["id"], forms.family_of(family), opened) for family, opened in held]
        if len(held) > LARGE:  # each family by itself, its markets one get away
            return {**out, "families": [{"family": family_id, "venue": venue, "title": whole.get("title"),
                                          "closes": whole.get("closes"), "open_markets": len(opened)}
                                         for family_id, whole, opened in shown]}
        return {**out, "columns": list(COLUMNS),
                "families": [entry(venue, family_id, whole.get("title"), whole.get("closes"), opened, quotes,
                                   many=len(opened) > 1) for family_id, whole, opened in shown],
                **({"prices_as_of": asof} if past and rows else {})}

    def read_market(self, mid: str) -> dict:
        """A market read from its venue, as the store would derive it if kept: its terms (its question, rules,
        resolution source, close date, strike, family, and fees), whether it is open, its price, its best bid and ask,
        its depth, the range of probabilities its price allows, its last trade, and its history to the as-of, its price
        on each day (MATH.md 3.3), read beside its book. Nothing is kept: a market enters the store when a link or a note
        names it."""
        m = MARKET_ID_RE.match(mid) if isinstance(mid, str) else None
        if not m or m.group(1) not in VENUES:
            raise StoreError(f"{mid} is not in the store")
        venue, vid = m.groups()
        with self.store.lock:
            asof = self._asof()
        past = self._past(asof)
        try:  # the venue is read outside the store's lock
            got = self.venues.market(venue, vid)
            record, past = got["payload"], self._past(asof, got["retrieved_at"])
            with ThreadPoolExecutor(max_workers=2) as pool:  # its history beside its book
                read = pool.submit(self._history, venue, record)
                q = None if past else self._quotes({mid: record}, asof, False, last=True)[mid]
                history, reason = read.result()
            if past:  # as it stood at the as-of's end, from its history (harness.derive.markets.past_price)
                q = past_price(venue, history, asof) if reason is None else {**NO_PRICE, "past": True,
                                                                            "reason": reason}
        except LookupError:
            raise StoreError(f"{VENUES_SHOWN[venue]} lists no market {vid}") from None
        except VenueError as e:
            raise StoreError(silent(e)) from None
        listing = listing_of(venue, record, terms_at(asof, got["retrieved_at"]))
        day = asof or self.today()
        return {"id": mid, "type": "Market", "kept": False, "stated": {"venue": venue, "id": vid}, **listing,
                "open": is_open(venue, record, asof, past), **_price_fields(q),
                **({"confirmed_at": q["quoted_at"]} if not past and q.get("quoted_at") else {}),  # read just now
                "risk_neutral": self._risk_neutral(venue, record, listing["close_time"], q.get("price"), day, past),
                "history": history_to(venue, history, day), "settlement": settlement_at(venue, record, asof, past)}

    def _history(self, venue: str, record: dict) -> tuple[dict | None, str | None]:
        """A market's history as its venue gives it, or None with the venue's reason where it keeps none for it (a
        market with no outcome named Yes). A venue that does not answer raises."""
        try:
            return self.venues.client(venue).history(record)["payload"], None
        except LookupError as e:
            return None, str(e)

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
        ticker, given = ticker.strip(), _asked(kind, params or {})
        ticker = (ticker[5:] if ticker.lower().startswith("name:") else ticker).upper()  # a name's id names its company
        what, since, dated_by = RECORD[kind]
        filed = kind in NONE_FILED
        with self.store.lock:
            asof = self.store.meta("asof") or self.today()
        from harness.derive import series
        if series.is_abroad(ticker):  # a company named by its home listing: its record on its own market, by the
            # listing's own symbol, or the one `{ISIN}:{country}`'s search finds
            return self._record_abroad(ticker, kind, what, asof, call,
                                       symbol=ticker if series.symbol_abroad(ticker) else None)
        name, cik, listed = self._reference(ticker, asof)
        if not cik:  # a line over the counter whose reference names none: the SEC's list of tickers
            from harness.derive.identity import store_lookup
            found = store_lookup(self.store, asof).cik_of(ticker)
            cik = str(found).zfill(10) if found else None
        sent = dict(given)
        names = COMPANY[kind][1]
        if names:
            sent[names] = ticker
        bound = _end_of_day(asof) if kind == "news" else asof
        # A report scheduled after today is known today, so at today's as-of earnings run past it; at a past as-of a
        # scheduled report's row has since been overwritten with its results, so they end at the as-of.
        if not (kind == "earnings" and not self._past(asof)):
            sent[dated_by] = min(str(sent[dated_by]), bound) if sent.get(dated_by) else bound
        if listed:
            sent[since] = max(str(sent[since]), listed) if sent.get(since) else listed
        if kind == "earnings" and "sort" not in given:  # nearest the as-of first, as the analyst reads them
            row, payload = self._nearest(ticker, cik, asof, sent)
        else:
            row = self._company_read(ticker, cik, asof, sent, kind)
            payload = None
        if row is None:
            raise StoreError(f"{ticker}'s {what} cannot be read here: the key its source needs is not set")
        if payload is None:
            with self.store.lock:
                payload = self.store.data_payload(row["id"]) or {}
        if kind in FOREIGN_FORMS and cik and answered(payload) and not payload.get("results"):
            foreign = self._foreign_reports(ticker, kind, cik, asof, sent, since, dated_by, given, call)
            if foreign is not None:
                return foreign
        if kind in ABROAD and answered(payload) and not payload.get("results"):  # none at Massive: its own listing's
            return self._record_abroad(ticker, kind, what, asof, call, symbol=ticker.replace(".", "-"))
        with self.store.lock:
            return self._company_answer(ticker, kind, what, filed, name, cik, asof, sent, row, call, payload)

    def _company_read(self, ticker: str, cik: str | None, asof: str, params: dict, kind: str) -> dict | None:
        """One request for a kind of the company's record, kept as data; None where its source's key is not set."""
        subject = canonical({"kind": kind, "ticker": ticker, "cik": cik, "params": params})
        try:  # Massive is read outside the store's lock
            return self.store.fetch_data("company", subject, covering=(asof, asof), required=False)
        except Unavailable as e:
            raise StoreError(str(e)) from None

    def _nearest(self, ticker: str, cik: str | None, asof: str, sent: dict) -> tuple[dict | None, dict | None]:
        """A company's earnings nearest the as-of first: what is scheduled from the as-of on, the nearest first, then
        what is reported before it, the newest first; within the analyst's bounds, up to its limit. Benzinga's endpoint
        sorts by when a row was last updated where no sort is named (https://massive.com/docs/rest/partners/benzinga/
        earnings, `sort`), so a limit there keeps the rows updated last, wherever they fall; each side is asked sorted
        by its date, outward from the as-of, and the two are read together. The first row read, and the answer."""
        limit = int(sent.get("limit") or EARNINGS_LIMIT)
        ahead = {**sent, "sort": "date.asc", "date.gte": max(str(sent["date.gte"]), asof) if sent.get("date.gte") else asof}
        before = {**sent, "sort": "date.desc", "date.lt": min(str(sent["date.lt"]), asof) if sent.get("date.lt") else asof}
        read = [self._company_read(ticker, cik, asof, side, "earnings") for side in (ahead, before)]
        if any(row is None for row in read):
            return None, None
        with self.store.lock:
            answers = [self.store.data_payload(row["id"]) or {} for row in read]
        for answer in answers:
            if not answered(answer):  # a refusal says why, as one request's would
                return read[0], answer
        rows = [r for answer in answers for r in answer.get("results") or []]
        more = len(rows) > limit or any(answer.get("next_url") for answer in answers)
        return read[0], {**answers[0], "results": rows[:limit], "next_url": "more" if more else None}

    def _foreign_reports(self, ticker: str, kind: str, cik: str, asof: str, sent: dict, since: str, dated_by: str,
                         given: dict, call: int | None) -> dict | None:
        """A foreign issuer's reports, from EDGAR by its CIK (harness.venues.sec): its 20-F or 40-F annual reports, or
        its 6-K reports between them, filed within the search's dates, the latest first, each held as a page as a 10-K's
        sections are; None for a company that files none of them, whose answer is Massive's."""
        try:  # EDGAR is read outside the store's lock
            listed = self.store.fetch_data("sec_filings", str(cik).zfill(10), covering=(asof, asof), required=False)
        except Unavailable as e:
            raise StoreError(str(e)) from None
        if listed is None:
            return None
        with self.store.lock:
            filings = (self.store.data_payload(listed["id"]) or {}).get("filings") or []
        low, high = str(sent.get(since) or ""), str(sent.get(dated_by) or asof)
        chosen = [f for f in filings if f["form"] in FOREIGN_FORMS[kind] and low[:10] <= f["filed"] <= high[:10]]
        if not chosen and not any(f["form"] in FOREIGN_FORMS[kind] for f in filings):
            return None
        limit = int(given.get("limit") or FOREIGN_LIMIT[kind])
        rows = []
        for f in chosen[:limit]:
            subject = f"{str(cik).zfill(10)}/{f['accession']}/{f['document']}"
            try:
                found = self.store.fetch_data("sec_document", subject, covering=(asof, asof))
            except Unavailable as e:
                raise StoreError(str(e)) from None
            with self.store.lock:
                doc = self.store.data_payload(found["id"]) or {}
                sid = self.store.put_page(doc["url"], doc["text"], retrieved_at=found["retrieved_at"]) \
                    if (doc.get("text") or "").strip() else None
                page = self.store.source_row(sid) if sid else None
            rows.append({"filing_date": f["filed"], "form_type": f["form"], "period_end": f["period"],
                         "accession_number": f["accession"], "url": doc.get("url"),
                         **({"page": {"id": sid, "length": len(page["text"]), "excerpt": page["text"][:LISTED]}}
                            if page else {})})
        with self.store.lock:
            search = self.store.record_search("search_company", sent, rows, endpoint=kind, query=ticker, call=call)
        return {"search": search, "ticker": ticker, "kind": kind, "params": sent, "source": SEC,
                "returned": len(rows), "rows": rows, "more": len(chosen) > limit}

    def _record_abroad(self, listing: str, kind: str, what: str, asof: str, call: int | None,
                       symbol: str | None = None) -> dict:
        """A company's results and analysts' ratings from its listing's record on its own market (harness.venues.yahoo
        `record`), in the fields a US listing's carry: for a company named by its home listing, and for a US line whose
        record holds none of them. Its results are those reported by the as-of, and each quarter's figures by its
        period's end alone, which carry no report date, only at today's as-of; its ratings are as they stand on the day
        they are read, so a past as-of has none."""
        if kind not in ABROAD:
            raise StoreError(f"{listing} is listed only abroad, and its record here is its results and its analysts' "
                             f"ratings (kind earnings or ratings), not its {what}")
        today = self.today()
        if kind == "ratings" and asof < today:
            raise StoreError(f"{listing}'s analysts' ratings are read as they stand on the day they are read, so a store "
                             f"as of {asof} has none")
        month = asof[:7]
        try:  # Yahoo is read outside the store's lock
            if symbol is None:
                found = self.store.fetch_data("listing_symbol", f"{listing}@{month}", covering=(asof, asof),
                                              required=False)
                with self.store.lock:
                    symbol = ((self.store.data_payload(found["id"]) or {}).get("symbol")) if found else None
            if not symbol:
                raise StoreError(f"no listing of {listing} on its own market is found")
            row = self.store.fetch_data("company_record", symbol, covering=(asof, asof))
        except Unavailable as e:
            raise StoreError(str(e)) from None
        with self.store.lock:
            record = self.store.data_payload(row["id"]) or {}
            if record.get("status") != "OK":
                rows = []
            elif kind == "ratings":
                rows = [record["ratings"]] if record.get("ratings") else []
            else:
                rows = [r for r in record.get("results") or []
                        if (r["date"] <= asof if r.get("date") else asof >= today)]
            search = self.store.record_search("search_company", {"symbol": symbol}, rows, endpoint=kind, query=listing,
                                              call=call)
        return {"search": search, "ticker": listing, "kind": kind, "symbol": symbol, "source": "Yahoo Finance",
                "returned": len(rows), "rows": rows, "more": False,
                **({} if record.get("status") == "OK" else {"reason": f"no record of {symbol} is found"})}

    def _company_answer(self, ticker: str, kind: str, what: str, filed: bool, name: str | None, cik: str | None,
                        asof: str, sent: dict, row: dict, call: int | None, payload: dict | None = None) -> dict:
        """A company search's answer from the record read (`payload`, or the row's own): its rows, each document held as
        a page, and the search recorded."""
        payload = payload if payload is not None else (self.store.data_payload(row["id"]) or {})
        out: dict[str, Any] = {"ticker": ticker, "kind": kind, "params": sent, "source": SOURCE[kind]}
        status = payload.get("status")
        if answered(payload):
            rows = self._record_rows(kind, payload, row["retrieved_at"], cik)
            if not rows and filed:
                company = f"{ticker}, {name} (CIK {cik})" if name and cik else ticker
                out["reason"] = f"No {what} of {company} filed by {asof} is found. {NONE_FILED[kind]}"
        else:
            rows = []
            said = payload.get("error") or payload.get("message") or status
            out["error"] = (f"{ticker}'s {what} cannot be read here" if status == "NOT_AUTHORIZED"
                            else f"These parameters were refused: {said}" if status == "INVALID"
                            else f"No {what} is found for {ticker}: {said}")
        search = self.store.record_search("search_company", sent, rows, endpoint=kind, query=ticker, call=call)
        return {"search": search, **out, "returned": len(rows), "rows": rows, "more": bool(payload.get("next_url"))}

    def listed(self, ticker: str, asof: str | None = None) -> bool:
        """Whether a ticker names an instrument listed at the as-of, the store's unless one is given (a portfolio's
        holdings, before its as-of is the store's): a US listing Massive's reference gives (its answer naming one), or
        a currency or crypto pair its source quotes (its hourly prices over the window, which a factor on it reads). A
        source that answers it lists no such thing gives False. A read that fails past its retries raises Waiting
        (harness.store), and an answer that says neither (a refusal for the key's plan) raises VenueError, so a call
        that needs the answer fails with nothing written and can be made again."""
        from harness.derive import series

        asof = asof or self._asof()
        if asof is None:
            raise Unavailable("the store has no portfolio, so it has no as-of to read a listing at")
        if series.is_pair(ticker):
            row = self.store.fetch_data("hourly", ticker, covering=(series.lookback(asof), asof))
            with self.store.lock:
                payload = self.store.data_payload(row["id"]) or {}
            return payload.get("status") == "OK" and bool(payload.get("results"))
        row = self.store.fetch_data("ticker_ref", ticker, covering=(asof, asof))
        with self.store.lock:
            payload = self.store.data_payload(row["id"]) or {}
        if answered(payload):
            return bool(payload.get("results"))
        if payload.get("status") in ("NOT_FOUND", "INVALID"):
            return False
        said = payload.get("error") or payload.get("status")
        raise VenueError(f"Whether {ticker} is listed could not be read: {said}")

    def _reference(self, ticker: str, asof: str) -> tuple[str | None, str | None, str | None]:
        """The company's name, its SEC registrant's CIK, and its listing date, from its ticker reference as of the
        as-of. The record is read from the listing date, because a ticker can have been another company's; the CIK
        chooses the company's own listing of a filing made by several registrants. A reference no client here reads,
        or one that names no listing date, gives no lower bound; a read that fails past its retries raises Waiting."""
        row = self.store.fetch_data("ticker_ref", ticker, covering=(asof, asof), required=False)
        if row is None:
            return None, None, None
        with self.store.lock:
            payload = self.store.data_payload(row["id"]) or {}
        found = (payload.get("results") or {}) if answered(payload) else {}
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
        with self.store.lock:
            if proposition_id is not None and self.store.type_of(proposition_id) != "Proposition":
                raise StoreError(f"{proposition_id} is not a proposition in the store")
        try:  # the web is searched outside the store's lock
            found = self.web.search(text, params or {})
        except WebError as e:
            raise StoreError(str(e)) from None
        with self.store.lock:
            search = self.store.record_search("search_web", params or {}, found["results"],
                                              proposition_id=proposition_id, query=text, call=call)
        return {"search": search, "query": text, "params": params or {}, **found}

    def fetch_source(self, url: str, text: str | None = None) -> dict:
        """The page's full text, held in the store: kept as a Source once it is cited for a stated
        fact or linked as evidence. A page the store holds is not fetched again. Given words to find in it, the passages
        that contain them come back with it, as get(id, text) finds them (store.find), each quoted from the page as a
        basis quotes it: the words a search's quoted passage gave are found in the page in the one call that reads it."""
        if not isinstance(url, str) or not re.match(r"^https?://\S+$", url):
            raise StoreError(f"{url!r} is not an http or https URL: a page is fetched by one")
        with self.store.lock:
            held = self.store.held_page(url)
        title = None
        if held is None:
            try:  # the page is fetched outside the store's lock
                page = self.web.fetch(url)
            except WebError as e:
                raise StoreError(str(e)) from None
            with self.store.lock:
                held = self.store.put_page(url, page["text"], retrieved_at=page["retrieved_at"])
            title = page.get("title")
        with self.store.lock:
            row = self.store.source_row(held)
        out = {"id": held, "url": url, "title": title, "dated": row["dated"], "retrieved_at": row["retrieved_at"],
               "kept": row["kept"], "length": len(row["text"]), "excerpt": body(row["text"])[:EXCERPT]}
        if isinstance(text, str) and text.strip():
            found = find(row["text"], text)
            out.update(text=text, **{k: v for k, v in found.items() if k != "length"})
        return out


def silent(error: VenueError) -> str:
    """A venue's failure as the analyst reads it: one that did not answer past its retries is a read to ask again
    shortly; one that refused the store's request is what happened, since asking again would not change its answer
    (the refusal itself is logged, harness.venues.markets.read)."""
    venue = max((name for name in VENUES_SHOWN.values() if str(error).startswith(name)), key=len, default="The venue")
    if isinstance(error, VenueRefused):
        return f"{venue} refused the store's request for this, and asking again would not change its answer"
    return f"{venue} did not answer just now; try again shortly"


def interleave(found: dict[str, list]) -> list[tuple[str, Any]]:
    """Each venue's families, rank by rank: every venue's first, then every venue's second, the venue that leads
    turning at each rank, so no venue's list comes first."""
    venues = list(found)
    out = []
    for rank in range(max((len(v) for v in found.values()), default=0)):
        for venue in venues[rank % len(venues):] + venues[:rank % len(venues)]:
            if rank < len(found[venue]):
                out.append((venue, found[venue][rank]))
    return out


# A compact market row. Its depth is in dollars (MATH.md 3.3), and its header says so, as a table's header does: in a
# row a bare number stands beside quotes between 0 and 1. The field is `depth` wherever else a market is read.
COLUMNS = ("id", "question", "closes", "bid", "ask", "price", "depth ($)")


def rows_of(venue: str, markets: list[tuple[str, dict]], quotes: dict[str, dict], *, many: bool) -> list[list]:
    """Markets as compact rows, in their venue's order: each its id; its question, or, in a family of several, its own
    name among them, which the family's title completes; its close date; its book's best bid and ask, and its price,
    their midpoint, none on a side with no quote (MATH.md 3.3; three places); and its depth (whole dollars). Every other
    field, the rules and the book's levels among them, is one get away."""
    forms, out = FORMS[venue], []

    def at(q: dict, key: str) -> float | None:
        return round(q[key], 3) if q.get(key) is not None else None

    for mid, record in markets:
        listing, q = forms.listing(record), quotes.get(mid) or {}
        name = forms.label(record) if many else None
        out.append([mid, name or listing["question"], listing["close_time"], at(q, "bid"), at(q, "ask"), at(q, "price"),
                    round(q["depth"]) if q.get("depth") is not None else None])
    return out


def entry(venue: str, fid: str, title: str | None, closes: str | None, opened: list[tuple[str, dict]],
          quotes: dict[str, dict], *, many: bool, within: dict | None = None) -> dict:
    """A family as a search or a wider family shows it: its id, its venue, its name in the venue's own words, its close
    date, the family that holds it, and its markets open on the as-of as compact rows; or, past LARGE of them, how many
    are open, what each asks (asked), and the span of their closes, read whole by its id."""
    out = {"family": fid, "venue": venue, "title": title, "closes": closes, **({"within": within} if within else {})}
    if len(opened) <= LARGE:
        return {**out, "markets": rows_of(venue, opened, quotes, many=many)}
    questions, ladders = asked(venue, opened)
    closes_ = sorted(c for c in (FORMS[venue].listing(r)["close_time"] for _, r in opened) if c)
    return {**out, "open_markets": len(opened), **({"questions": questions} if questions else {}),
            **({"ladders": ladders} if ladders else {}),
            "span": {"closes": [closes_[0], closes_[-1]] if closes_ else None}}


def question_of(venue: str, record: dict) -> str | None:
    """A market's question among its family's markets, in its venue's words: its own name there, which the family's
    title completes, or else its whole question."""
    return FORMS[venue].label(record) or FORMS[venue].listing(record)["question"]


def rung(strike: Any) -> tuple[str, Any] | None:
    """Where a market stands on a ladder, by the strike its venue states as data (each form's `strike`): what the
    ladder is, its strike's type with what the strike is of (Kalshi's index, or a player; Polymarket US's direction),
    and the market's strike on it, a band's its floor and cap. None for a market that states no strike, or one that is
    no number."""
    if not isinstance(strike, dict) or not strike.get("type"):
        return None
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)  # noqa: E731
    value: Any = None
    if strike["type"] == "between":
        if number(strike.get("floor")) and number(strike.get("cap")):
            value = (strike["floor"], strike["cap"])
    else:
        given = [strike[k] for k in ("floor", "cap", "strike") if strike.get(k) is not None]
        if len(given) == 1 and number(given[0]):
            value = given[0]
    if value is None:
        return None
    of = {k: v for k, v in strike.items() if k not in ("floor", "cap", "strike")}
    return canonical(of), value


def strikes(values: list) -> dict | list:
    """A ladder's strikes, low to high: the first, the last and the step where they are evenly spaced, a band's floor
    and cap each by one step; otherwise each strike."""
    def apart(a, b):
        return tuple(y - x for x, y in zip(a, b)) if isinstance(a, tuple) else b - a
    steps = [apart(a, b) for a, b in zip(values, values[1:])]
    first = steps[0]

    def same(d):
        pairs = zip(d, first) if isinstance(d, tuple) else [(d, first)]
        return all(abs(x - y) <= 1e-9 * max(1.0, abs(y)) for x, y in pairs)
    even = all(same(d) for d in steps) and (len(set(first)) == 1 if isinstance(first, tuple) else True) \
        and (first[0] if isinstance(first, tuple) else first) > 0
    shown = [list(v) if isinstance(v, tuple) else v for v in values]
    if not even:
        return shown
    step = first[0] if isinstance(first, tuple) else first
    return {"first": shown[0], "last": shown[-1], "step": round(step, 10)}


def asked(venue: str, opened: list[tuple[str, dict]]) -> tuple[list[str], list[dict]]:
    """What a family's open markets ask: where markets differ only by the strikes their venue states as data, each such
    ladder as its first and last question, in its venue's words, its number of rungs and its strikes (strikes); every
    other market's question, in the venue's order."""
    ladders: dict[str, list] = {}
    alone: list[tuple[int, str | None]] = []
    for i, (_, record) in enumerate(opened):
        question = question_of(venue, record)
        found = rung(FORMS[venue].listing(record).get("strike"))
        if found is None:
            alone.append((i, question))
        else:
            ladders.setdefault(found[0], []).append((found[1], question, i))
    out = []
    for rungs in ladders.values():
        if len(rungs) < 2:
            alone.append((rungs[0][2], rungs[0][1]))
            continue
        rungs.sort(key=lambda r: r[0])
        out.append({"first": rungs[0][1], "last": rungs[-1][1], "rungs": len(rungs),
                    "strikes": strikes([r[0] for r in rungs])})
    return [q for _, q in sorted(alone, key=lambda a: a[0])], out


def compact(venue: str, family: Any, opened: list[tuple[str, dict]], quotes: dict[str, dict],
            within: dict[str, dict] | None = None) -> dict:
    """A family as a search returns it (entry), from the venue's search: with the family that holds it, by `within`."""
    whole = FORMS[venue].family_of({"event": family.info, "markets": []})
    above = FORMS[venue].within(family.info)
    return entry(venue, f"family:{family.id}", whole.get("title") or family.title, FORMS[venue].family_close(family),
                 opened, quotes, many=len(family.markets) > 1,
                 within=(within or {}).get(above["id"]) if above else None)


SEVERAL = (".any_of", ".all_of")  # the forms of an endpoint's filter that take several values, comma-separated


def _asked(kind: str, params: dict) -> dict:
    """The analyst's parameters as the kind's endpoint takes them (harness.venues.massive.COMPANY_PARAMS, from each
    endpoint's page). Each is a name the endpoint has, or the call is refused naming those it takes: an endpoint
    answers a name it does not have as though it were not there, so a filter misnamed would read as a search it did
    not make. Each value is a string or a number, a filter's `.any_of` and `.all_of` forms taking several,
    comma-separated: a list where several are taken is sent so, and a list of one value is that value; several values
    where one is taken, or an object, are refused, so that no value reaches the endpoint in a form it reads as another.
    The parameter that names the company is the call's `ticker`."""
    from harness.venues.massive import COMPANY, COMPANY_PARAMS
    own = COMPANY_PARAMS[kind]
    names = {f + form for f, forms in own.items() for form in forms}
    unknown = [k for k in params if k not in names]
    if unknown:
        company = COMPANY[kind][1] or "ticker"
        takes = ", ".join(f + (f" ({', '.join(x for x in forms if x)})" if len(forms) > 1 else "")
                          for f, forms in own.items() if f != company)
        raise StoreError(f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not a filter of "
                         f"search_company's {kind}: it takes {takes}")
    out: dict = {}
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            if str(key).endswith(SEVERAL):
                value = ",".join(str(v) for v in value)
            elif len(value) == 1:
                value = value[0]
            else:
                forms = own.get(key, ())
                offered = [f"{key}.any_of takes any of several"] if ".any_of" in forms else []
                offered += [f"{key}.all_of all of them"] if ".all_of" in forms else []
                raise StoreError(f"{key} takes one value" + (f"; {' and '.join(offered)}" if offered else ""))
        if isinstance(value, (list, tuple, dict)):
            raise StoreError(f"{key} takes a string or a number")
        out[key] = value
    return out


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
    """A market's price as a read shows it: its price where both sides quote, its best bid and ask, its depth, and
    its last trade with its time, with the reason where there is no book; the moment its quotes stand at, when the
    book it rests on was read; at a past as-of, the day of the venue's closing quotes where it rests on them, and,
    where its venue's history holds a price alone, that price."""
    out = {"price": q.get("price"), "bid": q.get("bid"), "ask": q.get("ask"), "depth": q.get("depth"),
           "last_trade": q.get("last_trade"), **({"quoted_at": q["quoted_at"]} if q.get("quoted_at") else {})}
    if q.get("past"):
        out.update({k: q.get(k) for k in ("price_date",) if q.get(k)})
        if q.get("history_price") is not None:
            out["history_price"] = q["history_price"]
    if q.get("reason"):
        out["reason"] = q["reason"]
    return out
