"""A fund's rows as exposure: for each row of a fund's holdings, what it is exposed to and by how much, per dollar of
the net assets of the report it comes from (MATH.md 3.1). A fund's rows are one current set: what it holds, from ETF
Global's holdings (Massive) as of its latest processing, and what its derivatives are exposed to, from its latest
public Form N-PORT report, the one source that states every derivative's notional, by one rule for every issuer
(ETF Global weighs a swap at its market value for one issuer and at its notional for another, and lists no forward
of a currency-hedged fund's). A fund ETF Global gives no holdings for, a closed-end fund among them, is its report's
rows whole. Each row carries, beside its fields as its source gives them:

- `report_date` and `net_assets`: the date its source states it as of, and the net assets its exposure is per dollar
  of: ETF Global's processing date and the net assets its rows imply, each row's market value over its weight; or the
  report's period and its net assets.
- `exposure`: signed, per dollar of those net assets. A holding, cash, and a holding of another currency, at its own
  weight; a swap or a future at its notional, in dollars, signed as the report signs it; each side of a forward at its
  amount in dollars, the currency bought positive and the one sold negative. A listed option the current holdings name
  by its symbol, or by Bloomberg's description of the contract (option_symbol), at the delta its market quotes on the
  as-of times its signed contracts, the shares each controls and the underlying's price (`quote` beside it: the
  snapshot's status and delta, and what a delta is worked out from where it quotes none: the contract's price, the
  underlying's, and when they stood); an option no market lists (a FLEX contract), one its market quotes no delta,
  one named only in prose, and a report's, none. Where the report gives no way to put a notional in
  dollars, none.
- `payoff`: `"holding"`, `"linear"`, `"cash"`, or `{"option": {right, side, underlying_per_contract, contracts, strike,
  expiry, style, value, symbol}}`, a warrant an option purchased.
- `to`: what it is exposed to, where its identifiers say: `{"company": <its identifiers, as resolution reads them>}`,
  for a holding of a share or a derivative its report codes as on equity (Form N-PORT's DE, or no code), with
  `named_by: "ticker"` for a reference a report names by its US ticker alone (ticker_reference);
  `{"index": {name, identifier, figi, values}, "companies": [{<identifiers>, weight}], "basket": {fund, holdings_asof,
  tracking, sessions}, "followers": [<funds whose returns follow it>]}` where the index's companies are established,
  `figi` where OpenFIGI names it, `values` where Massive gives its values, and neither `companies` nor `basket` where
  they are not; `{"currency": <code>}`; `{"pair": "X:<coin>USD"}` for a coin the fund holds, whose currency its source
  gives as the coin's code and which Coinbase Exchange lists against the dollar (pairs); absent where only its kind is
  known.
- `kind`: the asset kind (harness.derive.assets).

A fund measured on its one reference (slope_reference, fit_slope, measure: MATH.md 3.1) has one row more, the reference
at the slope of the fund's daily returns on the reference's own over the SESSIONS sessions to the as-of, with how it
was measured (`measured`: slope, standard error, r2, sessions, from, to, fit) and no weight, so it is no holding; its
swaps and futures on the reference and its own shares of it carry no exposure, since the slope holds what each does,
and each is marked `measured_by`, the measured row's name, so that what reads them knows the slope holds them; each
share keeps its weight, for what the fund holds. The payload carries `measured` too. Set beside what issuers
state each day, on funds drawn by rule, the slope stood within 2% of it, where the report's ratio stood as far as 40%:
a report states its sizes on their trade dates, months before.

The payload carries the date and net assets of what the fund holds, `report_date` and `net_assets`, and, where its
derivatives come from a report of its own, that report's, `derivatives_date` and `derivatives_net_assets`.

A fund's value, the cash and the collateral it reports holding, and its signed exposures are separate quantities, and
its rows' exposures need not sum to its value: a fund long three times its index through swaps holds its value and is
exposed to three times it. No row is added that the fund's sources do not state; a swap's or a future's financing is
no holding of the fund's.

A swap's or a future's notional is in dollars where its row is in dollars. Where the row is in another currency, the
report does not say in which currency the notional is stated (Item C.11.d.iv asks for "aggregate notional amount or
contract value on trade date", and the row's currency, Item C.2.b, is the investment's), and filers state it both
ways: read live in the reports of the 500 most traded US funds, iShares' and Vanguard's foreign index futures carry
notionals in dollars under a row in yen or won (EWY's KOSPI 200 future, read in won, would have lost 113 times its
notional in a quarter, and VPL's 262 times), while JPMorgan's BBJP carries its TOPIX future's in yen (read in dollars,
82% of a fund that holds its index's shares). Nor is the row's rate quoted one way (Vanguard's VWO gives the Hong Kong
dollar at 0.127512, dollars per unit; iShares' EWJ the yen at 159.75, units per dollar). So such a notional has no
dollar value here, and its row keeps its reference and its kind with no exposure.

A swap's direction is its notional's sign. Its legs are kept as the report gives them, and the report gives them in
the form's fields loosely: a fund long a commodity index can file the index's return as a payment it makes (read live,
Invesco's PDBC, whose notionals are positive), while the notional's sign was the direction in every report read
(ProShares', Direxion's, Invesco's). A future is signed by its payoff profile.

What a derivative references (Item C.11.c.iii) identifies its underlying, and identifiers confirm it, as they confirm a
company; a name alone confirms nothing. A security referenced by its identifiers, by a derivative its report codes as on
equity (Item C.4, `DE`), or gives no code, is `{"company": ...}`, which resolution confirms or does not; one referenced
by a derivative on a commodity, a currency, a rate or credit (`DCO`, `DFE`, `DIR`, `DCR`) or anything else is no
company's, whatever its identifiers (a bitcoin future's "BTCU6 CURNCY"), since a row resolves to a company only when
what it refers to is equity (MATH.md 3.1). An index is given by the name and the identifier the report gives, with
OpenFIGI's reading of that identifier (`figi_index`): an index it names, or securities instead. Two references that
OpenFIGI reads as one index are one index (the S&P 500 by its ticker and by its ISIN). An identifier OpenFIGI reads as a
security, or as nothing while it names a US listing, conflicts with an index: its row keeps its index, with no
companies.

An index's companies are the holdings of the fund whose returns match the index's own values (harness.venues.trackers),
and come from nothing else: what a fund itself holds beside its derivatives is not its index (a fund holding one
company, cash and a future on the S&P 500 is exposed to the S&P 500's companies, not that one). Where Massive gives
the index's values, its row's index carries the symbol they are read under (`values`, `I:NDX`), the index's own
series; where a fund matches them, the row carries that fund's companies, each with its weight in the fund, per dollar
of the fund's net assets, and `basket`, which fund they are, as of when, and how closely it tracked. The symbol is
OpenFIGI's ticker for the index, which Massive's feeds share where the index's provider's symbol is Bloomberg's (the
Nasdaq-100, the S&P 500); where Bloomberg has its own (the Dow, `INDU` against `I:DJI`; the Russell 2000, `RTY`
against `I:RUT`; the Select Sectors, `IXT` against `I:SIXT`), it is the feed's code its providers state
(harness.venues.massive.INDEX_SYMBOLS), and an index with neither has no values, and keeps its index with no companies.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np

from harness.derive import series
from harness.derive.assets import HALF_CENT, Kinds, in_dollars
from harness.derive.options import delta as terms_delta
from harness.venues.massive import INDEX_SYMBOLS, OPTION_LABELS, answered

# The fields of a row its company is resolved by (harness.derive.identity), kept on what a reference names.
IDENTIFIERS = ("constituent_ticker", "constituent_name", "isin", "cusip", "figi", "lei", "country_of_exchange",
               "security_type", "asset_class")
LINEAR = ("SWP", "FUT")
OPTIONS = ("OPT", "WAR", "SWO")
OPTION_WORDS = OPTION_LABELS  # ETF Global's asset class for an option
WARRANT_WORDS = ("right", "equity wrt", "warrant")  # its security types for a right or a warrant
# An option's symbol as the Options Clearing Corporation writes it, a contract's identifier wherever it is listed in
# the US: its root, of one to six characters (a FLEX contract's begins with a digit, "4SPY"), the expiry as YYMMDD, C
# or P, and the strike in thousandths, eight digits; a fund's row can pad the root with spaces ("NDX   261218C29000000").
# Massive names the contract `O:` and the symbol unpadded (harness.venues.massive.options).
OCC = re.compile(r"([A-Z0-9]{1,6})\s*(\d{6})([CP])(\d{8})")
# Bloomberg's description of a US-listed option (DESCRIBED), as OpenFIGI gives it (`securityDescription`) for the
# contract its OCC symbol maps to (https://www.openfigi.com/api/documentation, idType `OCC_SYMBOL`; read live: `NDX   261120C29675000`
# is "NDX US 11/20/26 C29675", and `NEE   261120C00080000` "NEE US 11/20/26 C80"): the OCC's root, the composite
# exchange `US`, the expiry as MM/DD/YY, C or P, and the strike as a decimal. ETF Global writes it in a row's name for
# some issuers' contracts, where others write the OCC's symbol in its ticker; of the 1,500 most traded US funds' rows
# on 2026-11-09, 498 carry both, and the description gives the symbol in every one.
# The other fixed forms ETF Global writes a contract's terms in, in a row's name where the row gives no symbol, each
# stating the contract's root, expiry, right and strike in fixed places (read live on the funds' rows, 2019 to 2026):
# - Bloomberg's description with its market sector after it, the exchange's code left out for an index's option
#   ("SPXW US 10/28/22 C3920 Index", "SPX 10/21/22 C3990 Index", "EEM 08/19/22 P34 Equity"), and a FLEX contract's,
#   its style digit before the root (FLEX_STYLE; "2 JNJ US 01/27/2025 C238.88", "4 SPY US 04/14/22 C4.17",
#   "2 DOW  01/27/25 P81.69"), the year in two digits or four;
# - ETF Global's own: the root, the expiry as month/day/year, the strike, and C or P ("SPX 03/31/2020 3401.27 P",
#   "SPY 7/30/2021 328.67 C", "LLY 02/27/2026 1065 C"), stating no exercise style: the contract a market lists by
#   those terms where one does, and otherwise a FLEX contract whose style no source here states.
# A name in words ("S&P 500 3970 FLEX CALL 12/07/2027") states no root, and names no contract.
DESCRIBED = re.compile(r"(?:([1-4]) )?([A-Z0-9]{1,6})(?: US)? +(\d{1,2})/(\d{1,2})/(\d{4}|\d{2}) ([CP])"
                       r"(\d{1,5}(?:\.\d{1,3})?)(?: (?:Index|Equity))?")
ETF_GLOBAL_FORM = re.compile(r"([A-Z0-9]{1,6}) (\d{1,2})/(\d{1,2})/(\d{4}|\d{2}) (\d{1,5}(?:\.\d{1,3})?) ([CP])")
# - and a form stating the terms twice, its root, CALL or PUT, the currency, the strike and the expiry as DD/MON/YYYY,
#   and then Bloomberg's description ("SPY CALL USD 772 20/NOV/2026  SPY US 11/20/26 C772": HELO's, ROCY's, ROCQ's),
#   read where the two agree.
STATED_TWICE = re.compile(r"([A-Z0-9]{1,6}) (CALL|PUT) USD (\d{1,5}(?:\.\d{1,3})?) (\d{2})/([A-Z]{3})/(\d{4}) +(.+)")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
# A FLEX contract's symbol is a listed one's with a digit before its underlying's ("4SPY"), which states its exercise
# style: 1 and 3 American, 2 and 4 European, for an index or an equity alike (5 and 6, an Asian or a cliquet option, are
# no plain call or put): Cboe's FLEX symbology, https://cdn.cboe.com/resources/Institutional/flexsymbology.pdf, and
# OCC's FLEX reports, https://www.theocc.com/Market-Data/Market-Data-Reports/Series-and-Trading-Data/Flex-Reports.
FLEX_STYLE = {"1": "american", "2": "european", "3": "american", "4": "european"}
FLEX_UNITS = 100  # the units of its underlying a FLEX contract controls (read live: BALT's 1.91 call on SPY, valued in
# its holdings at 748.22 a unit against SPY's 752.69 close, as a contract that deep in the money is worth)
EQUITY_CODES = ("", "DE")  # Form N-PORT's code for a derivative on equity, or none (Item C.4)
SESSIONS = 63  # the sessions a fund's slope on its reference is measured over (MATH.md 3.1)


def _fold(value: Any) -> str:
    return str(value or "").strip().casefold()


def identifiers(row: dict) -> dict:
    """A row's identifiers, as resolution reads them."""
    return {k: row[k] for k in IDENTIFIERS if row.get(k) not in (None, "")}


