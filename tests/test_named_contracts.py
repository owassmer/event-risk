"""A contract a fund's row names (MATH.md 3.1; harness.derive.rows): read from its ticker or from a fixed form in its name,
never from words; its underlying resolved in Massive's own references; and, where no market lists it, priced at what
the fund's holdings state for it, every term a source states kept on the row, its exposure none only for want of
what no source states."""

from __future__ import annotations

import pytest

from harness.derive.assets import Kinds
from harness.derive.options import delta
from harness.derive.rows import FLEX_UNITS, close_moment, etf_global_rows, option_symbol, option_underlying


@pytest.mark.parametrize("row, symbol", [
    ({"constituent_ticker": "NDX   261218C29000000"}, "O:NDX261218C29000000"),          # the OCC's, padded
    ({"constituent_name": "NEE US 11/20/26 C80"}, "O:NEE261120C00080000"),             # Bloomberg's description
    ({"constituent_name": "SPXW US 10/28/22 C3920 Index"}, "O:SPXW221028C03920000"),  # with its market sector
    ({"constituent_name": "2 JNJ US 01/27/2025 C238.88"}, "O:2JNJ250127C00238880"),   # a FLEX one, its style digit
    ({"constituent_name": "SPX 03/31/2020 3401.27 P"}, "O:SPX200331P03401270"),       # ETF Global's own
    ({"constituent_name": "LLY 02/27/2026 1065 C", "asset_class": "Option"}, "O:LLY260227C01065000"),
    ({"constituent_name": "SPY CALL USD 772 20/NOV/2026  SPY US 11/20/26 C772"}, "O:SPY261120C00772000"),
])
def test_a_contract_named_in_a_fixed_form_is_that_contract(row, symbol):
    assert option_symbol(row) == symbol


@pytest.mark.parametrize("row", [
    {"constituent_name": "S&P 500 3970 FLEX CALL 12/07/2027"},                           # words name no root
    {"constituent_name": "SPY CALL USD 772 20/NOV/2026  SPY US 11/20/26 C770"},          # its two statements differ
    {"constituent_name": "SPX 03/31/2020 3401.27 P", "asset_class": "Equity"},           # its source says otherwise
    {"constituent_name": "SBAT 2024-A C 5.59 1/15/2030"},                                # a bond's name
])
def test_a_name_in_words_or_a_row_labelled_otherwise_names_no_contract(row):
    assert option_symbol(row) is None


INDEX = {"status": "OK", "results": {"ticker": "I:ABC", "market": "indices"}}
LISTING = {"status": "OK", "results": {"ticker": "ABC", "market": "stocks", "share_class_figi": "BBG0000000S1"}}
NONE = {"status": "NOT_FOUND"}


def test_an_underlying_is_what_the_contracts_record_says_or_what_its_root_names_in_one_reference_alone():
    company = {"company": {"constituent_ticker": "ABC", "figi": "BBG0000000S1"}}
    # its record's CFI code says which: an index's option is the index's, a share's the share's, whatever else the
    # root also names
    assert option_underlying("ABC", "I", LISTING, INDEX) == {"ticker": "I:ABC"}
    assert option_underlying("ABC", "S", LISTING, INDEX) == {"ticker": "ABC", **company}
    assert option_underlying("ABC", "I", LISTING, NONE) is None
    # with no record, the root names one of the two alone, or nothing here
    assert option_underlying("ABC", None, NONE, INDEX) == {"ticker": "I:ABC"}
    assert option_underlying("ABC", None, LISTING, NONE) == {"ticker": "ABC", **company}
    assert option_underlying("ABC", None, LISTING, INDEX) is None
    assert option_underlying("ABC", None, NONE, NONE) is None


def test_a_listing_its_reference_gives_no_figi_is_still_the_underlying_its_company_unconfirmed():
    # Massive's reference gives some US listings no share class FIGI (read live: MDT, LIN, ACN, NXPI): the contract is
    # still on that listing, priced by its ticker; which company it is waits for an identifier that confirms it
    bare = {"status": "OK", "results": {"ticker": "ABC", "market": "stocks", "type": "CS"}}
    assert option_underlying("ABC", "S", bare, NONE) == {"ticker": "ABC", "company": None}
    assert option_underlying("ABC", None, bare, NONE) == {"ticker": "ABC", "company": None}


DAY = "2026-06-30"
CURVE = [{"date": "2026-06-29", "curve": [[0.25, 0.04], [1.0, 0.04]]}]
TO = {"index": {"values": "I:ABC"}}


