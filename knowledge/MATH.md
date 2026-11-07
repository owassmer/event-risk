# Mathematics

Three identities and one propagation rule. Every number in the store is one of them, applied to
the objects and links you build and the assumptions you state. `p` is an event's probability; `D`
its full impact, the portfolio's value with the event against without it, in dollars; `g` the
portfolio's dollar beta to each channel, a factor on the page; and `u_E` the event's shock to each
channel, as a return.

## 1. Priced in

An event has one probability `p`, its point (section 4). `V_yes` and `V_no` are the portfolio's
value with the event and without it. Today's prices weight the two by `p`, which is what it means
for the event to be priced in:

`V = p * V_yes + (1 - p) * V_no`

This is the one assumption behind the split. Nothing observable says what prices embed apart from
what the markets and the evidence say, so the split uses the event's one `p`.

The full impact is `D = V_yes - V_no`, so

- `pnl_yes = (1 - p) * D`: when the event happens, the portfolio moves by the part not priced in;
- `pnl_no = -p * D`: when it does not, the portfolio moves the other way by the part that was;
- `pnl_yes - pnl_no = D`.

`emove = 2 * p * (1 - p) * abs(D)` is the expected move, the expected absolute move at resolution,
and it ranks the ledger. `worst`, the worst case, is the more negative of `pnl_yes` and `pnl_no`.

## 2. Impact

The portfolio's dollar beta to channel `c`:

`g[c] = sum_h V_h * b_h[c]`

over the holdings as given: `V_h` is holding `h`'s dollars and `b_h` its beta vector on the
orthogonalized channels, fitted on its own history, a fund's on the fund's (`ONTOLOGY.md`, channels
and betas). An event's full impact is

`D = g . u_E + sum_i e_i * d_i`

where `e_i` is the dollars of name `i` after look-through, its look-through exposure, held directly
and through funds, and `d_i` is the residual move of a name the event moves on its own news, its
move beyond what its betas give, from a consequence on its price (section 3), and zero otherwise.
A company's residual move reaches every dollar of it, through a fund as directly. Every event is a
vector in the same basis. The primary channel is `argmax_c abs(g[c] * u_E[c])`.

`by_name` is each name's contribution to `D`, the sum of its `parts`: what is held of it directly
moves with its own betas, `e_i,direct * (b_i . u_E)`; what a fund holds of it moves with the fund,
`e_i,f * (b_f . u_E)`, the fund's move split across what it holds by weight; and every dollar of it
carries its residual move, `e_i * d_i`. The largest are the event's largest contributors.
`by_holding` is each holding's contribution: the dollars it reaches at each name times the move of
those dollars. A contribution `s` comes to `(1 - p) * s` if the event happens and `-p * s` if it
does not, its `if_yes` and `if_no`, so the contributions sum to `pnl_yes` and to `pnl_no`.

## 3. The shock vector `u_E`

`u_E` comes from the event's consequences. A consequence links the event to a metric that tracks a
channel or a name, with one figure you state, what it means, and its basis:

- `gap`, the shock: `E[X given E] - E[X given not E]` at the end of the event's window, the full
  difference between the two worlds, which does not depend on how likely the event is: today's
  prices already carry its probability, and the split at `p` (section 1) is what takes it out;
- `level_if_happens` or `level_if_not`, for a traded metric: the metric's level at the window's end
  in that world.

A metric no market prices states the shock. For a traded metric at its current level `c`, the
pricing identity converts a level to the shock and places both worlds:

- from the level if it happens, `gap = (L_yes - c) / (1 - p)`;
- from the level if it does not, `gap = (c - L_no) / p`;
- `L_yes = c + (1 - p) * gap` and `L_no = c - p * gap`.

The levels shown and the dollars are one calculation. Whatever moves the metric in both worlds is
no part of the shock, and an event that changes nothing has a shock of 0. A level the identity
cannot move, the level if it happens at `p = 1` or the level if it does not at `p = 0`, gives no
shock, and the reason says to state the gap. History enters the figure through its basis: what
past episodes did, from the passages you quote or the window returns you read.

The `tracks` link converts the shock to a return on what the metric tracks, its raw shock: divided
by `c` for `price`, multiplied by `-duration / 100` for `yield(duration)`.

- **A channel's raw shock** enters the orthogonalized basis in channel order: its residual shock,
  `u_E[c]`, is its raw shock less what the earlier channels' residual shocks imply through its
  coefficients on them.
- **A channel no consequence sets** has a residual shock of 0, so it moves with the earlier
  channels by its coefficients on them.
- **A consequence on a name's price** sets that name instead of a channel. The raw shock at `price`
  is the name's whole move, so its residual move `d_i` is that move less `b_i . u_E`, the part its
  betas already give. The betas carry every name you state no consequence for.

An event's consequences set each channel, and each name, through one metric.

Illustration: X at 70, a traded price tracking a channel at `price`. You state that X ends the
window at 80.5 if the event happens. At `p = 0.25`, the shock is `(80.5 - 70) / 0.75 = 14`, the
channel's raw shock `14 / 70 = 0.2`, and `L_no = 70 - 0.25 * 14 = 66.5`.

## 4. Propagation

A proposition carries an interval `[lo, hi]`, the range markets imply; a point `p`, its
probability; and `coverage = 1 - (hi - lo)`.

### A market's price

A market's price is the one its venue displays (`ONTOLOGY.md`, markets), and a `quoted_by` link
takes it as the probability of what the market asks. Each side is paid for in full until the close,
so a price `q` allows the risk-neutral probability anywhere in `[1 - (1 - q) * (1 + k), q * (1 +
k)]`, within `[0, 1]`, where `k` is the interest the money in a contract forgoes until the market
closes, net of what the venue pays on a position: at any probability in that range, buying either
side at the price earns no more than that interest would.

