# ONTOLOGY.md

## 2. The store

One store holds one portfolio and everything the analyst reasons about it. Each thing is an object, and each relation between two things is a link. The analyst creates, changes and removes them with the same few tools (5), and every number the page shows follows from them.

**Stated and derived.** Every field is one or the other. A stated field is written by the analyst or by a fetch: what a proposition asserts, a holding as the member gave it, a chance, a shock. A derived field is computed from stated fields, links and fetched data. It is computed when it is read, and again when anything it read changes. Removing an object or a link removes everything derived from it. The store keeps when each stated field was written.

**Basis.** Every figure opens to what it rests on. A fact about the world, such as a metric's level, cites the page it was read from: `{"value": ..., "source_id": "src:N"}`. An assumption the analyst states, such as a chance or a shock, carries its basis: passages quoted word for word from a page it cites, `{"value": ..., "source_id": "src:N", "passages": ["..."]}`; a note with its reasoning, `{"value": ..., "note": "..."}`; or both. The note a statement gives its reasoning in stays with that statement, and goes when the statement is restated on another basis, cleared or removed. A derived figure carries the Computation that produced it.

**One place for each thing.** An event is a proposition the portfolio watches. A way of an event, a rung of a ladder, and a joint world of several events are propositions too, told apart by their links. Several events read together are a view of their rows, as the ledger is (1.9), and so is a contract considered as protection.

**How a thing is named.** A statement refers to a thing by its id. The portfolio is `portfolio`. A company or a fund is `name:{TICKER}`. Cash in a currency is `cash:{CURRENCY}`. A market is `market:{venue}:{id}` and a family of markets `family:{venue}:{id}`, as a search returns them. A metric the analyst adds is `metric:{id}`, by the `id` it gives it, and the price of a name or of a factor's proxy is the metric `metric:price:{TICKER}`. The store numbers what else is made: `prop:N`, `factor:N`, `link:N`, `note:N`, `src:N`, and what a derived figure rests on, `cmp:N`, a Computation.

**What comes from outside.** The venues' markets stay at the venues, where one search reaches them all (3.3). A document is read where it is: a page or a company's record. The store keeps what the analyst uses, for as long as it uses it: a market that a link, a note or a chance names, and a document that a stated fact cites or a link holds as evidence, which is then a `Source`. A search keeps the results it returned, read by its id. A proposition shows each of its searches by its words, where it ran, how many results it returned, and which of them the proposition uses.

### 2.1 Objects

| Object | What it is | Stated | Derived |
|---|---|---|---|
| `Portfolio` | what the member holds, as of a date, over a horizon | `asof`; `horizon_days` (180); `total`, where holdings are given as weights | `value`; its weight by company after look-through, by industry, and by country (3.1); `dollar_beta` to each factor; `volatility` to the horizon's end (3.2) |
| `Name` | a company or a fund: one the portfolio holds, holds through a fund, or an event names. It is priced through the security it is held by, and a company's several listings are one name | `ticker` | what it is: a company with what it does and its industry, or a fund with what it holds (3.1); its prices; `exposure`, the portfolio's dollars in it after look-through (3.1): a fund that is looked through holds its dollars in what it holds, so its own exposure is only what other funds hold of it; `betas`; its price as a `Metric` |
| `Factor` | a force that moves prices, carried by one proxy | `name`, in plain words; `proxy`; `order`, broad to narrow; and, for a factor quoted as the yield its proxy pays, that `yield` and the proxy's `duration`, each with the page it was read from, stated when the factor is added | how it is built from the factors before it; `largest_month` (3.2); its proxy's price as a `Metric` |
| `Metric` | what a proposition is decided on: a level in the world, or something that happens; or a level the analyst cites | `id`; `name`, in plain words; `kind`: `price`, `yield`, or `level` for any other level, with its `unit`, or `occurrence` for something that happens; `level`, where it stands today, with the page it was read from, `{"value": ..., "source_id": "src:N"}`; `asof`, the date that level stands at, where its page gives one; `resolution_source` | |
| `Proposition` | a claim that will turn out true or false | its definition, its chances, and what is said of its worlds (below) | `range`, what the markets it takes imply through the links; `probability`, or the range the statements leave where they leave it open; its searches; and, watched, its row (9) and the distribution of the portfolio's P&L at the horizon's end (1.8) |
| `Market` | a venue's contract | `venue`, `id` | its question, rules, resolution source and close; its book, with `bid`, `ask`, `price` and `depth`; its history; its terms and its family, the venue's grouping it belongs to (3.3); its settlement, as the venue reports it |
| `Source` | a document read from outside: a page or a company's record | where it was read, `retrieved_at`, `text`; `dated`, the date it speaks as of | |
| `Note` | the analyst's reasoning | `text` | |
| `Computation` | how a derived value was worked out | | the derivation, what it read, its output |

