"""A fund's holding's kind of asset, one set for every source: equity, debt, cash, derivative, commodity, currency, or
other (MATH.md 3.1, its kind of asset). Each source names its holdings' kinds in its own words, so each word is put in the
set by a table over that source's labels and codes, and where the words do not decide, by what the holding's
identifiers say.

- ETF Global (Massive's fund holdings) gives `asset_class` and `security_type` in each issuer's own words, in any case:
  a share is `Equity` or `EQUITY` (and a real-estate company's `Real Estate`); a bond `Fixed Income`, `DEBT`, or
  `Corporate Bond`; cash `Cash`, `SHORT_TERM`, `STIF`, `Cash Equivalent`, or `Cash Collateral and Margins`; a swap
  `Swap`, `SWAPS`, or `SW`; a future `Future`, `Futures`, or `FUTURES`; and so on, the table below. Where a row's asset
  class is too broad, its security type decides: a warrant or a right, `Equity` by class, is an option on a company's
  shares, a derivative; a mutual fund, `Equity` by class and `Open-End Fund` by type, is what it holds, cash where it is
  a money market fund and otherwise other. Where a row gives no asset class, its security type alone decides (a
  `Currency`, a `Forwards`), and a row that gives neither is other: nothing says what it is.
- Form N-PORT (the SEC) gives each holding's asset type as a code (Item C.4.a): common or preferred equity; debt, a
  loan, a structured note, or an asset-backed security; a short-term investment vehicle (a money market fund or a
  liquidity pool) or a repurchase agreement, cash; a derivative of any underlying; a commodity; real estate held
  directly, other; and with its issuer's type (Item C.4.b), another fund's shares, a share.

Two facts no label gives are read from their sources. A Treasury bill is cash: ETF Global gives a bill the class and
type it gives a thirty-year bond, or none, and N-PORT the code of any debt, so a holding of debt, or of no kind, whose
CUSIP the Treasury lists as a bill (harness.venues.treasury) is cash. Another fund's shares, a mutual fund's or an
exchange-traded fund's, are cash where the SEC's filings show it a money market fund (harness.venues.sec,
`money_market`): ETF Global gives a money market ETF the class of a share (read live, ProShares' IQMM, `Equity` and
`ETP`, 71% of QID), and its filings, not its listing, say what it is. Cash, and a holding of currency, are told apart by
the currency they are in: in dollars, cash; in another currency, currency. ProShares' funds hold their dollar collateral
in two rows, one `currency_traded` `USD` and one `USQ`, each `CASH OR CASH COLLATERAL` and summing with the other to the
fund's cash, so `USQ` is read as dollars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from harness.derive.series import is_crypto

CLASSES = ("equity", "debt", "cash", "derivative", "commodity", "currency", "other")
# A coin's kind: one asset, one kind, whatever its source writes of it. Every trust that holds a coin is one the SEC
# classes as a commodity trust or pool (COMMODITY_FUND_SIC), whose rows nothing labels read as a commodity; a row its
# source labels otherwise ("Alternative") reads the same once its pair is confirmed (harness.derive.rows.coin).
COIN = "commodity"

# ETF Global's asset classes, case-folded, each in the set.
ETF_GLOBAL = {
    "equity": "equity", "real estate": "equity",
    "fixed income": "debt", "fixed income-cdo": "debt", "debt": "debt", "corporate bond": "debt",
    "mortgage backed security": "debt", "collateralized mortgage obligations": "debt", "tba": "debt",
    "cash": "cash", "cash equivalent": "cash", "cash equivalents-short-term invest funds": "cash",
    "short_term": "cash", "short term security": "cash", "stif": "cash", "cash collateral and margins": "cash",
    "treasury bill": "cash",
    "swap": "derivative", "swaps": "derivative", "sw": "derivative", "future": "derivative", "futures": "derivative",
    "option": "derivative", "fx": "derivative", "forwards": "derivative",
    "currency": "currency", "currencies": "currency", "currency security": "currency", "cu": "currency",
    "commodity": "commodity",
    "alternative": "other", "mutual fund": "other", "mutual funds": "other", "other": "other",
}
# ETF Global's security types that decide a row's kind over its asset class, case-folded; a fund held by type is
# decided by whether it is a money market fund.
ETF_GLOBAL_TYPES = {"right": "derivative", "equity wrt": "derivative", "warrant": "derivative",
                    "forwards": "derivative", "currency": "currency", "delay-draw term": "debt"}
FUND_TYPE = "open-end fund"
# ETF Global's security types for another fund's shares: a mutual fund's, and an exchange-traded product's.
FUND_TYPES = (FUND_TYPE, "etp")
# Form N-PORT's asset type codes (Item C.4.a), each in the set. A row of the form's other type (`OTHER`) states what
# it is in the filer's words (`assetConditional`), read through the labels above: iShares states a holding of a foreign
# currency so, as "currency" (read live, EFA's Swiss francs and EWJ's yen).
NPORT = {
    "EC": "equity", "EP": "equity",
    "DBT": "debt", "LON": "debt", "SN": "debt",
    "ABS-MBS": "debt", "ABS-APCP": "debt", "ABS-CBDO": "debt", "ABS-O": "debt",
    "STIV": "cash", "RA": "cash",
    "DCO": "derivative", "DCR": "derivative", "DE": "derivative", "DFE": "derivative", "DIR": "derivative",
    "DO": "derivative",
    "COMM": "commodity",
    "RE": "other", "OTHER": "other",
}
# Form N-PORT's asset and issuer codes together where the asset's alone does not decide: another fund's shares, which
# the form gives as an "other" asset of a registered fund (its description, the fund's words, "Exchange traded fund"),
# are a share as ETF Global gives them, `Equity` and `ETP`.
NPORT_ISSUED = {("OTHER", "RF"): "equity"}
# The industry the SEC classes a commodity trust or pool under (SIC 6221, Commodity Contracts Brokers & Dealers), which a
# fund's own record gives (Massive's reference, the SEC's submissions): read live, every gold, silver, platinum, bitcoin,
# oil and broad commodity fund read (GLD, GLDM, IAU, SLV, PPLT, BITB, IBIT, USO, UNG, DBC), and no investment company.
# Such a fund's holdings rows often state no kind (its bullion or coin under a name alone), and what it holds is a
# commodity, so a row nothing labels in it is one.
COMMODITY_FUND_SIC = "6221"
DOLLARS = ("USD", "USQ")
BILLED = ("debt", "other")  # the kinds a CUSIP the Treasury lists as a bill makes cash: debt, or a row with no kind
_CUSIP = re.compile(r"[0-9A-Z]{9}")


def _fold(value) -> str:
    return str(value or "").strip().casefold()


def nport(row: dict) -> bool:
    """A row of a fund's Form N-PORT report, which gives its asset type's code (harness.venues.sec)."""
    return "asset_category" in row


# What identifies a security in ETF Global's rows: its ticker, its CUSIP (`us_code`), ISIN, FIGI and SEDOL
# (https://massive.com/docs/rest/partners/etf-global/constituents.md); its name identifies nothing
IDENTIFYING = ("constituent_ticker", "us_code", "isin", "figi", "sedol")
HALF_CENT = 0.005  # a value stated in dollars and cents is its shares at their close to within half a cent


def in_dollars(row: dict) -> bool:
    """Whether a row states its shares equal to its value, to the half cent: a position stated in dollars, whatever it is
    a position in, which names no listing by its price (harness.derive.rows.states_shares)."""
    shares, value = row.get("shares_held"), row.get("market_value")
    if not isinstance(shares, (int, float)) or not isinstance(value, (int, float)) or not shares or not value:
        return False
    return abs(abs(value) - abs(shares)) <= HALF_CENT


def names_nothing(row: dict) -> bool:
    """Whether a holdings row gives none of a security's identifiers (IDENTIFYING), whatever its name says."""
    return not nport(row) and not any(str(row.get(k) or "").strip() for k in IDENTIFYING)


