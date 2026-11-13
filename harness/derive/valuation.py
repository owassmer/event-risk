"""Holding terms read once and valued at a world's centre or a horizon state.

Amounts are per share and in today's dollars. A cash payment keeps its present value;
shares received keep the receiving security's price in the same state. None of these
values is normalized back to today's quote: the difference is economic information.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Any, Callable

import numpy as np

from harness import schema
from harness.derive import channels, estimate, history, option_funds, options, portfolio, rates, rows, series
from harness.derive.engine import Derivation, Inputs, Unavailable


def years(start: str, end: str) -> float:
    return max((date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days / 365.0, 0.0)


def raw_response(betas: dict, changes: dict) -> float:
    """The common compounded response, with a zero-price factor entering at its simple move."""
    live = sum(b * math.log1p(changes.get(c, 0.0)) for c, b in betas.items()
               if b and changes.get(c, 0.0) > -1.0)
    dead = math.prod(1 + b * changes.get(c, 0.0) for c, b in betas.items()
                     if b and changes.get(c, 0.0) <= -1.0)
    return max(math.exp(live) * dead, 0.0)


def fund_components(looked: dict, rows: dict) -> dict[str, dict[str, float]]:
    """The same immediate holdings per fund dollar, independent of which path reaches that fund; `rows` is each fund's
    rows (the fund_rows value), where an index basket's companies are read."""
    funds = looked.get("funds") or {}
    out = {schema.name_id(t): dict(info["parts"]) for t, info in funds.items() if "parts" in info}
    totals, parts = {}, {}
    for reaches in (looked.get("reaches") or {}).values():
        for item in reaches:
            fund = schema.name_id(item["fund"])
            totals[fund] = totals.get(fund, 0.0) + item["dollars"]
            if item["funds"]:
                parent = schema.name_id(item["funds"][-1])
                by = parts.setdefault(parent, {})
                by[fund] = by.get(fund, 0.0) + item["dollars"]
    for paths in (looked.get("paths") or {}).values():
        for item in paths:
            if item.get("funds"):
                fund = schema.name_id(item["funds"][-1])
                by = parts.setdefault(fund, {})
                by[item["name"]] = by.get(item["name"], 0.0) + item["dollars"]
    for fund, total in totals.items():
        if fund not in out and total:
            out[fund] = {n: amount / total for n, amount in parts.get(fund, {}).items()}
    # An explicitly identified index basket is evidence of its unheld follower's
    # companies. Assets held beside a derivative are not part of that index.
    for payload in (rows or {}).values():
        for row in payload.get("results") or []:
            target = row.get("to") or {}
            follower = (target.get("basket") or {}).get("fund")
            if not follower or not target.get("companies"):
                continue
            reference = schema.name_id(follower)
            if reference in out:
                continue
            basket = {}
            for company in target["companies"]:
                ticker = company.get("constituent_ticker") or company.get("ticker")
                if ticker is None or company.get("weight") is None:
                    continue
                name = next((n for n, securities in (looked.get("securities") or {}).items() if ticker in securities),
                            schema.name_id(ticker))
                basket[name] = basket.get(name, 0.0) + company["weight"]
            if basket:
                out[reference] = basket
    return out


def multiple_extras(components: dict, fund: str, reference: str, multiple: float) -> dict:
    """Exposures beside the explicit measured reference, leaving its own parts inside its single price."""
    if fund not in components:
        return {}
    result = dict(components.get(fund) or {})
    if reference in result:
        result[reference] -= multiple
    else:
        for child, weight in (components.get(reference) or {}).items():
            result[child] = result.get(child, 0.0) - multiple * weight
    return {child: weight for child, weight in result.items() if abs(weight) > 1e-12}


def curve_inputs(ctx: Inputs, asof: str, currency: str = "USD") -> dict:
    """The government yield curve of a currency as of `asof`, as its readings by year (MATH.md 3.1: cash paid on a date is
    discounted at its currency's government yield to that date): the dollar's, the Treasury's par curve, by `{year}`;
    another currency's, from the institution that publishes it, by `{CURRENCY}:{year}` (harness.venues.curves). A
    currency no source here reads is not obtained, as any reading the store could not obtain."""
    key = (lambda y: str(y)) if currency == "USD" else (lambda y: f"{currency}:{y}")  # noqa: E731
    return {key(y): ctx.data("yield_curve", key(y), covering=(asof, asof))
            for y in (int(asof[:4]) - 1, int(asof[:4]))}


def curve_at(readings: dict, load, asof: str) -> list:
    return _latest(readings, load, asof)["curve"]


def _latest(readings: dict, load, asof: str) -> dict:
    found = sorted(({**r, "basis": (load(ref) or {}).get("basis", "par")}
                    for ref in readings.values() if ref and ref.get("data")
                    for r in (load(ref) or {}).get("results", []) if r["date"] <= asof), key=lambda x: x["date"])
    if not found:
        raise Unavailable(f"the government yield curve has no observation on or before {asof}")
    return found[-1]


