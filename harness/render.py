"""The page's one renderer: the portfolio panel, the ledger, an opened event, a holding, a factor, the inspector, the
id chips, and the ledger as markdown, each read from the store, in the reader's terms: probability, priced in, beta and
dollar beta, shock, full impact, contributors, depth, look-through exposure.

Every figure is a derived field, or an output or input of the Computation or Judgment that is its basis; nothing here
computes a number of its own. Its arithmetic is rounding, each number to the precision of what it is: every dollar
figure to the cent, with the parts shown together apportioned in cents so that they add to the whole; a move, and an
event's share of the portfolio, to two decimals of a percent, and a weight to one; a probability in whole percents,
with one decimal below 10%; a contract's price in cents; a beta to two decimals. Where a fraction enters the
inspector's arithmetic it shows six significant figures, so each line reconciles to the cent, and the full details
keep every value exact. A chart's points are a stored series: a price's daily closes to its latest mark, or the
history of the market that prices an event, one minus it where the event is the market's opposite.

A render reads the store under its lock. While the analyst works, the page renders from what the store already holds
(`cache_only`): a value that waits on the judge or a data client shows as a quiet placeholder where it will sit, and
the page never asks or fetches on the analyst's behalf. When a reply ends, the panels render once more with the judge
and the data clients. A value that does not exist yet, such as dollars before any consequence, is simply not there.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from typing import Any, Iterable

from harness import schema
from harness.derive.engine import Unavailable
from harness.derive.portfolio import closes, pseudo, ticker_of
from harness.derive.propagate import PROPAGATE, STRUCTURE
from harness.ledger import channel_reach, ledger, row
from harness.store import Store, StoreError
from harness.venues import split
from harness.venues.polymarket import NY

MINUS = "\u2212"
NAMES_SHOWN = 10  # the largest names after look-through the portfolio panel lists
CONSTITUENTS_SHOWN = 12  # a fund's constituents shown before "all of them"
LISTED = 15  # the rows an inspector table lists before "and N more"
EVENTS_SHOWN = 5  # the events a holding's page lists, those that move it most
SMALL = 0.005  # a holding carrying under half a percent of a factor's exposure is not listed by name
ZERO = 1e-12  # coverage 0: no market reaches the event
VENUES = {"polymarket": "Polymarket", "kalshi": "Kalshi", "hyperliquid": "Hyperliquid"}


# ---------------------------------------------------------------- numbers, at the precision of what they are


def _half_up(x: float, quantum: str) -> Decimal:
    return Decimal(repr(x)).quantize(Decimal(quantum), rounding=ROUND_HALF_UP)


def _lead(x: float, nothing: bool, sign: bool) -> str:
    """A figure's sign: none where it rounds to nothing, a minus, or with `sign` a plus."""
    return "" if nothing else MINUS if x < 0 else "+" if sign else ""


def money(x: float | None, *, sign: bool = False) -> str:
    """A dollar figure, exact to the cent, halves up: −2,829.33 is −$2,829.33, 659.79 is $659.79 (+$659.79 with
    `sign`), 61,250.4 is $61,250.40, and an amount under half a cent is $0.00."""
    if x is None:
        return ""
    r = _half_up(abs(x), "0.01")
    return f"{_lead(x, r == 0, sign)}${r:,.2f}"


dollars = money  # every dollar figure on the page, a fact or an estimate, is exact to the cent


def apportion(parts: list[float], whole: float | None = None) -> list[int]:
    """Parts to whole units that add to the whole, itself rounded: each part rounded down, and the units still short
    of the whole given to the parts with the largest remainders. The whole is the parts' sum unless given."""
    if not parts:
        return []
    total = int(_half_up(sum(parts) if whole is None else whole, "1"))
    down = [math.floor(x) for x in parts]
    short = total - sum(down)
    order = sorted(range(len(parts)), key=lambda i: (-(parts[i] - down[i]), i))
    for i in order[:max(short, 0)]:
        down[i] += 1
    return down


def to_cents(parts: list[float], whole: float | None = None) -> list[float]:
    """Dollar amounts to the cent that add to the whole, itself to the cent: the parts apportioned in cents."""
    return [n / 100 for n in apportion([x * 100 for x in parts], None if whole is None else whole * 100)]


