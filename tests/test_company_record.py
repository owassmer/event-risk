"""A company's record, whichever market lists it (TOOLS.md 5, search_company; 3.1): a US listing's from
Massive; a company named by its home listing, its results and analysts' ratings from its listing's record on its own
market (harness.venues.yahoo `record`), in the fields a US listing's carry, bounded by the as-of; a foreign issuer's
annual reports (20-F, 40-F) and its reports between them (6-K) from EDGAR by its CIK, held as pages. Every source is
answered here; nothing reaches the network."""

from __future__ import annotations

import httpx
import pytest

from harness.store import StoreError
from harness.venues.sec import SEC
from harness.venues.yahoo import Yahoo

TODAY = "2026-11-11"


def yahoo(routes):
    """Yahoo's client on fixed answers to the site's surfaces, its cookie and crumb answered."""
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fc.yahoo.com":
            return httpx.Response(404, text="")
        if request.url.path == "/v1/test/getcrumb":
            return httpx.Response(200, text="crumb1")
        status, body = routes(request.url.path)
        return httpx.Response(status, json=body)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    return Yahoo(http=client, site=client, interval=0, pause=0)


MODULE = {"quoteSummary": {"result": [{
    "earnings": {"financialCurrency": "TWD",
                 "earningsChart": {"quarterly": [
                     {"date": "4Q2025", "reportedDate": {"fmt": "2026-01-15"}, "periodEndDate": {"fmt": "2025-12-31"},
                      "actual": {"raw": 19.5}, "estimate": {"raw": 18.15}, "difference": "1.35", "surprisePct": "7.44"},
                     {"date": "1Q2026", "reportedDate": {"fmt": "2026-04-16"}, "periodEndDate": {"fmt": "2026-03-31"},
                      "actual": {"raw": 22.08}, "estimate": {"raw": 20.96}, "difference": "1.12", "surprisePct": "5.33"},
                     {"date": "3Q2026", "reportedDate": {"fmt": "2026-11-20"}, "periodEndDate": {"fmt": "2026-09-30"},
                      "actual": {"raw": 29.43}, "estimate": {"raw": 28.45}, "difference": "0.98",
                      "surprisePct": "3.44"}]},
                 "financialsChart": {"quarterly": [{"date": "4Q2025", "revenue": {"raw": 1046}, "earnings": {"raw": 505}},
                                                   {"date": "1Q2026", "revenue": {"raw": 1134}, "earnings": {"raw": 572}}]}},
    "recommendationTrend": {"trend": [{"period": "0m", "strongBuy": 9, "buy": 25, "hold": 1, "sell": 0, "strongSell": 0}]},
    "financialData": {"financialCurrency": "TWD", "targetMeanPrice": {"raw": 3389.8}, "targetLowPrice": {"raw": 2500},
                      "targetHighPrice": {"raw": 5000}, "numberOfAnalystOpinions": {"raw": 35}}}]}}
SERIES = {"timeseries": {"result": [
    {"meta": {"type": ["quarterlyTotalRevenue"]}, "quarterlyTotalRevenue": [
        {"asOfDate": "2025-09-30", "reportedValue": {"raw": 989}}, {"asOfDate": "2025-12-31", "reportedValue": {"raw": 1046}}]},
    {"meta": {"type": ["quarterlyDilutedEPS"]}, "quarterlyDilutedEPS": [
        {"asOfDate": "2025-06-30", "reportedValue": {"raw": 15.36}}]}]}}
CALENDAR = {"finance": {"result": [{"documents": [{
    "columns": [{"id": "ticker"}, {"id": "startdatetime"}, {"id": "epsestimate"}, {"id": "epsactual"},
                {"id": "epssurprisepct"}],
    "rows": [["2330.TW", "2025-06-04T01:00:00.000Z", None, None, None],  # a shareholders' meeting: no report
             ["2330.TW", "2025-04-17T06:43:00.000Z", 13.58, 13.94, 2.62],
             ["2330.TW", "2014-10-16T09:49:00.000Z", 2.82, 2.94, 4.44]]}]}]}}


