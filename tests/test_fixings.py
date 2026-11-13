"""A currency's daily fixings before its hourly rates begin (harness.venues.fixings, MATH.md 3.1, 3.2): a close abroad,
a dividend, and a notional before Yahoo's hourly reach convert at a dated fixing, the first of the ECB's, the Federal
Reserve's H.10, and the BIS's to fix that date, each with its source and the moment it was struck; a close converts
at its own date's fixing, never an earlier date's; where the hourly rates reach, nothing changes."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from harness.derive import history, series
from harness.venues import fixings as fx
from harness.venues.fixings import Fixings, with_fixings

FED_CSV = """"Series Description","Australian Dollar","Euro-Area Euro","Taiwanese N.T. Dollar","Venezuelan Bolivar"
"Unit:","Currency","Currency","Currency","Currency"
"Multiplier:","1","1","1","1"
"Currency:","AUD","EUR","TWD","VEB"
"Unique Identifier: ","H10/H10/RXI$US_N.B.AL","H10/H10/RXI$US_N.B.EU","H10/H10/RXI_N.B.TA","H10/H10/RXI_N.B.VES"
"Time Period","RXI$US_N.B.AL","RXI$US_N.B.EU","RXI_N.B.TA","RXI_N.B.VES"
2023-05-01,0.6600,1.1000,30.0000,25.0000
2023-05-02,0.6610,1.1010,ND,25.1000
2023-05-03,ND,1.1020,30.2000,ND
"""

BIS_CSV = """FREQ,REF_AREA,CURRENCY,COLLECTION,TIME_PERIOD,OBS_VALUE
D,PE,PEN,A,2023-05-01,3.7
D,PE,PEN,A,2023-05-02,3.75
D,PE,PEN,A,2023-05-03,3.8
D,XX,PEN,A,2023-05-02,9.9
D,CL,CLP,A,2023-05-01,800
D,CL,CLP,A,2023-05-02,810
D,CL,CLP,A,2023-05-03,820
D,CL,CLP,A,2023-05-04,830
"""


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener(asked: list):
    def open_(request, timeout=None):
        asked.append(request.full_url)
        if "federalreserve" in request.full_url:
            return Answer(FED_CSV.encode())
        currency = request.full_url.split("/D..")[1].split(".A")[0]
        rows = [r for r in BIS_CSV.splitlines()[1:] if f",{currency}," in r]
        return Answer(("\n".join([BIS_CSV.splitlines()[0]] + rows) + "\n").encode())
    return open_


class ECB:
    """The ECB's per-euro rates for a window: EUR's dollar rate every day but 2023-05-01 (a TARGET closing day)."""

    def _window(self, start, end):
        return "https://ecb.example/window", "2026-11-11T00:00:00+00:00", {
            "USD": {"2023-05-02": 1.1, "2023-05-03": 1.1}, "JPY": {"2023-05-02": 150.0, "2023-05-03": 151.0}}


def fixings() -> tuple[Fixings, list]:
    asked: list = []
    return Fixings(ECB(), opener=opener(asked), pause=0), asked


def test_h10_reads_each_column_in_dollars_per_unit_by_its_stated_direction():
    rates = fx.fed_rates(FED_CSV)
    assert rates["AUD"]["2023-05-01"] == pytest.approx(0.66)          # an asterisked currency: dollars per unit
    assert rates["TWD"]["2023-05-01"] == pytest.approx(1 / 30.0)      # the rest: units per dollar, inverted
    assert "2023-05-02" not in rates["TWD"] and "2023-05-03" not in rates["AUD"]  # ND: no rate set that date
    assert rates["VES"]["2023-05-01"] == pytest.approx(1 / 25.0)      # its identifier's code, not its label's


def test_the_bis_takes_the_area_quoting_most_dates():
    pen = "\n".join(r for r in BIS_CSV.splitlines() if "CLP" not in r)  # the BIS answers one currency's rows
    area, rates = fx.bis_rates(pen)
    assert area == "PE" and rates["2023-05-02"] == pytest.approx(1 / 3.75)


def test_a_date_takes_the_first_source_that_fixes_it():
    f, _ = fixings()
    jpy, _ = f.daily("JPY", "2023-05-01", "2023-05-03")  # the ECB's, which H.10's package here does not carry
    assert [(x["day"], x["source"]) for x in jpy] == [("2023-05-02", "ECB"), ("2023-05-03", "ECB")]
    assert jpy[0]["rate"] == pytest.approx(1.1 / 150.0)
    assert jpy[0]["moment"] == "2023-05-02T14:10:00+02:00"
    eur, _ = f.daily("EUR", "2023-05-01", "2023-05-03")
    # the euro's dollar rate is the ECB's dollar column itself, every reference rate being a euro's price; on the ECB's
    # closing day it is H.10's, at noon New York
    assert [(x["day"], x["source"], x["moment"], round(x["rate"], 6)) for x in eur] == [
        ("2023-05-01", "Federal Reserve H.10", "2023-05-01T12:00:00-04:00", 1.1),
        ("2023-05-02", "ECB", "2023-05-02T14:10:00+02:00", 1.1), ("2023-05-03", "ECB", "2023-05-03T14:10:00+02:00", 1.1)]


def test_a_source_that_answers_nothing_fails_the_read_and_is_not_taken_for_one_that_does_not_publish_the_currency():
    """H.10 labels every currency it publishes, whatever the window: an answer that labels none, a 404 or a page that is
    not its CSV, fails the read, so the call waits, where it once passed the euro to the BIS. The BIS's 404 is its
    answer that it holds no series for the key."""
    import urllib.error
    from harness.venues.http import VenueError

    def opener(fed):
        def open_(request, timeout=None):
            if "federalreserve" in request.full_url:
                if fed == 404:
                    raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
                return Answer(b"<html><body>The Data Download Program is unavailable</body></html>")
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)  # the BIS: no such series
        return open_

    for fed in (404, "page"):
        f = Fixings(ECB(), opener=opener(fed), pause=0)
        with pytest.raises(VenueError, match="H.10|answered 404"):
            f.daily("TWD", "2023-05-01", "2023-05-03")
    f = Fixings(ECB(), opener=opener(None), pause=0)
    f._fed[("2023-05-01", "2023-05-03")] = ("u", fx.fed_rates(FED_CSV))
    assert f.daily("PEN", "2023-05-01", "2023-05-03")[0] == []  # the BIS holds none: no fixing, no failure

    answers = [b"<html>unavailable</html>", FED_CSV.encode()]  # such an answer once, then its CSV: asked again

    def once(request, timeout=None):
        return Answer(answers.pop(0))
    twd, _ = Fixings(ECB(), opener=once, pause=0).daily("TWD", "2023-05-01", "2023-05-03")
    assert {x["source"] for x in twd} == {"Federal Reserve H.10"} and answers == []


def test_a_currency_neither_publishes_comes_from_the_bis_with_its_date_and_no_moment():
    f, asked = fixings()
    pen, _ = f.daily("PEN", "2023-05-01", "2023-05-03")
    assert [(x["day"], round(x["rate"], 6), x["source"], x["moment"]) for x in pen] == [
        ("2023-05-01", round(1 / 3.7, 6), "BIS (PE)", None), ("2023-05-02", round(1 / 3.75, 6), "BIS (PE)", None),
        ("2023-05-03", round(1 / 3.8, 6), "BIS (PE)", None)]
    assert any("/D..PEN.A" in u for u in asked)


def test_a_figure_its_central_bank_dates_from_the_day_before_stands_for_that_market_day():
    f, _ = fixings()
    clp, _ = f.daily("CLP", "2023-05-01", "2023-05-03")
    # the figure dated 05-02 describes 05-01's market, and so on; 05-03's is the figure dated 05-04
    assert [(x["day"], round(1 / x["rate"])) for x in clp] == [("2023-05-01", 810), ("2023-05-02", 820),
                                                               ("2023-05-03", 830)]


def hours(first: str, n: int, start_rate: float = 0.0074) -> list[dict]:
    t0 = int(datetime.fromisoformat(first).replace(tzinfo=timezone.utc).timestamp()) * 1000
    return [{"t": t0 + k * 3_600_000, "o": start_rate, "h": start_rate, "l": start_rate, "c": start_rate}
            for k in range(n)]


def yahoo(first: str | None):
    """Yahoo's hourly read for a pair whose hours begin at `first`; a window ending before it refused."""
    def read(subject, covering):
        if first is None or first[:10] > covering[1]:
            raise LookupError(f"reach back to {first}")
        lo = datetime.fromisoformat(covering[0]).replace(tzinfo=timezone.utc).timestamp() * 1000
        hi = datetime.fromisoformat(covering[1]).replace(tzinfo=timezone.utc).timestamp() * 1000 + 86_400_000
        every = hours(first, 24 * 60)
        return {"payload": {"status": "OK", "ticker": subject, "base": subject[2:5], "quote": "USD", "parse": 2,
                            "results": [h for h in every if lo <= h["t"] < hi], "first": every[0]["t"]},  # the window's
                # hours alone, as Yahoo's read keeps them, with how far back its hours reach
                "request": {"method": "GET", "url": "https://yahoo.example"}, "retrieved_at": "2026-11-11T00:00:00+00:00",
                "range": covering}
    return read


