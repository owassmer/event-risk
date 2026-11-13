"""The SEC's EDGAR, public and with no key: the list of the tickers of the companies that file with it, and a fund's
holdings as it reports them on Form N-PORT.

The list of tickers: `GET https://www.sec.gov/files/company_tickers_exchange.json`
(https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) answers `{"fields": ["cik",
"name", "ticker", "exchange"], "data": [[cik, name, ticker, exchange], ...]}`, some ten thousand tickers, each with
the CIK of the company filing under it and its exchange (`NYSE`, `Nasdaq`, `CBOE`, `OTC`, or none), a company's
over-the-counter tickers among them, and a closed-end fund's or a unit trust's. The SEC updates it periodically and
does not guarantee its accuracy or scope, so the look-through confirms what it says at Massive, which lists a
company's listings by its CIK as of a date. It is the company behind a US listing, so that a company's share listed
abroad and over the counter in the US is found with its listing on a US exchange, such as a depositary receipt.

A fund's holdings (`nport`), the latest Form N-PORT holdings publicly available by the as-of:

1. The fund. `GET https://www.sec.gov/files/company_tickers_mf.json` (the same page) answers `{"fields": ["cik",
   "seriesId", "classId", "symbol"], "data": [...]}`, every open-end fund's and ETF's ticker with its series, the
   fund that files; a closed-end fund or a unit trust (SPY, DIA), which has no series, files under the CIK the list of
   tickers gives it. Both lists are read once a day.
2. Its filings. `GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series or CIK}&type=NPORT-P&
   dateb={YYYYMMDD}&owner=include&count=40&output=atom` (EDGAR's company feed,
   https://www.sec.gov/about/rss-feeds) answers an Atom feed of the filer's NPORT-P and NPORT-P/A filings before the
   date, newest first, each `<content>` giving `filing-type`, `filing-date`, `accession-number` and `filing-href`; a
   series id answers that series' own filings, though its trust files them all under one CIK. A fund files NPORT-P,
   the public report, once a quarter, as of the quarter's end, within sixty days of it; NPORT-P/A restates a report.
3. Its holdings. `GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession without dashes}/primary_doc.xml`
   answers the report (EDGAR Form N-PORT XML Technical Specification,
   https://www.sec.gov/info/edgar/specifications/form-n-port-xml-tech-specs.htm): `genInfo/repPdDate`, the date its
   holdings stand at, and `invstOrSecs/invstOrSec`, one per holding (Form N-PORT Part C,
   https://www.sec.gov/files/formn-port.pdf): `name`, `title`, `lei`, `cusip`, `identifiers` (`isin`, `ticker`,
   `other`, each its `value`), `balance` and `units`, `curCd` (or, for a holding in another currency,
   `currencyConditional` with its `curCd` and `exchangeRt`, the rate its value in dollars was taken at, quoted either
   way round: dollars per unit, read live, a Hong Kong share's 0.127512, or units per dollar, KMLM's and CTA's 2026
   reports' EUR 0.8752 and JPY 162.595; the form does not say which, so nothing here reads it as a rate), `valUSD`, `pctVal` (its value as a percentage of the
   fund's net assets), `payoffProfile`, `assetCat` and `issuerCat` (or `assetConditional` and `issuerConditional`
   where the category is `OTHER`, with its `desc`), `invCountry` (the country the issuer is organized in). A CUSIP or
   ISIN the fund has none for reads as zeros or `N/A`.

The report used is the latest one filed by the as-of, read whole: its restatement where one was filed after it by the
as-of. Each holding is kept under the field names Massive's ETF Global holdings use, so the look-through reads either:
`constituent_name`, `constituent_ticker`, `isin`, `weight` (`pctVal` / 100), `asset_class` and `security_type` (the
asset and issuer types in the form's own words), with what N-PORT adds: `cusip`, `lei`, `asset_category` and
`issuer_category` (the codes), `market_value`, `shares` and `units`, `currency`, `exchange_rate`,
`country_of_issuer`, and `payoff`. The
report gives no exchange, so no holding says where it is listed. A fund the SEC lists no series or CIK for, or one
with no report filed by the as-of (a grantor trust, a commodity pool, a business development company: they do not
file N-PORT), is answered `NOT_FOUND`, kept as data.

The SEC asks every automated request to declare who makes it, a name and a contact address, and to keep to ten
requests a second (https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). Its edge refuses
some undeclared clients with 403, judged by the client and its User-Agent together: measured, a User-Agent with a
contact address was answered from urllib, httpx and curl alike; this client's own, `event-risk/{version}` with no
address, was answered from httpx on every read and refused from urllib and curl, and one naming a code host (github)
or a library (python-httpx) was refused from any. The reads go through httpx naming this client, and no address is
sent: none is this package's to give.
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Callable

import httpx

from harness.venues.http import Cooldown, VenueError, client, now

URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FUNDS = "https://www.sec.gov/files/company_tickers_mf.json"
FEED = "https://www.sec.gov/cgi-bin/browse-edgar"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
INTERVAL = 0.11  # seconds between requests: the SEC asks for no more than ten a second
FILINGS = 40  # the filings the feed is asked for, ten years of quarterly reports and their restatements
# The form of a report's parse (report()), which a parse kept on this machine is kept under: a filing never changes,
# and its parse changes only when this does, so a parse made before a change is not served after it.
PARSE = 2

# Form N-PORT Item C.4's asset and issuer types, by the codes its XML gives them, in the form's own words.
ASSET_TYPES = {
    "STIV": "Short-term investment vehicle", "RA": "Repurchase agreement", "EC": "Equity-common",
    "EP": "Equity-preferred", "DBT": "Debt", "DCO": "Derivative-commodity", "DCR": "Derivative-credit",
    "DE": "Derivative-equity", "DFE": "Derivative-foreign exchange", "DIR": "Derivative-interest rate",
    "DO": "Derivatives-other", "SN": "Structured note", "LON": "Loan", "ABS-MBS": "ABS-mortgage backed security",
    "ABS-APCP": "ABS-asset backed commercial paper", "ABS-CBDO": "ABS-collateralized bond/debt obligation",
    "ABS-O": "ABS-other", "COMM": "Commodity", "RE": "Real estate", "OTHER": "Other",
}
ISSUER_TYPES = {
    "CORP": "Corporate", "UST": "U.S. Treasury", "USGA": "U.S. government agency",
    "USGSE": "U.S. government sponsored entity", "MUN": "Municipal", "NUSS": "Non-U.S. sovereign",
    "PF": "Private fund", "RF": "Registered fund", "OTHER": "Other",
}
_ID = re.compile(r"^[0-9A-Z]{9}$")


def _identifier(value: Any, length: int) -> str | None:
    """A CUSIP (9) or an ISIN (12) as given, or none where the report gives zeros or N/A."""
    v = str(value or "").strip().upper()
    if len(v) != length or not re.fullmatch(r"[0-9A-Z]+", v) or set(v) == {"0"}:
        return None
    return v


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    return next((c for c in node if _local(c.tag) == name), None)


def _text(node: ET.Element | None, name: str) -> str | None:
    found = _child(node, name)
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except ValueError:
        return None


def _category(node: ET.Element, name: str, conditional: str, words: dict[str, str]) -> tuple[str | None, str | None]:
    """A holding's category code, and its label: the form's words, or the fund's own description of an `OTHER`."""
    code = _text(node, name)
    other = _child(node, conditional)
    if code is None and other is not None:
        code = other.get(name)
        return code, other.get("desc") or words.get(code or "")
    return code, words.get(code or "")


