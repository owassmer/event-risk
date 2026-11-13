"""A fund's holding resolved to its company (MATH.md 3.1), by identifiers and never by a name or a ticker alone.

A fund's row names a security as ETF Global holds it: its ticker where it trades, which abroad can be an unrelated US
company's; its `figi`, usually the FIGI of its listing where it trades; its `isin`, the share class's; and
`country_of_exchange`, which can say US of a row traded abroad. A row from the fund's Form N-PORT report
(harness.venues.sec) gives its `isin` or its `cusip`, often only one, sometimes its ticker, and no FIGI or exchange;
where it gives no ISIN its share class is found by its CUSIP (class_id). The company is found through three
identifiers, each read where it is kept, and is priced through one US listing of it:

1. The security itself: the row's FIGI is the composite or share-class FIGI of the reference Massive holds under the
   row's own ticker, so the row is that US listing.
2. Its share class: OpenFIGI lists, by the row's ISIN or CUSIP, the share class's US listing (harness.venues.openfigi), which is
   Massive's listing under that ticker unless Massive's reference carries FIGIs or a CIK that name another; within the
   US a ticker names one listing on a date.
3. Its company: the SEC lists the CIK of the company behind a US listing (harness.venues.sec), an over-the-counter one
   among them, and Massive lists that company's exchange listings by its CIK as of the as-of, a depositary receipt
   among them.
4. Its listing then: a fund's report can name a security by identifiers its issuer has since replaced (a quarter-old
   Form N-PORT names ONEOK's and Exxon Mobil's shares before each became a new security, ExxonMobil Holdings' a new
   company), which no listing today carries. Where nothing above confirms a row, Massive's reference by its CUSIP as
   of its holdings' date gives the listing that carried it then, with its CIK; that listing's ticker today confirms
   it where its reference names the same CIK, or where the reference on the day the security now under the ticker
   took it (its ticker events) still named that CIK, the security having passed to it. A ticker another company took
   names another CIK on that day, and confirms nothing.

A read that fails past its retries (a service that does not answer) is not an answer: it raises, the look-through
waits for it, and a call that needs what the portfolio owns fails with nothing written (TOOLS.md 5). A row whose
identifiers confirm no US listing is the fund's other holdings, by name: that is what the sources answered.

Of the US listings these confirm, a row listed in the US is its listing on an exchange (Massive's `market` stocks)
where it has one: the security itself or its share class first, then the one of the row's kind, common or preferred.
A row listed abroad is the security the fund holds, on its home market and in its currency (MATH.md 3.1): its listing
`{ISIN}:{country}`, the share class its ISIN names on the exchange of the country its fund's holdings name (or, where
they name none, its ISIN's), which `home` reads (harness.venues.yahoo). A US listing that confirms it, a depositary
receipt among them, names its company (its CIK), never its price.

A company is one name, whatever securities the portfolio holds it by (MATH.md 3.1). Its id is a listing chosen by one
rule, over the company's listings and never over what is held: its first US ticker, the first the SEC's list of tickers
gives its CIK (`first_ticker`; the list gives a company's main listing first, GOOGL before GOOG, BRK-B before BRK-A,
BAC before its preferreds), in Massive's form (a class after a dot); and where the SEC gives it none, its home
listing. `name_of` gives the id and the pricing security of the company a ticker names, for a company the portfolio
holds nowhere: its home listing, which for a company in the US is its id's listing. A depositary receipt names no
ordinary among its identifiers (its ISIN is the receipt's own), so a company named only by its receipt is priced
through the receipt. It is taken by its identifiers, whether or not a source prices it: identity is not
pricing, and the company, the security the fund holds and its exposure are identified however a provider covers that
market, so a listing its price source does not cover stays a name the fund holds, with its exposure. A holding of
the US with no exchange listing is priced through one over the counter (`otc`) that Massive prices: one it has a close
for, however old. Where several remain, the company is ambiguous, and like one no identifier confirms or Massive does
not price, it stays in the fund's other holdings, with its reason. An identifier naming another listing of the same
company is no conflict: only one naming another security of the same class, or another company, is.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any, Callable

from harness.venues.massive import answered

# Massive's types that are a share of a company (https://massive.com/docs/rest/stocks/tickers/ticker-types): common
# stock, ordinary shares (as a company listed abroad trades over the counter), a depositary receipt of common or
# preferred shares, New York registry shares, a global depositary receipt, and preferred stock.
COMPANY_TYPES = ("CS", "OS", "ADRC", "ADRP", "NYRS", "GDR", "PFD")
PREFERRED_TYPES = ("PFD", "ADRP")
FUND_TYPES = ("ETF", "ETN", "ETV", "ETS", "FUND", "MF")  # FUND: a closed-end fund; MF: a mutual fund (MATH.md 3.1,
# harness.venues.funds)
EXCHANGE, OTC = "stocks", "otc"  # Massive's market for a listing on an exchange, and over the counter
FUNDS = "funds"  # a mutual fund's share class, which no exchange lists (harness.venues.funds)
SEC_FILE = "company_tickers_exchange"  # the subject the SEC's list of tickers is kept under
FUNDS_FILE = "company_tickers_mf"  # and its list of the funds' share classes (harness.venues.sec.FUNDS)

SELF, CLASS, COMPANY = "the security itself", "its share class", "its company"
THEN = "its listing then, by its CUSIP"
HOME = "its home listing"
_ISIN = re.compile(r"[A-Z]{2}[0-9A-Z]{9}[0-9]")


def results(payload: Any) -> dict:
    """A reference's fields, or none where Massive answered without one."""
    return (payload.get("results") or {}) if answered(payload) else {}


