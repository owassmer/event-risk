"""The ledger, check, and what a store records: queries over the store.

Each reads derived values on read, as `get` does, and computes no number of its own but the sums of what it leaves
out: a row's fields are the watched proposition's derived fields and its links' derived fields, and the contributions,
what each consequence sets, and the names' idiosyncratic terms are read from the impact Computation through the basis
its fields carry. A row names each holding's largest names and sums the shares of the rest, and what the portfolio
owns gives each fund's largest holdings, places, and kinds of asset and sums the others, so that each stays readable;
every name is in the impact Computation, and every holding of a fund in the look-through's.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from harness import schema
from harness.derive.checks import disputed
from harness.derive.engine import Unavailable
from harness.derive.portfolio import NAME_TYPES, pseudo
from harness.derive.propagate import component, store_links
from harness.store import Store, StoreError
from harness.trace import Trace

MOSTLY = 3  # the three names with the largest abs(by_name)
NAMED = 5  # the names a row gives each holding, those with the largest abs(D) through it
ROW_FIELDS = ("p", "lo", "hi", "coverage", "u_E", "primary_channel", "D", "by_name", "by_holding", "pnl_yes",
              "pnl_no", "emove", "worst")
TOL = 1e-9


def _value(derived: dict, field: str) -> Any:
    return (derived.get(field) or {}).get("value")


def _portfolio(store: Store) -> dict:
    if store.type_of("portfolio") != "Portfolio":
        raise StoreError("the store has no portfolio: add(\"Portfolio\", ...) adds it")
    return store.get("portfolio")


def _watched(store: Store) -> list[str]:
    return [link["to"] for link in store.links_of("portfolio", "watches")]


def _title(store: Store, oid: str) -> str | None:
    return store.stated_of(oid).get("title") or store.stated_of(oid).get("name")


def _company(store: Store, nid: str) -> str | None:
    """The name the ticker reference gives, when the store holds it."""
    asof = store.meta("asof")
    ticker = nid.split(":", 1)[1]
    try:
        row = store.data("ticker_ref", ticker, covering=(asof, asof), required=False)
    except Exception:
        return None
    if row is None:
        return None
    return ((store.data_payload(row["id"]) or {}).get("results") or {}).get("name")


# ---------------------------------------------------------------- one row


def _decomposition(store: Store, pid: str) -> list[str]:
    return component(store_links(store), pid)


def _searches(store: Store, props: list[str]) -> dict:
    """A reviewer's counts over the propositions of the event's decomposition."""
    markets, web, company, seen = [], [], [], set()
    for pid in props:
        for s in store._searches_of(pid):  # recorded on it, or returned a market or a page it uses
            if s["id"] not in seen:
                seen.add(s["id"])
                {"search_markets": markets, "search_web": web, "search_company": company}[s["tool"]].append(s)
    returned = {m for s in markets for m in s["results"]}
    linked = {link["to"] for pid in props for link in store.links_of(pid, "quoted_by")}
    sources = {link["to"] for pid in props for link in store.links_of(pid, "evidence")}
    return {"market_searches": len(markets), "markets_returned": len(returned), "markets_linked": len(linked),
            "web_searches": len(web), "sources_linked": len(sources),
            "queries": [{"search": s["id"], "proposition": s["proposition"], "venue": s.get("venue"),
                         "endpoint": s.get("endpoint"), "params": s["params"], "returned": len(s["results"])}
                        for s in markets],
            "web_queries": [{"search": s["id"], "proposition": s["proposition"], "query": s.get("query"),
                             "returned": len(s["results"])} for s in web],
            "company_searches": len(company),
            "company_queries": [{"search": s["id"], "ticker": s.get("query"), "kind": s.get("endpoint"),
                                 "params": s["params"], "returned": len(s["results"])} for s in company]}


def _markets(store: Store, props: list[str]) -> list[dict]:
    """Every market linked in the breakdown, with its price, bid, ask, depth, whether it is open, the judge's check
    both ways, and the bound it gives."""
    out = []
    for pid in props:
        for link in store.links_of(pid, "quoted_by"):
            got = store.get(link["id"])["derived"]
            market = store.get(link["to"])["derived"]
            out.append({"link": link["id"], "proposition": pid, "market": link["to"],
                        "question": _value(market, "question"), "close_time": _value(market, "close_time"),
                        "transform": link["stated"].get("transform", "identity"),
                        **{k: _value(market, k) for k in ("price", "bid", "ask", "depth", "open")},
                        "forward": _value(got, "forward"), "converse": _value(got, "converse"),
                        "bound": _value(got, "bound")})
    return out


def _by_name(store: Store, shares: list[dict] | None) -> list[dict] | None:
    if shares is None:
        return None
    out = []
    for s in shares[:MOSTLY]:  # sorted by abs(D); every name is in the impact Computation, the row's basis
        nid = s["name"]
        held = any(link["to"] == nid for link in store.links_of("portfolio", "holds"))
        through = sorted({n["object"]["id"] for n in store.neighbors(nid, "looks_through_to", "in")}) \
            if store.type_of(nid) == "Name" else []
        out.append({**s, "company": _company(store, nid) if store.type_of(nid) == "Name" else None,
                    "held_directly": held, "through": through})
    return out


def _by_holding(shares: list[dict] | None) -> list[dict] | None:
    """Each holding with the NAMED names that carry most of its share, sorted by abs(D) in the impact Computation, and
    the rest as their count and summed share."""
    if shares is None:
        return None
    out = []
    for h in shares:
        named, rest = h["names"][:NAMED], h["names"][NAMED:]
        out.append({**h, "names": named + ([{"rest": len(rest), "D": sum(n["D"] for n in rest)}] if rest else [])})
    return out


def row(store: Store, pid: str) -> dict:
    """One watched proposition with every field the ledger's sentences read, each null with its reason
    where it has no value."""
    obj = store.get(pid)
    stated, derived = obj["stated"], obj["derived"]
    out: dict[str, Any] = {"id": pid, **{k: stated.get(k) for k in (
        "title", "text", "metric", "relation", "value", "window_start", "window_end", "kind", "resolution_source")}}
    for f in ROW_FIELDS:
        out[f] = _value(derived, f)
    out["reasons"] = {f: derived[f]["reason"] for f in ROW_FIELDS if f in derived and derived[f].get("value") is None
                      and derived[f].get("reason")}
    out["basis"] = {f: derived[f].get("basis") for f in ROW_FIELDS if derived.get(f, {}).get("basis")}
    impact = store.computation_output(out["basis"]["D"]) if out["basis"].get("D") else {}
    channels = {c["id"]: c["stated"]["name"] for c in store.query("Channel")}
    out["share_yes"], out["share_no"] = impact.get("share_yes"), impact.get("share_no")  # of V, in each world
    out["contributions"] = [{"channel": c, "name": channels.get(c, c), "dollars": v}
                            for c, v in sorted((impact.get("contributions") or {}).items(), key=lambda kv: -abs(kv[1]))]
    if out["primary_channel"] is not None:
        out["primary_channel"] = {"channel": out["primary_channel"], "name": channels.get(out["primary_channel"])}
    out["names"] = len(out["by_name"]) if out["by_name"] is not None else None
    out["by_name"] = _by_name(store, out["by_name"])
    out["by_holding"] = _by_holding(out["by_holding"])
    point = store.computation_output(out["basis"]["p"]) if out["basis"].get("p") else {}
    out["point"] = {k: point[k] for k in ("rule", "market", "link", "row", "formula", "roles", "reads", "chances",
                                          "chance") if k in point}
    out["consequences"] = [_consequence(store, link, (impact.get("consequences") or {}).get(link["to"]) or {})
                           for link in sorted(store.links_of(pid, "consequence"), key=lambda link: link["to"])]
    out["d"] = impact.get("d")  # the idiosyncratic terms: each name a consequence on its price sets
    props = _decomposition(store, pid)
    out["decomposition"] = props
    out["markets"] = _markets(store, props)
    out["past"] = any(_quoted_from_history(store, m["market"]) for m in out["markets"])
    out["searches"] = _searches(store, props)
    out["notes"] = [{"id": link["from"], "text": store.stated_of(link["from"]).get("text")}
                    for link in store.links_of(pid, "about", "in")]
    return out


CONSEQUENCE_FIELDS = ("gap", "L_yes", "L_no")


def event_now(store: Store, pid: str) -> dict:
    """What a watched event now does, from its impact: each factor's raw move in the gap, in channel order, whether a
    consequence sets it or it moves with the earlier factors by its coefficients on them; and each holding's full
    impact, its share of D in dollars and that as a share of the holding, largest first. Or why there is none."""
    if not store.watched(pid):
        return {"id": pid, "reason": f"{pid} is not watched: what an event does to the portfolio is its impact, which "
                                     f"the store computes for an event the portfolio watches"}
    found = store.get(pid)["derived"].get("D") or {}
    if found.get("value") is None:
        return {"id": pid, "reason": found.get("reason") or f"{pid} has no impact yet"}
    impact = store.computation_output(found["basis"])
    channels = sorted(store.query("Channel"), key=lambda c: (c["stated"]["order"], c["id"]))
    set_by = set(impact["consequence_set"])
    factors = [{"channel": c["id"], "name": c["stated"]["name"], "move": round(impact["raw"][c["id"]], 6),
                "set": c["id"] in set_by} for c in channels if c["id"] in impact["raw"]]
    holdings = [{"ticker": (h["name"] or "").split(":", 1)[-1], "D": round(h["D"], 2),
                 "share": round(h["D"] / h["at_betas"]["dollars"], 6) if h["at_betas"]["dollars"] else None}
                for h in sorted(impact["by_holding"], key=lambda h: (-abs(h["D"]), h["name"] or ""))]
    return {"id": pid, "basis": found["basis"], "factors": factors, "holdings": holdings}


def _consequence(store: Store, link: dict, shown: dict) -> dict:
    """One consequence as the row shows it: the metric, the stated figure and what it means, the gap, a traded
    metric's two levels, and what it sets, each null with its reason."""
    got = store.get(link["id"])["derived"]
    out = {"link": link["id"], "metric": link["to"], "figure": link["stated"]["value"],
           "meaning": link["stated"]["meaning"], **{f: _value(got, f) for f in CONSEQUENCE_FIELDS}}
    placed = store.computation_output(got["gap"]["basis"]) if (got.get("gap") or {}).get("basis") else {}
    out.update(c=placed.get("c"), traded=placed.get("traded"), sets=shown.get("sets"))
    reasons = {f: got[f]["reason"] for f in CONSEQUENCE_FIELDS if f in got and got[f].get("value") is None
               and got[f].get("reason")}
    if shown.get("reason"):
        reasons["sets"] = shown["reason"]
    if reasons:
        out["reasons"] = reasons
    return out