def implied_net_assets(rows: Iterable[dict]) -> float | None:
    """The net assets ETF Global's rows imply: their market value over their weight, over every row that gives both."""
    value = weight = 0.0
    for r in rows:
        mv, w = r.get("market_value"), r.get("weight")
        if isinstance(mv, (int, float)) and isinstance(w, (int, float)) and w:
            value += abs(mv)
            weight += abs(w)
    return value / weight if weight else None


RECONCILED = 0.02  # how near a row's own value in dollars comes to its notional read one way, as a share of it


def notional_code(currency: str | None, row: dict) -> str:
    """The currency a report states a notional under: its own code, or else its row's."""
    stated = str(currency or "").strip().upper()
    return stated if stated not in ("", "N/A") else str(row.get("currency") or "USD").strip().upper()


def _dollars(amount: float | None, currency: str | None, row: dict, rates: dict | None = None) -> float | None:
    """A notional in dollars, its units established before it is converted (MATH.md 3.1: in dollars from the contract's
    currency). Stated in dollars, as it is. Stated under another currency's code, filers differ on whether the number
    is in that currency (read live in their 2026 reports: KMLM's Canadian bond future, 10,917,600.17 under CAD with
    the row's value $10,930,428.35, is in dollars; INTF's TOPIX future, 4,973,079.61 under JPY with the row's value
    $30,550.25, in yen; and EWY's KOSPI future, 253,649,810.70 under KRW, says neither, its row's value being what it
    has gained or lost): it is in whichever currency the row's own value in dollars reconciles it in, the notional as
    dollars or converted at the currency's rate on the report's date (`rates`, dollars per unit by day), within
    RECONCILED of it, the two readings standing an exchange rate apart; converted once, at that rate. Where neither
    reading reconciles, or both do, or no rate is read, none: its units are not established."""
    if amount is None:
        return None
    code = notional_code(currency, row)
    if code in ("USD", "N/A"):
        return amount
    value, size = row.get("market_value"), abs(amount)
    rate = series.rate_on((rates or {}).get(code) or {}, str(row.get("effective_date") or ""))
    if not isinstance(value, (int, float)) or not size:
        return None
    as_dollars = abs(abs(value) - size) <= RECONCILED * size
    in_currency = rate is not None and abs(abs(value) - size * rate) <= RECONCILED * size * rate
    if as_dollars != in_currency:
        return amount if as_dollars else amount * rate
    return None


def notional_codes(payload: Any) -> list[str]:
    """The currencies other than the dollar a report states its swaps' and futures' notionals under, which their
    rows' values may establish them in (_dollars): each one's rate is read for the report's date."""
    rows = (payload or {}).get("results") or [] if isinstance(payload, dict) else []
    return sorted({notional_code(d.get("currency"), r) for r in rows if isinstance(r, dict)
                   and (d := r.get("derivative")) and d.get("category") in LINEAR and d.get("notional") is not None}
                  - {"USD", "N/A"})


def strike_text(value: float) -> str:
    """A strike as a request names it: 7625, 102.5."""
    return ("%f" % value).rstrip("0").rstrip(".")


def report_option_key(row: dict) -> str | None:
    """A report's option by what its report states of it: what it refers to (an index's identifier, or a security's
    ISIN or CUSIP), its expiry, right and strike, and the report's date; None where the report leaves one unstated."""
    d = row.get("derivative") or {}
    ref = d.get("reference") or {}
    what = (ref.get("index") or {}).get("identifier") or (ref.get("security") or {}).get("isin") or \
        (ref.get("security") or {}).get("cusip")
    if d.get("category") != "OPT" or not what or not d.get("expiry") or not d.get("right") or \
            not isinstance(d.get("strike"), (int, float)) or not row.get("effective_date"):
        return None
    return f"{what}|{d['expiry']}|{d['right']}|{strike_text(d['strike'])}@{row['effective_date']}"


def _report_option(row: dict, d: dict, net_assets: Any, options: dict, payoff: dict) -> dict:
    """A report's option, where the report is the fund's rows (MATH.md 3.1): its terms as the report states them, and
    those it does not state (its exercise style, the shares each contract controls) from the listed contracts its
    stated terms name on the report's date (Massive.option_listings: `listings`, read with its underlying's ticker,
    `ticker`, by the look-through), where they agree; its price what the fund's holdings state for it, its value
    over its contracts and the shares each controls, at the report date's close, beside its underlying's close that
    day, its payer's dividends and the Treasury's curve then (terms_inputs); its delta, the one those give
    (harness.derive.options.delta). No exposure where a term no source states, or a price no volatility gives: a
    contract no listed one matches, an over-the-counter or FLEX one, or one whose stated strike its listing does not
    carry."""
    found = (options.get("report") or {}).get(report_option_key(row) or "") or {}
    listings = [c for c in found.get("listings") or [] if isinstance(c, dict)]
    styles = {c.get("exercise_style") for c in listings} - {None}
    units = {c.get("shares_per_contract") for c in listings} - {None}
    style = next(iter(styles)) if len(styles) == 1 else None
    per = next(iter(units)) if len(units) == 1 else None
    held = row.get("shares") if str(row.get("units") or "").upper() == "NC" else None
    contracts = abs(held) if isinstance(held, (int, float)) and held else None
    # what each contract controls, as the report states it, per contract or over every contract (sec.derivative): a
    # listed contract's terms stand for the report's only where the two agree, as every one the Options Clearing
    # Corporation clears does (100); one written over the counter with the same strike and expiry controls what its
    # report states (read live: GOF's call on HYG to Bank of America, one share a contract, 366,881 of them), and its
    # style no source states
    controls = d.get("underlying_per_contract")
    controls = abs(controls) if isinstance(controls, (int, float)) and controls else None
    if controls and per and contracts and controls == per * contracts:
        controls = per
    if controls and controls != per:
        style, per = None, controls
    value = row.get("market_value")
    price = abs(value) / (contracts * per) if isinstance(value, (int, float)) and contracts and \
        isinstance(per, (int, float)) and per else None
    day = row.get("effective_date")
    at, ticker = close_moment(day), found.get("ticker")
    terms_read = options.get("terms") or {}
    level = ((terms_read.get("closes") or {}).get(ticker) or {}).get(day or "")
    stated = {"status": "OK" if listings else None, "style": style, "listed": [c.get("ticker") for c in listings],
              "price": price, "price_from": "holdings", "underlying": ticker, "underlying_price": level, "at": at,
              **terms_inputs(ticker, at, level, terms_read)}
    worked = terms_delta(right=d.get("right"), style=style, strike=d.get("strike"), expiry=d.get("expiry"), at=at,
                         price=price, underlying_price=level, rate=stated.get("rate"),
                         dividend_yield=stated.get("dividend_yield"))
    if worked is not None:
        stated.update({"delta": worked["delta"], "delta_from": "terms", "worked": worked})
    out: dict[str, Any] = {"quote": {k: v for k, v in stated.items() if v is not None},
                           "payoff": {"option": {**payoff.get("option", {}), "style": style,
                                                 **({"underlying_per_contract": per} if per else {})}}}
    sign = -1.0 if d.get("side") == "written" else 1.0
    if worked is not None and all(isinstance(v, (int, float)) for v in (contracts, per, level, net_assets)) \
            and net_assets:
        out["exposure"] = sign * contracts * per * worked["delta"] * level / net_assets
    return out


def _option(row: dict, terms: dict) -> dict:
    contracts = row.get("shares") if (row.get("units") or "").upper() == "NC" else None
    return {"option": {"right": terms.get("right"), "side": terms.get("side"),
                       "underlying_per_contract": terms.get("underlying_per_contract"),
                       "contracts": abs(contracts) if isinstance(contracts, (int, float)) else None,
                       "strike": terms.get("strike"), "expiry": terms.get("expiry"),
                       "value": row.get("market_value")}}