US_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]*")  # a US listing's ticker as Massive writes it ("BRK.B")


def us_ticker(ticker: str | None) -> bool:
    """Whether a string can be a US listing's ticker, so asking Massive's reference for it is a request it can read: a
    local ticker abroad with its exchange code ("3750 HK") is answered 400 as invalid, a request of ours malformed."""
    return bool(ticker) and bool(US_TICKER.fullmatch(ticker))


def row_key(row: dict) -> str:
    """A fund's row by what identifies it, the same in every fund that holds it."""
    return json.dumps([row.get(k) for k in ("constituent_ticker", "figi", "isin", "cusip", "security_type",
                                             "asset_class")])


def is_preferred(row: dict) -> bool:
    """A preferred share: ETF Global says so in its security type, Form N-PORT in its asset type."""
    return "prefer" in f"{row.get('security_type') or ''} {row.get('asset_class') or ''}".lower()


def class_id(row: dict) -> tuple[str, str] | None:
    """What names a row's share class at OpenFIGI: its ISIN, or, where the fund gives none, its CUSIP (or CINS, a
    CUSIP's form for an issuer outside the US and Canada), as Form N-PORT often gives only that; or, for a listing
    abroad held by its symbol whose ISIN no source here gives, its share class's FIGI."""
    if row.get("isin"):
        return "figi", row["isin"]
    if row.get("cusip"):
        return "figi_cusip", row["cusip"]
    if row.get("share_class"):
        return "figi_class", row["share_class"]
    return None


