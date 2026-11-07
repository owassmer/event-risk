"""Channels and betas: the orthogonalization, each name's betas, and each channel's largest month.

The channels in order form an orthogonalized basis: channel 1 is its proxy's return, and
channel k is the residual of proxy k regressed with an intercept on channels 1 to k-1,
over the WINDOW sessions ending at asof on the first channel's calendar, every return a
total return (series.returns). A channel says
nothing on a session its proxy has no return for: its value there is zero, so a proxy
with fewer sessions uses what exists and shortens no other fit. Each name with at least
MIN_DAYS returns gets least-squares coefficients on the channels, with an intercept;
a name with fewer gets [1, 0, ..., 0]. Each holding moves with its own betas: a fund the look-through expands is
fitted on its own series, which holds everything it held in the weights it held it, and one with fewer than MIN_DAYS
returns of its own moves as its holdings do, each name at its own betas and its residual with the market. g[c] =
sum_h V_h * b_h[c] over the holdings as given. A channel's largest month, M_c, is the largest
absolute return of its own part over MONTH consecutive sessions of the window, each window's proxy return put through
the orthogonalization as window_returns puts it.
"""

from __future__ import annotations

import numpy as np

from harness.derive import series
from harness.derive.engine import Derivation, Inputs, Unavailable, digest
from harness.derive.portfolio import (FIRST, asof_of, closes, current_compute, current_inputs, is_today, name_id, paid,
                                      price_series, return_series, unlisted)

MIN_DAYS = 250  # one year, twenty-five observations per coefficient, the floor for a stable fit
MONTH = 21  # M_c is the channel's largest return of its own part over this many consecutive sessions


def ordered_channels(ctx: Inputs) -> list[dict]:
    return sorted(ctx.objects("Channel"), key=lambda c: (c["stated"]["order"], c["id"]))


def _calendar(ctx: Inputs, asof: str) -> dict:
    channels = ordered_channels(ctx)
    if not channels:
        raise Unavailable("the store has no channels")
    first = channels[0]["stated"]["proxy_ticker"]
    if series.is_crypto(first):
        raise Unavailable(f"the first channel's proxy gives the trading calendar, and {first} trades every day")
    return price_series(ctx, first, asof)


def _sessions(m: dict, load) -> list[str]:
    return series.window(closes(m["calendar"], load), m["asof"])


# ---------------------------------------------------------------- the orthogonalization


def _orthogonalize_inputs(ctx: Inputs, cid: str) -> dict:
    asof = asof_of(ctx)
    channels = ordered_channels(ctx)
    position = next(i for i, c in enumerate(channels) if c["id"] == cid)
    me = channels[position]
    return {"asof": asof, "channel": cid, "calendar": _calendar(ctx, asof),
            "proxy": return_series(ctx, me["stated"]["proxy_ticker"], asof),
            "earlier": [ctx.value("orthogonalize", c["id"], ref=True) for c in channels[:position]]}


def _orthogonalize_compute(m: dict, load) -> dict:
    sessions = _sessions(m, load)
    days = sessions[1:]
    found, dividends = closes(m["proxy"], load), paid(m["proxy"], load)
    r = series.returns(found, sessions, dividends)
    observed = [d for d in days if d in r]
    y = np.array([r[d] for d in observed])
    earlier = [load(ref) for ref in m["earlier"]]
    if not earlier:
        values, intercept, coefficients = y, None, {}
    else:
        # A proxy with fewer returns than regressors gets the minimum-norm fit, and its
        # residual, near zero, carries next to nothing; n shows how many returns it had.
        x = np.array([[e["series"].get(d, 0.0) for e in earlier] for d in observed])
        beta, values = series.ols(y, x)
        intercept = float(beta[0])
        coefficients = {e["channel"]: float(b) for e, b in zip(earlier, beta[1:])}
    component = {d: float(v) for d, v in zip(observed, values)}
    months = _months(sessions, found, dividends, coefficients, earlier)
    largest = max(months.items(), key=lambda x: (abs(x[1][1]), x[0]), default=None)
    # A pair its source does not list has no series: the factor is zero in every session, and says why.
    why = unlisted(load(m["proxy"]["ref"]), m["proxy"]["ticker"]) if m["proxy"]["ref"].get("data") else None
    return {"channel": m["channel"], "proxy": m["proxy"]["ticker"],
            "window": [days[0], days[-1]] if days else None, "n": len(observed),
            "intercept": intercept, "coefficients": coefficients,
            "largest_month": abs(largest[1][1]) if largest else 0.0,
            "largest_month_sessions": {"start": largest[1][0], "end": largest[0], "move": largest[1][1]}
            if largest else None,
            "months": {end: v for end, (_, v) in months.items()}, "series": component,
            **({"unavailable": {"orthogonalization": why}} if why else {})}


