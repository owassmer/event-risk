"""Where the portfolio's weight is (MATH.md 3.1; ONTOLOGY.md 2.1; TOOLS.md 5's `add`; the page's portfolio panel): its
weight by company after look-through, by industry, and by country, each summing to the portfolio's value, every row of
every fund counted.

The look-through accounts for every dollar of the portfolio by what it is (`owned`, harness.derive.portfolio): each
company a name stands for, each share no name stands for (a fund's row below the floor), and each kind of asset that is
not a company, a derivative's own value beyond what it carries in companies among them. These lists read that account.

**A company's industry** is its place in FinanceDatabase's one classification for every market, its sector and its
industry (harness.venues.equities), from the file's row of the company. The row is first that of one of the company's
own securities, those its identifiers confirmed it by (harness.derive.identity), by its symbol; then one of the rows of
its ISIN, or of the one its US CUSIP makes, the listing on the market its fund names or on its ISIN's own first (pick);
then the row of a listing its own ticker builds where it trades and on the exchanges of its ISIN's country
(`symbols_for`), since a symbol built from a ticker and a country is a guess at a listing and can name another company:
AngloGold Ashanti's row, its JSE ticker ANG under a US exchange and a British ISIN, builds London's ANG, Angling
Direct's. A listing the file holds as delisted names no company by its symbol, since its symbol can since have passed
to another; an ISIN's delisted rows are still its company's. **Its country** is its home: the country it is
registered in, its ISIN's, where it also trades there, by a listing of it there or the exchange its fund names there;
and otherwise the one its own record gives, the file's country, or, for a company the file holds by none of these and
that is at least the look-through's floor of the portfolio, the country of its profile at Yahoo Finance, the first that answers of the listing a search by its ISIN finds and of
each its own ticker builds. A depositary receipt's ISIN is the receipt's
own, issued where the receipt trades, and says nothing of where its company is registered, so a company met by its
receipt (the look-through marks the row) has the home its own record gives. A company whose classification the file
does not state stands under no industry, and one whose home no source states under no country, each with its name, so
the lists still sum.
**What is not a company** stands under its kind of asset, in each list: cash, debt, a derivative, a commodity, a
currency, or other.

**Each industry and each country has an id,** `industry:{key}` (industry_id) and `country:{code}` (country_id), on its
group and on each company in it: the one name the analyst's `get`, a chip in its words, and the page's views open it by.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable, Iterable

import pycountry

from harness.derive import series
from harness.derive.assets import isin_of
from harness.derive.engine import Derivation, Inputs
from harness.derive.portfolio import FLOOR, asof_of
from harness.venues.yahoo import pick, region_of, symbols_for

# Names of countries that ISO 3166 names otherwise: Yahoo's (read on profiles of funds drawn by rule) and FinanceDatabase's
# (every name its file gives).
ALIAS = {"Turkey": "TR", "Macau": "MO", "Curacao": "CW", "Ivory Coast": "CI", "Russia": "RU", "Korea": "KR",
         "Falkland Islands": "FK", "Macedonia": "MK", "Sint Maarten": "SX"}


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


def industry_id(industry: str | None) -> str | None:
    """An industry's id, `industry:{key}`: its name lowercased, each run of other characters one hyphen (`Semiconductors
    & Chip Equipment` is `industry:semiconductors-chip-equipment`). An id the analyst writes in a sentence, and the
    page opens."""
    return f"industry:{re.sub(r'[^a-z0-9]+', '-', industry.lower()).strip('-')}" if industry else None


def country_id(code: str | None) -> str | None:
    """A country's id, `country:{ISO 3166 alpha-2}`."""
    return f"country:{code}" if code else None


def country_name(code: str | None) -> str | None:
    """A country's common name from its ISO 3166 code."""
    found = pycountry.countries.get(alpha_2=code) if code else None
    return (getattr(found, "common_name", None) or found.name) if found else None


def companies(owned: dict[str, dict], kinds: dict[str, dict]) -> dict[str, dict]:
    """The account's companies: each a name stands for, and each share no name stands for."""
    return {key: entry for key, entry in owned.items()
            if key.startswith("row:") or (key.startswith("name:") and (kinds.get(key) or {}).get("kind") == "name")}


def own_symbols(key: str, securities: dict[str, dict]) -> list[str]:
    """The symbols of a company's own securities, each a listing its identifiers confirmed it by: a listing abroad by
    its own symbol, a US listing by its ticker as Yahoo writes it."""
    out: list[str] = []
    for sec in securities.get(key) or {}:
        if series.symbol_abroad(sec):
            out.append(sec)
        elif ":" not in sec:
            out += symbols_for(sec, "US")[:1]
    return list(dict.fromkeys(out))


def built_symbols(entry: dict) -> list[str]:
    """The symbols the row a company was met on builds from its own ticker where it trades, and on its ISIN's country's
    exchanges (symbols_for)."""
    row = entry.get("row") or {}
    ticker, country = traded(row)
    return list(dict.fromkeys(symbols_for(ticker, country, row.get("isin")))) if row else []


def symbols_of(rows: Iterable[dict]) -> list[str]:
    """The symbols a fund's rows' companies are first looked for under: those each row's own ticker builds where it
    trades (built_symbols)."""
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


def _held(rows: list[dict], country: str | None, isin: str) -> dict | None:
    """The row of an ISIN's that stands for its company: a listing on the market its fund names, else on its ISIN's
    own, the primary market first (pick), else its first listed row, else its first."""
    listed = [r for r in rows if not r["delisted"]]
    quotes = [{"symbol": r["symbol"], "exchange": r["exchange"], "quoteType": "EQUITY"} for r in listed]
    chosen = (pick(quotes, country) if country else None) or pick(quotes, isin[:2])
    return next((r for r in listed if r["symbol"] == chosen), None) or (listed or rows or [None])[0]


def classify(entries: dict[str, dict], securities: dict, equity: Callable[[str], list[dict] | None],
             found: Callable[[str], str | None], profile: Callable[[str], dict | None],
             figi: Callable[[str], list[str] | None], floor: float = 0.0) -> dict[str, dict]:
    """Each company's row in FinanceDatabase's file, industry and home, each read through the callables (the file's
    rows of `symbol:{SYMBOL}` or `isin:{ISIN}`; a listing's symbol by its ISIN; a symbol's profile; the US listings
    OpenFIGI maps an ISIN to), which give None for what is not read yet. Returns, for each company, `{symbol, how,
    sector, industry, home, name, wants}`: `wants`, what must be read before it is classified, the reads `inputs`
    makes."""
    out: dict[str, dict] = {}
    for key, entry in entries.items():
        wants: list[tuple[str, str]] = []
        row = entry.get("row") or {}
        isin = isin_of(row) or ""
        named_there = str(traded(row)[1] or "").upper()
        own, built = own_symbols(key, securities), built_symbols(entry)
        asked = [f"symbol:{s}" for s in own] + ([f"isin:{isin}"] if isin else []) + [f"symbol:{s}" for s in built]
        read = {a: equity(a) for a in asked}
        wants += [("equity", a) for a, rows in read.items() if rows is None]
        record, places = None, set()
        if not wants:
            listed = lambda a: next((r for r in read[a] if not r["delisted"]), None)  # noqa: E731
            if isin:
                places = {region_of(r["symbol"]).upper() for r in read[f"isin:{isin}"] if not r["delisted"]}
            record = next((listed(f"symbol:{s}") for s in own if listed(f"symbol:{s}")), None) or \
                (_held(read[f"isin:{isin}"], named_there, isin) if isin and read[f"isin:{isin}"] else None) or \
                next((listed(f"symbol:{s}") for s in built if listed(f"symbol:{s}")), None)
        symbol = record["symbol"] if record else None
        trades = {region_of(symbol).upper()} if symbol else set()
        # its ISIN's country where it trades there, by a listing of it there or the exchange its fund names, which a
        # depositary receipt's ISIN, issued where the receipt trades, does not say: a receipt's company has its home
        # from its own record
        home = isin[:2] if isin and isin[:2] in (trades | places | {named_there}) and not row.get("receipt") else None
        named = country_code(record.get("country")) if record else None
        p: dict = {}
        # the file holds it by none of them: its profile's country, for a company at or above the floor
        if not wants and record is None and home is None and abs(entry.get("dollars") or 0.0) >= floor:
            identified = _identified(entry)
            searched = found(identified) if identified else ""
            if searched is None:
                wants.append(("symbol", identified))
            for t in dict.fromkeys(([searched] if searched else []) + own + built) if not wants else ():
                got = profile(t)  # the listing its ISIN's search finds, then its own and those its ticker builds
                if got is None:
                    wants.append(("profile", t))
                    break
                if got.get("country"):
                    symbol, p = t, got
                    break
        out[key] = {"symbol": symbol, "how": "file" if record else ("profile" if p else None),
                    "sector": record.get("sector") if record else None,
                    "industry": record.get("industry") if record else None,
                    "home": home or named or country_code(p.get("country")),
                    "home_named": (record or p).get("country") if not home else None,
                    "name": (record or p).get("name") or row.get("constituent_name"),
                    "wants": wants}
    return out


def _readers(asof: str, equities: dict, symbols: dict, profiles: dict, figis: dict,
             load) -> tuple[Callable, Callable, Callable, Callable]:
    def equity(subject: str) -> list[dict] | None:
        ref = equities.get(subject)
        if ref is None:
            return None
        return ((load(ref) if ref.get("data") else None) or {}).get("rows") or []

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
    return equity, found, profile, figi


def _inputs(ctx: Inputs, subject: str) -> dict:
    """The look-through's account, and every read that classifies its companies: the file's rows of each company's
    symbols and ISIN, and, for a company the file holds by none of them and whose home they leave open, its listing's
    symbol by its ISIN, OpenFIGI's mapping of its ISIN and the profiles its home needs; read round by round, each
    round's reads side by side."""
    asof = asof_of(ctx)
    month = asof[:7]
    looked = ctx.value("lookthrough", "portfolio")["output"]
    entries = companies(looked.get("owned") or {}, looked["kinds"])
    floor = FLOOR * ((ctx.value("holdings", "portfolio")["output"] or {}).get("V") or 0.0)
    equities: dict[str, dict] = {}
    symbols: dict[str, dict] = {}
    profiles: dict[str, dict] = {}
    figis: dict[str, dict] = {}
    # each read's kind, where it goes, and its subject: a month's, or for the file and OpenFIGI's mapping, its own
    kinds = {"equity": ("equity", equities, ""), "symbol": ("listing_symbol", symbols, f"@{month}"),
             "profile": ("profile", profiles, f"@{month}"), "figi": ("figi", figis, "")}
    for _ in range(8):  # the file's rows, then the searches, mappings and profiles each needs, a round at a time
        equity, found, profile, figi = _readers(asof, equities, symbols, profiles, figis, ctx.payload)
        wanted: dict[str, set[str]] = defaultdict(set)
        for got in classify(entries, looked.get("securities") or {}, equity, found, profile, figi, floor).values():
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
            "equities": equities, "symbols": symbols, "profiles": profiles, "figis": figis}


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
    equity, found, profile, figi = _readers(m["asof"], m.get("equities") or {}, m["symbols"], m["profiles"],
                                            m.get("figis") or {}, load)
    got = classify(entries, looked.get("securities") or {}, equity, found, profile, figi, FLOOR * value)
    weight = (lambda d: d / value) if value else (lambda d: None)
    others = {k: d for k, d in _kinds_of(owned, looked.get("funds") or {}).items() if abs(d) > 0.005}
    kinds = [{"kind": k, "dollars": d, "weight": weight(d)} for k, d in sorted(others.items(), key=lambda kd: -abs(kd[1]))]
    by_company = sorted(({"name": key if key.startswith("name:") else None, "company": got[key]["name"],
                          "dollars": entry["dollars"], "weight": weight(entry["dollars"]),
                          "industry": got[key]["industry"], "industry_id": industry_id(got[key]["industry"]),
                          "country": got[key]["home"], "country_id": country_id(got[key]["home"])}
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
    by_industry = grouped(lambda c: c["industry"],
                          lambda i: {"id": industry_id(i), "industry": i, "sector": sector_of.get(i)})
    by_country = grouped(lambda c: c["country"],
                         lambda code: {"id": country_id(code), "country": code, "country_name": country_name(code)})
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


WEIGHTS = Derivation("weights", 10, "Portfolio", _inputs, _compute,
                     fields=("by_company", "by_industry", "by_country"),
                     subject_of=lambda store, entity: "portfolio", stage="industries")

DERIVATIONS = (WEIGHTS,)
