"""The page's one renderer: the portfolio panel, the ledger, an opened event, a holding, a factor, the inspector, the
id chips, and the ledger as markdown, each read from the store, in the reader's terms: probability, priced in, beta and
dollar beta, shock, full impact, contributors, depth, look-through exposure.

Every figure is a derived field, or an output or input of the Computation or Judgment that is its basis; nothing here
computes a number of its own. Its arithmetic is rounding, each kind of figure in one form wherever it appears, whatever
its size: money a statement shows (a holding's value, the total, a price, a day's change) to the cent; money the
system computes in whole dollars, a figure that is nothing as shown reading "–", and parts shown together apportioned
so that they add to their whole as shown; a probability, a market's price among them, a percent to one decimal; a
move, and an event's share of the portfolio, a percent to two; a weight a percent to one; a beta two decimals; a level
two decimals in its unit. The inspector's working is to the cent, each fraction in it a percent to four decimals; the
full details keep every value exact. A chart's points are a stored series: a price's daily closes to its latest mark,
or the history of the market that prices an event, one minus it where the event is the market's opposite.

A render reads the store under its lock. While the analyst works, the page renders from what the store already holds
(`cache_only`): a value that waits on the judge or a data client shows as a quiet placeholder where it will sit, and
the page never asks or fetches on the analyst's behalf. When a reply ends, the panels render once more with the judge
and the data clients. A value that does not exist yet, such as dollars before any consequence, is simply not there.
"""

from __future__ import annotations

import hashlib
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
from harness.derive.worlds import side_mean
from harness.judge import JudgeError
from harness.ledger import by_volatility, by_worst, channel_reach, ledger, row
from harness.ledger import MOVES_WITH, event_now, moves, outcomes as outcome_tree
from harness.ledger import latest_read as reading_time
from harness.ledger import factor as factor_reach
from harness.readings import prices_at
from harness.record import DEFINITION
from harness.store import Store, StoreError
from harness.venues import split
from harness.venues.markets import NY

log = logging.getLogger("harness.render")  # what the page could not link, for us: the member sees the figure

MINUS = "\u2212"
LISTS_SHOWN = 5  # each list of where the portfolio's weight is, as the portfolio panel shows it before it opens
DOWNLOAD_SHOWN = 10  # each of those lists in the download
CONSTITUENTS_SHOWN = 12  # a fund's constituents shown before "all of them"
LISTED = 15  # the rows an inspector table lists before "and N more"
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


# Each kind of figure has one form wherever it appears, whatever its size. Money a statement shows (a
# holding's value, the total, a price, a day's change) is exact to the cent (`money`); money the system computes from
# them (what an event or a factor would do, a volatility, a dollar beta, what the look-through puts in a company, a
# depth) is in whole dollars (`amount`), and is exact to the cent in the inspector's working, where it is checked. A
# figure that is nothing as shown reads "–". A probability is a percent to one decimal, a move a percent to two, a
# weight a percent to one, a beta two decimals. A chart's axis marks its places in thousands or millions ("−$40k").

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
    """A market's book as observed, by the side it is read through: "YES bid 16¢ · ask 49¢"; a side with no standing
    quote says so. As HTML, its words in the page's text and its figures in the figures' face."""
    fig = (lambda q: f'<span class="n">{cents(q)}</span>') if html else cents  # noqa: E731
    said = lambda q, word: f"{word} {fig(q)}" if q is not None else f"no {word}"  # noqa: E731
    return f"{side.upper()} {said(bid, 'bid')} \u00b7 {said(ask, 'ask')}"


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


def log_operand(x: float | None, *, sign: bool = False) -> str:
    """A compounded move, ln(1 + x), where it enters shown arithmetic: the logarithm itself to six decimals, signed,
    never as a percent, since it is no percent of anything: ln(0.9) is −0.105361."""
    if x is None:
        return ""
    r = _half_up(abs(x), "0.000001")
    return f"{_lead(x, r == 0, sign)}{r}"


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


def difference(x: float | None, unit: str | None) -> str:
    """A difference between two levels of a metric, in its unit, to two decimals: for a metric measured in percent, the
    change of the percent it is quoted in, −0.40% from 4.95% to 4.55%."""
    return level(x, unit)


def stated_shock(store: Store, target: str, x: float | None) -> str:
    """A shock as the analyst states it (MATH.md 1.3), a percent to two decimals: on a factor quoted as a yield, the
    yield's own change, which the analyst states in percentage points of it, −0.60 pp; otherwise a move, the fraction
    of the price in the baseline it states, as every move reads."""
    if x is None:
        return ""
    if quoted_as_yield(store, target):
        return points(x)
    return move(x)


def shock(x: float | None, unit: str | None) -> str:
    """A shock, the gap between where a metric ends with an event and without it: a difference with its sign,
    +$20.57, −0.25%."""
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


def note_html(text: str | None) -> str:
    """A note, in the analyst's words: the page renders its markdown as it renders a reply's, each id a chip."""
    return f'<div class="md" data-md="{e(text or "")}">{paragraphs(text)}</div>'


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
            formula: Iterable[str] = (), after: Iterable[tuple[str, str, str]] = ()) -> str:
    """Workings as a receipt: each input with what it is and its value, the result beneath a rule, what that result is
    in other terms (`after`), then the formula that makes it, in symbols and with the values filled in."""
    def row(term: str, what: str, value: str, cls: str = "") -> str:
        return f'<tr{f" class={cls}" if cls else ""}><th>{term}</th><td class="d">{what}</td><td class="v">{value}</td></tr>'
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
    if schema.PROXY_METRIC_RE.match(mid):
        return "USD"
    return store.stated_of(mid).get("unit") if store.type_of(mid) == "Metric" else None


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
    holdings, lt, shown = view_["holdings"], view_["lookthrough"], view_["shown"]
    if holdings and shown.total is not None:
        moved = ""
        if shown.total_moved is not None:
            moved = (f'<div class="dmove {_sign_tone(shown.total_moved)}">{num(dollars(shown.total_moved, sign=True))} '
                     f'({num(move(holdings.get("session_move")))})<span class="when">{_session(holdings)}</span></div>')
        head = (f'<div class="total">{num(dollars(shown.total))}</div>{moved}{_volatility_line(reader)}'
                f'<div class="asof">{_when(reader.store, holdings)}</div>')
    else:
        head = f'<div class="total">{WAIT}</div><div class="asof">{_when(reader.store, holdings)}</div>'
    rows = []
    widest = max((abs(v) for v in shown.value.values()), default=0.0) or 1.0  # the largest holding fills its bar
    for h in view_["holds"]:
        nid, hid = h["to"], h["id"]
        got = (holdings or {}).get("holds", {}).get(hid) or {}
        value = num(dollars(shown.value[hid])) if hid in shown.value else WAIT
        moved = ""
        if hid in shown.moved and _has_move(h):
            moved = (f'<span class="mv n {_sign_tone(shown.moved[hid])}">{dollars(shown.moved[hid], sign=True)} '
                     f'({move(got.get("session_move"))})</span>')
        share = shown.value.get(hid)
        bar = (f'<span class="bar" aria-hidden="true"><i style="width:{100 * abs(share) / widest:.1f}%"></i></span>'
               if share else "")
        inner = (f'{_ident(nid, _holding_label(reader, h))}<span class="val n">{value}</span>'
                 f'<span class="wt">{bar}<span class="n">{shown.weights.get(hid, "")}</span></span>{moved}')
        hover = market_question(reader, nid) if nid.startswith("market:") else _named(reader, nid)
        rows.append(f'<li data-k="{e(hid)}">{go(nid, inner, "go row hold", hover)}</li>')
    out = [f'<header class="ph"><h2>Portfolio</h2>{head}</header>',
           section("Holdings", f'<ul class="holdings">{"".join(rows)}</ul>')]
    lists = _weight_lists(reader, view_)
    if lists:
        out.append(lists)
    out.append(section("Factor exposures", _exposures(reader, view_), "exposures", "exposures"))
    return "".join(out)


