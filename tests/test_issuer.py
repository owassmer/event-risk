"""A company is the legal entity behind its securities (MATH.md 3.1): securities whose issuer is one LEI are one
company, the issuer as a fund's own Form N-PORT report gives a share, or as GLEIF gives a depositary receipt or a listing
abroad, never by a name; where that company names one CIK at most and one id a link names at most. On data in each
source's own shapes; nothing reaches the network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from harness.derive import default_registry
from harness.derive.identity import SEC_FILE, Lookup, entities, issuers
from harness.store import Store
from harness.venues.gleif import GLEIF, valid_lei
from harness.venues.http import VenueError
from massive_data import ASOF, FETCHED, flat_prices
from test_one_name import SEC, fund, looked, ref

PETROBRAS = "5493000J801JZRCMFE49"


@pytest.fixture
def store(tmp_path):
    s = Store.create(tmp_path / "scratch-issuer", registry=default_registry())
    flat_prices(s)
    s.put_data("sec_tickers", SEC_FILE, SEC, {"method": "GET", "url": "sec"}, retrieved_at=FETCHED, range=(ASOF, ASOF))
    yield s
    s.close()


def put(store, kind, subject, payload):
    store.put_data(kind, subject, payload, {"method": "GET", "url": kind}, retrieved_at=FETCHED, range=(ASOF, ASOF))


def report(store, fund_, rows):
    """A fund's Form N-PORT report as harness.venues.sec keeps it: each holding with its issuer's LEI."""
    put(store, "nport", fund_, {"status": "OK", "fund": fund_, "effective_date": "2026-09-30", "net_assets": 1e9,
                                "results": [{"asset_category": "EC", "issuer_category": "CORP", "currency": "USD",
                                             "effective_date": "2026-09-30", **r} for r in rows]})


def gleif(store, isin, *leis):
    put(store, "lei", isin, {"status": "OK" if leis else "NOT_FOUND", "isin": isin, "published": "2026-11-04T08:00:00Z",
                             "records": [{"lei": x, "name": "an entity", "jurisdiction": "BR", "entity_status": "ACTIVE",
                                          "registration_status": "ISSUED"} for x in leis]})


def petrobras(store, *, asked=True, preferred_weight=0.03, maps_to="FIGIPBR"):
    """Petrobras: its receipt PBR on the NYSE, whose ISIN a fund's row gives under its ticker, OpenFIGI maps to its own
    FIGI, and GLEIF names Petrobras the issuer of; its common and preferred shares in São Paulo, which no US listing
    shares a class with, held by fund V, whose report names Petrobras the issuer of both, and of a bond of its own."""
    ref(store, "PBR", "ADRC", cik="0001119639")
    put(store, "ticker_isin", "PBR", {"status": "OK", "ticker": "PBR", "isins": ["US71654V4086"]})
    put(store, "figi", "US71654V4086", {"status": "OK", "isin": "US71654V4086", "composites": [maps_to],
                                        "listings": [{"ticker": "PBR", "exchCode": "US", "compositeFIGI": maps_to}]})
    if asked:
        gleif(store, "US71654V4086", PETROBRAS)
    for isin in ("BRPETRACNOR9", "BRPETRACNPR6"):
        put(store, "figi", isin, {"status": "NOT_FOUND", "isin": isin, "listings": [], "composites": []})
    ref(store, "V", "ETF")
    fund(store, "V", [{"constituent_ticker": "PETR3", "isin": "BRPETRACNOR9", "country_of_exchange": "BR",
                       "weight": 0.02},
                      {"constituent_ticker": "PETR4", "isin": "BRPETRACNPR6", "country_of_exchange": "BR",
                       "weight": preferred_weight},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 1 - 0.02 - preferred_weight}])
    report(store, "V", [{"constituent_name": "Petroleo Brasileiro SA", "isin": "BRPETRACNOR9", "lei": PETROBRAS,
                         "weight": 0.02},
                        {"constituent_name": "Petroleo Brasileiro SA", "isin": "BRPETRACNPR6", "lei": PETROBRAS,
                         "asset_category": "EP", "weight": preferred_weight},
                        {"constituent_name": "Petroleo Brasileiro SA 6.85% 2115", "isin": "US71647NAN93",
                         "lei": PETROBRAS, "asset_category": "DBT", "weight": 0.01}])


def test_a_receipt_held_and_its_companys_home_shares_in_a_fund_are_one_name_priced_through_the_receipt(store):
    """MATH.md 3.1: a company is one name whatever securities the portfolio holds it by, priced through the one the member
    holds. The receipt and the two home classes share no CIK and no share class; their issuer is one legal entity, as
    GLEIF names it of the receipt and the fund's report of each class, so a shock on Petrobras reaches every dollar of
    it. Its bond in the same report is no share of it, and joins nothing."""
    petrobras(store)
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "PBR", "dollars": 1000.0},
                                                       {"ticker": "V", "dollars": 10000.0}]})
    out = looked(store)
    assert out["e"]["name:PBR"] == pytest.approx(1000.0 + 200.0 + 300.0)
    assert out["securities"]["name:PBR"] == pytest.approx(
        {"PBR": 1000.0, "BRPETRACNOR9:BR": 200.0, "BRPETRACNPR6:BR": 300.0})
    assert out["kinds"]["name:PBR"]["security"] == "PBR"
    assert not [n for n in out["e"] if "BRPETRA" in n]
    assert out["links"]["looks_through_to:name:V>name:PBR"]["fields"]["dollars"] == pytest.approx(500.0)


def test_a_store_never_told_the_receipts_issuer_joins_what_its_funds_reports_name(store):
    """A saved store reads with no keys: where GLEIF was never asked, the receipt's issuer is unknown, and
    it stays its own name; the home classes the fund's report names one issuer of are still one company, named by
    its home listing, the one in its ISIN's country, the first by the rule."""
    petrobras(store, asked=False)
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "PBR", "dollars": 1000.0},
                                                       {"ticker": "V", "dollars": 10000.0}]})
    out = looked(store)
    assert out["e"]["name:PBR"] == pytest.approx(1000.0)
    assert out["securities"]["name:BRPETRACNOR9:BR"] == pytest.approx(
        {"BRPETRACNOR9:BR": 200.0, "BRPETRACNPR6:BR": 300.0})


