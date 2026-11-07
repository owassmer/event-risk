"""The portfolio: its holdings at current prices, the look-through into names, and each name's price.

The holdings at current prices and V: as of today, each name's latest mark; for a past as-of, that day's close.
A holding given in dollars, or as a weight of a total, was worth what was given when it was given: its quantity is
implied at the price the store held then, the mark it had read by that moment or, before any, the first it read,
and from then on it is valued at the current price, as a holding given as a quantity is. When it was given is when
its link was made or its figure last edited (Store.given_at). Each holding's move is its price's session move,
in dollars its quantity times the change from the close before; the portfolio's is their sum, and as a fraction of
what the portfolio was worth at those closes. Then the look-through into Names and looks_through_to links, which
gives every Name its e and each holding the dollars it reaches at each name, by the funds they pass through, gives
each fund it expands its parts, and gives each fund whose holdings it reads what it holds; and each Name's type, sic,
company (what it is, from its reference), and current price.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Any

from harness.derive import series
from harness.derive.engine import Derivation, Inputs, Unavailable, canonical

# A constituent under a quarter of a percent of its fund, in absolute value, maps to the residual: it is a negligible
# part of the fund, while resolving it takes a reference call. One the portfolio already names maps whatever its
# weight, its reference already held.
FLOOR = 0.0025
# A plain basket's weights sum near one; a levered or inverse fund's sum near two, three, or
# a negative number, and it is not a basket of its constituents.
BASKET = (0.5, 1.5)
NAME_TYPES = ("CS", "ADRC")
FUND_TYPES = ("ETF", "ETN", "ETV", "ETS")
CASH = "CASH"


def ticker_of(name_id: str) -> str:
    return name_id.split(":", 1)[1]


def name_id(ticker: str) -> str:
    return f"name:{ticker}"


def pseudo(ticker: str) -> str | None:
    """CASH and CASH:{fund} are cash; UNMAPPED:{fund} is a fund's residual."""
    if ticker == CASH or ticker.startswith("CASH:"):
        return "cash"
    if ticker.startswith("UNMAPPED:"):
        return "unmapped"
    return None


def asof_of(ctx: Inputs) -> str:
    return ctx.object("portfolio")["stated"]["asof"]


def price_series(ctx: Inputs, ticker: str, asof: str, *, required: bool = True) -> dict:
    """The daily bars of a ticker over the window, or a crypto pair's hourly bars."""
    kind = "hourly" if series.is_crypto(ticker) else "bars"
    found = ctx.data(kind, ticker, covering=(series.lookback(asof), asof), required=required)
    return {"ticker": ticker, "ref": found}


def return_series(ctx: Inputs, ticker: str, asof: str, *, required: bool = True) -> dict:
    """What a ticker's returns read: its price series and, for a listed ticker, its
    dividends over the window. A crypto pair pays none. Not `required`, a series the store cannot read is empty."""
    found = price_series(ctx, ticker, asof, required=required)
    if not series.is_crypto(ticker):
        found["dividends"] = ctx.data("dividends", ticker, covering=(series.lookback(asof), asof), required=required)
    return found


def paid(item: dict, load) -> dict[str, float]:
    """The dividends a return series read, by ex-dividend date."""
    return series.dividends(load(item["dividends"])) if (item.get("dividends") or {}).get("data") else {}


def closes(item: dict, load) -> dict[str, float]:
    if not item["ref"].get("data"):  # a series the store could not read
        return {}
    payload, fetched = load(item["ref"]), item["ref"]["retrieved_at"]
    if series.is_crypto(item["ticker"]):
        return series.closes_at_1600(payload, fetched)
    return series.daily_closes(payload, fetched)