def _volatility_line(reader: Reader) -> str:
    """The portfolio's volatility to the horizon's end, a size either way, opening on its workings."""
    got = reader.derived("portfolio", "Portfolio", ("volatility",)).get("volatility") or {}
    if got.get("value") is None:  # being worked out: on a reply's turn (pending), or where it waits on a read
        waiting = reader.pending or got.get("waiting") or reader.waits("volatility", "portfolio")
        return f'<div class="pvol" data-k="pvol">Horizon volatility {WAIT}</div>' if waiting else ""
    return (f'<div class="pvol" data-k="pvol">Horizon volatility '
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
    key = g.get(by)
    return f"{by}:{key}" if key else None


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
            return section("Weight", WAIT, "pending weights", "weights")  # in the place of the lists it stands for
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
            bar = f'<span class="bar" aria-hidden="true"><i style="width:{100 * abs(g.dollars) / widest:.1f}%"></i></span>'
            inner = (f'<span class="id"><span class="tk">{e(heading(short) if by == "company" else short)}</span></span>'
                     f'<span class="val n">{num(amount(g.dollars, sign=False))}</span>'
                     f'<span class="wt">{bar}<span class="n">{_tenth(g.tenths)}</span></span>'
                     + (_via(reader, view_, g) if by == "company" else ""))
            title = g.label if by == "company" else None
            rows.append(f'<li data-k="{e(g.target or g.label)}">'
                        + (go(g.target, inner, "go row hold", title) if g.target else f'<span class="row">{inner}</span>')
                        + "</li>")
        count = len(named)
        noun = {"company": "companies", "industry": "industries", "country": "countries"}[by]
        more = go(f"weights:{by}", f"All {count} {noun}" if count > LISTS_SHOWN else f"Every {word.lower()}, in full",
                  "linkish all-of")
        lists.append(f'<div class="wl wl-{by}" data-k="wl:{by}"><ul class="holdings weights">{"".join(rows)}</ul>'
                     f'<p class="more">{more}</p></div>')
    return (f'<section class="sec weights" data-k="weights"><div class="wt-head"><h3>Weight</h3>{switch}</div>'
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
            f'{", then what is not a company, by kind" if kinds else ""}.</p>')
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


def _group_view(reader: Reader, by: str, key: str) -> dict:
    """An industry or a country: its dollars, its weight of the portfolio, and how many companies it holds;
    the events that move its companies most; then the companies in it, each with its dollars and weight, apportioned
    so that they add to it, and how each is held."""
    view_ = portfolio_view(reader, factors=False)
    groups = _groups_of(reader, view_["shown"].total) if view_ else None
    group = next((g for g in (groups or {}).get(by) or [] if g.got.get(by) == key), None)
    if group is None:
        return _missing()
    store = reader.store
    inside = [g for g in groups["company"] if not g.got.get("kind") and g.got.get(by) == key]
    cents = to_cents([g.got["dollars"] for g in inside], group.dollars)
    tenths = apportion([1000 * g.got["dollars"] / sum(x.got["dollars"] for x in inside) * group.tenths / 1000
                        for g in inside], group.tenths) if inside else []
    rows = []
    for g, c, t in zip(inside, cents, tenths):
        shown = Group(g.label, g.target, c, t, g.got)
        under = [x for x in ([e(name_of(store, g.target))] if g.target else []) + [_via(reader, view_, shown)] if x]
        rows.append(([(go(g.target, e(g.label)) if g.target else e(g.label))
                      + (f'<div class="aside">{" · ".join(under)}</div>' if under else "")], shown))
    kicker = "Industry" if by == "industry" else "Country"
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
    return ('<h3 class="sub">Events</h3><table class="ifs"><thead><tr><th>Event</th><th>Yes</th><th>No</th></tr>'
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
    items = []
    for c in found:
        beta = (f'{amount(c["per_point"], sign=False)} /pp' if c.get("per_point") is not None  # a yield: per point
                else amount(c["dollar_beta"], sign=False))
        inner = (f'<span class="lbl"><span class="nm">{e(heading(c["name"]))}</span></span>'
                 f'<span class="xv"><span class="v n">{beta}</span>'
                 f'<span class="v n">{amount(c["largest_month_applied"], sign=False)}</span></span>')
        items.append(f'<li data-k="{e(c["factor"])}">{go(c["factor"], inner, "go row")}</li>')
    return ('<div class="xhead"><span class="v">Dollar beta</span><span class="v">Largest month</span></div>'
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
    lines = "".join(_line(reader, by_id[link["to"]], ranked[link["to"]], worst[link["to"]], axis)
                    for link in store.links_of("portfolio", "watches"))
    days = store.stated_of("portfolio").get("horizon_days")
    key = (f'<div class="lkey"><i aria-hidden="true"></i>Horizon volatility '
           f'{gonum("x:portfolio_volatility:portfolio", either(sigma))}</div>' if sigma else "")
    return (f'<header class="ph lh"><h2>Events</h2>{_ledger_views("list")}</header>'
            f'<div class="lcap"><span>To {day(found["horizon_end"])} · {days} days</span>{ORDERS}</div>{key}'
            '<div class="lcols"><span>Forecast<small>Market-implied range</small></span><span>Yes<small>Of portfolio</small>'
            '</span><span>No</span><span>Volatility</span></div>'
            + (_axis_head(axis) if axis else "")
            + f'<ol class="events" data-hold data-ranked>{lines}</ol>')


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
    """The axis once above the list: its round ticks labelled in thousands or millions, the first and last anchored
    to the panel's edges, Today at zero."""
    n = len(axis["ticks"])
    labels = "".join(f'<span class="{"start" if i == 0 else "end" if i == n - 1 else "mid"}" '
                     f'style="left:{_at(axis, t)}">{axis_label(t)}</span>' for i, t in enumerate(axis["ticks"]))
    marks = "".join(f'<i style="left:{_at(axis, t)}"></i>' for t in axis["ticks"])
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
    or where there is no forecast (TOOLS.md 9): "Market YES bid 78¢ · ask 84¢"."""
    m = r.get("market") or {}
    q = m.get("probability")
    if q is None or (r.get("probability") is not None and chance(q) == chance(r["probability"])):
        return ""
    book = reader.derived(m["link"], "asks")
    return f"Market {quotes(_value(book, 'bid'), _value(book, 'ask'), side_of(reader.store, m['link']))}"


def _line(reader: Reader, r: dict, i: int, j: int, axis: dict | None) -> str:
    """An event's row: its title; its forecast, Yes, No and volatility under the ledger's heads, and beneath them,
    under the heads' second line, the market-implied range beneath the forecast and Yes's share of the portfolio
    beneath Yes; the market's own price on a line of its own where it differs; its bar on the ledger's axis; and, where
    its worst outcome is a way, that way. In the worst-outcome order, the worst outcome is marked where it stands.
    Each figure is the event's where it has it: its probability, or the range its statements leave it; and while the
    analyst works, a figure being worked out shows its place; a figure it does not have is not there."""
    pid = r["id"]
    head = f'<span class="et">{title_html(r["title"])}</span>'
    said = _implied(r) if _beside_open(r) else ""  # under the head that names it
    beside = _market_words(reader, r)
    yes, no, worst = r.get("yes"), r.get("no"), r.get("worst_outcome") or {}
    w = lambda side: " w" if worst.get("world") == side else ""  # noqa: E731
    working_out = reader.waits("impact", pid)
    cell = lambda x, shown, tone: (f'<span class="{tone}">{shown}</span>' if x is not None  # noqa: E731
                                   else f"<span>{WAIT}</span>" if working_out else "<span></span>")
    p = e(_figure_p(r)) or (WAIT if reader.reading_waits(pid) else "")
    vol = r.get("volatility")
    figures = (f'<span class="ep">{p}</span>' + cell(yes, amount(yes), _tone_whole(yes) + w("yes"))
               + cell(no, amount(no), _tone_whole(no) + w("no")) + cell(vol, either(vol), ""))
    caption = share(r.get("share_yes"))
    caption_tone = _move_tone(r.get("share_yes"), caption, share(0.0))  # an event's share takes its P&L's colour
    body = ((f'<span class="ef">{figures}</span>' if p or yes is not None or working_out else "")  # the lines it has
            + (f'<span class="ec"><span class="n">{e(said)}</span><span class="n {caption_tone}">{caption}</span><span></span><span></span>'
               '</span>' if said or caption else "")
            + (f'<span class="ec em"><span>{e(beside)}</span></span>' if beside else "")
            + (_bar(r, axis) if axis and _worlds_of(r) else ""))
    if worst.get("world") not in (None, "yes", "no"):  # a way: the one outcome the row does not otherwise show
        body += (f'<span class="eway">Worst outcome <span class="n {_tone_whole(worst["pnl"])} w">'
                 f'{amount(worst["pnl"])}</span> \u00b7 {e(worst.get("title") or worst["world"])}, '
                 f'{chance(worst["probability"])}</span>')
    return (f'<li class="event" data-k="{e(pid)}" data-volatility="{i}" data-worst="{j}">'
            f'<a class="evline" data-go="{e(pid)}">{head}{body}</a></li>')


def by_holding_view(reader: Reader) -> dict:
    """By holding: each event with a P&L on Yes as a row, in the ledger's order, with that P&L and beside it
    what each holding gains or loses on Yes, in whole dollars, the holdings apportioned so that they add to the event's
    Yes as shown. The event and its figure stay in place as the holdings scroll past what the panel holds.
    Each figure opens its working."""
    found = _ledger(reader)
    if not found:
        return _missing()
    store = reader.store
    rows_ = [r for r in found["rows"] if r.get("yes") is not None]  # each event with a P&L on Yes
    ranked = {r["id"]: i for i, r in enumerate(by_volatility(found["rows"]))}
    worst = {r["id"]: i for i, r in enumerate(by_worst(found["rows"]))}
    holds = store.links_of("portfolio", "holds")
    shown: dict[str, dict[str, float]] = {}
    for r in rows_:
        got = {h["holding"]: h.get("yes") or 0.0 for h in r.get("by_holding") or []}
        ids = [h["id"] for h in holds]
        parts = whole_parts([got.get(i, 0.0) for i in ids], r["yes"])
        shown[r["id"]] = dict(zip(ids, parts))
    moved = [h for h in holds if any(abs(shown[r["id"]].get(h["id"], 0.0)) > 1e-9 for r in rows_)]
    head = "".join(f'<th class="n">{go(h["to"], e(_holding_label(reader, h)), "go name")}</th>' for h in moved)
    body = []
    for r in rows_:
        pid = r["id"]
        cells = "".join(f'<td class="n {_tone_whole(v)}">{gonum(f"x:holding:{pid}:{hid}", amount(v))}</td>'
                        for hid in (h["id"] for h in moved) for v in [shown[pid].get(hid, 0.0)])
        body.append(f'<tr data-k="{e(pid)}" data-volatility="{ranked[pid]}" data-worst="{worst[pid]}">'
                    f'<th class="ev">{go(pid, title_html(r["title"]), "go name")}</th>'
                    f'<td class="n yes {_tone_whole(r["yes"])}">{gonum(f"x:outcome:{pid}:yes", amount(r["yes"]))}</td>'
                    f'{cells}</tr>')
    table = (f'<div class="bhwrap"><table class="bh"><thead><tr><th class="ev">Event</th><th class="n yes">Yes</th>'
             f'{head}</tr></thead><tbody data-ranked>{"".join(body)}</tbody></table></div>')
    html = (f'<header class="ph lh"><h2>Events</h2>{_ledger_views("holding")}</header>'
            f'<div class="lcap"><span>To {day(found["horizon_end"])} · each holding\u2019s P&amp;L on Yes</span>{ORDERS}</div>{table}')
    return {"title": "By holding", "html": html, "wide": True}


def _tone_whole(x: float | None) -> str:
    """A computed dollar figure's tone as shown in whole dollars: rust, teal, or quiet where it is nothing as shown."""
    if x is None:
        return ""
    return "nil" if nothing(x) else "loss" if x < 0 else "gain"


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
    """A row's figures, each under its label and rounded where it is read: Forecast, with the market-implied range;
    Yes, with its share of the portfolio; No; Full impact; Volatility; Largest contributors, with the funds they
    are held through. Each row is a list of parts, its label first, ("b", label), then text, ("go", text, target),
    ("aside", parts) for what reads beside the figure, or WAITING where a value is being worked out. A row with no
    consequence gives its probability only."""
    pid = r["id"]
    p, lo, hi = r["probability"], _range(r)[0], _range(r)[1]
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
        prob = [("b", "Market-implied range"), *_put(lo, hi, f"x:interval:{pid}", words="")]
    elif reader.pending and reader.reading_waits(pid):
        prob = [("b", "Forecast"), *P, ("aside", ["market-implied range ", WAITING, "–", WAITING])]
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


def _put(lo: float, hi: float, span: str, words: str = "market-implied range ") -> list | None:
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


def sentences_text(parts: list) -> str:
    """Parts as text: a label and its figure, with what reads beside the figure after a comma, or after the separator
    its aside names."""
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif part == WAITING:
            continue
        elif part[0] == "b":
            out.append(part[1] + " ")
        elif part[0] == "aside":
            out.append((part[2] if len(part) > 2 else ", ") + sentences_text(part[1]))
        else:
            out.append(part[1])
    return "".join(out)


def figures_text(rows: list[list]) -> str:
    """A row's figures as one line of text: each label with its figure, separated by semicolons."""
    return "; ".join(sentences_text(row) for row in rows) + "." if rows else ""


# ---------------------------------------------------------------- an opened event


MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!$|>~])")


def as_written(passage: str) -> str:
    """A passage as its page reads: a fetched page's text is markdown, which writes a dollar sign, an asterisk or a
    bracket with a backslash before it, and the passage quotes that text word for word."""
    return MD_ESCAPE.sub(r"\1", passage or "")


def _passage_target(sid: str, passage: str) -> str:
    return f"x:passage:{sid}:{hashlib.sha256(passage.encode()).hexdigest()[:12]}"


def _to_note(note: str, where: str = "above") -> str:
    """A link to the note the analyst gave its reasoning in, where the event's header holds it: its Rationale."""
    return f'<a class="to" data-to="{e(note)}" href="#">Rationale</a>'


def _stated_basis(reader: Reader, value: dict, owner: str, *, below: Iterable[str] = (),
                  where: str = "above") -> str:
    """A stated assumption's basis, as a card: the page it cites, with each passage quoted; and the analyst's reasoning,
    the note it was stated with, and, where what states it is a link, a statement of its own such as a consequence, any
    other note about that link; each note its first lines shown and the rest a click away, or, where it shows in the
    event's notes, a pointer to it. A note about a proposition is about the proposition, which states several chances,
    each with its own note, so it is the basis of none of them: the event's notes show it."""
    store = reader.store
    out = ""
    if value.get("source_id"):
        sid = value["source_id"]
        src = store.source_row(sid) or {}
        quotes = "".join(f'<blockquote class="q" data-k="q{i}">{go(_passage_target(sid, passage), e(as_written(passage)), "go quote")}'
                         f'</blockquote>' for i, passage in enumerate(value.get("passages") or []))
        dated = f" · {day(src.get('dated'))}" if src.get("dated") else ""
        head = (f'<p class="src"><a href="{e(src.get("url"))}" target="_blank" rel="noopener">{e(host(src.get("url")))}'
                f'</a>{dated}</p>')
        out += f'<div class="basis">{head}{quotes}</div>'
    notes = [value["note"]] if value.get("note") and store.type_of(value["note"]) == "Note" else []
    if store.type_of(owner) == "link":
        notes += [n["from"] for n in store.links_of(owner, "about", "in") if n["from"] not in notes]
    here = [n for n in notes if n not in below]
    if here:
        out += f'<div class="basis note">{fold("".join(note_html(store.stated_of(n).get("text")) for n in here))}</div>'
    return out + "".join(f'<p class="ptr">{_to_note(n, where)}</p>' for n in notes if n in below)


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
        return e(said[:1].upper() + said[1:]) + (f", {num(operand(x))}" if isinstance(x, (int, float)) else "")
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
                       f'{gonum("x:p:" + a, operand(q))}')
    out += [_statement_words(reader, t) if ".chances[" in t else _link_sentence(reader, t, side)
            for t in tags if t not in books and t not in held]
    return [x for x in out if x]


def _how_chance(reader: Reader, r: dict) -> str:
    """How the event's probability is worked out, where the markets below do not say it: total probability over the
    cases its chances are given, or our estimate with its basis."""
    pid, point = r["id"], r["point"]
    rule = point.get("rule")
    if r["probability"] is None:
        return ""
    if rule == "chance":
        own = next((c for c in point.get("chances") or [] if not c["given"] and not c["given_not"]), None)
        basis = _stated_basis(reader, _stated_chance(reader.store, own["key"]), pid, below=r["below"],
                              where="below") if own else ""
        return (f'<div class="how" data-k="how"><p class="lead">Analyst input, '
                f'{gonum("x:p:" + pid, chance(r["probability"]))}</p>{basis}</div>')
    if rule != "derived" or not point.get("terms"):
        return ""
    worked = _derivation(reader, pid, point, r["probability"], below=r["below"], where="below")
    return f'<div class="how" data-k="how">{worked}</div>'


LETTERS = "ABCDFGHJKLMNPQRSTUVWXYZ"  # each proposition a chance is given, E the one it is the chance of


