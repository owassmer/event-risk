# Ontology

The store is the one place everything lives: the portfolio, the channels and metrics, the
propositions you write, the markets you link, the pages you cite, the assumptions you state, and
every number derived from them. You write structure and state assumptions. The store observes and
computes every other number on read, and every number opens to what produced it: an observation,
a computation, an assumption you stated with its basis, or a check.

## The store

**One store, one portfolio.** The store has one `Portfolio`. Its `asof` is fixed when it is added:
every price, book, beta, and window is as of that date, and a different as-of is a different store.
An as-of of today prices every name and proxy at its latest mark and reads each market from its
book; an as-of before today prices at that day's close and reads each market's price from its
history.

**What a new store holds.** One channel, the market, with its proxy's price as a metric, and, once
the portfolio is added, each of its names with its price. The other channels and metrics are yours
to add as the events you build need them, and pages enter as you fetch them. `exposure()` shows the
channels.

**Stated and derived.** A stated field is written by you (`add`, `link`, `edit`) or by a fetch. A
derived field is computed on read from the stated fields, the links, and the data the store has
fetched. Each derived value carries its basis, the `Computation` (what it read, what it produced)
or the `Judgment` (the check, the evidence, the answer) behind it, or, where it has no value, the
reason, which says which input is missing. A value is recomputed exactly when one of its inputs
changes.

**Facts cite pages.** A stated fact about the world, a metric's `current_value` and `asof`, cites a
`Source`. A `Source` is a page you fetched with `fetch_source`, or a document of a company's record
that `search_company` read. Only a fetch creates one, so
evidence is always a page the world returned.

**Assumptions carry their basis.** Your assumptions are a proposition's `derivation`, with the
conditional probabilities it states; its `chance`, the probability you state for a proposition when
no market asks it and no derivation gives its point; and a consequence's figure, its shock or a
level. Each carries its basis: a page you cite with the passages that state what it rests on, each
quoted word for word, or a note about it with your reasoning. `check()` lists an assumption with
neither.

**What is kept.** The store keeps what you use, while you use it.

- A fetched page is held. It is kept as a `Source` once a fact or an assumption cites it or an
  `evidence` link quotes it. When its last use is removed it is held again under the same id, and
  you can cite it again.
- A market enters the store when a link or a note names it, and is kept while one does. Its book and
  history are read on its first use.
- A search is recorded with what it returned, and on the proposition you give it. A proposition's
  `searches` are the ones recorded on it and the ones that returned a market it is `quoted_by` or a
  page it links as `evidence`. When the link goes, so does the search it brought.

**Checkpoints.** The store checkpoints what you have written at each message you are sent: your
objects and links with their stated fields, the citations, the notes, the pages and markets kept,
and the record of your searches. When the person edits a message or retries a reply, the store
returns to its checkpoint before that message, and the conversation with it. What was fetched and
what the judge answered stay, so a restored state reads them again without asking.

**Ids.** `portfolio`; `name:{TICKER}`; `metric:{id}`, and the price of a proxy or a name
`metric:price:{TICKER}`; `market:{venue}:{id}`, with the id `DATA.md` gives for each venue;
`prop:N`, `channel:N`, `note:N`, `src:N`, `link:N`; `cmp:N` for a Computation and `jdg:N` for a
Judgment; `search:N` for a search. A derived link's id names its ends:
`looks_through_to:name:{FUND}>name:{TICKER}`, `loads_on:name:{TICKER}>channel:N`,
`tracks:metric:price:{TICKER}>channel:N`, `tracks:metric:price:{TICKER}>name:{TICKER}`.

## Objects