def on_equity(row: dict) -> bool:
    """Whether a report's derivative is on equity, by its code (Form N-PORT's DE), or gives no code: only such a
    derivative's reference can be a company's (MATH.md 3.1)."""
    return str(row.get("asset_category") or "").strip().upper() in EQUITY_CODES


def _security_to(security: dict, readings: dict[str, dict] | None = None) -> dict | None:
    """A security a derivative on equity references, by its identifiers; one a report names by its US ticker alone, by
    that listing where OpenFIGI reads the ticker as one US share class's (`readings[ticker]["listed"]`,
    ticker_reference), and otherwise by its ticker, which confirms nothing."""
    found = {k: security[k] for k in ("cusip", "isin") if security.get(k)}
    if security.get("ticker"):
        found["constituent_ticker"] = security["ticker"]
        if not found.keys() - {"constituent_ticker"}:
            found.update(ticker_reference(security["ticker"], (readings or {}).get(security["ticker"])) or {})
    if not found:
        return None
    if security.get("title") or security.get("issuer"):
        found["constituent_name"] = security.get("title") or security.get("issuer")
    return {"company": found}


def security_tickers(payload: Any) -> list[str]:
    """The US tickers a report's derivatives on equity name a security by and by nothing else, whose share classes
    OpenFIGI is asked for (_security_to)."""
    out = set()
    for r in (payload or {}).get("results") or [] if isinstance(payload, dict) else []:
        security = ((r.get("derivative") or {}).get("reference") or {}).get("security") \
            if isinstance(r, dict) and on_equity(r) else None
        if isinstance(security, dict) and security.get("ticker") and not (security.get("cusip") or security.get("isin")):
            out.add(security["ticker"])
    return sorted(out)


def held_securities(payload: Any) -> dict[str, dict]:
    """Each security a report holds outright, its row by each identifier the row gives (its ticker, ISIN and CUSIP),
    upper-cased: what an identifier its derivatives give names first (index_key)."""
    out: dict[str, dict] = {}
    for r in (payload or {}).get("results") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or r.get("derivative"):
            continue
        for k in ("constituent_ticker", "isin", "cusip"):
            if r.get(k):
                out.setdefault(str(r[k]).strip().upper(), r)
    return out


def index_key(index: dict, reading: dict | None, held: dict[str, dict] | None = None) -> tuple[str, str] | None:
    """What an index reference is: ("held", identifier) where its identifier names a security the same report holds
    (`held`, held_securities), which is that security, whatever else the identifier could name (EDC's swaps on "EEM",
    the fund it also holds, which OpenFIGI's index search reads as another index); and otherwise by its identifier as
    OpenFIGI reads it: ("figi", FIGI) for an index it names; ("conflict", identifier) for an identifier it reads as a
    security, or as nothing where the identifier is a US listing's (`listed`); ("identifier", identifier) for one it
    does not know; None for a reference with no identifier, which its name alone does not confirm."""
    identifier = (index or {}).get("identifier")
    if not identifier:
        return None
    if held and str(identifier).strip().upper() in held:
        return ("held", identifier)
    reading = reading or {}
    if (reading.get("index") or {}).get("figi"):
        return ("figi", reading["index"]["figi"])
    if reading.get("securities") or reading.get("listed"):
        return ("conflict", identifier)
    return ("identifier", identifier)


def nport_rows(payload: dict, kinds: Kinds, readings: dict[str, dict], indexes: dict | None = None,
               options: dict | None = None, rates: dict | None = None) -> list[dict]:
    """A Form N-PORT report's rows as exposure, with a forward's sold currency as a row of its own (with no weight, so
    what reads weights reads the report's rows alone); each index row with what `indexes` establishes of its index;
    and, where the report is the fund's rows, each option at the delta its terms and prices give (`options`,
    _report_option)."""
    indexes = indexes or {}
    na = payload.get("net_assets")
    stamp = {"report_date": payload.get("effective_date"), "net_assets": na}
    rows = [r for r in payload.get("results") or [] if isinstance(r, dict)]
    held = held_securities(payload)
    keys = {}
    for r in rows:
        ref = ((r.get("derivative") or {}).get("reference") or {})
        if "index" in ref:
            keys[id(r)] = index_key(ref["index"], readings.get(ref["index"].get("identifier") or ""), held)
    out: list[dict] = []
    for r in rows:
        kind = kinds.of(r)
        d = r.get("derivative")
        row = {**r, "kind": kind, **stamp}
        if d is None:
            row.update(_held(r, kind, r.get("weight")))
            out.append(row)
            continue
        category = d.get("category")
        reference = d.get("reference") or {}
        if "index" in reference and (keys.get(id(r)) or (None,))[0] == "held":
            # the security the report holds, as its own row names it (_held)
            to = {"company": identifiers(held[str(keys[id(r)][1]).strip().upper()])}
        elif "index" in reference:
            key = keys.get(id(r))
            to = {"index": dict(reference["index"])}
            found = indexes.get(key) if key is not None and key[0] == "figi" else None
            if key is not None and key[0] == "figi":
                to["index"]["figi"] = key[1]
            if found and found.get("values"):
                to["index"]["values"] = found["values"]
            if found and found.get("companies"):
                to.update({"companies": found["companies"], "basket": found["basket"],
                           "followers": found.get("followers") or []})
            listed = ticker_reference(reference["index"].get("identifier") or "",
                                      readings.get(reference["index"].get("identifier") or "")) \
                if key is not None and key[0] == "conflict" and on_equity(r) else None
            if listed:
                to = {"company": listed}
        elif "security" in reference and on_equity(r):
            to = _security_to(reference["security"], readings)
        else:
            to = None
        if category in LINEAR:
            notional = _dollars(d.get("notional"), d.get("currency"), r, rates)
            if notional is not None and category == "FUT" and d.get("payoff_profile") in ("Long", "Short"):
                notional = abs(notional) * (1 if d["payoff_profile"] == "Long" else -1)
            exposure = notional / na if notional is not None and na else None
            row.update({"exposure": exposure, "payoff": "linear", **({"to": to} if to else {})})
            out.append(row)
        elif category == "FWD":
            bought, sold = d.get("bought") or {}, d.get("sold") or {}
            dollars = _forward_dollars(bought, sold, r.get("market_value"))
            row.update({"exposure": dollars[0] / na if dollars and na else None, "payoff": "linear",
                        **({"to": {"currency": bought["currency"]}} if bought.get("currency") else {})})
            out.append(row)
            out.append({"constituent_name": f"{r.get('constituent_name') or 'Forward'}: the currency sold",
                        "weight": None, "kind": "currency" if (sold.get("currency") or "USD") != "USD" else "cash",
                        "exposure": dollars[1] / na if dollars and na else None, "payoff": "linear",
                        **({"to": {"currency": sold["currency"]}} if sold.get("currency") else {}),
                        "effective_date": r.get("effective_date"), "leg": "sold", **stamp})
        elif category in OPTIONS:
            terms = d if category != "WAR" else {**d, "right": d.get("right") or "call", "side": "purchased"}
            row.update({"exposure": None, "payoff": _option(r, terms), **({"to": to} if to else {})})
            if category == "OPT" and options:
                row.update(_report_option(r, d, na, options, row["payoff"]))
            out.append(row)
        else:
            row.update({"exposure": None, "payoff": "linear", **({"to": to} if to else {})})
            out.append(row)
    return out


def _forward_dollars(bought: dict, sold: dict, value: float | None) -> tuple[float, float] | None:
    """A forward's two sides in dollars, the one bought positive and the one sold negative, where one side is in
    dollars: that side at its amount, and the other at what it is worth today, the dollar side's amount less the
    forward's own value as the report gives it (its value is the gap between the two), so the sides sum to it. Where
    neither side is in dollars, none: the report gives no rate for either."""
    value = float(value) if isinstance(value, (int, float)) else 0.0
    if (bought.get("currency") or "").upper() == "USD" and bought.get("amount") is not None:
        return abs(bought["amount"]), -(abs(bought["amount"]) - value)
    if (sold.get("currency") or "").upper() == "USD" and sold.get("amount") is not None:
        return abs(sold["amount"]) + value, -abs(sold["amount"])
    return None


def _held(row: dict, kind: str, weight: float | None, pairs: frozenset[str] = frozenset()) -> dict:
    """What a row that is no derivative is exposed to, at its own weight: a coin, its pair (coin)."""
    if kind in ("cash", "currency"):
        currency = str(row.get("currency_traded") or row.get("currency") or "").upper()
        return {"exposure": weight, "payoff": "cash",
                **({"to": {"currency": currency}} if kind == "currency" and currency else {})}
    if kind == "equity":
        return {"exposure": weight, "payoff": "holding", "to": {"company": identifiers(row)}}
    code = coin(row, kind)
    if code and pair_of(code) in pairs:
        return {"exposure": weight, "payoff": "holding", "to": {"pair": pair_of(code)}}
    return {"exposure": weight, "payoff": "holding"}


# Bloomberg's tickers for a coin's price in dollars, which some issuers give as their holding's ticker (Fidelity's
# FBTC, "XBTUSD"; FETH, "XETUSD"), each as Bloomberg states it against the coin's name in its Bloomberg Galaxy Crypto
# Index factsheet of January 2022 (Name, Ticker: Bitcoin XBTUSD, Ethereum XETUSD, Litecoin XLCUSD, Bitcoin Cash
# XBNUSD, EOS XEOUSD)
# https://assets.bbhub.io/professional/sites/10/BGCI-Factsheet-January-2022.pdf
# and its coin's code. A code no document of Bloomberg's found states (Solana's, as SSK's "XSOUSD") is not read.
BLOOMBERG_COINS = {"XBTUSD": "BTC", "XETUSD": "ETH", "XLCUSD": "LTC", "XBNUSD": "BCH", "XEOUSD": "EOS"}


