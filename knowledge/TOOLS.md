# Tools

Sixteen tools. Five write structure, four bring the world into the store, and seven read. Every
argument is JSON, and every result is JSON.

The examples use placeholders: `E` is the event, the proposition the portfolio watches; `A`, `B`,
`C`, and `D` are propositions of its breakdown; `X` is a level and `x_level` its metric; `XYZ` is a
ticker; `src:N` is a page you fetched; `prop:N`, `link:N`, and `channel:N` are ids the store gave;
`YYYY-MM-DD` is a date.

## Errors

A call the store refuses returns an error and writes nothing:

```
{"error": "<the reason>", "dependents": ["<id or id.field>", ...]}
```

The store refuses a call that breaks one of its rules:

- a required field, or the source a fact cites, is missing;
- a passage is not in its page word for word;
- a composite would have a second `then` link of one step;
- a watched proposition's window would end past the horizon;
- a consequence's `meaning` is not `gap`, `level_if_happens`, or `level_if_not`, or it states a
  level of a metric no market prices;
- a stated probability or conditional probability is outside `[0, 1]`;
- a derivation's propositions and probabilities match no rule of the point (`MATH.md`), or its reads
  would run in a cycle; the reason names the rule it needs;
- a proposition would state both a `derivation` and a `chance`;
- a `consequence` link would set a channel or a name through a second metric; the error names the
  metric that already sets it;
- a removal `remove` refuses (`ONTOLOGY.md`, removing).

The reason says what to change, and `dependents` names what stands in the way. A value the store
cannot derive yet is a derived field with no value and its reason, which says what it waits on. A
stated assumption is accepted before its basis, because its note may follow it; `check()` lists it
until it has one.

## Facts and assumptions

A fact about the world names the page it comes from:

```
{"value": <the value>, "source_id": "src:N"}
```

An assumption, a proposition's `chance`, each conditional probability of its `derivation`, or a
consequence's figure, carries its basis in one of two ways:

- a page you cite, with the passages that state what it rests on, each word for word from the page:
  `{"value": 0.6, "source_id": "src:N", "passages": ["<the page's own words>"]}`;
- the number alone, `{"value": 0.6}` or `0.6`, and a note about it with your reasoning:
  `note("<why>", ["prop:N"])`, or about the link for a consequence's figure.

## Write structure

### `add(type, fields)`

Creates an object, and the store derives what follows from it (`ONTOLOGY.md`, derivations).

- `type`: `Portfolio`, `Proposition`, `Metric`, or `Channel`. Names, markets, sources, notes,
  judgments, and computations come in by other ways (`ONTOLOGY.md`, objects).
- `fields`: the stated fields, by `ONTOLOGY.md`'s object table, with facts and assumptions as
  above. A Portfolio also takes `holdings`, a list of `{"ticker", and one of "quantity", "dollars",
  "weight"}`.

It returns what the next step needs. A Proposition or a Metric returns the object as `get` returns
it.

A Portfolio returns what it owns:

```
{"id": "portfolio", "type": "Portfolio", "stated", "asof", "V", "session_move", "session_move_dollars",
 "holdings": [{"ticker", "given": {<quantity, dollars, or weight, as given>}, "dollars", "quantity",
               "weight", "price", "price_from", "price_at", "session", "previous_close",
               "session_move", "session_move_dollars",
               "company"?: {"name", "does", "industry"},
               "fund"?: {"name", "looked_through", "reason"?, "holdings_asof", "holdings",
                         "largest": [{"name", "ticker", "listed", "asset_class", "security_type",
                                      "industry", "weight", "dollars", "priced_as"}],
                         "by_listing": [{"listed", "weight", "dollars"}],
                         "by_kind": [{"asset_class", "security_type", "weight", "dollars"}]}}],
 "names": [{"name", "company", "e", "direct", "through": {<fund>: <dollars>}}],
 "news": [{"published", "publisher", "title", "summary", "keywords", "url", "about": [<ticker>]}],
 "news_unread"?: {<ticker>: <why>}}
```