def _quoted_from_history(store: Store, mid: str) -> bool:
    """A quote read from history at a past as-of, whose depth is unknown."""
    return bool((store.engine.value("market_price", mid).output or {}).get("past"))


def by_emove(rows: list[dict]) -> list[dict]:
    """The ledger's ranking: the largest expected move first; a row with no emove yet after those with one."""
    return sorted(rows, key=lambda r: (r["emove"] is None, -(r["emove"] or 0.0), r["id"]))


def by_worst(rows: list[dict]) -> list[dict]:
    """The ledger's other order: the most negative worst first, so the unlikely event with the large loss leads."""
    return sorted(rows, key=lambda r: (r["worst"] is None, r["worst"] if r["worst"] is not None else 0.0, r["id"]))


def ledger(store: Store) -> dict:
    """Every watched proposition with every field, coverage on every row, ranked by emove, and the
    worst order beside them, as the rows' ids; and the end of the period the ledger looks over."""
    portfolio = _portfolio(store)
    rows = [row(store, pid) for pid in _watched(store)]
    return {"asof": portfolio["stated"]["asof"], "V": _value(portfolio["derived"], "V"),
            "horizon_end": store.horizon_end(), "rows": by_emove(rows), "worst": [r["id"] for r in by_worst(rows)]}


