# MATH.md

## 1. Mathematics

The system's numbers come from one construction, completed in four steps.

**The construction.** An event turns out in one of a set of worlds. The worlds exclude one another, and together they cover everything that can happen. Each world has a probability. Each values every holding at what it would be worth today if that world were known to be the one that happens. Every figure a row shows is read from those worlds.

**The four steps.** The analyst states what it has a view on. The store completes the rest in four steps, each from statements of its own kind:

1. the worlds' probabilities, from the markets the analyst takes and the probabilities it states (1.2);
2. what each world does to the factors, from the shocks the analyst states on factors (1.3);
3. each company's residual in each world, from the shocks it states on that company's price (1.4);
4. where each world stands against today, from what is priced in (1.5).

The steps run in that order, each building on the ones before it.

Each holding is then valued in each world (1.6, 1.7). The distribution of outcomes within a world is drawn around it (1.8). Several events can be read together as one set of worlds (1.9). What cannot hold together is listed (1.10).

### 1.1 Worlds

**An event's worlds.** An event is a proposition the portfolio watches. The worlds in which it is true are its Yes side, and the others are its No side. A side is one world, or the ways the analyst gives it (2.2). Ways are propositions whose outcomes lie within the side, exclude one another, and together make up the side. A way can have ways of its own, by the same rule. An event's outcomes are its sides and all its ways. Its worlds are the outcomes the analyst leaves whole.

Each world `w` has a probability `p_w` (1.2), and the worlds' probabilities sum to 1. The event's probability, `p`, is the sum over its Yes side.

**The baseline.** One world of each event is its baseline: the world every other world's prices are measured from (1.3). The analyst names it. Like every world, it is all that its definition holds: a side left whole is every way that side can come about, so the side on which an event has not happened by the end of its window holds its happening later.

**A world's P&L.** `V_h(w)` is holding `h`'s value in world `w`: what it would be worth today if `w` were known to be the world that happens. `V_h` is its value today, at its observed price. Every figure is a change in today's value. The portfolio's P&L in `w` is

`A_w = sum_h (V_h(w) - V_h)`

**A row's figures** are read from the worlds.

- **Yes** (`pnl_yes`) is the mean of `A_w` over the Yes side, each world at its probability: `sum over Yes of p_w * A_w / p`. **No** (`pnl_no`) is the same over the No side, divided by `1 - p`.
- **Full impact,** `D = pnl_yes - pnl_no`: what the event happening is worth to the portfolio against its not happening.
- **Expected value,** `mu = sum_w p_w * A_w`: what the portfolio gains or loses on average over the worlds. Every placed price averages to today's price at the weights (1.5). So where today's prices carry each world at our probability, the expected value comes from holdings valued by their terms or by values the analyst states (1.6), and is zero without them.
- **Volatility,** `sqrt(sum_w p_w * (A_w - mu)^2)`: the standard deviation of what the event's resolution does to the portfolio, over its worlds.
- **Worst outcome:** the lowest `A_w` among the worlds with a probability above zero, with its world named.

From these definitions, `pnl_yes = mu + (1 - p) * D` and `pnl_no = mu - p * D`. With two worlds, `volatility = sqrt(p * (1 - p)) * abs(D)`. With ways, the variance has two parts, the part between the two sides and the part within each:

`volatility^2 = p * (1 - p) * D^2 + p * spread_yes^2 + (1 - p) * spread_no^2`

A side's `spread` is the standard deviation of its ways' P&L given that side, and a side left whole has a spread of 0. The part within each side is what ways add to one shock on the event.

**An event with ways is one row.** Its figures are over all its worlds, and its ways are shown under it, each with its probability and its P&L.

### 1.2 The worlds' probabilities

This is the first step. It reads the markets the analyst takes and the chances it states.

A proposition carries the range the markets imply for it, `[lo, hi]`, and its probability, `p`.

**A market's price** is the midpoint of its best bid and ask where both sides have quotes (3.3). Its level reflects traders' beliefs about the probability, the interest the money in a contract forgoes until the close, and the premium hedgers pay to shed a risk they hold. Each side is paid for in full until the close, so a price `q` is consistent with a risk-neutral probability between `q * (1 + k)` and `1 - (1 - q) * (1 + k)`, within 0 to 1, where `k` is that interest net of what the venue pays on a position. The hedgers' premium moves the price further from the probability of the event itself. The analyst reads a price with that range in mind, and the store takes the price as it stands.

**What a market asks.** A market asks a proposition: the one its own question and rules define, true where the market resolves Yes. The analyst relates it as it relates any proposition: a chance can be given it, an `implies` can run to or from it, and it can be a rung of a ladder. An `asks` link says that a proposition of the analyst's is the one a market asks: the market's Yes is the proposition being true and its No the proposition being false, or the reverse where the link names the market's No side.

**A market whose price is taken.** That a market asks a proposition says what its contract resolves on. That its price is the probability of what it asks is a judgment about its traders, which the analyst makes market by market. Linking or naming a market takes its price as that probability. Where the analyst judges otherwise, it says so on the market's `asks` link, with its reason. What it asks then rests on what else bears on it, or on a chance the analyst states that follows the market (below). Every market named is shown with its price, bid, ask and depth, and the taken ones bound what they ask, so the analyst can take one book and doubt another on the same question.

A taken market gives its proposition its bid and ask as a range, and its price `q`: `[bid, ask]` and `q`, or `[1 - ask, 1 - bid]` and `1 - q` through its No side. The book is kept as it was read.

**One set of probabilities.** A proposition is true in some of the outcomes that can happen and false in the others. The propositions the analyst relates to one another, by a link or by a chance, form a structure. Together they divide what can happen into cases, and in each case every proposition of the structure is true or false. One set of probabilities over those cases, summing to 1, gives every proposition its probability: the sum over the cases in which it is true.

Each thing the analyst states about a structure is a condition on that one set:

- a link says which cases can happen;
- a market it takes puts its proposition's probability between the market's bid and ask, and the deepest market taken on a proposition gives the price it stays near;
- a chance fixes one probability as a share of another.

The store reads them together, and returns for each proposition what they allow.

**The links** are the analyst's breakdown.

- `implies`, from A to B: B is true in every case where A is.
- `and`, `or`, from a composite to each of its two parts: the composite is true where both parts are, or where either is. A composite of more parts nests them.
- `then`, from a composite to each of its two parts, each link stating its `step`, 1 for the part that comes first and 2 for the part that follows: the composite is true where the first happens and the second follows it, so it implies each part. How often the second follows the first is the chance of the composite given the first.
- `way`, from a proposition, or from one side of an event, to each way it can turn out: the ways exclude one another and together make it up. A way can have ways of its own. Where A implies B, the outcomes B adds to A are a way of B beside A.

