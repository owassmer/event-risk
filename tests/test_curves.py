"""Each currency's government yield curve (MATH.md 1.6, 3.1; harness.venues.curves): read from the institution that
publishes it, in its own convention, and discounted by it (harness.derive.rates). Every source is answered here."""

from __future__ import annotations

import math

import httpx
import pytest

from harness.derive import rates
from harness.derive.engine import Unavailable
from harness.venues.curves import Curves, with_curves

ECB_CSV = """KEY,FREQ,REF_AREA,CURRENCY,PROVIDER_FM,INSTRUMENT_FM,PROVIDER_FM_ID,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_3M,2026-11-10,2.6206150000
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_1Y,2026-11-10,2.9000000000
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_10Y,2026-11-09,3.3265191239
"""
MOF_ALL = "Interest Rate,,,(Unit : %)\nDate,1Y,2Y,40Y\n1974/9/24,10.327,9.362,-\n2026/10/30,1.659,1.875,3.934\n"
MOF_NOW = "Interest Rate (November 2026),,,(Unit : %)\nDate,1Y,2Y,40Y\n2026/11/2,1.671,1.88,3.96\n"
BONDS = {"observations": [{"d": "2026-11-09", "BD.CDN.2YR.DQ.YLD": {"v": "3.13"}, "BD.CDN.10YR.DQ.YLD": {"v": "3.63"},
                           "BD.CDN.RRB.DQ.YLD": {"v": "1.82"}}]}
BILLS = {"observations": [{"d": "2026-10-28", "V80691344": {"v": "2.53"}},
                          {"d": "2026-11-03", "V80691303": {"v": "2.61"}},  # an auction's average, not read
                          {"d": "2026-11-04", "V80691342": {"v": "2.47"}, "V80691344": {"v": "2.58"}}]}


def curves():
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "data-api.ecb.europa.eu" in url:
            return httpx.Response(200, text=ECB_CSV)
        if url.endswith("historical/jgbcme_all.csv"):
            return httpx.Response(200, content=MOF_ALL.encode())
        if url.endswith("jgbcme.csv"):
            return httpx.Response(200, content=MOF_NOW.encode())
        if "bond_yields_benchmark" in url:
            return httpx.Response(200, json=BONDS)
        if "tbill_all" in url:
            return httpx.Response(200, json=BILLS)
        return httpx.Response(404)
    return Curves(http=httpx.Client(transport=httpx.MockTransport(handle)))


def test_each_currencys_curve_is_its_governments_in_its_own_convention():
    c = curves()
    eur = c.curve("EUR:2026", "2026-11-11")["payload"]
    assert (eur["currency"], eur["basis"]) == ("EUR", "spot")  # the ECB's spot rates, continuously compounded
    assert eur["results"] == [{"date": "2026-11-09", "curve": [[10, 0.03326519]]},
                              {"date": "2026-11-10", "curve": [[0.25, 0.02620615], [1, 0.029]]}]
    jpy = c.curve("JPY:2026", "2026-11-11")["payload"]
    assert jpy["basis"] == "par" and jpy["results"] == [  # each year's days, the history and the month together
        {"date": "2026-10-30", "curve": [[1.0, 0.01659], [2.0, 0.01875], [40.0, 0.03934]]},
        {"date": "2026-11-02", "curve": [[1.0, 0.01671], [2.0, 0.0188], [40.0, 0.0396]]}]
    cad = c.curve("CAD:2026", "2026-11-11")["payload"]
    assert cad["basis"] == "par" and cad["results"] == [  # its bonds with the latest week's bills on or before it
        {"date": "2026-11-09", "curve": [[1 / 12, 0.0247], [0.25, 0.0258], [2, 0.0313], [10, 0.0363]]}]
    with pytest.raises(Unavailable, match="GBP's government yield curve is not read by any source here"):
        c.curve("GBP:2026", "2026-11-11")


def test_a_year_alone_is_the_dollars_and_a_currency_and_year_its_governments():
    asked = []
    route = with_curves(lambda subject, covering: asked.append(subject) or {"payload": "treasury"}, curves())
    assert route("2026", ("2026-11-11", "2026-11-11"))["payload"] == "treasury"
    assert route("USD:2026", ("2026-11-11", "2026-11-11"))["payload"] == "treasury"
    assert route("EUR:2026", ("2026-11-11", "2026-11-11"))["payload"]["basis"] == "spot"
    assert asked == ["2026", "2026"]


def test_a_spot_curve_discounts_continuously_and_a_par_curve_as_the_treasurys():
    spot = [[0.25, 0.02], [1.0, 0.03], [10.0, 0.04]]
    assert rates.discount(spot, 0.5, "spot") == pytest.approx(math.exp(-(0.02 + 0.01 * 0.25 / 0.75) * 0.5))
    assert rates.discount(spot, 20.0, "spot") == pytest.approx(math.exp(-0.04 * 20.0))  # flat beyond the last
    assert rates.rate(spot, 2.0, "spot") == pytest.approx(0.03 + 0.01 / 9)
    assert rates.discount([[0.5, 0.04]], 0.5) == pytest.approx(1 / 1.02)  # MATH.md 1.6's 35.00 / 1.02, unchanged
    with pytest.raises(ValueError):
        rates.discount(spot, 1.0, "forward")