# ---------------------------------------------------------------- check


def _components(store: Store) -> list[list[str]]:
    seen, out = set(), []
    for p in store.query("Proposition"):
        if p["id"] in seen:
            continue
        props = component(store_links(store), p["id"])
        seen.update(props)
        out.append(props)
    return out


def channel_reach(store: Store) -> dict:
    """Each channel's dollar beta g, the portfolio's beta to it, g over V, its largest one-month move M_c, and
    abs(g) * M_c: what such a month would do to the portfolio, a measured fact."""
    portfolio = _portfolio(store)
    value, g = _value(portfolio["derived"], "V"), _value(portfolio["derived"], "g")
    if value is None or g is None:
        return {"V": value, "channels": None, "reason": "; ".join(
            r for r in ((portfolio["derived"].get(f) or {}).get("reason") for f in ("V", "g")) if r)}
    out = []
    for c in sorted(store.query("Channel"), key=lambda c: c["stated"]["order"]):
        found = store.get(c["id"])["derived"].get("orthogonalization") or {}
        m_c = (found.get("value") or {}).get("largest_month")
        exposure = g.get(c["id"], 0.0)
        out.append({"channel": c["id"], "name": c["stated"]["name"], "proxy": c["stated"]["proxy_ticker"],
                    "g": exposure, "beta": exposure / value if value else None, "M_c": m_c,
                    "g_times_M_c": abs(exposure) * m_c if m_c is not None else None,
                    **({"reason": found.get("reason")} if m_c is None else {})})
    return {"V": value, "channels": out}