A proposition's fields count its outcomes as they are stated, each threshold as a number and each window as its dates. So the analyst writes them as the quantity behaves: for a quantity that moves in steps, the threshold and comparison at a value the quantity can take, so the fields count the outcomes that qualify (`x > 2.5` on a count is `x >= 3`); and the kind by how the quantity is decided. Markets that resolve on the same outcomes ask one proposition, however each words its question.

Two propositions on one metric and resolution source are compared by their fields. A implies B when B's threshold allows every value A's allows, each comparison read as written, strict or not (`x >= 25` implies `x >= 20` and `x > 20`; `x > 25` implies `x >= 25`), and A's time lies within B's: a level on a date, or a decision on it, lies within that date and within a window of hitting that contains it, and a window of hitting lies within a window of hitting that contains it. On an occurrence, which has no threshold, A implies B where A's time lies within B's. Between such propositions the fields decide an implication, and `check()` lists a claim they contradict with the values compared (6).

**A chance** is the analyst's statement of how likely one proposition is where others hold, with its basis (2). "The chance of E, given B and not A, is `c`" says that of the cases where B is true and A is false, those where E is true as well carry the share `c` of their probability:

`P(E and B and not A) = c * P(B and not A)`

A chance carries other propositions' probabilities to this one, so a market on what an event depends on reaches the event through the chances the analyst states. A way's chance given its parent is the way's share of the parent. Which propositions a chance is given is the analyst's choice, since how a situation's markets bear on an event depends on its particulars.

Illustration: E is a narrower case of B, so E implies B. The chance of E given B, `c`, gives `p_E = c * p_B`. D bears on E, with no link between them. The chance of E given D, `c1`, and given not D, `c2`, give `p_E = p_D * c1 + (1 - p_D) * c2`.

A proposition that nothing else in its structure bears on takes a chance stated on its own, which is its probability.

**What the store returns** for each proposition of a structure:

- **The range the markets imply,** `[lo, hi]`: the lowest and the highest probability it has in any set of probabilities that keeps every link and puts each proposition between the bid and ask of every market taken on it. It is what the markets say through the breakdown, before any chance of the analyst's. A market on a narrower proposition lifts the floor, and one on a broader proposition lowers the ceiling. A proposition that no taken market bears on has the range `[0, 1]`.
- **Its probability,** read from the set of probabilities that keeps every link and every chance, puts each proposition between the bid and ask of every market taken on it, and lies nearest the taken markets' prices. Each proposition with a taken market is measured from the price of its deepest one, in that market's half-spread, and the sum of those distances squared is least. So a tight book moves little, a wide one takes up the difference, and a book whose bid and ask meet holds its proposition at its price.
- **The range the statements leave,** where they leave a proposition's probability open: the lowest and the highest it can have with every link and chance kept and each proposition that has a taken market at its probability. It is returned with what would settle it: each smallest set of chances given other propositions that would, and each proposition it rests on that is open itself. The figures that need the probability wait for it.

Taken markets on related propositions are read together this way. Where an event and each of its ways has a market, the prices seldom add up to the cent, and each proposition reads a little off its own market's price, within its bid and ask. Where a chance ties an event to another proposition and each has a taken market, both markets inform both probabilities. The books are kept as they were read.

An event's worlds are propositions of its structure (1.1), and each world's probability is read from it.

Illustration: an event T implies a broader claim R, and a narrower claim N implies T. R's market stands at 0.28 to 0.33, with a price of 0.305, and N's at 0.18 to 0.22, with a price of 0.20. The markets imply 0.18 to 0.33 for T. The analyst states the chance of T given R as 0.62. Each price taken alone would put T at 0.189, below N at 0.20, which implies it. Read together, R is 0.3116 and T and N are each 0.1932, each market's proposition within its bid and ask.

**Where the books cannot hold with what the analyst states.** Books on related propositions can stand further apart than their bids and asks allow, most often for a moment as prices move. The analyst's links and chances stand, and the books give way: each is stretched past its bid or its ask as far as the statements need. Depth is the money it takes to move a book's price (3.3), so the deepest book gives way least. Its stretch is made as small as the statements allow, then the next deepest's, and so on, and the shallowest book takes up what is left. Books of equal depth share it by the least sum of squared stretches, each in its half-spread. The probabilities are then read within the stretched books, and `check()` lists each stretched book with how far it was stretched (1.10). Where the books cannot all hold through the links alone, the range the markets imply is read within them, each stretched by the same rule as far as the links alone need.

Links and chances that cannot hold among themselves leave the structure's probabilities waiting until one is restated, and `check()` lists them (1.10). A chance given some propositions is a statement about the cases where they hold, so it cannot hold together with links and chances that leave those cases no probability.

Illustration: T implies R, as above, and the analyst states the chance of T given R as 0.62. R's market stands at 0.28 to 0.33 with $14,000 at its quotes. A market on T itself stands at 0.24 to 0.25 with $40 at its quotes. No probability for T lies within both books. R stays within its quotes, at 0.33, T reads 0.2046, and T's book is listed as stretched by 0.0354.

**A chance that follows a market.** Where the analyst's view of a proposition differs from a market it has linked to it, it can state two things: its chance now, and how far it would follow that market's later moves, its responsiveness `r`, 0 or more. The store reads the market's probability at the moment the chance was stated, `q_then`, from the book it kept, and carries the chance forward as the market moves. The market then informs the proposition through that chance, in place of being taken.

`logit(c_now) = logit(c_then) + r * (logit(q_now) - logit(q_then))`, with `logit(x) = ln(x / (1 - x))`

A responsiveness of 0 keeps the stated level. A responsiveness of 1 keeps a constant ratio between our odds and the market's. Taking the market's price is the case `c_then = q_then` at 1. As the market goes to 0 or to 1, so does the chance, for a responsiveness above 0. The analyst restates both when its understanding changes. The chance moves with each priced reading of the market, and shows the time of the one it rests on. The weight a holding's price implies on an event (1.6) can be followed in the same way: the chance moves as the holding's price moves, read through the values stated for it now.

Illustration: the market stands at 0.40 when the analyst states 0.30, following half of its moves. If the market goes to 0.50, ours is 0.344; to 0.25, 0.233; to 0.10, 0.149.