def test_an_isin_given_under_the_receipts_ticker_is_taken_where_openfigi_maps_it_to_the_receipt(store):
    """A ticker passes between securities, a FIGI does not: an ISIN a fund's row gives under the receipt's ticker that
    OpenFIGI maps to another listing's FIGI names another security, and the receipt's issuer stays unknown."""
    petrobras(store, maps_to="FIGIOTHER")
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "PBR", "dollars": 1000.0},
                                                       {"ticker": "V", "dollars": 10000.0}]})
    out = looked(store)
    assert out["e"]["name:PBR"] == pytest.approx(1000.0)


def test_funds_of_one_trust_whose_report_names_the_trust_their_issuer_stay_each_its_own_fund(store):
    """A fund of funds' report can name the trust each fund it holds is a series of as that fund's issuer, one LEI for
    several funds (read live: three Select Sector SPDR funds, six of First Trust's): a fund is its own name, and its
    issuer joins nothing."""
    for f in ("F", "A", "B"):
        ref(store, f, "ETF")
    fund(store, "F", [{"constituent_ticker": "A", "figi": "FIGIA", "isin": "US0000000A11", "weight": 0.5},
                      {"constituent_ticker": "B", "figi": "FIGIB", "isin": "US0000000B10", "weight": 0.5}])
    for f in ("A", "B"):
        fund(store, f, [{"constituent_ticker": "USD", "asset_class": "Cash", "weight": 1.0}])
    report(store, "F", [{"constituent_name": "The Trust", "isin": "US0000000A11", "lei": PETROBRAS, "weight": 0.5},
                        {"constituent_name": "The Trust", "isin": "US0000000B10", "lei": PETROBRAS, "weight": 0.5}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "F", "dollars": 1000.0}]})
    out = looked(store)
    assert out["kinds"]["name:A"]["kind"] == "fund" and out["kinds"]["name:B"]["kind"] == "fund"
    assert not out["securities"].get("name:A") and not out["securities"].get("name:B")