NAMES_GIVEN = 25  # the largest names after look-through that adding the portfolio returns
NEWS_NAMES = 10  # the largest companies whose latest news adding the portfolio returns
FUND_GIVEN = 10  # a fund's largest holdings, places, and kinds of asset that adding the portfolio returns


def _shown(item: dict, *, keep_null: bool = False) -> dict:
    """An entry as the return gives it: dollars to the cent, weights to six places, and, unless kept, only the fields
    it has."""
    out = {}
    for k, v in item.items():
        if v is None and not keep_null:
            continue
        out[k] = round(v, 2) if k == "dollars" and v is not None else round(v, 6) if k == "weight" and v is not None \
            else v
    return out


def _largest(groups: list[dict]) -> list[dict]:
    """The FUND_GIVEN largest groups, then the others summed, with how many."""
    shown = [_shown(g, keep_null=True) for g in groups[:FUND_GIVEN]]
    rest = groups[FUND_GIVEN:]
    if rest:
        shown.append(_shown({"others": len(rest), "weight": sum(g["weight"] for g in rest),
                             "dollars": sum(g["dollars"] for g in rest)}))
    return shown


def _what_it_is(store: Store, nid: str, fund: dict | None) -> dict:
    """What a holding is: a fund, with what it holds, its largest holdings, how its dollars divide by where its
    holdings are listed and by kind of asset, and whether the look-through prices them; or a company, with what it
    does and its industry."""
    company = store.engine.value("reference", nid).output or {}
    found = company.get("company") or {}
    if fund is None:
        return {"company": _shown(found)} if found else {}
    holds = fund.get("holds") or {}
    out = {"name": found.get("name"), "looked_through": fund.get("expanded"), "reason": fund.get("reason"),
           "holdings_asof": holds.get("holdings_asof"), "holdings": holds.get("count")}
    if holds:
        out.update(largest=[_shown(h) for h in holds["named"][:FUND_GIVEN]], by_listing=_largest(holds["by_listing"]),
                   by_kind=_largest(holds["by_kind"]))
    return {"fund": _shown(out)}