def reference_type(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        return None
    return (payload.get("results") or {}).get("type")


def kind_of(reference_payload: Any) -> str:
    """A ticker is a name, a fund, or other."""
    t = reference_type(reference_payload)
    return "name" if t in NAME_TYPES else "fund" if t in FUND_TYPES else "other"


# ---------------------------------------------------------------- a current price


def read_date(retrieved_at: str) -> str:
    """The New York date a fetch was made on."""
    return datetime.fromisoformat(retrieved_at).astimezone(series.NY).date().isoformat()


def is_today(asof: str) -> bool:
    """Whether the as-of is the New York date now: only which fetches go side by side ahead of their reads turns on
    it; what a price reads is decided by when its series was read."""
    return datetime.now(series.NY).date().isoformat() == asof


def unlisted(payload: Any, ticker: str) -> str | None:
    """Why a crypto pair has no prices, where its source does not list it: the source's answer, in plain words."""
    if series.is_crypto(ticker) and isinstance(payload, dict) and payload.get("status") == "NOT_FOUND":
        return f"{payload.get('error')}, so {ticker} has no prices"
    return None


FIRST = ""  # a moment before every reading: the reading as of it is the first


def current_inputs(ctx: Inputs, ticker: str, asof: str, at: str | None = None) -> dict:
    """What a ticker's current price reads: its series to the as-of, and, when the series was read on the
    as-of date itself, the as-of is today and its latest mark: a stock's snapshot, a crypto pair's last trade. With
    `at`, the mark the store held at that moment, and with FIRST its first mark of the as-of, in place of the
    latest."""
    item = price_series(ctx, ticker, asof)
    why = unlisted(ctx.payload(item["ref"]), ticker) if item["ref"].get("data") else None
    if why:
        raise Unavailable(why)
    today = item["ref"].get("data") is not None and read_date(item["ref"]["retrieved_at"]) == asof
    out = {"ticker": ticker, "asof": asof, "series": item, "today": today}
    if today:
        kind = "last_trade" if series.is_crypto(ticker) else "snapshot"
        out[kind] = ctx.data(kind, ticker, covering=(asof, asof), required=False, at=at)
    return out


def current_compute(m: dict, load) -> dict | None:
    """The current price with its time, what it is, and its session; the close of the session before; and the
    session's move between them, in dollars and as a fraction. When the as-of is today the price is the latest mark,
    whose session is its New York date: during a session the move is that day's, and after it, until the next opens,
    that session's whole move. A today with no mark reads the latest completed close, and a past as-of that day's
    close."""
    found = closes(m["series"], load)
    mark = None
    if m["today"]:
        if series.is_crypto(m["ticker"]):  # its last trade, or, where none was read, its latest hourly bar
            if (m.get("last_trade") or {}).get("data"):
                mark = series.trade_mark(load(m["last_trade"]))
            mark = mark or series.hourly_mark(load(m["series"]["ref"]))
        elif (m.get("snapshot") or {}).get("data"):
            mark = series.snapshot_mark(load(m["snapshot"]))
    if mark is not None:
        out = {**mark, "session": mark["time"][:10]}
    else:
        days = sorted(d for d in found if d <= m["asof"])
        if not days:
            return None
        out = {"price": found[days[-1]], "time": None, "session": days[-1], "from": "close"}
    before = sorted(d for d in found if d < out["session"])
    if before:
        out.update(previous_close=found[before[-1]], previous_session=before[-1],
                   session_change=out["price"] - found[before[-1]],
                   session_move=out["price"] / found[before[-1]] - 1.0 if found[before[-1]] else None)
    return out


# ---------------------------------------------------------------- a Name's price and reference


def _price_inputs(ctx: Inputs, nid: str) -> dict:
    kind = pseudo(ticker_of(nid))
    if kind:
        return {"name": nid, "pseudo": kind}
    asof = asof_of(ctx)
    return {"name": nid, **current_inputs(ctx, ticker_of(nid), asof)}


def _price_compute(m: dict, load) -> dict:
    if m.get("pseudo") == "cash":
        return {"prices": {"price": 1.0, "session": None, "time": None, "from": "cash"}}
    if m.get("pseudo"):
        return {"prices": None}
    return {"prices": current_compute(m, load)}


PRICE = Derivation("price", 5, "Name", _price_inputs, _price_compute, fields=("prices",))


def _reference_inputs(ctx: Inputs, nid: str) -> dict:
    kind = pseudo(ticker_of(nid))
    if kind:
        return {"name": nid, "pseudo": kind}
    asof = asof_of(ctx)
    return {"name": nid, "reference": ctx.data("ticker_ref", ticker_of(nid), covering=(asof, asof))}


def company_of(payload: Any) -> dict | None:
    """What a ticker is, from its reference: its company's name, or the fund's; what it does, in the reference's own
    words; and its industry, its SIC code's description. None where the reference holds nothing."""
    if not reference_type(payload):
        return None
    found = payload.get("results") or {}
    return {"name": found.get("name"), "does": found.get("description"), "industry": found.get("sic_description")}


def _reference_compute(m: dict, load) -> dict:
    if m.get("pseudo"):
        return {"type": m["pseudo"], "sic": None, "company": None}
    payload = load(m["reference"])
    results = (payload.get("results") or {}) if reference_type(payload) else {}
    return {"type": results.get("type"), "sic": results.get("sic_code"), "company": company_of(payload)}


REFERENCE = Derivation("reference", 2, "Name", _reference_inputs, _reference_compute,
                       fields=("type", "sic", "company"))


# ---------------------------------------------------------------- holdings and V


def _holdings_inputs(ctx: Inputs, subject: str) -> dict:
    portfolio = ctx.object("portfolio")
    holds = ctx.links("portfolio", "holds")
    asof = portfolio["stated"]["asof"]
    held = [ticker_of(h["to"]) for h in holds if not pseudo(ticker_of(h["to"]))]
    for kind in ("bars", "hourly"):  # every holding's series side by side, then each price reads its own
        ctx.prefetch(kind, [t for t in held if (kind == "hourly") == series.is_crypto(t)],
                     (series.lookback(asof), asof), stage="holdings")
    if is_today(asof):  # and each one's latest mark, which a price as of today reads
        ctx.prefetch("snapshot", [t for t in held if not series.is_crypto(t)], (asof, asof), stage="holdings")
        ctx.prefetch("last_trade", [t for t in held if series.is_crypto(t)], (asof, asof), stage="holdings")
    prices: dict[str, dict] = {}
    given: dict[str, dict] = {}  # for a holding given in dollars or as a weight, the price the store held then
    for h in holds:
        ticker = ticker_of(h["to"])
        if ticker == CASH:
            continue
        try:
            prices[h["to"]] = ctx.value("price", h["to"])
        except Unavailable as e:
            if "quantity" in h["stated"]:
                raise Unavailable(f"{h['to']} is held as a quantity, and its current price is not here: {e}") from None
            prices[h["to"]] = {"unavailable": str(e)}
            continue
        if "quantity" not in h["stated"] and not pseudo(ticker):
            try:
                given[h["id"]] = current_inputs(ctx, ticker, asof, at=ctx.given_at(h["id"]))
            except Unavailable:
                pass
    return {"portfolio": portfolio, "holds": holds, "prices": prices, "given": given}


def _holdings_compute(m: dict, load) -> dict:
    total = m["portfolio"]["stated"].get("total")
    rows: dict[str, dict] = {}
    for h in m["holds"]:
        stated = h["stated"]
        given = next(k for k in ("quantity", "dollars", "weight") if k in stated)
        cash = ticker_of(h["to"]) == CASH
        current = {} if cash else (m["prices"][h["to"]].get("output") or {}).get("prices") or {}
        price = 1.0 if cash else current.get("price")
        then = None  # the price the store held when a holding in dollars or a weight was given
        if given == "quantity":
            dollars = stated["quantity"] * price
            quantity = dollars / price if price else None
        else:
            worth = stated["dollars"] if given == "dollars" else stated["weight"] * total  # what it was worth then
            if h["id"] in m["given"]:
                then = (current_compute(m["given"][h["id"]], load) or {}).get("price")
            if then and price:  # its quantity implied then, valued now
                quantity, dollars = worth / then, worth * (price / then)
            else:
                dollars = worth
                quantity = dollars / price if price else None
        before = current.get("previous_close")
        rows[h["id"]] = {"name": h["to"], "price": price, "session": current.get("session"),
                         "price_time": current.get("time"), "price_from": current.get("from"),
                         "previous_close": before, "session_move": 0.0 if cash else current.get("session_move"),
                         "session_move_dollars": 0.0 if cash else (dollars - quantity * before
                                                                    if quantity is not None and before else None),
                         "stated": given, "dollars": dollars, "quantity": quantity,
                         **({"given_price": then} if then else {})}
    value = sum(r["dollars"] for r in rows.values())
    for r in rows.values():
        r["weight"] = r["dollars"] / value if value else None
    moved = [r["session_move_dollars"] for r in rows.values()]
    change = sum(moved) if moved and None not in moved else None
    return {"V": value, "holds": rows, "session_move_dollars": change,
            "session_move": change / (value - change) if change is not None and value - change else None}


def _holding_amounts(output: dict, link_id: str) -> dict:
    """The two amounts a holding was not given, at its current price."""
    row = output["holds"].get(link_id) or {}
    return {k: row[k] for k in ("quantity", "dollars", "weight") if k in row and k != row.get("stated")}


HOLDINGS = Derivation("holdings", 4, "Portfolio", _holdings_inputs, _holdings_compute, fields=("V",),
                      subject_of=lambda store, entity: "portfolio", stage="holdings",
                      extra=(("holds", ("quantity", "dollars", "weight"), _holding_amounts),))


# ---------------------------------------------------------------- look-through


def _valid_rows(payload: Any) -> list[dict]:
    """A fund's constituent rows. A row identical in every field to an earlier one is that
    row reported twice, and counts once (a fund of bonds can return each bond twice, doubling its weights); rows
    that share a ticker but differ in any field are distinct holdings, such as several bonds under one ticker."""
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        return []
    seen: set[str] = set()
    rows = []
    for r in payload.get("results") or []:
        if not isinstance(r, dict):
            continue
        key = canonical(r)
        if key not in seen:
            seen.add(key)
            rows.append(r)
    return rows


def _is_cash(row: dict) -> bool:
    return str(row.get("asset_class") or "").strip().lower() == "cash"


# The asset classes ETF Global gives a listed share: a company's stock, or a real-estate company's.
LISTED = ("equity", "real estate")
US = "US"  # the reference's listings are US ones: a row's country_of_exchange for a share that trades in the US


def _is_listed(row: dict) -> bool:
    """A constituent resolves through the ticker reference only when ETF Global classes it as a listed share, Equity
    or Real Estate, in any case: a bond's or a coin's ticker can be a listed company's or fund's (a when-issued
    Treasury's ticker can be a listed company's, and a coin's a listed fund's). Every other class, or none, maps to
    the fund's residual."""
    return str(row.get("asset_class") or "").strip().lower() in LISTED


def confirmed(row: dict, reference: Any) -> bool:
    """Whether a constituent's identifiers confirm that its ticker's reference is the security the fund holds. The
    reference is a US listing: `locale` us, `market` stocks, with `composite_figi`, the FIGI of the listing across US
    venues, and `share_class_figi`, the FIGI of its share class across countries
    (https://massive.com/docs/rest/stocks/tickers/ticker-overview). The fund's row names the security it holds by
    `figi`, and where it trades by `country_of_exchange`. A FIGI is assigned to a share class worldwide, to each
    country's listing of it (the composite), and to each trading venue's (https://www.openfigi.com/about/allocation-rules).
    So the reference is confirmed when the row's FIGI is its composite or share-class FIGI; or else when the row
    says the share trades in the US, where a ticker names one listing on a date, and the reference is that US
    listing, whose FIGIs are missing or of another level. It is not otherwise: a share listed abroad is another
    listing than the US one under its ticker, often another company's altogether, and an ADR is a security of its
    own. Only identifiers are compared, never names."""
    if not isinstance(reference, dict) or reference.get("status") != "OK":
        return False
    found = reference.get("results") or {}
    figi = row.get("figi")
    if figi and figi in (found.get("composite_figi"), found.get("share_class_figi")):
        return True
    return row.get("country_of_exchange") == US and found.get("locale") == "us"


# ---------------------------------------------------------------- what a fund holds


@cache
def _places() -> dict[str, str]:
    """Each country's short name by its two-letter ISO 3166 code, from the IANA time zone database's iso3166.tab,
    which the tzdata package ships."""
    text = files("tzdata").joinpath("zoneinfo", "iso3166.tab").read_text(encoding="utf-8")
    rows = (line.split("\t") for line in text.splitlines() if line and not line.startswith("#"))
    return {row[0]: row[1] for row in rows if len(row) == 2}


def listed_in(code: str | None) -> str | None:
    """Where a holding is listed, the country of its exchange, by name; a code the table lacks reads as given."""
    return _places().get(code, code) if code else None


def us_composites(payload: Any) -> frozenset[str]:
    """The composite FIGIs of the US listings Massive lists, each a listing in the US."""
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        return frozenset()
    return frozenset(r["composite_figi"] for r in payload.get("results") or []
                     if isinstance(r, dict) and r.get("composite_figi") and r.get("locale") == "us")


def where_listed(row: dict, us: frozenset[str]) -> str | None:
    """Where a holding is listed, decided by its identifiers where they decide it: a row whose FIGI is a US listing's
    composite FIGI is listed in the US, whatever country the fund's data gives its exchange; any other row is listed
    where the fund's data says. Only identifiers are compared, never names."""
    return "US" if row.get("figi") and row["figi"] in us else row.get("country_of_exchange") or None


def _group(rows: list[tuple[dict, float]], key, label, dollars: float) -> list[dict]:
    """Rows summed by a key, each group named as its heaviest row names it, largest weight first."""
    found: dict[Any, dict] = {}
    for row, w in rows:
        k = key(row)
        g = found.setdefault(k, {**label(row), "weight": 0.0, "heaviest": abs(w)})
        g["weight"] += w
        if abs(w) > g["heaviest"]:
            g.update({**label(row), "heaviest": abs(w)})
    out = []
    for g in sorted(found.values(), key=lambda g: -abs(g["weight"])):
        g.pop("heaviest")
        out.append({**g, "dollars": dollars * g["weight"]})
    return out


def _fold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _divisions(rows: list[tuple[dict, float]], dollars: float, us: frozenset[str]) -> dict:
    """Rows divided by where they are listed and by kind of asset: each place by name, null where a row names no
    exchange; each kind as its asset class and security type, as the fund's holdings give them."""
    return {"by_listing": _group(rows, lambda r: where_listed(r, us),
                                 lambda r: {"listed": listed_in(where_listed(r, us))}, dollars),
            "by_kind": _group(rows, lambda r: (_fold(r.get("asset_class")), _fold(r.get("security_type"))),
                              lambda r: {"asset_class": r.get("asset_class"), "security_type": r.get("security_type")},
                              dollars)}


def what_it_holds(fund: str, payload: Any, references: dict, dollars: float, priced: set[str],
                  us: frozenset[str] = frozenset()) -> dict | None:
    """What a fund holds, from its holdings, every row with a weight counted whether or not the look-through prices
    it: each holding of at least the floor by name, with its ticker as the fund gives it, where it is listed, its kind
    of asset, its industry where its reference is confirmed as it, its weight, and its dollars of the portfolio's, and
    the name the store prices it as; the rest summed, with how many and how they divide; and the whole fund divided
    by where its holdings are listed and by kind of asset. The fund's name for a holding is its reference's where the
    reference is confirmed, and the fund's own otherwise. None where its holdings cannot be read."""
    rows = [(r, r["weight"]) for r in _valid_rows(payload) if r.get("weight") is not None]
    if not rows:
        return None
    named, rest = [], []
    for r, w in sorted(rows, key=lambda x: -abs(x[1])):
        if abs(w) < FLOOR:
            rest.append((r, w))
            continue
        ticker = r.get("constituent_ticker")
        reference = references.get(ticker) if ticker else None
        known = company_of(reference) if _is_listed(r) and confirmed(r, reference) else None
        nid = name_id(ticker) if ticker and name_id(ticker) in priced else None
        named.append({"name": (known or {}).get("name") or r.get("constituent_name"), "ticker": ticker,
                      "listed": listed_in(where_listed(r, us)), "asset_class": r.get("asset_class"),
                      "security_type": r.get("security_type"), "industry": (known or {}).get("industry"),
                      "weight": w, "dollars": dollars * w, "priced_as": nid})
    rest_weight = sum(w for _, w in rest)
    return {"fund": fund, "holdings_asof": payload.get("effective_date"), "count": len(rows),
            "weights_sum": sum(w for _, w in rows), "dollars": dollars, "named": named,
            "rest": {"count": len(rest), "weight": rest_weight, "dollars": dollars * rest_weight,
                     **_divisions(rest, dollars, us)},
            **_divisions(rows, dollars, us)}


def _lookthrough_inputs(ctx: Inputs, subject: str) -> dict:
    """Read what the look-through reads, in the order it reads it: each holding's reference,
    each fund's constituents, and each constituent's reference, one level of funds deep: a fund constituent is
    walked into when its reference is confirmed as the fund the row holds."""
    asof = asof_of(ctx)
    holdings = ctx.value("holdings", "portfolio")
    refs: dict[str, dict] = {}
    constituents: dict[str, dict] = {}

    def reference(ticker: str) -> Any:
        if ticker not in refs:
            refs[ticker] = ctx.data("ticker_ref", ticker, covering=(asof, asof))
        return ctx.payload(refs[ticker])

    def kind(ticker: str) -> str:
        return kind_of(reference(ticker))

    def walk(fund: str, depth: int, path_weight: float) -> None:
        if fund not in constituents:
            constituents[fund] = ctx.data("constituents", fund, covering=(asof, asof))
        rows = _valid_rows(ctx.payload(constituents[fund]))
        ctx.prefetch("ticker_ref", [r["constituent_ticker"] for r in rows if r.get("weight") is not None
                                    and _is_listed(r) and r.get("constituent_ticker")
                                    and abs(path_weight * r["weight"]) >= FLOOR], (asof, asof),
                     stage="funds", about=fund)
        for row in rows:
            w = row.get("weight")
            ticker = row.get("constituent_ticker")
            if w is None or not _is_listed(row) or not ticker or abs(path_weight * w) < FLOOR:
                continue
            if kind(ticker) == "fund" and depth == 1 and confirmed(row, reference(ticker)):
                walk(ticker, 2, path_weight * w)

    held = [ticker_of(row["name"]) for row in holdings["output"]["holds"].values() if ticker_of(row["name"]) != CASH]
    ctx.prefetch("ticker_ref", held, (asof, asof), stage="funds")
    ctx.prefetch("constituents", [t for t in held if kind(t) == "fund"], (asof, asof), stage="funds")
    for ticker in held:
        if kind(ticker) == "fund":
            walk(ticker, 1, 1.0)
    listings = None
    if constituents:  # where each fund's holdings are listed, by their identifiers
        try:
            listings = ctx.data("listings", "stocks", covering=(asof, asof), required=False)
        except Unavailable:
            listings = None
    return {"asof": asof, "holdings": holdings, "references": refs, "constituents": constituents,
            "listings": listings}


def _look(m: dict, references: dict, constituents: dict, named: frozenset[str]) -> dict:
    """Book each holding's dollars to the names they reach: a name constituent at or above the floor, or of any weight
    when its ticker is in `named`; the residual and the cash of each fund it expands; and each fund it does not, whole.
    """
    e: dict[str, float] = defaultdict(float)
    kinds: dict[str, dict] = {}
    links: dict[str, dict] = {}
    funds: dict[str, dict] = {}
    by_holding: dict[str, dict[str, float]] = {}  # each holding's dollars at each name it reaches
    paths: dict[str, dict[tuple, float]] = {}  # each holding's dollars at each name, by the funds they pass through
    reached: dict[str, float] = defaultdict(float)  # each fund's dollars, by every path to it
    holding = [None]
    inside: list[str] = []  # the funds being expanded, outermost first

    def book(ticker: str, dollars: float, kind: str, beta: str | None) -> None:
        """Dollars that stop at a name: the series its own betas are fitted on, the fund's for a residual, or none
        for cash; and the funds they reach it through."""
        nid = name_id(ticker)
        e[nid] += dollars
        kinds[nid] = {"kind": kind, "beta": beta}
        reached = by_holding.setdefault(holding[0], {})
        reached[nid] = reached.get(nid, 0.0) + dollars
        path = paths.setdefault(holding[0], {})
        path[(tuple(inside), nid)] = path.get((tuple(inside), nid), 0.0) + dollars

    def link(fund: str, ticker: str, dollars: float, weight: float | None, effective: str | None) -> None:
        lid = f"looks_through_to:{name_id(fund)}>{name_id(ticker)}"
        found = links.setdefault(lid, {"relation": "looks_through_to", "from": name_id(fund), "to": name_id(ticker),
                                       "fields": {"weight": weight, "dollars": 0.0, "holdings_asof": effective}})
        found["fields"]["dollars"] += dollars

    def whole(fund: str, reason: str) -> bool:
        funds.setdefault(fund, {"expanded": False, "reason": reason})
        return False

    def expand(fund: str, dollars: float, depth: int, path_weight: float) -> bool:
        reached[fund] += dollars
        payload = constituents.get(fund)
        if isinstance(payload, dict) and payload.get("status") == "NOT_AUTHORIZED":
            return whole(fund, "its holdings cannot be read here")
        rows = _valid_rows(payload)
        if not rows:
            return whole(fund, f"no holdings are published for it on or before {m['asof']}")
        total = sum(r["weight"] for r in rows if r.get("weight") is not None)
        if not BASKET[0] <= total <= BASKET[1]:
            return whole(fund, f"its weights sum to {total:.4f}, outside [{BASKET[0]}, {BASKET[1]}]")
        cash, plan = 0.0, []
        rest = {"small": 0.0, "unresolved": 0.0, "not_shares": 0.0}  # what the residual holds, as weights of the fund
        for r in rows:
            w = r.get("weight")
            if w is None:
                continue
            if _is_cash(r):
                cash += dollars * w
                continue
            ticker = r.get("constituent_ticker")
            if not _is_listed(r) or not ticker:
                rest["not_shares"] += w
                continue
            if abs(path_weight * w) < FLOOR and ticker not in named:
                rest["small"] += w
                continue
            kind = kind_of(references.get(ticker))
            if kind in ("name", "fund") and confirmed(r, references.get(ticker)):
                plan.append((kind, ticker, w))
            else:
                rest["unresolved"] += w
        if not plan:
            return whole(fund, "none of its holdings is a US-listed share or fund")
        effective = payload.get("effective_date")
        mapped = 0.0
        inside.append(fund)
        for kind, ticker, w in plan:
            amount = dollars * w
            link(fund, ticker, amount, w, effective)
            mapped += amount
            if kind == "name":
                book(ticker, amount, "name", ticker)
            elif not (depth == 1 and expand(ticker, amount, 2, path_weight * w)):
                book(ticker, amount, "fund", ticker)  # a fund met at the next depth, or one that does not expand
        residual = dollars - mapped - cash
        if residual > 0:
            link(fund, f"UNMAPPED:{fund}", residual, None, effective)
            book(f"UNMAPPED:{fund}", residual, "unmapped", fund)
        else:
            cash += residual  # the financing leg of a levered fund
        if cash:
            link(fund, f"CASH:{fund}", cash, None, effective)
            book(f"CASH:{fund}", cash, "cash", None)
        inside.pop()
        rest["weight"] = max(residual, 0.0) / dollars if dollars else None
        funds[fund] = {"expanded": True, "effective_date": effective, "weights_sum": total, "mapped": mapped,
                       "cash": cash, "cash_weight": cash / dollars if dollars else None,
                       "unmapped": max(residual, 0.0), "depth": depth, "rest": rest}
        return True

    for hid, row in m["holdings"]["output"]["holds"].items():
        holding[0] = hid
        ticker, dollars = ticker_of(row["name"]), row["dollars"]
        if ticker == CASH:
            book(CASH, dollars, "cash", None)
            continue
        kind = kind_of(references.get(ticker))
        if kind == "fund":
            if not expand(ticker, dollars, 1, 1.0):
                book(ticker, dollars, "fund", ticker)
        else:
            book(ticker, dollars, kind, ticker)
    return {"e": e, "kinds": kinds, "links": links, "funds": funds, "by_holding": by_holding, "paths": paths,
            "reached": reached}


def _lookthrough_compute(m: dict, load) -> dict:
    references = {t: load(r) for t, r in m["references"].items()}
    constituents = {f: load(r) for f, r in m["constituents"].items()}
    listings = m.get("listings")
    us = us_composites(load(listings)) if listings and listings.get("data") else frozenset()
    # The names the portfolio names at the floor, held directly or above it in a fund: their references are held, so
    # a fund's constituent among them maps whatever its weight, and its own shock reaches every dollar of it.
    at_floor = _look(m, references, constituents, frozenset())
    named = frozenset(ticker_of(nid) for nid, k in at_floor["kinds"].items() if k["kind"] == "name")
    found = _look(m, references, constituents, named)
    e, kinds, links, funds, paths = found["e"], found["kinds"], found["links"], found["funds"], found["paths"]
    for fund, payload in constituents.items():
        if fund in funds:
            priced = {rec["to"] for rec in links.values() if rec["from"] == name_id(fund)}
            funds[fund]["holds"] = what_it_holds(fund, payload, references, found["reached"][fund], priced, us)
    # An expanded fund is fitted on its own series, as every holding is; its parts are what it holds at the next level,
    # a name, its residual, its cash, or a fund expanded inside it, each as a weight of its dollars over every path.
    parts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for reaching in paths.values():
        for (through, nid), dollars in reaching.items():
            for k, fund in enumerate(through):
                parts[fund][name_id(through[k + 1]) if k + 1 < len(through) else nid] += dollars
    for fund, info in funds.items():
        if info["expanded"]:
            kinds.setdefault(name_id(fund), {"kind": "fund", "beta": fund})["expanded"] = True
            total = sum(parts[fund].values())
            info["parts"] = {nid: dollars / total for nid, dollars in sorted(parts[fund].items())} if total else {}
    named = {rec["to"] for rec in links.values()}
    return {"asof": m["asof"], "e": dict(e), "kinds": kinds, "funds": funds, "by_holding": found["by_holding"],
            "paths": {hid: [{"funds": list(through), "name": nid, "dollars": dollars}
                            for (through, nid), dollars in reaching.items()] for hid, reaching in paths.items()},
            "objects": {nid: {"type": "Name", "fields": {}} for nid in sorted(named)},
            "links": links, "sum_e": sum(e.values())}


def _name_fields(output: dict, nid: str) -> dict:
    """What the look-through gives every Name, its e, and a fund whose holdings it read, what it holds."""
    fund = output["funds"].get(ticker_of(nid)) or {}
    return {"e": output["e"].get(nid, 0.0), **({"holds": fund["holds"]} if fund.get("holds") else {})}


LOOKTHROUGH = Derivation(
    "lookthrough", 12, "Portfolio", _lookthrough_inputs, _lookthrough_compute,
    subject_of=lambda store, entity: "portfolio", stage="funds",
    produces=("Name", "looks_through_to"),
    # Every Name has an e: what the look-through books to it, and 0 where it books nothing.
    extra=(("Name", ("e", "holds"), _name_fields),),
)

DERIVATIONS = (PRICE, REFERENCE, HOLDINGS, LOOKTHROUGH)
