"""The ECB's reference rates (harness.venues.ecb): every window cut from the ECB's one history file, read once and kept,
and read from Frankfurter where the file does not answer."""

from __future__ import annotations

import io
import json
import urllib.error
import zipfile

from harness.venues import ecb as ecb_module
from harness.venues.ecb import ECB

CSV = """Date,USD,JPY,CYP,
2026-11-12,1.1081,170.10,N/A,
2019-02-19,1.1317,125.09,N/A,
2007-12-31,1.4721,164.93,0.5853,
"""


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def zipped(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("eurofxref-hist.csv", text)
    return out.getvalue()


def opener(file_codes: list[int] | None = None):
    asked: list[str] = []
    codes = list(file_codes or [])

    def open_(request, timeout=None):
        asked.append(request.full_url)
        if request.full_url == ecb_module.HISTORY:
            if codes:
                raise urllib.error.HTTPError(request.full_url, codes.pop(0), "Unavailable", {}, None)
            return Answer(zipped(CSV))
        return Answer(json.dumps({"base": "EUR", "rates": {"2019-02-19": {"USD": 1.1317, "JPY": 125.09}}}).encode())
    return open_, asked


def test_every_window_is_cut_from_the_one_file_read_once():
    open_, asked = opener()
    client = ECB(opener=open_, pause=0)
    got = client.rates("JPY", "2019-02-19", "2026-11-12")
    assert got["payload"]["per_euro"] == {"JPY": {"2019-02-19": 125.09, "2026-11-12": 170.10},
                                          "USD": {"2019-02-19": 1.1317, "2026-11-12": 1.1081}}
    assert client.rates("EUR", "2019-01-01", "2019-12-31")["payload"]["per_euro"]["EUR"] == {"2019-02-19": 1.0}
    assert client.rates("CYP", "2019-01-01", "2026-11-12")["payload"]["status"] == "NOT_FOUND"  # none in the window
    assert asked == [ecb_module.HISTORY]


def test_where_the_file_does_not_answer_the_window_is_read_from_frankfurter():
    open_, asked = opener(file_codes=[503, 503, 503])
    client = ECB(opener=open_, pause=0)
    got = client.rates("JPY", "2019-02-19", "2019-02-19")
    assert got["payload"]["per_euro"] == {"JPY": {"2019-02-19": 125.09}, "USD": {"2019-02-19": 1.1317}}
    assert got["request"]["url"].startswith(ecb_module.FRANKFURTER)
    assert asked.count(ecb_module.HISTORY) == ecb_module.ATTEMPTS