def holdings(store: Store) -> dict:
    """What the portfolio owns. The holdings as given, each with its value, weight, and its price's session move, and
    what it is: a company, with what it does and its industry, or a fund, with its largest holdings and how its
    dollars divide by where its holdings are listed and by kind of asset; the portfolio's move, their sum; and the
    largest names after look-through, each with what is held directly and through which funds; and the latest news
    on the largest companies among them. Nothing about the channels: exposure() gives the portfolio's exposure to
    each."""
    portfolio = _portfolio(store)
    asof = portfolio["stated"]["asof"]
    held = store.engine.value("holdings", "portfolio")
    looked = store.engine.value("lookthrough", "portfolio")
    if held.output is None or looked.output is None:
        return {"asof": asof, "V": None, "holdings": None, "names": None, "reason": held.reason or looked.reason}
    funds = looked.output["funds"]
    rows, of_holding = [], {}
    for link in store.links_of("portfolio", "holds"):
        row = held.output["holds"][link["id"]]
        ticker = link["to"].split(":", 1)[1]
        of_holding[link["id"]] = ticker
        item = {"ticker": ticker, "given": link["stated"], "dollars": row["dollars"], "quantity": row["quantity"],
                "weight": row["weight"], "price": row["price"], "price_from": row.get("price_from"),
                "price_at": row.get("price_time") or row.get("session"), "session": row.get("session"),
                "previous_close": row.get("previous_close"), "session_move": row.get("session_move"),
                "session_move_dollars": row.get("session_move_dollars")}
        if ticker != "CASH":
            item.update(_what_it_is(store, link["to"], funds.get(ticker)))
        rows.append(item)
    names: dict[str, dict] = {}
    for hid, reached in looked.output["by_holding"].items():
        for nid, dollars in reached.items():
            if pseudo(nid.split(":", 1)[1]):
                continue  # cash, and what a fund holds that the look-through does not price on its own
            found = names.setdefault(nid, {"name": nid.split(":", 1)[1], "e": 0.0, "direct": 0.0, "through": {}})
            found["e"] += dollars
            if nid.split(":", 1)[1] == of_holding.get(hid):
                found["direct"] += dollars
            else:
                found["through"][of_holding.get(hid)] = found["through"].get(of_holding.get(hid), 0.0) + dollars
    ranked = sorted(names.values(), key=lambda n: (-abs(n["e"]), n["name"]))
    largest = ranked[:NAMES_GIVEN]
    for n in largest:
        company = ((store.engine.value("reference", f"name:{n['name']}").output or {}).get("company") or {})
        n["company"] = company.get("name")
    # The news looks back from the as-of over the span the horizon looks ahead over.
    since = (date.fromisoformat(asof) - timedelta(days=portfolio["stated"]["horizon_days"])).isoformat()
    news, unread = _news(store, _companies(store, ranked), since, asof)
    return {"asof": asof, "V": held.output["V"], "session_move": held.output.get("session_move"),
            "session_move_dollars": held.output.get("session_move_dollars"), "holdings": rows,
            "names": [{k: n[k] for k in ("name", "company", "e", "direct", "through")} for n in largest],
            "news": news, **({"news_unread": unread} if unread else {})}


def _companies(store: Store, ranked: list[dict]) -> list[str]:
    """The NEWS_NAMES largest names that are companies, a common stock or an ADR by its ticker reference. A fund's own
    news is about its flows and the market, so a fund gets none, whether it is booked whole or looked through."""
    out: list[str] = []
    for n in ranked:
        if len(out) == NEWS_NAMES:
            break
        if (store.engine.value("reference", f"name:{n['name']}").output or {}).get("type") in NAME_TYPES:
            out.append(n["name"])
    return out