- `holdings`: as the person gave them, each with its value at the current price, its weight, and
  its price's move over its `session` (`ONTOLOGY.md`, prices), as a fraction and in dollars.
  `price_from` is `last trade`, `latest minute bar`, or `latest hourly bar` for a latest mark, or
  `close`; `price_at` is its time or date.
- `company`, for a holding that is a company: its name, what it does in its reference's own words,
  and its industry.
- `fund`, for a holding that is a fund: its name; whether the look-through prices its holdings on
  their own (`looked_through`), and the `reason` where it does not; the date of its holdings and how
  many it has; its ten `largest` holdings, each with its name, its ticker as the fund gives it,
  where it is `listed` (the country of its exchange), its asset class and security type as the fund
  gives them, its industry where the store knows it, its `weight` of the fund, its `dollars` of the
  portfolio's, and `priced_as`, the name the store prices it as, where it does; and its dollars
  divided `by_listing` and `by_kind`, every holding counted, the ten largest of each and then
  `{"others", "weight", "dollars"}`. A holding leaves out the fields its fund's data does not give;
  `listed` is null for the holdings that name no exchange.
- `session_move` and `session_move_dollars`: the portfolio's move, the holdings' moves summed.
- `names`: the 25 largest names after look-through, each with its company's name and what is held
  directly and through which funds. A fund the look-through does not price on its own is one name.
- `news`: the latest articles on the ten largest companies among those names, five each, published
  in the 180 days to the as-of, newest first, each once with the names it is `about`; an article's
  `summary` and `keywords` are the news service's own. `news_unread` gives each name whose news
  could not be read, and why.

Where the portfolio cannot be priced, `holdings` and `names` are null, with the `reason`.

A Channel returns how it is built and what it means for this portfolio:

```
{"id": "channel:N", "type": "Channel", "stated", "proxy_metric",
 "orthogonalization": {"window", "n", "intercept", "coefficients": {<earlier channel>: <coefficient>},
                       "largest_month", "largest_month_sessions": {"start", "end", "move"}},
 "g", "beta", "M_c", "g_times_M_c"}
```

- `orthogonalization`: its coefficients on each earlier channel, fitted over `window` from `n`
  returns; what they leave is the channel's own part (`ONTOLOGY.md`, channels and betas). Its
  largest month is `largest_month`, `M_c`, run between the closes of `largest_month_sessions`, with
  its signed `move`.
- `g` and `beta`: the portfolio's dollar beta to it, and its beta, `g` over `V`. `g_times_M_c` is
  the largest month applied to the dollar beta. These are what `exposure()` gives for each channel.
- Each name's beta to the channel is one `get` away: among the name's `b`, `get("name:{TICKER}")`,
  or on its link, `get("loads_on:name:{TICKER}>channel:N")`. The return leaves them out because a
  channel reaches every name the portfolio holds.

```
add("Portfolio", {"asof": "YYYY-MM-DD", "total": 100000,
                  "holdings": [{"ticker": "XYZ", "weight": 0.6}, {"ticker": "CASH", "weight": 0.4}]})
add("Channel", {"name": "<what its proxy's price measures, in plain words>", "proxy_ticker": "XYZ",
                "order": <a number placing it, broad to narrow, among the channels>})
add("Metric", {"id": "x_level", "name": "<the level X, in plain words>", "unit": "<its unit>",
               "traded": false, "resolution_source": "<who publishes X>",
               "current_value": {"value": 70.0, "source_id": "src:N"},
               "asof": {"value": "YYYY-MM-DD", "source_id": "src:N"}})
```

A proposition is the event or a part of its breakdown; its links say which. Its `title` names it;
its `text` defines it, what counts and what does not, and is what the second model reads against a
market's terms.

The event, `E`, once `watches` links it:

```
add("Proposition", {"title": "<what happens, in plain words>",
                    "text": "<the definition: what counts, by when, and how it would be known>",
                    "relation": "occurs", "window_end": "YYYY-MM-DD", "kind": "hitting",
                    "resolution_source": "<the source that would show it>"})
```

