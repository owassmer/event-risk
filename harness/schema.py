"""The one declaration of the store: its objects and its links.

The store validates every write against these tables, the derivation registry checks
every derivation's fields against them, the tools take their types, relations, and
fields from them, and ONTOLOGY.md's tables are rendered from them.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

VENUES = ("polymarket", "kalshi", "polymarket_us")  # the venues switched on (harness.venues): each searched and read

# A ticker as its market writes it: a US listing's ("BRK.B"), a listing abroad's symbol with its exchange's suffix,
# which can carry an ampersand ("M&M.NS", series.SYMBOL), and a pair or an index after its prefix ("C:EURUSD").
TICKER = r"[A-Z0-9][A-Z0-9.&\-]*(?::[A-Z0-9.\-]+)?"
TICKER_RE = re.compile(rf"^{TICKER}$")
METRIC_ID = r"[a-z][a-z0-9_]*"

# Field kinds.
TEXT = "text"
NUMBER = "number"
INTEGER = "integer"
DATE = "date"
DATETIME = "datetime"
BOOL = "bool"
ENUM = "enum"
REF = "ref"
TEXTS = "texts"
ANSWER = "answer"
MAPPING = "mapping"
CHANCES = "chances"  # a proposition's chances, each given other propositions or stated on its own, with its basis
FIGURE = "figure"  # a stated figure, with its basis
MOVE = "move"  # a price's move in a world, with what it is measured against and its basis (MATH.md 1.3)
WEIGHT = "weight"  # the weight today's prices put on an event: a figure or a market, with its basis
TAKEN = "taken"  # whether a market's price is taken as the probability: true, or false with its basis
WORTH = "worth"  # the value or consideration a holding has in an outcome, with its basis
EPISODE = "episode"  # a dated window supplying a world's factor co-movement

# Who brings an instance into the store.
ANALYST = "analyst"  # add() for objects, link() for links
NOTE = "note"  # note() creates a Note; link(note, "about", x) adds a target
VENUE = "venue"  # a market enters when a link or a note names it, and is kept while one does
FETCH = "fetch"  # a page fetched with fetch_source, or a document search_company read, kept once cited or linked
HOLDINGS = "holdings"  # a Name: held, looked through to, or named by a link
STORE = "store"  # cites: written by add or edit with a source_id
DERIVATION = "derivation"  # follows from structure and data
JUDGE = "judge"
ENGINE = "engine"


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    required: bool = False
    default: object = None
    choices: tuple[str, ...] = ()
    ref: tuple[str, ...] = ()
    cited: bool = False  # a fact about the world: written with a source_id
    editable: bool = True  # identity fields and the as-of are fixed
    pattern: str | None = None
    doc: str = ""


@dataclass(frozen=True)
class ObjectType:
    name: str
    prefix: str
    stated: tuple[Field, ...]
    derived: tuple[str, ...]
    creator: str
    doc: str = ""
    add_params: tuple[str, ...] = ()
    watched: tuple[str, ...] = ()  # derived only while the portfolio watches it
    recorded: tuple[str, ...] = ()  # neither stated nor derived: recorded by a tool, returned by get

    def field(self, name: str) -> Field | None:
        return next((f for f in self.stated if f.name == name), None)


@dataclass(frozen=True)
class Relation:
    name: str
    sources: tuple[str, ...]
    targets: tuple[str, ...]  # ("*",) for any id in the store
    stated: tuple[Field, ...]
    derived: tuple[str, ...]
    creator: str
    doc: str = ""

    def field(self, name: str) -> Field | None:
        return next((f for f in self.stated if f.name == name), None)


OBJECTS: dict[str, ObjectType] = {
    t.name: t
    for t in (
        ObjectType(
            "Portfolio",
            "portfolio",
            (
                Field("asof", DATE, required=True, editable=False,
                      doc="fixed when the Portfolio is added; a different as-of is a different store"),
                Field("horizon_days", INTEGER, default=180),
                Field("total", NUMBER, doc="required when holdings are given as weights"),
            ),
            ("value", "by_company", "by_industry", "by_country", "dollar_beta", "volatility"),
            ANALYST,
            "The person's holdings; one per store. add takes holdings and creates the holds links.",
            add_params=("holdings",),
        ),
        ObjectType(
            "Name",
            "name",
            (Field("ticker", TEXT, required=True, editable=False, pattern=TICKER),),
            ("type", "company", "prices", "exposure", "holds", "betas"),
            HOLDINGS,
            "A company or a fund: one the portfolio holds, holds through a fund, or an event names. A company is what "
            "its reference says: its name, what it does, and its industry; a fund whose holdings the store reads has "
            "what it holds. Its price is a Metric the store derives, metric:price:{TICKER}.",
        ),
        ObjectType(
            "Factor",
            "factor",
            (
                Field("name", TEXT, required=True, doc="in plain words, as the page shows it"),
                Field("proxy", TEXT, required=True, pattern=TICKER),
                Field("order", NUMBER, required=True),
                Field("yield", NUMBER, cited=True,
                      doc="where the factor is quoted as the yield its proxy pays: that yield, in percent"),
                Field("duration", NUMBER, cited=True,
                      doc="where the factor is quoted as the yield its proxy pays: its proxy's duration, in years, "
                          "how far its price moves, in percent, for a point of the yield"),
            ),
            ("orthogonalization", "largest_month"),
            ANALYST,
            "A force that moves prices, carried by its proxy, whose price is a Metric the store derives. A factor "
            "whose proxy is a bond fund can be quoted as the yield that fund pays: it states that yield and the "
            "proxy's duration when it is added, and a shock on it is in percentage points of the yield. Its "
            "orthogonalization is how it is built from the factors before it; its largest month is the largest move "
            "of its own part over a month of the common history.",
        ),
        ObjectType(
            "Proposition",
            "prop",
            (
                Field("title", TEXT, required=True,
                      doc="what it asserts, in plain words, as the page shows it: for an event, what happens, with a "
                          "date only where the date is part of it"),
                Field("text", TEXT, required=True),
                Field("metric", REF, ref=("Metric",)),
                Field("comparison", ENUM, required=True, choices=(">", ">=", "<", "<=", "==", "occurs"),
                      doc="one of >, >=, <, <= and ==, or occurs where it is true when something happens: the "
                          "occurrence its metric names, or what its text says where it names none"),
                Field("value", NUMBER),
                Field("upper_comparison", ENUM, choices=("<", "<="),
                      doc="a band's upper end (MATH.md 1.2): with comparison > or >= its lower end, a level on a date "
                          "or a decision on it between the two values"),
                Field("upper_value", NUMBER),
                Field("window_start", DATE),
                Field("window_end", DATE, required=True),
                Field("kind", ENUM, required=True, choices=("hitting", "level_at_date", "scheduled_decision")),
                Field("resolution_source", TEXT),
                Field("chances", CHANCES,
                      doc="how likely it is where other propositions hold, each chance with its basis: given some "
                          "propositions true (given) and others false (given_not), the share of their probability in "
                          "which this one is true as well, a figure from 0 to 1. One chance for each set of "
                          "propositions it is given. A chance stated on its own is its probability, and can follow a "
                          "market it asks: {\"value\": the chance now, \"follow\": the market, \"responsiveness\": r, "
                          "0 or more}, moving with that market from its price when you stated it, "
                          "logit(c) = logit(value) + r * (logit(market now) - logit(market then))"),
                Field("baseline", TEXT, pattern=r"yes|no|prop:[0-9]+",
                      doc="the world its event's other worlds are measured from: yes or no for a side left whole, "
                          "or a way's id"),
                Field("episode", EPISODE,
                      doc="the past window whose factor co-movement this outcome uses: start and end, with its basis"),
                Field("priced_in", WEIGHT,
                      doc="on an event or a way, the weight today's prices put on it where you hold it is not its "
                          "probability: a figure from 0 to 1, or {\"market\": id}, a market that asks it, read live; "
                          "with its basis. It stands as stated, and the outcomes with none share what it leaves as "
                          "their probabilities divide it"),
            ),
            ("range", "probability", "left_open", "outcomes", "u_E", "full_impact", "contributors",
             "by_holding", "yes", "no", "expected", "volatility", "worst_outcome", "worlds", "distribution"),
            ANALYST,
            "A predicate about the world; an event is a proposition the portfolio watches, and a way of it is a "
            "proposition one of its sides can turn out as. Its range is what the markets it takes imply through the "
            "links; its probability is what its structure's links, chances and taken markets fix, and where they leave "
            "it open, left_open is the range they leave, with the chances that would settle it.",
            watched=("outcomes", "u_E", "full_impact", "contributors", "by_holding", "yes", "no", "expected", "volatility",
                     "worst_outcome", "worlds", "distribution"),
            recorded=("searches",),
        ),
        ObjectType(
            "Market",
            "market",
            (
                Field("venue", ENUM, required=True, choices=VENUES, editable=False),
                Field("id", TEXT, required=True, editable=False),
            ),
            ("question", "rules", "resolution_source", "close_time", "strike", "strike_date", "family", "fees",
             "book", "history", "bid", "ask", "price", "depth", "risk_neutral", "open", "last_trade", "quoted_at",
             "price_date", "history_price", "settlement"),
            VENUE,
            "A venue's market, kept while a link, a note or a chance names it.",
        ),
        ObjectType(
            "Metric",
            "metric",
            (
                Field("id", TEXT, required=True, editable=False, pattern=METRIC_ID),
                Field("name", TEXT, required=True,
                      doc="the metric in plain words, as the page shows it to the person: what a reader calls the "
                          "level, never its id"),
                Field("unit", TEXT, doc="the unit a level is stated in"),
                Field("level", NUMBER, cited=True,
                      doc="where it stands today, with the page it was read from, given where it has one"),
                Field("asof", DATE, doc="the date its level stands at, as the level's page gives it"),
                Field("resolution_source", TEXT),
                Field("kind", ENUM, required=True, choices=("price", "yield", "level", "occurrence"), editable=False,
                      doc="price, yield, or level for any other level, with its unit, or occurrence for something "
                          "that happens. A different kind is a different metric"),
            ),
            (),
            ANALYST,
            "What a proposition is decided on: a level in the world, or something that happens; or a level you cite. "
            "The price of each factor's proxy and of each name is a Metric the store derives.",
        ),
        ObjectType(
            "Source",
            "src",
            (
                Field("url", TEXT, required=True, editable=False),
                Field("retrieved_at", DATETIME, editable=False),
                Field("dated", DATE, doc="the date the page speaks as of, as the page states it; null until stated "
                                         "with edit, citing the page itself"),
                Field("text", TEXT, required=True, editable=False),
            ),
            (),
            FETCH,
            "A citation: a page fetched with fetch_source, or a document of a company's record read with search_company, "
            "kept while it is cited or linked as evidence; when its last use goes, it is again a fetched page, citable "
            "under the same id.",
        ),
        ObjectType("Note", "note", (Field("text", TEXT, required=True),), (), NOTE, "The analyst's reason."),
        ObjectType(
            "Judgment",
            "jdg",
            (
                Field("kind", TEXT), Field("statement", TEXT), Field("input_hash", TEXT),
                Field("inputs", MAPPING), Field("answer", ANSWER), Field("model", TEXT), Field("raw", TEXT),
            ),
            (),
            JUDGE,
            "The judge's answer, cached on the hash of its statement and evidence.",
        ),
        ObjectType(
            "Computation",
            "cmp",
            (Field("derivation", TEXT), Field("input_hash", TEXT), Field("inputs", MAPPING), Field("output", MAPPING)),
            (),
            ENGINE,
            "A derivation's output, cached on the hash of what it read.",
        ),
    )
}

assert all(set(t.watched) <= set(t.derived) for t in OBJECTS.values())

BASED = (CHANCES, FIGURE, MOVE, WEIGHT, TAKEN, WORTH, EPISODE)  # the stated assumptions, each with its basis

RELATIONS: dict[str, Relation] = {
    r.name: r
    for r in (
        Relation(
            "holds", ("Portfolio",), ("Name", "Market", "Cash"),
            (Field("quantity", NUMBER), Field("dollars", NUMBER), Field("weight", NUMBER),
             Field("cash", NUMBER, doc="cash held, its amount in its currency"),
             Field("side", ENUM, choices=("yes", "no")),
             Field("ticker", TEXT, editable=False, pattern=TICKER,
                   doc="the security held, as the person gave it: one of its company's securities, the company's "
                       "name being the one the link runs to")),
            ("quantity", "dollars", "weight"), ANALYST,
            "one of quantity, dollars, or weight, as the person gave it, of the security its ticker names; the number "
            "of shares is fixed when given, and the dollars and weight update with the price; or, for cash, its amount "
            "in its currency",
        ),
        Relation("looks_through_to", ("Name",), ("Name",), (), ("weight", "dollars", "holdings_asof"), DERIVATION,
                 "a fund to a name it holds"),
        Relation("beta_to", ("Name",), ("Factor",), (), ("beta",), DERIVATION, "a name's beta to a factor"),
        Relation("quotes", ("Metric",), ("Factor",), (), ("levels",), ANALYST,
                 "the metric is the factor's price in the metric's own unit, as a commodity's spot price is for a fund "
                 "that holds it: its level in each world of each event moves as the factor's placed price does"),
        Relation("watches", ("Portfolio",), ("Proposition",), (), (), ANALYST, "the proposition is an event"),
        Relation("implies", ("Proposition", "Market"), ("Proposition", "Market"), (), ("terms", "check"), ANALYST,
                 "every world where A is true is one where B is: on one metric and resolution source the fields test "
                 "it, and otherwise the judge checks it. A market at either end is named for the proposition its "
                 "question and rules define, true where it resolves Yes"),
        Relation("and", ("Proposition",), ("Proposition",), (), ("terms",), ANALYST,
                 "A = B and C, from the composite to each of its two parts: A is true where both are"),
        Relation("or", ("Proposition",), ("Proposition",), (), ("terms",), ANALYST,
                 "A = B or C, from the composite to each of its two parts: A is true where either is"),
        Relation("then", ("Proposition",), ("Proposition",),
                 (Field("step", INTEGER, required=True,
                        doc="1 for the part that comes first, 2 for the part that follows"),),
                 ("terms",), ANALYST,
                 "A = B then C, from the composite to each part, one link of each step: A is true where B happens and "
                 "C follows it, so A implies each; how often C follows B is the chance of A given B"),
        Relation(
            "way", ("Proposition",), ("Proposition",),
            (Field("side", ENUM, choices=("yes", "no"),
                   doc="on a way of an event, the side it lies on, yes or no; a way's own ways lie on its side"),),
            (), ANALYST,
            "the second is one way the first can turn out: the ways under one parent exclude one another and together "
            "make it up, and a way can have ways of its own; an event's worlds are its sides and ways left whole",
        ),
        Relation(
            "asks", ("Proposition",), ("Market",),
            (Field("side", ENUM, default="yes", choices=("yes", "no"),
                   doc="the market's side that asks the proposition: its Yes, or its No"),
             Field("taken", TAKEN, default=True,
                   doc="whether the market's price is taken as the proposition's probability: true, or false with "
                       "its basis where you judge its traders' price is not that probability. A market whose price "
                       "is not taken is shown with its book and bounds nothing")),
            ("forward", "converse", "bid", "ask", "price"), ANALYST,
            "the market asks exactly the proposition: it resolves YES exactly when the proposition is true, or "
            "under the complement exactly when it is false; the judge checks it both ways",
        ),
        Relation("evidence", ("Proposition",), ("Source",),
                 (Field("passages", TEXTS, required=True,
                        doc="the words of the page that bear, each quoted word for word from its text"),),
                 (), ANALYST, "a page that bears, through the passages the link quotes from it"),
        Relation("consequence", ("Proposition",), ("Factor", "Name"),
                 (Field("shock", MOVE, required=True,
                        doc="one figure with its basis, and inside it `against`: `baseline` where none is named, "
                            "the price's whole move against the baseline; or `what_it_follows`, its residual, its "
                            "further move from the value it would otherwise have in that world. A fraction of the "
                            "price at that place, -1 at the lowest; on a factor quoted as a yield, percentage points "
                            "of that yield"),),
                 ("levels", "moves"), ANALYST,
                 "what a world does to a price: an event, or a way of it, to the factor or the name whose price it "
                 "moves, with the shock you state; the price's level in each world, placed against today's, and its "
                 "move there, a name's own part with it"),
        Relation("worth", ("Proposition",), ("Name",),
                 (Field("world", ENUM, choices=("yes", "no")),
                  Field("value", WORTH, required=True)),
                 ("levels", "implied"), ANALYST,
                 "what a share of a holding is worth in an outcome: a present dollar amount, cash and shares paid "
                 "on a date, or its unaffected price on a past date carried forward; each with its basis"),
        Relation("cites", tuple(t.name for t in OBJECTS.values() if any(f.cited or f.kind in BASED
                                                                             for f in t.stated))
                 + ("consequence", "worth", "way", "asks"),
                 ("Source",), (), (), STORE, "a stated fact, or a stated assumption, to the page it cites"),
        Relation("basis", (), ("Judgment", "Computation"), (), (), ENGINE,
                 "a derived value to the Judgment or Computation that produced it"),
        Relation("about", ("Note",), ("*",), (), (), NOTE, "a note to what it concerns"),
    )
}

# The links a proposition's structure is walked by (MATH.md 1.2): the breakdown's, and a market's `asks`, the market
# standing as the proposition it asks. What a note, a page's evidence or a consequence links is no part of it.
BREAKDOWN = ("implies", "and", "or", "then", "way", "asks")

# a proposition's definition (ONTOLOGY.md 2.1): what would make it true, its title and text and the fields code compares, which
# edit writes whole as one field, `definition`, so that a claim becomes another kind of claim in one statement
DEFINITION_FIELD = "definition"
DEFINITION = ("title", "text", "metric", "comparison", "value", "upper_comparison", "upper_value", "window_start",
              "window_end", "kind", "resolution_source")

ADDABLE = tuple(t.name for t in OBJECTS.values() if t.creator == ANALYST)
LINKABLE = tuple(r.name for r in RELATIONS.values() if r.creator in (ANALYST, NOTE))

NOT_ADDED = {
    "Name": "a Name is a company or a fund: the portfolio's holdings and what its funds hold bring in the ones it "
            "owns, and a consequence that names name:TICKER brings in one an event names",
    "Market": "a market comes from its venue and enters the store when a link or a note names it",
    "Source": "a Source is a page fetched with fetch_source, or a document search_company read, kept once it is "
              "cited or linked as evidence",
    "Note": "note(text, about_ids) creates a Note",
    "Judgment": "a Judgment is the judge's answer to a question the structure causes to be asked",
    "Computation": "a Computation is the store's: it follows from structure and data",
}

NOT_LINKED = {
    "looks_through_to": "looks_through_to links follow from the portfolio's funds and their constituents",
    "beta_to": "beta_to links follow from each name's betas to the factors",
    "cites": "a citation is written by add or edit with a source_id",
    "basis": "each derived value carries its basis; the store writes none",
}

DERIVED_LINK_RE = re.compile(r"^(looks_through_to|beta_to):([^>]+)>([^>]+)$")
PROXY_METRIC_RE = re.compile(rf"^metric:price:({TICKER})$")
NAME_ID_RE = re.compile(rf"^name:({TICKER})$")
MARKET_ID_RE = re.compile(r"^market:([a-z_]+):(\S+)$")
CASH_ID_RE = re.compile(r"^cash:([A-Z]{3})$")  # cash in a currency, by its ISO 4217 code
FAMILY_ID_RE = re.compile(r"^family:([a-z_]+):(\S+)$")  # a venue's own grouping of markets, read whole by get
METRIC_ID_RE = re.compile(rf"^metric:({METRIC_ID})$")
SEQ_ID_RE = re.compile(r"^(prop|factor|note|src|jdg|cmp|link|search|data):([0-9]+)$")


def derived_link_id(relation: str, from_id: str, to_id: str) -> str:
    return f"{relation}:{from_id}>{to_id}"


def name_id(ticker: str) -> str:
    return f"name:{ticker}"


def cash_id(currency: str) -> str:
    return f"cash:{currency.upper()}"


def proxy_metric_id(ticker: str) -> str:
    return f"metric:price:{ticker}"


class Invalid(ValueError):
    """A value that does not fit its field."""


def _date(name: str, value) -> str:
    if isinstance(value, datetime):
        raise Invalid(f"{name} is a date (YYYY-MM-DD), not a date and time")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise Invalid(f"{name} is a date (YYYY-MM-DD)")


def _datetime(name: str, value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise Invalid(f"{name}, {value!r}, is not a date and time: it is YYYY-MM-DDTHH:MM:SS, with its offset")


def _number(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Invalid(f"{name} is a finite number")
    return float(value)


SOURCE_ID_RE = re.compile(r"^src:[0-9]+$")
NOTE_ID_RE = re.compile(r"^note:[0-9]+$")
BASIS = ("source_id", "passages", "note")  # a stated assumption's basis, beside its value
BASIS_SHAPE = ('your reasoning, {"value": ..., "note": "<your reasoning>"}; a page you cite with the passages that '
               'bear, each quoted word for word, {"value": ..., "source_id": "src:N", "passages": ["<the page\'s own '
               'words>"]}; or both')
CHANCES_SHAPE = ('[{"given": ["prop:B"], "given_not": ["prop:A"], "value": 0.6, "note": "<your reasoning>"}, '
                 '{"given": [], "given_not": ["prop:B"], "value": 0.1, "note": "<your reasoning>"}]')
PROP_ID_RE = re.compile(r"^prop:[0-9]+$")


def _basis(raw: dict) -> dict:
    return {k: raw[k] for k in BASIS if k in raw}


WEIGHT_SHAPE = ('a weight from 0 to 1, or a market that asks the proposition, read live: '
                '{"value": {"market": "market:{venue}:{id}"}, ...}, with its basis')


def weight(name: str, raw) -> dict:
    """The weight today's prices put on an event or a way (MATH.md 1.5), with its basis: a figure from 0 to 1, or a market
    that asks it, read live, `{"value": {"market": id}}`, the one form a market takes, as a figure's value is."""
    if not isinstance(raw, dict):
        raw = {"value": raw}
    if set(raw) - {"value", *BASIS}:
        raise Invalid(f"{name} is {WEIGHT_SHAPE}")
    value = raw.get("value")
    if isinstance(value, dict):
        if set(value) != {"market"} or not isinstance(value["market"], str) or not MARKET_ID_RE.match(value["market"]):
            raise Invalid(f"{name} is {WEIGHT_SHAPE}")
        return {**based(name, {**raw, "value": 0.0}, _number), "value": {"market": value["market"]}}
    return based(name, raw, _weight)


