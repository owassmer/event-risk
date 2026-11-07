# Data: how to search

How to search the web, a company's record, and then each venue: the web search tool's parameters;
each kind of a company's record and its parameters; and for each venue, its endpoints and their
parameters, how a market is named, what its terms are, and how it settles.

## The web

`search_web(text, params, proposition_id)` is one web search for `text`, which may run several
queries. `params` are the search tool's own:

- `max_uses`: how many queries it may run, 3 when not given;
- `allowed_domains` or `blocked_domains`, not both: bare domains with an optional path, without a
  scheme, such as `example.com` or `example.com/blog`;
- `user_location`: `{"type": "approximate", "city", "region", "country", "timezone"}`, at least one
  of the last four, `country` as a two-letter code and `timezone` as an IANA zone.

Forecasting platforms, futures prices, and surveys publish probabilities of their own, which you
find with `search_web`, read with `fetch_source`, and cite as evidence.

## A company's record

`search_company(ticker, kind, params)` reads one kind of a company's record, newest first, with
`params` the kind's own. With no dates, a kind returns the company's whole history, so give the
dates your question needs.

- `news`: articles about the company, with the full text of those that have it. `published.gte`
  and `published.lte`, a date or a UTC time; `channels` and `tags`, the publisher's topics; `limit`,
  100 when not given.
- `current_reports`: the items of its 8-Ks. `form_type`, `8-K` or `8-K/A`; `filing_date.gte` and
  `filing_date.lte`; `limit`, 10 when not given, 100 at most.
- `risk_factors`: each risk factor in its annual reports, under three levels of category, with its
  text. `filing_date.gte` and `filing_date.lte`; `limit`, 100 when not given.
- `annual_report`: its 10-Ks, section by section, each whole: `business`, `risk_factors`, and
  `mda`, the management's discussion and analysis; a filing can lack one. `section` names one;
  `filing_date.gte` and `filing_date.lte`; `limit`, 10 when not given, 100 at most.
- `earnings`: each result reported or scheduled, with the consensus estimates and the surprise.
  `date.gte` and `date.lte`; `date_status`, `projected` or `confirmed`; `fiscal_year` and
  `fiscal_period`; `limit`, 100 when not given.
- `guidance`: each range the company gave for a period, with its earlier range and the consensus
  then. `date.gte`, `date.lte`, `limit`.
- `ratings`: the analysts' consensus, one row: the rating, the count of each rating, and the
  consensus, high, and low price targets, over the ratings given between its dates. `date.gte` and
  `date.lte`.

`current_reports`, `risk_factors`, and `annual_report` come from the SEC filings of a company that
files 10-Ks and 8-Ks; a foreign issuer, which files a 20-F, has none of them.

## The venues

`search_markets(venue, endpoint, params, proposition_id)` passes `params` unchanged to one of a
venue's endpoints below and returns what the venue returned. Every filter is a parameter you pass;
the store adds none and hides nothing. A list repeats its key (`tickers=A&tickers=B`); a boolean is
sent as `true` or `false`.

## Polymarket

Polymarket groups markets under events, and each event has tags. A market is one question with the
outcomes `Yes` and `No`; a market whose outcomes are named otherwise has no YES side here, and no
price.

### `/public-search`

Free-text search over events and their markets.

- `q`: the text, required.
- `events_status`: `active` returns live events. Without it the answer includes closed events and
  markets. No parameter leaves out closed markets: a live event brings its closed markets with it,
  and each market says whether it is open.
- `limit_per_type`: how many events a page holds; each event brings all its markets. `page` turns
  the page, and the answer's `pagination` says `hasMore` and `totalResults`.
- `events_tag`: tag slugs to search within; `exclude_tag_id`: tag ids to leave out.
- `sort`, `ascending`, `keep_closed_markets` (an integer), `search_tags`, `search_profiles`,
  `recurrence`, `optimized`, and `cache` are further options the venue names without saying what
  each does.

### `/markets`

Lists markets.

- `closed`: `false` or omitted for open markets, `true` for closed ones.
- Identifiers: `id`, `slug`, `clob_token_ids`, `condition_ids`, `question_ids`.
- `tag_id`, with `related_tags`; `include_tag`.
- Ranges: `liquidity_num_min` and `_max`, `volume_num_min` and `_max`, `start_date_min` and `_max`,
  `end_date_min` and `_max`.
- `order` (comma-separated fields) and `ascending`; `limit` and `offset`. A deep offset is refused:
  page deeply with `/markets/keyset`.

### `/markets/keyset`

Pages through markets without a limit on depth. `limit` is at most 100, and `after_cursor` takes the
`next_cursor` of the page before, which is present only when a page is full. `closed` omitted or
`false` gives open markets.