def cusip_of(row: dict) -> str | None:
    """A holding's CUSIP: N-PORT's `cusip`, ETF Global's `us_code`, or a US ISIN's, the CUSIP it carries."""
    for value in (row.get("cusip"), row.get("us_code")):
        v = str(value or "").strip().upper()
        if _CUSIP.fullmatch(v) and set(v) != {"0"}:
            return v
    isin = str(row.get("isin") or "").strip().upper()
    return isin[2:11] if len(isin) == 12 and isin.startswith("US") else None


def isin_of(row: dict) -> str | None:
    """A holding's ISIN: its own, else the one its US CUSIP makes (ISO 6166: "US", the CUSIP, and a check digit, each
    letter its number from A=10 and the digits doubled alternately from the right, as Luhn's sum is)."""
    isin = str(row.get("isin") or "").strip().upper()
    if len(isin) == 12:
        return isin
    cusip = cusip_of(row)
    if not cusip:
        return None
    digits = "".join(str(int(c, 36)) for c in "US" + cusip)
    total = sum(sum(divmod(int(d) * (2 if i % 2 == 0 else 1), 10)) for i, d in enumerate(reversed(digits)))
    return f"US{cusip}{(10 - total % 10) % 10}"


def labelled(row: dict) -> str:
    """A row's kind as its source's words and codes give it, before the facts read for bills and money market funds:
    a mutual fund held reads as other."""
    if nport(row):
        code = str(row.get("asset_category") or "").strip().upper()
        found = NPORT_ISSUED.get((code, str(row.get("issuer_category") or "").strip().upper())) or \
            NPORT.get(code, "other")
        if code == "OTHER" and found == "other":  # the form's other, with the filer's description of it as its words
            found = ETF_GLOBAL.get(_fold(row.get("asset_class")), "other")  # iShares' "currency" for a currency held
    else:
        kind, given = _fold(row.get("security_type")), _fold(row.get("asset_class"))
        found = ETF_GLOBAL_TYPES.get(kind) or ("other" if kind == FUND_TYPE else ETF_GLOBAL.get(given, "other"))
    if found in ("cash", "currency"):
        currency = str(row.get("currency_traded") or row.get("currency") or "").strip().upper()
        return "currency" if currency and currency not in DOLLARS else "cash"
    return found