def _security(node: ET.Element | None) -> dict:
    """A security a derivative references, by its issuer, title and identifiers, each as the report gives it."""
    ids = _child(node, "identifiers")
    given = {}
    for c in (ids if ids is not None else []):
        tag = _local(c.tag)
        if tag == "other":
            given.setdefault("other", []).append({"desc": c.get("otherDesc"), "value": c.get("value")})
        elif c.get("value"):
            given[tag] = c.get("value")
    out = {"issuer": _text(node, "issuerName"), "title": _text(node, "issueTitle"),
           "cusip": _identifier(given.get("cusip"), 9), "isin": _identifier(given.get("isin"), 12),
           "ticker": (str(given.get("ticker") or "").strip().upper() or None), "other": given.get("other") or []}
    return {k: v for k, v in out.items() if v not in (None, "N/A", [])}


def _reference(node: ET.Element | None) -> dict | None:
    """What a derivative references (Item C.11.c.iii): an index or a custom basket by its name and identifier, with
    its components where the report lists them; a security by its issuer, title and identifiers; or another
    derivative, nested."""
    if node is None:
        return None
    index, other, nested = (_child(node, k) for k in ("indexBasketInfo", "otherRefInst", "nestedDerivInfo"))
    if index is not None:
        found = {"name": _text(index, "indexName"), "identifier": _text(index, "indexIdentifier")}
        components = [{"name": _text(c, "othIndName"), "identifier": _text(c, "othIndIdentifier"),
                       "notional": _number(_text(c, "othIndNotAmt")), "currency": _text(c, "othIndCurCd")}
                      for c in index.iter() if _local(c.tag) in ("indexComponent", "component")]
        return {"index": {k: v for k, v in found.items() if v not in (None, "N/A")},
                **({"components": components} if components else {})}
    if other is not None:
        return {"security": _security(other)}
    if nested is not None:
        return {"nested": True}
    return None