### `/events` and `/events/keyset`

Lists events, each with its markets.

- `closed`: omitted for open and closed events, `false` for open ones.
- `active`, `archived`, `featured`.
- `tag_id`, `tag_slug`, `exclude_tag_id`, `related_tags`.
- Ranges: `liquidity_min` and `_max`, `volume_min` and `_max`, `start_date_min` and `_max`,
  `end_date_min` and `_max`.
- `recurrence`, `limit`, `offset`, `order`, `ascending`.

`/events/keyset` pages events the way `/markets/keyset` pages markets.

### A Polymarket market

- **Named** `market:polymarket:{id}`, with the market's `id` as the venue gives it.
- **Terms:** its `question` and its rules, the market's description, which name the resolution
  source when its `resolution_source` is empty.
- **Settles** by its rules. `close_time` is the market's end date, or its event's where the market
  states none, as a New York date.

## Kalshi

Kalshi has no free-text search. A series is a family of recurring events that follow the same
format and rules; an event is one occurrence, and its markets are the outcomes or strikes it lists.
Markets are found through series, by their categories and tags, then their events and markets.

### `/series`

Finds series.

- `category`: exact and case-sensitive; it matches any of a series' categories.
- `tags`, `include_product_metadata`, `include_volume`, `min_updated_ts`.
- Every series matching comes back, with no paging, each with its `ticker`, `title`, `category`,
  `tags`, and `frequency`. A series' `ticker` is the `series_ticker` that `/events` and `/markets`
  take.

### `/events`

Lists events.

- `series_ticker`; `tickers`, event tickers, comma-separated.
- `status`: `unopened`, `open`, `closed`, or `settled`.
- `with_nested_markets=true` brings each event's markets; without it, an event brings none.
- `with_milestones`, `min_close_ts` (Unix seconds), `min_updated_ts`.
- `limit` is at most 200; the answer's `cursor` is passed as `cursor` for the next page.
- It leaves out multivariate events.

### `/markets`

Lists markets.

- `event_ticker` (one), `series_ticker`, `tickers` (market tickers, comma-separated).
- `status`: `unopened`, `open`, `paused`, `closed`, or `settled`, one at a time. A market's own
  status reads `active` while it trades.
- Timestamps in Unix seconds: `min_created_ts` and `max_created_ts`, with `unopened` or `open`;
  `min_close_ts` and `max_close_ts`, with `closed`; `min_settled_ts` and `max_settled_ts`, with
  `settled`; `min_updated_ts` and `max_updated_ts`, alone.
- `mve_filter`: `only` or `exclude`, for multivariate combinations.
- `limit` is at most 1000; the answer's `cursor` is passed as `cursor` for the next page.

### A Kalshi market

- **Named** `market:kalshi:{ticker}`, with the market's ticker.
- **Terms:** its title and its rules, `rules_primary` and `rules_secondary`. Its series names the
  settlement sources, which are its `resolution_source`, with its category and tags.
- **Settles** by its rules. A strike market states its strike: `strike_type` is `greater`,
  `greater_or_equal`, `less`, `less_or_equal`, or `between`, with `floor_strike`, `cap_strike`, or
  both, in the unit its title writes; `functional`, `custom`, and `structured` strikes state theirs
  in the rules. `close_time` is the market's close time, as a New York date.

## Hyperliquid

Hyperliquid's outcome markets are binary markets, each settling to 0 or 1: the venue's own recurring
outcomes on its perpetual contracts' prices, and outcomes that deployers define by templates, each
stating its terms in its parameters.

### `outcomeMeta`

The listing: every live outcome market, in one answer, whatever its topic. It takes no search
parameters, so one read puts the whole listing in your context.

- **An outcome** has a `name`, its template (`Recurring` for the venue's own, `template:{name}` for
  a deployer's), and a `description`, the template's parameters as `key:value` joined by `|`.
- **A question** groups named outcomes, exactly one of which settles YES. In `search_markets` it is
  the event; an outcome no question holds stands alone.

### A Hyperliquid market

- **Named** `market:hyperliquid:{outcome}`, with the outcome's number.
- **Terms:** its template and parameters, with its question's.
- **Settles:** a recurring price binary (`class:priceBinary`, with `underlying`, `expiry`, and
  `targetPrice`) settles YES if and only if the settlement mark price at `expiry` is at least
  `targetPrice`. A deployer's template settles by its own parameters, its dates among them (`time`,
  `resolutionDeadline`, and the like).
- **`close_time`** is a recurring binary's `expiry` (`YYYYMMDD-HHMM`, UTC), as a New York date. A
  deployer's template names its own dates among its parameters, and its `close_time` is null.
