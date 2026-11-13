"""Channels and betas (MATH.md 1.2, 1.3, 3.2): the channels' covariance and its orthogonalization, each name's response,
g, the portfolio's volatility, each channel's largest month, and any window's returns.

The channels (`factors`). The proxies' total returns on the sessions of the WINDOW ending at the as-of, the first
channel's proxy's closes, and the common history, the sessions on which every proxy has a return (estimate.common):
the factor covariance and the sensitivity fits read those, nothing being filled in for a session a proxy lacks.
Omega, the covariance of completed moves (estimate.covariances), is what the fill reads (impact), and its LDL' in
channel order is the orthogonalization (`orthogonalize`): channel 1 is its proxy's return, and channel k's own part is
its proxy's return less its coefficients on the earlier channels' own parts.

Each name's response (`betas`). Each holding, each name a fund reaches, and each currency a listing abroad is quoted
in is fitted on its own returns over the common history, over the same and the previous session
(estimate.fit_response); its full response, the summed coefficients, in the orthogonalized basis by L'
(estimate.Model.orthogonal), is b, which g, D, and d_i read. It leans on the market's sensitivity, 1 on the market and 0
on the rest (estimate.market_prior), but for a currency or crypto pair, which leans on no response, and a fund whose rows
are read, expanded or booked whole, which leans on its rows: each row at the response of the asset it refers to by the
fund's signed exposure to it, a company's or a fund's own, a currency's for money held in it, an index's or a crypto
pair's own for a derivative on it; the shares its rows reach and nothing prices at the listed share's prior; a dollar of
cash at none; and what the rows leave undetermined at no response, only its uncertainty, for its own history to
determine (_rows_centre).
A listing abroad is fitted on its translated local return a_t, and its currency once, on New York's clock, for every
listing quoted in it, exactly where a channel's proxy is that currency's rate in dollars in either direction: its full
response in dollars is b_a + b_FX, held directly or through a fund. Cash moves with nothing, and a name with no price
(portfolio.unpriced) has no betas: its exposure stays, and its fund's own response carries its dollars.

g[c] = sum_h V_h b_h[c] over the holdings as given. The portfolio's volatility to the horizon's end (MATH.md 3.2) is the
standard deviation of its move over the sessions to the horizon's end, from its variance of completed moves per
session (estimate.covariances): the channels' part, s' Omega s over the common history, s = sum_h V_h (b0_h + b1_h) the
holdings' full responses on the proxies' returns; and the holdings' own parts, each holding's residual e_h,t = r_h,t -
b0_h . x_t - b1_h . x_{t-1} on each session its return covers alone (its intercept being a constant the estimator's
centring removes), as its fit leaves it. The holdings join the total by the session their residuals begin, those
beginning together as one part: each as it has moved with the total before it over the sessions both have, and by its
own variability there, leaning as its fit does (estimate.joined), so a holding listed within the window takes nothing
from the history the others have, and the holdings with the whole window are taken jointly with the channels. A
channel's largest month, M_c, is the largest absolute return of its own part over MONTH consecutive
market sessions of the common history, each window's proxy returns put through the orthogonalization as
window_returns puts them.
"""

from __future__ import annotations

import math

from datetime import date, timedelta
from typing import Any, Callable, Iterable

import numpy as np

from harness.derive import estimate, history, portfolio, rows, series
from harness.derive.engine import Derivation, Inputs, Refused, Unavailable, digest
from harness.derive.estimate import Model, Settings
from harness.derive.history import calendar_inputs, ordered_channels, window_sessions
from harness.derive.portfolio import (asof_of, closes, current_compute, current_inputs, held_name, is_today, name_id,
                                      no_price, pseudo, shocked_names, ticker_of)

MONTH = 21  # M_c is the channel's largest move of its own part over this many consecutive sessions
# A channel whose own part's standard deviation is below this share of its proxy's has none: its proxy is a combination
# of the earlier ones, to the precision the returns carry (the tolerance R's lm uses to drop a collinear column).
COMBINED = 1e-7

__all__ = ["ordered_channels", "unpriced_names", "priced_names", "price_fields", "proxy_fields", "window_subject", "DERIVATIONS"]


def _prefetch(ctx: Inputs, tickers: list[str], asof: str, stage: str) -> None:
    window = (series.lookback(asof), asof)
    listed = [t for t in tickers if not series.is_pair(t) and not series.is_abroad(t)]
    ctx.prefetch("bars", listed, window, stage=stage)
    ctx.prefetch("dividends", [t for t in listed if not series.is_index(t)], window, stage=stage)
    ctx.prefetch("hourly", [t for t in tickers if series.is_pair(t)], window, stage=stage)
    ctx.prefetch("home", [t for t in tickers if series.is_abroad(t)], window, stage=stage)


def _quote_of(ctx: Inputs, item: dict) -> dict | None:
    """A pair proxy's base and quote currencies, as its source gives them with its rates."""
    if item.get("kind") != "pair" or not item["ref"].get("data"):
        return None
    payload = ctx.payload(item["ref"]) or {}
    return {"base": payload.get("base"), "quote": payload.get("quote")} if payload.get("quote") else None


# ---------------------------------------------------------------- the channels' model


def _factors_inputs(ctx: Inputs, subject: str) -> dict:
    asof = asof_of(ctx)
    channels = ordered_channels(ctx)
    calendar = calendar_inputs(ctx, asof)
    tickers = [c["stated"]["proxy"] for c in channels]
    _prefetch(ctx, tickers, asof, "prices")
    items = [history.series_inputs(ctx, t, asof) for t in tickers]
    return {"asof": asof, "settings": estimate.SETTINGS.as_dict(), "channels": [c["id"] for c in channels],
            "tickers": tickers, "calendar": calendar, "series": items,
            "pairs": {c["id"]: q for c, item in zip(channels, items) if (q := _quote_of(ctx, item))}}