def coin(row: dict, kind: str) -> str | None:
    """The coin a holding is, where its source says so as data: a row of no kind its words make equity, debt, cash or
    a currency, whose currency ETF Global gives as a code other than the dollar's (IBIT's bitcoin, `currency_traded:
    BTC`, with `asset_class: Alternative`; ETHB's staked ether, `ETH`), or whose ticker is Bloomberg's for a coin's
    price in dollars (BLOOMBERG_COINS). It is a coin where Coinbase Exchange lists that code against the dollar
    (pairs), the reference whose prices a pair's are; a name alone ("BITCOIN") says nothing."""
    if kind not in ("other", "commodity") or "derivative" in row:
        return None
    named = BLOOMBERG_COINS.get(str(row.get("constituent_ticker") or "").strip().upper())
    if named:
        return named
    code = str(row.get("currency_traded") or "").strip().upper()
    return code if code.isalnum() and code not in ("USD", "N/A") else None


def pair_of(code: str) -> str:
    """A coin's pair against the dollar, as the store names it (harness.venues.coinbase.product)."""
    return f"X:{code}USD"


def coins(payload: Any, kinds: Kinds) -> list[str]:
    """The pairs of the coins a fund's holdings may be, by their source's codes (coin), for Coinbase Exchange to
    confirm."""
    rows = (payload or {}).get("results") or [] if isinstance(payload, dict) else []
    return sorted({pair_of(c) for r in rows if isinstance(r, dict) for c in [coin(r, kinds.of(r))] if c})


def etf_global_rows(payload: dict, kinds: Kinds, options: dict | None = None, indexes: dict | None = None,
                    pairs: frozenset[str] = frozenset(), priced: dict | None = None) -> list[dict]:
    """ETF Global's rows as exposure. Its derivatives' weights are their market values, or their notionals, as each
    issuer reports them, so a derivative's exposure is none here: a fund's derivatives are read from its report where
    one is public (with_exposure). A row naming a listed contract (option_symbol) is an option, at the delta its market
    quotes (_listed_option), from `options`: `quotes`, Massive's snapshot of each contract, and `classes`, OpenFIGI's
    share classes of each US ticker a contract is on. A coin's row is its pair, where Coinbase Exchange lists it
    (`pairs`). A row that states shares of a listing its price names (`priced`, by price_subject: the listing, its
    company's identifiers, and how its close and the fund's returns established it) is exposed to that listing's
    company at its value over the fund's net assets, as data (MATH.md 3.1)."""
    net = implied_net_assets(payload.get("results") or [])
    stamp = {"report_date": payload.get("effective_date"), "net_assets": net}
    options = options or {}
    out = []
    for r in payload.get("results") or []:
        if not isinstance(r, dict):
            continue
        symbol = option_symbol(r)
        if symbol and named_expiry(symbol) < str(r.get("effective_date") or "")[:10]:
            # a contract named as having expired before its row's date: the row holds something, but not the contract
            # its name states, so it establishes none, and no treatment sizes it from terms that cannot be its own
            out.append({**r, "kind": "derivative", **stamp, "exposure": None, "payoff": _option(r, {})})
            continue
        if symbol:
            out.append({**r, "kind": "derivative", **stamp,
                        **_listed_option(r, symbol, (options.get("quotes") or {}).get(symbol), net, indexes or {},
                                         options.get("classes") or {}, options.get("terms"), options.get("held_at"))})
            continue
        kind = kinds.of(r)
        row = {**r, "kind": kind, **stamp}
        named = (priced or {}).get(price_subject(r)) if states_shares(r, kind) else None
        if named:
            row.update({"exposure": r["market_value"] / net if net else None, "payoff": "linear",
                        "to": {"company": named["company"]}, "priced": named})
        elif kind == "derivative":
            types, given = _fold(r.get("security_type")), _fold(r.get("asset_class"))
            if types in WARRANT_WORDS:
                payoff: Any = _option(r, {"right": "call", "side": "purchased"})
            elif given in OPTION_WORDS:
                payoff = _option(r, {})
            else:
                payoff = "linear"
            row.update({"exposure": None, "payoff": payoff})
        else:
            row.update(_held(r, kind, r.get("weight"), pairs))
        out.append(row)
    return out


def is_report(payload: Any) -> bool:
    """Whether a fund's holdings are a Form N-PORT report's."""
    return isinstance(payload, dict) and any(isinstance(r, dict) and "asset_category" in r
                                             for r in payload.get("results") or [])


def with_exposure(payload: Any, kinds: Kinds, readings: dict[str, dict] | None = None,
                  indexes: dict | None = None, report: Any = None, *, options: dict | None = None,
                  slope: dict | None = None, pairs: frozenset[str] = frozenset(), rates: dict | None = None,
                  priced: dict | None = None, census: dict | None = None) -> Any:
    """A fund's rows, each with what it is exposed to and by how much and the date and net assets it is stated per:
    what it holds from `payload`, ETF Global's holdings or its report; where those are ETF Global's, its options from
    them, each listed contract at its market's delta (`options`, etf_global_rows), each coin at its pair where Coinbase
    Exchange lists it (`pairs`), and its swaps and futures from its latest report, `report`, in place of ETF Global's
    own rows of them; and where the fund is measured on one reference, its slope on it (`slope`, fit_slope; measure),
    which is also the payload's `measured`. A row of its holdings that states shares of a listing its price names
    (`priced`: `rows`, by price_subject; `listed`, each US ticker an ISIN its report gives lists under) stands as
    that exposure, as of its date, and its report's derivative on the same company is not read: the same position,
    stated as of the report's date. Where the slope is measured and the fund's latest census (`census`, Form N-CEN)
    marks it as seeking a multiple, or the inverse, of a benchmark, the payload's `seeks_multiple` names that census:
    the slope is then the multiple it pays each session (MATH.md 1.6, 3.1). Where its holdings are ETF Global's and its
    report holds only cash, contracts on one reference and that reference's shares (cash_and_one_reference), a row of
    them that names nothing and is stated in dollars is cash, and the payload's `unnamed` says so, with the report's
    date: `{"kind": "cash", "report_date"}`, which the look-through's kinds read (portfolio.fund_kinds)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return payload
    if is_report(payload):
        rows, net = nport_rows(payload, kinds, readings or {}, indexes, options, rates), payload.get("net_assets")
        out = {**payload, "report_date": payload.get("effective_date"), "net_assets": net}
    else:
        # its own swaps and futures give way to its report's, where one gives the fund's derivatives; before a fund's
        # first report they stand, unsized, as what it holds (a young fund's swaps, read live: APLZ's four CFD rows).
        # Its options stand, a contract it names or not: a report's options are not read beside its holdings, and
        # what its source says of a row, that it is an option, stays though the contract is not named (SVOL's
        # "NOVEMBER 26 CALLS ON SPX 7740.00000", labelled an option)
        # a row that names nothing and is stated in dollars is among the fund's cash where its report holds only cash,
        # contracts on one reference and that reference's shares: its other positions name themselves
        filed = nport_rows(report, kinds, readings or {}, indexes, rates=rates) if is_report(report) else []
        unnamed = cash_and_one_reference(filed, readings) if filed else None
        if unnamed is not None:
            kinds = replace(kinds, unnamed="cash")
        given = has_derivatives(report)
        rows = [r for r in etf_global_rows(payload, kinds, options, indexes, pairs, (priced or {}).get("rows"))
                if r["kind"] != "derivative" or isinstance(r.get("payoff"), dict) or not given or r.get("priced")]
        named = {r["priced"]["ticker"] for r in rows if r.get("priced")}
        out = {**payload, "report_date": payload.get("effective_date"),
               "net_assets": implied_net_assets(payload["results"]),
               **({"unnamed": {"kind": "cash", "report_date": report.get("effective_date")}}
                  if unnamed is not None else {})}
        if has_derivatives(report):
            listed = (priced or {}).get("listed") or {}
            reported = [r for r in filed
                        if (r.get("derivative") and (r["derivative"].get("category") not in OPTIONS))
                        or r.get("leg") == "sold"]
            rows += [r for r in reported if not (named and _same_listing(r, named, listed))]
            # a row its price names whose position the report states as a derivative is that derivative, by the
            # report's own code for it (Form N-PORT's derivative category), whatever its holdings source labels it
            for row in rows:
                if row.get("priced"):
                    same = next((r for r in reported if r.get("derivative")
                                 and _same_listing(r, {row["priced"]["ticker"]}, listed)), None)
                    if same is not None:
                        row["reported_as"] = {"kind": "derivative", "category": same["derivative"].get("category")}
                        row["kind"] = "derivative"  # the row's kind is the report's, as every reader of it takes it
            out.update({"derivatives_date": report.get("effective_date"),
                        "derivatives_net_assets": report.get("net_assets")})
    measured = measure(rows, slope)
    found = any("measured" in r for r in measured)
    seeks = found and isinstance(census, dict) and census.get("seeks_multiple") is True
    return {**out, "results": measured, **({"measured": slope} if found else {}),
            **({"seeks_multiple": {k: census.get(k) for k in ("form", "accession", "filed", "url")}}
               if seeks else {})}


def _same_listing(row: dict, named: set[str], listed: dict[str, list[str]]) -> bool:
    """Whether a report's derivative is on what one of `named`, the listings its fund's current rows state shares
    of, is: by any identifier its report gives what it refers to that is one of their US tickers (a security's
    ticker, or an index's identifier where the report names a fund by its ticker as an index: EDC's swaps on "EEM",
    read live), or by an ISIN that lists under one of them (`listed`, OpenFIGI's US listings of each ISIN)."""
    referred = (row.get("derivative") or {}).get("reference") or {}
    to = row.get("to") or {}
    security, index = referred.get("security") or {}, referred.get("index") or {}
    given = {security.get("ticker"), index.get("identifier"), (to.get("company") or {}).get("constituent_ticker"),
             (to.get("index") or {}).get("identifier")}
    tickers = {str(x).strip().upper() for x in given if x}
    for isin in {security.get("isin"), (to.get("company") or {}).get("isin")} - {None, ""}:
        tickers |= set(listed.get(str(isin).strip().upper()) or [])
    return bool(tickers & named)


def _terms(row: dict) -> dict:
    """An option row's terms; none for any other."""
    payoff = row.get("payoff")
    return (payoff.get("option") or {}) if isinstance(payoff, dict) else {}


def has_derivatives(payload: Any) -> bool:
    """Whether a fund's report gives any derivative."""
    return isinstance(payload, dict) and any(isinstance(r, dict) and r.get("derivative")
                                             for r in payload.get("results") or [])


def index_identifiers(payload: Any) -> list[str]:
    """The identifiers a report's derivatives give the indexes they reference."""
    out = set()
    for r in (payload or {}).get("results") or [] if isinstance(payload, dict) else []:
        ref = ((r.get("derivative") or {}).get("reference") or {}) if isinstance(r, dict) else {}
        identifier = (ref.get("index") or {}).get("identifier")
        if identifier:
            out.add(identifier)
    return sorted(out)


INDEX_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.]*")  # what Massive's index symbols take after `I:` (I:SPX, I:VIX1D)