def test_a_window_wholly_before_the_hours_reads_its_fixings():
    f, _ = fixings()
    got = with_fixings(yahoo("2024-01-24T00:00:00"), f)("C:JPYUSD", ("2023-05-01", "2023-05-03"))
    p = got["payload"]
    assert p["status"] == "OK" and p["results"] == [] and p["parse"] == fx.FORM
    assert [x["day"] for x in p["fixings"]] == ["2023-05-02", "2023-05-03"]
    assert {r["url"] for r in got["request"]["fixings"]} >= {"https://ecb.example/window"}


def test_a_window_partly_before_the_hours_reads_fixings_through_the_first_hours_date():
    f, _ = fixings()
    got = with_fixings(yahoo("2023-05-02T15:00:00"), f)("C:JPYUSD", ("2023-05-01", "2023-05-05"))
    p = got["payload"]
    assert p["results"] and [x["day"] for x in p["fixings"]] == ["2023-05-02"]  # 05-03 has its hours


def test_a_window_within_the_hours_reads_no_fixing_and_changes_nothing_but_its_form():
    f, asked = fixings()
    got = with_fixings(yahoo("2023-04-01T00:00:00"), f)("C:JPYUSD", ("2023-05-01", "2023-05-03"))
    assert "fixings" not in got["payload"] and got["payload"]["parse"] == fx.FORM and asked == []


