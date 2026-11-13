"""The ECB's reference rates (harness.venues.ecb): a read waits for an answer that is slow to begin, as a query the ECB
has not answered before is, and is asked again where its gateway gives the request up."""

from __future__ import annotations

import io
import urllib.error

from harness.venues.ecb import ECB

CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
EXR.D.JPY.EUR.SP00.A,D,JPY,EUR,SP00,A,2019-02-19,125.09
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2019-02-19,1.1317
"""


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ecb(begins: float, gateway: list[int] | None = None) -> tuple[ECB, list[float]]:
    """An ECB whose answer begins `begins` seconds after it is asked; `gateway`, the codes it answers first."""
    asked: list[float] = []
    codes = list(gateway or [])

    def open_(request, timeout=None):
        asked.append(timeout)
        if codes:
            raise urllib.error.HTTPError(request.full_url, codes.pop(0), "Gateway Timeout", {}, None)
        if timeout is not None and timeout < begins:
            raise TimeoutError("The read operation timed out")
        return Answer(CSV.encode())

    client = ECB(opener=open_)
    return client, asked


def test_an_answer_slow_to_begin_is_waited_for_in_one_request():
    client, asked = ecb(begins=25.0)  # a query the ECB has not answered before: read live, 8 to 25 s to begin
    got = client.rates("JPY", "2019-02-19", "2026-11-12")
    assert got["payload"]["per_euro"] == {"JPY": {"2019-02-19": 125.09}, "USD": {"2019-02-19": 1.1317}}
    assert len(asked) == 1


def test_a_request_the_gateway_gives_up_is_asked_again(monkeypatch):
    monkeypatch.setattr("harness.venues.ecb.PAUSE", 0)
    client, asked = ecb(begins=1.0, gateway=[504])
    assert client.rates("USD", "2024-11-05", "2026-11-12")["payload"]["status"] == "OK"
    assert len(asked) == 2