def index_symbol(reading: dict | None) -> str | None:
    """The symbol Massive gives an index's values under: its feed's code for an index it names otherwise than
    Bloomberg does, by the FIGI OpenFIGI names the index by (harness.venues.massive.INDEX_SYMBOLS), and otherwise the
    ticker OpenFIGI names it by."""
    index = (reading or {}).get("index") or {}
    if index.get("figi") in INDEX_SYMBOLS:
        return INDEX_SYMBOLS[index["figi"]]
    ticker = (index.get("ticker") or "").strip()
    bare = ticker[:-len(" Index")] if ticker.endswith(" Index") else ticker
    # a ticker Massive's index symbols cannot take (OpenFIGI's "CBTX:CGI", which Massive refuses as "incorrectly
    # formatted") names no values here: the index keeps its name and reaches no company (MATH.md 3.1)
    return f"I:{bare}" if INDEX_TICKER.fullmatch(bare) else None


def indexes_exposed(payload: Any, readings: dict[str, dict], rates: dict | None = None) -> dict[tuple[str, str], str]:
    """Each index a report's derivatives are exposed to, where OpenFIGI names it, by its key, with the symbol its
    values are read under: only a derivative whose exposure is known, since an index's companies spread an exposure."""
    out = {}
    held = held_securities(payload)
    for r in nport_rows(payload, Kinds(), readings, rates=rates) if isinstance(payload, dict) else []:
        ref = (r.get("derivative") or {}).get("reference") or {}
        if "index" not in ref or not isinstance(r.get("exposure"), (int, float)):
            continue
        reading = readings.get(ref["index"].get("identifier") or "")
        key = index_key(ref["index"], reading, held)
        symbol = index_symbol(reading)
        if key is not None and key[0] == "figi" and symbol:
            out[key] = symbol
    return out


def companies_of(payload: Any) -> list[dict]:
    """A fund's holdings of companies' shares, each by its identifiers with its weight in the fund: its rows its source
    labels equity, weighed positive."""
    kinds = Kinds()
    return [{**identifiers(r), "weight": r["weight"]} for r in (payload or {}).get("results") or []
            if isinstance(r, dict) and isinstance(r.get("weight"), (int, float)) and r["weight"] > 0
            and "derivative" not in r and kinds.of(r) == "equity"] if isinstance(payload, dict) else []


def establish(readings: dict[str, dict], trackers: dict[str, dict], baskets: dict[str, dict]) -> dict:
    """What is established of each index OpenFIGI names: the symbol its values are read under, where Massive gives them
    (`trackers`, each index's answer by its symbol); and where a fund's returns match them, the companies of the closest
    such fund whose holdings (`baskets`, by fund) give companies, with which fund they are."""
    out: dict[tuple[str, str], dict] = {}
    for symbol, found in trackers.items():
        if (found or {}).get("status") != "OK":
            continue
        info: dict[str, Any] = {"values": symbol, "followers": [m["listing"] for m in found.get("matches") or []]}
        for match in found.get("matches") or []:
            held = baskets.get(match["listing"])
            companies = companies_of(held)
            if companies:
                info.update({"companies": companies,
                             "basket": {"fund": match["listing"], "holdings_asof": held.get("effective_date"),
                                        "tracking": match["tracking"], "sessions": found.get("sessions")}})
                break
        out[("values", symbol)] = info
    for identifier, reading in readings.items():
        key, symbol = index_key({"identifier": identifier}, reading), index_symbol(reading)
        if key is not None and key[0] == "figi" and ("values", symbol) in out:
            out[key] = out[("values", symbol)]
    return out


def reached(rows: list[dict], path_weight: float) -> list[tuple[dict, float]]:
    """Each company a fund's derivatives reach, by its identifiers as resolution reads them, with what it reaches of
    the fund's holder: a security a derivative references, at the derivative's exposure, and each of an index's
    companies, at the derivative's exposure times its weight; an option's security whose exposure is not stated, at
    none, so that it is read whatever its size. Each carries the derivative row's date, as the booking resolves it
    (a security named by its CUSIP alone is found by the listing that carried it then)."""
    out = []
    for r in rows:
        if not r.get("derivative") and r.get("kind") != "derivative" and not r.get("priced"):
            continue
        to, e = r.get("to") or {}, r.get("exposure")
        dated = {"effective_date": r["effective_date"]} if r.get("effective_date") else {}
        size = abs(path_weight * e) if isinstance(e, (int, float)) else None
        if to.get("company"):
            out.append(({**to["company"], **dated}, size))
        for c in to.get("companies") or [] if size is not None else []:
            out.append(({**{k: v for k, v in c.items() if k != "weight"}, **dated}, size * abs(c["weight"])))
    return out


def option_symbol(row: dict) -> str | None:
    """The contract a fund's row names, as Massive names it, `O:` and the symbol the Options Clearing Corporation writes
    (OCC): the row's ticker where that is such a symbol; otherwise, where its source labels it as nothing or as an
    option, its name where that states the contract's terms in a fixed form (STATED_TWICE, DESCRIBED, ETF_GLOBAL_FORM),
    whose root, expiry, right and strike are the symbol's, a FLEX contract's root led by its style digit, a strike in
    thousandths of a dollar filling its eight digits. Whether a market lists the contract, its snapshot or its reference says
    (_listed_option). A name in words names no contract, and a row its source labels as something else is that."""
    found = OCC.fullmatch(str(row.get("constituent_ticker") or "").strip())
    if found:
        return f"O:{''.join(found.groups())}"
    labelled = _fold(row.get("asset_class"))
    if labelled and labelled not in OPTION_WORDS:  # its source says it is something else: its name is not read
        return None
    name = str(row.get("constituent_name") or "").strip()
    twice = STATED_TWICE.fullmatch(name)
    if twice:  # the description at its end, read where the terms before it are the same contract's
        root, right, strike, day, month, year, described = twice.groups()
        symbol = option_symbol({"constituent_name": described})
        stated = _symbol(root, year, str(MONTHS.index(month) + 1), day, right[0], strike) if month in MONTHS else None
        return symbol if symbol is not None and symbol == stated else None
    found = DESCRIBED.fullmatch(name)
    if found:
        style, root, month, day, year, right, strike = found.groups()
        return _symbol((style or "") + root, year, month, day, right, strike)
    found = ETF_GLOBAL_FORM.fullmatch(" ".join(name.split()))
    if found:
        root, month, day, year, strike, right = found.groups()
        return _symbol(root, year, month, day, right, strike)
    return None


def contract_key(symbol: str) -> str:
    """A contract by its terms, as `option_symbol` names it without a FLEX contract's style digit: a FLEX row named by its
    ticker ("4SPY  261231C00006820") and by its name ("SPY 12/31/2026 6.82 C") are one contract from day to day."""
    found = OCC.fullmatch(symbol[2:]) if symbol.startswith("O:") else None
    if not found:
        return symbol
    root, day, right, strike = found.groups()
    if len(root) > 1 and root[0] in FLEX_STYLE:
        root = root[1:]
    return f"O:{root}{day}{right}{strike}"


def contracts_by_day(payload: Any) -> dict[str, dict]:
    """What a fund held of option contracts on each day, from its holdings over a window (harness.venues.massive,
    `holdings_history`), for its remainder's fit (MATH.md 1.7): for each day, its net assets, and each contract it held,
    by its terms (contract_key), with its value, the position's market value, and its quantity, its contracts, each
    signed as the fund's holdings state them, a contract's rows on one day summed. A FLEX contract's value is the
    fund's own, which no market quotes (MATH.md 3.1). A row naming no contract is in the day's net assets alone."""
    out: dict[str, dict] = {}
    for day, held in sorted(((payload or {}).get("days") or {}).items()):
        contracts: dict[str, dict] = {}
        for r in held.get("rows") or []:
            symbol = option_symbol(r)
            if symbol is None:
                continue
            c = contracts.setdefault(contract_key(symbol), {"contract": contract_key(symbol), "value": 0.0,
                                                            "quantity": 0.0})
            c["value"] += float(r.get("market_value") or 0.0)
            c["quantity"] += float(r.get("shares_held") or 0.0)
        out[day] = {"net_assets": held.get("net_assets"), "contracts": sorted(contracts.values(),
                                                                          key=lambda c: c["contract"])}
    return out


def named_expiry(symbol: str) -> str:
    """The expiry a contract's symbol names (`O:` and the OCC's), as YYYY-MM-DD."""
    day = OCC.fullmatch(symbol[2:]).group(2)
    return f"20{day[:2]}-{day[2:4]}-{day[4:]}"


def _symbol(root: str, year: str, month: str, day: str, right: str, strike: str) -> str:
    """A contract's symbol as Massive names it, from its terms: `O:`, the root, the expiry as YYMMDD, C or P, and the
    strike in thousandths of a dollar, eight digits (OCC)."""
    whole, _, part = strike.partition(".")
    return f"O:{root}{year[-2:]}{int(month):02d}{int(day):02d}{right}{int(whole) * 1000 + int(part.ljust(3, '0') or 0):08d}"


def listed_by_ticker(ticker: str, share_classes: Any) -> dict | None:
    """A security a source names by its US ticker alone, as a report names a swap's reference or Massive an option's
    underlying (MATH.md 3.1): that listing, where OpenFIGI reads the ticker as exactly one US share class's, given by the
    share class's FIGI, which Massive's reference under the ticker must name for resolution to confirm it
    (harness.derive.identity, the security itself)."""
    classes = share_classes if isinstance(share_classes, list) else []
    if not ticker or len(classes) != 1:
        return None
    return {"constituent_ticker": ticker.strip().upper(), "figi": classes[0]}


