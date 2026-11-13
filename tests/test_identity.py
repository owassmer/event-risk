"""A fund's holding listed abroad resolves to the security the fund holds, on its home market (harness.derive.identity):
its listing `{ISIN}:{country}`, the subject `home` reads, in place of a US line over the counter, by its identifiers and
whether or not a source prices it, a US listing of its share class naming its company; and a company's id and the
security that prices it (MATH.md 3.1)."""

from __future__ import annotations

from harness.derive.identity import Lookup, home_listing, resolve


def lookup(answers: dict, asked: list | None = None) -> Lookup:
    def read(kind, subject):
        if asked is not None:
            asked.append(kind)
        return answers.get((kind, subject))
    return Lookup(read, "2026-11-06")


def reference(ticker, kind, market, cik=None):
    return {"status": "OK", "results": {"ticker": ticker, "type": kind, "market": market, "cik": cik}}


def test_a_holding_listed_abroad_with_only_a_line_over_the_counter_is_priced_on_its_home_market():
    row = {"constituent_ticker": "005930", "isin": "KR7005930003", "country_of_exchange": "KR",
           "asset_class": "Equity", "weight": 0.2}
    answers = {("figi", "KR7005930003"): {"status": "OK", "isin": "KR7005930003",
                                          "listings": [{"ticker": "SSNLF", "exchCode": "US"}], "composites": []},
               ("ticker_ref", "SSNLF"): reference("SSNLF", "OS", "otc"),
               ("bars", "SSNLF"): {"status": "OK", "results": [{"c": 60.0, "t": 1793944800000}]},
               ("home", "KR7005930003:KR"): {"status": "OK", "listing": "KR7005930003:KR", "currency": "KRW",
                                             "results": [{"session": "2026-11-06", "moment": "2026-11-06T06:30:00Z",
                                                          "c": 98000.0}]}}
    found = resolve(row, lookup(answers))
    assert found == {"ticker": "KR7005930003:KR", "kind": "name", "market": "home", "type": "OS",
                     "how": "its home listing", "isin": "KR7005930003"}
    # a row of a Form N-PORT report names no exchange: its ISIN's country
    assert home_listing({"isin": "TW0002330008"}) == "TW0002330008:TW"
    assert home_listing({"isin": "US0378331005"}) is None and home_listing({"cusip": "037833100"}) is None


def test_a_holding_listed_abroad_is_priced_on_its_home_market_and_its_us_listing_names_its_company():
    """MATH.md 3.1: a company held through funds is priced through its listing on its home market, in that market's
    currency; a US listing of its share class names its company (its CIK), never its price."""
    row = {"constituent_ticker": "TD", "isin": "CA8911605092", "country_of_exchange": "CA",
           "asset_class": "Equity", "weight": 0.05}
    answers = {("figi", "CA8911605092"): {"status": "OK", "isin": "CA8911605092",
                                          "listings": [{"ticker": "TD", "exchCode": "US"}], "composites": []},
               ("ticker_ref", "TD"): reference("TD", "CS", "stocks", cik="0000947263")}
    found = resolve(row, lookup(answers))
    assert (found["ticker"], found["market"], found["how"], found["cik"]) == \
        ("CA8911605092:CA", "home", "its home listing", 947263)