def _derivation(reader: Reader, pid: str, point: dict, p: float, *, below: Iterable[str] = (),
                where: str = "above") -> str:
    """A probability its chances fix, as a receipt (MATH.md 1.2): each proposition its chances are given; each case of
    them with its probability and the share of it in which E is true, stated by a chance or set by a link; E's
    probability beneath them, their sum, P(E) = sum of P(case) x P(E | case); then the basis of each chance."""
    store = reader.store
    terms = point["terms"]
    over = list(dict.fromkeys(x for t in terms for x in (*t["given"], *t["given_not"])))
    letter = dict(zip(over, LETTERS))
    rows = [(eqn(letter[q]), go(q, e(title_of(store, q))) + (f'<span class="how-set">{_how_set(reader, q)}</span>'
                                                             if _how_set(reader, q) else ""), "") for q in over]
    case = lambda t: ", ".join([*(letter[x] for x in t["given"]), *(f"not {letter[x]}" for x in t["given_not"])])  # noqa: E731
    # a case's probability, or the range its statements leave it, where E is the same anywhere in it; a share by the
    # chance that states it, by a link, or read from the probabilities; where nothing gives one, its symbol stands
    p_of = lambda t: (operand(t["p"]) if t.get("p") is not None else  # noqa: E731
                      f'{operand(t["range"]["lo"])}–{operand(t["range"]["hi"])}' if t.get("range") else "")
    how_of = lambda t: ("Analyst input" if t.get("chance") else "By its definition" if t.get("by") == "links"  # noqa: E731
                        else "")
    for t in terms:
        rows.append((eqn(f"P({case(t)})"), "", num(p_of(t))))
        rows.append((eqn(f"P(E | {case(t)})"), f'<span class="how-set">{how_of(t)}</span>' if how_of(t) else "",
                     num(operand(t["share"])) if t.get("share") is not None else ""))
    filled = lambda t: (f"{operand(t['p'])}" if t.get("p") is not None else f"P({case(t)})") + " × " + (  # noqa: E731
        f"{operand(t['share'])}" if t.get("share") is not None else f"P(E | {case(t)})")
    out = receipt(rows, (eqn("P(E)"), "", num(operand(p))),
                  [eqn("P(E) = " + " + ".join(f"P({case(t)}) × P(E | {case(t)})" for t in terms)),
                   eqn("= " + " + ".join(filled(t) for t in terms) + f" = {operand(p)}")])
    for t in terms:
        if t.get("chance"):
            basis = _stated_basis(reader, _stated_chance(store, t["chance"]), pid, below=below, where=where)
            if basis:
                out += f'<h3 class="sub">Basis for {eqn(f"P(E | {case(t)})")}</h3>{basis}'
    return out


def _reading_of(reader: Reader, pid: str) -> dict:
    """A proposition's reading in its structure, from the Computation behind its range."""
    d = reader.derived(pid, "Proposition", READING)
    cid = _basis(d, "probability") or _basis(d, "left_open") or _basis(d, "range")
    return ((reader.computation(cid).get("output") or {}).get("props") or {}).get(pid) or {}


# how a proposition's forecast is set, beside it in a working: a derived one opens its own calculation from its name
HOW_SET = {"market": "Midpoint", "read": "Read from the markets", "chance": "Analyst input"}


def _how_set(reader: Reader, pid: str) -> str:
    """How a proposition's probability is set, in a few words: a market's price; read from the markets, where its
    market's price is read with the markets on related propositions; derived; or our estimate."""
    return HOW_SET.get(_reading_of(reader, pid).get("rule"), "")


def _tree(store: Store, pid: str) -> tuple[dict[str, list[dict]], list[str]]:
    """The event's structure, each proposition reached once from the event, by the first link or chance that reaches it:
    a chance ties the proposition it is the chance of to each it is given."""
    seen, queue, order = {pid}, deque([pid]), [pid]
    nodes: dict[str, list[dict]] = {pid: []}
    while queue:
        here = queue.popleft()
        ties = [link for link in store.links_of(here, direction="both") if link["relation"] in STRUCTURE]
        ties += [{"id": f"{here}.chances[{i}]", "relation": "given", "from": here, "to": x, "stated": {}}
                 for i, c in enumerate(store.stated_of(here).get("chances") or [])
                 for x in (*c["given"], *c["given_not"])]
        ties += [{"id": f"{q}.chances[{i}]", "relation": "given", "from": q, "to": here, "stated": {}}
                 for q, i in store.given_by(here)]
        for link in ties:
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


def _stretch_rows(stretched: dict | None) -> list[tuple[str, str, str]]:
    """A stretched book in a probability's working, beneath its bid and its ask: how far it was stretched below the bid
    or above the ask, in the proposition's terms, as the bid and the ask above it are."""
    if not stretched:
        return []
    return [(words, "", num(operand(stretched.get(k)))) for k, words in
            (("below", "Stretched below its bid"), ("above", "Stretched above its ask")) if (stretched.get(k) or 0) > 0]


def _stretch_words(stretched: dict | None) -> str:
    """A stretched book beside the market it is, in a probability's working: stretched by how much, below its bid or
    above its ask, in the proposition's terms."""
    if not stretched:
        return ""
    said = [f"{num(operand(stretched.get(k)))} {words}" for k, words in (("below", "below its bid"),
                                                                       ("above", "above its ask"))
            if (stretched.get(k) or 0) > 0]
    return f'<span class="how-set">stretched {" and ".join(said)}</span>' if said else ""


def _asks_sides(reader: Reader, lid: str) -> str:
    """A taken market's bid and ask for the proposition it asks, through the side it asks (MATH.md 1.2)."""
    book = reader.derived(lid, "asks")
    bid, ask = _value(book, "bid"), _value(book, "ask")
    return quotes(bid, ask, side_of(reader.store, lid)) if bid is not None or ask is not None else ""


def _market_item(reader: Reader, link: dict) -> str:
    """A market under the proposition it asks, as a card: its question and its midpoint, through the side it asks; its
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
        figure = '<span class="pt-w">Midpoint</span> ' + gonum("x:price:" + mid, cents(_value(asked, "price")))
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
    lines = [f'<div class="mmeta">{" · ".join(meta)}</div>']
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
        said = quotes(m.get("bid"), m.get("ask"), side_of(store, m.get("link")))
        plot = (f'<a class="pt-ba{"" if held else " pt-not"}" data-go="{e("x:price:" + mid)}" title="{e(said)}" '
                f'style="left:{_pt_x(bid, dom)};width:calc({_pt_x(ask, dom)} - {_pt_x(bid, dom)})"></a>')
        if m.get("price") is not None:
            plot += (f'<a class="pt-dot{"" if held else " pt-not"}" data-go="{e("x:price:" + mid)}" title="{e(said)}" '
                     f'style="left:{_pt_x(m["price"], dom)}"></a>'
                     + _pt_label(m["price"], dom, '<span class="pt-w">Midpoint</span> ' + gonum("x:price:" + mid, cents(m["price"]))))
        how = " · followed" if m.get("followed") else "" if held else " · price not used"
        sub = (f'{e(venue)} \u00b7 {quotes(m.get("bid"), m.get("ask"), side_of(store, m.get("link")), html=True)}'
               f'{f" · depth <span class=n>{e(depth)}</span>" if depth else ""}{how}')
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
        plot += (f'<a class="pt-band" data-go="{e("x:interval:" + pid)}" title="Market-implied range {e(_implied(r))}" '
                 f'style="left:{_pt_x(lo, dom)};width:calc({_pt_x(hi, dom)} - {_pt_x(lo, dom)})"></a>')
    if p is not None:
        plot += (f'<a class="pt-dot pt-ours" data-go="{e("x:p:" + pid)}" style="left:{_pt_x(p, dom)}"></a>'
                 + _pt_label(p, dom, gonum("x:p:" + pid, chance(p)), "pt-ourv"))
    elif left_open and _span(left_open):
        plot += (f'<a class="pt-open" data-go="{e("x:p:" + pid)}" style="left:{_pt_x(left_open["lo"], dom)};'
                 f'width:calc({_pt_x(left_open["hi"], dom)} - {_pt_x(left_open["lo"], dom)})"></a>'
                 + _pt_label(left_open["hi"], dom, gonum("x:p:" + pid, _span(left_open)), "pt-ourv"))
    direct = (r.get("point") or {}).get("rule") == "chance"
    rows.append(f'<div class="pt-row pt-fore"><div class="pt-lab"><span class="pt-name">Forecast</span>'
                f'<span class="pt-sub">{"Market-implied range " + f"<span class=nw>{e(_implied(r))}</span>" if bounded else "Analyst input" if direct else ""}</span></div>'
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
                      f'<span class="pt-sub">Conditional probability</span></div>'
                      f'<span class="pt-fig">{gonum("x:p:" + pid, chance(t.get("share")))}</span>'
                      f'<span class="pt-tag">Analyst input</span></div>')
    return drawing + (f'<div class="pt-inputs" data-k="inputs">{"".join(inputs)}</div>' if inputs else "")


def _market_section(reader: Reader, r: dict) -> str:
    """The event's probability, where it comes from: the drawing; a chance that follows a market; the
    probability's history; the markets read and not used; and the searches."""
    pid = r["id"]
    point = r.get("point") or {}
    basis = ""
    if point.get("rule") == "chance":  # our estimate, stated on its own: its basis under it
        own = next((c for c in point.get("chances") or [] if not c["given"] and not c["given_not"]), None)
        basis = _stated_basis(reader, _stated_chance(reader.store, own["key"]), pid, below=r["below"],
                              where="below") if own else ""
    open_ = _how_chance(reader, r) if r.get("probability") is None else ""
    store = reader.store
    nodes, order = _tree(store, pid)
    faint = _faint(store, order)
    unused = "".join(f'<li>{go(mid, e(market_question(reader, mid) or mid), "go name")}'
                     f'<span class="pt-sub">{e(VENUES.get(split(mid)[0], split(mid)[0]))}</span>'
                     f'{go(note, "Rationale", "go why")}</li>' for q in order for mid, note in faint.get(q, []))
    markets = any(store.links_of(q, "asks") for q in order) or any(schema.MARKET_ID_RE.match(q) for q in order)
    words = "" if markets else '<p class="none" data-k="none">No market bears on it.</p>'
    return (_probability_drawing(reader, r) + words + (f'<div class="how" data-k="how">{basis}</div>' if basis else "")
            + open_ + _following(reader, point) + _history(reader, r)
            + (f'<div class="pt-unused" data-k="unused"><span class="glab">Not used</span><ul>{unused}</ul></div>'
               if unused else "")
            + _searches(r, open_=not markets))


def _following(reader: Reader, point: dict) -> str:
    """The observed source of a moving estimate, including an implied weight that its worlds leave open."""
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
        form = chance if follow.get("name") else cents  # a weight a holding's price implies; a market's midpoint
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
    now_def = {k: r.get(k) for k in DEFINITION}
    replies: list[tuple[str, dict]] = []
    for rec in store.records():
        got = next((x for x in rec["rows"] or [] if x.get("id") == pid), None)
        if got is None:
            continue
        if {k: got.get(k) for k in DEFINITION} != now_def:
            replies = []  # what it said before it was restated answered another question
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
    """Whole values the analyst states, in their worlds, and the weight today's price implies."""
    out = reader.output("impact", r["id"]) or {}
    stated = out.get("worth") or {}
    if not stated:
        return ""
    titles = {o["id"]: o["title"] for o in r.get("outcomes") or []}
    items = []
    for nid, value in stated.items():
        links = value.get("considerations") or {w: item["link"] for w, item in
                ((out.get("valuation_terms") or {}).get("worth") or {}).get(nid, {}).items()}
        rows = [(e(titles.get(wid, wid)), "", gonum(links[wid], money(amount)) if links.get(wid) else num(money(amount)))
                for wid, amount in (value.get("levels") or {}).items()]
        implied = _implied_words(value.get("implied"))
        bases = []
        for lid in dict.fromkeys(links.values()):  # each basis under the world it is the basis of
            link = reader.store.link_by_id(lid) or {}
            on = ", ".join(titles.get(w, w) for w, x in links.items() if x == lid)
            bases.append(f'<p class="aside">{e(on)}</p>' +
                         _stated_basis(reader, (link.get("stated") or {}).get("value") or {}, lid, below=r.get("below") or []))
        items.append(f'<div class="worth" data-k="worth:{e(nid)}"><h3 class="sub">'
                     f'{go(nid, e(name_of(reader.store, nid)))} · per share</h3>{receipt(rows)}{implied}{"".join(bases)}</div>')
    return section("Values stated", "".join(items), "worth", "worth")


def _implied_words(implied: float | None) -> str:
    """The weight a holding's price implies for the event, from the values stated for it (MATH.md 1.6); values that put it
    outside 0 to 1 contradict the price (1.10), and say so."""
    if implied is None:
        return ""
    if implied < 0 or implied > 1:
        return (f'<p class="held">These values contradict today’s price: they put the weight it implies for the event '
                f'at {num(f"{implied * 100:.0f}%")}.</p>')
    return f'<p>Today’s price implies {num(chance(implied))} for the event.</p>'


def _worth_view(reader: Reader, lid: str) -> dict:
    """The consideration per share, its valued worlds and the cited basis for stating it."""
    link = reader.store.link_by_id(lid)
    stated = link["stated"]["value"]
    value = stated["value"]
    name = name_of(reader.store, link["to"])
    source = link["from"]
    world = link["stated"].get("world")
    context = go(source, e(title_of(reader.store, source))) + (f' · {world.title()}' if world else "")
    head = hero("Value stated · per share", title=e(name), context=context)
    rows = []
    if "amount" in value:
        rows.append(("Value today", "", num(money(value["amount"]))))
    if "cash" in value:
        rows.append(("Cash", e(day(value["date"])), num(level(value["cash"], value.get("currency", "USD")))))
    if value.get("shares"):
        shares = value["shares"]
        rows.append(("Shares received", go(shares["name"], e(name_of(reader.store, shares["name"]))),
                     num(f'{shares["quantity"]:g}')))
    if "return" in value:
        rows.append(("Its return since its price on", e(day(value["date"])), num(move(value["return"]))))
    out = reader.output("worth", lid) or {}
    values = [(_world_label(reader.store, w), "", num(money(amount))) for w, amount in (out.get("levels") or {}).items()]
    body = receipt(rows)
    if values:
        body += '<h3 class="sub">Value today in each world</h3>' + receipt(values)
    body += _implied_words(out.get("implied"))
    body += _stated_basis(reader, stated, lid)
    return {"title": f"Value of {name}", "html": head + body + full(reader, lid)}


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