def _news(store: Store, tickers: list[str], since: str, asof: str) -> tuple[list[dict], dict[str, str]]:
    """The latest news on the largest companies: each one's most recent articles published from `since` to the
    as-of, read side by side and kept as data, each article once, newest first, with every one of these companies it
    is about; its date, publisher, headline, summary, keywords, and link, as its source writes them. A company with no
    article then has none. A sentiment label is a reading of what the news means, which is the analyst's, so it stays
    out, as do images and logos. And each company whose news could not be read, with why."""
    store.prefetch("news", tickers, (since, asof), stage={"stage": "news"})
    articles: dict[str, dict] = {}
    unread: dict[str, str] = {}
    for ticker in tickers:
        try:
            row = store.data("news", ticker, covering=(since, asof), required=False)
        except Unavailable as e:
            unread[ticker] = str(e)
            continue
        if row is None:
            unread[ticker] = "the news is read with the Massive key, which is not set here"
            continue
        payload = store.data_payload(row["id"]) or {}
        if payload.get("status") != "OK":
            unread[ticker] = f"Massive answered {payload.get('http_status') or payload.get('status')} for its news"
            continue
        for r in payload.get("results") or []:
            articles.setdefault(r.get("id") or r.get("article_url"), r)
    order = {t: i for i, t in enumerate(tickers)}
    out = [{"published": r.get("published_utc"), "publisher": (r.get("publisher") or {}).get("name"),
            "title": r.get("title"), "summary": r.get("description"), "keywords": r.get("keywords") or [],
            "url": r.get("article_url"),
            "about": sorted((t for t in r.get("tickers") or [] if t in order), key=order.get)}
           for r in articles.values()]
    return sorted(out, key=lambda a: a["published"] or "", reverse=True), unread


def channel(store: Store, cid: str) -> dict:
    """A channel as adding it returns it: its stated fields and its proxy's price metric; how it is built, its
    orthogonalization, with its coefficients on the earlier channels and its largest month; and what it means for the
    portfolio, its dollar beta g, its beta g over V, M_c, and abs(g) * M_c, as exposure() gives them. Each name's beta
    to it is one get away."""
    stated = store.stated_of(cid)
    orth = store.engine.derived(cid, "Channel").get("orthogonalization") or {}
    out = {"id": cid, "type": "Channel", "stated": stated,
           "proxy_metric": schema.proxy_metric_id(stated["proxy_ticker"]), "orthogonalization": orth.get("value"),
           **({"reason": orth["reason"]} if orth.get("reason") else {})}
    reach = channel_reach(store)
    found = next((c for c in reach["channels"] or [] if c["channel"] == cid), None)
    if found is None:
        return {**out, "g": None, "beta": None, "M_c": None, "g_times_M_c": None,
                "exposure_reason": reach.get("reason")}
    return {**out, **{k: found[k] for k in ("g", "beta", "M_c", "g_times_M_c")}}


def exposure(store: Store) -> dict:
    """The portfolio's dollar beta to each channel the store models, g, with the channel's largest one-month move M_c
    and abs(g) * M_c, largest first, and each proxy's price and session move: the basis on which what an event does
    is sized. It runs no searches."""
    portfolio = _portfolio(store)
    reach = channel_reach(store)
    proxies = []
    for c in reach["channels"] or []:
        got = store.engine.value("proxy_price", f"metric:price:{c['proxy']}")
        out = got.output or {}
        proxies.append({"ticker": c["proxy"], "channel": c["channel"], "price": out.get("current_value"),
                        "price_from": out.get("from"), "price_at": out.get("time") or out.get("asof"),
                        "session": out.get("asof"), "previous_close": out.get("previous_close"),
                        "session_move": out.get("session_move"),
                        **({"reason": got.reason} if got.output is None else {})})
    channels = sorted(reach["channels"] or [], key=lambda c: -(c["g_times_M_c"] or 0.0))
    return {"asof": portfolio["stated"]["asof"], "V": reach["V"], "channels": channels, "proxies": proxies,
            **({"reason": reach["reason"]} if reach.get("reason") else {})}