def _leg(node: ET.Element) -> dict:
    """One side of a swap's payments: fixed, floating, or other, with what the report gives of it."""
    tag = _local(node.tag)
    kind = "fixed" if tag.startswith("fixed") else "floating" if tag.startswith("floating") else "other"
    out = {"kind": kind, "currency": node.get("curCd"), "rate": _number(node.get("fixedRt")),
           "index": node.get("floatingRtIndex"), "spread": _number(node.get("floatingRtSpread")),
           "description": node.get("desc") or (node.text.strip() if node.text and node.text.strip() else None)}
    return {k: v for k, v in out.items() if v not in (None, "N/A")}


def derivative(info: ET.Element) -> dict:
    """A derivative's terms as Form N-PORT Item C.11 gives them, under one shape: its category (`FWD` a forward, `FUT`
    a future, `SWP` a swap, `OPT` an option, `SWO` a swaption, `WAR` a warrant, `OTH` another), its reference, its
    notional (signed as the report signs it) and the currency it is in; a future's payoff profile; a swap's legs, what
    the fund receives and what it pays; a forward's currencies and amounts sold (negative) and bought; an option's or
    a warrant's right, side, underlying per contract, strike and expiry, its delta where the report gives one (the
    public report does not: the SEC withholds it, Form N-PORT's General Instructions); and its unrealized appreciation.
    Each is as filed. `underlying_per_contract` is `shareNo`, which the form asks as the "number of shares or principal
    amount of underlying reference instrument per contract" (Item C.11.c.iv, https://www.sec.gov/files/formn-port.pdf);
    filers also state it as the total over every contract, signed (read live, Global X's QYLD: -289,300 for 2,893
    written NDX calls), and a filed strike can be wrong at its source (the same row's `exercisePrice` 550, where its
    title and its ticker identifier, "IXQ26C Y 28550.00 US Index", name a strike of 28,550)."""
    node = next(iter(info), None)
    if node is None:
        return {}
    out: dict[str, Any] = {"category": node.get("derivCat"), "reference": _reference(_child(node, "descRefInstrmnt")),
                           "notional": _number(_text(node, "notionalAmt")), "currency": _text(node, "curCd"),
                           "unrealized": _number(_text(node, "unrealizedAppr"))}
    kind = _local(node.tag)
    if kind == "futrDeriv":
        out.update({"payoff_profile": _text(node, "payOffProf"), "expiry": _text(node, "expDate")})
    elif kind == "swapDeriv":
        legs = [c for c in node if _local(c.tag).endswith(("RecDesc", "PmntDesc"))]
        out.update({"receive": next((_leg(c) for c in legs if _local(c.tag).endswith("RecDesc")), None),
                    "pay": next((_leg(c) for c in legs if _local(c.tag).endswith("PmntDesc")), None),
                    "termination": _text(node, "terminationDt")})
    elif kind == "fwdDeriv":
        out.update({"sold": {"currency": _text(node, "curSold"), "amount": _number(_text(node, "amtCurSold"))},
                    "bought": {"currency": _text(node, "curPur"), "amount": _number(_text(node, "amtCurPur"))},
                    "settlement": _text(node, "settlementDt")})
    elif kind == "optionSwaptionWarrantDeriv":
        right, side = _text(node, "putOrCall"), _text(node, "writtenOrPur")
        out.update({"right": right.lower() if right else None, "side": side.lower() if side else None,
                    "underlying_per_contract": _number(_text(node, "shareNo")),
                    "strike": _number(_text(node, "exercisePrice")),
                    "strike_currency": _text(node, "exercisePriceCurCd"), "expiry": _text(node, "expDt"),
                    "delta": _number(_text(node, "delta"))})
    elif kind == "othDeriv":
        out.update({"description": _text(node, "othDesc"), "termination": _text(node, "terminationDt"),
                    "delta": _number(_text(node, "delta"))})
    return {k: v for k, v in out.items() if v not in (None, "N/A")}


