"""The clients of the world. Massive supplies prices, references, and fund constituents; the prediction-market venues,
read through Synpath (harness.venues.markets), quote event probabilities. A venue is the one thing the analyst cannot
change, because a venue is a client and a client is code.

Which venues the store searches and reads is `schema.VENUES`, a switch: a venue is on when it is listed there and has
its module here, with its adapter and the forms its records are read by. Kalshi, Polymarket, and Polymarket US are on.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from harness import schema
from harness.venues import kalshi, polymarket, polymarket_us
from harness.venues.markets import FAMILIES, PAST_PAGE, client as http_client
from harness.venues.paid import Paid

# Every venue this package can read, each a module with its adapter class and its forms: the listing of a record, its
# family, the YES levels of a book, the price shown, the daily prices of a history, whether it is open, its settlement.
MODULES = {"kalshi": kalshi, "polymarket": polymarket, "polymarket_us": polymarket_us}
ADAPTERS = {"kalshi": kalshi.Kalshi, "polymarket": polymarket.Polymarket, "polymarket_us": polymarket_us.PolymarketUS}
FORMS = {venue: MODULES[venue] for venue in schema.VENUES}
# Each venue as a reason names it.
NAMES = {"polymarket": "Polymarket", "kalshi": "Kalshi", "polymarket_us": "Polymarket US"}


def split(market_id: str) -> tuple[str, str]:
    venue, venue_id = schema.MARKET_ID_RE.match(market_id).groups()
    return venue, venue_id


class Venues:
    """The venues switched on, behind one interface: a market's record (how a market enters the store), a family read
    whole, the one search, and the fetchers of a kept market's book and history."""

    def __init__(self, clients: dict[str, Any] | None = None, paid: Paid | None = None):
        self.clients = clients if clients is not None else \
            {venue: ADAPTERS[venue](client=http_client()) for venue in schema.VENUES}
        # what each venue pays on a position, read from its page (harness.venues.paid)
        self.paid = paid if paid is not None else Paid({v: MODULES[v].PAID for v in MODULES}, NAMES)

    def client(self, venue: str):
        if venue not in self.clients:
            raise ValueError(f"the venues are {', '.join(NAMES[v] for v in self.clients)}")
        return self.clients[venue]

    def market(self, venue: str, venue_id: str) -> dict:
        """The record of one market, as the store keeps it: LookupError when the venue lists none."""
        return self.client(venue).market(venue_id)

    def search(self, text: str, *, past: bool) -> dict[str, list]:
        """Each venue's own search for the text, side by side: its families in its order of relevance. A venue that
        does not answer past its retries raises (harness.venues.markets.read)."""
        with ThreadPoolExecutor(max_workers=len(self.clients) or 1) as pool:
            found = {venue: pool.submit(client.find, text, past=past, limit=PAST_PAGE if past else FAMILIES)
                     for venue, client in self.clients.items()}
            return {venue: job.result() for venue, job in found.items()}

    def fetchers(self, store) -> dict[str, Callable[[str, Any], dict]]:
        """A kept market's book and history, fetched from the record the store keeps for it; and what a venue pays on a
        position, read from its page."""

        def record(subject: str) -> dict:
            return store.data_payload(store.data("market", subject)["id"])

        return {
            "book": lambda subject, covering: self.client(split(subject)[0]).book(record(subject)),
            "history": lambda subject, covering: self.client(split(subject)[0]).history(record(subject)),
            "venue_rate": lambda subject, covering: self.paid.read(subject, covering),
        }