def weight_market(stated) -> str | None:
    """The market a stated weight reads live, where it names one."""
    value = (stated or {}).get("value")
    return value.get("market") if isinstance(value, dict) else None


def _chance(name: str, value) -> float:
    v = _number(name, value)
    if not 0.0 <= v <= 1.0:
        raise Invalid(f"{name} is a chance, from 0 to 1")
    return v


def _weight(name: str, value) -> float:
    v = _number(name, value)
    if not 0.0 <= v <= 1.0:
        raise Invalid(f"{name} is {WEIGHT_SHAPE}")
    return v


def based(name: str, raw, number, *, needed: bool = True) -> dict:
    """A stated assumption (ONTOLOGY.md 2; TOOLS.md 5): its value with its basis, a page cited with the passages that
    bear, each quoted word for word (source_id, passages), or the reasoning (note), or both. A note is the reasoning in
    words, which the store keeps as a Note about what was stated and names here by its id, or that id, given again to
    keep it. Every assumption is stated with one; `needed` is false for a value that states none, as a market's price
    taken does."""
    if not isinstance(raw, dict):
        raw = {"value": raw}
    extra = sorted(set(raw) - {"value", *BASIS})
    if extra or "value" not in raw:
        raise Invalid(f"{name} is its value with its basis: {BASIS_SHAPE}" + (f"; not {', '.join(extra)}" if extra
                                                                              else ""))
    out = {"value": number(name, raw["value"])}
    sid, passages, note = raw.get("source_id"), raw.get("passages"), raw.get("note")
    if sid is not None or passages is not None:
        if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
            raise Invalid(f"{name}'s page is its source_id, src:N, with the passages that bear")
        if not isinstance(passages, (list, tuple)) or not passages or \
                not all(isinstance(x, str) and x.strip() for x in passages):
            raise Invalid(f"{name}'s basis is {sid} with the passages that bear: a list of the page's own words")
        out.update(source_id=sid, passages=list(passages))
    if note is not None:
        if not isinstance(note, str) or not note.strip():
            raise Invalid(f"{name}.note is your reasoning, in words")
        out["note"] = note
    if needed and "source_id" not in out and "note" not in out:
        raise Invalid(f"{name} is stated with its basis: {BASIS_SHAPE}")
    return out