def test_a_share_of_the_held_company_below_the_floor_reaches_it_by_its_issuer(store):
    """MATH.md 3.1: a company at any exposure that the portfolio already names becomes a link, so that its own shock
    reaches every dollar of exposure to it: the preferred at a tenth of a percent of the fund, below the floor, by the
    issuer its report names."""
    petrobras(store, preferred_weight=0.001)
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "PBR", "dollars": 1000.0},
                                                       {"ticker": "V", "dollars": 10000.0}]})
    out = looked(store)
    assert out["e"]["name:PBR"] == pytest.approx(1000.0 + 200.0 + 10.0)


def test_shares_named_alike_with_no_issuer_in_common_are_two_companies(store):
    """A company is known by its identifiers, never by its name: two lines a report names alike, one with an LEI and one
    whose LEI the report gives as N/A, stay two names."""
    ref(store, "V", "ETF")
    for isin in ("KR7005930003", "KR7005931001"):
        put(store, "figi", isin, {"status": "NOT_FOUND", "isin": isin, "listings": [], "composites": []})
    fund(store, "V", [{"constituent_ticker": "005930", "isin": "KR7005930003", "country_of_exchange": "KR",
                       "weight": 0.3},
                      {"constituent_ticker": "005935", "isin": "KR7005931001", "country_of_exchange": "KR",
                       "weight": 0.1},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.6}])
    report(store, "V", [{"constituent_name": "Samsung Electronics Co Ltd", "isin": "KR7005930003",
                         "lei": "9884007ER46L6N7EI764", "weight": 0.3},
                        {"constituent_name": "Samsung Electronics Co Ltd", "isin": "KR7005931001", "lei": "N/A",
                         "asset_category": "EP", "weight": 0.1}])
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "V", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"]["name:KR7005930003:KR"] == pytest.approx(300.0)
    assert out["e"]["name:KR7005931001:KR"] == pytest.approx(100.0)


def test_a_company_is_what_its_identifiers_make_one_where_they_agree():
    """identity.entities: the listings of one share class are one security; a CIK and an issuer's LEI each join the
    securities it names. An identifier is taken whole or not at all: an LEI naming securities of two CIKs, or a CIK
    whose securities name two issuers, joins nothing it names, and that CIK names no company; and a company two links
    name by two ids keeps each link's meaning."""
    receipt = {"isin": None, "cik": 1, "lei": "L", "linked": {"PBR"}}
    common, preferred = {"isin": "P3", "cik": None, "lei": "L", "linked": set()}, \
        {"isin": "P4", "cik": None, "lei": "L", "linked": set()}
    members = {"PBR": receipt, "P3.SA": common, "P4.SA": preferred}
    assert entities(members) == {"PBR": "cik:1", "P3.SA": "cik:1", "P4.SA": "cik:1"}
    # the same LEI given a bank's share, under another CIK: one of them is wrong, and nothing it names is joined by it
    assert entities({**members, "BANK": {"isin": None, "cik": 2, "lei": "L", "linked": set()}}) == \
        {"PBR": "cik:1", "P3.SA": "isin:P3", "P4.SA": "isin:P4", "BANK": "cik:2"}
    # the preferred held directly, linked to its own id: the receipt and it stay two names, each as its link named it
    assert entities({**members, "P4.SA": {**preferred, "linked": {"P4.SA"}}})["P4.SA"] == "isin:P4"
    # two lines abroad, no CIK: one company by their issuer
    assert entities({"P3.SA": common, "P4.SA": preferred}) == {"P3.SA": "lei:L", "P4.SA": "lei:L"}
    # a parent's and its listed subsidiary's lines, which the SEC's list files under the parent's CIK through their
    # over-the-counter lines, and whose issuers are two: two companies, neither named by that CIK
    parent, child = {"isin": "JP1", "cik": 9, "lei": "A", "linked": set()}, {"isin": "JP2", "cik": 9, "lei": "B",
                                                                          "linked": set()}
    assert entities({"9984.T": parent, "9434.T": child}) == {"9984.T": "isin:JP1", "9434.T": "isin:JP2"}
    # a class with no issuer named still joins its company by its CIK
    assert entities({"GOOGL": {"isin": None, "cik": 3, "lei": "G", "linked": set()},
                     "GOOG": {"isin": None, "cik": 3, "lei": None, "linked": set()}}) == {"GOOGL": "cik:3", "GOOG": "cik:3"}