def holding(node: ET.Element) -> dict:
    """One `invstOrSec`, under the field names ETF Global's holdings use, with what N-PORT adds."""
    ids = _child(node, "identifiers")
    given = {_local(c.tag): c.get("value") for c in (ids if ids is not None else [])}
    asset, asset_label = _category(node, "assetCat", "assetConditional", ASSET_TYPES)
    issuer, issuer_label = _category(node, "issuerCat", "issuerConditional", ISSUER_TYPES)
    pct = _number(_text(node, "pctVal"))
    other = _child(node, "currencyConditional")  # a holding in another currency: its code, and dollars per unit of it
    currency = _text(node, "curCd") or (other.get("curCd") if other is not None else None)
    return {"constituent_name": _text(node, "name"), "title": _text(node, "title"),
            "constituent_ticker": (given.get("ticker") or "").strip().upper() or None,
            "isin": _identifier(given.get("isin"), 12), "cusip": _identifier(_text(node, "cusip"), 9),
            "lei": _text(node, "lei"), "weight": pct / 100 if pct is not None else None,
            "asset_class": asset_label, "security_type": issuer_label,
            "asset_category": asset, "issuer_category": issuer,
            "market_value": _number(_text(node, "valUSD")), "shares": _number(_text(node, "balance")),
            "units": _text(node, "units"), "currency": currency,
            "exchange_rate": _number(other.get("exchangeRt")) if other is not None else None,
            "country_of_issuer": _text(node, "invCountry"), "payoff_profile": _text(node, "payoffProfile"),
            **({"derivative": derivative(found)} if (found := _child(node, "derivativeInfo")) is not None else {})}


def report(xml: bytes | str) -> dict:
    """A Form N-PORT report's date, series, net assets and holdings."""
    root = ET.fromstring(xml)
    form = next((n for n in root.iter() if _local(n.tag) == "formData"), root)
    gen, fund = _child(form, "genInfo"), _child(form, "fundInfo")
    holdings = _child(form, "invstOrSecs")
    effective = _text(gen, "repPdDate")  # each holding is as of it, as each of ETF Global's rows is
    return {"effective_date": effective, "period_end": _text(gen, "repPdEnd"),
            "series": _text(gen, "seriesId"), "net_assets": _number(_text(fund, "netAssets")),
            "results": [{**holding(n), "effective_date": effective}
                        for n in (holdings if holdings is not None else []) if _local(n.tag) == "invstOrSec"]}