def discount_at(readings: dict, load, asof: str, years: float) -> float:
    """The price at `asof` of a unit paid `years` later, on the latest curve read on or before it, by how its points
    discount, par or spot (rates.discount)."""
    if years <= 0:
        return 1.0
    latest = _latest(readings, load, asof)
    return rates.discount(latest["curve"], years, latest["basis"])


def contract_inputs(ctx: Inputs, pid: str, tree: dict, *, through: str | None = None,
                    states: dict | None = None, receipt: dict | None = None) -> tuple[list[dict], dict]:
    """Held contracts affected by this event, with one shared logical outcome state."""
    from harness.derive.together import contract_states
    if not any(h["to"].startswith("market:") for h in ctx.links("portfolio", "holds")):
        return [], {}
    if states is None:
        states = contract_states(ctx, pid, tree, through=through, **({"receipt": receipt} if receipt is not None else {}))
    probabilities = {}
    for world, outcomes in states.items():
        for outcome in outcomes:
            for hid, payoff in outcome["payoffs"].items():
                by = probabilities.setdefault(hid, {})
                by[world] = by.get(world, 0.0) + outcome["p"] * payoff
    if not probabilities:
        return [], {}
    asof = portfolio.asof_of(ctx)
    holdings = ctx.value("holdings", "portfolio")["output"]["holds"]
    out, curves = [], None
    for hid, by_world in probabilities.items():
        held = holdings[hid]
        listing = ctx.value("market_listing", held["name"])
        close = listing["output"].get("close_time")
        if not close:
            raise Unavailable(f"{held['name']}'s payment date is needed to value its held contract")
        close = datetime.fromisoformat(close.replace("Z", "+00:00")).astimezone(series.NY).isoformat()
        life = years(asof, close)
        if life > 0 and curves is None:
            curves = curve_inputs(ctx, asof)
        discount = discount_at(curves, ctx.payload, asof, life) if life > 0 else 1.0
        out.append({"holding": hid, "market": held["name"], "quantity": held["quantity"],
                    "side": held["side"], "current": held["dollars"], "price": held["price"],
                    "discount": discount, "close": close, "asof": asof, "curves": curves,
                    "listing_basis": listing["basis"], "probabilities": by_world})
    return out, states


def worth_inputs(ctx: Inputs, pid: str, ways: list[str], kinds: dict) -> list[dict]:
    """Each stated consideration and every observation it needs, independent of the event's probability."""
    asof = portfolio.asof_of(ctx)
    out = []
    for on in (pid, *ways):
        for link in ctx.links(on, "worth"):
            value = link["stated"]["value"]["value"]
            target = link["to"]
            info = kinds.get(target) or {}
            ticker = info.get("security") or info.get("beta") or portfolio.ticker_of(target)
            price = ctx.value("price", schema.name_id(ticker))
            item = {"link": link["id"], "on": on, "target": target,
                    "world": link["stated"].get("world"), "value": value, "asof": asof,
                    "c": (price["output"].get("prices") or {}).get("price"), "price_basis": price["basis"]}
            if "cash" in value:  # discounted at its currency's government yield, then at that currency's rate (3.1)
                currency = value.get("currency", "USD")
                if years(asof, value["date"]) > 0:
                    item["curves"] = curve_inputs(ctx, asof, currency)
                if currency != "USD":
                    fx = ctx.value("proxy_price", schema.proxy_metric_id(series.dollar_pair(currency)))
                    item["fx"] = {"name": schema.name_id(series.dollar_pair(currency)),
                                  "c": fx["output"].get("level"), "basis": fx["basis"]}
            if "shares" in value:
                n = value["shares"]["name"]
                ni = kinds.get(n) or {}
                t = ni.get("security") or ni.get("beta") or portfolio.ticker_of(n)
                got = ctx.value("price", schema.name_id(t))
                item["share_price"] = {"name": n, "c": (got["output"].get("prices") or {}).get("price"), "basis": got["basis"]}
            if "return" in value:  # the company on its own, carried from an earlier date by its fit up to then
                item["anchor"] = ctx.value("fit_before", f"{ticker}@{value['date']}", ref=True)
            out.append(item)
    return out


def _worth_worlds(m: dict) -> dict[str, dict[str, dict]]:
    """Nearest stated consideration for each name in each terminal world, including the baseline."""
    ws = m["worlds"]
    tree = m.get("tree") or {}
    parent = {w["id"]: w.get("parent") for w in ws}
    parent.update({n: v.get("parent") for n, v in tree.items()})
    out: dict[str, dict[str, dict]] = {}
    entries = m.get("worth") or []
    for w in ws:
        path = [w["id"]]
        while parent.get(path[-1]) is not None:
            path.append(parent[path[-1]])
        path.append(m["event"])
        for n in path:
            for item in entries:
                if item["on"] != n:
                    continue
                if n == m["event"] and item.get("world") != w["side"]:
                    continue
                out.setdefault(item["target"], {}).setdefault(w["id"], item)
    for target, found in out.items():
        missing = [w["id"] for w in ws if w["id"] not in found]
        if missing:
            raise Unavailable(f"{target}'s stated value leaves these worlds without a value: {', '.join(missing)}")
    return out