def softbank(store, *, issuers_named=True):
    """SoftBank Group and SoftBank Corp, its listed subsidiary: each one's Tokyo line, whose share class OpenFIGI maps to
    one over-the-counter line in the US, SFTBF and SFBQF, which the SEC's list files under one CIK, SoftBank Group's;
    held by fund J, whose report names each line's own issuer."""
    put(store, "sec_tickers", SEC_FILE, {**SEC, "data": SEC["data"] + [[1065521, "SOFTBANK GROUP CORP.", "SFTBF", "OTC"],
                                                                       [1065521, "SOFTBANK GROUP CORP.", "SFBQF", "OTC"]]})
    for isin, otc in (("JP3436100006", "SFTBF"), ("JP3732000009", "SFBQF")):
        put(store, "figi", isin, {"status": "OK", "isin": isin, "composites": [f"FIGI{otc}"],
                                  "listings": [{"ticker": otc, "exchCode": "US", "compositeFIGI": f"FIGI{otc}"}]})
        put(store, "ticker_ref", otc, {"status": "OK", "results": {"ticker": otc, "type": "OS", "market": "otc",
                                                                   "composite_figi": f"FIGI{otc}"}})
    put(store, "company_listings", "0001065521", {"status": "OK", "results": []})
    ref(store, "J", "ETF")
    fund(store, "J", [{"constituent_ticker": "9984", "isin": "JP3436100006", "country_of_exchange": "JP", "weight": 0.3},
                      {"constituent_ticker": "9434", "isin": "JP3732000009", "country_of_exchange": "JP", "weight": 0.2},
                      {"constituent_ticker": "USD", "asset_class": "Cash", "weight": 0.5}])
    if issuers_named:
        report(store, "J", [{"constituent_name": "SoftBank Group Corp", "isin": "JP3436100006",
                             "lei": "549300KB6NK5SBD14S87", "weight": 0.3},
                            {"constituent_name": "SoftBank Corp", "isin": "JP3732000009",
                             "lei": "549300DAQ1CVT6CXN342", "weight": 0.2}])


def test_a_parent_and_its_listed_subsidiary_that_one_cik_files_two_issuers_of_are_two_companies(store):
    """A CIK and an LEI each name the legal entity behind a security. The SEC's list files SoftBank Corp's line over
    the counter under SoftBank Group's CIK, and the fund's report names each line's own issuer: where the two disagree,
    the securities stay two companies, a shock on one reaching none of the other's dollars, and that CIK names
    neither."""
    softbank(store)
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "J", "dollars": 1000.0}]})
    out = looked(store)
    assert out["e"]["name:JP3436100006:JP"] == pytest.approx(300.0)
    assert out["e"]["name:JP3732000009:JP"] == pytest.approx(200.0)
    assert "name:SFTBF" not in out["e"]


def test_with_no_issuer_to_contradict_it_one_cik_makes_one_company(store):
    """Where no report names the lines' issuers, nothing contradicts the CIK, and its lines are one company, its id
    the CIK's first US ticker: what the store's data establishes."""
    softbank(store, issuers_named=False)
    store.add("Portfolio", {"asof": ASOF, "holdings": [{"ticker": "J", "dollars": 1000.0}]})
    out = looked(store)
    assert out["securities"]["name:SFTBF"] == pytest.approx({"JP3436100006:JP": 300.0, "JP3732000009:JP": 200.0})