def chance(p: float | None) -> str:
    """A probability in whole percents, with one decimal below 10%: 0.19 is 19%, 0.0803 is 8.0%."""
    if p is None:
        return ""
    tenths = Decimal(repr(p * 100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if tenths < 10:
        return f"{tenths}%"
    return f"{Decimal(repr(p * 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)}%"


def share(x: float | None) -> str:
    """A share of the portfolio, what an event would do as a fraction of its value: a signed percent to two decimals,
    −9.82%."""
    return percent(x, 2, sign=True)


def operand(x: float | None, *, sign: bool = False) -> str:
    """A fraction where it enters shown arithmetic, a probability, a shock, or a move, as a percent with the digits it
    has, to six significant figures, so that the arithmetic reconciles to the cent: 0.175 is 17.5%, 0.3 is 30%,
    0.1023218 is 10.2322%, and with `sign` 0.03997271 is +3.99727%."""
    if x is None:
        return ""
    d = Decimal(repr(abs(x))) * 100
    r = d if d == 0 else d.quantize(Decimal(1).scaleb(d.adjusted() - 5), rounding=ROUND_HALF_UP)
    text = f"{r:f}"
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return f"{_lead(x, r == 0, sign)}{text}%"


def per(x: float | None) -> str:
    """A contract's price in cents, to a tenth of a cent where it has one: 0.335 is 33.5¢, 0.33 is 33¢."""
    if x is None:
        return ""
    r = (Decimal(repr(x)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{r:f}".rstrip("0").rstrip(".") + "¢"


def percent(x: float | None, places: int = 2, *, sign: bool = False) -> str:
    """A fraction as a percent to `places` decimals: 0.04351 is 4.35%."""
    if x is None:
        return ""
    r = (Decimal(repr(abs(x))) * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{_lead(x, r == 0, sign)}{r}%"


def either(x: float | None) -> str:
    """An expected move: a size either way, as an options desk quotes one, \u00b1$X."""
    return "" if x is None else f"\u00b1{money(abs(x))}"


def move(x: float | None) -> str:
    """A move: a signed percent to two decimals, −4.35%."""
    return percent(x, 2, sign=True)


def sens(x: float | None) -> str:
    """A beta, to two decimals: 1.02, −0.31."""
    if x is None:
        return ""
    r = _half_up(abs(x), "0.01")
    return f"{_lead(x, r == 0, False)}{r}"


def count(x: float | None) -> str:
    """A number of shares or contracts: whole where it is whole, else to four decimals."""
    if x is None:
        return ""
    if float(x).is_integer():
        return f"{x:,.0f}"
    return f"{x:,.4f}".rstrip("0").rstrip(".")


PERCENT = ("percent", "%", "pct")


def level(x: float | None, unit: str | None) -> str:
    """A metric's level for reading: a price in dollars to the cent, anything else in its unit as quoted."""
    if x is None:
        return ""
    if unit in ("USD", "usd", "dollars"):  # to the cent, halves up, as every figure on the page rounds
        r = _half_up(abs(x), "0.01")
        return (MINUS if x < 0 and r else "") + f"${r:,.2f}"
    text = f"{x:,.4g}".replace("-", MINUS)
    if unit in PERCENT:
        return text + "%"
    return f"{text} {escape(unit)}" if unit else text


def difference(x: float | None, unit: str | None, *, short: bool = False) -> str:
    """A difference between two levels of a metric, in its unit; for a metric measured in percent, in percentage
    points, because a level that ends a quarter of a percent lower and one that ends a quarter of a point lower are
    different moves. `short`, as a chip says it: "points"."""
    if x is None or unit not in PERCENT:
        return level(x, unit)
    text = f"{x:,.4g}".replace("-", MINUS)
    word = "point" if abs(x) == 1 else "points"
    return f"{text} {word}" if short else f"{text} percentage {word}"


def shock(x: float | None, unit: str | None) -> str:
    """A shock, the gap between where a metric ends with an event and without it: a difference with its sign,
    +$20.57, −0.25 percentage points."""
    text = difference(x, unit)
    return f"+{text}" if x is not None and x > 0 and text[:1] != "+" else text


def moment(iso: str | None) -> str:
    """A time as a member reads it, in New York: 6:47 PM on Nov 5, 2026."""
    try:
        t = datetime.fromisoformat(iso).astimezone(NY)
    except (TypeError, ValueError):
        return ""
    return f"{t.strftime('%I').lstrip('0')}:{t.strftime('%M %p')} on {day(t.date().isoformat())}"


def day(iso: str | None, *, year: bool = True) -> str:
    """A date as a member reads it: Nov 4, 2026, or Nov 4."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return escape(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}" if year else f"{d.strftime('%b')} {d.day}"


def added(xs: list[float]) -> str:
    """Dollar figures added, each sign written as the operation: −$8,219.37 + $42.20."""
    out = money(xs[0])
    for x in xs[1:]:
        out += f" − {money(abs(x))}" if x < 0 else f" + {money(x)}"
    return out


# ---------------------------------------------------------------- reading the store


class Reader:
    """The store as the page reads it. With `pending`, a value that waits on a judge or a data client shows as a
    placeholder; otherwise a value with none is simply not there."""

    def __init__(self, store: Store, *, pending: bool = False):
        self.store = store
        self.engine = store.engine
        self.pending = pending

    def value(self, name: str, subject: str) -> Any:
        """A derivation's output, or None: not computed, being computed further up this read, or unavailable."""
        if self.engine.computing(name, subject):
            return None
        try:
            v = self.engine.value(name, subject)
        except (Unavailable, RuntimeError, StoreError, KeyError):
            return None
        return v

    def output(self, name: str, subject: str) -> dict | None:
        v = self.value(name, subject)
        return v.output if v is not None else None

    def get(self, oid: str) -> dict | None:
        try:
            return self.store.get(oid)
        except (StoreError, Unavailable, RuntimeError):
            return None

    def derived(self, eid: str, kind: str) -> dict:
        try:
            return self.engine.derived(eid, kind)
        except (StoreError, Unavailable, RuntimeError, KeyError):
            return {}

    def waits(self, name: str, subject: str) -> bool:
        """Whether the value is being worked out: this read waited on a judge or a data client the page leaves to
        the analyst's turn."""
        return self.pending and self.engine.pending(name, subject)

    def computation(self, cid: str | None) -> dict:
        """A Computation's record: its inputs and output."""
        if not cid or not str(cid).startswith("cmp:"):
            return {}
        try:
            return self.store.get(cid)
        except StoreError:
            return {}


@contextmanager
def cache_only(store: Store):
    """The store answering from what it holds: no judge, no data client, no venue. Its memo of this revision is
    cleared before, so a value read earlier with them, a fetch that failed among them, is read again here and shows
    as waiting; and after, so the next read with them works out what this one could not."""
    saved = store.engine.judge, store.fetchers, store.venues
    store.engine.reset()
    store.engine.judge, store.fetchers, store.venues = None, {}, None
    try:
        yield
    finally:
        store.engine.judge, store.fetchers, store.venues = saved
        store.engine.reset()


def _value(derived: dict, field_: str) -> Any:
    return (derived.get(field_) or {}).get("value")


def _basis(derived: dict, field_: str) -> str | None:
    return (derived.get(field_) or {}).get("basis")


# ---------------------------------------------------------------- html


def e(text: Any) -> str:
    return escape("" if text is None else str(text))


def go(target: str, text: str, cls: str = "go", title: str | None = None) -> str:
    """A number or a name that opens a step deeper on the right."""
    t = f' title="{e(title)}"' if title else ""
    return f'<a class="{cls}" data-go="{e(target)}"{t}>{text}</a>'


def num(text: str, cls: str = "") -> str:
    return f'<span class="n v{(" " + cls) if cls else ""}">{text}</span>'


def gonum(target: str, text: str, cls: str = "") -> str:
    """A figure that opens its explanation."""
    return go(target, num(e(text), cls), "go num")


WAIT = '<span class="wait" aria-label="working it out"></span>'


def section(title: str, body: str, cls: str = "", key: str | None = None) -> str:
    k = f' data-k="{e(key)}"' if key else ""
    return f'<section class="sec {cls}"{k}><h3>{e(title)}</h3>{body}</section>'


def paragraphs(text: str | None) -> str:
    return "".join(f"<p>{e(x.strip())}</p>" for x in (text or "").split("\n\n") if x.strip())


def note_html(text: str | None) -> str:
    """A note, in the analyst's words: the page renders its markdown as it renders a reply's, each id a chip."""
    return f'<div class="md" data-md="{e(text or "")}">{paragraphs(text)}</div>'


def full(record_id: str | None, text: str = "Full details") -> str:
    """The whole Computation or Judgment, one click away."""
    return f'<p class="raw">{go("raw:" + record_id, e(text), "rawlink")}</p>' if record_id else ""


MATH_VAR = re.compile(r"(?<![A-Za-z])([A-EpV])(?![A-Za-z])")


def eqn(text: str) -> str:
    """A formula in math type: each variable, a single letter, in italics, and the operators and figures upright."""
    return '<span class="m">' + MATH_VAR.sub(lambda m: f"<var>{m.group(1)}</var>", e(text)) + "</span>"


def pill(text: str, tone: str = "") -> str:
    """A small label for what a page is or how its figure was set."""
    return f'<span class="pill{" " + tone if tone else ""}">{text}</span>'


def hero(kicker: str, *, figure: str = "", title: str = "", context: str = "", pills: Iterable[str] = ()) -> str:
    """The head of a page on the right: what it is; its figure, or its title; what it belongs to; and how it was
    set."""
    shown = "".join(x for x in pills if x)
    parts = [f'<p class="kicker">{kicker}</p>']
    if figure:
        parts.append(f'<div class="fig">{figure}</div>')
    if title:
        parts.append(f'<h2 class="title">{title}</h2>')
    if context:
        parts.append(f'<p class="ctx">{context}</p>')
    if shown:
        parts.append(f'<div class="pills">{shown}</div>')
    return f'<header class="hero">{"".join(parts)}</header>'


def stats(items: Iterable[tuple[str, str, str]]) -> str:
    """Facts in a grid, each label above its figure, with what qualifies it beneath."""
    cells = "".join(f'<div class="stat"><div class="l">{name}</div><div class="v">{value}</div>'
                    + (f'<div class="s">{sub}</div>' if sub else "") + "</div>"
                    for name, value, sub in items if value)
    return f'<div class="stats">{cells}</div>' if cells else ""


def receipt(rows: Iterable[tuple[str, str, str]], total: tuple[str, str, str] | None = None,
            formula: Iterable[str] = ()) -> str:
    """Workings as a receipt: each input with what it is and its value, the result beneath a rule, then the formula
    that makes it, in symbols and with the values filled in."""
    def row(term: str, what: str, value: str, cls: str = "") -> str:
        return f'<tr{f" class={cls}" if cls else ""}><th>{term}</th><td class="d">{what}</td><td class="v">{value}</td></tr>'
    body = "".join(row(*x) for x in rows) + (row(*total, cls="tot") if total else "")
    lines = "".join(f"<div>{x}</div>" for x in formula if x)
    return f'<table class="rcpt"><tbody>{body}</tbody></table>' + (f'<div class="eq">{lines}</div>' if lines else "")


def worked(formula: str, values: str) -> str:
    """What a figure is, in symbols, with the values filled in beneath."""
    return f'{eqn(formula)}<div class="sub-eq">{eqn(values)}</div>'


def fold(inner: str) -> str:
    """Text that shows its first lines, the rest a click away."""
    return (f'<div class="fold"><div class="fold-in">{inner}</div>'
            '<button class="linkish more" type="button" data-more>Show more</button></div>')


def title_html(text: str | None) -> str:
    """A title, escaped, with a range of numbers, such as December 8–9, kept on one line."""
    return re.sub("(\\d+)\u2013(\\d+)", lambda m: f'<span class="nw">{m.group(0)}</span>', e(text))


def series_of(items: list[str]) -> str:
    """A, B, and C."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def host(url: str | None) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0]


def quote(text: str) -> str:
    return f"\u201c{text}\u201d"


# ---------------------------------------------------------------- names and labels


def name_label(nid: str) -> str:
    ticker = nid.split(":", 1)[1]
    kind = pseudo(ticker)
    if kind == "unmapped":
        return f"{ticker.split(':', 1)[1]}'s other holdings"
    if ticker.startswith("CASH:"):
        return f"{ticker.split(':', 1)[1]}'s cash"
    return "cash" if ticker == "CASH" else ticker


def company(store: Store, nid: str) -> str | None:
    asof = store.meta("asof")
    ticker = nid.split(":", 1)[1]
    if pseudo(ticker) or not asof:
        return None
    try:
        r = store.data("ticker_ref", ticker, covering=(asof, asof), required=False)
    except Exception:
        return None
    if r is None:
        return None
    return ((store.data_payload(r["id"]) or {}).get("results") or {}).get("name")


def market_question(reader: Reader, mid: str) -> str | None:
    out = reader.output("market_listing", mid)
    return out.get("question") if out else None


def title_of(store: Store, oid: str) -> str:
    stated = store.stated_of(oid) if store.type_of(oid) else {}
    return stated.get("title") or stated.get("name") or oid


def metric_label(store: Store, mid: str) -> str:
    """A metric as the page names it: a price for its ticker, else the name the analyst gave it; a metric with no
    name reads by its id."""
    m = schema.PROXY_METRIC_RE.match(mid)
    if m:
        return f"{m.group(1)}'s price"
    return (store.stated_of(mid) or {}).get("name") or mid.split(":", 1)[1].replace("_", " ")


def heading(text: str) -> str:
    """A name where it heads a section or a panel: its first letter capital, as a plain name written to sit in a
    sentence ("the US effective tariff rate") begins in lower case."""
    return text[:1].upper() + text[1:]


def unit_of(store: Store, mid: str) -> str | None:
    if schema.PROXY_METRIC_RE.match(mid):
        return "USD"
    return store.stated_of(mid).get("unit") if store.type_of(mid) == "Metric" else None


def channel_names(store: Store) -> dict[str, str]:
    return {c["id"]: c["stated"]["name"] for c in store.query("Channel")}


def label(store: Store, oid: str) -> str | None:
    """What a member calls an id: an event's title, a market's question, a holding's ticker, a factor's name."""
    kind = store.type_of(oid)
    if kind is None:
        return None
    if kind == "Proposition":
        return title_of(store, oid)
    if kind == "Market":
        return market_question(Reader(store), oid) or None
    if kind == "Metric":
        return metric_label(store, oid)
    if kind == "Name":
        return name_label(oid)
    if kind == "Channel":
        return store.stated_of(oid).get("name")
    if kind == "Source":
        return host(store.stated_of(oid).get("url"))
    if kind == "Note":
        return "a note"
    if kind == "Computation":
        return "how it was worked out"
    if kind == "Judgment":
        return "the second model's check"
    if kind == "link":
        link = store.link_by_id(oid) or {}
        a, b = link.get("from"), link.get("to")
        la, lb = (label(store, a) if a else None) or "it", (label(store, b) if b else None) or "it"
        return {"consequence": f"the shock to {lb} from {quote(la)}", "quoted_by": f"how {quote(la)} matches {quote(lb)}",
                "evidence": f"a quote for {quote(la)}", "watches": quote(lb), "holds": lb}.get(
            link.get("relation"), f"how {quote(la)} relates to {quote(lb)}")
    return None


def labels(store: Store, ids: list[str]) -> dict[str, str]:
    """Each id's label, for the line that says what the analyst is doing."""
    out = {}
    for oid in dict.fromkeys(ids):
        try:
            found = label(store, oid)
        except (StoreError, KeyError, TypeError):
            found = None
        if found:
            out[oid] = found
    return out


# ---------------------------------------------------------------- the portfolio panel


@dataclass
class Shown:
    """The dollars and weights the page shows of the portfolio, each to its precision, so that the parts add to the
    whole wherever they are shown together: each holding's value, weight, and move, and what each fund's value comes
    to at each thing it holds."""
    value: dict[str, float] = field(default_factory=dict)  # by holds link, to the cent
    weights: dict[str, str] = field(default_factory=dict)
    moved: dict[str, float] = field(default_factory=dict)
    parts: dict[tuple[str, str], float] = field(default_factory=dict)  # (fund, what it holds): dollars, to the cent
    total: float | None = None
    total_moved: float | None = None


def _shown(view_: dict) -> Shown:
    holdings, lt = view_["holdings"] or {}, view_["lookthrough"]
    rows = holdings.get("holds") or {}
    ids = [h["id"] for h in view_["holds"] if h["id"] in rows]
    if not ids:
        return Shown()
    value = dict(zip(ids, to_cents([rows[i]["dollars"] for i in ids], holdings["V"])))
    tenths = apportion([(rows[i]["weight"] or 0.0) * 1000 for i in ids], 1000)
    weights = {i: f"{t / 10:.1f}%" for i, t in zip(ids, tenths)}
    whole = holdings.get("session_move_dollars")
    moving = [i for i in ids if rows[i].get("session_move_dollars") is not None]
    moved = dict(zip(moving, to_cents([rows[i]["session_move_dollars"] for i in moving], whole))) \
        if whole is not None else {}
    parts: dict[tuple[str, str], float] = {}
    if lt:
        out_of: dict[str, list[dict]] = {}
        for rec in lt["links"].values():
            out_of.setdefault(rec["from"], []).append(rec)

        def split(fund: str, shown: float, exact: float, seen: frozenset) -> None:
            recs = sorted(out_of.get(fund, []), key=lambda r: r["to"])
            if not recs or fund in seen:
                return
            amounts = [r["fields"]["dollars"] for r in recs]
            for r, n in zip(recs, to_cents(amounts, shown if abs(sum(amounts) - exact) < 0.01 else None)):
                parts[(fund, r["to"])] = n
                split(r["to"], n, r["fields"]["dollars"], seen | {fund})

        for h in view_["holds"]:
            if h["id"] in value:
                split(h["to"], value[h["id"]], rows[h["id"]]["dollars"], frozenset())
    return Shown(value, weights, moved, parts, sum(value.values()),
                 sum(moved.values()) if whole is not None else None)


def portfolio_view(reader: Reader) -> dict | None:
    """What the portfolio panel shows, read from the holdings, the look-through, and g, each as it is ready."""
    store = reader.store
    if store.type_of("portfolio") != "Portfolio":
        return None
    stated = store.stated_of("portfolio")
    holds = store.links_of("portfolio", "holds")
    holdings = reader.output("holdings", "portfolio")
    lookthrough = reader.output("lookthrough", "portfolio") if holdings else None
    g = reader.output("g", "portfolio") if lookthrough else None
    reach = None
    if g is not None:
        try:
            reach = channel_reach(store)
        except Exception:
            reach = None
    view_ = {"stated": stated, "holds": holds, "holdings": holdings, "lookthrough": lookthrough, "g": g,
             "reach": reach}
    view_["shown"] = _shown(view_)
    return view_


def held_of(view_: dict, nid: str) -> tuple[float, float | None, list[tuple[str, float]]]:
    """What is held of a name, as the page shows it: in all, directly, and through each fund, largest first, the parts
    adding to the whole."""
    shown, lt = view_["shown"], view_["lookthrough"] or {}
    expanded = ((lt.get("funds") or {}).get(ticker_of(nid)) or {}).get("expanded")
    direct = None if expanded else next((shown.value[h["id"]] for h in view_["holds"]
                                         if h["to"] == nid and h["id"] in shown.value), None)
    through = sorted(((f, n) for (f, to), n in shown.parts.items() if to == nid), key=lambda x: (-abs(x[1]), x[0]))
    return (direct or 0) + sum(n for _, n in through), direct, through


def _when(holdings: dict | None) -> str:
    rows_ = (holdings or {}).get("holds", {}).values()
    times = sorted(r["price_time"] for r in rows_ if r.get("price_time"))
    dates = sorted({r["session"] for r in rows_ if r.get("session")})
    return (f"Latest prices at {moment(times[-1])}" if times else
            f"Prices at the close of {day(dates[-1])}" if dates else "Pricing the holdings")


def _session(holdings: dict | None) -> str:
    """The session the portfolio's move covers, where its holdings share one: " on Nov 5"."""
    sessions = {r["session"] for r in ((holdings or {}).get("holds") or {}).values() if r.get("session")}
    return f" on {day(sessions.pop(), year=False)}" if len(sessions) == 1 else ""


def _sign_tone(x: float | None) -> str:
    return "loss" if x is not None and x < 0 else "gain" if x is not None and x > 0 else ""


def portfolio_panel(reader: Reader) -> str:
    view_ = portfolio_view(reader)
    if view_ is None:
        return ""
    holdings, lt, shown = view_["holdings"], view_["lookthrough"], view_["shown"]
    if holdings and shown.total is not None:
        moved = ""
        if shown.total_moved is not None:
            moved = (f'<div class="dmove {_sign_tone(shown.total_moved)}">{num(dollars(shown.total_moved, sign=True))} '
                     f'({num(move(holdings.get("session_move")))})<span class="when">{_session(holdings)}</span></div>')
        head = f'<div class="total">{num(dollars(shown.total))}</div>{moved}<div class="asof">{_when(holdings)}</div>'
    else:
        head = f'<div class="total">{WAIT}</div><div class="asof">{_when(holdings)}</div>'
    rows = []
    widest = max((abs(v) for v in shown.value.values()), default=0.0) or 1.0  # the largest holding fills its bar
    for h in view_["holds"]:
        nid, hid = h["to"], h["id"]
        got = (holdings or {}).get("holds", {}).get(hid) or {}
        value = num(dollars(shown.value[hid])) if hid in shown.value else WAIT
        moved = ""
        if hid in shown.moved and ticker_of(nid) != "CASH":
            moved = (f'<span class="mv n {_sign_tone(shown.moved[hid])}">{dollars(shown.moved[hid], sign=True)} '
                     f'({move(got.get("session_move"))})</span>')
        share = shown.value.get(hid)
        bar = (f'<span class="bar" aria-hidden="true"><i style="width:{100 * abs(share) / widest:.1f}%"></i></span>'
               if share else "")
        inner = (f'{_ident(nid)}<span class="val n">{value}</span>'
                 f'<span class="wt">{bar}<span class="n">{shown.weights.get(hid, "")}</span></span>{moved}')
        rows.append(f'<li data-k="{e(hid)}">{go(nid, inner, "go row hold", _named(reader, nid))}</li>')
    out = [f'<header class="ph"><h2>Portfolio</h2>{head}</header>',
           section("Holdings", f'<ul class="holdings">{"".join(rows)}</ul>')]
    if lt is None:
        out.append(section("Look-through exposure", WAIT, "pending"))
    elif shown.parts:  # where no fund is looked through, the names are the holdings, already listed
        out.append(section("Look-through exposure", _largest_names(reader, view_)))
    out.append(section("Factor exposures", _exposures(reader, view_), "exposures"))
    return "".join(out)


def _ident(nid: str) -> str:
    """A holding or a name as a row names it: its ticker, as the person and the ledger name it."""
    return f'<span class="id"><span class="tk">{e(heading(name_label(nid)))}</span></span>'


def _named(reader: Reader, nid: str) -> str | None:
    """The company's or fund's name, which a row gives on hover and its own page in full."""
    return company(reader.store, nid) if ticker_of(nid) != "CASH" else None


def _largest_names(reader: Reader, view_: dict) -> str:
    """The largest names after look-through, each with your exposure, and the funds it comes through: where it comes
    one way only, which; where several, how much each, each part kept whole on its line."""
    lt = view_["lookthrough"]
    e_ = {n: v for n, v in lt["e"].items() if v and not pseudo(ticker_of(n))}
    items = []
    for nid in sorted(e_, key=lambda n: (-abs(e_[n]), n))[:NAMES_SHOWN]:
        total, direct, through = held_of(view_, nid)
        via = ""
        if through and not direct and len(through) == 1:
            via = f'<span class="via"><span class="part">through {e(name_label(through[0][0]))}</span></span>'
        elif through:
            parts = ([f"{num(dollars(direct))} directly"] if direct else []) + \
                [f"{num(dollars(n))} through {e(name_label(f))}" for f, n in through]
            via = ('<span class="via">' + '<span class="dot"> · </span>'.join(
                f'<span class="part">{x}</span>' for x in parts) + '</span>')
        inner = f'{_ident(nid)}<span class="val n">{num(dollars(total))}</span>{via}'
        items.append(f'<li data-k="{e(nid)}">{go(nid, inner, "go row", _named(reader, nid))}</li>')
    return f'<ul class="names">{"".join(items)}</ul>'


def _exposures(reader: Reader, view_: dict) -> str:
    """Each factor's dollar beta and its largest one-month move in the last two years applied to the dollar beta,
    largest first: the name on its own line, the figures under column labels."""
    reach = view_["reach"]
    if not reach or not reach.get("channels"):
        return WAIT if reader.pending or view_["g"] is None else ""
    found = sorted((c for c in reach["channels"] if c["g_times_M_c"] is not None), key=lambda c: -c["g_times_M_c"])
    items = []
    for c in found:
        inner = (f'<span class="lbl"><span class="nm">{e(heading(c["name"]))}</span></span>'
                 f'<span class="xv"><span class="v n">{money(c["g"])}</span>'
                 f'<span class="v n">{money(c["g_times_M_c"])}</span></span>')
        items.append(f'<li data-k="{e(c["channel"])}">{go(c["channel"], inner, "go row")}</li>')
    return ('<div class="xhead"><span class="v">Dollar beta</span><span class="v">Largest month</span></div>'
            f'<ul class="exposures">{"".join(items)}</ul>')


# ---------------------------------------------------------------- the ledger panel


def _ledger(reader: Reader) -> dict | None:
    store = reader.store
    if store.type_of("portfolio") != "Portfolio" or not store.links_of("portfolio", "watches"):
        return None
    try:
        return ledger(store)
    except (StoreError, Unavailable):
        return None


def ledger_panel(reader: Reader) -> str:
    """One line per watched event, in the order it was watched, each with its place in both rankings: the page holds
    the lines in place while the analyst works and moves them into rank when a reply ends. Its horizon heads it, and
    column labels name the figures."""
    found = _ledger(reader)
    if not found:
        return ""
    store = reader.store
    by_id = {r["id"]: r for r in found["rows"]}
    emove = {r["id"]: i for i, r in enumerate(found["rows"])}
    worst = {pid: i for i, pid in enumerate(found["worst"])}
    lines = "".join(_line(reader, by_id[link["to"]], emove[link["to"]], worst[link["to"]])
                    for link in store.links_of("portfolio", "watches"))
    switch = ('<div class="rank" role="group" aria-label="Order the events">'
              '<button type="button" data-rank="emove">Expected move</button>'
              '<button type="button" data-rank="worst">Worst case</button></div>')
    days = store.stated_of("portfolio").get("horizon_days")
    return (f'<header class="ph lh"><h2>Events</h2>{switch}</header>'
            f'<p class="caption">Horizon {day(found["horizon_end"])} · {days} days</p>'
            '<div class="evhead"><span>Probability · last month</span>'
            '<span><span class="k-emove">Expected move</span><span class="k-worst">Worst case</span></span></div>'
            f'<ol class="events" data-hold>{lines}</ol>')


# ---------------------------------------------------------------- charts: the series each one draws


def chart(points: list, *, kind: str, label: str, ranges: bool = False, mark: dict | None = None,
          band: list | None = None, spark: bool = False, shown: str | None = None) -> str:
    """A chart the page draws (app.js): its points, [date, value], of a price ("price") or a chance ("chance"), with
    range buttons where it is long, opening on `shown` or else on all of it, a stretch to mark, and an interval at its
    last point."""
    if len(points) < 2:
        return ""
    # a price's points are to the cent, rounded once as the page rounds a price; a probability's keep their decimals
    spec = {"kind": kind, "points": [[d, float(_half_up(v, "0.01")) if kind == "price" else round(v, 6)]
                                     for d, v in points]}
    spec.update({k: v for k, v in (("ranges", ranges), ("range", shown), ("mark", mark), ("band", band)) if v})
    data = e(json.dumps(spec, separators=(",", ":")))
    if spark:
        return f'<span class="spark" data-chart="{data}" title="{e(label)}" role="img" aria-label="{e(label)}"></span>'
    return f'<figure class="chart" data-k="chart" data-chart="{data}" role="img" aria-label="{e(label)}"></figure>'


def priced_by(reader: Reader, r: dict) -> tuple[str, bool] | None:
    """The market whose price is the event's probability, and whether the probability is one minus it."""
    point = r.get("point") or {}
    if point.get("rule") != "market":
        return None
    link = reader.store.link_by_id(point["link"]) or {}
    return point["market"], (link.get("stated") or {}).get("transform") == "complement"


def chance_series(reader: Reader, r: dict, *, days: int | None = None) -> list:
    """The event's probability over time: the stored history of the market that prices it, one minus it under the
    complement, to today's probability."""
    found = priced_by(reader, r)
    asof = reader.store.meta("asof")
    if not found or r["p"] is None or not asof:
        return []
    mid, complement = found
    history = (reader.output("market_history", mid) or {}).get("history") or {}
    points = [[d, 1.0 - v if complement else v] for d, v in sorted(history.items()) if d < asof] + [[asof, r["p"]]]
    if days:
        start = (date.fromisoformat(asof) - timedelta(days=days)).isoformat()
        points = [x for x in points if x[0] >= start]
    return points


def _unbounded(rule: str | None, short: bool = False) -> str:
    """What stands beside a probability the markets put no range on, which says how it was worked out: derived, from
    the propositions its derivation reads, whose markets or stated probabilities the event shows; or the analyst's
    estimate, stated whole."""
    if rule == "derivation":
        return "derived"
    if rule == "market":
        return ""
    return "our estimate" if short else "our estimate, no market prices it"


def _interval_short(r: dict) -> str:
    lo, hi, cov = r["lo"], r["hi"], r["coverage"]
    if cov is not None and cov <= ZERO:
        return _unbounded((r.get("point") or {}).get("rule"), short=True)
    if lo is None or hi is None:
        return ""
    floor, ceiling = lo > ZERO, hi < 1.0 - ZERO
    return (f"{chance(lo)}\u2013{chance(hi)}" if floor and ceiling else f"\u2265 {chance(lo)}" if floor else
            f"\u2264 {chance(hi)}" if ceiling else "")


def _interval_html(kind: str, interval: str) -> str:
    """Beside a line's probability: the range the markets imply, named as theirs so no figure reads as a claim about
    another; or how the probability was worked out, in words."""
    if not interval:
        return ""
    if kind == "ei":
        return f'<span class="ei"><span class="mw">markets</span> {interval}</span>'
    return f'<span class="ew">{interval}</span>'


def _line(reader: Reader, r: dict, i: int, j: int) -> str:
    """An event's line: its title and the figure it is ranked by, expected move or worst case as the ledger is ordered;
    under them its probability, the range markets imply, and its last month, and what it does on Yes and on No."""
    pid = r["id"]
    p, yes = r["p"], r["pnl_yes"]
    shown_p = num(chance(p)) if p is not None else WAIT if reader.waits("point", pid) else ""
    if yes is not None:
        key = (f'<span class="k-emove">{num(either(r["emove"]))}</span>'
               f'<span class="k-worst">{num(money(r["worst"], sign=True), _tone(r["worst"]))}</span>')
        no = r.get("pnl_no")
        if_yes = (f'<span class="yn">Yes {num(money(yes, sign=True), _tone(yes))}</span>'
                  + (f'<span class="yn">No {num(money(no, sign=True), _tone(no))}</span>' if no is not None else ""))
    else:
        key, if_yes = (WAIT if reader.waits("impact", pid) else ""), ""
    interval = _interval_short(r)
    kind = "ew" if r["coverage"] is not None and r["coverage"] <= ZERO else "ei"  # words, or a range in figures
    spark = chart(chance_series(reader, r, days=31), kind="chance", spark=True, label="Probability, last month")
    return (f'<li class="event" data-k="{e(pid)}" data-emove="{i}" data-worst="{j}"><a class="evline" data-go="{e(pid)}">'
            f'<span class="et">{title_html(r["title"])}</span><span class="ey">{key}</span>'
            f'<span class="eb"><span class="er"><span class="ep">{shown_p}</span>'
            f'{_interval_html(kind, interval)}{spark}</span>'
            f'<span class="es">{if_yes}</span></span></a></li>')


# ---------------------------------------------------------------- a row's figures


WAITING = ("wait",)


def sentences(reader: Reader, r: dict) -> list[list]:
    """A row's figures, each under its label and rounded where it is read: Probability, with the range markets imply;
    Yes, with its share of the portfolio; No; Full impact; Expected move; Largest contributors, with the funds they
    are held through. Each row is a list of parts, its label first, ("b", label), then text, ("go", text, target),
    ("aside", parts) for what reads beside the figure, or WAITING where a value is being worked out. A row with no
    consequence gives its probability only."""
    store, pid = reader.store, r["id"]
    p, lo, hi, cov = r["p"], r["lo"], r["hi"], r["coverage"]
    out: list[list] = []
    if p is None and not reader.waits("point", pid):
        return out
    P = ("go", chance(p), f"x:p:{pid}") if p is not None else WAITING
    if cov is not None and cov <= ZERO:
        beside = _unbounded((r.get("point") or {}).get("rule"))
        out.append([("b", "Probability"), P, *([("aside", [beside])] if beside else [])])
    elif lo is not None and hi is not None and _put(lo, hi, f"x:interval:{pid}"):
        out.append([("b", "Probability"), P, ("aside", _put(lo, hi, f"x:interval:{pid}"))])
    elif reader.pending and reader.waits("propagate", PROPAGATE.subject_of(store, pid)):
        out.append([("b", "Probability"), P, ("aside", ["markets imply ", WAITING, "–", WAITING])])
    else:
        out.append([("b", "Probability"), P])
    yes, no, swing = r["pnl_yes"], r["pnl_no"], r["D"]
    if yes is None and not reader.waits("impact", pid):
        return out
    dollars_ = f"x:swing:{pid}"
    Y, N, D = (("go", money(x, sign=True), dollars_) if x is not None else WAITING for x in (yes, no, swing))
    part = [("aside", [("go", share(r["share_yes"]), dollars_), " of portfolio"])] if r.get("share_yes") is not None \
        else []
    out.append([("b", "Yes"), Y, *part])
    out.append([("b", "No"), N])
    out.append([("b", "Full impact"), D])
    emove = r.get("emove")
    if emove is not None or reader.waits("impact", pid):
        out.append([("b", "Expected move"), ("go", either(emove), f"x:emove:{pid}") if emove is not None else WAITING])
    names = r["by_name"] or []
    if names:
        parts: list = [("b", "Largest contributors")]
        for i, n in enumerate(names):
            parts += ([", "] if i else []) + [("go", name_label(n["name"]), n["name"])]
        # the funds the named holdings come through, except a fund already named by its own other holdings
        named_funds = {f for n in names if pseudo(ticker_of(n["name"])) for f in n["through"]}
        funds = sorted({f for n in names if not pseudo(ticker_of(n["name"])) for f in n["through"]} - named_funds)
        for i, f in enumerate(funds):
            parts += [", through " if not i else ", ", ("go", name_label(f), f)]
        out.append(parts)
    return out


def _put(lo: float, hi: float, span: str) -> list | None:
    """The range markets imply for a probability: a floor to a ceiling, a floor alone, or a ceiling alone; None where
    they set neither."""
    floor, ceiling = lo > ZERO, hi < 1.0 - ZERO
    if floor and ceiling:
        return ["markets imply ", ("go", chance(lo), span), "–", ("go", chance(hi), span)]
    if floor:
        return ["markets imply ≥ ", ("go", chance(lo), span)]
    if ceiling:
        return ["markets imply ≤ ", ("go", chance(hi), span)]
    return None


def sentences_html(parts: list) -> str:
    """Parts as the page shows them; a row's label is its own cell, where the page lays the figures out (figures_html)."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(e(part))
        elif part == WAITING:
            out.append(WAIT)
        elif part[0] == "b":
            out.append(f"<b>{e(part[1])}</b>")
        elif part[0] == "aside":
            out.append(f'<span class="fa">{sentences_html(part[1])}</span>')
        else:
            text, target = part[1], part[2]
            out.append(gonum(target, text) if target.startswith("x:") else go(target, e(text)))
    return "".join(out)


def figures_html(rows: list[list]) -> str:
    """A row's figures, each under its label."""
    cells = "".join(f'<dt>{e(row[0][1])}</dt><dd data-k="f{i}">{sentences_html(row[1:])}</dd>'
                    for i, row in enumerate(rows))
    return f'<dl class="figs">{cells}</dl>'


def sentences_text(parts: list) -> str:
    """Parts as text: a label and its figure, with what reads beside the figure after a comma."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif part == WAITING:
            continue
        elif part[0] == "b":
            out.append(part[1] + " ")
        elif part[0] == "aside":
            out.append(", " + sentences_text(part[1]))
        else:
            out.append(part[1])
    return "".join(out)


def figures_text(rows: list[list]) -> str:
    """A row's figures as one line of text: each label with its figure, separated by semicolons."""
    return "; ".join(sentences_text(row) for row in rows) + "." if rows else ""


# ---------------------------------------------------------------- an opened event


ROW_FORMULA = {  # each rule of the point, E the proposition worked out
    "narrower": "P(E | B) × P(B)",
    "broader": "P(A) + P(E | not A) × (1 − P(A))",
    "between": "P(A) + P(E | B, not A) × (P(B) − P(A))",
    "driver": "P(D) × P(E | D) + (1 − P(D)) × P(E | not D)",
    "part": "P(B) × P(C | B)",
    "either": "P(B) + P(C) − P(B) × P(C | B)",
    "other": "P(B) + (1 − P(B)) × P(C | not B)",
}


def _arithmetic(row_: str, roles: dict, reads: dict, chances: dict) -> str:
    """The row's formula with its values filled in, each as the Computation read it."""
    P = lambda role: operand(reads[roles[role]])  # noqa: E731
    C = lambda key: operand(chances[key]["value"])  # noqa: E731
    return {
        "narrower": lambda: f"{C('E given B')} × {P('B')}",
        "broader": lambda: f"{P('A')} + {C('E given not A')} × (1 − {P('A')})",
        "between": lambda: f"{P('A')} + {C('E given B and not A')} × ({P('B')} − {P('A')})",
        "driver": lambda: f"{P('D')} × {C('E given D')} + (1 − {P('D')}) × {C('E given not D')}",
        "part": lambda: f"{P('B')} × {C('C given B')}",
        "either": lambda: f"{P('B')} + {P('C')} − {P('B')} × {C('C given B')}",
        "other": lambda: f"{P('B')} + (1 − {P('B')}) × {C('C given not B')}",
    }[row_]()


def _passage_target(sid: str, passage: str) -> str:
    return f"x:passage:{sid}:{hashlib.sha256(passage.encode()).hexdigest()[:12]}"


def _to_note(note: str) -> str:
    """Where a note shown once, in the event's notes, bears: a pointer to it, up the page, since the notes come before
    what it moves and how it was matched."""
    return f'Why: <a class="to" data-to="{e(note)}" href="#">the analyst\'s note, above</a>.'


def _stated_basis(reader: Reader, value: dict, owner: str, *, below: Iterable[str] = ()) -> str:
    """A stated assumption's basis, as a card: the page it cites, with each passage quoted; or the analyst's note about
    it, its first lines shown and the rest a click away; or, where that note shows in the event's notes, a pointer to
    it."""
    store = reader.store
    if value.get("source_id"):
        sid = value["source_id"]
        src = store.source_row(sid) or {}
        quotes = "".join(f'<blockquote class="q" data-k="q{i}">{go(_passage_target(sid, passage), e(passage), "go quote")}'
                         f'</blockquote>' for i, passage in enumerate(value.get("passages") or []))
        dated = f" · {day(src.get('dated'))}" if src.get("dated") else ""
        head = (f'<p class="src"><a href="{e(src.get("url"))}" target="_blank" rel="noopener">{e(host(src.get("url")))}'
                f'</a>{dated}</p>')
        return f'<div class="basis">{head}{quotes}</div>'
    notes = [n["from"] for n in store.links_of(owner, "about", "in")]
    if not notes:
        return ""
    here = [n for n in notes if n not in below]
    pointers = "".join(f'<p class="ptr">{_to_note(n)}</p>' for n in notes if n in below)
    if not here:
        return pointers
    inner = "".join(note_html(store.stated_of(n).get("text")) for n in here)
    return f'<div class="basis note">{fold(inner)}</div>{pointers}'


def _how_chance(reader: Reader, r: dict) -> str:
    """How the event's probability is worked out, where the tree below does not say it: the derivation as a receipt,
    or the analyst's estimate with its basis."""
    pid, point = r["id"], r["point"]
    rule = point.get("rule")
    if r["p"] is None:
        return ""
    if rule == "chance":
        return (f'<div class="how" data-k="how"><p class="lead">The analyst\'s estimate, '
                f'{gonum("x:p:" + pid, chance(r["p"]))}</p>'
                f'{_stated_basis(reader, point["chance"], pid, below=r["below"])}</div>')
    if rule != "derivation":
        return ""
    return f'<div class="how" data-k="how">{_derivation(reader, pid, point, r["p"], below=r["below"])}</div>'


CHANCE_LABEL = {  # each conditional probability a derivation states, as its formula writes it
    "E given B": "P(E | B)", "E given not A": "P(E | not A)", "E given B and not A": "P(E | B, not A)",
    "E given D": "P(E | D)", "E given not D": "P(E | not D)", "C given B": "P(C | B)", "C given not B": "P(C | not B)",
}


def _derivation(reader: Reader, pid: str, point: dict, p: float, *, below: Iterable[str] = ()) -> str:
    """A derivation as a receipt: each proposition it reads at its probability, with how that is set, and each
    probability the analyst states; the event's probability beneath them, then its rule in symbols and with the values
    filled in; then the basis of each probability the analyst states."""
    store = reader.store
    roles, reads, chances = point["roles"], point["reads"], point["chances"]
    rows = []
    for role, q in roles.items():
        how = _how_set(reader, q) if q in reads else ""
        what = go(q, e(title_of(store, q))) + (f'<span class="how-set">{how}</span>' if how else "")
        rows.append((eqn(f"P({role})" if q in reads else role), what,
                     gonum("x:p:" + q, operand(reads[q])) if q in reads else ""))
    for key, c in chances.items():
        rows.append((eqn(CHANCE_LABEL[key]), '<span class="how-set">the analyst\'s estimate</span>',
                     gonum("x:p:" + pid, operand(c["value"]))))
    out = receipt(rows, (eqn("P(E)"), "", num(operand(p))),
                  [eqn(f"P(E) = {ROW_FORMULA[point['row']]}"),
                   eqn(f"= {_arithmetic(point['row'], roles, reads, chances)} = {operand(p)}")])
    for key, c in chances.items():
        basis = _stated_basis(reader, c, pid, below=below)
        if basis:
            out += f'<h3 class="sub">Basis for {eqn(CHANCE_LABEL[key])}</h3>{basis}'
    return out


def _how_set(reader: Reader, pid: str) -> str:
    """How a proposition's probability is set, in a few words: a market's price, derived, or the analyst's estimate."""
    rule = (reader.computation(_basis(reader.derived(pid, "Proposition"), "p")).get("output") or {}).get("rule")
    return {"market": "market price", "derivation": "derived", "chance": "the analyst's estimate"}.get(rule, "")


@dataclass
class _Tree:
    reader: Reader
    root: str
    nodes: dict[str, list[dict]]  # each proposition's children: {link, id, out}
    order: list[str]
    reads: dict[str, float]
    point_market: str | None
    edges: dict[str, dict]
    faint: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    below: Iterable[str] = ()


def _tree(store: Store, pid: str) -> tuple[dict[str, list[dict]], list[str]]:
    """The event's propositions, each reached once from the event, by the first link that reaches it."""
    seen, queue, order = {pid}, deque([pid]), [pid]
    nodes: dict[str, list[dict]] = {pid: []}
    while queue:
        here = queue.popleft()
        for link in store.links_of(here, direction="both"):
            if link["relation"] not in STRUCTURE:
                continue
            other = link["to"] if link["from"] == here else link["from"]
            if other in seen:
                continue
            seen.add(other)
            order.append(other)
            nodes[here].append({"link": link, "id": other, "out": link["from"] == here})
            nodes[other] = []
            queue.append(other)
    return nodes, order


def _faint(store: Store, order: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Markets a note names that no link uses, where the note is about the event or a proposition it reaches: each
    under the first proposition its note is about, with the note, the reason."""
    out: dict[str, list[tuple[str, str]]] = {}
    seen = set()
    for pid in order:
        for about in store.links_of(pid, "about", "in"):
            note = about["from"]
            for named in store.links_of(note, "about"):
                mid = named["to"]
                if mid in seen or store.type_of(mid) != "Market" or store.links_of(mid, "quoted_by", "in"):
                    continue
                seen.add(mid)
                out.setdefault(pid, []).append((mid, note))
    return out


GROUPS = (  # how the children of a proposition read, from the proposition's side
    ("implies", True, "Implies", "Implies"),
    ("and", True, "Requires both", "Requires both"),
    ("or", True, "Either is enough", "Either is enough"),
    ("then", True, "Requires both, in order", "Requires both, in order"),
    ("implies", False, "Implied by", "Implied by each of"),
    ("and", False, "Part of", "Part of each of"),
    ("or", False, "One way for", "One way for each of"),
    ("then", False, "A step of", "A step of each of"),
)


def _bound_words(t: _Tree, link: dict, parent: str) -> str:
    """The bound a link gives the proposition above it in the tree, where it gives one: what it implies caps it, and
    what implies it lifts it."""
    bound = (t.edges.get(link["id"]) or {}).get("bound") or {}
    span = "x:interval:" + parent
    if bound.get("implying") == parent and bound["ceiling"] is not None and bound["ceiling"] < 1.0 - ZERO:
        return f'ceiling {gonum(span, chance(bound["ceiling"]))}'
    if bound.get("implied") == parent and bound["floor"] is not None and bound["floor"] > ZERO:
        return f'floor {gonum(span, chance(bound["floor"]))}'
    return ""


def _link_check(t: _Tree, link: dict) -> str:
    """An implication's check, where the fields do not decide it."""
    if link["relation"] != "implies":
        return ""
    d = t.reader.derived(link["id"], "implies")
    if _value(d, "by_fields"):
        return "by the numbers"
    answer = _value(d, "check")
    out = t.reader.computation(_basis(d, "check")).get("output") or {}
    if answer is not None and out.get("judgment"):
        disputed = answer < 0.5  # its answer, both ways of reading it, opens in the check
        return f'second model {go("x:check:" + out["judgment"], "disputes" if disputed else "agrees", "go bad" if disputed else "go")}'
    if t.reader.waits("implication_check", link["id"]):
        return f"second model {WAIT}"
    return ""


def _composite_words(t: _Tree, links: list[dict]) -> str:
    bound = (t.edges.get(links[0]["id"]) or {}).get("bound") or {}
    rule = bound.get("composite")
    if not rule or (rule["lo"] <= ZERO and rule["hi"] >= 1.0 - ZERO):
        return ""
    span = "x:interval:" + rule["of"]
    return f' · together {gonum(span, chance(rule["lo"]))}–{gonum(span, chance(rule["hi"]))}'


def _node(t: _Tree, pid: str, meta: str = "") -> str:
    store = t.reader.store
    is_root = pid == t.root
    head = "" if is_root else f'<a class="go nt" data-go="{e(pid)}">{e(title_of(store, pid))}</a>'
    read = ""
    if pid in t.reads:
        read = f'<div class="tag read">Read at {gonum("x:p:" + pid, operand(t.reads[pid]))}</div>'
    markets = "".join(_market_item(t.reader, link, t.point_market) for link in store.links_of(pid, "quoted_by"))
    faint = "".join(_faint_item(t.reader, mid, note, t.below) for mid, note in t.faint.get(pid, []))
    kids = _groups(t, pid)
    cls = "node" + (" root" if is_root else "") + (" read" if read else "")
    return (f'<li class="{cls}" data-k="n:{e(pid)}">{head}{meta}{read}'
            f'{f"<ul class=mkts>{markets}</ul>" if markets else ""}{kids}'
            f'{f"<ul class=mkts>{faint}</ul>" if faint else ""}</li>')


def _groups(t: _Tree, pid: str) -> str:
    children = t.nodes.get(pid, [])
    out = []
    for relation, outward, one, many in GROUPS:
        group = [c for c in children if c["link"]["relation"] == relation and c["out"] == outward]
        if not group:
            continue
        if relation == "then" and outward:
            group.sort(key=lambda c: c["link"]["stated"].get("step", 0))
        heading = (one if len(group) == 1 else many)
        if outward and relation in ("and", "or"):
            heading += _composite_words(t, [c["link"] for c in group])
        items = []
        for c in group:
            bits = [x for x in (_bound_words(t, c["link"], pid), _link_check(t, c["link"])) if x]
            if relation == "then" and outward:
                bits.insert(0, f'step {c["link"]["stated"].get("step")}')
            meta = f'<div class="rel">{" · ".join(bits)[:1].upper() + " · ".join(bits)[1:]}</div>' if bits else ""
            items.append(_node(t, c["id"], meta))
        out.append(f'<li class="group" data-k="g:{e(pid)}:{relation}:{int(outward)}"><div class="gh">{heading}</div>'
                   f'<ul class="kids">{"".join(items)}</ul></li>')
    return f'<ul class="groups">{"".join(out)}</ul>' if out else ""


def _market_price_line(reader: Reader, mid: str) -> str:
    """A market's quote in cents, as its venue shows it: its price, bid, and ask, and its depth, or, at a past as-of,
    the day its price is from."""
    price = reader.output("market_price", mid) or {}
    bits = []
    if price.get("price") is not None:
        bits.append(f'Price {gonum("x:price:" + mid, per(price["price"]))}')
    elif price:
        bits.append(f'{go("x:price:" + mid, "Shows no price", "go")}')
    elif reader.waits("market_price", mid):
        bits.append(f"Price {WAIT}")
    if price.get("bid") is not None:
        bits.append(f'bid {num(per(price["bid"]))}')
    if price.get("ask") is not None:
        bits.append(f'ask {num(per(price["ask"]))}')
    if price.get("past"):
        bits.append(f'from its history on {day(price.get("price_date"))}')
    elif price.get("depth") is not None:
        bits.append(f'depth {num(dollars(price["depth"]))}')
    return " · ".join(bits)


def _market_item(reader: Reader, link: dict, point_market: str | None = None) -> str:
    """A market under the proposition it asks, as a card: its question and its price; its venue, whether it is open,
    its bid, ask, and depth; and whether the second model reads it as asking exactly that, both ways."""
    mid, lid = link["to"], link["id"]
    listing = reader.output("market_listing", mid) or {}
    price = reader.output("market_price", mid) or {}
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    opened = price.get("open")
    complement = (link["stated"].get("transform") or "identity") == "complement"
    if price.get("price") is not None:
        figure = gonum("x:price:" + mid, per(price["price"]))
    else:
        figure = WAIT if reader.waits("market_price", mid) else go("x:price:" + mid, "no price", "go")
    meta = [venue] + (["open"] if opened else ["closed"] if opened is False else [])
    if price.get("bid") is not None:
        meta.append(f'bid {num(per(price["bid"]))}')
    if price.get("ask") is not None:
        meta.append(f'ask {num(per(price["ask"]))}')
    if price.get("past"):
        meta.append(f'from its history on {day(price.get("price_date"))}')
    elif price.get("depth") is not None:
        meta.append(f'depth {num(dollars(price["depth"]))}')
    lines = [f'<div class="mmeta">{" · ".join(meta)}</div>']
    if complement:
        q = _value(reader.derived(lid, "quoted_by"), "price")
        lines.append(f'<div class="mmeta">It asks the opposite: 1 − price'
                     f'{" = " + num(operand(q)) if q is not None else ""}</div>')
    d = reader.derived(lid, "quoted_by")
    judged = (reader.computation(_basis(d, "forward")).get("output") or {}).get("judgments") or {}
    answers = [a for f in ("forward", "converse") if judged.get(f) and (a := _value(d, f)) is not None]
    waiting = len(answers) < 2 and reader.waits("market_check", lid)
    if answers or waiting:
        state = ("" if not answers else '<span class="ck bad">Disputed</span>' if min(answers) < 0.5
                 else '<span class="ck">Matches</span>')
        opens = go("x:checks:" + lid, state, "go ckgo") if state else ""
        lines.append(f'<div class="mline checks">{opens}<span class="by">second model</span>{WAIT if waiting else ""}'
                     f'</div>')
    badge = '<div class="tag read">Its probability</div>' if mid == point_market else ""
    return (f'<li class="mkt{" priced" if badge else ""}" data-k="m:{e(lid)}">{badge}'
            f'<div class="mhead"><a class="go mq" data-go="{e(mid)}">{e(listing.get("question") or mid)}</a>'
            f'<span class="mpx">{figure}</span></div>{"".join(lines)}</li>')


def _faint_item(reader: Reader, mid: str, note: str, below: Iterable[str]) -> str:
    q = market_question(reader, mid) or mid
    why = f'<span>Not used.</span> {_to_note(note)}' if note in below else \
        f'<span>Not used:</span> {note_html(reader.store.stated_of(note).get("text"))}'
    return (f'<li class="mkt faint" data-k="f:{e(mid)}"><a class="go mq" data-go="{e(mid)}">{e(q)}</a>'
            f'<div class="mline">{_market_price_line(reader, mid)}</div><div class="why">{why}</div></li>')


def _searches(r: dict, *, open_: bool) -> str:
    s = r["searches"]
    n_m, n_w = s["market_searches"], s["web_searches"]
    if not n_m and not n_w:
        return '<p class="aside" data-k="searches">No searches recorded.</p>'
    counts = series_of([x for x in (f"{n_m} market search{'es' if n_m != 1 else ''}" if n_m else "",
                                    f"{n_w} web search{'es' if n_w != 1 else ''}" if n_w else "") if x])
    items = []
    for q in s["queries"]:
        params = q.get("params") or {}
        text = next((v for v in params.values() if isinstance(v, str) and v.strip()), None)
        rest = ", ".join(f"{k} {v}" for k, v in params.items() if v != text)
        words = f"{VENUES.get(q.get('venue'), q.get('venue'))}" + (f" for {quote(text)}" if text else "") + \
            (f" ({rest})" if rest else "")
        items.append(f'<li>{e(words)} · {q["returned"]} returned</li>')
    for q in s["web_queries"]:
        items.append(f'<li>The web for {e(quote(q.get("query") or ""))} · {q["returned"]} returned</li>')
    return (f'<details class="searches" data-k="searches"{" open" if open_ else ""}><summary>{e(counts)}</summary>'
            f'<ul>{"".join(items)}</ul></details>')


def _matched(reader: Reader, r: dict) -> str:
    """How it was matched to markets: a vertical tree from the event, through the propositions it reaches, to the
    markets under each, with the derivation of its probability marked and the searches behind it."""
    store, pid = reader.store, r["id"]
    nodes, order = _tree(store, pid)
    point = r["point"]
    edges = (reader.computation(r["basis"].get("lo") or _basis(reader.derived(pid, "Proposition"), "lo"))
             .get("output") or {}).get("edges") or {}
    t = _Tree(reader, pid, nodes, order, dict(point.get("reads") or {}),
              point.get("market") if point.get("rule") == "market" else None, edges, _faint(store, order), r["below"])
    none = "" if r["markets"] else \
        '<p class="plain" data-k="none">No market prices it or any part of it.</p>'
    tree = f'<ul class="tree" data-k="tree">{_node(t, pid)}</ul>'
    if not r["markets"] and not nodes[pid] and not t.faint:
        tree = ""
    body = _chance_chart(reader, r) + none + _how_chance(reader, r) + tree + _searches(r, open_=not r["markets"])
    return section("Markets", body, "matched", "matched")


def _figure_words(c: dict, unit: str | None, target: str) -> str:
    """A consequence's shock: where the metric ends with the event against without it, as the analyst states it or
    from the level the analyst states for one of the two."""
    gap = c.get("gap")
    if gap is None and c["meaning"] == "gap":
        gap = c["figure"]["value"]
    if gap is None:
        return f"Shock {WAIT}"
    figure, _, words = shock(gap, unit).partition(" ")  # the figure in figures, a unit in words after it
    return f'Shock {gonum(target, figure)}{" " + e(words) if words else ""}'


def _consequence_item(reader: Reader, r: dict, c: dict) -> str:
    """A metric the event moves: its name and its shock, then today's level and where it ends if the event happens and
    if not, then the basis of the figure the analyst states."""
    store, lid, mid = reader.store, c["link"], c["metric"]
    unit = unit_of(store, mid)
    target = f"x:consequence:{lid}"
    head = (f'<div class="chead"><a class="go ct" data-go="{e(mid)}">{e(heading(metric_label(store, mid)))}</a>'
            f'<span class="cfig">{_figure_words(c, unit, target)}</span></div>')
    lines = []
    if c["L_yes"] is not None and c["L_no"] is not None:
        lines.append(f'<div class="clevels">Today {num(level(c["c"], unit))} · '
                     f'Yes {gonum(target, level(c["L_yes"], unit))} · '
                     f'No {gonum(target, level(c["L_no"], unit))}</div>')
    elif reader.waits("consequence", lid):
        lines.append(f'<div class="clevels">{WAIT}</div>')
    basis = _stated_basis(reader, c["figure"], lid, below=r["below"])
    return f'<li class="cons" data-k="c:{e(lid)}">{head}{"".join(lines)}{basis}</li>'


def _holding_rows(reader: Reader, r: dict) -> str:
    """Each holding the event moves, if it happens and if not, with the names that contribute most through it: every
    name read from the row's working, where the row names only the largest."""
    pid = r["id"]
    rows_ = []
    working = (reader.computation((r.get("basis") or {}).get("by_holding")).get("output") or {}).get("by_holding")
    moved = [h for h in working or r["by_holding"] or []
             if not (money(h["if_yes"]) == "$0.00" and money(h["if_no"]) == "$0.00")]  # what moved, and only that
    widest = max((abs(h["if_yes"] - h["if_no"]) for h in moved), default=0.0)
    for h in moved:
        swing = h["if_yes"] - h["if_no"]
        size = f"{100 * abs(swing) / widest:.1f}%" if widest else "0%"
        bar = (f'<td class="hbar" aria-hidden="true"><span class="bar {"loss" if swing < 0 else "gain"}" '
               f'style="--w: {size}"></span></td>')
        target = f"x:holding:{pid}:{h['holding']}"
        named = [n["name"] for n in h["names"] if "name" in n and n["name"] != h["name"] and not pseudo(ticker_of(n["name"]))]
        through = (f'<div class="aside">{e(", ".join(name_label(n) for n in named[:3]))}{" …" if len(named) > 3 else ""}'
                   f'</div>') if named else ""
        rows_.append(f'<tr data-k="h:{e(h["holding"])}"><td>{go(h["name"], e(heading(name_label(h["name"]))), "go name")}{through}</td>'
                     f'{bar}<td>{gonum(target, money(h["if_yes"], sign=True), _tone(h["if_yes"]))}</td>'
                     f'<td>{gonum(target, money(h["if_no"], sign=True), _tone(h["if_no"]))}</td></tr>')
    if not rows_:
        return ""
    return (f'<table class="ifs byh" data-k="holdings"><thead><tr><th>Holding</th><th class="hbar">Full impact</th>'
            f'<th>Yes</th><th>No</th></tr></thead><tbody>{"".join(rows_)}</tbody></table>')


def _tone(x: float | None) -> str:
    shown = x is not None and money(x) != "$0"  # an amount that rounds to nothing is neither
    return "loss" if shown and x < 0 else "gain" if shown and x > 0 else ""


def _by_holding(reader: Reader, r: dict) -> str:
    """What the event does to each holding it moves, on Yes and on No, with a bar for its full impact."""
    if not r["consequences"]:
        return ""
    holdings = _holding_rows(reader, r)
    if not holdings and reader.waits("impact", r["id"]):
        holdings = f'<p data-k="holdings">{WAIT}</p>'
    return section("By holding", holdings, "byholding", "byholding") if holdings else ""


def _would_do(reader: Reader, r: dict) -> str:
    """What it moves: each consequence with its shock, what it means, its basis, and where it ends either way."""
    if not r["consequences"]:
        return ""
    items = "".join(_consequence_item(reader, r, c) for c in r["consequences"])
    return section("Shocks", f'<ul class="conss">{items}</ul>', "would", "would")


def _shown_below(store: Store, r: dict) -> list[str]:
    """The notes an opened event shows once, in its notes: those about the event, and those about more than one thing
    it shows. Every other note shows in place, where it bears."""
    shown = {r["id"], *(c["link"] for c in r["consequences"]), *r["decomposition"], *(m["market"] for m in r["markets"])}
    notes = {n["from"] for x in shown for n in store.links_of(x, "about", "in")}
    out = [n for n in notes if r["id"] in (about := {a["to"] for a in store.links_of(n, "about")}) or
           len(about & shown) > 1]
    return sorted(out, key=lambda n: int(n.split(":")[1]))


def _notes(reader: Reader, r: dict) -> str:
    if not r["below"]:
        return ""
    body = "".join(f'<div class="note" data-k="{e(n)}" data-note="{e(n)}">'
                   f'{fold(note_html(reader.store.stated_of(n).get("text")))}</div>' for n in r["below"])
    return section("Note" if len(r["below"]) == 1 else "Notes", body, "notes", "notes")


def _when_known(stated: dict, horizon_end: str | None, asof: str | None = None) -> str:
    """When an event can happen and where it would be known from, as labelled facts."""
    return f'<div class="when">{_dl([(k, e(v)) for k, v in _when_pairs(stated, horizon_end, asof)])}</div>'


def _when_pairs(stated: dict, horizon_end: str | None, asof: str | None = None) -> list[tuple[str, str]]:
    """When an event can happen: any time in its window, the level on its date, or the decision on its date; and where
    it would be known from, in the analyst's words."""
    end = day(stated.get("window_end"))
    kind = stated.get("kind")
    if kind == "hitting":
        begins = stated.get("window_start")
        start = f"from {day(begins)} " if begins and begins != asof else ""
        when = f"Any time {start}to {end}"
    elif kind == "level_at_date":
        when = f"The level on {end}"
    else:
        when = f"The decision on {end}"
    pairs = [("When", when)]
    if stated.get("resolution_source"):
        pairs.append(("Known from", stated["resolution_source"]))
    return pairs


def _chance_chart(reader: Reader, r: dict) -> str:
    """The event's probability history, from the history of the market that prices it, with the range markets imply
    today at its end."""
    points = chance_series(reader, r)
    found = priced_by(reader, r)
    if len(points) < 2 or not found:
        return ""
    mid, complement = found
    lo, hi = r["lo"], r["hi"]
    band = [lo, hi] if lo is not None and hi is not None and (lo > ZERO or hi < 1.0 - ZERO) else None
    q = go(mid, e(quote(market_question(reader, mid) or mid)))
    return (f'<div class="chancechart" data-k="chancechart"><h3 class="sub">Probability history</h3>'
            f'{chart(points, kind="chance", ranges=True, band=band, label="Probability history")}'
            f'<p class="aside">{"1 − price of " if complement else "Price of "}{q}</p></div>')


def event_view(reader: Reader, pid: str) -> dict:
    """An opened event, the member's reading first and the reviewer's depth after: when it can happen and how it would
    be known, its definition folded below its first lines; its figures; what it does to each holding; the analyst's
    note; what it moves; and how it was matched to markets, under its probability history."""
    store = reader.store
    r = row(store, pid)
    r["below"] = _shown_below(store, r)
    rows = sentences(reader, r)
    html = (f'<p class="kicker">Event</p><h2 class="title">{title_html(r["title"])}</h2>'
            f'<div class="text" data-k="text">{fold(paragraphs(r["text"]))}</div>'
            f'{_when_known(store.stated_of(pid), store.horizon_end(), store.meta("asof"))}'
            f'{f"<div class=sentences data-k=sentences>{figures_html(rows)}</div>" if rows else ""}'
            f'{_by_holding(reader, r)}{_notes(reader, r)}{_would_do(reader, r)}{_matched(reader, r)}')
    return {"title": r["title"], "html": html}


# ---------------------------------------------------------------- the inspector


def _dl(pairs: list[tuple[str, str]]) -> str:
    return "<dl class=fields>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs if v) + "</dl>"


def _neg(text: str, x: float | None) -> str:
    """A figure in parentheses where it is negative, so that what is subtracted reads plainly."""
    return f"({text})" if x is not None and x < 0 else text


def _inspect_point(reader: Reader, pid: str) -> dict:
    """The probability: the price of the market that asks it, the derivation as a receipt, or the analyst's estimate,
    each with its basis."""
    store = reader.store
    d = reader.derived(pid, "Proposition")
    p, cid = _value(d, "p"), _basis(d, "p")
    title = title_of(store, pid)
    if p is None:
        head = hero("Probability", context=go(pid, e(title)))
        return {"title": f"Probability: {title}", "html": head + (WAIT if reader.waits("point", pid) else "")}
    out = reader.computation(cid).get("output") or {}
    rule = out.get("rule")
    tag = {"market": pill("Market price", "teal"), "derivation": pill("Derived"),
           "chance": pill("The analyst's estimate")}.get(rule, "")
    head = hero("Probability", figure=e(chance(p)), context=go(pid, e(title)), pills=[tag])
    body = []
    if rule == "market":
        mid = out["market"]
        q = market_question(reader, mid) or mid
        shown = reader.output("market_price", mid) or {}
        link = store.link_by_id(out["link"]) or {}
        complement = (link.get("stated") or {}).get("transform") == "complement"
        rows = [("Market", go(mid, e(q)), gonum("x:price:" + mid, per(shown.get("price"))))]
        if complement:
            total = (eqn("P(E)"), "it asks the opposite: one minus its price", num(operand(p)))
            formula = [eqn(f"P(E) = 1 − price = 1 − {operand(shown.get('price'))} = {operand(p)}")]
        else:
            total, formula = (eqn("P(E)"), "its price", num(operand(p))), []
        body.append(f'<h3 class="sub">How it is set</h3>{receipt(rows, total, formula)}')
        others = [m for m in out.get("markets") or [] if m["market"] != mid]
        if others:
            best = next(m for m in out["markets"] if m["market"] == mid)
            listed = [("", go(m["market"], e(market_question(reader, m["market"]) or m["market"])),
                       num(dollars(m.get("depth")))) for m in others]
            body.append(f'<h3 class="sub">Other markets that ask it</h3><p class="aside">The deepest of '
                        f'{len(out["markets"])} sets it: this one, with {num(dollars(best.get("depth")))} at its '
                        f'quotes. The others, by depth:</p>{receipt(listed)}')
        body.append(_market_checks(reader, out["link"]))
    elif rule == "chance":
        body.append(f'<h3 class="sub">Basis</h3>{_stated_basis(reader, out["chance"], pid)}')
    elif rule == "derivation":
        body.append(f'<h3 class="sub">How it is worked out</h3>{_derivation(reader, pid, out, p)}')
    return {"title": f"Probability: {title}", "html": head + "".join(body) + full(cid)}


def _market_checks(reader: Reader, lid: str, *, heading: bool = True) -> str:
    """The second model's check that the market asks exactly the proposition, both ways, as two statements and their
    probabilities."""
    link = reader.store.link_by_id(lid) or {}
    side = "No" if (link.get("stated") or {}).get("transform") == "complement" else "Yes"
    d = reader.derived(lid, "quoted_by")
    judged = (reader.computation(_basis(d, "forward")).get("output") or {}).get("judgments") or {}
    words = {"forward": f"If this is true, the market resolves {side}",
             "converse": f"If the market resolves {side}, this is true"}
    rows = [("", words[f], gonum("x:check:" + judged[f], chance(_value(d, f))))
            for f in ("forward", "converse") if judged.get(f) and _value(d, f) is not None]
    if not rows:
        return ""
    return ('<h3 class="sub">Second model</h3>' if heading else "") + receipt(rows)


def _link_sentence(reader: Reader, lid: str, side: str, edges: dict) -> str:
    """One link that sets a bound: a market's bid or ask, or a relation between two propositions."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None:
        return ""
    rel, a, b = link["relation"], link["from"], link["to"]
    A, B = go(a, e(quote(title_of(store, a)))), go(b, e(quote(title_of(store, b))))
    if rel == "quoted_by":
        bound = (edges.get(lid) or {}).get("bound") or {}
        q = go(b, e(quote(market_question(reader, b) or b)))
        complement = link["stated"].get("transform") == "complement"
        if side == "lo":
            words = "1 − ask on" if complement else "Bid on"
            return f'{words} {q}, for {A}: {gonum("x:price:" + b, operand(bound.get("lo")))}'
        words = "1 − bid on" if complement else "Ask on"
        return f'{words} {q}, for {A}: {gonum("x:price:" + b, operand(bound.get("hi")))}'
    if rel == "implies":
        return f"{A} implies {B}"
    if rel == "and":
        return f"{A} requires {B}"
    if rel == "or":
        return f"{B} is enough for {A}"
    if rel == "then":
        return f"{A} requires {B}, step {link['stated'].get('step')}"
    return ""


def _inspect_interval(reader: Reader, pid: str) -> dict:
    """The range markets imply: its floor and its ceiling, with the coverage they leave, then what sets each, link by
    link."""
    store = reader.store
    d = reader.derived(pid, "Proposition")
    lo, hi, cov, cid = _value(d, "lo"), _value(d, "hi"), _value(d, "coverage"), _basis(d, "lo")
    out = reader.computation(cid).get("output") or {}
    node = (out.get("nodes") or {}).get(pid) or {}
    edges = out.get("edges") or {}
    title = title_of(store, pid)
    shown = f"{chance(lo)}–{chance(hi)}" if lo is not None and hi is not None else ""
    head = hero("Range markets imply", figure=e(shown), context=go(pid, e(title)))
    if lo is None or hi is None:
        return {"title": f"Range: {title}", "html": head + full(cid)}
    chains = {}
    for side in ("lo", "hi"):
        links = node.get(f"{side}_from") or []
        quoted = [lid for lid in links if (store.link_by_id(lid) or {}).get("relation") == "quoted_by"]
        chains[side] = [x for x in (_link_sentence(reader, lid, side, edges)
                                    for lid in quoted + [x for x in links if x not in quoted]) if x]
    said = lambda side: "set by the markets below" if chains[side] else "no market sets it"  # noqa: E731
    body = [receipt([("Floor", said("lo"), num(operand(lo))), ("Ceiling", said("hi"), num(operand(hi)))],
                    ("Coverage", "", num(operand(cov))),
                    [eqn("coverage = 1 − (ceiling − floor)"), eqn(f"= 1 − ({operand(hi)} − {operand(lo)}) = {operand(cov)}")])]
    for side, words in (("lo", "What sets the floor"), ("hi", "What sets the ceiling")):
        if chains[side]:
            body.append(f'<h3 class="sub">{words}</h3><ol class="chain">'
                        + "".join(f"<li>{x}</li>" for x in chains[side]) + "</ol>")
    return {"title": f"Range: {title}", "html": head + "".join(body) + full(cid)}


def _book_table(book: dict) -> str:
    """A market's book as a ladder: the best five bids and asks side by side, best first, each with a bar for how many
    contracts stand at it."""
    bids, asks = (book.get("bids") or [])[:5], (book.get("asks") or [])[:5]
    n = max(len(bids), len(asks))
    if not n:
        return ""
    top = max([lv[1] for lv in bids + asks] or [1]) or 1

    def side(lv, kind: str) -> str:
        if not lv:
            return "<td></td><td></td>"
        size = f'<td class="sz {kind}" style="--w:{100 * lv[1] / top:.1f}%">{lv[1]:,.0f}</td>'
        price = f'<td class="px {kind}">{per(lv[0])}</td>'
        return size + price if kind == "bid" else price + size

    rows = "".join(f'<tr>{side(bids[i] if i < len(bids) else None, "bid")}'
                   f'{side(asks[i] if i < len(asks) else None, "ask")}</tr>' for i in range(n))
    return ('<table class="ladder"><thead><tr><th>Contracts</th><th>Bid</th><th>Ask</th><th>Contracts</th></tr>'
            f'</thead><tbody>{rows}</tbody></table>')


def _inspect_price(reader: Reader, mid: str) -> dict:
    """A market's price as its venue shows it: the quote, how the price follows from it, and the book."""
    v = reader.value("market_price", mid)
    out = (v.output if v else None) or {}
    q = market_question(reader, mid) or mid
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    price, bid, ask = out.get("price"), out.get("bid"), out.get("ask")
    status = pill("Open", "teal") if out.get("open") else pill("Closed") if out.get("open") is False else ""
    head = hero("Price", figure=e(per(price)) if price is not None else "No price", context=go(mid, e(q)),
                pills=[pill(venue), status])
    midpoint = out.get("rule") == "midpoint" and not out.get("past")  # its bid and ask show in its workings
    body = [stats([("Bid", "" if midpoint else num(per(bid)), ""), ("Ask", "" if midpoint else num(per(ask)), ""),
                   ("Depth", num(dollars(out.get("depth"))) if out.get("depth") is not None else "",
                    "top five levels, smaller side"),
                   ("As a probability", num(operand(price)) if price is not None else "", "")])]
    if out.get("past"):
        body.append(receipt([], ("Price", f"its history on {day(out.get('price_date'))}", num(per(price))),
                            [eqn("bid, ask = price ∓ 1¢")]))
    elif split(mid)[0] == "hyperliquid" and price is not None:
        body.append(receipt([], ("Price", "the mid Hyperliquid publishes", num(per(price)))))
    elif out.get("rule") == "midpoint":
        body.append(receipt([("Bid", "", num(per(bid))), ("Ask", "", num(per(ask)))],
                            ("Price", "the midpoint", num(per(price))), [eqn("price = (bid + ask) / 2")])
                    + f'<p class="aside">{venue} shows the midpoint while the bid and the ask are within 10¢.</p>')
    elif price is not None:
        why = "the bid and the ask are more than 10¢ apart" if bid is not None and ask is not None else \
            f"it has no {'bids' if bid is None else 'asks'}"
        body.append(receipt([], ("Price", "the last trade", num(per(price))))
                    + f'<p class="aside">{venue} shows the last trade: {why}.</p>')
    else:
        body.append('<p class="aside">No price: the book is wide or one-sided, and it hasn\'t traded.</p>')
    if out.get("book"):
        body.append(f'<h3 class="sub">Book</h3>{_book_table(out["book"])}')
    return {"title": f"Price: {q}", "html": head + "".join(body) + full(v.basis if v else None)}


def _evidence_item(store: Store, item: dict) -> str:
    content = item.get("content") or {}

    def shown(v: Any) -> str:
        if isinstance(v, dict):
            return _dl([(e(k), shown(x)) for k, x in v.items()])
        if isinstance(v, list):
            return "".join(f"<p>{e(x)}</p>" for x in v)
        if isinstance(v, bool):
            return "yes" if v else "no"
        return fold(e(v)) if isinstance(v, str) and len(v) > 320 else e(v)  # a long text shows its first lines

    ref = item.get("ref")
    head = go(ref, e(label(store, ref) or ref)) if ref and ref != "portfolio" and store.type_of(ref) else ""
    return f'<div class="evidence">{f"<div class=evh>{head}</div>" if head else ""}{shown(content)}</div>'


def _inspect_check(reader: Reader, jid: str) -> dict:
    store = reader.store
    try:
        j = store.get(jid)
    except StoreError:
        return _missing()
    answer = j.get("answer")
    state = (pill("Holds", "teal") if answer >= 0.5 else pill("Disputed", "rust")) if isinstance(answer, (int, float)) else ""
    head = hero("Second model", figure=e(chance(answer)), pills=[state])
    body = [f'<h3 class="sub">Question</h3><blockquote class="statement">{e(j["statement"])}</blockquote>',
            '<h3 class="sub">What it read</h3>' + "".join(_evidence_item(store, x) for x in j["inputs"]),
            f'<p class="aside">Model {e(j["model"])}</p>']
    return {"title": "Second model", "html": head + "".join(body) + full(jid)}


def _today(reader: Reader, mid: str, unit: str | None) -> str:
    """Today's level and where it comes from: a holding's or a proxy's latest price, or the analyst's level from a
    page."""
    store = reader.store
    if schema.PROXY_METRIC_RE.match(mid):
        out = reader.output("proxy_price", mid) or {}
        shown = num(level(out.get("current_value"), unit))
        what = {"last trade": "last trade", "latest minute bar": "latest minute's close",
                "latest hourly bar": "latest hourly price"}.get(out.get("from"), "price")
        if out.get("time"):
            return f'{shown}<div class="aside">{what} {moment(out["time"])}</div>'
        return f'{shown}<div class="aside">close on {day(out.get("asof"))}</div>'
    stated = store.stated_of(mid)
    sid = (store.citations_of(mid) or {}).get("current_value")
    src = f' · {go(sid, e(host(store.stated_of(sid).get("url"))))}' if sid else ""
    return f'{num(level(stated.get("current_value"), unit))}<div class="aside">as of {day(stated.get("asof"))}{src}</div>'


def _inspect_consequence(reader: Reader, lid: str) -> dict:
    """A shock: where the metric stands today and where it ends if the event happens and if not; the figure the analyst
    states and its basis; how the levels follow from it; then the shock to the factor or the name the metric
    tracks."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None:
        return _missing()
    pid, mid = link["from"], link["to"]
    unit = unit_of(store, mid)
    d = reader.derived(lid, "consequence")
    cid = _basis(d, "gap") or _basis(d, "L_yes")
    out = reader.computation(cid).get("output") or {}
    metric = metric_label(store, mid)
    fig = out.get("figure", link["stated"]["value"]["value"])
    meaning = link["stated"]["meaning"]
    c, p, gap = out.get("c"), out.get("p"), out.get("gap")
    P = operand(p)
    head = hero(f"Shock to {e(metric)}", figure=e(shock(gap, unit)) if gap is not None else "",
                context=go(pid, e(title_of(store, pid))))
    stated = {"level_if_happens": "the level if Yes", "level_if_not": "the level if No"}.get(meaning, "the shock")
    facts = [("The analyst states", num(level(fig, unit) if meaning != "gap" else shock(fig, unit)), stated)]
    if c is not None and (out.get("traded") or meaning != "gap"):
        facts.append(("Today", num(level(c, unit)), ""))
        if out.get("L_yes") is not None:
            facts += [("Yes", num(level(out["L_yes"], unit)), ""), ("No", num(level(out["L_no"], unit)), "")]
        facts.append(("Probability", gonum("x:p:" + pid, P), ""))
    body = [stats(facts)]
    basis = _stated_basis(reader, link["stated"]["value"], lid)
    if basis:
        body.append(f'<h3 class="sub">Basis</h3>{basis}')
    G = _neg(shock(gap, unit), gap)
    rows = []
    if gap is not None and meaning == "level_if_happens":
        rows.append(("Shock", worked("(Yes − today) / (1 − p)",
                                     f"({level(fig, unit)} − {level(c, unit)}) / (1 − {P})"), num(shock(gap, unit))))
    elif gap is not None and meaning == "level_if_not":
        rows.append(("Shock", worked("(today − No) / p", f"({level(c, unit)} − {level(fig, unit)}) / {P}"),
                     num(shock(gap, unit))))
    if out.get("L_yes") is not None:
        rows += [("Yes", worked("today + (1 − p) × shock", f"{level(c, unit)} + (1 − {P}) × {G}"),
                  num(level(out["L_yes"], unit))),
                 ("No", worked("today − p × shock", f"{level(c, unit)} − {P} × {G}"), num(level(out["L_no"], unit)))]
    if rows:
        body.append(f'<h3 class="sub">How the levels follow</h3>{receipt(rows)}')
    elif gap is not None and not out.get("traded"):
        body.append('<p class="aside">No market prices it: no levels to place.</p>')
    body.append(_tracks(reader, pid, mid, lid))
    impact = _basis(reader.derived(pid, "Proposition"), "D")
    return {"title": f"Shock to {metric}",
            "html": head + "".join(body) + full(cid) + (full(impact, "Full impact, full details") if impact else "")}


def _tracks(reader: Reader, pid: str, mid: str, lid: str) -> str:
    """Through the metric's tracks link: the shock to the factor, less what the factors above it imply, its residual
    shock; or the shock to the name, less what its betas give, its residual move."""
    store = reader.store
    unit = unit_of(store, mid)
    cid = _basis(reader.derived(pid, "Proposition"), "D")
    rec = reader.computation(cid)
    out = (rec.get("output") or {}).get("consequences", {}).get(mid)
    if not out:
        return ""
    if not out.get("sets"):
        return '<p class="aside">It tracks no factor and no holding: it moves nothing in the portfolio.</p>'
    found = next((c for c in (rec.get("inputs") or {}).get("consequences", []) if c["metric"] == mid), {})
    conversions = {s["target"]: s["conversion"] for s in found.get("sets") or []}
    coefficients = {s["target"]: s.get("coefficients") for s in found.get("sets") or []}
    names = channel_names(store)
    today = "price" if schema.PROXY_METRIC_RE.match(mid) else "level"
    blocks = []
    for s in out["sets"]:
        if "channel" in s:
            conv = conversions.get(s["channel"], "price")
            factor = names.get(s["channel"], s["channel"])
            if conv == "price":
                first = ("Factor shock", worked(f"shock / {today}", f"{shock(out['gap'], unit)} / {level(out['c'], unit)}"),
                         num(operand(s["raw"], sign=True)))
            else:
                first = ("Factor shock", eqn(f"shock × −{conv[len('yield('):-1]} / 100"), num(operand(s["raw"], sign=True)))
            rows, total = [first], None
            if coefficients.get(s["channel"]):
                rows.append(("From the factors above", "", num(operand(s["implied"], sign=True))))
                total = ("Residual shock", eqn("factor shock − from the factors above"),
                         num(operand(s["component"], sign=True)))
            blocks.append(f'<h3 class="sub">{go(s["channel"], e(heading(factor)))}</h3>{receipt(rows, total)}')
        else:
            ticker = name_label(s["name"])
            rows = [(f"{e(ticker)} shock", worked("shock / price", f"{shock(out['gap'], unit)} / {level(out['c'], unit)}"),
                     num(operand(s["raw"], sign=True))),
                    ("From its betas", "", num(operand(s["betas"], sign=True)))]
            blocks.append(f'<h3 class="sub">{go(s["name"], e(ticker))}</h3>'
                          + receipt(rows, ("Residual move", eqn("shock − from its betas"), num(operand(s["d"], sign=True)))))
    return "".join(blocks)


def _inspect_swing(reader: Reader, pid: str) -> dict:
    """The full impact: each factor's dollar beta times its shock, and each name's exposure times its residual move,
    adding to it; then what is priced in, Yes and No, which differ by it, and the share of the portfolio."""
    store = reader.store
    d = reader.derived(pid, "Proposition")
    cid = _basis(d, "D")
    rec = reader.computation(cid)
    out, inputs = rec.get("output") or {}, rec.get("inputs") or {}
    title = title_of(store, pid)
    if "D" not in out:
        return {"title": "Full impact", "html": hero("Full impact", context=go(pid, e(title))) + full(cid)}
    names = channel_names(store)
    g = inputs.get("g") or {}
    setter = {}
    for metric, c in (out.get("consequences") or {}).items():
        for s in c.get("sets") or []:
            setter[s.get("channel") or s.get("name")] = c["link"]
    p, D = out["p"], out["D"]
    P = operand(p)
    head = hero("Full impact", figure=e(money(D, sign=True)), context=go(pid, e(title)),
                pills=[pill(f"Probability {chance(p)}")])
    entries = []  # each line's label, what it multiplies, and its dollars
    for c, amount in sorted(out["contributions"].items(), key=lambda kv: -abs(kv[1])):
        if not out["u_E"].get(c):
            continue
        moved = gonum(f"x:consequence:{setter[c]}", operand(out["u_E"][c], sign=True)) if c in setter else \
            num(operand(out["u_E"][c], sign=True))
        entries.append((go(c, e(heading(names.get(c, c)))), f'{gonum(c, money(g.get(c)))} × {moved}', amount))
    own = out.get("own") or {}
    held = (reader.computation((inputs.get("lookthrough") or {}).get("basis")).get("output") or {}).get("e", {})
    for n, amount in sorted(own.items(), key=lambda kv: -abs(kv[1])):
        moved = gonum(f"x:consequence:{setter[n]}", operand(out["d"][n], sign=True)) if n in setter else \
            num(operand(out["d"][n], sign=True))
        entries.append((f"{go(n, e(name_label(n)))}, residual", f"{gonum(n, dollars(held.get(n)))} × {moved}", amount))
    terms = to_cents([x[2] for x in entries], D)  # to the cent, adding to the full impact as shown
    rows = [(label_, what, num(money(t, sign=True))) for (label_, what, _), t in zip(entries, terms)]
    unset = [c for c in out["contributions"] if not out["u_E"].get(c)]
    other = (f'<p class="aside">No residual shock: {series_of([e(heading(names.get(c, c))) for c in unset])}.</p>'
             if unset and len(unset) < len(out["contributions"]) else "")
    body = ['<h3 class="sub">Dollar beta × shock</h3>' + receipt(rows, ("Full impact", "", num(money(D, sign=True)))) + other]
    priced = [("Yes", worked("(1 − p) × full impact", f"(1 − {P}) × {_neg(money(D), D)}"),
               num(money(out["pnl_yes"], sign=True))),
              ("No", worked("−p × full impact", f"−{P} × {_neg(money(D), D)}"), num(money(out["pnl_no"], sign=True))),
              ("Full impact", eqn("Yes − No"), num(money(D, sign=True)))]
    if out.get("share_yes") is not None:
        priced.append(("Share of portfolio", worked("Yes / value",
                                                    f"{_neg(money(out['pnl_yes']), out['pnl_yes'])} / {dollars(out['V'])}"),
                       num(share(out["share_yes"]))))
    body.append(f'<h3 class="sub">Priced in</h3>{receipt(priced)}')
    return {"title": "Full impact", "html": head + "".join(body) + full(cid)}


def _inspect_emove(reader: Reader, pid: str) -> dict:
    """The expected move: each outcome's size weighed by its probability. What is priced in makes the expected signed
    move nothing, so the two halves are equal and the move is a size either way; then the worst case."""
    store = reader.store
    cid = _basis(reader.derived(pid, "Proposition"), "D")
    out = reader.computation(cid).get("output") or {}
    title = title_of(store, pid)
    if out.get("emove") is None:
        return {"title": "Expected move", "html": hero("Expected move", context=go(pid, e(title))) + full(cid)}
    p, yes, no, D = out["p"], out["pnl_yes"], out["pnl_no"], out["D"]
    P = operand(p)
    halves = to_cents([p * abs(yes), (1.0 - p) * abs(no)], out["emove"])  # to the cent, adding to the move as shown
    head = hero("Expected move", figure=e(either(out["emove"])), context=go(pid, e(title)),
                pills=[pill(f"Probability {chance(p)}")])
    rows = [("Yes", worked("p × |Yes|", f"{P} × {money(abs(yes))}"), num(money(halves[0]))),
            ("No", worked("(1 − p) × |No|", f"(1 − {P}) × {money(abs(no))}"), num(money(halves[1])))]
    formula = [eqn("Expected move = 2 × p × (1 − p) × |full impact|"),
               eqn(f"= 2 × {P} × (1 − {P}) × {money(abs(D))}")]
    worst = [("Worst case", "the more negative of Yes and No", num(money(out["worst"], sign=True)))]
    body = (f'<h3 class="sub">Each outcome by its probability</h3>'
            f'{receipt(rows, ("Expected move", "", num(either(out["emove"]))), formula)}'
            f'<h3 class="sub">Worst case</h3>{receipt(worst)}')
    return {"title": "Expected move", "html": head + body + full(cid)}


def _inspect_checks(reader: Reader, lid: str) -> dict:
    """How the second model reads a market: the claim that it asks exactly its proposition, both ways, each with its
    answer, each opening to its question and the evidence read."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None or link["relation"] != "quoted_by":
        return _missing()
    pid, mid = link["from"], link["to"]
    d = reader.derived(lid, "quoted_by")
    answers = [a for a in (_value(d, "forward"), _value(d, "converse")) if a is not None]
    state = "" if not answers else "Disputed" if min(answers) < 0.5 else "Matches"
    q = market_question(reader, mid) or mid
    head = hero("Second model", title=e(state), context=f"{go(mid, e(quote(q)))} for {go(pid, e(quote(title_of(store, pid))))}")
    side = "No" if (link.get("stated") or {}).get("transform") == "complement" else "Yes"
    judged = (reader.computation(_basis(d, "forward")).get("output") or {}).get("judgments") or {}
    words = {"forward": f"If this is true, the market resolves {side}",
             "converse": f"If the market resolves {side}, this is true"}
    rows = [("", words[f], gonum("x:check:" + judged[f], chance(_value(d, f))))
            for f in ("forward", "converse") if judged.get(f) and _value(d, f) is not None]
    body = f'<div class="claims">{receipt(rows)}</div>' if rows else (WAIT if reader.waits("market_check", lid) else "")
    return {"title": f"Second model: {state}" if state else "Second model", "html": head + body}


def _inspect_holding(reader: Reader, rest: str) -> dict:
    """What a holding does if the event happens: its dollars at the betas they move with, and each company's own shock
    on its dollars there, which together are its contribution; then what is priced in. A fund with under a year of
    prices moves as its holdings do, so its names are listed, each at its own move."""
    store = reader.store
    pid, hid = rest[:rest.index(":", rest.index(":") + 1)], rest[rest.index(":", rest.index(":") + 1) + 1:]
    cid = _basis(reader.derived(pid, "Proposition"), "D")
    rec = reader.computation(cid)
    out, inputs = rec.get("output") or {}, rec.get("inputs") or {}
    h = next((x for x in out.get("by_holding") or [] if x["holding"] == hid), None)
    if h is None:
        return _missing()
    ticker = heading(name_label(h["name"]))
    head = hero(f"{e(ticker)}, full impact", figure=e(money(h["D"], sign=True)),
                context=go(pid, e(title_of(store, pid))))
    outcomes = stats([("Yes", num(money(h["if_yes"], sign=True), _tone(h["if_yes"])), ""),
                      ("No", num(money(h["if_no"], sign=True), _tone(h["if_no"])), "")])
    sensitivity = (((reader.output("g", "portfolio") or {}).get("by_holding") or {}).get(hid) or {}).get("sensitivity")
    at, own = h.get("at_betas") or {}, h.get("own") or []
    entries = []  # each line's label, what it multiplies, and its contribution
    if at.get("dollars") and abs(at.get("D") or 0.0) >= 0.005:
        how = "at its holdings' betas" if sensitivity == "holdings" else "at its betas"
        entries.append((f"{e(ticker)}, {how}", f'{num(dollars(at["dollars"]))} × {num(operand(at.get("move"), sign=True))}',
                        at["D"]))
    for x in own:
        entries.append((f'{go(x["name"], e(name_label(x["name"])))}, residual',
                        f'{num(dollars(x["dollars"]))} × {num(operand(x["d"], sign=True))}', x["D"]))
    terms = to_cents([x[2] for x in entries], h["D"])  # to the cent, adding to the contribution as shown
    rows = [(label_, what, num(money(t, sign=True))) for (label_, what, _), t in zip(entries, terms)]
    body = [outcomes, ('<h3 class="sub">Exposure × move</h3>' + receipt(rows, ("Full impact", "", num(money(h["D"], sign=True)))))
            if rows else '<p class="aside">Nothing it holds moves with the event.</p>']
    if sensitivity == "holdings" and h["names"]:
        reached = ((reader.computation((inputs.get("lookthrough") or {}).get("basis")).get("output") or {})
                   .get("by_holding") or {}).get(hid) or {}
        listed = [(go(n["name"], e(name_label(n["name"]))),
                   f'{num(dollars(reached.get(n["name"])))} × {num(operand(n.get("move"), sign=True))}',
                   num(money(n["D"], sign=True))) for n in h["names"][:LISTED]]
        more = f'<p class="aside">{len(h["names"]) - LISTED} more.</p>' if len(h["names"]) > LISTED else ""
        body.append(f'<h3 class="sub">By name</h3>{receipt(listed)}{more}')
    P = operand(out["p"])
    priced = [("Yes", worked("(1 − p) × full impact", f"(1 − {P}) × {_neg(money(h['D']), h['D'])}"),
               num(money(h["if_yes"], sign=True))),
              ("No", worked("−p × full impact", f"−{P} × {_neg(money(h['D']), h['D'])}"),
               num(money(h["if_no"], sign=True)))]
    body.append(f'<h3 class="sub">Priced in</h3>{receipt(priced)}')
    return {"title": f"{ticker}, full impact", "html": head + "".join(body) + full(cid)}


# ---------------------------------------------------------------- a holding, and a factor, opened


def price_points(reader: Reader, name: str, subject: str) -> list:
    """A ticker's daily closes over the two years its price read, to its latest mark: the series the store fetched
    for its price, read as the price reads it."""
    v = reader.value(name, subject)
    if v is None or v.output is None:
        return []
    item = (reader.computation(v.basis).get("inputs") or {}).get("series") or {}
    if not (item.get("ref") or {}).get("data"):
        return []
    found = sorted(closes(item, reader.engine.load).items())
    now = (v.output.get("prices") if name == "price" else
           {"price": v.output.get("current_value"), "session": v.output.get("asof")}) or {}
    if now.get("price") is not None and now.get("session"):
        found = [x for x in found if x[0] < now["session"]] + [(now["session"], now["price"])]
    return [list(x) for x in found]


def _quote_lines(prices: dict) -> str:
    """A price as a broker's header gives it: the price and its day change in dollars and percent, then what the price
    is and when."""
    change, moved = prices.get("session_change"), prices.get("session_move")
    head = f'<div class="quote"><span class="px">{num(level(prices.get("price"), "USD"))}</span>'
    if change is not None and moved is not None:
        head += f'<span class="chg {_sign_tone(change)}">{num(_signed_usd(change))} ({num(move(moved))})</span>'
    head += "</div>"
    what = {"last trade": "Last trade", "latest minute bar": "Latest minute's close",
            "latest hourly bar": "Latest hourly price", "close": "Close"}.get(prices.get("from"), "Price")
    when = f"{what} {moment(prices['time'])}" if prices.get("time") else f"{what} on {day(prices.get('session'))}"
    return head + f'<p class="aside">{when}</p>'


def _signed_usd(x: float) -> str:
    r = _half_up(abs(x), "0.01")
    return f"{_lead(x, r == 0, True)}${r:,.2f}"


def _price_chart(points: list, label: str, mark: dict | None = None, shown: str = "1Y") -> str:
    """A price over time, opening on `shown`, a year, unless the series is shorter."""
    return chart(points, kind="price", ranges=True, mark=mark, label=label, shown=shown)


def _events_moving(reader: Reader, *, holding: str | None, nid: str) -> str:
    """The events that move a holding, or a name, most: each with what it does if it happens and if not."""
    found = _ledger(reader)
    items = []
    for r in (found or {}).get("rows") or []:
        rec = reader.computation((r.get("basis") or {}).get("D"))
        out = rec.get("output") or {}
        if holding:
            share = next((x for x in out.get("by_holding") or [] if x["holding"] == holding), None)
            target = f"x:holding:{r['id']}:{holding}"
        else:
            share = next((x for x in out.get("by_name") or [] if x["name"] == nid), None)
            target = f"x:swing:{r['id']}"
        if share and money(share["D"]) != "$0":
            items.append((abs(share["D"]), r, share, target))
    items.sort(key=lambda x: -x[0])
    if not items:
        return ""
    rows_ = "".join(f'<tr data-k="ev:{e(r["id"])}"><td>{go(r["id"], e(r["title"]))}'
                    f'<div class="aside">Probability {chance(r["p"])}</div></td>'
                    f'<td>{gonum(t, money(s["if_yes"], sign=True), _tone(s["if_yes"]))}</td>'
                    f'<td>{gonum(t, money(s["if_no"], sign=True), _tone(s["if_no"]))}</td></tr>'
                    for _, r, s, t in items[:EVENTS_SHOWN])
    return ('<h3 class="sub">Events</h3><table class="ifs"><thead><tr><th>Event</th>'
            f'<th>Yes</th><th>No</th></tr></thead><tbody>{rows_}</tbody></table>')


def _sensitivities(reader: Reader, b: dict, fit: dict) -> str:
    """A name's betas, one to each factor in order, each factor below the first net of the factors above it: fitted on
    its own daily returns, a fund's on the fund's, or, for a fund with too few, weighted from its holdings."""
    store = reader.store
    names = channel_names(store)
    order = [c["id"] for c in sorted(store.query("Channel"), key=lambda c: c["stated"]["order"])]
    shown = [c for c in order if c in b]
    rows_ = "".join(f'<tr><td>{go(c, e(heading(names.get(c, c))))}</td><td>{num(sens(b[c]))}</td></tr>' for c in shown)
    if fit.get("sensitivity") == "holdings":
        how = (f"{fit['n']} daily returns of its own, too few to fit: weighted from its holdings" if fit.get("n")
               else "Weighted from its holdings")
    elif fit.get("rule") == "least squares" and fit.get("n"):
        how = f"{fit['n']} daily returns"
    elif fit.get("n") is not None:
        how = f"{fit['n']} daily returns, too few to fit: beta 1 to the market"
    else:
        how = "Beta 1 to the market" if fit.get("rule") else ""
    net = "each factor net of those above it" if len(shown) > 1 else ""
    caption = "; ".join(x for x in (how, net) if x)
    return (f'<h3 class="sub">Betas</h3>{f"<p class=aside>{caption}.</p>" if caption else ""}'
            f'<table class="calc-t"><tbody>{rows_}</tbody></table>')


def _rows_of(entries: list[tuple[str, float, float]], rest: tuple[str, float, float] | None = None, *,
             first: int = CONSTITUENTS_SHOWN) -> list[str]:
    """Table rows, each a label with its weight of the fund and your exposure to it, then, always shown, the rest
    together: the weights in tenths of a percent and the dollars apportioned so that each column adds to its total,
    which ends the table; the rows past `first` open with "Show N more"."""
    every = entries + ([rest] if rest else [])
    tenths = apportion([w * 1000 for _, w, _ in every])
    amounts = to_cents([d for _, _, d in every])
    tenth = lambda t: f"{MINUS if t < 0 else ''}{abs(t) / 10:.1f}%"  # noqa: E731
    row = lambda label, t, n, cls="": (f'<tr class="{cls}"><td>{label}</td><td>{num(tenth(t))}</td>'  # noqa: E731
                                       f'<td>{num(dollars(n))}</td></tr>')
    kept = [(label, t, n) for (label, _, _), t, n in zip(entries, tenths, amounts) if t or n]  # nothing shown, no row
    rows_ = [row(label, t, n, "more" if i >= first else "") for i, (label, t, n) in enumerate(kept)]
    if len(kept) > first:
        rows_.append(f'<tr class="all"><td colspan="3"><button class="linkish" type="button" data-all>Show '
                     f'{len(kept) - first} more</button></td></tr>')
    if rest:
        rows_.append(row(rest[0], tenths[-1], amounts[-1], "rest"))
    rows_.append(f'<tr class="sum"><td>Total</td><td>{num(tenth(sum(tenths)))}</td>'
                 f'<td>{num(dollars(sum(amounts)))}</td></tr>')
    return rows_


def _table(first: str, rows_: list[str]) -> str:
    return (f'<table class="calc-t holds"><thead><tr><th>{first}</th><th>Weight</th><th>Exposure</th></tr></thead>'
            f'<tbody>{"".join(rows_)}</tbody></table>')


def _sum_percent(x: float) -> str:
    """A sum of weights as a percent with the decimals that show how far it is from 100%: 100.00002%."""
    for places in range(2, 7):
        shown = f"{x * 100:.{places}f}"
        if float(shown) != 100.0:
            return shown + "%"
    return f"{x * 100:.6f}%"


def _kind_words(g: dict) -> str:
    """A kind of asset as the fund's data names it: its asset class, and its security type where it has one."""
    words = [str(x).replace("_", " ") for x in (g.get("asset_class"), g.get("security_type")) if x]
    return " · ".join(words) or "Not given"


def _fund_holdings(reader: Reader, nid: str, fund: dict) -> str:
    """What a fund holds, from its holdings, every one counted whether or not the look-through prices it: each holding
    down to the look-through floor by name, with its ticker and where it is listed, then the others together; and the
    whole fund by country of listing and by asset class. Each with its weight and your exposure."""
    holds = fund.get("holds")
    if not holds:
        return ""
    ticker = name_label(nid)
    entries = []
    for h in holds["named"]:
        name = e(h.get("name") or h.get("ticker") or "")
        label = go(h["priced_as"], name, "go name") if h.get("priced_as") else name
        about = " · ".join(e(x) for x in (h.get("ticker"), h.get("listed")) if x)
        entries.append((f'{label}{f"<div class=aside>{about}</div>" if about else ""}', h["weight"], h["dollars"]))
    rest_ = holds["rest"]
    rest = (f'{count(rest_["count"])} other{"s" if rest_["count"] != 1 else ""}', rest_["weight"], rest_["dollars"]) \
        if rest_["count"] else None
    out = [f'<h3 class="sub">Holdings</h3><p class="aside">{count(holds["count"])} as of '
           f'{day(holds.get("holdings_asof"))}</p>{_table("Holding", _rows_of(entries, rest))}']
    if dollars(holds["weights_sum"] * holds["dollars"]) != dollars(holds["dollars"]):
        out.append(f'<p class="aside">Published weights sum to {_sum_percent(holds["weights_sum"])}.</p>')
    priced = sum(1 for h in holds["named"] if h.get("priced_as"))
    fit = ((reader.output("betas", "portfolio") or {}).get("fits") or {}).get(nid) or {}
    if fund.get("expanded") and fit.get("sensitivity") == "holdings":
        out.append(f'<p class="aside">Looked through: {e(ticker)} has under a year of prices, so it moves as its '
                   f'holdings do, {count(priced)} on their own betas and the rest with the market.</p>')
    elif fund.get("expanded"):
        out.append(f'<p class="aside">Looked through: {count(priced)} {"is" if priced == 1 else "are"} priced on '
                   f'{"its" if priced == 1 else "their"} own, so a shock to one of those companies reaches what '
                   f'{e(ticker)} holds of it.</p>')
    else:
        out.append(f'<p class="aside">Not looked through: {e(fund.get("reason"))}.</p>')
    places = [(e(g["listed"]) if g["listed"] else "No exchange given", g["weight"], g["dollars"])
              for g in holds["by_listing"]]
    out.append(f'<h3 class="sub">Listing country</h3>{_table("Country", _rows_of(places))}')
    kinds = [(e(_kind_words(g)), g["weight"], g["dollars"]) for g in holds["by_kind"]]
    out.append(f'<h3 class="sub">Asset class</h3>{_table("Class", _rows_of(kinds))}')
    return "".join(out)


def _about(reader: Reader, nid: str) -> str:
    """What a company does, in its reference's words, and its industry."""
    found = (reader.output("reference", nid) or {}).get("company") or {}
    if not found.get("does") and not found.get("industry"):
        return ""
    body = (f'<div class="profile">{paragraphs(found["does"])}<button class="linkish" type="button" data-more>More'
            '</button></div>') if found.get("does") else ""
    if found.get("industry"):
        body += _dl([("Industry", f'<span class="sic">{e(found["industry"])}</span>')])
    return f'<h3 class="sub">Profile</h3>{body}'


def _name_view(reader: Reader, nid: str) -> dict:
    """A holding, or a name the funds hold, as a broker shows a position: its price and its day change, its price over
    time, your position or your exposure to it, what the company does or what a fund holds, its betas, and the events
    that move it most."""
    store = reader.store
    ticker = name_label(nid)
    kind = pseudo(ticker_of(nid))
    view_ = portfolio_view(reader) or {"holds": [], "holdings": None, "lookthrough": None, "shown": Shown()}
    co = company(store, nid)
    holding = next((h for h in view_["holds"] if h["to"] == nid), None)
    rows = (view_["holdings"] or {}).get("holds") or {}
    shown = view_["shown"]
    lt = view_["lookthrough"] or {}
    fund = (lt.get("funds") or {}).get(ticker_of(nid))
    within = ticker_of(nid).split(":", 1)[1] if ":" in ticker_of(nid) else None
    total, direct, through = held_of(view_, nid) if lt else (0, None, [])
    if holding:
        kicker = "Holding · fund" if fund else "Holding"
    elif within:
        kicker = f"Held through {e(within)}"
    elif through:
        kicker = "Held through " + e(series_of([name_label(f) for f, _ in through]))
    else:
        kicker = "Name"
    head = hero(kicker, title=e(heading(ticker)), context=e(co) if co else "")
    body = []
    prices = (reader.output("price", nid) or {}).get("prices") if not kind else None
    if prices and prices.get("price") is not None:
        body.append(_quote_lines(prices))
        body.append(_price_chart(price_points(reader, "price", nid), f"{ticker} price history"))
    facts = []
    if holding and holding["id"] in shown.value:
        got = rows.get(holding["id"]) or {}
        facts += [("Value", num(dollars(shown.value[holding["id"]])), ""),
                  ("Weight", num(shown.weights.get(holding["id"], "")), "")]
        if got.get("quantity") is not None and not kind:
            facts.append(("Quantity", num(count(got["quantity"])), "shares"))
        if holding["id"] in shown.moved and not kind and got.get("session"):
            facts.append(("Day change", num(dollars(shown.moved[holding["id"]], sign=True),
                                            _sign_tone(shown.moved[holding["id"]])), ""))
    if through:
        if not direct and len(through) == 1:
            parts = [f'through {go(through[0][0], e(name_label(through[0][0])), "go name")}']
        else:
            parts = ([f"{dollars(direct)} directly"] if direct else []) + \
                [f'{dollars(n)} through {go(f, e(name_label(f)), "go name")}' for f, n in through]
        facts.append(("Look-through exposure", num(dollars(total)), " · ".join(parts)))
    if facts:
        body.append(f'<h3 class="sub">{"Position" if holding else "Exposure"}</h3>{stats(facts)}')
    if fund and fund.get("holds"):
        body.append(_fund_holdings(reader, nid, fund))
    elif fund:
        body.append(f'<p class="aside">Not looked through: {e(fund.get("reason"))}.</p>')
    elif not kind:
        body.append(_about(reader, nid))
    betas = reader.output("betas", "portfolio") or {}
    b = ((betas.get("objects") or {}).get(nid) or {}).get("fields", {}).get("b")
    fit = (betas.get("fits") or {}).get(nid) or {}
    if holding and fund and fund.get("expanded"):
        g = reader.output("g", "portfolio") or {}
        b = ((g.get("by_holding") or {}).get(holding["id"]) or {}).get("b")
    if b and kind != "cash":
        body.append(_sensitivities(reader, b, fit))
    body.append(_events_moving(reader, holding=holding["id"] if holding else None, nid=nid))
    return {"title": heading(ticker), "html": head + "".join(body) + full(_basis(reader.derived(nid, "Name"), "e"))}


def _carriers(reader: Reader, view_: dict, cid: str) -> str:
    """The contribution of each holding to the portfolio's dollar beta to a factor: its value, its beta, and its dollar
    beta, largest first, down to the contributions that matter."""
    g = reader.output("g", "portfolio") or {}
    shown = view_["shown"]
    found = []
    for h in view_["holds"]:
        part = (g.get("by_holding") or {}).get(h["id"])
        if part and h["id"] in shown.value:
            found.append((part["g"].get(cid, 0.0), part["b"].get(cid), h))
    if not found:
        return ""
    found = sorted((x for x in found if money(x[0]) != "$0"), key=lambda x: -abs(x[0]))
    scale = sum(abs(x[0]) for x in found) or 1.0
    kept = [x for x in found if abs(x[0]) >= SMALL * scale]
    rows_ = "".join(f'<tr><td>{go(h["to"], e(heading(name_label(h["to"]))), "go name")}</td>'
                    f'<td>{num(dollars(shown.value[h["id"]]))}</td><td>{num(sens(b))}</td>'
                    f'<td>{num(money(part))}</td></tr>' for part, b, h in kept)
    rest = len(found) - len(kept)
    more = f'<p class="aside">{rest} smaller holding{"s" if rest != 1 else ""} not shown.</p>' if rest else ""
    return ('<h3 class="sub">Contribution by holding</h3><table class="calc-t"><thead><tr><th>Holding</th><th>Value</th>'
            f'<th>Beta</th><th>Dollar beta</th></tr></thead><tbody>{rows_}</tbody></table>{more}')


def _channel_view(reader: Reader, cid: str) -> dict:
    """A factor: its proxy's price over two years with its largest one-month move marked; the portfolio's dollar beta
    and, for the market, its beta; that move, and what it comes to at the dollar beta; the contribution by holding;
    and how its return is measured."""
    store = reader.store
    stated = store.stated_of(cid)
    name = heading(stated.get("name") or cid)
    try:
        listed = channel_reach(store)["channels"] or []
    except (StoreError, Unavailable):
        listed = []
    found = next((c for c in listed if c["channel"] == cid), {})
    first = bool(listed) and listed[0]["channel"] == cid
    v = reader.value("orthogonalize", cid)
    orth = (v.output if v else None) or {}
    proxy = stated.get("proxy_ticker")
    mid = schema.proxy_metric_id(proxy)
    head = hero("Factor", title=e(name), context=f"Proxy {go(mid, e(proxy))}")
    body = []
    proxied = reader.output("proxy_price", mid) or {}
    if proxied.get("current_value") is not None:
        body.append(_quote_lines({"price": proxied["current_value"], "session": proxied.get("asof"),
                                  "time": proxied.get("time"), "from": proxied.get("from"),
                                  "previous_session": proxied.get("previous_session"),
                                  "session_change": proxied.get("session_change"),
                                  "session_move": proxied.get("session_move")}))
    month = orth.get("largest_month_sessions")
    mark = {"from": month["start"], "to": month["end"], "label": "Largest month"} if month else None
    body.append(_price_chart(price_points(reader, "proxy_price", mid), f"{proxy} price history", mark, "2Y"))
    facts = []
    if found.get("g") is not None:
        facts.append(("Dollar beta", num(money(found["g"])), ""))
        if first and found.get("beta") is not None:
            facts.append(("Portfolio beta", num(sens(found["beta"])), ""))  # the portfolio's, never the index's own
    if found.get("M_c") is not None and month:
        span = f'{day(month["start"], year=month["start"][:4] != month["end"][:4])} – {day(month["end"])}'
        facts.append(("Largest month" if first else "Largest month, residual", num(move(month["move"])), span))
        facts.append(("At the dollar beta", num(money(found["g_times_M_c"])), "the largest month applied to it"))
    body.append(stats(facts))
    view_ = portfolio_view(reader)
    if view_ and found.get("g") is not None:
        body.append(_carriers(reader, view_, cid))
    if orth.get("n"):
        names = channel_names(store)
        if orth.get("coefficients"):
            less = " ".join(f'− {sens(b)} × {go(c, e(names.get(c, c)))}' for c, b in orth["coefficients"].items())
            how = f'<p>Residual return = {e(proxy)} daily return {less}, fitted over {orth["n"]} days.</p>'
        else:
            how = f'<p>Return = {e(proxy)} daily total return, over {orth["n"]} days.</p>'
        body.append(f'<details class="how-measured"><summary>Method</summary>{how}</details>')
    return {"title": name, "html": head + "".join(body) + full(v.basis if v else None)}


def _quoted(store: Store, sid: str) -> list[str]:
    """Every passage quoted from a page: in an evidence link, and in each stated value that cites it."""
    out = []
    for link in store.links_of(sid, "evidence", "in"):
        out += link["stated"].get("passages") or []
    for cite in store.links_of(sid, "sourced_by", "in"):
        owner = cite["from"]
        stated = store.stated_of(owner)
        found = []
        if store.type_of(owner) == "link":
            found = [stated.get("value")]
        else:
            found = [stated.get("chance")] + list((stated.get("derivation") or {}).get("chances") or [])
        for v in found:
            if isinstance(v, dict) and v.get("source_id") == sid:
                out += v.get("passages") or []
    return list(dict.fromkeys(out))


def _inspect_passage(reader: Reader, rest: str) -> dict:
    store = reader.store
    sid, _, h = rest.rpartition(":")
    src = store.source_row(sid)
    if src is None:
        return _missing()
    passage = next((x for x in _quoted(store, sid) if hashlib.sha256(x.encode()).hexdigest()[:12] == h), None)
    text = src.get("text") or ""
    at = text.find(passage) if passage else -1
    page = e(text) if at < 0 else f"{e(text[:at])}<mark>{e(passage)}</mark>{e(text[at + len(passage):])}"
    dated = f" · dated {day(src.get('dated'))}" if src.get("dated") else ""
    head = hero("Passage", title=e(host(src.get("url"))),
                context=(f'<a href="{e(src.get("url"))}" target="_blank" rel="noopener">{e(src.get("url"))}</a>'
                         f'{dated} · read {day(src.get("retrieved_at"))}'))
    return {"title": "Passage", "html": head + f'<div class="page">{page}</div>' + full(sid)}


def _missing() -> dict:
    return {"title": "Not found", "html": '<p class="why">This isn\'t in the conversation any more.</p>'}


INSPECT = {"p": _inspect_point, "interval": _inspect_interval, "price": _inspect_price, "check": _inspect_check,
           "consequence": _inspect_consequence, "swing": _inspect_swing, "emove": _inspect_emove,
           "holding": _inspect_holding, "checks": _inspect_checks, "passage": _inspect_passage}


# ---------------------------------------------------------------- the other objects, opened on the right


def _market_view(reader: Reader, mid: str) -> dict:
    """A market: its question, its quote, when it closes and what settles it, the propositions it is matched to with
    the second model's checks, the notes about it, and its rules."""
    store = reader.store
    listing = reader.output("market_listing", mid) or {}
    price = reader.output("market_price", mid) or {}
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    q = listing.get("question") or mid
    status = pill("Open", "teal") if price.get("open") else pill("Closed") if price.get("open") is False else ""
    head = hero(f"Market · {venue}", title=e(q), pills=[status])
    source = listing.get("resolution_source") or ""
    shown_source = (f'<a href="{e(source)}" target="_blank" rel="noopener">{e(host(source))}</a>'
                    if source.startswith("http") else e(source))
    facts = [("Price", gonum("x:price:" + mid, per(price["price"])) if price.get("price") is not None else "", ""),
             ("Bid", num(per(price.get("bid"))) if price.get("bid") is not None else "", ""),
             ("Ask", num(per(price.get("ask"))) if price.get("ask") is not None else "", ""),
             ("Depth", num(dollars(price["depth"])) if price.get("depth") is not None else "", "top five levels"),
             ("Closes", e(day(listing.get("close_time"))) if listing.get("close_time") else "", ""),
             ("Settled by", shown_source, "")]
    body = [stats(facts)]
    asks = store.links_of(mid, "quoted_by", "in")
    if asks:
        items = "".join(f'<li>{go(link["from"], e(quote(title_of(store, link["from"]))))}'
                        f'{" (the opposite)" if link["stated"].get("transform") == "complement" else ""}'
                        f'{_market_checks(reader, link["id"], heading=False)}</li>'
                        for link in asks)
        body.append(f'<h3 class="sub">Matched to</h3><ul class="plainlist matched">{items}</ul>')
    notes = [store.stated_of(a["from"]).get("text") for a in store.links_of(mid, "about", "in")]
    if notes:
        body.append('<h3 class="sub">Notes</h3>' + "".join(f'<div class="basis note">{fold(note_html(t))}</div>' for t in notes))
    if listing.get("rules"):
        body.append(f'<details class="rules"><summary>Rules</summary>{paragraphs(listing["rules"])}</details>')
    return {"title": q, "html": head + "".join(body) + full(mid)}


def _metric_view(reader: Reader, mid: str) -> dict:
    store = reader.store
    unit = unit_of(store, mid)
    price = bool(schema.PROXY_METRIC_RE.match(mid))
    head = hero("Price" if price else "Level", title=e(heading(metric_label(store, mid))))
    facts = [("Today", _today(reader, mid, unit), "")]
    if not price:
        stated = store.stated_of(mid)
        facts.append(("Traded", "yes" if stated.get("traded") else "no", ""))
        if stated.get("resolution_source"):
            facts.append(("Source", e(stated["resolution_source"]), ""))
    body = [stats(facts)]
    used = store.links_of(mid, "consequence", "in")
    if used:
        rows_ = "".join(f'<tr><td>{go(link["from"], e(title_of(store, link["from"])))}</td>'
                        f'<td>{gonum("x:consequence:" + link["id"], _figure_short(reader, link).removeprefix("shock "))}'
                        f'</td></tr>' for link in used)
        body.append('<h3 class="sub">Events</h3><table class="ifs"><thead><tr><th>Event</th><th>Shock</th>'
                    f'</tr></thead><tbody>{rows_}</tbody></table>')
    return {"title": heading(metric_label(store, mid)), "html": head + "".join(body) + full(mid)}


def _proposition_view(reader: Reader, pid: str) -> dict:
    """A proposition the event reaches: what it says, its probability where one is worked out, the range markets imply,
    and the markets that ask it."""
    store = reader.store
    stated = store.stated_of(pid)
    d = reader.derived(pid, "Proposition")
    p, lo, hi = _value(d, "p"), _value(d, "lo"), _value(d, "hi")
    rows = []
    if p is not None:
        put = _put(lo, hi, "x:interval:" + pid) if lo is not None and hi is not None else None
        beside = put or _unbounded((reader.computation(_basis(d, "p")).get("output") or {}).get("rule"))
        rows.append([("b", "Probability"), ("go", chance(p), "x:p:" + pid), *([("aside", beside)] if put else
                                                                              [("aside", [beside])] if beside else [])])
    head = (hero("Part of an event's breakdown", title=title_html(stated.get("title")))
            + f'<p class="text">{e(stated.get("text"))}</p>'
            + _when_known(stated, store.horizon_end(), store.meta("asof")))
    body = [f'<div class="sentences">{figures_html(rows)}</div>' if rows else ""]
    markets = "".join(_market_item(reader, link) for link in store.links_of(pid, "quoted_by"))
    if markets:
        body.append(f'<h3 class="sub">Markets</h3><ul class="mkts">{markets}</ul>')
    for about in store.links_of(pid, "about", "in"):
        body.append(f'<div class="basis note">{fold(note_html(store.stated_of(about["from"]).get("text")))}</div>')
    return {"title": stated.get("title") or pid, "html": head + "".join(body) + full(pid)}


def _source_view(reader: Reader, sid: str) -> dict:
    src = reader.store.source_row(sid) or {}
    dated = f" · dated {day(src.get('dated'))}" if src.get("dated") else ""
    head = hero("Source", title=e(host(src.get("url"))),
                context=f'<a href="{e(src.get("url"))}" target="_blank" rel="noopener">{e(src.get("url"))}</a>{dated}')
    return {"title": host(src.get("url")), "html": head + f'<div class="page">{e(src.get("text"))}</div>' + full(sid)}


def _note_view(reader: Reader, nid: str) -> dict:
    store = reader.store
    about = [go(link["to"], e(label(store, link["to"]) or link["to"])) for link in store.links_of(nid, "about")]
    head = hero("The analyst's note", context=f"About {series_of(about)}" if about else "")
    body = f'<div class="note">{note_html(store.stated_of(nid).get("text"))}</div>'
    return {"title": "The analyst's note", "html": head + body + full(nid)}


def _raw_view(reader: Reader, rid: str) -> dict:
    got = reader.get(rid)
    if got is None:
        return _missing()
    kind = got.get("type")
    what = {"Computation": "Calculation", "Judgment": "Second model's answer"}.get(
        kind, label(reader.store, rid) or "Everything about it")
    return {"title": "Full details", "html": hero("Full details", title=e(what)) + _record(got)}


def _record(obj: Any) -> str:
    return f'<pre class="record">{e(json.dumps(obj, indent=1, ensure_ascii=False, default=str))}</pre>'


def view(store: Store, target: str, *, pending: bool = False) -> dict:
    """One step of the right panel: an event, a number's explanation, or an object, with its whole record one click
    away."""
    reader = Reader(store, pending=pending)
    try:
        if target.startswith("raw:"):
            return _raw_view(reader, target[len("raw:"):])
        if target.startswith("x:"):
            kind, _, rest = target[2:].partition(":")
            if kind not in INSPECT:
                return _missing()
            return INSPECT[kind](reader, rest)
        kind = store.type_of(target)
        if kind is None:
            return _missing()
        if kind == "Proposition":
            return event_view(reader, target) if store.watched(target) else _proposition_view(reader, target)
        if kind == "link" and (store.link_by_id(target) or {}).get("relation") == "consequence":
            return INSPECT["consequence"](reader, target)  # a consequence opens its explanation
        if kind == "Judgment":
            return INSPECT["check"](reader, target)
        return {"Market": _market_view, "Metric": _metric_view, "Name": _name_view, "Channel": _channel_view,
                "Source": _source_view, "Note": _note_view}.get(kind, lambda r, t: _raw_view(r, t))(reader, target)
    except (StoreError, Unavailable):
        return _missing()


# ---------------------------------------------------------------- id chips


def _figure_short(reader: Reader, link: dict) -> str:
    """A consequence as its chip shows it: its shock with its sign, or, until the shock is worked out, the figure the
    analyst states."""
    unit = unit_of(reader.store, link["to"])
    gap = _value(reader.derived(link["id"], "consequence"), "gap")
    if isinstance(gap, (int, float)):
        return "shock " + ("+" if gap > 0 else "") + difference(gap, unit, short=True)
    figure = (link.get("stated") or {}).get("value")
    fig = figure.get("value") if isinstance(figure, dict) else figure
    if not isinstance(fig, (int, float)):
        return ""
    meaning = (link.get("stated") or {}).get("meaning")
    if meaning == "level_if_happens":
        return f"{level(fig, unit)} if Yes"
    if meaning == "level_if_not":
        return f"{level(fig, unit)} if No"
    return "shock " + ("+" if fig > 0 else "") + difference(fig, unit, short=True)


def chips(store: Store, ids: list[str]) -> dict[str, dict]:
    """Each id the analyst writes, in a reply or a note, as its chip shows it: the object's title and its current
    value."""
    reader = Reader(store, pending=True)
    out: dict[str, dict] = {}
    reach: dict | None = None
    view_: dict | None = None
    for oid in ids:
        kind = store.type_of(oid)
        if kind is None:
            out[oid] = {"label": oid, "value": "", "exists": False}
            continue
        label_, value, title = oid, "", None
        if kind == "Proposition":
            label_ = title_of(store, oid)
            p = _value(reader.derived(oid, "Proposition"), "p")
            value = chance(p) if p is not None else ""
        elif kind == "Market":
            label_ = market_question(reader, oid) or oid
            shown = reader.output("market_price", oid) or {}
            value = per(shown.get("price")) if shown.get("price") is not None else ""
        elif kind == "Metric":
            label_ = metric_label(store, oid)
            got = reader.get(oid) or {}
            if got.get("ticker"):
                value = level(_value(got["derived"], "current_value"), "USD")
            else:
                s = got.get("stated") or {}
                value = level(s.get("current_value"), s.get("unit"))
        elif kind == "Name":
            label_ = name_label(oid)
            if view_ is None:
                view_ = portfolio_view(reader) or {}
            held = {h["to"]: h["id"] for h in view_.get("holds") or []}
            shown = view_.get("shown")
            if shown and held.get(oid) in shown.value:  # a holding: its value, as the portfolio panel shows it
                value = dollars(shown.value[held[oid]])
            elif shown and view_.get("lookthrough"):  # a name the funds hold: your exposure, as its page shows it
                total = held_of(view_, oid)[0]
                value = dollars(total) if total else ""
        elif kind == "Channel":
            label_ = heading(store.stated_of(oid).get("name") or oid)
            if reach is None:
                try:
                    reach = {c["channel"]: c for c in channel_reach(store)["channels"] or []}
                except Exception:
                    reach = {}
            exposure = (reach.get(oid) or {}).get("g")
            value = f"dollar beta {money(exposure)}" if exposure is not None else ""
        elif kind == "Source":
            src = store.source_row(oid) or {}
            label_, value = host(src.get("url")), day(src.get("dated")) if src.get("dated") else ""
        elif kind == "Note":
            label_ = "The analyst's note"
        elif kind == "link":
            label_ = label(store, oid) or oid
            link = store.link_by_id(oid) or {}
            if link.get("relation") == "consequence":  # the metric it moves, with its shock; the event is named around it
                title, label_ = label_, metric_label(store, link["to"])
                value = _figure_short(reader, link)
        elif kind in ("Computation", "Judgment"):
            label_ = label(store, oid)
            answer = (reader.get(oid) or {}).get("answer") if kind == "Judgment" else None
            value = chance(answer) if isinstance(answer, (int, float)) else ""
        out[oid] = {"label": label_, "value": value, "exists": True, "kind": kind, "title": title or label_}
    return out


# ---------------------------------------------------------------- the download


def ledger_markdown(store: Store) -> str:
    """The ledger as a readable document: the portfolio, its holdings, look-through exposure, and factor exposures,
    then the events over the horizon in ledger order, each with when it can happen and its figures, with the as-of
    date."""
    reader = Reader(store)
    view_ = portfolio_view(reader)
    lines = ["# Event risk", ""]
    if view_ is None:
        return "\n".join(lines + ["No portfolio yet.", ""])
    asof = store.stated_of("portfolio").get("asof")
    holdings, shown = view_["holdings"] or {}, view_["shown"]
    lines += [f"As of {day(asof)}. {_when(holdings)}.", ""]
    if holdings and shown.total is not None:
        moved = f", {dollars(shown.total_moved, sign=True)} ({move(holdings.get('session_move'))}){_session(holdings)}" \
            if shown.total_moved is not None else ""
        lines += [f"## Portfolio: {dollars(shown.total)}{moved}", "", "| Holding | Value | Weight | Day change |",
                  "|---|---:|---:|---:|"]
        for h in view_["holds"]:
            got = holdings.get("holds", {}).get(h["id"]) or {}
            mv = f"{dollars(shown.moved[h['id']], sign=True)} ({move(got.get('session_move'))})" \
                if h["id"] in shown.moved and ticker_of(h["to"]) != "CASH" else ""
            lines.append(f"| {heading(name_label(h['to']))} | {dollars(shown.value.get(h['id']))} | "
                         f"{shown.weights.get(h['id'], '')} | {mv} |")
        lines.append("")
    lt = view_["lookthrough"]
    if lt:
        e_ = {n: v for n, v in lt["e"].items() if v and not pseudo(ticker_of(n))}
        lines += ["### Look-through exposure", ""]
        for nid in sorted(e_, key=lambda n: (-abs(e_[n]), n))[:NAMES_SHOWN]:
            total, direct, through = held_of(view_, nid)
            if through and not direct and len(through) == 1:
                via = f" (through {name_label(through[0][0])})"
            elif through:
                via = " (" + "; ".join(([f"{dollars(direct)} directly"] if direct else []) +
                                       [f"{dollars(n)} through {name_label(f)}" for f, n in through]) + ")"
            else:
                via = ""
            lines.append(f"- {name_label(nid)}: {dollars(total)}{via}")
        lines.append("")
    reach = view_["reach"]
    if reach and reach.get("channels"):
        first = reach["channels"][0]["channel"]
        lines += ["### Factor exposures", "", "| Factor | Dollar beta | Beta | Largest month |", "|---|---:|---:|---:|"]
        for c in sorted((c for c in reach["channels"] if c["g_times_M_c"] is not None), key=lambda c: -c["g_times_M_c"]):
            beta = sens(c["beta"]) if c["channel"] == first and c.get("beta") is not None else ""
            lines.append(f"| {heading(c['name'])} | {money(c['g'])} | {beta} | {money(c['g_times_M_c'])} |")
        lines.append("")
    found = _ledger(reader)
    lines += ["## Events by expected move", ""]
    if not found:
        lines += ["No events yet.", ""]
    else:
        days = store.stated_of("portfolio").get("horizon_days")
        lines += [f"Horizon {day(found['horizon_end'])} · {days} days", ""]
    for i, r in enumerate((found or {}).get("rows") or [], 1):
        when = " · ".join(f"{k}: {v}" for k, v in _when_pairs(store.stated_of(r["id"]), found["horizon_end"], asof))
        lines += [f"### {i}. {r['title']}", "", r["text"], "", when, ""]
        if r.get("emove") is not None:
            lines += [f"Expected move {either(r['emove'])} · Worst case {money(r['worst'], sign=True)}", ""]
        said = figures_text(sentences(reader, r))
        if said:
            lines += [said, ""]
    return "\n".join(lines).rstrip() + "\n"


