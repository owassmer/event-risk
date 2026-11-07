"""Massive: ticker reference, daily aggregates, dividends, ETF Global constituents, and a stock's
snapshot, its latest mark. Base `https://api.massive.com`; each shape below is verified against its
documentation page and a live response.

The key, `MASSIVE_API_KEY`, travels as `Authorization: Bearer <key>` (https://massive.com/docs/rest/quickstart)
and nowhere else, so every request is recorded by its URL with no credential in it. A refusal for entitlement (401,
403), a ticker Massive does not know (404), and one it cannot read as a ticker (400, "Invalid ticker", as a fund's
local listing is) are answers, recorded as data like any other, so the store does not ask again. A network or server
failure raises, and the next read retries. A plan's asset classes have no request limit and the others are on the
free tier, five requests a minute (https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis);
a 429 is waited out. A crypto pair's prices come from Coinbase Exchange (harness.venues.coinbase).

- `ticker_ref`: `GET /v3/reference/tickers/{t}?date={asof}` (https://massive.com/docs/rest/stocks/tickers/ticker-overview).
  `results` holds `type`, the asset type; `name`, the company's registered name or the fund's; `description`, what
  the company does, in the reference's words; `sic_code` and `sic_description`, its industry, which a fund mostly
  lacks; `locale` (`us` or `global`) and `market` (`stocks`, ...); `primary_exchange`, an ISO exchange code; and
  `composite_figi` and `share_class_figi`, the listing's OpenFIGI identifiers, either of which can be absent.
- `listings`: `GET /v3/reference/tickers?market=stocks&active=true&date={asof}&limit=1000`, every page
  (https://massive.com/docs/rest/stocks/tickers/all-tickers): each US listing active on the date, with `ticker`,
  `type`, `primary_exchange`, `locale` us, and, where Massive has them, `composite_figi` and `share_class_figi`: some
  fourteen pages and thirteen thousand listings, about 4 MB, four in five with a composite FIGI.
- `bars`: `GET /v2/aggs/ticker/{t}/range/1/day/{from}/{to}?adjusted=true&sort=asc&limit=50000`
  (https://massive.com/docs/rest/stocks/aggregates/custom-bars).
  Each bar is `{o, h, l, c, v, vw, n, t}`, `t` the bar's start in Unix milliseconds, 00:00 New York of the session
  for a daily bar. Aggregates are built from qualifying trades across the pre-market, regular, and after-hours
  sessions, and a daily bar's `c` is the session's official close: the `close` that the Daily Ticker Summary,
  `GET /v1/open-close/{t}/{date}` (https://massive.com/docs/rest/stocks/aggregates/daily-ticker-summary), gives apart
  from its `afterHours`, the last trade after the close. `adjusted=true` adjusts for splits, not dividends. `limit`
  counts the base aggregates a bar is built from, and `next_url` pages.
- `dividends`: `GET /stocks/v1/dividends?ticker=&ex_dividend_date.gte=&ex_dividend_date.lte=&sort=ex_dividend_date.asc&limit=5000`
  (https://massive.com/docs/rest/stocks/corporate-actions/dividends), each row with `id`, `ex_dividend_date`,
  `cash_amount`, `split_adjusted_cash_amount` (on today's share basis), `currency`, `distribution_type`, and the
  declaration, record, and pay dates; `next_url` pages.
- `snapshot`: `GET /v2/snapshot/locale/us/markets/stocks/tickers/{t}`
  (https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot): `ticker` holds `lastTrade` (`p`, and `t`
  in SIP nanoseconds), `min` (the latest minute bar, `c` and `t` in milliseconds), `day`, `prevDay`, and `updated`.
  It is cleared at 3:30 AM Eastern and fills again from 4:00 AM.
- `news`: `GET /v2/reference/news?ticker=&published_utc.gte=&published_utc.lte=&order=desc&sort=published_utc&limit=5`
  (https://massive.com/docs/rest/stocks/news), every Stocks plan: a ticker's articles, newest first, each with `id`,
  `published_utc`, `publisher` (`name`, `homepage_url`, `logo_url`, `favicon_url`), `title`, `author`, `description`,
  `keywords`, `tickers`, `article_url`, `amp_url`, `image_url`, and `insights`, a sentiment label per ticker. The
  ticker filter is exact, and `published_utc.gte` and `.lte` take an RFC 3339 time.
- `company`: a company's own record, one endpoint for each kind, the analyst's parameters passed as given beside the
  company's identity and the as-of's bound. Each answers `{status, request_id, results, next_url?}`, and a list's rows
  as below; a plan without the endpoint answers 403. The kinds:
  - `news`: `GET /benzinga/v2/news?tickers=` (https://massive.com/docs/rest/partners/benzinga/news), Benzinga's
    newswire, newest first, 100 by default: `{benzinga_id, published, last_updated, author, title, teaser, body,
    url, channels, tags, tickers, images}`, `teaser` and `body` in HTML, `body` the article's full text where it has
    one; dated by `published`.
  - `earnings`: `GET /benzinga/v1/earnings?ticker=` (https://massive.com/docs/rest/partners/benzinga/earnings), the
    reported and scheduled results: `date`, `time`, `date_status`, `fiscal_period`, `fiscal_year`, `currency`, the
    actual, estimated, and previous EPS and revenue with each surprise, their methods, `importance`, `notes`; dated by
    `date`.
  - `guidance`: `GET /benzinga/v1/guidance?ticker=` (https://massive.com/docs/rest/partners/benzinga/corporate-guidance),
    the ranges the company issued, with the prior ranges and the consensus at the time; dated by `date`.
  - `ratings`: `GET /benzinga/v1/consensus-ratings/{ticker}`
    (https://massive.com/docs/rest/partners/benzinga/consensus-ratings), one row: the consensus rating and its value
    from 1 to 5, the count of each rating, the consensus, high, and low price targets, and the contributors; the
    ratings aggregated are dated by `date`.
  - `risk_factors`: `GET /stocks/filings/vX/risk-factors?ticker=`
    (https://massive.com/docs/rest/stocks/filings/risk-factors), each a filing's risk factor in Massive's three-level
    taxonomy, `supporting_text` its words, dated by `filing_date`; a row names no filing URL, so the 10-K sections
    filed on the same dates give each filing's.
  - `annual_report`: `GET /stocks/filings/10-K/vX/sections?ticker=`
    (https://massive.com/docs/rest/stocks/filings/10-k-sections), 10 by default: each a 10-K section's whole text,
    with `section` (`business`, `risk_factors`, or `mda`), `filing_date`, `period_end`, and `filing_url`, the SEC
    filing's.
  - `current_reports`: `GET /stocks/filings/8-K/vX/text?ticker=`
    (https://massive.com/docs/rest/stocks/filings/8-k-text), 10 by default: each 8-K's items as text, with
    `form_type`, `accession_number`, `filing_date`, and `filing_url`.

  The SEC kinds take `ticker` or `cik`, and `filing_date` with `.gte` and `.lte`, and each row names its `ticker` and
  `cik`. Read by ticker, they return the filings of every registrant tagged with it: the company's under each
  registrant it has filed as, and an earlier holder's of the ticker, which the listing date leaves out. A row filed
  before the company took its ticker carries its ticker then, or none, so a company that changed its ticker has its
  earlier filings under the old one. A filing made by several registrants is listed once under each, with the same
  accession number.
- `constituents`: `GET /etf-global/v1/constituents` (https://massive.com/docs/rest/partners/etf-global/constituents),
  a separate entitlement. `composite_ticker` names the fund; `effective_date` and `processed_date` take `.lte`,
  `.gte`, `.lt`, `.gt`; `sort` takes only `composite_ticker`, `processed_date`, or `constituent_ticker` (sorting by
  `effective_date` is answered 400); `limit` at most 5000; `next_url` pages. Each row has `constituent_ticker`,
  `constituent_name`, `constituent_rank`, `weight` (a decimal fraction, negative for a short, absent on a few rows),
  `asset_class` (`Equity`, `Cash`, `Fixed Income`, `Alternative`, `Real Estate`, others, in any case, or none),
  `security_type` (ETF Global's own taxonomy: a share's kind, or a bond's issuer and kind), `effective_date`,
  `processed_date`, `market_value`, and `shares_held`; and, where known, its identifiers `figi`, `isin`, `sedol`,
  and `us_code`, and where it trades, `country_of_exchange` (a two-letter country code), `exchange`, and
  `currency_traded`. A share listed abroad carries its local ticker, which can be an unrelated US company's. A US
  share's `figi` is its US composite FIGI, the reference's `composite_figi`, though some rows lack it or carry
  another level. A fund's rows can be reported twice in one processing, identical in every field.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as Date, datetime, timedelta, timezone
from typing import Any, Callable

from harness.derive.series import NY

BASE = "https://api.massive.com"
PAGE = 1000  # rows per ETF Global page: the documented maximum is 5000, and a 5000-row request timed out
NEWS = 5  # the most recent articles read for each of the portfolio's largest companies
# Each kind of a company's record: its endpoint, and the parameter that names the company in it.
COMPANY = {"news": ("/benzinga/v2/news", "tickers"), "earnings": ("/benzinga/v1/earnings", "ticker"),
           "guidance": ("/benzinga/v1/guidance", "ticker"), "ratings": ("/benzinga/v1/consensus-ratings/{ticker}", None),
           "risk_factors": ("/stocks/filings/vX/risk-factors", "ticker"),
           "annual_report": ("/stocks/filings/10-K/vX/sections", "ticker"),
           "current_reports": ("/stocks/filings/8-K/vX/text", "ticker")}


class MassiveError(Exception):
    """Massive did not answer: the network failed, or the server kept erring."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Massive:
    """A Massive client whose fetchers feed the store's data rows."""

    def __init__(self, key: str, *, opener: Callable | None = None, base: str = BASE, timeout: float = 60,
                 retries: int = 3, throttled: int = 8, pause: float = 1.0):
        if not key:
            raise ValueError("Massive needs its key")
        self._key = key
        self._open = opener or urllib.request.urlopen
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries  # for server errors and the network
        self.throttled = throttled  # for 429: the plan's rate limit, waited out
        self.pause = pause

    # ---------------------------------------------------------------- HTTP

    def url(self, path: str, params: dict | None = None) -> str:
        return self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")

    def _wait(self, error: urllib.error.HTTPError, attempt: int) -> float:
        """How long a throttled request waits: the server's Retry-After, or a backoff to a minute."""
        after = (error.headers or {}).get("Retry-After") if error.headers is not None else None
        try:
            return min(float(after), 60.0)
        except (TypeError, ValueError):
            return min(self.pause * 2 ** attempt, 60.0)

    def get(self, url: str) -> tuple[int, dict]:
        """The status and JSON body of one GET. Throttling is waited out; server errors and
        network failures are retried."""
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._key}",
                                                       "Accept": "application/json",
                                                       "User-Agent": "event-risk"})
        throttled = failed = 0
        while True:
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code == 429 and throttled < self.throttled:
                    throttled += 1
                    time.sleep(self._wait(e, throttled))
                    continue
                if e.code in (500, 502, 503, 504) and failed < self.retries:
                    failed += 1
                    time.sleep(self.pause * 2 ** failed)
                    continue
                if e.code in (400, 401, 403, 404):
                    try:
                        parsed = json.loads(body)
                    except ValueError:
                        parsed = {"error": body[:300].decode("utf-8", "replace")}
                    status = {400: "INVALID", 404: "NOT_FOUND"}.get(e.code, "NOT_AUTHORIZED")
                    return e.code, {"status": status, "http_status": e.code,
                                    "error": parsed.get("error") or parsed.get("message")}
                raise MassiveError(f"{url.split('?')[0]} answered {e.code}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if failed < self.retries:
                    failed += 1
                    time.sleep(self.pause * 2 ** failed)
                    continue
                raise MassiveError(f"{url.split('?')[0]}: {e}") from None

    def pages(self, url: str) -> tuple[dict, list[str]]:
        """Every page of a paged response, following next_url: the first body with all
        results, and the URLs requested."""
        status, body = self.get(url)
        urls = [url]
        if status != 200:
            return body, urls
        results = list(body.get("results") or [])
        while body.get("next_url"):
            urls.append(body["next_url"])
            status, body = self.get(body["next_url"])
            if status != 200:
                raise MassiveError(f"a page of {url.split('?')[0]} answered {status}")
            results.extend(body.get("results") or [])
        body = {k: v for k, v in body.items() if k not in ("results", "next_url")}
        body["results"] = results
        return body, urls

    @staticmethod
    def _record(urls: list[str]) -> dict:
        return {"method": "GET", "url": urls[0], "pages": len(urls)} if len(urls) > 1 else \
            {"method": "GET", "url": urls[0]}

    # ---------------------------------------------------------------- endpoints

    def ticker(self, ticker: str, date: str) -> dict:
        """/v3/reference/tickers/{t} as of `date`: type, sic_code, and the rest of the reference."""
        url = self.url(f"/v3/reference/tickers/{urllib.parse.quote(ticker, safe=':.-')}", {"date": date})
        retrieved = _now()
        _, body = self.get(url)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (date, date)}

    def listings(self, date: str) -> dict:
        """/v3/reference/tickers: every US stock listing active on `date`, every page."""
        url = self.url("/v3/reference/tickers", {"market": "stocks", "active": "true", "date": date, "limit": 1000})
        retrieved = _now()
        body, urls = self.pages(url)
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (date, date)}

    def bars(self, ticker: str, start: str, end: str, *, timespan: str = "day") -> dict:
        """/v2/aggs/ticker/{t}/range/1/{timespan}/{start}/{end}, split-adjusted, every page."""
        url = self.url(f"/v2/aggs/ticker/{urllib.parse.quote(ticker, safe=':.-')}/range/1/{timespan}/{start}/{end}",
                       {"adjusted": "true", "sort": "asc", "limit": 50000})
        retrieved = _now()
        body, urls = self.pages(url)
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (start, end)}

    def dividends(self, ticker: str, start: str, end: str) -> dict:
        """/stocks/v1/dividends: the ticker's cash dividends whose ex-dividend date falls in
        [start, end], every page."""
        url = self.url("/stocks/v1/dividends",
                       {"ticker": ticker, "ex_dividend_date.gte": start, "ex_dividend_date.lte": end,
                        "sort": "ex_dividend_date.asc", "limit": 5000})
        retrieved = _now()
        body, urls = self.pages(url)
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (start, end)}

    def constituents(self, fund: str, asof: str) -> dict:
        """A fund's constituents at the latest effective_date on or before `asof`, in two requests: the latest
        effective_date among the most recently processed rows on or before `asof` is the date, and then every row
        of that date's latest processing. ETF Global processes one effective date's holdings again each day until
        the next, each processing the whole basket, and one processing can carry more than one effective date."""
        retrieved = _now()
        find = self.url("/etf-global/v1/constituents",
                        {"composite_ticker": fund, "effective_date.lte": asof, "sort": "processed_date.desc",
                         "limit": PAGE})
        status, body = self.get(find)
        if status != 200:
            return {"payload": body, "request": self._record([find]), "retrieved_at": retrieved,
                    "range": (asof, asof)}
        dates = [r.get("effective_date") for r in body.get("results") or [] if r.get("effective_date")]
        if not dates:
            payload = {"status": "OK", "composite_ticker": fund, "effective_date": None, "results": []}
            return {"payload": payload, "request": self._record([find]), "retrieved_at": retrieved,
                    "range": (asof, asof)}
        effective = max(dates)
        processed = max((r["processed_date"] for r in body.get("results") or []
                         if r.get("effective_date") == effective and r.get("processed_date")), default=None)
        url = self.url("/etf-global/v1/constituents",
                       {"composite_ticker": fund, "effective_date": effective,
                        **({"processed_date": processed} if processed else {}), "limit": PAGE})
        body, urls = self.pages(url)
        body.update({"composite_ticker": fund, "effective_date": effective, "processed_date": processed})
        return {"payload": body, "request": self._record([find] + urls), "retrieved_at": retrieved,
                "range": (asof, asof)}

    def snapshot(self, ticker: str, date: str) -> dict:
        """/v2/snapshot/locale/us/markets/stocks/tickers/{t}: the ticker's latest trade, latest minute bar, and the
        previous day's bar, now. It is read on the as-of date for that date's latest mark."""
        url = self.url(f"/v2/snapshot/locale/us/markets/stocks/tickers/{urllib.parse.quote(ticker, safe=':.-')}")
        retrieved = _now()
        _, body = self.get(url)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (date, date)}

    def news(self, ticker: str, since: str, day: str) -> dict:
        """/v2/reference/news: a ticker's NEWS most recent articles published from the start of `since` to the end of
        `day`, in New York."""
        start = datetime.combine(Date.fromisoformat(since), datetime.min.time(), NY)
        end = datetime.combine(Date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), NY)
        utc = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        url = self.url("/v2/reference/news", {"ticker": ticker, "published_utc.gte": utc(start),
                                              "published_utc.lte": utc(end), "order": "desc",
                                              "sort": "published_utc", "limit": NEWS})
        retrieved = _now()
        _, body = self.get(url)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (since, day)}

    def company(self, subject: str, day: str) -> dict:
        """One kind of a company's record: `subject` is `{"kind", "ticker", "cik", "params"}` as canonical JSON, the
        params already naming the company and bounded by its listing date and the as-of, and `cik` its registrant's,
        or null. One page, the endpoint's own; for risk factors, the URLs of the filings they come from beside it."""
        ask = json.loads(subject)
        path, _ = COMPANY[ask["kind"]]
        path = path.format(ticker=urllib.parse.quote(ask["ticker"], safe=":.-"))
        url = self.url(path, ask["params"])
        retrieved = _now()
        status, body = self.get(url)
        urls = [url]
        if ask["kind"] == "risk_factors" and status == 200 and body.get("results"):
            dates = sorted({r["filing_date"] for r in body["results"] if r.get("filing_date")})
            lookup = self.url(COMPANY["annual_report"][0], {"ticker": ask["ticker"], "section": "risk_factors",
                                                            "filing_date.gte": dates[0], "filing_date.lte": dates[-1],
                                                            "limit": 100})
            urls.append(lookup)
            found_status, found = self.get(lookup)
            filings: dict[str, str] = {}  # each date's filing, the company's own listing of it where it has several
            for r in (found.get("results") or []) if found_status == 200 else []:
                if r.get("filing_url") and (r.get("filing_date") not in filings or r.get("cik") == ask["cik"]):
                    filings[r["filing_date"]] = r["filing_url"]
            body = {**body, "filing_urls": filings}
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (day, day)}

    # ---------------------------------------------------------------- the store's fetchers

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        """The data kinds this client fetches, each a function of (subject, covering)."""
        return {
            "ticker_ref": lambda subject, covering: self.ticker(subject, covering[1]),
            "listings": lambda subject, covering: self.listings(covering[1]),
            "bars": lambda subject, covering: self.bars(subject, covering[0], covering[1]),
            "dividends": lambda subject, covering: self.dividends(subject, covering[0], covering[1]),
            "constituents": lambda subject, covering: self.constituents(subject, covering[1]),
            "snapshot": lambda subject, covering: self.snapshot(subject, covering[1]),
            "news": lambda subject, covering: self.news(subject, covering[0], covering[1]),
            "company": lambda subject, covering: self.company(subject, covering[1]),
        }
