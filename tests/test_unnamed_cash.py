"""A fund's row that names nothing: with its shares equal to its value, it is cash where the fund's latest
report holds only cash, contracts on one reference and that reference's shares, since every other position of such a
fund is identified by its own row; elsewhere it stays what nothing says it is. Read from the report's own codes and
the row's structured fields, never its name. The reports here are in Form N-PORT's shapes as read for a buffer fund
on one fund's shares (FSEP), a merger-arbitrage fund of swaps on many companies (MNA), and a currency-hedged fund of
forwards, futures and shares (DBEF)."""

from __future__ import annotations

import pytest

from harness.derive import option_funds
from harness.derive.assets import Kinds, in_dollars, names_nothing
from harness.derive.portfolio import fund_kinds
from harness.derive.rows import cash_and_one_reference, nport_rows, option_symbol, with_exposure
from test_named_contracts import DAY, terms

SPY = {"issuer": "SPDR S&P 500 ETF TRUST", "cusip": "78462F103", "isin": "US78462F1030", "ticker": "SPY"}
OTHER = {"issuer": "ANOTHER CO", "cusip": "000000AA1", "isin": "US000000AA19", "ticker": "ANO"}
BILL = "912797QZ7"


def opt(reference, value=1000.0, **more):
    return {"constituent_name": "CBOE GLOBAL MARKETS, INC.", "asset_category": "DE", "issuer_category": "CORP",
            "market_value": value, "shares": 10.0, "units": "NC", "currency": "USD", "weight": 0.9,
            "derivative": {"category": "OPT", "reference": reference, "right": "call", "side": "purchased",
                           "strike": 100.0, "expiry": "2027-09-17"}, **more}


def swap(security, value=0.0):
    return {"constituent_name": "A SWAP", "asset_category": "DE", "issuer_category": "CORP", "market_value": value,
            "currency": "USD", "weight": 0.0,
            "derivative": {"category": "SWP", "reference": {"security": security}, "notional": 1_000_000.0,
                           "currency": "USD"}}


def money_market(value=40.0):
    return {"constituent_name": "A GOVERNMENT CASH FUND", "constituent_ticker": "MMXXX", "cusip": "262006208",
            "asset_category": "STIV", "issuer_category": "RF", "market_value": value, "shares": value,
            "units": "NS", "currency": "USD", "weight": 0.003}


def share(security, value=500.0):
    return {"constituent_name": security["issuer"], "constituent_ticker": security["ticker"], "cusip": security["cusip"],
            "isin": security["isin"], "asset_category": "EC", "issuer_category": "CORP", "market_value": value,
            "shares": 5.0, "units": "NS", "currency": "USD", "weight": 0.05}


def report(*rows):
    return {"status": "OK", "fund": "FUND", "effective_date": "2026-08-31", "net_assets": 1_000_000.0,
            "results": [{"effective_date": "2026-08-31", **r} for r in rows]}


def reads(rep, kinds=Kinds()):
    return cash_and_one_reference(nport_rows(rep, kinds, {}))


def test_a_report_of_cash_contracts_on_one_reference_and_its_shares_is_such_a_fund():
    on_spy = {"security": SPY}
    assert reads(report(opt(on_spy), opt(on_spy, -50.0), money_market()))["company"]["cusip"] == SPY["cusip"]
    assert reads(report(opt(on_spy), share(SPY), money_market())) is not None  # the reference's own shares
    bill = {"constituent_name": "UNITED STATES TREASURY BILL", "cusip": BILL, "asset_category": "DBT",
            "issuer_category": "UST", "market_value": 30.0, "currency": "USD", "weight": 0.03}
    assert reads(report(opt(on_spy), bill), Kinds(bills=frozenset({BILL}))) is not None  # a bill is cash (MATH.md 3.1)
    on_index = {"index": {"name": "AN INDEX", "identifier": "ABC"}}
    assert reads(report(opt(on_index), opt(on_index, -20.0), money_market())) is not None
    assert reads(report(swap(SPY), money_market())) is not None  # a swap is a contract as an option is