**A ladder** is a set of propositions on one metric and resolution source that differ only in their date, or only in their threshold. Each rung's threshold is at a value the quantity can take, and each market joins the ladder through the rung it asks. The fields order the rungs: a deadline implies every later one, and a threshold implies every lower one on its date. So a ladder is a structure, read as any other, whether or not the analyst links its rungs. A deadline reading states that the proposition and its rungs concern the same occurrence or threshold, counted from the same start, by their respective dates. The store places them by those dates: the earlier rung implies the proposition, and the proposition implies the later rung. Those relations also determine the range the markets imply; the chosen reading determines the chance between them. A market can be a rung: a reading between rungs then names the day its question counts through, which is in its rules and can fall before its close. Where neighbouring rungs' prices stand slightly out of order, each inside its own bid and ask, the rungs' probabilities are the ordered set nearest them. Read whole, a ladder gives when an event happens, or how a quantity is distributed.

- *Timing.* A ladder of deadlines, "by `t_1`", "by `t_2`", gives the probability `F_j` that the event has happened by each. A proposition "by `t`", with `t` between two rungs, lies between them. Its probability comes from the chance of "by `t`" given "by `t_2`" and not "by `t_1`": the share `s` of the probability between the rungs that falls by `t`. The analyst names the reading that gives `s`:
  - *even through time:* `s = (t - t_1) / (t_2 - t_1)`;
  - *constant hazard,* a steady chance per day for what has yet to happen: `1 - F(t) = (1 - F_1) * ((1 - F_2) / (1 - F_1)) ^ ((t - t_1) / (t_2 - t_1))`, and `s = (F(t) - F_1) / (F_2 - F_1)`, with `F_1` and `F_2` the two rungs' probabilities as the structure gives them;
  - *by the calendar:* the analyst names the dates on which it can happen, with a weight on each where it holds some likelier than others. `s` is the share of the weight on the dates after `t_1`, through `t_2`, that falls on or before `t`. Unweighted dates count equally.

  Before the first rung, the same readings run from the as-of, where the probability starts at 0: the chance of "by `t`" given "by `t_1`" is `(t - asof) / (t_1 - asof)` even through time, and `(1 - (1 - F_1) ^ ((t - asof) / (t_1 - asof))) / F_1` at a constant hazard. Past the last rung, the analyst states the chance of "by `t`" given not "by `t_n`".
- *Bands.* A band is a proposition with a lower and an upper threshold on the ladder's metric, on a date or at a scheduled decision, each end strict or not, so its fields place it between two rungs. It is the way the lower rung can turn out beside the higher one, and its probability is the lower rung's less the higher's. With the two open bands beyond the ladder's ends, the bands exclude one another and together cover every outcome, so they can stand as the worlds of whichever rung the analyst watches, the bands above it on its Yes side and those below it on its No side. A side stays whole until the analyst states something of one of its bands as it would of a way (a shock, a value, a weight, an episode, or that it is the baseline); the side is then its bands, and the store mixes them (1.1). A centre or a spread of the quantity itself takes one more statement, where the quantity falls within each band and beyond the outer rungs, which the analyst gives where it wants one.

A reading stays current with the rungs' prices.

Illustration: "by `t_1`" at 0.20 and "by `t_2`" at 0.50, for a date two thirds of the way between them. Even through time gives 0.400; constant hazard gives 0.415. Rungs "above 2.5" at 0.80, "above 3" at 0.45, and "above 3.5" at 0.12 give the bands 0.20, 0.35, 0.33, and 0.12.

**Where no market asks.** The analyst states chances: for the smaller claims a proposition breaks into, each narrow enough to rest on figures the analyst found, which the links and the chances given them carry to the proposition; or for the proposition itself, where it is already that narrow. A market on what drives a proposition reaches it through a chance given that driver.

### 1.3 What each world does to the factors

This is the second step. It reads the shocks stated on factors.

**A consequence** links an event, or a way, to a factor or a name (2.2). It carries one figure the analyst states, the shock, with its basis (2). The shock is how far the price stands in a world from where it stands in the baseline, both seen from today: its value today if that world were known to happen, against its value today if the baseline were. It is a fraction of the price in the baseline, and it is the full difference between the two worlds: the store takes out what today's prices already carry of it (1.5). It is the difference the world makes: what happens to the price in every world cancels out of it, and a world that leaves the price where the baseline has it has a shock of 0.

A factor whose proxy is a bond fund can be quoted as the yield that fund pays (2.1). A shock on it is then in percentage points of that yield, and gives the factor's compounded move against the baseline: `ln(1 + x) = -duration * shock / 100`, with the proxy's duration.

**The whole move, or the residual.** A factor's response comes from its coefficients on the factors before it (3.2). A company's response comes from its betas to the factors (1.6). A fund's value comes from its response and what the companies it holds do beyond theirs, or from its terms where they value it (1.6). A price's residual in a world is its further move from the price or value those give there, as a fraction of it.

So a shock states, with its basis, the price's whole move against the baseline, as above, or its residual.

On a factor, a shock fixes its whole move or its residual, and the store completes the factors left unstated from every factor move the world states, together (below). On a factor quoted as a yield, either is in percentage points of that yield, and gives the move of its proxy's price as above. For a factor or a company the two compound: with `x` the whole move, `a` the residual, and `b_j` its coefficient or beta on each factor its response rests on, whose own residual is `a_j`, `1 + x = (1 + a) * prod_j (1 + a_j) ^ b_j`, each a move of a price.

History enters the figure through its basis: what past episodes did, from the passages the analyst quotes or the window returns it reads.

**Which shock applies in a world.** A consequence on the event gives its shock to every world on the other side from the baseline. A consequence on a way gives its shock to that way and the ways under it. In each world, each factor and each name takes the shock stated nearest above that world.

**What an unstated factor does.** In each world, a factor left unstated moves as it ordinarily would with the factors that are set there. A completed move is many daily moves compounded, so that co-movement holds between compounded moves. With `S` the set factors and `x_S` their moves, the others, `U`, take

`ln(1 + x_U) = cov(U, S) * cov(S, S)^-1 * ln(1 + x_S)`

from the factors' covariance over completed moves (3.2). A stated residual is a condition of the same kind, on the factor's residual in place of its whole move, and each factor whose whole move is not stated takes its mean given every condition together. Residuals ordinarily move independently of one another, so a stated residual moves each later factor by its coefficient on it, and leaves the factors before it where the other statements put them, unless a factor after it has its whole move stated, which then speaks for both. A factor stated at -1 stands at zero in that world, and the factors left unstated there move with the other stated ones. Where the analyst names a past episode for the world (1.8), the same calculation reads the factors' covariance over that episode's sessions, measured as the ordinary one is (3.2). What a world does to the portfolio is the same in any order of the factors.

