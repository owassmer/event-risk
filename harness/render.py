"""The page's one renderer: the portfolio panel, the ledger, an opened event, a holding, a factor, a market, a
figure's working, the conversation's id chips, and the ledger as markdown, each read from the store and named in the
words its reader uses.

Every figure is a derived field, or an output or input of the Computation or Judgment that is its basis; nothing here
computes a number of its own. Its arithmetic is rounding, each kind of figure in one form wherever it appears (the
section on numbers, below), and apportioning, so that parts shown together add to their whole as shown.

A render reads the store under its lock. While the analyst works, the page renders from what the store already holds
(`cache_only`): a value that waits on the judge or a data client shows as a quiet placeholder where it will sit, and
the page never asks or fetches on the analyst's behalf. When a reply ends, the panels render once more with the judge
and the data clients. A value that does not exist yet, such as dollars before any consequence, is simply not there.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
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
from harness.derive.engine import Unavailable, Waiting
from harness.derive.impact import FOLLOWED, MOVE, stated_move
from harness.derive.portfolio import CASH, closes, held_name, pseudo, security_of, ticker_of
from harness.derive.structure import LINKS as STRUCTURE, subject as structure_of
from harness.derive.worlds import in_its_terms, side_mean
from harness.judge import JudgeError
from harness.ledger import by_volatility, by_worst, channel_reach, ledger, row
from harness.ledger import MOVES_WITH, event_now, moves, outcomes as outcome_tree
from harness.ledger import drawn_on, latest_read as reading_time
from harness.ledger import factor as factor_reach
from harness.readings import prices_at
from harness.schema import DEFINITION
from harness.store import Store, StoreError
from harness.store import _EMPHASIS, _FRONT, _IMAGE, _LINK, body as page_body, words as page_words  # how a page's
# passages are found in it, as the store checks each is quoted word for word
from harness.venues import split
from harness.venues.markets import NY

log = logging.getLogger("harness.render")  # what the page could not link, for us: the member sees the figure

MINUS = "\u2212"
LISTS_SHOWN = 5  # each list of where the portfolio's weight is, as the portfolio panel shows it before it opens
CONSTITUENTS_SHOWN = 12  # a fund's constituents shown before "all of them"
LISTED = 15  # the rows a list of where the portfolio's weight is shows before "Show N more"
EVENTS_SHOWN = 5  # the events a holding's page lists, those that move it most
SMALL = 0.005  # a holding carrying under half a percent of a factor's exposure is not listed by name
ZERO = 1e-12  # a bound this near 0 or 1 is there: no market sets that end of the range
VENUES = {"kalshi": "Kalshi", "polymarket": "Polymarket", "polymarket_us": "Polymarket US"}  # MATH.md 3.3


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


# Each kind of figure has one form wherever it appears, whatever its size. Money a statement shows (a
# holding's value, the total, a price, a day's change) is exact to the cent (`money`); money the system computes from
# them (what an event or a factor would do, a volatility, a dollar beta, what the look-through puts in a company, a
# depth) is in whole dollars (`amount`), and is exact to the cent in the inspector's working, where it is checked. A
# figure that is nothing as shown reads "–". A probability is a percent to one decimal (`chance`); a market's bid,
# ask and midpoint are in cents, as its venue quotes them (`cents`, `quotes`); a move, and an event's share of the
# portfolio, a percent to two (`move`, `share`); a yield's change in percentage points of the yield (`points`); a weight
# a percent to one; a beta two decimals; a level two decimals in its unit. The inspector's working is to the cent, each
# fraction in it a percent to four decimals (`operand`); the full details keep every value exact. A chart's axis marks
# its places in thousands or millions ("−$40k").

NIL = "\u2013"  # a figure that is nothing as shown


def nothing(x: float | None) -> bool:
    """Whether a computed dollar figure is nothing as shown, in whole dollars."""
    return x is None or _half_up(abs(x), "1") == 0


def amount(x: float | None, *, sign: bool = True) -> str:
    """A dollar figure the system computes, in whole dollars, halves away from zero: −$48,038, +$1,475; "–" where it
    is nothing as shown."""
    if x is None:
        return ""
    r = _half_up(abs(x), "1")
    return NIL if r == 0 else f"{_lead(x, False, sign)}${r:,}"


def as_shown(x: float) -> int:
    """A computed dollar figure as the page shows it, in whole dollars, halves away from zero."""
    n = int(_half_up(abs(x), "1"))
    return -n if x < 0 else n


def whole_parts(parts: list[float], whole: float) -> list[int]:
    """Parts in whole dollars that add to their whole as shown: each rounded down, the largest remainders taking what
    rounding each alone leaves over."""
    return apportion(parts, whole)


def axis_label(t: float) -> str:
    """A round place on a dollar axis, in thousands or millions, no trailing zeros: −$40k, +$2.5k, $1.5M, Today."""
    if abs(t) < 1e-9:
        return "Today"
    scale, suffix = (1e6, "M") if abs(t) >= 1e6 else (1e3, "k") if abs(t) >= 1e3 else (1.0, "")
    figure = f"{abs(t) / scale:,.3f}".rstrip("0").rstrip(".")
    return f"{_lead(t, False, True)}${figure}{suffix}"


def chance(p: float | None) -> str:
    """A probability as a percent to one decimal: 0.19 is 19.0%, 0.0803 is 8.0%, 0.325 is 32.5%."""
    if p is None:
        return ""
    return f"{Decimal(repr(p * 100)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def quotes(bid: float | None, ask: float | None, side: str = "yes", *, html: bool = False) -> str:
    """A market's book as observed, by the side it is read through: "Yes bid 16¢ · Ask 49¢"; a side with no standing
    quote shows a dash. As HTML, its words in the page's text and its figures in the figures' face."""
    fig = (lambda q: f'<span class="n">{cents(q)}</span>') if html else cents  # noqa: E731
    said = lambda q: fig(q) if q is not None else "\u2013"  # noqa: E731
    return f"{side.title()} bid {said(bid)} \u00b7 Ask {said(ask)}"


def side_of(store: Store, lid: str | None) -> str:
    """The side of its market an asks link reads, yes or no."""
    link = store.link_by_id(lid) if lid else None
    return ((link or {}).get("stated") or {}).get("side") or "yes"


def cents(q: float | None) -> str:
    """A market's quote or midpoint, the price of a contract that pays $1, in cents as its venue quotes it: whole
    cents, with a tenth where it has one. 0.16 is 16¢, 0.325 is 32.5¢."""
    if q is None:
        return ""
    c = Decimal(repr(q * 100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{c.quantize(Decimal('1')) if c == c.to_integral_value() else c}\u00a2"


def share(x: float | None) -> str:
    """A share of the portfolio, what an event would do as a fraction of its value: a signed percent to two decimals,
    −9.82%."""
    return percent(x, 2, sign=True)


def operand(x: float | None, *, sign: bool = False) -> str:
    """A fraction where it enters the inspector's arithmetic, a probability, a shock, or a move, as a percent to four
    decimals: 0.175 is 17.5000%, 0.1023218 is 10.2322%, and with `sign` 0.03997271 is +3.9973%. Each line's result is
    the engine's, to the cent; every value is exact in the full details."""
    return percent(x, 4, sign=sign)