def _factors_compute(m: dict, load) -> dict:
    """The proxies' returns on each session of the window, none where a proxy has none on it; the common history, the
    sessions on which every proxy has one; and over it the channels' mean, same-session covariance Gamma_0, covariance
    of completed moves Omega, and Omega's LDL' in channel order. Where the channels as stated leave none of that,
    Refused, naming them: a write that would leave it so is refused (TOOLS.md 5), by this computation."""
    settings = Settings(**m["settings"])
    sessions = window_sessions(m["calendar"], load, m["asof"])
    t_len, k = len(sessions) - 1, len(m["channels"])
    if t_len < 2:  # the window's sessions are the first channel's proxy's closes: fewer than two returns to the as-of,
        # none where it has no close to the as-of at all
        held = f"1 session in common in the window, {sessions[1]}" if t_len == 1 else \
            "no session in common in the window"
        raise Refused(f"the factors' proxies share {held}, too few to measure how {k} factors move together",
                      [m["channels"][0]])
    _one_rate_one_channel(m)
    x = np.full((t_len, k), np.nan)
    for j, item in enumerate(m["series"]):
        for a, b, r in history.returns(item, load, sessions)["dollar"]:
            if b == a + 1:  # a return over one session; one over several leaves each of them out
                x[a, j] = r
    ok = estimate.common(x)
    n = int(ok.sum())
    days = [sessions[t + 1] for t in np.flatnonzero(ok)]
    held = f"{n} sessions in common in the window, from {days[0]}" if n else "no session in common in the window"
    if n <= k:  # the covariance of k channels needs more sessions than channels: named, the proxies with the fewest
        counts = (~np.isnan(x)).sum(axis=0)  # returns, whose history is what a correction lengthens
        raise Refused(f"the factors' proxies share {held}, too few to measure how {k} factors move together",
                      [c for c, count in zip(m["channels"], counts) if count == counts.min()])
    mean, gamma0, omega = estimate.covariances(x, ok, settings.memory)
    try:
        lower, d = estimate.ldl(omega)
    except np.linalg.LinAlgError:  # a proxy's returns are the others' combined: no channel of its own
        lower, d = None, None
    combined = [j for j in range(k) if d is None or d[j] <= (COMBINED * COMBINED) * omega[j, j]]
    if combined:
        j = combined[0]
        raise Refused(f"over the {held}, {m['tickers'][j]}'s returns are a combination of "
                      f"{', '.join(m['tickers'][:j]) or 'none'}'s, so it carries nothing the factors before it do not",
                      m["channels"][:j + 1])
    pack = estimate.pack
    return {"sessions": sessions, "channels": m["channels"], "tickers": m["tickers"], "settings": m["settings"],
            "window": {"sessions": n, "first": days[0], "last": days[-1]}, "x": pack(x), "mean": pack(mean),
            "gamma0": pack(gamma0), "omega": pack(omega), "lower": pack(lower), "d": pack(d), "pairs": m["pairs"]}


def _one_rate_one_channel(m: dict) -> None:
    """Two channels whose proxies are one rate, quoted each way round, by the two currencies each pair's source gives:
    one instrument carries one channel, so the second is refused, naming the first."""
    seen: dict[frozenset, tuple[str, str]] = {}
    for cid, ticker in zip(m["channels"], m["tickers"]):
        pair = (m.get("pairs") or {}).get(cid) or {}
        if not (pair.get("base") and pair.get("quote")):
            continue
        key = frozenset((pair["base"], pair["quote"]))
        if key in seen:
            first, proxy = seen[key]
            raise Refused(f"{ticker} is {proxy}, the proxy of {first}, quoted the other way round, and one instrument "
                          f"carries one factor", [first, cid])
        seen[key] = (cid, ticker)


FACTORS = Derivation("factors", 4, "Portfolio", _factors_inputs, _factors_compute,
                     applies=lambda store, entity: False, stage="exposures")


def model(out: dict) -> Model:
    return Model.load(out)


# ---------------------------------------------------------------- the orthogonalization


def _orthogonalize_inputs(ctx: Inputs, cid: str) -> dict:
    asof = asof_of(ctx)
    channels = ordered_channels(ctx)
    position = next(i for i, c in enumerate(channels) if c["id"] == cid)
    me = channels[position]
    return {"asof": asof, "channel": cid, "factors": ctx.value("factors", "market", ref=True),
            "proxy": history.series_inputs(ctx, me["stated"]["proxy"], asof),
            "earlier": [ctx.value("orthogonalize", c["id"], ref=True) for c in channels[:position]]}


def _orthogonalize_compute(m: dict, load) -> dict:
    """The channel's coefficients on the earlier channels' own parts, its own part's variance, the common history it
    is measured over, by its sessions and their first and last, and its largest month."""
    fm = load(m["factors"])
    sessions = fm["sessions"]
    k = fm["channels"].index(m["channel"])
    lower = np.array(fm["lower"])
    earlier = [load(ref) for ref in m["earlier"]]
    coefficients = {c: float(lower[k, j]) for j, c in enumerate(fm["channels"][:k])}
    item = m["proxy"]
    # in dollars, net of the income accrued from the distributions of the history it reads (MATH.md 3.2)
    moves = history.moves(item, load, sessions, since=series.lookback(m["asof"]), until=m["asof"])
    ok = estimate.common(np.array(fm["x"], dtype=float).reshape(-1, len(fm["channels"])))
    months = _months(sessions, ok, moves, coefficients, earlier)
    largest = max(months.items(), key=lambda x: (abs(math.expm1(x[1][1])), x[0]), default=None)
    window = fm["window"]
    return {"channel": m["channel"], "proxy": item["ticker"], "window": [window["first"], window["last"]],
            "n": window["sessions"], "coefficients": coefficients, "variance": float(fm["d"][k]),
            "largest_month": abs(math.expm1(largest[1][1])) if largest else 0.0,
            "largest_month_sessions": {"start": largest[1][0], "end": largest[0], "move": math.expm1(largest[1][1])}
            if largest else None,
            "months": {end: v for end, (_, v) in months.items()}}


