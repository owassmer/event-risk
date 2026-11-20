"""Where a fund's filings are listed (harness.venues.sec): a fund filing under a CIK alone from EDGAR's submissions
record; a series from EDGAR's full-text search, whose hits come in no order of date, asked again where the SEC does not
answer; and whether a series is a money market fund, by the latest of its N-MFP and N-PORT filings."""

from __future__ import annotations

import httpx
import pytest

from harness.venues import sec
from harness.venues.http import VenueError, VenueRefused


def report(series: str | None, date: str, holdings: int = 1) -> str:
    rows = "".join(f"<invstOrSec><name>H{i}</name><cusip>000000000</cusip><pctVal>1</pctVal><valUSD>10</valUSD>"
                   f"<assetCat>EC</assetCat></invstOrSec>" for i in range(holdings))
    sid = f"<seriesId>{series}</seriesId>" if series else ""
    return (f'<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>'
            f"<genInfo><repPdDate>{date}</repPdDate>{sid}</genInfo><fundInfo><netAssets>1000</netAssets></fundInfo>"
            f"<invstOrSecs>{rows}</invstOrSecs></formData></edgarSubmission>")


# Newest first: a restatement of the June report, the June report, the March report.
SUBMITTED = {"filings": {"recent": {
    "form": ["NPORT-P/A", "NPORT-P", "10-Q", "NPORT-P"],
    "filingDate": ["2026-09-10", "2026-08-28", "2026-08-01", "2026-05-28"],
    "accessionNumber": ["0000000009-26-000003", "0000000009-26-000002", "0000000009-26-000099",
                        "0000000009-26-000001"],
    "reportDate": ["", "", "", ""], "primaryDocument": ["primary_doc.xml"] * 4}}}
REPORTS = {"000000000926000003": ("S000000001", "2026-06-30", 3), "000000000926000002": ("S000000001", "2026-06-30", 2),
           "000000000926000001": ("S000000001", "2026-03-31", 1)}


def hit(form: str, filed: str, accession: str) -> dict:
    return {"_id": f"{accession}:primary_doc.xml",
            "_source": {"adsh": accession, "form": form, "root_forms": [form.split("/")[0]], "file_date": filed,
                        "ciks": ["0000000009"]}}


# The series' filings, in no order of date, as the search gives them.
HITS = [hit("NPORT-P", "2026-05-28", "0000000009-26-000001"), hit("NPORT-P/A", "2026-09-10", "0000000009-26-000003"),
        hit("NPORT-P", "2026-08-28", "0000000009-26-000002")]
FUNDS = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[9, "S000000001", "C1", "ONE"]]}


def client(search=lambda: httpx.Response(200, json={"hits": {"total": {"value": 3}, "hits": HITS}}),
           series_in_report="S000000001"):
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == "/submissions/CIK0000000009.json":
            return httpx.Response(200, json=SUBMITTED)
        if path == "/LATEST/search-index":
            assert request.url.params["enddt"] and request.url.params["q"] == "S000000001"
            return search()
        if path.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=FUNDS)
        if path.endswith("primary_doc.xml"):
            _, date, n = REPORTS[path.split("/")[-2]]
            return httpx.Response(200, text=report(series_in_report, date, n))
        raise AssertionError(path)
    got = sec.SEC(http=httpx.Client(transport=httpx.MockTransport(answer)), interval=0.0, pause=0.0)
    return got, asked


def test_a_fund_under_a_cik_alone_takes_its_latest_report_by_the_as_of_from_the_submissions_record(monkeypatch):
    got, asked = client(series_in_report=None)
    monkeypatch.setattr(got, "filer", lambda fund: (None, "9"))
    now = got.nport("CEF", "2026-11-15")["payload"]
    assert (now["form"], now["accession"], len(now["results"])) == ("NPORT-P/A", "0000000009-26-000003", 3)
    then = got.nport("CEF", "2026-09-01")["payload"]  # before the restatement was filed
    assert (then["form"], then["accession"], then["effective_date"]) == ("NPORT-P", "0000000009-26-000002", "2026-06-30")
    assert got.nport("CEF", "2026-05-01")["payload"]["status"] == "NOT_FOUND"
    assert "/LATEST/search-index" not in asked


def test_a_series_takes_its_latest_report_by_the_as_of_from_the_search_whatever_the_order_of_its_hits(monkeypatch):
    got, asked = client()
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    now = got.nport("ONE", "2026-11-15")["payload"]
    assert (now["series"], now["form"], now["accession"]) == ("S000000001", "NPORT-P/A", "0000000009-26-000003")
    then = got.nport("ONE", "2026-09-01")["payload"]
    assert (then["form"], then["accession"]) == ("NPORT-P", "0000000009-26-000002")
    assert "/submissions/CIK0000000009.json" not in asked