def on_one_reference(looked: dict, responds: Callable[[str], Any]) -> dict[str, dict]:
    """Each fund on one reference that moves by its multiple of its reference's move (MATH.md 1.6), by name, with its
    reference and multiple (3.1): one whose reference is another name that `responds`, having betas of its own. A
    fund whose reference has none moves by its own price."""
    return {schema.name_id(t): v for t, v in (looked.get("multiples") or {}).items()
            if v.get("reference") and v["reference"] != schema.name_id(t) and v.get("multiple") is not None
            and responds(v["reference"])}


def _fit_before_inputs(ctx: Inputs, subject: str) -> dict:
    """What a price on an earlier date is carried to today by (MATH.md 1.6, 3.1): that day's close, with the history its
    betas are fitted on up to that date as every fit is (3.8): the factors' series then, with the currency pairs a
    proxy quotes; the security's own; and for a listing abroad, its currency's rates, whose response it shares with
    every listing quoted in that currency; and each factor's proxy then and as of today."""
    ticker, day = subject.rsplit("@", 1)
    asof = portfolio.asof_of(ctx)
    factors = channels.ordered_channels(ctx)
    proxies = [c["stated"]["proxy"] for c in factors]
    # every series it reads on the day at once, and those today at once, so a call stands aside once for each set:
    # each exactly what series_inputs reads of it (history.series_inputs_many)
    found = history.series_inputs_many(ctx, [*proxies, ticker], day)
    items = [found[t] for t in proxies]
    fm = {"asof": day, "settings": estimate.SETTINGS.as_dict(),
          "channels": [c["id"] for c in factors], "tickers": proxies,
          "calendar": history.calendar_inputs(ctx, day), "series": items,
          "pairs": {c["id"]: q for c, item in zip(factors, items) if (q := channels._quote_of(ctx, item))}}
    name = found[ticker]
    currency = name.get("currency") if name.get("kind") == "abroad" else None
    rates_ = ({"currency": currency, "rates": ctx.data("hourly", series.dollar_pair(currency),
                                                       covering=(series.lookback(day), day))}
              if currency and currency != series.PRICE_CURRENCY else {})
    return {"ticker": ticker, "day": day, "asof": asof, "factors": fm, "name": name, **rates_,
            "now": list(history.series_inputs_many(ctx, proxies, asof).values()),
            "current_basis": ctx.value("factors", "market", ref=True)}


def _fit_before_compute(a: dict, load) -> dict:
    """The security's close on the day, its betas fitted on its history up to that day as every fit is (3.2), and that
    close carried to today by its response to the factors' moves since, compounded: `carried`. A listing abroad's
    local part is kept beside its whole response: its betas in its own currency, which a window measures it by. Its
    residual's history to that day, and its covariance with the factors then, are what its draws vary by (1.8)."""
    fm = channels._factors_compute(a["factors"], load)
    mdl = channels.model(fm)
    found = history.returns(a["name"], load, mdl.sessions)["dollar"]
    early = history.struck_early(a["name"], load, mdl.sessions)
    # a listed share leans on the market's sensitivity; a currency or crypto pair, being none, on no response (3.8)
    prior = mdl.no_response_prior() if a["name"].get("kind") == "pair" else mdl.market_prior()
    if a.get("currency"):  # abroad: its local return fitted, and its currency's response added, as every fit is (3.8)
        local = mdl.fit(history.returns(a["name"], load, mdl.sessions)["own"], prior, early=early)
        fx = channels._currency_fit(a["currency"], a["rates"], fm, mdl, load)
        if fx is None:
            raise Unavailable(f"{a['ticker']} is quoted in {a['currency']}, whose rates its source has none of then")
        fit = {**local, "full": local["full"] + fx["full"], "session": local["session"] + fx["session"],
               "local_full": local["full"]}
    else:
        fit = mdl.fit(found, prior, early=early)
    old = {d: c for d, c in history.dollar_closes(a["name"], load, mdl.sessions).items() if d <= a["day"]}
    if not old:
        raise Unavailable(f"{a['ticker']} has no price on or before {a['day']}")
    start = old[max(old)]  # that day's close, or the last before it: the series read can run past the day
    factor_moves = []
    for before, now in zip(a["factors"]["series"], a["now"]):
        old_prices, new_prices = portfolio.closes(before, load), portfolio.closes(now, load)
        old_days = [d for d in old_prices if d <= a["day"]]
        new_days = [d for d in new_prices if d <= a["asof"]]
        if not old_days or not new_days:
            raise Unavailable("the company on its own needs each factor's price on its date and today")
        factor_moves.append(math.log(new_prices[max(new_days)] / old_prices[max(old_days)]))
    full = np.asarray(fit["full"])
    current = channels.model(load(a["current_basis"]))
    residual = estimate.residuals(mdl.x, mdl.ok, fit["session"], found)
    state = np.column_stack([mdl.x, residual])
    good = estimate.common(state)
    if np.count_nonzero(good) < 2:
        raise Unavailable("the company on its own needs at least two observed sessions to carry its uncertainty")
    state_covariance = estimate.covariances(state, good, mdl.settings.memory)[2] / estimate.centring(good, mdl.settings.memory)
    return {"date": a["day"], "observed_price": start, "factor_moves": dict(zip(a["factors"]["channels"], factor_moves)),
            "carried": start * math.exp(float(full @ np.asarray(factor_moves))),
            "raw_betas": dict(zip(a["factors"]["channels"], map(float, full))),
            "betas": dict(zip(current.channels, map(float, current.orthogonal(full)))), "sigma2": fit["sigma2"],
            "full": fit["full"].tolist(), "session": fit["session"].tolist(), "alpha": fit["alpha"],
            **({"currency": a["currency"], "local_full": fit["local_full"].tolist(),
                "local_betas": dict(zip(current.channels, map(float, current.orthogonal(fit["local_full"]))))}
               if a.get("currency") else {}),
            "returns": found, "residuals": estimate.pack(residual), "sessions": mdl.sessions,
            "state_covariance": state_covariance.tolist()}