def _months(sessions: list[str], found: dict[str, float], dividends: dict[str, float], coefficients: dict,
            earlier: list[dict]) -> dict[str, tuple[str, float]]:
    """The channel's own part over every MONTH consecutive sessions of the window, or over all of them where there are
    fewer, by the session each ends on: the session it starts from, and the proxy's return from that session's close
    to the end's, less its coefficients on the earlier channels times their own parts over the same sessions, as
    window_returns computes a window. A window the proxy has no close at an end of is 0."""
    width = min(MONTH, len(sessions) - 1)
    out: dict[str, tuple[str, float]] = {}
    for i in range(len(sessions) - width) if width > 0 else ():
        start, end = sessions[i], sessions[i + width]
        raw = series.window_return(found, dividends, start, end)
        own = 0.0 if raw is None else raw - sum(
            coefficients[e["channel"]] * e["months"].get(end, 0.0) for e in earlier)
        out[end] = (start, own)
    return out


ORTHOGONALIZE = Derivation(
    "orthogonalize", 7, "Channel", _orthogonalize_inputs, _orthogonalize_compute,
    fields=("orthogonalization",),
    pick=lambda out, cid: {"orthogonalization": {k: out[k] for k in ("window", "n", "intercept", "coefficients",
                                                                    "largest_month", "largest_month_sessions")}},
)


# ---------------------------------------------------------------- the betas


def _betas_inputs(ctx: Inputs, subject: str) -> dict:
    asof = asof_of(ctx)
    lookthrough = ctx.value("lookthrough", "portfolio")
    kinds = lookthrough["output"]["kinds"]
    channels = ordered_channels(ctx)
    # Every name's and every proxy's series, fetched side by side before they are read one by one.
    tickers = [c["stated"]["proxy_ticker"] for c in channels] + \
        [info["beta"] for info in kinds.values() if info["kind"] in ("name", "fund", "unmapped")]
    window = (series.lookback(asof), asof)
    ctx.prefetch("bars", [t for t in tickers if not series.is_crypto(t)], window, stage="prices")
    ctx.prefetch("dividends", [t for t in tickers if not series.is_crypto(t)], window, stage="prices")
    ctx.prefetch("hourly", [t for t in tickers if series.is_crypto(t)], window, stage="prices")
    if is_today(asof):  # each proxy's latest mark, which its price as of today reads
        proxies = [c["stated"]["proxy_ticker"] for c in channels]
        ctx.prefetch("snapshot", [t for t in proxies if not series.is_crypto(t)], (asof, asof), stage="prices")
        ctx.prefetch("last_trade", [t for t in proxies if series.is_crypto(t)], (asof, asof), stage="prices")
    bars = {}
    # a fund the look-through expands moves as its holdings do where the store cannot read its own series
    expanded = {info["beta"] for info in kinds.values() if info.get("expanded")}
    for info in kinds.values():
        if info["kind"] in ("name", "fund", "unmapped") and info["beta"] not in bars:
            bars[info["beta"]] = return_series(ctx, info["beta"], asof, required=info["beta"] not in expanded)
    funds = {name_id(f): {"depth": info["depth"], "parts": info["parts"]}
             for f, info in lookthrough["output"]["funds"].items() if info.get("expanded")}
    return {"asof": asof, "names": kinds, "funds": funds, "calendar": _calendar(ctx, asof),
            "channels": [ctx.value("orthogonalize", c["id"], ref=True) for c in channels], "bars": bars}