def site(path):
    if path.startswith("/v10/finance/quoteSummary/2330.TW"):
        return 200, MODULE
    if path.startswith("/ws/fundamentals-timeseries"):
        return 200, SERIES
    if path == "/v1/finance/visualization":
        return 200, CALENDAR
    if path.startswith("/v10/finance/quoteSummary/"):
        return 400, {"quoteSummary": {"error": {"code": "Bad Request"}}}
    return 404, {}


def test_a_listing_abroad_has_its_reports_each_quarter_once_and_its_earlier_reports_in_a_us_listings_fields():
    p = yahoo(site).record("2330.TW", TODAY)["payload"]
    rows = p["results"]
    assert [r.get("date") or r["period_end"] for r in rows] == \
        ["2026-11-20", "2026-04-16", "2026-01-15", "2025-09-30", "2025-06-30", "2025-04-17", "2014-10-16"]
    first = rows[2]
    assert first == {"date": "2026-01-15", "period_end": "2025-12-31", "fiscal_period": "Q4", "fiscal_year": 2025,
                     "currency": "TWD", "actual_eps": 19.5, "estimated_eps": 18.15, "eps_surprise": 1.35,
                     "eps_surprise_percent": 0.0744, "actual_revenue": 1046, "actual_earnings": 505}
    # a quarter the series gives that no report holds stands by its period's end, with no report date
    assert rows[3] == {"period_end": "2025-09-30", "actual_revenue": 989, "currency": "TWD"}
    # the calendar's earlier reports, a meeting with no figures left out
    assert rows[5] == {"date": "2025-04-17", "actual_eps": 13.94, "estimated_eps": 13.58, "eps_surprise": 0.36,
                       "eps_surprise_percent": 0.0262, "currency": "TWD"}
    assert p["ratings"] == {"as_of": TODAY, "ratings_contributors": 35, "strong_buy_ratings": 9, "buy_ratings": 25,
                            "hold_ratings": 1, "sell_ratings": 0, "strong_sell_ratings": 0,
                            "price_target_contributors": 35, "consensus_price_target": 3389.8,
                            "low_price_target": 2500, "high_price_target": 5000, "currency": "TWD"}


def test_a_symbol_no_listing_answers_is_its_answer_that_there_is_none():
    assert yahoo(site).record("ZZZZ.T", TODAY)["payload"] == {"status": "NOT_FOUND", "symbol": "ZZZZ.T"}


@pytest.fixture
def abroad(world):
    """A store whose world reads a listing abroad's symbol and its record from fixed answers."""
    y = yahoo(site)
    world.today = lambda: TODAY
    world.store.fetchers["listing_symbol"] = lambda subject, covering: {
        "payload": {"status": "OK", "listing": subject.split("@")[0],
                    "symbol": "2330.TW" if subject.startswith("TW0002330008:TW") else None},
        "request": {"method": "GET", "url": "search"}, "retrieved_at": "2026-11-11T15:00:00Z",
        "range": (covering[1], covering[1])}
    world.store.fetchers["company_record"] = lambda subject, covering: y.record(subject, covering[1])
    return world


def test_a_company_named_by_its_home_listing_has_its_results_reported_by_the_as_of_and_its_ratings_today(abroad):
    got = abroad.search_company("TW0002330008:TW", "earnings")
    assert got["symbol"] == "2330.TW" and got["source"] == "Yahoo Finance"
    assert [r.get("date") or r["period_end"] for r in got["rows"]] == \
        ["2026-04-16", "2026-01-15", "2025-09-30", "2025-06-30", "2025-04-17", "2014-10-16"]  # none reported after
    assert abroad.search_company("name:TW0002330008:TW", "earnings")["rows"] == got["rows"]  # named by its id
    ratings = abroad.search_company("TW0002330008:TW", "ratings")
    assert ratings["rows"][0]["consensus_price_target"] == 3389.8


def test_a_past_as_of_reads_only_what_was_reported_by_then_and_no_ratings(abroad):
    with abroad.store.transaction():
        abroad.store._set_meta("asof", "2025-05-01")
    got = abroad.search_company("TW0002330008:TW", "earnings")
    assert [r["date"] for r in got["rows"]] == ["2025-04-17", "2014-10-16"]  # a period's figures alone are today's
    with pytest.raises(StoreError, match="read as they stand on the day they are read, so a store as of 2025-05-01"):
        abroad.search_company("TW0002330008:TW", "ratings")


