"""Our copy of the parts of Synpath our three venues run: Synpath 0.11.0 (git ab7c64b,
https://github.com/Synpath-ai/synpath), under its MIT licence, whose copyright notice and text are in LICENSE beside
this file.

What it holds, each module named and laid out as upstream's:
- `base`: the HTTP client every venue reads through (`HttpClient`), paced by a token bucket shared per venue
  (`RateLimiter`), retrying a 429 with doubling backoff or its `Retry-After`, at most MAX_RETRY_WAIT seconds a wait,
  and mapping every other non-2xx answer to a typed error with the venue's own reason; and the status and page-size
  checks the searches use.
- `errors`: those typed errors.
- `ids`: `qualify`, a venue's native id as `venue:native`.
- `types`: the unified shapes the normalizers return: `Market` with its `Outcome`s, `Quote` and `MarketStats`;
  `Event`; `OrderBook` and `OrderLevel`; `FeeSchedule`; `Series`; `Page`.
- `kalshi`: the normalizers of a market, an event, a book (YES bids, and asks reflected from NO bids) and a series
  with its fee schedule; and the site search, `api.elections.kalshi.com/v1/search/series`, with its check of the
  answer's shape, its events then read whole by ticker.
- `polymarket`: the normalizers of a market, an event and a book, a market's fee schedule, and its settlement sources.
- `polymarket_us`: the normalizers of a market, an event and a book, its timestamps, and the gateway's search.

What it leaves out: everything our read paths do not run. That is trading, streaming, the server, matching, the
history and bucket modules, the other thirteen venues, the client that routes between venues, and the Rust core,
whose one use upstream is the websocket book. Within the modules kept, every top-level definition no read path reaches
is left out, with the imports only those used. Each definition kept is upstream's, verbatim, but for three changes:
- Kalshi's `search_events`, `_search_page`, `_events_by_ticker` and `_raw_events_by_ticker`, and Polymarket US's
  `search_events`, were methods of upstream's adapter classes. Here they are functions of the clients they read
  through (`http`, and Kalshi's `search`), their bodies otherwise unchanged. Our venue classes
  (harness.venues.kalshi, .polymarket, .polymarket_us) are plain classes holding those clients, built as upstream's
  constructors build them.
- `FeeSchedule.estimate` and `_rounded`, the fee formulas of every venue, which nothing here calls, are left out with
  the Limitless curve they read.
- The deprecation shim (`_deprecated`, and `_Base._renamed`, which ran it) is left out: it renames `amount` to
  `quantity` on order and trade models, none of which is kept.

Following upstream. Because each definition kept is upstream's text, a fix upstream shows as a difference in a
definition of the same name. To take up a later release: install it in a scratch environment, diff each module here
against its namesake there, definition by definition, and bring over each change to a definition kept here, with
whatever new definition it reaches. A definition upstream adds that our read paths would call is taken whole. Each
release's notes (https://github.com/Synpath-ai/synpath/releases) say which venues changed; a release that changes none
of Kalshi, Polymarket, Polymarket US or the shared client needs nothing here.
"""