FIT_BEFORE = Derivation("fit_before", 3, "Name", _fit_before_inputs, _fit_before_compute,
                        applies=lambda store, subject: False)


# ---------------------------------------------------------------- a name over a past window


def name_window_subject(start: str, end: str, nid: str) -> str:
    return f"{start}:{end}:{nid}"


def _name_window_inputs(ctx: Inputs, subject: str) -> dict:
    """What a name's move over a past window is measured against (MATH.md 1.3, 1.4, 3.2): the factors' moves over it
    (window_returns), the security it is priced through, read over the window as a factor's proxy is, and that
    security's fit on its history up to the window's start (fit_before), so the window's own move enters none of the
    betas it is measured by. A name of the portfolio is read through the security it is priced by (3.1), as a shock on
    it is stated on that security's price (1.4); any other, through its own ticker. A factor's proxy is its factor's
    price, and a fund valued by its terms is what its terms give (1.6): neither has a fit read for it."""
    start, end, nid = subject.split(":", 2)
    asof = portfolio.asof_of(ctx)
    if start > asof:
        raise Unavailable(f"the window begins on {start}, after the as-of, {asof}")
    looked = ctx.payload(ctx.value("lookthrough", "portfolio", ref=True)) or {}
    info = (looked.get("kinds") or {}).get(nid) or {}
    security = info.get("security") or info.get("beta") or portfolio.ticker_of(nid)
    factors = channels.ordered_channels(ctx)
    if not factors:
        raise Unavailable("the store has no factors")
    out = {"name": nid, "security": security, "window": [start, end]}
    proxy_of = next((c["id"] for c in factors if c["stated"]["proxy"] == security), None)
    if proxy_of is not None:
        return {**out, "proxy_of": proxy_of,
                "factors": ctx.value("window_returns", channels.window_subject(start, end), ref=True)}
    fitted = ctx.payload(ctx.value("betas", "portfolio", ref=True)) or {}

    def responds(n: str) -> dict:
        return ((fitted.get("objects") or {}).get(n) or {}).get("fields", {}).get("betas") or {}
    terms = None  # what values a fund by its terms, as an event's impact reads it (1.6)
    on_reference = on_one_reference(looked, responds).get(nid)
    if on_reference is not None:
        terms = {"reference": on_reference["reference"], "multiple": on_reference["multiple"]}
    else:
        held = ((ctx.payload(ctx.value("fund_rows", "portfolio", ref=True)) or {}).get("rows") or {}).get(security)
        contracts = option_funds.valued_contracts(looked, held, asof)
        if contracts:
            terms = {"contracts": [{"on": on, "expiry": expiry} for on, expiry in sorted(
                {(c["reference"]["name"] or c["reference"]["ticker"], c["expiry"][:10]) for c in contracts})]}
    record = channels.window_record(start, end, asof)
    # New York's sessions, by the first factor's proxy listed there, as the factors' own window reads them
    calendar = next((c["stated"]["proxy"] for c in factors
                     if not series.is_pair(c["stated"]["proxy"]) and not series.is_abroad(c["stated"]["proxy"])), None)
    if calendar is None:
        raise Unavailable("no factor's proxy is listed in the US, and a window is measured over New York's sessions")
    # its own series first, so that a call reading several names, and the factors' window, starts their reads together
    items = history.moves_inputs_many(ctx, [calendar, security], (start, min(end, asof)), record=record)
    out.update({"record": list(record), "calendar": items[calendar], "series": items[security],
                "factors": ctx.value("window_returns", channels.window_subject(start, end), ref=True)})
    if terms is not None:
        return {**out, "terms": terms}
    return {**out, "fit": ctx.value("fit_before", f"{security}@{start}", ref=True)}