def event_view(reader: Reader, pid: str) -> dict:
    """An opened event, a working surface: its header, what it is, when and how it would be known; one strip of its
    figures, the forecast first; then by tab its outcomes with each holding's P&L there, its probability with the
    markets and inputs it rests on, and its assumptions, each price's move and level there with their bases. An
    outcome selected in one selects it in all; a figure opens its calculation beside the event."""
    store = reader.store
    r = row(store, pid)
    r["below"] = _shown_below(store, r)
    tabs = "".join(f'<button type="button" role="tab" data-tab="{k}">{v}</button>' for k, v in TABS)
    outcomes = (_outcome_picture(reader, r) + _outcome_grid(reader, r, only=("holdings",))
                + _contributors_table(reader, r))
    assumptions = (_outcome_grid(reader, r, only=("moves", "levels"), heads=False, key="assumptions")
                   + _worth_values(reader, r)
                   + _episodes(reader, r))
    body = (_event_head(reader, r) + _ws_strip(reader, r) + f'<nav class="tabs" role="tablist">{tabs}</nav>'
            + f'<section data-pane="outcomes">{outcomes}</section>'
            + f'<section data-pane="probability">{_market_section(reader, r)}</section>'
            + f'<section data-pane="assumptions">{assumptions}</section>')
    return {"title": r["title"], "html": f'<div class="ev ws">{body}</div>', "wide": True}


def _ws_strip(reader: Reader, r: dict) -> str:
    """The event's figures in one strip, each under its label (TOOLS.md 9): the forecast, the range the markets imply and
    a market's own price where it differs; then Yes, with its share of the portfolio, No, the full impact, the
    volatility, the worst outcome where it is a way, and the expected value where it is not zero as shown. Each opens
    its calculation."""
    pid, yes = r["id"], r.get("yes")
    cells = []
    prob = gonum(f"x:p:{pid}", _figure_p(r)) if _figure_p(r) else WAIT if reader.reading_waits(pid) else ""
    if prob:
        cells.append(("Forecast", prob, "first"))
    lo, hi = _range(r)
    if lo is not None and hi is not None and (lo > ZERO or hi < 1.0 - ZERO) and _beside_open(r):
        said = _implied(r)
        cells.append(("Market-implied range", gonum(f"x:interval:{pid}", said), ""))
    m = r.get("market") or {}
    if m.get("probability") is not None and _market_words(reader, r):
        book = reader.derived(m["link"], "asks")  # its book through the side the event asks
        cells.append(("Market", '<span class="qt">' + quotes(_value(book, "bid"), _value(book, "ask"),
                                                            side_of(reader.store, m["link"]), html=True) + "</span>"
                      + f'<span class="cap">Midpoint <span class="n">{cents(m["probability"])}</span></span>', ""))
    if yes is not None:
        full = as_shown(yes) - as_shown(r["no"])
        share_ = (f'<span class="cap"><span class="n {_move_tone(r["share_yes"], share(r["share_yes"]), share(0.0))}">'
                  f'{share(r["share_yes"])}</span> of portfolio</span>') if r.get("share_yes") is not None else ""
        cells += [("Yes", gonum(f"x:outcome:{pid}:yes", amount(yes), _tone_whole(yes)) + share_, ""),
                  ("No", gonum(f"x:outcome:{pid}:no", amount(r["no"]), _tone_whole(r["no"])), ""),
                  ("Full impact", gonum(f"x:swing:{pid}", amount(full), _tone_whole(full)), ""),
                  ("Volatility", gonum(f"x:volatility:{pid}", either(r.get("volatility"))), "")]
        worst = r.get("worst_outcome") or {}
        if worst.get("world") not in (None, "yes", "no"):
            cells.append(("Worst outcome", gonum(f"x:outcome:{pid}:{worst['world']}", amount(worst["pnl"]),
                                                 _tone_whole(worst["pnl"]))
                          + f'<span class="cap">{e(worst.get("title") or worst["world"])}</span>', ""))
        mu = r.get("expected")
        if mu is not None and _tone_whole(mu) != "nil":
            cells.append(("Expected value", gonum(f"x:swing:{pid}", amount(mu), _tone_whole(mu)), ""))
    elif reader.waits("impact", pid):
        cells.append(("Yes", WAIT, ""))
    calc = f'<a class="calc" data-go="{e("x:p:" + pid)}">Calculation</a>' if prob else ""
    figs = "".join(f'<div class="sf{" " + c if c else ""}" data-k="fig:{e(k)}"><span class="lab">{e(k)}</span>'
                   f'<span class="v">{v}</span>{calc if c == "first" else ""}</div>' for k, v, c in cells)
    return f'<div class="strip">{figs}</div>' if figs else ""


def _event_head(reader: Reader, r: dict) -> str:
    """What the event is and when: its title; when it can happen and how it would be known; its definition,
    and the analyst's note about it, each opening in place."""
    store, pid = reader.store, r["id"]
    stated = store.stated_of(pid)
    when = dict(_when_pairs(stated, store.horizon_end(), store.meta("asof")))
    facts = f'<span>{e(when["When"])}</span>' + (
        f'<span class="sep">\u00b7</span><span class="src">Known from {e(when["Known from"])}</span>'
        if when.get("Known from") else "")
    opens = [f'<details class="hd" data-k="definition"><summary>Definition</summary>'
             f'<div class="hdb text">{paragraphs(r["text"])}</div></details>'] if r.get("text") else []
    if r["below"]:
        notes = "".join(f'<div class="note" data-k="{e(n)}" data-note="{e(n)}">'
                        f'{note_html(store.stated_of(n).get("text"))}</div>' for n in r["below"])
        opens.append(f'<details class="hd" data-k="notes"><summary>Rationale'
                     f'</summary><div class="hdb">{notes}</div></details>')
    return (f'<div class="evh"><h2 class="title">{title_html(r["title"])}</h2>'
            f'<p class="facts">{facts}</p>' + (f'<div class="hds">{"".join(opens)}</div>' if opens else "") + '</div>')


def _contributors_table(reader: Reader, r: dict) -> str:
    """The row's largest contributors (TOOLS.md 9), one line each: the name, where the portfolio holds it, directly and
    through each fund, and its contribution to the full impact, each opening what it names."""
    store, pid = reader.store, r["id"]
    names = r.get("contributors") or []
    if not names:
        return ""
    rows_ = []
    for n in names:
        held = (["Direct"] if n.get("held_directly") else []) + [go(f, e(name_of(store, f)), "go name")
                                                                 for f in n.get("through") or []]
        rows_.append(f'<tr><th>{go(n["name"], e(name_of(store, n["name"])), "go name")}</th>'
                     f'<td class="held">{" · ".join(held)}</td>'
                     f'<td class="n {_tone_whole(n["full_impact"])}">{gonum(f"x:swing:{pid}", amount(n["full_impact"]))}'
                     f'</td></tr>')
    return (f'<table class="ctb" data-k="fig:Largest contributors"><thead><tr><th>Largest contributors</th><th>Held</th>'
            f'<th class="n">Full impact</th></tr></thead><tbody>{"".join(rows_)}</tbody></table>')


def _distribution_of(reader: Reader, r: dict) -> tuple[dict, str | None, bool]:
    """The event's distribution at the horizon's end (MATH.md 1.8), where its working opens, and whether it is being
    computed. It is read as the page reads what is computed apart: where it is not yet computed, its computation starts
    and the read returns at once, as it was last worked out where it has been, so the opened event shows without
    waiting for its draws; the read says the value is being made (Engine.last_where_waiting), and the page reads the
    view again, the picture and the horizon's rows changing in place once it is made."""
    with reader.engine.placeholders():
        found = reader.value("distribution", r["id"])
    if found is None:  # on a reply's turn, a distribution this read could not give is being worked out
        return {}, None, bool(getattr(reader, "pending", False) and _worlds_of(r))
    if found.waiting:
        return {}, None, True
    return found.output or {}, working(reader, found.basis), False


LATER = (f'<p class="later">{WAIT}<span>Working out each outcome at the horizon</span></p>')


def _outcome_picture(reader: Reader, r: dict) -> str:
    """The picture: the portfolio's P&L at the horizon's end, in today's dollars (1.8), one row for each
    outcome at its own height, so each outcome's own range reads whole however likely it is, its probability said as
    a figure beside its title; its P&L were it known today marked, and its 5th to 95th percentiles beneath; on one dollar
    axis, today at zero. A last, quiet row is all the outcomes together, each at its probability: the forecast. Each
    row is the engine's draws counted on its one set of bins; the page draws them, each scaled to its own peak."""
    dist, opens, waiting = _distribution_of(reader, r)
    if waiting and _worlds_of(r):  # never worked out: its place, saying so, until the view read again brings it
        return (f'<div class="pic" data-k="picture-sec"><div class="gridhead">'
                f'<span class="plab">At {e(day(reader.store.horizon_end()))}</span></div>{LATER}</div>')
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
    at = day(dist.get("horizon_end"))
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
    probability and average, the baseline marked. Its rows give each outcome's probability and its P&L, and at the
    horizon's end its 5th and 95th percentiles (1.8).
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
    tag = lambda w: '<span class="otag">baseline</span>' if w.get("baseline") else ""  # noqa: E731, above its title
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
        horizon = ""
        lowers = {"moves": _moves_rows(reader, r, worlds, n), "levels": "",
                  "holdings": _against_holdings_rows(reader, r, worlds, n)}
    else:
        dist, opens, waiting = _distribution_of(reader, r)
        rows = [f'<tr><th>Probability</th>{cells(p_of)}</tr>',
                f'<tr><th>P&amp;L</th>' + cells(lambda w: gonum(f"x:outcome:{pid}:{w['id']}", amount(w["A"]),
                                                                 _tone_whole(w["A"]))) + '</tr>']
        horizon = "".join(_horizon_rows(dist, opens, worlds)) if not waiting else "".join(
            f'<tr class="g2"><th>{k}, {e(day(reader.store.horizon_end(), year=False))}</th>'
            + "".join(f'<td data-outcome="{e(w["id"])}" class="n">{WAIT}</td>' for w in worlds) + '</tr>'
            for k in ("5th percentile", "95th percentile"))
        lowers = {"moves": _moves_rows(reader, r, worlds, n), "levels": _levels_rows(reader, r, worlds, n),
                  "holdings": _holdings_rows(reader, r, worlds, n)}
    lowers = {k: v for k, v in lowers.items() if k in only}
    if not heads:  # the outcomes' probabilities alone, as the head of rows another view reads
        rows, horizon = rows[:1], ""
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
            f'<tbody data-k="horizon">{horizon}</tbody>{lower}</table></div>')