Every factor's move in a world, stated or filled, is measured from the baseline, so a world left without shocks stands where the baseline does.

Each world's factors are completed once, and every view of the worlds reads them as completed.

Every factor's compounded move, `ln(1 + x)`, then gives its compounded residual, `ln(1 + a)`, in factor order: that move less its regression on the earlier factors' compounded residuals (3.2). The compounded residuals are the world's `z_w`.

### 1.4 Each company's residual

This is the third step. It reads the shocks stated on names.

A consequence on a name sets that name's price in a world, and the factors stay as the world has them. Where the shock states the whole move, that is the name's move there. Where it states the residual, the name's whole move is the move it would have there left unstated, compounded with the residual:

`1 + s_{i,w} = (1 + r_{i,w}) * (1 + a_{i,w})`

where `s_{i,w}` is the whole move, `a_{i,w}` the residual, and `r_{i,w}` the move of the name left unstated, a company's response to that world's factors (1.6). A name left unstated has a residual of 0. With each world's whole move settled, the name's price is placed from its moves in all of them, once (1.5), whichever of the two each world states.

Illustration: a world moves the market -10%. A company with a beta of 1.8 to it moves `0.9^1.8 - 1 = -17.28%` by its response. The analyst states -0.25 on its price, as its whole move. Its residual there is `0.75 / 0.8272 - 1 = -9.34%`, and -0.0934 stated as the residual is the same world.

A consequence on a fund the portfolio holds sets that fund's value in the world (1.6).

The price of a factor's proxy is the factor's own (3.2), so a shock on it is stated on the factor.

A name quoted in another currency has its price in that currency, the price its market and the news quote, and a shock on it is a shock on that price. What the exchange rate does to its value in dollars is valued with it (1.6).

### 1.5 What is priced in

This is the fourth step. The first three give how far each world stands from the baseline. This one gives where each stands against today.

**The weights.** Today's price already puts a weight on each world, `q_w`, the weights summing to 1: today's price is the worlds' prices at those weights. So for a price now at `c`, with `s_w` world `w`'s shock on it and the baseline's 0, the baseline's price is

`L_base = c / sum_v q_v * (1 + s_v)`

and world `w`'s is `L_w = (1 + s_w) * L_base`. The world's move against today is `m_w = L_w / c - 1`. This is what "priced in" means: the more weight today's price puts on a world, the nearer it already stands to that world's price, and the less it moves when that world arrives.

Every price is placed this way, once: each factor, stated or filled (1.3), and each name and holding that stands at its stated shock or its response (1.4, 1.6). A factor quoted as a yield shows the yield its placed price implies, `y - 100 * ln(1 + m_w) / duration`, with `y` its yield today. The factors' placed moves `m_w`, each less its regression on the earlier factors' residuals, are the world's `ũ_w`. So the levels shown and the dollars are one calculation.

For two worlds, a shock `s` at weight `q` places them `s / (1 + q * s)` of today's price apart.

**The weights are an assumption.** What the prices of stocks and bonds carry is unobserved, so today's prices are taken to carry each world at our probability, `q_w = p_w`. Prices are then fair in our view, and everything placed has an expected value of zero.

Where the analyst holds that today's prices carry the event, or a way of it, at a weight other than its probability, it states that weight, with its basis: as the probability of a market that asks it, which the store then reads live, or as a figure. A stated weight stays as stated when the analyst's probability changes, and the row then carries an expected value (1.1). The worlds beneath a stated weight that are left unstated share what remains of it as our probabilities divide them, and the worlds outside every stated weight share what remains of the whole in the same way. For one weight `q` on the event, that is `q_w = q * p_w / p` on the Yes side and `(1 - q) * p_w / (1 - p)` on the No side. A weight read from a market moves with each priced reading, and shows the time of the one it rests on.

**For two worlds** this is the familiar split. With `D` the full impact and the event priced in at `q`:

`A_yes = (1 - q) * D`, `A_no = -q * D`, `mu = (p - q) * D`

A holding valued by its terms, or by values the analyst states, adds to each what it leaves at the weights beyond its value today (1.6).

With `q = p`: `pnl_yes = (1 - p) * D`, `pnl_no = -p * D`, and `mu = 0`. When the event happens, the portfolio moves by the part the market had not priced. When it fails, it moves the other way by the part it had.

**A stated value.** Cash the analyst states a holding pays in a world (1.6) is that world's value at any weights: a change in a probability or a weight moves where the store places a world, and the cash stays as stated. Shares of another name, and the company on its own, stand where that world's prices stand, and move with them.

Illustration, two worlds: an event at `p = 0.62 * 0.305 = 0.1891` with a full impact of -$3,597.56 gives Yes -$2,917.26, No +$680.30, and a volatility of ±$1,408.76.

Illustration, a level: a traded price at 70. The analyst states a shock of +0.20. At `p = 0.25`, the price stands at 80.00 if the event happens and at 66.67 if it does not: 20% apart, and 70 at those weights.

Illustration, ways: an event at `p = 0.30` with two ways, the first at 0.7 of it and the second at 0.3, so worlds at 0.21, 0.09 and 0.70. With the event not happening as the baseline, the first way's shock on a market fund now at 600 is -0.05 and the second's -0.20. The weights put `0.21 * 0.95 + 0.09 * 0.80 + 0.70 = 0.9715` of the baseline's price in today's, so the fund stands at 617.60 if the event does not happen, 586.72 in the first way, and 494.08 in the second. With $100,000 held in that fund, the worlds' P&L are +$2,933.61, -$2,213.07 and -$17,653.11. The row reads Yes -$6,845.08, No +$2,933.61, full impact -$9,778.69, worst outcome -$17,653.11 in the second way, volatility ±$5,924.50. One shock averaged over the two ways would give the same full impact and a volatility of ±$4,481.16.

### 1.6 A holding's value in each world

The four steps give, for each world, the factors' moves, each stated name's move, and where each stands against today. Each holding is then valued in each world, and the world's P&L is the holdings summed (1.1).

**A price's response.** A price moves with the factors percent for percent, at its betas (3.2), and the relation is the same at every level of the price. Across a completed move it compounds, as the factors' own co-movement does (1.3). So in world `w` a price left unstated stands against the baseline at

`1 + r_{h,w} = exp(b_h . z_w)`

with `b_h` its betas and `z_w` the world's factors (1.3). A company, a fund and an exchange rate each respond this way. Where a factor stands at zero in a world, a price takes its beta's share of that loss.