def cik_number(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class Lookup:
    """What the resolution reads, by kind and subject, from `read(kind, subject)`, which gives a payload or None. A read
    that fails past its retries raises where it is fetched, so the resolution reads only answers."""

    def __init__(self, read: Callable[[str, str], Any], asof: str):
        self._get = read
        self.asof = asof
        self._sec: dict[str, int] | None = None

    def _read(self, kind: str, subject: str) -> Any:
        return self._get(kind, subject)

    def reference(self, ticker: str | None) -> dict:
        """The reference Massive holds under a US ticker; none for a string that cannot be one (a fund's local ticker
        abroad, "3750 HK"), which is never asked."""
        return results(self._read("ticker_ref", ticker)) if us_ticker(ticker) else {}

    def us_listings(self, row: dict) -> list[dict]:
        """The US listings of a row's share class (class_id), each under its ticker as Massive writes it: a class
        letter after a dot, where OpenFIGI writes a slash."""
        named = class_id(row)
        found = self._read(*named) if named else None
        listings = (found or {}).get("listings") or [] if isinstance(found, dict) else []
        return [{**x, "ticker": str(x["ticker"]).replace("/", ".")} for x in listings if x.get("ticker")]

    def cik_of(self, ticker: str) -> int | None:
        if self._sec is None:
            found = self._read("sec_tickers", SEC_FILE)
            if found is None:
                return None
            fields = found.get("fields") or []
            at = {f: i for i, f in enumerate(fields)}
            self._sec = {row[at["ticker"]]: row[at["cik"]] for row in found.get("data") or []
                         if "ticker" in at and "cik" in at and row[at["ticker"]]} if at else {}
        # the SEC writes a class after a dash (BRK-B) where Massive writes a dot (BRK.B)
        return cik_number(self._sec.get(ticker) or (self._sec.get(ticker.replace(".", "-")) if ticker else None))

    def company_cik(self, ticker: str | None, given: Any = None) -> int | None:
        """A US listing's company: the CIK the SEC's list gives its ticker, the SEC being what issues a CIK; else the
        one Massive's record of it gives (`given`). Massive's copy is out of date for some listings (a ticker that
        passed to a new registrant, a tracking stock of a company since split off), and taken first it would make one
        company of two, or two of one."""
        return (self.cik_of(ticker) if ticker else None) or cik_number(given)

    def first_ticker(self, cik: int) -> str | None:
        """The company's first US ticker: the first the SEC's list gives its CIK that is a share of a company, the
        list's own order putting a company's main listing first, in Massive's form (the SEC's `BRK-B` is Massive's
        `BRK.B`); a ticker of a fund its sponsor lists under its CIK (an ETF, an ETN) is passed over."""
        found = self._read("sec_tickers", SEC_FILE)
        if not isinstance(found, dict):
            return None
        at = {f: i for i, f in enumerate(found.get("fields") or [])}
        if "ticker" not in at or "cik" not in at:
            return None
        for row in found.get("data") or []:
            if cik_number(row[at["cik"]]) != cik or not row[at["ticker"]]:
                continue
            ticker = str(row[at["ticker"]]).replace("-", ".")
            kind = self.reference(ticker).get("type")
            if kind is None or kind in COMPANY_TYPES:
                return ticker
        return None

    def company_name(self, cik: int) -> str | None:
        """The company's name as the SEC's list of tickers gives it."""
        found = self._read("sec_tickers", SEC_FILE)
        at = {f: i for i, f in enumerate((found or {}).get("fields") or [])} if isinstance(found, dict) else {}
        if "cik" not in at or "name" not in at:
            return None
        return next((row[at["name"]] for row in found.get("data") or [] if cik_number(row[at["cik"]]) == cik), None)

    def tickers_of(self, cik: int) -> list[str]:
        """Every US ticker the SEC gives the company with this CIK, an ordinary's over the counter among them."""
        self.cik_of("")  # the SEC's list, read
        return sorted(t for t, c in (self._sec or {}).items() if cik_number(c) == cik)

    def composites_of_isin(self, isin: str | None) -> list[str]:
        """Every listing's composite FIGI of the share class an ISIN names, on every exchange."""
        return self.composites_of({"isin": isin})

    def composites_of(self, row: dict) -> list[str]:
        """Every listing's composite FIGI of a row's share class (class_id), on every exchange."""
        named = class_id(row)
        found = self._read(*named) if named else None
        return list((found or {}).get("composites") or []) if isinstance(found, dict) else []

    def share_classes(self, ticker: str) -> list[str]:
        """The share classes of a US ticker's listing."""
        found = self._read("figi_ticker", ticker)
        return list((found or {}).get("share_classes") or []) if isinstance(found, dict) else []

    def composites_of_class(self, share_class: str) -> list[str]:
        """Every listing's composite FIGI of a share class, on every exchange."""
        found = self._read("figi_class", share_class)
        return list((found or {}).get("composites") or []) if isinstance(found, dict) else []

    def listing_symbol(self, home: str) -> str | None:
        """The symbol of a listing abroad `{ISIN}:{country}` on its home market, as its search by ISIN finds it this
        month (harness.venues.yahoo `listing_symbol`); None where none is found or it is not read."""
        found = self._read("listing_symbol", f"{home}@{self.asof[:7]}")
        return (found or {}).get("symbol") or None if isinstance(found, dict) else None

    def listing_class(self, symbol: str, mic: str | None) -> dict:
        """A listing abroad's share class, by its symbol on its exchange (harness.venues.openfigi `figi_listing`):
        `{share_classes, composites}`, none where OpenFIGI names none or it is not read."""
        found = self._read("figi_listing", f"{symbol}@{mic}") if mic else None
        return found if isinstance(found, dict) and found.get("status") == "OK" else {}

    def isin_of(self, figi: str) -> str | None:
        """The ISIN of the share class a listing's FIGI names, as a fund's holdings give it (harness.venues.massive
        `figi_isin`)."""
        found = self._read("figi_isin", figi)
        return (found or {}).get("isin") if isinstance(found, dict) else None

    def listing_then(self, cusip: str, day: str) -> list[dict]:
        """The listings that carried a security on a date, by its CUSIP."""
        found = self._read("cusip_listing", f"{cusip}@{day}")
        return [r for r in (found or {}).get("results") or [] if isinstance(r, dict)] if isinstance(found, dict) else []

    def reference_on(self, ticker: str, day: str) -> dict:
        """A ticker's reference on a date."""
        return results(self._read("ticker_on", f"{ticker}@{day}"))

    def taken(self, ticker: str) -> str | None:
        """The date the security now listed under a ticker took it, from its ticker events."""
        found = self._read("ticker_events", ticker)
        events = ((found or {}).get("results") or {}).get("events") or [] if isinstance(found, dict) else []
        dates = [e.get("date") for e in events if isinstance(e, dict)
                 and (e.get("ticker_change") or {}).get("ticker") == ticker and e.get("date")]
        return max(dates) if dates else None

    def company_listings(self, cik: int) -> list[dict]:
        found = self._read("company_listings", str(cik).zfill(10))
        return [r for r in (found or {}).get("results") or [] if isinstance(r, dict)] if isinstance(found, dict) else []

    def priced(self, ticker: str) -> bool:
        """Whether Massive prices the listing: it has a close for it on or before the as-of, however old. Its dollars
        come from its fund's weight, and its price matters only to a shock stated on that price, which converts to a
        return relative to the price shown, its latest mark with its time."""
        found = self._read("bars", ticker)
        days = [datetime.fromtimestamp(b["t"] / 1000, timezone.utc).date() for b in (found or {}).get("results") or []
                if isinstance(b, dict) and b.get("t") and b.get("c")] if isinstance(found, dict) else []
        return any(d <= date.fromisoformat(self.asof) for d in days)


def _conflicts(reference: dict, listing: dict, look: Lookup, ticker: str) -> bool:
    """Whether Massive's reference under a US ticker names another security than OpenFIGI's listing under it: FIGIs that
    differ from the listing's, or a CIK other than the one the SEC gives the ticker."""
    figis = {reference.get("composite_figi"), reference.get("share_class_figi")} - {None}
    if figis and not figis & {listing.get("compositeFIGI"), listing.get("shareClassFIGI")}:
        return True
    mine, sec = cik_number(reference.get("cik")), look.cik_of(ticker)
    return mine is not None and sec is not None and mine != sec


MAPPINGS = ("figi", "figi_cusip", "figi_ticker")  # OpenFIGI's mappings the resolution reads what they name through


def unresolved(kind: str, mapping: Any, look: Lookup, asked: Callable[[str], bool]) -> bool:
    """Whether a mapping kept from OpenFIGI no longer resolves to what it names, as the resolution read it: an
    identifier's mapping (`figi`, `figi_cusip`) naming a US listing that Massive's reference under its ticker no longer
    has, or holds as another security; a US ticker's (`figi_ticker`) naming no share class Massive's reference under
    the ticker names. A mapping is kept undated (harness.observations), so what it names is checked where it is used:
    a share class's listings change when one lists, delists or changes its ticker. `asked(ticker)` is whether the
    resolution read that reference, so only what it read is judged."""
    if not isinstance(mapping, dict) or mapping.get("status") != "OK":
        return False
    if kind == "figi_ticker":
        ticker = mapping.get("ticker")
        found = look.reference(ticker).get("share_class_figi") if ticker and asked(ticker) else None
        return bool(found) and found not in (mapping.get("share_classes") or [])
    for listing in mapping.get("listings") or []:
        ticker = str(listing.get("ticker") or "").replace("/", ".")
        if us_ticker(ticker) and asked(ticker):
            reference = look.reference(ticker)
            if not reference or _conflicts(reference, listing, look, ticker):
                return True
    return False


def home_listing(row: dict) -> str | None:
    """A row's listing on its home market, `{ISIN}:{country}`, where it is listed abroad: the country of the exchange
    its fund's holdings name, or, where they name none (a Form N-PORT report names no exchange), its ISIN's; None for a
    row listed in the US or with no ISIN."""
    isin = str(row.get("isin") or "").strip().upper()
    if not _ISIN.fullmatch(isin):
        return None
    country = str(row.get("country_of_exchange") or isin[:2]).strip().upper()
    return f"{isin}:{country}" if country and country != "US" else None


def own_ticker(row: dict) -> str | None:
    """The ticker a row's own security may be listed in the US under (step 1): the row's ticker where it can be a US
    listing's, but for a row its fund lists on an exchange abroad with an ISIN. There the ticker is the exchange's own
    ("ADANIENT"), which names no US listing or an unrelated one, and every US listing of its share class is found by
    its ISIN (step 2), so asking Massive under it confirms nothing step 2 does not (read live: 140 to 170 of a broad
    foreign fund's rows, each answered that Massive has no such ticker)."""
    ticker = row.get("constituent_ticker")
    if not us_ticker(ticker):
        return None
    country = str(row.get("country_of_exchange") or "").strip().upper()
    if country and country != "US" and _ISIN.fullmatch(str(row.get("isin") or "").strip().upper()):
        return None
    return ticker


def resolve(row: dict, look: Lookup) -> dict:
    """The listing a fund's row is priced through, and how its identifiers confirm it: `{"ticker", "kind" ("name" or
    "fund"), "market", "type", "how"}`, a US listing, a mutual fund's share class where its identifiers name no listing
    (`market` "funds": a fund of funds' holdings), or a holding listed abroad's home listing (`market` "home",
    `ticker` `{ISIN}:{country}`); or `{"ticker": None, "why", "candidates"}` where its identifiers confirm neither:
    the fund's other holdings, by name."""
    candidates: dict[str, dict] = {}

    def add(ticker: str, record: dict, how: str) -> None:
        kind = "name" if record.get("type") in COMPANY_TYPES else "fund" if record.get("type") in FUND_TYPES else None
        if kind and ticker not in candidates and record.get("market") in (EXCHANGE, OTC, FUNDS):
            candidates[ticker] = {"ticker": ticker, "kind": kind, "market": record["market"], "type": record["type"],
                                  "how": how, "cik": cik_number(record.get("cik"))}

    own_listing, figi = own_ticker(row), row.get("figi")
    own = look.reference(own_listing) if own_listing else {}
    if figi and own and figi in (own.get("composite_figi"), own.get("share_class_figi")):
        add(own_listing, own, SELF)
    for listing in look.us_listings(row):
        ticker = listing.get("ticker")
        reference = look.reference(ticker)
        if ticker and reference and not _conflicts(reference, listing, look, ticker):
            add(ticker, reference, CLASS)
    if not any(c["market"] == EXCHANGE for c in candidates.values()):
        ciks = sorted({look.company_cik(c["ticker"], c["cik"]) for c in list(candidates.values())} - {None})
        for cik in ciks:
            for listing in look.company_listings(cik):
                if listing.get("ticker") and listing.get("type") in COMPANY_TYPES and \
                        cik_number(listing.get("cik")) == cik:
                    add(listing["ticker"], listing, COMPANY)
    if not candidates and row.get("cusip") and row.get("effective_date"):
        for then in look.listing_then(row["cusip"], row["effective_date"]):
            ticker, cik = then.get("ticker"), cik_number(then.get("cik"))
            today = look.reference(ticker) if ticker else {}
            if not today or cik is None:
                continue
            if cik_number(today.get("cik")) == cik:
                add(ticker, today, THEN)
                continue
            took = look.taken(ticker)  # a change made after the as-of is no part of what the ticker named then
            if took and row["effective_date"] < took <= look.asof and \
                    cik_number(look.reference_on(ticker, took).get("cik")) == cik:
                add(ticker, today, THEN)
    exchange = [c for c in candidates.values() if c["market"] == EXCHANGE]
    home = home_listing(row)
    if home:  # a holding listed abroad: the security the fund holds, on its home market, by its exchange's own symbol
        # (`{ISIN}:{country}` where no listing of it is found); its US listings name its company
        kinds = sorted({c["kind"] for c in candidates.values()})
        ciks = sorted({look.company_cik(c["ticker"], c["cik"]) for c in candidates.values()} - {None})
        return {"ticker": look.listing_symbol(home) or home, "kind": kinds[0] if len(kinds) == 1 else "name",
                "market": "home", "type": next((c["type"] for c in candidates.values() if c["kind"] == "name"), None),
                "how": HOME, "isin": home.split(":")[0], **({"cik": ciks[0]} if len(ciks) == 1 else {})}
    pool = exchange or [c for c in candidates.values() if c["market"] == OTC and look.priced(c["ticker"])] or \
        [c for c in candidates.values() if c["market"] == FUNDS]  # a fund held in a fund as a mutual fund's class
    if not pool:
        why = ("Massive has no close for its US listings over the counter" if candidates else
               "no identifier names a US listing of it")
        return {"ticker": None, "why": why, "candidates": sorted(candidates)}
    if len(pool) > 1:
        pool = [c for c in pool if c["how"] != COMPANY] or pool  # the security itself, or its share class
    if len(pool) > 1:
        kinds = PREFERRED_TYPES if is_preferred(row) else tuple(t for t in COMPANY_TYPES if t not in PREFERRED_TYPES)
        pool = [c for c in pool if c["type"] in kinds + FUND_TYPES] or pool
    if len(pool) > 1:
        return {"ticker": None, "why": f"it is listed in the US as {', '.join(sorted(c['ticker'] for c in pool))}",
                "candidates": sorted(c["ticker"] for c in pool)}
    chosen = pool[0]
    cik = look.company_cik(chosen["ticker"], chosen.get("cik"))
    return {**{k: chosen[k] for k in ("ticker", "kind", "market", "type", "how")}, **({"cik": cik} if cik else {})}


def held_identifiers(ticker: str, reference: dict, look: Lookup) -> set[str]:
    """What names a company held directly through a US listing wherever its shares are listed: the listing's own
    FIGIs; and every listing's composite FIGI of each share class of it and of each US ticker the SEC gives its
    company, an ordinary's over the counter among them, so a fund's row of its ordinary shares abroad is known as it
    (MATH.md 3.1, the floor's exception)."""
    out = {reference.get("composite_figi"), reference.get("share_class_figi")} - {None}
    cik = look.company_cik(ticker, reference.get("cik"))
    classes = set(look.share_classes(ticker)) | ({reference["share_class_figi"]} if reference.get("share_class_figi")
                                                  else set())
    for sibling in look.tickers_of(cik) if cik else []:
        if sibling != ticker:
            classes |= set(look.share_classes(sibling))
    for share_class in sorted(classes):
        out |= set(look.composites_of_class(share_class))
    return out


def abroad(symbol: str, reference: dict, look: Lookup) -> dict:
    """A listing abroad held by its symbol, as its identifiers name it: `{"kind", "cik", "isin", "share_classes",
    "composites"}`. Its share class is OpenFIGI's for its ticker on its exchange (its reference's MIC); its ISIN, the
    one a fund's holdings give beside its FIGI, by which a fund's report names it; its company, the CIK its share
    class's US listings name, an ordinary's over the counter among them, where they name one; and every listing's
    composite FIGI of its share class, by which a fund's row of it on any market is known. What it is (`kind`) is its
    reference's: a share of a company, or a fund."""
    listing = look.listing_class(symbol, reference.get("primary_exchange"))
    classes = list(listing.get("share_classes") or [])
    composites = set(listing.get("composites") or [])
    isin = next((x for x in (look.isin_of(c) for c in sorted(composites)) if x), None)
    row = {"isin": isin} if isin else {"share_class": classes[0]} if classes else {}
    ciks = set()
    for found in look.us_listings(row) if row else []:
        ticker = found.get("ticker")
        reference_ = look.reference(ticker)
        if ticker and reference_ and not _conflicts(reference_, found, look, ticker):
            ciks.add(look.company_cik(ticker, reference_.get("cik")))
    for share_class in classes:
        composites |= set(look.composites_of_class(share_class))
    composites |= set(look.composites_of(row)) if row else set()
    kind = "name" if reference.get("type") in COMPANY_TYPES else "fund" if reference.get("type") in FUND_TYPES else None
    ciks.discard(None)
    return {"kind": kind, "cik": next(iter(ciks)) if len(ciks) == 1 else None, "isin": isin,
            "share_classes": classes, "composites": sorted(composites)}


RECEIPTS = ("ADRC", "ADRP", "NYRS", "GDR")  # a depositary receipt's types: a company abroad, its home line not named


def company_id(look: Lookup, cik: int | None, fallback: str) -> str:
    """A company's id: its first US ticker, where the SEC gives its CIK one; else `fallback`, its home listing or the
    one listing it has."""
    return (look.first_ticker(cik) if cik else None) or fallback


def store_lookup(store: Any, asof: str) -> Lookup:
    """identity's reads through the store's data seam, as the look-through makes them (harness.derive.portfolio._Reads):
    a price series over its window, everything else as of the as-of; what no client here reads is absent."""
    from harness.derive import series

    def read(kind: str, subject: str) -> Any:
        covering = (series.lookback(asof), asof) if kind == "bars" else (asof, asof)
        row = store.data(kind, subject, covering=covering, required=False)
        return store.data_payload(row["id"]) if row else None
    return Lookup(read, asof)


def name_of(store: Any, ticker: str, asof: str) -> dict:
    """The name of what a ticker names, as a company, a fund or another holding (MATH.md 3.1): `{"name": "name:{id}",
    "security": its pricing security where the member holds none of it, "company": {"cik", "title"} or None}`. A
    company's id is its first US ticker, else its home listing; its pricing security, where the member holds it
    nowhere, its home listing: a listing abroad named by `{ISIN}:{country}` is its own, and a company in the US is
    priced through its id's listing. A depositary receipt names no ordinary, so a company named by its receipt is
    priced through the receipt. A fund, a currency or crypto pair, and cash are their own names. LookupError where no
    source lists the ticker; a read that fails past its retries raises where it is read."""
    from harness.derive import series

    t = str(ticker).strip()
    if series.is_pair(t):
        return {"name": f"name:{t}", "security": t, "company": None}
    look = store_lookup(store, asof)
    if series.is_abroad(t) and not series.symbol_abroad(t):  # `{ISIN}:{country}`: its listing's symbol is its id
        isin, country = t.split(":", 1)
        found = resolve({"isin": isin, "country_of_exchange": country, "asset_class": "Equity"}, look)
        if found.get("kind") == "fund":
            return {"name": f"name:{found['ticker']}", "security": t, "company": None}
        cik = found.get("cik")
        title = look.company_name(cik) if cik else None
        return {"name": f"name:{company_id(look, cik, found['ticker'])}", "security": t,
                "company": {"cik": cik, "title": title} if cik or title else None}
    ref = look.reference(t)
    if not ref:
        raise LookupError(f"no source lists {t}")
    if ref.get("market") == "home":  # a listing abroad by its symbol: its own id, or its company's first US ticker
        found = abroad(t, ref, look)
        if found["kind"] != "name":
            return {"name": f"name:{t}", "security": t, "company": None}
        cik = found["cik"]
        return {"name": f"name:{company_id(look, cik, t)}", "security": t,
                "company": {"cik": cik, "title": (look.company_name(cik) if cik else None) or ref.get("name")}}
    if ref.get("type") in FUND_TYPES or ref.get("type") not in COMPANY_TYPES:
        return {"name": f"name:{t}", "security": t, "company": None}
    cik = look.company_cik(t, ref.get("cik"))
    named = company_id(look, cik, t)
    first = look.reference(named) if named != t else ref
    security = t if ref.get("type") in RECEIPTS else named  # a company in the US is priced through its id's listing
    return {"name": f"name:{named}", "security": security,
            "company": {"cik": cik, "title": (look.company_name(cik) if cik else None) or first.get("name")
                        or ref.get("name")}}