def _name_window_compute(m: dict, load) -> dict:
    """A name's move over a past window, what holding it earned less the income it accrued, as a factor's is (MATH.md
    3.2); its response over the window at its betas as fitted up to the window's start, exp(b . z) on the factors'
    residuals over it (1.6); and its residual, (1 + move) / (1 + response) - 1 (1.3, 1.4). Each is a fraction of the
    price a shock on the name is stated on: a listing abroad's in its own currency, at its local betas, with its rate in
    dollars' move beside them. A factor's proxy gives its move, its residual being its factor's; a fund valued by its
    terms gives its move with what its terms are written on, its residual in a world being against what they give
    there. A name with no close at an end of the window, or a factor with none, has no figures over it."""
    start, end = m["window"]
    factors = load(m["factors"])
    out: dict = {"name": m["name"], "window": [start, end]}
    if "proxy_of" in m:
        move = factors["moves"].get(m["proxy_of"])
        if move is None:
            raise Unavailable(f"{m['security']} has no close at both ends of the window, {start} to {end}")
        return {**out, "move": move, "proxy_of": m["proxy_of"]}
    sessions = sorted(portfolio.closes(m["calendar"], load))
    item = m["series"]
    since, until = m["record"]
    abroad = item.get("kind") == "abroad" and item.get("currency")
    if abroad:
        moving, rates_ = history.local_moves(item, load, sessions, since=since, until=until)
    else:
        moving = history.moves(item, load, sessions, since=since, until=until)
    move = moving(start, end)
    if move is None:
        held = [d for d in (start, end) if d not in moving.closes]
        raise Unavailable(f"{m['security']} has no close on {' or '.join(held)}" if held else
                          f"{m['security']} has no return on a session between {start} and {end}")
    out["move"] = move
    if abroad:
        missing = [d for d in (start, end) if not rates_.get(d)]
        if missing:
            raise Unavailable(f"{m['security']}'s rate in dollars has no price at its close on {' or '.join(missing)}")
        out.update({"currency": item["currency"], "rate_move": rates_[end] / rates_[start] - 1.0})
    if "terms" in m:
        return {**out, "terms": m["terms"]}
    unmoved = [factors["proxies"][c] for c, x in factors["moves"].items() if x is None]
    if unmoved:
        raise Unavailable(f"{', '.join(unmoved)} {'has' if len(unmoved) == 1 else 'have'} no close at both ends of the "
                          f"window, so the factors' moves over it, and {m['security']}'s response, are not known")
    fit = load(m["fit"])
    betas = fit["local_betas"] if abroad else fit["betas"]
    z = {c: math.log1p(a) for c, a in factors["own_part"].items()}
    response = math.expm1(sum(b * z[c] for c, b in betas.items()))
    return {**out, "response": response, "residual": (1.0 + move) / (1.0 + response) - 1.0, "betas": betas}


NAME_WINDOW = Derivation("name_window", 1, "Name", _name_window_inputs, _name_window_compute,
                         applies=lambda store, subject: False)
DERIVATIONS = (FIT_BEFORE, NAME_WINDOW)


def _anchor(item: dict, load) -> dict:
    """The company on its own in one stated value: the fit before its date, the analyst's return since, a fraction,
    applied."""
    fit = load(item["anchor"])
    return {**fit, "amount": fit["carried"] * (1 + item["value"]["return"]), "return": item["value"]["return"],
            "state_key": "anchor:" + item["target"] + ":" + fit["date"]}


def worth_values(m: dict, load, ratios, factors: dict[str, dict[str, float]]) -> tuple[dict, dict]:
    """Per-share values and their terms, using the same prices as every other holding."""
    out, terms = {}, {}
    for target, by_world in _worth_worlds(m).items():
        out[target], terms[target] = {}, {}
        for w, item in by_world.items():
            value = item["value"]
            if item["c"] is None or item["c"] <= 0:
                raise Unavailable(f"{target}'s current price is needed to value its shares")
            amount = float(value.get("amount", 0))
            detail = {"link": item["link"], "c": item["c"], "fixed": amount}
            if "cash" in value:
                t = years(item["asof"], value["date"])
                discount = discount_at(item["curves"], load, item["asof"], t) if t > 0 else 1.0
                pv = float(value["cash"]) * discount
                cash = {"amount": value["cash"], "date": value["date"],
                        "currency": value.get("currency", "USD"), "pv": pv}
                if item.get("fx"):
                    fx = item["fx"]
                    if fx["c"] is None:
                        raise Unavailable(f"the payment needs the rate of {cash['currency']} in dollars")
                    cash["fx"] = fx
                    amount += pv * fx["c"] * ratios(fx["name"])[w]
                else:
                    amount += pv
                    detail["fixed"] = amount
                detail["cash"] = cash
            if "shares" in value:
                shares = value["shares"]
                quoted = item["share_price"]["c"]
                if quoted is None:
                    raise Unavailable(f"{shares['name']}'s price is needed for the shares paid")
                detail["shares"] = {**shares, "price": quoted}
                amount += shares["quantity"] * quoted * ratios(shares["name"])[w]
            if "return" in value:
                anchor = _anchor(item, load)
                # An unaffected company is carried by the betas fitted before the event moved its price.
                raw = anchor["raw_betas"]
                growth = raw_response(raw, factors[w])
                amount += anchor["amount"] * growth
                detail["anchor"] = anchor
            out[target][w] = amount
            detail["amount"] = amount
            terms[target][w] = detail
    return out, terms