# What a shock states (MATH.md 1.3; ONTOLOGY.md 2.2): the price's whole move against the baseline; or its residual, its
# further move from the value it would otherwise have in that world (a factor's from its coefficients on the factors
# before it; a company's from its betas; a fund's from its response and what its companies do beyond theirs, or from its
# terms). Each is one token, in one place.
AGAINST = "against"
BASELINE = "baseline"
FOLLOWS = "what_it_follows"
REFERENCES = (BASELINE, FOLLOWS)


def against(stated: dict) -> str:
    """What a stated move is measured from: the baseline where it names none."""
    return stated.get(AGAINST) or BASELINE


def move(name: str, raw) -> dict:
    """A shock, with its basis (MATH.md 1.3; ONTOLOGY.md 2.2): its value, and `against`, `baseline` for the whole move,
    where it names none, or `what_it_follows` for the residual."""
    if not isinstance(raw, dict):
        raw = {"value": raw}
    ref = raw.get(AGAINST)
    if ref is not None and ref not in REFERENCES:
        raise Invalid(f"{name}.{AGAINST} is `{BASELINE}`, the whole move, or `{FOLLOWS}`, the residual")
    out = based(name, {k: v for k, v in raw.items() if k != AGAINST}, _number)
    return {**out, AGAINST: ref} if ref is not None else out