def _months(sessions: list[str], ok: np.ndarray, moves: series.Moves, coefficients: dict,
            earlier: list[dict]) -> dict[str, tuple[str, float]]:
    """The channel's own part over every MONTH consecutive market sessions of the common history (`ok`, by the row of
    each session's return), or over as many as its longest run of them holds where that is fewer, by the session each
    ends on: the session it starts from, and the proxy's compounded move from that session's close to the end's, what
    holding it earned less the income it accrued (series.Moves), less its coefficients on the earlier channels times their own parts over
    the same sessions, as window_returns computes a window (MATH.md 3.2). A window the proxy has no close at an end of is
    0."""
    run, longest = 0, 0
    for good in ok:
        run = run + 1 if good else 0
        longest = max(longest, run)
    width = min(MONTH, longest)
    out: dict[str, tuple[str, float]] = {}
    for i in range(len(sessions) - width) if width > 0 else ():
        if not ok[i:i + width].all():  # the returns of sessions i + 1 .. i + width
            continue
        start, end = sessions[i], sessions[i + width]
        moved = moves(start, end)
        # the own part, compounded (1.3): the window's compounded move less its coefficients on the earlier factors'
        # own parts there
        out[end] = (start, 0.0 if moved is None else
                    math.log1p(moved) - sum(coefficients[e["channel"]] * e["months"].get(end, 0.0) for e in earlier))
    return out


ORTHOGONALIZE = Derivation(
    "orthogonalize", 20, "Factor", _orthogonalize_inputs, _orthogonalize_compute,
    fields=("orthogonalization", "largest_month"),
    pick=lambda out, cid: {"orthogonalization": {k: out[k] for k in ("window", "n", "coefficients", "variance",
                                                                    "largest_month", "largest_month_sessions")},
                           "largest_month": out["largest_month"]},
)


# ---------------------------------------------------------------- each name's response


def read_beside(lookthrough: dict, shocked: Iterable[str] = ()) -> dict[str, dict]:
    """The names whose prices the valuation reads beside those the look-through books (MATH.md 1.4, 1.6; ONTOLOGY.md 2.1): each
    name a
    consequence runs to, a company or a fund an event names, and each fund on one reference's reference, a company or
    the fund that follows an index, where the portfolio reaches it through no row. Each is fitted on its own series, as
    a name is, priced through its own listing, and carries no exposure."""
    kinds = lookthrough.get("kinds") or {}
    refs = {spec["reference"] for spec in (lookthrough.get("multiples") or {}).values() if spec.get("reference")}
    return {nid: {"kind": "name", "beta": ticker_of(nid)} for nid in sorted(refs | set(shocked))
            if nid not in kinds and not pseudo(ticker_of(nid))}


def _betas_inputs(ctx: Inputs, subject: str) -> dict:
    asof = asof_of(ctx)
    lookthrough = ctx.value("lookthrough", "portfolio")
    kinds = {**lookthrough["output"]["kinds"], **read_beside(lookthrough["output"], shocked_names(ctx))}
    channels = ordered_channels(ctx)
    tickers = sorted({info["beta"] for info in kinds.values() if info["kind"] in ("name", "fund", "other")})
    # each index a fund's rows are exposed to whose own values are read, and no companies established: its series
    underlyings = sorted({u for f in lookthrough["output"]["funds"].values()
                          for u in ((f.get("rest") or {}).get("underlyings") or {})})
    _prefetch(ctx, [c["stated"]["proxy"] for c in channels] + tickers + underlyings, asof, "prices")
    if is_today(asof):  # each proxy's latest mark, which its price as of today reads
        proxies = [c["stated"]["proxy"] for c in channels]
        ctx.prefetch("snapshot", [t for t in proxies if not series.is_pair(t)], (asof, asof), stage="prices")
        ctx.prefetch("last_trade", [t for t in proxies if series.is_pair(t)], (asof, asof), stage="prices")
    expanded = {info["beta"] for info in kinds.values() if info.get("expanded")}
    # a fund the look-through expands can be read where its own series is: its rows give its response until then
    found = history.series_inputs_many(ctx, tickers, asof, required=lambda t: t not in expanded)
    # every fund whose rows were read, expanded or booked whole, leans on them; one booked whole has no parts
    funds = {name_id(f): {"expanded": bool(info.get("expanded")), "depth": info.get("depth", 0),
                          "parts": info.get("parts") or {}, "rest": info["rest"]}
             for f, info in lookthrough["output"]["funds"].items() if "rest" in info}
    currencies = sorted({c for item in found.values() if item["kind"] == "abroad" and (c := item.get("currency"))}
                        | {c for fund in funds.values() for c in (fund["rest"].get("currencies") or {})})
    window = (series.lookback(asof), asof)
    # each currency's rates, side by side: the currencies are known only once the listings abroad have answered
    ctx.prefetch("hourly", [series.dollar_pair(c) for c in currencies], window, stage="prices")
    return {"asof": asof, "settings": estimate.SETTINGS.as_dict(), "names": kinds, "funds": funds,
            "factors": ctx.value("factors", "market", ref=True), "series": found,
            "underlyings": history.series_inputs_many(ctx, underlyings, asof),
            "currencies": {c: ctx.data("hourly", series.dollar_pair(c), covering=window) for c in currencies}}


