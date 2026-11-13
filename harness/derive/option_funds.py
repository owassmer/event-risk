"""Current option holdings valued once, with their historical sleeve supplied to the remainder's fit.

The source's signed contract count and same-day net assets set the unit ownership. A current option quote and
underlying quote are one valuation moment. Each option is calibrated at pricing weights; forecast probabilities
do not enter this calculation. A partial sleeve is returned separately so its history can be removed before the
remainder is fitted. A whole payoff never inherits the fund's historical beta on top of its current contracts.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from harness.derive import options, portfolio, rates, rows, series
from harness.derive.engine import Inputs, Unavailable


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _name(looked: dict, ticker: str) -> str:
    for name, securities in (looked.get("securities") or {}).items():
        if ticker in securities:
            return name
    return f"name:{ticker}"


def _reference(row: dict, looked: dict) -> dict | None:
    quote, target = row.get("quote") or {}, row.get("to") or {}
    ticker = quote.get("underlying") or (target.get("index") or {}).get("values")
    company = target.get("company") or {}
    ticker = ticker or company.get("constituent_ticker") or company.get("ticker")
    if not ticker:
        return None
    index = str(ticker).startswith("I:")
    basket = (target.get("basket") or {}).get("fund")
    return {"ticker": ticker, "name": None if index else _name(looked, ticker), "basket": basket,
            "index": index}


def _curves(ctx: Inputs, day: str) -> dict:
    return {str(y): ctx.data("yield_curve", str(y), covering=(day, day))
            for y in (int(day[:4]) - 1, int(day[:4]))}


def _valued(row: dict, contract: dict, looked: dict, payload: dict, event_date: str) -> dict | None:
    """A contract a fund's row holds, where it is valued by what it pays in an event on `event_date` (MATH.md 1.6): it
    outlives the event, what it refers to is identified, and its terms are whole. Its reference, expiry and the net
    assets its row is stated against; None for a contract the fund's measured remainder represents."""
    count, per = contract.get("contracts"), contract.get("underlying_per_contract")
    ref = _reference(row, looked)
    expiry = contract.get("expiry")
    net = row.get("net_assets") or payload.get("net_assets")
    if (expiry and expiry[:10] >= event_date[:10] and ref and contract.get("right") in ("call", "put")
            and contract.get("style") in ("european", "american") and _number(contract.get("strike"))
            and _number(count) and _number(per) and count > 0 and per > 0 and _number(net) and net > 0):
        return {"reference": ref, "expiry": expiry, "net": net}
    return None


def valued_contracts(looked: dict, payload: dict, event_date: str) -> list[dict]:
    """The contracts in one fund's rows that are valued by what they pay in an event on `event_date`, as `inputs`
    chooses them: each one's reference and expiry. A fund with any is valued by its contracts there (MATH.md 1.6)."""
    out = []
    for row in (payload or {}).get("results") or []:
        payoff = row.get("payoff")
        contract = payoff.get("option") if isinstance(payoff, dict) else None
        if contract is None or contract.get("contracts") == 0:
            continue
        valued = _valued(row, contract, looked, payload, event_date)
        if valued is not None:
            out.append(valued)
    return out