def fund(name):
    """A fund of one share and one contract, its net assets 1,000,000 by its rows' values over their weights: two
    contracts held, valued at 50,000, so 250 a unit of the underlying, which closed at 5,100 that day."""
    return {"effective_date": DAY, "results": [
        {"constituent_name": "A SHARE", "constituent_ticker": "SHR", "weight": 0.95, "market_value": 950_000.0,
         "effective_date": DAY, "asset_class": "Equity"},
        {"constituent_name": name, "weight": 0.05, "market_value": 50_000.0, "shares_held": 2.0,
         "effective_date": DAY, "asset_class": "Option"}]}


def terms(symbol, **more):
    return {"underlying": {symbol: {"ticker": "I:ABC", "to": TO}}, "payer": {"I:ABC": "ABCF"},
            "closes": {"I:ABC": {DAY: 5100.0}, "ABCF": {"2026-06-29": 500.0}}, "paid": {"ABCF": {"2026-03-16": 6.0}},
            "curves": CURVE, **more}


def contract(rows):
    return next(r for r in rows if r["kind"] == "derivative")


def test_a_contract_no_market_lists_is_priced_at_its_holdings_with_its_underlying_and_unsized_for_its_style_alone():
    symbol = option_symbol({"constituent_name": "ABC 12/18/2026 5000 C"})
    rows = etf_global_rows(fund("ABC 12/18/2026 5000 C"), Kinds(), {"terms": terms(symbol, unlisted=[symbol])})
    row = contract(rows)
    quote = row["quote"]
    assert (quote["price"], quote["price_from"], quote["underlying"], quote["underlying_price"]) == \
        (pytest.approx(250.0), "holdings", "I:ABC", 5100.0)
    assert quote["at"] == close_moment(DAY) and quote["rate"] == CURVE[0]["curve"]
    assert quote["dividend_yield"] == {"paid": 6.0, "price": 500.0, "payer": "ABCF"}
    assert row["payoff"]["option"]["style"] is None and row["payoff"]["option"]["contracts"] == 2.0
    assert row["exposure"] is None and "delta" not in quote and row["to"] == TO


def test_the_same_contract_with_its_style_stated_is_sized_at_the_delta_its_terms_give():
    symbol = option_symbol({"constituent_name": "4 ABC US 12/18/2026 C5000"})  # 4: a European FLEX contract
    rows = etf_global_rows(fund("4 ABC US 12/18/2026 C5000"), Kinds(), {"terms": terms(symbol)})
    row = contract(rows)
    worked = delta(right="call", style="european", strike=5000.0, expiry="2026-12-18", at=close_moment(DAY),
                   price=250.0, underlying_price=5100.0, rate=CURVE[0]["curve"],
                   dividend_yield={"paid": 6.0, "price": 500.0})
    assert 0.0 < worked["delta"] < 1.0
    assert row["quote"]["delta"] == pytest.approx(worked["delta"])
    assert row["exposure"] == pytest.approx(2.0 * FLEX_UNITS * worked["delta"] * 5100.0 / 1_000_000.0)


def test_a_contract_the_snapshot_no_longer_knows_is_valued_by_its_record_on_its_rows_date():
    symbol = option_symbol({"constituent_name": "ABC 12/18/2026 5000 C"})
    at = close_moment(DAY)
    record = {"exercise_style": "american", "shares_per_contract": 100, "underlying_ticker": "ABC"}
    standing = {"bid": 249.0, "ask": 251.0, "at": at}
    minute = int(__import__("datetime").datetime.fromisoformat(at).timestamp() * 1000) - 60_000
    read = terms(symbol, contracts={symbol: record}, at_close={symbol: standing},
                 minutes={"I:ABC": {DAY: [[minute, 5100.0]]}})
    gone = {"status": "NOT_FOUND", "error": "NOT_FOUND"}  # today's snapshot: the contract has expired since
    row = contract(etf_global_rows(fund("ABC 12/18/2026 5000 C"), Kinds(), {"quotes": {symbol: gone}, "terms": read}))
    worked = delta(right="call", style="american", strike=5000.0, expiry="2026-12-18", at=at, price=250.0,
                   underlying_price=5100.0, rate=CURVE[0]["curve"], dividend_yield={"paid": 6.0, "price": 500.0})
    assert row["payoff"]["option"]["style"] == "american" and row["quote"]["status"] == "OK"
    assert row["quote"]["price_from"] == "quote" and row["quote"]["underlying_price"] == 5100.0
    assert row["exposure"] == pytest.approx(2.0 * 100 * worked["delta"] * 5100.0 / 1_000_000.0)