def test_a_shares_issuer_is_the_one_its_reports_agree_on():
    """identity.issuers: equity rows only, a valid LEI only; a share two reports name two issuers of has none."""
    a = {"results": [{"isin": "BRPETRACNOR9", "lei": PETROBRAS, "asset_category": "EC"},
                     {"isin": "US71647NAN93", "lei": PETROBRAS, "asset_category": "DBT"},
                     {"isin": "KR7005931001", "lei": "N/A", "asset_category": "EP"},
                     {"isin": "XS0000000001", "lei": "549300KB6NK5SBD14S87", "asset_category": "EC"}]}
    b = {"results": [{"isin": "XS0000000001", "lei": "549300DAQ1CVT6CXN342", "asset_category": "EC"}]}
    assert issuers([a, b]) == {"BRPETRACNOR9": PETROBRAS}
    assert valid_lei(PETROBRAS) and not valid_lei("5493000J801JZRCMFE48")


def test_gleifs_issuer_is_one_lei_or_none():
    payloads = {("lei", "A"): None,
                ("lei", "US71654V4086"): {"records": [{"lei": PETROBRAS}]},
                ("lei", "US0000000002"): {"records": [{"lei": PETROBRAS}, {"lei": "549300KB6NK5SBD14S87"}]},
                ("lei", "US0000000003"): {"records": [{"lei": "NOT-AN-LEI"}]}}
    look = Lookup(lambda kind, subject: payloads.get((kind, subject)), ASOF)
    assert look.issuer("US71654V4086") == PETROBRAS
    assert look.issuer("US0000000002") is None and look.issuer("US0000000003") is None
    assert look.issuer("not an isin") is None and look.issuer(None) is None


class _Answer:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_gleifs_answer_is_kept_in_its_own_shape_and_a_read_that_fails_is_no_answer(monkeypatch):
    """harness.venues.gleif: an ISIN's records as GLEIF's JSON:API gives them; none as NOT_FOUND; a 5xx asked again;
    a read that fails past its attempts raises, so no read stands as an answer of no issuer, and the next read asks
    GLEIF again."""
    monkeypatch.setattr("harness.venues.gleif.time.sleep", lambda s: None)
    record = {"type": "lei-records", "id": PETROBRAS, "attributes": {
        "lei": PETROBRAS, "entity": {"legalName": {"name": "PETROLEO BRASILEIRO S A PETROBRAS"}, "jurisdiction": "BR",
                                     "status": "ACTIVE"}, "registration": {"status": "ISSUED"}}}
    asked: list[str] = []
    answers = [urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO()),
               _Answer({"data": [record], "meta": {"goldenCopy": {"publishDate": "2026-11-15T08:00:00Z"}}}),
               _Answer({"data": [], "meta": {}})]

    def opener(request, timeout):
        asked.append(request.full_url)
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    g = GLEIF(opener=opener)
    found = g.lei("US71654V4086", ASOF)
    assert found["payload"] == {"status": "OK", "isin": "US71654V4086", "published": "2026-11-15T08:00:00Z",
                                "records": [{"lei": PETROBRAS, "name": "PETROLEO BRASILEIRO S A PETROBRAS",
                                             "jurisdiction": "BR", "entity_status": "ACTIVE",
                                             "registration_status": "ISSUED"}]}
    assert asked[0].endswith("filter%5Bisin%5D=US71654V4086") and len(asked) == 2
    assert g.lei("TW0002330008", ASOF)["payload"]["status"] == "NOT_FOUND"

    def failing(request, timeout):
        raise urllib.error.HTTPError("u", 502, "bad gateway", {}, io.BytesIO())

    down = GLEIF(opener=failing)
    with pytest.raises(VenueError, match="502"):
        down.lei("US71654V4086", ASOF)
    with pytest.raises(VenueError, match="502"):  # asked, and failed again
        down.lei("US8740391003", ASOF)