A part, `B`, here a threshold a market asks, linked `quoted_by` that market and to `E` by
`implies`, `and`, `or`, or `then`:

```
add("Proposition", {"title": "<X at 80 or more on its date>", "text": "<the market's terms, in words>",
                    "metric": "metric:x_level", "relation": ">=", "value": 80,
                    "window_end": "YYYY-MM-DD", "kind": "level_at_date"})
```

A part no market asks, `C`, with the probability you state:

```
add("Proposition", {"title": "<the claim, in plain words>", "text": "<its definition>",
                    "relation": "occurs", "window_end": "YYYY-MM-DD", "kind": "hitting",
                    "chance": {"value": 0.05, "source_id": "src:N", "passages": ["<the page's own words>"]}})
```

### A derivation

A proposition's `derivation` names the propositions its point reads and states the conditional
probabilities its rule needs:

```
{"reads": ["prop:B"],
 "chances": [{"of": "prop:E", "given": ["prop:B"], "given_not": [], "value": 0.6,
              "source_id": "src:N", "passages": ["<the page's own words>"]}]}
```

- `reads`: one or two propositions, each read at its own point.
- `chances`: one or two conditional probabilities. Each is the probability `of` a proposition
  (omitted, the proposition itself) `given` some propositions true and `given_not` others false,
  with its `value` and its basis.

The store finds the rule from what the derivation reads, what each probability is of and given, and
the links between the propositions, and refuses one that fits no rule. For a proposition E:

| E reads | `chances` |
|---|---|
| B, which E implies | `[{"given": [B], "value": P(E given B)}]` |
| A, which implies E | `[{"given_not": [A], "value": P(E given not A)}]` |
| A and B, A implying E and E implying B | `[{"given": [B], "given_not": [A], "value": P(E given B and not A)}]` |
| D, which neither implies E nor is implied by it | `[{"given": [D], "value": P(E given D)}, {"given_not": [D], "value": P(E given not D)}]` |
| B, where E = B `and` C, or B `then` C | `[{"of": C, "given": [B], "value": P(C given B)}]` |
| B and C, where E = B `or` C | `[{"of": C, "given": [B], "value": P(C given B)}]` |
| B, where E = B `or` C | `[{"of": C, "given_not": [B], "value": P(C given not B)}]` |

The links come first: write the breakdown, then state the derivation with `edit`. A derivation
whose links are later removed gives no point, with the reason, until it is restated.

```
edit("prop:E", "derivation", {"reads": ["prop:B"], "chances": [{"given": ["prop:B"], "value": 0.6}]})
edit("prop:E", "derivation", {"reads": ["prop:D"], "chances": [{"given": ["prop:D"], "value": 0.8},
                                                               {"given_not": ["prop:D"], "value": 0.1}]})
```

### `link(from_id, relation, to_id, fields)`

Creates a link by `ONTOLOGY.md`'s link table. The derivations follow.

- `fields` (optional) holds the link's stated fields: `transform` for `quoted_by` (default
  `identity`), `conversion` for `tracks`, `step` for `then`, `passages` for `evidence`, `value` and
  `meaning` for `consequence`, and one of `quantity`, `dollars`, or `weight` for `holds`.
- A `consequence` link's `sets` is a list: for a channel, `{"channel", "raw", "implied",
  "component"}`, its raw shock, the part the earlier channels imply, and its residual shock; for a
  name, `{"name", "raw", "betas", "d"}`, its whole move, the part its betas give, and its residual
  move. An event the portfolio does not watch has
  no impact, and `sets` has that reason in place of a value.

Returns the link with its derived fields:

```
{"id": "link:N", "type": "link", "relation", "from", "to", "origin", "stated",
 "derived": {<field>: {"value", "basis"} | {"value": null, "reason"}}}