# Form N-CEN, the annual census of a registered fund (https://www.sec.gov/files/formn-cen.pdf): Item C.3 asks each
# series which types of fund it is, and its box (c) is a fund that "Seeks to achieve performance results that are a
# multiple of an index or other benchmark, the inverse of an index or other benchmark, or a multiple of the inverse of
# an index or other benchmark". In the filing's XML each series is a `managementInvestmentQuestion` naming its
# `mgmtInvSeriesId`, its types each a `fundTypes/fundType`, and box (c) is the value MULTIPLE: read in ProShares Trust's
# census of 2026-08-14 (0002071691-26-017775), which gives it to 102 of its 151 series, its 2x long funds among them
# as well as its inverse ones. The box states neither the multiple nor how often the fund resets to it: the fund's
# daily returns on its reference say what it pays (harness.derive.rows.measure). The census is filed once a year, within
# 75 days of the fiscal year's end, so a fund in its first year has none.
MULTIPLE = "Inverse of a benchmark"


def census(xml: bytes | str) -> dict:
    """Each series' fund types, as a census filing gives them: `{series: {series id: [fund type, ...]}}`."""
    root = ET.fromstring(xml)
    found: dict[str, list[str]] = {}
    for q in root.iter():
        if _local(q.tag) != "managementInvestmentQuestion":
            continue
        sid = _text(q, "mgmtInvSeriesId")
        types = [n.text.strip() for n in q.iter() if _local(n.tag) == "fundType" and n.text]
        if sid:
            found[sid] = types
    return {"series": found}


def filings(feed: str) -> list[dict]:
    """The filings an EDGAR company feed lists, newest first: each one's form, filing date, accession number and CIK."""
    root = ET.fromstring(feed)
    out = []
    for entry in (n for n in root.iter() if _local(n.tag) == "entry"):
        content = _child(entry, "content")
        href = _text(content, "filing-href") or ""
        cik = re.search(r"/edgar/data/(\d+)/", href)
        out.append({"form": _text(content, "filing-type"), "filed": _text(content, "filing-date"),
                    "accession": _text(content, "accession-number"), "cik": cik.group(1) if cik else None})
    return [f for f in out if f["form"] and f["filed"] and f["accession"] and f["cik"]]


