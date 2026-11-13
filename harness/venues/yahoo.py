"""Yahoo Finance, public and with no key: a listing abroad's daily prices on its home market, in its own currency,
each close with the moment its exchange struck it, with its dividends and splits, and its latest mark; and a currency
pair's hourly rates and its latest rate (MATH.md 3.1, 3.2). It is one client, and what the store keeps from it is in a
shape of its own, not Yahoo's, so a licensed feed can replace it by giving the same shapes.

What a listing abroad is to the store: `{ISIN}:{country}`, the share class the fund holds, by its ISIN, on the
exchange of the country the fund's holdings name. A currency pair is `C:{BASE}{QUOTE}`, the quote currency's price of
one unit of the base, as `C:EURUSD` is the dollars one euro buys.

The endpoints, undocumented by Yahoo and the ones its own site reads (the yfinance project's reading of them,
https://ranaroussi.github.io/yfinance/, says the data is for personal use); each shape below was checked against
live answers:
- `GET https://query1.finance.yahoo.com/v1/finance/search?q={ISIN}&quotesCount=&newsCount=0` answers `{quotes:
  [{symbol, exchange, exchDisp, quoteType, ...}]}`, the listings of the security the ISIN names, each with its
  exchange as Yahoo codes it (`KSC` the Korea Exchange, `JPX` Tokyo, `LSE` London, `NYQ` the New York Stock
  Exchange), the code a chart's `meta.exchangeName` gives too. EXCHANGES puts each code Yahoo was read giving, live, in
  the ISO 10383 market identifier (MIC) of the exchange and the country it is in: the exchange is named by that data,
  and a licensed feed that replaces this client brings its own table. The listing kept is the one on an exchange of
  the fund's country, its primary market first where the country has several (the table's order); where Yahoo lists
  the security on none there (a fund can hold a company's line on another market, as German exchanges list many),
  its listing in the country its ISIN names, the same share class on its own market; and where it lists the security
  in neither, the same share class on any exchange the table holds (read live: STMicroelectronics' Milan line, which a
  fund holds, is found only in Paris; Nordea's Helsinki line only in Stockholm). Yahoo lists no exchange in the
  Philippines or Abu Dhabi (searched by name and ISIN), so neither has a code. The search answered an ISIN it finds
  with no quotes at all, read live under load (Porsche SE's, found on the next read), so a search that finds nothing
  is asked once more before the listing is none. A dealer's quote of the share on another market (DEALERS) is not
  taken for it.
- `GET /v8/finance/chart/{symbol}?interval=1d&period1=&period2=&events=div,splits` answers `{chart: {result:
  [{meta, timestamp, indicators: {quote: [{open, high, low, close, volume}], adjclose}, events: {dividends,
  splits}}]}}`. `meta` holds `currency` (a minor unit for some markets: `GBp` pence, `ZAc` cents, `ILA` agorot, `KWF`
  fils), `exchangeName`, `exchangeTimezoneName`, `currentTradingPeriod.regular` `{start, end}` (today's session, Unix
  seconds), `regularMarketPrice` and `regularMarketTime`, its latest price and when it was struck. A daily bar's
  `timestamp` is its session's opening, its `close` is adjusted for splits and not for dividends (NVDA's closes
  before its 10-for-1 split of 2024-06-10 read near $120), and a session in progress has a bar whose close is its
  latest price. `events.dividends` gives each `{date, amount}`, `date` the ex-date's session opening and `amount` per
  share on the same split-adjusted basis (NVDA's $0.04 of March 2024 reads 0.004), in the listing's currency;
  `events.splits` gives each `{date, numerator, denominator}`, numerator new shares for denominator old.
- The same endpoint with `interval=60m&range=730d` answers hourly bars and `meta.tradingPeriods`, one list for each
  session, `[{start, end}]` in Unix seconds: the exchange's own sessions, its shortened ones as they were, over
  roughly the last three years. An explicit `period1` older than 730 days is refused (422, "The requested range must
  be within the last 730 days"), and `range=730d` reaches further back, so the hourly reads ask by range and keep
  their window. For a currency pair, `{BASE}{QUOTE}=X` is the symbol (`EURUSD=X`, `KRWUSD=X`, `USDJPY=X`), each bar's
  `timestamp` the start of its hour, `open` the rate at that moment; a bar can hold no rate.

What is kept:
- `home`: `{status: "OK", listing, symbol, exchange, mic, timezone, currency, results: [{session, moment, c}],
  dividends: [{ex_date, amount}], splits: [{date, from, to}]}`, `mic` the exchange's (EXCHANGES), every price and
  amount in the currency's own unit (a minor unit divided out), each close of a session complete when read:
  `session` its date on its exchange, `moment` when its price was struck, the later of the exchange's official close
  for that session (harness.sessions, its calendar) and the end of Yahoo's own record of the session: Yahoo's Korea
  Exchange sessions end at 15:00, while its daily close is the 15:30 closing auction's, the calendar's close; its Hong
  Kong sessions end at 16:10, after the closing auction session the calendar's 16:00 ends before. Where the exchange's
  own timetable states its close (harness.sessions.stated), that close alone: Yahoo's sessions are a fixed schedule
  (Santiago's end at 17:00 all year, where its exchange closes at 16:00). Where neither is known, the opening plus the
  regular session's length. `parse`, the form the moments are in (PARSE), so a close kept with a moment of another
  form is read again, not served. `NOT_FOUND` where Yahoo lists the ISIN on no exchange but a
  dealer's.
- `home_mark`: `{status: "OK", listing, symbol, currency, price, time}`, its latest price and when it was struck.
- `hourly` for a currency pair: `{status: "OK", ticker, symbol, base, quote, results: [{t, o, h, l, c}], first}`,
  `first` the start of Yahoo's first hour as read (Unix milliseconds), how far back its hours reached then; `base`
  and `quote` its currencies as Yahoo names them (`shortName` "KRW/USD"), `t` the hour's start in Unix milliseconds,
  `o` the rate at that moment and `c` the last within the hour: the shape a crypto pair's hourly prices are kept in,
  so its price at each session's close in New York reads as theirs does (harness.derive.series.session_closes), and
  its rate at any moment as the latest at or before it (harness.derive.series.rate_at). A currency's dollar rate is
  `C:{CCY}USD`'s (harness.derive.series.dollar_pair): `{CCY}USD=X` is served for every currency tried, 38 of them,
  each with some 2.8 years of hours.
- `last_trade` for a currency pair: `{status: "OK", ticker, symbol, base, quote, price, time}`, its latest rate, read
  as a crypto pair's last trade is (harness.derive.series.trade_mark).

What a company is, on any market (MATH.md 3.1, its industry and its home), and its results and analysts' ratings (TOOLS.md 5,
a company's record), from the surfaces the site's own pages read, each needing the site's cookie and its crumb: a
cookie from `GET https://fc.yahoo.com`, then `GET /v1/test/getcrumb`, whose body is the crumb each request carries as
`crumb=` (read live; a request without it is answered 401, "Invalid Crumb"). Its edge gives the crumb only to a reader
named as a browser is (`SITE_AGENT`): named as this client, or as httpx, the crumb was answered 429 on every read, and as
"Mozilla/5.0" 200, so these surfaces are read by a client of their own, with its own cookie. The search by ISIN is
read by it too: after some thousands of searches in a day, the search answered this client's own name 429 on every
read (830 of 830) while it answered the browser's name 200; and on 2026-11-12 the chart answered this client's name 429
on every read and the browser's 200, so the chart is read under the browser's name too, by a client of its own, paced
and cooled apart from the site's. Each shape was checked against live answers on 2026-11-11:
- `POST /v1/finance/screener?crumb=&formatted=false&useRecordsResponse=true` with `{size, offset, sortField,
  sortType, quoteType: "EQUITY", includeFields, topOperator: "AND", query: {operator: "or", operands: [{operator:
  "eq", operands: ["ticker", <symbol>]}, ...]}}` answers `{finance: {result: [{total, records: [{ticker, companyName,
  sector, industry, exchange}]}]}}`: each listing it holds among the symbols named, on any market, with its sector and
  its industry in Yahoo's one classification for every market. A query names at most SCREEN_TICKERS symbols, so its
  answer is one page of SCREEN_PAGE (read live: 250 answered in a fraction of a second, 500 were refused
  "Too many operators"). A symbol it holds no listing for is left out of its answer, and a listing with no sector carries
  no classification to read.
- `GET /v10/finance/quoteSummary/{symbol}?crumb=&modules=assetProfile,price` answers `{quoteSummary: {result:
  [{assetProfile: {country, sector, industry, ...}, price: {longName, shortName, ...}}]}}`: the company's own record
  of where it is, its sector and industry, and its name. Some 60 a second hold, 12 at once; 24 at once were refused
  (429) on 2,201 of 4,860 reads.
- A company's results and analysts' ratings on any market (search_company, `record`) are three surfaces, each read
  live on 2026-11-11 for 2330.TW, 7203.T, ASML.AS and 0700.HK:
  - the same with `modules=earnings,recommendationTrend,financialData`: `earnings.earningsChart.quarterly`, its
    latest four reports (`date` "3Q2026", `reportedDate`, `periodEndDate`, `actual`, `estimate`, `difference`,
    `surprisePct`), `earnings.financialsChart.quarterly` their revenue and earnings by the same `date`, in
    `earnings.financialCurrency`; `recommendationTrend.trend[0]`, this month's count of analysts at each rating
    (`strongBuy` ... `strongSell`); `financialData`'s price targets (`targetMeanPrice`, `targetLowPrice`,
    `targetHighPrice`, `numberOfAnalystOpinions`), each figure `{raw, fmt}`;
  - `POST /v1/finance/visualization?crumb=` with `{entityIdType: "earnings", sortField: "startdatetime", sortType,
    includeFields, query: {operator: "and", operands: [{operator: "eq", operands: ["ticker", <symbol>]}]}, offset,
    size}`, the site's earnings calendar, answers `{finance: {result: [{documents: [{columns: [{id}], rows: [[...]]}]}]}}`:
    each report with `startdatetime`, `epsestimate`, `epsactual` and `epssurprisepct`, back to 2014 for each of the
    four, and other events of the company (a shareholders' meeting) with none of the three. Its latest report was in
    mid-2025 for each, where the module's earliest is in 2026;
  - `GET /ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}?type=quarterlyTotalRevenue,quarterlyNetIncome,
    quarterlyDilutedEPS&period1=&period2=` answers `{timeseries: {result: [{meta: {type: [<type>]}, <type>: [{asOfDate,
    reportedValue: {raw}}]}]}}`: each quarter's figures by its period's end, some five quarters back, among them the
    quarters between the calendar's last report and the module's first.

A listing's symbol is its ticker where it trades with its exchange's suffix (`symbols_for`, SUFFIXES): `7203.T` Tokyo,
`2330.TW` Taiwan, `0700.HK` Hong Kong with its number to four digits, `600519.SS` Shanghai and `000001.SZ` Shenzhen
by the six-digit number's first digit, `000100.KS` Seoul (ETF Global writes `A000100`), `AC.MX` Mexico (ETF Global
writes `AC *`), `BRK-B` a US class. A fund's row states its ticker as its exchange gives it, sometimes
with the exchange's code after a space (`AAL LN`), a Swedish class after one (`ADDT B`, Yahoo's `ADDT-B.ST`) and a
Thai foreign board after a dash (`ADVANC-F`, Yahoo's `ADVANC.BK`). A symbol is confirmed by the screener's listings,
never taken on its form alone. Read on the equity rows of 14 funds drawn by rule, 10,036 of 11,040 matched their
listing this way; 501 more were found by searching their ISIN (`symbol`), 72 by their profile.

What industry and country a company has changes seldom, and a listing new since is found by its profile, so a listing's
sector, a listing's symbol and a company's profile are kept a month on a machine: each read under a subject naming
its month (`{subject}@{YYYY-MM}`).

A 429 or a server error is waited out and tried again; past its retries the read fails (VenueError), and where a
surface does not answer it is not asked again for a minute: the price charts, and the site's own surfaces, each apart.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from harness import sessions
from harness.venues.http import Cooldown, Pace, VenueError, VenueRefused, carried, now

BASE = "https://query1.finance.yahoo.com"
PARSE = 2  # the form a `home` payload's moments are in: 2, each exchange's own timetable where it states one
REACH = 2  # the form an `hourly` answer is in: 2, a window before Yahoo's first hour a failed read, never an empty one
ATTEMPTS = 4  # a read tried this many times in all
INTERVAL = 0.05  # seconds between requests
# A minor unit Yahoo quotes some markets in, and the currency and divisor it stands for.
MINOR = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ZAC": ("ZAR", 100.0),
         "ILA": ("ILS", 100.0), "KWF": ("KWD", 1000.0)}
# Each exchange code Yahoo gives a listing (a search's `exchange`, a chart's `meta.exchangeName`), as read live: the
# exchange's ISO 10383 MIC and its country, each country's primary market first. Two of Yahoo's codes are segments of
# another exchange: `IOB`, the London Stock Exchange's International Order Book, and `VSE`, the Ho Chi Minh City
# exchange's listings.
EXCHANGES = {
    "NYQ": ("XNYS", "US"), "NMS": ("XNAS", "US"), "NGM": ("XNAS", "US"), "NCM": ("XNAS", "US"), "ASE": ("XASE", "US"),
    "PCX": ("ARCX", "US"), "BTS": ("BATS", "US"), "PNK": ("OTCM", "US"),
    "KSC": ("XKRX", "KR"), "KOE": ("XKOS", "KR"), "TAI": ("XTAI", "TW"), "TWO": ("ROCO", "TW"),
    "HKG": ("XHKG", "HK"), "JPX": ("XTKS", "JP"), "SHH": ("XSHG", "CN"), "SHZ": ("XSHE", "CN"),
    "NSI": ("XNSE", "IN"), "BSE": ("XBOM", "IN"), "SES": ("XSES", "SG"), "KLS": ("XKLS", "MY"),
    "SET": ("XBKK", "TH"), "JKT": ("XIDX", "ID"), "VSE": ("XSTC", "VN"), "ASX": ("XASX", "AU"),
    "NZE": ("XNZE", "NZ"), "LSE": ("XLON", "GB"), "IOB": ("XLON", "GB"), "ISE": ("XDUB", "IE"),
    "GER": ("XETR", "DE"), "FRA": ("XFRA", "DE"), "STU": ("XSTU", "DE"), "DUS": ("XDUS", "DE"), "MUN": ("XMUN", "DE"),
    "PAR": ("XPAR", "FR"), "AMS": ("XAMS", "NL"), "BRU": ("XBRU", "BE"), "LIS": ("XLIS", "PT"),
    "EBS": ("XSWX", "CH"), "VIE": ("XWBO", "AT"), "MIL": ("XMIL", "IT"), "MCE": ("XMAD", "ES"),
    "STO": ("XSTO", "SE"), "CPH": ("XCSE", "DK"), "OSL": ("XOSL", "NO"), "HEL": ("XHEL", "FI"), "ICE": ("XICE", "IS"),
    "WSE": ("XWAR", "PL"), "BUD": ("XBUD", "HU"), "PRA": ("XPRA", "CZ"), "ATH": ("ASEX", "GR"), "IST": ("XIST", "TR"),
    "TLV": ("XTAE", "IL"), "SAU": ("XSAU", "SA"), "DOH": ("DSMD", "QA"), "DFM": ("XDFM", "AE"), "KUW": ("XKUW", "KW"),
    "CAI": ("XCAI", "EG"), "JNB": ("XJSE", "ZA"), "TOR": ("XTSE", "CA"), "VAN": ("XTSX", "CA"),
    "MEX": ("XMEX", "MX"), "SAO": ("BVMF", "BR"), "SGO": ("XSGO", "CL"), "BUE": ("XBUE", "AR"),
    "NEO": ("NEOE", "CA"), "HAM": ("XHAM", "DE"),
}
_ORDER = {code: k for k, code in enumerate(EXCHANGES)}
# The venues whose listing of another market's share is a dealer's quote of it, as a line over the counter is: the
# German regional exchanges' open market and the US over-the-counter market. A listing there is taken only where the
# fund's holdings name that country.
DEALERS = ("FRA", "STU", "DUS", "MUN", "HAM", "PNK")


# Seconds before a search that found nothing is asked again.
SEARCH_AGAIN = 2.0
SITE = 1 / 60  # seconds between the site's own reads (the screener, a company's record): their pace, apart from prices
SCREEN_PAGE = SCREEN_TICKERS = 250  # a page of the screener's answer, and the symbols one query names
SCREEN_AT_ONCE = 8  # queries read at once
# Each country's exchanges, by the suffix Yahoo writes their listings' symbols with, the main market first.
SUFFIXES = {"JP": (".T",), "HK": (".HK",), "TW": (".TW", ".TWO"), "KR": (".KS", ".KQ"), "IN": (".NS", ".BO"),
            "GB": (".L",), "FR": (".PA",), "DE": (".DE", ".F"), "NL": (".AS",), "CH": (".SW",), "SE": (".ST",),
            "DK": (".CO",), "NO": (".OL",), "FI": (".HE",), "IT": (".MI",), "ES": (".MC",), "BE": (".BR",),
            "PT": (".LS",), "AT": (".VI",), "IE": (".IR", ".L"), "AU": (".AX",), "NZ": (".NZ",), "CA": (".TO", ".V"),
            "BR": (".SA",), "MX": (".MX",), "ZA": (".JO",), "MY": (".KL",), "TH": (".BK",), "ID": (".JK",),
            "PH": (".PS",), "SG": (".SI",), "IL": (".TA",), "TR": (".IS",), "SA": (".SR",), "QA": (".QA",),
            "KW": (".KW",), "AE": (".AE",), "PL": (".WA",), "CZ": (".PR",), "HU": (".BD",), "GR": (".AT",),
            "CL": (".SN",), "CO": (".CL",), "PE": (".LM",), "EG": (".CA",), "AR": (".BA",), "VN": (".VN",),
            "LU": (".LU",), "IS": (".IC",), "EE": (".TL",), "LT": (".VS",), "LV": (".RG",), "RO": (".RO",)}
SPACED = ("SE", "DK", "NO", "FI")  # where a class after a space is part of the symbol, joined by a dash
# The suffixes of venues a member can hold a listing on whose shares are another market's, or a second exchange of a
# country, each by the code Yahoo gives its exchange (read live): London's International Order Book, the
# German regional exchanges, and Cboe Canada.
VENUE_SUFFIXES = {".IL": "IOB", ".SG": "STU", ".DU": "DUS", ".MU": "MUN", ".HM": "HAM", ".NE": "NEO"}
# Every suffix a listing abroad's symbol can end in: each country's above, Shanghai's and Shenzhen's, and the venues'.
EXCHANGE_SUFFIXES = frozenset({x for xs in SUFFIXES.values() for x in xs} | {".SS", ".SZ"} | set(VENUE_SUFFIXES))


# Bloomberg's codes for a US exchange, which a fund's row can write after a ticker, after a space or a dot ("SARO US",
# "OKE.UN", "MQ.UW"): the composite, NYSE, the Nasdaq's three tiers, NYSE American, NYSE Arca, over the counter, Cboe.
US_CODES = ("US", "U", "UN", "UW", "UQ", "UR", "UA", "UP", "UV", "UF")


def symbols_for(ticker: str | None, country: str | None, isin: str | None = None) -> list[str]:
    """The symbols a listing can have on Yahoo, from its ticker where it trades and its country (else its ISIN's):
    each of the country's exchanges' suffixes, then, where its ISIN names another country than the one given, that
    country's, since a fund can name the wrong one. A US ticker is read without a US exchange's code after it, and a
    class after a dot or a slash is Yahoo's after a dash."""
    t = str(ticker or "").strip().upper()
    isin_country = str(isin or "")[:2].upper()
    c = str(country or "").strip().upper() or isin_country
    if not t:
        return []
    if c == "US" or not c:
        parts = re.split(r"\s+|\.(?=[A-Z]{2}$)", t, maxsplit=1)
        if len(parts) == 2 and parts[1] in US_CODES:
            t = parts[0]
        own = [re.sub(r"\s+", "-", t.replace("/", "-").replace(".", "-"))]
        abroad = [t.split(" ")[0] + x for x in SUFFIXES.get(isin_country, ())] if isin_country not in ("", "US") else []
        return own + abroad
    base = re.sub(r"\s+", "-", t) if c in SPACED else t.split(" ")[0]
    base = t.replace(" ", "").replace("*", "") if c == "MX" else base.replace("/", "-")
    if c == "HK":
        base = base.lstrip("0").zfill(4)
    if c == "KR" and re.fullmatch(r"A\d{6}", base):  # the Korea Exchange's short code with its A before it, as ETF
        base = base[1:]                                 # Global writes it (A000100); Yahoo's is the six digits
    if c == "CN":
        base = base.zfill(6) if base.isdigit() else base  # a six-digit code its leading zeros dropped (688: 000688)
        return [f"{base}.{'SS' if base.startswith(('6', '9')) else 'SZ'}"]
    if c == "GB":
        base = base.rstrip(".")
    if c == "TH":
        base = re.sub(r"-[FR]$", "", base)
    own = [base + s for s in SUFFIXES.get(c, ())]
    other = [base + s for s in SUFFIXES.get(isin_country, ())] if isin_country not in ("", c, "US") else []
    return own + other


def region_of(symbol: str) -> str:
    """The country a symbol's listing trades in, by its suffix, lower-cased; a symbol with none is a US listing."""
    _, _, suffix = symbol.rpartition(".")
    if f".{suffix}" in VENUE_SUFFIXES and "." in symbol:
        return EXCHANGES[VENUE_SUFFIXES[f".{suffix}"]][1].lower()
    for country, suffixes in SUFFIXES.items():
        if f".{suffix}" in suffixes and "." in symbol:
            return "cn" if suffix in ("SS", "SZ") else country.lower()
    return "cn" if symbol.endswith((".SS", ".SZ")) else "us"


COOKIE, CRUMB = "https://fc.yahoo.com", "/v1/test/getcrumb"
SITE_AGENT = "Mozilla/5.0"  # how the site's own surfaces are read: its edge answers its crumb to a browser's name
PROFILE = "assetProfile,price"
RECORD = "earnings,recommendationTrend,financialData"  # a company's results and analysts' ratings


def mic_of(code: str | None) -> str | None:
    """The MIC of the exchange Yahoo codes `code`; None for a code the table does not hold."""
    return EXCHANGES.get(code or "", (None, None))[0]


def unit(currency: str | None) -> tuple[str | None, float]:
    """A quoted currency as its own: a minor unit as the currency it divides, with its divisor."""
    return MINOR.get(currency or "", (currency, 1.0))


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick(quotes: list[dict], country: str | None) -> str | None:
    """The listing on an exchange of `country`, its primary market first, among a search's quotes, each exchange named
    by the code Yahoo gives it (EXCHANGES); with no country, the listing on any exchange the table holds but those that
    quote other markets' shares through dealers (DEALERS)."""
    found = [(_ORDER[q["exchange"]], str(q.get("symbol") or "")) for q in quotes
             if isinstance(q, dict) and q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND") and q.get("symbol")
             and q.get("exchange") in EXCHANGES and (EXCHANGES[q["exchange"]][1] == country if country is not None
                                                     else q["exchange"] not in DEALERS)]
    return min(found)[1] if found else None


def trading_periods(chart: dict) -> dict[str, tuple[float, float]]:
    """Each session of an hourly read's exchange, by its date there: when it opened and closed."""
    meta = chart.get("meta") or {}
    zone = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    out: dict[str, tuple[float, float]] = {}
    for day in meta.get("tradingPeriods") or []:
        for period in day if isinstance(day, list) else [day]:
            if isinstance(period, dict) and period.get("start") and period.get("end"):
                key = datetime.fromtimestamp(period["start"], zone).date().isoformat()
                start, end = out.get(key, (period["start"], period["end"]))
                out[key] = (min(start, period["start"]), max(end, period["end"]))
    return out


def home_payload(listing: str, chart: dict, periods: dict[str, tuple[float, float]], retrieved: float,
                 start: str, end: str, official: dict[str, float] | None = None, stated: bool = False) -> dict:
    """A listing abroad's daily closes, dividends and splits over [start, end] in its currency's own unit, each close of
    a session complete at `retrieved` with the moment it was struck: the later of its exchange's official close for
    that session (`official`, harness.sessions) and the end Yahoo's own sessions give it, or the official close alone
    where its exchange's own timetable states it (`stated`); and where neither is known, its opening plus the length
    of the exchange's regular session."""
    official = official or {}
    meta = chart.get("meta") or {}
    zone = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    currency, divisor = unit(meta.get("currency"))
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    length = (regular.get("end") or 0) - (regular.get("start") or 0)
    closes = ((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    results = []
    for stamp, close in zip(chart.get("timestamp") or [], closes):
        session = datetime.fromtimestamp(stamp, zone).date().isoformat()
        ends = [t for t in (official.get(session),
                            periods[session][1] if session in periods and not (stated and session in official)
                            else None) if t]
        closed = max(ends) if ends else (stamp + length if length > 0 else None)
        if close is None or closed is None or closed > retrieved or not start <= session <= end:
            continue
        results.append({"session": session, "moment": _iso(closed), "c": float(close) / divisor})
    events = chart.get("events") or {}
    dividends = sorted(({"ex_date": datetime.fromtimestamp(d["date"], zone).date().isoformat(),
                         "amount": float(d["amount"]) / divisor}
                        for d in (events.get("dividends") or {}).values()
                        if isinstance(d, dict) and d.get("date") and d.get("amount") is not None),
                       key=lambda d: d["ex_date"])
    splits = sorted(({"date": datetime.fromtimestamp(s["date"], zone).date().isoformat(),
                      "from": float(s["denominator"]), "to": float(s["numerator"])}
                     for s in (events.get("splits") or {}).values()
                     if isinstance(s, dict) and s.get("date") and s.get("numerator") and s.get("denominator")),
                    key=lambda s: s["date"])
    return {"status": "OK", "listing": listing, "symbol": meta.get("symbol"), "exchange": meta.get("exchangeName"),
            "mic": mic_of(meta.get("exchangeName")), "timezone": meta.get("exchangeTimezoneName"),
            "currency": currency, "results": results,
            "dividends": [d for d in dividends if start <= d["ex_date"] <= end],
            "splits": [s for s in splits if s["date"] >= start], "parse": PARSE}


def _parsed(got: dict) -> dict:
    """A `home` answer that names no listing, in this client's form (PARSE)."""
    return {**got, "payload": {**got["payload"], "parse": PARSE}}


def currencies(pair: str, meta: dict) -> tuple[str, str]:
    """A pair's base and quote currencies, as Yahoo names them ("KRW/USD"), else as the pair is written."""
    named = str(meta.get("shortName") or "")
    if named.count("/") == 1 and all(len(x.strip()) == 3 for x in named.split("/")):
        base, quote = (x.strip().upper() for x in named.split("/"))
        return base, quote
    return pair[2:5], pair[5:8]


def hourly_payload(pair: str, chart: dict, start: str, end: str) -> dict:
    """A currency pair's hourly bars from the start of `start` in UTC to the end of `end` in New York, the hour's
    start in Unix milliseconds."""
    lo = datetime.combine(date.fromisoformat(start), datetime.min.time(), timezone.utc).timestamp()
    hi = datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(),
                          ZoneInfo("America/New_York")).timestamp()
    quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    rows = zip(chart.get("timestamp") or [], quote.get("open") or [], quote.get("high") or [],
               quote.get("low") or [], quote.get("close") or [])
    results = [{"t": int(t) * 1000, "o": float(o), "h": float(h if h is not None else o),
                "l": float(low if low is not None else o), "c": float(c)}
               for t, o, h, low, c in rows if lo <= t < hi and o is not None and c is not None]
    meta = chart.get("meta") or {}
    base, quote_currency = currencies(pair, meta)
    stamps = [t for t in chart.get("timestamp") or [] if t is not None]
    return {"status": "OK", "ticker": pair, "symbol": meta.get("symbol"), "base": base, "quote": quote_currency,
            "results": results, "first": int(min(stamps)) * 1000 if stamps else None, "parse": REACH}


def _raw(x: Any) -> Any:
    """A figure as the site gives it, `{raw, fmt}` or bare."""
    return x.get("raw") if isinstance(x, dict) else x


def _reported(earnings: dict, currency: str | None) -> list[dict]:
    """The module's latest reports: each quarter's date, period end, fiscal period and year, EPS actual and estimated
    with the surprise, and its revenue and earnings."""
    money = {q.get("date"): q for q in (earnings.get("financialsChart") or {}).get("quarterly") or []}
    rows = []
    for q in (earnings.get("earningsChart") or {}).get("quarterly") or []:
        fq = str(q.get("fiscalQuarter") or q.get("date") or "")
        m = re.fullmatch(r"(\d)Q(\d{4})", fq)
        f = money.get(q.get("date")) or {}
        surprise = q.get("surprisePct")
        rows.append({k: v for k, v in {
            "date": (q.get("reportedDate") or {}).get("fmt"), "period_end": (q.get("periodEndDate") or {}).get("fmt"),
            "fiscal_period": f"Q{m.group(1)}" if m else fq or None, "fiscal_year": int(m.group(2)) if m else None,
            "currency": currency, "actual_eps": _raw(q.get("actual")), "estimated_eps": _raw(q.get("estimate")),
            "eps_surprise": float(q["difference"]) if q.get("difference") not in (None, "") else None,
            "eps_surprise_percent": round(float(_raw(surprise)) / 100, 6) if _raw(surprise) not in (None, "") else None,
            "actual_revenue": _raw(f.get("revenue")), "actual_earnings": _raw(f.get("earnings"))}.items()
            if v is not None})
    return rows


class Yahoo:
    def __init__(self, *, http: httpx.Client | None = None, interval: float = INTERVAL, pause: float = 1.0,
                 site: httpx.Client | None = None):
        # the chart is read named as a browser, as the site's surfaces are: its edge answered this client's name, and
        # httpx's, 429 on every read while it answered a browser's 200 on the same request (read live)
        self.http = http or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": SITE_AGENT})
        self.interval, self.pause = interval, pause
        # each surface its own: a price chart refused says nothing of the site's listings, a profile, or a search, which
        # are answered apart and paced apart (read live: charts refused 429 while the screener answered every page)
        self.cooldown = Cooldown("Yahoo Finance")
        self.site_cooldown = Cooldown("Yahoo Finance's listings and company records")
        self._pace = Pace(interval)  # the price charts' pace
        self._symbols: dict[tuple[str, str], str | None] = {}  # each listing's symbol, or none, found once a day
        self._sessions: dict[tuple[str, str], dict] = {}  # each exchange's sessions, read once a day
        self._reading: dict[tuple[str, str], threading.Lock] = {}  # one read of an exchange's sessions at a time
        self._readers = threading.Lock()
        self.site = site or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": SITE_AGENT})
        self._crumb: str | None = None  # the site's crumb, with the cookie its client keeps
        self._crumb_lock = threading.Lock()
        self._site_pace = Pace(min(SITE, interval))  # the site's own surfaces' pace, apart from the charts'

    # ---------------------------------------------------------------- HTTP

    def _get(self, path: str, params: dict) -> Any:
        """One answer: a 429 or a server error waited out and tried again, past ATTEMPTS a VenueError; an answer that
        the symbol names nothing (404, or a chart with no result) is None; a request Yahoo refuses as malformed (400,
        or 422, as for an hourly window older than it serves) is ours to correct, raised as VenueRefused and never
        kept as an answer."""
        self.cooldown.check()
        for attempt in range(ATTEMPTS):
            self._pace.take()
            try:
                response = self.http.get(BASE + path, params=params)
            except httpx.HTTPError as e:
                failure = f"Yahoo Finance: {e}"
            else:
                if response.status_code == 404:
                    return None
                if response.status_code in (400, 422):
                    raise VenueRefused(f"Yahoo Finance refused our request for {path}: {response.text[:160]}")
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError:
                        failure = "Yahoo Finance answered with no data"
                else:
                    failure = f"Yahoo Finance answered {response.status_code}"
                    after = response.headers.get("Retry-After")
                    if response.status_code == 429 and after and after.isdigit():
                        time.sleep(min(float(after), 30.0))
                        continue
            if attempt < ATTEMPTS - 1:
                time.sleep(self.pause * 2 ** attempt)
        self.cooldown.failed()
        raise VenueError(failure)

    def _crumbed(self, again: bool = False) -> str:
        """The site's crumb, read once with its cookie, and again where a request says it no longer holds."""
        with self._crumb_lock:
            if self._crumb and not again:
                return self._crumb
            try:
                self.site.get(COOKIE)  # sets the cookie, whatever its own answer
            except httpx.HTTPError:
                pass
            try:
                response = self.site.get(BASE + CRUMB)
            except httpx.HTTPError as e:
                raise VenueError(f"Yahoo Finance gave no crumb: {e}") from None
            crumb = response.text.strip()
            if response.status_code != 200 or not crumb or "<" in crumb or " " in crumb:
                raise VenueError(f"Yahoo Finance gave no crumb ({response.status_code})")
            self._crumb = crumb
            return crumb

    def _site(self, path: str, params: dict | None = None, body: dict | None = None) -> Any:
        """One answer of the site's own surfaces, with its crumb, at their own pace: a crumb it no longer holds read
        again once; a 429 or a server error waited out and tried again; 404 is None; past ATTEMPTS a VenueError."""
        self.site_cooldown.check()
        again = False
        for attempt in range(ATTEMPTS):
            self._site_pace.take()
            query = {**(params or {}), "crumb": self._crumbed(again)}
            again = False
            try:
                response = self.site.post(BASE + path, params=query, json=body) if body is not None else \
                    self.site.get(BASE + path, params=query)
            except httpx.HTTPError as e:
                failure = f"Yahoo Finance: {e}"
            else:
                if response.status_code == 404:
                    return None
                if response.status_code == 401:  # its crumb: read again
                    again, failure = True, "Yahoo Finance refused its crumb"
                    continue
                if response.status_code in (400, 422):
                    raise VenueRefused(f"Yahoo Finance refused our request for {path}: {response.text[:160]}")
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError:
                        failure = "Yahoo Finance answered with no data"
                else:
                    failure = f"Yahoo Finance answered {response.status_code}"
            if attempt < ATTEMPTS - 1:
                time.sleep(self.pause * 2 ** attempt)
        self.site_cooldown.failed()
        raise VenueError(failure)

    def _screen(self, symbols: list[str]) -> list[dict]:
        """The listings the screener holds among these symbols, at most SCREEN_TICKERS of them: one page."""
        body = {"size": SCREEN_PAGE, "offset": 0, "sortField": "ticker", "sortType": "ASC", "quoteType": "EQUITY",
                "includeFields": ["ticker", "companyshortname", "sector", "industry", "exchange"],
                "topOperator": "AND", "query": {"operator": "or", "operands": [
                    {"operator": "eq", "operands": ["ticker", s]} for s in symbols]}}
        found = self._site("/v1/finance/screener", {"formatted": "false", "useRecordsResponse": "true",
                                                    "lang": "en-US", "region": "US"}, body)
        result = (((found or {}).get("finance") or {}).get("result") or [{}])[0] or {}
        return result.get("records") or []

    def sectors(self, symbols: list[str], day: str) -> dict[str, dict]:
        """Each listing's sector and industry, by its symbol: `{status: "OK", symbol, name, sector, industry, exchange}`
        where the screener holds it with a sector, and `{status: "NOT_FOUND", symbol}` where it holds none; the
        symbols asked SCREEN_TICKERS to a query, SCREEN_AT_ONCE queries at once."""
        from concurrent.futures import ThreadPoolExecutor
        retrieved = now()
        asked = sorted(set(symbols))
        batches = [asked[k:k + SCREEN_TICKERS] for k in range(0, len(asked), SCREEN_TICKERS)]
        with ThreadPoolExecutor(min(SCREEN_AT_ONCE, len(batches) or 1)) as pool:
            records = [r for page in pool.map(carried(self._screen), batches) for r in page if isinstance(r, dict)]
        held = {r["ticker"]: r for r in records if r.get("ticker") and r.get("sector")}
        out = {}
        for symbol in asked:
            r = held.get(symbol)
            payload = {"status": "OK", "symbol": symbol, "name": r.get("companyName"), "sector": r.get("sector"),
                       "industry": r.get("industry"), "exchange": r.get("exchange")} if r else \
                {"status": "NOT_FOUND", "symbol": symbol}
            out[symbol] = {"payload": payload, "request": {"method": "POST", "url": BASE + "/v1/finance/screener",
                                                          "ticker": symbol},
                           "retrieved_at": retrieved, "range": (day, day)}
        return out

    def record(self, symbol: str, day: str) -> dict:
        """A company's results and analysts' ratings, in the fields a US listing's carry (Massive's Benzinga rows), in
        its currency. `results`: each report, the latest first, with its date, and where its source states them, its
        period's end, fiscal period and year, its EPS actual and estimated with the surprise, and its revenue and
        earnings: the module's latest four, with each quarter's figures by its period's end that no report row holds
        (no report date), then the calendar's earlier reports. `ratings`: how many analysts give each rating this month,
        and their price targets, as they stand on the day read."""
        retrieved = now()
        request = {"method": "GET", "url": BASE + f"/v10/finance/quoteSummary/{symbol}", "modules": RECORD}
        try:
            found = self._site(f"/v10/finance/quoteSummary/{symbol}", {"modules": RECORD})
        except VenueRefused:  # a symbol Yahoo lists nothing under: no such listing
            found = None
        result = (((found or {}).get("quoteSummary") or {}).get("result") or [None])[0] if found else None
        if not result:
            return {"payload": {"status": "NOT_FOUND", "symbol": symbol}, "request": request, "retrieved_at": retrieved,
                    "range": (day, day)}
        earnings = result.get("earnings") or {}
        fd = result.get("financialData") or {}
        currency = earnings.get("financialCurrency") or fd.get("financialCurrency")
        rows = _reported(earnings, currency)
        covered = {r.get("period_end") for r in rows}
        rows += [{**q, "currency": currency} for q in self._quarters(symbol) if q["period_end"] not in covered]
        first = min((r["date"] for r in rows if r.get("date")), default=None)
        rows += [{**r, "currency": currency} for r in self._calendar(symbol) if first is None or r["date"] < first]
        rows.sort(key=lambda r: r.get("date") or r.get("period_end") or "", reverse=True)
        trend = ((result.get("recommendationTrend") or {}).get("trend") or [{}])[0] or {}
        ratings = {k: v for k, v in {
            "as_of": day, "ratings_contributors": sum(trend.get(x) or 0 for x in ("strongBuy", "buy", "hold", "sell",
                                                                                   "strongSell")),
            "strong_buy_ratings": trend.get("strongBuy"), "buy_ratings": trend.get("buy"),
            "hold_ratings": trend.get("hold"), "sell_ratings": trend.get("sell"),
            "strong_sell_ratings": trend.get("strongSell"),
            "price_target_contributors": _raw(fd.get("numberOfAnalystOpinions")),
            "consensus_price_target": _raw(fd.get("targetMeanPrice")), "low_price_target": _raw(fd.get("targetLowPrice")),
            "high_price_target": _raw(fd.get("targetHighPrice")),
            "currency": fd.get("financialCurrency") or currency}.items() if v is not None}
        return {"payload": {"status": "OK", "symbol": symbol, "results": rows, "ratings": ratings},
                "request": request, "retrieved_at": retrieved, "range": (day, day)}

    def _calendar(self, symbol: str) -> list[dict]:
        """The site's earnings calendar for one symbol: each report it holds, with its date and its EPS estimated,
        actual and surprise; an event with none of the three (a meeting) is not a report."""
        body = {"sortType": "DESC", "entityIdType": "earnings", "sortField": "startdatetime", "offset": 0, "size": 250,
                "includeFields": ["ticker", "startdatetime", "epsestimate", "epsactual", "epssurprisepct"],
                "query": {"operator": "and", "operands": [{"operator": "eq", "operands": ["ticker", symbol]}]}}
        found = self._site("/v1/finance/visualization", {"lang": "en-US", "region": "US"}, body) or {}
        docs = ((((found.get("finance") or {}).get("result") or [{}])[0] or {}).get("documents") or [{}])
        doc = docs[0] if docs else {}
        at = {c.get("id"): i for i, c in enumerate(doc.get("columns") or [])}
        out = []
        for row in doc.get("rows") or []:
            get = (lambda f: row[at[f]] if f in at and at[f] < len(row) else None)
            estimate, actual, pct = get("epsestimate"), get("epsactual"), get("epssurprisepct")
            if estimate is None and actual is None and pct is None or not get("startdatetime"):
                continue
            out.append({k: v for k, v in {
                "date": str(get("startdatetime"))[:10], "actual_eps": actual, "estimated_eps": estimate,
                "eps_surprise": round(actual - estimate, 10) if actual is not None and estimate is not None else None,
                "eps_surprise_percent": round(pct / 100, 6) if pct is not None else None}.items() if v is not None})
        return out

    def _quarters(self, symbol: str) -> list[dict]:
        """Each quarter's revenue, earnings and diluted EPS by its period's end, as the site's fundamentals series
        give them."""
        types = {"quarterlyTotalRevenue": "actual_revenue", "quarterlyNetIncome": "actual_earnings",
                 "quarterlyDilutedEPS": "actual_eps"}
        found = self._site(f"/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
                           {"type": ",".join(types), "period1": "0", "period2": str(int(time.time())),
                            "lang": "en-US", "region": "US"}) or {}
        by_end: dict[str, dict] = {}
        for res in ((found.get("timeseries") or {}).get("result") or []):
            kind = ((res.get("meta") or {}).get("type") or [None])[0]
            for v in res.get(kind) or [] if kind in types else []:
                if isinstance(v, dict) and v.get("asOfDate") and _raw(v.get("reportedValue")) is not None:
                    by_end.setdefault(v["asOfDate"], {"period_end": v["asOfDate"]})[types[kind]] = \
                        _raw(v["reportedValue"])
        return list(by_end.values())

    def listing_symbol(self, listing: str, day: str) -> dict:
        """A listing abroad's symbol, `{ISIN}:{country}`, as Yahoo's search by ISIN finds it (`symbol`)."""
        retrieved = now()
        found = self.symbol(listing)
        return {"payload": {"status": "OK" if found else "NOT_FOUND", "listing": listing, "symbol": found},
                "request": {"method": "GET", "url": BASE + "/v1/finance/search", "q": listing.split(":")[0]},
                "retrieved_at": retrieved, "range": (day, day)}

    def profile(self, symbol: str, day: str) -> dict:
        """A company's own record of what it is: its country, its sector and industry, and its name."""
        retrieved = now()
        try:
            found = self._site(f"/v10/finance/quoteSummary/{symbol}", {"modules": PROFILE})
        except VenueRefused:  # read live: a symbol Yahoo lists nothing under is answered 400, as no such listing
            found = None
        result = (((found or {}).get("quoteSummary") or {}).get("result") or [None])[0] if found else None
        request = {"method": "GET", "url": BASE + f"/v10/finance/quoteSummary/{symbol}", "modules": PROFILE}
        if not result:
            return {"payload": {"status": "NOT_FOUND", "symbol": symbol}, "request": request, "retrieved_at": retrieved,
                    "range": (day, day)}
        p, price = result.get("assetProfile") or {}, result.get("price") or {}
        return {"payload": {"status": "OK", "symbol": symbol, "name": price.get("longName") or price.get("shortName"),
                            "country": p.get("country"), "sector": p.get("sector"), "industry": p.get("industry")},
                "request": request, "retrieved_at": retrieved, "range": (day, day)}

    def _chart(self, symbol: str, params: dict) -> dict | None:
        """A symbol's chart, or None where it names nothing. A span that ends before the symbol first traded has no
        prices, and Yahoo refuses it (400, read live: TSDD's from 2022-01-03 to 2022-10-12, "Data doesn't
        exist"), where the chart's own `meta.firstTradeDate` states when it first traded (TSDD's 2023-08-28): such a
        span is answered as a chart with no prices, in its meta now. Any other refusal is raised."""
        try:
            body = self._get(f"/v8/finance/chart/{symbol}", params)
        except VenueRefused:
            end = params.get("period2")
            if not isinstance(end, (int, float)):
                raise
            meta = ((self._chart(symbol, {"interval": "1d", "range": "5d"}) or {}).get("meta")) or {}
            first = meta.get("firstTradeDate")
            if not isinstance(first, (int, float)) or end > first:
                raise
            return {"meta": meta, "timestamp": [], "indicators": {"quote": [{"close": []}]}, "events": {}}
        found = ((body or {}).get("chart") or {}).get("result") if isinstance(body, dict) else None
        return found[0] if found else None

    def charted(self, symbol: str, params: dict) -> tuple[str, dict | None]:
        """A listing's chart, and the symbol Yahoo gives it under: its own, or, for a US share class written as Massive
        writes it, a letter after a dot (`MKC.V`, McCormick's voting class, whose form is also a TSX Venture
        symbol's), Yahoo's form for a US class, the letter after a dash (`MKC-V`), where its own names nothing (read
        live, 2026-11-12: MKC.V 404, MKC-V on NYQ)."""
        chart = self._chart(symbol, params)
        if chart is None and re.fullmatch(r"[A-Z]+\.[A-Z]", symbol):
            us = symbol.replace(".", "-")
            found = self._chart(us, params)
            if found is not None and EXCHANGES.get((found.get("meta") or {}).get("exchangeName"), ("", ""))[1] == "US":
                return us, found
        return symbol, chart

    def reference(self, symbol: str, day: str) -> dict:
        """A listing abroad's reference, from its chart, in the shape Massive gives a listing's (MATH.md 3.1: a holding's
        reference says what it is): `{status, results: {ticker, name, type, market: "home", locale, primary_exchange,
        currency_name, active}}`, `type` Massive's code for what Yahoo's `meta.instrumentType` says (EQUITY a share,
        CS; ETF; MUTUALFUND, MF), `primary_exchange` its exchange's MIC and `locale` its country (EXCHANGES), and
        `currency_name` its currency's own unit, lower-cased as Massive writes it. NOT_FOUND where Yahoo charts no
        listing under the symbol, or charts one on an exchange the table does not hold."""
        retrieved = now()
        params = {"interval": "1d", "range": "5d"}
        _, chart = self.charted(symbol, params)
        meta = (chart or {}).get("meta") or {}
        exchange = EXCHANGES.get(meta.get("exchangeName") or "")
        request = {"method": "GET", "url": f"{BASE}/v8/finance/chart/{symbol}", "params": params}
        if exchange is None:
            return {"payload": {"status": "NOT_FOUND", "ticker": symbol,
                                "error": f"Yahoo Finance lists no listing on an exchange it names under {symbol}"},
                    "request": request, "retrieved_at": retrieved, "range": (day, day)}
        currency, _ = unit(meta.get("currency"))
        kind = {"EQUITY": "CS", "ETF": "ETF", "MUTUALFUND": "MF"}.get(meta.get("instrumentType") or "",
                                                                        meta.get("instrumentType"))
        try:  # what it does, in its own record's words (`assetProfile.longBusinessSummary`), as Massive's description is
            found = self._site(f"/v10/finance/quoteSummary/{symbol}", {"modules": "assetProfile"})
        except VenueRefused:  # read live: a symbol Yahoo keeps no record for is answered 400
            found = None
        profile = ((((found or {}).get("quoteSummary") or {}).get("result") or [{}])[0] or {}).get("assetProfile") or {}
        results = {"ticker": symbol, "name": meta.get("longName") or meta.get("shortName"), "type": kind,
                   "market": "home", "locale": exchange[1].lower(), "primary_exchange": exchange[0],
                   "currency_name": (currency or "").lower() or None, "active": True,
                   **({"description": profile["longBusinessSummary"]} if profile.get("longBusinessSummary") else {})}
        return {"payload": {"status": "OK", "results": results}, "request": request, "retrieved_at": retrieved,
                "range": (day, day)}

    # ---------------------------------------------------------------- a listing abroad

    def symbol(self, listing: str) -> str | None:
        """The symbol of a listing: its own where it is named by its symbol (`0700.HK`); for `{ISIN}:{country}`, the
        security the ISIN names, on its country's exchange."""
        if ":" not in listing:
            return listing
        key = (listing, date.today().isoformat())  # a listing not found today is asked again tomorrow, as one found is
        if key not in self._symbols:
            isin, _, country = listing.partition(":")
            params = {"q": isin, "quotesCount": 20, "newsCount": 0}
            quotes = (self._site("/v1/finance/search", params) or {}).get("quotes") or []
            if not quotes:  # read live, the search answered an ISIN it finds with none, under load: asked once more
                time.sleep(SEARCH_AGAIN)
                quotes = (self._site("/v1/finance/search", params) or {}).get("quotes") or []
            self._symbols[key] = pick(quotes, country.upper()) or pick(quotes, isin[:2].upper()) or pick(quotes, None)
        return self._symbols[key]

    def _exchange_sessions(self, symbol: str, exchange: str | None) -> dict[str, tuple[float, float]]:
        """An exchange's sessions as Yahoo records them, from a listing's hourly read, once a day. For a listing
        younger than the two years an hourly read reaches back, Yahoo answers that span "1h data not available"
        (422): it holds no such span, so a shorter one is read."""
        key = (exchange or symbol, date.today().isoformat())
        with self._readers:
            reading = self._reading.setdefault(key, threading.Lock())
        with reading:  # listings of one exchange read side by side wait for its one read (read live: 98 for some 12)
            if key not in self._sessions:
                chart = None
                for span in ("730d", "1y", "60d"):
                    try:
                        chart = self._chart(symbol, {"interval": "60m", "range": span})
                        break
                    except VenueRefused:
                        continue
                self._sessions[key] = trading_periods(chart) if chart else {}
        return self._sessions[key]

    def _none(self, listing: str, said: str, url: str, covering: tuple[str, str]) -> dict:
        return {"payload": {"status": "NOT_FOUND", "listing": listing, "error": said},
                "request": {"method": "GET", "url": url}, "retrieved_at": now(), "range": covering}

    def home(self, listing: str, start: str, end: str) -> dict:
        """A listing abroad's daily closes, dividends and splits over [start, end], each close with its moment."""
        symbol, retrieved = self.symbol(listing), now()
        if symbol is None:
            isin, _, country = listing.partition(":")
            return _parsed(self._none(listing, f"Yahoo Finance lists no listing of {isin} in {country}",
                                      f"{BASE}/v1/finance/search?q={isin}", (start, end)))
        lo = int(datetime.combine(date.fromisoformat(start), datetime.min.time(), timezone.utc).timestamp())
        hi = int(datetime.combine(date.fromisoformat(end) + timedelta(days=2), datetime.min.time(),
                                  timezone.utc).timestamp())
        params = {"interval": "1d", "period1": lo, "period2": hi, "events": "div,splits"}
        symbol, chart = self.charted(symbol, params)
        url = f"{BASE}/v8/finance/chart/{symbol}"
        if chart is None:
            return _parsed(self._none(listing, f"Yahoo Finance gives no prices for {symbol}", url, (start, end)))
        periods = self._exchange_sessions(symbol, (chart.get("meta") or {}).get("exchangeName"))
        mic = mic_of((chart.get("meta") or {}).get("exchangeName"))
        payload = home_payload(listing, chart, periods, datetime.fromisoformat(retrieved).timestamp(), start, end,
                               sessions.closes(mic, start, end), sessions.stated(mic))
        return {"payload": payload, "request": {"method": "GET", "url": url, "params": params,
                                                "sessions": {"interval": "60m", "range": "730d"}},
                "retrieved_at": retrieved, "range": (start, end)}

    def home_mark(self, listing: str, day: str) -> dict:
        """A listing abroad's latest price, in its currency's own unit, and when it was struck."""
        symbol, retrieved = self.symbol(listing), now()
        if symbol is None:
            return self._none(listing, f"Yahoo Finance lists no listing of {listing}", f"{BASE}/v1/finance/search",
                              (day, day))
        symbol, chart = self.charted(symbol, {"interval": "1d", "range": "1d"})
        url = f"{BASE}/v8/finance/chart/{symbol}"
        meta = (chart or {}).get("meta") or {}
        if meta.get("regularMarketPrice") is None or not meta.get("regularMarketTime"):
            return self._none(listing, f"Yahoo Finance gives no price for {symbol}", url, (day, day))
        currency, divisor = unit(meta.get("currency"))
        payload = {"status": "OK", "listing": listing, "symbol": symbol, "currency": currency,
                   "price": float(meta["regularMarketPrice"]) / divisor, "time": _iso(meta["regularMarketTime"])}
        return {"payload": payload, "request": {"method": "GET", "url": url, "params": {"range": "1d"}},
                "retrieved_at": retrieved, "range": (day, day)}

    # ---------------------------------------------------------------- a currency pair

    def hourly(self, pair: str, start: str, end: str) -> dict:
        """A currency pair's hourly rates over the window, as far back as Yahoo serves them: read live, its first hour
        some 1,020 days back (EURUSD=X's 2024-01-24 on 2026-11-10), before today's window opens. A window that ends
        before Yahoo's first hour is one this surface cannot give, not one with no rates: the source's answer that it
        holds none for it (LookupError), which refuses the call that needs it with its reach, an answer no retry
        changes, so that no listing quoted in the currency passes as priced with no dollar close."""
        symbol, retrieved = f"{pair[2:]}=X", now()
        params = {"interval": "60m", "range": "730d"}
        chart = self._chart(symbol, params)
        url = f"{BASE}/v8/finance/chart/{symbol}"
        if chart is None or not chart.get("timestamp"):
            return self._none(pair, f"Yahoo Finance gives no rates for {pair[2:5]} in {pair[5:]}", url, (start, end))
        first = datetime.fromtimestamp(min(chart["timestamp"]), timezone.utc).date()
        if first > date.fromisoformat(end):
            raise LookupError(f"Yahoo Finance's hourly rates for {pair[2:5]} in {pair[5:]} reach back to {first}, "
                              f"after {end}")
        return {"payload": hourly_payload(pair, chart, start, end), "request": {"method": "GET", "url": url,
                                                                               "params": params},
                "retrieved_at": retrieved, "range": (start, end)}

    def mark(self, pair: str, day: str) -> dict:
        """A currency pair's latest rate and when it was struck: the close of the minute's bar its latest rate was
        struck in, which carries the rate whole. `meta.regularMarketPrice` is the same rate rounded to `priceHint`'s
        places, four for a pair (read live: KRWUSD=X 0.0007 where its bar closed at 0.00074975, 6.6% apart;
        JPYUSD=X 0.0063 against 0.0062612), so it is taken only where no bar holds the moment."""
        symbol, retrieved = f"{pair[2:]}=X", now()
        params = {"interval": "1m", "range": "1d"}
        chart = self._chart(symbol, params)
        url = f"{BASE}/v8/finance/chart/{symbol}"
        meta = (chart or {}).get("meta") or {}
        if meta.get("regularMarketPrice") is None or not meta.get("regularMarketTime"):
            return self._none(pair, f"Yahoo Finance gives no rate for {pair[2:5]} in {pair[5:]}", url, (day, day))
        struck = meta["regularMarketTime"]
        closes = (((chart.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        price = next((c for t, c in zip(chart.get("timestamp") or [], closes)
                      if c is not None and t <= struck < t + 60), meta["regularMarketPrice"])
        base, quote = currencies(pair, meta)
        payload = {"status": "OK", "ticker": pair, "symbol": symbol, "base": base, "quote": quote,
                   "price": float(price), "time": _iso(struck)}
        return {"payload": payload, "request": {"method": "GET", "url": url, "params": params},
                "retrieved_at": retrieved, "range": (day, day)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        """A listing abroad's prices and latest mark; a currency pair's hourly rates and latest rate."""
        def home(subject: str, covering: Any) -> dict:
            return self.home(subject, covering[0], covering[1])

        def hourly(subject: str, covering: Any) -> dict:
            return self.hourly(subject, covering[0], covering[1])

        home.parse = PARSE  # type: ignore[attr-defined]
        hourly.parse = REACH  # type: ignore[attr-defined]
        def monthly(read: Callable[[str, str], dict]) -> Callable[[str, Any], dict]:
            return lambda subject, covering: read(subject.rsplit("@", 1)[0], covering[1])

        def listing_sector(subject: str, covering: Any) -> dict:  # `{symbol}@{YYYY-MM}`, a month's reading
            return self.sectors([subject.rsplit("@", 1)[0]], covering[1])[subject.rsplit("@", 1)[0]]

        def sectors_of(subjects: Any, covering: Any) -> dict[str, dict]:  # many at once, as the store's prefetch asks
            named = {str(s).rsplit("@", 1)[0]: s for s in subjects}
            return {named[k]: v for k, v in self.sectors(sorted(named), covering[1]).items()}

        listing_sector.many = sectors_of  # type: ignore[attr-defined]
        return {"home": home,
                "listing_sector": listing_sector,
                "profile": monthly(self.profile),
                "listing_symbol": monthly(self.listing_symbol),
                "company_record": lambda subject, covering: self.record(subject, covering[1]),
                "home_mark": lambda subject, covering: self.home_mark(subject, covering[1]),
                "hourly": hourly,
                "last_trade": lambda subject, covering: self.mark(subject, covering[1])}
