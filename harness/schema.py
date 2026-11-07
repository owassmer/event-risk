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

VENUES = ("polymarket", "kalshi", "hyperliquid")

TICKER = r"[A-Z0-9][A-Z0-9.\-]*(?::[A-Z0-9.\-]+)?"
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
CONVERSION = "conversion"
CHANCE = "chance"  # a stated chance, with its basis
FIGURE = "figure"  # a stated figure, with its basis
DERIVATION_FIELD = "derivation"  # the propositions a point reads and the conditional probabilities its rule needs

# Who brings an instance into the store.
ANALYST = "analyst"  # add() for objects, link() for links
NOTE = "note"  # note() creates a Note; link(note, "about", x) adds a target
VENUE = "venue"  # a market enters when a link or a note names it, and is kept while one does
FETCH = "fetch"  # a page fetched with fetch_source, or a document search_company read, kept once cited or linked
HOLDINGS = "holdings"  # a Name: held, looked through to, or named by a link
STORE = "store"  # sourced_by: written by add or edit with a source_id
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
    pointed: tuple[str, ...] = ()  # derived while the portfolio watches it or a derivation reads it
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
            ("V", "g"),
            ANALYST,
            "The person's holdings; one per store. add takes holdings and creates the holds links; "
            "the reserved ticker CASH has price 1 and zero beta.",
            add_params=("holdings",),
        ),
        ObjectType(
            "Name",
            "name",
            (Field("ticker", TEXT, required=True, editable=False, pattern=TICKER),),
            ("type", "sic", "company", "prices", "e", "holds", "b"),
            HOLDINGS,
            "A ticker: held, looked through to, or named by a link. Its company is what it is, from its reference: "
            "its name, what it does, and its industry; a fund whose holdings the store reads has what it holds. A "
            "held or looked-through name's price is a traded Metric, metric:price:{TICKER}, tracking it when its e "
            "is not zero.",
        ),
        ObjectType(
            "Channel",
            "channel",
            (
                Field("name", TEXT, required=True, doc="in plain words, as the page shows it"),
                Field("proxy_ticker", TEXT, required=True, pattern=TICKER),
                Field("order", NUMBER, required=True),
            ),
            ("orthogonalization",),
            ANALYST,
            "A factor on the page: a channel of the orthogonalized basis; its proxy's price is a traded Metric "
            "tracking it. Its orthogonalization carries M_c, its largest one-month move.",
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
                Field("relation", ENUM, required=True, choices=(">", ">=", "<", "<=", "==", "occurs")),
                Field("value", NUMBER),
                Field("window_start", DATE),
                Field("window_end", DATE, required=True),
                Field("kind", ENUM, required=True, choices=("hitting", "level_at_date", "scheduled_decision")),
                Field("resolution_source", TEXT),
                Field("derivation", DERIVATION_FIELD,
                      doc="the propositions the point reads and the conditional probabilities its rule needs, each "
                          "with its basis (MATH.md, the point)"),
                Field("chance", CHANCE, doc="the probability you state, its point when no market asks it and "
                                            "no derivation gives one, with its basis"),
            ),
            ("lo", "hi", "coverage", "p", "u_E", "primary_channel", "D", "by_name", "by_holding",
             "pnl_yes", "pnl_no", "emove", "worst"),
            ANALYST,
            "A predicate about the world; an event is a proposition the portfolio watches.",
            watched=("u_E", "primary_channel", "D", "by_name", "by_holding", "pnl_yes", "pnl_no", "emove", "worst"),
            pointed=("p",),
            recorded=("searches",),
        ),
        ObjectType(
            "Market",
            "market",
            (
                Field("venue", ENUM, required=True, choices=VENUES, editable=False),
                Field("id", TEXT, required=True, editable=False),
            ),
            ("question", "rules", "resolution_source", "close_time", "tags", "categories", "series",
             "yes_token", "book", "history", "bid", "ask", "price", "depth", "open"),
            VENUE,
            "A venue's market, kept while a link or a note uses it.",
        ),
        ObjectType(
            "Metric",
            "metric",
            (
                Field("id", TEXT, required=True, editable=False, pattern=METRIC_ID),
                Field("name", TEXT, required=True,
                      doc="the metric in plain words, as the page shows it to the person: what a reader calls the "
                          "level, never its id"),
                Field("unit", TEXT, required=True),
                Field("current_value", NUMBER, cited=True),
                Field("asof", DATE, cited=True),
                Field("resolution_source", TEXT),
                Field("traded", BOOL, default=False),
            ),
            (),
            ANALYST,
            "A level in the world, added with its current level from a page; the price of each channel's proxy "
            "and of each name is a derived, traded Metric.",
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

assert all(set(t.watched) | set(t.pointed) <= set(t.derived) for t in OBJECTS.values())

MEANINGS = ("gap", "level_if_happens", "level_if_not")  # what a consequence's figure is
BASED = (CHANCE, FIGURE, DERIVATION_FIELD)  # the stated assumptions, each with its basis

RELATIONS: dict[str, Relation] = {
    r.name: r
    for r in (
        Relation(
            "holds", ("Portfolio",), ("Name",),
            (Field("quantity", NUMBER), Field("dollars", NUMBER), Field("weight", NUMBER)),
            ("quantity", "dollars", "weight"), ANALYST,
            "one of quantity, dollars, or weight, as the person gave it; the number of shares is fixed when given, "
            "and the dollars and weight update with the price",
        ),
        Relation("looks_through_to", ("Name",), ("Name",), (), ("weight", "dollars", "holdings_asof"), DERIVATION,
                 "a fund to a name it holds"),
        Relation("loads_on", ("Name",), ("Channel",), (), ("beta",), DERIVATION, "a name's beta on a channel"),
        Relation("tracks", ("Metric",), ("Channel", "Name"), (Field("conversion", CONVERSION, required=True),), (),
                 ANALYST, "a metric whose move is the channel's or the name's: price divides the shock by the level; "
                          "yield(duration) multiplies it by -duration / 100"),
        Relation("watches", ("Portfolio",), ("Proposition",), (), (), ANALYST, "the proposition is an event"),
        Relation("implies", ("Proposition",), ("Proposition",), (), ("by_fields", "check", "bound"), ANALYST,
                 "every world where A is true is one where B is: it holds by the fields where they decide it, and "
                 "otherwise the judge checks it; it bounds A from above and B from below"),
        Relation("and", ("Proposition",), ("Proposition",), (), ("bound",), ANALYST,
                 "A = B and C, from the composite to each of its two components"),
        Relation("or", ("Proposition",), ("Proposition",), (), ("bound",), ANALYST,
                 "A = B or C, from the composite to each of its two components"),
        Relation("then", ("Proposition",), ("Proposition",),
                 (Field("step", INTEGER, required=True,
                        doc="1 for B, the part that comes first, or 2 for C, the part that follows it"),),
                 ("bound",), ANALYST,
                 "A = B then C, from the composite to each component; one link of each step per composite"),
        Relation(
            "quoted_by", ("Proposition",), ("Market",),
            (Field("transform", ENUM, default="identity", choices=("identity", "complement")),),
            ("forward", "converse", "bid", "ask", "price", "bound"), ANALYST,
            "the market asks exactly the proposition: it resolves YES exactly when the proposition is true, or "
            "under the complement exactly when it is false; the judge checks it both ways",
        ),
        Relation("evidence", ("Proposition",), ("Source",),
                 (Field("passages", TEXTS, required=True,
                        doc="the words of the page that bear, each quoted word for word from its text"),),
                 (), ANALYST, "a page that bears, through the passages the link quotes from it"),
        Relation("consequence", ("Proposition",), ("Metric",),
                 (Field("value", FIGURE, required=True,
                        doc="the figure, in the metric's unit, with its basis"),
                  Field("meaning", ENUM, required=True, choices=MEANINGS,
                        doc="gap: the shock, the gap between the worlds where the event happens and where it does "
                            "not at the end of its window; level_if_happens or level_if_not: the metric's level then, "
                            "in that world, for a traded metric")),
                 ("gap", "L_yes", "L_no", "sets"), ANALYST,
                 "the event to a metric it moves, which tracks a channel or a name: one figure the analyst states, "
                 "converted to the shock, and for a traded metric the two worlds' levels placed around today's"),
        Relation("sourced_by", tuple(t.name for t in OBJECTS.values() if any(f.cited or f.kind in BASED
                                                                             for f in t.stated)) + ("consequence",),
                 ("Source",), (), (), STORE, "a stated fact, or a stated assumption, to the page it cites"),
        Relation("basis", (), ("Judgment", "Computation"), (), (), ENGINE,
                 "a derived value to the Judgment or Computation that produced it"),
        Relation("about", ("Note",), ("*",), (), (), NOTE, "a note to what it concerns"),
    )
}

ADDABLE = tuple(t.name for t in OBJECTS.values() if t.creator == ANALYST)
LINKABLE = tuple(r.name for r in RELATIONS.values() if r.creator in (ANALYST, NOTE))

NOT_ADDED = {
    "Name": "a Name is a ticker the portfolio holds or looks through to: hold it with the Portfolio's "
            "holdings or link(portfolio, holds, name:TICKER)",
    "Market": "a market comes from its venue and enters the store when a link or a note names it",
    "Source": "a Source is a page fetched with fetch_source, or a document search_company read, kept once it is "
              "cited or linked as evidence",
    "Note": "note(text, about_ids) creates a Note",
    "Judgment": "a Judgment is the judge's answer to a question the structure causes to be asked",
    "Computation": "a Computation is the store's: it follows from structure and data",
}

NOT_LINKED = {
    "looks_through_to": "looks_through_to links follow from the portfolio's funds and their constituents",
    "loads_on": "loads_on links follow from each name's betas on the channels",
    "sourced_by": "a citation is written by add or edit with a source_id",
    "basis": "each derived value carries its basis; the store writes none",
}

DERIVED_LINK_RE = re.compile(r"^(looks_through_to|loads_on|tracks):([^>]+)>([^>]+)$")
PROXY_METRIC_RE = re.compile(rf"^metric:price:({TICKER})$")
NAME_ID_RE = re.compile(rf"^name:({TICKER})$")
MARKET_ID_RE = re.compile(r"^market:([a-z]+):(\S+)$")
METRIC_ID_RE = re.compile(rf"^metric:({METRIC_ID})$")
SEQ_ID_RE = re.compile(r"^(prop|channel|note|src|jdg|cmp|link|search|data):([0-9]+)$")


def derived_link_id(relation: str, from_id: str, to_id: str) -> str:
    return f"{relation}:{from_id}>{to_id}"


def name_id(ticker: str) -> str:
    return f"name:{ticker}"


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
BASIS_SHAPE = '{"value": ..., "source_id": "src:N", "passages": ["<the words that bear, quoted from the page>"]}'
DERIVATION_SHAPE = ('{"reads": ["prop:B"], "chances": [{"of": "prop:E", "given": ["prop:B"], "given_not": [], '
                    '"value": 0.6, "source_id": "src:N", "passages": ["..."]}]}')


def _chance(name: str, value) -> float:
    v = _number(name, value)
    if not 0.0 <= v <= 1.0:
        raise Invalid(f"{name} is a chance, from 0 to 1")
    return v


def based(name: str, raw, number) -> dict:
    """A stated number with its basis: the number alone, its basis then a note about it, or
    {value, source_id, passages}, a page cited with the passages that bear, quoted word for word."""
    if not isinstance(raw, dict):
        return {"value": number(name, raw)}
    extra = sorted(set(raw) - {"value", "source_id", "passages"})
    if extra or "value" not in raw:
        raise Invalid(f"{name} is a number, or {BASIS_SHAPE}" + (f"; not {', '.join(extra)}" if extra else ""))
    out = {"value": number(name, raw["value"])}
    sid, passages = raw.get("source_id"), raw.get("passages")
    if sid is None and not passages:
        return out
    if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
        raise Invalid(f"{name}'s basis is a page: its source_id, src:N, with the passages that bear")
    if not isinstance(passages, (list, tuple)) or not passages or \
            not all(isinstance(x, str) and x.strip() for x in passages):
        raise Invalid(f"{name}'s basis is {sid} with the passages that bear: a list of the page's own words")
    return {**out, "source_id": sid, "passages": list(passages)}


def _ids(name: str, raw, *, least: int, most: int) -> list[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in raw) \
            or len(set(raw)) != len(raw) or not least <= len(raw) <= most:
        count = f"{least} to {most}" if least != most else str(least)
        raise Invalid(f"{name} is a list of {count} distinct proposition ids")
    return list(raw)


def derivation(name: str, raw) -> dict:
    """The derivation's shape: the propositions the point reads, and each conditional chance its rule needs, what it
    is the chance of and what it is given, with its basis. Which rule of the point it is follows from these and the
    links between the propositions, and the store decides that."""
    if not isinstance(raw, dict) or sorted(raw) != ["chances", "reads"]:
        raise Invalid(f"{name} is {DERIVATION_SHAPE}")
    reads = _ids(f"{name}.reads", raw["reads"], least=1, most=2)
    chances = raw["chances"]
    if not isinstance(chances, (list, tuple)) or not 1 <= len(chances) <= 2:
        raise Invalid(f"{name}.chances is a list of one or two conditional chances")
    out = []
    for i, c in enumerate(chances):
        where = f"{name}.chances[{i}]"
        if not isinstance(c, dict):
            raise Invalid(f"{where} is {{\"of\", \"given\", \"given_not\", \"value\", \"source_id\", \"passages\"}}")
        extra = sorted(set(c) - {"of", "given", "given_not", "value", "source_id", "passages"})
        if extra:
            raise Invalid(f"{where} has of, given, given_not, value, source_id, and passages; not {', '.join(extra)}")
        if c.get("of") is not None and (not isinstance(c["of"], str) or not c["of"].strip()):
            raise Invalid(f"{where}.of is the proposition it is the chance of; omitted, the proposition itself")
        given = _ids(f"{where}.given", c.get("given"), least=0, most=2)
        given_not = _ids(f"{where}.given_not", c.get("given_not"), least=0, most=2)
        if not given and not given_not:
            raise Invalid(f"{where} is a conditional chance: it is given one proposition or more, true (given) or "
                          f"false (given_not)")
        basis = based(where, {k: c[k] for k in ("value", "source_id", "passages") if k in c}, _chance)
        out.append({"of": c.get("of"), "given": given, "given_not": given_not, **basis})
    return {"reads": reads, "chances": out}


def check_value(f: Field, value):
    """The value normalized to its stored form, or Invalid."""
    if value is None:
        if f.required:
            raise Invalid(f"{f.name} is required")
        return None
    k = f.kind
    if k == TEXT or k == REF:
        if not isinstance(value, str) or not value.strip():
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
    if k == CHANCE:
        return based(f.name, value, _chance)
    if k == FIGURE:
        return based(f.name, value, _number)
    if k == DERIVATION_FIELD:
        return derivation(f.name, value)
    if k == CONVERSION:
        if value == "price":
            return value
        m = re.fullmatch(r"yield\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)", value) if isinstance(value, str) else None
        if m and float(m.group(1)) > 0:
            return f"yield({float(m.group(1)):g})"
        raise Invalid(f"{f.name} is price or yield(duration) with a positive duration")
    raise AssertionError(f"unknown field kind {k}")


def check_object(type_name: str, stated: dict) -> None:
    """Rules across the stated fields of one object."""
    if type_name == "Proposition":
        rel = stated.get("relation")
        if rel in (">", ">=", "<", "<=", "==") and (stated.get("metric") is None or stated.get("value") is None):
            raise Invalid(f"a proposition with relation {rel} names its metric and its value")
        ws, we = stated.get("window_start"), stated.get("window_end")
        if ws and we and ws > we:
            raise Invalid(f"window_start, {ws}, is after window_end, {we}: the window runs from its start to its end")
        if stated.get("derivation") is not None and stated.get("chance") is not None:
            raise Invalid("a proposition states its derivation or its chance, not both: clear one with edit(id, "
                          "field, null) first")
    if type_name == "Portfolio" and stated.get("horizon_days") is not None and stated["horizon_days"] <= 0:
        raise Invalid("horizon_days is a positive number of days")


def check_link(relation: str, stated: dict) -> None:
    """Rules across the stated fields of one link."""
    if relation == "holds":
        given = [k for k in ("quantity", "dollars", "weight") if stated.get(k) is not None]
        if len(given) != 1:
            raise Invalid("a holding gives exactly one of quantity, dollars, or weight")
    if relation == "then" and stated.get("step") not in (1, 2):
        raise Invalid("step is 1 for B, the part that comes first, or 2 for C, the part that follows it")