def _betas_compute(m: dict, load) -> dict:
    sessions = _sessions(m, load)
    days = sessions[1:]
    channels = [load(ref) for ref in m["channels"]]
    ids = [c["channel"] for c in channels]
    basis = np.array([[c["series"].get(d, 0.0) for c in channels] for d in days]) if days else np.zeros((0, len(ids)))
    row = {d: i for i, d in enumerate(days)}
    market = [1.0] + [0.0] * (len(ids) - 1)
    objects, links, fits = {}, {}, {}
    for nid, info in sorted(m["names"].items()):
        if info["kind"] == "cash":
            b, fit = [0.0] * len(ids), {"rule": "cash has zero beta"}
        elif info["kind"] == "other":
            b, fit = market, {"rule": "a ticker that is neither a name nor a fund moves with the market"}
        else:
            found = m["bars"][info["beta"]]
            r = series.returns(closes(found, load), sessions, paid(found, load))
            observed = [d for d in days if d in r]
            if len(observed) < max(MIN_DAYS, len(ids) + 2):
                b = market
                fit = {"rule": f"{len(observed)} returns, under the {MIN_DAYS} a fit needs", "n": len(observed)}
            else:
                coefficients, residuals = series.ols(np.array([r[d] for d in observed]),
                                                     basis[[row[d] for d in observed]])
                b = [float(x) for x in coefficients[1:]]
                fit = {"rule": "least squares", "n": len(observed), "alpha": float(coefficients[0]),
                       "residual_sd": float(np.std(residuals))}
        if info["beta"] and info["kind"] in ("fund", "unmapped"):
            fit["beta_of"] = info["beta"]
        objects[nid] = {"type": "Name", "fields": {"b": dict(zip(ids, b))}}
        fits[nid] = fit
    # A fund the look-through expands moves with its own betas where it has a year of returns, and otherwise as its
    # holdings do: its parts at their own betas, a fund inside it first.
    for nid, fund in sorted(m["funds"].items(), key=lambda kv: (-kv[1]["depth"], kv[0])):
        if fits[nid]["rule"] == "least squares":
            fits[nid]["sensitivity"] = "own"
            continue
        b = [sum(w * objects[part]["fields"]["b"][cid] for part, w in fund["parts"].items()) for cid in ids]
        objects[nid] = {"type": "Name", "fields": {"b": dict(zip(ids, b))}}
        fits[nid] = {"rule": f"{fits[nid].get('n', 0)} returns of its own, under the {MIN_DAYS} a fit needs: it moves "
                             f"as its holdings do", "n": fits[nid].get("n", 0), "sensitivity": "holdings",
                     "beta_of": fits[nid].get("beta_of")}
    for nid, obj in objects.items():
        for cid, beta in obj["fields"]["b"].items():
            links[f"loads_on:{nid}>{cid}"] = {"relation": "loads_on", "from": nid, "to": cid, "fields": {"beta": beta}}
    return {"channels": ids, "objects": objects, "links": links, "fits": fits}


def moves_with(funds: list[str], nid: str, betas: dict) -> dict:
    """The betas a holding's dollars at a name move with, by the funds they pass through, outermost first: the first
    fund that moves with its own betas, its holdings at the next level where it moves as they do, and the name's own
    where they pass through none."""
    for fund in funds:
        fid = name_id(fund)
        if (betas["fits"].get(fid) or {}).get("sensitivity") == "own":
            return betas["objects"][fid]["fields"]["b"]
    return betas["objects"][nid]["fields"]["b"]


BETAS = Derivation("betas", 3, "Portfolio", _betas_inputs, _betas_compute,
                   subject_of=lambda store, entity: "portfolio", produces=("Name", "loads_on"), stage="exposures")


# ---------------------------------------------------------------- g


def _g_inputs(ctx: Inputs, subject: str) -> dict:
    return {"lookthrough": ctx.value("lookthrough", "portfolio", ref=True),
            "betas": ctx.value("betas", "portfolio", ref=True)}


def _g_compute(m: dict, load) -> dict:
    """g, the sum over the holdings as given of each one's dollars times its betas, and each holding's part of it: a
    name, or a fund booked whole, at its own betas; a fund the look-through expands at its own, or, with fewer than a
    year of its own returns, as its holdings do; and that part over the holding's dollars, the holding's beta."""
    looked = load(m["lookthrough"])
    betas = load(m["betas"])
    ids = betas["channels"]
    g = dict.fromkeys(ids, 0.0)
    by_holding = {}
    for hid, found in (looked.get("paths") or {}).items():
        held = sum(x["dollars"] for x in found)
        part = dict.fromkeys(ids, 0.0)
        for x in found:
            b = moves_with(x["funds"], x["name"], betas)
            for cid in ids:
                part[cid] += x["dollars"] * b[cid]
        for cid in ids:
            g[cid] += part[cid]
        top = next((x["funds"][0] for x in found if x["funds"]), None)  # the fund the holding is, where expanded
        sensitivity = (betas["fits"].get(name_id(top)) or {}).get("sensitivity") if top else None
        by_holding[hid] = {"dollars": held, "g": part, "b": {cid: part[cid] / held if held else None for cid in ids},
                           **({"sensitivity": sensitivity} if sensitivity else {})}
    return {"g": g, "channels": ids, "by_holding": by_holding}


G = Derivation("g", 3, "Portfolio", _g_inputs, _g_compute, fields=("g",),
               subject_of=lambda store, entity: "portfolio", stage="exposures")


# ---------------------------------------------------------------- the proxy Metric