def percent(x: float | None, places: int = 2, *, sign: bool = False) -> str:
    """A fraction as a percent to `places` decimals: 0.04351 is 4.35%."""
    if x is None:
        return ""
    r = (Decimal(repr(abs(x))) * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{_lead(x, r == 0, sign)}{r}%"


def either(x: float | None, *, cents: bool = False) -> str:
    """A volatility: a size either way, as an options desk quotes one, in whole dollars, \u00b1$8,413; to the cent in
    the inspector's working, \u00b1$8,413.27."""
    if x is None:
        return ""
    return f"\u00b1{money(abs(x)) if cents else amount(abs(x), sign=False)}"


def move(x: float | None) -> str:
    """A move: a signed percent to two decimals, −4.35%."""
    return percent(x, 2, sign=True)


def points(x: float | None) -> str:
    """A yield's change, in percentage points of the yield, signed, to two decimals: −0.60 pp. Its level stays a
    percent, 4.25%."""
    if x is None:
        return ""
    r = Decimal(repr(abs(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{_lead(x, r == 0, True)}{r} pp"


def factor_move(item: dict, x: float | None) -> str:
    """A price's move in a world against the baseline (ledger.event_now): for a factor quoted as a yield, whose entry
    carries its `yield`, the yield's change in percentage points, +0.50 pp from 4.25% to 4.75%; for any other price,
    its move, a signed percent to two decimals, −4.35%."""
    if x is None:
        return ""
    if "yield" in item:
        return points(x)
    return move(x)


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
    """A metric's level for reading, to two decimals in its unit: a price in dollars to the cent, $645.88; a level in
    percent, 4.56%; any other, 18.35 in its unit."""
    if x is None:
        return ""
    r = _half_up(abs(x), "0.01")
    lead = MINUS if x < 0 and r else ""
    if unit in ("USD", "usd", "dollars"):
        return f"{lead}${r:,.2f}"
    if unit in PERCENT:
        return f"{lead}{r:,.2f}%"
    return f"{lead}{r:,.2f} {escape(unit)}" if unit else f"{lead}{r:,.2f}"


def stated_shock(store: Store, target: str, x: float | None) -> str:
    """A shock as the analyst states it (MATH.md 1.3): on a factor quoted as a yield, in percentage points of the yield,
    −0.60 pp; on any other price, a move, a percent to two decimals, −4.35%."""
    if x is None:
        return ""
    if quoted_as_yield(store, target):
        return points(x)
    return move(x)


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
        except (Unavailable, Waiting, RuntimeError, StoreError, KeyError):
            return None
        return v

    def output(self, name: str, subject: str) -> dict | None:
        v = self.value(name, subject)
        return v.output if v is not None else None

    def get(self, oid: str) -> dict | None:
        try:
            return self.store.get(oid)
        except (StoreError, Unavailable, Waiting, RuntimeError):
            return None

    def derived(self, eid: str, kind: str, fields: Iterable[str] | None = None) -> dict:
        """The derived fields of an object, each with its basis: the ones named, where they are named, so that a reader
        showing one figure works out only the values that give it (Engine.derived)."""
        try:
            return self.engine.derived(eid, kind, fields=None if fields is None else set(fields))
        except (StoreError, Unavailable, Waiting, RuntimeError, KeyError):
            return {}

    def waits(self, name: str, subject: str) -> bool:
        """Whether the value is being worked out, so that it is arriving and has its place: this read waited on a data
        client the page leaves to the analyst's turn, or met it, or a value it rests on, being worked out apart
        (Engine.page_read). A value read as last worked out shows that; this answers for one never worked out."""
        read = self.engine.page_read()
        return (self.pending and self.engine.pending(name, subject)) or bool(read and (name, subject) in read.apart)

    def reading_waits(self, pid: str) -> bool:
        """Whether a proposition's structure is being read."""
        return self.waits("structure", structure_of(self.store, pid))

    def computation(self, cid: str | None) -> dict:
        """A Computation's record: its inputs and output."""
        if not cid or not str(cid).startswith("cmp:"):
            return {}
        try:
            return self.store.get(cid)
        except StoreError:
            return {}


class _OnTheTurn:
    """The judge while the page reads from what the store holds: what it has answered is read, and what it has not
    waits for the analyst's turn."""

    def __init__(self, judge):
        self.models = judge.models

    def answer_all(self, questions, evidence):
        raise JudgeError("asked on the analyst's turn, not the page's")


@contextmanager
def cache_only(store: Store):
    """The store answering from what it holds: what a judge or a data client here would give, and the store does not
    hold, waits for the analyst's turn; what no judge or client here gives is absent, as ever; no venue is read. A
    value the engine holds is what any reader would compute (Engine._current), so the page reads it as it is; a value
    computed here that met a wait is not what a call would compute, and the engine drops it when the page's read ends
    (Engine.held_only), so the next read with the judge and the clients works out what this one could not. A value this
    read cannot give at the newest state without working it out here or waiting is read as it was last worked out
    (Engine.last_where_waiting): a figure shown stays until its new value is ready, and a placeholder stands only for
    a value never worked out."""
    saved = store.engine.judge, store.venues
    store.engine.judge = _OnTheTurn(saved[0]) if saved[0] is not None else None
    store.venues = None
    try:  # the page's reads, on this thread alone (Store.as_page): every other thread reads with the store's sources
        with (store.as_page(), store.engine.held_only(), store.engine.placeholders(),
              store.engine.last_where_waiting() as read):
            yield read
    finally:
        store.engine.judge, store.venues = saved


@contextmanager
def reading(store: Store, *, inline: bool = False):
    """The page's read between replies: it works out and reads what it needs as any reader does, except a value
    computed apart (a fit, a distribution), which it never waits for while holding the store's lock. One under way is
    read as it was last worked out, or, never worked out, as waiting; the read says so (`later`), and the page reads
    the view again once it is ready, its figures changing in place. With `inline`, a value it would work out
    here, holding the lock, is read as last worked out too, and the read says so (`unworked`): a view opened shows at
    once, and is read again working them out."""
    with (store.engine.held_only(), store.engine.placeholders(),
          store.engine.last_where_waiting(inline=inline) as read):
        yield read


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


_ENUM = re.compile(r"(?:^|(?<=\s))\((\d{1,2})\)\s+")


def _enumerated(text: str) -> str:
    """A paragraph where the analyst numbers its points inline, "(1) … (2) …", as the list it is, so each point reads
    on its own line: only where the markers run 1, 2, 3 in order, and the words are the analyst's as written."""
    out = []
    for para in text.split("\n\n"):
        marks = list(_ENUM.finditer(para))
        if len(marks) < 2 or [int(m.group(1)) for m in marks] != list(range(1, len(marks) + 1)):
            out.append(para)
            continue
        lead = para[:marks[0].start()].strip()
        items = [para[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(para))].strip()
                 for i, m in enumerate(marks)]
        out.append((lead + "\n\n" if lead else "") + "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1)))
    return "\n\n".join(out)


def note_html(text: str | None) -> str:
    """A note, in the analyst's words: the page renders its markdown as it renders a reply's, each id a chip."""
    text = _enumerated(text or "")
    return f'<div class="md" data-md="{e(text)}">{paragraphs(text)}</div>'


def working(reader: Reader, record_id: str | None) -> str | None:
    """Where a figure's whole record opens. A Computation is the working of one value, and the value is worked out
    again whenever what it reads changes, so a figure shown links the value, `raw:value:{derivation}:{subject}`, and
    opens the working the value has when it is opened (Store.current_computation), as the figure itself follows its
    value. A Judgment, a link, a page or an object opens by its id."""
    if not record_id:
        return None
    if not str(record_id).startswith("cmp:"):
        return "raw:" + record_id
    of = reader.store.computation_of(record_id)
    if of is None:  # a basis read just now that the store no longer keeps: the store's to keep
        log.warning("%s, the basis of a figure shown, is not kept in the store: its Full details are not linked",
                    record_id)
        return None
    if of["subject"] is None:
        return "raw:" + record_id
    return f"raw:value:{of['derivation']}:{of['subject']}"


def full(reader: Reader, record_id: str | None, text: str = "Full details") -> str:
    """The whole Computation or Judgment, one click away."""
    target = working(reader, record_id)
    return f'<p class="raw">{go(target, e(text), "rawlink")}</p>' if target else ""


MATH_VAR = re.compile(r"(?<![A-Za-z])([A-EpV])(?![A-Za-z])")


def _math(text: str) -> str:
    """Math text: each variable, a single letter, in italics, and the operators and figures upright."""
    return MATH_VAR.sub(lambda m: f"<var>{m.group(1)}</var>", e(text))


def eqn(text: str) -> str:
    """A formula in math type."""
    return f'<span class="m">{_math(text)}</span>'


def pill(text: str, tone: str = "") -> str:
    """A small label for what a page is or how its figure was set."""
    return f'<span class="pill{" " + tone if tone else ""}">{text}</span>'


def hero(kicker: str, *, figure: str = "", title: str = "", context: str = "", pills: Iterable[str] = (),
         under: str = "") -> str:
    """The head of a view: what it is; its figure, with what it is measured from beneath it where that needs saying;
    or its title; what it belongs to; and, as text, the state it is in (open or closed, the baseline)."""
    shown = "".join(x for x in pills if x)
    parts = [f'<p class="kicker">{kicker}</p>']
    if figure:
        parts.append(f'<div class="fig">{figure}</div>')
    if under:
        parts.append(f'<p class="under">{under}</p>')
    if title:
        parts.append(f'<h2 class="title">{title}</h2>')
    if context:
        parts.append(f'<p class="ctx">{context}</p>')
    if shown:
        parts.append(f'<div class="pills">{shown}</div>')
    return f'<header class="hero">{"".join(parts)}</header>'


def facts(items: Iterable[tuple]) -> str:
    """Figures in a line under a view's head, each under its label, with what qualifies it beneath where it says
    something: (label, figure html[, beneath])."""
    cells = "".join(f'<span class="of"><span class="lab">{e(x[0])}</span>{x[1]}'
                    + (f'<span class="of-s">{x[2]}</span>' if len(x) > 2 and x[2] else "") + "</span>"
                    for x in items if x[1])
    return f'<div class="ofacts">{cells}</div>' if cells else ""


def _cols(heads: Iterable[str], rows: Iterable[tuple[str, list[str]]], total: tuple[str, list[str]] | None = None,
          cls: str = "") -> str:
    """Figures under column names: each row its name and its cells; a total beneath, where the rows add to one."""
    head = "".join(f'<th class="n">{e(h)}</th>' for h in heads)
    body = "".join(f'<tr><th>{label_}</th>{"".join(f"<td class=n>{c}</td>" for c in cells)}</tr>' for label_, cells in rows)
    if total:
        body += f'<tr class="tot"><th>{total[0]}</th>{"".join(f"<td class=n>{c}</td>" for c in total[1])}</tr>'
    return f'<table class="cmp {cls}"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody></table>'


def stats(items: Iterable[tuple[str, str, str]]) -> str:
    """Facts in a grid, each label above its figure, with what qualifies it beneath."""
    cells = "".join(f'<div class="stat"><div class="l">{name}</div><div class="v">{value}</div>'
                    + (f'<div class="s">{sub}</div>' if sub else "") + "</div>"
                    for name, value, sub in items if value)
    return f'<div class="stats">{cells}</div>' if cells else ""


def receipt(rows: Iterable[tuple[str, str, str]], total: tuple[str, str, str] | None = None,
            formula: Iterable[str] = (), after: Iterable[tuple[str, str, str]] = ()) -> str:
    """Workings as a receipt: each input with what it is and its value, the result beneath a rule, what that result is
    in other terms (`after`), then the formula that makes it, in symbols and with the values filled in."""
    def row(term: str, what: str, value: str, cls: str = "") -> str:
        tr = f'<tr{f" class={cls}" if cls else ""}>'
        if not what:  # an input with nothing to say of what it is: its name takes that space beside its value
            return f'{tr}<th colspan="2">{term}</th><td class="v">{value}</td></tr>'
        return f'{tr}<th>{term}</th><td class="d">{what}</td><td class="v">{value}</td></tr>'
    body = "".join(row(*x) for x in rows) + (row(*total, cls="tot") if total else "") + "".join(row(*x) for x in after)
    lines = "".join(f"<div>{x}</div>" for x in formula if x)
    return f'<table class="rcpt"><tbody>{body}</tbody></table>' + (f'<div class="eq">{lines}</div>' if lines else "")


def worked(formula: str, values: str) -> str:
    """What a figure is, in symbols, with the values filled in beneath."""
    return f'{eqn(formula)}<div class="sub-eq">{eqn(values)}</div>'


def eqn_sum(head: str, terms: Iterable[str], tail: str = "", sep: str = " + ") -> str:
    """A sum in math type whose terms each stay whole on a line, so a long one wraps between its terms."""
    joined = f'<span class="m">{e(sep)}</span>'.join(f'<span class="m t">{_math(t)}</span>' for t in terms)
    return f'<span class="m">{_math(head)}</span>{joined}<span class="m">{_math(tail)}</span>'


def worked_sum(formula: str, head: str, terms: Iterable[str], tail: str = "") -> str:
    """What a figure is, in symbols, with a long sum of its values filled in beneath, wrapping between its terms."""
    return f'{eqn(formula)}<div class="sub-eq">{eqn_sum(head, terms, tail)}</div>'


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
    if nid.startswith("cash:") or ticker == CASH:  # cash a holding gives, in a currency, and dollar cash as booked
        currency = ticker if nid.startswith("cash:") else "USD"
        return "cash" if currency == "USD" else f"cash in {currency}"
    if ticker.startswith("CASH:"):
        return f"{ticker.split(':', 1)[1]}'s cash"
    return ticker


def name_of(store: Store | None, nid: str) -> str:
    """A name as the member knows it: by the security they hold it by, where they hold it directly by another listing
    than the one its name is kept by, since that is the price it moves by (MATH.md 3.1); otherwise by its own ticker. A
    contract the member holds is named by its side and its market's question (ONTOLOGY.md 2.2)."""
    if store is not None and nid.startswith("market:") and store.type_of("portfolio") == "Portfolio":
        held = next((h for h in store.links_of("portfolio", "holds") if h["to"] == nid), None)
        side = ((held or {}).get("stated") or {}).get("side", "")
        question = market_question(Reader(store), nid) or nid
        return f"{side.title()} · {question}" if side else question
    if store is not None and nid.startswith("name:") and store.type_of("portfolio") == "Portfolio":
        for h in store.links_of("portfolio", "holds"):
            if h["to"] == nid and (h.get("stated") or {}).get("ticker"):
                return h["stated"]["ticker"]
            if h["to"].startswith("cash:") and held_name(h) == nid:  # cash in a currency, valued as its rate moves
                return name_label(h["to"])
    return name_label(nid)


def _company_of(store: Store | None, nid: str) -> dict:
    """A company as the portfolio's weight by company gives it (MATH.md 3.1): its name, its industry and its home, each
    from one classification for every market; nothing where it is no company of the portfolio's."""
    if store is None or store.type_of("portfolio") != "Portfolio":
        return {}
    listed = (Reader(store).output("weights", "portfolio") or {}).get("by_company") or []
    return next((g for g in listed if g.get("name") == nid), {})


def company(store: Store, nid: str) -> str | None:
    """A company's or a fund's name: a company's as its classification gives it, and otherwise its listing's."""
    named = _company_of(store, nid).get("company")
    if named:
        return named
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


def as_asked(question: str | None) -> str:
    """A market's question in its venue's words, its markdown emphasis rendered as the venue means it to read, not
    printed: "Will **real GDP** increase" reads "Will real GDP increase"."""
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", (question or "").strip())
    return re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)


def market_question(reader: Reader, mid: str) -> str | None:
    """A market's question, as the page names the market everywhere (MATH.md 3.3)."""
    out = reader.output("market_listing", mid)
    return as_asked(out.get("question")) or None if out else None


def title_of(store: Store, oid: str) -> str:
    """What the page calls an object: a proposition by its title, a factor or a metric by its name, and a market, which
    stands as the proposition its question asks (MATH.md 1.2), by its question, the one name the page gives a market."""
    if schema.MARKET_ID_RE.match(oid):
        try:
            listing = store.engine.value("market_listing", oid).output or {}
        except (StoreError, Unavailable, Waiting, RuntimeError, KeyError):
            listing = {}
        return as_asked(listing.get("question")) or oid
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
    """The unit a metric's level is in: a price in the currency its listing is quoted in, which is dollars unless the
    name's fit records another, a listing abroad's (MATH.md 1.4, 3.1); any other level in the unit stated for it."""
    if schema.PROXY_METRIC_RE.match(mid):
        return _quoted_in(store, "name:" + mid.split(":", 2)[2]) or "USD"
    return store.stated_of(mid).get("unit") if store.type_of(mid) == "Metric" else None


def _quoted_in(store: Store, nid: str) -> str | None:
    """The currency a name's price is quoted in where it is not dollars, as the portfolio's fits record it."""
    fits = (Reader(store).output("betas", "portfolio") or {}).get("fits") or {}
    return ((fits.get(nid) or {}).get("currency") or {}).get("currency")


def channel_names(store: Store) -> dict[str, str]:
    return {c["id"]: c["stated"]["name"] for c in store.query("Factor")}


def target_label(store: Store, target: str) -> str:
    """The price a consequence moves, as the page names it: a factor by its name, a name by its ticker."""
    if target.startswith("name:"):
        return name_of(store, target)
    return (store.stated_of(target) or {}).get("name") or target


def price_metric(store: Store, target: str) -> str:
    """The price Metric of what a consequence moves: a factor's proxy's price, or a name's."""
    if target.startswith("name:"):
        return schema.proxy_metric_id(ticker_of(target))
    return schema.proxy_metric_id((store.stated_of(target) or {}).get("proxy", ""))


def quoted_as_yield(store: Store, target: str) -> bool:
    """Whether a consequence's factor is quoted as the yield its proxy pays, its shocks in points of it."""
    return store.type_of(target) == "Factor" and (store.stated_of(target) or {}).get("yield") is not None


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
        return name_of(store, oid)
    if kind == "Factor":
        return store.stated_of(oid).get("name")
    if kind == "Source":
        return host(store.stated_of(oid).get("url"))
    if kind == "Note":
        return "a note"
    if kind == "Computation":
        return "Calculation"
    if kind == "link":
        link = store.link_by_id(oid) or {}
        a, b = link.get("from"), link.get("to")
        la, lb = (label(store, a) if a else None) or "it", (label(store, b) if b else None) or "it"
        return {"consequence": f"the shock to {lb} from {quote(la)}", "asks": f"how {quote(la)} matches {quote(lb)}",
                "worth": f"the value of {lb} in {quote(la)}",
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


def portfolio_view(reader: Reader, *, factors: bool = True) -> dict | None:
    """What the portfolio panel shows, read from the holdings, the look-through, and g, each as it is ready; with
    `factors` off, the holdings and the look-through alone, for a reader that shows a holding's value or what the funds
    carry of a name and none of the factor exposures."""
    store = reader.store
    if store.type_of("portfolio") != "Portfolio":
        return None
    stated = store.stated_of("portfolio")
    holds = store.links_of("portfolio", "holds")
    holdings = reader.output("holdings", "portfolio")
    lookthrough = reader.output("lookthrough", "portfolio") if holdings else None
    g = reader.output("g", "portfolio") if lookthrough and factors else None
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


def _when(store: Store, holdings: dict | None) -> str:
    """When the holdings' prices are from: the moment as of which every one is still its holding's latest
    (readings.prices_at), or the close they are priced at."""
    rows_ = (holdings or {}).get("holds", {}).values()
    at = prices_at(store, holdings)
    dates = sorted({r["session"] for r in rows_ if r.get("session")})
    return (f"Latest prices at {moment(at)}" if at else
            f"Prices at the close of {day(dates[-1])}" if dates else "Pricing the holdings")


def _session(holdings: dict | None) -> str:
    """The session the portfolio's move covers, where its holdings share one: " on Nov 5"."""
    sessions = {r["session"] for r in ((holdings or {}).get("holds") or {}).values() if r.get("session")}
    return f" on {day(sessions.pop(), year=False)}" if len(sessions) == 1 else ""


def _sign_tone(x: float | None) -> str:
    """A figure's colour by its sign: teal for what rises or is gained, rust for what falls or is lost."""
    return "loss" if x is not None and x < 0 else "gain" if x is not None and x > 0 else ""


def _move_tone(x: float | None, shown: str, nothing: str) -> str:
    """A move's or a share's colour, the colour of its direction, a yield's change among them: none where it is
    nothing as shown."""
    return "" if shown == nothing else _sign_tone(x)


def portfolio_panel(reader: Reader) -> str:
    view_ = portfolio_view(reader)
    if view_ is None:
        return ""
    holdings, shown = view_["holdings"], view_["shown"]
    asof_text = _when(reader.store, holdings)
    vol_html = _volatility_line(reader)
    n_pos = len(view_["holds"])
    if holdings and shown.total is not None:
        total_str = money(shown.total)
        move_str, move_tone = "", ""
        move_fig, move_sub = '<div class="p-kval nil">–</div>', '<div class="p-ksub">No session move</div>'
        if shown.total_moved is not None:
            move_tone = _sign_tone(shown.total_moved)
            pct_str = move(holdings.get("session_move"))
            move_str = f'{money(shown.total_moved, sign=True)} ({pct_str})'
            sess = _session(holdings).strip()
            move_fig = f'<div class="p-kval dmove {move_tone}">{num(money(shown.total_moved, sign=True))}</div>'
            move_sub = f'<div class="p-ksub {move_tone}">{num(pct_str)}' + (f' <span class="when">· {e(sess)}</span>' if sess else "") + '</div>'
        vol_raw = (reader.derived("portfolio", "Portfolio", ("volatility",)).get("volatility") or {}).get("value")
        vol_str = either(vol_raw) if vol_raw is not None else ""
        meta_attrs = (f' data-total="{e(total_str)}" data-move="{e(move_str)}" data-move-tone="{e(move_tone)}"'
                      f' data-vol="{e(vol_str)}" data-asof="{e(asof_text)}" data-count="{n_pos}"')
        head = (f'<div class="p-kpi-grid">'
                f'<div class="p-kpi primary"><span class="p-klab">Total value</span>'
                f'<div class="p-kval total">{num(total_str)}</div><div class="p-ksub asof">{asof_text}</div></div>'
                f'<div class="p-kpi"><span class="p-klab">Today</span>{move_fig}{move_sub}</div>'
                f'<div class="p-kpi"><span class="p-klab">Horizon volatility</span>'
                f'{vol_html or "<div class=p-kval>–</div>"}<div class="p-ksub">To {e(day(reader.store.horizon_end()))}</div></div>'
                f'<div class="p-kpi"><span class="p-klab">Positions</span>'
                f'<div class="p-kval">{num(str(n_pos))}</div><div class="p-ksub">Direct holdings &amp; cash</div></div></div>')
    else:
        meta_attrs = f' data-asof="{e(asof_text)}" data-count="{n_pos}"'
        head = (f'<div class="p-kpi-grid"><div class="p-kpi primary"><span class="p-klab">Total value</span>'
                f'<div class="p-kval total">{WAIT}</div><div class="p-ksub asof">{asof_text}</div></div></div>')
    rows = []
    widest = max((abs(v) for v in shown.value.values()), default=0.0) or 1.0  # the largest holding fills its bar
    for h in sorted(view_["holds"], key=lambda h: -abs(shown.value.get(h["id"]) or 0.0)):  # largest first, whatever it is
        nid, hid = h["to"], h["id"]
        got = (holdings or {}).get("holds", {}).get(hid) or {}
        value = num(money(shown.value[hid])) if hid in shown.value else WAIT
        moved_el = ""
        if hid in shown.moved and _has_move(h):
            moved_el = (f'<span class="mv n {_sign_tone(shown.moved[hid])}">{money(shown.moved[hid], sign=True)} '
                        f'({move(got.get("session_move"))})</span>')
        share_v = shown.value.get(hid)
        bar = (f'<span class="bar" aria-hidden="true"><i style="width:{100 * abs(share_v) / widest:.1f}%"></i></span>'
               if share_v else '<span class="bar" aria-hidden="true"></span>')
        hover = market_question(reader, nid) if nid.startswith("market:") else _named(reader, nid)
        lbl = _holding_label(reader, h)
        sub_nm = f'<span class="nm">{e(hover)}</span>' if hover and hover != lbl else ""
        col_id = f'<span class="h-main"><span class="id"><span class="tk">{e(lbl)}</span>{sub_nm}</span></span>'
        col_wt = f'<span class="wt">{bar}<span class="wt-n n">{shown.weights.get(hid, "")}</span></span>'
        col_val = f'<span class="h-val"><span class="val n">{value}</span>{moved_el}</span>'
        rows.append(f'<li data-k="{e(hid)}">{go(nid, col_id + col_wt + col_val, "go row hold", hover)}</li>')
    h_head = '<div class="h-cols"><span>Position</span><span class="r">Weight</span><span class="r">Value</span></div>'
    holdings_sec = section("Holdings", f'{h_head}<ul class="holdings">{"".join(rows)}</ul>', "p-card")
    # two columns: the holdings, the list whose length is the member's, and beside it a stack of the summaries,
    # where the portfolio's weight is and its factor exposures, so neither column leaves a card-sized gap
    side = _weight_lists(reader, view_) + section("Factor exposures", _exposures(reader, view_), "exposures p-card",
                                                 "exposures")
    grid = f'<div class="p-grid">{holdings_sec}<div class="p-side">{side}</div></div>'
    return f'<header class="ph"{meta_attrs}><h2>Portfolio</h2>{head}</header>' + grid


def _volatility_line(reader: Reader) -> str:
    """The portfolio's volatility to the horizon's end, a size either way, opening on its workings."""
    got = reader.derived("portfolio", "Portfolio", ("volatility",)).get("volatility") or {}
    if got.get("value") is None:  # being worked out: on a reply's turn (pending), or where it waits on a read
        waiting = reader.pending or got.get("waiting") or reader.waits("volatility", "portfolio")
        return f'<div class="p-kval pvol" data-k="pvol">{WAIT}</div>' if waiting else ""
    return (f'<div class="p-kval pvol" data-k="pvol">'
            f'{go("x:portfolio_volatility:portfolio", num(either(got["value"])))}</div>')


def held_as(h: dict) -> str:
    """A holding as the member holds it: the security held, which can be another listing of its company than the one
    its name is kept by (GOOG, of the company kept as GOOGL), or the name's own ticker."""
    return (h.get("stated") or {}).get("ticker") or name_label(h["to"])


def _holding_label(reader: Reader, holding: dict) -> str:
    """A security by its held listing, or a held contract by its side and question."""
    link = holding if "to" in holding else reader.store.link_by_id(holding["holding"])
    if link["to"].startswith("market:"):
        side = (link.get("stated") or {}).get("side", "").title()
        return f'{side} · {market_question(reader, link["to"]) or link["to"]}'
    return heading(held_as(link))


def _ident(nid: str, held: str | None = None) -> str:
    """A holding or a name as a row names it: its ticker, as the person and the ledger name it; a holding by the
    security held."""
    return f'<span class="id"><span class="tk">{e(heading(held or name_label(nid)))}</span></span>'


def _named(reader: Reader, nid: str) -> str | None:
    """The company's or fund's name, which a row gives on hover and its own page in full."""
    return company(reader.store, nid) if not nid.startswith("cash:") and ticker_of(nid) != CASH else None


def _has_move(holding: dict) -> bool:
    """Whether a holding has a move to show: every holding but dollar cash, which responds to nothing. Cash in another
    currency moves with its rate (MATH.md 3.1)."""
    return security_of(holding) != CASH


# ---------------------------------------------------------------- where the portfolio's weight is

BY = (("company", "Company"), ("industry", "Industry"), ("country", "Country"))


@dataclass
class Group:
    """One entry of a list of where the portfolio's weight is, as the page shows it: what it is called, what it opens,
    its dollars to the cent and its weight in tenths of a percent, each apportioned so that the list adds to the
    portfolio's value as the panel shows it; and the entry as the store gives it."""
    label: str
    target: str | None
    dollars: float
    tenths: int
    got: dict


def _label_of(store: Store, by: str, g: dict) -> str:
    """An entry's name: a company's, an industry's, a country's, or, for what is not a company, its kind of asset."""
    if g.get("kind"):
        return KIND_WORDS.get(g["kind"], g["kind"].capitalize())
    if by == "company":
        return g.get("company") or name_of(store, g["name"])
    if by == "industry":
        return g.get("industry") or "Without an industry"
    return g.get("country_name") or g.get("country") or "Without a country"


def _target_of(by: str, g: dict) -> str | None:
    """What an entry opens: a company its page, an industry or a country the names in it; what is not a company,
    nothing further."""
    if g.get("kind"):
        return None
    if by == "company":
        return g.get("name")
    return g.get("id")  # industry:{key} or country:{code}, as the store gives it (derive/owned.py)


def _groups_of(reader: Reader, total: float | None) -> dict[str, list[Group]] | None:
    """The portfolio's weight read three ways (MATH.md 3.1), as the store gives it: by company after look-through, every
    company of every fund's rows; by industry; by country; each ending in what is not a company, by kind, and each
    summing to the portfolio's value. Each list's dollars are apportioned to the cent to the value the panel shows, and
    its weights to the tenth of a percent, so a list adds to the whole it stands under."""
    w = reader.output("weights", "portfolio")
    if not w:
        return None
    out = {}
    for by, _ in BY:
        listed = [g for g in w.get(f"by_{by}") or [] if g.get("dollars")]
        value = w.get("value") or sum(g["dollars"] for g in listed) or 1.0
        cents = to_cents([g["dollars"] for g in listed], total)
        tenths = apportion([1000 * g["dollars"] / value for g in listed], 1000)
        out[by] = [Group(_label_of(reader.store, by, g), _target_of(by, g), c, t, g)
                   for g, c, t in zip(listed, cents, tenths)]
    return out


def _tenth(t: int) -> str:
    return f"{MINUS if t < 0 else ''}{abs(t) / 10:.1f}%"


def _via(reader: Reader, view_: dict, g: Group) -> str:
    """How a company is held, beneath it: through which fund, where one way only; how much directly and through each
    fund, where several, in whole dollars apportioned to the company's dollars as shown so that they add to them; a
    way that is nothing as shown is not listed."""
    nid = g.got.get("name")
    if not nid or not view_["lookthrough"]:
        return ""
    _, direct, through = held_of(view_, nid)
    if not through:
        return ""
    if not direct and len(through) == 1:
        return f'<span class="via"><span class="part">through {e(name_of(reader.store, through[0][0]))}</span></span>'
    names = (["directly"] if direct else []) + [f"through {name_of(reader.store, f)}" for f, _ in through]
    parts = whole_parts(([direct] if direct else []) + [n for _, n in through], g.dollars)
    return ('<span class="via">' + '<span class="dot"> · </span>'.join(
        f'<span class="part">{num(amount(n, sign=False))} {e(w)}</span>' for n, w in zip(parts, names) if n)
        + '</span>')


def _weight_lists(reader: Reader, view_: dict) -> str:
    """Where the portfolio's weight is: its largest companies once its funds are looked through, its largest
    industries, and its largest countries, one shown at a time by the switch above them, each entry with its dollars
    and its weight beside a bar to scale, a company with the funds it is held through; each list opening whole."""
    groups = _groups_of(reader, view_["shown"].total)
    if groups is None:
        if view_["holdings"] and (reader.pending or reader.waits("weights", "portfolio")):
            return section("Exposure", WAIT, "p-card pending weights", "weights")
        return ""
    switch = ('<div class="wt-by" role="group" aria-label="Weight by">'
              + "".join(f'<button type="button" data-wt="{by}">{e(word)}</button>' for by, word in BY) + "</div>")
    lists = []
    for by, word in BY:
        named = [g for g in groups[by] if not g.got.get("kind")]
        widest = max((abs(g.dollars) for g in named[:LISTS_SHOWN]), default=0.0) or 1.0
        rows = []
        for g in named[:LISTS_SHOWN]:
            short = name_of(reader.store, g.got["name"]) if by == "company" and g.got.get("name") else g.label
            full_w = 100 * abs(g.dollars) / widest
            nid_c = g.got.get("name") if by == "company" else None
            if nid_c and view_["lookthrough"]:
                tot_c, dir_c, _ = held_of(view_, nid_c)
                dir_ratio = (abs(dir_c or 0.0) / abs(tot_c)) if tot_c else 0.0
                dir_w = full_w * min(max(dir_ratio, 0.0), 1.0)
                thr_w = max(full_w - dir_w, 0.0)
                bar = (f'<span class="bar stacked" aria-hidden="true">'
                       f'<i class="b-dir" style="width:{dir_w:.1f}%"></i>'
                       f'<i class="b-thr" style="width:{thr_w:.1f}%"></i></span>')
            else:
                bar = f'<span class="bar" aria-hidden="true"><i style="width:{full_w:.1f}%"></i></span>'
            co_sub = f'<span class="nm">{e(g.label)}</span>' if by == "company" and g.label and g.label != short else ""
            via_html = _via(reader, view_, g) if by == "company" else ""
            col_id = (f'<span class="h-main"><span class="id">'
                      f'<span class="tk">{e(heading(short) if by == "company" else short)}</span>{co_sub}</span>'
                      f'{via_html}</span>')
            col_wt = f'<span class="wt">{bar}<span class="wt-n n">{_tenth(g.tenths)}</span></span>'
            col_val = f'<span class="h-val"><span class="val n">{num(amount(g.dollars, sign=False))}</span></span>'
            title = g.label if by == "company" else None
            rows.append(f'<li data-k="{e(g.target or g.label)}">'
                        + (go(g.target, col_id + col_wt + col_val, "go row hold", title)
                           if g.target else f'<span class="row">{col_id}{col_wt}{col_val}</span>')
                        + "</li>")
        count = len(named)
        noun = {"company": "companies", "industry": "industries", "country": "countries"}[by]
        more = go(f"weights:{by}", f"All {count} {noun}" if count > LISTS_SHOWN else "View full breakdown",
                  "linkish all-of")
        w_head = f'<div class="h-cols"><span>{e(word)}</span><span class="r">Weight</span><span class="r">Exposure</span></div>'
        lists.append(f'<div class="wl wl-{by}" data-k="wl:{by}">{w_head}<ul class="holdings weights">{"".join(rows)}</ul>'
                     f'<p class="more">{more}</p></div>')
    return (f'<section class="sec weights p-card" data-k="weights"><div class="wt-head"><h3>Exposure</h3>{switch}</div>'
            f'{"".join(lists)}</section>')


def _weights_view(reader: Reader, by: str) -> dict:
    """A list of where the portfolio's weight is, whole (MATH.md 3.1): every company once its funds are looked through,
    with its industry and country; every industry, with its sector; or every country; each with its dollars and its
    weight, then what is not a company, by kind; adding to the portfolio's value."""
    word = dict(BY).get(by)
    view_ = portfolio_view(reader, factors=False)
    groups = _groups_of(reader, (view_ or {}).get("shown", Shown()).total) if view_ else None
    if not word or groups is None:
        return _missing()
    store = reader.store
    named = [g for g in groups[by] if not g.got.get("kind")]
    kinds = [g for g in groups[by] if g.got.get("kind")]
    if by == "company":
        industries, countries = {g.got.get("industry"): g for g in groups["industry"]}, \
            {g.got.get("country"): g for g in groups["country"]}
        head = ["Company"]

        def cells(g: Group) -> list[str]:  # its name, then its ticker, its industry and its country, quieter beneath
            placed = [_opens(industries.get(g.got.get("industry"))), _opens(countries.get(g.got.get("country")))]
            under = [e(name_of(store, g.target))] if g.target else []
            return [(go(g.target, e(g.label)) if g.target else e(g.label))
                    + f'<div class="aside">{" · ".join(under + [x for x in placed if x])}</div>']
    elif by == "industry":
        head = ["Industry", "Sector", "Companies"]
        cells = lambda g: [_opens(g), e(g.got.get("sector") or ""), num(count(g.got.get("companies")))]  # noqa: E731
    else:
        head = ["Country", "Companies"]
        cells = lambda g: [_opens(g), num(count(g.got.get("companies")))]  # noqa: E731
    rows = [(cells(g), g) for g in named] + [([e(g.label)] + [""] * (len(head) - 1), g) for g in kinds]
    noun = {"company": "companies", "industry": "industries", "country": "countries"}[by]
    title = f"Weight by {word.lower()}"
    lead = (f'<p class="aside">{count(len(named))} {noun if len(named) != 1 else word.lower()}'
            f'.{" Other assets are grouped by type." if kinds else ""}</p>')
    return {"title": title, "html": hero("Portfolio", title=e(title)) + lead + _weight_table(head, rows, len(named))}


def _opens(g: Group | None) -> str:
    return "" if g is None else go(g.target, e(g.label)) if g.target else e(g.label)


def _weight_table(head: list[str], rows: list[tuple[list[str], Group]], named: int, *, first: int = LISTED) -> str:
    """A list as a table: each entry's cells, its dollars and its weight; the entries past `first` shown on request;
    the kinds of what is not a company after the companies; and the total, the sum of what it shows."""
    cols = len(head) + 2
    out = []
    for i, (cells, g) in enumerate(rows):
        cls = "more" if first <= i < named else "kind" if i >= named else ""
        out.append(f'<tr class="{cls}">' + "".join(f"<td>{c}</td>" for c in cells)
                   + f'<td>{num(amount(g.dollars, sign=False))}</td><td>{num(_tenth(g.tenths))}</td></tr>')
        if i == named - 1 and named > first:
            out.append(f'<tr class="all"><td colspan="{cols}"><button class="linkish" type="button" data-all>Show '
                       f'{named - first} more</button></td></tr>')
    out.append(f'<tr class="sum"><td>Total</td>{"<td></td>" * (len(head) - 1)}'
               f'<td>{num(amount(sum(g.dollars for _, g in rows), sign=False))}</td>'
               f'<td>{num(_tenth(sum(g.tenths for _, g in rows)))}</td></tr>')
    return (f'<table class="calc-t holds wtable"><thead><tr>{"".join(f"<th>{h}</th>" for h in head)}<th>Value</th>'
            f'<th>Weight</th></tr></thead><tbody>{"".join(out)}</tbody></table>')


def _group_view(reader: Reader, by: str, gid: str) -> dict:
    """An industry or a country, by its id (`industry:{key}`, `country:{code}`): its dollars, its weight of the
    portfolio, and how many companies it holds; the events that move its companies most; then the companies in it,
    each with its dollars and weight, apportioned so that they add to it, and how each is held. The store knows the
    groups the portfolio holds companies in, and no others: an id that names none of them is as any id that names
    nothing (_missing)."""
    view_ = portfolio_view(reader, factors=False)
    groups = _groups_of(reader, view_["shown"].total) if view_ else None
    group = next((g for g in (groups or {}).get(by) or [] if g.got.get("id") == gid), None)
    if group is None:
        return _missing()
    kicker = "Industry" if by == "industry" else "Country"
    store = reader.store
    inside = [g for g in groups["company"] if not g.got.get("kind") and g.got.get(f"{by}_id") == gid]
    cents = to_cents([g.got["dollars"] for g in inside], group.dollars)
    tenths = apportion([1000 * g.got["dollars"] / sum(x.got["dollars"] for x in inside) * group.tenths / 1000
                        for g in inside], group.tenths) if inside else []
    rows = []
    for g, c, t in zip(inside, cents, tenths):
        shown = Group(g.label, g.target, c, t, g.got)
        under = [x for x in ([e(name_of(store, g.target))] if g.target else []) + [_via(reader, view_, shown)] if x]
        rows.append(([(go(g.target, e(g.label)) if g.target else e(g.label))
                      + (f'<div class="aside">{" · ".join(under)}</div>' if under else "")], shown))
    context = e(group.got.get("sector") or "") if by == "industry" else ""
    facts = stats([("Value", num(amount(group.dollars, sign=False)), ""), ("Weight", num(_tenth(group.tenths)), ""),
                   ("Companies", num(count(group.got.get("companies"))), "after look-through")])
    table = _weight_table(["Company"], rows, len(rows))
    named = [g.got["name"] for g in inside if g.got.get("name")]
    return {"title": group.label, "html": hero(kicker, title=e(group.label), context=context) + facts
            + _group_events(reader, named) + f'<h3 class="sub">Companies</h3>{table}'}


def _group_events(reader: Reader, names: list[str]) -> str:
    """The events that move a group's companies most: each with what it does to them if it happens and if it
    does not, their shares summed as the engine gives them (ledger.moves, from the event's impact, which each figure
    opens to), and the companies that carry most of it."""
    if not names:
        return ""
    try:
        found = moves(reader.store, names)
    except (Unavailable, Waiting, StoreError, RuntimeError):
        return f'<h3 class="sub">Events</h3>{WAIT}' if reader.pending else ""
    items = sorted((m for m in found if not nothing(m["full_impact"])), key=lambda m: -abs(m["full_impact"]))
    if not items:
        return ""
    rows_ = []
    for m in items[:EVENTS_SHOWN]:
        carried = sorted(m["names"], key=lambda n: -abs(n["full_impact"]))[:3]
        by = ("largest contributors " if len(carried) > 1 else "largest contributor ") + \
            series_of([e(name_of(reader.store, n["name"])) for n in carried])
        target = f"x:swing:{m['event']}"
        rows_.append(f'<tr data-k="ev:{e(m["event"])}"><td>{go(m["event"], e(m["title"] or m["event"]))}'
                     f'<div class="aside">Forecast {chance(m["probability"])} · {by}</div></td>'
                     f'<td>{gonum(target, amount(m["yes"]), _tone_whole(m["yes"]))}</td>'
                     f'<td>{gonum(target, amount(m["no"]), _tone_whole(m["no"]))}</td></tr>')
    return ('<h3 class="sub">Events</h3><table class="ifs"><thead><tr><th>Event</th><th>If Yes</th><th>If No</th></tr>'
            f'</thead><tbody>{"".join(rows_)}</tbody></table>')


def _exposures(reader: Reader, view_: dict) -> str:
    """Each factor's dollar beta, for a factor quoted as a yield its dollars for each point the yield rises (MATH.md 3.2),
    and its largest one-month move in the last two years applied to the dollar beta, largest first: the name on its
    own line, the figures under column labels."""
    reach = view_["reach"]
    if not reach or not reach.get("factors"):
        return WAIT if reader.pending or view_["g"] is None else ""
    found = sorted((c for c in reach["factors"] if c["largest_month_applied"] is not None),
                   key=lambda c: -c["largest_month_applied"])
    peak = max((abs(c["largest_month_applied"] or 0.0) for c in found), default=0.0) or 1.0
    items = []
    for c in found:
        proxy = (reader.store.stated_of(c["factor"]) or {}).get("proxy")
        px_tag = f'<span class="f-px">{e(proxy)}</span>' if proxy else ""
        beta = (f'{amount(c["per_point"], sign=True)} /pp' if c.get("per_point") is not None
                else amount(c["dollar_beta"], sign=False) if (c.get("dollar_beta") or 0) >= 0
                else amount(c["dollar_beta"], sign=True))
        stress = c["largest_month_applied"]
        w_pct = 100 * abs(stress or 0.0) / peak
        bar = f'<span class="f-bar" aria-hidden="true"><i style="width:{w_pct:.1f}%"></i></span>'
        inner = (f'<span class="lbl"><span class="nm">{e(heading(c["name"]))}</span>{px_tag}</span>'
                 f'{bar}'
                 f'<span class="xv"><span class="v n">{beta}</span>'
                 f'<span class="v n">{amount(stress, sign=False)}</span></span>')
        items.append(f'<li data-k="{e(c["factor"])}">{go(c["factor"], inner, "go row exp-row")}</li>')
    return ('<div class="xhead"><span class="xh-l">Factor</span><span class="xh-b"></span>'
            '<span class="v">Dollar beta</span><span class="v">Largest month</span></div>'
            f'<ul class="exposures">{"".join(items)}</ul>')


# ---------------------------------------------------------------- the ledger panel


def _ledger(reader: Reader) -> dict | None:
    store = reader.store
    if store.type_of("portfolio") != "Portfolio" or not store.links_of("portfolio", "watches"):
        return None
    try:
        return ledger(store)
    except (StoreError, Unavailable, Waiting):
        return None


def ledger_panel(reader: Reader) -> str:
    """The ledger (TOOLS.md 9): one row per watched event, in the order it was watched, each with its place in both
    orders, volatility and worst outcome, so the page holds the rows in place while the analyst works and moves them
    into the order picked when a reply ends. Its period heads it, then the portfolio's volatility to the horizon's end,
    the band every row's bar is read against, and the heads of the row's four figures over one dollar axis that every
    row's bar shares, labelled once, so the size of each event and the way it cuts read across the list."""
    found = _ledger(reader)
    if not found:
        return ""
    store = reader.store
    by_id = {r["id"]: r for r in found["rows"]}
    ranked = {r["id"]: i for i, r in enumerate(by_volatility(found["rows"]))}  # the page's orders, by the figure picked
    worst = {r["id"]: i for i, r in enumerate(by_worst(found["rows"]))}
    sigma = found.get("volatility")
    axis = _ledger_axis([o["A"] for r in found["rows"] for o in _worlds_of(r)], sigma)
    watches = store.links_of("portfolio", "watches")
    lines = "".join(_line(reader, by_id[link["to"]], ranked[link["to"]], worst[link["to"]], axis)
                    for link in watches)
    days = store.stated_of("portfolio").get("horizon_days")
    n_ev = len(watches)
    key = (f'<span class="lkey"><i aria-hidden="true"></i>Portfolio volatility '
           f'{gonum("x:portfolio_volatility:portfolio", either(sigma))}</span>' if sigma else "")
    axis_col = _axis_head(axis) if axis else '<span class="lcol-sub">Outcomes</span>'
    return (f'<header class="ph lh" data-event-count="{n_ev}">'
            f'<div class="lh-meta"><h2>Events</h2>'
            f'<span class="l-horizon">To {day(found["horizon_end"])} · {days} days</span>{key}</div>'
            f'<div class="lh-actions">{ORDERS}</div></header>'
            f'<div class="ltable-card"><div class="lcols">'
            f'<span class="lc-ev">Event</span>'
            f'<span class="lc-num lc-fc">Forecast<small>Market range</small></span>'
            f'<span class="lc-group">P&amp;L</span>'  # what Yes and No are, said once over both, as the page says it
            f'<span class="lc-num lc-yes">If Yes<small>Of portfolio</small></span>'
            f'<span class="lc-num lc-no">If No</span>'
            f'<div class="lc-axis">{axis_col}</div>'
            f'<span class="lc-num lc-vol">Volatility</span></div>'
            f'<ol class="events" data-hold data-ranked>{lines}</ol></div>')


ORDERS = ('<div class="rank" role="group" aria-label="Order the events">'
          '<button type="button" data-rank="volatility">Volatility</button>'
          '<button type="button" data-rank="worst">Worst outcome</button></div>')


def _ledger_views(shown: str) -> str:
    """The ledger's two views, by the page's one switch: the list, and By holding, which opens as a step."""
    pressed = lambda k: ' aria-pressed="true"' if k == shown else ""  # noqa: E731
    return ('<div class="rank" role="group" aria-label="View">'
            f'<button type="button"{pressed("list")}{"" if shown == "list" else " data-ledger"}>List</button>'
            f'<button type="button"{pressed("holding")}{" data-go=byholding" if shown == "list" else ""}>By holding'
            '</button></div>')


def _round_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round values spanning [lo, hi], the first at or below lo and the last at or above hi, so an axis ends on
    labelled ticks: a step of 1, 2, 2.5 or 5 times a power of ten, about n of them."""
    span = hi - lo
    if span <= 0:
        return [lo]
    step = 10 ** math.floor(math.log10(span / n))
    step *= next(m for m in (1, 2, 2.5, 5, 10) if span / (step * m) <= n)
    first, last = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    return [round(first + k * step, 6) for k in range(round((last - first) / step) + 1)]


def _ledger_axis(values: list[float], sigma: float | None) -> dict | None:
    """The ledger's one dollar axis: it holds every outcome, today, and the portfolio's band, and ends on round ticks,
    so that every row's bar and the labels above them meet the panel's two edges."""
    vals = values + [0.0] + ([-sigma, sigma] if sigma else [])
    if not values or max(vals) - min(vals) < 0.005:
        return None
    ticks = _round_ticks(min(vals), max(vals))
    return {"lo": ticks[0], "hi": ticks[-1], "ticks": ticks, "sigma": sigma}


def _at(axis: dict, x: float) -> str:
    """A place on an axis, as a percent of its width, for the page to lay out at whatever width it has."""
    return f"{100 * (x - axis['lo']) / (axis['hi'] - axis['lo']):.3f}%"


def _axis_head(axis: dict) -> str:
    """The axis once above the list: its end ticks and Today labelled cleanly without crowding,
    Today at zero, and all round tick marks shown beneath."""
    ticks = axis["ticks"]
    shown = [ticks[0]]
    if 0.0 in ticks and 0.0 not in (ticks[0], ticks[-1]):
        pos = (0.0 - axis["lo"]) / (axis["hi"] - axis["lo"])
        if 0.18 <= pos <= 0.82:
            shown.append(0.0)
    if ticks[-1] != ticks[0]:
        shown.append(ticks[-1])
    n = len(shown)
    labels = "".join(f'<span class="{"start" if i == 0 else "end" if i == n - 1 else "mid"}" '
                     f'style="left:{_at(axis, t)}">{axis_label(t)}</span>' for i, t in enumerate(shown))
    marks = "".join(f'<i style="left:{_at(axis, t)}"></i>' for t in ticks)
    return f'<div class="laxis" aria-hidden="true"><div class="tl">{labels}</div><div class="tm">{marks}</div></div>'


def _bar(r: dict, axis: dict) -> str:
    """An event on the ledger's axis: Today at zero, the portfolio's volatility to the horizon the band behind; each
    side of Today reaches the event's farthest outcome there, so the bar's length is the event's whole reach and Today
    divides it where today's prices stand; with ways, each world a tick where it lies."""
    worlds = _worlds_of(r)
    out = []
    if axis.get("sigma"):
        lo, hi = -axis["sigma"], axis["sigma"]
        out.append(f'<i class="band" style="left:{_at(axis, lo)};right:calc(100% - {_at(axis, hi)})"></i>')
    for losing in (True, False):
        side = [o["A"] for o in worlds if (o["A"] < 0 if losing else o["A"] > 0)]
        if side:
            far = min(side) if losing else max(side)
            a, b = sorted((0.0, far))
            out.append(f'<i class="seg {"loss" if losing else "gain"}" style="left:{_at(axis, a)};'
                       f'right:calc(100% - {_at(axis, b)})"></i>')
    if len(worlds) > 2:
        out += [f'<i class="wt {_tone(o["A"])}" style="left:{_at(axis, o["A"])}"></i>' for o in worlds]
    out.append(f'<i class="today" style="left:{_at(axis, 0.0)}"></i>')
    return f'<span class="ebar" aria-hidden="true">{"".join(out)}</span>'


def _implied(r: dict) -> str:
    """The range the markets imply, as a figure (MATH.md 1.2): from its floor to its ceiling, one figure where the two
    meet as shown, a floor or a ceiling alone where they set only one, and nothing where they set neither."""
    lo, hi = _range(r)
    if lo is None or hi is None:
        return ""
    return _span({"lo": lo, "hi": hi})


def _market_words(reader: Reader, r: dict) -> str:
    """The event's own market beside the forecast, as observed, where its midpoint and the forecast differ as shown,
    or where there is no forecast (TOOLS.md 9): "Market · Yes bid 78¢ · Ask 84¢"."""
    book = _own_book(reader, r)
    return f"Market \u00b7 {quotes(*book)}" if book else ""


def _own_book(reader: Reader, r: dict) -> tuple | None:
    """The event's own market's bid and ask, by the side it asks the event through, (bid, ask, side), where its
    midpoint and the forecast differ as shown, or where there is no forecast (TOOLS.md 9); None otherwise."""
    m = r.get("market") or {}
    q = m.get("probability")
    if q is None or (r.get("probability") is not None and chance(q) == chance(r["probability"])):
        return None
    book = reader.derived(m["link"], "asks")
    return _value(book, "bid"), _value(book, "ask"), side_of(reader.store, m["link"])


def _line(reader: Reader, r: dict, i: int, j: int, axis: dict | None) -> str:
    """An event's row: its title; its forecast, Yes, No and volatility under the ledger's heads, and beneath them,
    under the heads' second line, the market-implied range beneath the forecast and Yes's share of the portfolio
    beneath Yes; the event's own market's bid and ask on a line of its own where its midpoint and the forecast
    differ; its bar on the ledger's axis; and, where
    its worst outcome is a way, that way. In the worst-outcome order, the worst outcome is marked where it stands.
    Each figure is the event's where it has it: its probability, or the range its statements leave it; and while the
    analyst works, a figure being worked out shows its place; a figure it does not have is not there."""
    pid = r["id"]
    stated = reader.store.stated_of(pid) or {}
    wend = stated.get("window_end")
    when_sub = f'<span class="esub">By {e(day(wend))}</span>' if wend else ""
    said = _implied(r) if _beside_open(r) else ""
    beside = _market_words(reader, r)
    yes, no, worst = r.get("yes"), r.get("no"), r.get("worst_outcome") or {}
    w = lambda side: " w" if worst.get("world") == side else ""  # noqa: E731
    working_out = reader.waits("impact", pid)
    p_raw = _figure_p(r)
    p = e(p_raw) or (WAIT if reader.reading_waits(pid) else "")
    vol = r.get("volatility")
    caption = share(r.get("share_yes")) if yes is not None else ""
    caption_tone = _move_tone(r.get("share_yes"), caption, share(0.0)) if caption else ""
    way_line = ""
    if worst.get("world") not in (None, "yes", "no"):  # a way: the one outcome the row does not otherwise show
        way_line = (f'<span class="eway">Worst outcome <span class="n {_tone_whole(worst["pnl"])} w">'
                    f'{amount(worst["pnl"])}</span> \u00b7 {e(worst.get("title") or worst["world"])}, '
                    f'{chance(worst["probability"])}</span>')
    mkt_line = f'<span class="em">{e(beside)}</span>' if beside else ""
    col_ev = f'<span class="ec-ev"><span class="et">{title_html(r["title"])}</span>{when_sub}{mkt_line}{way_line}</span>'
    prob_sub = f'<small class="ec-sub mkt-rng">{e(said)}</small>' if said else ""
    col_prob = f'<span class="ec-num"><span class="ep">{p}</span>{prob_sub}</span>'
    yes_val = (f'<span class="{_tone_whole(yes)}{w("yes")}">{amount(yes)}</span>' if yes is not None
               else WAIT if working_out else "")
    yes_sub = f'<small class="ec-sub {caption_tone}">{caption}</small>' if caption else ""
    col_yes = f'<span class="ec-num">{yes_val}{yes_sub}</span>'
    no_val = (f'<span class="{_tone_whole(no)}{w("no")}">{amount(no)}</span>' if no is not None
              else WAIT if working_out else "")
    col_no = f'<span class="ec-num">{no_val}</span>'
    col_bar = f'<span class="ec-bar">{_bar(r, axis) if axis and _worlds_of(r) else ""}</span>'
    vol_val = f'<span>{either(vol)}</span>' if vol is not None else WAIT if working_out else ""
    col_vol = f'<span class="ec-num ec-vol">{vol_val}</span>'
    yes_attr = e(amount(yes)) if yes is not None else ""
    tone_attr = _tone_whole(yes) if yes is not None else ""
    attrs = (f'data-go="{e(pid)}" data-title="{e(r["title"] or pid)}" data-prob="{e(p_raw)}" '
             f'data-yes="{yes_attr}" data-tone="{tone_attr}"')
    return (f'<li class="event" data-k="{e(pid)}" data-volatility="{i}" data-worst="{j}">'
            f'<a class="evline" {attrs}>{col_ev}{col_prob}{col_yes}{col_no}{col_bar}{col_vol}</a></li>')


def by_holding_view(reader: Reader) -> dict:
    """By holding: each event with a P&L on Yes as a row, in the ledger's order, with that P&L and beside it
    what each holding gains or loses on Yes, in whole dollars, the holdings apportioned so that they add to the event's
    Yes as shown. The event and its figure stay in place as the holdings scroll past what the panel holds.
    Each figure opens its working."""
    found = _ledger(reader)
    if not found:
        return _missing()
    rows_ = [r for r in found["rows"] if r.get("yes") is not None]  # each event with a P&L on Yes
    ranked = {r["id"]: i for i, r in enumerate(by_volatility(found["rows"]))}
    worst = {r["id"]: i for i, r in enumerate(by_worst(found["rows"]))}
    moved, shown = _on_yes(reader.store, rows_)
    peak = max((abs(v) for d in shown.values() for v in d.values()), default=0.0) or 1.0
    head = "".join(f'<th class="n">{go(h["to"], e(_holding_label(reader, h)), "go name")}</th>' for h in moved)
    body = []
    for r in rows_:
        pid = r["id"]
        cells = []
        for hid in (h["id"] for h in moved):
            v = shown[pid].get(hid, 0.0)
            tone = _tone_whole(v)
            heat = f' style="--heat:{min(abs(v) / peak, 1.0) * 0.14:.3f}"' if tone in ("loss", "gain") else ""
            cells.append(f'<td class="n {tone}"{heat}>{gonum(f"x:holding:{pid}:{hid}", amount(v))}</td>')
        body.append(f'<tr data-k="{e(pid)}" data-volatility="{ranked[pid]}" data-worst="{worst[pid]}">'
                    f'<th class="ev">{go(pid, title_html(r["title"]), "go name")}</th>'
                    f'<td class="n yes {_tone_whole(r["yes"])}">{gonum(f"x:outcome:{pid}:yes", amount(r["yes"]))}</td>'
                    f'{"".join(cells)}</tr>')
    table = (f'<div class="bhwrap"><table class="bh"><thead><tr><th class="ev">Event</th><th class="n yes">If Yes</th>'
             f'{head}</tr></thead><tbody data-ranked>{"".join(body)}</tbody></table></div>')
    html = (f'<header class="ph lh"><div class="lh-meta"><h2>By holding</h2>'
            f'<span class="l-horizon">To {day(found["horizon_end"])} · each holding\u2019s P&amp;L on Yes</span></div>'
            f'<div class="lh-actions">{ORDERS}</div></header>{table}')
    return {"title": "By holding", "html": html, "wide": True, "surface": "matrix"}


def _on_yes(store: Store, rows_: list[dict]) -> tuple[list[dict], dict[str, dict[str, int]]]:
    """Each holding's P&L on Yes under each event with one, in whole dollars, apportioned so that the holdings add to
    the event's Yes as shown; and the holdings any of the events moves, in the portfolio's order."""
    holds = store.links_of("portfolio", "holds")
    ids = [h["id"] for h in holds]
    shown: dict[str, dict[str, int]] = {}
    for r in rows_:
        got = {h["holding"]: h.get("yes") or 0.0 for h in r.get("by_holding") or []}
        shown[r["id"]] = dict(zip(ids, whole_parts([got.get(i, 0.0) for i in ids], r["yes"])))
    return [h for h in holds if any(shown[r["id"]].get(h["id"]) for r in rows_)], shown


def _tone_whole(x: float | None) -> str:
    """A computed dollar figure's tone as shown in whole dollars: rust, teal, or quiet where it is nothing as shown."""
    if x is None:
        return ""
    return "nil" if nothing(x) else "loss" if x < 0 else "gain"


# ---------------------------------------------------------------- a probability in its place, or the range it lies in


def _span(o: dict | None) -> str:
    """A range a probability lies in, where the statements leave it one (MATH.md 1.2, ONTOLOGY.md 2.1), in the probability's place and
    in the form of the range the markets imply: from its floor to its ceiling; a floor or a ceiling alone where the
    other end is 1 or 0; one figure where the two ends meet as shown; nothing where it is anywhere from 0 to 1."""
    if not o:
        return ""
    floor, ceiling = o["lo"] > ZERO, o["hi"] < 1.0 - ZERO
    if floor and ceiling:
        return chance(o["lo"]) if chance(o["lo"]) == chance(o["hi"]) else f"{chance(o['lo'])}\u2013{chance(o['hi'])}"
    return f"\u2265 {chance(o['lo'])}" if floor else f"\u2264 {chance(o['hi'])}" if ceiling else ""


def _figure_p(r: dict) -> str:
    """An event's probability in its place, as text: its figure; or the range its statements leave it; or nothing."""
    return chance(r["probability"]) if r.get("probability") is not None else _span(r.get("left_open"))


def _beside_open(r: dict) -> bool:
    """Whether the range the markets imply stands beside a probability: beside a figure, and beside the range the
    statements leave only where the two differ as shown, so one range is not said twice."""
    o, (lo, hi) = r.get("left_open"), _range(r)
    if r["probability"] is not None or not _span(o):
        return True
    return lo is not None and hi is not None and _span({"lo": lo, "hi": hi}) != _span(o)


# ---------------------------------------------------------------- a row's figures


WAITING = ("wait",)


def sentences(reader: Reader, r: dict) -> list[list]:
    """A row's figures, each under its label and rounded where it is read: Forecast, with the market-implied range and
    the event's own market's bid and ask where its midpoint differs; Yes, with its share of the portfolio; No; Full
    impact; Volatility; Worst outcome, with its outcome and its probability; Expected value, where it is not zero as
    shown; Largest
    contributors, with the funds they are held through. Each row is a list of parts, its label first, ("b", label), then text, ("go", text, target),
    ("aside", parts) for what reads beside the figure, or WAITING where a value is being worked out. A row with no
    consequence gives its probability only."""
    pid = r["id"]
    lo, hi = _range(r)
    out: list[list] = []
    # its figure, or the range its statements leave it, or its place while it is worked out; else nothing in its place
    P = [("go", _figure_p(r), f"x:p:{pid}")] if _figure_p(r) else [WAITING] if reader.reading_waits(pid) else []
    if not _beside_open(r):
        prob = [("b", "Forecast"), *P]
    elif lo is not None and hi is not None and lo <= ZERO and hi >= 1.0 - ZERO:  # the markets imply nothing of it
        prob = [("b", "Forecast"), *P]
    elif lo is not None and hi is not None and _put(lo, hi, f"x:interval:{pid}") and P:
        prob = [("b", "Forecast"), *P, ("aside", _put(lo, hi, f"x:interval:{pid}"))]
    elif lo is not None and hi is not None and _put(lo, hi, f"x:interval:{pid}"):  # no forecast: the range alone
        prob = [("b", "Market range"), *_put(lo, hi, f"x:interval:{pid}", words="")]
    elif reader.pending and reader.reading_waits(pid):
        prob = [("b", "Forecast"), *P, ("aside", ["market range ", WAITING, "–", WAITING])]
    else:
        prob = [("b", "Forecast"), *P]
    prob += _market_beside(reader, r)
    if len(prob) == 1:  # nothing to say of its probability
        return out
    out.append(prob)
    yes, no, swing = r["yes"], r["no"], r["full_impact"]
    if yes is None and not reader.waits("impact", pid):
        return out
    dollars_ = f"x:swing:{pid}"
    if yes is not None and no is not None:  # the full impact as Yes less No are shown; its exact figure opens from it
        swing = as_shown(yes) - as_shown(no)
    Y, N, D = (("go", amount(x), dollars_) if x is not None else WAITING for x in (yes, no, swing))
    part = [("aside", [("go", share(r["share_yes"]), dollars_, _move_tone(r["share_yes"], share(r["share_yes"]),
                                                                         share(0.0))), " of portfolio"])] \
        if r.get("share_yes") is not None \
        else []
    out.append([("b", "Yes"), Y, *part])
    out.append([("b", "No"), N])
    out.append([("b", "Full impact"), D])
    vol = r.get("volatility")
    if vol is not None or reader.waits("impact", pid):
        out.append([("b", "Volatility"), ("go", either(vol), f"x:volatility:{pid}") if vol is not None else WAITING])
    worst = r.get("worst_outcome") or {}
    if worst.get("pnl") is not None:
        out.append([("b", "Worst outcome"), ("go", amount(worst["pnl"]), f"x:outcome:{pid}:{worst['world']}"),
                    ("aside", [f"{worst.get('title') or worst['world']}, {chance(worst['probability'])}"], " · ")])
    mu = r.get("expected")
    if mu is not None and not nothing(mu):  # nothing where today's prices carry each outcome at its probability
        out.append([("b", "Expected value"), ("go", amount(mu), dollars_)])
    names = r["contributors"] or []
    if names:
        parts: list = [("b", "Largest contributors")]
        for i, n in enumerate(names):  # each with its contribution to the full impact (TOOLS.md 9)
            parts += ([", "] if i else []) + [("go", name_of(reader.store, n["name"]), n["name"]), " ",
                                              ("go", amount(n["full_impact"]), dollars_)]
        # the funds the named holdings come through, except a fund already named by its own other holdings
        named_funds = {f for n in names if pseudo(ticker_of(n["name"])) for f in n["through"]}
        # in the order they come, each name's funds largest first and the names largest first
        funds = [f for f in dict.fromkeys(f for n in names if not pseudo(ticker_of(n["name"])) for f in n["through"])
                 if f not in named_funds]
        for i, f in enumerate(funds):
            parts += [", through " if not i else ", ", ("go", name_of(reader.store, f), f)]
        out.append(parts)
    return out


def _market_beside(reader: Reader, r: dict) -> list:
    """The event's own market beside the forecast, as observed, where its midpoint and the forecast differ as shown
    (TOOLS.md 9): the deepest market that asks the event with a price, through the side it asks, opening the market."""
    if not _market_words(reader, r):
        return []
    m = r["market"]
    book = reader.derived(m["link"], "asks")
    said = quotes(_value(book, "bid"), _value(book, "ask"), side_of(reader.store, m["link"]))
    return [("aside", ["market ", ("go", said, m["market"])])]


def _put(lo: float, hi: float, span: str, words: str = "market range ") -> list | None:
    """The market-implied range beside a forecast, after its words: a floor to a ceiling, a floor alone, or a
    ceiling alone; None where the markets set neither."""
    floor, ceiling = lo > ZERO, hi < 1.0 - ZERO
    if floor and ceiling and chance(lo) == chance(hi):  # a range closed to one figure as shown is said once
        return [words, ("go", chance(lo), span)]
    if floor and ceiling:
        return [words, ("go", chance(lo), span), "–", ("go", chance(hi), span)]
    if floor:
        return [words + "≥ ", ("go", chance(lo), span)]
    if ceiling:
        return [words + "≤ ", ("go", chance(hi), span)]
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
        elif len(part) > 3:  # a figure in its colour: ("go", text, target, class)
            out.append(gonum(part[2], part[1], part[3]) if part[2].startswith("x:") else
                       go(part[2], e(part[1]), f"go {part[3]}"))
        else:
            text, target = part[1], part[2]
            out.append(gonum(target, text) if target.startswith("x:") else go(target, e(text)))
    return "".join(out)


def figures_html(rows: list[list]) -> str:
    """A row's figures, each under its label."""
    cells = "".join(f'<dt>{e(row[0][1])}</dt><dd data-k="fig:{e(row[0][1])}">{sentences_html(row[1:])}</dd>'
                    for row in rows)
    return f'<dl class="figs">{cells}</dl>'


# ---------------------------------------------------------------- an opened event


MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!$|>~])")


def as_written(passage: str) -> str:
    """A passage as its page reads: a fetched page's text is markdown, which writes a dollar sign, an asterisk or a
    bracket with a backslash before it, and the passage quotes that text word for word."""
    return MD_ESCAPE.sub(r"\1", passage or "")


def _passage_target(sid: str, passage: str) -> str:
    return f"x:passage:{sid}:{_passage_hash(passage)}"


def _to_note(note: str) -> str:
    """A link to the note the analyst gave its reasoning in, where the event's header holds it: its Rationale."""
    return f'<a class="to" data-to="{e(note)}" href="#">Rationale</a>'


def _stated_basis(reader: Reader, value: dict, owner: str, *, below: Iterable[str] = ()) -> str:
    """A stated assumption's basis: the page it cites, with each passage quoted; and the analyst's reasoning,
    the note it was stated with, and, where what states it is a link, a statement of its own such as a consequence, any
    other note about that link; each note its first lines shown and the rest a click away, or, where it shows in the
    event's Rationale, a link to it there. A note about a proposition is about the proposition, which states several
    chances, each with its own note, so it is the basis of none of them: the event's Rationale shows it."""
    store = reader.store
    out = ""
    if value.get("source_id"):
        sid = value["source_id"]
        src = store.source_row(sid) or {}
        quoted = "".join(f'<blockquote class="q" data-k="q{i}">{go(_passage_target(sid, passage), e(_as_read(passage)), "go quote")}'
                         f'</blockquote>' for i, passage in enumerate(value.get("passages") or []))
        dated = f" · {day(src.get('dated'))}" if src.get("dated") else ""
        head = f'<p class="src">{go(sid, e(host(src.get("url"))))}{dated}</p>'  # the page: what rests on it, its text
        out += f'<div class="basis">{head}{quoted}</div>'
    notes = [value["note"]] if value.get("note") and store.type_of(value["note"]) == "Note" else []
    if store.type_of(owner) == "link":
        notes += [n["from"] for n in store.links_of(owner, "about", "in") if n["from"] not in notes]
    here = [n for n in notes if n not in below]
    if here:
        out += f'<div class="basis note">{fold("".join(note_html(store.stated_of(n).get("text")) for n in here))}</div>'
    return out + "".join(f'<p class="ptr">{_to_note(n)}</p>' for n in notes if n in below)


def _stated_chance(store: Store, key: str) -> dict:
    """A chance as the analyst stated it, with its basis, by its key: prop:N.chances[i]."""
    pid, _, rest = key.partition(".chances[")
    items = (store.stated_of(pid) or {}).get("chances") or []
    i = int(rest.rstrip("]")) if rest.rstrip("]").isdigit() else -1
    return items[i] if 0 <= i < len(items) else {}


def _said_chance(store: Store, of: str, given, given_not) -> str:
    """A chance in words, by the propositions' titles."""
    cond = [*(quote(title_of(store, x)) for x in given), *(f"not {quote(title_of(store, x))}" for x in given_not)]
    head = f"the chance of {quote(title_of(store, of))}"
    return f"{head} given {' and '.join(cond)}" if cond else f"{head} stated on its own"


READING = ("probability", "left_open", "range")  # a proposition's reading in its structure (1.2)


def _structure_of(reader: Reader, pid: str) -> dict:
    """A proposition's structure read whole, from the Computation behind its reading: what cannot hold together, and
    each stretched book, keyed by its asks link."""
    d = reader.derived(pid, "Proposition", READING)
    cid = _basis(d, "probability") or _basis(d, "left_open") or _basis(d, "range")
    return reader.computation(cid).get("output") or {}


def _statement_words(reader: Reader, tag: str) -> str:
    """One of the analyst's statements, by its key: a chance, with its figure, or a link between two propositions."""
    if ".chances[" in tag:
        c = _stated_chance(reader.store, tag)
        if not c:
            return ""
        said = _said_chance(reader.store, tag.split(".chances[")[0], c.get("given") or [], c.get("given_not") or [])
        x = c.get("value")  # a reading of a ladder in place of a figure says itself in the working above
        return e(said[:1].upper() + said[1:]) + (f", {num(chance(x))}" if isinstance(x, (int, float)) else "")
    return _link_sentence(reader, tag, "lo")


def _rests(reader: Reader, tags: Iterable[str], side: str, stretched: dict | None = None) -> list[str]:
    """What one end of a range rests on, in words (MATH.md 1.2): each taken book's bid or ask, with how far it was
    stretched where it was; each proposition held at its probability; then each chance and each link."""
    tags = list(tags)
    books = [t for t in tags if t.endswith(":book")]
    held = [t for t in tags if t.endswith(":price")]
    out = [_link_sentence(reader, t, side, (stretched or {}).get(t.removesuffix(":book"))) for t in books]
    for t in held:  # a proposition its market prices, at the probability it reads
        link = reader.store.link_by_id(t.removesuffix(":price")) or {}
        a = link.get("from")
        q = _reading_of(reader, a).get("p") if a else None
        if q is not None:
            out.append(f'{go(a, e(quote(title_of(reader.store, a))))} at its probability, '
                       f'{gonum("x:p:" + a, chance(q))}')
    out += [_statement_words(reader, t) if ".chances[" in t else _link_sentence(reader, t, side)
            for t in tags if t not in books and t not in held]
    return [x for x in out if x]


def _rung_date(reader: Reader, ch: dict, q: str) -> str | None:
    """A ladder rung's date, from the chance's `through` map, the proposition's window_end, or the market's strike_date."""
    through = ch.get("through") or {}
    if q in through:
        return through[q]
    if schema.MARKET_ID_RE.match(q):
        listing = reader.output("market_listing", q) or {}
        return listing.get("strike_date") or (listing.get("close_time") or "")[:10] or None
    return (reader.store.stated_of(q) or {}).get("window_end")


def _prop_name(reader: Reader, q: str) -> str:
    """Plain human label for a proposition or market."""
    if schema.MARKET_ID_RE.match(q):
        return market_question(reader, q) or q
    return title_of(reader.store, q)


def _jointly(reader: Reader, pid: str, out: dict) -> str:
    """A forecast the store reads from its structure as a whole, where no sum gives it (MATH.md 1.2): each chance the
    analyst states, beside what it is given, with the reading it names and its basis; then the markets its bounds rest
    on. What the chances and the books come to together is the structure's, in the full details."""
    store = reader.store
    rows, bases, through = [], [], {}
    for c in out.get("chances") or []:
        ch = _stated_chance(store, c["key"])
        kind = (c.get("reading") or {}).get("kind") if isinstance(c.get("reading"), dict) else ch.get("reading")
        how = {"even": "Even through time", "hazard": "Constant daily hazard"}.get(kind, "By the calendar" if kind else "")
        for q in (*c.get("given", []), *c.get("given_not", [])):
            when = _rung_date(reader, ch, q) if ch.get("through") else None
            if when:
                through[q] = when
        rows.append(f'<tr><td><div class="fc-case">{_given_words(store, c)}</div>'
                    + (f'<div class="fc-sub">{e(how)}</div>' if how else "") + '</td>'
                    f'<td class="fc-val n">{num(chance(c.get("value")))}</td></tr>')
        basis = _stated_basis(reader, ch, pid)
        if basis:
            bases.append(basis)
    table = (f'<table class="fc-table"><thead><tr><th>Condition</th><th class="r">Chance</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>')
    rng = out.get("range") or {}
    markets = list(dict.fromkeys(m for x in (*(rng.get("lo_rests") or []), *(rng.get("hi_rests") or []),
                                             *(out.get("rests") or []))
                                 if schema.MARKET_ID_RE.match(m := x.rsplit(":", 1)[0] if x.endswith((":book", ":price")) else x)))
    cards = "".join(_market_card(reader, m, tag=f"Through {day(through[m])}" if m in through else "") for m in markets)
    return (table + (f'<h3 class="sub">Bounds</h3><div class="fc-rungs">{cards}</div>' if cards else "")
            + ('<h3 class="sub">Rationale</h3>' + "".join(bases) if bases else ""))


def _market_card(reader: Reader, mid: str, *, lid: str | None = None, tag: str = "", extra: str = "") -> str:
    """A market where a forecast rests on it, as one card: its question, opening the market; its midpoint, the
    probability it is read as, a percent as the forecast's scale has it, opening its book; then the market itself, its
    venue, what it counts through where a date is named, and its book as the venue quotes it, in cents, through the
    side it asks where `lid` names its link."""
    if lid:
        book = reader.derived(lid, "asks")
        bid, ask, price = _value(book, "bid"), _value(book, "ask"), _value(book, "price")
    else:
        mp = reader.output("market_price", mid) or {}
        bid, ask, price = mp.get("bid"), mp.get("ask"), mp.get("price")
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    quoted = quotes(bid, ask, side_of(reader.store, lid) if lid else "yes", html=True) \
        if bid is not None or ask is not None else ""  # the book as its venue quotes it
    sub = " \u00b7 ".join(heading(x) for x in (e(venue), e(tag), quoted, extra) if x)
    figure = gonum("x:price:" + mid, chance(price)) if price is not None else ""
    return (f'<div class="fc-rung"><div class="fc-rhead">{go(mid, e(market_question(reader, mid) or mid), "go name")}'
            f'<span class="fc-rmid n">{figure}</span></div><div class="fc-rsub">{sub}</div></div>')


def _scale(pid: str, p: float | None, lo: float | None, hi: float | None, said: str | None = "Market range") -> str:
    """A probability on its scale, the one picture every forecast is drawn in (MATH.md 1.2): 0% to 100%, the bounds the
    markets set on it (its market range: a market's bid and ask where one asks it, the markets that bound it
    otherwise, 0% and 100% where none does) as a band, and the forecast as a dot, each figure a percent; `said` names
    where the bounds come from, opening the range's working."""
    bound = lo is not None and hi is not None and (lo > ZERO or hi < 1.0 - ZERO)
    lo_, hi_ = (lo, hi) if bound else (0.0, 1.0)
    at = lambda x: f"{100.0 * min(max(x, 0.0), 1.0):.2f}%"  # noqa: E731
    band = (f'<i class="ps-band" style="left:{at(lo_)};width:calc({at(hi_)} - {at(lo_)})"></i>' if bound else "")
    dot = f'<i class="ps-dot" style="left:{at(p)}"></i>' if p is not None else ""
    end = lambda words, x, set_, cls="": (f'<div class="fcb-end{cls}"><span>{words}</span><strong>{chance(x)}</strong></div>'  # noqa: E731
                                           if bound and set_ else f'<div class="fcb-end{cls}"></div>')  # an end the markets set
    ends = (f'<div class="fcb-ends">{end("Lower bound", lo_, lo_ > ZERO)}'
            + (f'<div class="fcb-mid"><span>Forecast</span><strong>{chance(p)}</strong></div>' if p is not None else "")
            + f'{end("Upper bound", hi_, hi_ < 1.0 - ZERO, " r")}</div>')
    sub = (f'<div class="fcb-sub">{go(f"x:interval:{pid}", e(said))}</div>'
           if bound and said is not None else "")
    return (f'<div class="fc-bridge pscale">{ends}<div class="ps-track">{band}{dot}</div>'
            f'<div class="ps-ticks"><span>0%</span><span>100%</span></div>{sub}</div>')


def _derivation(reader: Reader, pid: str, point: dict, p: float) -> str:
    """First-principles breakdown of a derived forecast: either a date-ladder interpolation between two market rungs
    or a contribution table across cases: each case's probability, the event's share of it, and its contribution, each
    under its label as a probability is shown, the contributions adding to the forecast as shown, and no arithmetic
    written out that figures at one decimal could not keep. The working to four decimals is a step down, in the full
    details."""
    store = reader.store
    terms = point["terms"]

    # 1. Check if this is a date interpolation between ladder rungs (e.g., Strait of Hormuz between Apr 30 and May 31)
    interp_term = next((t for t in terms if t.get("chance") and _stated_chance(store, t["chance"]).get("reading")), None)
    if interp_term is not None:
        ch = _stated_chance(store, interp_term["chance"])
        reading = ch.get("reading")
        ev_date = (store.stated_of(pid) or {}).get("window_end")
        q_lo = interp_term["given_not"][0] if interp_term.get("given_not") else None
        q_hi = interp_term["given"][0] if interp_term.get("given") else None
        d_lo = _rung_date(reader, ch, q_lo) if q_lo else store.meta("asof")
        d_hi = _rung_date(reader, ch, q_hi) if q_hi else None
        base_term = next((t for t in terms if not t.get("chance") and (t.get("share") or 0) >= 1.0 - 1e-9), None)
        p_lo = base_term["p"] if base_term and base_term.get("p") is not None else 0.0
        span_p = interp_term.get("p")
        share_s = interp_term.get("share") or 0.0

        method = {"even": "Even through time", "hazard": "Constant daily hazard"}.get(
            reading if isinstance(reading, str) else "", "Calendar-weighted")
        days_note = ""
        if reading == "even" and d_lo and d_hi and ev_date:
            try:
                dt0, dt1, dte = date.fromisoformat(d_lo), date.fromisoformat(d_hi), date.fromisoformat(ev_date)
                total_d, part_d = (dt1 - dt0).days, (dte - dt0).days
                if total_d > 0 and 0 <= part_d <= total_d:
                    days_note = f"{part_d} of {total_d} days"
            except ValueError:
                pass

        active = [t for t in (base_term, interp_term) if t is not None and (t.get("joint") is not None or t.get("share"))]
        pts = apportion([1000 * (t.get("joint") or 0.0) for t in active], round(1000 * p)) if active else []
        trows = []
        for idx, (t, pt) in enumerate(zip(active, pts)):
            contrib = f"+{_tenth(pt)}" if idx > 0 and pt > 0 else _tenth(pt)
            if t is base_term:
                lbl = f"By {e(day(d_lo))}" if d_lo else go(q_lo, e(_prop_name(reader, q_lo))) if q_lo else "Prior window"
                sub = "From its market"
                prob, share = chance(t.get("p")), chance(t.get("share"))
            else:
                lbl = f"{e(day(d_lo, year=False))} – {e(day(ev_date))}" if d_lo and ev_date else "Interpolated window"
                sub = f"{e(method)}" + (f" ({e(days_note)})" if days_note else "")
                prob, share = chance(span_p), chance(share_s)
            trows.append(f'<tr><td><div class="fc-case">{lbl}</div><div class="fc-sub">{sub}</div></td>'
                         f'<td class="fc-math n">{e(prob)}</td><td class="fc-math n">{e(share)}</td>'
                         f'<td class="fc-val n">{num(contrib)}</td></tr>')
        trows.append(f'<tr class="tot"><td>Forecast' + (f' (by {e(day(ev_date))})' if ev_date else "") + '</td>'
                     f'<td></td><td></td><td class="fc-val n">{num(chance(p))}</td></tr>')
        table = (f'<table class="fc-table"><thead><tr><th>Window</th><th class="r">Probability</th>'
                 f'<th class="r">Chance</th><th class="r">Contribution</th></tr></thead>'
                 f'<tbody>{"".join(trows)}</tbody></table>')

        # the markets that bound it, each as a market shows in a forecast
        rungs_html = []
        for q in [x for x in (q_lo, q_hi) if x]:
            d_r = _rung_date(reader, ch, q)
            tag = f"Through {day(d_r)}" if d_r else ""
            if schema.MARKET_ID_RE.match(q):
                rungs_html.append(_market_card(reader, q, tag=tag))
            else:
                rp = _reading_of(reader, q).get("p")
                rungs_html.append(f'<div class="fc-rung"><div class="fc-rhead">{go(q, e(_prop_name(reader, q)), "go name")}'
                                  f'<span class="fc-rmid n">{gonum("x:p:" + q, chance(rp)) if rp is not None else ""}</span></div>'
                                  + (f'<div class="fc-rsub">{e(tag)}</div>' if tag else "") + '</div>')
        rungs_sec = f'<h3 class="sub">Bounds</h3><div class="fc-rungs">{"".join(rungs_html)}</div>' if rungs_html else ""
        basis = _stated_basis(reader, ch, pid)
        basis_sec = f'<h3 class="sub">Rationale</h3>{basis}' if basis else ""
        return table + rungs_sec + basis_sec

    # 2. General conditional breakdown (e.g., total probability over one or more conditions)
    active = [t for t in terms if t.get("chance") or (t.get("share") or 0.0) > 1e-9]
    if not active:
        active = terms

    # Remove redundant implied conditions in labels: if x in given always appears with y in given across terms, x alone names it
    given_sets = [set(t.get("given") or []) for t in terms]

    def _clean_cond(t: dict) -> tuple[str, str]:
        g_raw = list(t.get("given") or [])
        gn_raw = list(t.get("given_not") or [])
        g_keep = [x for x in g_raw if not any(y != x and all((x not in s) or (y in s) for s in given_sets) for y in g_raw)]
        gn_keep = [y for y in gn_raw if not any(x != y and all((x not in s) or (y in s) for s in given_sets) for x in gn_raw)]
        parts, plain_parts = [], []
        for x in (g_keep or g_raw):
            nm = _prop_name(reader, x)
            parts.append(go(x, e(nm), "go name"))
            plain_parts.append(nm)
        for x in (gn_keep or gn_raw):
            nm = _prop_name(reader, x)
            parts.append(f'Not {go(x, e(nm), "go name")}')
            plain_parts.append(f"not {nm}")
        return (" · ".join(parts) or "Base case"), (", ".join(plain_parts) or "base case")

    all_joint = all(isinstance(t.get("joint"), (int, float)) for t in active)
    pts = apportion([1000 * t["joint"] for t in active], round(1000 * p)) if all_joint and active else [None] * len(active)

    trows = []
    bases = []
    for idx, (t, pt) in enumerate(zip(active, pts)):
        cond_html, cond_plain = _clean_cond(t)
        p_case = (chance(t["p"]) if isinstance(t.get("p"), (int, float)) else
                  f'{chance(t["range"]["lo"])}–{chance(t["range"]["hi"])}' if t.get("range") else "—")
        sh = chance(t["share"]) if isinstance(t.get("share"), (int, float)) else "—"
        how = "Analyst input" if t.get("chance") else "Included by definition" if t.get("by") == "links" else ""
        j_raw = t.get("joint")
        contrib = ((f"+{_tenth(pt)}" if idx > 0 and pt > 0 else _tenth(pt)) if pt is not None else
                   f'{chance(j_raw["lo"])}–{chance(j_raw["hi"])}' if isinstance(j_raw, dict) and "lo" in j_raw else "—")
        trows.append(f'<tr><td><div class="fc-case">{cond_html}</div>'
                     + (f'<div class="fc-sub">{e(how)}</div>' if how else "") + '</td>'
                     f'<td class="fc-math n">{e(p_case)}</td><td class="fc-math n">{e(sh)}</td>'
                     f'<td class="fc-val n">{num(contrib)}</td></tr>')
        if t.get("chance"):
            b_html = _stated_basis(reader, _stated_chance(store, t["chance"]), pid)
            if b_html:
                hdr = f"Rationale · {sh} given {e(cond_plain)}" if len(active) > 1 else "Rationale"
                bases.append(f'<h3 class="sub">{hdr}</h3>{b_html}')

    trows.append(f'<tr class="tot"><td>Forecast</td><td></td><td></td><td class="fc-val n">{num(chance(p))}</td></tr>')
    table = (f'<table class="fc-table"><thead><tr><th>Condition</th><th class="r">Probability</th>'
             f'<th class="r">Chance</th><th class="r">Contribution</th></tr></thead><tbody>{"".join(trows)}</tbody></table>')
    return table + "".join(bases)


def _reading_of(reader: Reader, pid: str) -> dict:
    """A proposition's reading in its structure, from the Computation behind its range."""
    d = reader.derived(pid, "Proposition", READING)
    cid = _basis(d, "probability") or _basis(d, "left_open") or _basis(d, "range")
    return ((reader.computation(cid).get("output") or {}).get("props") or {}).get(pid) or {}


# how a proposition's forecast is set, beside it in a working: a derived one opens its own calculation from its name
HOW_SET = {"market": "Midpoint", "read": "Read from the markets", "chance": "Analyst input"}


def _how_set(reader: Reader, pid: str) -> str:
    """How a proposition's forecast is set, in the working's words: Midpoint, its own market's midpoint; Read from the
    markets, that market read with the markets on related propositions; Analyst input, a chance the analyst states
    on its own; and nothing where its conditional probabilities give it, whose own calculation opens from its name."""
    return HOW_SET.get(_reading_of(reader, pid).get("rule"), "")


def _reached(store: Store, pid: str) -> list[str]:
    """The propositions the event's structure reaches, the event first, each once, in the order a walk from the event
    meets them: by a link of the breakdown, and by a chance, which ties the proposition it is the chance of to each it
    is given."""
    seen, queue, order = {pid}, deque([pid]), [pid]
    while queue:
        here = queue.popleft()
        near = [link["to"] if link["from"] == here else link["from"]
                for link in store.links_of(here, direction="both") if link["relation"] in STRUCTURE]
        near += [x for c in store.stated_of(here).get("chances") or [] for x in (*c["given"], *c["given_not"])]
        near += [q for q, _ in store.given_by(here)]
        for other in near:
            if other not in seen:
                seen.add(other)
                order.append(other)
                queue.append(other)
    return order


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
                if mid in seen or store.type_of(mid) != "Market" or store.links_of(mid, "asks", "in"):
                    continue
                seen.add(mid)
                out.setdefault(pid, []).append((mid, note))
    return out


def _market_head(listing: dict, mid: str) -> str:
    """A market's question, opening the market, under its family's title where the two differ: a market of a family
    can be named by a fragment the title completes (MATH.md 3.3)."""
    q = as_asked(listing.get("question")) or mid
    family = ((listing.get("family") or {}).get("title") or "").strip()
    above = f'<div class="mfam">{e(family)}</div>' if family and family != q.strip() else ""
    return f'{above}<a class="go mq" data-go="{e(mid)}">{e(q)}</a>'


def _stretched_on(reader: Reader, pid: str) -> dict[str, dict]:
    """The taken books on a proposition that its structure's reading stretched past their bid or their ask so that they
    hold with the statements, and that its probability is read within (MATH.md 1.2), each by its asks link."""
    found = _structure_of(reader, pid).get("stretched") or {}
    return {k: s for k, s in found.items() if s.get("on") == pid}


def _stretch_words(stretched: dict | None) -> str:
    """A stretched book beside the market it is, in a probability's working: stretched by how much, below its bid or
    above its ask, in the proposition's terms."""
    if not stretched:
        return ""
    said = [f"{num(chance(stretched.get(k)))} {words}" for k, words in (("below", "below its bid"),
                                                                        ("above", "above its ask"))
            if (stretched.get(k) or 0) > 0]
    return f'<span class="how-set">Stretched {" and ".join(said)}</span>' if said else ""


def _asks_sides(reader: Reader, lid: str) -> str:
    """A taken market's bid and ask for the proposition it asks, through the side it asks (MATH.md 1.2)."""
    book = reader.derived(lid, "asks")
    bid, ask = _value(book, "bid"), _value(book, "ask")
    return quotes(bid, ask, side_of(reader.store, lid)) if bid is not None or ask is not None else ""


def _market_item(reader: Reader, link: dict) -> str:
    """A market under the proposition it asks, as a row: its question and its midpoint, through the side it asks; its
    venue, whether it is open, its book as observed through that side, its depth, and where its price is not taken,
    that it is not used; who won, once it has settled (MATH.md 3.3)."""
    mid, lid = link["to"], link["id"]
    listing = reader.output("market_listing", mid) or {}
    price = reader.output("market_price", mid) or {}
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    opened = price.get("open")
    side = link["stated"].get("side") or "yes"
    asked = reader.derived(lid, "asks")  # its book through the side it asks
    settled = (reader.output("market_settlement", mid) or {}).get("settlement") or {}
    if settled.get("settled"):  # its result, in the market's own terms, in place of a book its settling emptied
        figure = f'<span class="won">{e({1: "Yes won", 0: "No won"}.get(settled.get("result"), "Neither side won"))}</span>'
    elif _value(asked, "price") is not None:
        figure = '<span class="pt-w">Midpoint</span> ' + gonum("x:price:" + mid, chance(_value(asked, "price")))
    else:
        figure = WAIT if reader.waits("market_price", mid) else go("x:price:" + mid, "no price", "go")
    meta = [venue] + ([f"settled {day(settled['at'])}" if settled.get("at") else "settled"] if settled.get("settled")
                      else ["open"] if opened else ["closed"] if opened is False else [])
    book = {} if settled.get("settled") else price  # a settled market's quotes, if any stand, say nothing
    if book.get("bid") is not None or book.get("ask") is not None:
        meta.append(quotes(_value(asked, "bid"), _value(asked, "ask"), side, html=True))
    if book.get("past"):
        meta.append(f'from its history on {day(price.get("price_date"))}')
    elif book.get("depth") is not None:
        meta.append(f'depth {num(amount(price["depth"], sign=False))}')
    taken = (link["stated"].get("taken") or {}).get("value", True) is not False
    if not taken:
        meta.append("price not used")
    lines = [f'<div class="mmeta">{" · ".join(heading(m) for m in meta)}</div>']
    return (f'<li class="mkt" data-k="m:{e(lid)}">'
            f'<div class="mhead"><div class="mqw">{_market_head(listing, mid)}</div>'
            f'<span class="mpx">{figure}</span></div>{"".join(lines)}</li>')


def _searches(r: dict, *, open_: bool) -> str:
    """The searches behind the event's breakdown (MATH.md 3.3, TOOLS.md 5): each search of the venues, in the analyst's words, of
    the web, and of a company's record, with how many results it returned."""
    s = r["searches"]
    n_m, n_w, n_c = s["market_searches"], s["web_searches"], s.get("company_searches", 0)
    if not n_m and not n_w and not n_c:
        return '<p class="aside" data-k="searches">No searches recorded.</p>'
    counts = series_of([x for x in (f"{n_m} market search{'es' if n_m != 1 else ''}" if n_m else "",
                                    f"{n_w} web search{'es' if n_w != 1 else ''}" if n_w else "",
                                    f"{n_c} company record{'s' if n_c != 1 else ''}" if n_c else "") if x])
    items = []
    for q in s["queries"]:
        items.append(f'<li>The markets for {e(quote(q.get("text") or ""))} · {q["returned"]} returned</li>')
    for q in s["web_queries"]:
        items.append(f'<li>The web for {e(quote(q.get("query") or ""))} · {q["returned"]} returned</li>')
    for q in s.get("company_queries") or []:
        items.append(f'<li>{e(q.get("ticker") or "")}\'s {e(q.get("kind") or "record")} · {q["returned"]} returned</li>')
    return (f'<details class="searches" data-k="searches"{" open" if open_ else ""}><summary>{e(counts)}</summary>'
            f'<ul>{"".join(items)}</ul></details>')


# ---------------------------------------------------------------- where the probability comes from, drawn
#
# Where a mark or a figure sits says what it is. On the scale, a mark is a position: a book at its quotes and its
# price, the forecast, and the range the markets imply. Each figure stands beside the mark it states, and a
# conditional probability beside its whole condition.


def _pt_domain(places: list[float]) -> tuple[float, float]:
    """The scale: 0% to 100%, unless every position the drawing places lies within fifteen points of the others, as a
    ladder's rungs often do; then the span they lie in, to the nearest five points either side, its ends on the axis.
    A span that would start within ten points of 0% starts at 0%."""
    xs = [x for x in places if x is not None]
    if len({round(x, 6) for x in xs}) < 2 or max(xs) - min(xs) > 0.15:
        return (0.0, 1.0)
    lo = max(0.0, math.floor((min(xs) - 0.005) * 20) / 20)
    hi = min(1.0, math.ceil((max(xs) + 0.005) * 20) / 20)
    if hi - lo < 0.10:
        hi = min(1.0, lo + 0.10)
        lo = max(0.0, hi - 0.10)
    if lo <= 0.10:  # a scale that would start this near 0% starts at 0%, with no break
        lo = 0.0
    return (lo, hi)


def _pt_x(v: float, dom: tuple[float, float]) -> str:
    lo, hi = dom
    return f"{max(0.0, min(1.0, (v - lo) / (hi - lo))) * 100:.3f}%"


def _pt_label(v: float, dom: tuple[float, float], text: str, cls: str = "") -> str:
    """A figure beside the point it states, after it, or before it where the scale's end is near."""
    lo, hi = dom
    at = (v - lo) / (hi - lo)
    side = " pt-l" if at > 0.82 else ""
    return f'<span class="pt-v{side}{" " + cls if cls else ""}" style="left:{_pt_x(v, dom)}">{text}</span>'


def _given_words(store: Store, t: dict) -> str:
    """A case the analyst's chance is given, each proposition in its own title on its own line: those that hold, after
    "Given", and those that do not, after "Not"."""
    lines = [f'<span class="pt-k">Given</span>{go(x, e(title_of(store, x)), "go name")}' for x in t.get("given") or []]
    lines += [f'<span class="pt-k">Not</span>{go(x, e(title_of(store, x)), "go name")}' for x in t.get("given_not") or []]
    return "".join(f'<span class="pt-c">{x}</span>' for x in lines)


def _rested_on(reader: Reader, rng: dict) -> list[str]:
    """The propositions whose markets an end of a range rests on (MATH.md 1.2): each taken book's proposition, a market
    standing as its own, and each proposition held at its market's probability."""
    out = []
    for tag in (*(rng.get("lo_rests") or ()), *(rng.get("hi_rests") or ())):
        base = tag.removesuffix(":book").removesuffix(":price")
        if base == tag:
            continue
        if schema.MARKET_ID_RE.match(base):
            out.append(base)
        elif (link := reader.store.link_by_id(base)) is not None:
            out.append(link["from"])
    return list(dict.fromkeys(out))


def _probability_drawing(reader: Reader, r: dict) -> str:
    """Where the forecast comes from, on one scale (MATH.md 1.2): each market it rests on, by the proposition it asks, at
    its own bid, ask and price, the price beside it: the event's own, those of the propositions its conditional
    probabilities are given, and those its market-implied range rests on; the forecast with that range; and, beneath,
    each conditional probability the analyst states, beside its whole condition. Every position is a figure of the
    store's, and its calculation opens from the forecast."""
    store, pid = reader.store, r["id"]
    p = r.get("probability")
    try:
        st = _structure_of(reader, pid)
    except Exception:  # a structure not yet read: the drawing waits with the rest of the row
        st = {}
    props = st.get("props") or {}
    me = props.get(pid) or {}
    # each conditional probability the analyst states: its term where the forecast is worked out, which carries its
    # value now, and otherwise the chance as stated, where the statements leave the forecast open
    terms = [t for t in me.get("terms") or [] if t.get("chance")] or [
        {"given": c.get("given") or [], "given_not": c.get("given_not") or [], "share": c["value"], "chance": c["key"]}
        for c in me.get("chances") or [] if (c.get("given") or c.get("given_not")) and c.get("value") is not None]
    rng = me.get("range") or {}
    lo, hi = rng.get("lo"), rng.get("hi")
    bounded = lo is not None and hi is not None and (lo > ZERO or hi < 1 - ZERO)
    givens = list(dict.fromkeys(g for t in terms for g in (*t["given"], *t["given_not"])))
    rested = [q for q in _rested_on(reader, rng) if q != pid and q not in givens]
    lanes = sorted((q for q in [*givens, *rested] if props.get(q, {}).get("markets")),
                   key=lambda q: ((props[q].get("p") is None), props[q].get("p") or 0.0))
    own = me.get("markets") or []
    left_open = r.get("left_open") if p is None else None
    places = [x for x in (p, *((lo, hi) if bounded else ()),
                          *((left_open["lo"], left_open["hi"]) if left_open else ())) if x is not None]
    for q in [*lanes, pid]:
        for m in (props.get(q) or {}).get("markets") or []:
            places += [x for x in (m.get("bid"), m.get("ask"), m.get("price")) if x is not None]
    if not places:
        return ""
    dom = _pt_domain(places)

    def book(m: dict, label: str, opens: str) -> str:
        mid = m["market"]
        venue = VENUES.get(split(mid)[0], split(mid)[0])
        depth = amount(m.get("depth"), sign=False) if m.get("depth") is not None else ""
        bid = m["bid"] if m.get("bid") is not None else 0.0
        ask = m["ask"] if m.get("ask") is not None else 1.0
        held = m.get("taken", True) and not m.get("followed")
        said = quotes(m.get("bid"), m.get("ask"), side_of(store, m.get("link")))  # the market's own quote
        plot = (f'<a class="pt-ba{"" if held else " pt-not"}" data-go="{e("x:price:" + mid)}" title="{e(said)}" '
                f'style="left:{_pt_x(bid, dom)};width:calc({_pt_x(ask, dom)} - {_pt_x(bid, dom)})"></a>')
        if m.get("price") is not None:
            plot += (f'<a class="pt-dot{"" if held else " pt-not"}" data-go="{e("x:price:" + mid)}" title="{e(said)}" '
                     f'style="left:{_pt_x(m["price"], dom)}"></a>'
                     + _pt_label(m["price"], dom, '<span class="pt-w">Midpoint</span> ' + gonum("x:price:" + mid, chance(m["price"]))))
        how = " · Followed" if m.get("followed") else "" if held else " · Price not used"
        sub = (f'{e(venue)} \u00b7 {quotes(m.get("bid"), m.get("ask"), side_of(store, m.get("link")), html=True)}'
               f'{f" · Depth <span class=n>{e(depth)}</span>" if depth else ""}{how}')
        return (f'<div class="pt-row"><div class="pt-lab">{go(opens, e(label), "go name")}<span class="pt-sub">{sub}'
                f'</span></div><div class="pt-plot">{plot}</div></div>')

    rows = []
    for q in lanes:
        label = (market_question(reader, q) if schema.MARKET_ID_RE.match(q) else None) or title_of(store, q)
        rows += [book(m, label, q) for m in props[q]["markets"]]
    rows += [book(m, r["title"], pid) for m in own]
    # the forecast, with the range the markets imply
    plot = ""
    if bounded:
        plot += (f'<a class="pt-band" data-go="{e("x:interval:" + pid)}" title="Market range {e(_implied(r))}" '
                 f'style="left:{_pt_x(lo, dom)};width:calc({_pt_x(hi, dom)} - {_pt_x(lo, dom)})"></a>')
    if p is not None:
        plot += (f'<a class="pt-dot pt-fc" data-go="{e("x:p:" + pid)}" style="left:{_pt_x(p, dom)}"></a>'
                 + _pt_label(p, dom, gonum("x:p:" + pid, chance(p)), "pt-fcv"))
    elif left_open and _span(left_open):
        plot += (f'<a class="pt-open" data-go="{e("x:p:" + pid)}" style="left:{_pt_x(left_open["lo"], dom)};'
                 f'width:calc({_pt_x(left_open["hi"], dom)} - {_pt_x(left_open["lo"], dom)})"></a>'
                 + _pt_label(left_open["hi"], dom, gonum("x:p:" + pid, _span(left_open)), "pt-fcv"))
    direct = (r.get("point") or {}).get("rule") == "chance"
    rows.append(f'<div class="pt-row pt-fore"><div class="pt-lab"><span class="pt-name">Forecast</span>'
                f'<span class="pt-sub">{"Market range " + f"<span class=nw>{e(_implied(r))}</span>" if bounded else "Analyst estimate" if direct else ""}</span></div>'
                f'<div class="pt-plot">{plot}</div></div>')
    step = 0.25 if dom == (0.0, 1.0) else 0.05
    ticks = [dom[0] + i * step for i in range(int(round((dom[1] - dom[0]) / step)) + 1)]
    axis = "".join(f'<span class="pt-ax{" pt-ax-l" if i == 0 else " pt-ax-r" if i == len(ticks) - 1 else ""}" '
                   f'style="left:{_pt_x(v, dom)}">{round(v * 100):d}%</span>' for i, v in enumerate(ticks))
    lines = "".join(f'<i style="left:{_pt_x(v, dom)}"></i>' for v in ticks)
    drawing = (f'<div class="pt" data-k="mv"><div class="pt-row pt-head"><div class="pt-lab"></div>'
               f'<div class="pt-plot">{axis}</div></div><div class="pt-body"><div class="pt-grid">{lines}</div>'
               f'{"".join(rows)}</div></div>')
    # each conditional probability the analyst states, beside its whole condition
    inputs = []
    for t in terms:
        if not t.get("chance"):
            continue
        inputs.append(f'<div class="pt-in"><div class="pt-lab"><span class="pt-name pt-cs">{_given_words(store, t)}</span>'
                      f'</div><span class="pt-fig">{gonum("x:p:" + pid, chance(t.get("share")))}</span></div>')
    return drawing + (f'<div class="pt-inputs" data-k="inputs"><h3 class="sub">Chances</h3>{"".join(inputs)}</div>'
                      if inputs else "")


def _market_section(reader: Reader, r: dict) -> str:
    """The event's probability, where it comes from: the drawing; a chance that follows a market; the
    probability's history; the markets read and not used; and the searches."""
    pid = r["id"]
    point = r.get("point") or {}
    basis = ""
    if point.get("rule") == "chance":  # the analyst's input, stated on its own: its basis under it
        own = next((c for c in point.get("chances") or [] if not c["given"] and not c["given_not"]), None)
        basis = _stated_basis(reader, _stated_chance(reader.store, own["key"]), pid, below=r["below"]) if own else ""
    store = reader.store
    order = _reached(store, pid)
    faint = _faint(store, order)
    unused = "".join(f'<li>{go(mid, e(market_question(reader, mid) or mid), "go name")}'
                     f'<span class="pt-sub">{e(VENUES.get(split(mid)[0], split(mid)[0]))}</span>'
                     f'{go(note, "Rationale", "go why")}</li>' for q in order for mid, note in faint.get(q, []))
    markets = any(store.links_of(q, "asks") for q in order) or any(schema.MARKET_ID_RE.match(q) for q in order)
    words = "" if markets else '<p class="none" data-k="none">No market bears on it.</p>'
    return (_probability_drawing(reader, r) + words + (f'<div class="how" data-k="how">{basis}</div>' if basis else "")
            + _following(reader, point) + _history(reader, r)
            + (f'<div class="pt-unused" data-k="unused"><span class="glab">Not used</span><ul>{unused}</ul></div>'
               if unused else "")
            + _searches(r, open_=not markets))


def _following(reader: Reader, point: dict) -> str:
    """A forecast that follows a market, or the weight a holding's price implies (MATH.md 1.2): the forecast and what it
    follows, as stated and now, and how far it follows."""
    parts = []
    for chance_ in point.get("chances") or []:
        follow = chance_.get("follow") or {}
        target = follow.get("name") or follow.get("market")
        if not target:
            continue
        name = name_of(reader.store, target) if follow.get("name") else market_question(reader, target) or target
        then = day(follow.get("stated_at") or follow.get("then_at"))
        now = day(follow.get("now_at"))
        market = "Implied by its price" if follow.get("name") else "Market midpoint"
        form = chance  # a weight a holding's price implies, or a market's midpoint: the probability beside the forecast
        table = (f'<table class="cmp"><thead><tr><th></th><th class="n">Stated, {e(then)}</th><th class="n">Now, {e(now)}'
                 f'</th></tr></thead><tbody>'
                 f'<tr><th>Forecast</th><td class="n">{num(chance(chance_.get("stated")))}</td>'
                 f'<td class="n">{num(chance(chance_.get("value")))}</td></tr>'
                 f'<tr><th>{e(market)}</th><td class="n">{num(form(follow.get("q_then")))}</td>'
                 f'<td class="n">{num(form(follow.get("q_now")))}</td></tr></tbody></table>'
                 f'<p class="aside">Responsiveness {num(format(follow["responsiveness"], "g"))}</p>')
        parts.append(f'<h3 class="sub">Following {go(target, e(name))}</h3>{table}'
                     + (f'<p class="aside">{e(follow["why"])}</p>' if follow.get("why") else ""))
    return "".join(parts)


def _range(r: dict) -> tuple:
    """A row's range, what the markets it takes imply: (lo, hi), each None where it has none."""
    return tuple(r.get("range") or (None, None))


def _world_label(store: Store, w: str) -> str:
    """A world as the page names it: an event's side, Yes or No, or a way by its title."""
    return {"yes": "Yes", "no": "No"}.get(w) or title_of(store, w)


# ---------------------------------------------------------------- an event's outcomes: the picture and the grid


def _worlds_of(r: dict) -> list[dict]:
    """The event's worlds, the outcomes left whole, in their tree's order."""
    return [o for o in r.get("outcomes") or [] if o.get("world") and o.get("A") is not None]


def _now(reader: Reader, pid: str) -> dict:
    """What the event now does in every outcome (ledger.event_now, TOOLS.md 5): placed against today; or, while its
    statements leave its outcomes' probabilities open, what each does against the baseline, with no levels; or nothing
    where it values nothing yet."""
    try:
        got = event_now(reader.store, pid)
    except (StoreError, Unavailable, Waiting, KeyError, RuntimeError):
        return {}
    return got if got.get("factors") is not None else {}


def _set_by(now: dict, target: str) -> dict:
    """How each world sets a target's price (MATH.md 1.3): by a shock of its own or its parent's, each with the consequence
    that states it, or moving with the others."""
    for kind in ("factors", "names"):
        for item in now.get(kind) or []:
            if item.get("factor", item.get("name")) == target:
                return item["worlds"]
    return {}


def _tone(x: float | None) -> str:
    shown = x is not None and money(x) != "$0.00"  # an amount that rounds to nothing is neither
    return "loss" if shown and x < 0 else "gain" if shown and x > 0 else ""


def _shown_below(store: Store, r: dict) -> list[str]:
    """The notes an opened event shows once, in its notes: those about the event, and those about more than one thing
    it shows. Every other note shows in place, where it bears."""
    shown = {r["id"], *(c["link"] for c in r["consequences"]), *r["decomposition"], *(m["market"] for m in r["markets"])}
    notes = {n["from"] for x in shown for n in store.links_of(x, "about", "in")}
    out = [n for n in notes if r["id"] in (about := {a["to"] for a in store.links_of(n, "about")}) or
           len(about & shown) > 1]
    return sorted(out, key=lambda n: int(n.split(":")[1]))


def _when_known(stated: dict, asof: str | None = None) -> str:
    """When an event can happen and where it would be known from, as labelled facts."""
    return f'<div class="when">{_dl([(k, e(v)) for k, v in _when_pairs(stated, asof)])}</div>'


def _when_pairs(stated: dict, asof: str | None = None) -> list[tuple[str, str]]:
    """When an event can happen: at any time to the end of its window, or on the date it is decided, the level
    then or the decision; and where it would be known from, in the analyst's words."""
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


HISTORY_MARKETS = 3  # the markets drawn beside a probability's history, the deepest of those that inform it
HISTORY_DAYS = 30  # how far back a market's daily history is drawn before the first reply


def _instant(iso: str) -> datetime:
    """A moment, comparable whatever its offset or precision."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _informing(reader: Reader, r: dict) -> list[dict]:
    """The markets that inform an event's probability, to stand beside its history: those that ask it, or, where none
    does, those that ask the propositions its chances are given; each with its price through the side it asks, the
    deepest first."""
    point = r.get("point") or {}
    found = [m for m in point.get("markets") or [] if m.get("price") is not None]
    if not found:
        given = dict.fromkeys(x for t in point.get("terms") or [] for x in (*t["given"], *t["given_not"]))
        found = [m for q in given for m in _reading_of(reader, q).get("markets") or [] if m.get("price") is not None]
    return sorted(found, key=lambda m: -(m.get("depth") or 0.0))[:HISTORY_MARKETS]


def _history(reader: Reader, r: dict) -> str:
    """How the probability got here: as the system gave it at each reply's end, as the record keeps it (2),
    and now, each standing until the next, under the definition it has now, since a question restated is another
    question; each outcome's the same way, drawn when one is selected; and beside it the markets that inform it, each
    over the month before the first reply from its daily closes, and now. Every figure is the record's or the
    store's; the page draws them."""
    store, pid = reader.store, r["id"]
    now_def = {k: v for k in DEFINITION if (v := r.get(k)) is not None}
    replies: list[tuple[str, dict]] = []
    for rec in store.records():
        written = next((o.get("stated") or {} for o in (rec["written"].get("object") or []) if o.get("id") == pid), None)
        got = next((x for x in rec["rows"] or [] if x.get("id") == pid), None)
        src = written if written is not None else got
        if src is not None and {k: v for k in DEFINITION if (v := src.get(k)) is not None} != now_def:
            replies = []  # what it said before it was restated answered another question
            continue
        if got is None:
            continue
        replies.append((rec["created_at"], got))
    basis = (r.get("basis") or {}).get("probability")
    at = reading_time(store, basis) if basis and r["probability"] is not None else None
    if at is None and replies:
        at = replies[-1][0]
    later = at is not None and (not replies or _instant(at) > _instant(replies[-1][0]))  # the store has read since
    ours = [[t, x["probability"]] for t, x in replies if x.get("probability") is not None]
    if later and r["probability"] is not None:
        ours.append([at, r["probability"]])
    if not ours:
        return ""
    worlds: dict[str, dict] = {}
    for t, x in [*replies, *([(at, r)] if later else [])]:
        for o in x.get("outcomes") or []:
            if o.get("p") is not None:
                got = worlds.setdefault(o["id"], {"title": o.get("title") or o["id"], "points": []})
                got["points"].append([t, o["p"]])
    start = ours[0][0][:10]
    markets = []
    for m in _informing(reader, r):
        complement = ((store.link_by_id(m["link"]) or {}).get("stated") or {}).get("side") == "no"
        since = (date.fromisoformat(start) - timedelta(days=HISTORY_DAYS)).isoformat()
        daily = (reader.output("market_history", m["market"]) or {}).get("history") or {}
        points = [[d, 1.0 - v if complement else v] for d, v in sorted(daily.items()) if since <= d < start]
        points += [[t, x["market"]["probability"]] for t, x in replies if (x.get("market") or {}).get("market") ==
                   m["market"] and x["market"].get("probability") is not None]
        points.append([at or ours[-1][0], m["price"]])
        venue = VENUES.get(split(m["market"])[0], split(m["market"])[0])
        asks = (store.link_by_id(m["link"]) or {}).get("from")  # named as the drawing above names it: what it asks
        name = venue if asks in (None, pid) else f"{title_of(store, asks)} · {venue}"
        markets.append({"label": name, "short": venue, "own": asks in (None, pid), "points": points})
    markets = [m for m in markets if len(m["points"]) > 1]  # a market with one reading has no line to draw

    def moved(points: list) -> bool:
        return len({round(v, 4) for _, v in points if v is not None}) > 1

    if not moved(ours) and not any(moved(m["points"]) for m in markets):
        return ""  # nothing moved, ours or a market beside it: the figure above says all of it
    lo, hi = _range(r)
    spec = {"ours": ours, "outcomes": worlds, "markets": markets,
            "band": [lo, hi] if lo is not None and hi is not None and (lo > ZERO or hi < 1.0 - ZERO) else None}
    return (f'<div class="hist" data-k="history" data-history="{e(json.dumps(spec, separators=(",", ":")))}" '
            f'role="img" aria-label="Probability history"></div>')


def _worth_values(reader: Reader, r: dict) -> str:
    """The values the analyst states for a holding, under the event's assumptions: each holding as `_worth_items` gives
    it."""
    out = reader.output("impact", r["id"]) or {}
    items = _worth_items(reader, r["id"], out, r.get("outcomes") or [], r.get("below") or [], r.get("probability"))
    return section("Values stated", "".join(items), "worth", "worth") if items else ""


def _worth_items(reader: Reader, pid: str, out: dict, outcomes: list[dict], below: Iterable[str],
                 p: float | None) -> list[str]:
    """Each holding the analyst values world by world (MATH.md 1.6), from the event's impact (`out`): its value per share in
    each world, each opening the statement it was stated in; the weight today's price implies for the event, beside
    the event's probability `p`; and the basis of each value, under the worlds it is the basis of."""
    stated = out.get("worth") or {}
    titles = {o["id"]: o["title"] for o in outcomes}
    items = []
    for nid, value in stated.items():
        links = value.get("considerations") or {w: item["link"] for w, item in
                ((out.get("valuation_terms") or {}).get("worth") or {}).get(nid, {}).items()}
        rows = [(e(titles.get(wid, wid)), "", gonum(links[wid], money(v)) if links.get(wid) else num(money(v)))
                for wid, v in (value.get("levels") or {}).items()]
        implied = _implied_row(value.get("implied"), pid, p, nid)
        bases = []
        for lid in dict.fromkeys(links.values()):  # each basis under the world it is the basis of
            link = reader.store.link_by_id(lid) or {}
            on = ", ".join(titles.get(w, w) for w, x in links.items() if x == lid)
            bases.append(f'<p class="aside">{e(on)}</p>' +
                         _stated_basis(reader, (link.get("stated") or {}).get("value") or {}, lid, below=below))
        items.append(f'<div class="worth" data-k="worth:{e(nid)}"><h3 class="sub">'
                     f'{go(nid, e(name_of(reader.store, nid)))} · per share</h3>{receipt(rows)}{implied}{"".join(bases)}</div>')
    return items


def _implied_row(implied: float | None, event: str | None = None, p: float | None = None,
                 holding: str | None = None) -> str:
    """The weight a holding's price implies for the event, from the values stated for it (MATH.md 1.6), under its label,
    opening its working where the holding is named, with the event's probability beneath it where it is given: the row
    carries an expected value where the two differ. Where the values put today's price beyond them, the figure is
    outside 0 to 1 and is no weight: the values stand as stated, and nothing is shown for it."""
    if implied is None or implied < 0 or implied > 1:
        return ""
    probability = gonum(f"x:p:{event}", chance(p)) if event and p is not None else ""
    figure = gonum(f"x:implied:{event}:{holding}", chance(implied)) if event and holding else num(chance(implied))
    return ('<div class="pin-row"><span class="pin-t">Implied by today’s price</span>'
            f'<span class="pin-w"><span class="pin-v">{figure}</span>'
            + (f'<span class="pin-c">Probability {probability}</span>' if probability else "") + '</span></div>')


def _event_above(store: Store, pid: str) -> str | None:
    """The watched event a proposition is, or is a way of, through the ways above it."""
    seen = set()
    while pid and pid not in seen:
        if store.watched(pid):
            return pid
        seen.add(pid)
        up = store.links_of(pid, "way", "in")
        pid = up[0]["from"] if up else None
    return None


def _worth_view(reader: Reader, lid: str) -> dict:
    """A value the analyst states for a holding in an outcome (MATH.md 1.6), per share: what the outcome pays, in its own
    terms; its value today in each outcome; the weight today's price implies; and its basis."""
    link = reader.store.link_by_id(lid)
    value = link["stated"]["value"]
    paid = value["value"]
    name = name_of(reader.store, link["to"])
    source = link["from"]
    world = link["stated"].get("world")
    context = go(source, e(title_of(reader.store, source))) + (f' \u00b7 {world.title()}' if world else "")
    head = hero("Value stated", title=e(name), context=context)
    terms = []
    if "amount" in paid:
        terms.append(("Per share", num(e(money(paid["amount"])))))
    if "cash" in paid:
        terms.append(("Cash", num(e(level(paid["cash"], paid.get("currency", "USD")))), e(f"on {day(paid['date'])}")))
    if paid.get("shares"):
        shares = paid["shares"]
        terms.append(("Shares", f'{num(e(f"{shares['quantity']:g}"))} {go(shares["name"], e(name_of(reader.store, shares["name"])))}'))
    if "return" in paid:
        terms.append((f"Return since {day(paid['date'])}", num(e(move(paid["return"])))))
    out = reader.output("worth", lid) or {}
    body = facts(terms)
    values = [(e(_world_label(reader.store, w)), [num(e(money(v)))]) for w, v in (out.get("levels") or {}).items()]
    if values:
        body += '<h3 class="sub">Value today</h3>' + _cols(("Per share",), values)
    event = _event_above(reader.store, source)
    p_ = _value(reader.derived(event, "Proposition", ("probability",)), "probability") if event else None
    body += f'<div class="worth">{_implied_row(out.get("implied"), event, p_, link["to"])}</div>'
    body += _stated_basis(reader, value, lid)
    return {"title": f"Value of {name}", "html": head + body + full(reader, lid)}


def _priced_in(reader: Reader, r: dict) -> str:
    """What today's prices are taken to carry, where the analyst states it (MATH.md 1.5), under the event's assumptions:
    each stated weight as `_priced_items` gives it. Where nothing is stated, today's prices carry each outcome at its
    probability, a fact of every event and of none in particular."""
    items = _priced_items(reader, r["id"], r.get("outcomes") or [], r.get("priced_in") or {}, r.get("below") or [])
    return section("Priced in", "".join(items), "priced", "priced") if items else ""


def _priced_items(reader: Reader, pid: str, outcomes: list[dict], read: dict, below: Iterable[str]) -> list[str]:
    """Each weight the analyst states for what today's prices carry (MATH.md 1.5), on the event or on a way of it: the
    outcome it is stated on; the weight the store reads for it (`read`, by outcome, yes for the event), the figure as
    stated or the probability of the market it is read from at that market's latest priced reading, beside the
    outcome's probability; the market, with its book through the side it is read and the time of the reading the
    weight rests on; and the basis it was stated on. Each stands as stated, whether or not the weights hold together."""
    store = reader.store
    by = {o["id"]: o for o in outcomes}
    items = []
    for on in dict.fromkeys([pid, *(o["id"] for o in outcomes if o["id"].startswith("prop:"))]):
        stated = store.stated_of(on).get("priced_in")
        if not stated:
            continue
        key = "yes" if on == pid else on
        o, got = by.get(key) or {}, read.get(key) or {}
        mid = schema.weight_market(stated)
        q = got.get("q") if mid else got.get("q", stated.get("value"))
        weight = (gonum("x:price:" + mid, chance(q)) if mid else num(chance(q))) if q is not None else ""
        p = gonum(f"x:p:{on}", chance(o["p"])) if o.get("p") is not None else ""
        said = ""
        if mid:  # the market it is read from: its question, its book through the side it is read, and the reading's time
            book = reader.derived(got["link"], "asks") if got.get("link") else {}
            parts = [go(mid, e(title_of(store, mid)))]
            if book:
                parts.append('<span class="qt">' + quotes(_value(book, "bid"), _value(book, "ask"),
                                                          side_of(store, got.get("link")), html=True) + "</span>")
            if got.get("at"):
                parts.append(e(moment(got["at"])))
            said = f'<p class="pin-f"><span class="k">Read from</span> {" · ".join(parts)}</p>'
        title = o.get("title") or title_of(store, on)
        # the outcome, and its weight under the section's label with its probability beneath, as a row's caption
        items.append(f'<div class="priced" data-k="priced:{e(on)}"><div class="pin-row">'
                     f'<span class="pin-t">{go(f"x:outcome:{pid}:{key}", e(title))}</span>'
                     f'<span class="pin-w"><span class="pin-v">{weight}</span>'
                     + (f'<span class="pin-c">Probability {p}</span>' if p else "") + '</span></div>' + said
                     + _stated_basis(reader, stated, on, below=below) + "</div>")
    return items


def _episodes(reader: Reader, r: dict) -> str:
    """The stated past windows used to complete factor moves, each beside the basis for choosing it."""
    ids = [r["id"], *(o["id"] for o in r.get("outcomes") or [] if o["id"].startswith("prop:"))]
    pieces = []
    for pid in dict.fromkeys(ids):
        episode = reader.store.stated_of(pid).get("episode")
        if not episode:
            continue
        window = episode["value"]
        pieces.append(f'<h3 class="sub">{go(pid, e(title_of(reader.store, pid)))}</h3>'
                      f'<p>The factors move together as they did from {day(window["start"])} to {day(window["end"])}.</p>' +
                      _stated_basis(reader, episode, pid, below=r.get("below") or []))
    return section("Past episode", "".join(pieces), "episodes", "episodes") if pieces else ""


TABS = (("outcomes", "Outcomes"), ("probability", "Probability"), ("assumptions", "Assumptions"))


def _payoff_overview(reader: Reader, r: dict) -> str:
    """Each outcome's P&L were it known today, paired with a toggle to the distribution on the event's date (1.8)."""
    worlds = _worlds_of(r)
    pic = _outcome_picture(reader, r)
    if not worlds:
        return pic
    pid = r["id"]
    vals = [w["A"] for w in worlds if w.get("A") is not None] + [0.0]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span < 1.0:
        lo, hi, span = -100.0, 100.0, 200.0
    else:
        pad = span * 0.08
        lo, hi = lo - pad, hi + pad
    axis = {"lo": lo, "hi": hi}
    zero_x = _at(axis, 0.0)
    bars = []
    for w in worlds:
        a = w.get("A") or 0.0
        tone = _tone_whole(a)
        left, right = sorted((0.0, a))
        seg = (f'<i class="po-seg {tone}" style="left:{_at(axis, left)};'
               f'right:calc(100% - {_at(axis, right)})"></i>' if abs(a) >= 0.5 else "")
        tag = '<span class="otag">Baseline</span>' if w.get("baseline") else ""
        p_txt = chance(w["p"]) if w.get("p") is not None else ""
        pnl_el = gonum(f"x:outcome:{pid}:{w['id']}", amount(a), tone)
        bars.append(f'<div class="po-row" data-outcome="{e(w["id"])}">'
                    f'<div class="po-lab"><span class="po-title">{e(w["title"])}</span>{tag}'
                    f'<span class="po-p n">{p_txt}</span></div>'
                    f'<div class="po-track"><i class="po-zero" style="left:{zero_x}"></i>{seg}</div>'
                    f'<div class="po-val">{pnl_el}</div></div>')
    mode_switch = ('<div class="rank po-switch" role="group" aria-label="When">'
                   '<button type="button" data-payoff="now" aria-pressed="true">Today</button>'
                   f'<button type="button" data-payoff="dated">On {e(day(drawn_on(reader.store, pid), year=False))}</button>'
                   '</div>') if pic else ""
    now_pane = f'<div class="po-pane" data-payoff-pane="now">{"".join(bars)}</div>'
    dated = f'<div class="po-pane" data-payoff-pane="dated" hidden>{pic}</div>' if pic else ""
    return (f'<div class="ev-card po-card" data-k="payoff">'
            f'<div class="card-head"><span class="plab">P&amp;L by outcome</span>{mode_switch}</div>'
            f'{now_pane}{dated}</div>')


def _drivers_summary(reader: Reader, r: dict) -> str:
    """Compact summary of stated shocks on the primary Outcomes tab so cause and effect read together."""
    if not _worlds_of(r):
        return ""
    store, now = reader.store, _now(reader, r["id"])
    if not now:
        return ""
    chips_ = []
    items = [(f["factor"], f.get("name") or target_label(store, f["factor"]), f) for f in now.get("factors") or []] + \
            [(m["name"], name_of(store, m["name"]), m) for m in now.get("names") or []]
    for target, label_, item in items:
        entries = []
        first_open = target
        for wid, how in (item.get("worlds") or {}).items():
            if how.get("by") not in ("own", "parent", "worth"):
                continue
            there = how.get("move")
            if there is None:
                continue
            fig = factor_move(item, there)
            tone = _move_tone(there, fig, factor_move(item, 0.0))
            said = how.get("stated") or {}
            res = " res." if schema.against(said) == schema.FOLLOWS else ""
            if first_open == target:
                first_open = ("x:consequence:" + how["consequence"] if how.get("consequence")
                              else how.get("worth") or target)
            val_html = f'<span class="drv-v n {tone}">{e(fig)}{res}</span>'
            if val_html not in entries:
                entries.append(val_html)
        if not entries:
            continue
        vals = '<span class="drv-sep"> / </span>'.join(entries)
        txt = f'<span class="drv-nm">{e(heading(label_))}</span>{vals}'
        chips_.append(go(first_open, txt, "drv-chip"))
    if not chips_:
        return ""
    more = '<button type="button" class="linkish drv-more" data-switch-tab="assumptions">Assumptions →</button>'
    return (f'<div class="ev-card drv-strip" data-k="drivers">'
            f'<div class="card-head"><span class="plab">Shocks</span>{more}</div>'
            f'<div class="drv-list">{"".join(chips_)}</div></div>')


def event_view(reader: Reader, pid: str) -> dict:
    """An opened event, a working surface: its header, what it is, when and how it would be known; one strip of its
    figures, the forecast first; then by tab its outcomes with each holding's P&L there, its probability with the
    markets and inputs it rests on, and its assumptions: each price's move and level there with their bases, and what
    today's prices are taken to carry where the analyst states it, the values stated for a holding, a past episode. An
    outcome selected in one selects it in all; a figure opens its calculation beside the event."""
    store = reader.store
    r = row(store, pid)
    r["below"] = _shown_below(store, r)
    tabs = "".join(f'<button type="button" role="tab" data-tab="{k}">{v}</button>' for k, v in TABS)
    outcomes = (_payoff_overview(reader, r) + _drivers_summary(reader, r)
                + f'<div class="ev-card">{_outcome_grid(reader, r, only=("holdings",))}</div>'
                + _contributors_table(reader, r))
    assumptions = (f'<div class="ev-card">{_outcome_grid(reader, r, only=("moves", "levels"), heads=False, key="assumptions")}</div>'
                   + _priced_in(reader, r)
                   + _worth_values(reader, r)
                   + _episodes(reader, r))
    body = (_event_head(reader, r) + _ws_strip(reader, r) + f'<nav class="tabs" role="tablist">{tabs}</nav>'
            + f'<section data-pane="outcomes">{outcomes}</section>'
            + f'<section data-pane="probability">{_market_section(reader, r)}</section>'
            + f'<section data-pane="assumptions">{assumptions}</section>')
    return {"title": r["title"], "html": f'<div class="ev ws">{body}</div>', "wide": True, "surface": "event"}


def _ws_strip(reader: Reader, r: dict) -> str:
    """The event's figures in one strip, each under its label (TOOLS.md 9): every label on Row 1, every primary figure
    locked to one horizontal baseline on Row 2, and optional qualifiers on Row 3 beneath without shifting Row 2."""
    pid, yes = r["id"], r.get("yes")
    cells: list[tuple[str, str, str, str]] = []
    prob = gonum(f"x:p:{pid}", _figure_p(r)) if _figure_p(r) else WAIT if reader.reading_waits(pid) else ""
    if prob:
        how_cap = f'<a class="cap go calc" data-go="{e("x:p:" + pid)}">How it’s set →</a>' if _figure_p(r) else ""
        cells.append(("Forecast", prob, how_cap, "first"))
    lo, hi = _range(r)
    if lo is not None and hi is not None and (lo > ZERO or hi < 1.0 - ZERO) and _beside_open(r):
        said = _implied(r)
        cells.append(("Market range", gonum(f"x:interval:{pid}", said), "", ""))
    m = r.get("market") or {}
    if m.get("probability") is not None and _market_words(reader, r):
        book = reader.derived(m["link"], "asks")
        bid, ask = _value(book, "bid"), _value(book, "ask")
        q_cap = f'<span class="cap">{quotes(bid, ask, side_of(reader.store, m["link"]), html=True)}</span>'
        cells.append(("Market", gonum("x:price:" + m["market"], chance(m["probability"])), q_cap, ""))
    if yes is not None:
        full = as_shown(yes) - as_shown(r["no"])
        share_ = (f'<span class="cap"><span class="n {_move_tone(r["share_yes"], share(r["share_yes"]), share(0.0))}">'
                  f'{share(r["share_yes"])}</span> of portfolio</span>') if r.get("share_yes") is not None else ""
        cells += [("If Yes", gonum(f"x:outcome:{pid}:yes", amount(yes), _tone_whole(yes)), share_, ""),
                  ("If No", gonum(f"x:outcome:{pid}:no", amount(r["no"]), _tone_whole(r["no"])), "", ""),
                  ("Full impact", gonum(f"x:swing:{pid}", amount(full), _tone_whole(full)), "", ""),
                  ("Volatility", gonum(f"x:volatility:{pid}", either(r.get("volatility"))), "", "")]
        worst = r.get("worst_outcome") or {}
        if worst.get("world") not in (None, "yes", "no"):
            cells.append(("Worst outcome", gonum(f"x:outcome:{pid}:{worst['world']}", amount(worst["pnl"]),
                                                 _tone_whole(worst["pnl"])),
                          f'<span class="cap">{e(worst.get("title") or worst["world"])}</span>', ""))
        mu = r.get("expected")
        if mu is not None and _tone_whole(mu) != "nil":
            cells.append(("Expected value", gonum(f"x:swing:{pid}", amount(mu), _tone_whole(mu)), "", ""))
    elif reader.waits("impact", pid):
        cells.append(("If Yes", WAIT, "", ""))
    figs = "".join(f'<div class="sf{" " + c if c else ""}" data-k="fig:{e(k)}">'
                   f'<span class="lab">{e(k)}</span>'
                   f'<span class="v">{v}</span>{cap}</div>' for k, v, cap, c in cells)
    n_c = len(cells)
    return f'<div class="strip" data-count="{n_c}" style="--cols:{min(n_c, 5)}">{figs}</div>' if figs else ""


def _event_head(reader: Reader, r: dict) -> str:
    """What the event is and when: its title; when it can happen and how it would be known; its definition,
    and the analyst's note about it, each opening in place."""
    store, pid = reader.store, r["id"]
    stated = store.stated_of(pid)
    when = dict(_when_pairs(stated, store.meta("asof")))
    facts = f'<span>{e(when["When"])}</span>' + (
        f'<span class="sep">\u00b7</span><span class="src">Known from {e(when["Known from"])}</span>'
        if when.get("Known from") else "")
    opens = [f'<details class="hd" data-k="definition"><summary>Definition</summary>'
             f'<div class="hdb text">{paragraphs(r["text"])}</div></details>'] if r.get("text") else []
    notes = "".join(f'<div class="note" data-k="{e(n)}" data-note="{e(n)}">'
                    f'{note_html(store.stated_of(n).get("text"))}</div>' for n in r["below"])
    evidence = _evidence(reader, pid)
    if notes or evidence:
        opens.append(f'<details class="hd" data-k="notes"><summary>Rationale'
                     f'</summary><div class="hdb">{notes}{evidence}</div></details>')
    return (f'<div class="evh"><h2 class="title">{title_html(r["title"])}</h2>'
            f'<p class="facts">{facts}</p>' + (f'<div class="hds">{"".join(opens)}</div>' if opens else "") + '</div>')


def _evidence(reader: Reader, pid: str) -> str:
    """The pages the analyst linked to an event or its ways as evidence (ONTOLOGY.md 2.2): each by its site, opening the page
    and what rests on it, with the proposition where it is a way's, and the passages it quoted, each opening the page
    there."""
    store = reader.store
    ids = [pid, *(o for o in _ways_of_event(store, pid))]
    out = []
    for p_ in ids:
        for link in store.links_of(p_, "evidence"):
            sid = link["to"]
            src = store.source_row(sid) or {}
            on = f' \u00b7 {e(title_of(store, p_))}' if p_ != pid else ""
            quoted = "".join(f'<blockquote class="q">{go(_passage_target(sid, x), e(_as_read(x)), "go quote")}</blockquote>'
                             for x in link["stated"].get("passages") or [])
            out.append(f'<div class="basis"><p class="src">{go(sid, e(host(src.get("url"))))}{on}</p>{quoted}</div>')
    return ('<h4 class="ev-k">Evidence</h4>' + "".join(out)) if out else ""


def _ways_of_event(store: Store, pid: str) -> list[str]:
    """An event's ways at every depth, each a proposition."""
    try:
        return [o["id"] for o in outcome_tree(store, pid) or [] if str(o["id"]).startswith("prop:")]
    except (StoreError, Unavailable, Waiting, KeyError):
        return []


def _contributors_table(reader: Reader, r: dict) -> str:
    """The row's largest contributors (TOOLS.md 9), one line each: the name, where the portfolio holds it, directly and
    through each fund, and its contribution to the full impact, each opening what it names."""
    store, pid = reader.store, r["id"]
    names = r.get("contributors") or []
    if not names:
        return ""
    peak = max((abs(n["full_impact"] or 0.0) for n in names), default=0.0) or 1.0
    rows_ = []
    for n in names:
        held = (["Direct"] if n.get("held_directly") else []) + [go(f, e(name_of(store, f)), "go name")
                                                                 for f in n.get("through") or []]
        tone = _tone_whole(n["full_impact"])
        w_pct = 100 * abs(n["full_impact"] or 0.0) / peak
        bar = f'<span class="ctb-bar" aria-hidden="true"><i class="{tone}" style="width:{w_pct:.1f}%"></i></span>'
        rows_.append(f'<tr><th>{go(n["name"], e(name_of(store, n["name"])), "go name")}</th>'
                     f'<td class="held">{" · ".join(held)}</td>'
                     f'<td class="ctb-vis">{bar}</td>'
                     f'<td class="n {tone}">{gonum(f"x:swing:{pid}", amount(n["full_impact"]))}'
                     f'</td></tr>')
    return (f'<div class="ev-card ctb-card"><table class="ctb" data-k="fig:Largest contributors">'
            f'<thead><tr><th>Largest contributors</th><th>Held</th><th class="ctb-vis"></th>'
            f'<th class="n">Full impact</th></tr></thead><tbody>{"".join(rows_)}</tbody></table></div>')


def _distribution_of(reader: Reader, r: dict) -> tuple[dict, str | None, bool]:
    """The event's distribution on the date it is drawn on (MATH.md 1.8), where its working opens, and whether it is being
    computed. It is read as the page reads what is computed apart: where it is not yet computed, its computation starts
    and the read returns at once, as it was last worked out where it has been, so the opened event shows without
    waiting for its draws; the read says the value is being made (Engine.last_where_waiting), and the page reads the
    view again, the picture and the outcomes' percentile rows changing in place once it is made."""
    with reader.engine.placeholders():
        found = reader.value("distribution", r["id"])
    if found is None:  # on a reply's turn, a distribution this read could not give is being worked out
        return {}, None, bool(reader.pending and _worlds_of(r))
    if found.waiting:
        return {}, None, True
    return found.output or {}, working(reader, found.basis), False


LATER = (f'<p class="later">{WAIT}<span>Working out each outcome</span></p>')


def _outcome_picture(reader: Reader, r: dict) -> str:
    """The picture: the portfolio's P&L on the event's date (drawn_on), in today's dollars (1.8), one row for each
    outcome at its own height, so each outcome's own range reads whole however likely it is, its probability said as
    a figure beside its title; its P&L were it known today marked, and its 5th to 95th percentiles beneath; on one dollar
    axis, today at zero. A last, quiet row is all the outcomes together, each at its probability: the forecast. Each
    row is the engine's draws counted on its one set of bins; the page draws them, each scaled to its own peak."""
    dist, opens, waiting = _distribution_of(reader, r)
    if waiting and _worlds_of(r):  # never worked out: its place, saying so, until the view read again brings it
        return (f'<div class="pic" data-k="picture-sec"><div class="gridhead">'
                f'<span class="plab">At {e(day(drawn_on(reader.store, r["id"])))}</span></div>{LATER}</div>')
    return f'<div class="pic" data-k="picture-sec">{_picture_of(dist, opens, r)}</div>' if dist else ""


def _picture_of(dist: dict, opens: str | None, r: dict) -> str:
    """The picture, from the distribution as the engine gives it: each outcome's probability, its mass, its percentiles
    and its centre, the P&L were it known today that the distribution itself read, so that the picture is one working
    even where the row above it has since been worked out again. The row gives the outcomes' titles."""
    worlds = dist.get("worlds") or {}
    tree = {o["id"]: o for o in _worlds_of(r)}
    shown = [w for w in tree if w in worlds and (worlds[w].get("p") or 0) > 0]
    edges = (dist.get("bins") or {}).get("edges")
    if not shown or not edges:
        return ""
    q = lambda d, k: (d.get("quantiles") or {}).get(k)  # noqa: E731
    lows = [q(worlds[w], "0.01") for w in shown] + [q(dist, "0.01"), 0.0]
    highs = [q(worlds[w], "0.99") for w in shown] + [q(dist, "0.99"), 0.0]
    lows, highs = [x for x in lows if x is not None], [x for x in highs if x is not None]
    ticks = _round_ticks(min(lows), max(highs), 5)
    spec = {"edges": edges, "lo": ticks[0], "hi": ticks[-1], "ticks": [[t, axis_label(t)] for t in ticks],
            "rows": [{"id": w, "title": tree[w]["title"], "p": chance(worlds[w]["p"]), "base": bool(tree[w].get("baseline")),
                      "mass": worlds[w]["mass"], "centre": worlds[w].get("centre"),
                      "q05": q(worlds[w], "0.05"), "q95": q(worlds[w], "0.95")} for w in shown],
            "all": {"mass": (dist.get("bins") or {}).get("mass"), "mean": dist.get("mean"),
                    "q05": q(dist, "0.05"), "q95": q(dist, "0.95")}}
    at = day(dist.get("drawn_on"))
    data = e(json.dumps(spec, separators=(",", ":")))
    return (f'<div class="gridhead"><span class="plab">At {e(at)}</span>'
            + (go(opens, "Full details", "go quiet") if opens else "") + '</div>'
            f'<div class="dist" data-k="picture" data-dist="{data}" role="img" '
            f'aria-label="The portfolio’s P&amp;L on {e(at)} in each outcome"></div>')


def _grid_columns(r: dict) -> list[tuple[dict, list[dict]]]:
    """The grid's columns: each side with the worlds that make it up, a side left whole its own one world."""
    tree = r.get("outcomes") or []
    worlds = _worlds_of(r)
    out = []
    for side in ("yes", "no"):
        top = next((o for o in tree if o["id"] == side), None)
        if top is None:
            continue
        under = [o for o in worlds if o.get("side") == side]  # the worlds with their P&L, which a finished event has
        if under:
            out.append((top, under))
    return out


GRID_ROWS = (("moves", "Moves"), ("levels", "Scenario prices"), ("holdings", "Holding P&L"))


def _outcome_grid(reader: Reader, r: dict, only: tuple[str, ...] = ("moves", "levels", "holdings"),
                  heads: bool = True, key: str = "outcomes") -> str:
    """The outcomes: one grid whose columns are the event's worlds, a side's ways under it with its
    probability and average, the baseline marked. Its rows give each outcome's probability and its P&L, and on the
    event's date its 5th and 95th percentiles (1.8).
    Beneath them, by the reader's choice: each price's move there against the baseline, stated, then moving with the
    stated ones (1.3), each stated move opening its basis; each price's level there (1.5); or each holding's P&L there,
    the holdings adding to the outcome's P&L as shown (1.7).
    While its statements leave its outcomes' probabilities open, the event is what each outcome does against the
    baseline, which reads none (TOOLS.md 5): each outcome's probability where it has one, the portfolio's value there less
    its value in the baseline, today's prices standing at the baseline, and beneath, each price's move there and each
    holding's value there against the baseline; its P&L, its levels and the picture are placed, and follow once the
    probabilities are there."""
    pid = r["id"]
    against = {} if _worlds_of(r) else _now(reader, pid)
    if against.get("outcomes"):
        r = {**r, "outcomes": [{**o, "A": o.get("against_baseline")} for o in against["outcomes"]]}
    sides = _grid_columns(r)
    worlds = [w for _, ws in sides for w in ws]
    if not worlds:
        return ""
    two = any(len(ws) > 1 or ws[0]["id"] != top["id"] for top, ws in sides)
    oc = lambda w, extra="": f' data-outcome="{e(w["id"])}"' + (f' class="{extra}"' if extra else "")  # noqa: E731
    tag = lambda w: '<span class="otag">Baseline</span>' if w.get("baseline") else ""  # noqa: E731, above its title
    if two:
        def figures(top: dict) -> str:  # a side's probability and its P&L, where it has them
            said = [chance(top.get("p")), f'<span class="{_tone_whole(top.get("A"))}">{amount(top.get("A"))}</span>'
                    if top.get("A") is not None else ""]
            return " \u00b7 ".join(x for x in said if x)
        h1 = "".join(f'<th class="side" colspan="{len(ws)}">{e(top["title"])} <span class="n">{figures(top)}</span>'
                     '</th>' for top, ws in sides)
        whole = {top["id"] for top, ws in sides if [w["id"] for w in ws] == [top["id"]]}  # named once, above
        h2 = "".join(f'<th{oc(w, "w")}>{tag(w)}{"" if w["id"] in whole else e(w["title"])}</th>' for w in worlds)
        head = f'<tr class="h1"><th></th>{h1}</tr><tr class="h2"><th></th>{h2}</tr>'
    else:
        head = '<tr class="h2"><th></th>' + "".join(f'<th{oc(w, "w")}>{tag(w)}{e(w["title"])}</th>' for w in worlds) \
            + '</tr>'

    def cells(fn) -> str:
        return "".join(f'<td{oc(w, "n")}>{fn(w)}</td>' for w in worlds)

    def p_of(w: dict) -> str:
        target = f"x:p:{w['id']}" if w["id"].startswith("prop:") else f"x:p:{pid}"
        return gonum(target, chance(w["p"])) if w.get("p") is not None else ""

    n = len(worlds) + 1
    if against.get("outcomes"):
        opens = working(reader, against.get("basis"))
        figure = lambda w: "" if w.get("baseline") else (  # noqa: E731, every outcome is against it
            gonum(opens, amount(w["A"]), _tone_whole(w["A"])) if opens else num(amount(w["A"]), _tone_whole(w["A"])))
        rows = [f'<tr><th>Probability</th>{cells(p_of)}</tr>' if any(w.get("p") is not None for w in worlds) else "",
                f'<tr><th>Against the baseline</th>{cells(figure)}</tr>']
        spread = ""
        lowers = {"moves": _moves_rows(reader, r, worlds, n), "levels": "",
                  "holdings": _against_holdings_rows(reader, r, worlds, n)}
    else:
        dist, opens, waiting = _distribution_of(reader, r)
        rows = [f'<tr><th>Probability</th>{cells(p_of)}</tr>',
                '<tr><th>P&amp;L</th>' + cells(lambda w: gonum(f"x:outcome:{pid}:{w['id']}", amount(w["A"]),
                                                                 _tone_whole(w["A"]))) + '</tr>']
        spread = "".join(_percentile_rows(dist, opens, worlds)) if not waiting else "".join(
            f'<tr class="g2"><th>{k}, {e(day(drawn_on(reader.store, pid), year=False))}</th>'
            + "".join(f'<td data-outcome="{e(w["id"])}" class="n">{WAIT}</td>' for w in worlds) + '</tr>'
            for k in ("5th percentile", "95th percentile"))
        lowers = {"moves": _moves_rows(reader, r, worlds, n), "levels": _levels_rows(reader, r, worlds, n),
                  "holdings": _holdings_rows(reader, r, worlds, n)}
    lowers = {k: v for k, v in lowers.items() if k in only}
    if not heads:  # the outcomes' probabilities alone, as the head of rows another view reads
        rows, spread = rows[:1], ""
    lower = "".join(f'<tbody class="v-{k}">{v}</tbody>' for k, v in lowers.items() if v)
    shown = [k for k, _ in GRID_ROWS if lowers.get(k)]
    switch = ('<div class="rank" role="group" aria-label="Rows">' + "".join(
        f'<button type="button" data-rows="{k}">{v}</button>' for k, v in GRID_ROWS if k in shown) + '</div>') \
        if len(shown) > 1 else ""
    cols = "".join('<col class="wc">' for _ in worlds)
    fixed = f' data-rows="{shown[0]}" data-fixed="1"' if len(shown) == 1 else ""
    return ((f'<div class="gridhead"><span class="plab"></span>{switch}</div>' if switch else "")
            + f'<div class="ogwrap"><table class="og" data-k="{key}" style="--worlds:{len(worlds)}"{fixed}><colgroup>'
            f'<col class="lc">{cols}</colgroup><thead>{head}</thead><tbody>{"".join(rows)}</tbody>'
            f'<tbody data-k="percentiles">{spread}</tbody>{lower}</table></div>')


def _percentile_rows(dist: dict, opens: str | None, worlds: list[dict]) -> list[str]:
    """The outcomes' rows on the event's date, from the engine's distribution (MATH.md 1.8): each outcome's 5th and 95th
    percentiles, each opening the distribution's working. Its mean there is in the outcome's working, beside its P&L
    today, which it differs from only where a holding's value is not in proportion to a price."""
    at = dist.get("worlds") or {}
    if not at:
        return []
    date_ = day(dist.get("drawn_on"), year=False)
    of = lambda w: at.get(w["id"]) or {}  # noqa: E731

    def row(label: str, figure) -> str:
        def cell(x: float | None) -> str:
            if x is None:
                return ""
            return gonum(opens, amount(x), _tone_whole(x)) if opens else num(amount(x), _tone_whole(x))

        tds = "".join(f'<td data-outcome="{e(w["id"])}" class="n">{cell(figure(of(w)))}</td>' for w in worlds)
        return f'<tr class="g2"><th>{label}, {e(date_)}</th>{tds}</tr>'

    out = []
    out.append(row("5th percentile", lambda o: (o.get("quantiles") or {}).get("0.05")))
    out.append(row("95th percentile", lambda o: (o.get("quantiles") or {}).get("0.95")))
    return out


def _group_row(label: str, n: int) -> str:
    return f'<tr class="grp"><th colspan="{n}">{e(label)}</th></tr>'


def _moves_rows(reader: Reader, r: dict, worlds: list[dict], n: int) -> str:
    """Each price's move in each outcome against the baseline (MATH.md 1.3, TOOLS.md 5), in one list, the prices a shock sets
    first. Each cell says how it is set, since a price can be shocked in one outcome and move with the stated ones in
    another: a shock, a factor in its own terms, opening its basis; a move with the stated ones, quieter. A key names
    the two where both are shown. A price that moves in no outcome is not listed."""
    store, now = reader.store, _now(reader, r["id"])
    if not now:
        return ""
    base = next((w["id"] for w in worlds if w.get("baseline")), None)
    items = [(f["factor"], f.get("name") or target_label(store, f["factor"]), f) for f in now.get("factors") or []] + \
            [(m["name"], name_of(store, m["name"]), m) for m in now.get("names") or []]
    stated = [it for it in items if any(v.get("by") in ("own", "parent", "worth") for v in it[2]["worlds"].values())]
    implied = [it for it in items if it not in stated and
               any(factor_move(it[2], v.get("move")) not in (move(0.0), factor_move(it[2], 0.0))
                   for v in it[2]["worlds"].values())]
    out = ""
    kinds = set()
    for group, members in (("st", stated), ("im", implied)):
        for target, label_, item in members:
            proxy = (store.stated_of(target) or {}).get("proxy") if store.type_of(target) == "Factor" else None
            head = go(target, e(heading(label_)), "go name") + (f'<span class="px">{e(proxy)}</span>' if proxy else "")
            tds = []
            for w in worlds:
                how = item["worlds"].get(w["id"]) or {}  # a world with no entry is one it does not move in
                there = how.get("move", 0.0)
                figure = factor_move(item, there)
                tone = _move_tone(there, figure, factor_move(item, 0.0))  # the colour of its direction
                own = how.get("by") in ("own", "parent", "worth")
                if w["id"] == base:  # every move is against it: it has none of its own to show
                    tds.append(f'<td data-outcome="{e(w["id"])}" class="n base"></td>')
                elif own and (how.get("consequence") or how.get("worth")):  # it opens what states it, with its basis
                    opens = "x:consequence:" + how["consequence"] if how.get("consequence") else how["worth"]
                    said = how.get("stated") or {}
                    residual = (f'<span class="st-as">residual {e(factor_move(item, said["value"]))}</span>'
                                if schema.against(said) == schema.FOLLOWS and said.get("value") is not None else "")
                    tds.append(f'<td data-outcome="{e(w["id"])}" class="n st">{gonum(opens, figure, tone)}{residual}</td>')
                    kinds.add("st")
                else:
                    tds.append(f'<td data-outcome="{e(w["id"])}" class="n {"st" if own else "im"}">'
                               f'{num(e(figure), tone)}</td>')
                    kinds.add("st" if own else "im")
            out += f'<tr class="{group}"><th>{head}</th>{"".join(tds)}</tr>'
    if kinds == {"st", "im"}:  # each cell's form, named once
        out += (f'<tr class="key"><td colspan="{n}"><span class="k-st">Shock</span><span class="k-im">'
                f'{e(heading(MOVES_WITH))}</span></td></tr>')
    return out


def _levels_rows(reader: Reader, r: dict, worlds: list[dict], n: int) -> str:
    """Each price a shock sets, its level today beneath its name and its level in each outcome (MATH.md 1.5, TOOLS.md 5): a factor
    quoted as a yield by its yield, and each metric that quotes a factor in its own unit. Each opens how it is
    placed."""
    store, now = reader.store, _now(reader, r["id"])
    levels = (now or {}).get("levels") or {}
    if not levels:
        return ""
    any_link = {c["target"]: c["link"] for c in r["consequences"]}
    out = ""  # named by the grid's switch
    for target, got in levels.items():
        by = _set_by(now, target)
        unit = unit_of(store, price_metric(store, target))
        series = [(got.get("yields"), got.get("yield"), "%", heading(target_label(store, target)) + ", its yield")] \
            if got.get("yields") else [(got.get("worlds"), got.get("today"), unit, heading(target_label(store, target)))]
        series += [(q["worlds"], q.get("today"), unit_of(store, mid), heading(metric_label(store, mid)))
                   for mid, q in (got.get("quoted") or {}).items()]
        for there, today, u, name in series:
            if not there:
                continue
            tds = []
            for w in worlds:
                how = by.get(w["id"]) or {}
                link = how.get("consequence") or any_link.get(target)
                x = there.get(w["id"])
                figure = gonum(f"x:consequence:{link}", level(x, u)) if link and x is not None else e(level(x, u))
                tds.append(f'<td data-outcome="{e(w["id"])}" class="n">{figure}</td>')
            out += (f'<tr><th>{go(target, e(name), "go name")}<span class="sub">Today {e(level(today, u))}</span></th>'
                    f'{"".join(tds)}</tr>')
    return out


def _against_holdings_rows(reader: Reader, r: dict, worlds: list[dict], n: int) -> str:
    """Each holding's value in each outcome less its value in the baseline (TOOLS.md 5), in whole dollars, apportioned in
    each outcome so the holdings add to the portfolio's as shown; largest first, each opening the working; the
    baseline's column blank, every figure being against it. A holding the event moves in no outcome, as shown, is not
    listed."""
    found = reader.value("against_baseline", r["id"])
    hs = sorted(((found.output if found else None) or {}).get("by_holding") or [],
                key=lambda h: -max((abs(v) for v in h["against_baseline"].values()), default=0.0))
    if not hs:
        return ""
    opens = working(reader, found.basis)
    shown = {w["id"]: whole_parts([h["against_baseline"].get(w["id"], 0.0) for h in hs], w["A"] or 0.0)
             for w in worlds}
    out = _group_row("Each holding against the baseline", n)
    for k, h in enumerate(hs):
        vals = [shown[w["id"]][k] for w in worlds]
        if all(_tone_whole(v) == "nil" for v in vals):
            continue
        tds = "".join(f'<td data-outcome="{e(w["id"])}" class="n">'
                      + ("" if w.get("baseline") else gonum(opens, amount(v), _tone_whole(v)) if opens else
                         num(amount(v), _tone_whole(v))) + '</td>' for w, v in zip(worlds, vals))
        out += f'<tr><th>{go(h.get("name") or h["holding"], e(_holding_label(reader, h)), "go name")}</th>{tds}</tr>'
    return out


def _holdings_rows(reader: Reader, r: dict, worlds: list[dict], n: int) -> str:
    """Each holding's P&L in each outcome (MATH.md 1.7), in whole dollars, apportioned in each outcome so the holdings add
    to its P&L as shown; largest first; each opening its working, with the names it reaches through a
    fund. A holding the event moves in no outcome, as shown, is not listed."""
    pid = r["id"]
    found = (reader.computation((r.get("basis") or {}).get("by_holding")).get("output") or {}).get("by_holding")
    hs = sorted(found or r.get("by_holding") or [], key=lambda h: -abs(h.get("full_impact") or 0.0))
    if not hs:
        return ""
    shown = {w["id"]: whole_parts([(h.get("worlds") or {}).get(w["id"], 0.0) for h in hs], w["A"]) for w in worlds}
    out = _group_row("Holding P&L", n)
    for k, h in enumerate(hs):
        vals = [shown[w["id"]][k] for w in worlds]
        if all(_tone_whole(v) == "nil" for v in vals):
            continue
        target = f"x:holding:{pid}:{h['holding']}"
        tds = "".join(f'<td data-outcome="{e(w["id"])}" class="n">{gonum(target, amount(v), _tone_whole(v))}</td>'
                      for w, v in zip(worlds, vals))
        out += f'<tr><th>{go(h["name"], e(_holding_label(reader, h)), "go name")}</th>{tds}</tr>'
    return out


# ---------------------------------------------------------------- the inspector


def _dl(pairs: list[tuple[str, str]]) -> str:
    return "<dl class=fields>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs if v) + "</dl>"


def _neg(text: str, x: float | None) -> str:
    """A figure in parentheses where it is negative, so that what is subtracted reads plainly."""
    return f"({text})" if x is not None and x < 0 else text


def _inspect_point(reader: Reader, pid: str) -> dict:
    """A forecast's explanation: clean 1-decimal percentages, visual interpolation or contribution breakdown,
    direct market book where priced by a market, or direct supporting evidence where estimated by the analyst."""
    store = reader.store
    d = reader.derived(pid, "Proposition", READING)
    p = _value(d, "probability")
    cid = _basis(d, "probability") or _basis(d, "left_open")
    title = title_of(store, pid)
    out = _reading_of(reader, pid)
    stretched = _stretched_on(reader, pid)
    if p is None:
        o = _value(d, "left_open")
        head = hero("Forecast", figure=e(_span(o)), context=go(pid, e(title)))
        body = _left_ends(reader, out.get("open") or o) if _span(o) else WAIT if reader.reading_waits(pid) else ""
        return {"title": f"Forecast: {title}", "html": head + body + _following(reader, out) + full(reader, cid or _basis(d, "range"))}
    rule = out.get("rule")
    pills = [pill("Analyst estimate")] if rule == "chance" else [pill("Market midpoint")] if rule == "market" else []
    head = hero("Forecast", figure=e(chance(p)), context=go(pid, e(title)), pills=pills)
    lo, hi = (_value(d, "range") or [None, None])[:2]
    direct = rule in ("market", "read") and out.get("target")
    body = [_scale(pid, p, lo, hi)]
    if direct:
        lid = out["target"]["link"]
        mid = (store.link_by_id(lid) or {}).get("to")
        extra = " \u00b7 ".join(x for x in ("Read with related markets" if rule == "read" else "",
                                            _stretch_words(stretched.get(lid)).strip(" ,")) if x)
        body.append('<h3 class="sub">Market</h3>' + _market_card(reader, mid, lid=lid, extra=extra))
        others = [m for m in out.get("markets") or [] if m["link"] != lid and m.get("taken")]
        if others:
            body.append('<h3 class="sub">Other markets</h3>' + "".join(
                _market_card(reader, m["market"], lid=m["link"], extra=_stretch_words(stretched.get(m["link"])).strip(" ,"))
                for m in others))
    elif rule == "chance":
        own = next((c for c in out.get("chances") or [] if not c["given"] and not c["given_not"]), None)
        if own:
            basis = _stated_basis(reader, _stated_chance(store, own["key"]), pid)
            if basis:
                body.append(f'<h3 class="sub">Rationale</h3>{basis}')
    elif rule == "derived" and out.get("terms"):
        body.append(_derivation(reader, pid, out, p))
    elif rule == "derived":
        # Event forecast formed as the sum of its mutually exclusive Yes-side ways
        ways_links = [l for l in store.links_of(pid, "way") if (l.get("stated") or {}).get("side", "yes") == "yes"]
        way_items = []
        for l in ways_links:
            wid = l["to"]
            wp = _value(reader.derived(wid, "Proposition", ("probability",)), "probability")
            if wp is not None:
                way_items.append((wid, title_of(store, wid), wp))
        if not way_items and out.get("chances"):
            body.append(_jointly(reader, pid, out))
        if way_items:
            pts = apportion([1000 * wp for _, _, wp in way_items], round(1000 * p))
            trows = []
            for idx, ((wid, wtitle, _), pt) in enumerate(zip(way_items, pts)):
                contrib = f"+{_tenth(pt)}" if idx > 0 and pt > 0 else _tenth(pt)
                trows.append(f'<tr><td><div class="fc-case">{go(wid, e(wtitle), "go name")}</div></td>'
                             f'<td class="fc-val n">{gonum("x:p:" + wid, contrib)}</td></tr>')
            trows.append(f'<tr class="tot"><td>Forecast</td><td class="fc-val n">{num(chance(p))}</td></tr>')
            body.append(f'<table class="fc-table"><thead><tr><th>Way</th>'
                        f'<th class="r">Probability</th></tr></thead><tbody>{"".join(trows)}</tbody></table>')
    if rule not in ("market", "read") and stretched:
        held = [("", go(m["market"], e(market_question(reader, m["market"]) or m["market"]))
                 + _stretch_words(stretched.get(m["link"])), num(_asks_sides(reader, m["link"])))
                for m in out.get("markets") or [] if m["link"] in stretched]
        if held:
            body.append(f'<h3 class="sub">Markets that ask it</h3>{receipt(held)}')
    return {"title": f"Forecast: {title}", "html": head + "".join(body) + _following(reader, out) + full(reader, cid)}


def _left_ends(reader: Reader, o: dict) -> str:
    """The range a proposition's statements leave its probability (MATH.md 1.2), in clean 1-decimal percentages."""
    out = facts([("Lower bound", num(chance(o["lo"]))), ("Upper bound", num(chance(o["hi"])))])
    for side, words in (("lo", "What sets the lower bound"), ("hi", "What sets the upper bound")):
        chain = _rests(reader, o.get(f"{side}_rests") or [], side)
        if chain:
            out += f'<h3 class="sub">{words}</h3><ol class="chain">' + "".join(f"<li>{x}</li>" for x in chain) + "</ol>"
    return out


def _link_sentence(reader: Reader, tag: str, side: str, stretched: dict | None = None) -> str:
    """One statement an end of a range rests on: a taken market's bid or ask, with how far it was stretched where the
    range is read within it stretched (MATH.md 1.2), or a link between two propositions."""
    store = reader.store
    lid = tag.removesuffix(":book")
    link = store.link_by_id(lid)
    if link is None:
        return ""
    rel, a, b = link["relation"], link["from"], link["to"]
    A, B = go(a, e(quote(title_of(store, a)))), go(b, e(quote(title_of(store, b))))
    if rel == "asks":
        quoted = reader.derived(lid, "asks")
        q = go(b, e(quote(market_question(reader, b) or b)))
        said = (link["stated"].get("side") or "yes").upper()
        by = (stretched or {}).get("below" if side == "lo" else "above") or 0.0
        given = f', stretched {"below" if side == "lo" else "above"} it by {num(chance(by))}' if by > 0 else ""
        end = "bid" if side == "lo" else "ask"
        return f'{said} {end} on {q}, for {A}: {gonum("x:price:" + b, chance(_value(quoted, end)))}{given}'
    if rel == "implies":
        return f"{A} implies {B}"
    if rel == "and":
        return f"{A} requires {B}"
    if rel == "or":
        return f"{B} is enough for {A}"
    if rel == "then":
        return f"{A} requires {B}, step {link['stated'].get('step')}"
    if rel == "way":
        return f"{B} is one way {A} can turn out"
    return ""


def _inspect_interval(reader: Reader, pid: str) -> dict:
    """The range markets imply: its floor and its ceiling, then what each rests on."""
    store = reader.store
    d = reader.derived(pid, "Proposition", ("range",))
    lo, hi, cid = (_value(d, "range") or [None, None])[0], (_value(d, "range") or [None, None])[1], _basis(d, "range")
    node = _reading_of(reader, pid).get("range") or {}
    title = title_of(store, pid)
    shown = f"{chance(lo)}–{chance(hi)}" if lo is not None and hi is not None else ""
    head = hero("Market range", figure=e(shown), context=go(pid, e(title)))
    if lo is None or hi is None:
        return {"title": f"Market range: {title}", "html": head + full(reader, cid)}
    stretched = _structure_of(reader, pid).get("implied_stretched") or {}
    chains = {side: _rests(reader, node.get(f"{side}_rests") or [], side, stretched) for side in ("lo", "hi")}
    p = _value(reader.derived(pid, "Proposition", ("probability",)), "probability")
    body = [_scale(pid, p, lo, hi, None)]
    for side, words in (("lo", "What sets the lower bound"), ("hi", "What sets the upper bound")):
        if chains[side]:
            body.append(f'<h3 class="sub">{words}</h3><ol class="chain">'
                        + "".join(f"<li>{x}</li>" for x in chains[side]) + "</ol>")
    return {"title": f"Market range: {title}", "html": head + "".join(body) + full(reader, cid)}


def _book_table(book: dict) -> str:
    """A market's YES book as a ladder: the best five bids and asks side by side, best first, in cents, each with a
    bar for how many contracts stand at it."""
    bids, asks = (book.get("bids") or [])[:5], (book.get("asks") or [])[:5]
    n = max(len(bids), len(asks))
    if not n:
        return ""
    top = max([lv[1] for lv in bids + asks] or [1]) or 1

    def side(lv, kind: str) -> str:
        if not lv:
            return "<td></td><td></td>"
        size = f'<td class="sz {kind}" style="--w:{100 * lv[1] / top:.1f}%">{lv[1]:,.0f}</td>'
        price = f'<td class="px {kind}">{cents(lv[0])}</td>'
        return size + price if kind == "bid" else price + size

    rows = "".join(f'<tr>{side(bids[i] if i < len(bids) else None, "bid")}'
                   f'{side(asks[i] if i < len(asks) else None, "ask")}</tr>' for i in range(n))
    return ('<table class="ladder"><thead><tr><th>Contracts</th><th>Bid</th><th>Ask</th><th>Contracts</th></tr>'
            f'</thead><tbody>{rows}</tbody></table>')


def _inspect_price(reader: Reader, mid: str) -> dict:
    """A market's price (MATH.md 3.3): the midpoint of its best bid and ask, beside them; its depth and its last trade; at
    a past as-of, when its quotes were read; then its book's best levels."""
    v = reader.value("market_price", mid)
    out = (v.output if v else None) or {}
    q = market_question(reader, mid) or mid
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    price, bid, ask = out.get("price"), out.get("bid"), out.get("ask")
    status = pill("Open", "teal") if out.get("open") else pill("Closed") if out.get("open") is False else ""
    head = hero("Midpoint" if price is not None else "Market", figure=e(cents(price)) if price is not None else "",
                context=go(mid, e(q)), pills=[pill(venue), status])
    trade = out.get("last_trade") or {}
    body = [facts([("Bid", num(e(cents(bid))) if bid is not None else "none"),
                   ("Ask", num(e(cents(ask))) if ask is not None else "none"),
                   ("Depth", num(e(amount(out["depth"], sign=False))) if out.get("depth") is not None else ""),
                   ("Last trade", num(e(cents(trade["price"]))) if trade.get("price") is not None else "",
                    e(moment(trade.get("time"))) if trade.get("time") else ""),
                   ("Quoted", e(moment(out["quoted_at"])) if out.get("past") and out.get("quoted_at") else ""),
                   ("History", e(day(out.get("price_date"))) if price is not None and bid is None and ask is None
                    else "")])]
    if out.get("book"):
        body.append(f'<h3 class="sub">Book</h3>{_book_table(out["book"])}')
    return {"title": f"Market: {q}", "html": head + "".join(body) + full(reader, v.basis if v else None)}


def _today(reader: Reader, mid: str, unit: str | None) -> str:
    """Today's level and where it comes from: a holding's or a proxy's latest price, or the analyst's level from a
    page."""
    store = reader.store
    if schema.PROXY_METRIC_RE.match(mid):
        out = reader.output("proxy_price", mid) or {}
        shown = num(level(out.get("level"), unit))
        what = {"last trade": "last trade", "latest minute bar": "latest minute's close",
                "latest hourly bar": "latest hourly price"}.get(out.get("from"), "price")
        if out.get("time"):
            return f'{shown}<div class="aside">{heading(what)} {moment(out["time"])}</div>'
        return f'{shown}<div class="aside">Close on {day(out.get("asof"))}</div>'
    stated = store.stated_of(mid)
    sid = (store.citations_of(mid) or {}).get("level")
    src = f' · {go(sid, e(host(store.stated_of(sid).get("url"))))}' if sid else ""
    return f'{num(level(stated.get("level"), unit))}<div class="aside">As of {day(stated.get("asof"))}{src}</div>'


def _stated(link: dict) -> tuple[str, dict]:
    """What a consequence states of its price's move (impact.stated_move): its shock, with what it is measured
    against and its basis."""
    kind = next(iter(stated_move(link)))
    return kind, link["stated"][kind]


AGAINST_WORDS = {schema.BASELINE: "whole move", schema.FOLLOWS: "residual"}  # what a shock states (MATH.md 1.3)


def _against(link: dict) -> str:
    """What a consequence's shock is measured against (MATH.md 1.3): the baseline, or what the price follows."""
    return schema.against(_stated(link)[1])


def _impact_of(reader: Reader, pid: str) -> tuple[str | None, dict]:
    """The Computation that values the worlds a statement on `pid` belongs to, and its output: the watched event's, for
    a statement on the event or on a way of it."""
    event = _event_above(reader.store, pid) or pid
    cid = _basis(reader.derived(event, "Proposition", ("full_impact",)), "full_impact")
    return cid, reader.computation(cid).get("output") or {}


def _inspect_consequence(reader: Reader, lid: str) -> dict:
    """A shock, opened from the question it answers (MATH.md 1.3, 1.5, ONTOLOGY.md 2): how large it is, beside what its price did
    over each past window it is sized from, on one scale and in the terms it is stated in; the analyst's basis for it;
    then where it puts the price in each outcome, in the shock's unit, the baseline marked. How the shock makes the
    price's whole move and residual in each outcome it applies in, and how each level is placed against today, are each
    a step down. A move's direction is its sign and its side of zero, never a gain or a loss."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None:
        return _missing()
    pid, moved = link["from"], link["to"]
    _, said = _stated(link)
    what = target_label(store, moved)
    event = _event_above(store, pid) or pid
    head = hero("Shock", title=go(moved, e(heading(what)), "go name"), context=go(event, e(title_of(store, event))))
    impact_cid, impact = _impact_of(reader, pid)
    cid = _basis(reader.derived(lid, "consequence", ("levels",)), "levels")
    out = reader.computation(cid).get("output") or {}
    basis = _stated_basis(reader, said, lid)
    body = (_sizes(reader, link, said, impact) + (f'<h3 class="sub">Basis</h3>{basis}' if basis else "")
            + _levels_table(reader, link, out, impact))
    return {"title": f"Shock to {what}", "html": head + body + full(reader, cid or impact_cid)}


def _applies_in(store: Store, link: dict, impact: dict) -> str:
    """The outcome a shock is stated for, as the page names it: the side of its event it applies to, Yes or No, where
    it is stated on the event; the way or the joint world it is stated on, by its title, otherwise."""
    pid = link["from"]
    at = list(((impact.get("consequences") or {}).get(link["id"]) or {}).get("worlds") or {})
    if pid == _event_above(store, pid):
        return _world_label(store, (impact.get("worlds") or {}).get(at[0], {}).get("side") or "") if at else ""
    return title_of(store, pid)


def _sizes(reader: Reader, link: dict, said: dict, impact: dict) -> str:
    """The shock as stated, then what its price did over each past window it is sized from (MATH.md 1.3, ONTOLOGY.md 2), one row each
    on one scale: a bar from a common zero and the figure, in the terms the shock is stated in, its whole move or its
    residual, as the section's name says. The shock's row names the outcome it is stated for and opens its whole move
    and residual there (x:shock); each window's row names its dates, with the two closes it ran between where they are
    other dates, and opens its working (x:window). A residual the store does not measure says so."""
    store, lid, moved = reader.store, link["id"], link["to"]
    follows = _against(link) == schema.FOLLOWS
    key, form = (FOLLOWED, "Residual") if follows else (MOVE, "Whole move")
    moves_ = (reader.computation(_basis(reader.derived(lid, "consequence", ("levels",)), "levels")).get("output")
              or {}).get("moves") or {}
    other = any(stated_shock(store, moved, m.get(MOVE)) != stated_shock(store, moved, m.get(FOLLOWED))
                for m in moves_.values())  # the step shows the other form only where it is another figure
    rows = [{"label": "Shock", "sub": _applies_in(store, link, impact), "x": said.get("value"),
             "go": f"x:shock:{lid}" if other else None, "cls": "st"}]
    measured = _measured_windows(reader, lid)
    for i, w in enumerate(said.get(schema.WINDOWS) or []):
        m = measured[i] if i < len(measured) else {}
        closes = m.get("sessions") or []
        sub = (f"Closes {day(closes[0], year=closes[0][:4] != closes[1][:4])} and {day(closes[1])}"
               if len(closes) == 2 and closes != [w["start"], w["end"]] else "")
        rows.append({"label": _dates(w["start"], w["end"]), "sub": sub, "x": m.get(key), "go": f"x:window:{lid}:{i}",
                     "cls": "pw", "state": "" if m else ("wait" if reader.waits("shock_windows", lid) else "none")})
    values = [r["x"] for r in rows if r["x"] is not None] + [0.0]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.06 or 1.0
    axis = {"lo": lo - pad if lo < 0 else lo, "hi": hi + pad if hi > 0 else hi}
    compared = len(rows) > 1 and hi > lo  # a scale where there is more than one size, and one other than zero
    zero = _at(axis, 0.0) if compared else ""
    html = []
    for r in rows:
        x = r["x"]
        if x is None:
            seg = ""
            figure = (WAIT if r.get("state") == "wait" else "" if r.get("state") == "none"
                      else go(r["go"], "Not measured", "go unmeasured"))
        else:
            left, right = sorted((0.0, x))
            seg = (f'<i class="sz-seg {r["cls"]}" style="left:{_at(axis, left)};right:calc(100% - {_at(axis, right)})"></i>'
                   if compared else "")
            shown = stated_shock(store, moved, x)
            figure = gonum(r["go"], shown) if r["go"] else num(e(shown))
        sub = f'<span class="sz-s" title="{e(r["sub"])}">{e(r["sub"])}</span>' if r["sub"] else ""
        track = f'<div class="po-track"><i class="po-zero" style="left:{zero}"></i>{seg}</div>' if compared else "<div></div>"
        html.append(f'<div class="sz-row {r["cls"]}"><div class="sz-lab"><span class="sz-t">{e(r["label"])}</span>{sub}'
                    f'</div>{track}<div class="sz-v">{figure}</div></div>')
    return f'<h3 class="sub">{form}</h3><div class="sz">{"".join(html)}</div>'


def _levels_table(reader: Reader, link: dict, out: dict, impact: dict) -> str:
    """The price in each outcome, in the unit the shock is stated in (MATH.md 1.5, ONTOLOGY.md 2.2): a factor quoted as a yield by
    its yield, a factor by its proxy's price, a name by its own price in its own currency; today's first, then each
    outcome in the event's order, the baseline marked as the event view marks it. Each opens how it is placed
    (x:level)."""
    store, lid, moved = reader.store, link["id"], link["to"]
    yields = out.get("yields")
    there = yields or out.get("levels") or {}
    if not there:
        return ""
    if yields:
        head, today, unit = "Yield", out.get("yield"), "%"
    else:
        proxy = (store.stated_of(moved) or {}).get("proxy") if moved.startswith("factor:") else None
        head, today, unit = proxy or "Price", out.get("c"), unit_of(store, price_metric(store, moved))
    base = impact.get("baseline")
    rows = [f'<tr><th>Today</th><td class="n">{num(e(level(today, unit)))}</td></tr>'] if today is not None else []
    for w, v in there.items():
        tag = ' <span class="otag">Baseline</span>' if w == base else ""
        rows.append(f'<tr><th>{e(_world_label(store, w))}{tag}</th>'
                    f'<td class="n">{gonum(f"x:level:{lid}", level(v, unit))}</td></tr>')
    return (f'<h3 class="sub">Levels</h3><table class="cmp lv"><thead><tr><th></th>'
            f'<th class="n">{e(head)}</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _inspect_shock(reader: Reader, lid: str) -> dict:
    """A shock in each outcome it applies in (MATH.md 1.3, 1.4): the price's whole move against the baseline there, the
    response the factors give it, and its residual, each in the terms the shock is stated in, the form the analyst
    states in bold. Whichever is stated, the impact works out the others."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None or link.get("relation") != "consequence":
        return _missing()
    pid, moved = link["from"], link["to"]
    cid = _basis(reader.derived(lid, "consequence", ("levels",)), "levels")
    out = reader.computation(cid).get("output") or {}
    moves_, responses = out.get("moves") or {}, out.get("responses") or {}  # each in the shock's terms
    impact_cid, _ = _impact_of(reader, pid)
    stated = FOLLOWED if _against(link) == schema.FOLLOWS else MOVE
    columns = [(MOVE, "Whole move"), *([("response", "Response")] if any(v is not None for v in responses.values())
                                       else []), (FOLLOWED, "Residual")]
    rows = []
    for w, m in moves_.items():
        figures = {MOVE: m.get(MOVE), FOLLOWED: m.get(FOLLOWED), "response": responses.get(w)}
        cells = "".join(f'<td class="n{" st" if k == stated else ""}">{num(e(stated_shock(store, moved, figures[k])))}</td>'
                        for k, _ in columns)
        rows.append(f'<tr><th>{go(f"x:outcome:{_event_above(store, pid) or pid}:{w}", e(_world_label(store, w)), "go name")}'
                    f'</th>{cells}</tr>')
    table = (f'<table class="cmp ws"><thead><tr><th></th>{"".join(f"<th class=n>{e(h)}</th>" for _, h in columns)}</tr>'
             f'</thead><tbody>{"".join(rows)}</tbody></table>') if rows else ""
    event = _event_above(store, pid) or pid  # its rows are the event's outcomes, so it is named by its event
    head = hero("Whole move and residual", title=go(moved, e(heading(target_label(store, moved))), "go name"),
                context=go(event, e(title_of(store, event))))
    return {"title": "Whole move and residual", "html": head + table + full(reader, impact_cid)}


def _inspect_level(reader: Reader, lid: str) -> dict:
    """How a price a shock sets is placed (MATH.md 1.5): in each outcome, the weight today's prices carry on it, the
    price's move there against the baseline, and its level; today's level is the outcomes' at those weights. For a
    factor quoted as a yield, the yield each level implies. Every figure is the event's impact's."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None or link.get("relation") != "consequence":
        return _missing()
    pid, moved = link["from"], link["to"]
    impact_cid, impact = _impact_of(reader, pid)
    got = (impact.get("consequences") or {}).get(lid) or {}
    worlds_, base = impact.get("worlds") or {}, impact.get("baseline")
    levels = got.get("levels") or {}
    event = _event_above(store, pid) or pid
    head = hero("Level", title=go(moved, e(heading(target_label(store, moved))), "go name"),
                context=go(event, e(title_of(store, event))))
    if not levels or got.get("c") is None:
        return {"title": "Level", "html": head + full(reader, impact_cid)}
    unit = unit_of(store, price_metric(store, moved))
    if moved.startswith("factor:"):
        price = (store.stated_of(moved) or {}).get("proxy") or "Price"
        against = {w: (impact.get("against_baseline") or {}).get(w, {}).get(moved) for w in levels}
    else:
        price = "Price"
        against = {w: ((impact.get("whole") or {}).get(moved) or {}).get(w) for w in levels}
    yields = got.get("yields") if got.get("yield") is not None else None
    # the weights today's prices carry: each outcome's probability, unless the analyst states a weight that differs
    priced = any(chance((worlds_.get(w) or {}).get("q")) != chance((worlds_.get(w) or {}).get("p")) for w in levels)
    rows = []
    for w, v in levels.items():
        tag = ' <span class="otag">Baseline</span>' if w == base else ""
        rows.append(f'<tr><th>{e(_world_label(store, w))}{tag}</th><td class="n">{num(chance((worlds_.get(w) or {}).get("q")))}'
                    f'</td><td class="n">{num(e(level(v, unit)))}</td><td class="n">{num(e(move(against.get(w))))}</td>'
                    + (f'<td class="n">{num(e(level(yields.get(w), "%")))}</td>' if yields else "") + "</tr>")
    rows.append(f'<tr class="tot"><th>Today</th><td class="n">{num(chance(1.0))}</td>'
                f'<td class="n">{num(e(level(got["c"], unit)))}</td><td class="n"></td>'
                + (f'<td class="n">{num(e(level(got["yield"], "%")))}</td>' if yields else "") + "</tr>")
    # each outcome's weight beside its price, which they average to today's; the price's move beside it
    table = (f'<table class="cmp lw"><thead><tr><th></th><th class="n">{"Priced in" if priced else "Probability"}</th>'
             f'<th class="n">{e(price)}</th>'
             f'<th class="n">vs baseline</th>{"<th class=n>Yield</th>" if yields else ""}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>')
    return {"title": "Level", "html": head + table + full(reader, impact_cid)}


def _dates(start: str, end: str) -> str:
    """Two dates as a span: Nov 25 – 26, 2024 within a month, Jun 8 – Jul 18, 2026 within a year, Dec 29, 2025 –
    Jan 5, 2026 across a year's end."""
    if start[:7] == end[:7]:
        return f"{day(start, year=False)} \u2013 {date.fromisoformat(end[:10]).day}, {end[:4]}"
    return f"{day(start, year=start[:4] != end[:4])} \u2013 {day(end)}"


def _measured_windows(reader: Reader, lid: str) -> list[dict]:
    """What the price a consequence moves did over each past window its shock is sized from, as the store measures it
    (impact.SHOCK_WINDOWS): one entry per window, in the order stated, or none while it is not measured."""
    return (reader.output("shock_windows", lid) or {}).get("windows") or []


def _inspect_window(reader: Reader, rest: str) -> dict:
    """A past window a shock is sized from, `{link}:{i}` (MATH.md 1.3, 3.2): what the price did over it, in the form the
    shock is stated in, under the window's dates, and the close each end was read at where it is not the date. Then
    its whole move and its residual, and for a name the response its betas give it to the factors' moves there, each
    in the terms a shock on it is stated in, a listing abroad's in its own currency with the rate beside them; then
    the factors over the window, beside their moves in each outcome the shock applies in where any factor moves
    there (1.3). Each figure is read from the Computation that measured it."""
    store = reader.store
    lid, _, i = rest.rpartition(":")
    link = store.link_by_id(lid) if lid else None
    if link is None or link.get("relation") != "consequence" or not i.isdigit():
        return _missing()
    _, said = _stated(link)
    stated = said.get(schema.WINDOWS) or []
    if int(i) >= len(stated):
        return _missing()
    w = stated[int(i)]
    measured = _measured_windows(reader, lid)
    m = measured[int(i)] if int(i) < len(measured) else {}
    pid, moved = link["from"], link["to"]
    what = heading(target_label(store, moved))
    form = FOLLOWED if _against(link) == schema.FOLLOWS else MOVE
    shown = stated_shock(store, moved, m.get(form))
    closes = m.get("sessions") or [None, None]
    differ = closes[0] and (closes[0], closes[1]) != (w["start"], w["end"])
    currency = (reader.computation(m.get("basis")).get("output") or {}).get("currency") if m else None
    context = (f'{e(what)} {"residual" if form == FOLLOWED else "whole move"}{f", in {e(currency)}" if currency else ""} \u00b7 '
               + go(f"x:consequence:{lid}", e(f"shock {stated_shock(store, moved, said['value'])}"))
               + (f" \u00b7 closes {e(_dates(closes[0], closes[1]))}" if differ else ""))
    head = hero("Past window", figure=num(e(shown)) if shown else (WAIT if not m else ""),
                title=e(_dates(w["start"], w["end"])), context=context)
    if not m:
        return {"title": "Past window", "html": head}
    rec = reader.computation(m.get("basis"))
    got = rec.get("output") or {}
    currency = got.get("currency")  # a listing abroad: its moves are in its own currency, the rate beside them

    def figure(x: float | None) -> str:
        return num(e(stated_shock(store, moved, x))) if x is not None else num("Not measured", "unmeasured")
    rows = [("Whole move", m.get(MOVE), form == MOVE)]
    if moved.startswith("name:") and got.get("response") is not None:
        rows.append(("Response", got["response"], False))
    rows.append(("Residual", m.get(FOLLOWED), form == FOLLOWED))
    body = "".join(f'<tr{CLASS_ST if bold else ""}><th>{label_}</th><td class="n">{figure(x)}</td></tr>'
                   for label_, x, bold in rows)
    if currency and got.get("rate_move") is not None:
        body += f'<tr class="rate"><th>{e(currency)}/USD</th><td class="n">{num(e(move(got["rate_move"])))}</td></tr>'
    table = f'<table class="cmp pw"><tbody>{body}</tbody></table>'
    factors = got if moved.startswith("factor:") else reader.computation(got.get("factors")).get("output") or {}
    return {"title": "Past window", "html": head + table
            + _window_factors(reader, _event_above(store, pid) or pid, lid, factors) + full(reader, m.get("basis"))}


CLASS_ST = ' class="st"'  # the row of the figure the analyst states


def _window_factors(reader: Reader, pid: str, lid: str, factors: dict) -> str:
    """Each factor's move over a past window (window_returns), in the terms a shock on it is stated in, beside its move
    against the baseline in each outcome the consequence `lid` applies in where any factor moves there
    (ledger.event_now): set by a shock, or moving with the stated ones (MATH.md 1.3)."""
    store = reader.store
    moves_ = factors.get("moves") or {}
    if not moves_:
        return ""
    now = _now(reader, pid)
    entries = {f["factor"]: f for f in now.get("factors") or []}
    target = (store.link_by_id(lid) or {}).get("to")
    here = (entries.get(target) or next((n for n in now.get("names") or [] if n.get("name") == target), {}))

    def there(c: str, wid: str) -> tuple[str, str]:  # a factor's move in an outcome, as shown, and how it is set
        item = entries.get(c) or {"factor": c}
        how = (item.get("worlds") or {}).get(wid) or {}
        x = how.get("move", 0.0)
        shown = factor_move(item, x) if "yield" in item or not quoted_as_yield(store, c) else stated_shock(store, c, x)
        return shown, "st" if how.get("by") in ("own", "parent") else "im"
    nothing = {move(0.0), points(0.0)}
    worlds = [wid for wid, how in (here.get("worlds") or {}).items() if how.get("consequence") == lid
              and any(there(c, wid)[0] not in nothing for c in moves_)]
    heads = "".join(f'<th class="n"><span class="clamp" title="{e(_world_label(store, wid))}">'
                    f'{e(_world_label(store, wid))}</span></th>' for wid in worlds)
    kinds, rows = set(), []
    for c, raw in moves_.items():
        over = None if raw is None else in_its_terms(store.stated_of(c) or {}, raw)
        shown = stated_shock(store, c, over) if over is not None else ""
        tds = [f'<td class="n">{num(e(shown)) if shown else num("Not measured", "unmeasured")}</td>']
        for wid in worlds:
            figure, kind = there(c, wid)
            kinds.add(kind)
            tds.append(f'<td class="n {kind}">{num(e(figure))}</td>')
        rows.append(f'<tr><th>{go(c, e(heading(channel_names(store).get(c) or c)), "go name")}</th>{"".join(tds)}</tr>')
    key = (f'<p class="aside"><span class="k-st">Shock</span> \u00b7 <span class="k-im">{e(heading(MOVES_WITH))}</span></p>'
           if kinds == {"st", "im"} else "")
    return (f'<h3 class="sub">Factors</h3><table class="cmp wf"><thead><tr><th></th>'
            f'<th class="n">Window</th>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table>{key}')


def _inspect_swing(reader: Reader, pid: str) -> dict:
    """The full impact, Yes less No (MATH.md 1.1, 1.6, TOOLS.md 9): Yes, No, the expected value where it is not zero and Yes as a
    share of the portfolio; then what moves it: each factor's dollar beta and its move between the two sides, each
    stated company's exposure and its move beyond its response, a held contract, and the rest, in whole dollars adding
    to it as shown; then any weight stated for what today's prices carry, and any value stated for a holding."""
    store = reader.store
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    rec = reader.computation(cid)
    out, inputs = rec.get("output") or {}, rec.get("inputs") or {}
    title = title_of(store, pid)
    if "full_impact" not in out:
        return {"title": "Full impact", "html": hero("Full impact", context=go(pid, e(title))) + full(reader, cid)}
    D = out["full_impact"]
    head = hero("Full impact", figure=num(e(amount(D)), _tone_whole(D)), context=go(pid, e(title)))
    tree = _outcomes(reader, pid, out)
    body = [facts(_side_facts(pid, out, tree))]
    entries = _what_moves(reader, pid, out, inputs.get("g") or {}, "E", out["contributions"], out.get("own") or {},
                          sum(((h.get("curve") or {}).get("full_impact") or 0.0) for h in out.get("by_holding") or []),
                          {c: out["u_E"].get(c) for c in out["contributions"]})
    body.append('<h3 class="sub">What moves it</h3>' + _moves_table(entries, D, "Full impact"))
    priced = _priced_items(reader, pid, tree, out.get("priced_in") or {}, ())
    if priced:
        body.append('<h3 class="sub">Priced in</h3>' + "".join(priced))
    valued = _worth_items(reader, pid, out, tree, (), out.get("p"))
    if valued:
        body.append('<h3 class="sub">Values stated</h3>' + "".join(valued))
    return {"title": "Full impact", "html": head + "".join(body) + full(reader, cid)}


def _side_facts(pid: str, out: dict, tree: list[dict]) -> list[tuple]:
    """Yes and No, each its side's P&L, opening its outcome; the expected value where it is not nothing as shown; Yes
    as a share of the portfolio."""
    out_ = []
    for side, label_ in (("yes", "If Yes"), ("no", "If No")):
        on = [o for o in tree if o["world"] and o["side"] == side]
        x = out.get(side)
        target = f"x:outcome:{pid}:{on[0]['id'] if len(on) == 1 else side}"
        out_.append((label_, gonum(target, amount(x), _tone_whole(x)) if x is not None else ""))
    mu = out.get("expected")
    if mu is not None and not nothing(mu):
        out_.append(("Expected value", num(e(amount(mu)), _tone_whole(mu))))
    if out.get("share_yes") is not None:
        out_.append(("If Yes, of portfolio", num(e(share(out["share_yes"])))))
    return out_


def _what_moves(reader: Reader, pid: str, out: dict, g: dict, world: str, contributions: dict, own: dict,
                curve: float, moves_: dict) -> list[tuple[str, str, str, float]]:
    """What moves a figure, each part's name, exposure and move, and its dollars (MATH.md 1.6): each factor at its dollar
    beta and its move, a factor quoted as a yield naming its proxy, whose price move it is; each stated company at the
    portfolio's exposure to it and its move beyond its response; a held contract by its side and quantity; the rest,
    what compounding and a holding's terms add. `world` is the outcome, or "E" for the move between the sides."""
    store = reader.store
    names = channel_names(store)
    setter = {c["target"]: c["link"] for c in (out.get("consequences") or {}).values()}
    entries = []
    for c, x in sorted(contributions.items(), key=lambda kv: -abs(kv[1])):
        if not moves_.get(c):
            continue
        proxy = (store.stated_of(c) or {}).get("proxy") if quoted_as_yield(store, c) else None
        label_ = go(c, e(heading(names.get(c, c))), "go name") + (f'<span class="sub">{e(proxy)}</span>' if proxy else "")
        mv = move(moves_[c])
        entries.append((label_, num(e(amount(g.get(c), sign=False))),
                        gonum(f"x:consequence:{setter[c]}", mv) if c in setter else num(e(mv)), x))
    reach = out.get("reach") or {}
    for n, x in sorted(own.items(), key=lambda kv: -abs(kv[1])):
        contracts = [h for h in out.get("by_holding") or [] if h["name"] == n and h.get("contract")]
        if contracts:
            held = " · ".join(f'{h["contract"]["side"].title()}, {count(h["contract"]["quantity"])} contracts'
                              for h in contracts)
            entries.append((go(n, e(market_question(reader, n) or n), "go name") + f'<span class="sub">{e(held)}</span>',
                            "", "", x))
            continue
        mv = move((out.get("own_moves") or {}).get(n, {}).get(world))
        entries.append((go(n, e(name_of(store, n)), "go name") + '<span class="sub">Beyond its response</span>',
                        num(e(amount(reach.get(n), sign=False))),
                        gonum(f"x:consequence:{setter[n]}", mv) if n in setter else num(e(mv)), x))
    if not nothing(curve):
        entries.append(("The rest", "", "", curve))
    return entries


def _moves_table(entries: list[tuple[str, str, str, float]], whole: float, word: str) -> str:
    """What moves a figure as a table: each part's exposure, move and dollars, in whole dollars adding to the figure
    as shown, the figure beneath."""
    shown = apportion([x for *_, x in entries], whole)
    rows = [(label_, [exposure, mv, num(e(amount(d)), _tone_whole(d))])
            for (label_, exposure, mv, _), d in zip(entries, shown) if d]
    return _cols(("Exposure", "Move", word), rows, (e(word), ["", "", num(e(amount(whole)), _tone_whole(whole))]), "pl")


def _outcomes(reader: Reader, pid: str, out: dict) -> list[dict]:
    """The event's outcomes as a tree, from its impact (ledger.outcomes, MATH.md 1.1): each by its own title, with its
    parent, its side, its probability, the weight today's prices put on it, and the portfolio's P&L there; each world
    marked, and the baseline."""
    try:
        return outcome_tree(reader.store, pid, out) or []
    except (StoreError, Unavailable, Waiting, KeyError):
        return []


def _contract_operand(reader: Reader, pid: str, out: dict, h: dict, world: str | None = None) -> str:
    """The held quantity and actual payoff terms, not an exposure inferred from a dollar contribution."""
    contract = h["contract"]
    probabilities = contract["probabilities"]
    fixed = f'{num(count(contract["quantity"]))} × {num("$1")} × {num(operand(contract["discount"]))}'
    if world is not None:
        return f'{fixed} × {num(operand(probabilities[world]))} − {num(_neg(money(contract["current"]), contract["current"]))}'
    worlds = [o for o in _outcomes(reader, pid, out) if o["world"]]
    chances = {side: side_mean(worlds, probabilities, side) for side in ("yes", "no")}
    return f'{fixed} × ({num(operand(chances["yes"]))} − {num(operand(chances["no"]))})'


def _mean_of(formula: str, worlds: list[dict], over: str, pnl=lambda o: o["A"]) -> str:
    """A side's mean, with its formula and the values filled in: each outcome's probability times its P&L, summed,
    over the side's probability."""
    return worked_sum(formula, "(", [f"{operand(o['p'])} × {_neg(money(pnl(o)), pnl(o))}" for o in worlds],
                      f") / {over}")


def _sides(reader: Reader, pid: str, out: dict) -> list[tuple[str, str, str]]:
    """Yes and No, each its side's outcomes averaged at their probabilities (MATH.md 1.1): a side left whole is its one
    outcome's P&L; then the expected value, where it is not zero; the full impact, Yes less No; and Yes as a share of
    the portfolio."""
    tree = _outcomes(reader, pid, out)
    P = operand(out["p"])
    rows = []
    for side, label_, over in (("yes", "Yes", P), ("no", "No", f"(1 − {P})")):
        on = [o for o in tree if o["world"] and o["side"] == side]
        what = (_mean_of("its outcomes' P&L averaged at their probabilities", on, over) if len(on) > 1
                else go(f"x:outcome:{pid}:{on[0]['id']}", "the P&L in its outcome") if on else "")
        rows.append((label_, what, gonum(f"x:outcome:{pid}:{on[0]['id']}", money(out[side], sign=True))
                     if len(on) == 1 else num(money(out[side], sign=True))))
    mu = out.get("expected")
    if mu is not None and money(mu) != "$0.00":
        filled = [f"{operand(o['p'])} × {_neg(money(o['A']), o['A'])}" for o in tree if o["world"]]
        rows.append(("Expected value", worked_sum("each outcome's P&L at its probability, summed", "", filled),
                     num(money(mu, sign=True))))
    rows.append(("Full impact", eqn("Yes − No"), num(money(out["full_impact"], sign=True))))
    if out.get("share_yes") is not None:
        rows.append(("Share of portfolio", worked("Yes / value", f"{_neg(money(out['yes']), out['yes'])} / "
                                                                 f"{money(out['V'])}"), num(share(out["share_yes"]))))
    return rows


def _inspect_implied(reader: Reader, rest: str) -> dict:
    """The weight a holding's price implies on the event (MATH.md 1.6): it is where today's price falls between the value
    stated for each side, so the view gives the price and the two values, then each side's values by outcome with the
    share each counts at in its side's value."""
    m = re.fullmatch(r"(prop:\d+):(.+)", rest)
    if not m:
        return _missing()
    pid, nid = m.groups()
    store = reader.store
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    value = ((reader.output("impact", pid) or {}).get("worth") or {}).get(nid)
    if not value:
        return _missing()
    implied, c, sides = value.get("implied"), value.get("c"), value.get("sides") or {}
    levels, links = value.get("levels") or {}, value.get("considerations") or {}
    name = name_of(store, nid)
    head = hero("Implied by today\u2019s price", figure=e(chance(implied)) if implied is not None else "",
                context=f'{go(nid, e(name))} \u00b7 {go(pid, e(title_of(store, pid)))}')
    Y, N = (sides.get("yes") or {}).get("mean"), (sides.get("no") or {}).get("mean")
    body = facts([("Value if No", num(e(money(N))) if N is not None else ""),
                  ("Price today", gonum(nid, money(c)) if c is not None else ""),
                  ("Value if Yes", num(e(money(Y))) if Y is not None else "")])
    for side, label_ in (("yes", "Yes"), ("no", "No")):
        got = sides.get(side) or {}
        shares = got.get("shares") or {}
        if got.get("mean") is None or len(shares) < 2:
            continue
        rows = [(go(links[w], e(_world_label(store, w))) if links.get(w) else e(_world_label(store, w)),
                 [num(e(chance(share))), num(e(money(levels.get(w))))]) for w, share in shares.items()]
        body += f'<h3 class="sub">{label_}</h3>' + _cols(("Share", "Value"), rows, (f"Value if {label_}",
                                                                                   ["", num(e(money(got["mean"])))]))
    return {"title": f"Implied by today\u2019s price: {name}", "html": head + body + full(reader, cid)}


def _inspect_volatility(reader: Reader, pid: str) -> dict:
    """The volatility (MATH.md 1.1): the spread of the portfolio's P&L across the event's outcomes at their probabilities,
    each outcome's P&L on one scale with its probability; where a side has ways, the spread within it."""
    store = reader.store
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    out = reader.computation(cid).get("output") or {}
    title = title_of(store, pid)
    if out.get("volatility") is None:
        return {"title": "Volatility", "html": hero("Volatility", context=go(pid, e(title))) + full(reader, cid)}
    head = hero("Volatility", figure=e(either(out["volatility"])), context=go(pid, e(title)))
    worlds = [o for o in _outcomes(reader, pid, out) if o["world"]]
    rows = _bar_rows([{"label": go(f"x:outcome:{pid}:{o['id']}", e(o["title"]), "go name"),
                       "sub": f"Probability {chance(o['p'])}", "x": o["A"], "shown": amount(o["A"]),
                       "go": f"x:outcome:{pid}:{o['id']}"} for o in worlds])
    mu = out.get("expected")
    spread = [(f"Within {label_}", num(e(either(x)))) for side, label_ in (("yes", "Yes"), ("no", "No"))
              if (x := (out.get("spread") or {}).get(side)) is not None and not nothing(x)]
    extra = facts(([("Expected value", num(e(amount(mu)), _tone_whole(mu)))] if mu is not None and not nothing(mu)
                   else []) + spread)
    return {"title": "Volatility", "html": head + f'<h3 class="sub">Outcomes</h3><div class="hb">{rows}</div>' + extra
            + full(reader, cid)}


def _inspect_portfolio_volatility(reader: Reader, _: str) -> dict:
    """The portfolio's volatility to the horizon's end (MATH.md 3.2): its daily move in dollars at today's holdings, the
    sessions to the horizon's end, and the history it is measured over; the holdings whose own series is shorter."""
    cid = _basis(reader.derived("portfolio", "Portfolio", ("volatility",)), "volatility")
    out = reader.computation(cid).get("output") or {}
    title = "Horizon volatility"
    if out.get("volatility") is None:
        return {"title": title, "html": hero(title) + full(reader, cid)}
    first, last = out["window"]
    head = hero(title, figure=e(either(out["volatility"])), context=f"To {e(day(out['horizon_end']))}")
    body = facts([("Daily", num(e(either(out["daily_sd"])))),
                  ("Sessions ahead", num(e(count(out["sessions_ahead"])))),
                  ("Measured over", e(_dates(first, last)), f"{count(out['sessions'])} sessions")])
    short = [(e(heading(name_of(reader.store, h["name"]))), [e(day(h["from"])) if h["from"] else "none"])
             for h in out["by_holding"].values() if h["own_sessions"] < out["sessions"] - 1]
    if short:
        body += '<h3 class="sub">Shorter histories</h3>' + _cols(("Own closes from",), short)
    return {"title": title, "html": head + body + full(reader, cid)}


def _inspect_holding(reader: Reader, rest: str) -> dict:
    """A holding's full impact under an event (MATH.md 1.6, 1.7): Yes and No; what moves it, its dollars at its response,
    each stated company it carries beyond its response, and the rest, adding to it as shown; then its P&L in each
    outcome. A contract the portfolio holds: its side and quantity, and its P&L in each outcome beside the chance it
    pays there."""
    store = reader.store
    pid, hid = rest[:rest.index(":", rest.index(":") + 1)], rest[rest.index(":", rest.index(":") + 1) + 1:]
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    out = reader.computation(cid).get("output") or {}
    h = next((x for x in out.get("by_holding") or [] if x["holding"] == hid), None)
    if h is None:
        return _missing()
    ticker = _holding_label(reader, h)
    D = h["full_impact"]
    head = hero("Full impact", figure=num(e(amount(D)), _tone_whole(D)), title=e(ticker),
                context=go(pid, e(title_of(store, pid))))
    sides = facts([(label_, num(e(amount(h[side])), _tone_whole(h[side]))) for side, label_ in (("yes", "If Yes"), ("no", "If No"))])
    worlds = [o for o in _outcomes(reader, pid, out) if o["world"]]
    contract = h.get("contract")
    if contract:
        held = facts([("Side", e(contract["side"].title())), ("Quantity", num(e(count(contract["quantity"]))))])
        rows = [(go(f"x:outcome:{pid}:{o['id']}", e(o["title"]), "go name"),
                 [num(e(chance(contract["probabilities"].get(o["id"])))),
                  num(e(amount(h["worlds"][o["id"]])), _tone_whole(h["worlds"][o["id"]]))]) for o in worlds]
        body = sides + held + '<h3 class="sub">Outcomes</h3>' + _cols(("Pays", "P&L"), rows)
        return {"title": f"{ticker}, full impact", "html": head + body + full(reader, cid)}
    at, entries = h.get("at_betas") or {}, []
    if at.get("dollars") and not nothing(at.get("full_impact")):
        entries.append((e(ticker) + '<span class="sub">Its response</span>', num(e(amount(at["dollars"], sign=False))),
                        num(e(move(at.get("move")))), at["full_impact"]))
    for x in h.get("own") or []:
        entries.append((go(x["name"], e(name_of(store, x["name"])), "go name") + '<span class="sub">Beyond its response</span>',
                        num(e(amount(x["dollars"], sign=False))),
                        num(e(move((out.get("own_moves") or {}).get(x["name"], {}).get("E")))), x["full_impact"]))
    curve = (h.get("curve") or {}).get("full_impact") or 0.0
    if not nothing(curve):
        entries.append(("The rest", "", "", curve))
    body = [sides, ('<h3 class="sub">What moves it</h3>' + _moves_table(entries, D, "Full impact")) if entries else ""]
    body.append('<h3 class="sub">Outcomes</h3><div class="hb">' + _bar_rows(
        [{"label": go(f"x:outcome:{pid}:{o['id']}", e(o["title"]), "go name"), "sub": f"Probability {chance(o['p'])}",
          "x": h["worlds"][o["id"]], "shown": amount(h["worlds"][o["id"]]), "go": f"x:outcome:{pid}:{o['id']}"}
         for o in worlds]) + "</div>")
    body.append(_option_values(reader, pid, out, h["name"]))
    return {"title": f"{ticker}, full impact", "html": head + "".join(body) + full(reader, cid)}


def _option_values(reader: Reader, pid: str, out: dict, nid: str) -> str:
    """The held option book's actual unit values and any pricing boundary, inside its holding's receipt."""
    book = ((out.get("valuation_terms") or {}).get("options") or {}).get(nid) or {}
    pieces = []
    for contract in book.get("contracts") or []:
        ref = contract["reference"]
        name = ref.get("ticker") or ref.get("name") or ""
        heading_ = f'{name} {contract["kind"]} {contract["strike"]:g} · {day(contract["expiry"])}'
        rows = [("Observed price", "per underlying unit", num(money(contract["value_today"]))),
                ("Initial pricing volatility", "annual", num(percent(contract["volatility"])))]
        for world, value in (contract.get("values") or {}).items():
            volatility = (contract.get("world_volatility") or {}).get(world)
            calibration = (contract.get("world_calibration") or {}).get(world) or {}
            model = (f"conditional volatility {num(percent(volatility))} annual" if volatility is not None else
                     f"{e(calibration.get('bound'))} pricing limit" if calibration.get("bound") else "")
            rows.append((go(f"x:outcome:{pid}:{world}", e(_world_label(reader.store, world))), model, num(money(value))))
        if contract.get("pricing_gap") is not None:
            rows.append(("Pricing gap", "weighted value less observed price", num(money(contract["pricing_gap"], sign=True))))
        adjustment = (contract.get("surface") or {}).get("adjustment")
        if adjustment and adjustment > 1e-9:
            rows.append(("Strike constraints", "largest adjustment to a world’s unit price", num(money(adjustment))))
        bound = contract.get("bound")
        boundary = f'<p class="aside">Pricing boundary: {e(bound)}.</p>' if bound else ""
        pieces.append(f'<h3 class="sub">{e(heading_)}</h3>' + boundary + receipt(rows))
    return ('<details class="rules"><summary>Option values</summary>' + "".join(pieces) + '</details>') if pieces else ""


def _holding_in_each(reader: Reader, pid: str, out: dict, h: dict) -> list[tuple[str, str, str]]:
    """A holding's P&L in each of the event's outcomes, each with its probability; then Yes and No, each its side's
    outcomes averaged at their probabilities (MATH.md 1.1, 1.7)."""
    worlds = [o for o in _outcomes(reader, pid, out) if o["world"]]
    rows = [(go(f"x:outcome:{pid}:{o['id']}", e(o["title"]), "go name"), f"probability {operand(o['p'])}",
             num(money(h["worlds"][o["id"]], sign=True), _tone(h["worlds"][o["id"]]))) for o in worlds]
    P = operand(out["p"])
    pnl = lambda o: h["worlds"][o["id"]]  # noqa: E731
    for side, label_, over in (("yes", "Yes", P), ("no", "No", f"(1 − {P})")):
        on = [o for o in worlds if o["side"] == side]
        if len(on) > 1:
            rows.append((label_, _mean_of("its outcomes averaged at their probabilities", on, over, pnl),
                         num(money(h[side], sign=True), _tone(h[side]))))
    return rows


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
           {"price": v.output.get("level"), "session": v.output.get("asof")}) or {}
    if now.get("price") is not None and now.get("session"):
        found = [x for x in found if x[0] < now["session"]] + [(now["session"], now["price"])]
    return [list(x) for x in found]


def _quote_lines(prices: dict) -> str:
    """A price as a broker's header gives it: the price and its day change in dollars and percent, then what the price
    is and when."""
    change, moved = prices.get("session_change"), prices.get("session_move")
    head = f'<div class="quote"><span class="px">{num(level(prices.get("price"), "USD"))}</span>'
    if change is not None and moved is not None:
        head += f'<span class="chg {_sign_tone(change)}">{num(money(change, sign=True))} ({num(move(moved))})</span>'
    head += "</div>"
    what = {"last trade": "Last trade", "latest minute bar": "Latest minute's close",
            "latest hourly bar": "Latest hourly price", "close": "Close"}.get(prices.get("from"), "Price")
    when = f"{what} {moment(prices['time'])}" if prices.get("time") else f"{what} on {day(prices.get('session'))}"
    return head + f'<p class="aside">{when}</p>'


def _price_chart(points: list, label: str, mark: dict | None = None, shown: str = "1Y") -> str:
    """A price over time, as the page draws it (app.js drawChart): its points, [date, price], each to the cent as the
    page rounds a price, with a button for each range the series covers, opening on `shown`, a year, or on all of it
    where the series is shorter; and a stretch to mark."""
    if len(points) < 2:
        return ""
    spec = {"kind": "price", "points": [[d, float(_half_up(v, "0.01"))] for d, v in points], "ranges": True,
            "range": shown}
    if mark:
        spec["mark"] = mark
    data = e(json.dumps(spec, separators=(",", ":")))
    return f'<figure class="chart" data-k="chart" data-chart="{data}" role="img" aria-label="{e(label)}"></figure>'


def _events_moving(reader: Reader, *, holding: str | None, nid: str) -> str:
    """The events that move a holding, or a name, most: each with what it does if it happens and if not."""
    found = _ledger(reader)
    items = []
    for r in (found or {}).get("rows") or []:
        rec = reader.computation((r.get("basis") or {}).get("full_impact"))
        out = rec.get("output") or {}
        if holding:
            share = next((x for x in out.get("by_holding") or [] if x["holding"] == holding), None)
            target = f"x:holding:{r['id']}:{holding}"
        else:
            share = next((x for x in out.get("contributors") or [] if x["name"] == nid), None)
            target = f"x:swing:{r['id']}"
        if share and not nothing(share["full_impact"]):
            items.append((abs(share["full_impact"]), r, share, target))
    items.sort(key=lambda x: -x[0])
    if not items:
        return ""
    rows_ = "".join(f'<tr data-k="ev:{e(r["id"])}"><td>{go(r["id"], e(r["title"]))}'
                    f'<div class="aside">Forecast {chance(r["probability"])}</div></td>'
                    f'<td>{gonum(t, amount(s["yes"]), _tone_whole(s["yes"]))}</td>'
                    f'<td>{gonum(t, amount(s["no"]), _tone_whole(s["no"]))}</td></tr>'
                    for _, r, s, t in items[:EVENTS_SHOWN])
    return ('<h3 class="sub">Events</h3><table class="ifs"><thead><tr><th>Event</th>'
            f'<th>If Yes</th><th>If No</th></tr></thead><tbody>{rows_}</tbody></table>')


def _sensitivities(reader: Reader, b: dict, fit: dict) -> str:
    """A name's betas, one to each factor in order, each factor below the first net of the factors above it (MATH.md 3.2);
    how each was fitted is in the full details."""
    store = reader.store
    names = channel_names(store)
    order = [c["id"] for c in sorted(store.query("Factor"), key=lambda c: c["stated"]["order"])]
    rows = [(go(c, e(heading(names.get(c, c))), "go name"), [num(e(sens(b[c])))]) for c in order if c in b]
    return '<h3 class="sub">Betas</h3>' + _cols(("Beta",), rows) if rows else ""


def _rows_of(entries: list[tuple[str, float, float]], rest: tuple[str, float, float] | None = None, *,
             first: int = CONSTITUENTS_SHOWN) -> list[str]:
    """Table rows, each a label with its weight of the fund and your exposure to it, then, always shown, the rest
    together: the weights in tenths of a percent and the dollars, whole, apportioned so that each column adds to its
    total, which ends the table; the rows past `first` open with "Show N more"."""
    every = entries + ([rest] if rest else [])
    tenths = apportion([w * 1000 for _, w, _ in every])
    amounts = whole_parts([d for _, _, d in every], sum(d for _, _, d in every))
    row = lambda label, t, n, cls="": (f'<tr class="{cls}"><td>{label}</td><td>{num(_tenth(t))}</td>'  # noqa: E731
                                       f'<td>{num(amount(n, sign=False))}</td></tr>')
    kept = [(label, t, n) for (label, _, _), t, n in zip(entries, tenths, amounts) if t or n]  # nothing shown, no row
    rows_ = [row(label, t, n, "more" if i >= first else "") for i, (label, t, n) in enumerate(kept)]
    if len(kept) > first:
        rows_.append(f'<tr class="all"><td colspan="3"><button class="linkish" type="button" data-all>Show '
                     f'{len(kept) - first} more</button></td></tr>')
    if rest:
        rows_.append(row(rest[0], tenths[-1], amounts[-1], "rest"))
    rows_.append(f'<tr class="sum"><td>Total</td><td>{num(_tenth(sum(tenths)))}</td>'
                 f'<td>{num(amount(sum(amounts), sign=False))}</td></tr>')
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


KIND_WORDS = {"equity": "Equity", "debt": "Fixed income", "cash": "Cash", "derivative": "Derivatives",
              "commodity": "Commodities", "currency": "Currency", "other": "Other"}


def _kind_words(g: dict) -> str:
    """A kind of asset in the practitioner's word for it, from the one set every source's holdings are put in."""
    return KIND_WORDS.get(g.get("kind"), "Other")


def _fund_holdings(reader: Reader, nid: str, fund: dict) -> str:
    """What a fund holds, from its holdings, every one counted whether or not the look-through prices it: each holding
    down to the look-through floor by name, with its ticker and where it is listed, then the others together; and the
    whole fund by country of listing and by asset class. Each with its weight and your exposure."""
    holds = fund.get("holds")
    if not holds:
        return ""
    ticker = name_of(reader.store, nid)
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
    if money(holds["weights_sum"] * holds["dollars"]) != money(holds["dollars"]):
        out.append(f'<p class="aside">Published weights sum to {_sum_percent(holds["weights_sum"])}.</p>')
    if not fund.get("expanded"):
        out.append(f'<p class="aside">Not looked through: {e(fund.get("reason"))}.</p>')
    places = [(e(g["listed"]) if g["listed"] else "No exchange given", g["weight"], g["dollars"])
              for g in holds["by_listing"]]
    out.append(f'<h3 class="sub">Listing country</h3>{_table("Country", _rows_of(places))}')
    kinds = [(e(_kind_words(g)), g["weight"], g["dollars"]) for g in holds["by_kind"]]
    out.append(f'<h3 class="sub">Asset class</h3>{_table("Class", _rows_of(kinds))}')
    return "".join(out)


def _about(reader: Reader, nid: str) -> str:
    """What a company does, in its reference's words; its industry and its country, as the portfolio's weight by
    company places it (MATH.md 3.1), each opening to the names in it."""
    does = ((reader.output("reference", nid) or {}).get("company") or {}).get("does")
    placed = _company_of(reader.store, nid)
    pairs = []
    if placed.get("industry"):
        named = e(placed["industry"])
        pairs.append(("Industry", go(placed["industry_id"], named) if placed.get("industry_id") else named))
    if placed.get("country"):
        country = next((g.get("country_name") for g in (reader.output("weights", "portfolio") or {}).get("by_country")
                        or [] if g.get("country") == placed["country"]), None) or placed["country"]
        pairs.append(("Country", go(placed["country_id"], e(country)) if placed.get("country_id") else e(country)))
    if not does and not pairs:
        return ""
    body = (f'<div class="profile">{paragraphs(does)}<button class="linkish" type="button" data-more>More'
            '</button></div>') if does else ""
    return f'<h3 class="sub">Profile</h3>{body}{_dl(pairs) if pairs else ""}'


def _name_view(reader: Reader, nid: str) -> dict:
    """A holding, or a name the funds hold, as a broker shows a position: its price and its day change, its price over
    time, your position or your exposure to it, what the company does or what a fund holds, its betas, and the events
    that move it most."""
    store = reader.store
    ticker = name_of(reader.store, nid)
    kind = pseudo(ticker_of(nid))
    view_ = portfolio_view(reader, factors=False) or {"holds": [], "holdings": None, "lookthrough": None, "shown": Shown()}
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
        kicker = "Held through " + e(series_of([name_of(reader.store, f) for f, _ in through]))
    else:
        kicker = "Name"
    head = hero(kicker, title=e(heading(ticker)), context=e(co) if co else "")
    body = []
    prices = (reader.output("price", nid) or {}).get("prices") if not kind else None
    if prices and prices.get("price") is not None:
        body.append(_quote_lines(prices))
        body.append(_price_chart(price_points(reader, "price", nid), f"{ticker} price history"))
    held = []
    if holding and holding["id"] in shown.value:
        got = rows.get(holding["id"]) or {}
        held += [("Value", num(money(shown.value[holding["id"]]))), ("Weight", num(shown.weights.get(holding["id"], "")))]
        if got.get("quantity") is not None and not kind:
            held.append(("Shares", num(count(got["quantity"]))))
        if holding["id"] in shown.moved and not kind and got.get("session"):
            held.append(("Today", num(money(shown.moved[holding["id"]], sign=True), _sign_tone(shown.moved[holding["id"]]))))
    if through:
        if not direct and len(through) == 1:
            parts = [f'through {go(through[0][0], e(name_of(reader.store, through[0][0])), "go name")}']
        else:
            whole = whole_parts(([direct] if direct else []) + [n for _, n in through], total)
            ways = (["directly"] if direct else []) + \
                [f'through {go(f, e(name_of(reader.store, f)), "go name")}' for f, _ in through]
            parts = [f"{amount(n, sign=False)} {w}" for n, w in zip(whole, ways) if n]
        held.append(("Look-through exposure", num(amount(total, sign=False)), " · ".join(parts)))
    if held:
        body.append(facts(held))
    if fund and fund.get("holds"):
        body.append(_fund_holdings(reader, nid, fund))
    elif fund:
        body.append(f'<p class="aside">Not looked through: {e(fund.get("reason"))}.</p>')
    elif not kind:
        body.append(_about(reader, nid))
    betas = reader.output("betas", "portfolio") or {}
    b = ((betas.get("objects") or {}).get(nid) or {}).get("fields", {}).get("betas")
    fit = (betas.get("fits") or {}).get(nid) or {}
    if holding and fund and fund.get("expanded"):
        g = reader.output("g", "portfolio") or {}
        b = ((g.get("by_holding") or {}).get(holding["id"]) or {}).get("b")
    if b and kind != "cash":
        body.append(_sensitivities(reader, b, fit))
    body.append(_events_moving(reader, holding=holding["id"] if holding else None, nid=nid))
    return {"title": heading(ticker), "html": head + "".join(body) + full(reader, _basis(reader.derived(nid, "Name", ("exposure",)), "exposure"))}


def _cash_view(reader: Reader, cid: str) -> dict:
    """Cash, as the holding it is: its value and its weight. Cash in another currency moves with its rate (MATH.md 3.1),
    so it also shows that rate over time, its day change, and the events that move it most; dollar cash responds to
    nothing."""
    view_ = portfolio_view(reader, factors=False) or {"holds": [], "holdings": None, "shown": Shown()}
    holding = next((h for h in view_["holds"] if h["to"] == cid), None)
    shown, title = view_["shown"], heading(name_label(cid))
    body, held = [], []
    moves = holding is not None and _has_move(holding)
    if moves:
        pair = held_name(holding)
        prices = (reader.output("price", pair) or {}).get("prices")
        if prices and prices.get("price") is not None:
            body.append(_quote_lines(prices))
            body.append(_price_chart(price_points(reader, "price", pair), f"{title}: its rate in dollars"))
    if holding and holding["id"] in shown.value:
        held += [("Value", num(money(shown.value[holding["id"]]))), ("Weight", num(shown.weights.get(holding["id"], "")))]
        if moves and holding["id"] in shown.moved:
            held.append(("Today", num(money(shown.moved[holding["id"]], sign=True), _sign_tone(shown.moved[holding["id"]]))))
    body.append(facts(held))
    if moves:
        body.append(_events_moving(reader, holding=holding["id"], nid=held_name(holding)))
    return {"title": title, "html": hero("Holding" if holding else "Cash", title=e(title)) + "".join(body)}


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
    found = sorted((x for x in found if not nothing(x[0])), key=lambda x: -abs(x[0]))
    scale = sum(abs(x[0]) for x in found) or 1.0
    kept = [x for x in found if abs(x[0]) >= SMALL * scale]
    rows_ = [(go(h["to"], e(heading(held_as(h))), "go name"),
              [num(money(shown.value[h["id"]])), num(sens(b)), num(amount(part, sign=False))]) for part, b, h in kept]
    rest = len(found) - len(kept)
    more = f'<p class="aside">{rest} smaller not shown</p>' if rest else ""
    return '<h3 class="sub">By holding</h3>' + _cols(("Value", "Beta", "Dollar beta"), rows_) + more


def _channel_view(reader: Reader, cid: str) -> dict:
    """A factor: its proxy's price over two years with its largest one-month move marked; the portfolio's dollar beta
    and, for the market, its beta; that move, and what it comes to at the dollar beta; the contribution by holding;
    and how its return is measured."""
    store = reader.store
    stated = store.stated_of(cid)
    name = heading(stated.get("name") or cid)
    try:
        listed = channel_reach(store)["factors"] or []
    except (StoreError, Unavailable, Waiting):
        listed = []
    found = next((c for c in listed if c["factor"] == cid), {})
    first = bool(listed) and listed[0]["factor"] == cid
    v = reader.value("orthogonalize", cid)
    orth = (v.output if v else None) or {}
    proxy = stated.get("proxy")
    mid = schema.proxy_metric_id(proxy)
    head = hero("Factor", title=e(name), context=f"Proxy {go(mid, e(proxy))}")
    body = []
    proxied = reader.output("proxy_price", mid) or {}
    if proxied.get("level") is not None:
        body.append(_quote_lines({"price": proxied["level"], "session": proxied.get("asof"),
                                  "time": proxied.get("time"), "from": proxied.get("from"),
                                  "previous_session": proxied.get("previous_session"),
                                  "session_change": proxied.get("session_change"),
                                  "session_move": proxied.get("session_move")}))
    month = orth.get("largest_month_sessions")
    mark = {"from": month["start"], "to": month["end"], "label": "Largest month"} if month else None
    body.append(_price_chart(price_points(reader, "proxy_price", mid), f"{proxy} price history", mark, "2Y"))
    held = []
    yield_ = found.get("per_point") is not None  # a factor quoted as the yield its proxy pays (MATH.md 3.2)
    if found.get("dollar_beta") is not None:
        held.append(("Dollar beta", num(amount(found["dollar_beta"], sign=False))))
        if yield_:
            held.append(("Per point of yield", num(f'{amount(found["per_point"], sign=False)}')))
        if first and found.get("beta") is not None:
            held.append(("Portfolio beta", num(sens(found["beta"]))))  # the portfolio's, never the index's own
    if found.get("largest_month") is not None and month:
        span = f'{day(month["start"], year=month["start"][:4] != month["end"][:4])} – {day(month["end"])}'
        said = (factor_reach(store, cid).get("largest_month") or {}).get("move") if yield_ else month["move"]
        shown = points(said) if yield_ else move(said)
        held.append(("Largest month" if first else "Largest month, net", num(shown), span))
        held.append(("Largest month applied", num(amount(found["largest_month_applied"], sign=False))))
    body.append(facts(held))
    view_ = portfolio_view(reader, factors=False)
    if view_ and found.get("dollar_beta") is not None:
        body.append(_carriers(reader, view_, cid))
    if orth.get("coefficients"):  # what it is net of: each factor above it, at its coefficient (MATH.md 3.2)
        names = channel_names(store)
        body.append('<h3 class="sub">Net of</h3>' + _cols(("Coefficient",), [
            (go(c, e(heading(names.get(c, c))), "go name"), [num(e(sens(b)))]) for c, b in orth["coefficients"].items()]))
    return {"title": name, "html": head + "".join(body) + full(reader, v.basis if v else None)}


# ---------------------------------------------------------------- a page, and what rests on it


def _resting(reader: Reader, sid: str) -> list[dict]:
    """Each statement that rests on a page (ONTOLOGY.md 2): each fact that cites it, each assumption whose basis quotes it,
    and each proposition it is evidence for, as the page names each: what it is (`kind`), what it is about (`what`),
    its figure where it has one, the passages it quotes from the page, and what it opens."""
    store = reader.store
    out = []
    for cite in store.links_of(sid, "cites", "in"):
        owner, field_ = cite["from"], cite["field"]
        link = store.link_by_id(owner) if store.type_of(owner) == "link" else None
        kind = link["relation"] if link else store.type_of(owner)
        stated = store.stated_of(owner) or {}
        v = schema.assumptions(kind, stated).get(field_)
        if v is None:  # a fact stated with its page alone, as a factor's yield and its proxy's duration
            v = {"value": stated.get(field_)}
        if isinstance(v, dict) and v.get("source_id") not in (None, sid):
            continue
        out.append({**_statement(reader, owner, kind, field_, v, link),
                    "passages": list(v.get("passages") or []) if isinstance(v, dict) else []})
    for link in store.links_of(sid, "evidence", "in"):
        out.append({"kind": "Evidence", "what": title_of(store, link["from"]), "figure": "", "opens": link["from"],
                    "passages": list(link["stated"].get("passages") or [])})
    return out


def _statement(reader: Reader, owner: str, kind: str, field_: str, v: dict, link: dict | None) -> dict:
    """One statement that rests on a page, by its kind and the field that cites it, in the words the page names it by
    elsewhere: a chance with what it is given, a shock on its price in its outcome, a value stated, the weight today's
    prices carry, a past episode, a market whose price is not used, a metric's level, a factor's yield or its proxy's
    duration."""
    store = reader.store
    x = v.get("value") if isinstance(v, dict) else v
    if kind == "Proposition" and field_.startswith("chances"):
        cond = [*(quote(title_of(store, g)) for g in v.get("given") or []),
                *(f"not {quote(title_of(store, g))}" for g in v.get("given_not") or [])]
        figure = chance(x) if isinstance(x, (int, float)) else ""
        return {"kind": "Chance", "what": title_of(store, owner), "sub": f"given {' and '.join(cond)}" if cond else "",
                "figure": figure, "opens": f"x:p:{owner}"}
    if kind == "Proposition" and field_ == "priced_in":
        figure = chance(x) if isinstance(x, (int, float)) else ""
        return {"kind": "Priced in", "what": title_of(store, owner), "figure": figure, "opens": owner}
    if kind == "Proposition" and field_ == "episode" and isinstance(x, dict):
        return {"kind": "Past episode", "what": title_of(store, owner),
                "figure": f'{day(x.get("start"))} \u2013 {day(x.get("end"))}', "opens": owner}
    if kind == "consequence" and link:
        return {"kind": "Shock", "what": target_label(store, link["to"]), "sub": title_of(store, link["from"]),
                "figure": _figure_short(reader, link), "opens": f"x:consequence:{link['id']}"}
    if kind == "worth" and link:
        world = (link.get("stated") or {}).get("world")
        on = title_of(store, link["from"]) + (f" \u00b7 {world.title()}" if world else "")
        return {"kind": "Value stated", "what": name_of(store, link["to"]), "sub": on,
                "figure": _worth_said(store, x if isinstance(x, dict) else {}), "opens": link["id"]}
    if kind == "asks" and link:
        return {"kind": "Price not used", "what": market_question(reader, link["to"]) or link["to"], "figure": "",
                "opens": link["to"]}
    if kind == "Metric" and field_ == "level":
        return {"kind": "Level", "what": heading(metric_label(store, owner)),
                "figure": level(x, (store.stated_of(owner) or {}).get("unit")), "opens": owner}
    if kind == "Factor" and field_ == "yield":
        return {"kind": "Yield", "what": heading(label(store, owner) or owner), "figure": level(x, "%"),
                "opens": owner}
    if kind == "Factor" and field_ == "duration":
        proxy = (store.stated_of(owner) or {}).get("proxy") or ""
        return {"kind": "Duration", "what": proxy, "sub": heading(label(store, owner) or owner),
                "figure": f"{x:g} years" if isinstance(x, (int, float)) else "", "opens": owner}
    return {"kind": heading(schema.field_of(field_)), "what": label(store, owner) or owner, "figure": "", "opens": owner}


def _worth_said(store: Store, value: dict) -> str:
    """A value stated per share, in its own terms (MATH.md 1.6): an amount today, cash on a date, shares of another
    name, or its price on an earlier date with its return since."""
    said = []
    if "amount" in value:
        said.append(money(value["amount"]))
    if "cash" in value:
        said.append(f'{level(value["cash"], value.get("currency", "USD"))} on {day(value.get("date"))}')
    if value.get("shares"):
        said.append(f'{value["shares"]["quantity"]:g} {name_of(store, value["shares"]["name"])}')
    if "return" in value:
        said.append(f'{move(value["return"])} since {day(value.get("date"))}')
    return " + ".join(said)


def _passage_hash(passage: str) -> str:
    return hashlib.sha256(passage.encode()).hexdigest()[:12]


def _words_at(text: str) -> tuple[str, list[int]]:
    """A text's words as the store finds a passage in them (store.words: without markdown's image, link and emphasis
    markup, single-spaced), with where each of their characters stands in the text, so that a passage found in the
    words is found in the page as it is written."""
    chars = [(c, i) for i, c in enumerate(text)]

    def sub(chars: list[tuple[str, int]], rx: re.Pattern, keep) -> list[tuple[str, int]]:
        s = "".join(c for c, _ in chars)
        out, last = [], 0
        for m in rx.finditer(s):
            out += chars[last:m.start()] + keep(m, chars)
            last = m.end()
        return out + chars[last:]

    chars = sub(chars, _IMAGE, lambda m, ch: [(" ", ch[m.start()][1])])
    chars = sub(chars, _LINK, lambda m, ch: ch[m.start(1):m.end(1)])
    chars = sub(chars, _EMPHASIS, lambda m, ch: [])
    out: list[tuple[str, int]] = []
    for c, i in chars:
        if not c.isspace():
            out.append((c, i))
        elif out and out[-1][0] != " ":
            out.append((" ", i))
    if out and out[-1][0] == " ":
        out.pop()
    return "".join(c for c, _ in out), [i for _, i in out]


QUOTED, HERE = ("\ue000", "\ue001"), ("\ue002", "\ue003")  # where a quoted passage begins and ends in a page's text


def _page_text(text: str, url: str | None, passages: Iterable[str], here: str | None = None) -> str:
    """A page's text as its reader meets it: its markdown set as the analyst's words are, after the front matter its
    fetch opens with, every passage quoted from it marked where the store finds it, and the one opened (`here`)
    marked as such. Each passage's place is found as the store checks it is quoted word for word (store.words), and
    held in the text by a pair of characters no page writes, which the page's markdown renders as marks; a link that
    is relative to the page is to the page's address."""
    page = page_body(text)
    said, at = _words_at(page)
    cuts = []
    for passage in dict.fromkeys(passages):
        sought = page_words(passage)
        k = said.find(sought) if sought else -1
        if k < 0:
            continue
        start, end = HERE if passage == here else QUOTED
        cuts += [(at[k], 1, start), (at[k + len(sought) - 1] + 1, 0, end)]
    pieces, last = [], 0
    for pos, _, mark in sorted(cuts):  # where one passage ends as another begins, the one ends first
        pieces += [page[last:pos], mark]
        last = pos
    marked = "".join(pieces) + page[last:]
    return f'<div class="page md" data-md="{e(marked)}" data-base="{e(url or "")}"></div>'


def _front_title(text: str) -> str | None:
    """The page's title, as its fetch records it in the front matter it opens with, where it has one."""
    front = _FRONT.match(text or "")
    if not front:
        return None
    line = next((x for x in front.group(0).splitlines() if x.startswith("title:")), None)
    return line.split(":", 1)[1].strip().strip('"') or None if line else None


def _as_read(passage: str) -> str:
    """A passage as its page reads, in the page's words: without the markdown its text writes links, emphasis and
    escapes in, the words by which the store finds it (store.words)."""
    return as_written(page_words(passage))


def _page_head(src: dict, kicker: str = "Source") -> str:
    """A page's head: its title, where its fetch records one, else its site; where it was read, opening the page
    itself: its site under a title, its address under a site; the date it speaks as of, where one is stated; and when
    it was read."""
    site, title = host(src.get("url")), _front_title(src.get("text") or "")
    where = site if title else re.sub(r"^https?://(www\.)?", "", src.get("url") or "")
    said = [f'<a href="{e(src.get("url"))}" target="_blank" rel="noopener">{e(where)}</a>']
    if src.get("dated"):
        said.append(f"dated {day(src['dated'])}")
    if src.get("retrieved_at"):
        said.append(f"read {day(src['retrieved_at'])}")
    return hero(kicker, title=title_html(title or site), context=" \u00b7 ".join(said))


def _inspect_passage(reader: Reader, rest: str) -> dict:
    """A page's text, one step from its page (`x:passage:{src}:`), or opened at one passage quoted from it
    (`x:passage:{src}:{hash}`): every passage quoted from it marked, the one opened marked as such."""
    store = reader.store
    sid, _, h = rest.rpartition(":")
    src = store.source_row(sid)
    if src is None:
        return _missing()
    quoted = [p for s in _resting(reader, sid) for p in s["passages"]]
    here = next((x for x in quoted if _passage_hash(x) == h), None) if h else None
    if h and here is None:
        return _missing()
    text = _page_text(src.get("text") or "", src.get("url"), quoted, here)
    title = "Passage" if here else "Full text"
    return {"title": title, "html": _page_head(src, title) + text + full(reader, sid)}


def _missing() -> dict:
    return {"title": "Not found", "html": '<p class="why">This isn\'t in the conversation.</p>'}


def _how_set_there(store: Store, how: dict) -> str:
    """How an outcome sets a price (MATH.md 1.3): by a shock of its own, by one taken from the outcome above it, or
    moving as it ordinarily would with the factors that are set there."""
    said = AGAINST_WORDS[schema.against(how.get("stated") or {})]
    if how.get("by") == "own":
        return f"its own {said}"
    if how.get("by") == "parent":
        return f"the {said} on {quote(_world_label(store, how.get('on') or ''))}"
    return MOVES_WITH


def _bar_axis(values: Iterable[float]) -> dict:
    """One scale for rows of dollars, from the lowest to the highest with zero on it."""
    values = list(values) + [0.0]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.04 or 1.0
    if hi == lo:  # every row at zero: a scale about it, each bar drawn at nothing
        return {"lo": -pad, "hi": pad}
    return {"lo": lo - pad if lo < 0 else lo, "hi": hi + pad if hi > 0 else hi}


def _bar_row(r: dict, axis: dict) -> str:
    """A row of dollars, as P&L by outcome draws one: a name, a bar from a common zero in the colour of a gain or a
    loss, and its figure. A row marked `under` is a part of the row above it, set in from it."""
    left, right = sorted((0.0, r["x"]))
    tone = _tone_whole(r["x"])
    seg = (f'<i class="po-seg {tone}" style="left:{_at(axis, left)};right:calc(100% - {_at(axis, right)})"></i>'
           if r["shown"] != amount(0.0) else "")
    sub = f'<span class="sz-s" title="{e(r["sub"])}">{e(r["sub"])}</span>' if r.get("sub") else ""
    figure = gonum(r["go"], r["shown"], tone) if r.get("go") else num(e(r["shown"]), tone)
    return (f'<div class="hb-row{" under" if r.get("under") else ""}"><div class="sz-lab"><span class="sz-t">'
            f'{r["label"]}</span>{sub}</div><div class="po-track"><i class="po-zero" style="left:{_at(axis, 0.0)}">'
            f'</i>{seg}</div><div class="sz-v">{figure}</div></div>')


def _bar_rows(rows: list[dict]) -> str:
    axis = _bar_axis(r["x"] for r in rows)
    return "".join(_bar_row(r, axis) for r in rows)


NAMES_SHOWN = 3  # a fund's companies shown under it in an outcome, largest first; the others fold under one line


def _holding_rows(reader: Reader, pid: str, found: list[dict], at, whole: float) -> list[dict]:
    """Where an outcome's P&L lands (MATH.md 1.6, 1.7): each holding's P&L there, largest first, in whole dollars that
    add to the outcome's as shown; under a fund the look-through opens, the companies it carries, each its exposure
    times its placed move, and what is left of each fund's move, its remainder, named by its fund, adding to the
    holding's figure as shown. The largest companies show, and the others fold under one line that carries what they
    come to, so the rows under a holding add to it whether the fold is open or not. Every figure under a holding opens
    the holding's working. `at` reads an entry's figure in the outcome: a holding's, a name's or a remainder's."""
    store = reader.store
    held = [(h, at(h)) for h in found if at(h) is not None]
    shown = apportion([v for _, v in held], whole)
    rows = []
    for (h, v), dollars in sorted(zip(held, shown), key=lambda x: -abs(x[0][1])):
        if dollars == 0:
            continue
        opens = f"x:holding:{pid}:{h['holding']}"
        rows.append({"label": go(opens, e(_holding_label(reader, h)), "go name"), "x": v, "shown": amount(dollars),
                     "go": opens})
        names = [n for n in h.get("names") or [] if at(n) is not None]
        rest = [x for x in h.get("remainders") or [] if at(x) is not None]
        if not rest and len(names) <= 1:  # a holding that is its own company, or a fund held whole
            continue
        parts = apportion([at(n) for n in names] + [at(x) for x in rest], dollars)
        named = sorted(((n, d) for n, d in zip(names, parts) if d), key=lambda x: -abs(at(x[0])))
        under = [{"label": go(n["name"], e(name_of(store, n["name"])), "go name"), "x": at(n), "d": d,
                  "shown": amount(d), "go": opens, "under": True} for n, d in named]
        rows += under[:NAMES_SHOWN]
        more = under[NAMES_SHOWN:]
        if more:  # the companies past the largest, under one line carrying what they come to as shown
            rows.append({"label": f"{len(more)} more", "x": sum(m["x"] for m in more),
                         "shown": amount(sum(m["d"] for m in more)), "under": True, "more": more})
        rows += [{"label": e(f"Rest of {name_of(store, x['fund'])}"), "x": at(x), "shown": amount(d), "go": opens,
                  "under": True} for x, d in zip(rest, parts[len(names):]) if d]
    return rows


def _holdings_section(rows: list[dict]) -> str:
    """The holdings' rows on one scale; a fund's further companies folded under the line that carries them."""
    axis = _bar_axis([r["x"] for r in rows] + [m["x"] for r in rows for m in r.get("more", [])])
    out = []
    for r in rows:
        if "more" in r:
            inner = "".join(_bar_row(m, axis) for m in r["more"])
            out.append(f'<details class="hb-more"><summary>{_bar_row(r, axis)}</summary>{inner}</details>')
        else:
            out.append(_bar_row(r, axis))
    return "".join(out)


def _inspect_outcome(reader: Reader, rest: str) -> dict:
    """An outcome, opened from the question it answers (MATH.md 1.1, 1.3, 1.5 to 1.8): what the portfolio does there,
    and what moves it. Its P&L against today, opening what moved it; its probability, with the weight today's prices
    carry and the mean on the event's date where either differs from it as shown; where the P&L lands, each holding
    on one scale with the names each fund carries and its rest, adding to the P&L as shown; then each move there
    against the baseline, a shock the analyst states there or takes from the outcome above it, or a move with the
    stated ones, with the level of each price a shock sets. An outcome with ways shows its ways first."""
    store = reader.store
    kind, n, w = (rest.split(":", 2) + ["", ""])[:3]  # the event's id, then the outcome's: yes, no, or a way's
    pid = f"{kind}:{n}"
    r = row(store, pid)
    tree = r.get("outcomes") or []
    o = next((x for x in tree if x["id"] == w), None)
    if o is None or o.get("A") is None:
        return _missing()
    cid = (r.get("basis") or {}).get("full_impact")
    rec = reader.computation(cid)
    out = rec.get("output") or {}
    world = bool(o.get("world"))
    parts = world and (out.get("parts") or {}).get(w)
    tone = _tone_whole(o["A"])
    figure = gonum(f"x:parts:{pid}:{w}", amount(o["A"]), tone) if parts else num(e(amount(o["A"])), tone)
    tag = ' <span class="otag">Baseline</span>' if o.get("baseline") else ""
    head = hero("Outcome", figure=figure, title=e(o["title"]) + tag, context=go(pid, e(title_of(store, pid))),
                under=_from_value(store))
    # its probability opens its derivation: a side's, the event's; a way's, its own
    facts = [("Probability", gonum(f"x:p:{w if w.startswith('prop:') else pid}", chance(o["p"])))]
    if o.get("q") is not None and chance(o["q"]) != chance(o["p"]):  # today's prices carry it at another weight
        facts.append(("Priced in", num(chance(o["q"]))))
    dist = _distribution_of(reader, r)[0]
    mean = ((dist.get("worlds") or {}).get(w) or {}).get("mean")
    if mean is not None and amount(mean) != amount(o["A"]):  # a holding not in proportion to a price
        facts.append((f"Mean on {day(dist.get('drawn_on'), year=False)}", num(amount(mean), _tone_whole(mean))))
    body = ['<div class="ofacts">' + "".join(f'<span class="of"><span class="lab">{e(k)}</span>{v}</span>'
                                             for k, v in facts) + "</div>"]
    if not world:
        ways = [x for x in tree if x.get("parent") == o["id"] and x.get("A") is not None]
        body.append('<h3 class="sub">Ways</h3><div class="hb">' + _bar_rows(
            [{"label": go(f"x:outcome:{pid}:{x['id']}", e(x["title"]), "go name"), "sub": f"Probability {chance(x['p'])}",
              "x": x["A"], "shown": amount(x["A"]), "go": f"x:outcome:{pid}:{x['id']}"} for x in ways]) + "</div>")

    def at(entry: dict) -> float | None:  # an entry's figure in this outcome: a world's, a side's, or a way's with ways
        if world:
            return (entry.get("worlds") or {}).get(w)
        return entry.get(w) if w in ("yes", "no") else (entry.get("outcomes") or {}).get(w)

    holdings = _holding_rows(reader, pid, out.get("by_holding") or [], at, o["A"])
    if holdings:
        body.append(f'<h3 class="sub">Holdings</h3><div class="hb">{_holdings_section(holdings)}</div>')
    if world and not o.get("baseline"):
        body.append(_outcome_moves(reader, pid, w))
    return {"title": o["title"], "html": head + "".join(body) + full(reader, cid), "wide": True}


def _outcome_moves(reader: Reader, pid: str, w: str) -> str:
    """Each price's move in a world against the baseline (MATH.md 1.3, TOOLS.md 5), the prices the analyst states there first:
    each a shock stated on the world or taken from the outcome above it, opening its shock, or a move with the stated
    ones, quieter; with the level there of each price a shock sets."""
    store, now = reader.store, _now(reader, pid)
    levels = now.get("levels") or {}
    rows: list[tuple[bool, str]] = []
    for item in (now.get("factors") or []) + (now.get("names") or []):
        how = (item.get("worlds") or {}).get(w)
        if not how:  # a price that does not move in this world against the baseline has no entry there
            continue
        target = item.get("factor") or item.get("name")
        label_ = item.get("name") if item.get("factor") else name_of(store, target)
        stated = how.get("by") in ("own", "parent", "worth")
        opens = ("x:consequence:" + how["consequence"]) if how.get("consequence") else how.get("worth") or target
        on = how.get("on") or ""  # a shock taken from the outcome above: the event's, or a way's by its title
        sub = (f'<span class="sub">From {"the event" if on == pid else e(_world_label(store, on))}</span>'
               if how.get("by") == "parent" else "")
        got = levels.get(target) or {}
        there = (got.get("yields") or {}).get(w) if got.get("yields") else (got.get("worlds") or {}).get(w)
        unit = "%" if got.get("yields") else unit_of(store, price_metric(store, target))
        placed = ("x:level:" + how["consequence"]) if how.get("consequence") else how.get("worth")  # how it is placed
        level_ = "" if there is None else gonum(placed, level(there, unit)) if placed else num(e(level(there, unit)))
        proxy = (store.stated_of(target) or {}).get("proxy") if item.get("factor") and not got.get("yields") else None
        if level_ and proxy:  # a factor's level is its proxy's price, named as such
            level_ = f'<span class="lv-of">{e(proxy)}</span>{level_}'
        move_ = gonum(opens, factor_move(item, how.get("move"))) if stated else num(e(factor_move(item, how.get("move"))))
        rows.append((stated, f'<tr><th>{go(target, e(heading(label_ or target)), "go name")}{sub}</th>'
                             f'<td class="n {"st" if stated else "im"}">{move_}</td><td class="n">{level_}</td></tr>'))
    if not rows:
        return ""
    kinds = {k for k, _ in rows}
    key = (f'<p class="aside"><span class="k-st">Shock</span> \u00b7 <span class="k-im">{e(heading(MOVES_WITH))}</span></p>'
           if kinds == {True, False} else "")
    ordered = [x for k, x in rows if k] + [x for k, x in rows if not k]
    return ('<h3 class="sub">Prices</h3><table class="cmp mv"><thead><tr><th></th>'
            f'<th class="n">vs baseline</th><th class="n">Level</th></tr></thead><tbody>{"".join(ordered)}</tbody></table>'
            f'{key}')


def _inspect_parts(reader: Reader, rest: str) -> dict:
    """What moves an outcome's P&L (MATH.md 1.6), against today: each factor's dollar beta and its placed move there, each
    stated company's exposure and its placed move beyond its response, a held contract, and the rest, what compounding
    and a holding's terms add; in whole dollars that add to the P&L as shown, each move a percent to two decimals;
    every value exact in the full details."""
    store = reader.store
    kind, n, w = (rest.split(":", 2) + ["", ""])[:3]
    pid = f"{kind}:{n}"
    r = row(store, pid)
    o = next((x for x in r.get("outcomes") or [] if x["id"] == w), None)
    if o is None or o.get("A") is None:
        return _missing()
    cid = (r.get("basis") or {}).get("full_impact")
    rec = reader.computation(cid)
    out, g = rec.get("output") or {}, (rec.get("inputs") or {}).get("g") or {}
    tone = _tone_whole(o["A"])
    head = hero("P&amp;L", figure=num(e(amount(o["A"])), tone), title=e(o["title"]), context=go(pid, e(title_of(store, pid))),
                under=_from_value(store))
    got = (out.get("parts") or {}).get(w)
    if not got:
        return {"title": "P&L", "html": head + full(reader, cid)}
    entries = _what_moves(reader, pid, out, g, w, got["factors"], got["own"], got.get("curve") or 0.0,
                          (out.get("components") or {}).get(w) or {})
    return {"title": "P&L", "html": head + _moves_table(entries, o["A"], "P&L") + full(reader, cid)}


INSPECT = {"outcome": _inspect_outcome, "p": _inspect_point, "interval": _inspect_interval, "price": _inspect_price,
           "consequence": _inspect_consequence, "swing": _inspect_swing, "volatility": _inspect_volatility,
           "implied": _inspect_implied, "window": _inspect_window, "shock": _inspect_shock, "level": _inspect_level,
           "parts": _inspect_parts,
           "portfolio_volatility": _inspect_portfolio_volatility,
           "holding": _inspect_holding, "passage": _inspect_passage}


# ---------------------------------------------------------------- the other objects, opened on the right


def _market_view(reader: Reader, mid: str) -> dict:
    """A market: its question; its book, midpoint and depth; when it closes and what settles it; the contracts of it
    the portfolio holds; the propositions it asks; the analyst's rationale about it; and its rules."""
    store = reader.store
    listing = reader.output("market_listing", mid) or {}
    price = reader.output("market_price", mid) or {}
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    q = as_asked(listing.get("question")) or mid
    status = pill("Open", "teal") if price.get("open") else pill("Closed") if price.get("open") is False else ""
    head = hero(f"Market · {venue}", title=e(q), pills=[status])
    source = listing.get("resolution_source") or ""
    shown_source = (f'<a href="{e(source)}" target="_blank" rel="noopener">{e(host(source))}</a>'
                    if source.startswith("http") else e(source))
    body = [facts([("Midpoint", gonum("x:price:" + mid, cents(price["price"])) if price.get("price") is not None else ""),
                   ("Book", f'<a class="go" data-go="{e("x:price:" + mid)}">{quotes(price.get("bid"), price.get("ask"), html=True)}</a>'
                    if price.get("bid") is not None or price.get("ask") is not None else ""),
                   ("Depth", num(amount(price["depth"], sign=False)) if price.get("depth") is not None else ""),
                   ("Closes", e(day(listing.get("close_time"))) if listing.get("close_time") else ""),
                   ("Settled by", shown_source)])]
    held = [h for h in store.links_of("portfolio", "holds") if h["to"] == mid]
    if held:
        positions = (reader.output("holdings", "portfolio") or {}).get("holds") or {}
        body.append('<h3 class="sub">Held</h3>' + _cols(("Contracts", "Value"), [
            (e(h["stated"]["side"].title()), [num(count(h["stated"]["quantity"])),
                                              num(money((positions.get(h["id"]) or {}).get("dollars")))]) for h in held]))
    asks = store.links_of(mid, "asks", "in")
    if asks:
        items = "".join(f'<li>{go(link["from"], e(title_of(store, link["from"])))}'
                        f'{" · Through its No side" if link["stated"].get("side") == "no" else ""}</li>'
                        for link in asks)
        body.append(f'<h3 class="sub">Asks</h3><ul class="plainlist asks">{items}</ul>')
    notes = [store.stated_of(a["from"]).get("text") for a in store.links_of(mid, "about", "in")]
    if notes:
        body.append('<h3 class="sub">Rationale</h3>' + "".join(f'<div class="basis note">{fold(note_html(t))}</div>' for t in notes))
    if listing.get("rules"):
        body.append(f'<details class="rules"><summary>Rules</summary>{paragraphs(listing["rules"])}</details>')
    return {"title": q, "html": head + "".join(body) + full(reader, mid)}


def _metric_view(reader: Reader, mid: str) -> dict:
    store = reader.store
    unit = unit_of(store, mid)
    price = bool(schema.PROXY_METRIC_RE.match(mid))
    head = hero("Price" if price else "Level", title=e(heading(metric_label(store, mid))))
    held = [("Today", _today(reader, mid, unit))]
    if not price:
        stated = store.stated_of(mid)
        if stated.get("resolution_source"):
            held.append(("Source", e(stated["resolution_source"])))
    for q in store.links_of(mid, "quotes"):  # the factor whose price it is, in its own unit
        held.append(("Factor", go(q["to"], e(heading(target_label(store, q["to"]))))))
    body = [facts(held)]
    return {"title": heading(metric_label(store, mid)), "html": head + "".join(body) + full(reader, mid)}


def _proposition_view(reader: Reader, pid: str) -> dict:
    """A proposition the event reaches: what it says, its forecast where one is worked out, the market-implied range,
    the markets that ask it, and the analyst's rationale."""
    store = reader.store
    stated = store.stated_of(pid)
    d = reader.derived(pid, "Proposition", READING)
    p, lo, hi = _value(d, "probability"), (_value(d, "range") or [None, None])[0], (_value(d, "range") or [None, None])[1]
    if p is not None:  # its forecast on the scale every forecast is drawn on, opening how it is set
        scale = _scale(pid, p, lo, hi) + f'<p class="raw">{go("x:p:" + pid, "How it\u2019s set", "rawlink")}</p>'
    elif _span(_value(d, "left_open")):  # the range its statements leave it
        scale = facts([("Forecast", gonum("x:p:" + pid, _span(_value(d, "left_open"))))])
    else:
        scale = ""
    head = (hero("Proposition", title=title_html(stated.get("title")))
            + f'<p class="text">{e(stated.get("text"))}</p>'
            + _when_known(stated, store.meta("asof")))
    body = [scale]
    markets = "".join(_market_item(reader, link) for link in store.links_of(pid, "asks"))
    if markets:
        body.append(f'<h3 class="sub">Markets</h3><ul class="mkts">{markets}</ul>')
    notes = [f'<div class="basis note">{fold(note_html(store.stated_of(a["from"]).get("text")))}</div>'
             for a in store.links_of(pid, "about", "in")]
    if notes:
        body.append('<h3 class="sub">Rationale</h3>' + "".join(notes))
    return {"title": stated.get("title") or pid, "html": head + "".join(body) + full(reader, pid)}


def _source_view(reader: Reader, sid: str) -> dict:
    """A page, opened from the question it answers: what did it say that the figures rest on (ONTOLOGY.md 2). The page
    first, its title, its site, its date where one is stated, and when it was read; then each statement that rests on
    it, as the page names it, with its figure, and under it each passage it quotes, opening the page's text there;
    then the page's text whole, one step further."""
    src = reader.store.source_row(sid) or {}
    items = []
    for s in _resting(reader, sid):
        figure = f'<span class="rs-v">{gonum(s["opens"], s["figure"])}</span>' if s["figure"] else ""
        quoted = "".join(f'<blockquote class="q">{go(_passage_target(sid, p), e(_as_read(p)), "go quote")}</blockquote>'
                         for p in s["passages"])
        sub = f'<span class="rs-s">{e(s["sub"])}</span>' if s.get("sub") else ""
        items.append(f'<div class="rs"><p class="rs-k">{e(s["kind"])}</p><div class="rs-h"><span class="rs-t">'
                     f'{go(s["opens"], title_html(s["what"]), "go name")}{sub}</span>{figure}</div>{quoted}</div>')
    body = ('<h3 class="sub">Cited by</h3><div class="rests">' + "".join(items) + "</div>") if items else ""
    text = f'<p class="raw">{go(f"x:passage:{sid}:", "Full text", "rawlink")}</p>'
    return {"title": _front_title(src.get("text") or "") or host(src.get("url")),
            "html": _page_head(src) + body + text + full(reader, sid)}


def _from_value(store: Store) -> str:
    """What an outcome's P&L is measured from (MATH.md 1.1): the portfolio's value on its as-of."""
    asof = store.meta("asof")
    return f"From the portfolio’s value on {e(day(asof))}" if asof else ""


def _note_view(reader: Reader, nid: str) -> dict:
    store = reader.store
    about = [go(link["to"], e(heading(label(store, link["to"]) or link["to"]))) for link in store.links_of(nid, "about")]
    head = hero("Rationale", context=" \u00b7 ".join(about))
    body = f'<div class="note">{note_html(store.stated_of(nid).get("text"))}</div>'
    return {"title": "Rationale", "html": head + body + full(reader, nid)}


def _value_view(reader: Reader, address: str) -> dict:
    """A value's working as it is now (`raw:value:{derivation}:{subject}`): its Computation, read through the engine,
    so a step left open follows the value as it is worked out again. While it is being worked out, the step says so,
    and it is read again when the store changes."""
    derivation, _, subject = address.partition(":")  # a derivation's name has no colon; a subject can have several
    try:
        got = reader.store.current_computation(derivation, subject)
    except (StoreError, Unavailable, Waiting, RuntimeError, KeyError):
        got = None
    if got is not None:
        return _record_view(reader, got)
    if reader.waits(derivation, subject):
        return {"title": "Full details",
                "html": hero("Full details", title="Calculation") + f'<p class="later">{WAIT}<span>Working it out</span></p>'}
    return _missing()


def _raw_view(reader: Reader, rid: str) -> dict:
    got = reader.get(rid)
    if got is None:
        return _missing()
    return _record_view(reader, got)


def _record_view(reader: Reader, got: dict) -> dict:
    rid, kind = got.get("id"), got.get("type")
    what = {"Computation": "Calculation"}.get(
        kind, label(reader.store, rid) or "Everything about it")
    return {"title": "Full details", "html": hero("Full details", title=e(what)) + _record(got, reader)}


RECORD_ID = re.compile(r"&quot;(cmp:\d+)&quot;")


def _record(obj: Any, reader: Reader | None = None) -> str:
    """A record whole, as the store keeps it; each Computation it names opens its own record, so what a value was
    worked out from is one step further."""
    text = e(json.dumps(obj, indent=1, ensure_ascii=False, default=str))
    if reader is not None:
        own = obj.get("id") if isinstance(obj, dict) else None
        opens: dict[str, str | None] = {}

        def linked(m: re.Match) -> str:
            rid = m.group(1)
            if rid not in opens:
                opens[rid] = None if rid == own else working(reader, rid)
            return f'&quot;{go(opens[rid], rid, "go rec")}&quot;' if opens[rid] else m.group(0)
        text = RECORD_ID.sub(linked, text)
    return f'<pre class="record">{text}</pre>'


def view(store: Store, target: str, *, pending: bool = False) -> dict:
    """One step of the right panel: an event, a number's explanation, or an object, with its whole record one click
    away. Whatever is opened is worked on, so each takes the main area beside the conversation (`wide`)."""
    got = _view(store, target, pending=pending)
    return {"surface": "inspector", **got, "wide": True}


def _view(store: Store, target: str, *, pending: bool = False) -> dict:
    reader = Reader(store, pending=pending)
    try:
        if target.startswith("raw:value:"):
            return _value_view(reader, target[len("raw:value:"):])
        if target.startswith("raw:"):
            return _raw_view(reader, target[len("raw:"):])
        if target == "byholding":
            return by_holding_view(reader)
        by, _, key = target.partition(":")
        if by == "weights":
            return _weights_view(reader, key)
        if by in ("industry", "country"):
            return _group_view(reader, by, target)
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
        if kind == "link" and (store.link_by_id(target) or {}).get("relation") == "worth":
            return _worth_view(reader, target)
        if kind == "Judgment":  # the judge's readings are kept in the store and the record; the page shows none
            return _missing()
        if kind == "Cash":
            return _cash_view(reader, target)
        return {"Market": _market_view, "Metric": _metric_view, "Name": _name_view, "Factor": _channel_view,
                "Source": _source_view, "Note": _note_view}.get(kind, lambda r, t: _raw_view(r, t))(reader, target)
    except (StoreError, Unavailable, Waiting):
        return _missing()


# ---------------------------------------------------------------- id chips


def _figure_short(reader: Reader, link: dict) -> str:
    """A consequence as its chip shows it: the shock the analyst states, with its sign, and, where it is measured
    against what the price follows, that."""
    try:
        _, said = _stated(link)
    except (KeyError, StopIteration, TypeError):
        return ""
    x = said.get("value") if isinstance(said, dict) else said
    if not isinstance(x, (int, float)):
        return ""
    return f"{AGAINST_WORDS[_against(link)]} {stated_shock(reader.store, link['to'], x)}"


def _shock_tone(store: Store, link: dict) -> str:
    """A consequence's colour, its stated shock's direction, as a move's is."""
    try:
        _, said = _stated(link)
    except (KeyError, StopIteration, TypeError):
        return ""
    x = said.get("value") if isinstance(said, dict) else said
    if not isinstance(x, (int, float)):
        return ""
    return _move_tone(x, stated_shock(store, link["to"], x), stated_shock(store, link["to"], 0.0))


def chips(store: Store, ids: list[str]) -> dict[str, dict]:
    """Each id the analyst writes, in a reply or a note, as its chip shows it: the object's title and its current
    value."""
    reader = Reader(store, pending=True)
    out: dict[str, dict] = {}
    reach: dict | None = None
    view_: dict | None = None
    groups: dict | None = None
    for oid in ids:
        by = oid.partition(":")[0]
        if by in ("industry", "country"):  # a group, by its id: its name and its weight, as the portfolio panel shows them
            if groups is None:
                view_ = view_ or portfolio_view(reader, factors=False) or {}
                groups = _groups_of(reader, view_["shown"].total) if view_.get("shown") else {}
                groups = groups or {}
            g = next((x for x in groups.get(by) or [] if x.got.get("id") == oid), None)
            out[oid] = {"label": g.label, "value": _tenth(g.tenths), "exists": True, "kind": "group", "title": g.label} \
                if g else {"label": oid, "value": "", "exists": False}
            continue
        kind = store.type_of(oid)
        if kind is None:
            out[oid] = {"label": oid, "value": "", "exists": False}
            continue
        label_, value, title, tone = oid, "", None, ""
        if kind == "Proposition":
            label_ = title_of(store, oid)
            d = reader.derived(oid, "Proposition", ("probability", "left_open"))
            p = _value(d, "probability")
            value = chance(p) if p is not None else _span(_value(d, "left_open"))
        elif kind == "Market":
            label_ = market_question(reader, oid) or oid
            shown = reader.output("market_price", oid) or {}
            value = quotes(shown.get("bid"), shown.get("ask")) \
                if shown.get("bid") is not None or shown.get("ask") is not None else ""
        elif kind == "Metric":
            label_ = metric_label(store, oid)
            got = reader.get(oid) or {}
            if got.get("ticker"):
                value = level(_value(got["derived"], "level"), "USD")
            else:
                s = got.get("stated") or {}
                value = level(s.get("level"), s.get("unit"))
        elif kind in ("Name", "Cash"):
            label_ = name_of(store, oid)
            if view_ is None:
                view_ = portfolio_view(reader, factors=False) or {}
            held = {h["to"]: h["id"] for h in view_.get("holds") or []}
            shown = view_.get("shown")
            if shown and held.get(oid) in shown.value:  # a holding: its value, as the portfolio panel shows it
                value = money(shown.value[held[oid]])
            elif shown and view_.get("lookthrough"):  # a name the funds hold: your exposure, as its page shows it
                total = held_of(view_, oid)[0]
                value = amount(total, sign=False) if total else ""
        elif kind == "Factor":
            label_ = heading(store.stated_of(oid).get("name") or oid)
            if reach is None:
                try:
                    reach = {c["factor"]: c for c in channel_reach(store)["factors"] or []}
                except Exception:
                    reach = {}
            exposure = (reach.get(oid) or {}).get("dollar_beta")
            value = f"dollar beta {amount(exposure, sign=False)}" if exposure is not None else ""
        elif kind == "Source":
            src = store.source_row(oid) or {}
            label_, value = host(src.get("url")), day(src.get("dated")) if src.get("dated") else ""
        elif kind == "Note":
            label_ = "Rationale"
        elif kind == "link":
            label_ = label(store, oid) or oid
            link = store.link_by_id(oid) or {}
            if link.get("relation") == "consequence":  # the price it moves, with its shock; the event is named around it
                title, label_ = label_, target_label(store, link["to"])
                value, tone = _figure_short(reader, link), _shock_tone(store, link)
            elif link.get("relation") == "worth":
                title, label_ = label_, name_of(store, link["to"])
                value = "value stated"
        elif kind == "Computation":
            label_ = label(store, oid)
        out[oid] = {"label": label_, "value": value, "exists": True, "kind": kind, "title": title or label_,
                    **({"tone": tone} if tone else {})}
    return out


# ---------------------------------------------------------------- the download


def ledger_csv(store: Store) -> str:
    """The ledger as a spreadsheet reads it (TOOLS.md 9): one line for each event, in the list's order, by volatility, with
    its figures as the page shows them, each a plain number with its unit in its column's heading: the date by which
    its outcomes are known; the forecast, the market range's ends where the markets set them, and the event's own
    market's bid and ask where its midpoint differs from the forecast; Yes, its share of the portfolio, No, the full
    impact as Yes less No, the volatility, the worst outcome with its P&L and probability, and the expected value; then
    each holding's P&L on Yes, as By holding shows it, the holdings adding to the event's Yes. A figure still being
    worked out, or one the event does not have, is an empty cell. UTF-8 with its byte-order mark, which a spreadsheet
    reads as UTF-8."""
    reader = Reader(store)
    found = _ledger(reader)
    rows_ = by_volatility(found["rows"]) if found else []
    moved, on_yes = _on_yes(store, [r for r in rows_ if r.get("yes") is not None])
    pct = lambda x, places: "" if x is None else str(Decimal(repr(x * 100)).quantize(  # noqa: E731
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    whole = lambda x: "" if x is None else str(as_shown(x))  # noqa: E731
    head = ["Event", "Known by", "Forecast (%)", "Lower bound (%)", "Upper bound (%)", "Market bid (\u00a2)",
            "Market ask (\u00a2)", "If Yes ($)", "If Yes (% of portfolio)", "If No ($)", "Full impact ($)", "Volatility ($)",
            "Worst outcome ($)", "Worst outcome", "Worst outcome probability (%)", "Expected value ($)",
            *(f"{_holding_label(reader, h)} if Yes ($)" for h in moved)]
    out = io.StringIO()
    sheet = csv.writer(out)
    sheet.writerow(head)
    for r in rows_:
        lo, hi = _range(r)
        book = _own_book(reader, r) or (None, None, None)
        yes, no = r.get("yes"), r.get("no")
        worst = r.get("worst_outcome") or {}
        sheet.writerow([
            r["title"], drawn_on(store, r["id"]) or "", pct(r.get("probability"), 1),
            pct(lo, 1) if lo is not None and lo > ZERO else "",
            pct(hi, 1) if hi is not None and hi < 1.0 - ZERO else "",
            pct(book[0], 1).removesuffix(".0"), pct(book[1], 1).removesuffix(".0"),
            whole(yes), pct(r.get("share_yes"), 2), whole(no),
            str(as_shown(yes) - as_shown(no)) if yes is not None and no is not None else "",
            whole(r.get("volatility")), whole(worst.get("pnl")),
            (worst.get("title") or _world_label(store, worst["world"])) if worst.get("pnl") is not None else "",
            pct(worst.get("probability"), 1) if worst.get("pnl") is not None else "", whole(r.get("expected")),
            *(str(on_yes[r["id"]].get(h["id"], 0)) if r["id"] in on_yes else "" for h in moved)])
    return "\ufeff" + out.getvalue()


