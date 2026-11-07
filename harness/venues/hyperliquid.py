"""Hyperliquid: HIP-4 outcome markets through the info endpoint, `POST https://api.hyperliquid.xyz/info`,
no key (https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint,
https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets,
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids), each shape verified against a live
response.

The listing, `{"type": "outcomeMeta"}`, is every live outcome in one answer and takes no search parameters:
`{outcomes, questions, deployers, feeScale}`, some hundreds of outcomes and about 100 KB. An outcome is `{outcome,
name, description, sideSpecs, quoteToken, venue, deployerFeeScale}`: its `name` is its template and its `description`
the template's parameters, `key:value` joined by `|`. The protocol's own recurring outcomes are named `Recurring` (or
`Recurring Named Outcome`, `Recurring Fallback`, under a question), with `class:...` parameters; a deployer's are
named `template:{name}`, `venue` naming the deployer, its parameters its own, its dates among them (`time`,
`resolutionDeadline`, and the like). A question `{question, name, description, namedOutcomes, fallbackOutcome,
settledNamedOutcomes}` groups named outcomes, exactly one of which settles YES; its fallback outcome settles YES when
none of them does. A side is named `Yes` or `No`, or `template:Yes` and `template:No`.

Each outcome is a binary market with two sides; a side's book and candles are addressed by the coin
`#<10 * outcome + side>`, and the YES side is the `sideSpec` named Yes. The settlement rule is documented for the
protocol's recurring outcomes (`class:priceBinary|underlying|expiry|targetPrice`: YES if and only if the settlement
mark price is at least `targetPrice`, `expiry` as `YYYYMMDD-HHMM` in UTC); a deployer's template states its own
terms, its dates among them.

Book: `{"type": "l2Book", "coin": "#…"}` answers `{levels: [bids, asks]}`, each level `{px, sz, n}`, at most 20 a
side, best first, and `null` for a coin it does not know. The price is the coin's entry in `{"type": "allMids"}`,
read beside the book. History: `{"type": "candleSnapshot", "req": {"coin", "interval": "1d", "startTime",
"endTime"}}` answers up to the 5,000 most recent candles `{t, T, o, h, l, c, v, n}`; the daily price is the close.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterator

from harness.venues.http import Http, VenueError, now
from harness.venues.polymarket import NY

INFO = "https://api.hyperliquid.xyz/info"
ENDPOINTS = ("outcomeMeta",)
WORKERS = 8  # books read side by side


def params_of(description: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (description or "").split("|"):
        key, sep, value = part.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def yes_side(outcome: dict) -> int | None:
    for i, side in enumerate(outcome.get("sideSpecs") or []):
        if str(side.get("name", "")).split(":")[-1].strip().lower() == "yes":
            return i
    return None


def yes_coin(record: dict) -> str | None:
    outcome = record.get("outcome") or {}
    side = yes_side(outcome)
    return f"#{10 * outcome['outcome'] + side}" if side is not None else None


def _date(stamp: str | None) -> str | None:
    """The New York date of a protocol time, `YYYYMMDD-HHMM` in UTC."""
    try:
        moment = datetime.strptime(stamp, "%Y%m%d-%H%M").replace(tzinfo=timezone.utc) if stamp else None
    except ValueError:
        return None
    return moment.astimezone(NY).date().isoformat() if moment else None


def listing(record: dict) -> dict:
    outcome = record.get("outcome") or {}
    question = record.get("question") or {}
    p = {**params_of(question.get("description")), **params_of(outcome.get("description"))}
    text = f"{outcome.get('name')}: {outcome.get('description')}"
    if question:
        text = f"{question.get('name')}: {question.get('description')} | {text}"
    return {
        "question": text,
        "rules": text,
        "resolution_source": p.get("officialSource") or p.get("priceDescription") or None,
        "close_time": _date(p.get("expiry")),  # the protocol's settlement time; a deployer's template states its own
        "tags": [],
        "categories": [outcome["name"]] if outcome.get("name") else [],
        "series": [outcome["venue"]] if outcome.get("venue") else [],
        "yes_token": yes_coin(record),
    }


def lists(endpoint: str, body: dict) -> dict:
    """What a listing returns besides markets: nothing here."""
    return {}


def paging(endpoint: str, body) -> dict:
    """outcomeMeta is the whole listing in one answer: it has no pages."""
    return {}


def event(record: dict) -> dict | None:
    """The question an outcome belongs to, if any: exactly one of its named outcomes settles YES."""
    q = record.get("question")
    if not q:
        return None
    return {"event": q.get("question"), "title": q.get("name"), "tags": []}


def displayed(bid: float | None, ask: float | None, payload: dict, record: dict) -> dict:
    """The coin's mid as the info endpoint publishes it (`allMids`, https://hyperliquid.gitbook.io/hyperliquid-docs/
    for-developers/api/info-endpoint): the midpoint of the book, "if the book is empty, the last trade price"."""
    try:
        mid = float(payload["mid"]) if payload.get("mid") not in (None, "") else None
    except (TypeError, ValueError):
        mid = None
    if mid is not None:
        return {"price": mid, "rule": "midpoint" if bid is not None and ask is not None else "last trade"}
    return {"price": (bid + ask) / 2 if bid is not None and ask is not None else None, "rule": "midpoint"}


def is_open(record: dict, asof: str | None, past: bool) -> bool | None:
    """Whether the outcome trades on the as-of date: listed and not settled on a record read that day; for a past
    as-of, whether its expiry had not passed."""
    question = record.get("question") or {}
    if not past:  # outcomeMeta lists live outcomes; a question names those it has settled
        me = (record.get("outcome") or {}).get("outcome")
        return me not in [o[0] if isinstance(o, list) and o else o for o in question.get("settledNamedOutcomes") or []]
    close = listing(record).get("close_time")
    return None if asof is None or close is None else close >= asof


def levels(payload: dict) -> dict:
    sides = (payload or {}).get("levels") or [[], []]
    bids = [(float(level["px"]), float(level["sz"])) for level in sides[0]]
    asks = [(float(level["px"]), float(level["sz"])) for level in sides[1]]
    return {"bids": sorted(bids, key=lambda x: -x[0]), "asks": sorted(asks, key=lambda x: x[0])}


def daily(payload: Any) -> dict[str, float]:
    """The daily price: each daily candle's close, dated by the UTC day it opens."""
    out: dict[str, float] = {}
    for candle in payload if isinstance(payload, list) else []:
        out[datetime.fromtimestamp(candle["t"] / 1000, timezone.utc).date().isoformat()] = float(candle["c"])
    return out