**A proposition's stated fields** are of three kinds.

- **Its definition** says what would make it true: `title`, in plain words, as the page shows it; `text`; and the fields code can compare: `metric`; `comparison`, one of `>`, `>=`, `<`, `<=` and `==`, or `occurs` where it is true when something happens: the occurrence its `metric` names, or what its text says where it names none; `value`; `window_end`, the date it is decided on or by; `kind`, which is `hitting` where it is reached at any time through `window_end`, from `window_start` where one is stated and otherwise from the as-of, `level_at_date` for a level on `window_end`, and `scheduled_decision` for a decision scheduled on it; and `resolution_source`. A band is a level between two thresholds, on a date or at a scheduled decision: `comparison`, `>` or `>=`, and `value` are its lower end, and `upper_comparison`, `<` or `<=`, and `upper_value` its upper end (1.2).
- **Its chances** are what the analyst states about how likely it is (1.2), each with its basis: `chances`, a list, each `{"given": [...], "given_not": [...], "value": ...}`: a figure from 0 to 1 with the propositions it is given, some true and some false, each by a proposition's id or by a market's, for the proposition it asks (1.2). A chance between two rungs of a ladder is given the later rung and not the earlier, or the first rung alone for a date before it. In place of `value` it can name a `reading` of the ladder (1.2): `"even"`, `"hazard"`, or `{"calendar": [{"date": "YYYY-MM-DD", "weight": ...}]}`, the dates on which it can happen, a date with no `weight` counting as 1; and where a rung is a market, `through`, the day its question counts through, `{"market:{venue}:{id}": "YYYY-MM-DD"}`. A chance stated on its own can `follow`, by a stated `responsiveness`, a market the proposition asks, `"follow": "market:{venue}:{id}"`, or the weight a holding's price implies on it (1.6), `"follow": "name:{TICKER}"`, a holding whose values are stated in its worlds.
- **What is said of its worlds,** on an event or a way: `baseline`, on an event, naming the world its other worlds are measured from (1.1): `yes` or `no` for a side left whole, or the id of a way; `priced_in`, the weight today's prices carry (1.5), a figure, `{"value": ..., "note": "..."}`, or a market read live, `{"value": {"market": "market:{venue}:{id}"}, "note": "..."}`; and `episode`, a past window whose co-movement stands in for the ordinary one in that world (1.3), `{"value": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, "note": "..."}`, ending on or before the as-of. `priced_in` and `episode` each carry their basis beside their value, as every assumption does (2).

### 2.2 Links