def _currency_fit(cur: str, ref: dict, fm: dict, mdl: Model, load) -> dict | None:
    """A currency's full response in dollars: exactly a channel's, where the channel's proxy is its rate in dollars
    either way round, matched by the base and quote currencies its source gives, with no uncertainty; otherwise fitted
    once, on its rate at each New York session's close, as any series is. None where its rate's source answered it
    has no rates for it: nothing is invented for it."""
    k = mdl.k
    for j, cid in enumerate(mdl.channels):
        pair = (fm.get("pairs") or {}).get(cid) or {}
        if {pair.get("base"), pair.get("quote")} == {cur, series.PRICE_CURRENCY}:
            sign = 1.0 if pair.get("base") == cur else -1.0
            full = np.zeros(k)
            full[j] = sign
            return {"currency": cur, "by": cid, "direction": "direct" if sign > 0 else "inverse", "full": full,
                    "session": np.vstack([full, np.zeros(k)]), "cov": np.zeros((k, k)), "n": None}
    if ref.get("data") and no_price(load(ref)):
        return None
    item = {"ticker": series.dollar_pair(cur), "kind": "pair", "ref": ref}
    prior = mdl.no_response_prior()
    fit = mdl.fit(history.returns(item, load, mdl.sessions)["dollar"], prior)
    return {"currency": cur, "by": "fit", "full": fit["full"], "session": fit["session"], "cov": fit["cov"],
            "n": fit["n"], "prior": prior.source}


def _underlying_fit(ticker: str, item: dict, mdl: Model, load) -> dict | None:
    """The full response of what a fund's rows refer to by its own series, fitted once for every fund whose rows are
    exposed to it: an index's, on its values, as any listed series is; a crypto pair's, on its price at each New York
    session's close, leaning on no response, as a pair held directly does. What a derivative on it moves with. None
    where its series gives no return in the common history: nothing is invented for it."""
    prior = mdl.no_response_prior() if series.is_pair(ticker) else mdl.market_prior()
    fit = mdl.fit(history.returns(item, load, mdl.sessions)["own"], prior)
    if not fit["n"]:
        return None
    return {"full": fit["full"], "cov": fit["cov"], "session": fit["session"], "n": fit["n"], "prior": prior.source}


def _rows_centre(fund: dict, posts: dict, currencies: dict, underlyings: dict, k: int, quoted: str | None = None
                 ) -> tuple[np.ndarray, np.ndarray, dict]:
    """What a fund's rows say its full response is (MATH.md 1.2, 3.2): each row at the response of the asset it refers
    to, by the fund's signed exposure to it per dollar of the fund: a company's or a fund's own, in dollars, where it
    has one; a currency's, for money held in it; an index's or a crypto pair's own, fitted once on its series, for a
    derivative on it; a share no series prices, the listed share's prior, the market's sensitivity; a dollar of cash,
    none. With that sum's uncertainty: each row's own, and each currency's and each underlying's once, on every dollar
    that rides on it. What the rows leave undetermined, a bond, a rate or credit derivative, what has no series, an
    underlying whose series gives no returns, money in a currency whose rate is not published, says no response, only
    its uncertainty (estimate.rows_prior), so that the fund's own returns determine it.

    A fund quoted in another currency (`quoted`) is fitted on its translated local return, and its currency's response
    added (MATH.md 3.2), so its prior is stated in that basis: the rows' response in dollars less its currency's, and on
    that currency the rows' exposure to it less one."""
    centre, cov = np.zeros(k), np.zeros((k, k))
    shares = float(fund["rest"].get("shares") or 0.0)
    undetermined = float(fund["rest"].get("undetermined") or 0.0)
    referred: dict[str, float] = {}  # each underlying the fund is exposed to whose own series gives its response
    for u, x in sorted((fund["rest"].get("underlyings") or {}).items()):
        if u in underlyings:
            centre += x * underlyings[u]["full"]
            cov += x * x * underlyings[u]["cov"]
            referred[u] = x
        else:  # its series gives no returns: nothing says how it moves
            undetermined += abs(x)
    riding: dict[str, float] = {}  # each currency, and the exposure per dollar of the fund that moves with it
    priced: dict[str, float] = {}
    for part, w in sorted(fund["parts"].items()):
        post = posts.get(part)
        if post is None:  # a share with no price: no response of its own, the listed share's prior
            shares += w
            continue
        centre += w * post["full"]
        cov += w * w * post["cov"]
        priced[part] = w
        if post.get("currency"):
            riding[post["currency"]] = riding.get(post["currency"], 0.0) + w
    held: dict[str, float] = {}
    for c, x in sorted((fund["rest"].get("currencies") or {}).items()):
        if c in currencies:
            held[c] = float(x)
            centre += x * currencies[c]["full"]
            riding[c] = riding.get(c, 0.0) + x
        else:  # its rate is not published: what money in it does is undetermined
            undetermined += abs(x)
    if quoted:  # the fund's own currency's response, which its fit adds to its translated local return
        centre -= currencies[quoted]["full"]
        riding[quoted] = riding.get(quoted, 0.0) - 1.0
    for c, x in riding.items():
        cov += x * x * currencies[c]["cov"]
    centre[0] += shares  # the market's sensitivity, 1 on the first channel
    return centre, cov, {"parts": priced, "currencies": held, "underlyings": referred, "shares": shares,
                         "undetermined": undetermined, **({"quoted": quoted} if quoted else {})}


