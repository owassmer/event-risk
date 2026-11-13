"""Where the portfolio's weight is (MATH.md 3.1; ONTOLOGY.md 2.1; TOOLS.md 5's `add`; the page's portfolio panel): its
weight by company after look-through, by industry, and by country, each summing to the portfolio's value, every row of
every fund counted.

The look-through accounts for every dollar of the portfolio by what it is (`owned`, harness.derive.portfolio): each
company a name stands for, each share no name stands for (a fund's row below the floor), and each kind of asset that is
not a company, a derivative's own value beyond what it carries in companies among them. These lists read that account.

**A company's industry** is its place in Yahoo's one classification for every market, its sector and its industry
(harness.venues.yahoo): its listing's, as Yahoo's screener gives it for the listing's symbol, the listing found by its
own ticker where it trades (`symbols_for`), else by searching its ISIN; and where the screener gives it none, its
profile's. **Its country**
is its home: the country it is registered in, its ISIN's, where it also trades there, its listing in that country; and
otherwise the one its own record gives, its profile's country. A depositary receipt's ISIN is the receipt's own, issued
where the receipt trades, and says nothing of where its company is registered, so a company met by its receipt (the
look-through marks the row) has the home its own record gives. A row whose own ticker matches no listing where it
trades (an exchange's code after it, a when-issued line, a ticker since changed, a fund naming the wrong country) is
found by searching its ISIN, or the one its US CUSIP makes, wherever it trades. A company whose classification no source states stands
under no industry, and one whose home no source states under no country, each with its name, so the lists still sum.
**What is not a company** stands under its kind of asset, in each list: cash, debt, a derivative, a commodity, a
currency, or other.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable

import pycountry

from harness.derive import series
from harness.derive.assets import isin_of
from harness.derive.engine import Derivation, Inputs
from harness.derive.portfolio import asof_of
from harness.venues.yahoo import region_of, symbols_for

# Yahoo's names of countries that ISO 3166 names otherwise (read on profiles of funds drawn by rule).
ALIAS = {"Turkey": "TR", "Macau": "MO", "Curacao": "CW", "Ivory Coast": "CI", "Russia": "RU", "Korea": "KR"}


def country_code(name: str | None) -> str | None:
    """A country's ISO 3166 code from its name as a profile gives it; None for a name no table here holds."""
    if not name:
        return None
    if name in ALIAS:
        return ALIAS[name]
    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        return None


def country_name(code: str | None) -> str | None:
    """A country's common name from its ISO 3166 code."""
    found = pycountry.countries.get(alpha_2=code) if code else None
    return (getattr(found, "common_name", None) or found.name) if found else None


def companies(owned: dict[str, dict], kinds: dict[str, dict]) -> dict[str, dict]:
    """The account's companies: each a name stands for, and each share no name stands for."""
    return {key: entry for key, entry in owned.items()
            if key.startswith("row:") or (key.startswith("name:") and (kinds.get(key) or {}).get("kind") == "name")}


def candidates(key: str, entry: dict, securities: dict[str, dict]) -> list[str]:
    """The symbols a company's listing can have: from the row it was met on, its own ticker where it trades; and for a
    company held directly, its US listing."""
    row = entry.get("row") or {}
    ticker, country = traded(row)
    out = symbols_for(ticker, country, row.get("isin")) if row else []
    for sec in securities.get(key) or {}:
        if series.symbol_abroad(sec):  # a listing abroad by its own symbol, which the screener holds it under
            out.append(sec)
        elif ":" not in sec:
            out += symbols_for(sec, "US")
    return list(dict.fromkeys(out))


def symbols_of(rows: Iterable[dict]) -> list[str]:
    """The symbols a fund's rows' companies are first looked for under: those each row's own ticker builds where it
    trades (candidates)."""
    return sorted({s for r in rows for s in symbols_for(*traded(r), r.get("isin"))} - {None, ""})


