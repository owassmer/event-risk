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
- `cusip_listing`: `GET /v3/reference/tickers?cusip={cusip}&date={day}`
  (https://massive.com/docs/rest/stocks/tickers/all-tickers): the listing that carried a security on a date, by its
  CUSIP, which the answer never gives back: Exxon Mobil's 30231G102 on 2026-06-30 is XOM with CIK 34088, and the
  same CUSIP today answers no listing, the security having become ExxonMobil Holdings' (CIK 2115436) on 2026-07-02.
  `ticker_on` is a ticker's reference on a date other than the as-of (`{ticker}@{day}`), as `ticker_ref` is.
- `listings`: `GET /v3/reference/tickers?market=stocks&active=true&date={asof}&limit=1000`, every page
  (https://massive.com/docs/rest/stocks/tickers/all-tickers): each US listing active on the date, with `ticker`,
  `type`, `primary_exchange`, `locale` us, and, where Massive has them, `composite_figi` and `share_class_figi`: some
  fourteen pages and thirteen thousand listings, about 4 MB, four in five with a composite FIGI.
- `company_listings`: `GET /v3/reference/tickers?cik={cik}&date={asof}&active=true&limit=1000`
  (https://massive.com/docs/rest/stocks/tickers/all-tickers): the listings of the company filing under a CIK active on
  the date, each `{ticker, name, market, locale, type, primary_exchange, active, cik, composite_figi?,
  share_class_figi?}`. Massive gives a CIK for a listing on an exchange (`market` stocks) and none for one over the
  counter (`market` otc), so these are the company's exchange listings.
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
  declaration, record, and pay dates; `next_url` pages. A record paid in another currency than USD carries no
  `split_adjusted_cash_amount` (nor `historical_adjustment_factor`): of the 109,276 records with an ex-date in two
  years, every one of the 19,971 in another currency lacked it, and no USD record with a past ex-date did. A dividend
  can be listed twice under two ids, identical in every other field.
- `splits`: `GET /stocks/v1/splits?ticker=&execution_date.gte=&sort=execution_date.asc&limit=5000`
  (https://massive.com/docs/rest/stocks/corporate-actions/splits), each row with `id`, `execution_date`, `split_from`,
  `split_to`, `adjustment_type` (`forward_split`, `reverse_split`, `stock_dividend`), and
  `historical_adjustment_factor`: a quantity of date D is put on today's share basis by the factor of the first split
  executed after D. Every split from the start of the window to now, so that it matches the split-adjusted bars.
- `events`: `GET /vX/reference/tickers/{t}/events?types=ticker_change`
  (https://massive.com/docs/rest/stocks/corporate-actions/ticker-events), experimental: `{status, results: {name,
  events: [{type: "ticker_change", date, ticker_change: {ticker}}]}}`, newest first, the tickers the company now
  listed under `t` has traded under, each from its date. Aggregates and dividends are kept by ticker and split at a
  change: measured, FB's bars end 2022-06-08 and META's begin 2022-06-09; and a ticker taken by another company keeps
  the earlier holder's history under it (BNY's bars and monthly dividends before 2026-05-21 are a municipal bond
  fund's, Bank of New York Mellon's are BK's). So a stock's `bars` and `dividends` over a window a ticker change falls
  in are its company's: each ticker it traded under, read over the days it traded under it, the current one from its
  change on, joined in date order, with `tenure` naming each ticker and its days. Massive's events follow one
  security: a company that moved its US listing from one security to another under the same ticker (TotalEnergies,
  from its depositary receipt to its ordinary shares on 2025-12-08) has events naming only the new security's earlier
  placeholder ticker, which traded nothing; where an earlier ticker gives no rows, the ticker's own earlier rows stand,
  if its reference names the same company (one CIK) then as now (`read` names the ticker read). Before the first
  event, the security had no ticker Massive's events name, though its rows can be under one: a security Massive
  re-identified lists only itself (USO from 2020-04-29, at its reverse split, GLD from 2008-05-22, UUP from 2018-06-04,
  EWT and SMH from 2016-11-04 and 2016-05-03, each of whose bars go back to its listing and join continuously). So the
  days before the first event are read under its first ticker, and stand where that ticker's reference the day before
  names the same company as the current ticker's does now; a ticker another company held then names another CIK, and
  its rows are left out. A reference of long ago can name no CIK (GLD's before 2008-05-22): then the two are the same
  listing where they name one composite FIGI, or else one listing date, since a ticker another listing held before
  names that listing's own (BNY's on 2025-01-02 names a municipal fund's of 2001-07-27, and BNY Mellon's of 1969-12-04
  today), and no two listings under one ticker listed on one day. An earlier ticker's bars are
  adjusted for its own splits only, so each is put on today's share basis by the splits executed under the tickers
  after it, as is an earlier dividend's split-adjusted amount. A ticker Massive gives no events for (not a stock,
  or a 404) is read as itself. Of VTI's 3,423 holdings on 2026-11-09, 76 (0.54% of its weight) changed ticker inside
  two years.
- Every listing Massive prices is quoted in USD (its reference's `currency_name`). A currency pair's rates come from
  Yahoo Finance (harness.venues.yahoo), and a listing abroad's prices from its home market there.
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
  another level. A fund's rows can be reported twice in one processing, identical in every field. Read over a window
  (`effective_date.gte` and `.lte`), it gives each day's rows, a day with a second processing carrying it beside the
  first (read live: PJAN's 2026-11-06 processed twice, one with each FLEX contract's ticker and one without). An
  option row's `asset_class` is `Option` or none, its `market_value` the position's value and its `shares_held` its
  contracts, signed; a FLEX contract's value is the fund's own, which no market quotes (PJAN, BALT, QYLD). Read over a
  window with no `sort`, its pages can end early: PJAN's two years stopped at 2,000 rows and 373 of its 499 days,
  read live twice alike; sorted by `processed_date`, every row of each half-year read alone came back, and no other.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from harness.venues.http import TRANSIENT, VenueError, VenueRefused, pooled

NY = ZoneInfo("America/New_York")  # the exchanges' clock, as harness.derive.series keeps it

BASE = "https://api.massive.com"
PAGE = 1000  # rows per ETF Global page: the documented maximum is 5000, and a 5000-row request timed out
NEWS = 5  # the most recent articles read for each of the portfolio's largest companies
# The form of a stock's daily bars and dividends over a window (`tenure`): 2, only the ticker changes made by the
# window's end joining its rows. An answer kept in an earlier form is read again, since one read at a past as-of could
# hold a company's rows cut short at a change made after it, or rows from after its end.
JOINED = 2
# The form of a fund's holdings answer (`constituents`): 2, the latest processing made by the as-of (MATH.md 3.1). An
# answer kept in an earlier form, which took the latest processing whenever it was made, is read again.
HELD = 2
# ETF Global's asset class for an option. A row it labels so, or labels as nothing, can be an option; one it labels as
# anything else is that (harness.derive.rows.option_symbol).
OPTION_LABELS = ("option",)
# The form of a fund's holdings over a window (`holdings_history`): 1, each day's net assets and its rows that can be
# options, in the latest processing made by the as-of.
HISTORY = 1
# What a window's read keeps of each row that can be an option: what names the contract and what the fund holds of it.
HISTORY_FIELDS = ("constituent_ticker", "constituent_name", "asset_class", "security_type", "market_value",
                  "shares_held", "weight")
# Each kind of a company's record: its endpoint, and the parameter that names the company in it.
COMPANY = {"news": ("/benzinga/v2/news", "tickers"), "earnings": ("/benzinga/v1/earnings", "ticker"),
           "guidance": ("/benzinga/v1/guidance", "ticker"), "ratings": ("/benzinga/v1/consensus-ratings/{ticker}", None),
           "risk_factors": ("/stocks/filings/vX/risk-factors", "ticker"),
           "annual_report": ("/stocks/filings/10-K/vX/sections", "ticker"),
           "current_reports": ("/stocks/filings/8-K/vX/text", "ticker")}
# Each kind's endpoint's parameters, as its page lists them (https://massive.com/docs/rest/partners/
# benzinga/news, .../benzinga/earnings, .../benzinga/corporate-guidance, .../benzinga/consensus-ratings, and
# https://massive.com/docs/rest/stocks/filings/risk-factors, .../10-k-sections, .../8-k-text): each field with the forms
# it takes after it. An endpoint answers a name it does not have as though it were not there (read live: Benzinga's
# news answered `channelz=News` with every article), so a name is checked against these before it is asked.
_RANGE = ("", ".gt", ".gte", ".lt", ".lte")
_EVERY = ("", ".any_of", ".gt", ".gte", ".lt", ".lte")
_ARRAY = ("", ".all_of", ".any_of")
_PAGE = {"limit": ("",), "sort": ("",), "fields": ("",)}
COMPANY_PARAMS = {
    "news": {"published": _RANGE, "channels": _ARRAY, "tags": _ARRAY, "author": ("", ".any_of", ".gt", ".gte", ".lt", ".lte"),
             "stocks": _ARRAY, "tickers": _ARRAY, **_PAGE},
    "earnings": {**{f: _EVERY for f in ("date", "ticker", "importance", "last_updated", "date_status",
                                       "eps_surprise_percent", "revenue_surprise_percent", "fiscal_year",
                                       "fiscal_period")}, **_PAGE},
    "guidance": {**{f: _EVERY for f in ("date", "ticker", "positioning", "importance", "last_updated", "fiscal_year",
                                       "fiscal_period")}, **_PAGE},
    "ratings": {"date": _EVERY, "ticker": ("",), "limit": ("",), "fields": ("",)},
    "risk_factors": {"filing_date": _EVERY, "ticker": _EVERY, "cik": _EVERY, **_PAGE},
    "annual_report": {"cik": _EVERY, "ticker": _EVERY, "section": ("", ".any_of"), "filing_date": _RANGE,
                      "period_end": _RANGE, **_PAGE},
    "current_reports": {"cik": _EVERY, "ticker": _EVERY, "form_type": _EVERY, "filing_date": _RANGE, **_PAGE},
}


# What a Massive answer's `status` says. "OK" is an answer with its data. "DELAYED" is too: on an aggregates request
# whose range reaches the current day, on a product the key holds at a delayed tier, "the bars in it were served
# delayed rather than in real time. It is not an error: the HTTP status is 200 and the bars are there"; a range that
# ends before the current day answers "OK" on the same key, and an endpoint can answer "OK" with data as delayed
# (https://massive.com/knowledge-base/article/how-quickly-can-i-access-massives-market-date). Read live on this key:
# daily bars of I:SPX, C:EURUSD, C:JPYUSD, and X:BTCUSD to the current day answer "DELAYED" with every bar, those of
# SPY and AAPL "OK"; references, listings, constituents, dividends, splits, news, and a company's records "OK". A
# time a value carries comes from the data's own timestamps, never from this status. The other statuses the client
# writes itself, from the HTTP status of a refusal: NOT_FOUND, INVALID, NOT_AUTHORIZED.
ANSWERED = ("OK", "DELAYED")

# Massive's symbol for an index its feed names otherwise than Bloomberg does, by the index's FIGI as OpenFIGI names
# it (harness.derive.rows.index_symbol). A fund's report identifies an index by Bloomberg's code, which OpenFIGI maps
# to its FIGI and ticker; Massive gives an index's values (`/v2/aggs/ticker/I:{symbol}/...`) under its feed's code,
# which for most indexes is Bloomberg's ("NDX", "SPX") and for these is not. Each entry translates one identifier
# into another, as each provider's own document states it; an index with no entry is read under OpenFIGI's ticker,
# and one Massive does not carry under it has no values.
INDEX_SYMBOLS = {
    # The Dow Jones averages, on CME's Dow Jones feed (Massive's index reference, `source_feed`
    # CMEMarketDataPlatformDowJones). S&P Dow Jones Indices states their Bloomberg codes, DJI, TRAN, UTIL, and COMP,
    # and their Refinitiv RICs, .DJI, .DJT, .DJU, and .DJA, in its Dow Jones Averages Methodology, Tickers
    # (https://www.spglobal.com/spdji/en/documents/methodologies/methodology-dj-averages.pdf); Bloomberg's older code
    # for the industrial average, INDU, which funds' reports give, is OpenFIGI's "INDU Index", another FIGI.
    "BBG000H59SG1": "I:DJI",  # INDU, the Dow Jones Industrial Average
    "BBG000H5BW33": "I:DJT",  # TRAN, the Dow Jones Transportation Average
    "BBG000H5BWC3": "I:DJU",  # UTIL, the Dow Jones Utility Average
    "BBG000H5BXF8": "I:DJC",  # COMP, the Dow Jones Composite Average
    # The Russell 1000 and 2000, on Cboe's FTSE feed (CboeGlobalIndicesFTSE). FTSE Russell states each one's Bloomberg
    # price-return ticker and Refinitiv price-return RIC: RIY and .RUI
    # (https://www.lseg.com/content/dam/ftse-russell/en_us/documents/other/russell-1000-index-product-highlights-cut-sheet.pdf),
    # RTY and .RUT
    # (https://www.lseg.com/content/dam/ftse-russell/en_us/documents/other/russell-2000-index-product-highlights-revisions-cut-sheet.pdf).
    # The Russell 3000 (RAY) has no values on Massive.
    "BBG000HS18T9": "I:RUI",  # RIY, the Russell 1000
    "BBG000HS1999": "I:RUT",  # RTY, the Russell 2000
    # The Select Sector indexes, on Cboe's feed (CboeGlobalIndicesMain). S&P Dow Jones Indices states their Bloomberg
    # codes, IXY, IXR, IXE, IXM, IXV, IXI, IXB, IXRE, IXT, IXU, in its U.S. Indices Methodology
    # (https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf), and Cboe, which
    # disseminates them, their symbols, SIXY, SIXR, SIXE, SIXM, SIXV, SIXI, SIXB, SIXRE, SIXT, SIXU, in its Select
    # Sector Index Options fact sheet
    # (https://cdn.cboe.com/resources/education/research_publications/select-sector-indexes-fact-sheet.pdf), each
    # against the index's name. Communication Services is left out: the methodology's text does not give its
    # price-return code legibly.
    "BBG000PQ2X54": "I:SIXY",  # IXY, Consumer Discretionary
    "BBG000PQ2T62": "I:SIXR",  # IXR, Consumer Staples
    "BBG000PQ2QJ4": "I:SIXE",  # IXE, Energy
    "BBG000PQ2SR1": "I:SIXM",  # IXM, Financial
    "BBG000PQ2WK9": "I:SIXV",  # IXV, Health Care
    "BBG000PQ2SB8": "I:SIXI",  # IXI, Industrial
    "BBG000PQ13P9": "I:SIXB",  # IXB, Materials
    "BBG009THYM41": "I:SIXRE",  # IXRE, Real Estate
    "BBG000PQ2VL0": "I:SIXT",  # IXT, Technology
    "BBG000PQ2W92": "I:SIXU",  # IXU, Utilities
}


FEED_SUFFIXED = re.compile(r"(I:[^:]+):(.+)")  # an index's ticker with its feed after it, as an option's snapshot gives
NAMED_AS_REFERENCED = 2  # the form option snapshots are kept in: an index underlying named as its reference names it


def answered(payload: Any) -> bool:
    """Whether a Massive payload is an answer with its data: "OK", or "DELAYED", served late and whole."""
    return isinstance(payload, dict) and payload.get("status") in ANSWERED


class MassiveError(VenueError):
    """Massive did not answer: the network failed, or the server kept erring."""


def _parsed(fetcher: Callable[[str, Any], dict], parse: int) -> Callable[[str, Any], dict]:
    """A fetcher declaring the form its answers are in, so that one kept in another is read again
    (harness.observations)."""
    fetcher.parse = parse  # type: ignore[attr-defined]
    return fetcher


def _nanos_iso(stamp: Any) -> str | None:
    """A moment Massive gives in Unix nanoseconds, in ISO 8601 UTC."""
    if not isinstance(stamp, (int, float)) or not stamp:
        return None
    return datetime.fromtimestamp(stamp / 1e9, timezone.utc).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session(stamp: Any) -> str | None:
    """A daily bar's session: the New York date its start falls on."""
    return datetime.fromtimestamp(stamp / 1000, NY).date().isoformat() if isinstance(stamp, (int, float)) else None


class Massive:
    """A Massive client whose fetchers feed the store's data rows."""

    def __init__(self, key: str, *, opener: Callable | None = None, base: str = BASE, timeout: float = 60,
                 retries: int = 3, throttled: int = 8, pause: float = 1.0):
        if not key:
            raise ValueError("Massive needs its key")
        self._key = key
        self._open = opener or pooled()
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries  # for server errors and the network
        self.throttled = throttled  # for 429: the plan's rate limit, waited out
        self.pause = pause
        self._events: dict[tuple[str, str], list[tuple[str, str]]] = {}  # each ticker's changes, read once a day

    # ---------------------------------------------------------------- HTTP

    def url(self, path: str, params: dict | None = None) -> str:
        """An endpoint's URL with its parameters, each a string or a number: a list or an object has no form an
        endpoint takes, and would be sent as its Python text ("['news']"), which the endpoint reads as a value no
        record has."""
        bad = [k for k, v in (params or {}).items() if isinstance(v, (list, tuple, dict))]
        if bad:
            raise ValueError(f"{', '.join(bad)}: a parameter's value is a string or a number")
        return self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")

    def _wait(self, error: urllib.error.HTTPError, attempt: int) -> float:
        """How long a throttled request waits: the server's Retry-After, or a backoff to a minute."""
        after = (error.headers or {}).get("Retry-After") if error.headers is not None else None
        try:
            return min(float(after), 60.0)
        except (TypeError, ValueError):
            return min(self.pause * 2 ** attempt, 60.0)

    def get(self, url: str, *, names: bool = False) -> tuple[int, dict]:
        """The status and JSON body of one GET. Throttling is waited out; server errors and network failures are
        retried, and past their retries raise MassiveError. A refusal for entitlement (401, 403) and a ticker Massive
        does not know (404) are answers. A 400 is read by what the endpoint means by it: where the request carries a
        name the analyst or the person gave (`names`: a ticker's reference, a company's record with the analyst's own
        parameters), it is the answer that the name names nothing; anywhere else it is a request of ours malformed,
        raised as VenueRefused and never kept."""
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
                    if e.code == 400 and not names:
                        raise VenueRefused(f"Massive refused our request to {url.split('?')[0].replace(self.base, '')}: "
                                           f"{parsed.get('error') or parsed.get('message')}") from None
                    status = {400: "INVALID", 404: "NOT_FOUND"}.get(e.code, "NOT_AUTHORIZED")
                    return e.code, {"status": status, "http_status": e.code,
                                    "error": parsed.get("error") or parsed.get("message")}
                raise MassiveError(f"{url.split('?')[0]} answered {e.code}") from None
            except TRANSIENT as e:
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
        _, body = self.get(url, names=True)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (date, date)}

    def listings(self, date: str) -> dict:
        """/v3/reference/tickers: every US stock listing active on `date`, every page."""
        url = self.url("/v3/reference/tickers", {"market": "stocks", "active": "true", "date": date, "limit": 1000})
        retrieved = _now()
        body, urls = self.pages(url)
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (date, date)}

    def us_composites(self, date: str) -> dict:
        """The listings active on `date`, as the store keeps them: what the look-through reads of each, its ticker,
        its composite FIGI and its locale, by which a fund's holding is known to be a US listing (MATH.md 3.1). The
        whole list, some 3.9 MB, is read whole; the slice kept is some 0.7 MB."""
        got = self.listings(date)
        body = got["payload"]
        if answered(body):
            body = {**{k: v for k, v in body.items() if k != "results"},
                    "results": [{k: r.get(k) for k in ("ticker", "composite_figi", "locale")}
                                for r in body.get("results") or [] if isinstance(r, dict)]}
        return {**got, "payload": body}

    def company_listings(self, cik: str, date: str) -> dict:
        """/v3/reference/tickers by CIK: the listings of the company filing under it, active on `date`."""
        url = self.url("/v3/reference/tickers", {"cik": cik, "date": date, "active": "true", "limit": 1000})
        retrieved = _now()
        body, urls = self.pages(url)
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (date, date)}

    def _bars(self, ticker: str, start: str, end: str, timespan: str = "day") -> tuple[dict, list[str]]:
        url = self.url(f"/v2/aggs/ticker/{urllib.parse.quote(ticker, safe=':.-')}/range/1/{timespan}/{start}/{end}",
                       {"adjusted": "true", "sort": "asc", "limit": 50000})
        return self.pages(url)

    def _dividends(self, ticker: str, start: str, end: str) -> tuple[dict, list[str]]:
        url = self.url("/stocks/v1/dividends",
                       {"ticker": ticker, "ex_dividend_date.gte": start, "ex_dividend_date.lte": end,
                        "sort": "ex_dividend_date.asc", "limit": 5000})
        return self.pages(url)

    def tenure(self, ticker: str, start: str, end: str) -> list[dict] | None:
        """The tickers the company listed under `ticker` on `end` traded under over [start, end], oldest first, each
        with the first and last day it did, where a change to `ticker` falls inside the window; None where none does,
        or Massive gives no events for it. Only the changes made by `end` count: a change after it is no part of the
        window, and the security under `ticker` then is the one its own rows over the window are."""
        if ":" in ticker:
            return None
        key = (ticker, Date.today().isoformat())  # read once a day, whatever it answered
        if key not in self._events:
            _, body = self.get(self.url(f"/vX/reference/tickers/{urllib.parse.quote(ticker, safe='.-')}/events",
                                        {"types": "ticker_change"}))
            found = (body.get("results") or {}) if answered(body) and isinstance(body.get("results"), dict) else {}
            self._events[key] = sorted(((e["date"], e["ticker_change"]["ticker"]) for e in found.get("events") or []
                                           if isinstance(e, dict) and e.get("date") and
                                           (e.get("ticker_change") or {}).get("ticker")))
        changes = [c for c in self._events[key] if c[0] <= end]
        if not changes or changes[-1][1] != ticker or changes[-1][0] <= start:
            return None
        out = []
        if changes[0][0] > start:  # the days before the first event, under its first ticker, if the company's (_joined)
            out.append({"ticker": changes[0][1], "from": start,
                        "to": (Date.fromisoformat(changes[0][0]) - timedelta(days=1)).isoformat(), "before": True})
        for i, (since, held) in enumerate(changes):
            until = (Date.fromisoformat(changes[i + 1][0]) - timedelta(days=1)).isoformat() \
                if i + 1 < len(changes) else end
            first, last = max(since, start), min(until, end)
            if first <= last:
                out.append({"ticker": held, "from": first, "to": last})
        return out

    def _identity(self, ticker: str, day: str) -> tuple[str, str, str]:
        """What a ticker's reference on a day names its listing by: its company's CIK, its composite FIGI, and its
        listing date, each empty where it names none."""
        _, body = self.get(self.url(f"/v3/reference/tickers/{urllib.parse.quote(ticker, safe='.-')}", {"date": day}))
        found = (body.get("results") or {}) if answered(body) else {}
        return (str(found.get("cik") or "").lstrip("0"), str(found.get("composite_figi") or ""),
                str(found.get("list_date") or ""))

    def _same_company(self, ticker: str, then: str, now: str, *, earlier: str | None = None) -> bool:
        """Whether the listing under `earlier` (by default `ticker`) on `then` was the company listed under `ticker` on
        `now`: one CIK in both references; where the earlier names none, one listing, by its composite FIGI or else
        its listing date."""
        before, after = self._identity(earlier or ticker, then), self._identity(ticker, now)
        if before[0]:
            return before[0] == after[0]
        return bool(before[1]) and before[1] == after[1] if before[1] and after[1] else \
            bool(before[2]) and before[2] == after[2]

    def _later_splits(self, tenure: list[dict]) -> list[float]:
        """For each ticker of a tenure, what puts its amounts on today's share basis beyond its own splits: the splits
        executed under each ticker after it, from its first day to now (its last ticker's) or to its last day."""
        factors = []
        for i in range(len(tenure)):
            factor = 1.0
            for j, later in enumerate(tenure[i + 1:], start=i + 1):
                body, _ = self.pages(self.url("/stocks/v1/splits", {"ticker": later["ticker"],
                                                                   "execution_date.gte": later["from"],
                                                                   "sort": "execution_date.asc", "limit": 5000}))
                for split in body.get("results") or [] if answered(body) else []:
                    within = j == len(tenure) - 1 or split.get("execution_date", "") <= later["to"]
                    if within and split.get("split_from") and split.get("split_to"):
                        factor *= float(split["split_from"]) / float(split["split_to"])
            factors.append(factor)
        return factors

    def _joined(self, read: Callable, ticker: str, start: str, end: str, day_of: Callable[[dict], str | None],
                adjust: Callable[[dict, float], dict]) -> tuple[dict, list[str]]:
        """A stock's rows over a window its ticker changed in: each ticker's over the days it traded under it, put on
        today's share basis, in date order (`tenure`)."""
        tenure = self.tenure(ticker, start, end)
        if tenure is None:
            return read(ticker, start, end)
        if tenure[0].get("before"):
            first = tenure.pop(0)
            if self._same_company(ticker, first["to"], end, earlier=first["ticker"]):
                if tenure and tenure[0]["ticker"] == first["ticker"]:
                    tenure[0]["from"] = first["from"]  # one ticker throughout: its own earlier rows stand
                else:
                    tenure.insert(0, {"ticker": first["ticker"], "from": first["from"], "to": first["to"]})
            if len(tenure) == 1 and tenure[0]["ticker"] == ticker and tenure[0]["from"] == start:
                return read(ticker, start, end)
        factors = self._later_splits(tenure)
        body, urls, rows = None, [], []
        for held, factor in zip(tenure, factors):
            found, asked = read(held["ticker"], held["from"], held["to"])
            urls += asked
            if held["ticker"] != ticker and not (answered(found) and found.get("results")) and \
                    self._same_company(ticker, held["to"], tenure[-1]["to"]):
                found, asked = read(ticker, held["from"], held["to"])  # the company's earlier security under it
                urls += asked
                held["read"] = ticker
            if held["ticker"] == ticker or body is None:
                body = {k: v for k, v in found.items() if k != "results"}
            rows += [adjust(r, factor) if factor != 1.0 else r for r in found.get("results") or [] if isinstance(r, dict)
                     and held["from"] <= (day_of(r) or "") <= held["to"]] if answered(found) else []
        return {**(body or {}), "status": "OK", "ticker": ticker, "tenure": tenure, "results": rows}, urls

    def cusip_listing(self, subject: str) -> dict:
        """/v3/reference/tickers by CUSIP as of a date (`{CUSIP}@{date}`): the listing that carried the security
        then, its ticker, CIK and FIGIs as they were (the CUSIP itself is never answered back)."""
        cusip, _, day = subject.partition("@")
        url = self.url("/v3/reference/tickers", {"cusip": cusip, "date": day})
        retrieved = _now()
        _, body = self.get(url)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (day, day)}

    def events(self, ticker: str, day: str) -> dict:
        """/vX/reference/tickers/{t}/events: the tickers the security now listed under `t` has traded under, each
        from its date."""
        url = self.url(f"/vX/reference/tickers/{urllib.parse.quote(ticker, safe='.-')}/events", {"types": "ticker_change"})
        retrieved = _now()
        _, body = self.get(url)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (day, day)}

    def bars(self, ticker: str, start: str, end: str, *, timespan: str = "day") -> dict:
        """/v2/aggs/ticker/{t}/range/1/{timespan}/{start}/{end}, split-adjusted, every page: a stock's daily bars its
        company's over a window its ticker changed in (`tenure`)."""
        retrieved = _now()
        if timespan == "day":
            body, urls = self._joined(self._bars, ticker, start, end, lambda b: _session(b.get("t")),
                                      lambda b, f: {**b, **{k: b[k] * f for k in ("o", "h", "l", "c", "vw")
                                                            if isinstance(b.get(k), (int, float))}})
        else:
            body, urls = self._bars(ticker, start, end, timespan)
        if timespan == "day":
            body = {**body, "parse": JOINED}
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (start, end)}

    def dividends(self, ticker: str, start: str, end: str) -> dict:
        """/stocks/v1/dividends: the ticker's cash dividends whose ex-dividend date falls in [start, end], every page:
        its company's over a window its ticker changed in (`tenure`)."""
        retrieved = _now()
        body, urls = self._joined(self._dividends, ticker, start, end, lambda r: r.get("ex_dividend_date"),
                                  lambda r, f: {**r, **({"split_adjusted_cash_amount": r["split_adjusted_cash_amount"] * f}
                                                        if isinstance(r.get("split_adjusted_cash_amount"), (int, float))
                                                        else {})})
        return {"payload": {**body, "parse": JOINED}, "request": self._record(urls), "retrieved_at": retrieved,
                "range": (start, end)}

    def splits(self, ticker: str, start: str, end: str) -> dict:
        """/stocks/v1/splits: the ticker's splits executed from `start` to now, every page; `end` is the as-of the
        range is recorded against, while the bars a split adjusts are adjusted for every split to the day they are
        read."""
        url = self.url("/stocks/v1/splits", {"ticker": ticker, "execution_date.gte": start,
                                             "sort": "execution_date.asc", "limit": 5000})
        retrieved = _now()
        body, urls = self.pages(url)
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (start, end)}

    def constituents(self, fund: str, asof: str) -> dict:
        """A fund's constituents at the latest effective_date on or before `asof`, in their latest processing made by
        `asof` (MATH.md 3.1), in two requests: the latest effective_date among the most recently processed rows on or
        before `asof` is the date, and then every row of that date's latest processing. ETF Global processes one
        effective date's holdings again each day until the next, each processing the whole basket, and one processing
        can carry more than one effective date; a processing made after the as-of is what it published later.
        Read live, a past date's processing is of that date itself (DIVZ and QQQ on 2021-12-15), and a fund's rows
        can carry an effective date that is no date of its holdings (DIVZ's 0015-08-19 in its 2026-09 processings),
        which the latest date read passes over."""
        retrieved = _now()
        find = self.url("/etf-global/v1/constituents",
                        {"composite_ticker": fund, "effective_date.lte": asof, "processed_date.lte": asof,
                         "sort": "processed_date.desc", "limit": PAGE})
        status, body = self.get(find)
        if status != 200:
            return {"payload": body, "request": self._record([find]), "retrieved_at": retrieved,
                    "range": (asof, asof)}
        dates = [r.get("effective_date") for r in body.get("results") or [] if r.get("effective_date")]
        if not dates:
            payload = {"status": "OK", "composite_ticker": fund, "effective_date": None, "results": [], "parse": HELD}
            return {"payload": payload, "request": self._record([find]), "retrieved_at": retrieved,
                    "range": (asof, asof)}
        effective = max(dates)
        processed = max((r["processed_date"] for r in body.get("results") or []
                         if r.get("effective_date") == effective and r.get("processed_date")), default=None)
        url = self.url("/etf-global/v1/constituents",
                       {"composite_ticker": fund, "effective_date": effective,
                        **({"processed_date": processed} if processed else {}), "limit": PAGE})
        body, urls = self.pages(url)
        body.update({"composite_ticker": fund, "effective_date": effective, "processed_date": processed, "parse": HELD})
        return {"payload": body, "request": self._record([find] + urls), "retrieved_at": retrieved,
                "range": (asof, asof)}

    def processed(self, asof: str) -> dict:
        """ETF Global's latest processing of any fund's holdings made by `asof`: one row of the whole holdings table,
        newest processing first (`sort=processed_date.desc`, `processed_date.lte`, `limit=1`; read live: 2026-11-10 by
        2026-11-12, in 0.25 s). ETF Global processes every fund it covers each day, so a fund whose own latest
        processing is older than this by more than a session is one it no longer reads (MATH.md 3.1)."""
        retrieved = _now()
        url = self.url("/etf-global/v1/constituents", {"processed_date.lte": asof, "sort": "processed_date.desc",
                                                         "limit": 1, "fields": "processed_date"})
        status, body = self.get(url)
        if status != 200:
            raise VenueError(f"ETF Global's latest processing could not be read: {status}")
        found = (body.get("results") or [{}])[0].get("processed_date")
        return {"payload": {"status": "OK" if found else "NOT_FOUND", "source": "ETF Global", "processed_date": found},
                "request": self._record([url]), "retrieved_at": retrieved, "range": (asof, asof)}

    def figi_isin(self, figi: str, asof: str) -> dict:
        """The ISIN of the share class a listing's FIGI names, as ETF Global gives it beside the FIGI in a fund's
        holdings (https://massive.com/docs/rest/partners/etf-global/constituents: `figi` and `fields` filter it; read
        live, 2026-11-12: Tencent's Hong Kong composite BBG000BJ35F4 in 316 rows, each with KYG875721634): one row of any
        fund's holdings that names it. A listing abroad named by its symbol is known to a fund's report, which names a
        holding by its ISIN alone, through it. NOT_FOUND where no fund ETF Global reads holds it. Asked of the week of
        processing to the as-of, every fund it still reads being processed each day: asked of the whole table, a FIGI no
        fund holds was answered 504 after 15 s (read live), and within the week in 0.4 s."""
        retrieved = _now()
        week = (Date.fromisoformat(asof) - timedelta(days=7)).isoformat()
        url = self.url("/etf-global/v1/constituents", {"figi": figi, "processed_date.gte": week,
                                                         "processed_date.lte": asof, "limit": 1, "fields": "isin,figi"})
        status, body = self.get(url)
        if status != 200:
            raise VenueError(f"ETF Global's holdings naming {figi} could not be read: {status}")
        found = next((r.get("isin") for r in body.get("results") or [] if r.get("isin")), None)
        return {"payload": {"status": "OK" if found else "NOT_FOUND", "figi": figi, "isin": found},
                "request": self._record([url]), "retrieved_at": retrieved, "range": (asof, asof)}

    def isin_listings(self, isins: list[str], asof: str) -> dict[str, dict]:
        """Where each ISIN's share class trades, as the funds ETF Global reads name it in their holdings: each local
        ticker and exchange's country a fund's row gives it beside the ISIN, with how many rows give each, in the
        latest processing by the as-of (https://massive.com/docs/rest/partners/etf-global/constituents: `isin.any_of`,
        a comma-separated list, and `fields`; read live: a hundred ISINs of a total-international fund's
        report in 5,833 rows over two pages, 0.8 s, every one named, as `('700', 'HK')` and `('7203', 'JP')`). A fund's
        report names a holding by its ISIN alone; this is how its listing, and the classification its listing carries,
        are read for thousands of rows at once (harness.derive.owned). A hundred ISINs to a request, each request
        paged whole. NOT_FOUND for an ISIN no fund ETF Global reads holds."""
        latest = self.processed(asof)["payload"].get("processed_date")
        retrieved = _now()
        found: dict[str, dict[tuple[str, str], int]] = {i: {} for i in isins}
        urls = []

        def chunk(part: list[str]) -> list[dict]:  # a hundred ISINs, paged whole
            url, rows = self.url("/etf-global/v1/constituents", {"isin.any_of": ",".join(part),
                                                                   "processed_date": latest, "limit": 5000,
                                                                   "fields": "isin,constituent_ticker,country_of_exchange"}), []
            urls.append(url)
            while url:
                status, body = self.get(url)
                if status != 200:
                    raise VenueError(f"ETF Global's holdings naming {len(part)} ISINs could not be read: {status}")
                rows += body.get("results") or []
                url = body.get("next_url")
            return rows

        with ThreadPoolExecutor(max_workers=4) as pool:  # side by side: some 50 requests for a broad fund's report
            for rows in pool.map(chunk, [isins[k:k + 100] for k in range(0, len(isins), 100)]):
                for r in rows:
                    if r.get("isin") in found and r.get("constituent_ticker"):
                        key = (str(r["constituent_ticker"]), str(r.get("country_of_exchange") or ""))
                        found[r["isin"]][key] = found[r["isin"]].get(key, 0) + 1
        return {isin: {"payload": {"status": "OK" if pairs else "NOT_FOUND", "isin": isin, "processed_date": latest,
                                   "listings": [{"ticker": t, "country": c or None, "rows": n}
                                                for (t, c), n in sorted(pairs.items(), key=lambda kv: -kv[1])]},
                       "request": self._record(urls[:1]), "retrieved_at": retrieved, "range": (asof, asof)}
                for isin, pairs in found.items()}

    def holdings_history(self, fund: str, start: str, asof: str) -> dict:
        """A fund's holdings on each day from `start` to `asof` (MATH.md 1.7: what the contracts it held on each day did):
        for each effective_date in the window, its rows in their latest processing made by `asof`, the rule a day's
        holdings take (constituents), a row reported twice in that processing counted once. Kept for each day: its net
        assets, the sum of its rows' market values; and the rows its source labels as options or labels as nothing,
        each with what names its contract and what the fund holds of it (HISTORY_FIELDS), from which
        harness.derive.rows reads the contracts. The rest of a day's rows are in its net assets alone, which keeps two
        years of a fund of a hundred shares and a written call to the call's rows. One paged request; a day with no
        rows is not in the answer."""
        retrieved = _now()
        url = self.url("/etf-global/v1/constituents",
                       {"composite_ticker": fund, "effective_date.gte": start, "effective_date.lte": asof,
                        "processed_date.lte": asof, "sort": "processed_date.asc", "limit": PAGE})
        body, urls = self.pages(url)
        request, covered = self._record(urls), (start, asof)
        if not answered(body):
            return {"payload": body, "request": request, "retrieved_at": retrieved, "range": covered}
        processings: dict[str, dict[str, list[dict]]] = {}
        for r in body.get("results") or []:
            day = r.get("effective_date") if isinstance(r, dict) else None
            if day and start <= day <= asof:
                processings.setdefault(day, {}).setdefault(r.get("processed_date") or "", []).append(r)
        days = {}
        for day in sorted(processings):
            processed = max(processings[day])
            seen, rows = set(), []
            for r in processings[day][processed]:
                key = json.dumps(r, sort_keys=True, default=str)
                if key not in seen:  # one row reported twice
                    seen.add(key)
                    rows.append(r)
            labelled = lambda r: str(r.get("asset_class") or "").strip().casefold()  # noqa: E731
            days[day] = {"processed_date": processed or None,
                         "net_assets": sum(float(r.get("market_value") or 0.0) for r in rows),
                         "rows": [{k: r.get(k) for k in HISTORY_FIELDS} for r in rows
                                  if not labelled(r) or labelled(r) in OPTION_LABELS]}
        payload = {"status": "OK", "composite_ticker": fund, "start": start, "asof": asof, "days": days,
                   "parse": HISTORY}
        return {"payload": payload, "request": request, "retrieved_at": retrieved, "range": covered}

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
        status, body = self.get(url, names=True)
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

    def options(self, symbols: list[str], date: str) -> dict[str, dict]:
        """Each listed option's snapshot now, by its symbol (`O:` and the OCC's), up to 250 to a request: the unified
        snapshot, `GET /v3/snapshot?ticker.any_of=...`
        (https://massive.com/docs/rest/options/snapshots/unified-snapshot.md). Each result, in the order asked, is `{ticker, greeks: {delta, ...}, implied_volatility, details: {contract_type,
        exercise_style, expiration_date, shares_per_contract, strike_price}, underlying_asset: {ticker, value (an index)
        or price (a share)}, last_quote: {bid, ask, midpoint, ...}, ...}`, or `{ticker, error: "NOT_FOUND", message}`
        for a contract no market lists (read live: a FLEX contract, `O:4SPY261231C00001910`; an expired one). `greeks`
        are absent "in certain circumstances, such as options contracts that are deep in the money" (read live:
        `O:MSTR261113C00040000`, a 40 call with MSTR at 121.56, quoted 80.60 bid and 81.75 ask, its intrinsic value of
        81.56 between them), while its `last_quote` stands; an index option's `last_quote` reads 0 bid and 0 ask once
        its market has closed. An index's `underlying_asset.ticker` can carry its feed after the symbol (read live:
        XBCI's calls on the Cboe Bitcoin U.S. ETF Index, `I:CBTX:CGI`), a ticker Massive's reference does not know and
        its aggregates refuse as "incorrectly formatted", where both name the index `I:CBTX` (its reference's
        `source_feed` "CboeGlobalIndicesCGI"): it is read as that ticker, the feed kept as `feed`. It is read on the
        as-of date, the delta its market quotes then."""
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 250):
            chunk = symbols[i:i + 250]
            url = self.url("/v3/snapshot", {"ticker.any_of": ",".join(chunk), "limit": 250})
            retrieved = _now()
            status, body = self.get(url)
            if status != 200 or not answered(body):
                raise MassiveError(f"Massive gave no option snapshots: {body.get('status')}")
            found = {r.get("ticker"): r for r in body.get("results") or [] if isinstance(r, dict)}
            for symbol in chunk:
                result = found.get(symbol) or {"ticker": symbol, "error": "NOT_FOUND"}
                given = (result.get("underlying_asset") or {}).get("ticker")
                fed = FEED_SUFFIXED.fullmatch(str(given or ""))
                if fed:  # the index as Massive's reference and its values name it, the feed kept beside it
                    result = {**result, "underlying_asset": {**result["underlying_asset"], "ticker": fed.group(1),
                                                             "feed": fed.group(2)}}
                out[symbol] = {"payload": {"status": "OK" if not result.get("error") else result["error"], **result,
                                           "parse": NAMED_AS_REFERENCED},
                               "request": self._record([url]), "retrieved_at": retrieved, "range": (date, date)}
        return out

    def option_at(self, subject: str) -> dict:
        """An option's price standing at a moment (`{O:ticker}@{ISO moment}`): the NBBO in force then, from
        `/v3/quotes/{O:ticker}?timestamp.gte={its day's start}&timestamp.lte={ns}&order=desc&sort=timestamp&limit=1`
        (https://massive.com/docs/rest/options/trades-quotes/quotes.md), `{results: [{bid_price, ask_price,
        sip_timestamp}]}`; and its last trade that day at or before it, from `/v3/trades/{O:ticker}` with the same
        parameters (https://massive.com/docs/rest/options/trades-quotes/trades.md), `{results: [{price,
        sip_timestamp}]}`. Read live: an S&P 500 call's NBBO at 16:00 New York on 2025-12-10 was 15:59:50's, and its
        quotes went on to 17:00. None of either that day is the answer that none stood."""
        symbol, _, moment = subject.partition("@")
        at = datetime.fromisoformat(moment)
        day = at.astimezone(NY).date()
        start = datetime(day.year, day.month, day.day, tzinfo=NY)
        params = {"timestamp.gte": int(start.timestamp() * 1e9), "timestamp.lte": int(at.timestamp() * 1e9),
                  "order": "desc", "sort": "timestamp", "limit": 1}
        retrieved = _now()
        path = urllib.parse.quote(symbol, safe=":.-")
        found = {}
        for what in ("quotes", "trades"):
            url = self.url(f"/v3/{what}/{path}", params)
            status, body = self.get(url)
            if status != 200 or not answered(body):
                raise MassiveError(f"Massive gave no {what} for {symbol}: {body.get('status')}")
            found[what] = ((body.get("results") or [None])[0] or {}), url
        quote, trade = found["quotes"][0], found["trades"][0]
        payload = {"status": "OK", "ticker": symbol, "at": moment, "bid": quote.get("bid_price"),
                   "ask": quote.get("ask_price"), "quoted": _nanos_iso(quote.get("sip_timestamp")),
                   "trade": trade.get("price"), "traded": _nanos_iso(trade.get("sip_timestamp"))}
        return {"payload": payload, "request": {"method": "GET", "url": found["quotes"][1], "trades": found["trades"][1]},
                "retrieved_at": retrieved, "range": (day.isoformat(), day.isoformat())}

    def option_contract(self, subject: str) -> dict:
        """An option contract's terms as of a date (`{O:ticker}@{date}`), from
        `/v3/reference/options/contracts/{O:ticker}?as_of={date}`
        (https://massive.com/docs/rest/options/contracts/contract-overview.md): `{results: {exercise_style,
        shares_per_contract, underlying_ticker, contract_type, strike_price, expiration_date}}`. Read live, an expired
        contract's terms are kept as they were (O:SPY251219C00060000 as of 2025-12-10: american, 100, SPY), and an index
        option's underlying is named by the index's own ticker (SPX), not by the symbol its values are read under."""
        symbol, _, day = subject.partition("@")
        url = self.url(f"/v3/reference/options/contracts/{urllib.parse.quote(symbol, safe=':.-')}", {"as_of": day})
        retrieved = _now()
        _, body = self.get(url, names=True)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (day, day)}

    def option_listings(self, subject: str) -> dict:
        """The listed contracts a contract's stated terms name on a date (`{underlying}|{expiry}|{call or put}|{strike}@
        {date}`), from `/v3/reference/options/contracts?underlying_ticker=&expiration_date=&contract_type=&strike_price=
        &as_of=` (https://massive.com/docs/rest/options/contracts/all-contracts.md): `{results: [{ticker, exercise_style,
        shares_per_contract, underlying_ticker}]}`. Read live: a Form N-PORT report's SPX call, 7,625 to 2026-08-03, as
        of 2026-07-31 is O:SPXW260803C07625000, european, 100; its Amazon call, 245 to 2026-07-17, as of 2026-06-30, is
        O:AMZN260717C00245000, american, 100. An index option's underlying is named by the index's own ticker (SPX).
        None named is the answer that no listed contract had these terms then."""
        terms, _, day = subject.partition("@")
        underlying, expiry, right, strike = terms.split("|")
        url = self.url("/v3/reference/options/contracts",
                       {"underlying_ticker": underlying, "expiration_date": expiry, "contract_type": right,
                        "strike_price": strike, "as_of": day, "limit": 100})
        retrieved = _now()
        _, body = self.get(url, names=True)
        return {"payload": body, "request": self._record([url]), "retrieved_at": retrieved, "range": (day, day)}

    def minutes(self, subject: str) -> dict:
        """A listing's or an index's minute bars on a session (`{ticker}@{date}`), as traded: the price it stood at
        at any moment that day, each bar's close the last price of the minute it starts
        (https://massive.com/docs/rest/stocks/aggregates/custom-bars.md,
        https://massive.com/docs/rest/indices/aggregates/custom-bars.md), `{results: [{t, c}]}`, `t` the minute's
        start in Unix milliseconds."""
        ticker, _, day = subject.partition("@")
        url = self.url(f"/v2/aggs/ticker/{urllib.parse.quote(ticker, safe=':.-')}/range/1/minute/{day}/{day}",
                       {"adjusted": "false", "sort": "asc", "limit": 50000})
        retrieved = _now()
        body, urls = self.pages(url)
        if answered(body):
            body = {**body, "results": [{"t": r["t"], "c": r["c"]} for r in body.get("results") or []
                                        if isinstance(r, dict) and r.get("t") and isinstance(r.get("c"), (int, float))]}
        return {"payload": body, "request": self._record(urls), "retrieved_at": retrieved, "range": (day, day)}

    # ---------------------------------------------------------------- the store's fetchers

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        """The data kinds this client fetches, each a function of (subject, covering)."""
        return {
            "ticker_ref": lambda subject, covering: self.ticker(subject, covering[1]),
            "ticker_on": lambda subject, covering: self.ticker(*subject.split("@", 1)),
            "cusip_listing": lambda subject, covering: self.cusip_listing(subject),
            "ticker_events": lambda subject, covering: self.events(subject, covering[1]),
            "listings": lambda subject, covering: self.us_composites(covering[1]),
            "company_listings": lambda subject, covering: self.company_listings(subject, covering[1]),
            "bars": _parsed(lambda subject, covering: self.bars(subject, covering[0], covering[1]), JOINED),
            "dividends": _parsed(lambda subject, covering: self.dividends(subject, covering[0], covering[1]), JOINED),
            "splits": lambda subject, covering: self.splits(subject, covering[0], covering[1]),
            "constituents": _parsed(lambda subject, covering: self.constituents(subject, covering[1]), HELD),
            "holdings_history": _parsed(lambda subject, covering: self.holdings_history(subject, *covering), HISTORY),
            "holdings_processed": lambda subject, covering: self.processed(covering[1]),
            "figi_isin": lambda subject, covering: self.figi_isin(subject, covering[1]),
            "isin_listings": self._isin_listings_fetcher(),
            "snapshot": lambda subject, covering: self.snapshot(subject, covering[1]),
            "news": lambda subject, covering: self.news(subject, covering[0], covering[1]),
            "company": lambda subject, covering: self.company(subject, covering[1]),
            "option": _parsed(self._option_fetcher(), NAMED_AS_REFERENCED),
            "option_at": lambda subject, covering: self.option_at(subject),
            "option_contract": lambda subject, covering: self.option_contract(subject),
            "minutes": lambda subject, covering: self.minutes(subject),
            "option_listings": lambda subject, covering: self.option_listings(subject),
        }

    def _isin_listings_fetcher(self) -> Callable[[str, Any], dict]:
        """`isin_listings` by `{ISIN}@{YYYY-MM}`, a month's reading, as a listing's symbol is kept (harness.venues.yahoo);
        `many` reads every subject asked at once."""
        def many(subjects: Any, covering: Any) -> dict[str, dict]:
            isins = {str(s).rsplit("@", 1)[0]: s for s in subjects}
            got = self.isin_listings(sorted(isins), covering[1])
            return {isins[i]: g for i, g in got.items()}

        def one(subject: str, covering: Any) -> dict:
            return many([subject], covering)[subject]

        one.many = many  # type: ignore[attr-defined]
        return one

    def _option_fetcher(self) -> Callable[[str, Any], dict]:
        def one(subject: str, covering: Any) -> dict:
            return self.options([subject], covering[1])[subject]

        one.many = lambda subjects, covering: self.options(list(subjects), covering[1])  # type: ignore[attr-defined]
        return one