def _bool(name: str, value) -> bool:
    if not isinstance(value, bool):
        raise Invalid(f"{name} is true or false")
    return value


def taken(name: str, raw) -> dict:
    """Whether a market's price is taken as the probability of what it asks (MATH.md 1.2): true, or false with its basis,
    the analyst's reason for judging its traders' price is not that probability."""
    value = raw.get("value") if isinstance(raw, dict) else raw
    if value is False and not (isinstance(raw, dict) and set(raw) & set(BASIS)):
        raise Invalid(f"{name} is false with its basis, your reason for judging that its traders' price is not the "
                      f"probability: {BASIS_SHAPE.replace('...', 'false')}")
    return based(name, raw, _bool, needed=value is False)


def assumptions(kind: str, stated: dict) -> dict[str, dict]:
    """Each assumption an object or a link of this type or relation states, by the field that states it, a
    proposition's chances each as chances[i]: its value with its basis, as stored."""
    t = OBJECTS.get(kind) or RELATIONS.get(kind)
    out: dict[str, dict] = {}
    for f in t.stated if t else ():
        v = stated.get(f.name)
        if f.kind == CHANCES:
            for i, c in enumerate(v or []):
                out[f"{f.name}[{i}]"] = c
        elif f.kind in BASED and isinstance(v, dict):
            out[f.name] = v
    return out