def traded(row: dict) -> tuple[str | None, str | None]:
    """A row's ticker where it trades and its exchange's country: as its fund's holdings give them, else, for a share a
    fund's report names by its ISIN alone, as the funds' holdings say it trades (harness.derive.portfolio._listed)."""
    listing = row.get("listing") or {}
    if row.get("constituent_ticker") or row.get("country_of_exchange"):
        return row.get("constituent_ticker"), row.get("country_of_exchange")
    return listing.get("ticker"), listing.get("country")


def _identified(entry: dict) -> str | None:
    """A row's listing by its identifiers, `{ISIN}:{country}`, which a search by ISIN finds wherever it trades: its ISIN,
    or the one its US CUSIP makes, and the country its fund names, else its ISIN's."""
    row = entry.get("row") or {}
    isin = isin_of(row)
    country = str(traded(row)[1] or (isin or "")[:2]).upper()
    return f"{isin}:{country}" if isin and country else None


def classify(entries: dict[str, dict], securities: dict, listing: Callable[[str], dict | None],
             found: Callable[[str], str | None], profile: Callable[[str], dict | None],
             figi: Callable[[str], list[str] | None]) -> dict[str, dict]:
    """Each company's listing, industry and home, by what its sources state, each read through the callables (a
    listing's sector and industry by its symbol, empty where the screener holds none with a sector; a listing's symbol
    by its ISIN; a symbol's profile; the US listings OpenFIGI maps an ISIN to), which give None for what is not read
    yet. A company's listing is the one its own ticker names that the screener holds, else the one its identifiers
    find; where the screener holds neither, the first of these whose profile answers: the one its ISIN's
    search found, each its own ticker builds where it trades, and each US line its ISIN maps to. Returns, for each
    company, `{symbol, how, sector, industry, home, name, wants}`: `wants`, what must be read before it is
    classified, the reads `inputs` makes."""
    out: dict[str, dict] = {}
    for key, entry in entries.items():
        wants: list[tuple[str, str]] = []
        row = entry.get("row") or {}
        symbols = candidates(key, entry, securities)
        listed = None
        for s in symbols:
            record = listing(s)
            if record is None:
                wants.append(("listing", s))
            elif record:
                listed = (s, record)
                break
        symbol, how = (listed[0], "listing") if listed else (None, None)
        isin = isin_of(row) or ""
        p = None
        if listed is None and not wants:  # its own ticker matches no listing where it trades: its identifiers
            identified = _identified(entry)
            searched = found(identified) if identified else ""
            if searched is None:
                wants.append(("symbol", identified))
            elif searched and listing(searched) is None:
                wants.append(("listing", searched))
            elif searched and listing(searched):
                listed, symbol, how = (searched, listing(searched)), searched, "search"
            else:  # the screener holds it under none of them: the first listing of it whose profile answers
                lines = figi(isin) if isin else []
                if lines is None:
                    wants.append(("figi", isin))
                tries = list(dict.fromkeys(([searched] if searched else []) + symbols + (lines or [])))
                for t in tries:
                    found_ = profile(t)
                    if found_ is None:
                        wants.append(("profile", t))
                        break
                    if found_.get("industry") or found_.get("country"):
                        symbol, p = t, found_
                        break
        record = listed[1] if listed else {}
        trades = region_of(symbol).upper() if listed else \
            str(traded(row)[1] or "").upper() or (region_of(symbol).upper() if symbol else "")
        # its ISIN's country where it trades there, which a depositary receipt's ISIN, issued where the receipt trades,
        # does not say: a receipt's company has its home from its own record
        home = isin[:2] if isin and isin[:2] == trades and not row.get("receipt") else None
        if p is None and symbol and listed and (home is None or not record.get("industry")):
            p = profile(symbol)
            if p is None:
                wants.append(("profile", symbol))
        p = p or {}
        sector, industry = (record.get("sector"), record.get("industry")) if record.get("industry") else \
            (p.get("sector"), p.get("industry"))
        out[key] = {"symbol": symbol, "how": how if record.get("industry") else ("profile" if industry else None),
                    "sector": sector, "industry": industry, "home": home or country_code(p.get("country")),
                    "home_named": p.get("country") if not home else None,
                    "name": record.get("name") or p.get("name") or row.get("constituent_name"),
                    "wants": wants}
    return out