<!-- objects: rendered from harness/schema.py -->
| Type | Is | Id | Stated fields | Derived fields | Enters by |
|---|---|---|---|---|---|
| `Portfolio` | The person's holdings; one per store. add takes holdings and creates the holds links; the reserved ticker CASH has price 1 and zero beta. | `portfolio` | `asof` (date, required, fixed): fixed when the Portfolio is added; a different as-of is a different store; `horizon_days` (integer, default `180`); `total` (number): required when holdings are given as weights; `add` also takes `holdings` | `V`, `g` | `add` |
| `Name` | A ticker: held, looked through to, or named by a link. Its company is what it is, from its reference: its name, what it does, and its industry; a fund whose holdings the store reads has what it holds. A held or looked-through name's price is a traded Metric, metric:price:{TICKER}, tracking it when its e is not zero. | `name:{TICKER}` | `ticker` (text, required, fixed) | `type`, `sic`, `company`, `prices`, `e`, `holds`, `b` | the holdings and the look-through |
| `Channel` | A factor on the page: a channel of the orthogonalized basis; its proxy's price is a traded Metric tracking it. Its orthogonalization carries M_c, its largest one-month move. | `channel:N` | `name` (text, required): in plain words, as the page shows it; `proxy_ticker` (text, required); `order` (number, required) | `orthogonalization` | `add` |
| `Proposition` | A predicate about the world; an event is a proposition the portfolio watches. | `prop:N` | `title` (text, required): what it asserts, in plain words, as the page shows it: for an event, what happens, with a date only where the date is part of it; `text` (text, required); `metric` (the id of a Metric); `relation` (one of `>`, `>=`, `<`, `<=`, `==`, `occurs`, required); `value` (number); `window_start` (date); `window_end` (date, required); `kind` (one of `hitting`, `level_at_date`, `scheduled_decision`, required); `resolution_source` (text); `derivation` (a derivation, with its probabilities' bases): the propositions the point reads and the conditional probabilities its rule needs, each with its basis (MATH.md, the point); `chance` (a probability from 0 to 1, with its basis): the probability you state, its point when no market asks it and no derivation gives one, with its basis | `lo`, `hi`, `coverage`; recorded: `searches`; when watched or read by a derivation: `p`; when watched: `u_E`, `primary_channel`, `D`, `by_name`, `by_holding`, `pnl_yes`, `pnl_no`, `emove`, `worst` | `add` |
| `Market` | A venue's market, kept while a link or a note uses it. | `market:{venue}:{id}` | `venue` (one of `polymarket`, `kalshi`, `hyperliquid`, required, fixed); `id` (text, required, fixed) | `question`, `rules`, `resolution_source`, `close_time`, `tags`, `categories`, `series`, `yes_token`, `book`, `history`, `bid`, `ask`, `price`, `depth`, `open` | from its venue, when a link or a note names it |
| `Metric` | A level in the world, added with its current level from a page; the price of each channel's proxy and of each name is a derived, traded Metric. | `metric:{id}`; a proxy's price `metric:price:{TICKER}` | `id` (text, required, fixed); `name` (text, required): the metric in plain words, as the page shows it to the person: what a reader calls the level, never its id; `unit` (text, required); `current_value` (number, cites a Source); `asof` (date, cites a Source); `resolution_source` (text); `traded` (true or false, default `false`) |  | `add` |
| `Source` | A citation: a page fetched with fetch_source, or a document of a company's record read with search_company, kept while it is cited or linked as evidence; when its last use goes, it is again a fetched page, citable under the same id. | `src:N` | `url` (text, required, fixed); `retrieved_at` (date and time, fixed); `dated` (date): the date the page speaks as of, as the page states it; null until stated with edit, citing the page itself; `text` (text, required, fixed) |  | `fetch_source` or `search_company`; kept once cited or linked as evidence |
| `Note` | The analyst's reason. | `note:N` | `text` (text, required) |  | `note` |
| `Judgment` | The judge's answer, cached on the hash of its statement and evidence. | `jdg:N` | `kind` (text); `statement` (text); `input_hash` (text); `inputs` (object); `answer` (a probability); `model` (text); `raw` (text) |  | the judge, when structure asks a question |
| `Computation` | A derivation's output, cached on the hash of what it read. | `cmp:N` | `derivation` (text); `input_hash` (text); `inputs` (object); `output` (object) |  | the store, on read |
<!-- end objects -->

- **An event**'s components are propositions of the same type, unwatched.
- **The ledger looks over one period**, the horizon, from `asof` to `asof + horizon_days`. An event
  that could happen at any time is watched to the horizon's end; one with a date of its own, a
  decision, a release, or a deadline, on that date within it.
- **A market's close date is the market's own**: a market that closes before or after the event's
  date bears on it through the breakdown (`MATH.md`, propagation).
- **A proposition** states either its `derivation` or its `chance` (`MATH.md`, the point;
  `TOOLS.md`, a derivation).
- **A Metric** is `traded` when a market prices it. A price the store derives is named
  "{TICKER}'s price", and as of today it is the latest mark.
- **A Channel** is a factor on the page. Its `name` says in plain words what its proxy's price
  measures, so that the sign of a dollar beta to it reads as it is. Its `order`, any number, places
  it among the others.

## Links

<!-- links: rendered from harness/schema.py -->
| Relation | Is | From | To | Stated fields | Derived fields | Created by |
|---|---|---|---|---|---|---|
| `holds` | one of quantity, dollars, or weight, as the person gave it; the number of shares is fixed when given, and the dollars and weight update with the price | Portfolio | Name | `quantity` (number); `dollars` (number); `weight` (number) | `quantity`, `dollars`, `weight` | `link` |
| `looks_through_to` | a fund to a name it holds | Name | Name |  | `weight`, `dollars`, `holdings_asof` | the store, from holdings and data |
| `loads_on` | a name's beta on a channel | Name | Channel |  | `beta` | the store, from holdings and data |
| `tracks` | a metric whose move is the channel's or the name's: price divides the shock by the level; yield(duration) multiplies it by -duration / 100 | Metric | Channel, Name | `conversion` (price or yield(duration), required) |  | `link` |
| `watches` | the proposition is an event | Portfolio | Proposition |  |  | `link` |
| `implies` | every world where A is true is one where B is: it holds by the fields where they decide it, and otherwise the judge checks it; it bounds A from above and B from below | Proposition | Proposition |  | `by_fields`, `check`, `bound` | `link` |
| `and` | A = B and C, from the composite to each of its two components | Proposition | Proposition |  | `bound` | `link` |
| `or` | A = B or C, from the composite to each of its two components | Proposition | Proposition |  | `bound` | `link` |
| `then` | A = B then C, from the composite to each component; one link of each step per composite | Proposition | Proposition | `step` (integer, required): 1 for B, the part that comes first, or 2 for C, the part that follows it | `bound` | `link` |
| `quoted_by` | the market asks exactly the proposition: it resolves YES exactly when the proposition is true, or under the complement exactly when it is false; the judge checks it both ways | Proposition | Market | `transform` (one of `identity`, `complement`, default `"identity"`) | `forward`, `converse`, `bid`, `ask`, `price`, `bound` | `link` |
| `evidence` | a page that bears, through the passages the link quotes from it | Proposition | Source | `passages` (list of texts, required): the words of the page that bear, each quoted word for word from its text |  | `link` |
| `consequence` | the event to a metric it moves, which tracks a channel or a name: one figure the analyst states, converted to the shock, and for a traded metric the two worlds' levels placed around today's | Proposition | Metric | `value` (a number, with its basis, required): the figure, in the metric's unit, with its basis; `meaning` (one of `gap`, `level_if_happens`, `level_if_not`, required): gap: the shock, the gap between the worlds where the event happens and where it does not at the end of its window; level_if_happens or level_if_not: the metric's level then, in that world, for a traded metric | `gap`, `L_yes`, `L_no`, `sets` | `link` |
| `sourced_by` | a stated fact, or a stated assumption, to the page it cites | Proposition, Metric, consequence | Source |  |  | `add`, `edit`, or `link` with a `source_id` |
| `basis` | a derived value to the Judgment or Computation that produced it | any derived field | Judgment, Computation |  |  | the store, on read |
| `about` | a note to what it concerns | Note | any |  |  | `note`, or `link` from a Note |
<!-- end links -->

- **`evidence`** quotes at least one passage. Word for word means as the page's text reads, its
  line breaks, spacing, and the markup of its links, images, and emphasis aside.
- **`about`**: a note about a stated assumption is its basis; a note about a link the judge disputes
  answers the dispute.

## Derivations

What the store computes when structure exists. This is the map of the environment.

| When this exists | The store derives |
|---|---|
| `Portfolio` with holdings | prices as of `asof`; what each holding is, a company or what a fund holds; look-through into `Name` objects and `looks_through_to` links; `e`, `V`; betas and `loads_on` links from completed sessions; `g`; each name's price as a traded `Metric`, tracking the name when its `e` is not zero (a fund the store looks through has `e` 0, and its price tracks nothing) |
| `Channel` | the orthogonalization in channel order, with `M_c`; every beta recomputed; the proxy's price as a traded `Metric` with a `tracks` link whose conversion is `price` |
| `Metric` | an edit to its `current_value` recomputes every consequence that uses it, and an edit to its `resolution_source` every `implies` whose arithmetic reads it |
| `Proposition` | nothing until linked; a stated `derivation` or `chance` gives its point |
| `quoted_by` | the market check, both ways; the proposition's `[bid, ask]` and price; the interval |
| `implies` | whether it holds: by the fields where they decide it, otherwise the implication check; the interval |
| `and`, `or`, `then` | the interval |
| `watches` | the point `p`, its probability: the price of a market that asks it, its derivation evaluated by total probability, or the probability you state; a market, a link, a derivation, or a probability it reads, added, edited, or removed, recomputes it |
| `consequence` | the stated figure converted to the shock and, for a traded metric, the two levels; through the metric's `tracks` link, `sets`: that channel's raw shock, the part the earlier channels imply, and its residual shock, or that name's whole move, the part its betas give, and its residual move `d` |
| `watches` with impact links | from the proposition's `p` and its `consequence` links: `u_E`, `D`, `by_name` and `by_holding`, each contribution with its `if_yes` and `if_no`, `primary_channel`, `pnl_yes`, `pnl_no`, `emove`, `worst` |

The formulas are in `MATH.md`. The store asks the judge: each claim you write causes its check,
asked again whenever the claim changes.

## Removing

`remove` takes what you created and the initial contents, which are yours once loaded. Removing a
link removes it and every value derived from it. Removing an object removes it and its links the
same way. The Computations and Judgments those values named stay: nothing reads them as numbers,
and the same check asked again is answered from them.

`remove` refuses:

- a derived object, link, or value (a Name, a price Metric, a `looks_through_to`, `loads_on`, or
  price `tracks` link, a Computation, a Judgment), which follows from structure: change what it
  follows from;
- a market, which stops bearing on a proposition when its `quoted_by` link is removed and goes when
  nothing names it;
- a removal that would leave a stated fact or an assumption without its Source, or a stated
  reference without its object (a proposition a derivation names, a metric a proposition names),
  naming the dependents: edit or remove those first.

## The derivations in detail

The fixed values below are `MATH.md`'s constants, where each has its reason.

### Portfolio and look-through

A holding's ticker resolves through the ticker reference: a common stock (`CS`) or an ADR (`ADRC`)
is a name; an `ETF`, `ETN`, `ETV`, or `ETS` is a fund; anything else is booked whole with beta
`[1, 0, ..., 0]`, the market.

A fund is expanded into its constituents as of its latest holdings on or before `asof`, the
`holdings_asof` of each `looks_through_to` link:

- A constituent resolves through the ticker reference only when the fund's holdings class it as a
  listed share, equity or real estate, because a bond's or a coin's ticker can be a listed company's
  or fund's; and only when its identifiers confirm that the reference is the security the fund
  holds, because a share listed abroad can carry the ticker of an unrelated US one. A constituent of
  any other class, or one the reference is not confirmed to be, maps to the fund's residual.
- A name constituent with a weight of at least 0.0025 in absolute value becomes a
  `looks_through_to` link with `dollars = fund_dollars * weight`. A fund constituent is expanded
  once more at the product of the two weights, with the same floor on the product; a fund met at the
  next depth is booked whole.
- Cash constituents become `CASH:{fund}`. The residual, the fund's dollars less what is mapped and
  its cash, is `UNMAPPED:{fund}`, the fund's other holdings, when positive, and `CASH:{fund}` when
  negative (a levered fund's financing).
- A fund is booked whole, as a name with its own beta, when its weights sum outside `[0.5, 1.5]`,
  when its holdings cannot be read, or when it maps nothing (a fund of bonds, of a commodity, of a
  coin, or of shares listed abroad). Adding the portfolio returns, for each fund, whether it was
  looked through and why not.

`e_i` is the sum of the `looks_through_to` links into name `i`, plus its `holds` link when it is not
expanded; an expanded fund has `e = 0`, its own dollars moving with its betas in `g` (`MATH.md`,
impact). The `e` sum to `V`.

**What the portfolio owns** is read from the same data. A name is its company (`company`): its name,
what it does, and its industry, from its ticker reference. A fund is what it holds (`holds`): each
holding by name, where it is listed, and its kind of asset, from the fund's holdings, every holding
counted whether or not the look-through prices it on its own. Where it is listed is the country of
its exchange: the US where its FIGI is a US listing's, and otherwise as the fund's data gives it. It
says where the shares trade, which can differ from where the business earns. A bond fund's holdings
are loans, each with what the fund's data says of it: its issuer's kind and the loan's.

**Prices.** When the as-of is today, each name's and each proxy's current price is its latest mark,
its last trade or else its latest minute's close, with its time, so that the portfolio and the
markets, whose books are read live, describe the same moment. Returns, betas, and `M_c` use
completed sessions only. For an as-of in the past, the price is that day's close. Holdings given in
dollars, or as weights of a `total`, keep the number of shares they had when given, and their value
updates with the price, like holdings given as quantities. A price's move runs from the close of the
session before its latest mark's session to the mark: during a session, that day's move; after it,
until the next opens, that session's whole move. `session` names the session it covers.

### Channels and betas

A return, daily or over a window, is the close-to-close return with the dividend added back on its
ex-dividend date: what holding the instrument earned.

- Betas are fitted over the 504 trading days ending at `asof`. Channel 1 is its proxy's return;
  channel `k` is the residual of proxy `k` regressed, with intercept, on channels 1 to `k-1`.
- A name with at least 250 days of closes gets OLS coefficients on the orthogonalized channels; one
  with fewer gets `[1, 0, ..., 0]`, a beta of 1 to the market, the one beta every listed name has.
- Each holding is fitted on its own history, and a fund the look-through expands is fitted on the
  fund's, because a fund's history holds everything it held, in the weights it held it. A fund with
  fewer than 250 days of its own moves as its holdings do: what it holds of each name at that name's
  betas, its other holdings with the market, and its cash not at all. Its fit, in its betas' basis,
  says which: `sensitivity` `own` or `holdings`.
- A proxy with fewer than 504 days uses what it has. A proxy is a listed ticker or a crypto pair,
  `X:{COIN}USD`, whose daily price is its price at 16:00 New York, when listed proxies close.
- `M_c`, a channel's largest month, is its largest one-month move: the largest absolute return of
  its residual part over 21 consecutive sessions of the 504, each window's proxy return put through
  the orthogonalization as `window_returns` puts it. A dollar beta is dollars per unit of the
  channel's residual return, and channels move by very different amounts, so `abs(g[c]) * M_c`, the
  largest one-month move applied to the dollar beta, is what such a month would do to the
  portfolio, a measured fact.

The store starts with one channel, the market. The other forces that move this portfolio are yours
to find, from what it owns and the events you build: add a channel for each force an event moves the
portfolio through, a rate, a currency, a commodity, a region, or a sector, named in plain words for
what its proxy's price measures, with a proxy that has two years of history. The channels run broad
to narrow by `order`, so that each holds what the earlier ones do not: a channel is what its proxy
does beyond the channels before it. Adding a channel, or editing one's proxy or order, recomputes
the orthogonalization and every beta.

### Markets

The universe is every market Polymarket, Kalshi, and Hyperliquid list, and it stays at the venues.
`search_markets` passes your parameters to a venue's own search and listing endpoints (`DATA.md`)
and returns what the venue returns; the store applies no filter and keeps no listing. A market's
terms are its question and rules, in prose: you read them and write the propositions they ask, and
the judge reads them in every market check. `close_time` is the venue's own end date for the market
(`DATA.md`).

A market's book is its YES side's: the best `bid` and `ask` are the highest bid and the lowest ask
for YES. `price` is the price the venue displays:

- **Polymarket**: the midpoint of the best bid and ask, or the last trade where the spread is wider
  than ten cents or a side is empty.
- **Kalshi**: the midpoint where the best bid and ask are within ten cents, and otherwise the last
  trade; a market that has never traded then has no price. The book lists bids on each side, and a
  NO bid at `q` is a YES ask at `1 - q`.
- **Hyperliquid**: the mid the venue publishes for the YES side: the midpoint of its book, or its
  last trade when the book is empty.

`price_rule` says which rule gave the price: `midpoint`, `last trade`, or, at a past as-of,
`history`. `depth` is the smaller of what the top five levels on each side cost to buy, how much
money stands at the quotes: YES up the asks, at `price * size`, and NO against the bids, at
`(1 - price) * size`.

`open` is whether the market trades on the as-of date, by the venue's own word: Polymarket's
`closed`; Kalshi's status, open when `active`; and on Hyperliquid, whether its question has settled
the outcome.

For an as-of in the past, a market's price is its history price at the as-of, its bid and ask that
price less and plus 0.01, and its `depth` unknown; it is `open` when it had started and not yet
closed by then.

### The judge

The judge checks claims you make about texts. Each check states the situation, the as-of date and
the texts it compares, and asks one plain question in their words; its answer is the probability the
claim holds. It reads what the store serializes: propositions as you wrote them; markets with their
question, rules, resolution source, close date, and whether they are still open on the as-of date;
and the passages linked as evidence, with their page's URL and date. It reads the texts themselves,
independent of your argument: your notes are yours, and of a page it reads the passages you link.

| Check | The claim | Evidence |
|---|---|---|
| Market, forward | if the proposition is true, the market resolves YES (NO under the complement) | the proposition; the market; the passages linked to the proposition |
| Market, converse | if the market resolves YES (NO under the complement), the proposition is true | the same |
| Implication | if A is true, B is true, where the fields do not decide it | A and B; the passages linked to either |

A check answered below one half is disputed. The claim stands, because it is yours, and `check()`
lists the dispute until you answer it: by changing the claim, which asks again, or by a note about
the link explaining why the reading is wrong. A check the judge has not answered leaves its value
null, with the reason.