@pytest.mark.parametrize("rows", [
    pytest.param([opt({"security": SPY}), share(OTHER), money_market()], id="another company's shares"),
    pytest.param([opt({"security": SPY}), opt({"security": OTHER}), money_market()], id="two references"),
    pytest.param([opt({"index": {"identifier": "ABC"}}), opt({"index": {"identifier": "XYZ"}})], id="two indexes"),
    pytest.param([opt({"security": SPY}), {"constituent_name": "A BOND", "cusip": "123456AB1", "asset_category": "DBT",
                                          "issuer_category": "CORP", "market_value": 30.0, "currency": "USD",
                                          "weight": 0.03}], id="a bond"),
    pytest.param([money_market(), share(SPY)], id="no contract"),
    pytest.param([opt({"index": {"name": "AN INDEX"}}), money_market()], id="an index nothing identifies"),
    # MNA: swaps on many companies, and the shares of many
    pytest.param([swap(SPY), swap(OTHER), share(SPY), share(OTHER), money_market()], id="MNA"),
    # DBEF: currency forwards, futures on an index, and shares
    pytest.param([{"constituent_name": "A FORWARD", "asset_category": "DFE", "market_value": 10.0, "currency": "USD",
                   "weight": 0.0, "derivative": {"category": "FWD", "bought": {"currency": "USD", "amount": 100.0},
                                                 "sold": {"currency": "EUR", "amount": 90.0}}},
                  {**opt({"index": {"identifier": "SX5E"}}), "derivative": {
                      "category": "FUT", "reference": {"index": {"identifier": "SX5E"}}, "notional": 1000.0,
                      "currency": "USD", "payoff_profile": "Long"}},
                  share(OTHER), money_market()], id="DBEF"),
])
def test_a_report_holding_anything_else_is_not(rows):
    assert reads(report(*rows)) is None


def test_an_index_its_report_names_by_a_held_securitys_identifier_is_that_security():
    """An identifier a report's derivative gives its reference names first what the same report holds: TSLL's swaps on
    Tesla, coded as on something other than equity (DO) and naming their reference as an index "TSLA", refer to the
    Tesla shares the fund holds under that ticker, whatever OpenFIGI reads TSLA as, an index of that name included;
    and EDC's swaps on "EEM", which OpenFIGI's index search reads as another index, refer to the iShares fund the
    report holds. Each such report holds only cash, contracts on one reference and that reference's shares. An
    identifier the report holds nothing under is read as before."""
    tesla = {"issuer": "TESLA INC", "cusip": "88160R101", "isin": "US88160R1014", "ticker": "TSLA"}
    on_tsla = {**swap(SPY), "asset_category": "DO",
               "derivative": {"category": "SWP", "reference": {"index": {"name": "TESLA INC", "identifier": "TSLA"}},
                              "notional": 1_000_000.0, "currency": "USD"}}
    rep = report(on_tsla, on_tsla, share(tesla), money_market())
    listing = {"identifier": "TSLA", "index": None, "securities": [], "listed": ["BBG001SQKGD7"]}
    an_index = {**listing, "index": {"figi": "BBG000000IDX"}, "listed": []}  # an index of that name
    for readings in ({}, {"TSLA": listing}, {"TSLA": an_index}):
        rows = nport_rows(rep, Kinds(), readings)
        assert rows[0]["to"] == {"company": {k: v for k, v in share(tesla).items() if k in (
            "constituent_name", "constituent_ticker", "cusip", "isin")}}
        assert cash_and_one_reference(rows, readings) is not None
    ishares = {"issuer": "ISHARES MSCI EMERGINGS MARKETS", "cusip": None, "isin": "US4642872349", "ticker": "EEM"}
    on_eem = {**on_tsla, "derivative": {**on_tsla["derivative"], "reference": {"index": {"identifier": "EEM"}}}}
    europe = {"identifier": "EEM", "index": {"figi": "BBG00XRS3Q87", "name": "BBG Europe EM L/M PR"}, "securities": []}
    edc = report(on_eem, on_eem, {**share(ishares), "issuer_category": "RF"}, money_market(), money_market(41.0))
    assert cash_and_one_reference(nport_rows(edc, Kinds(), {"EEM": europe}), {"EEM": europe}) is not None
    elsewhere = report(on_eem, share(tesla), money_market())  # "EEM" names nothing this report holds: an index
    assert nport_rows(elsewhere, Kinds(), {"EEM": europe})[0]["to"]["index"]["figi"] == "BBG00XRS3Q87"