def _readers(asof: str, listings: dict, symbols: dict, profiles: dict, figis: dict,
             load) -> tuple[Callable, Callable, Callable, Callable]:
    def listing(symbol: str) -> dict | None:
        ref = listings.get(symbol)
        if ref is None:
            return None
        payload = (load(ref) if ref.get("data") else None) or {}
        return payload if payload.get("status") == "OK" and payload.get("sector") else {}

    def found(listing: str) -> str | None:
        ref = symbols.get(listing)
        if ref is None:
            return None
        payload = load(ref) if ref.get("data") else None
        return (payload or {}).get("symbol") or ""

    def profile(symbol: str) -> dict | None:
        ref = profiles.get(symbol)
        if ref is None:
            return None
        return (load(ref) if ref.get("data") else None) or {}

    def figi(isin: str) -> list[str] | None:
        """The US listings OpenFIGI maps an ISIN to, each by its ticker as Yahoo writes it."""
        ref = figis.get(isin)
        if ref is None:
            return None
        payload = (load(ref) if ref.get("data") else None) or {}
        return [str(x["ticker"]).replace("/", "-").replace(".", "-") for x in payload.get("listings") or []
                if isinstance(x, dict) and x.get("ticker") and x.get("exchCode") in (None, "US")]
    return listing, found, profile, figi


def _inputs(ctx: Inputs, subject: str) -> dict:
    """The look-through's account, and every read that classifies its companies: each listing's sector by the symbols
    their rows trade under, each listing abroad's symbol the screener does not hold, and each profile a company's
    industry or home needs; read stage by stage, each stage's reads side by side."""
    asof = asof_of(ctx)
    month = asof[:7]
    looked = ctx.value("lookthrough", "portfolio")["output"]
    entries = companies(looked.get("owned") or {}, looked["kinds"])
    listings: dict[str, dict] = {}
    symbols: dict[str, dict] = {}
    profiles: dict[str, dict] = {}
    figis: dict[str, dict] = {}
    # each read's kind, where it goes, and its subject: a month's, or for OpenFIGI's mapping the identity reads, its own
    kinds = {"listing": ("listing_sector", listings, f"@{month}"), "symbol": ("listing_symbol", symbols, f"@{month}"),
             "profile": ("profile", profiles, f"@{month}"), "figi": ("figi", figis, "")}
    for _ in range(8):  # the listings, the searches, then the profiles and mappings each needs, a round at a time
        listing, found, profile, figi = _readers(asof, listings, symbols, profiles, figis, ctx.payload)
        wanted: dict[str, set[str]] = defaultdict(set)
        for got in classify(entries, looked.get("securities") or {}, listing, found, profile, figi).values():
            for kind, what in got["wants"]:
                wanted[kind].add(what)
        if not wanted:
            break
        for kind, subjects in wanted.items():  # a round's reads of every kind started side by side, none waiting on
            data_kind, _, at = kinds[kind]  # another's, then each kind's read in turn
            ctx.prefetch(data_kind, [f"{s}{at}" for s in sorted(subjects)], (asof, asof), wait=False)
        for kind, subjects in wanted.items():
            data_kind, into, at = kinds[kind]
            named = sorted(subjects)
            ctx.prefetch(data_kind, [f"{s}{at}" for s in named], (asof, asof), stage="industries")
            refs = ctx.data_many(data_kind, [f"{s}{at}" for s in named], covering=(asof, asof), required=False)
            into.update({s: refs[f"{s}{at}"] for s in named})
    return {"asof": asof, "lookthrough": ctx.value("lookthrough", "portfolio", ref=True),
            "holdings": ctx.value("holdings", "portfolio", ref=True),
            "listings": listings, "symbols": symbols, "profiles": profiles, "figis": figis}