def field_of(key: str) -> str:
    """The stated field an assumption's key names: chances[2] is one of the chances."""
    return re.sub(r"\[[0-9]+\]$", "", key.split(".")[0])


def condition(c: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What a chance is given: the propositions true, and those false, each in order."""
    return tuple(sorted(c.get("given") or ())), tuple(sorted(c.get("given_not") or ()))


def _ids(name: str, raw, *, least: int, most: int | None) -> list[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in raw) \
            or len(set(raw)) != len(raw) or not least <= len(raw) <= (len(raw) if most is None else most):
        count = f"{least} or more" if most is None else f"{least} to {most}" if least != most else str(least)
        raise Invalid(f"{name} is a list of {count} distinct proposition ids")
    return list(raw)


READINGS = ("even", "hazard")  # MATH.md 1.2's readings of a ladder of deadlines, with {"calendar": [...]}


def reading(name: str, raw) -> str | dict:
    """A reading of a ladder of deadlines, in place of a chance's figure (MATH.md 1.2): "even", through time; "hazard", a
    constant hazard; or {"calendar": [{"date": ..., "weight": ...}]}, the dates on which it can happen, each weighted
    where some are likelier than others."""
    if raw in READINGS:
        return raw
    if isinstance(raw, dict) and set(raw) == {"calendar"} and isinstance(raw["calendar"], (list, tuple)) \
            and raw["calendar"]:
        dates = []
        for i, d in enumerate(raw["calendar"]):
            if not isinstance(d, dict) or "date" not in d or set(d) - {"date", "weight"}:
                raise Invalid(f"{name}.calendar[{i}] is {{\"date\": \"YYYY-MM-DD\"}}, with a \"weight\" where some dates "
                              f"are likelier than others")
            day = _date(f"{name}.calendar[{i}].date", d["date"])
            w = _number(f"{name}.calendar[{i}].weight", d.get("weight", 1.0))
            if w <= 0:
                raise Invalid(f"{name}.calendar[{i}].weight is above 0: a date on which it cannot happen is left out")
            dates.append({"date": day, "weight": w})
        if len({d["date"] for d in dates}) != len(dates):
            raise Invalid(f"{name}.calendar names each date once")
        return {"calendar": sorted(dates, key=lambda d: d["date"])}
    raise Invalid(f"{name}.reading is \"even\", \"hazard\", or {{\"calendar\": [{{\"date\": \"YYYY-MM-DD\", "
                  f"\"weight\": w}}]}}")


def chance(name: str, raw) -> dict:
    """One chance (MATH.md 1.2; ONTOLOGY.md 2.1): given some propositions true (given) and others false (given_not), the
    share of their probability in which the proposition is true as well, a figure from 0 to 1, with its basis. One
    stated on its own, given none, is the proposition's probability, and can follow a market the proposition asks, by a
    responsiveness of 0 or more."""
    if not isinstance(raw, dict):
        raise Invalid(f"{name} is {{\"given\": [...], \"given_not\": [...], \"value\": ...}} with its basis")
    extra = sorted(set(raw) - {"given", "given_not", "value", "follow", "responsiveness", "reading", "through",
                               *BASIS})
    if "of" in raw:
        raise Invalid(f"{name}: a chance is stated on the proposition it is the chance of, and names what it is given")
    if extra:
        raise Invalid(f"{name} has given, given_not, value and its basis ({', '.join(BASIS)}); a chance stated on its "
                      f"own can also have follow and responsiveness; not {', '.join(extra)}")
    given = _ids(f"{name}.given", raw.get("given"), least=0, most=None)
    given_not = _ids(f"{name}.given_not", raw.get("given_not"), least=0, most=None)
    for x in (*given, *given_not):
        if not (PROP_ID_RE.match(x) or MARKET_ID_RE.match(x)):
            raise Invalid(f"{name} is given {x}: a chance is given propositions, prop:N, or markets, "
                          f"market:{{venue}}:{{id}}, each for the proposition its question and rules define")
    both = sorted(set(given) & set(given_not))
    if both:
        raise Invalid(f"{name} is given {', '.join(both)} both true and false")
    if "through" in raw and "reading" not in raw:
        raise Invalid(f"{name}.through gives the date a market's question counts through where it is a rung of "
                      f"a ladder, with a reading")
    if "reading" in raw:  # a reading of a ladder in place of a figure, with its basis
        if "value" in raw or "follow" in raw or "responsiveness" in raw:
            raise Invalid(f"{name} names a reading of a ladder in place of a figure: its value is the reading's share, "
                          f"and it follows no market")
        rungs = [x for x in (*given, *given_not) if MARKET_ID_RE.match(x)]
        through = raw.get("through") or {}
        if not isinstance(through, dict) or sorted(through) != sorted(rungs):
            raise Invalid(f"{name}.through gives, for each market the reading stands between, the date its question "
                          f"counts through, as its rules give it: {{\"market:...\": \"YYYY-MM-DD\"}}" if rungs else
                          f"{name}.through is for a market that is a rung, and this reading's rungs are "
                          f"propositions, whose dates are their window_end")
        basis = based(name, {"value": 0.0, **_basis(raw)}, _chance)
        return {"given": given, "given_not": given_not, "reading": reading(f"{name}.reading", raw["reading"]),
                **({"through": {m: _date(f"{name}.through.{m}", d) for m, d in through.items()}} if rungs else {}),
                **{k: v for k, v in basis.items() if k != "value"}}
    basis = based(name, {k: raw[k] for k in ("value", *BASIS) if k in raw}, _chance)
    out = {"given": given, "given_not": given_not, **basis}
    if "follow" not in raw and "responsiveness" not in raw:
        return out
    if given or given_not:
        raise Invalid(f"{name} follows a market and is given other propositions: a chance that follows a market is "
                      f"stated on its own")
    if not isinstance(raw.get("follow"), str) or not (MARKET_ID_RE.match(raw["follow"]) or
                                                     NAME_ID_RE.match(raw["follow"])):
        raise Invalid(f"{name}.follow is a market the proposition asks, or a holding valued in its worlds, "
                      f"with responsiveness")
    if "responsiveness" not in raw:
        raise Invalid(f"{name}.responsiveness is how far it follows the market's moves, 0 or more: 0 keeps the stated "
                      f"chance, 1 keeps a constant ratio between its odds and the market's")
    r = _number(f"{name}.responsiveness", raw["responsiveness"])
    if r < 0:
        raise Invalid(f"{name}.responsiveness is 0 or more: 0 keeps the stated chance, 1 keeps a constant ratio between "
                      f"its odds and the market's")
    return {**out, "follow": raw["follow"], "responsiveness": r}


def chances(name: str, raw) -> list[dict]:
    """A proposition's chances (ONTOLOGY.md 2.1): a list, one for each set of propositions a chance is given."""
    if not isinstance(raw, (list, tuple)):
        raise Invalid(f"{name} is a list of chances, each with its basis: {CHANCES_SHAPE}")
    out = [chance(f"{name}[{i}]", c) for i, c in enumerate(raw)]
    seen: dict[tuple, int] = {}
    for i, c in enumerate(out):
        k = condition(c)
        if k in seen:
            said = f"given {' and '.join([*k[0], *(f'not {x}' for x in k[1])])}" if k[0] or k[1] else \
                "stated on their own"
            raise Invalid(f"{name}[{seen[k]}] and {name}[{i}] are both {said}: one chance is stated for each set of "
                          f"propositions it is given, and one on its own")
        seen[k] = i
    return out


def episode(name: str, raw) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"start", "end"}:
        raise Invalid(f"{name} gives start and end as dates")
    start, end = (_date(f"{name}.{k}", raw[k]) for k in ("start", "end"))
    if start >= end:
        raise Invalid(f"{name}.start precedes its end")
    return {"start": start, "end": end}