def held_fund(row: dict) -> str | None:
    """The ticker of another fund a fund holds, a mutual fund or an exchange-traded one, which is cash where it is a
    money market fund: ETF Global's row of a fund's type, or Form N-PORT's of another registered fund's shares."""
    if nport(row):
        code = (str(row.get("asset_category") or "").strip().upper(),
                str(row.get("issuer_category") or "").strip().upper())
        if code not in NPORT_ISSUED:
            return None
    elif _fold(row.get("security_type")) not in FUND_TYPES:
        return None
    return str(row.get("constituent_ticker") or "").strip().upper() or None


@dataclass(frozen=True)
class Kinds:
    """Each holding's kind, with the bills the Treasury lists, by CUSIP; the funds held, mutual or exchange-traded,
    the SEC's filings show to be money market funds, by ticker; and for a row its words leave with no kind, the code
    its fund's own Form N-PORT report gives the same security, by CUSIP or ISIN, where that report is read, and else
    OpenFIGI's reading of its CUSIP (`sectors`), which names commercial paper and certificates of deposit as money
    market instruments; and else, for a row its source labels as nothing, the kind its fund's own record gives such
    rows: `unnamed` for one that also names nothing and states its shares equal to its value (cash, in a fund whose
    latest report holds only cash, contracts on one reference and that reference's shares), and
    `unlabelled` for any other (a commodity, in a fund the SEC classes as a commodity trust or pool)."""
    bills: frozenset[str] = field(default_factory=frozenset)
    money_market: frozenset[str] = field(default_factory=frozenset)
    codes: dict[str, str] = field(default_factory=dict)
    sectors: dict[str, str] = field(default_factory=dict)
    unlabelled: str = "other"  # the kind of a row its source labels as nothing: a commodity fund's is a commodity
    unnamed: str | None = None  # the kind of such a row that names nothing and is stated in dollars, where its fund's
    # report says (harness.derive.rows.cash_and_one_reference): cash
    # each row's kind once found, by the row itself, which it keeps so that no other row takes its id: a look-through
    # asks a row's kind about ten times, and nothing in a row or in these facts changes while one compute reads them
    _found: dict = field(default_factory=dict, init=False, compare=False, hash=False, repr=False)  # replace: anew

    def of(self, row: dict) -> str:
        held = self._found.get(id(row))
        if held is not None and held[0] is row:
            return held[1]
        kind = self._of(row)
        self._found[id(row)] = (row, kind)
        return kind

    def _of(self, row: dict) -> str:
        if row.get("payoff") == "holding" and is_crypto(str((row.get("to") or {}).get("pair") or "")):
            return COIN  # a coin, its pair confirmed by its quotes, whatever its source's label
        if isinstance(row.get("payoff"), dict) and "option" in row["payoff"]:
            return "derivative"  # a contract the look-through read from its terms (rows), whatever its source's label
        if (row.get("reported_as") or {}).get("kind"):
            return row["reported_as"]["kind"]  # a position its fund's report states, by the report's own code (rows)
        found = labelled(row)
        if found in BILLED and cusip_of(row) in self.bills:
            return "cash"
        fund = held_fund(row)
        if fund is not None and fund in self.money_market:
            return "cash"
        if found == "other":
            for key in (cusip_of(row), row.get("isin")):
                if key and key in self.codes:
                    return self.codes[key]
            if cusip_of(row) in self.sectors:
                return self.sectors[cusip_of(row)]
            if not nport(row) and not _fold(row.get("asset_class")) and not _fold(row.get("security_type")):
                if self.unnamed and names_nothing(row) and in_dollars(row):
                    return self.unnamed
                return self.unlabelled
        return found