A `quoted_by` link from A to M claims that M asks exactly A: M resolves YES exactly when A is true,
or, under the complement, exactly when A is false. It gives A the interval `[bid, ask]` and the
observation `q`, the displayed price; or `[1 - ask, 1 - bid]` and `1 - q` under the complement. A
side of the book with no orders bounds nothing. A market that asks a different question enters
through a proposition you write stating what it asks, linked `quoted_by` it and related to the rest
by the breakdown.

### The breakdown and the interval

The breakdown is yours: `implies`, `and`, `or`, and `then`, each a link between two propositions,
written so the relation is exact.

An `implies` link from A to B claims that every world where A is true is one where B is. The fields
decide it where they can:

- a threshold on a metric implies every weaker threshold on the same metric, resolution source, and
  window, strict and non-strict kept distinct: `x >= k` implies `x >= j` and `x > j` for `j < k`,
  and does not imply `x > k`;
- a level on a date implies hitting that level over any window containing the date;
- hitting by an earlier date implies hitting by a later one.

The judge checks it otherwise. `and`, `or`, and `then` link a composite to its two components; a
composite of more nests them. Logic carries the markets' intervals through the breakdown, applied
to a fixed point:

| Link | Rule |
|---|---|
| A `implies` B | `hi_A = min(hi_A, hi_B)`, `lo_B = max(lo_B, lo_A)` |
| A = B `and` C | `lo = max(0, lo_B + lo_C - 1)`, `hi = min(hi_B, hi_C)` |
| A = B `or` C | `lo = max(lo_B, lo_C)`, `hi = min(1, hi_B + hi_C)` |
| A = B `then` C | `lo = 0`, `hi = hi_B` |

A composite of `and` or `then` implies each of its components, and each component of `or` implies
its composite, so each such link also bounds as that `implies` does. Each bound is what the claim
says if it holds, at full strength whatever its check: the check tells you whether to stand by the
claim.

The interval is what the markets alone say. Bounds that cross mean a claim or a market is wrong:
the proposition has no interval until you correct it, and `check()` lists what set each side.

### The point

The point is the price of a market that asks the proposition (where several do, the one with the
most depth); otherwise it is your derivation of it; otherwise, the probability you state as its
`chance`. A derivation names the propositions the point reads and states the conditional
probabilities its rule needs, each tied to the claim it answers and carrying its basis, and the
store evaluates it by total probability, each input at its own point. Several markets can enter one
derivation, and moving any of them moves the point. For a proposition E:

| E reads | Point | Stated |
|---|---|---|
| B, which E implies | `P(E given B) * p_B` | `P(E given B)` |
| A, which implies E | `p_A + P(E given not A) * (1 - p_A)` | `P(E given not A)` |
| A and B, A implying E and E implying B | `p_A + P(E given B and not A) * (p_B - p_A)` | `P(E given B and not A)` |
| D, which neither implies E nor is implied by it | `p_D * P(E given D) + (1 - p_D) * P(E given not D)` | both |
| B, where E = B `and` C, or B `then` C | `p_B * P(C given B)` | `P(C given B)`, for `then` C following B |
| B and C, where E = B `or` C | `p_B + p_C - p_B * P(C given B)` | `P(C given B)`, 0 when they cannot both happen |
| B, where E = B `or` C | `p_B + (1 - p_B) * P(C given not B)` | `P(C given not B)` |

"Implies" here is your links: `implies`, and the implications of `and`, `or`, and `then` above,
followed through any chain of them. A derivation reads across composites by the first three rows.

A market the derivation does not read still bounds the proposition, and forecasts nothing of its
own: which markets the point reads is your choice, made visible. A point outside the range its
markets imply means an input is wrong: the point stays where its inputs put it, and `check()` lists
it, so you find and correct the input. A derivation whose arithmetic leaves 0 to 1 gives no point,
and says why.

A proposition no market bounds has the interval `[0, 1]` and coverage 0, and a market on what drives
it can still set its point through a derivation. Where no market asks the proposition or anything in
its breakdown, you state probabilities: for the smaller claims it breaks into, each narrower than
the event and so small and resting on figures you found, which a derivation carries to its point; or
for the proposition itself, when it is already that narrow. The point is derived for each watched
proposition and each proposition a derivation reads.

## 5. The constants

The fixed values the store's derivations use, each where `ONTOLOGY.md` describes its derivation.

| Constant | Value | Why |
|---|---|---|
| the spread up to which Polymarket and Kalshi display the midpoint | 10 cents | wider, they display the last trade: Polymarket documents ten cents, and Kalshi names no width |
| depth | the top 5 levels of a side | the part of a book that is standing rather than fleeting |
| bid and ask at a past as-of | the history price less and plus 0.01 | a history keeps trades, not books, and a cent is a liquid contract's spread |
| a dispute | a check below 0.5 | the judge reads the claim as false |
| horizon | 180 days | the one period every event is watched over, the longest over which two-year betas and today's prices describe the same world |
| betas | 504 trading days, 250 at least | two years: enough observations per coefficient, while short enough that a name's betas are the current ones; one year is the floor for a stable fit |
| a channel's largest month, its largest one-month move | 21 consecutive sessions | a month of trading |
| look-through floor | 0.0025 of the fund | below it, a constituent is a negligible part of how the fund moves |
| a basket | weights summing within `[0.5, 1.5]` | a plain basket sums near one; a levered or inverse fund sums near two, three, or a negative number, and is not a basket of its constituents |