WORTH_SHAPES = ('an amount in today\'s dollars, {"amount": ...}; cash paid on a date, {"cash": ..., "currency": ..., '
                '"date": ...}, shares of another name, {"shares": {"name": "name:TICKER", "quantity": ...}}, or both; '
                'or the holding\'s price on an earlier date with the return since, as a fraction, {"date": ..., "return": ...}')


def from_percent(x: float) -> float:
    """A figure written as a percent, as the fraction it is: -3.3 is -0.033. It is divided by 100 in decimal, so the
    fraction is the one the percent was written as (1.1 is 0.011, where binary arithmetic gives 0.011000000000000001).
    A store of the schema before this one stated what an event does to a price this way (Store._admit_fractions)."""
    return float(Decimal(repr(x)).scaleb(-2))


def worth(name: str, raw) -> dict:
    """What a share of a holding is worth in a world (MATH.md 1.6; ONTOLOGY.md 2.2): an amount in today's dollars; cash
    paid on a date in its currency, shares of another name, or both; or the holding's price on an earlier date with the
    return since. The store discounts cash at its currency's government yield (3.1), so a value states none."""
    if not isinstance(raw, dict):
        raise Invalid(f"{name} is a value per share: {WORTH_SHAPES}")
    if set(raw) == {"amount"}:
        amount = _number(f"{name}.amount", raw["amount"])
        if amount < 0:
            raise Invalid(f"{name}.amount is at least zero per share")
        return {"amount": amount}
    if set(raw) == {"date", "return"}:
        change = _number(f"{name}.return", raw["return"])
        if change < -1:
            raise Invalid(f"{name}.return is a fraction of the price, at least -1")
        return {"date": _date(f"{name}.date", raw["date"]), "return": change}
    if not (set(raw) & {"cash", "shares"}) or set(raw) - {"cash", "currency", "date", "shares"}:
        raise Invalid(f"{name} is a value per share: {WORTH_SHAPES}")
    out = {}
    if "cash" in raw:
        out["cash"] = _number(f"{name}.cash", raw["cash"])
        if out["cash"] < 0:
            raise Invalid(f"{name}.cash is at least zero per share")
        out["currency"] = raw.get("currency", "USD")
        if not isinstance(out["currency"], str) or not re.fullmatch(r"[A-Z]{3}", out["currency"]):
            raise Invalid(f"{name}.currency is its three-letter currency code")
        out["date"] = _date(f"{name}.date", raw.get("date"))
    elif "currency" in raw:
        raise Invalid(f"{name}.currency is the currency of its cash")
    elif "date" in raw:
        out["date"] = _date(f"{name}.date", raw["date"])
    if "shares" in raw:
        shares = raw["shares"]
        if not isinstance(shares, dict) or set(shares) != {"name", "quantity"} or \
                not isinstance(shares["name"], str) or not NAME_ID_RE.match(shares["name"]):
            raise Invalid(f"{name}.shares names name:TICKER and quantity per share")
        quantity = _number(f"{name}.shares.quantity", shares["quantity"])
        if quantity < 0:
            raise Invalid(f"{name}.shares.quantity is at least zero per share")
        out["shares"] = {"name": shares["name"], "quantity": quantity}
    return out


