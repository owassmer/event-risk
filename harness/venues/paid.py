"""What a venue pays on the value of a position in its markets, read from the venue's own page (MATH.md 1.2, 3.3).

No venue states it as data (none of their APIs carries it, read against each documentation index on 2026-11-11). Each
states it in prose, as a figure under a label of its own, on a page of its own, and each venue's module names that
page, the label, how the venue pays it, and on which of its markets (`PAID`). What is read is that one figure, under
that label: the page's words with its markup, entities and emphasis taken out, a line for each of its blocks, the figure
beside the label, and the sentence it stands in, quoted as stated. A page where the label is missing, or carries two different figures, is a read
that fails, and the last reading kept stands (harness.readings); a figure is never taken from anywhere else on the page.
A venue that pays nothing for holding states it in its documentation, and its module names that page (`PAID` with no
label): its rate is none, and nothing is read.

What is kept, as `venue_rate` for the venue: `{status: "OK", venue, rate, paid, stated, url}`, `rate` a decimal a year,
`paid` how the venue pays it (`annual`, an annual percentage yield; `daily`, an annual rate paid each day on the value
held), and `stated` the sentence as the page states it. It carries no moment of its own, so a reading taken again keeps
a row only where what the page says changed.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from harness.venues.http import VenueError, client, now

TIMEOUT = 15.0  # seconds: a help page answers in well under one


BLOCK = r"</?(p|div|li|ul|ol|h[1-6]|tr|td|th|br|section|article|header|footer|nav|table)\b[^>]*>"


def plain(page: str) -> str:
    """A page's words, a line for each of its blocks (a paragraph, a heading, an item): its scripts and styles, tags,
    entities, and emphasis marks taken out, and its spaces collapsed within each line."""
    text = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>", " ", page)
    text = re.sub(BLOCK, "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[*_`]+", "", text)
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def sentence(text: str, start: int, end: int) -> str:
    """The sentence of `text` that the span from `start` to `end` stands in, within its line."""
    begin = max(text.rfind(". ", 0, start), text.rfind("! ", 0, start), text.rfind("? ", 0, start),
                text.rfind("\n", 0, start))
    begin = 0 if begin < 0 else begin + (1 if text[begin] == "\n" else 2)
    stops = [i + 1 for i in (text.find(". ", end), text.find("! ", end), text.find("? ", end)) if i >= 0]
    stops += [i for i in (text.find("\n", end),) if i >= 0]
    stop = min(stops, default=len(text))
    return text[begin:stop].strip()


def stated(venue: str, name: str, spec: dict, page: str) -> dict:
    """The venue's rate as its page states it under its label: the figure and the sentence it stands in. A page with no
    such figure, or two different ones, raises VenueError: the read failed, and the last reading stands."""
    text = plain(page)
    found = [(float(m.group("rate")) / 100.0, sentence(text, m.start(), m.end()))
             for m in re.finditer(spec["label"], text)]
    rates = {rate for rate, _ in found}
    if not found:
        raise VenueError(f"{name}'s page {spec['url']} states no rate under its label {spec['words']!r}")
    if len(rates) > 1:
        raise VenueError(f"{name}'s page {spec['url']} states {len(rates)} different rates under its label "
                         f"{spec['words']!r}: " + ", ".join(f"{r:.2%}" for r in sorted(rates)))
    rate, said = found[0]
    return {"status": "OK", "venue": venue, "rate": round(rate, 6), "paid": spec["paid"], "stated": said,
            "url": spec["url"]}


class Paid:
    """Each venue's page, read for the rate it pays on a position."""

    def __init__(self, specs: dict[str, dict | None], names: dict[str, str], *, http: httpx.Client | None = None):
        self.specs, self.names = specs, names
        self.http = http if http is not None else client(TIMEOUT)

    def read(self, venue: str, covering: Any) -> dict:
        spec = self.specs.get(venue)
        if spec is None or not spec.get("label"):
            raise LookupError(f"{self.names.get(venue, venue)} states no rate it pays on a position as a figure")
        retrieved = now()
        try:
            response = self.http.get(spec["url"])
        except httpx.HTTPError as e:
            raise VenueError(f"{self.names[venue]}'s page {spec['url']}: {e}") from None
        if response.status_code != 200:
            raise VenueError(f"{self.names[venue]}'s page {spec['url']} answered {response.status_code}")
        payload = stated(venue, self.names[venue], spec, response.text)
        day = covering[1] if isinstance(covering, (tuple, list)) and covering else None
        return {"payload": payload, "request": {"method": "GET", "url": spec["url"]}, "retrieved_at": retrieved,
                **({"range": (covering[0], covering[1])} if day else {})}