```

A consequence on a watched event also returns what the event now does:

```
"event": {"id", "basis", "factors": [{"channel", "name", "move", "set"}],
          "holdings": [{"ticker", "D", "share"}]}
```

- `factors`: every factor in channel order, its `move` the raw move of its proxy in the gap, and
  `set` true where one of the event's consequences sets it. A factor no consequence sets moves with
  the factors before it, by its coefficients on them (`MATH.md`, the shock vector), so leaving it
  alone gives it this move.
- `holdings`: each holding's full impact `D`, largest first, and its `share`, `D` over the
  holding's dollars.
- On an event the portfolio does not watch, or one with no impact yet, `event` is
  `{"id", "reason"}`.

```
link("portfolio", "watches", "prop:E")
link("prop:B", "quoted_by", "market:{venue}:{id}")
link("prop:B", "quoted_by", "market:{venue}:{id}", {"transform": "complement"})
link("prop:E", "implies", "prop:B")
link("prop:E", "then", "prop:B", {"step": 1})
link("prop:E", "evidence", "src:N", {"passages": ["<a sentence of the page, word for word>"]})
link("prop:E", "consequence", "metric:price:XYZ",
     {"value": {"value": 95.0, "source_id": "src:N", "passages": ["<the page's own words>"]},
      "meaning": "level_if_happens"})
link("prop:E", "consequence", "metric:x_level", {"value": 3.5, "meaning": "gap"})
link("metric:x_level", "tracks", "channel:N", {"conversion": "price"})
link("metric:x_level", "tracks", "channel:N", {"conversion": "yield(7)"})
link("portfolio", "holds", "name:XYZ", {"dollars": 10000})
```

### `note(text, about_ids)`

Creates a `Note`, your reason in words, linked by `about` to each id in `about_ids` (optional).
`link("note:N", "about", id)` adds a target. Returns the Note.

### `edit(id, field, value, source_id)`

Changes one stated field of an object or a link. `source_id` is required when the field is a fact
about the world, and refused otherwise; an assumption's basis rides in its value. A `null` value
clears a field that is not required. A fetched page's one stated field is `dated`.

Returns the object or link as `get` returns it, with three exceptions. A channel returns as `add`
returns it: an edit to its proxy or its order rebuilds it and every beta, so what comes back is how
it now stands against the earlier channels, its largest month, and the portfolio's dollar beta and
beta to it. A page returns with its `length` in place of its text. A consequence returns as `link`
returns it, with what its event now does.

```
edit("prop:E", "chance", {"value": 0.04, "source_id": "src:N", "passages": ["<the page's own words>"]})
edit("channel:N", "proxy_ticker", "XYZ")
edit("link:N", "value", 2.5)
edit("metric:x_level", "current_value", 72.0, "src:N")
edit("src:N", "dated", "YYYY-MM-DD", "src:N")
```

### `remove(id)`

Removes an object or link you created, or that came with the initial contents, with the values
derived from it. It refuses what `ONTOLOGY.md` lists under removing, naming the dependents.
Removing a kept page removes its `evidence` links, and the page is held again.

Returns what went:

```
{"removed": {...}, "cascaded": [<links removed with it>], "released": [<markets and pages nothing uses now>]}
```

A market released is no longer in the store, and naming it again brings it back. A page released is
held again, `"kept": false`, citable under the same id.

## Bring the world in

### `search_web(text, params, proposition_id)`

One web search for `text`. `params` (optional) are the search tool's own parameters (`DATA.md`).
`proposition_id` (optional) records the search on that proposition.

Returns the results as data:

```
{"search": "search:N", "query", "params", "queries": [<the queries run>],
 "results": [{"url", "title", "page_age", "query", "passages": [<quoted text>]}], "errors": [...]}