def inputs(ctx: Inputs, looked: dict, rows: dict, event_date: str, *, exclude=()) -> dict:
    """Read the established, still-held option sleeve in each fund's normalized rows.

    ``rows`` are each fund's rows, the ``fund_rows`` value (``portfolio.fund_rows`` payloads, keyed by fund ticker),
    which the look-through books from. An expired contract,
    an unspecified contract, or a different asset prevents a whole-payoff claim; its fund's measured remainder
    continues to represent it. Every contract selected here has complete terms and a priced underlying.
    """
    asof = looked.get("asof") or portfolio.asof_of(ctx)
    out = {}
    for ticker, payload in (rows or {}).items():
        if _name(looked, ticker) in exclude:
            continue
        held = payload.get("results") or []
        contracts, others, full = [], [], True
        for row in held:
            payoff = row.get("payoff")
            contract = payoff.get("option") if isinstance(payoff, dict) else None
            if contract is None:
                # A measured exposure is not another asset in the current book.
                if row.get("measured") and row.get("weight") is None and row.get("market_value") is None:
                    continue
                others.append(row)
                continue
            count, per = contract.get("contracts"), contract.get("underlying_per_contract")
            if count == 0:
                continue
            valued = _valued(row, contract, looked, payload, event_date)
            if valued is None:
                full = False
                continue
            ref, expiry, net = valued["reference"], valued["expiry"], valued["net"]
            quote = row.get("quote") or {}
            units = count * per * (-1 if contract.get("side") == "written" else 1)
            at = quote.get("at") or rows.close_moment(row.get("report_date") or asof)
            today = quote.get("price")
            spot = quote.get("underlying_price")
            # A source value is useful only at its own date, with the underlying read at that date too.
            if not _number(today):
                if not _number(contract.get("value")):
                    full = False
                    continue
                today = abs(contract["value"] / units)
                at = rows.close_moment(row.get("report_date") or asof)
                spot = None
            day = at[:10]
            item = {"symbol": contract.get("symbol"), "kind": contract["right"], "style": contract["style"],
                    "strike": contract["strike"], "expiry": expiry, "at": at, "years": options.life(expiry, at),
                    "underlying_today": spot, "value_today": today, "units_per_nav": units / net,
                    "reference": ref, "source_date": row.get("report_date"), "source_net_assets": net,
                    "source_value_per_nav": contract.get("value", 0.0) / net
                    if _number(contract.get("value")) else None}
            if not _number(spot):
                item["spot_series"] = portfolio.return_series(ctx, ref["ticker"], day)
            worked = quote.get("worked") or {}
            yield_read = quote.get("dividend_yield") or {}
            if _number(worked.get("rate")) and _number(worked.get("yield")):
                item.update({"rate": worked["rate"], "yield": worked["yield"]})
            else:
                if quote.get("rate"):
                    item["curve"] = quote["rate"]
                else:
                    item["curves"] = _curves(ctx, day)
                if _number(yield_read.get("paid")) and _number(yield_read.get("price")) and yield_read["price"] > 0:
                    item["yield"] = yield_read["paid"] / yield_read["price"]
                else:
                    payer = ref["basket"] if ref["index"] else ref["ticker"]
                    if not payer:
                        raise Unavailable(f"{contract.get('symbol')}'s dividend payer is needed to value its contracts")
                    item["payer"] = portfolio.return_series(ctx, payer, day)
            contracts.append(item)
        if not contracts:
            continue
        refs = {c["reference"]["ticker"] for c in contracts}
        full = full and len(refs) == 1
        cash, shares = 0.0, []
        for row in others:
            if row.get("weight") == 0 or row.get("market_value") == 0:
                continue
            payoff, to = row.get("payoff"), row.get("to") or {}
            net = row.get("net_assets") or payload.get("net_assets")
            weight = row.get("weight")
            if _number(row.get("market_value")) and _number(net) and net > 0:
                weight = row["market_value"] / net
            if not _number(weight):
                full = False
                continue
            if payoff == "cash" and to.get("currency", "USD") == "USD":
                cash += weight
            elif payoff == "holding":
                ref = _reference(row, looked)
                if ref and ref["ticker"] in refs:
                    shares.append({"reference": ref, "weight": weight})
                else:
                    full = False
            else:
                full = False
        spec = {"asof": asof, "event_date": event_date, "contracts": contracts, "full": full,
                "cash": cash, "shares": shares}
        if not full:
            spec["history"] = ctx.data("holdings_history", ticker, covering=(series.lookback(asof), asof))
        out[_name(looked, ticker)] = spec
    return out


def _curve(item: dict, load) -> list:
    if item.get("curve"):
        return item["curve"]
    found = sorted((r for ref in (item.get("curves") or {}).values() if ref and ref.get("data")
                    for r in (load(ref) or {}).get("results", []) if r["date"] <= item["at"][:10]),
                   key=lambda r: r["date"])
    if not found:
        raise Unavailable("an option's government yield curve is unavailable at its valuation date")
    return found[-1]["curve"]


def _close(item: dict, load, day: str) -> float:
    prices = portfolio.closes(item, load)
    days = [d for d in prices if d <= day]
    if not days:
        raise Unavailable(f"{item.get('ticker')}'s price is unavailable at the option's valuation date")
    return prices[max(days)]


def parameters(item: dict, load) -> dict:
    """Complete pricing inputs only from the dated observations the input stage read."""
    spot = item.get("underlying_today")
    if not _number(spot):
        spot = _close(item["spot_series"], load, item["at"][:10])
    rate = item.get("rate")
    if rate is None:
        rate = rates.rate(_curve(item, load), item["years"])
    dividend_yield = item.get("yield")
    if dividend_yield is None:
        day = item["at"][:10]
        since = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
        paid = sum(amount for d, amount in portfolio.paid(item["payer"], load).items() if since < d <= day)
        dividend_yield = paid / _close(item["payer"], load, day)
    return {"kind": item["kind"], "style": item["style"], "strike": item["strike"], "years": item["years"],
            "rate": rate, "dividend_yield": dividend_yield, "underlying_today": spot,
            "value_today": item["value_today"]}


def reference_ratio(reference: dict, name_ratio, factor_ratio=None) -> dict:
    """The same underlying price ratios that the rest of the portfolio reads, never its option delta."""
    name = reference.get("name") or (f"name:{reference['basket']}" if reference.get("basket") else None)
    if name is not None:
        return name_ratio(name)
    if factor_ratio is not None:
        found = factor_ratio(reference["ticker"])
        if found is not None:
            return found
    raise Unavailable(f"{reference['ticker']}'s price in each world is needed to value its contracts")


class _Book:
    """A prepared book whose security dependencies are resolved only when its funds are valued."""

    def __init__(self, value, prices, terms):
        self._value, self.prices, self.terms = value, prices, terms

    def value(self, fund: str) -> tuple[dict, dict]:
        return self._value(fund)