def _proxy_inputs(ctx: Inputs, mid: str) -> dict:
    """The proxy's current price, and its first mark of the as-of, which the judge reads (evidence.metric)."""
    ticker, asof = mid.split(":", 2)[2], asof_of(ctx)
    out = {"metric": mid, **current_inputs(ctx, ticker, asof)}
    if out["today"]:
        kind = "last_trade" if series.is_crypto(ticker) else "snapshot"
        out["first"] = ctx.data(kind, ticker, covering=(asof, asof), required=False, at=FIRST)
    return out


def _proxy_compute(m: dict, load) -> dict:
    ticker = m["ticker"]
    found = current_compute(m, load) or {}
    first = found
    if (m.get("first") or {}).get("data"):  # the price as the store first read it on the as-of
        kind = "last_trade" if series.is_crypto(ticker) else "snapshot"
        first = current_compute({**m, kind: m["first"]}, load) or found
    source = ("Coinbase Exchange: the latest trade as of today, else the price at 16:00 America/New_York"
              if series.is_crypto(ticker) else "Massive: the latest trade as of today, else the close")
    return {"id": f"price:{ticker}", "unit": "USD", "traded": True, "resolution_source": source,
            "current_value": found.get("price"), "asof": found.get("session"), "time": found.get("time"),
            "from": found.get("from"), "previous_close": found.get("previous_close"),
            "previous_session": found.get("previous_session"), "session_change": found.get("session_change"),
            "session_move": found.get("session_move"),
            "first_value": first.get("price"), "first_asof": first.get("session")}


PROXY_PRICE = Derivation(
    "proxy_price", 6, "Metric", _proxy_inputs, _proxy_compute,
    fields=("id", "unit", "current_value", "asof", "traded", "resolution_source"),
    applies=lambda store, mid: mid.startswith("metric:price:"),
)

# ---------------------------------------------------------------- any window: how the channels moved


def window_subject(start: str, end: str) -> str:
    return f"window:{start}:{end}"


def _ref(value: dict) -> dict:
    return {"derivation": value["derivation"], "subject": value["subject"], "basis": value["basis"],
            "sha256": digest(value["output"])}


def _window_inputs(ctx: Inputs, subject: str) -> dict:
    """Each channel's proxy series over the window, with the orthogonalization it goes through."""
    _, start, end = subject.split(":")
    channels = []
    for c in ordered_channels(ctx):
        orth = ctx.value("orthogonalize", c["id"])
        ticker = orth["output"]["proxy"]
        kind = "hourly" if series.is_crypto(ticker) else "bars"
        item = {"ticker": ticker, "ref": ctx.data(kind, ticker, covering=(start, end))}
        if not series.is_crypto(ticker):
            item["dividends"] = ctx.data("dividends", ticker, covering=(start, end))
        channels.append({"channel": c["id"], "name": c["stated"]["name"], "orthogonalization": _ref(orth),
                         "series": item})
    if not channels:
        raise Unavailable("the store has no channels")
    return {"window": [start, end], "channels": channels}


def _window_compute(m: dict, load) -> dict:
    """Each proxy's total return from the close of start to the close of end, and the same put through the
    orthogonalization in channel order: channel k's return less its coefficients on the earlier channels times
    their orthogonalized returns. A proxy with no close at an end of the window has none, and 0 orthogonalized."""
    start, end = m["window"]
    found_closes, returns, orthogonalized = {}, {}, {}
    for c in m["channels"]:
        cid, item = c["channel"], c["series"]
        found = closes(item, load)
        found_closes[item["ticker"]] = {d: v for d, v in sorted(found.items()) if start <= d <= end}
        r = series.window_return(found, paid(item, load), start, end)
        returns[cid] = r
        if r is None:
            orthogonalized[cid] = 0.0
            continue
        coefficients = load(c["orthogonalization"])["coefficients"]
        orthogonalized[cid] = r - sum(beta * orthogonalized[earlier] for earlier, beta in coefficients.items())
    return {"window": [start, end], "closes": found_closes, "returns": returns, "orthogonalized": orthogonalized,
            "proxies": {c["channel"]: c["series"]["ticker"] for c in m["channels"]}}


# window_returns(start, end): how the channels moved over a past episode. It gives no object a field: the tool
# reads its Computation by subject, window:{start}:{end}.
WINDOW_RETURNS = Derivation("window_returns", 3, "Channel", _window_inputs, _window_compute,
                            applies=lambda store, entity: False)

DERIVATIONS = (ORTHOGONALIZE, BETAS, G, PROXY_PRICE, WINDOW_RETURNS)
