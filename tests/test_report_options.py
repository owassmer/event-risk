"""A fund's report's option, where the report is the fund's rows: a listed contract's terms stand for the report's
only where the two are one contract, in one currency."""

from __future__ import annotations

from harness.derive import rows


def _option(currency: str | None, kind: str | None = None) -> tuple[dict, dict]:
    """A report's call on a share class listed in the US and at home under one ISIN, its strike 100 in `currency` as
    the report states it, and the contract listed in the US that its expiry, right and strike figure name there,
    American, 100 shares a contract, of Massive's `type` `kind`."""
    d = {"category": "OPT", "reference": {"security": {"isin": "CA0000000001"}}, "expiry": "2026-12-18",
         "right": "call", "strike": 100.0, "side": "purchased", **({"strike_currency": currency} if currency else {})}
    row = {"market_value": 3_000.0, "shares": 10, "units": "NC", "effective_date": "2026-09-30", "derivative": d,
           "currency": "USD"}
    listing = {"ticker": "O:XYZ261218C00100000", "exercise_style": "american", "shares_per_contract": 100,
               **({"type": kind} if kind else {})}
    options = {"report": {rows.report_option_key(row): {"ticker": "XYZ", "listings": [listing]}},
               "terms": {"closes": {"XYZ": {"2026-09-30": 120.0}},
                         "curves": [{"date": "2026-09-30", "curve": [[0.25, 0.04], [1.0, 0.04]]}],
                         "payer": {"XYZ": "XYZ"}, "paid": {"XYZ": {}}}}
    return row, options


def _read(currency, kind=None):
    row, options = _option(currency, kind)
    return rows._report_option(row, row["derivative"], 1_000_000.0, options, {"option": {}})


def test_a_report_option_in_another_currency_takes_no_us_listings_terms():
    """A contract listed in the US is in dollars. The report's call with its strike in dollars is that contract: it
    takes its style and its shares, and its delta from its price and its underlying's. The same call with its strike in
    Canadian dollars is another contract, a strike of 100 in another unit; and one whose strike's currency the report
    leaves unstated has a term no source states. Neither takes the listing's terms, so no delta and no exposure are
    worked from a dollar price and a dollar spot against its strike, and the fund's measured remainder holds it. The row's
    own currency is the one its value is reported in, and stands for nothing here. A binary listing, whose strike is a
    figure of a reported KPI, stands for no report's contract."""
    dollars = _read("USD")
    assert dollars["payoff"]["option"]["style"] == "american"
    assert dollars["payoff"]["option"]["underlying_per_contract"] == 100
    assert "exposure" in dollars and dollars["quote"]["status"] == "OK"
    assert _read("USD", "standard")["payoff"]["option"]["style"] == "american"
    for got in (_read("CAD"), _read(None), _read("USD", "binary")):
        assert got["payoff"]["option"]["style"] is None and "underlying_per_contract" not in got["payoff"]["option"]
        assert "exposure" not in got and "delta" not in got["quote"] and "status" not in got["quote"]
    assert rows.us_listed_terms({"strike_currency": "USD"}) and not rows.us_listed_terms({"strike_currency": "MXN"})
