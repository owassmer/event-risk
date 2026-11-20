"""A fund's holdings as its issuer publishes them (harness.venues.issuers): Vanguard's own file, read by the fund's
ticker or by another class of its series, each row as the file states it, with what the file leaves of the fund's net
assets as one row of cash; nothing where Vanguard keeps no such fund or its file is dated after the as-of, so that the
look-through reads the fund's report at the SEC."""

from __future__ import annotations

from harness.venues import issuers
from harness.venues.issuers import Issuers, net_assets

STOCKS = [
    {"longName": "Alpha Corp", "ticker": "ALP", "isin": "US0000000011", "cusip": "000000001", "sedol": "B000001",
     "sharesHeld": "6", "marketValue": "600", "percentWeight": "60.00"},
    {"longName": "Beta plc", "ticker": "BET", "isin": "GB0000000022", "cusip": "", "sedol": "B000002",
     "sharesHeld": "30", "marketValue": "300", "percentWeight": "30.00"},
    {"longName": "Gamma KK", "ticker": "2330", "isin": "JP0000000033", "cusip": None, "sedol": "B000003",
     "sharesHeld": "5", "marketValue": "50", "percentWeight": "5.00"},
]


class Answer:
    def __init__(self, status: int, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("an error page, not JSON")
        return self._body


class Vanguard:
    """Vanguard's file: the stocks of the classes it keeps, a page at a time; a 404 for a kind a fund has none of; an
    error page in place of JSON for a class it keeps the fund under no file of."""

    def __init__(self, kept: dict[str, str]):
        self.kept, self.asked = kept, []  # each class it keeps a file under, with the file's date

    def get(self, url: str, params: dict):
        ticker, kind = url.split("/api/")[1].split("/portfolio-holding/")
        self.asked.append((ticker, kind, params["start"]))
        if ticker not in self.kept:
            return Answer(200)
        if kind != "stock.json":
            return Answer(404)
        start = params["start"] - 1
        return Answer(200, {"size": len(STOCKS), "asOfDate": self.kept[ticker] + "T00:00:00",
                            "fund": {"entity": STOCKS[start:start + params["count"]]}})


class Sec:
    """The SEC's list of funds' share classes: VXUS and VGTSX are classes of one series."""

    def _list(self, url: str) -> dict:
        return {"fields": ["cik", "seriesId", "classId", "symbol"],
                "data": [[1, "S1", "C1", "VTI"], [2, "S2", "C2", "VXUS"], [2, "S2", "C3", "VGTSX"], [3, "S3", "C4", "SPY"]]}


def test_a_vanguard_funds_holdings_are_its_files_rows_each_as_stated_with_the_rest_of_its_net_assets_as_cash(monkeypatch):
    """MATH.md 3.1: a fund's rows are what it holds, each with the date its source states. Every page is read; a row keeps the
    ticker, ISIN, CUSIP and SEDOL the file states, and its weight is its value over the fund's net assets, which the
    largest holdings' values and weights give; what the file leaves of the net assets is one row of cash."""
    monkeypatch.setattr(issuers, "PAGE", 2)  # three holdings in two pages
    http = Vanguard({"VTI": "2026-10-31"})
    got = Issuers(Sec(), http=http).constituents("vti", "2026-11-19")
    found = got["payload"]
    assert (found["status"], found["source"], found["read_as"], found["effective_date"]) == \
        ("OK", "Vanguard", "VTI", "2026-10-31")
    assert got["range"] == ("2026-11-19", "2026-11-19")
    assert [(s, p) for t, k, p in http.asked if k == "stock.json" for s in [t]] == [("VTI", 1), ("VTI", 3)]
    rows = found["results"]
    assert [r.get("constituent_ticker") for r in rows] == ["ALP", "BET", "2330", None]
    assert {k: v for k, v in rows[0].items() if k != "weight"} == {
        "composite_ticker": "VTI", "constituent_ticker": "ALP", "constituent_name": "Alpha Corp",
        "isin": "US0000000011", "us_code": "000000001", "sedol": "B000001", "market_value": 600.0,
        "shares_held": 6.0, "asset_class": "Equity", "effective_date": "2026-10-31"}
    assert "us_code" not in rows[1] and "us_code" not in rows[2]  # a CUSIP the file leaves blank is none
    assert abs(found["net_assets"] - 1000.0) < 1e-6  # 950 of value at 95% of the fund
    assert [round(r["weight"], 9) for r in rows] == [0.6, 0.3, 0.05, 0.05]
    assert rows[3]["asset_class"] == "Cash" and round(rows[3]["market_value"], 6) == 50.0
    assert round(sum(r["weight"] for r in rows), 9) == 1.0


def test_a_fund_vanguard_keeps_under_another_class_is_read_by_that_class_of_its_series():
    """VXUS's file is kept under the fund's other class: the classes the SEC lists under its series are asked in turn,
    and the rows are the fund's own."""
    http = Vanguard({"VGTSX": "2026-10-31"})
    found = Issuers(Sec(), http=http).constituents("VXUS", "2026-11-19")["payload"]
    assert found["read_as"] == "VGTSX" and found["composite_ticker"] == "VXUS"
    assert {r["composite_ticker"] for r in found["results"]} == {"VXUS"}
    assert [t for t, _, _ in http.asked][:2] == ["VXUS", "VXUS"]  # its own ticker first, each kind


def test_no_holdings_come_from_vanguard_for_a_fund_it_does_not_keep_or_a_file_dated_after_the_as_of():
    """The look-through then reads the fund's report at the SEC: a fund of another issuer, and an as-of before the
    file's date, each answer nothing."""
    assert Issuers(Sec(), http=Vanguard({"VTI": "2026-10-31"})).constituents("SPY", "2026-11-19") is None
    assert Issuers(Sec(), http=Vanguard({"VTI": "2026-10-31"})).constituents("VTI", "2026-10-15") is None


def test_net_assets_come_from_the_largest_holdings_where_rounding_costs_least():
    """A weight is a percent to two decimals, so a small holding's rounds to nothing: only holdings of half a percent
    or more are used, and a file with none gives no net assets."""
    rows = [{"marketValue": "500", "percentWeight": "50.00"}, {"marketValue": "1", "percentWeight": "0.00"},
            {"marketValue": "4", "percentWeight": "0.40"}]
    assert net_assets(rows) == 1000.0
    assert net_assets(rows[1:]) is None