class SEC:
    def __init__(self, *, http: httpx.Client | None = None, interval: float = INTERVAL, archive: Any = None):
        self.http = http or client()
        self.archive = archive  # this machine's observations: a filed report, by its accession, is read once on it
        self.interval = interval
        self.cooldown = Cooldown("The SEC")
        self._slot = threading.Lock()
        self._last = 0.0
        self._lists: dict[str, tuple[str, Any]] = {}  # each list of tickers, by URL, with the day it was read

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        """One request, its interval after the one before; where the SEC does not answer, it is not asked again for a
        minute."""
        self.cooldown.check()
        with self._slot:
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            response = self.http.get(url, params=params)
        except httpx.HTTPError as e:
            self.cooldown.failed()
            raise VenueError(f"the SEC: {e}") from None
        if response.status_code != 200:
            self.cooldown.failed()
            raise VenueError(f"the SEC answered {response.status_code}")
        return response

    def _json(self, url: str) -> Any:
        try:
            return self._get(url).json()
        except ValueError:
            raise VenueError("the SEC answered with no list") from None

    def _list(self, url: str) -> Any:
        """A list of tickers, read once a day."""
        today = date.today().isoformat()
        if url not in self._lists or self._lists[url][0] != today:
            self._lists[url] = (today, self._json(url))
        return self._lists[url][1]

    def funds(self, day: str) -> dict:
        """The funds' list, now (FUNDS): every open-end fund's and ETF's share class by its ticker, with its series and
        its trust's CIK, `{"fields": ["cik", "seriesId", "classId", "symbol"], "data": [...]}` (read live: 28,641 classes,
        1.35 MB). A mutual fund's share class has a ticker here and none in a listing's reference."""
        retrieved = now()
        body = self._json(FUNDS)
        return {"payload": {"status": "OK", **body}, "request": {"method": "GET", "url": FUNDS},
                "retrieved_at": retrieved, "range": (day, day)}

    def tickers(self, day: str) -> dict:
        """The list, now."""
        retrieved = now()
        body = self._json(URL)
        return {"payload": {"status": "OK", **body}, "request": {"method": "GET", "url": URL},
                "retrieved_at": retrieved, "range": (day, day)}

    def filer(self, fund: str) -> tuple[str | None, str | None]:
        """The series a fund files N-PORT as, from the funds' list, or else the CIK the list of tickers gives it."""
        symbol = fund.upper()
        listed = self._list(FUNDS)
        at = {f: i for i, f in enumerate(listed.get("fields") or [])}
        for row in listed.get("data") or []:
            if str(row[at["symbol"]]).upper() == symbol and row[at["seriesId"]]:
                return str(row[at["seriesId"]]), str(row[at["cik"]])
        companies = self._list(URL)
        at = {f: i for i, f in enumerate(companies.get("fields") or [])}
        for row in companies.get("data") or []:
            if str(row[at["ticker"]]).upper() == symbol:
                return None, str(row[at["cik"]])
        return None, None

    def nport(self, fund: str, asof: str) -> dict:
        """The fund's latest Form N-PORT holdings filed by `asof`, its restatement filed by then included."""
        retrieved = now()
        series, cik = self.filer(fund)
        before = (date.fromisoformat(asof) + timedelta(days=1)).strftime("%Y%m%d")  # filings on the as-of included
        params = {"action": "getcompany", "CIK": series or cik, "type": "NPORT-P", "dateb": before,
                  "owner": "include", "count": FILINGS, "output": "atom"}
        request = {"method": "GET", "url": FEED, "params": params}

        def none(said: str) -> dict:
            return {"payload": {"status": "NOT_FOUND", "fund": fund, "error": said}, "request": request,
                    "retrieved_at": retrieved, "range": (asof, asof)}

        if not (series or cik):
            return none(f"the SEC lists no fund under {fund}")
        try:
            listed = filings(self._get(FEED, params).text)
        except ET.ParseError:
            raise VenueError("the SEC answered its filings feed with no feed") from None
        listed = [f for f in listed if f["form"] in ("NPORT-P", "NPORT-P/A") and f["filed"] <= asof]
        originals = [f for f in listed if f["form"] == "NPORT-P"]
        if not originals:
            return none(f"{fund} filed no Form N-PORT by {asof}")
        latest = max(originals, key=lambda f: (f["filed"], f["accession"]))
        chosen, found = latest, self._report(latest)
        for amendment in sorted((f for f in listed if f["form"] == "NPORT-P/A" and f["filed"] >= latest["filed"]),
                                key=lambda f: (f["filed"], f["accession"]), reverse=True):
            restated = self._report(amendment)
            if restated["effective_date"] == found["effective_date"]:
                chosen, found = amendment, restated
                break
        return {"payload": {"status": "OK", "fund": fund, "cik": chosen["cik"], "series": series,
                            "form": chosen["form"], "accession": chosen["accession"], "filed": chosen["filed"],
                            "url": self._document(chosen), **found},
                "request": request, "retrieved_at": retrieved, "range": (asof, asof)}

    def money_market(self, fund: str, asof: str) -> dict:
        """Whether a fund, mutual or exchange-traded, is a money market fund, as the SEC's filings say: a money market
        fund files Form N-MFP each month (Rule 2a-7; N-MFP, N-MFP2, and since 2024 N-MFP3), and EDGAR's feed of the
        fund's series, asked for `type=N-MFP`, lists every one of them, the type matching by its prefix (read live: a
        money market fund's series answered N-MFP3 filings, an index fund's none). By the as-of: a series that had
        filed none by then reads as none then, and since the first reports were filed in December 2010, none does
        before. The feed asked for a type lists its filings in no order of date (read live: the ten it gave for one
        series ran from 2011 on one read and from 2026 on another), so whether it lists any is what is read, with the
        filings it listed. A ticker the SEC's list of funds gives no series for is answered `NOT_FOUND`."""
        retrieved = now()
        series, _ = self.filer(fund)
        before = (date.fromisoformat(asof) + timedelta(days=1)).strftime("%Y%m%d")
        params = {"action": "getcompany", "CIK": series, "type": "N-MFP", "dateb": before, "owner": "include",
                  "count": 10, "output": "atom"}
        request = {"method": "GET", "url": FEED, "params": params}
        if not series:
            return {"payload": {"status": "NOT_FOUND", "fund": fund,
                                "error": f"the SEC lists no fund series under {fund}"},
                    "request": {"method": "GET", "url": FUNDS}, "retrieved_at": retrieved, "range": (asof, asof)}
        try:
            listed = filings(self._get(FEED, params).text)
        except ET.ParseError:
            raise VenueError("the SEC answered its filings feed with no feed") from None
        reports = sorted((f for f in listed if str(f["form"]).startswith("N-MFP") and f["filed"] <= asof),
                         key=lambda f: f["filed"])
        return {"payload": {"status": "OK", "fund": fund, "series": series, "money_market": bool(reports),
                            "filings": [{k: f[k] for k in ("form", "filed")} for f in reports]},
                "request": request, "retrieved_at": retrieved, "range": (asof, asof)}

    def ncen(self, fund: str, asof: str) -> dict:
        """The fund's latest Form N-CEN filed by `asof` that answers for its series (an amendment filed after the
        original standing in its place): its fund types, and whether it seeks a multiple, or the inverse, of a
        benchmark (MULTIPLE). A fund with no census filed by then, or one the SEC lists no fund for, is NOT_FOUND."""
        retrieved = now()
        series, cik = self.filer(fund)
        before = (date.fromisoformat(asof) + timedelta(days=1)).strftime("%Y%m%d")
        params = {"action": "getcompany", "CIK": series or cik, "type": "N-CEN", "dateb": before, "owner": "include",
                  "count": 10, "output": "atom"}
        request = {"method": "GET", "url": FEED, "params": params}

        def none(said: str) -> dict:
            return {"payload": {"status": "NOT_FOUND", "fund": fund, "error": said}, "request": request,
                    "retrieved_at": retrieved, "range": (asof, asof)}

        if not (series or cik):
            return none(f"the SEC lists no fund under {fund}")
        try:
            listed = filings(self._get(FEED, params).text)
        except ET.ParseError:
            raise VenueError("the SEC answered its filings feed with no feed") from None
        listed = sorted((f for f in listed if f["form"] in ("N-CEN", "N-CEN/A") and f["filed"] <= asof),
                        key=lambda f: (f["filed"], f["accession"]), reverse=True)
        for filing in listed:
            found = self._census(filing)
            types = found["series"].get(series) if series else []
            if types is None:
                continue  # a census of the trust that does not answer for this series
            return {"payload": {"status": "OK", "fund": fund, "series": series, "cik": filing["cik"],
                                "form": filing["form"], "accession": filing["accession"], "filed": filing["filed"],
                                "fund_types": types, "seeks_multiple": MULTIPLE in types,
                                "url": self._document(filing)},
                    "request": request, "retrieved_at": retrieved, "range": (asof, asof)}
        return none(f"{fund} filed no Form N-CEN by {asof}")

    def _census(self, filing: dict) -> dict:
        """A census filing read once on this machine: a filing does not change."""
        span = (filing["filed"], filing["filed"])
        subject = f"{filing['accession']}#{PARSE}"
        if self.archive is not None:
            kept = self.archive.answer("ncen_report", subject, span)
            if kept is not None:
                return kept["payload"]
        retrieved = now()
        try:
            found = census(self._get(self._document(filing)).content)
        except ET.ParseError:
            raise VenueError(f"the SEC answered {filing['accession']} with no census") from None
        if self.archive is not None:
            self.archive.keep("ncen_report", subject, span,
                              {"payload": found, "request": {"method": "GET", "url": self._document(filing)},
                               "retrieved_at": retrieved, "range": span})
        return found

    def filed(self, cik: str, day: str) -> dict:
        """A filer's filings, as EDGAR's submissions record lists them by CIK, the most recent first
        (https://www.sec.gov/search-filings/edgar-application-programming-interfaces: `GET
        https://data.sec.gov/submissions/CIK##########.json`, `filings.recent` a column per field, read live): each its
        form, filing date, period, accession number and primary document."""
        retrieved = now()
        number = str(int(cik)).zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{number}.json"
        body = self._json(url)
        r = (body.get("filings") or {}).get("recent") or {}
        rows = [{"form": r["form"][i], "filed": r["filingDate"][i], "period": r["reportDate"][i] or None,
                 "accession": r["accessionNumber"][i], "document": r["primaryDocument"][i]}
                for i in range(len(r.get("form") or []))]
        return {"payload": {"status": "OK", "cik": number, "name": body.get("name"), "filings": rows},
                "request": {"method": "GET", "url": url}, "retrieved_at": retrieved, "range": (day, day)}

    def filing(self, subject: str, day: str) -> dict:
        """One filing's text, `{cik}/{accession}/{document}`: its primary document, with its XBRL header left out, and
        after it each exhibit 99 it furnishes in HTML or text, under its name (the filing's `index.json` lists them): a
        6-K's press releases and reports, and a 40-F's annual information form and financial statements, which that
        form files as exhibits to a cover."""
        from harness.web import html_text
        retrieved = now()
        cik, accession, document = subject.split("/", 2)
        base = f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}"
        page = re.sub(r"(?is)<ix:header>.*?</ix:header>", "", self._get(f"{base}/{document}").text)  # its XBRL facts
        text = html_text(page)
        index = self._json(f"{base}/index.json")
        exhibits = sorted(i["name"] for i in (index.get("directory") or {}).get("item") or []
                          if re.search(r"(?i)ex-?99", i.get("name", "")) and i["name"].lower().endswith((".htm", ".html", ".txt")))
        for name in exhibits:
            text += f"\n\n## {name}\n\n" + html_text(self._get(f"{base}/{name}").text)
        return {"payload": {"status": "OK", "url": f"{base}/{document}", "text": text, "exhibits": exhibits},
                "request": {"method": "GET", "url": f"{base}/{document}"}, "retrieved_at": retrieved,
                "range": (day, day)}

    def _document(self, filing: dict) -> str:
        return f"{ARCHIVES}/{filing['cik']}/{filing['accession'].replace('-', '')}/primary_doc.xml"

    def _report(self, filing: dict) -> dict:
        """A filed report's holdings, read once on this machine in this parse's form (PARSE): a filing does not
        change (a restatement is a filing of its own)."""
        span = (filing["filed"], filing["filed"])
        subject = f"{filing['accession']}#{PARSE}"
        if self.archive is not None:
            kept = self.archive.answer("nport_report", subject, span)
            if kept is not None:
                return kept["payload"]
        retrieved = now()
        try:
            found = report(self._get(self._document(filing)).content)
        except ET.ParseError:
            raise VenueError(f"the SEC answered {filing['accession']} with no report") from None
        if self.archive is not None:
            self.archive.keep("nport_report", subject, span,
                              {"payload": found, "request": {"method": "GET", "url": self._document(filing)},
                               "retrieved_at": retrieved, "range": span})
        return found

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        return {"sec_tickers": lambda subject, covering: self.tickers(covering[1]),
                "sec_funds": lambda subject, covering: self.funds(covering[1]),
                "nport": lambda subject, covering: self.nport(subject, covering[1]),
                "money_market": lambda subject, covering: self.money_market(subject, covering[1]),
                "ncen": lambda subject, covering: self.ncen(subject, covering[1]),
                "sec_filings": lambda subject, covering: self.filed(subject, covering[1]),
                "sec_document": lambda subject, covering: self.filing(subject, covering[1])}
