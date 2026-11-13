"""The US Treasury's record of the bills it auctions, from TreasuryDirect, and its daily par yield curve. The bills are
how a fund's holding is known to be a Treasury bill by its identifier, whatever words the fund's data gives it (ETF
Global gives a bill the asset class and security type it gives a thirty-year bond, `Fixed Income` and `US
GOVERNMENT`).

`GET https://www.treasurydirect.gov/TA_WS/securities/search?type=Bill&format=json&dateFieldName=maturityDate&startDate=
{YYYY-MM-DD}&endDate={YYYY-MM-DD}` (TreasuryDirect's Web APIs, Securities: `type`, `format` one of json, jsonp, xhtml,
`dateFieldName` the date field `startDate` and `endDate` bound, `issueDate` by default;
https://www.treasurydirect.gov/legal-information/developers/web-api-security/, whose content has moved to the
Treasury's API portal, https://api-community.fiscal.treasury.gov/) answers a JSON list, one object per auction, each
with `cusip`, `securityType` (`Bill`), `securityTerm` (`4-Week` ... `52-Week`, or a cash management bill's days),
`cashManagementBill`, `announcementDate`, `issueDate`, and `maturityDate` (each `YYYY-MM-DDT00:00:00`), among some
150 fields of the auction's terms and results; an empty list where none matches. A malformed request (a date that is
not one, a type that is not one) is answered 400 with `{"status": 400, "error": "Bad Request"}`, read live: a defect
of the request's, never an answer. An auction is listed from its announcement, days before the bill is issued.

The Fiscal Data API's Treasury Securities Auctions dataset gives the same auctions in a few fields
(https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/), and was read live as the lighter
source: on 2026-11-10 its newest bill was issued 2026-10-22, while TreasuryDirect listed those to be issued on
2026-11-12. A bill a fund bought in those weeks would read as a bond, so the bills are read from TreasuryDirect.

A bill is reopened under its CUSIP, so a CUSIP can have several auctions. A bill matures within 52 weeks of its issue,
and the holdings a look-through reads stand on a date before its as-of (ETF Global's latest effective date, days
before it, or the latest Form N-PORT report public by it, a quarter's end up to five months before), so the bills read
are those maturing from HELD days before the as-of to a year after it. Read live for the as-of 2026-11-09: 273
auctions of 108 bills, 1.85 MB, in 9.4 seconds; what is kept of each, its CUSIP, term and dates, some 30 KB.

The par yield curve, the rate an option's delta is worked out at where its market quotes none (harness.derive.options):
`GET https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve
&field_tdr_date_value={YYYY}` (the Treasury's interest rate XML feed,
https://home.treasury.gov/treasury-daily-interest-rate-xml-feed) answers an Atom feed, one `entry` a business day, its `m:properties` holding `d:NEW_DATE`
(`YYYY-MM-DDT00:00:00`) and each tenor's par yield in percent, `d:BC_1MONTH`, `d:BC_1_5MONTH`, `d:BC_2MONTH`,
`d:BC_3MONTH`, `d:BC_4MONTH`, `d:BC_6MONTH`, `d:BC_1YEAR`, `d:BC_2YEAR` ... `d:BC_30YEAR` (and `d:BC_30YEARDISPLAY`,
the same figure), a tenor not quoted that day absent or empty. Read live for 2026: 216 days, 334 KB. Kept as `{status,
year, results: [{date, curve: [[years, yield]]}]}`, each yield a decimal on the Treasury's own basis.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Callable

import httpx

from harness.venues.http import Cooldown, VenueError, VenueRefused, client, now

URL = "https://www.treasurydirect.gov/TA_WS/securities/search"
HELD = 200  # days: the oldest holdings a look-through reads stand at most this long before its as-of
TIMEOUT = 60.0  # seconds: an answer of some two megabytes was read in nine
CURVE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
TENOR = re.compile(r"BC_(\d+)(?:_(\d+))?(MONTH|YEAR)$")  # a tenor's field: BC_1_5MONTH is a month and a half


def _day(value: Any) -> str | None:
    return str(value)[:10] if value else None


class Treasury:
    def __init__(self, *, http: httpx.Client | None = None):
        self.http = http or client(TIMEOUT)
        self.cooldown = Cooldown("TreasuryDirect")

    def bills(self, asof: str) -> dict:
        """Every bill a fund's holdings read by `asof` can hold, each by its CUSIP, with its term and dates."""
        retrieved = now()
        start = (date.fromisoformat(asof) - timedelta(days=HELD)).isoformat()
        end = (date.fromisoformat(asof) + timedelta(days=366)).isoformat()
        params = {"type": "Bill", "format": "json", "dateFieldName": "maturityDate", "startDate": start, "endDate": end}
        self.cooldown.check()
        try:
            response = self.http.get(URL, params=params)
        except httpx.HTTPError as e:
            self.cooldown.failed()
            raise VenueError(f"TreasuryDirect: {e}") from None
        if response.status_code == 400:
            raise VenueRefused(f"TreasuryDirect refused our request for bills: {response.text[:200]}")
        if response.status_code != 200:
            self.cooldown.failed()
            raise VenueError(f"TreasuryDirect answered {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise VenueError("TreasuryDirect answered with no list") from None
        if not isinstance(body, list):
            raise VenueError("TreasuryDirect answered with no list")
        results = sorted(({"cusip": r["cusip"], "term": r.get("securityTerm"), "issue_date": _day(r.get("issueDate")),
                           "maturity_date": _day(r.get("maturityDate"))}
                          for r in body if isinstance(r, dict) and r.get("cusip") and r.get("securityType") == "Bill"),
                         key=lambda r: (r["maturity_date"] or "", r["cusip"], r["issue_date"] or ""))
        return {"payload": {"status": "OK", "results": results},
                "request": {"method": "GET", "url": URL, "params": params}, "retrieved_at": retrieved,
                "range": (asof, asof)}

    def yield_curve(self, year: str, asof: str) -> dict:
        """The Treasury's daily par yield curve over a year, each day's tenors in years with their yields."""
        retrieved = now()
        params = {"data": "daily_treasury_yield_curve", "field_tdr_date_value": year}
        self.cooldown.check()
        try:
            response = self.http.get(CURVE, params=params, follow_redirects=True)
        except httpx.HTTPError as e:
            self.cooldown.failed()
            raise VenueError(f"the Treasury's yield curve: {e}") from None
        if response.status_code != 200:
            self.cooldown.failed()
            raise VenueError(f"the Treasury answered {response.status_code} for its yield curve")
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            raise VenueError("the Treasury answered its yield curve with no feed") from None
        results = []
        for props in root.iter("{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties"):
            day, curve = None, []
            for field in props:
                name, text = field.tag.rsplit("}", 1)[-1], (field.text or "").strip()
                found = TENOR.match(name)
                if name == "NEW_DATE":
                    day = text[:10]
                elif found and text:
                    whole, part, unit = found.groups()
                    n = float(f"{whole}.{part}") if part else float(whole)
                    curve.append([n / 12 if unit == "MONTH" else n, round(float(text) / 100, 6)])
            if day and curve:
                results.append({"date": day, "curve": sorted(curve)})
        return {"payload": {"status": "OK", "year": year, "results": sorted(results, key=lambda r: r["date"])},
                "request": {"method": "GET", "url": CURVE, "params": params}, "retrieved_at": retrieved,
                "range": (asof, asof)}

    def fetchers(self) -> dict[str, Callable[[str, Any], dict]]:
        return {"bills": lambda subject, covering: self.bills(covering[1]),
                "yield_curve": lambda subject, covering: self.yield_curve(subject, covering[1])}