def _horizon_rows(dist: dict, opens: str | None, worlds: list[dict]) -> list[str]:
    """The outcomes' rows at the horizon's end, from the engine's distribution (MATH.md 1.8): each outcome's 5th and 95th
    percentiles, each opening the distribution's working. Its mean there is in the outcome's working, beside its P&L
    today, which it differs from only where a holding's value is not in proportion to a price."""
    at = dist.get("worlds") or {}
    if not at:
        return []
    date_ = day(dist.get("horizon_end"), year=False)
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
                    target = "x:consequence:" + how["consequence"] if how.get("consequence") else how["worth"]
                    stated = how.get("stated") or {}
                    residual = (f'<span class="st-as">residual {e(factor_move(item, stated["value"]))}</span>'
                                if schema.against(stated) == schema.FOLLOWS and stated.get("value") is not None else "")
                    tds.append(f'<td data-outcome="{e(w["id"])}" class="n st">{gonum(target, figure, tone)}{residual}</td>')
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
            out += (f'<tr><th>{go(target, e(name), "go name")}<span class="sub">today {e(level(today, u))}</span></th>'
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
    working = (reader.computation((r.get("basis") or {}).get("by_holding")).get("output") or {}).get("by_holding")
    hs = sorted(working or r.get("by_holding") or [], key=lambda h: -abs(h.get("full_impact") or 0.0))
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
    """The probability: the price of the market that asks it, total probability over the cases its chances are given,
    or the analyst's estimate, each with its basis; or the range its statements leave it, with what each end rests
    on."""
    store = reader.store
    d = reader.derived(pid, "Proposition", READING)
    p = _value(d, "probability")
    cid = _basis(d, "probability") or _basis(d, "left_open")
    title = title_of(store, pid)
    out = _reading_of(reader, pid)
    stretched = _stretched_on(reader, pid)
    if p is None:  # the range its statements leave it, each end with what it rests on; or its place while it is read
        o = _value(d, "left_open")
        head = hero("Forecast", figure=e(_span(o)), context=go(pid, e(title)))
        # each end with what it rests on, from the reading in its structure, where the derived field keeps only the ends
        body = _left_ends(reader, out.get("open") or o) if _span(o) else WAIT if reader.reading_waits(pid) else ""
        return {"title": f"Forecast: {title}", "html": head + body + _following(reader, out) + full(reader, cid or _basis(d, "range"))}
    rule = out.get("rule")
    head = hero("Forecast", figure=e(chance(p)), context=go(pid, e(title)))  # how it is set, its calculation says
    body = []
    if rule in ("market", "read") and out.get("target"):
        lid = out["target"]["link"]
        link = store.link_by_id(lid) or {}
        mid = link.get("to")
        q = market_question(reader, mid) or mid
        side = side_of(store, lid)
        book = reader.derived(lid, "asks")  # its bid, ask and midpoint, through the side it asks
        rows = [("Market", go(mid, e(q)), "")]
        rows += [(f"{side.upper()} bid", "", gonum("x:price:" + mid, cents(_value(book, "bid")))),
                 (f"{side.upper()} ask", "", gonum("x:price:" + mid, cents(_value(book, "ask")))),
                 ("Midpoint", "", gonum("x:price:" + mid, cents(_value(book, "price"))))]
        if rule == "read":
            rows += _stretch_rows(stretched.get(lid))  # how far the statements stretched it, where they did
            within = "within its book as stretched" if stretched.get(lid) else "within its bid and ask"
            total = (eqn("P(E)"), f"{within}, read with the markets on related propositions", num(operand(p)))
            formula = []
        else:
            total, formula = (eqn("P(E)"), "its midpoint", num(operand(p))), []
        body.append(f'<h3 class="sub">Calculation</h3>{receipt(rows, total, formula)}')
        others = [m for m in out.get("markets") or [] if m["link"] != lid and m.get("taken")]
        if others:
            listed = [("", go(m["market"], e(market_question(reader, m["market"]) or m["market"]))
                       + _stretch_words(stretched.get(m["link"])),
                       f'{quotes(m.get("bid"), m.get("ask"), side_of(store, m["link"]), html=True)} \u00b7 depth '
                       f'{num(amount(m.get("depth"), sign=False))}')
                      for m in others]
            body.append(f'<h3 class="sub">Other markets that ask it</h3><p class="aside">The deepest sets the midpoint '
                        f'it stays near: this one, with {num(amount(out["target"].get("depth"), sign=False))} at its '
                        f'quotes. The others, by depth:</p>{receipt(listed)}')
    elif rule == "chance":
        own = next((c for c in out.get("chances") or [] if not c["given"] and not c["given_not"]), None)
        if own:
            body.append(f'<h3 class="sub">Calculation</h3>{receipt([], (eqn("P(E)"), "Analyst input", num(operand(p))))}'
                        f'<h3 class="sub">Basis</h3>{_stated_basis(reader, _stated_chance(store, own["key"]), pid)}')
    elif rule == "derived" and out.get("terms"):
        body.append(f'<h3 class="sub">Calculation</h3>{_derivation(reader, pid, out, p)}')
    if rule not in ("market", "read") and stretched:  # its own markets, which the chances stand over
        held = [("", go(m["market"], e(market_question(reader, m["market"]) or m["market"]))
                 + _stretch_words(stretched.get(m["link"])), num(_asks_sides(reader, m["link"])))
                for m in out.get("markets") or [] if m["link"] in stretched]
        if held:
            body.append(f'<h3 class="sub">Markets that ask it</h3>{receipt(held)}')
    return {"title": f"Forecast: {title}", "html": head + "".join(body) + _following(reader, out) + full(reader, cid)}


def _left_ends(reader: Reader, o: dict) -> str:
    """The range a proposition's statements leave its probability (MATH.md 1.2), as a receipt: its floor and its ceiling,
    the lowest and the highest it can have with every link and chance kept and each proposition a market prices at
    its probability; then what each end rests on."""
    rows = [("Floor", "", num(operand(o["lo"]))), ("Ceiling", "", num(operand(o["hi"])))]
    out = f'<h3 class="sub">Forecast range</h3>{receipt(rows)}'
    for side, words in (("lo", "What sets the floor"), ("hi", "What sets the ceiling")):
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
        quoted = reader.derived(lid, "asks")  # its book through the side the proposition asks
        q = go(b, e(quote(market_question(reader, b) or b)))
        said = (link["stated"].get("side") or "yes").upper()
        by = (stretched or {}).get("below" if side == "lo" else "above") or 0.0
        given = f', stretched {"below" if side == "lo" else "above"} it by {num(operand(by))}' if by > 0 else ""
        end = "bid" if side == "lo" else "ask"
        return f'{said} {end} on {q}, for {A}: {gonum("x:price:" + b, cents(_value(quoted, end)))}{given}'
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
    head = hero("Market-implied range", figure=e(shown), context=go(pid, e(title)))
    if lo is None or hi is None:
        return {"title": f"Market-implied range: {title}", "html": head + full(reader, cid)}
    stretched = _structure_of(reader, pid).get("implied_stretched") or {}  # books the links alone stretch, by link
    chains = {side: _rests(reader, node.get(f"{side}_rests") or [], side, stretched) for side in ("lo", "hi")}
    said = lambda side: "set by the markets below" if chains[side] else "no market sets it"  # noqa: E731
    body = [receipt([("Floor", said("lo"), num(operand(lo))), ("Ceiling", said("hi"), num(operand(hi)))])]
    for side, words in (("lo", "What sets the floor"), ("hi", "What sets the ceiling")):
        if chains[side]:
            body.append(f'<h3 class="sub">{words}</h3><ol class="chain">'
                        + "".join(f"<li>{x}</li>" for x in chains[side]) + "</ol>")
    return {"title": f"Market-implied range: {title}", "html": head + "".join(body) + full(reader, cid)}


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
    """A market's price (MATH.md 3.3): its book as observed, YES bid and ask, and the midpoint of the two where both sides
    quote; its depth and its last trade; at a past as-of, what the quotes were read from; then the book's best levels.
    A market with a quote missing on a side has no midpoint."""
    v = reader.value("market_price", mid)
    out = (v.output if v else None) or {}
    q = market_question(reader, mid) or mid
    venue = VENUES.get(split(mid)[0], split(mid)[0])
    price, bid, ask = out.get("price"), out.get("bid"), out.get("ask")
    status = pill("Open", "teal") if out.get("open") else pill("Closed") if out.get("open") is False else ""
    head = hero("Midpoint" if price is not None else "Market", figure=e(cents(price)) if price is not None else "",
                context=go(mid, e(q)), pills=[pill(venue), status])
    rows = [("YES bid", "", num(cents(bid)) if bid is not None else "none"),
            ("YES ask", "", num(cents(ask)) if ask is not None else "none")]
    if price is not None:
        body = [receipt(rows, ("Midpoint", "", num(cents(price))),
                        [eqn(f"midpoint = (bid + ask) / 2 = ({cents(bid)} + {cents(ask)}) / 2 = {cents(price)}")])]
    elif out.get("history_price") is not None:  # a past day its venue's history gives a price for, and no quote
        body = [receipt([("History", e(day(out.get("price_date"))), num(cents(out["history_price"])))])]
    else:
        body = [receipt(rows)]
    facts = [("Depth", num(amount(out["depth"], sign=False)) if out.get("depth") is not None else "", "")]
    trade = out.get("last_trade") or {}
    if trade.get("price") is not None:
        facts.append(("Last trade", num(cents(trade["price"])), e(moment(trade.get("time"))) if trade.get("time") else ""))
    if out.get("past") and out.get("quoted_at"):
        facts.append(("Quoted", e(moment(out["quoted_at"])),
                      "its book as read" if out.get("from") == "reading" else "the day's closing quotes"))
    body.append(stats(facts))
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
            return f'{shown}<div class="aside">{what} {moment(out["time"])}</div>'
        return f'{shown}<div class="aside">close on {day(out.get("asof"))}</div>'
    stated = store.stated_of(mid)
    sid = (store.citations_of(mid) or {}).get("level")
    src = f' · {go(sid, e(host(store.stated_of(sid).get("url"))))}' if sid else ""
    return f'{num(level(stated.get("level"), unit))}<div class="aside">as of {day(stated.get("asof"))}{src}</div>'


def _stated(link: dict) -> tuple[str, dict]:
    """What a consequence states of its price's move (impact.stated_move): its shock, with what it is measured
    against and its basis."""
    kind = next(iter(stated_move(link)))
    return kind, link["stated"][kind]


AGAINST_WORDS = {schema.BASELINE: "whole move", schema.FOLLOWS: "residual"}  # what a shock states (MATH.md 1.3)


def _against(link: dict) -> str:
    """What a consequence's shock is measured against (MATH.md 1.3): the baseline, or what the price follows."""
    return schema.against(_stated(link)[1])


def _inspect_consequence(reader: Reader, lid: str) -> dict:
    """A consequence: the price it moves, where it stands today and where it ends in each world; the figure the
    analyst states, the shock, against the baseline or against what the price follows (MATH.md 1.3, 1.4), and its basis;
    then, in each world, how the shock and what the price follows make its move against the baseline."""
    store = reader.store
    link = store.link_by_id(lid)
    if link is None:
        return _missing()
    pid, moved = link["from"], link["to"]
    unit = unit_of(store, price_metric(store, moved))
    d = reader.derived(lid, "consequence", ("levels",))
    cid = _basis(d, "levels")
    out = reader.computation(cid).get("output") or {}
    what = target_label(store, moved)
    kind, said = _stated(link)
    x, against = said["value"], _against(link)
    c = out.get("c")
    title = f"Shock to {what}"
    shock = stated_shock(store, moved, x)
    tone = _move_tone(x, shock, stated_shock(store, moved, 0.0))  # the colour of its direction
    head = hero(e(title), figure=num(e(shock), tone), context=go(pid, e(title_of(store, pid))))
    said_as = ("the whole move against the baseline" if against == schema.BASELINE else "the residual") + \
        (", in percentage points of its yield" if quoted_as_yield(store, moved) else "")
    facts = [("Shock", num(e(shock), tone), said_as)]
    if c is not None:
        facts.append(("Today", num(level(c, unit)), ""))
    facts += [(_world_label(store, w), num(level(v, unit)), "") for w, v in (out.get("levels") or {}).items()]
    if out.get("yields"):
        facts.append(("Yield today", num(level(out["yield"], "%")), ""))
        facts += [(f"Yield, {_world_label(store, w)}", num(level(v, "%")), "") for w, v in out["yields"].items()]
    facts.append(("Forecast", gonum("x:p:" + pid, operand(_value(reader.derived(pid, "Proposition", ("probability",)), "probability"))), ""))
    body = [stats(facts)]
    basis = _stated_basis(reader, said, lid)
    if basis:
        body.append(f'<h3 class="sub">Basis</h3>{basis}')
    body.append(_moves(reader, pid, moved, lid, out.get("moves") or {}))
    impact = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    cid = cid or impact
    return {"title": title,
            "html": head + "".join(body) + full(reader, cid) + (full(reader, impact, "Full impact, full details") if impact else "")}


def _moves(reader: Reader, pid: str, moved: str, lid: str, stated_form: dict) -> str:
    """What the shock does in each world it applies in (MATH.md 1.3, 1.4). To a factor: its residual and what the factors
    above it give it, compounded, adding to its whole move against the baseline. To a name: its whole move and its
    response to the factors there, its residual the ratio of the two. Whichever the analyst states, the other follows
    from it; `stated_form` is each world's moves in the terms a shock on the price is stated in."""
    store = reader.store
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    rec = reader.computation(cid)
    out = (rec.get("output") or {}).get("consequences", {}).get(lid)
    if not out:
        return ""
    duration = (((rec.get("inputs") or {}).get("yields") or {}).get(moved) or {}).get("duration")
    link = store.link_by_id(lid) or {}
    follows = bool(link) and _against(link) == schema.FOLLOWS
    entry = next((f for f in _now(reader, pid).get("factors") or [] if f.get("factor") == moved), {"factor": moved})
    blocks = []
    for w, x in (out.get("worlds") or {}).items():
        where = e(_world_label(store, w))
        stated = stated_form.get(w) or {}
        if "factor" in out:  # compounded moves, which add; then the figure they come to, in the factor's own terms
            whole = num(log_operand(x["compounded"] if x.get("compounded") is not None else x["raw"], sign=True))
            above = x.get("implied") is not None and abs(x["implied"]) > 0
            residual = num(log_operand(x["own_part"], sign=True)) if x.get("own_part") is not None else ""

            def back(of: str) -> str:  # a compounded move in the factor's own terms: a move, or points of its yield
                return eqn(f"e^({of}) − 1") if duration is None else eqn(f"−100 × {of} / {duration:g}")
            if follows:  # stated as the residual: with what the factors above give it, its whole move
                rows = [("Residual, compounded", eqn("ln(1 + the shock)") if duration is None else
                         eqn(f"shock × −{duration:g} / 100"), residual)]
                total = None
                if above:
                    rows.append(("From the factors above", "", num(log_operand(x["implied"], sign=True))))
                    total = ("Whole move, compounded", eqn("residual + from the factors above"), whole)
                after = [("Whole move", back("whole move, compounded" if total else "residual, compounded"),
                          num(factor_move(entry, stated[MOVE])))] if stated.get(MOVE) is not None else []
            else:
                rows = [("Whole move, compounded", eqn("ln(1 + the shock)") if duration is None else
                         eqn(f"shock × −{duration:g} / 100"), whole)]
                total = None
                if above:
                    rows.append(("From the factors above", "", num(log_operand(x["implied"], sign=True))))
                    total = ("Residual, compounded", eqn("whole move − from the factors above"), residual)
                after = [("Residual", back("residual, compounded"),
                          num(factor_move(entry, stated[FOLLOWED])))] \
                    if above and stated.get(FOLLOWED) is not None else []
            blocks.append(f'<h3 class="sub">{go(moved, e(heading(target_label(store, moved))))}, {where}</h3>'
                          f'{receipt(rows, total, after=after)}')
        else:
            ticker = name_of(reader.store, moved)
            betas = ("From its betas", "", num(operand(x["betas"], sign=True)))
            if follows:
                rows = [("Residual", eqn("the shock"), num(operand(x["d"], sign=True))), betas]
                total = ("Whole move", eqn("(1 + residual) × (1 + from its betas) − 1"),
                         num(operand(x["whole"], sign=True)))
            else:
                rows = [("Whole move", eqn("the shock"), num(operand(x["whole"], sign=True))), betas]
                total = ("Residual", eqn("(1 + whole move) / (1 + from its betas) − 1"),
                         num(operand(x["d"], sign=True)))
            blocks.append(f'<h3 class="sub">{go(moved, e(ticker))}, {where}</h3>' + receipt(rows, total))
    return "".join(blocks)


def _inspect_swing(reader: Reader, pid: str) -> dict:
    """The full impact: each factor's dollar beta times its shock, and each stated company's exposure times its placed
    move beyond its response's, adding to it; then what is priced in, Yes and No, which differ by it, and the share of the portfolio."""
    store = reader.store
    d = reader.derived(pid, "Proposition", ("full_impact",))
    cid = _basis(d, "full_impact")
    rec = reader.computation(cid)
    out, inputs = rec.get("output") or {}, rec.get("inputs") or {}
    title = title_of(store, pid)
    if "full_impact" not in out:
        return {"title": "Full impact", "html": hero("Full impact", context=go(pid, e(title))) + full(reader, cid)}
    names = channel_names(store)
    g = inputs.get("g") or {}
    setter = {c["target"]: c["link"] for c in (out.get("consequences") or {}).values()}
    p, D = out["p"], out["full_impact"]
    head = hero("Full impact", figure=e(money(D, sign=True)), context=go(pid, e(title)),
                pills=[pill(f"Forecast {chance(p)}")])
    entries = []  # each line's label, what it multiplies, and its dollars
    for c, amount in sorted(out["contributions"].items(), key=lambda kv: -abs(kv[1])):
        if not out["u_E"].get(c):
            continue
        moved = gonum(f"x:consequence:{setter[c]}", operand(out["u_E"][c], sign=True)) if c in setter else \
            num(operand(out["u_E"][c], sign=True))
        entries.append((go(c, e(heading(names.get(c, c)))), f'{gonum(c, money(g.get(c)))} × {moved}', amount))
    own = out.get("own") or {}
    reach = out.get("reach") or {}
    for n, amount in sorted(own.items(), key=lambda kv: -abs(kv[1])):
        contracts = [h for h in out.get("by_holding") or [] if h["name"] == n and h.get("contract")]
        if contracts:
            entries.append((go(n, e(market_question(reader, n) or n)),
                            " + ".join(_contract_operand(reader, pid, out, h) for h in contracts), amount))
            continue
        per = (out.get("own_moves") or {}).get(n, {}).get("E")
        moved = gonum(f"x:consequence:{setter[n]}", operand(per, sign=True)) if n in setter else \
            num(operand(per, sign=True))
        entries.append((f"{go(n, e(name_of(reader.store, n)))}, beyond its response",
                        f"{gonum(n, dollars(reach.get(n)))} × {moved}", amount))
    curve = sum(((h.get("curve") or {}).get("full_impact") or 0.0) for h in out.get("by_holding") or [])
    if abs(curve) >= 0.005:
        entries.append(("The rest", "", curve))
    terms = to_cents([x[2] for x in entries], D)  # to the cent, adding to the full impact as shown
    rows = [(label_, what, num(money(t, sign=True))) for (label_, what, _), t in zip(entries, terms)]
    unset = [c for c in out["contributions"] if not out["u_E"].get(c)]
    other = (f'<p class="aside">No move between Yes and No: {series_of([e(heading(names.get(c, c))) for c in unset])}.</p>'
             if unset and len(unset) < len(out["contributions"]) else "")
    body = ['<h3 class="sub">Dollar beta × shock</h3>' + receipt(rows, ("Full impact", "", num(money(D, sign=True)))) + other]
    body.append(f'<h3 class="sub">Yes and No</h3>{receipt(_sides(reader, pid, out))}')
    return {"title": "Full impact", "html": head + "".join(body) + full(reader, cid)}


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
                                                                 f"{dollars(out['V'])}"), num(share(out["share_yes"]))))
    return rows