# OpenFIGI's reading of a security (https://www.openfigi.com/api/documentation): its `securityType2` where it names a
# derivative, else its `marketSector`, each in the set. A share's sector is not read: a share resolves to its company
# by its identifiers (harness.derive.identity), not by OpenFIGI's word.
FIGI_TYPES = {"Future": "derivative", "Option": "derivative", "Warrant": "derivative", "Swap": "derivative"}
FIGI_SECTORS = {"M-Mkt": "cash", "Govt": "debt", "Corp": "debt", "Muni": "debt", "Mtge": "debt", "Curncy": "currency",
                "Comdty": "commodity"}


def figi_kind(sectors: Iterable[dict]) -> str | None:
    """A security's kind from what OpenFIGI gives of it, where every listing it gives agrees; None where it gives
    none, or where they disagree."""
    found = {FIGI_TYPES.get(s.get("securityType2") or "") or FIGI_SECTORS.get(s.get("marketSector") or "")
             for s in sectors if isinstance(s, dict)}
    return found.pop() if len(found) == 1 and None not in found else None


def unclassed(rows: Iterable[dict], kinds: Kinds) -> list[str]:
    """The CUSIPs of the rows whose kind nothing read so far decides, which OpenFIGI's reading of them can."""
    return sorted({c for r in rows if kinds.of(r) == "other" and (c := cusip_of(r))})


def to_read(rows: Iterable[dict]) -> tuple[bool, list[str]]:
    """What deciding these rows' kinds reads: the Treasury's bills, where a row of debt, or of no kind, has a CUSIP,
    and the SEC's filings of each other fund held by ticker, mutual or exchange-traded."""
    rows = list(rows)
    bills = any(labelled(r) in BILLED and cusip_of(r) for r in rows)
    funds = sorted({t for t in (held_fund(r) for r in rows) if t})
    return bills, funds