**Each holding at its price.** A holding is worth its shares at its price in each world, `V_h(w) = V_h * (1 + m_{h,w})`, on either side the portfolio holds it. Its price stands there at the shock a consequence sets on it, or at its response, placed once (1.5), and the paragraphs below say what moves a holding beyond that. At the weights, a placed price's worlds average to its value today.

**What moved it.** The portfolio's exposure to factor `c` in dollars, its dollar beta, is

`g[c] = sum_h V_h * b_h[c]`

over the holdings as given. `V_h` is holding `h`'s dollars, and `b_h` its betas: its full response to each factor's move, estimated on its own history (3.2), a stock's on the stock's and a fund's on the fund's, with cash at zero. In world `w` the portfolio's P&L is

`g . ũ_w + sum_i e_i * d̃_{i,w} + curve_w`

- `g . ũ_w` is each factor's dollar beta times its placed move (1.5).
- `e_i * d̃_{i,w}` is what each stated company does beyond its response, on every dollar of exposure to it. `e_i` is the portfolio's exposure to name `i` in dollars after look-through, held directly and through funds (3.1), negative where it is short. The dollars inside a fund whose move the analyst sets are left out of it, since that fund's whole move already holds them (below). `d̃_{i,w}` is the name's placed move less its response's.
- `curve_w` is the rest. For a price that moves by its response, it is what compounding adds beyond those two, as a bond's convexity stands beside its duration: small for moves of a few percent, and growing with the move and with the beta. For a holding valued by its terms, it is also what those terms give beyond its betas, as where a fund on one reference reaches zero.

The full impact has the same three parts, read from each side's worlds averaged at their probabilities, with `u_E` the difference between the two sides' placed factors.

Illustration: $10,000 of a holding with a beta of 2 to the market, and an event at 0.30 whose Yes side moves the market -40%. The holding stands at `0.6^2 = 0.36` of its baseline price if the event happens. Placed, the market moves -31.82% and +13.64% against today, and the holding -55.45% and +23.76%. If the event happens the holding loses $5,544.55: its dollar beta of $20,000 times -31.82% is -$6,363.64, and compounding adds +$819.08. If it does not, the holding gains $2,376.24: +$2,727.27, and compounding takes $351.04.

**A company held through a fund.** A company has one price in each world, and every dollar of exposure to it moves by that price, held directly or through a fund. So a shock stated on a company reaches each fund that holds it. The fund moves by its own response, placed. Each dollar it carries of the company also moves by what the company does beyond its response: its placed move less its response's placed move. The fund's remainder takes what is left (1.7).

**A fund whose move the analyst sets.** A whole move on a fund the portfolio holds replaces the value its response and the companies it holds would give it in that world, or the value its terms would. A residual adjusts the value the fund would otherwise have there, those companies' effects or its terms' value included. A fund the store places is then placed once, as a company is (1.4, 1.5). A fund valued by its terms is worth in every other world what its terms give there, so a shock on it changes its expected value (1.1) and no other world's value. The resulting move applies to the holding's dollars, which hold the dollars it carries of each company.

**A name quoted in another currency.** Its price in dollars is its local price times the exchange rate, in dollars per unit of its currency. The local price stands at its stated shock or its local response. The rate stands at the level of the factor whose proxy it is, in the direction the proxy quotes it, or else at the currency's own response. The dollar price moves by the two together, `(1 + s_local) * (1 + s_rate)`, and is placed once.

**A holding whose price rides on the event.** Where a holding's price is moved mainly by the event itself, the analyst states what the holding is worth in each world, each with its basis, and the store values it there at that:

`V_h(w) = shares_h * Y_{h,w}`

- `Y_{h,w}` is an amount per share in today's dollars, stated for every world, the baseline included: each is that world's value.
- Where the world pays cash on a date, the value is that cash discounted to today at its currency's government yield to that date, and converted to dollars at the currency's rate in that world (3.1).
- Where it pays shares of another name, the value is that many of the other name's shares at that name's value in the same world. A world can pay cash and shares together.
- Where the world leaves the company on its own, the analyst can state the value as the holding's price on a date before the event moved it. The store carries that price to today by the holding's response to the factors' moves since, compounded as every response is, at its betas as fitted on the history up to that date, times one plus the return the analyst states for what has changed in the company, with its basis. In each world, the company on its own moves by those same betas with the factors' placed moves there (1.5), and a cash amount stays as stated.
- The exposure to the holding through funds moves by the same whole move in each world, `Y_{h,w} / c_h - 1`.

The holding's price also says what weight the market puts on the event:

`implied = (c_h - N) / (Y - N)`

where `c_h` is its price today, and `Y` and `N` are the means of its stated values over the Yes side and the No side, each way at its share of the weight today's prices carry on its side. It is evidence about the event's probability, as good as the values it is computed from, and the analyst can follow it (1.2). Values that put it outside 0 to 1 contradict the price (1.10).

The holding's P&L in each world is what its stated value gives, at any probability the analyst holds. So the row carries an expected value wherever our probability differs from the weight the holding's price implies.

Illustration: a holding trades at $27.95. If the event happens it pays $35.00 a share in cash in six months, and the Treasury's six-month yield is 4%, so it is worth `35.00 / 1.02 = $34.31` a share today in that world. The analyst puts it at $23.84 if the event does not happen. Its P&L per share is +$6.36 and -$4.11. Its price implies `(27.95 - 23.84) / (34.31 - 23.84) = 39.2%`. At the analyst's probability of 30%, the holding's expected value is `0.30 * 6.36 - 0.70 * 4.11 = -$0.97` a share. At 39.2% it is zero.

**An option a fund holds.** An option reaches what it refers to at its delta (3.1), which is its value's response to a small move. In a world that moves its underlying far, what matters is whether the fund will still hold the contract when the event resolves.

- A contract that outlives the event is valued in each world at its underlying's level there, by the calculation that gives its delta, at the volatility that makes its worlds average, at the weights (1.5), to its value today by that calculation. Where even a volatility of zero leaves its worlds above its value today, it is valued at zero volatility, and the difference is its expected value (1.1).
- A contract that expires before the event is replaced before the event resolves, so the fund moves by its response, fitted on a history that holds how its contracts have behaved.
- For valuation, an event that can happen at any time uses the end of its own window as its assumed date.

Where a fund's rows are contracts on one reference, that reference's shares, and cash, its contracts are its whole payoff. The fund is worth in each world what they are worth there, and its betas give way to them, since the contracts set a cap or a floor that betas fitted on the past would run past.

