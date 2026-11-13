# TOOLS.md

## 5. Tools

Sixteen tools, in three groups: five state, four bring the world in, and seven read. What the analyst says is an object, a link or a field of section 2, stated with the same five.

**What a statement does.** A statement is written whole. Where it states an assumption, its basis comes in the same call: its reasoning, which is kept as a note about it, and, where it rests on a page, that page with the passages quoted. The statement returns what it now implies: the object or the link with its derived fields, and each watched event it can move, as it now stands. A statement that names a market also returns that market's question, its rules, its resolution source and its close, as its venue states them, and its book as the store holds it (3.3), or, where no book is held, why it could not be read.

**The event as it now stands** is what each statement that can move a watched event returns of it. For each world it shows:

- its probability and the weight today's prices put on it;
- each price a shock sets: the shock as stated, whether it states the whole move or the residual, the whole move against the baseline, the move against today, and the level there beside today's;
- each other factor that moves there, with its move against the baseline and against today, marked as moving with the stated ones;
- each holding it moves, with its P&L there, and the portfolio's.

The row follows (9). A statement that reaches every watched event, as a factor's, the portfolio's and a holding's do, returns each event's row (9) in its place, in the order the events were made. A consequence read with `get` gives its price's level in each world and, in each world it applies in, its whole move and its residual. While its statements leave the probabilities of its worlds open, it is what each world does against the baseline, which needs none: each price a shock sets and each other factor that moves, with its move against the baseline; each holding it moves, with its value there less its value in the baseline, today's price standing at the baseline, a holding valued by what it is worth at its stated value; and the portfolio's, the holdings summed. The moves against today, the levels and the row follow once the probabilities are settled.

### The five that state

| Tool | Does | Returns |
|---|---|---|
| `add(type, fields)` | creates an object (2.1) | the object. A `Portfolio` returns the date its horizon ends, and what it owns (3.1): each holding with its value, weight and day change, and what it is; each fund's largest holdings, and how it divides by listing and by kind; the portfolio's weight by company, by industry and by country; and the news of each company that is 5% or more of it. A `Factor` returns how it is built from the factors before it, its residual's largest month (3.2), the history every proxy shares, and the portfolio's dollar beta to it, with each watched event's row |
| `link(from_id, relation, to_id, fields)` | creates a link (2.2) | the link, and what it now implies. An `asks`, a link of the breakdown, or a `way`: its structure's ranges and probabilities, with each watched event of that structure as it now stands; an `asks` or an `implies` also says what differs between its two texts, where they describe different outcomes. A `consequence`, a `worth`, or a contract held: the event it is on, as it now stands. A holding: what the portfolio owns, as it now stands, with each watched event's row |
| `edit(id, field, value, source_id)` | changes a stated field. With the field `definition`, it replaces a proposition's definition as a whole (2.1): its title, its text and the fields code can compare. Required fields must be included; an optional definition field left out is cleared. Its chances, its links and what is said of its worlds stay as they are | what `add` or `link` returns for it |
| `note(text, about_ids)` | writes the analyst's reasoning about one or more objects or links | the note |
| `remove(id)` | removes an object or a link, with everything derived from it (2.3) | what was removed, and each watched event it moved, as it now stands, or each event's row where it reached every event; a holding removed also returns what the portfolio owns |

### The four that bring the world in

| Tool | Does | Returns |
|---|---|---|
| `search_web(text, params, proposition_id)` | searches the web | each page's address, title and date, with the passages quoted; where a proposition is given, the search is recorded on it |
| `fetch_source(url, text)` | reads a page whole | its id, its length and an excerpt, and, where a text is given, the passages that contain it; `get` reads it whole, or the passages that contain another text |
| `search_company(ticker, kind, params)` | reads a company's own record, from its listing date to the as-of: its news, filings, results, guidance, and analysts' ratings | a list as compact rows, with who published it, and each document as a page |
| `search_markets(text, proposition_id)` | searches every venue, in the analyst's own words, for markets that bear on a proposition, and records the search on it | each venue's most relevant families of markets open at the as-of, the venues taking turns; a large family as each market's question, a ladder as its first and last question and its strikes, read whole with `get` |

A page and a company's document are each kept as a `Source` once the analyst cites them (2).

### The seven that read

| Tool | Returns |
|---|---|
| `get(id, text)` | one object or link whole: its stated fields, its derived fields and its links. The portfolio is read as `add` returns it, as it now stands, with its dollar betas and its volatility. A watched event is read as it now stands, with the distribution of the portfolio's P&L at the horizon's end. A page is read whole, or by the passages that contain `text`. A market or a family still at its venue is read from there |
| `query(type, where)` | the objects of a type that match |
| `neighbors(id, relation, direction)` | the objects linked to one |
| `ledger(together, considering)` | every event's row (9), in the order the events were made. With `together`, the events named are read as one set of worlds (1.9). With `considering`, a contract, named by its market, its side and a quantity, is valued in those worlds beside the portfolio: what it pays in each, less what buying it costs, with what it offsets and what it leaves |
| `exposure()` | the portfolio's dollar beta and beta to each factor, and for a factor quoted as a yield its dollars for each percentage point that yield rises; the largest month of each factor's residual (3.2), in percentage points for a factor quoted as a yield, and that month applied to the dollar beta; and each proxy's price and day change |
| `window_returns(start, end, names)` | how the factors moved over a past window: each proxy's move and each factor's residual over it (3.2), expressed in percentage points of yield for a factor quoted as a yield. For each company or fund named, in the terms a shock on it is stated in: its move over the window (3.2), its response to the factors' moves using betas fitted on its own history up to the window's start, and its residual. For a foreign listing, those moves are in its quote currency, with the exchange rate's move beside them. A fund valued by its terms (1.6) gives its move over the window (3.2), with its reference and multiple, or its contracts. Naming a factor's proxy returns its move and identifies the factor |
| `check()` | what cannot hold together, and each link whose two texts describe different outcomes (6) |

