"""Each currency's government yield curve, from the institution that publishes it (MATH.md 1.6, 3.1: cash paid on a date
is discounted at its currency's government yield to that date). The dollar's is the Treasury's par curve
(harness.venues.treasury); this client reads the others, each kept as `yield_curve` data under `{CURRENCY}:{year}`,
`{status, currency, year, basis, results: [{date, curve: [[years, yield]]}]}`, each yield a decimal as its source
states it, and `basis` saying how a point discounts (harness.derive.rates.discount): "par", the annual coupon, paid
half-yearly, of a bond priced at par; "spot", the continuously compounded rate of a single payment.

- The euro: the ECB's euro area yield curve, the spot rates of the triple-A rated central governments' bonds, by the
  Svensson model, continuously compounded (its series title: "Yield curve spot rate, ... Government bond, nominal, all
  issuers whose rating is triple A - Svensson model - continuous compounding"), each business day.
  `GET https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{SR_3M+...+SR_30Y}?startPeriod=
  &endPeriod=&format=csvdata&detail=dataonly` (https://data.ecb.europa.eu/help/api/data) answers CSV rows of
  `DATA_TYPE_FM` (SR_3M ... SR_30Y), `TIME_PERIOD` and `OBS_VALUE` in percent; read live, a week of it 4.6 KB in 8 s.
- The yen: Japan's Ministry of Finance's interest rates of Japanese government bonds, 1 to 40 years, each business day
  (https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/index.htm): the current month's
  `.../interest_rate/jgbcme.csv` and every earlier day's `.../interest_rate/historical/jgbcme_all.csv` (read live,
  1.2 MB, from 1974), each a title line, then `Date,1Y,...,40Y`, dates as `2026/11/2`, a tenor not quoted as `-`.
  A bond's yield, its coupon paid half-yearly: "par".
- The Canadian dollar: the Bank of Canada's Valet (https://www.bankofcanada.ca/valet/docs): its benchmark bond yields
  (`/valet/observations/group/bond_yields_benchmark/json`, 2, 3, 5, 7 and 10 years, and long, taken as 30, each
  business day) and its Treasury bills' yields (`/valet/observations/group/tbill_all/json`, 1, 3, 6 and 12 months,
  each week's), `{observations: [{d, SERIES: {v}}]}` in percent; a day's curve is its bonds and the bills of the latest
  week on or before it. "par".

A currency no source here reads raises Unavailable: its read cannot be obtained, as any reading the store could not
obtain, and nothing stands in for it.
"""

from __future__ import annotations

import csv
import io
import ssl
from datetime import date
from typing import Any, Callable

import httpx
import truststore

from harness.derive.engine import Unavailable
from harness.venues.http import USER_AGENT, Cooldown, VenueError, now

ECB = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{}"
ECB_TENORS = {"SR_3M": 0.25, "SR_6M": 0.5, "SR_9M": 0.75, "SR_1Y": 1, "SR_2Y": 2, "SR_3Y": 3, "SR_5Y": 5, "SR_7Y": 7,
              "SR_10Y": 10, "SR_15Y": 15, "SR_20Y": 20, "SR_30Y": 30}
MOF = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
MOF_ALL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
BOC = "https://www.bankofcanada.ca/valet/observations/group/{}/json"
BOC_BONDS = {"BD.CDN.2YR.DQ.YLD": 2, "BD.CDN.3YR.DQ.YLD": 3, "BD.CDN.5YR.DQ.YLD": 5, "BD.CDN.7YR.DQ.YLD": 7,
             "BD.CDN.10YR.DQ.YLD": 10, "BD.CDN.LONG.DQ.YLD": 30}
BOC_BILLS = {"V80691342": 1 / 12, "V80691344": 0.25, "V80691345": 0.5, "V80691346": 1}  # "Treasury bills", weekly
BASIS = {"EUR": "spot", "JPY": "par", "CAD": "par"}
TIMEOUT = 30.0


