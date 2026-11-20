"""What the analyst first reads of the portfolio (MATH.md 3.1, TOOLS.md 5): every company in one compact form, each industry and
country by its id, and a concentrated company's own record over the span."""

from __future__ import annotations

import pytest

from harness.ledger import HOME_RECORD, SEC_RECORD, group, holdings
from harness.store import StoreError
from massive_data import ASOF, FETCHED, put_reference
from test_impact import holding_world


def _filings(store, cik: str, filings: list[dict]) -> None:
    store.put_data("sec_filings", cik, {"status": "OK", "cik": cik, "filings": filings},
                   {"method": "GET", "url": f"https://data.sec.gov/submissions/CIK{cik}.json"},
                   retrieved_at=FETCHED, range=(ASOF, ASOF))


def test_a_concentrated_company_comes_with_its_own_record_over_the_span(tmp_path):
    """NVDA, 74% of the portfolio held and through FUND, files at the SEC: its record is its current and periodic
    reports filed over the 180 days to the as-of, newest first, an 8-K with the items it reports under and a 10-Q with
    its period, each at the address fetch_source reads it whole; what its insiders file is not its report, and a report
    from before the span is not in it. XOM, 14% through FUND, files nothing there: its record is its results reported
    within the span, as its home market's record gives them. Each entry names the record it is. FUND, a fund, has none."""
    w = holding_world(tmp_path)
    store = w.store
    store.put_data("ticker_ref", "NVDA", {"status": "OK", "results": {"ticker": "NVDA", "type": "CS", "cik": "1045810",
                                                                       "market": "stocks", "locale": "us"}},
                   {"method": "GET", "url": "ref:NVDA"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    _filings(store, "0001045810", [
        {"form": "8-K", "filed": "2026-08-27", "period": "2026-08-27", "accession": "0001045810-26-000101",
         "document": "nvda-20260827.htm", "items": ["2.02", "9.01"]},
        {"form": "4", "filed": "2026-09-02", "period": None, "accession": "0001045810-26-000102",
         "document": "form4.xml"},  # as a payload read before `filed` kept reports alone
        {"form": "10-Q", "filed": "2026-08-27", "period": "2026-07-26", "accession": "0001045810-26-000100",
         "document": "nvda-20260726.htm"},
        {"form": "10-K", "filed": "2026-02-25", "period": "2026-01-25", "accession": "0001045810-26-000010",
         "document": "nvda-20260125.htm"}])  # before the span
    store.put_data("company_record", "XOM", {"status": "OK", "symbol": "XOM", "results": [
        {"date": "2026-10-31", "period_end": "2026-09-30", "fiscal_period": "Q3", "actual_eps": 1.88},
        {"date": "2026-04-30", "period_end": "2026-03-31", "fiscal_period": "Q1", "actual_eps": 1.76}]},
        {"method": "GET", "url": "record:XOM"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    got = holdings(store)["reports"]
    assert [(r["company"], r["record"]) for r in got] == [("name:NVDA", SEC_RECORD), ("name:XOM", HOME_RECORD)]
    assert got[0]["reports"] == [
        {"filed": "2026-08-27", "form": "8-K", "items": ["2.02", "9.01"], "period": "2026-08-27",
         "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/nvda-20260827.htm"},
        {"filed": "2026-08-27", "form": "10-Q", "period": "2026-07-26",
         "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000100/nvda-20260726.htm"}]
    assert [r["date"] for r in got[1]["results"]] == ["2026-10-31"]  # its Q1 report was before the span


def test_every_company_is_one_compact_row_and_the_rest_one_line(tmp_path):
    """Each company the look-through names is its id, its name and its weight, largest first; nothing repeats it."""
    w = holding_world(tmp_path)
    got = holdings(w.store)
    companies = [c for c in got["by_company"] if "id" in c]
    assert [c["id"] for c in companies] == ["name:NVDA", "name:XOM"]
    assert all(set(c) == {"id", "company", "weight"} for c in companies)
    held = {h["ticker"]: h for h in got["holdings"] if "ticker" in h}
    assert set(held["FUND"]["fund"]) == {"looked_through", "holdings_asof", "holdings"}  # what it holds is its get
    assert "largest" not in str(got["holdings"])


def test_an_industry_or_a_country_is_read_by_its_id_with_the_companies_in_it(tmp_path):
    """TOOLS.md 5: get reads a group by its id: its weight and the companies in it, each by its id, its name and its weight;
    an id that names none of the portfolio's groups is refused, naming where their ids are."""
    from test_owned import equities
    w = holding_world(tmp_path)
    equities(w.store, [("NVDA", "NVIDIA Corporation", "Information Technology", "Semiconductors & Chip Equipment",
                        "US67066G1040", "United States"),
                       ("XOM", "Exxon Mobil Corporation", "Energy", "Oil, Gas & Fuels", "US30231G1022",
                        "United States")])
    first = holdings(w.store)
    ids = {g["id"] for g in first["by_industry"] if g.get("id")}
    assert ids == {"industry:semiconductors-chip-equipment", "industry:oil-gas-fuels"}
    chips = group(w.store, "industry:semiconductors-chip-equipment")
    assert (chips["industry"], chips["sector"], chips["companies"]) == \
        ("Semiconductors & Chip Equipment", "Information Technology", 1)
    assert chips["by_company"] == [{"id": "name:NVDA", "company": "NVIDIA Corporation",
                                    "weight": pytest.approx(chips["weight"])}]
    with pytest.raises(StoreError) as refused:  # the store knows the portfolio's groups, not every industry
        group(w.store, "industry:gold")
    assert str(refused.value) == ("industry:gold is not one of the industries the portfolio lists. Its by_industry "
                                  "lists each that is at least 0.05% of it, by its id.")
    assert all(set(g) == {"id", "weight"} for g in first["by_industry"] if g.get("id"))  # get gives how many companies


def test_industries_and_countries_under_the_line_stand_together_as_its_companies_do():
    """TOOLS.md 5: each industry and country at least 0.05% of the portfolio by its id, with its weight, those under it
    together with how many, one no source classifies by its want of a name, and what is not a company by its kind."""
    from harness.ledger import _groups
    got = _groups([{"id": "industry:a", "weight": 0.3, "companies": 9}, {"id": "industry:b", "weight": 0.0004},
                   {"id": "industry:c", "weight": 0.0001}, {"industry": None, "weight": 0.002},
                   {"kind": "debt", "weight": 0.1}], "industry")
    assert got == [{"id": "industry:a", "weight": 0.3}, {"industry": None, "weight": 0.002},
                   {"industries": 2, "weight": 0.0005}, {"kind": "debt", "weight": 0.1}]


def test_a_document_filed_at_the_sec_is_read_as_its_filing(tmp_path):
    """TOOLS.md 5's fetch_source: an address in the SEC's archive is read as the filing it belongs to, the document with
    its exhibits 99 (harness.venues.sec `filing`), and held under that address; the web is not asked."""
    from harness.world import World
    w = holding_world(tmp_path)
    url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/nvda-20260827.htm"
    text = "FORM 8-K. Item 2.02 Results of Operations.\n\n## ex99-1.htm\n\nNVIDIA announces its results."
    w.store.put_data("sec_document", "0001045810/0001045810-26-000101/nvda-20260827.htm",
                     {"status": "OK", "url": url, "text": text, "exhibits": ["ex99-1.htm"]},
                     {"method": "GET", "url": url}, retrieved_at=FETCHED, range=(ASOF, ASOF))

    class NoWeb:
        def fetch(self, url):
            raise AssertionError("the SEC's archive is read through its own client")
    got = World(w.store, w.venues, web=NoWeb()).fetch_source(url, "announces its results")
    assert got["length"] == len(text) and got["passages"]


def test_a_company_with_a_cik_and_no_reports_at_the_sec_has_its_home_markets_record(tmp_path):
    """A company the SEC lists, by a receipt's registration alone, files no report there: its record is its results as
    its home market's record gives them, never an empty list of reports at the SEC, which would read as a span with
    nothing reported. (NVDA, held directly, stands for such a company here: a company reached only through a fund is
    resolved by its CIK through the SEC's list of tickers, which this store holds none of.)"""
    w = holding_world(tmp_path)
    store = w.store
    store.put_data("ticker_ref", "NVDA", {"status": "OK", "results": {"ticker": "NVDA", "type": "CS", "cik": "1045810",
                                                                       "market": "stocks", "locale": "us"}},
                   {"method": "GET", "url": "ref:NVDA"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    _filings(store, "0001045810", [{"form": "F-6", "filed": "2026-03-02", "period": None,
                                    "accession": "0001045810-26-000001", "document": "f6.htm"}])
    store.put_data("company_record", "NVDA", {"status": "OK", "symbol": "NVDA", "results": [
        {"date": "2026-08-27", "period_end": "2026-07-26", "fiscal_period": "Q2", "actual_eps": 1.05}]},
        {"method": "GET", "url": "record:NVDA"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    [nvda] = [r for r in holdings(store)["reports"] if r["company"] == "name:NVDA"]
    assert nvda["record"] == HOME_RECORD and [r["date"] for r in nvda["results"]] == ["2026-08-27"]


def test_a_companys_cik_is_the_one_the_secs_list_gives_its_ticker_before_its_reference(tmp_path):
    """identity.Lookup.company_cik: a US listing's company is the CIK the SEC's list gives its ticker, before Massive's
    reference, whose copy names none for some (a tracking stock): such a company's record is still its reports at the
    SEC."""
    from harness.derive.identity import SEC_FILE
    w = holding_world(tmp_path)
    store = w.store
    store.put_data("sec_tickers", SEC_FILE, {"status": "OK", "fields": ["cik", "name", "ticker", "exchange"],
                                             "data": [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"]]},
                   {"method": "GET", "url": "sec:tickers"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    _filings(store, "0001045810", [{"form": "10-Q", "filed": "2026-08-27", "period": "2026-07-26",
                                    "accession": "0001045810-26-000100", "document": "nvda-20260726.htm"}])
    [nvda] = [r for r in holdings(store)["reports"] if r["company"] == "name:NVDA"]
    assert nvda["record"] == SEC_RECORD and [r["form"] for r in nvda["reports"]] == ["10-Q"]