def _betas_compute(m: dict, load) -> dict:
    parsed: dict[str, Any] = {}  # each reading parsed once: fetched data never changes, and a currency's two years of
    read = load  # hourly rates serve every listing quoted in it and the currency's own fit

    def load(ref: dict) -> Any:
        key = ref.get("data")
        if key is None:
            return read(ref)
        if key not in parsed:
            parsed[key] = read(ref)
        return parsed[key]

    fm = load(m["factors"])
    mdl = model(fm)
    ids, k = mdl.channels, mdl.k
    currencies = {c: f for c, ref in (m.get("currencies") or {}).items()
                  if (f := _currency_fit(c, ref, fm, mdl, load)) is not None}
    underlyings = {u: f for u, item in sorted((m.get("underlyings") or {}).items())
                   if (f := _underlying_fit(u, item, mdl, load)) is not None}
    posts: dict[str, dict] = {}  # each name's full response in dollars, raw, its covariance apart from its currency's
    fits: dict[str, dict] = {}

    def fit_series(nid: str, info: dict, prior: estimate.Prior) -> None:
        item = m["series"][info["beta"]]
        early = history.struck_early(item, load, mdl.sessions)  # its closes struck before New York's: its lag lighter
        found = history.returns(item, load, mdl.sessions)["own"]
        fit = mdl.fit(found, prior, early=early)
        full, session = fit["full"].copy(), fit["session"].copy()
        # where a prior of strength none leaves directions its returns do not span (fewer returns than one more than
        # the channels), how many: the fit holds its prior's centre along them (estimate.fit_response)
        record = {"n": fit["n"], "prior": prior.source, "sigma2": fit["sigma2"], "alpha": fit["alpha"],
                  "lag_strength": mdl.settings.lag_strength_early if early else mdl.settings.lag_strength,
                  **({"undetermining": fit["undetermined"]} if fit.get("undetermined") else {})}
        currency = item.get("currency") if item["kind"] == "abroad" else None
        if currency:  # its currency's response, once for every listing in it
            fx = currencies[currency]
            full, session = full + fx["full"], session + fx["session"]
            record["currency"] = {"currency": fx["currency"], "by": fx["by"], **(
                {"direction": fx["direction"]} if fx.get("direction") else {})}
            record["local"] = {"full": fit["full"].tolist(), "session": fit["session"].tolist()}
        posts[nid] = {"full": full, "cov": fit["cov"], "currency": currency}
        fits[nid] = {**record, "full": full.tolist(), "session": session.tolist()}

    names = m["names"]
    unpriced = {nid for nid, info in names.items() if (item := m["series"].get(info.get("beta")))
                and portfolio.unpriced(info["beta"], _answers(item, load))}
    for nid in sorted(unpriced):  # identified, with an exposure, and no price: its fund's response carries its dollars
        fits[nid] = {"rule": "no price"}
    for nid, info in sorted(names.items()):
        if info["kind"] in ("name", "other") and nid not in m["funds"] and nid not in unpriced:
            # a listed share leans on the market's sensitivity; a currency or crypto pair, being none, on no response
            fit_series(nid, info, mdl.no_response_prior() if series.is_pair(info["beta"]) else mdl.market_prior())
    # a fund booked whole has no parts and may be one inside another, so it is fitted first; then the funds the
    # look-through expands, a fund inside one before it, each on what its rows reach
    for nid, fund in sorted(m["funds"].items(), key=lambda kv: (kv[1]["expanded"], -kv[1]["depth"], kv[0])):
        if nid in unpriced:  # no price of its own: no series to fit, and its fund's move carries its dollars
            continue
        item = m["series"][names[nid]["beta"]]
        quoted = item.get("currency") if item["kind"] == "abroad" else None  # fitted on its local return
        centre, cov, rows = _rows_centre(fund, posts, currencies, underlyings, k, quoted)
        fit_series(nid, names[nid], mdl.rows_prior(centre, cov, shares=rows["shares"],
                                                   undetermined=rows["undetermined"]))
        fits[nid].update({"sensitivity": "own", "rows": {**rows, "centre": centre.tolist()}})
    for nid, info in sorted(names.items()):
        if info["kind"] == "fund" and nid not in fits:  # a fund whose rows are not read leans on the market's
            fit_series(nid, info, mdl.market_prior())
            fits[nid]["sensitivity"] = "own"
    objects, links = {}, {}
    for nid, info in sorted(names.items()):
        if nid in unpriced:  # no betas: no series to fit, and none is invented
            objects[nid] = {"type": "Name", "fields": {}}
            continue
        if info["kind"] == "cash":
            b = np.zeros(k)
            fits[nid] = {"rule": "cash"}
        else:
            b = mdl.orthogonal(np.array(fits[nid]["full"]))
            if info["kind"] == "fund":
                fits[nid]["beta_of"] = info["beta"]
        objects[nid] = {"type": "Name", "fields": {"betas": dict(zip(ids, (float(v) for v in b)))}}
        for cid, beta in objects[nid]["fields"]["betas"].items():
            links[f"beta_to:{nid}>{cid}"] = {"relation": "beta_to", "from": nid, "to": cid, "fields": {"beta": beta}}
    return {"channels": ids, "objects": objects, "links": links, "fits": fits,
            "currencies": {c: {"by": f["by"], "full": estimate.pack(f["full"]), "n": f["n"],
                               "session": estimate.pack(f["session"]),
                               "b": dict(zip(ids, (float(v) for v in mdl.orthogonal(np.array(f["full"]))))),
                               **({"direction": f["direction"]} if f.get("direction") else {}),
                               **({"prior": f["prior"]} if f.get("prior") else {})}
                           for c, f in currencies.items()},
            "underlyings": {u: {"full": estimate.pack(f["full"]), "n": f["n"], "session": estimate.pack(f["session"]),
                                "b": dict(zip(ids, (float(v) for v in mdl.orthogonal(np.array(f["full"]))))),
                                "prior": f["prior"]}
                            for u, f in underlyings.items()}}