def ticker_reference(identifier: str, reading: dict | None) -> dict | None:
    """What an identifier a report gives an index refers to where OpenFIGI reads it as no index and no security, and as
    one US share class's ticker (`listed`): that listing (listed_by_ticker), marked as named by its ticker, so the
    fund's own returns corroborate it where its slope is measured (measure)."""
    reading = reading or {}
    if reading.get("index") or reading.get("securities"):
        return None
    found = listed_by_ticker(identifier, reading.get("listed"))
    return {**found, "named_by": "ticker"} if found else None


def _listed_option(row: dict, symbol: str, quote: Any, net_assets: float | None, indexes: dict,
                   classes: dict[str, Any], terms_read: dict | None = None, held_at: dict | None = None) -> dict:
    """A row naming a listed contract (option_symbol): its terms from the symbol; its exposure, the delta its market
    quotes (Massive's snapshot, `quote`) times its signed contracts, the shares each controls and the underlying's
    price, per dollar of the fund's net assets; and what the underlying is: an index by the symbol Massive gives its
    values under, with its companies where they are established (`indexes`), or the US listing the contract is on
    (listed_by_ticker). `quote` beside it carries what a delta is worked out from where its market quotes none
    (MATH.md 3.1): the contract's exercise style, its price and the underlying's, and the moment they are of
    (option_price), the Treasury's curve then and the underlying's dividend yield (terms_inputs). A FLEX contract, which
    no market lists, has its terms from its symbol (FLEX_STYLE, FLEX_UNITS), its price from what the fund's holdings
    state for it, and its underlying from the symbol's root, resolved as a report's index identifier is (`terms_read`).
    So does a
    contract named with no style digit that no market lists, which neither its market's snapshot nor its record knows
    (`terms_read["unlisted"]`): a FLEX contract whose style no source here states (ETF_GLOBAL_FORM), its terms its
    symbol's and FLEX_UNITS, its style none. Where no delta is
    quoted, the delta those give (harness.derive.options.delta: `worked`, `delta_from` "terms"); where they give none
    (a term no source states, a price no volatility gives), no exposure, every term and price that is stated kept on
    the row.

    Every input of its size is of one valuation moment (MATH.md 3.1): the row's count is read against the contract's
    price standing at its holdings' close (held_contracts, `held_at`); where its market quotes no delta, the
    underlying's price is its price at the moment the contract's price stood (price_at); and at a past as-of, where no
    snapshot stands, its terms are its reference's then (`contracts`) and its price the one standing at the as-of's
    close (`at_close`), its underlying resolved from its terms as a FLEX contract's root is; and so today for a
    contract the snapshot does not know, one since expired, on the latest date its fund's rows hold it."""
    root, day, right, strike = OCC.fullmatch(symbol[2:]).groups()
    terms_read = terms_read or {}
    found = quote if isinstance(quote, dict) else {}
    details, underlying = found.get("details") or {}, found.get("underlying_asset") or {}
    delta = (found.get("greeks") or {}).get("delta")
    level = underlying.get("value") if underlying.get("value") is not None else underlying.get("price")
    per, style = details.get("shares_per_contract"), details.get("exercise_style")
    standing = (held_at or {}).get(f"{symbol}@{close_moment(row.get('effective_date'))}")
    contracts, counted = held_contracts(row, per, standing)
    price, price_from, at = option_price(found)
    flex = flex_underlying(symbol)
    unlisted = symbol in (terms_read.get("unlisted") or ())
    past = (terms_read.get("contracts") or {}).get(symbol) if not found or found.get("error") else None
    if delta is None and found:  # its delta worked out from terms: the underlying at the moment its price stood
        level = price_at(terms_read.get("minutes"), underlying.get("ticker"), at)
    if past:  # a past as-of: its terms then, and its price and its underlying's standing at the as-of's close
        held = (terms_read.get("underlying") or {}).get(symbol) or {}
        per, style = past.get("shares_per_contract"), past.get("exercise_style")
        contracts, counted = held_contracts(row, per, standing)
        price, price_from, at = standing_price((terms_read.get("at_close") or {}).get(symbol))
        underlying = {"ticker": held.get("ticker")}
        level = price_at(terms_read.get("minutes"), held.get("ticker"), at)
    if flex or unlisted:
        held = (terms_read.get("underlying") or {}).get(symbol) or {}
        per, contracts, counted = FLEX_UNITS, row.get("shares_held"), "contracts"
        style = FLEX_STYLE[root[0]] if flex else None
        value = row.get("market_value")
        price = abs(value) / (abs(contracts) * per) if isinstance(value, (int, float)) and contracts else None
        price_from, at = "holdings", close_moment(row.get("effective_date"))
        underlying = {"ticker": held.get("ticker")}
        level = ((terms_read.get("closes") or {}).get(held.get("ticker")) or {}).get(row.get("effective_date") or "")
    terms = {"right": "call" if right == "C" else "put",
             "side": "written" if isinstance(contracts, (int, float)) and contracts < 0 else "purchased",
             "underlying_per_contract": per, "contracts": abs(contracts) if isinstance(contracts, (int, float)) else None,
             "strike": int(strike) / 1000, "expiry": f"20{day[:2]}-{day[2:4]}-{day[4:]}",
             "style": style, "value": row.get("market_value"), "symbol": symbol}
    stated = {"status": "OK" if past or (found and not found.get("error")) else found.get("error"), "delta": delta,
              "delta_from": "market" if delta is not None else None, "price": price, "price_from": price_from,
              "underlying": underlying.get("ticker"), "underlying_price": level, "at": at, "counted_in": counted,
              **(terms_inputs(underlying.get("ticker"), at, level, terms_read) if delta is None else {})}
    if delta is None:  # the delta its terms and prices give (MATH.md 3.1), where its market quotes none
        worked = terms_delta(right=terms["right"], style=style, strike=terms["strike"], expiry=terms["expiry"],
                             at=at, price=price, underlying_price=level, rate=stated.get("rate"),
                             dividend_yield=stated.get("dividend_yield"))
        if worked is not None:
            delta = worked["delta"]
            stated.update({"delta": delta, "delta_from": "terms", "worked": worked})
    out: dict[str, Any] = {"payoff": {"option": terms}, "exposure": None,
                           "quote": {k: v for k, v in stated.items() if v is not None}}
    if all(isinstance(v, (int, float)) for v in (contracts, per, delta, level, net_assets)) and net_assets:
        out["exposure"] = contracts * per * delta * level / net_assets
    elif row.get("shares_held") == 0:  # a position closed, kept in its processing at no contracts: it holds nothing
        out["exposure"] = 0.0
    ticker = underlying.get("ticker")
    if (flex or unlisted or past) and ((terms_read.get("underlying") or {}).get(symbol) or {}).get("to"):
        out["to"] = terms_read["underlying"][symbol]["to"]
    elif ticker and ticker.startswith("I:"):
        info = indexes.get(("values", ticker)) or {}
        out["to"] = {"index": {"values": ticker}, **({"companies": info["companies"], "basket": info["basket"]}
                                                     if info.get("companies") else {})}
    elif ticker:
        company = listed_by_ticker(ticker, classes.get(ticker))
        if company:
            out["to"] = {"company": company}
    return out


def option_underlying(root: str, kind: str | None, listing: Any, index: Any) -> dict | None:
    """What an option's underlying is, where Massive's record of the contract (its `underlying_ticker`) or the OCC's
    symbol (a FLEX contract's root, its style digit left off) names it, read in Massive's own references as of the
    as-of, `listing` the reference of the root as a ticker and `index` that of `I:{root}`: an index, its values read
    under `I:{root}`, where Massive gives an index of that symbol (market `indices`); a US listing (market `stocks`),
    priced by its ticker, and its share class by the FIGI Massive's reference gives it then, which resolution confirms
    as any holding's (listed_by_ticker), `company` none where the reference gives no FIGI (read live: MDT's, LIN's,
    ACN's, NXPI's give none; 87 of the 581 common stocks' references read in 46j) and nothing else confirms it. The contract's own record says which where it has one (`kind`, the fourth letter of its CFI
    code, ISO 10962: I for an index, any other an instrument listed in its own right; read live, SPX's and VIX's
    contracts are OCEICS, SMCI's and SPY's OCASPS). A FLEX contract, which has none, is the one its root names in one
    of the two alone; one named in both, or in neither, refers to nothing here. Read so, an index option's underlying
    is its index whatever another identifier service calls it (RUT, which OpenFIGI names RTY), and a share's is the
    share even where some index carries its ticker (SMCI), at the as-of even for a listing since gone (WBA)."""
    found = (index or {}).get("results") if answered(index) else None
    held = (listing or {}).get("results") if answered(listing) else None
    is_index = isinstance(found, dict) and found.get("market") == "indices"
    is_listed = isinstance(held, dict) and held.get("market") == "stocks"
    if kind == "I":
        pick = "index" if is_index else None
    elif kind:
        pick = "listing" if is_listed else None
    else:
        pick = "index" if is_index and not is_listed else "listing" if is_listed and not is_index else None
    if pick == "index":
        return {"ticker": f"I:{root}"}
    if pick == "listing":
        figi = held.get("share_class_figi")
        return {"ticker": root, "company": listed_by_ticker(root, [figi]) if figi else None}
    return None


def flex_underlying(symbol: str) -> str | None:
    """A FLEX contract's underlying's symbol, its root less the digit before it (FLEX_STYLE); None for any other."""
    found = OCC.fullmatch(symbol[2:]) if symbol.startswith("O:") else None
    root = found.group(1) if found else ""
    return root[1:] if len(root) > 1 and root[0] in FLEX_STYLE else None


def close_moment(day: Any) -> str | None:
    """A session's close in New York, 16:00, as an ISO moment: when a fund's holdings of that date are valued."""
    if not day:
        return None
    return datetime.fromisoformat(f"{str(day)[:10]}T16:00:00").replace(tzinfo=ZoneInfo("America/New_York")) \
        .astimezone(timezone.utc).isoformat(timespec="seconds")