def _noted(store: Store, oid: str) -> bool:
    """Whether a note is about the object or link."""
    return bool(store.links_of(oid, "about", "in"))


def _no_basis(store: Store) -> list[dict]:
    """Stated assumptions with neither a cited page nor a note about them: a proposition's chance, each
    chance of its derivation, a consequence's figure."""
    out = []
    for p in store.query("Proposition"):
        s, noted = p["stated"], _noted(store, p["id"])
        if s.get("chance") is not None and not s["chance"].get("source_id") and not noted:
            out.append({"id": p["id"], "field": "chance", "value": s["chance"]["value"]})
        for i, c in enumerate((s.get("derivation") or {}).get("chances") or []):
            if not c.get("source_id") and not noted:
                out.append({"id": p["id"], "field": f"derivation.chances[{i}]", "value": c["value"],
                            "of": c["of"], "given": c["given"], "given_not": c["given_not"]})
    for r in store._all("SELECT id FROM link WHERE relation = 'consequence' ORDER BY rowid"):
        link = store.link_by_id(r["id"])
        if not link["stated"]["value"].get("source_id") and not _noted(store, link["id"]):
            out.append({"id": link["id"], "field": "value", "value": link["stated"]["value"]["value"],
                        "meaning": link["stated"]["meaning"]})
    return out


def _disputes(store: Store) -> list[dict]:
    """The judge's checks below one half on claims the analyst has neither changed nor answered with a note about
    the link."""
    out = []
    for relation, fields in (("quoted_by", ("forward", "converse")), ("implies", ("check",))):
        for r in store._all("SELECT id FROM link WHERE relation = ? ORDER BY rowid", relation):
            lid = r["id"]
            if _noted(store, lid):
                continue
            got = store.get(lid)
            for f in fields:
                v = _value(got["derived"], f)
                if disputed(v):
                    basis = (got["derived"].get(f) or {}).get("basis")
                    out.append({"link": lid, "relation": relation, "from": got["from"], "to": got["to"],
                                "check": "implication" if f == "check" else f"market, {f}", "answer": v,
                                "basis": basis})
    return out


def _wrong(store: Store) -> dict:
    """Inputs that are wrong: bounds that cross, and a point outside its interval."""
    crossed, outside = [], []
    for props in _components(store):
        found = store.engine.value("propagate", props[0])
        for pid, node in ((found.output or {}).get("nodes") or {}).items():
            if node.get("crossed"):
                crossed.append({"id": pid, **node["crossed"], "basis": found.basis})
    for p in store.query("Proposition"):
        derived = store.get(p["id"])["derived"]
        point, lo, hi = (_value(derived, k) for k in ("p", "lo", "hi"))
        if point is not None and lo is not None and hi is not None and not lo - TOL <= point <= hi + TOL:
            outside.append({"id": p["id"], "p": point, "lo": lo, "hi": hi,
                            "rule": store.computation_output(derived["p"]["basis"]).get("rule")})
    return {"crossed": crossed, "outside_interval": outside}


def check(store: Store) -> dict:
    """What the analyst's work is missing or contradicts. It gates nothing."""
    watched = _watched(store)
    no_point = []
    for pid in watched:
        got = store.get(pid)["derived"].get("p") or {}
        if got.get("value") is None:
            no_point.append({"id": pid, "title": _title(store, pid), "reason": got.get("reason")})
    no_consequence = [{"id": pid, "title": _title(store, pid)} for pid in watched
                      if not store.links_of(pid, "consequence")]
    return {"watched_without_point": no_point, "watched_without_consequence": no_consequence,
            "assumptions_without_basis": _no_basis(store), "disputed": _disputes(store), **_wrong(store)}