def option_remainder_returns(dated: dict, fund_returns: list, sessions: list[str], *, event_days: int = 0) -> list:
    """Returns of the non-option capital, with the actual previous day's contracts removed once.

    Trade flows are excluded: previous quantities are marked at the later unit price. A day whose
    expired or sold position has no later mark supplies no observed sleeve return and is left out;
    a gap is never turned into a zero return. Holdings reports with no options are valid cash/asset days.
    """
    out = []
    for a, b, change in fund_returns:
        before, after = dated.get(sessions[a]), dated.get(sessions[b])
        if not before or not after or not before.get("net_assets"):
            continue
        nav = before["net_assets"]
        cutoff = (date.fromisoformat(sessions[a]) + timedelta(days=event_days)).isoformat()
        old = {r["contract"]: r for r in before["contracts"] if r.get("quantity")
               and (rows.named_expiry(r["contract"]) or "") >= cutoff}
        new = {r["contract"]: r for r in after["contracts"] if r.get("quantity")}
        if any(c not in new for c in old):
            continue
        sleeve = sum(r["value"] for r in old.values())
        remainder = 1 - sleeve / nav
        if remainder <= 1e-12:
            continue
        pnl = sum(r["quantity"] * (new[c]["value"] / new[c]["quantity"] - r["value"] / r["quantity"])
                  for c, r in old.items())
        out.append((a, b, (change - pnl / nav) / remainder))
    return out


def _answers(item: dict, load) -> Callable[[str, str], Any]:
    """What a series' sources answered, as portfolio.unpriced reads them: the readings the betas read for it, its
    prices and, for a listing abroad, its currency's rate."""
    def answer(kind: str, subject: str) -> Any:
        for ref in (item.get("ref"), item.get("rate")):
            if ref and ref.get("kind") == kind and ref.get("subject") == subject:
                return load(ref) if ref.get("data") else None
        return None
    return answer


def unpriced_names(betas: dict) -> set[str]:
    """The names the betas found with no price (portfolio.unpriced, on the readings they read): no betas, and no price
    metric; each keeps its exposure, and its fund's own move carries its dollars."""
    return {n for n, f in (betas.get("fits") or {}).items() if f.get("rule") == "no price"}


def priced_names(e: dict, held: Iterable[str], unpriced: Callable[[str], bool]) -> set[str]:
    """The portfolio's names with a price (MATH.md 1.6): each it holds, a fund it looks through among them, or books
    dollars of exposure to, e not zero, that has one, `unpriced` saying which has none (portfolio.unpriced)."""
    return {n for n in {*held, *(n for n, v in e.items() if v)} if not pseudo(ticker_of(n)) and not unpriced(n)}


BETAS = Derivation("betas", 13, "Portfolio", _betas_inputs, _betas_compute,
                   subject_of=lambda store, entity: "portfolio", produces=("Name", "beta_to"), stage="exposures")


# ---------------------------------------------------------------- g


def _g_inputs(ctx: Inputs, subject: str) -> dict:
    return {"holdings": ctx.value("holdings", "portfolio", ref=True),
            "betas": ctx.value("betas", "portfolio", ref=True)}


def _g_compute(m: dict, load) -> dict:
    """g = sum_h V_h b_h over the holdings as given (MATH.md 1.2): each holding's value at its own betas, a company's,
    a fund's own response whether or not it is looked through, and cash at none; and each holding's part of it, with
    its betas."""
    holds = load(m["holdings"])["holds"]
    betas = load(m["betas"])
    ids = betas["channels"]
    g = dict.fromkeys(ids, 0.0)
    by_holding = {}
    for hid, row in sorted(holds.items()):
        nid, held = row["name"], row["dollars"]
        b = (betas["objects"].get(nid) or {}).get("fields", {}).get("betas") or {}
        part = {cid: held * b.get(cid, 0.0) for cid in ids}
        for cid in ids:
            g[cid] += part[cid]
        sensitivity = (betas["fits"].get(nid) or {}).get("sensitivity")
        by_holding[hid] = {"dollars": held, "g": part, "b": {cid: b.get(cid, 0.0) for cid in ids},
                           **({"sensitivity": sensitivity} if sensitivity else {})}
    return {"g": g, "channels": ids, "by_holding": by_holding}


G = Derivation("g", 4, "Portfolio", _g_inputs, _g_compute, fields=("dollar_beta",),
               pick=lambda out, pid: {"dollar_beta": out["g"]},
               subject_of=lambda store, entity: "portfolio", stage="exposures")


# ---------------------------------------------------------------- the portfolio's volatility


def _volatility_inputs(ctx: Inputs, subject: str) -> dict:
    """Each holding's own series, as the betas read it; with the holdings' dollars, each holding's session
    coefficients, and the channels' returns and covariance of completed moves."""
    portfolio = ctx.object("portfolio")
    asof = portfolio["stated"]["asof"]
    end = (date.fromisoformat(asof) + timedelta(days=portfolio["stated"]["horizon_days"])).isoformat()
    kinds = ctx.value("lookthrough", "portfolio")["output"]["kinds"]
    names = {}
    for h in ctx.links("portfolio", "holds"):
        held = held_name(h)  # cash, as its currency moves
        if held.startswith("name:") and not pseudo(ticker_of(held)):
            names.setdefault(held, ticker_of(held))

    def needed(ticker: str) -> bool:
        info = kinds.get(name_id(ticker)) or {}
        return info.get("kind") in ("name", "fund") and not info.get("expanded")

    found = history.series_inputs_many(ctx, list(names.values()), asof, required=needed)
    held = {nid: found[ticker] for nid, ticker in names.items()}
    return {"asof": asof, "horizon_end": end, "settings": estimate.SETTINGS.as_dict(),
            "holdings": ctx.value("holdings", "portfolio", ref=True), "betas": ctx.value("betas", "portfolio", ref=True),
            "factors": ctx.value("factors", "market", ref=True), "series": held}