def test_a_row_that_names_nothing_and_is_stated_in_dollars_takes_the_kind_its_fund_says():
    row = {"constituent_name": "A GOVERNMENT CASH FUND 289", "market_value": 14705740.65, "shares_held": 14705740.65,
           "weight": 0.01, "effective_date": DAY}
    assert names_nothing(row) and in_dollars(row)
    assert Kinds().of(row) == "other" and Kinds(unnamed="cash").of(dict(row)) == "cash"
    for named in ({"constituent_ticker": "MMXXX"}, {"us_code": "262006208"}, {"isin": "US2620062081"},
                  {"figi": "BBG000000001"}, {"sedol": "B000000"}):
        assert Kinds(unnamed="cash").of({**row, **named}) == "other"  # a row that names something is read by it
    assert Kinds(unnamed="cash").of({**row, "shares_held": 120.0}) == "other"  # shares that are not its dollars
    assert Kinds(unnamed="cash").of({**row, "asset_class": "Swap"}) == "derivative"  # a row its source labels
    assert Kinds(unnamed="cash", unlabelled="commodity").of({**row, "shares_held": 120.0}) == "commodity"


def holdings(*more):
    """A buffer fund's holdings as ETF Global gives them: its FLEX contracts named only in Bloomberg's form, and its money
    market fund under a name alone, its shares its value."""
    return {"effective_date": DAY, "results": [
        {"constituent_name": "4 ABC US 12/18/2026 C5000", "weight": 0.97, "market_value": 970_000.0,
         "shares_held": 2.0, "effective_date": DAY},
        {"constituent_name": "4 ABC US 12/18/2026 C5500", "weight": -0.02, "market_value": -20_000.0,
         "shares_held": -2.0, "effective_date": DAY},
        {"constituent_name": "A GOVERNMENT CASH FUND 289", "weight": 0.05, "market_value": 50_000.0,
         "shares_held": 50_000.0, "effective_date": DAY}, *more]}


ON_ABC = report(opt({"index": {"name": "AN INDEX", "identifier": "ABC"}}),
                opt({"index": {"name": "AN INDEX", "identifier": "ABC"}}, -20.0), money_market())
MNA = report(swap(SPY), swap(OTHER), share(SPY), share(OTHER), money_market())


def unnamed_row(payload):
    return next(r for r in payload["results"] if r["constituent_name"].startswith("A GOVERNMENT"))


def test_its_rows_read_it_as_cash_where_its_report_holds_only_cash_and_contracts_on_one_reference():
    symbols = [option_symbol(r) for r in holdings()["results"][:2]]
    read = {"terms": terms(symbols[0])}
    got = with_exposure(holdings(), Kinds(), report=ON_ABC, options=read)
    row = unnamed_row(got)
    assert (row["kind"], row["payoff"], row["exposure"]) == ("cash", "cash", 0.05)
    assert got["unnamed"] == {"kind": "cash", "report_date": "2026-08-31"}
    assert fund_kinds(Kinds(), None, got).of(row) == "cash"  # the look-through takes the same kinds
    for other_report in (MNA, None, {"status": "NOT_FOUND", "fund": "FUND"}):  # anything else, or no report read
        got = with_exposure(holdings(), Kinds(), report=other_report, options=read)
        row = unnamed_row(got)
        assert (row["kind"], row["payoff"]) == ("other", "holding") and "unnamed" not in got
        assert fund_kinds(Kinds(), None, got).of(row) == "other"


def test_so_its_contracts_are_its_whole_payoff():
    """MATH.md 1.6: where a fund's rows are contracts on one reference, its shares and cash, its contracts are its whole
    payoff. The money market row was what kept a buffer fund from it."""
    symbols = {option_symbol(r) for r in holdings()["results"][:2]}
    read = {"terms": {**terms(sorted(symbols)[0]),
                      "underlying": {s: {"ticker": "I:ABC", "to": {"index": {"values": "I:ABC"}}} for s in symbols}}}

    class Reads:
        asked: list = []

        def data(self, *a, **k):
            self.asked.append(a)
            return {"data": False}

        def payload(self, ref):
            return {"status": "OK", "results": []}

    for rep, whole in ((ON_ABC, True), (MNA, False)):
        rows = with_exposure(holdings(), Kinds(), report=rep, options=read)
        specs = option_funds.inputs(Reads(), {"asof": DAY}, {"FUND": rows}, "2026-12-01")
        assert specs["name:FUND"]["full"] is whole
        if whole:
            assert specs["name:FUND"]["cash"] == pytest.approx(0.05)
