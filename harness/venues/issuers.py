"""A fund's holdings as its issuer publishes them (MATH.md 3.1: "those published for it, as of the latest date on or before
`asof`, by a source that still publishes them"), each row as the issuer states it, in the shape ETF Global's rows are
kept in (harness.venues.massive, `constituents`), so that whatever reads a fund's holdings reads them the same way.
An exchange-traded fund posts its holdings each business day, free, on its website (SEC Rule 6c-11).

- **Vanguard:** `GET https://investor.vanguard.com/vmf/api/{TICKER}/portfolio-holding/{stock|bond}.json?start=&count=`,
  the file its own fund pages read: `{size, asOfDate, fund: {entity: [...]}}`, `size` the holdings of that kind and
  `entity` a page of at most 500, each `{longName, shortName, ticker, isin, cusip, sedol, sharesHeld, marketValue,
  percentWeight, asOfDate}` (and for a bond `couponRate`, `maturityDate`, `faceAmount`), the ticker its local one
  (`2330` for TSMC) and no exchange or country stated. Read live: VTI's 3,475 stocks as of Oct 31 in 7 pages, BND's
  10,083 bonds, each page in about 0.5 s, to a plain client. An exchange-traded class whose fund the file keeps under
  another class (VXUS's under VTIAX) answers an error page in place of JSON: the series' other classes, from the SEC's
  list of funds' share classes, are asked in turn. `percentWeight` is a percent to two decimals, which rounds a small
  holding to nothing, so a row's weight is its market value over the fund's net assets, those read from its largest
  holdings' values and weights. The two files list every
  security the fund holds, so what they leave of its net assets is its cash and short-term reserves, one row of cash.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import httpx

from harness.venues.http import VenueError, client, now

VANGUARD = "https://investor.vanguard.com/vmf/api/{ticker}/portfolio-holding/{kind}.json"
PAGE = 500  # the holdings a page of Vanguard's file holds at most
KINDS = {"stock": "Equity", "bond": "Fixed Income"}  # each file Vanguard keeps a fund's holdings in, and their class
TRIES = 3


def net_assets(rows: list[dict]) -> float | None:
    """A fund's net assets from its holdings' values and their weights as a percent to two decimals: the largest
    holdings' values over their weights, where the rounding costs least."""
    big = [r for r in rows if _number(r.get("percentWeight")) and _number(r.get("percentWeight")) >= 0.5
           and _number(r.get("marketValue"))]
    weight = sum(_number(r["percentWeight"]) for r in big)
    return sum(_number(r["marketValue"]) for r in big) / (weight / 100) if weight else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class Issuers:
    def __init__(self, sec: Any, *, http: httpx.Client | None = None):
        self.sec = sec
        self.http = http or client()

    def _get(self, url: str, params: dict) -> dict | None:
        """A page of Vanguard's file; None where the file keeps no fund under the ticker (an error page in place of
        JSON, or a 404)."""
        failure = "Vanguard did not answer"
        for _ in range(TRIES):
            try:
                r = self.http.get(url, params=params)
            except httpx.HTTPError as e:
                failure = f"Vanguard: {e}"
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    return None
                return body if isinstance(body, dict) and "fund" in body else None
            failure = f"Vanguard answered {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                return None
        raise VenueError(failure)

    def _kind(self, ticker: str, kind: str) -> tuple[str | None, list[dict]]:
        """One kind of a fund's holdings under a ticker, every page: their date, and the rows."""
        url = VANGUARD.format(ticker=ticker, kind=kind)
        first = self._get(url, {"start": 1, "count": PAGE})
        if first is None:
            return None, []
        rows = list(((first.get("fund") or {}).get("entity")) or [])
        size = int(first.get("size") or 0)
        starts = list(range(1 + PAGE, size + 1, PAGE))
        if starts:
            with ThreadPoolExecutor(4) as pool:
                for page in pool.map(lambda s: self._get(url, {"start": s, "count": PAGE}), starts):
                    rows += list((((page or {}).get("fund") or {}).get("entity")) or [])
        return str(first.get("asOfDate") or "")[:10] or None, rows

    def _classes(self, fund: str) -> list[str]:
        """The fund's ticker, then the other classes the SEC lists under its series."""
        from harness.venues.sec import FUNDS
        listed = self.sec._list(FUNDS)
        at = {f: i for i, f in enumerate(listed.get("fields") or [])}
        series = next((r[at["seriesId"]] for r in listed.get("data") or [] if str(r[at["symbol"]]).upper() == fund),
                      None)
        others = sorted({str(r[at["symbol"]]).upper() for r in listed.get("data") or []
                         if series and r[at["seriesId"]] == series} - {fund})
        return [fund] + others

    def vanguard(self, fund: str, asof: str) -> dict | None:
        """The fund's holdings as Vanguard publishes them, as of their date, where Vanguard keeps the fund and its date
        is on or before the as-of; None otherwise."""
        for ticker in self._classes(fund.upper()):
            read = {kind: self._kind(ticker, kind) for kind in KINDS}
            dated = {kind: (day, rows) for kind, (day, rows) in read.items() if day}
            if not dated:
                continue
            day = max(d for d, _ in dated.values())
            rows = [(kind, r) for kind, (d, found) in dated.items() if d == day for r in found]
            if not rows or day > asof:
                return None
            net = net_assets([r for _, r in rows])
            results = []
            for kind, r in rows:
                value = _number(r.get("marketValue"))
                weight = value / net if value is not None and net else (_number(r.get("percentWeight")) or 0) / 100
                results.append({k: v for k, v in {
                    "composite_ticker": fund.upper(), "constituent_ticker": (r.get("ticker") or "").strip() or None,
                    "constituent_name": r.get("longName") or r.get("shortName"),
                    "isin": (r.get("isin") or "").strip() or None, "us_code": (r.get("cusip") or "").strip() or None,
                    "sedol": (r.get("sedol") or "").strip() or None, "weight": weight, "market_value": value,
                    "shares_held": _number(r.get("sharesHeld")), "asset_class": KINDS[kind],
                    "effective_date": day}.items() if v is not None})
            listed = sum(r["weight"] for r in results)
            if net and 0 < 1 - listed < 1:  # what its files leave of its net assets is its cash and short-term reserves
                results.append({"composite_ticker": fund.upper(), "constituent_name": "Cash and short-term reserves",
                                "weight": 1 - listed, "market_value": net * (1 - listed), "asset_class": "Cash",
                                "effective_date": day})
            return {"status": "OK", "composite_ticker": fund.upper(), "effective_date": day, "source": "Vanguard",
                    "read_as": ticker, "net_assets": net, "results": results}
        return None

    def constituents(self, fund: str, asof: str) -> dict | None:
        """A fund's holdings from the first issuer here that publishes them by the as-of; None where none does."""
        found = self.vanguard(fund, asof)
        if found is None:
            return None
        return {"payload": found, "request": {"method": "GET", "url": VANGUARD.format(ticker=found["read_as"],
                                                                                      kind="{stock,bond}")},
                "retrieved_at": now(), "range": (asof, asof)}