def _inspect_volatility(reader: Reader, pid: str) -> dict:
    """The volatility (MATH.md 1.1): the outcomes, each with its probability and the portfolio's P&L there, and their
    standard deviation, sqrt(sum of p * (P&L - expected value)^2). Where a side has ways, its two parts: the spread
    between the two sides, p * (1 - p) * full impact^2, and the spread within each, p * spread_yes^2 and
    (1 - p) * spread_no^2."""
    store = reader.store
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    out = reader.computation(cid).get("output") or {}
    title = title_of(store, pid)
    if out.get("volatility") is None:
        return {"title": "Volatility", "html": hero("Volatility", context=go(pid, e(title))) + full(reader, cid)}
    p, D, mu = out["p"], out["full_impact"], out.get("expected") or 0.0
    P = operand(p)
    head = hero("Volatility", figure=e(either(out["volatility"], cents=True)), context=go(pid, e(title)))
    worlds = [o for o in _outcomes(reader, pid, out) if o["world"]]
    rows = [(go(f"x:outcome:{pid}:{o['id']}", e(o["title"]), "go name"), f"probability {operand(o['p'])}",
             num(money(o["A"], sign=True))) for o in worlds]
    centred = money(mu) != "$0.00"  # an expected value of nothing at the cent drops out of each term
    term = (lambda o: f"{operand(o['p'])} × ({money(o['A'])} − {_neg(money(mu), mu)})²") if centred else \
        (lambda o: f"{operand(o['p'])} × {_neg(money(o['A']), o['A'])}²")  # noqa: E731
    formula = [eqn("Volatility = √(Σ p × (P&L − expected value)²)" if centred else "Volatility = √(Σ p × P&L²)"),
               eqn_sum("= √(", [term(o) for o in worlds], ")")]
    body = (f'<h3 class="sub">The outcomes</h3>'
            f'{receipt(rows, ("Volatility", "", num(either(out["volatility"], cents=True))), formula)}')
    spread = {s: x for s, x in (out.get("spread") or {}).items() if x is not None and money(x) != "$0.00"}
    if spread:  # a side with ways spreads within itself; a side left whole does not
        parts = [("Between Yes and No", worked("p × (1 − p) × full impact²", f"{P} × (1 − {P}) × {_neg(money(D), D)}²"),
                  "")]
        terms = ["p × (1 − p) × full impact²"]
        if "yes" in spread:
            parts.append(("Within Yes", worked("p × spread²", f"{P} × {money(spread['yes'])}²"), ""))
            terms.append("p × spread within Yes²")
        if "no" in spread:
            parts.append(("Within No", worked("(1 − p) × spread²", f"(1 − {P}) × {money(spread['no'])}²"), ""))
            terms.append("(1 − p) × spread within No²")
        body += (f'<h3 class="sub">Between the sides and within them</h3>'
                 f'{receipt(parts, None, [eqn_sum("Volatility² = ", terms)])}')
    return {"title": "Volatility", "html": head + body + full(reader, cid)}


def _inspect_portfolio_volatility(reader: Reader, _: str) -> dict:
    """The portfolio's volatility to the horizon's end: the standard deviation of its daily moves in dollars at
    today's holdings over the window, times the square root of the sessions to the horizon's end; and the holdings
    that move as their betas say before their own series begins."""
    cid = _basis(reader.derived("portfolio", "Portfolio", ("volatility",)), "volatility")
    out = reader.computation(cid).get("output") or {}
    title = "Horizon volatility"
    if out.get("volatility") is None:
        return {"title": title, "html": hero(title) + full(reader, cid)}
    first, last = out["window"]
    sd, n = out["daily_sd"], out["sessions_ahead"]
    head = hero(title, figure=e(either(out["volatility"], cents=True)), context="Portfolio")
    rows = [("Daily moves", f"the standard deviation of the portfolio's daily moves in dollars at today's holdings, "
                            f"{out['sessions']} sessions, {day(first)} to {day(last)}", num(money(sd))),
            ("Sessions", f"after the as-of to the horizon's end, {day(out['horizon_end'])}", num(str(n)))]
    formula = [eqn("Volatility = σ × √n"), eqn(f"= {money(sd)} × √{n}")]
    body = (f'<h3 class="sub">Its daily moves, to the horizon\'s end</h3>'
            f'{receipt(rows, ("Volatility", "", num(either(out["volatility"], cents=True))), formula)}')
    short = [(heading(name_of(reader.store, h["name"])), f"its own closes from {day(h['from'])}; before, as its betas say"
              if h["from"] else "no closes of its own: as its betas say", "")
             for h in out["by_holding"].values() if h["own_sessions"] < out["sessions"] - 1]
    if short:
        body += f'<h3 class="sub">Before a holding\'s own series</h3>{receipt(short)}'
    return {"title": title, "html": head + body + full(reader, cid)}


def _inspect_holding(reader: Reader, rest: str) -> dict:
    """What a holding does if the event happens: its dollars at the betas they move with, and each company's own shock
    on its dollars there, which together are its contribution; then what is priced in. A fund with under a year of
    prices moves as its holdings do, so its names are listed, each at its own move."""
    store = reader.store
    pid, hid = rest[:rest.index(":", rest.index(":") + 1)], rest[rest.index(":", rest.index(":") + 1) + 1:]
    cid = _basis(reader.derived(pid, "Proposition", ("full_impact",)), "full_impact")
    rec = reader.computation(cid)
    out, inputs = rec.get("output") or {}, rec.get("inputs") or {}
    h = next((x for x in out.get("by_holding") or [] if x["holding"] == hid), None)
    if h is None:
        return _missing()
    ticker = _holding_label(reader, h)
    head = hero(f"{e(ticker)}, full impact", figure=e(money(h["full_impact"], sign=True)),
                context=go(pid, e(title_of(store, pid))))
    outcomes = stats([("Yes", num(money(h["yes"], sign=True), _tone(h["yes"])), ""),
                      ("No", num(money(h["no"], sign=True), _tone(h["no"])), "")])
    if h.get("contract"):
        contract = h["contract"]
        rows = [("Full impact", _contract_operand(reader, pid, out, h), num(money(h["full_impact"], sign=True)))]
        each = [(e(o["title"]), _contract_operand(reader, pid, out, h, o["id"]),
                 num(money(h["worlds"][o["id"]], sign=True))) for o in _outcomes(reader, pid, out) if o["world"]]
        body = (outcomes + '<h3 class="sub">Quantity × discounted payoff × change in win probability</h3>' + receipt(rows) +
                '<h3 class="sub">In each outcome</h3><p class="aside">Quantity × discounted payoff × win probability, '
                'less its value today.</p>' + receipt(each))
        return {"title": f"{ticker}, full impact", "html": head + body + full(reader, cid)}
    sensitivity = (((reader.output("g", "portfolio") or {}).get("by_holding") or {}).get(hid) or {}).get("sensitivity")
    at, own = h.get("at_betas") or {}, h.get("own") or []
    entries = []  # each line's label, what it multiplies, and its contribution
    if at.get("dollars") and abs(at.get("full_impact") or 0.0) >= 0.005:
        how = "at its holdings' betas" if sensitivity == "holdings" else "at its betas"
        entries.append((f"{e(ticker)}, {how}", f'{num(dollars(at["dollars"]))} × {num(operand(at.get("move"), sign=True))}',
                        at["full_impact"]))
    for x in own:
        entries.append((f'{go(x["name"], e(name_of(reader.store, x["name"])))}, beyond its response',
                        f'{num(dollars(x["dollars"]))} × {num(operand((out.get("own_moves") or {}).get(x["name"], {}).get("E"), sign=True))}', x["full_impact"]))
    if abs(((h.get("curve") or {}).get("full_impact") or 0.0)) >= 0.005:
        entries.append((f"{e(ticker)}, the rest", "", h["curve"]["full_impact"]))
    terms = to_cents([x[2] for x in entries], h["full_impact"])  # to the cent, adding to the contribution as shown
    rows = [(label_, what, num(money(t, sign=True))) for (label_, what, _), t in zip(entries, terms)]
    body = [outcomes, ('<h3 class="sub">Exposure × move</h3>' + receipt(rows, ("Full impact", "", num(money(h["full_impact"], sign=True)))))
            if rows else '<p class="aside">Nothing it holds moves with the event.</p>']
    if sensitivity == "holdings" and h["names"]:
        reached = ((reader.computation((inputs.get("lookthrough") or {}).get("basis")).get("output") or {})
                   .get("by_holding") or {}).get(hid) or {}
        listed = [(go(n["name"], e(name_of(reader.store, n["name"]))),
                   f'{num(dollars(reached.get(n["name"])))} × {num(operand(n.get("move"), sign=True))}',
                   num(money(n["full_impact"], sign=True))) for n in h["names"][:LISTED]]
        more = f'<p class="aside">{len(h["names"]) - LISTED} more.</p>' if len(h["names"]) > LISTED else ""
        body.append(f'<h3 class="sub">By name</h3>{receipt(listed)}{more}')
    body.append(f'<h3 class="sub">In each outcome</h3>{receipt(_holding_in_each(reader, pid, out, h))}')
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
                ("Initial pricing volatility", "annual", num(percent(contract["volatility"]))) ]
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
            f'<th>Yes</th><th>No</th></tr></thead><tbody>{rows_}</tbody></table>')


