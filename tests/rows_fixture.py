"""A look-through over fund rows stated in the rows' contract (harness.derive.rows): each row's `exposure`, signed, per
dollar of the fund's net assets, its `payoff`, `to`, and `kind`, as data's rows give them. The booking is production's
(portfolio.booked); only what the rows are and how a company resolves are stated: a row's company resolves to the
ticker it names, a company where its reference is a share's and a fund where it is a fund's. A store using it sets
`store.fixture_rows = {fund: [rows]}` before it adds the portfolio."""

from __future__ import annotations

from harness.derive import Derivation, Registry, default_registry, portfolio
from harness.derive.assets import Kinds
from harness.derive.identity import Lookup


def holding(ticker: str, exposure: float, **fields) -> dict:
    """A row of a share held, its exposure its weight."""
    return {"constituent_ticker": ticker, "weight": exposure, "asset_class": "Equity", "kind": "equity",
            "exposure": exposure, "payoff": "holding", "to": {"company": {"constituent_ticker": ticker}}, **fields}


def cash(exposure: float, currency: str | None = None) -> dict:
    """Cash, or with `currency`, a holding of another currency."""
    return {"constituent_name": "Cash", "weight": exposure, "asset_class": "Cash",
            "kind": "currency" if currency else "cash", "exposure": exposure, "payoff": "cash",
            **({"to": {"currency": currency}} if currency else {})}


def on_index(exposure: float, companies: list[tuple[str, float]] | None = None, *, weight: float = 0.0,
             values: str | None = None) -> dict:
    """A swap or a future on an index at its notional per dollar of the fund, with the index's companies where they
    are established, each (ticker, its weight in the index), and the symbol its own values are read under where
    they are (`values`)."""
    to = {"index": {"name": "An index", "identifier": "INDEX1", **({"values": values} if values else {})}}
    if companies is not None:
        to["companies"] = [{"constituent_ticker": t, "weight": w} for t, w in companies]
    return {"constituent_name": "A swap on an index", "weight": weight, "kind": "derivative", "exposure": exposure,
            "payoff": "linear", "to": to, "derivative": {"category": "SWP"}}


def on_reference(slope: float, companies: list[tuple[str, float]], follower: str) -> dict:
    """A fund's row on its one reference, an index the fund `follower` follows, its whole exposure the slope measured
    on the index's values (MATH.md 3.1), with the index's companies, each (ticker, its weight in the index)."""
    measured = {"slope": slope, "r2": 0.999, "sessions": 63, "from": "2026-08-07", "to": "2026-11-03"}
    to = {"index": {"name": "An index", "identifier": "INDEX1", "values": "I:INDEX1"}, "basket": {"fund": follower},
          "companies": [{"constituent_ticker": t, "weight": w} for t, w in companies]}
    return {"constituent_name": "the reference", "kind": "derivative", "payoff": "linear", "exposure": slope,
            "to": to, "measured": measured}


def _inputs(ctx, subject):
    holdings = ctx.value("holdings", "portfolio", ref=True)
    rows = ctx._store.fixture_rows
    named = {portfolio.ticker_of(r["name"]) for r in ctx.payload(holdings)["holds"].values()} | set(rows)
    for found in rows.values():
        for r in found:
            to = r.get("to") or {}
            named.add((to.get("company") or {}).get("constituent_ticker"))
            named.update(c.get("constituent_ticker") for c in to.get("companies") or [])
    named = sorted(t for t in named if t and t != portfolio.CASH)
    asof = portfolio.asof_of(ctx)
    return {"asof": asof, "holdings": holdings, "fund_rows": ctx.value("fund_rows", "portfolio", ref=True),
            "references": {t: ctx.data("ticker_ref", t, covering=(asof, asof)) for t in named}}


def _rows_inputs(ctx, subject):
    return {"rows": ctx._store.fixture_rows}


def _rows_compute(m, load):
    return {"rows": {f: {"status": "OK", "effective_date": "2026-11-03", "results": rows,
                         **({"measured": r["measured"], "seeks_multiple": {"form": "N-CEN", "accession": "A",
                                                                            "filed": "2026-08-14", "url": "u"}}
                            if (r := next((x for x in rows if x.get("measured")), None)) else {})}
                     for f, rows in m["rows"].items()}}


def _compute(m, load):
    references = {t: load(r) if r.get("data") else None for t, r in m["references"].items()}

    def resolved(row: dict) -> dict:
        ticker = row.get("constituent_ticker")
        kind = portfolio.kind_of(references.get(ticker))
        return {"ticker": ticker, "kind": kind} if kind in ("name", "fund") else {"ticker": None}

    constituents = load(m["fund_rows"])["rows"]
    return {**portfolio.booked({"asof": m["asof"], "holdings": {"output": load(m["holdings"])}}, references,
                               constituents, resolved, Lookup(lambda kind, subject: None, m["asof"]), Kinds()),
            "multiples": portfolio.multiples(constituents, resolved)}


def rows_registry() -> Registry:
    """The production registry with its look-through reading the fixture's rows."""
    real = default_registry()
    look = portfolio.LOOKTHROUGH
    fixture = Derivation("lookthrough", 1, "Portfolio", _inputs, _compute, subject_of=look.subject_of,
                         produces=look.produces, extra=look.extra)
    rows = Derivation("fund_rows", 1, "Portfolio", _rows_inputs, _rows_compute, subject_of=look.subject_of)
    return Registry([d for d in real._by_name.values() if d.name not in ("lookthrough", "fund_rows")] + [fixture, rows])