# ---------------------------------------------------------------- what a store records


def records(store: Store, trace: list[dict] | None = None) -> dict:
    """What a store records, read back from the store and its trace: each conversation; checks by kind and by model;
    markets searched and linked, and coverage per event with its mean; the assumptions stated, with their bases; the
    consequences, channels, and metrics added; the links removed."""
    entries = trace if trace is not None else Trace(store.trace_path).entries()
    sessions: dict[str, dict] = {}
    removed = []
    for e in entries:
        s = sessions.setdefault(e.get("session"), {"session": e.get("session"), "turns": 0, "calls": 0,
                                                   "errors": 0, "tokens": {}})
        if e["type"] == "user":
            s["turns"] += 1
        elif e["type"] == "assistant":
            for k, v in (e.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    s["tokens"][k] = s["tokens"].get(k, 0) + v
        elif e["type"] == "call":
            s["calls"] += 1
            s["errors"] += "error" in e
            if e["tool"] == "remove" and "result" in e:
                got = e["result"]
                for link in [got["removed"], *got.get("cascaded", [])]:
                    if "relation" in link:
                        removed.append({"call": e["call"], **link})
    coverage = {}  # read first: what it derives may ask the judge
    if store.type_of("portfolio") == "Portfolio":
        for pid in _watched(store):
            coverage[pid] = _value(store.get(pid)["derived"], "coverage")
    known = [v for v in coverage.values() if v is not None]
    assumptions = [{"id": p["id"], "field": f, "value": p["stated"][f]} for p in store.query("Proposition")
                   for f in ("derivation", "chance") if p["stated"].get(f) is not None]
    consequence_links = [store.link_by_id(r["id"]) for r in
                         store._all("SELECT id FROM link WHERE relation = 'consequence' ORDER BY rowid")]
    assumptions += [{"id": link["id"], "field": "value", "value": link["stated"]["value"],
                     "meaning": link["stated"]["meaning"]} for link in consequence_links]
    for a in assumptions:
        a["noted"] = _noted(store, a["id"])
    by_kind: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for r in store._all("SELECT kind, model, count(*) AS n FROM judgment GROUP BY kind, model ORDER BY kind, model"):
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["n"]
        by_model[r["model"]] = by_model.get(r["model"], 0) + r["n"]
    searched = [dict(r) for r in store._all("SELECT id, tool, venue, endpoint, query, proposition_id, results, "
                                            "created_call FROM search ORDER BY rowid")]
    for s in searched:
        s["returned"] = len(json.loads(s.pop("results")))
    linked = [{"link": link["id"], "proposition": link["from"], "market": link["to"]}
              for link in (store.link_by_id(r["id"]) for r in store._all(
                  "SELECT id FROM link WHERE relation = 'quoted_by' ORDER BY rowid"))]
    title = lambda stated: stated.get("title") or stated.get("name")  # noqa: E731
    added = {kind: [{"id": r["id"], "title": title(json.loads(r["stated"])), "created_call": r["created_call"]}
                    for r in store._all("SELECT id, stated, created_call FROM object WHERE type = ? AND origin = "
                                        "'analyst' ORDER BY rowid", kind)]
             for kind in ("Channel", "Metric")}
    consequences = [{"link": r["id"], "event": r["from_id"], "metric": r["to_id"], "created_call": r["created_call"]}
                    for r in store._all("SELECT id, from_id, to_id, created_call FROM link WHERE relation = "
                                        "'consequence' ORDER BY rowid")]
    return {"conversations": [s for s in sessions.values() if s["session"] is not None],
            "judgments": {"by_question": by_kind, "by_model": by_model},
            "markets": {"searches": searched, "linked": linked},
            "coverage": {"by_event": coverage, "mean": sum(known) / len(known) if known else None},
            "added": {"channels": added["Channel"], "metrics": added["Metric"],
                      "consequences": consequences},
            "assumptions": assumptions, "removed_links": removed}