def _volatility_compute(m: dict, load) -> dict:
    """The portfolio's volatility to the horizon's end (MATH.md 3.2): sqrt(variance per session x sessions ahead), the
    variance of completed moves per session of its value at the as-of's holdings, each holding as g takes it, a fund
    whole on its own history and cash at nothing.

    Each holding's residual is its return less what its session coefficients give (estimate.residuals), and the
    figure is the channels' part and the holdings' own parts taken together, a holding listed within the window
    joining those before it (estimate.figure): the same two functions the evaluation's tooling calls."""
    mdl = model(load(m["factors"]))
    x, ok = mdl.x, mdl.ok
    rows, k = x.shape
    holds = load(m["holdings"])["holds"]
    fits = load(m["betas"])["fits"]
    holdings, names = {}, {}
    for hid, row in sorted(holds.items()):
        nid, dollars = row["name"], row["dollars"]
        fit = fits.get(nid) or {}
        if pseudo(ticker_of(nid)) or not dollars or "session" not in fit:  # cash, and what has no price, move nothing
            continue
        session = np.array(fit["session"], dtype=float).reshape(2, k)
        item = m["series"].get(nid)
        spans = history.returns(item, load, mdl.sessions)["dollar"] if item else []
        holdings[hid], names[hid] = (dollars, session, estimate.residuals(x, ok, session, spans)), nid
    days = [mdl.sessions[t + 1] for t in np.flatnonzero(ok)]
    if len(days) < 2:  # a variance needs two sessions: none
        return {"sessions": len(days)}
    settings = mdl.settings
    got = estimate.figure(x, ok, mdl.omega, holdings, memory=settings.memory, lean=settings.own_lean,
                          kappa=settings.kappa)
    parts = [{"from": mdl.sessions[p["start"] + 1] if p["start"] < rows else None,
              **{key: p[key] for key in ("holdings", "sessions", "beta", "with", "own")}} for p in got["parts"]]
    part_of = {h: i for i, p in enumerate(parts) for h in p["holdings"]}
    by_holding = {}
    for hid, (dollars, _, e) in holdings.items():
        mine = np.flatnonzero(~np.isnan(e))
        by_holding[hid] = {"name": names[hid], "dollars": dollars, "own_sessions": int(mine.size),
                           "from": mdl.sessions[mine[0] + 1] if mine.size else None, "part": part_of[hid]}
    sd = got["daily_sd"]
    ahead = series.sessions_after(m["asof"], m["horizon_end"])
    return {"volatility": sd * float(np.sqrt(ahead)), "daily_sd": sd, "sessions_ahead": ahead,
            "horizon_end": m["horizon_end"], "window": [days[0], days[-1]], "sessions": len(days),
            "variance": got["variance"], "parts": parts, "by_holding": by_holding}


VOLATILITY = Derivation("volatility", 7, "Portfolio", _volatility_inputs, _volatility_compute, fields=("volatility",),
                        subject_of=lambda store, entity: "portfolio", stage="exposures")


# ---------------------------------------------------------------- the proxy Metric


def proxy_fields(ticker: str, quote: str | None = None) -> dict:
    """What a price Metric is: its id, its name, "{TICKER}'s price", its unit, and its publisher where the price is
    the publisher's own: a crypto pair's is Coinbase Exchange's own trade. A listed ticker's price is the listing's own,
    which Massive relays, and a currency's rate is the market's, which Yahoo Finance relays, so neither has one. A US
    listing's unit is the dollar; a pair's is its quote currency and a listing abroad's the currency it is quoted in,
    as its source gives it (`quote`), and none where it is not given."""
    unit = (quote or None) if series.is_pair(ticker) or series.is_abroad(ticker) else "USD"
    out = {"id": f"price:{ticker}", "name": f"{ticker}'s price", "unit": unit, "kind": "price"}
    if unit is None:
        out.pop("unit")
    if series.is_crypto(ticker):
        out["resolution_source"] = "Coinbase Exchange"
    return out


def quote_of(found: dict | None) -> str | None:
    """A pair's quote currency, or a listing abroad's currency, as its source gives it with its prices or its latest
    mark."""
    if not isinstance(found, dict):
        return None
    return str(found.get("quote") or found.get("currency") or "") or None


def price_fields(ctx: Inputs, ticker: str) -> dict:
    """What a price Metric is, its unit the currency its kept prices are quoted in (a pair's quote currency, a listing
    abroad's currency), which does not move with its price: as a check reads it, nothing that moves with the price."""
    quote = None
    if series.is_pair(ticker) or series.is_abroad(ticker):
        try:
            asof = asof_of(ctx)
        except Unavailable:
            asof = None
        if asof:
            kind = "hourly" if series.is_pair(ticker) else "home"
            ref = ctx.data(kind, ticker, covering=(series.lookback(asof), asof), required=False)
            quote = quote_of(ctx.payload(ref)) if ref.get("data") else None
    return proxy_fields(ticker, quote)


def _quote(m: dict, load) -> str | None:
    for ref in ((m.get("series") or {}).get("ref"), m.get("last_trade"), m.get("home_mark")):
        found = load(ref) if isinstance(ref, dict) and ref.get("data") else None
        if quote_of(found):
            return quote_of(found)
    return None


def _proxy_inputs(ctx: Inputs, mid: str) -> dict:
    """The proxy's current price."""
    ticker, asof = mid.split(":", 2)[2], asof_of(ctx)
    return {"metric": mid, **current_inputs(ctx, ticker, asof)}