| Link | From, to | What it says | Stated | Derived |
|---|---|---|---|---|
| `holds` | Portfolio, Name, Market or cash | the member holds it | for a name, the `ticker` of the security held and one of `quantity`, `dollars`, `weight`, as given; for cash, `cash`, its amount in its currency; for a market's contract, its `side`, `yes` or `no`, and `quantity`, the number of contracts | the other two; the shares stay fixed and the dollars move with the price (3.1) |
| `looks_through_to` | Name, Name | a fund holds this name | | `weight`, the fund's exposure to it per dollar of the fund; `dollars`; the date of the fund's row |
| `beta_to` | Name, Factor | the name moves with the factor | | `beta` |
| `quotes` | Metric, Factor | the metric is one factor's price, in the metric's own unit | | the metric's level in each world, moving as the factor's placed price does |
| `watches` | Portfolio, Proposition | the proposition is an event of this ledger, decided by the horizon's end: its `window_end` is on or before the as-of plus `horizon_days` | | |
| `implies` | Proposition or Market, Proposition or Market | wherever the first is true, so is the second; a market is named for the proposition it asks (1.2) | | whether the fields decide it; what differs between its two texts, where they describe different outcomes |
| `and`, `or`, `then` | Proposition, Proposition | a composite and one of its two parts | `step`, on `then`: 1 for the part that comes first, 2 for the part that follows | whether the fields decide it |
| `way` | Proposition, Proposition | the second is one way the first can turn out | `side`, where the first is an event: `yes`, its Yes side, or `no`, its No side | whether the fields decide it; its place among its parent's ways, in the order they were linked |
| `asks` | Proposition, Market | the market asks this proposition (1.2) | `side`: `yes`, the market's Yes, or `no`, its No; `taken`: whether its price is taken as the probability, `{"value": false}` with its basis where it is not | the proposition's bid, ask and price from it (1.2); what differs between the market's rules and the proposition, where they describe different outcomes |
| `evidence` | Proposition, Source | the page bears on it | `passages`, a list, each quoted word for word | |
| `consequence` | Proposition, Factor or Name | what a world does to a price | One figure, `shock`, `{"value": ..., "against": ..., "note": "..."}`: its value, its basis beside it (2), and `against`, which is `baseline` where none is named, or `what_it_follows` (1.3). Against the baseline, the shock is the price's whole move: how far the price stands in the world from where it stands in the baseline, as a fraction of its price in the baseline; for a factor quoted as a yield, how far the yield stands from the baseline's, in percentage points. Against what it follows, the shock is the price's residual: its further move from the value it would otherwise have in that world (1.3), as a fraction of that value; for a factor quoted as a yield, in percentage points of the yield. | the price's level in each world (1.5); a name's residual (1.4) |
| `worth` | Proposition, Name | what a holding is worth in a world | the world, `world`, where the proposition is an event: `yes`, its Yes side, or `no`, its No side; on a way, the way is the world. And the `value` per share, with its basis (1.6), as one of: an amount in today's dollars, `{"amount": ...}`; cash paid on a date, `{"cash": ..., "currency": ..., "date": ...}`, shares of another name, `{"shares": {"name": "name:{TICKER}", "quantity": ...}}`, or both; or the holding's price on an earlier date with the return since that the analyst states, as a fraction, `{"date": ..., "return": ...}`. | the value today; the weight the holding's price implies (1.6) |
| `cites` | a stated fact, Source | where the fact was read | | |
| `basis` | a derived field, Computation | what produced it | | |
| `about` | Note, anything | what the note concerns | | |

What a fund holds is read from its sources (3.1). What the analyst knows of a fund beyond them, it states as a shock on the fund's price (1.6).

### 2.3 What the store derives

| When this exists | The store derives |
|---|---|
| a `Portfolio` with holdings | each holding's price as of `asof` (3.1); what each holding is; the look-through, as `Name` objects and `looks_through_to` links; each name's `exposure`; `value`, and its weight by company, by industry and by country; every beta; `dollar_beta`; `volatility`; each name's price as a `Metric` |
| a `Factor` | how it is built from the factors before it; its `largest_month`; every beta again; its proxy's price as a `Metric` |
| a `Metric` | a change to its `level` moves every figure read from it |
| an `asks` link | its structure's ranges and probabilities (1.2); what differs between the market's rules and the proposition, where they describe different outcomes |
| an `implies`, `and`, `or`, `then` or `way` link | whether the fields decide it; its structure's ranges and probabilities (1.2); for an `implies`, what differs between its two texts, where they describe different outcomes |
| a chance | its structure's probabilities (1.2); a chance that follows a market moves with it |
| a `watches` link | the event's worlds, each with its probability (1.2) |
| a `consequence` or a `worth` on a watched event or its ways | each world's factors, each company's residual, where each stands against today, and each holding's value there (1.3 to 1.7), a held contract's by what it pays (1.9); the row (9) |
| several events named together | their joint worlds, each with its probability and what it does (1.9) |

`remove` takes what the analyst created, and the factor a new store starts with (3.2). Removing a link removes what is derived from it, and removing an object removes its links the same way. A stated fact keeps its page, an assumption keeps the note it rests on, and a chance keeps the propositions it is given. A removal that would break one of these is refused, naming what depends on it.