def _kinds_of(owned: dict[str, dict], funds: dict) -> dict[str, float]:
    """The dollars of what is not a company, by kind: each kind the account names, and each fund booked whole by how
    its holdings of a positive weight divide by kind; where none are read, the kind its own class gives it (a commodity
    trust's or pool's, a commodity), and else other (a fund whose rows state only the exposures it carries, an inverse
    fund's swaps, says nothing of what its value is held in)."""
    out: dict[str, float] = defaultdict(float)
    for key, entry in owned.items():
        if key.startswith("kind:"):
            out[entry.get("kind") or key[5:]] += entry["dollars"]
        elif key.startswith("whole:"):  # by the kinds of what carries its value, its holdings of a positive weight
            division = [d for d in ((funds.get(key[6:]) or {}).get("holds") or {}).get("by_kind") or []
                        if d["weight"] > 0]
            total = sum(d["weight"] for d in division)
            if total:
                for d in division:
                    out[d["kind"]] += entry["dollars"] * d["weight"] / total
            else:
                out[entry.get("kind") or "other"] += entry["dollars"]
    return out


def _compute(m: dict, load) -> dict:
    looked = load(m["lookthrough"])
    value = (load(m["holdings"]) or {}).get("V") or 0.0
    owned = looked.get("owned") or {}
    entries = companies(owned, looked["kinds"])
    listing, found, profile, figi = _readers(m["asof"], m["listings"], m["symbols"], m["profiles"], m.get("figis") or {},
                                            load)
    got = classify(entries, looked.get("securities") or {}, listing, found, profile, figi)
    weight = (lambda d: d / value) if value else (lambda d: None)
    others = {k: d for k, d in _kinds_of(owned, looked.get("funds") or {}).items() if abs(d) > 0.005}
    kinds = [{"kind": k, "dollars": d, "weight": weight(d)} for k, d in sorted(others.items(), key=lambda kd: -abs(kd[1]))]
    by_company = sorted(({"name": key if key.startswith("name:") else None, "company": got[key]["name"],
                          "dollars": entry["dollars"], "weight": weight(entry["dollars"]),
                          "industry": got[key]["industry"], "country": got[key]["home"]}
                         for key, entry in entries.items()), key=lambda c: -abs(c["dollars"]))

    def grouped(field: Callable[[dict], Any], about: Callable[[Any], dict]) -> list[dict]:
        groups: dict[Any, dict] = {}
        for c in by_company:
            g = groups.setdefault(field(c), {**about(field(c)), "dollars": 0.0, "companies": 0, "names": []})
            g["dollars"] += c["dollars"]
            g["companies"] += 1
            if c["name"]:
                g["names"].append(c["name"])
        out = sorted(groups.values(), key=lambda g: -abs(g["dollars"]))
        return [{**g, "weight": weight(g["dollars"])} for g in out]

    sectors = {(got[k]["sector"], got[k]["industry"]) for k in got}
    sector_of = {i: s for s, i in sectors}
    by_industry = grouped(lambda c: c["industry"], lambda i: {"industry": i, "sector": sector_of.get(i)})
    by_country = grouped(lambda c: c["country"], lambda code: {"country": code, "country_name": country_name(code)})
    unclassified = {"industry": sum(1 for c in by_company if not c["industry"]),
                    "country": sum(1 for c in by_company if not c["country"])}
    return {"by_company": by_company + kinds, "by_industry": by_industry + kinds, "by_country": by_country + kinds,
            "value": value, "unclassified": unclassified,
            "how": dict(sorted(_counted(g["how"] for g in got.values()).items()))}


def _counted(values) -> dict:
    out: dict = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return out


WEIGHTS = Derivation("weights", 6, "Portfolio", _inputs, _compute,
                     fields=("by_company", "by_industry", "by_country"),
                     subject_of=lambda store, entity: "portfolio", stage="industries")

DERIVATIONS = (WEIGHTS,)
