"""The clients of the world. Massive supplies prices, references, and fund constituents;
Polymarket, Kalshi, and Hyperliquid quote event probabilities. A venue is the one
thing the analyst cannot change, because a venue is a client and a client is code."""

from __future__ import annotations

from typing import Any, Callable

from harness import schema
from harness.venues import hyperliquid, kalshi, polymarket

# Each venue's normalizers: the listing of a record, the YES levels of a book, the daily prices of a history.
FORMS = {"polymarket": polymarket, "kalshi": kalshi, "hyperliquid": hyperliquid}
# Each venue as a reason names it.
NAMES = {"polymarket": "Polymarket", "kalshi": "Kalshi", "hyperliquid": "Hyperliquid"}


def split(market_id: str) -> tuple[str, str]:
    venue, venue_id = schema.MARKET_ID_RE.match(market_id).groups()
    return venue, venue_id


class Venues:
    """The three venue clients behind one interface: a market's record (how a market enters
    the store), and the fetchers of a kept market's book and history."""

    def __init__(self, clients: dict[str, Any] | None = None):
        self.clients = clients if clients is not None else {
            "polymarket": polymarket.Polymarket(), "kalshi": kalshi.Kalshi(), "hyperliquid": hyperliquid.Hyperliquid()}

    def client(self, venue: str):
        if venue not in self.clients:
            raise ValueError(f"the venues are {', '.join(self.clients)}")
        return self.clients[venue]

    def market(self, venue: str, venue_id: str) -> dict:
        """The record of one market, as the store keeps it: LookupError when the venue lists none."""
        return self.client(venue).market(venue_id)

    def fetchers(self, store) -> dict[str, Callable[[str, Any], dict]]:
        """A kept market's book and history, fetched from the record the store keeps for it."""

        def record(subject: str) -> dict:
            return store.data_payload(store.data("market", subject)["id"])

        return {
            "book": lambda subject, covering: self.client(split(subject)[0]).book(record(subject)),
            "history": lambda subject, covering: self.client(split(subject)[0]).history(record(subject)),
        }