**A call that fails** returns its reason and what depends on it, and leaves the store as it was. A statement that breaks a rule of section 2 fails this way. So does a call that needs a reading the store could not obtain, which can be made again.

## 6. check

`check()` shows what stands open across the analyst's statements.

- **What cannot hold together** (1.10), each case with the figures involved, and each claim between two propositions that their fields contradict, with the values compared (1.2).
- **Each `asks` or `implies` link whose two texts describe different outcomes,** with what differs.

## 9. The ledger

One row for each watched event. Each figure is read from the event's outcomes (1.1) and shown under its label.

- **Forecast** {probability}, with its market-implied range, {lo}–{hi}, a floor or a ceiling alone where the markets set only one. Where it differs from a market that asks the event, that market's bid and ask stand beside it.
- **Yes** {yes} ({share} of the portfolio) and **No** {no}: the portfolio's P&L on each side, the side's outcomes averaged at their probabilities.
- **Full impact** {full_impact}: Yes less No.
- **Volatility** ±{volatility}: the spread between the outcomes' P&L, each at its probability.
- **Worst outcome** {P&L} · {the outcome's title}, {its probability}: the lowest of the outcomes' P&L.
- **Expected value** {expected}: the outcomes' P&L averaged at their probabilities. It is shown where it is not zero (1.5).
- **What moves it:** the full impact in three parts (1.6): each factor's dollar beta times its move between the two sides, what each stated company does beyond its response on the portfolio's exposure to it, and what compounding and a holding's terms add.
- **Largest contributors:** the three largest named contributions to the full impact, with their amounts and the funds the names are held through.

The row also carries its outcomes, each with its probability, the weight today's prices put on it, and its P&L. `ledger()` returns every row with every field, its dollars to the cent, and beside them each holding's P&L on Yes and on No under each event.

## Each tool's signature

### `add(type, fields)`

- `type` (string, required): the object type: Portfolio, Proposition, Metric, or Factor
- `fields` (object, required): the object's stated fields (ONTOLOGY.md, 2.1). A `Portfolio` also takes `holdings`, a list, each `{"ticker", and one of "quantity", "dollars" or "weight"}`, cash `{"cash", "currency"}`, or a contract `{"market", "side", "quantity"}`.

### `link(from_id, relation, to_id, fields)`

- `from_id` (string, required): the id the link runs from
- `relation` (string, required): the relation, by ONTOLOGY.md's link table
- `to_id` (string, required): the id the link runs to; a market as market:{venue}:{id}
- `fields` (object): the link's stated fields (ONTOLOGY.md, 2.2).

### `note(text, about_ids)`

- `text` (string, required): the reason, in words
- `about_ids` (array): the ids the note concerns

### `edit(id, field, value, source_id)`

- `id` (string, required): the object, link, or fetched page
- `field` (string, required): a stated field, or definition for a proposition's definition whole
- `value` (any, required): the field's new value, whole; null clears a field that is not required. For definition, an object of the proposition's title, its text and the fields code can compare, each as add takes it. Required fields must be included; an optional definition field left out is cleared.
- `source_id` (string): the page a fact about the world cites

### `remove(id)`

- `id` (string, required): the object or link

### `search_web(text, params, proposition_id)`

- `text` (string, required): the query
- `params` (object): the search tool's own parameters, as DATA.md lists
- `proposition_id` (string): the proposition the search is for; recorded on it

### `fetch_source(url, text)`

- `url` (string, required): the page's http or https URL
- `text` (string): words to find in the page, returning the passages that contain them

### `search_company(ticker, kind, params)`

- `ticker` (string, required): the company's ticker
- `kind` (string, required): news, current_reports, risk_factors, annual_report, earnings, guidance, or ratings
- `params` (object): the kind's own parameters, as DATA.md lists them

### `search_markets(text, proposition_id)`

- `text` (string, required): what you are looking for, in your own words, with dates where they belong to the event
- `proposition_id` (string, required): the proposition the markets would bear on; the search is recorded on it

### `window_returns(start, end, names)`

- `start` (string, required): the window's first date, YYYY-MM-DD: moves run from its close
- `end` (string, required): the window's last date, YYYY-MM-DD: moves run to its close
- `names` (array): companies or funds to read over the window as well, each by its id, name:{TICKER}

### `get(id, text)`

- `id` (string, required): any id in the store
- `text` (string): for a page: words to find in it, returning the passages that contain them

### `query(type, where)`

- `type` (string, required): an object type
- `where` (object): field filters: a value, or {op: value} with op eq, ne, lt, le, gt, ge, in, or contains

### `neighbors(id, relation, direction)`

- `id` (string, required): any id in the store
- `relation` (string): one relation, or all
- `direction` (string): out, in, or both (the default)

### `ledger(together, considering)`

- `together` (array): the ids of the events to read as one set of worlds
- `considering` (object): a contract to value in those worlds, `{"market", "side", "quantity"}`: its market's id, its side, `yes` or `no`, and the number of contracts

### `check()`


### `exposure()`