**A fund on one reference.** Such a fund carries a multiple of its own value in one index or one company (3.1): three dollars of it for each dollar of the fund, or two dollars short. The session in which a world becomes known takes the reference from today's price to its price there, and the fund moves by its multiple of that move, to zero at the lowest. It moves by this in place of a response of its own. An index's move is the move of the fund that tracks it (3.1), which holds what the stated companies the index carries do beyond their responses. The reference's worlds average to today's price at the weights, so the fund's do as well, except where it reaches zero: there, at the weights, they come to more than its value today, and the row's expected value holds the difference (1.1). Over the period to the horizon's end such a fund resets its multiple each session. So at a draw there (1.8) it stands at its multiple of the move that makes the world known, and over the rest of the period at its reference's move to the power of its multiple, less what resetting loses: `max(1 + L * j, 0) * (1 + m)^L * exp(-(L^2 - L) * v / 2)` of its value today. `L` is its multiple, `j` the reference's placed move in that world, `1 + m` the reference's further move at the draw, and `v` the variance of the reference's compounded move over the period.

**One valuation, read twice.** Each holding has one valuation: what it is worth given the factors' moves, its residual, and its terms, on a date. The row reads it at each world's centre, as of today: what the holding would be worth today if that world were known. The chart reads it at each draw at the horizon's end (1.8). A contract nearer its expiry at the horizon is valued as it stands there.

### 1.7 A fund, and each name's share

**A fund is its identified holdings and a remainder.** The holding's dollars move by the fund's own price (1.6). Each company the look-through identifies in the fund (3.1) moves as that company does, on the dollars of exposure the fund carries to it. The fund's remainder is what its own move comes to beyond those companies moving as they do: what its other holdings do, and the rest of what its own price carries. A remainder is taken in each world: the fund's P&L there, less what its identified companies and the funds it holds contribute. The same rule applies inside each held fund.

Where a fund holds contracts that are valued by what they pay (1.6), its remainder's betas are fitted on the fund's history less what the contracts it held on each day did, so the contracts are counted once. Where the contracts are the fund's whole payoff, the remainder is its cash.

**Each name's share** of a world's P&L (`by_name`) is its exposure times its placed move there, `e_i * m_{i,w}`. **Each holding's share** (`by_holding`) is its value there less its value today. In every world, the names' shares and the funds' remainders sum to the holdings', and the holdings' to the portfolio's P&L.

### 1.8 The distribution within a world

A world's P&L in 1.1 is one figure: the portfolio at the world's centre. What the portfolio does in a world also varies, and the member sees an event's effect whole as a distribution.

**What is forecast.** `R` is the portfolio's value at the horizon's end, with the cash its holdings have paid by then, in today's dollars, less its value today. Under an event,

`P(R <= x) = sum_w p_w * P(R <= x given w)`

Two worlds, an event's ways, a ladder's bands, and events together share this one form.

**What varies within a world** is the state: the factors, each company's residual, and each fund's remainder, together. In world `w` the state is centred at that world's moves against today (1.3 to 1.5). Around that centre it varies as it ordinarily does over the period to the horizon's end: by the covariance of the state over completed moves, estimated as the portfolio's volatility is (3.2). States are drawn from that covariance jointly, so what moves together stays together, and a company takes the same draw wherever the portfolio owns it, directly or through its funds.

**What each world does is its own.** What a world does against the baseline, and how it varies around its centre, are fixed by what is stated about it. The worlds' probabilities weigh the worlds and place them against today (1.5).

**Each holding is valued at each draw,** by its one valuation (1.6), at the horizon's end. The chart's figure for a world is the mean of those values. Where a holding's value does not move in proportion to a price, as an option's does not, that mean and the row's figure differ, and the page shows both. Cash the analyst states a holding pays in a world is a point there. Shares of another name, and the company on its own, vary with the state (1.6).

**Zero drift.** Beyond a world's shocks, the move of every price between today and the horizon's end, each factor's proxy's and each company's, has a mean of zero in today's dollars. It is an assumption. Cash a holding is known to pay is counted as paid.

**How severe a world turns out is the analyst's to state.** Ordinary variation is the uncertainty a world carries before anything more is said about it. How severe a development turns out is a further uncertainty, and a judgment about the world, as the shock is. The analyst states it as ways of the event, each a degree of it with its own probability and its own shocks (1.1). The analyst can also name one past episode for how the factors moved together in a world, with its basis (1.3).

**The default.** Ordinary variation around each world is the default.

**The parts of the whole.** The variance of `R` is the variance between the worlds' means on the chart, plus the worlds' own variances at their probabilities. The first part is the event's volatility read at the horizon's end. The row's volatility (1.1) reads the same worlds at their centres today.

### 1.9 Events together

A combination reads several events as one set of worlds. Each joint world is one world of every event at once: with two plain events, both happen, only the first does, only the second does, or neither does. In each joint world every metric has one level and the portfolio one P&L.

**Probabilities.** Events read together form one structure (1.2): the events, their ways, and what the analyst has stated between them. A chance of one event given another says how the two depend on each other. So does a market that asks whether both happen. An implication puts one event's worlds within another's. Each joint world's probability is read from that structure, and every event and every way keeps the probability it has alone.

Two events with nothing stated between them are read as independent. More widely, events depend on one another only as their statements say. Each event is read given what its statements tie it to: the propositions its chances are given, the broader propositions it implies, and the parent it is a way of. Two events each read given a third are independent given it, and two events that a third is read given are independent of each other. The combination marks each probability it assumed this way. What the analyst then states about how the events depend takes its place.

**What today's prices carry.** Where no event states a weight, today's prices carry each joint world at its joint probability (1.5). Where an event's row states weights, each joint world's weight is its joint probability times one factor for each event's world in it, the factors set so that every event's worlds carry the weights its row has. So today's prices carry the events' dependence on one another as our probabilities do: the odds ratios between the events' worlds are the ones our joint probabilities give. Where one event alone states weights, the joint worlds within each of its worlds keep the proportions our joint probabilities give them, which is 1.5's sharing.

**What each joint world does** is the analyst's to state, as it states a way: a consequence on the joint world, against the joint world in which every event is at its baseline.