SEC = {"status": "OK", "fields": ["cik", "name", "ticker", "exchange"],
       "data": [[1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"], [19617, "JPMORGAN CHASE & CO", "VYLD", "NYSE"],
                [19617, "JPMORGAN CHASE & CO", "JPM", "NYSE"], [1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"],
                [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"], [1046179, "TAIWAN SEMICONDUCTOR", "TSM", "NYSE"]]}


def test_a_companys_id_is_its_first_us_ticker_in_massives_form_and_never_a_fund_its_sponsor_lists():
    answers = {("sec_tickers", "company_tickers_exchange"): SEC,
               ("ticker_ref", "GOOGL"): reference("GOOGL", "CS", "stocks", cik=1652044),
               ("ticker_ref", "VYLD"): reference("VYLD", "ETF", "stocks", cik=19617),
               ("ticker_ref", "JPM"): reference("JPM", "CS", "stocks", cik=19617),
               ("ticker_ref", "BRK.B"): reference("BRK.B", "CS", "stocks", cik=1067983)}
    look = lookup(answers)
    assert look.first_ticker(1652044) == "GOOGL"  # the list's first, whatever is held
    assert look.first_ticker(19617) == "JPM"  # its sponsor's ETF listed under its CIK is passed over
    assert look.first_ticker(1067983) == "BRK.B"  # the SEC's BRK-B, in Massive's form
    assert look.first_ticker(5) is None


def test_a_us_listings_company_is_the_cik_the_secs_list_gives_it_and_else_the_one_massives_record_gives():
    look = lookup({("sec_tickers", "company_tickers_exchange"): SEC})
    assert look.company_cik("JPM", "0000869614") == 19617  # the SEC's, over a record that names another
    assert look.company_cik("BRK.B", None) == 1067983  # the SEC's BRK-B, asked in Massive's form
    assert look.company_cik("ZZZZ", "0000000042") == 42  # a ticker the SEC does not list: Massive's
    assert look.company_cik(None, None) is None


class Store:
    """The store's data seam over fixed answers, as name_of reads it."""

    def __init__(self, answers):
        self.answers = answers

    def data(self, kind, subject, covering=None, required=True):
        return {"id": (kind, subject)} if (kind, subject) in self.answers else None

    def data_payload(self, did):
        return self.answers[did]


def test_name_of_gives_a_securitys_company_its_id_and_its_pricing_security_where_none_is_held():
    from harness.derive.identity import name_of
    answers = {("sec_tickers", "company_tickers_exchange"): SEC,
               ("ticker_ref", "GOOG"): {"status": "OK", "results": {"ticker": "GOOG", "type": "CS", "market": "stocks",
                                                                     "cik": "0001652044", "name": "Alphabet Inc."}},
               ("ticker_ref", "GOOGL"): {"status": "OK", "results": {"ticker": "GOOGL", "type": "CS",
                                                                      "market": "stocks", "cik": "0001652044",
                                                                      "name": "Alphabet Inc."}},
               ("ticker_ref", "TSM"): {"status": "OK", "results": {"ticker": "TSM", "type": "ADRC", "market": "stocks",
                                                                    "cik": "0001046179", "name": "Taiwan Semiconductor"}},
               ("ticker_ref", "QQQ"): reference("QQQ", "ETF", "stocks"),
               ("figi", "TW0002330008"): {"status": "OK", "isin": "TW0002330008",
                                          "listings": [{"ticker": "TSM", "exchCode": "US"}], "composites": []}}
    store = Store(answers)
    assert name_of(store, "GOOG", "2026-11-06") == {"name": "name:GOOGL", "security": "GOOGL",
                                                    "company": {"cik": 1652044, "title": "Alphabet Inc."}}
    assert name_of(store, "TSM", "2026-11-06")["security"] == "TSM"  # a receipt names no ordinary
    abroad = name_of(store, "TW0002330008:TW", "2026-11-06")  # its home line, named by its first US ticker
    assert (abroad["name"], abroad["security"]) == ("name:TSM", "TW0002330008:TW")
    assert name_of(store, "QQQ", "2026-11-06") == {"name": "name:QQQ", "security": "QQQ", "company": None}
    import pytest
    with pytest.raises(LookupError, match="no source lists ZZZZ"):
        name_of(store, "ZZZZ", "2026-11-06")


def test_a_holding_listed_abroad_is_its_home_listing_whatever_a_price_source_covers():
    """Identity is not pricing: a share class no US listing confirms is its home listing, read by no price source."""
    row = {"constituent_ticker": "NCCB", "isin": "SE0000117970", "country_of_exchange": "SE",
           "asset_class": "Equity", "weight": 0.01}
    answers = {("figi", "SE0000117970"): {"status": "OK", "isin": "SE0000117970", "listings": [], "composites": []}}
    asked: list = []
    found = resolve(row, lookup(answers, asked))
    assert (found["ticker"], found["market"], found["how"]) == ("SE0000117970:SE", "home", "its home listing")
    assert "home" not in asked and "bars" not in asked


def test_a_rows_company_found_by_its_cik_is_its_own_share_never_a_fund_its_sponsor_lists_under_that_cik():
    """A company's listings by CIK include the funds its sponsor issues under it (an ETF a bank lists): those are
    funds, and a row reached through the company's CIK is the company's share alone."""
    row = {"constituent_ticker": "JPM", "isin": "US46625H1005", "asset_class": "Equity", "weight": 0.03}
    answers = {("figi", "US46625H1005"): {"status": "OK", "isin": "US46625H1005",
                                          "listings": [{"ticker": "JPMX", "exchCode": "US"}], "composites": []},
               ("ticker_ref", "JPMX"): reference("JPMX", "CS", "otc", cik="0000019617"),
               ("company_listings", "0000019617"): {"status": "OK", "results": [
                   {"ticker": "VYLD", "type": "ETF", "market": "stocks", "cik": "0000019617"},
                   {"ticker": "JPM", "type": "CS", "market": "stocks", "cik": "0000019617"}]},
               ("ticker_ref", "JPM"): reference("JPM", "CS", "stocks", cik="0000019617")}
    found = resolve(row, lookup(answers))
    assert (found["ticker"], found["kind"], found["how"]) == ("JPM", "name", "its company")