def _sensitivities(reader: Reader, b: dict, fit: dict) -> str:
    """A name's betas, one to each factor in order, each factor below the first net of the factors above it: fitted on
    its own daily returns, a fund's on the fund's, or, for a fund with too few, weighted from its holdings."""
    store = reader.store
    names = channel_names(store)
    order = [c["id"] for c in sorted(store.query("Factor"), key=lambda c: c["stated"]["order"])]
    shown = [c for c in order if c in b]
    rows_ = "".join(f'<tr><td>{go(c, e(heading(names.get(c, c))))}</td><td>{num(sens(b[c]))}</td></tr>' for c in shown)
    leans = {"population": "its comparables", "rows": "its holdings", "market": "the market's"}.get(
        (fit.get("prior") or {}).get("from"))
    how = (f"{fit['n']} daily returns, leaning on {leans} where they say little" if leans else
           f"{fit['n']} daily returns") if fit.get("n") is not None else ""
    net = "each factor net of those above it" if len(shown) > 1 else ""
    caption = "; ".join(x for x in (how, net) if x)
    return (f'<h3 class="sub">Betas</h3>{f"<p class=aside>{caption}.</p>" if caption else ""}'
            f'<table class="calc-t"><tbody>{rows_}</tbody></table>')


def _rows_of(entries: list[tuple[str, float, float]], rest: tuple[str, float, float] | None = None, *,
             first: int = CONSTITUENTS_SHOWN) -> list[str]:
    """Table rows, each a label with its weight of the fund and your exposure to it, then, always shown, the rest
    together: the weights in tenths of a percent and the dollars, whole, apportioned so that each column adds to its
    total, which ends the table; the rows past `first` open with "Show N more"."""
    every = entries + ([rest] if rest else [])
    tenths = apportion([w * 1000 for _, w, _ in every])
    amounts = whole_parts([d for _, _, d in every], sum(d for _, _, d in every))
    tenth = lambda t: f"{MINUS if t < 0 else ''}{abs(t) / 10:.1f}%"  # noqa: E731
    row = lambda label, t, n, cls="": (f'<tr class="{cls}"><td>{label}</td><td>{num(tenth(t))}</td>'  # noqa: E731
                                       f'<td>{num(amount(n, sign=False))}</td></tr>')
    kept = [(label, t, n) for (label, _, _), t, n in zip(entries, tenths, amounts) if t or n]  # nothing shown, no row
    rows_ = [row(label, t, n, "more" if i >= first else "") for i, (label, t, n) in enumerate(kept)]
    if len(kept) > first:
        rows_.append(f'<tr class="all"><td colspan="3"><button class="linkish" type="button" data-all>Show '
                     f'{len(kept) - first} more</button></td></tr>')
    if rest:
        rows_.append(row(rest[0], tenths[-1], amounts[-1], "rest"))
    rows_.append(f'<tr class="sum"><td>Total</td><td>{num(tenth(sum(tenths)))}</td>'
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
    if dollars(holds["weights_sum"] * holds["dollars"]) != dollars(holds["dollars"]):
        out.append(f'<p class="aside">Published weights sum to {_sum_percent(holds["weights_sum"])}.</p>')
    priced = sum(1 for h in holds["named"] if h.get("priced_as"))
    if fund.get("expanded"):
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
    """What a company does, in its reference's words; its industry and its country, as the portfolio's weight by
    company places it (MATH.md 3.1), each opening to the names in it."""
    does = ((reader.output("reference", nid) or {}).get("company") or {}).get("does")
    placed = _company_of(reader.store, nid)
    pairs = []
    if placed.get("industry"):
        pairs.append(("Industry", go(f"industry:{placed['industry']}", e(placed["industry"]))))
    if placed.get("country"):
        country = next((g.get("country_name") for g in (reader.output("weights", "portfolio") or {}).get("by_country")
                        or [] if g.get("country") == placed["country"]), None) or placed["country"]
        pairs.append(("Country", go(f"country:{placed['country']}", e(country))))
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
            parts = [f'through {go(through[0][0], e(name_of(reader.store, through[0][0])), "go name")}']
        else:
            whole = whole_parts(([direct] if direct else []) + [n for _, n in through], total)
            ways = (["directly"] if direct else []) + \
                [f'through {go(f, e(name_of(reader.store, f)), "go name")}' for f, _ in through]
            parts = [f"{amount(n, sign=False)} {w}" for n, w in zip(whole, ways) if n]
        facts.append(("Look-through exposure", num(amount(total, sign=False)), " · ".join(parts)))
    if facts:
        body.append(f'<h3 class="sub">{"Position" if holding else "Exposure"}</h3>{stats(facts)}')
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
    body, facts = [], []
    moves = holding is not None and _has_move(holding)
    if moves:
        pair = held_name(holding)
        prices = (reader.output("price", pair) or {}).get("prices")
        if prices and prices.get("price") is not None:
            body.append(_quote_lines(prices))
            body.append(_price_chart(price_points(reader, "price", pair), f"{title}: its rate in dollars"))
    if holding and holding["id"] in shown.value:
        facts += [("Value", num(dollars(shown.value[holding["id"]])), ""),
                  ("Weight", num(shown.weights.get(holding["id"], "")), "")]
        if moves and holding["id"] in shown.moved:
            facts.append(("Day change", num(dollars(shown.moved[holding["id"]], sign=True),
                                            _sign_tone(shown.moved[holding["id"]])), ""))
    if facts:
        body.append(f'<h3 class="sub">Position</h3>{stats(facts)}')
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
    rows_ = "".join(f'<tr><td>{go(h["to"], e(heading(held_as(h))), "go name")}</td>'
                    f'<td>{num(dollars(shown.value[h["id"]]))}</td><td>{num(sens(b))}</td>'
                    f'<td>{num(amount(part, sign=False))}</td></tr>' for part, b, h in kept)
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
    facts = []
    yield_ = found.get("per_point") is not None  # a factor quoted as the yield its proxy pays (MATH.md 3.2)
    if found.get("dollar_beta") is not None:
        facts.append(("Dollar beta", num(amount(found["dollar_beta"], sign=False)), ""))
        if yield_:
            facts.append(("Per point of yield", num(f'{amount(found["per_point"], sign=False)} /pp'), ""))
        if first and found.get("beta") is not None:
            facts.append(("Portfolio beta", num(sens(found["beta"])), ""))  # the portfolio's, never the index's own
    if found.get("largest_month") is not None and month:
        span = f'{day(month["start"], year=month["start"][:4] != month["end"][:4])} – {day(month["end"])}'
        said = (factor_reach(store, cid).get("largest_month") or {}).get("move") if yield_ else month["move"]
        shown = points(said) if yield_ else move(said)
        facts.append(("Largest month" if first else "Largest month of its residual",
                      num(shown, _move_tone(said, shown, points(0.0) if yield_ else move(0.0))), span))
        facts.append(("Largest month applied", num(amount(found["largest_month_applied"], sign=False)), ""))
    body.append(stats(facts))
    view_ = portfolio_view(reader, factors=False)
    if view_ and found.get("dollar_beta") is not None:
        body.append(_carriers(reader, view_, cid))
    if orth.get("n"):
        names = channel_names(store)
        if orth.get("coefficients"):
            less = " ".join(f'− {sens(b)} × {go(c, e(names.get(c, c)))}' for c, b in orth["coefficients"].items())
            how = f'<p>Residual = {e(proxy)} daily return {less}, fitted over {orth["n"]} days.</p>'
        else:
            how = f'<p>Return = {e(proxy)} daily total return, over {orth["n"]} days.</p>'
        body.append(f'<details class="how-measured"><summary>Method</summary>{how}</details>')
    return {"title": name, "html": head + "".join(body) + full(reader, v.basis if v else None)}


def _quoted(store: Store, sid: str) -> list[str]:
    """Every passage quoted from a page: in an evidence link, and in each stated value that cites it."""
    out = []
    for link in store.links_of(sid, "evidence", "in"):
        out += link["stated"].get("passages") or []
    for cite in store.links_of(sid, "cites", "in"):
        owner = cite["from"]
        kind = store.link_by_id(owner)["relation"] if store.type_of(owner) == "link" else store.type_of(owner)
        v = schema.assumptions(kind, store.stated_of(owner)).get(cite["field"])
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
    return {"title": "Passage", "html": head + f'<div class="page">{page}</div>' + full(reader, sid)}


def _missing() -> dict:
    return {"title": "Not found", "html": '<p class="why">This isn\'t in the conversation any more.</p>'}


def _how_set_there(store: Store, how: dict) -> str:
    """How an outcome sets a price (MATH.md 1.3): by a shock of its own, by one taken from the outcome above it, or
    moving as it ordinarily would with the factors that are set there."""
    said = AGAINST_WORDS[schema.against(how.get("stated") or {})]
    if how.get("by") == "own":
        return f"its own {said}"
    if how.get("by") == "parent":
        return f"the {said} on {quote(_world_label(store, how.get('on') or ''))}"
    return MOVES_WITH


def _inspect_outcome(reader: Reader, rest: str) -> dict:
    """An outcome opened: the portfolio's P&L there, its probability, and the weight today's prices put on it;
    for a world, each factor's move there against the baseline and how it is set, each price a shock sets there; the
    P&L in its parts as the engine gives them, each factor's dollar beta times its placed move, each company's own
    part, and the rest; and each holding's P&L there, the holdings adding to the outcome's P&L; for an
    outcome with ways, its ways and their average."""
    store = reader.store
    kind, n, w = (rest.split(":", 2) + ["", ""])[:3]  # the event's id, then the outcome's: yes, no, or a way's
    pid = f"{kind}:{n}"
    r = row(store, pid)
    tree = r.get("outcomes") or []
    o = next((x for x in tree if x["id"] == w), None)
    if o is None or o.get("A") is None:
        return _missing()
    head = hero("Outcome", figure=e(money(o["A"], sign=True)), title=e(o["title"]), context=go(pid, e(title_of(store, pid))),
                pills=[pill("baseline")] if o.get("baseline") else [])
    facts = [("Probability", num(chance(o["p"])), "")]
    if o.get("q") is not None:
        facts.append(("Priced in", num(chance(o["q"])), ""))
    at = (_distribution_of(reader, r)[0].get("worlds") or {}).get(w) or {}
    if at.get("mean") is not None:  # beside its P&L today: they differ where a holding is not in proportion to a price
        facts.append((f"Mean on {day(reader.store.horizon_end(), year=False)}", num(money(at["mean"], sign=True)),
                      "the P&L at the horizon's end, averaged over its draws"))
    body = [stats(facts)]
    if not o.get("world"):
        ways = [x for x in tree if x.get("parent") == o["id"] and x.get("A") is not None]
        rows = [(go(f"x:outcome:{pid}:{x['id']}", e(x["title"]), "go name"), f"probability {operand(x['p'])}",
                 num(money(x["A"], sign=True))) for x in ways]
        body.append('<h3 class="sub">Its ways</h3>' + receipt(
            rows, (e(o["title"]), _mean_of("its ways' P&L averaged at their probabilities", ways, operand(o["p"])),
                   num(money(o["A"], sign=True)))))
        return {"title": o["title"], "html": head + "".join(body), "wide": True}
    now = _now(reader, pid)
    names = []
    for item in (now.get("factors") or []) + (now.get("names") or []):
        how = (item.get("worlds") or {}).get(w)
        if not how:  # a price that does not move in this world against the baseline has no entry there
            continue
        target = item.get("factor") or item.get("name")
        label_ = item.get("name") if item.get("factor") else name_of(reader.store, target)
        names.append((go(target, e(heading(label_ or target)), "go name"), _how_set_there(store, how),
                      num(factor_move(item, how.get("move")))))
    if names:
        body.append(f'<h3 class="sub">Against the baseline</h3>{receipt(names)}')
    prices = []
    for target, got in (now.get("levels") or {}).items():
        unit = unit_of(store, price_metric(store, target))
        for there, today, shown_unit, what in ((got.get("worlds"), got.get("today"), unit, ""),
                                               (got.get("yields"), got.get("yield"), "%", ", its yield")):
            if there and there.get(w) is not None:
                link = ((_set_by(now, target).get(w) or {}).get("consequence") or
                        next((c["link"] for c in r["consequences"] if c["target"] == target), None))
                prices.append((go(target, e(heading(target_label(store, target))) + e(what), "go name"),
                               f"today {num(level(today, shown_unit))}",
                               gonum(f"x:consequence:{link}", level(there[w], shown_unit)) if link else
                               num(level(there[w], shown_unit))))
    if prices:
        body.append(f'<h3 class="sub">Prices</h3>{receipt(prices)}')
    cid = (r.get("basis") or {}).get("full_impact")
    rec = reader.computation(cid)
    out, g = rec.get("output") or {}, (rec.get("inputs") or {}).get("g") or {}
    got = (out.get("parts") or {}).get(w)
    if got:  # the engine's receipt of the outcome's P&L (MATH.md 1.6): g . u~_w, each company beyond its response, the rest
        factors, moved = channel_names(store), (out.get("components") or {}).get(w) or {}
        setter = {c["target"]: c["link"] for c in r["consequences"]}
        entries = [(go(c, e(heading(factors.get(c, c)))), f'{gonum(c, money(g.get(c)))} × '
                    f'{num(operand(moved.get(c), sign=True))}', amount)
                   for c, amount in sorted(got["factors"].items(), key=lambda kv: -abs(kv[1])) if money(amount) != "$0.00"]
        reach = out.get("reach") or {}
        for n, amount in sorted(got["own"].items(), key=lambda kv: -abs(kv[1])):
            if money(amount) == "$0.00":
                continue
            contracts = [h for h in out.get("by_holding") or [] if h["name"] == n and h.get("contract")]
            if contracts:
                entries.append((go(n, e(market_question(reader, n) or n)),
                                " + ".join(_contract_operand(reader, pid, out, h, w) for h in contracts), amount))
            else:
                entries.append((f"{go(n, e(name_of(store, n)))}, beyond its response",
                                f'{gonum(n, dollars(reach.get(n)))} × '
                                f'{num(operand((out.get("own_moves") or {}).get(n, {}).get(w), sign=True))}', amount))
        if money(got.get("curve") or 0.0) != "$0.00":
            entries.append(("The rest", "", got["curve"]))
        terms = to_cents([x[2] for x in entries], o["A"])  # to the cent, adding to the outcome's P&L as shown
        rows = [(label_, what, num(money(t, sign=True))) for (label_, what, _), t in zip(entries, terms)]
        body.append('<h3 class="sub">Dollar beta × move</h3>' + receipt(rows, ("P&amp;L", "", num(money(o["A"], sign=True)))))
    working = (reader.computation((r.get("basis") or {}).get("by_holding")).get("output") or {}).get("by_holding") \
        or r.get("by_holding") or []
    held = [h for h in working if money((h.get("worlds") or {}).get(w) or 0.0) != "$0.00"]
    shown = to_cents([h["worlds"][w] for h in held], o["A"])  # to the cent, adding to the outcome's P&L as shown
    rows = [(go(f"x:holding:{pid}:{h['holding']}", e(_holding_label(reader, h)), "go name"), "",
             num(money(v, sign=True), _tone(v))) for h, v in zip(held, shown)]
    body.append('<h3 class="sub">Each holding</h3>' + receipt(rows, ("P&amp;L", "", num(money(o["A"], sign=True)))))
    return {"title": o["title"], "html": head + "".join(body) + full(reader, cid), "wide": True}


INSPECT = {"outcome": _inspect_outcome, "p": _inspect_point, "interval": _inspect_interval, "price": _inspect_price,
           "consequence": _inspect_consequence, "swing": _inspect_swing, "volatility": _inspect_volatility,
           "portfolio_volatility": _inspect_portfolio_volatility,
           "holding": _inspect_holding, "passage": _inspect_passage}


# ---------------------------------------------------------------- the other objects, opened on the right


def _market_view(reader: Reader, mid: str) -> dict:
    """A market: its question, its quote, when it closes and what settles it, the propositions it is matched to, the
    notes about it, and its rules."""
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
    facts = [("Book", f'<a class="go" data-go="{e("x:price:" + mid)}">{quotes(price.get("bid"), price.get("ask"), html=True)}</a>'
              if price.get("bid") is not None or price.get("ask") is not None else "", ""),
             ("Midpoint", gonum("x:price:" + mid, cents(price["price"])) if price.get("price") is not None else "", ""),
             ("Depth", num(amount(price["depth"], sign=False)) if price.get("depth") is not None else "", ""),
             ("Closes", e(day(listing.get("close_time"))) if listing.get("close_time") else "", ""),
             ("Settled by", shown_source, "")]
    body = [stats(facts)]
    held = [h for h in store.links_of("portfolio", "holds") if h["to"] == mid]
    if held:
        positions = (reader.output("holdings", "portfolio") or {}).get("holds") or {}
        rows = [(h["stated"]["side"].title(), f'{count(h["stated"]["quantity"])} contracts',
                 num(money((positions.get(h["id"]) or {}).get("dollars")))) for h in held]
        body.append('<h3 class="sub">Your holdings</h3>' + receipt(rows))
    asks = store.links_of(mid, "asks", "in")
    if asks:
        items = "".join(f'<li>{go(link["from"], e(title_of(store, link["from"])))}'
                        f'{" · through its No side" if link["stated"].get("side") == "no" else ""}</li>'
                        for link in asks)
        body.append(f'<h3 class="sub">Asks</h3><ul class="plainlist matched">{items}</ul>')
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
    facts = [("Today", _today(reader, mid, unit), "")]
    if not price:
        stated = store.stated_of(mid)
        facts.append(("Kind", {"price": "a price", "yield": "a yield"}.get(stated.get("kind"), "a level"), ""))
        if stated.get("resolution_source"):
            facts.append(("Source", e(stated["resolution_source"]), ""))
    for q in store.links_of(mid, "quotes"):  # the factor whose price it is, in its own unit
        facts.append(("Quotes", go(q["to"], e(heading(target_label(store, q["to"])))), ""))
    body = [stats(facts)]
    return {"title": heading(metric_label(store, mid)), "html": head + "".join(body) + full(reader, mid)}


def _proposition_view(reader: Reader, pid: str) -> dict:
    """A proposition the event reaches: what it says, its forecast where one is worked out, the market-implied range,
    the markets that ask it, and the analyst's rationale."""
    store = reader.store
    stated = store.stated_of(pid)
    d = reader.derived(pid, "Proposition", READING)
    p, lo, hi = _value(d, "probability"), (_value(d, "range") or [None, None])[0], (_value(d, "range") or [None, None])[1]
    rows = []
    if p is not None:
        put = _put(lo, hi, "x:interval:" + pid) if lo is not None and hi is not None else None
        rows.append([("b", "Forecast"), ("go", chance(p), "x:p:" + pid), *([("aside", put, ", ")] if put else [])])
    elif _span(_value(d, "left_open")):  # the range its statements leave it
        rows.append([("b", "Forecast"), ("go", _span(_value(d, "left_open")), "x:p:" + pid)])
    head = (hero("Proposition", title=title_html(stated.get("title")))
            + f'<p class="text">{e(stated.get("text"))}</p>'
            + _when_known(stated, store.horizon_end(), store.meta("asof")))
    body = [f'<div class="sentences">{figures_html(rows)}</div>' if rows else ""]
    markets = "".join(_market_item(reader, link) for link in store.links_of(pid, "asks"))
    if markets:
        body.append(f'<h3 class="sub">Markets</h3><ul class="mkts">{markets}</ul>')
    notes = [f'<div class="basis note">{fold(note_html(store.stated_of(a["from"]).get("text")))}</div>'
             for a in store.links_of(pid, "about", "in")]
    if notes:
        body.append('<h3 class="sub">Rationale</h3>' + "".join(notes))
    return {"title": stated.get("title") or pid, "html": head + "".join(body) + full(reader, pid)}


def _source_view(reader: Reader, sid: str) -> dict:
    src = reader.store.source_row(sid) or {}
    dated = f" · dated {day(src.get('dated'))}" if src.get("dated") else ""
    head = hero("Source", title=e(host(src.get("url"))),
                context=f'<a href="{e(src.get("url"))}" target="_blank" rel="noopener">{e(src.get("url"))}</a>{dated}')
    return {"title": host(src.get("url")), "html": head + f'<div class="page">{e(src.get("text"))}</div>' + full(reader, sid)}


def _note_view(reader: Reader, nid: str) -> dict:
    store = reader.store
    about = [go(link["to"], e(label(store, link["to"]) or link["to"])) for link in store.links_of(nid, "about")]
    head = hero("Rationale", context=f"About {series_of(about)}" if about else "")
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
    return {"title": "Full details", "html": hero("Full details", title=e(what)) + _record(got)}


def _record(obj: Any) -> str:
    return f'<pre class="record">{e(json.dumps(obj, indent=1, ensure_ascii=False, default=str))}</pre>'


def view(store: Store, target: str, *, pending: bool = False) -> dict:
    """One step of the right panel: an event, a number's explanation, or an object, with its whole record one click
    away. Whatever is opened is worked on, so each takes the main area beside the conversation (`wide`)."""
    return {**_view(store, target, pending=pending), "wide": True}


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
            return _group_view(reader, by, key)
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
        if kind == "Judgment":  # the judge's readings are kept for us, in the store and the record; the page shows none
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
        kind, said = _stated(link)
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
    for oid in ids:
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
                value = dollars(shown.value[held[oid]])
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
    lines += [f"As of {day(asof)}. {_when(store, holdings)}.", ""]
    if holdings and shown.total is not None:
        moved = f", {dollars(shown.total_moved, sign=True)} ({move(holdings.get('session_move'))}){_session(holdings)}" \
            if shown.total_moved is not None else ""
        lines += [f"## Portfolio: {dollars(shown.total)}{moved}", ""]
        vol = (reader.derived("portfolio", "Portfolio", ("volatility",)).get("volatility") or {}).get("value")
        if vol is not None:
            lines += [f"Horizon volatility {either(vol)}", ""]
        lines += ["| Holding | Value | Weight | Day change |", "|---|---:|---:|---:|"]
        for h in view_["holds"]:
            got = holdings.get("holds", {}).get(h["id"]) or {}
            mv = f"{dollars(shown.moved[h['id']], sign=True)} ({move(got.get('session_move'))})" \
                if h["id"] in shown.moved and _has_move(h) else ""
            lines.append(f"| {_holding_label(reader, h)} | {dollars(shown.value.get(h['id']))} | "
                         f"{shown.weights.get(h['id'], '')} | {mv} |")
        lines.append("")
    groups = _groups_of(reader, shown.total)
    for by, word in BY if groups else ():
        listed = [g for g in groups[by] if not g.got.get("kind")]
        lines += [f"### Weight by {word.lower()}", "", f"| {word} | Value | Weight |", "|---|---:|---:|"]
        for g in listed[:DOWNLOAD_SHOWN]:
            held = ""
            if by == "company" and g.got.get("name") and view_["lookthrough"]:
                _, direct, through = held_of(view_, g.got["name"])
                if through and not direct and len(through) == 1:
                    held = f" (through {name_of(store, through[0][0])})"
                elif through:
                    parts = whole_parts(([direct] if direct else []) + [n for _, n in through], g.dollars)
                    names = (["directly"] if direct else []) + [f"through {name_of(store, f)}" for f, _ in through]
                    held = " (" + "; ".join(f"{amount(n, sign=False)} {w}" for n, w in zip(parts, names) if n) + ")"
            label = f"{g.label}, {name_of(store, g.got['name'])}" if by == "company" and g.got.get("name") else g.label
            lines.append(f"| {label}{held} | {amount(g.dollars, sign=False)} | {_tenth(g.tenths)} |")
        if len(listed) > DOWNLOAD_SHOWN:
            lines.append(f"| and {len(listed) - DOWNLOAD_SHOWN} more | | |")
        lines.append("")
    reach = view_["reach"]
    if reach and reach.get("factors"):
        first = reach["factors"][0]["factor"]
        lines += ["### Factor exposures", "", "| Factor | Dollar beta | Beta | Largest month |", "|---|---:|---:|---:|"]
        for c in sorted((c for c in reach["factors"] if c["largest_month_applied"] is not None),
                        key=lambda c: -c["largest_month_applied"]):
            beta = sens(c["beta"]) if c["factor"] == first and c.get("beta") is not None else ""
            lines.append(f"| {heading(c['name'])} | {amount(c['dollar_beta'], sign=False)} | {beta} | "
                         f"{amount(c['largest_month_applied'], sign=False)} |")
        lines.append("")
    found = _ledger(reader)
    lines += ["## Events by volatility", ""]
    if not found:
        lines += ["No events yet.", ""]
    else:
        days = store.stated_of("portfolio").get("horizon_days")
        lines += [f"Horizon {day(found['horizon_end'])} · {days} days", ""]
    for i, r in enumerate(by_volatility((found or {}).get("rows") or []), 1):
        when = " · ".join(f"{k}: {v}" for k, v in _when_pairs(store.stated_of(r["id"]), found["horizon_end"], asof))
        lines += [f"### {i}. {r['title']}", "", r["text"], "", when, ""]
        said = figures_text(sentences(reader, r))
        if said:
            lines += [said, ""]
    return "\n".join(lines).rstrip() + "\n"