A joint world left unstated stands where the events' rows put it. A row gives each price the store places (1.5) a move against its event's baseline: the whole move a statement on it settles there (1.4), or, for one left unstated, its move in that world of the row (1.3, 1.6). With other events in view, that baseline is a mix of their worlds. So the store takes the joint worlds in which each event's moves compound with the others' and each row's relative price moves still hold: in each world of an event, a price, averaged over what the other events do there at their joint probabilities, stands from the price at the event's baseline, averaged the same way, by the move the row gives it. Where the events are independent, that is the rows' moves compounded as they stand. Each holding the store does not place is valued in each joint world as in any world (1.6): by its terms there, or by the value stated for the corresponding world of its event. An amount in today's dollars stays as stated; cash is discounted under its stated payment terms and converted at that joint world's rate, and shares of another name and the company on its own are valued at that joint world's prices. Each event's Yes and No averages are computed from the portfolio's gain or loss in the joint worlds on that side, at their conditional probabilities.

Illustration: events A and B exclude each other, each at 0.30. A's row states a shock of -0.10 on a price and B's +0.10, each against its own event not happening. With both in view, A's baseline is B three times in seven. The joint worlds that keep both rows put the price at -7.51% where A happens and at +6.46% where B happens, each against the world where neither does. A's baseline then stands at `1 + 3/7 * 0.0646 = 1.0277` of that world, and `0.9249 / 1.0277` is 0.90.

A stated joint world stands as stated, and the others stay where the rows alone put them. Each event's average over the joint worlds then shows what the statement changed.

**What the analyst is shown.** Every joint world, with its probability, what it does to each factor and to the portfolio, and whether that was stated or taken from the rows. Beside each event's row, the event's average over the joint worlds. A row is its event read alone.

**Events that cover every outcome** are ways of one event (1.1). One of them happens in each of their joint worlds, so the world in which every event is at its baseline, which the others would be stated against, is empty, and the store says so when the combination is made.

**What it gives.** A loss the member names is a set of joint worlds, read with their probabilities. A contract that would protect is valued in each joint world at what it pays there, less what buying it costs at its book (3.3), so what it offsets and what it leaves are read from the same worlds as the loss.

### 1.10 What cannot hold together

Some of what the analyst states cannot all be true at once. `check()` lists each case with the figures involved (6), and the statements stand as stated until the analyst changes one:

- links and chances that cannot all hold, among themselves or with the order a ladder's fields give its rungs (1.2), each smallest group of them that conflicts: the structure's probabilities wait on one being restated, and so do the figures of its events;
- a taken market's book that cannot hold with the links and chances (1.2), with how far it was stretched;
- values stated for a holding that put the weight its price implies outside 0 to 1 (1.6);
- weights stated for what today's prices carry (1.5) that cannot hold together: the ways of one outcome stating more than the outcome's own weight, or, where each of them states one, a sum other than it;
- a price that stands at zero in every world today's prices carry (1.5): the event's impact figures wait on a statement changing;
- differing shocks stated on two events that are true in the same worlds (1.9).

## 3. The derivations in detail

### 3.1 What the portfolio owns

**A holding** is what the member gives: a ticker with a quantity, an amount in dollars, or a weight, or cash as an amount in its currency. Its ticker's reference says what it is. Cash in dollars responds to nothing, and cash in another currency moves with its rate (1.6). A share of a company in any form, common or preferred, ordinary or a depositary receipt, is a name. An exchange-traded, closed-end or mutual fund is a fund, and is looked through. Anything else is held whole and measured on its own prices (3.2).

**A company is one name,** whatever securities the portfolio holds it by, and is priced through one of them. Where the member holds it directly, that is the security the member holds. Where it is held only through funds, it is its listing on its home market where a fund holds it there, in that market's currency, and otherwise the security its funds hold. Every dollar of exposure to the company moves with that price, in dollars at its currency's rate. Its id is a listing chosen once by one rule and kept: its first US ticker where it has one, and otherwise its home listing.

**A fund's value and its exposures are two quantities.** Its value is the holding's dollars, which move by the fund's own price (1.6). Its exposures are the dollars of each company that ride in it, which that company's own shock reaches (1.6). The two differ wherever a fund carries more or less of something than a dollar per dollar: a fund that carries three dollars of an index for each dollar of its value is exposed to each of the index's companies at three times its weight in the index, and an inverse fund at a negative multiple.

**A fund's rows** are what it holds, each kept with the date its source states it as of.

**Each row counts at the fund's exposure to what it refers to,** per dollar of the fund.

What a derivative refers to is identified as a holding is, by its identifiers: a company, a fund, or an index. A fund it refers to is looked through as a fund held by a fund is (below), at the derivative's exposure. An index's companies are the holdings of the fund whose returns track the index's own published values, each at its weight there.

**The look-through.** A company to which a fund's exposure is at least 0.0025 in absolute value becomes a `looks_through_to` link carrying `dollars = fund_dollars * exposure`, with its row's date. So does a company at any exposure that the portfolio already names, so that its own shock reaches every dollar of exposure to it (1.6). A fund held by a fund is expanded once more, at the product of the two exposures with the same floor, and a fund met at the next depth is a name with its own betas. A fund whose rows reach no company, as a fund of bonds, of a commodity or of a coin does, is held whole as a name with its own betas. `e_i`, the portfolio's dollars in name `i` after look-through, is the sum of every `looks_through_to` link into it, with its `holds` link where it is a company or a fund held whole. `V` is the sum of the holdings' values.

**What the analyst is shown.** A name is its company: its name, what it does, its industry and its home. A fund is what it holds: each holding by name, where it is listed, and its kind of asset, one of equity, debt, cash, derivative, commodity, currency and other. A Treasury bill and a money market fund are cash. Where a fund's rows come to more than the fund's value and it holds derivatives, the difference is their financing, and stands under derivative.

The portfolio's weight is read three ways, each summing to the portfolio: by company after look-through, by industry, and by country. Every row of every fund is counted. A company's industry is its place in one classification for every market, and its country is its home: the country it is registered in where it also trades there, and otherwise the one its own record gives. What is not a company stands under its kind of asset.

**News.** Each company with an absolute net exposure of at least 5% of the portfolio's value, across direct holdings and funds, comes with its latest news: the five most recent articles of the 180 days to the as-of, the span the horizon looks ahead over, each with its date, publisher, headline, summary, keywords and link. An article about several of these companies is given once, with each company it is about. Five percent is the line diversification rules draw for one issuer: above it, the portfolio rides on that company's own developments. The news comes as its sources write it, and what it means is the analyst's to judge.

**Prices.** When the as-of is today, each name's and each proxy's price is its latest mark, with its time, so that the portfolio and the markets, whose books are read live, describe the same moment. The store reads its marks and the books of the markets it keeps again every minute, while the analyst works and between its replies, and every value updates with them. Returns, betas and each factor's largest month use completed sessions. For an as-of in the past, the price is that day's close. A mutual fund is priced at its net asset value, which is struck once a day at New York's close, so its latest mark is its last net asset value.