```

### `fetch_source(url)`

Fetches the page's full text into the store; a PDF comes as its text. Returns:

```
{"id": "src:N", "url", "title", "dated", "retrieved_at", "kept", "length", "excerpt"}
```

`excerpt` is the first 1,500 characters and `length` the whole; `get(id, text)` finds words in
it, and `get(id)` returns it whole. A URL the store holds is not fetched again.

### `search_company(ticker, kind, params)`

Reads one kind of a company's own record, the company named by `ticker`. `params` (optional) are
the kind's own parameters (`DATA.md`), passed as given. What comes back runs from the company's
listing date to the as-of, since a ticker can have been another company's.

- `kind`: `news`, `current_reports`, `risk_factors`, `annual_report`, `earnings`, `guidance`, or
  `ratings`.

Returns, newest first:

```
{"search": "search:N", "ticker", "kind", "params", "returned", "rows": [<row>], "more",
 "reason"?, "error"?}
```

- `rows`, by kind:
  - `news`: `{"published", "author", "title", "teaser", "channels", "tags", "tickers", "url",
    "page"?}`;
  - `current_reports`: `{"filing_date", "form_type", "accession_number", "url", "page"}`;
  - `risk_factors`: `{"filing_date", "category", "url", "page"}`;
  - `annual_report`: `{"filing_date", "period_end", "section", "url", "page"}`;
  - `earnings` and `guidance`: each record's own fields;
  - `ratings`: one row, the consensus.
- `page` is the row's document, held as a page under its `url`: `{"id": "src:N", "length",
  "excerpt"}`, the excerpt its first 500 characters; an article's gives only its id and length, its
  title and teaser saying what it holds. `get(id, text)` finds its passages, and it is kept as a
  `Source` once you cite it.
- `more` says the kind holds more than came back: dates or a `limit` reach the rest.
- `reason` says why a kind holds nothing, and `error` is a refusal, each in plain words.

### `search_markets(venue, endpoint, params, proposition_id)`

Passes `params`, the venue's own parameters, unchanged to one of its search or listing endpoints.

- `venue`: `polymarket`, `kalshi`, or `hyperliquid`.
- `endpoint`: one of the venue's endpoints, as `DATA.md` lists them.
- `params` (optional): text, filters, and paging, as `DATA.md` gives their correct use.
- `proposition_id` (optional): the search is recorded on that proposition.

Returns what the venue returned, in its order, its markets grouped under their events:

```
{"search": "search:N", "venue", "endpoint", "params", "returned": <the number of markets>,
 "events": [{"event", "title", "tags",
             "markets": [{"id": "market:{venue}:{id}", "question", "close_time", "resolution_source",
                          "tags"?, "categories"?, "series"?, "open", "price", "price_rule", "bid", "ask",
                          "depth", "reason"?, "kept"}]}],
 "paging": {<the venue's next-page fields>}, "error"?}
```

- An event is a Polymarket or Kalshi event, or a Hyperliquid question; a market with no event
  stands alone, with `event` null. An event's tags are given once, on the event.
- `price`, `price_rule`, `bid`, `ask`, `depth`, and `open` are as `ONTOLOGY.md` defines them. A
  market with no book has no price, and its `reason`. In a store whose as-of is past, each price is
  the history price at the as-of, with its `price_date`, and `depth` is null.
- A Kalshi `/series` search returns series, not markets: `"series": [{"ticker", "title",
  "category", "tags", "frequency"}]`.
- `error` is the venue's own words when it refuses the parameters.

A market enters the store when a link or a note names it; until then `get` reads it from its venue.

## Read

### `exposure()`

The portfolio's dollar beta to each channel the store models, a factor on the page: the basis on
which the store sizes what an event does. It runs no searches.

```
{"asof", "V",
 "channels": [{"channel", "name", "proxy", "g", "beta", "M_c", "g_times_M_c"}],
 "proxies": [{"ticker", "channel", "price", "price_from", "price_at", "session", "previous_close",
              "session_move"}]}
```

- `channels`: each channel's dollar beta `g`, and `beta`, the portfolio's beta to it, `g` over
  `V`; its largest one-month move `M_c`; and `g_times_M_c`, that move applied to the dollar beta
  (`ONTOLOGY.md`, channels and betas), largest `g_times_M_c` first.
- `proxies`: each channel's proxy, with its price and its move over its `session`.

### `window_returns(start, end)`

How the channels moved over any past window: each proxy's return from the close of `start` to the
close of `end`, and the same put through the orthogonalization in channel order.

```
{"window": [start, end], "basis": "cmp:N",
 "channels": [{"channel", "name", "proxy", "raw", "orthogonalized"}],
 "closes": {<ticker>: {<date>: <close>}}}
```

`raw` is the proxy's total return, its dividends added back, null where it has no close at an end;
`orthogonalized` is channel `k`'s residual return, its return less its coefficients on the earlier
channels times their residual returns, 0 where `raw` is null.

### `get(id, text)`

Reads any id in the store. `text` (optional) finds words in a page.

An object:

```
{"id", "type", "origin", "stated", "citations": {<field>: "src:N"},
 "derived": {<field>: {"value", "basis"} | {"value": null, "reason"}},
 "links": [{"id", "relation", "direction": "out"|"in", "other", "stated", "derived"?}],
 "searches": [...]}
```

- `citations` names the page each fact or assumption cites: `current_value`, `chance`,
  `derivation.chances[0]`, a consequence link's `value`.
- `searches`, on a Proposition, are the searches recorded on it (`recorded` true) and those that
  returned a market it is `quoted_by` or a page it links as `evidence`, naming them in `quoted_by`
  and `evidence`.
- A Name has, among its derived fields, `company`: its name, what it does in its reference's own
  words, and its industry. A fund whose holdings the store reads has `holds`: what it holds, every
  holding counted, with `holdings_asof`, `count`, `weights_sum`, and `dollars`, the portfolio's in
  it; `named`, each holding down to the look-through floor (`MATH.md`, the constants), as `add`
  gives a fund's `largest`; `rest`, the others' `count`, `weight`, and `dollars`, and their
  `by_listing` and `by_kind`; and `by_listing` and `by_kind`, the whole fund.
- A link has `relation`, `from`, `to`, `stated`, and `derived`.
- A market has `data`, what was read for it, and its derived fields. A market the store does not
  hold is read from its venue and not kept: `{"id", "type": "Market", "kept": false, "stated",
  "question", "rules", "resolution_source", "close_time", "tags", "categories", "series", "open",
  "price", "price_rule", "bid", "ask", "depth"}`.
- A Computation has `derivation`, `inputs`, and `output`; a Judgment has `kind`, `statement`,
  `inputs` (the evidence it was asked over), `answer`, `model`, and `raw`. Follow a value's `basis`
  with `get` to see what produced it.

A page, `src:N`, kept or only fetched, comes back whole: its `text`, `dated`, `kept`, and
`cited_for`. Every result stays in your context for the rest of the conversation, so a long page
read whole crowds what follows. Give `text`, words you expect the page to use (a name, a figure, a
defined term), and `get` returns the passages that contain them:

```
{"id": "src:N", "type": "Source", "kept", "url", "dated", "text", "length", "found",
 "passages": [{"at", "under", "passage"}], "front_matter"?}
```

- Each `passage` is the page's own words as the passage check reads them: single-spaced, a link as
  its text. Any part of it quotes word for word, as a basis or as evidence.
- The search reads the page's body. The front matter a fetched page opens with, its URL, its title,
  and the site's descriptions of it, repeats the title and can be cut off mid-word, so it is not
  searched; `front_matter` counts the places the text is there, where there are any, and
  `get(src:N)` returns them with the rest of the page.
- A passage is the paragraph the text is in, or, in a longer paragraph, up to 1,000 characters
  around it, cut at words; finds close together share one passage.
- The search ignores case, spacing and line breaks, the markup of links, images, and emphasis, and
  whether quotes and dashes are curly or plain; it finds the words in the order given. A shorter
  text finds more places, a longer one fewer, and a text the page does not use finds none.
- `found` counts every place the text is in the body; the first ten passages, in page order, come
  back. `at` is where a passage starts among the body's `length` characters of words, and `under`
  the heading above it, where the page has headings.

A page is evidence for what it says, wherever it says it: a long page searched is as usable as a
short one read whole.

```
get("prop:N")
get("src:N", "<words the page would use>")
get("cmp:N")
```

### `query(type, where)`

The objects of a type whose fields match `where` (optional): `{field: value}` for equality, or
`{field: {op: value}}` with `op` one of `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, `contains`. A
filter on a derived field derives it. Returns `[{"id", "type", "stated", "derived"?}]`.

```
query("Proposition", {"title": {"contains": "X"}})
query("Metric", {"traded": true})
```

### `neighbors(id, relation, direction)`

The objects linked to `id`. `relation` (optional) is one relation; `direction` (optional) is `out`,
`in`, or `both`, the default. Returns `[{"link", "relation", "direction", "object": {"id",
"type"}}]`.

### `ledger()`

Every watched proposition, ranked by `emove`, with the `worst` order beside them: the rows' ids from
the most negative `worst` to the least. `horizon_end` is the end of the period the ledger looks
over.

```
{"asof", "V", "horizon_end", "rows": [<row>], "worst": [id]}
```

A row has the proposition's `id` and stated fields, and:

- `p`, `lo`, `hi`, `coverage`; `point`: its `rule` (`market`, `derivation`, or `chance`), and for a
  market the `market` and `link`, for a derivation its `row`, `formula`, `roles`, the points it
  `reads`, and its `chances`, for a stated probability the `chance`;
- `pnl_yes`, `pnl_no`, `D`, `emove`, `worst`, `u_E`; `share_yes` and `share_no`, `pnl_yes` and
  `pnl_no` as fractions of `V`;
- `primary_channel`, and `contributions`, each channel's dollars of `D`;
- `by_name`: the three largest contributors, each name with its contribution `D` and its `parts`,
  `direct`, `through` (by fund), and `own` (`MATH.md`, impact), its `if_yes` and `if_no`, its
  `company`, `held_directly`, and the funds it is held `through`; `names` counts every name, and
  every contribution is in `basis.by_name`;
- `by_holding`: each holding's contribution `D`, `if_yes`, and `if_no`, with the five `names` that
  contribute most through it, each with its contribution `D` and the `move` of those dollars, and
  `{"rest", "D"}`, the count of the others and their summed contribution; every name is in
  `basis.by_holding`;
- `consequences`, each with its `link`, `metric`, `figure` (with its basis), `meaning`, `gap` (the
  shock), `c`,
  `traded`, `L_yes` and `L_no` (null for a metric no market prices), and `sets`, with `reasons`
  where a field has no value;
- `d`, the residual move of each name a consequence sets;
- `markets`: every market in the breakdown with its `question`, `close_time`, `price`, `bid`,
  `ask`, `depth`, `open`, its `transform`, the judge's `forward` and `converse`, and its `bound`;
- `decomposition`, the propositions of the event's breakdown; `searches`, their counts and queries;
  `notes`; `past`, whether a market's price is a history price;
- `reasons`, each field with no value and why; `basis`, each field's Computation.

### `check()`

What your work is missing or contradicts, for you to act on:

```
{"watched_without_point": [{"id", "title", "reason"}],
 "watched_without_consequence": [{"id", "title"}],
 "assumptions_without_basis": [{"id", "field", "value", ...}],
 "disputed": [{"link", "check", "answer", ...}],
 "crossed": [{"id", "lo", "hi", "lo_from", "hi_from"}],
 "outside_interval": [{"id", "p", "lo", "hi", "rule"}]}
```

`watched_without_point` lists watched propositions that no market asks and that state no
`derivation` or `chance`, or whose derivation gives none, with the reason;
`watched_without_consequence`, watched propositions with no consequence. The other lists are what
`MATH.md` and `ONTOLOGY.md` say `check()` lists: assumptions without a basis, disputes you have not
answered, crossed bounds, and points outside their interval.