class Hyperliquid:
    venue = "hyperliquid"

    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def _meta(self, params: dict | None = None) -> tuple[int, Any]:
        return self.http.call("POST", INFO, {**(params or {}), "type": "outcomeMeta"})

    def search(self, endpoint: str, params: dict) -> dict:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"Hyperliquid's listing is the info request {', '.join(ENDPOINTS)}")
        status, body = self._meta(params)
        markets = list(self._markets_in(body)) if status == 200 else []
        return {"request": {"method": "POST", "url": INFO, "body": {**params, "type": "outcomeMeta"}}, "status": status,
                "response": body, "markets": markets, "retrieved_at": now()}

    @staticmethod
    def _markets_in(body: Any) -> Iterator[tuple[str, dict]]:
        questions = {}
        for q in (body or {}).get("questions") or []:
            for o in list(q.get("namedOutcomes") or []) + [q.get("fallbackOutcome")]:
                if o is not None:
                    questions[o] = {k: q.get(k) for k in ("question", "name", "description", "settledNamedOutcomes")}
        for outcome in (body or {}).get("outcomes") or []:
            yield str(outcome["outcome"]), {"outcome": outcome, "question": questions.get(outcome["outcome"])}

    def market(self, venue_id: str) -> dict:
        status, body = self._meta()
        found = dict(self._markets_in(body)).get(str(venue_id)) if status == 200 else None
        if not found:
            raise LookupError(f"Hyperliquid lists no outcome {venue_id}")
        return {"payload": found, "request": {"method": "POST", "url": INFO, "body": {"type": "outcomeMeta"}},
                "retrieved_at": now()}

    def mids(self) -> dict:
        """Every coin's mid as the venue publishes it."""
        status, body = self.http.call("POST", INFO, {"type": "allMids"})
        return body if status == 200 and isinstance(body, dict) else {}

    def book(self, record: dict) -> dict:
        """The YES coin's book, with the venue's own mid for it read at the same moment."""
        coin = yes_coin(record)
        body = {"type": "l2Book", "coin": coin}
        status, payload = self.http.call("POST", INFO, body)
        if status != 200:
            return {"payload": {"status": status, "error": payload},
                    "request": {"method": "POST", "url": INFO, "body": body}, "retrieved_at": now()}
        return {"payload": {**(payload or {}), "mid": self.mids().get(coin)},
                "request": {"method": "POST", "url": INFO, "bodies": [body, {"type": "allMids"}]},
                "retrieved_at": now()}

    def books(self, coins: list[str]) -> dict[str, dict]:
        """Many coins' books, each with the venue's own mid for it: the mids read once and the books side by side. Info
        requests share a weight of 1,200 a minute per IP, an `l2Book` or `allMids` weighing 2
        (https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits), so a whole
        listing's books fit in a minute's budget. A book the venue does not give is left out, and its market shows no
        book."""
        mids = self.mids() if coins else {}

        def one(coin: str) -> dict | None:
            try:
                status, payload = self.http.call("POST", INFO, {"type": "l2Book", "coin": coin})
            except VenueError:
                return None
            return {**(payload or {}), "mid": mids.get(coin)} if status == 200 else None

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            found = dict(zip(coins, pool.map(one, coins)))
        return {coin: book for coin, book in found.items() if book is not None}

    def history(self, record: dict) -> dict:
        return self.coin_history(yes_coin(record))

    def coin_history(self, coin: str) -> dict:
        """A coin's daily candles, the most recent 5,000."""
        end = int(datetime.now(timezone.utc).timestamp() * 1000)
        body = {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": 0, "endTime": end}}
        status, payload = self.http.call("POST", INFO, body)
        return {"payload": payload if status == 200 else [],
                "request": {"method": "POST", "url": INFO, "body": body}, "retrieved_at": now()}