def _proxy_compute(m: dict, load) -> dict:
    found = current_compute(m, load) or {}
    quote = _quote(m, load) if series.is_pair(m["ticker"]) or series.is_abroad(m["ticker"]) else None
    return {**proxy_fields(m["ticker"], quote),
            "level": found.get("price"), "asof": found.get("session"), "time": found.get("time"),
            "from": found.get("from"), "previous_close": found.get("previous_close"),
            "previous_session": found.get("previous_session"), "session_change": found.get("session_change"),
            "session_move": found.get("session_move")}


PROXY_PRICE = Derivation(
    "proxy_price", 11, "Metric", _proxy_inputs, _proxy_compute,
    fields=("id", "name", "unit", "level", "asof", "kind", "resolution_source"),
    applies=lambda store, mid: mid.startswith("metric:price:"),
)

# ---------------------------------------------------------------- any window: how the channels moved


def window_subject(start: str, end: str) -> str:
    return f"window:{start}:{end}"


def _ref(value: dict) -> dict:
    return {"derivation": value["derivation"], "subject": value["subject"], "basis": value["basis"],
            "sha256": digest(value["output"])}


def window_record(start: str, end: str, asof: str) -> tuple[str, str]:
    """The span a window's distributions are read over: its income accrues over the stretches that run from
    the distribution before its start to the one after its end, and a stretch since the one before runs up to
    series.MISSED of a schedule's periods, a year being the longest. So a window within the history the factors are
    estimated on reads that history's distributions, to the as-of, as its months do, which every store that has its
    factors holds; and an earlier window reads that far after its end, to the as-of at the latest, and twice that far
    before its start, since a distribution followed by missed payments goes on for one more period of its own stretch,
    which can reach the window from one stretch before it. Its reading is then fixed by the window and the as-of,
    whatever else the store holds."""
    history_begins = series.lookback(asof)
    if start >= history_begins:
        return history_begins, asof
    reach = timedelta(days=math.ceil(series.MISSED * 365))
    return ((date.fromisoformat(start) - 2 * reach).isoformat(),
            min(asof, (date.fromisoformat(end) + reach).isoformat()))


def _window_inputs(ctx: Inputs, subject: str) -> dict:
    """Each channel's proxy series over the window, with its distributions from where the window's record begins
    (window_record), and the orthogonalization it goes through. Every proxy is read at once (moves_inputs_many), so a
    window the store holds none of is one round of reads."""
    _, start, end = subject.split(":")
    asof = asof_of(ctx)
    if start > asof:
        raise Unavailable(f"the window begins on {start}, after the as-of, {asof}")
    # each proxy's prices read over the window itself, to the as-of at the latest, and its distributions over the
    # window's record: a window within the history the factors are estimated on is read from that history's own
    # reading, and one before it reads nothing the history reads where its record ends before the as-of
    record = window_record(start, end, asof)
    found = [(c, ctx.value("orthogonalize", c["id"])) for c in ordered_channels(ctx)]
    items = history.moves_inputs_many(ctx, [orth["output"]["proxy"] for _, orth in found], (start, min(end, asof)),
                                      record=record)
    channels = [{"channel": c["id"], "name": c["stated"]["name"], "orthogonalization": _ref(orth),
                 "series": items[orth["output"]["proxy"]]} for c, orth in found]
    if not channels:
        raise Unavailable("the store has no factors")
    return {"window": [start, end], "record": list(record), "channels": channels}


def _window_compute(m: dict, load) -> dict:
    """Each proxy's move from the close of start to the close of end, what holding it earned less the income it
    accrued (series.window_move), and each factor's own part of it (MATH.md 1.3, 3.2): the compounded move, ln(1 + r), less
    its coefficients on the earlier factors' own parts, given as a move, e^z - 1, so that the window stated back by its
    own parts reproduces its moves. A proxy with no close at an end of the window has none, and an own part of 0."""
    start, end = m["window"]
    found_closes, moves, orthogonalized = {}, {}, {}
    listed = next((c["series"] for c in m["channels"] if c["series"]["kind"] == "listing"), None)
    sessions = sorted(closes(listed, load)) if listed else []  # New York's sessions, the market's own (calendar_inputs)
    for c in m["channels"]:
        cid, item = c["channel"], c["series"]
        moving = history.moves(item, load, sessions, since=m["record"][0], until=m["record"][1])
        found = moving.closes
        found_closes[item["ticker"]] = {d: v for d, v in sorted(found.items()) if start <= d <= end}
        r = moving(start, end)
        moves[cid] = r
        if r is None:
            orthogonalized[cid] = 0.0
            continue
        coefficients = load(c["orthogonalization"])["coefficients"]
        orthogonalized[cid] = math.log1p(r) - sum(beta * orthogonalized[earlier] for earlier, beta in coefficients.items())
    if all(r is None for r in moves.values()):  # no proxy has a close at both ends of the window
        def lacks(t: str) -> str:
            held = found_closes[t]
            missing = [d for d in (start, end) if d not in held]
            return f"{t} has no close from {start} to {end}" if not held else \
                f"{t} has no close on {' or '.join(dict.fromkeys(missing))}"
        raise Unavailable(f"no factor's proxy has a close at both ends of the window: "
                          f"{'; '.join(lacks(t) for t in found_closes)}")
    return {"window": [start, end], "closes": found_closes, "moves": moves,
            "own_part": {c: math.expm1(z) for c, z in orthogonalized.items()},
            "proxies": {c["channel"]: c["series"]["ticker"] for c in m["channels"]}}


# window_returns(start, end): how the channels moved over a past episode. It gives no object a field: the tool
# reads its Computation by subject, window:{start}:{end}.
WINDOW_RETURNS = Derivation("window_returns", 16, "Factor", _window_inputs, _window_compute,
                            applies=lambda store, entity: False)

DERIVATIONS = (FACTORS, ORTHOGONALIZE, BETAS, G, VOLATILITY, PROXY_PRICE, WINDOW_RETURNS)