def parse_ecb(text: str) -> dict[str, list]:
    """The ECB's CSV as each day's curve."""
    days: dict[str, dict[float, float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        tenor = ECB_TENORS.get(row.get("DATA_TYPE_FM") or "")
        if tenor is not None and row.get("OBS_VALUE") not in (None, ""):
            days.setdefault(row["TIME_PERIOD"], {})[tenor] = round(float(row["OBS_VALUE"]) / 100, 8)
    return {d: sorted([t, y] for t, y in c.items()) for d, c in days.items()}


def parse_mof(text: str) -> dict[str, list]:
    """The Ministry of Finance's CSV as each day's curve."""
    lines = [r for r in csv.reader(io.StringIO(text)) if r]
    head = next((i for i, r in enumerate(lines) if r and r[0].strip() == "Date"), None)
    if head is None:
        return {}
    tenors = [float(c.strip().rstrip("Y")) if c.strip().endswith("Y") else None for c in lines[head]]
    out = {}
    for r in lines[head + 1:]:
        try:
            y, m, d = (int(x) for x in r[0].strip().split("/"))
        except ValueError:
            continue
        curve = [[tenors[i], round(float(v) / 100, 8)] for i, v in enumerate(r) if i and i < len(tenors)
                 and tenors[i] is not None and v.strip() not in ("", "-")]
        if curve:
            out[date(y, m, d).isoformat()] = curve
    return out


def parse_boc(bonds: dict, bills: dict) -> dict[str, list]:
    """The Bank of Canada's bonds of each day with the bills of the latest week on or before it."""
    weeks = sorted((o["d"], {BOC_BILLS[k]: float(v["v"]) / 100 for k, v in o.items()
                             if k in BOC_BILLS and isinstance(v, dict) and v.get("v") not in (None, "")})
                   for o in bills.get("observations") or [])
    out = {}
    for o in bonds.get("observations") or []:
        curve = {BOC_BONDS[k]: float(v["v"]) / 100 for k, v in o.items()
                 if k in BOC_BONDS and isinstance(v, dict) and v.get("v") not in (None, "")}
        week = [b for d, b in weeks if d <= o["d"] and b]
        if curve:
            out[o["d"]] = sorted([t, round(y, 8)] for t, y in {**(week[-1] if week else {}), **curve}.items())
    return out


class Curves:
    """The government yield curves of the currencies other than the dollar."""

    def __init__(self, *, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT},
                                         verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        self.cooldown = Cooldown("the government yield curves")

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        self.cooldown.check()
        try:
            response = self.http.get(url, params=params)
        except httpx.HTTPError as e:
            self.cooldown.failed()
            raise VenueError(f"{url}: {e}") from None
        if response.status_code == 404:
            return response
        if response.status_code != 200:
            self.cooldown.failed()
            raise VenueError(f"{url} answered {response.status_code}")
        return response

    def curve(self, subject: str, asof: str) -> dict:
        """`{CURRENCY}:{year}`: that year's curves, through the as-of."""
        currency, _, year = subject.partition(":")
        currency = currency.upper()
        if currency not in BASIS or not year.isdigit():
            raise Unavailable(f"{currency}'s government yield curve is not read by any source here")
        retrieved = now()
        start, end = f"{year}-01-01", min(f"{year}-12-31", asof)
        if currency == "EUR":
            url = ECB.format("+".join(ECB_TENORS))
            params = {"startPeriod": start, "endPeriod": end, "format": "csvdata", "detail": "dataonly"}
            got = self._get(url, params)
            days = parse_ecb(got.text) if got.status_code == 200 else {}
            request = {"method": "GET", "url": url, "params": params}
        elif currency == "JPY":
            days = {}
            for url in (MOF_ALL, MOF):
                got = self._get(url)
                if got.status_code == 200:
                    days.update(parse_mof(got.content.decode("utf-8", errors="replace")))
            request = {"method": "GET", "url": MOF, "also": MOF_ALL}
            url = MOF
        else:
            params = {"start_date": start, "end_date": end}
            bonds = self._get(BOC.format("bond_yields_benchmark"), params).json()
            bills = self._get(BOC.format("tbill_all"), {"start_date": f"{int(year) - 1}-12-01", "end_date": end}).json()
            days = parse_boc(bonds, bills)
            request = {"method": "GET", "url": BOC.format("bond_yields_benchmark"), "params": params,
                       "also": BOC.format("tbill_all")}
        results = [{"date": d, "curve": c} for d, c in sorted(days.items()) if start <= d <= end]
        return {"payload": {"status": "OK", "currency": currency, "year": year, "basis": BASIS[currency],
                            "results": results},
                "request": request, "retrieved_at": retrieved, "range": (asof, asof)}


def with_curves(treasury: Callable[[str, Any], dict], curves: Curves) -> Callable[[str, Any], dict]:
    """The `yield_curve` kind: a year alone is the Treasury's (the dollar's), `{CURRENCY}:{year}` another currency's."""
    def one(subject: str, covering: Any) -> dict:
        if subject.isdigit():
            return treasury(subject, covering)
        if subject.upper().startswith("USD:"):
            return treasury(subject.split(":", 1)[1], covering)
        return curves.curve(subject, covering[1])
    return one