def terms_inputs(underlying: str | None, at: str | None, level: Any, terms_read: dict) -> dict:
    """What a delta is worked out from beside the contract's terms and prices (harness.derive.options): `rate`, the
    Treasury's par yield curve on the latest day on or before `at`'s date, `[[years, yield]]`; and `dividend_yield`,
    the cash its payer paid over the 365 days to `at` and the payer's price then, the payer being the underlying, or
    for an index the fund whose holdings are its companies (`basket`). None of either where it was not read."""
    if not at:
        return {}
    day = at[:10]
    curves = [c for c in terms_read.get("curves") or [] if c["date"] <= day]
    payer = (terms_read.get("payer") or {}).get(underlying or "")
    out: dict[str, Any] = {"rate": curves[-1]["curve"] if curves else None, "rate_date": curves[-1]["date"]
                           if curves else None, "dividend_yield": None}
    if payer:
        since = (datetime.fromisoformat(day) - timedelta(days=365)).date().isoformat()
        paid = sum(a for d, a in ((terms_read.get("paid") or {}).get(payer) or {}).items() if since < d <= day)
        if payer == underlying:
            price = level
        else:
            closes = (terms_read.get("closes") or {}).get(payer) or {}
            before = [d for d in closes if d <= day]
            price = closes[max(before)] if before else None
        if isinstance(price, (int, float)) and price > 0:
            out["dividend_yield"] = {"paid": paid, "price": price, "payer": payer}
    return out


def option_price(quote: dict) -> tuple[float | None, str | None, str | None]:
    """A listed contract's price in its market's snapshot, per unit of its underlying, where it came from, and when it
    stood: the midpoint of its best bid and ask where both stand (`quote`), else its session's last trade (`trade`;
    read live, an index option's quote reads 0 bid and 0 ask once its market has closed)."""
    last = quote.get("last_quote") or {}
    if all(isinstance(last.get(k), (int, float)) and last[k] > 0 for k in ("bid", "ask")):
        return (last["bid"] + last["ask"]) / 2, "quote", _nanos(last.get("last_updated"))
    trade = quote.get("last_trade") or {}
    if isinstance(trade.get("price"), (int, float)) and trade["price"] > 0:
        return trade["price"], "trade", _nanos(trade.get("sip_timestamp"))
    return None, None, None


def standing_price(found: dict | None) -> tuple[float | None, str | None, str | None]:
    """A contract's price standing at a moment (Massive.option_at), per unit of its underlying, where it came from, and
    the moment it is of: the midpoint of the NBBO in force then where both sides stand, at the moment asked; else its
    last trade that day before it, at the trade's moment."""
    found = found or {}
    bid, ask = found.get("bid"), found.get("ask")
    if all(isinstance(v, (int, float)) and v > 0 for v in (bid, ask)):
        return (bid + ask) / 2, "quote", found.get("at")
    if isinstance(found.get("trade"), (int, float)) and found["trade"] > 0:
        return found["trade"], "trade", found.get("traded")
    return None, None, None


def subject_day(subject: str) -> str:
    """The day a dated subject names, `{what}@{date}` or `{what}@{moment}`, a moment's day in New York."""
    when = subject.rpartition("@")[2]
    if "T" not in when:
        return when
    return datetime.fromisoformat(when).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def price_at(minutes: dict | None, ticker: str | None, moment: str | None) -> float | None:
    """An underlying's price at a moment: the close of the last of its minute bars on the moment's day in New York to
    end by it (Massive.minutes, `{ticker: {day: [[start ms, close]]}}`); None where none was read."""
    if not ticker or not moment:
        return None
    bars = ((minutes or {}).get(ticker) or {}).get(subject_day(f"@{moment}")) or []
    end = datetime.fromisoformat(moment).timestamp() * 1000
    ended = [c for t, c in bars if t + 60_000 <= end]
    return ended[-1] if ended else None


def _nanos(stamp: Any) -> str | None:
    """A moment Massive gives in Unix nanoseconds, in ISO 8601 UTC."""
    if not isinstance(stamp, (int, float)) or not stamp:
        return None
    return datetime.fromtimestamp(stamp / 1e9, timezone.utc).isoformat(timespec="seconds")


def held_contracts(row: dict, per: Any, standing: dict | None) -> tuple[float | None, str | None]:
    """The contracts a fund's option row holds, signed, and in what ETF Global counted them: its `shares_held`, which
    is the contracts for most issuers and the underlying's shares for some (read live: Fidelity's FHEQ, whose rows'
    market values are a hundredth of their count of contracts at the market's price). The row's own market value says
    which, set against the contract's price on the same date, the one standing at its holdings' close
    (standing_price, Massive.option_at): counted as contracts at that price and the shares each controls, the row's
    value comes to about the count's, or to about a hundredth of it, two readings `per` apart; below the geometric
    middle of the two, one in the square root of `per`, the count is of shares. Where the row gives no value or no
    price stood that day, the count as it is."""
    held = row.get("shares_held")
    if not isinstance(held, (int, float)) or not held:
        return None, None
    price = standing_price(standing)[0]
    value = row.get("market_value")
    if not all(isinstance(v, (int, float)) and v for v in (per, price, value)):
        return held, "contracts"
    if abs(value) / (abs(held) * per * price) < 1 / math.sqrt(per):
        return held / per, "shares"
    return held, "contracts"


def _ids(found: dict) -> set[tuple[str, str]]:
    """What identifies a security across sources, each as (kind, value): its ISIN, CUSIP, FIGI, and US ticker."""
    out = set()
    for key, kind in (("isin", "isin"), ("cusip", "cusip"), ("figi", "figi"), ("constituent_ticker", "ticker")):
        value = str(found.get(key) or "").strip().upper()
        if value:
            out.add((kind, value))
    return out


def category(row: dict) -> str | None:
    """A row's derivative category, by its fund's report's own code for it (Form N-PORT's): the row's own, where it is
    the report's row; for a leg its price names, the category of the position the report states it as (reported_as)."""
    return (row.get("derivative") or {}).get("category") or (row.get("reported_as") or {}).get("category")


def _one_reference(contracts: list[dict]) -> tuple[dict, set[tuple[str, str]]] | None:
    """The one thing a fund's contracts all refer to, as their rows give it (`to`), with what identifies that thing's
    shares: one company, by the identifiers its contracts give it, each sharing one with another's; or one index, by
    the identifier OpenFIGI names it under or else the report's own, its shares those of its companies and of the funds
    whose returns follow it, where they are established. A contract on a fund whose returns follow the index refers to
    the index (QLD's swaps on QQQ). None where they refer to more than one thing, or to an index nothing identifies,
    or give no reference."""
    first = next((r.get("to") for r in contracts if "index" in (r.get("to") or {})), None) \
        or contracts[0].get("to") or {}
    if "index" in first:
        key, followers = _index_key(first["index"]), set(first.get("followers") or [])
        if key is None or any(_index_key((r.get("to") or {}).get("index") or {}) != key
                              and str(((r.get("to") or {}).get("company") or {}).get("constituent_ticker") or "")
                              .upper() not in followers for r in contracts):
            return None
        own = set().union(*(_ids(c) for c in first.get("companies") or []))
        return first, own | {("ticker", t) for t in followers}
    if "company" in first:
        own = _ids(first["company"])
        for r in contracts[1:]:
            theirs = _ids((r.get("to") or {}).get("company") or {})
            if not theirs & own:
                return None
            own |= theirs
        return first, own
    return None


def _index_key(index: dict) -> tuple[str, str] | None:
    """What an index a contract refers to is, for telling two apart: the FIGI OpenFIGI names it by, else the
    identifier its report gives it."""
    if index.get("figi"):
        return ("figi", str(index["figi"]))
    identifier = str(index.get("identifier") or "").strip().upper()
    return ("identifier", identifier) if identifier else None


def cash_and_one_reference(rows: list[dict], readings: dict[str, dict] | None = None) -> dict | None:
    """Whether a fund's report holds only cash, contracts on one reference and that reference's shares, and
    if so the reference, as its contracts give it. Every position of such a fund other than its cash is identified by
    its own row (a contract by its symbol or its terms, a share by its identifiers), so a row of its holdings that
    names nothing and is stated in dollars is among its cash (harness.derive.assets.Kinds, `unnamed`). `rows` are the
    report's (nport_rows): each holding's kind by its code and the facts read for it, so a money market fund, a
    repurchase agreement and a Treasury bill are cash; each contract, a swap, a future or an option, with what it
    refers to. An index its report names by an identifier OpenFIGI reads as one US listing's ticker (`readings`,
    ticker_reference: TSLL's swaps on "TSLA", coded as on something other than equity) has that listing's shares as
    its own. A report that holds anything else (a forward, bonds, another company's shares, money in another
    currency) or no contract at all is no such fund."""
    contracts, held = [], []
    for r in rows:
        if r.get("leg") == "sold":
            continue  # a forward's other currency, read with the forward
        if category(r) in LINEAR or category(r) in OPTIONS:
            contracts.append(r)
        elif r.get("derivative") is not None:
            return None
        else:
            held.append(r)
    found = _one_reference(contracts) if contracts else None
    if found is None:
        return None
    first, own = found
    if "index" in first:
        identifier = str(first["index"].get("identifier") or "")
        listed = ticker_reference(identifier, (readings or {}).get(identifier)) if identifier else None
        own = own | _ids(listed or {})
    for r in held:
        if r.get("kind") == "cash":
            continue
        if r.get("kind") == "equity" and r.get("payoff") == "holding" and _ids(r) & own:
            continue  # the reference's own shares
        return None
    return first


def slope_reference(rows: list[dict]) -> dict | None:
    """The one reference a fund is measured on (MATH.md 3.1): where its swaps and futures all refer to one index whose
    companies are established, or one company, and its shares, if it holds any, are that reference's. A swap on a fund
    whose returns follow the index refers to the index. A share is the
    reference's where its identifiers are the company's, or one of the index's companies', or it is a fund whose
    returns follow the index (`followers`); the shares that are not may come to less than the floor in all, the share
    of a fund the look-through counts as negligible (portfolio.FLOOR), such as a company the index has just left. A
    fund holding an option is not measured: its slope would be the options' average delta over the window, not
    today's, and its options count at their own. Returns `{to, rows, shares, series, named_by}`: the reference as the
    rows give it, the rows the slope stands for, and the series its values are read under for an index."""
    linear = [r for r in rows if category(r) in LINEAR]  # a swap's leg its price names among them
    if not linear or any(isinstance(r.get("payoff"), dict) and "option" in r["payoff"] for r in rows):
        return None
    found = _one_reference(linear)
    if found is None:
        return None
    first, own = found
    if "index" in first and (not first["index"].get("figi") or not first.get("companies")
                             or not first["index"].get("values")):
        return None  # an index is measured on where its values are read and its companies are established
    from harness.derive.portfolio import FLOOR  # the look-through's line for a negligible part of a fund

    shares, other = [], 0.0
    for r in rows:
        if r.get("payoff") == "holding" and r.get("kind") == "equity":
            if _ids(r) & own:
                shares.append(r)
            else:
                other += abs(r.get("weight") or 0.0)
    if other >= FLOOR:
        return None
    return {"to": first, "rows": [id(r) for r in linear], "shares": [id(r) for r in shares],
            "series": (first.get("index") or {}).get("values"),
            "named_by": (first.get("company") or {}).get("named_by")}