def test_a_company_listed_only_abroad_names_what_its_record_here_is(abroad):
    with pytest.raises(StoreError, match=r"listed only abroad, and its record here is its results and its analysts' "
                                         r"ratings \(kind earnings or ratings\), not its news"):
        abroad.search_company("TW0002330008:TW", "news")
    with pytest.raises(StoreError, match="no listing of JP0000000000:JP on its own market is found"):
        abroad.search_company("JP0000000000:JP", "earnings")


# ---------------------------------------------------------------- a foreign issuer's reports at the SEC

SUBMISSIONS = {"name": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "filings": {"recent": {
    "form": ["6-K", "6-K", "20-F", "6-K", "20-F"],
    "filingDate": ["2026-10-16", "2026-09-10", "2026-04-15", "2025-07-17", "2025-04-17"],
    "reportDate": ["", "", "2025-12-31", "", "2024-12-31"],
    "accessionNumber": ["0001046179-26-000090", "0001046179-26-000080", "0001046179-26-000020",
                        "0001046179-25-000060", "0001046179-25-000020"],
    "primaryDocument": ["a6k.htm", "b6k.htm", "tsm-20251231.htm", "c6k.htm", "tsm-20241231.htm"]}}}


def sec_routes(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/submissions/CIK0001046179.json":
        return httpx.Response(200, json=SUBMISSIONS)
    if path.endswith("000104617926000090/index.json"):  # the 6-K's: its press release, a PDF and XBRL's pages
        return httpx.Response(200, json={"directory": {"item": [{"name": "a6k.htm"}, {"name": "ex99-1.htm"},
                                                                {"name": "ex99_2.pdf"}, {"name": "R1.htm"}]}})
    if path.endswith("/index.json"):
        return httpx.Response(200, json={"directory": {"item": [{"name": "doc.htm"}]}})
    if path.endswith("/a6k.htm"):
        return httpx.Response(200, text="<html><body><ix:header>facts</ix:header><p>Form 6-K cover</p></body></html>")
    if path.endswith("/ex99-1.htm"):
        return httpx.Response(200, text="<html><body><p>Third quarter revenue rose.</p></body></html>")
    if path.endswith(".htm"):
        return httpx.Response(200, text="<html><body><p>Annual report on Form 20-F.</p></body></html>")
    return httpx.Response(404, text="")


def sec():
    return SEC(http=httpx.Client(transport=httpx.MockTransport(sec_routes)), interval=0)


def test_edgar_lists_a_filers_filings_and_a_6k_reads_with_its_press_releases_after_it():
    listed = sec().filed("1046179", TODAY)["payload"]
    assert listed["filings"][2] == {"form": "20-F", "filed": "2026-04-15", "period": "2025-12-31",
                                    "accession": "0001046179-26-000020", "document": "tsm-20251231.htm"}
    doc = sec().filing("0001046179/0001046179-26-000090/a6k.htm", TODAY)["payload"]
    assert doc["exhibits"] == ["ex99-1.htm"]  # the press release it furnishes, a PDF and XBRL's pages left aside
    assert "Form 6-K cover" in doc["text"] and "facts" not in doc["text"]
    assert doc["text"].index("Form 6-K cover") < doc["text"].index("## ex99-1.htm") < \
        doc["text"].index("Third quarter revenue rose.")


def text(world, sid):
    with world.store.lock:
        return world.store.source_row(sid)["text"]


@pytest.fixture
def filer(world):
    """A foreign issuer with a US receipt: Massive's filings hold none of its reports, EDGAR's do."""
    world.today = lambda: TODAY
    client = sec()
    empty = {"status": "OK", "results": []}
    world.store.fetchers["ticker_ref"] = lambda subject, covering: {
        "payload": {"status": "OK", "results": {"ticker": subject, "name": "Taiwan Semiconductor", "type": "ADRC",
                                                "cik": "0001046179", "list_date": "1997-10-09"}},
        "request": {"method": "GET", "url": "ref"}, "retrieved_at": "2026-11-11T15:00:00Z",
        "range": (covering[1], covering[1])}
    world.store.fetchers["company"] = lambda subject, covering: {
        "payload": empty, "request": {"method": "GET", "url": "company"}, "retrieved_at": "2026-11-11T15:00:00Z",
        "range": (covering[1], covering[1])}
    world.store.fetchers["sec_filings"] = lambda subject, covering: client.filed(subject, covering[1])
    world.store.fetchers["sec_document"] = lambda subject, covering: client.filing(subject, covering[1])
    return world


def test_a_foreign_issuers_annual_report_and_its_reports_between_come_from_edgar_held_as_pages(filer):
    annual = filer.search_company("TSM", "annual_report")
    assert annual["source"] == "the SEC" and annual["more"] is True  # one read, where no limit is named
    row = annual["rows"][0]
    assert (row["form_type"], row["filing_date"], row["period_end"]) == ("20-F", "2026-04-15", "2025-12-31")
    assert text(filer, row["page"]["id"]).strip() == "Annual report on Form 20-F."
    between = filer.search_company("TSM", "current_reports", {"filing_date.gte": "2026-01-01"})
    assert [r["filing_date"] for r in between["rows"]] == ["2026-10-16", "2026-09-10"]
    assert "Third quarter revenue rose." in text(filer, between["rows"][0]["page"]["id"])


def test_a_past_as_of_reads_no_report_filed_after_it(filer):
    with filer.store.transaction():
        filer.store._set_meta("asof", "2025-12-31")
    annual = filer.search_company("TSM", "annual_report")
    assert [r["filing_date"] for r in annual["rows"]] == ["2025-04-17"]


def test_a_company_that_files_neither_kind_has_massives_answer_with_what_else_was_read(filer):
    filer.store.fetchers["sec_filings"] = lambda subject, covering: {
        "payload": {"status": "OK", "cik": subject, "name": "X", "filings": [
            {"form": "10-Q", "filed": "2026-08-01", "period": "2026-06-30", "accession": "a", "document": "q.htm"}]},
        "request": {"method": "GET", "url": "sub"}, "retrieved_at": "2026-11-11T15:00:00Z",
        "range": (covering[1], covering[1])}
    got = filer.search_company("TSM", "annual_report")
    assert got["rows"] == [] and got["source"] == "the SEC"
    assert got["reason"].endswith("The SEC lists no annual report of it on Form 20-F or 40-F within the dates searched "
                                  "either.")


# ---------------------------------------------------------------- a US listing's record at Massive

def test_a_parameter_reaches_the_endpoint_as_one_value_or_several_comma_separated_never_as_a_list(filer):
    """A run asked for a company's news with `"channels": ["news"]`, which reached Benzinga as the text "['news']", a
    channel no article has, and read as no news. A list of one is its value; where a filter takes several (`.any_of`,
    `.all_of`) they go comma-separated, the endpoint's own form; several for a parameter that takes one are refused."""
    from harness.venues.massive import Massive
    asked = []

    def company(subject, covering):
        import json
        asked.append(json.loads(subject)["params"])
        return {"payload": {"status": "OK", "results": []}, "request": {"method": "GET", "url": "company"},
                "retrieved_at": "2026-11-11T15:00:00Z", "range": (covering[1], covering[1])}
    filer.store.fetchers["company"] = company
    filer.search_company("TSM", "news", {"channels": ["news"], "limit": 25})
    assert asked[-1]["channels"] == "news" and asked[-1]["limit"] == 25
    filer.search_company("TSM", "news", {"channels.any_of": ["news", "markets"]})
    assert asked[-1]["channels.any_of"] == "news,markets"
    with pytest.raises(StoreError, match=r"channels takes one value; channels.any_of takes any of several and "
                                         r"channels.all_of all of them$"):
        filer.search_company("TSM", "news", {"channels": ["news", "markets"]})
    with pytest.raises(StoreError, match=r"^limit takes one value$"):
        filer.search_company("TSM", "news", {"limit": [10, 20]})
    n = len(asked)
    with pytest.raises(StoreError) as refused:  # a name the endpoint does not have would be answered as if absent
        filer.search_company("TSM", "news", {"channelz": "news", "published.gte": "2026-10-01"})
    assert str(refused.value).startswith("channelz is not a filter of search_company's news: it takes published (.gt, "
                                         ".gte, .lt, .lte), channels (.all_of, .any_of), tags (.all_of, .any_of)")
    assert "tickers" not in str(refused.value) and len(asked) == n  # nothing asked
    filer.search_company("TSM", "news", {"author.any_of": ["A", "B"], "tags": "Earnings"})
    assert (asked[-1]["author.any_of"], asked[-1]["tags"]) == ("A,B", "Earnings")
    with pytest.raises(ValueError, match="a parameter's value is a string or a number"):
        Massive("key").url("/benzinga/v2/news", {"channels": ["news"]})
    assert Massive("key").url("/benzinga/v2/news", {"channels": "news"}).endswith("?channels=news")


def test_earnings_at_todays_as_of_include_the_reports_scheduled_after_it_and_at_a_past_as_of_end_there(filer):
    """A report scheduled after today is known today: at today's as-of `earnings` asks for no end, unless one is given;
    at a past as-of, whose scheduled rows have since been overwritten with their results, they end at the as-of."""
    asked = []

    def company(subject, covering):
        import json
        asked.append(json.loads(subject)["params"])
        return {"payload": {"status": "OK", "results": [{"date": "2026-10-16", "date_status": "confirmed"}]},
                "request": {"method": "GET", "url": "company"}, "retrieved_at": "2026-11-11T15:00:00Z",
                "range": (covering[1], covering[1])}
    filer.store.fetchers["company"] = company
    got = filer.search_company("TSM", "earnings")
    assert "date.lte" not in asked[-1] and asked[-1]["date.gte"] == "1997-10-09"  # from its listing
    assert got["source"] == "Benzinga"  # its publisher
    filer.search_company("TSM", "earnings", {"date.lte": "2027-06-30"})
    assert asked[-1]["date.lte"] == "2027-06-30"
    filer.search_company("TSM", "guidance")
    assert asked[-1]["date.lte"] == TODAY  # every other kind ends at the as-of
    with filer.store.transaction():
        filer.store._set_meta("asof", "2025-12-31")
    filer.search_company("TSM", "earnings", {"date.lte": "2027-06-30"})
    assert asked[-1]["date.lte"] == "2025-12-31"


def test_earnings_come_nearest_the_as_of_first_and_a_limit_keeps_the_nearest(filer):
    """MATH.md 3.1; TOOLS.md 5: what is scheduled comes first, the nearest first, then what is reported, the newest
    first. The endpoint sorts by when a row was last updated where no sort is named, so a limit there kept the rows
    updated last, wherever they fell: the farthest scheduled report hid the nearest. A sort the analyst names stands as
    given."""
    import json
    rows = [{"date": "2026-07-17", "last_updated": "2026-07-17"}, {"date": "2026-10-16", "last_updated": "2026-10-16"},
            {"date": "2027-01-22", "last_updated": "2026-10-20"}, {"date": "2027-04-20", "last_updated": "2026-10-21"},
            {"date": "2027-07-20", "last_updated": "2026-10-22"}, {"date": "2027-10-20", "last_updated": "2026-11-10"}]

    def company(subject, covering):  # the endpoint as its page documents it: its filters, its sort, its limit
        p = json.loads(subject)["params"]
        found = [r for r in rows if all(
            {"gte": r["date"] >= v, "gt": r["date"] > v, "lte": r["date"] <= v, "lt": r["date"] < v}[k.split(".")[1]]
            for k, v in p.items() if k.startswith("date."))]
        field, order = (p.get("sort") or "last_updated.desc").split(".")
        found.sort(key=lambda r: r[field], reverse=order == "desc")
        return {"payload": {"status": "OK", "results": found[:int(p.get("limit", 100))]},
                "request": {"method": "GET", "url": "company"}, "retrieved_at": "2026-11-11T15:00:00Z",
                "range": (covering[1], covering[1])}

    filer.store.fetchers["company"] = company
    dates = lambda got: [r["date"] for r in got["rows"]]
    assert dates(filer.search_company("TSM", "earnings", {"date.gte": TODAY, "limit": 3})) == [
        "2027-01-22", "2027-04-20", "2027-07-20"]
    assert dates(filer.search_company("TSM", "earnings", {"limit": 5})) == [
        "2027-01-22", "2027-04-20", "2027-07-20", "2027-10-20", "2026-10-16"]
    assert dates(filer.search_company("TSM", "earnings", {"sort": "date.desc", "limit": 2})) == ["2027-10-20", "2027-07-20"]