def check_value(f: Field, value):
    """The value normalized to its stored form, or Invalid."""
    if value is None:
        if f.required:
            raise Invalid(f"{f.name} is required")
        return None
    k = f.kind
    if k == TEXT or k == REF:
        said = isinstance(value, str) and value.strip()
        if f.pattern and f.doc and not (said and re.fullmatch(f.pattern, value)):
            raise Invalid(f"{f.name} is {f.doc}")  # what the field is, in place of the form it failed
        if not said:
            raise Invalid(f"{f.name} is a non-empty text")
        if f.pattern and not re.fullmatch(f.pattern, value):
            raise Invalid(f"{f.name} {value!r} does not match {f.pattern}")
        return value
    if k == NUMBER:
        return _number(f.name, value)
    if k == INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise Invalid(f"{f.name} is an integer")
        return value
    if k == DATE:
        return _date(f.name, value)
    if k == DATETIME:
        return _datetime(f.name, value)
    if k == BOOL:
        if not isinstance(value, bool):
            raise Invalid(f"{f.name} is true or false")
        return value
    if k == ENUM:
        if value not in f.choices:
            raise Invalid(f"{f.name} is one of {', '.join(f.choices)}")
        return value
    if k == TEXTS:
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
            raise Invalid(f"{f.name} is a list of non-empty texts")
        if f.required and not value:
            raise Invalid(f"{f.name} is required: a list of at least one")
        return list(value)
    if k == MAPPING:
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise Invalid(f"{f.name} is an object with text keys")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise Invalid(f"{f.name} holds only JSON values: {e}") from None
        return dict(value)
    if k == CHANCES:
        return chances(f.name, value)
    if k == FIGURE:
        return based(f.name, value, _number)
    if k == MOVE:
        return move(f.name, value)
    if k == WEIGHT:
        return weight(f.name, value)
    if k == TAKEN:
        return taken(f.name, value)
    if k == WORTH:
        return based(f.name, value, worth)
    if k == EPISODE:
        return based(f.name, value, episode)
    raise AssertionError(f"unknown field kind {k}")