def prepare(specs: dict, load, q: dict, W: list[str], name_ratio, factor_ratio=None) -> _Book:
    """Pin a common observation convention without asking for any security's world price yet.

Valuing a fund then asks the shared completed-security callback for its reference. Each contract is valued in each
world at its own volatility (MATH.md 1.6), once across funds, independently of their position quantities.
"""
    values, terms, prepared, groups, observations, fund_groups = {}, {}, {}, {}, {}, {}
    for fund, spec in specs.items():
        for item in spec["contracts"]:
            key = (item["reference"]["ticker"], item["expiry"], item["style"])
            model = parameters(item, load)
            at = item.get("at") or rows.close_moment(spec["asof"])
            observations.setdefault(key, []).append((datetime.fromisoformat(at).timestamp(), at, item, model))
    common = {key: max(items, key=lambda row: row[0]) for key, items in observations.items()}
    for fund, spec in specs.items():
        sleeve_today, source_today, contracts = 0.0, 0.0, []
        fund_groups[fund] = []
        for item in spec["contracts"]:
            key = (item["reference"]["ticker"], item["expiry"], item["style"])
            source = next(model for _, _, entry, model in observations[key] if entry is item)
            _, at, _, current = common[key]
            # Option quotes update independently. Every strike is valued on one latest observed underlying
            # and clock; its own observed premium and observation time remain in the receipt.
            model = {**source, **{k: current[k] for k in ("years", "underlying_today", "rate", "dividend_yield")}}
            units = item["units_per_nav"]
            sleeve_today += units * model["value_today"]
            source_today += item.get("source_value_per_nav") or 0.0
            contract = {**model, "expiry": item["expiry"], "symbol": item.get("symbol"),
                        "reference": item["reference"], "units_per_nav": units, "valuation_at": at,
                        "quote_at": item.get("at"), "quoted_underlying": source["underlying_today"]}
            contracts.append(contract)
            key = (item["reference"]["ticker"], item["expiry"], model["style"], model["rate"], model["dividend_yield"])
            groups.setdefault(key, []).append(contract)
            if key not in fund_groups[fund]:
                fund_groups[fund].append(key)
        prepared[fund] = {"full": spec["full"], "sleeve_today": sleeve_today,
                       "source_sleeve_today": source_today, "contracts": contracts,
                       "cash": spec["cash"], "shares": spec["shares"],
                       "history": spec.get("history"), "asof": spec["asof"]}
    completed, evaluating_groups, evaluating_funds = set(), set(), set()

    def group(key):
        if key in completed:
            return
        if key in evaluating_groups:
            raise Unavailable("the option books depend on one another without an independently priced reference")
        evaluating_groups.add(key)
        try:
            calibrate(key)
            completed.add(key)
        finally:
            evaluating_groups.remove(key)

    def calibrate(key):
        contracts = groups[key]
        first = contracts[0]
        if any(abs(c["underlying_today"] - first["underlying_today"]) > 1e-8 * max(1, first["underlying_today"])
               or abs(c["years"] - first["years"]) > 1e-10 for c in contracts):
            raise Unavailable(f"{key[0]}'s option book needs underlying quotes at a common observation time")
        ratio = reference_ratio(first["reference"], name_ratio, factor_ratio)
        for c in contracts:  # each contract at the volatility that makes its own worlds average to its value (1.6)
            model = {k: c[k] for k in ("kind", "style", "strike", "years", "rate", "dividend_yield",
                                       "underlying_today", "value_today")}
            c.update(options.worlds_value(**model, levels={w: model["underlying_today"] * ratio[w] for w in W},
                                          weights=q))

    def value(fund):
        if fund in values:
            return values[fund], terms[fund]
        if fund in evaluating_funds:
            raise Unavailable(f"{fund}'s option holdings depend on their own price without an independent value")
        evaluating_funds.add(fund)
        try:
            for key in fund_groups[fund]:
                group(key)
            spec, term = specs[fund], prepared[fund]
            sleeve = {w: sum(c["units_per_nav"] * c["values"][w] for c in term["contracts"]) for w in W}
            if spec["full"]:
                whole = {w: 1.0 + sleeve[w] - term["sleeve_today"] for w in W}
                for share in spec["shares"]:
                    ratios = reference_ratio(share["reference"], name_ratio, factor_ratio)
                    for w in W:
                        whole[w] += share["weight"] * (ratios[w] - 1.0)
                values[fund] = whole
            else:
                values[fund] = sleeve
            terms[fund] = term
            return values[fund], term
        finally:
            evaluating_funds.remove(fund)

    return _Book(value, values, terms)


def compute(specs: dict, load, q: dict, W: list[str], name_ratio, factor_ratio=None) -> tuple[dict, dict]:
    """Every full fund's contractual value change, or its partial option sleeve before the fitted remainder."""
    book = prepare(specs, load, q, W, name_ratio, factor_ratio)
    for fund in specs:
        book.value(fund)
    return book.prices, book.terms