def implied_from_values(values: dict[str, float], current: float, ws: list[dict], q: dict) -> float | None:
    def mean(side):
        selected = [w["id"] for w in ws if w["side"] == side]
        weight = sum(q[w] for w in selected)
        if weight > 0:
            return sum(q[w] * values[w] for w in selected) / weight
        p = sum(w["p"] for w in ws if w["side"] == side)
        if p > 0:
            return sum(w["p"] * values[w["id"]] for w in ws if w["side"] == side) / p
        return values[selected[0]] if len(selected) == 1 else None
    y, n = mean("yes"), mean("no")
    if y is None or n is None or abs(y - n) <= 1e-12:
        return None
    return (current - n) / (y - n)


def remainder_inputs(ctx: Inputs, specs: dict) -> dict:
    """Only a partial live option sleeve needs the dated fund history it is removed from."""
    asof = portfolio.asof_of(ctx)
    return {n: {"history": spec["history"], "event_days": (date.fromisoformat(spec["event_date"][:10]) - date.fromisoformat(asof)).days,
                "series": history.series_inputs(ctx, portfolio.ticker_of(n), asof),
                "factors": ctx.value("factors", "market", ref=True)}
            for n, spec in specs.items() if not spec["full"]}


def remainder_fit(m: dict, load) -> dict:
    mdl = channels.model(load(m["factors"]))
    observed = channels.option_remainder_returns(rows.contracts_by_day(load(m["history"])),
                                                 history.returns(m["series"], load, mdl.sessions)["dollar"], mdl.sessions,
                                                 event_days=m.get("event_days", 0))
    if not observed:
        raise Unavailable("the option fund's remainder needs dated values of the contracts held across its sessions")
    fit = mdl.fit(observed, mdl.no_response_prior())
    return {"n": fit["n"], "full": fit["full"].tolist(), "session": fit["session"].tolist(),
            "sigma2": fit["sigma2"], "alpha": fit["alpha"], "returns": observed,
            "betas": dict(zip(mdl.channels, map(float, mdl.orthogonal(fit["full"])))), "history": m["history"]}


_ERFC = np.frompyfunc(math.erfc, 1, 1)  # math.erfc over an array: each draw's value the scalar calculation's, bit for bit


def _option_batch(contract: dict, spot: np.ndarray, remaining: float) -> np.ndarray:
    """The existing exercise model over many states, with the same steps as its scalar calculation."""
    call, early = contract["kind"] == "call", contract["style"] == "american"
    k, r, q, vol = contract["strike"], contract["rate"], contract["yield"], contract["volatility"]
    sign = 1.0 if call else -1.0
    if remaining <= 0:
        return np.maximum(sign * (spot - k), 0)
    if vol <= 0 or k <= 0:
        return np.array([options.value(kind=contract["kind"], style=contract["style"], strike=k, years=remaining,
                                       rate=r, dividend_yield=q, underlying=float(s), volatility=vol) for s in spot])
    safe = np.maximum(spot, 1e-300)
    if not early:
        scale = vol * math.sqrt(remaining)
        d1 = (np.log(safe / k) + (r - q + .5 * vol * vol) * remaining) / scale
        d2 = d1 - scale
        cdf = lambda xs: .5 * _ERFC(-np.asarray(xs, dtype=float) / math.sqrt(2)).astype(float)  # noqa: E731
        a, b = spot * math.exp(-q * remaining), k * math.exp(-r * remaining)
        return a * cdf(d1) - b * cdf(d2) if call else b * cdf(-d2) - a * cdf(-d1)
    def tree(steps):
        dt = remaining / steps
        up = math.exp(vol * math.sqrt(dt))
        down = 1 / up
        prob = (math.exp((r - q) * dt) - down) / (up - down)
        if not 0 < prob < 1:
            # At a near-zero volatility the deterministic exercise value is the limit.
            return np.array([options._zero(call, True, float(s), k, remaining, r, q)[0] for s in spot])
        discount = math.exp(-r * dt)
        prices = spot[:, None] * up ** (steps - 2 * np.arange(steps + 1))
        value = np.maximum(sign * (prices - k), 0)
        for step in range(steps - 1, -1, -1):
            prices = prices[:, 1:] * up
            value = np.maximum(discount * (prob * value[:, :-1] + (1 - prob) * value[:, 1:]),
                               np.maximum(sign * (prices - k), 0))
        return value[:, 0]
    return (tree(options.STEPS) + tree(options.STEPS + 1)) / 2


def zero_drift(ratios: np.ndarray) -> np.ndarray:
    """A price's draws as ratios to its centre, with their mean over the draws exactly 1 (MATH.md 1.8, zero drift): each
    is drawn with its mean of 1 in expectation, and dividing by the draws' own mean makes it 1 on the draws themselves,
    so a holding whose value moves in proportion to the price has its world's centre as its mean. A constant scales
    every draw alike, so what moves together stays together."""
    ratios = np.asarray(ratios, dtype=float)
    mean = float(ratios.mean()) if ratios.size else 0.0
    return ratios / mean if mean > 0.0 else ratios