def test_a_report_that_names_another_series_is_not_the_series_report(monkeypatch):
    got, _ = client(series_in_report="S000000002")
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    assert got.nport("ONE", "2026-09-01")["payload"]["status"] == "NOT_FOUND"


def test_the_sec_is_asked_again_where_it_does_not_answer_and_a_refusal_is_its_answer(monkeypatch):
    answers = iter([httpx.Response(503), httpx.Response(200, json={"hits": {"total": {"value": 3}, "hits": HITS}})])
    got, asked = client(search=lambda: next(answers))
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    assert got.nport("ONE", "2026-09-01")["payload"]["accession"] == "0000000009-26-000002"
    assert asked.count("/LATEST/search-index") == 2

    down, asked = client(search=lambda: httpx.Response(503))
    monkeypatch.setattr(down, "filer", lambda fund: ("S000000001", "9"))
    with pytest.raises(VenueError, match="503"):
        down.nport("ONE", "2026-09-01")
    assert asked.count("/LATEST/search-index") == sec.TRIES

    refused, asked = client(search=lambda: httpx.Response(403))
    monkeypatch.setattr(refused, "filer", lambda fund: ("S000000001", "9"))
    with pytest.raises(VenueRefused, match="403"):
        refused.nport("ONE", "2026-09-01")
    assert asked.count("/LATEST/search-index") == 1


def test_a_series_is_a_money_market_fund_where_its_latest_filing_is_an_n_mfp(monkeypatch):
    became = HITS + [hit("N-MFP3", "2026-10-07", "0000000009-26-000010")]  # a fund whose latest report is an N-MFP
    got, _ = client(search=lambda: httpx.Response(200, json={"hits": {"total": {"value": 4}, "hits": became}}))
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    assert got.money_market("ONE", "2026-11-15")["payload"]["money_market"] is True
    assert got.money_market("ONE", "2026-09-30")["payload"]["money_market"] is False  # before it
    assert got.money_market("ONE", "2026-01-01")["payload"]["money_market"] is False  # none filed by then


def test_the_reader_names_itself_to_the_sec_with_its_contact_where_one_is_given():
    seen = []
    transport = httpx.MockTransport(lambda r: seen.append(r.headers["User-Agent"]) or httpx.Response(200, json=FUNDS))
    sec.SEC(http=httpx.Client(transport=transport), interval=0.0, contact="Jane Doe jane@example.com").funds("2026-11-19")
    sec.SEC(http=httpx.Client(transport=transport), interval=0.0, contact="").funds("2026-11-19")
    assert seen == [f"{sec.USER_AGENT} Jane Doe jane@example.com", sec.USER_AGENT]


def test_a_companys_reports_are_listed_back_through_its_older_pages_with_an_8ks_items():
    """A company's own reports at the SEC (sec.REPORTS), back RECORD_REACH days: its recent page holds its last
    thousand filings, which for a filer of many a day spans weeks, so its older pages are read until one reaches back
    that far (2025-11-12, from 2026-11-13), and no further; what others file about it (an insider's Form 4, an underwriter's offering document) is
    not kept; an 8-K keeps the items it reports under, as the filer states them."""
    recent = {"form": ["424B2", "8-K", "4"], "filingDate": ["2026-11-10", "2026-10-13", "2026-10-01"],
              "accessionNumber": ["A-3", "A-2", "A-1"], "reportDate": ["", "2026-10-13", ""],
              "primaryDocument": ["p.htm", "k.htm", "f.xml"], "items": ["", "2.02,9.01", ""]}
    older = {"form": ["10-Q", "424B2"], "filingDate": ["2026-08-05", "2025-11-01"], "accessionNumber": ["A-0", "A-9"],
             "reportDate": ["2026-06-30", ""], "primaryDocument": ["q.htm", "p.htm"], "items": ["", ""]}
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/submissions/CIK0000000019.json":
            return httpx.Response(200, json={"name": "A BANK", "filings": {"recent": recent, "files": [
                {"name": "CIK0000000019-submissions-001.json", "filingFrom": "2025-11-01", "filingTo": "2026-09-30"},
                {"name": "CIK0000000019-submissions-002.json", "filingFrom": "2024-01-01", "filingTo": "2025-11-30"}]}})
        if request.url.path == "/submissions/CIK0000000019-submissions-001.json":
            return httpx.Response(200, json=older)
        raise AssertionError(request.url.path)
    got = sec.SEC(http=httpx.Client(transport=httpx.MockTransport(answer)), interval=0.0, pause=0.0)
    filings = got.filed("19", "2026-11-13")["payload"]["filings"]
    assert [(f["form"], f["filed"], f.get("items")) for f in filings] == [
        ("8-K", "2026-10-13", ["2.02", "9.01"]), ("10-Q", "2026-08-05", None)]
    assert asked == ["/submissions/CIK0000000019.json", "/submissions/CIK0000000019-submissions-001.json"]