def test_a_window_no_hour_and_no_fixing_reaches_is_refused():
    f, _ = fixings()
    with pytest.raises(LookupError, match="no central bank"):
        with_fixings(yahoo("2024-01-24T00:00:00"), f)("C:KZTUSD", ("2023-05-01", "2023-05-03"))


def test_a_pair_not_in_dollars_is_left_to_its_hourly_read():
    f, asked = fixings()
    with pytest.raises(LookupError):
        with_fixings(yahoo("2024-01-24T00:00:00"), f)("C:EURJPY", ("2023-05-01", "2023-05-03"))
    assert asked == []


RATES = {"status": "OK", "results": hours("2023-05-04T00:00:00", 72, 0.0075),
         "fixings": [{"day": "2023-05-01", "rate": 0.0070, "source": "Federal Reserve H.10",
                      "moment": "2023-05-01T12:00:00-04:00"},
                     {"day": "2023-05-02", "rate": 0.0072, "source": "ECB", "moment": "2023-05-02T14:10:00+02:00"},
                     {"day": "2023-05-03", "rate": 0.0073, "source": "ECB", "moment": "2023-05-03T14:10:00+02:00"}]}


def test_a_close_before_the_hours_converts_at_its_own_dates_fixing():
    index = series.rate_index(RATES)
    # Frankfurt closes at 17:30 there, after that day's ECB fixing and before H.10's: its own date's either way
    frankfurt = series.rate_for(index, "2023-05-02T17:30:00+02:00", "2023-05-02")
    assert frankfurt == {"price": 0.0072, "time": "2023-05-02T14:10:00+02:00", "source": "ECB"}
    # Tokyo closes at 15:00, before any fixing of its date: still its own date's, never the day before's
    tokyo = series.rate_for(index, "2023-05-03T15:00:00+09:00", "2023-05-03")
    assert tokyo["price"] == 0.0073 and tokyo["source"] == "ECB"
    # from the first hour on, the rate struck at the close's moment, as before
    after = series.rate_for(index, "2023-05-04T10:30:00+00:00", "2023-05-04")
    assert after["price"] == 0.0075 and after["source"] == "hourly"


def test_a_date_no_source_fixes_gives_no_dollar_close():
    item = {"ticker": "JP0000000001:JP", "kind": "abroad", "currency": "JPY", "ref": {"data": "home"},
            "rate": {"data": "rates"}}
    home = {"status": "OK", "currency": "JPY", "results": [
        {"session": "2023-04-28", "moment": "2023-04-28T15:00:00+09:00", "c": 100.0},   # no fixing that date
        {"session": "2023-05-01", "moment": "2023-05-01T15:00:00+09:00", "c": 101.0},
        {"session": "2023-05-02", "moment": "2023-05-02T15:00:00+09:00", "c": 102.0}]}
    load = {"home": home, "rates": RATES}.get
    closes = history.home_closes(item, lambda ref: load(ref["data"]), ["2023-04-27", "2023-04-28", "2023-05-01",
                                                                      "2023-05-02"])
    assert closes["2023-04-28"]["close"] is None and "rate_source" not in closes["2023-04-28"]
    assert closes["2023-05-01"]["close"] == pytest.approx(101.0 * 0.0070)
    assert closes["2023-05-01"]["rate_source"] == "Federal Reserve H.10"
    assert closes["2023-05-01"]["rate_moment"] == "2023-05-01T12:00:00-04:00"
    assert closes["2023-05-02"]["close"] == pytest.approx(102.0 * 0.0072)


def test_a_dividend_paid_before_the_hours_in_a_currency_the_ecb_does_not_publish_converts_at_its_fixing():
    paid = {"results": [{"ex_dividend_date": "2023-05-02", "cash_amount": 0.24, "currency": "PEN",
                         "split_adjusted_cash_amount": 0.24}]}
    pen = {"status": "OK", "results": [], "fixings": [{"day": "2023-05-02", "rate": 1 / 3.75, "source": "BIS (PE)",
                                                        "moment": None}]}
    got = series.dividends(paid, {"PEN": series.daily_rates(pen, "2026-11-11T00:00:00+00:00")})
    assert got == {"2023-05-02": pytest.approx(0.24 / 3.75)}


def test_a_pairs_form_is_its_own_clients_by_subject():
    from harness.analyst import by_pair

    def crypto(subject, covering):
        return {}

    def currency(subject, covering):
        return {}
    currency.parse = fx.FORM
    read = by_pair(crypto, currency)
    assert read.parse("C:JPYUSD") == fx.FORM and read.parse("X:BTCUSD") is None