def _theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """The median of the slopes between every two sessions, and its standard error from Sen's interval for it at 95%
    (Sen, JASA 63, 1968: the ranks of the pairwise slopes the normal approximation to Kendall's statistic gives),
    halved over 1.96: robust to a few sessions on which the reference's close was struck at another moment, or a
    distribution fell, which rule a least-squares slope over 63 sessions."""
    n = len(x)
    i, j = np.triu_indices(n, 1)
    dx = x[j] - x[i]
    keep = dx != 0
    slopes = np.sort((y[j] - y[i])[keep] / dx[keep])
    m = len(slopes)
    z = 1.959963984540054
    spread = z * math.sqrt(n * (n - 1) * (2 * n + 5) / 18.0)
    lo = slopes[max(int(round((m - spread) / 2.0)) - 1, 0)]
    hi = slopes[min(int(round((m + spread) / 2.0)), m - 1)]
    return float(np.median(slopes)), float((hi - lo) / (2 * z))


def fit_slope(fund: tuple[dict[str, float], dict[str, float]], reference: tuple[dict[str, float], dict[str, float]],
              asof: str) -> dict | None:
    """A fund's slope on its reference (MATH.md 3.1): over the SESSIONS sessions to the as-of the fund closed on, each
    series' daily return as the betas take it (harness.derive.series.returns: the close, with what went ex that day
    added back), `fund` and `reference` each its closes and its dividends paid; the fund's return on the reference's,
    by Theil and Sen's median of pairwise slopes, with its standard error, and the share of the fund's variance a
    least-squares line explains (`r2`), which corroborates a reference named by its ticker. None for a fund that has
    not closed on SESSIONS sessions plus the one before them, whose report states its sizes instead."""
    days = sorted(d for d in fund[0] if d <= asof)
    if len(days) < SESSIONS + 1:
        return None
    window = days[-(SESSIONS + 1):]
    mine, theirs = series.returns(fund[0], window, fund[1]), series.returns(reference[0], window, reference[1])
    common = [d for d in window[1:] if d in mine and d in theirs]
    if len(common) < 3:
        return None
    x, y = np.array([theirs[d] for d in common]), np.array([mine[d] for d in common])
    slope, se = _theil_sen(x, y)
    xc, yc = x - x.mean(), y - y.mean()
    r2 = float((xc @ yc) ** 2 / ((xc @ xc) * (yc @ yc))) if (xc @ xc) and (yc @ yc) else 0.0
    return {"slope": slope, "se": se, "r2": r2, "sessions": len(common), "from": window[0], "to": window[-1],
            "fit": "Theil-Sen"}


CORROBORATES = 0.9  # the share of a fund's daily variance its named listing's returns explain, at least (measure),
# and its rows that state shares reproduce, at the exposures they state (established_by_price)
CONFIRMING = 10  # the sessions of a fund's own returns that confirm the listings its rows state shares of


def states_shares(row: dict, kind: str) -> bool:
    """Whether a fund's row states shares held and their value as a position on one listing other than its shares
    (MATH.md 3.1): an ETF Global row its labels make a derivative, or give no kind, with no FIGI of its own and naming no
    contract, whose shares and value are both stated, on a date. Its value per share is then the listing's close on
    that date (a swap's or a contract for difference's equity leg, as its issuer states it each day). A row whose
    shares are its value is stated in dollars, whatever it is a position on (a financing leg; a swap stated at its
    notional), and names no listing by its price."""
    if kind not in ("derivative", "other") or row.get("figi") or not row.get("effective_date") or option_symbol(row) \
            or _fold(row.get("security_type")) in WARRANT_WORDS or _fold(row.get("asset_class")) in OPTION_WORDS:
        return False
    shares, value = row.get("shares_held"), row.get("market_value")
    if not isinstance(shares, (int, float)) or not isinstance(value, (int, float)) or not shares or not value:
        return False
    return not in_dollars(row)


def price_subject(row: dict) -> str:
    """What a row that states shares is read against (harness.venues.trackers.closed_at): its date, and the prices
    its value per share can be, its value to the half cent over its shares."""
    shares, value = abs(float(row["shares_held"])), abs(float(row["market_value"]))
    return f"{str(row['effective_date'])[:10]}|{(value - HALF_CENT) / shares:.15g}|{(value + HALF_CENT) / shares:.15g}"


def reproduced(fund: tuple[dict[str, float], dict[str, float]],
               stated: list[tuple[float, tuple[dict[str, float], dict[str, float]]]], asof: str) -> float | None:
    """The share of a fund's daily variance over its last CONFIRMING sessions to the as-of that its rows reproduce at
    the exposures they state: 1 less the sum of squares of its return less each stated row's exposure times its
    listing's return, over the sum of squares of its return about its mean, each series a return as the betas take
    it (closes, with what went ex added back), on the sessions every one has. None for a fund that has not closed on
    CONFIRMING sessions and the one before them."""
    days = sorted(d for d in fund[0] if d <= asof)
    if len(days) < CONFIRMING + 1:
        return None
    window = days[-(CONFIRMING + 1):]
    mine = series.returns(fund[0], window, fund[1])
    theirs = [(w, series.returns(c, window, p)) for w, (c, p) in stated]
    common = [d for d in window[1:] if d in mine and all(d in x for _, x in theirs)]
    if len(common) < 2:
        return 0.0
    y = np.array([mine[d] for d in common])
    q = np.array([sum(w * x[d] for w, x in theirs) for d in common])
    total = float(((y - y.mean()) ** 2).sum())
    return 1.0 - float(((y - q) ** 2).sum()) / total if total else 0.0


def established_by_price(rows: list[tuple[str, list[str]]], reproduces) -> dict[str, str]:
    """The listing each of a fund's rows that state shares names (MATH.md 3.1), by the row's key, from `rows`, each its
    key and its matches: the listings whose close on its date is its value per share. `reproduces(chosen)` is what
    the fund's rows reproduce of its returns (reproduced) with each chosen row at its listing, None where the fund
    has too few sessions to say.

    A row with one match names it; a row with several names the one another of the fund's rows matches alone, the
    legs on one reference being at one close. The fund's returns then confirm them: its rows, at the exposures they
    state, reproduce at least CORROBORATES of its daily variance over its last CONFIRMING sessions. Rows left with
    the same several matches are legs on one reference, and name the one with which the fund's rows reproduce its
    returns, where exactly one does. A fund whose rows do not reproduce its returns establishes none of them: a
    match the returns do not confirm is a price that collided, or a fund whose other legs state another price. A fund
    with too few sessions takes each row's match where it is the only one.

    Measured on every ETF's holdings processed on one day, the 3,074 swap and contract-for-difference
    rows of 614 funds whose value per share met a close: the fund's returns confirmed 567 funds and 1,687 rows, each
    naming the listing its own words name; the slope's sign alone named another listing for 224 rows."""
    alone = {m[0] for _, m in rows if len(m) == 1}
    chosen = {k: m[0] for k, m in rows if len(m) == 1}
    for k, m in rows:
        anchored = [t for t in m if t in alone]
        if len(m) > 1 and len(anchored) == 1:
            chosen[k] = anchored[0]
    if reproduces(chosen) is None:
        return {k: m[0] for k, m in rows if len(m) == 1}
    legs: dict[tuple[str, ...], list[str]] = {}
    for k, m in rows:
        if k not in chosen and m:
            legs.setdefault(tuple(sorted(m)), []).append(k)
    for matches, keys in sorted(legs.items()):
        reach = [t for t in matches if (reproduces({**chosen, **{k: t for k in keys}}) or 0.0) >= CORROBORATES]
        if len(reach) == 1:
            chosen.update({k: reach[0] for k in keys})
    return chosen if chosen and (reproduces(chosen) or 0.0) >= CORROBORATES else {}


def measure(rows: list[dict], slope: dict | None) -> list[dict]:
    """A fund's rows with its exposure to its one reference measured (MATH.md 3.1): where slope_reference holds and the
    slope is fitted, one row for the reference whose `exposure` is the slope, with how it was measured (`measured`),
    and the rows it stands for, the swaps and futures on it and the fund's own shares of it, carrying no exposure,
    since the slope holds what each does, and marked `measured_by`, the measured row's name, so that a reader of the
    rows (the fund's prior, harness.derive.channels) counts them as the slope's, not as exposure no row states; each
    share keeps its weight, for what the fund holds. A reference a report
    names by its ticker is corroborated by the fit: where the listing's returns explain less than CORROBORATES of the
    fund's, the identifier is not taken as that listing, and its rows keep no company."""
    ref = slope_reference(rows)
    if ref is None or not slope:
        return rows
    if ref["named_by"] == "ticker" and slope.get("r2", 0.0) < CORROBORATES:
        return [{**r, "to": None} if id(r) in ref["rows"] else r for r in rows]
    stood = set(ref["rows"]) | set(ref["shares"])
    first = next(r for r in rows if id(r) in ref["rows"] and r.get("to") is ref["to"])
    referred = (first.get("derivative") or {}).get("reference") or {}
    name = (referred.get("index") or {}).get("name") or (referred.get("security") or {}).get("title") or \
        first.get("constituent_name") or ref["series"] or "the measured reference"
    out = [{**r, "exposure": None, "measured_by": name} if id(r) in stood else r for r in rows]
    out.append({"constituent_name": name, "kind": "derivative", "payoff": "linear", "weight": None,
                "exposure": slope["slope"], "to": ref["to"], "measured": slope,
                "derivative": {"category": "measured", "reference": referred},
                "asset_category": first.get("asset_category"), "effective_date": slope["to"],
                "report_date": slope["to"], "net_assets": None})
    return out
