"""Where a fund's Form N-PORT reports are listed (harness.venues.sec): a fund filing under a CIK alone, and a series
whose registrant files for no other, from EDGAR's submissions record; any other series from EDGAR's company feed,
asked again where it does not answer before the SEC is taken as not answering."""

from __future__ import annotations

import httpx
import pytest

from harness.venues import sec
from harness.venues.http import VenueError


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
FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><content><accession-number>0000000009-26-000002</accession-number><filing-date>2026-08-28</filing-date>
<filing-type>NPORT-P</filing-type>
<filing-href>https://www.sec.gov/Archives/edgar/data/9/000000000926000002/0000000009-26-000002-index.htm</filing-href>
</content></entry></feed>"""
FUNDS = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[9, "S000000001", "C1", "ONE"]]}


def client(feed=lambda: httpx.Response(200, text=FEED), funds=FUNDS, series_in_report="S000000001"):
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == "/submissions/CIK0000000009.json":
            return httpx.Response(200, json=SUBMITTED)
        if path.endswith("browse-edgar"):
            return feed()
        if path.endswith("company_tickers_mf.json"):
            return httpx.Response(200, json=funds)
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
    assert not any(p.endswith("browse-edgar") for p in asked)


def test_a_series_whose_registrant_files_for_no_other_is_read_from_the_submissions_record(monkeypatch):
    got, asked = client(feed=lambda: pytest.fail("the feed is not asked"))
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    found = got.nport("ONE", "2026-09-01")["payload"]
    assert (found["series"], found["accession"]) == ("S000000001", "0000000009-26-000002")
    assert found and not any(p.endswith("browse-edgar") for p in asked)


def test_a_series_whose_report_names_another_series_is_read_from_the_feed(monkeypatch):
    got, asked = client(series_in_report="S000000002")  # the registrant files for a series the list does not name
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    got.nport("ONE", "2026-09-01")
    assert any(p.endswith("browse-edgar") for p in asked)


def test_the_feed_is_asked_again_where_it_does_not_answer_and_the_sec_rests_only_once_it_still_does_not(monkeypatch):
    answers = iter([httpx.Response(503), httpx.Response(200, text=FEED)])
    two = {"fields": FUNDS["fields"], "data": FUNDS["data"] + [[9, "S000000002", "C2", "TWO"]]}
    got, asked = client(feed=lambda: next(answers), funds=two)
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    assert got.nport("ONE", "2026-09-01")["payload"]["accession"] == "0000000009-26-000002"
    assert sum(p.endswith("browse-edgar") for p in asked) == 2

    down, _ = client(feed=lambda: httpx.Response(503), funds=two)
    monkeypatch.setattr(down, "filer", lambda fund: ("S000000001", "9"))
    with pytest.raises(VenueError, match="503"):
        down.nport("ONE", "2026-09-01")
    with pytest.raises(VenueError, match="a moment ago"):  # not asked again for a minute
        down.nport("ONE", "2026-09-01")


def test_an_answer_that_is_not_a_failure_to_answer_is_not_asked_again(monkeypatch):
    calls = []
    two = {"fields": FUNDS["fields"], "data": FUNDS["data"] + [[9, "S000000002", "C2", "TWO"]]}
    got, _ = client(feed=lambda: calls.append(1) or httpx.Response(404), funds=two)
    monkeypatch.setattr(got, "filer", lambda fund: ("S000000001", "9"))
    with pytest.raises(VenueError, match="404"):
        got.nport("ONE", "2026-09-01")
    assert len(calls) == 1