A listing abroad is priced on its home market in its currency, each close and mark converted to dollars at the currency's rate when it was struck, and a close from before the currency's hourly rates begin at its own day's fixing by a central bank.

Holdings given in dollars, or as weights of a `total`, keep the number of shares they had when given, and their value moves with the price, as holdings given as quantities do. A price's move is its latest mark against the close of the session before the latest one to open: during a session, that day's move; after it, until the next opens, that session's whole move.

**What a stated value reads.** A holding the analyst values world by world (1.6) reads what its stated values name. Cash paid on a date is discounted at its currency's government yield to that date, and cash in another currency is converted at that currency's rate in each world. Shares of another name are valued at that name's price in each world, the name read and fitted as a holding is. A price on an earlier date is that day's close, with the history its betas are fitted on up to that date.

### 3.2 Factors and betas

**A factor** is a force that moves many prices at once. One instrument carries it, its proxy, whose price measures it. The store starts with one factor, the market. The analyst adds a factor for each force an event moves the portfolio through that the factors so far do not carry. It names the factor in plain words for what its proxy's price measures, so that an exposure's sign reads as it is, and gives it an order, broad to narrow. Each factor then holds what the earlier ones do not: the first is its proxy's return, and each later one is its proxy's return less what the earlier factors explain of it. One instrument carries one factor. Which forces move a portfolio belongs to the portfolio and its events, which is why the store starts with the market alone.

**A return** is what holding an instrument earned from one close to the next: the change in its price, with a dividend added back on its ex-dividend date, in the price's currency at that day's rate where it is paid in another. A currency or a crypto pair trades around the clock, so its close for a session is its price at that session's close in New York.

**The common history** is what the factors are estimated on: of the 504 completed sessions ending at `asof`, two years, those on which every factor's proxy has a return. A proxy with a shorter history shortens it for every factor, so a proxy's history is part of what it brings. `add` returns how many sessions the proxies share, and the first of them.

**A beta** is a name's full response to a factor: by how many percent its price moves when the factor's residual moves one percent and the other factors' residuals stay still. So a name's beta to the market is its response to a market move, with the later factors moving with the market as they ordinarily do, and its beta to a later factor is its response to what that factor does beyond the earlier ones.

**The factors' covariance** is measured over completed moves, because a shock is a completed move, made of many daily ones. What an unstated factor does (1.3) and how each factor is built from the ones before it both read this covariance.

**A past window.** A window's move is the proxy's price move from the close of its start to the close of its end: what holding it earned, less the income it accrued. Its residual is its further move from what the earlier factors' residuals over the window give it, compounded as a world's is (1.3). So a window stated back by its residuals gives every factor the window's move.

**A factor's largest month,** `M_c`, is the largest move, up or down, of its residual over any 21 consecutive sessions of the common history, each window's move as `window_returns` gives it, or over the whole common history where that is shorter. The portfolio's dollar beta to the factor times that move, `abs(g[c]) * M_c`, is what such a month would do to the portfolio: a measured size to read an exposure against.

A factor quoted as the yield its proxy pays is read in that yield, as its shocks are stated (1.3): the portfolio's exposure to it is the dollars it gains or loses for each percentage point the yield rises, `-g[c] * duration / 100`, and its largest month is the yield's move over that month in percentage points, `-100 * ln(1 + M_c) / duration`.

**The portfolio's volatility to the horizon's end** is how far its value ordinarily moves over the period the ledger looks over: the standard deviation of its move over the sessions the New York exchange is open from the as-of to the horizon's end.

### 3.3 Markets

**One search.** The venues' markets stay at the venues. `search_markets` passes the analyst's own words to each venue's search, and returns each venue's most relevant families of markets open at the as-of, in the venue's own order, the venues taking turns. What a venue's search returns reaches the analyst as the venue ranked it, because which markets matter to a portfolio is the analyst's question, answered by searching.

**A family** is a venue's own grouping of markets, and a family can hold families: a venue can file each date of one question as a family of its own, inside a larger one. Each family names the family that holds it. `get` reads a family whole at its level: each open market in it, or each family it holds, each of which `get` reads whole in turn. In a search, a family too large to list with its books gives its number of open markets, each market's question in its venue's words, and the span of their closes. Where the questions differ only by strikes the venue supplies, it gives their first and last question and the strikes: the first, last and step where evenly spaced, otherwise each strike. `get` reads the family whole.

**A market** is read in one form, whichever venue lists it.

- **Its terms:** its question and its rules, in the venue's words; its `resolution_source`, the sources its venue names for deciding the outcome; its `close_time`, the end date the venue reports; its `strike`, the threshold, and its `strike_date`, the date its question is about, each where the venue states one as data; its fees, those in effect at the as-of; and its family. The period a question counts is in its rules, and can differ from both dates.
- **Its book:** `bid` and `ask`, the best standing quote on each side; `price`, their midpoint where both sides have quotes; `depth`, in dollars: what it costs to take the smaller side of the book over its five best levels, buying Yes at each ask or No at one minus each bid, which is how much money it takes to move the price; and its last trade, with its time. A side with no standing quote has none, and the range the market gives its proposition runs to 0 on that side for the bid, or to 1 for the ask. What a large order would pay, walking its side of the book, is the cost of that order, and the price is the midpoint. `quoted_at` dates the stored book reading. The return also gives `confirmed_at`, when the book was last successfully checked, including a read that found its quotes unchanged.
- **The range its price allows** for the risk-neutral probability (1.2). `1 + k` is what a dollar grows to at the Treasury's rate by the close, over what it grows to at the rate the venue pays on a position. The range is shown with `k`, the days to the close, and the venue's rate as the venue states it.
- **Its history,** its price on each past day, which comes with a market read whole with `get`; and **its settlement,** as its venue reports it: whether it has settled, whether Yes or No won or neither did, and when.

**A market the analyst uses is kept.** A market enters the store when a link, a note or a chance names it. Its book and history are read then, and its book is read again every minute while the as-of is today (3.1).

**At a past as-of,** the markets are those open on that date, including those since closed or settled. The store uses the latest book snapshot or historical closing quotes dated at or before that day's end, retaining their observation time. A saved snapshot supplies the whole book; historical closing quotes supply the bid and ask without recorded depth. Where only a historical price is available, the store returns that price and its date, and the market has no bid, ask or price then, so it bounds and sets nothing.