def test_what_a_contracts_record_says_reaches_its_row_when_no_underlying_resolves():
    from harness.derive.portfolio import _options
    unlisted = option_symbol({"constituent_name": "ABC 12/18/2026 5000 C"})
    listed = option_symbol({"constituent_name": "ABD 12/18/2026 5000 C"})
    record = {"exercise_style": "european", "shares_per_contract": 100, "underlying_ticker": "ABD"}
    kept = {f"{listed}@{DAY}": {"data": "r1", "payload": {"status": "OK", "results": record}}}
    found = {"unlisted": [unlisted], "contracts": kept, "past": {listed: {"root": "ABD", "kind": "I", "day": DAY}}}
    read = _options({"options": found}, lambda ref: ref["payload"])  # no underlying read, no terms for a delta
    one = contract(etf_global_rows(fund("ABC 12/18/2026 5000 C"), Kinds(), read))
    assert (one["quote"]["price"], one["quote"]["price_from"]) == (pytest.approx(250.0), "holdings")
    assert one["payoff"]["option"]["underlying_per_contract"] == FLEX_UNITS and one["exposure"] is None
    assert "underlying" not in one["quote"] and "to" not in one
    other = contract(etf_global_rows(fund("ABD 12/18/2026 5000 C"), Kinds(), read))
    assert other["payoff"]["option"]["style"] == "european" and other["payoff"]["option"]["underlying_per_contract"] == 100
    assert other["quote"]["status"] == "OK" and other["exposure"] is None


def test_such_a_listing_is_priced_by_its_ticker_and_reaches_its_company_only_where_a_figi_confirms_it():
    from harness.derive.portfolio import _options
    symbol = option_symbol({"constituent_name": "ABC 12/18/2026 5000 C"})
    bare = {"status": "OK", "results": {"ticker": "ABC", "market": "stocks", "type": "CS"}}
    refs = {"ABC": {"listing": {"data": "l", "payload": bare}, "index": {"data": "i", "payload": NONE}}}
    found = {"unlisted": [symbol], "underlying_refs": refs}
    past = _options({"options": found}, lambda ref: ref["payload"])  # a past as-of: no share classes read
    assert past["terms"]["underlying"][symbol] == {"ticker": "ABC"}
    row = contract(etf_global_rows(fund("ABC 12/18/2026 5000 C"), Kinds(), past))
    assert row["quote"]["underlying"] == "ABC" and "to" not in row
    classes = {"ABC": {"data": "c", "payload": {"share_classes": ["BBG0000000S1"]}}}  # today: OpenFIGI's, by ticker
    today = _options({"options": {**found, "classes": classes}}, lambda ref: ref["payload"])
    row = contract(etf_global_rows(fund("ABC 12/18/2026 5000 C"), Kinds(), today))
    assert row["to"] == {"company": {"constituent_ticker": "ABC", "figi": "BBG0000000S1"}}


def test_a_row_naming_a_contract_expired_before_its_own_date_establishes_no_contract():
    # a row dated after the expiry its name states holds something, valued live, but not that contract: it is an
    # option whose terms no source states, with no price, underlying, style or delta drawn from the name
    named = "ABC US 12/18/2026 C5000"
    late = {**fund(named), "effective_date": "2027-01-05"}
    late["results"] = [{**row, "effective_date": "2027-01-05"} for row in late["results"]]
    symbol = option_symbol({"constituent_name": named})
    row = contract(etf_global_rows(late, Kinds(), {"terms": terms(symbol, unlisted=[symbol])}))
    assert row["exposure"] is None and "quote" not in row and "to" not in row
    assert row["payoff"]["option"]["strike"] is None and row["payoff"]["option"]["expiry"] is None
    current = contract(etf_global_rows(fund(named), Kinds(), {"terms": terms(symbol, unlisted=[symbol])}))
    assert current["payoff"]["option"]["expiry"] == "2026-12-18" and current["quote"]["price_from"] == "holdings"


def test_a_contract_read_from_its_terms_is_a_derivative_wherever_its_kind_is_read_whatever_its_source_labels():
    """MATH.md 3.1: a fund's holding is shown with its kind of asset, one kind: a contract the look-through read from its
    terms is a derivative where its exposure is counted, where the portfolio's weight is booked by kind, and where the
    fund is shown dividing by kind (each asks Kinds.of), though its source labels it as nothing, as a buffer fund's
    rows are."""
    payload = fund("4ABC US 12/18/26 C5000")
    payload["results"][1].pop("asset_class")
    symbol = option_symbol(payload["results"][1])
    row = contract(etf_global_rows(payload, Kinds(), {"terms": terms(symbol, unlisted=[symbol])}))
    assert row["payoff"]["option"]["symbol"] == symbol == "O:4ABC261218C05000000"
    assert Kinds().of(row) == "derivative"
    assert Kinds().of({k: v for k, v in row.items() if k not in ("payoff", "kind")}) == "other"  # its labels alone