def value_state(m: dict, load, centre: dict, world: str, factors: dict, names: dict,
                variances: dict, at: str, *, expiry_states: dict | None = None,
                contract_payoffs: dict | None = None, receipt: dict | None = None) -> dict:
    """Value each holding on the same state, in today's dollars, with distributions supplied as gross ratios.

    The input state has zero drift in today's dollars. For an option, its underlying's nominal ex-dividend
    price at the valuation date grows by its financing rate less dividends; the resulting price or expiry
    payment is discounted once. Ordinary shares already include dividends in their total-return state.
    """
    sample = next(iter(names.values()), next(iter(factors.values()), np.ones(1)))
    count = len(np.atleast_1d(sample))
    ones = np.ones(count)
    looked = load(m["lookthrough"])
    terms = centre.get("valuation_terms") or {}
    worth = terms.get("worth") or {}
    option_books = terms.get("options") or {}
    actual = centre.get("name_ratios") or {}
    response = centre.get("responding_ratios") or {}
    components = fund_components(looked, (load(m["fund_rows"]) or {}).get("rows"))
    fitted = load(m["betas"]) if m.get("betas") else {}
    multiples = on_one_reference(looked, lambda r: ((fitted.get("objects") or {}).get(r) or {}).get("fields", {})
                                 .get("betas"))  # the funds on one reference, as the event's impact reads them
    declared = set(centre.get("names_set") or [])
    # a fund valued by its terms that a statement sets: its terms at each draw, times what the statement does to it in
    # this world, its value there over where it stands unstated (1.6), as the row values its centre
    unstated = centre.get("unstated") or {}
    stated_by = {n: actual[n][world] / unstated[n][world] for n in unstated
                 if (n in option_books or n in multiples) and world in unstated[n] and n in actual
                 and unstated[n][world] > 1e-12}
    cache, evaluating = {}, set()
    channels_ = m["channels"]
    factor_cov = np.asarray(variances.get("__factor_covariance__", np.diag([variances.get(c, 0) for c in channels_])))

    def ordinary(n, state=None):
        ns = names if state is None else state["names"]
        if n in ns:
            return np.asarray(ns[n], dtype=float)
        return ones * (actual.get(n) or centre.get("reference_ratios", {}).get(n) or {}).get(world, 1.0)

    def responding(n, state=None):
        ns = names if state is None else state["names"]
        if "response:" + n in ns:
            return np.asarray(ns["response:" + n], dtype=float)
        base = (actual.get(n) or {}).get(world, 1.0)
        before = response.get(n, {}).get(world, base)
        if base:
            return ordinary(n, state) * before / base
        if not before:
            return np.zeros(count)
        raise Unavailable(f"{n}'s zero-price world needs its same unconstrained company state through the fund")

    def name_price(n, state=None):
        cache_key = (n, id(state))
        if cache_key in cache:
            return cache[cache_key]
        if cache_key in evaluating:
            raise Unavailable("the share considerations have a circular valuation")
        evaluating.add(cache_key)
        ns = names if state is None else state["names"]
        fs = factors if state is None else state["factors"]
        if n in worth:
            claim = worth[n][world]
            amount = ones * claim["fixed"]
            cash = claim.get("cash") or {}
            if cash.get("fx"):
                fx = cash["fx"]
                amount = amount + cash["pv"] * fx["c"] * name_price(fx["name"], state)
            if claim.get("shares"):
                share = claim["shares"]
                amount = amount + share["quantity"] * share["price"] * name_price(share["name"], state)
            if claim.get("anchor"):
                anchor = claim["anchor"]
                placed = centre["placed"][world]
                key = anchor["state_key"]
                if key in ns:
                    amount = amount + anchor["amount"] * np.asarray(ns[key])
                else:
                    active = [c for c in channels_ if placed[c] > -1]
                    positions = [channels_.index(c) for c in active]
                    b = np.array([anchor["raw_betas"].get(c, 0) for c in active])
                    full_cov = factor_cov if state is None else np.asarray((state.get("variances") or {}).get("__factor_covariance__", factor_cov))
                    cov = full_cov[np.ix_(positions, positions)]
                    log_extra = np.column_stack([np.log(np.maximum(fs[c], 1e-300) / (1 + placed[c]))
                                                  for c in active]) if active else np.zeros((count, 0))
                    correction = .5 * (float(b @ np.diag(cov)) - float(b @ cov @ b))
                    base = anchor["amount"] * raw_response(anchor["raw_betas"], placed)
                    amount = amount + base * zero_drift(np.exp(log_extra @ b + correction))
            result = amount / claim["c"]
        elif n in declared and n not in stated_by:
            result = ordinary(n, state)
        elif n in option_books:
            result = option_fund(n, state)
        elif n in multiples:
            spec = multiples[n]
            ref = spec["reference"]
            base = (actual.get(ref) or centre.get("reference_ratios", {}).get(ref) or {}).get(world, 1)
            extra = name_price(ref, state) / base if base > 0 else ones
            multiplier = spec["multiple"]
            jump = ((unstated if n in stated_by else actual).get(n) or {}).get(
                world, max(1 + multiplier * (base - 1), 0.0))
            variance = variances if state is None else state.get("variances") or {}
            reference_variance = variance.get(ref, 0) if np.ptp(extra) else 0.0
            growth = zero_drift(np.power(np.maximum(extra, 1e-300), multiplier) * math.exp(
                -.5 * multiplier * (multiplier - 1) * reference_variance))
            extras = multiple_extras(components, n, ref, multiplier)
            before = sum(weight * (actual.get(child, {}).get(world, 1) - response.get(child, {}).get(world, 1))
                         for child, weight in extras.items())
            outside = sum((weight * (name_price(child, state) - responding(child, state))
                           for child, weight in extras.items()), np.zeros(count))
            result = np.maximum((jump - before) * growth + outside, 0.0) if jump > 0 else np.zeros(count)
        elif n in components:
            result = responding(n, state).copy()
            for child, weight in components[n].items():
                result += weight * (name_price(child, state) - responding(child, state))
            result = np.maximum(result, 0.0)
        else:
            result = ordinary(n, state)
        if n in stated_by:
            result = result * stated_by[n]
        evaluating.remove(cache_key)
        cache[cache_key] = result
        return result

    def reference(ref, state=None):
        fs = factors if state is None else state["factors"]
        n = ref.get("name") or (f"name:{ref['basket']}" if ref.get("basket") else None)
        if n is not None:
            return name_price(n, state)
        for c, ticker in (m.get("proxies") or {}).items():
            if ticker == ref["ticker"] and c in fs:
                return np.asarray(fs[c])
        raise Unavailable(f"{ref['ticker']}'s price in the same state is needed to value its contract")

    option_values, groups, group_of, pricing = {}, {}, {}, set()
    dated_states = {day: {**state, "at": day} for day, state in (expiry_states or {}).items()}
    for fund, book in option_books.items():
        for index, contract in enumerate(book["contracts"]):
            ref = contract["reference"]
            key = (ref.get("ticker"), ref.get("name"), ref.get("basket"), contract["expiry"],
                   contract["style"], contract["rate"], contract["yield"], book["asof"])
            groups.setdefault(key, []).append((fund, index, book, contract))
            group_of[fund, index] = key

    def price_option_group(key, requested_state=None):
        requested = (key, id(requested_state))
        if requested in pricing:
            raise Unavailable("the option books contain a circular underlying valuation")
        pricing.add(requested)
        entries = groups[key]
        _, _, book, contract = entries[0]
        expiry = contract["expiry"]
        at_date = at[:10] if requested_state is None else requested_state["at"]
        end = min(at_date, expiry)
        elapsed = years(book["asof"], end)
        state = dated_states.get(expiry) if expiry < at_date else requested_state
        if expiry < at_date and state is None:
            raise Unavailable(f"the option expiring {expiry} needs the state at its expiry")
        ratio = reference(contract["reference"], state)
        spot = contract["underlying_today"] * ratio * math.exp((contract["rate"] - contract["yield"]) * elapsed)
        at_close = datetime.combine(date.fromisoformat(end), time(16), series.NY).isoformat()
        remaining = max(contract.get("years", options.life(expiry, at_close) + elapsed) - elapsed, 0.0) if end < expiry else 0.0
        contracts = [entry[3] for entry in entries]
        if any(not math.isclose(c["underlying_today"], contract["underlying_today"], rel_tol=1e-9, abs_tol=1e-9)
               for c in contracts):
            raise Unavailable("contracts on the same underlying need a common current underlying price")
        # each contract by the calculation that gives its delta, at the one volatility its worlds were valued at (1.6)
        values = np.column_stack([_option_batch({**c, "volatility": c["volatility"] or 0.0}, spot, remaining)
                                  for c in contracts])
        discount = math.exp(-contract["rate"] * elapsed)
        for j, (fund, index, _, _) in enumerate(entries):
            option_values[fund, index, id(requested_state)] = values[:, j] * discount
        pricing.remove(requested)

    def option_fund(n, state=None):
        book = option_books[n]
        for index in range(len(book["contracts"])):
            if (n, index, id(state)) not in option_values:
                price_option_group(group_of[n, index], state)
        sleeve = sum((contract["units_per_nav"] * option_values[n, index, id(state)]
                      for index, contract in enumerate(book["contracts"])), np.zeros(count))
        if book["full"]:
            result = ones * (1 - book["sleeve_today"]) + sleeve
            for share in book["shares"]:
                result += share["weight"] * (reference(share["reference"], state) - 1)
        else:
            rem = "remainder:" + n
            baseline = (centre.get("remainder_ratios") or {}).get(rem, {}).get(world, 1)
            ns = names if state is None else state["names"]
            result = sleeve + (1 - book["sleeve_today"]) * np.asarray(ns.get(rem, ones * baseline))
        return np.maximum(result, 0)

    contracts = {c["holding"]: c for c in m.get("contracts", [])}
    out = {}
    for hid, n in m["holds"].items():
        dollars = m["values"].get(hid, 0)
        if hid in contracts:
            contract = contracts[hid]
            payoff = ones * contract["probabilities"][world]
            if contract["close"][:10] <= at[:10] and hid in (contract_payoffs or {}):
                payoff = np.asarray(contract_payoffs[hid], dtype=float)
            out[hid] = contract["quantity"] * contract["discount"] * payoff
            continue
        ratio = name_price(n)
        out[hid] = dollars * ratio
    return out
