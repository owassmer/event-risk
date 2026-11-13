"""A fund on one reference (MATH.md 1.6, 3.1): its measured slope is the multiple of its own value it carries in its
reference, named with its reference in the look-through's `multiples`; its census, Form N-CEN, whose Item C.3.c marks a
fund seeking a multiple or the inverse of a benchmark (harness.venues.sec.ncen), stays a stated fact of the fund."""

from __future__ import annotations

import httpx
import pytest

from harness.derive.portfolio import multiples
from harness.derive.rows import with_exposure
from harness.venues import sec

CENSUS = """<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/ncen"><formData>
<managementInvestmentQuestionSeriesInfo>
 <managementInvestmentQuestion><mgmtInvSeriesId>S000000001</mgmtInvSeriesId>
  <fundTypes><fundType>Exchange-Traded Fund</fundType><fundType>Inverse of a benchmark</fundType>
   <indexFundInfo fundType="Index Fund"/></fundTypes></managementInvestmentQuestion>
 <managementInvestmentQuestion><mgmtInvSeriesId>S000000002</mgmtInvSeriesId>
  <fundTypes><fundType>Exchange-Traded Fund</fundType></fundTypes></managementInvestmentQuestion>
</managementInvestmentQuestionSeriesInfo></formData></edgarSubmission>"""

FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><category term="N-CEN"/><content><accession-number>0000000001-26-000001</accession-number>
<filing-date>2026-08-14</filing-date><filing-type>N-CEN</filing-type>
<filing-href>https://www.sec.gov/Archives/edgar/data/1174610/000000000126000001/0000000001-26-000001-index.htm</filing-href>
</content></entry>
</feed>"""


def test_a_census_gives_each_series_its_fund_types_and_its_period():
    found = sec.census(CENSUS)
    assert found == {"series": {
        "S000000001": ["Exchange-Traded Fund", "Inverse of a benchmark"], "S000000002": ["Exchange-Traded Fund"]}}


def test_a_funds_census_says_whether_it_seeks_a_multiple_and_a_fund_with_none_filed_by_then_has_none(monkeypatch):
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("browse-edgar"):
            return httpx.Response(200, text=FEED)
        return httpx.Response(200, text=CENSUS)
    client = sec.SEC(http=httpx.Client(transport=httpx.MockTransport(answer)), interval=0.0)
    monkeypatch.setattr(client, "filer", lambda fund: ({"QLD": "S000000001", "QQQX": "S000000002"}[fund], "1174610"))
    lev = client.ncen("QLD", "2026-11-11")["payload"]
    assert (lev["seeks_multiple"], lev["filed"], lev["accession"]) == (True, "2026-08-14", "0000000001-26-000001")
    assert client.ncen("QQQX", "2026-11-11")["payload"]["seeks_multiple"] is False
    assert client.ncen("QLD", "2026-08-13")["payload"]["status"] == "NOT_FOUND"  # filed the day after


MEASURED = {"slope": 2.0123, "r2": 0.998, "sessions": 63, "from": "2026-08-07", "to": "2026-11-10"}
BASKET = {"index": {"name": "NASDAQ 100", "values": "I:NDX"}, "companies": [], "basket": {"fund": "QQQ"}}


def rows(to: dict) -> dict:
    """A fund on one reference, swaps on it, its slope measured."""
    return {"status": "OK", "results": [
        {"constituent_name": "SWAP ON THE REFERENCE", "kind": "derivative", "payoff": "linear", "exposure": None,
         "measured_by": "the reference", "to": to},
        {"constituent_name": "the reference", "kind": "derivative", "payoff": "linear", "exposure": MEASURED["slope"],
         "to": to, "measured": MEASURED}], "measured": MEASURED}


def test_the_payload_names_its_census_only_where_the_box_is_marked_and_the_slope_is_measured(monkeypatch):
    from harness.derive import rows as rows_module
    from harness.derive.assets import Kinds
    census = {"seeks_multiple": True, "form": "N-CEN", "accession": "A", "filed": "2026-08-14",
              "url": "u", "fund_types": ["Inverse of a benchmark"]}
    payload = {"status": "OK", "results": []}
    assert "seeks_multiple" not in with_exposure(payload, Kinds(), {}, census=census)  # no slope: no multiple stated
    monkeypatch.setattr(rows_module, "measure", lambda found, slope: [{"to": BASKET, "measured": slope}])
    got = with_exposure(payload, Kinds(), {}, slope=MEASURED, census=census)
    assert got["seeks_multiple"] == {"form": "N-CEN", "accession": "A", "filed": "2026-08-14", "url": "u"} and got["measured"] == MEASURED
    assert "seeks_multiple" not in with_exposure(payload, Kinds(), {}, slope=MEASURED,
                                                 census={**census, "seeks_multiple": False})


def test_multiples_name_each_funds_reference_and_its_measured_slope():
    company = {"isin": "US67066G1040", "figi": "BBG000BBJQV0"}
    resolved = lambda identifiers: {"ticker": "NVDA"} if identifiers == company else {"ticker": None}
    seeks = {"form": "N-CEN", "accession": "A", "filed": "2026-08-14", "url": "u"}
    found = multiples({"QLD": {**rows(BASKET), "seeks_multiple": seeks},
                       "NVDL": {**rows({"company": company}), "seeks_multiple": seeks},
                       "CONL": rows({"company": company}),  # its census marks no multiple: its slope is still its multiple
                       "ABSENT": {"status": "ABSENT"}}, resolved)
    assert found == {"QLD": {"reference": "name:QQQ", "multiple": 2.0123},
                     "NVDL": {"reference": "name:NVDA", "multiple": 2.0123},
                     "CONL": {"reference": "name:NVDA", "multiple": 2.0123}}
    itself = multiples({"QQQ": {**rows(BASKET), "seeks_multiple": seeks}}, resolved)  # its index's following fund
    assert itself == {}