def check_object(type_name: str, stated: dict) -> None:
    """Rules across the stated fields of one object."""
    if type_name == "Proposition":
        rel = stated.get("comparison")
        if rel in (">", ">=", "<", "<=", "==") and (stated.get("metric") is None or stated.get("value") is None):
            raise Invalid(f"a proposition with comparison {rel} names its metric and its value")
        upper, top = stated.get("upper_comparison"), stated.get("upper_value")
        if (upper is None) != (top is None):
            raise Invalid("a band states its upper end whole: upper_comparison, < or <=, and upper_value")
        if upper is not None:
            if rel not in (">", ">="):
                raise Invalid(f"a band runs from its lower end, comparison > or >= its value, to its upper end; "
                              f"comparison {rel} is not a lower end")
            if stated.get("kind") == "hitting":
                raise Invalid("a band is a level between two thresholds on a date or at a scheduled decision: kind "
                              "level_at_date or scheduled_decision")
            if top <= stated["value"]:
                raise Invalid(f"a band's lower end is its value and its upper end its upper_value, above it: value is "
                              f"{stated['value']:g} and upper_value {top:g}")
        ws, we = stated.get("window_start"), stated.get("window_end")
        if ws and we and ws > we:
            raise Invalid(f"window_start, {ws}, is after window_end, {we}: the window runs from its start to its end")
    if type_name == "Metric":
        if stated.get("kind") == "occurrence":
            given = [f for f in ("unit", "level", "asof") if stated.get(f) is not None]
            if given:
                raise Invalid(f"a metric of kind occurrence is something that happens, which has no "
                              f"{' or '.join(given)}: a unit, a level and its date belong to a level")
        elif stated.get("unit") is None:
            raise Invalid(f"a metric of kind {stated.get('kind')} is a level, stated in its unit")
    if type_name == "Factor" and (stated.get("yield") is None) != (stated.get("duration") is None):
        raise Invalid("a factor quoted as the yield its proxy pays states that yield and its proxy's duration, each "
                      "with the page it was read from")
    if type_name == "Factor" and stated.get("duration") is not None and stated["duration"] <= 0:
        raise Invalid("duration is how far the proxy's price moves, in percent, for a point of its yield: a positive "
                      "number of years")
    if type_name == "Portfolio" and stated.get("horizon_days") is not None and stated["horizon_days"] <= 0:
        raise Invalid("horizon_days is a positive number of days")


def check_link(relation: str, stated: dict) -> None:
    """Rules across the stated fields of one link."""
    if relation == "holds":
        given = [k for k in ("quantity", "dollars", "weight", "cash") if stated.get(k) is not None]
        if len(given) != 1:
            raise Invalid("a holding gives exactly one of quantity, dollars, or weight; cash gives its amount")
    if relation == "then" and stated.get("step") not in (1, 2):
        raise Invalid("step is 1 for the part that comes first, 2 for the part that follows")
