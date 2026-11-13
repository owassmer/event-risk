"""One set of probabilities for a structure (MATH.md 1.2), as pure arithmetic (harness.derive.oneform): the illustrations
of 1.2 and 10 by hand; the nine ways one probability is read from others, each as the statements an analyst makes,
against its formula on drawn values; what cannot hold together; books that give way by depth; and the junction tree
against every combination of the propositions, on structures drawn by rule."""

from __future__ import annotations

import random

import pytest

from harness.derive import oneform


def book(link, on, bid, ask, depth=1000.0):
    return {"link": link, "on": on, "bid": bid, "ask": ask,
            "price": None if bid is None or ask is None else (bid + ask) / 2, "depth": depth}


def implies(lid, a, b):
    return {"id": lid, "relation": "implies", "from": a, "to": b}


def chance(of, value, given=(), given_not=(), key=None):
    return {"key": key or f"{of}|{','.join(given)}|{','.join(given_not)}", "of": of, "given": list(given),
            "given_not": list(given_not), "value": value}


def read(props, links=(), chances=(), books=()):
    return oneform.read({"props": list(props), "links": list(links), "chances": list(chances), "books": list(books)})


def p(out, x):
    return out["props"][x].get("p")


# ---------------------------------------------------------------- the illustrations of MATH.md 1


def tariff():
    return {"props": ["T", "L", "R", "A30", "A20"],
            "links": [implies("l1", "L", "T"), implies("l2", "T", "R"), implies("l3", "A30", "L"),
                      implies("l4", "L", "A20")],
            "chances": [chance("T", 0.62, ["R"], key="T.chances[0]")],
            "books": [book("m1", "R", 0.28, 0.33, 14000), book("m2", "A30", 0.10, 0.15), book("m3", "A20", 0.86, 0.90)]}


def test_spec_10_the_markets_imply_0_10_to_0_33_and_t_is_0_62_of_r():
    """R's book 0.28-0.33 caps T, which implies R; A30's 0.10-0.15 lifts it through A30 implies L implies T;
    the chance of T given R, 0.62, gives 0.62 x 0.305 = 0.1891. Each figure rests on the statements its program does,
    the floor on L implies T, A30 implies L and M2's bid, the ceiling on T implies R and M1's ask."""
    out = oneform.read(tariff())
    t = out["props"]["T"]
    assert (t["range"]["lo"], t["range"]["hi"]) == pytest.approx((0.10, 0.33))
    assert set(t["range"]["lo_rests"]) == {"l1", "l3", "m2:book"} and set(t["range"]["hi_rests"]) == {"l2", "m1:book"}
    assert t["p"] == pytest.approx(0.62 * 0.305, abs=1e-9) and t["rule"] == "derived"
    assert set(t["rests"]) == {"T.chances[0]", "m1:price", "l2"}
    (given_r, given_not_r) = t["terms"]  # total probability over R
    assert (given_r["p"], given_r["share"], given_r["chance"]) == (pytest.approx(0.305), pytest.approx(0.62), "T.chances[0]")
    assert (given_not_r["p"], given_not_r["share"]) == (pytest.approx(0.695), pytest.approx(0.0))
    assert (given_r["joint"], given_not_r["joint"]) == (pytest.approx(0.1891, abs=1e-9), pytest.approx(0.0, abs=1e-9))
    assert out["props"]["R"]["rule"] == "market" and out["props"]["R"]["p"] == pytest.approx(0.305)
    assert out["cannot_hold"] == [] and out["stretched"] == []


def test_each_case_of_a_derived_probability_gives_it_the_probability_of_the_case_and_it_together():
    """MATH.md 1.2; TOOLS.md 9: a probability its chances fix opens to each case of what they are given, with the case's
    probability, the share of it where the proposition is true, and what the case gives it, the probability of the
    case and the proposition together, read from the same program. The chance of E given B is 0.5 and given not B 0.5,
    so E is 0.5 whatever B is, and B, with nothing bearing on it, is open: each case's probability is open from 0 to 1,
    and what it gives E from 0 to 0.5."""
    out = read(["E", "B"], chances=[chance("E", 0.5, ["B"]), chance("E", 0.5, (), ["B"])])
    e = out["props"]["E"]
    assert e["p"] == pytest.approx(0.5) and e["rule"] == "derived"
    by_b, not_b = e["terms"]
    for case in (by_b, not_b):
        assert case["range"] == {"lo": pytest.approx(0.0), "hi": pytest.approx(1.0)} and case["share"] == 0.5
        assert case["joint"] == {"lo": pytest.approx(0.0, abs=1e-9), "hi": pytest.approx(0.5)}


def test_spec_1_2_books_read_together():
    """MATH.md 1.2: T implies R, N implies T; R's book 0.28-0.33 priced 0.305, N's 0.18-0.22 priced 0.20; the chance of T
    given R 0.62. Each price alone would put T at 0.189, below N's 0.20; read together, R is 798 / 2561 = 0.3116 and T
    and N 0.62 R = 0.1932, each market's proposition within its bid and ask."""
    out = read(["T", "R", "N"], [implies("l1", "T", "R"), implies("l2", "N", "T")], [chance("T", 0.62, ["R"])],
               [book("mR", "R", 0.28, 0.33), book("mN", "N", 0.18, 0.22)])
    r = 798 / 2561
    assert (p(out, "R"), p(out, "T"), p(out, "N")) == pytest.approx((r, 0.62 * r, 0.62 * r), abs=1e-9)
    assert (round(p(out, "R"), 4), round(p(out, "T"), 4)) == (0.3116, 0.1932)
    assert (out["props"]["T"]["range"]["lo"], out["props"]["T"]["range"]["hi"]) == pytest.approx((0.18, 0.33))
    assert out["props"]["R"]["rule"] == "read" and out["props"]["N"]["rule"] == "read"


def test_spec_1_2_the_deeper_book_holds_and_the_thin_one_is_stretched():
    """MATH.md 1.2: T implies R, the chance of T given R 0.62, R's book 0.28-0.33 with $14,000 at its quotes, T's 0.24-0.25
    with $40. No probability of T lies in both: R stays within its quotes at 0.33, T reads 0.2046, and T's book is
    stretched by 0.0354 below its bid."""
    out = read(["T", "R"], [implies("l1", "T", "R")], [chance("T", 0.62, ["R"])],
               [book("mR", "R", 0.28, 0.33, 14000), book("mT", "T", 0.24, 0.25, 40)])
    assert (p(out, "R"), p(out, "T")) == pytest.approx((0.33, 0.62 * 0.33), abs=1e-9)
    (s,) = out["stretched"]
    assert (s["link"], s["below"], s["above"]) == ("mT", pytest.approx(0.24 - 0.62 * 0.33, abs=1e-9), 0.0)


def test_books_of_equal_depth_share_a_crossing_by_their_half_spreads():
    """Two books on one proposition that do not meet, 0.20-0.24 and 0.30-0.34, of equal depth and spread: each is
    stretched 0.03, by the least sum of squares, and they meet at 0.27. The deeper of two holds."""
    out = read(["E"], books=[book("a", "E", 0.20, 0.24), book("b", "E", 0.30, 0.34)])
    assert p(out, "E") == pytest.approx(0.27, abs=1e-9)
    assert {s["link"]: (round(s["below"], 9), round(s["above"], 9)) for s in out["stretched"]} == \
        {"a": (0.0, 0.03), "b": (0.03, 0.0)}
    out = read(["E"], books=[book("a", "E", 0.20, 0.24, 5000), book("b", "E", 0.30, 0.34, 100)])
    assert p(out, "E") == pytest.approx(0.24, abs=1e-9) and [s["link"] for s in out["stretched"]] == ["b"]


def test_a_book_whose_bid_and_ask_meet_holds_its_proposition_and_one_quoted_on_one_side_has_no_depth():
    """A book with no spread holds its proposition at its price by its bounds; a book quoted on one side has a depth of
    0, so it gives way before any book with money on both sides."""
    out = read(["E", "B"], [implies("l", "E", "B")], books=[book("e", "E", 0.30, 0.30), book("b", "B", 0.20, 0.40)])
    assert p(out, "E") == pytest.approx(0.30, abs=1e-9) and p(out, "B") == pytest.approx(0.30, abs=1e-9)
    out = read(["E", "B"], [implies("l", "E", "B")], books=[book("e", "E", 0.30, None, 9e9), book("b", "B", 0.20, 0.25, 1)])
    assert [s["link"] for s in out["stretched"]] == ["e"] and p(out, "E") == pytest.approx(0.25, abs=1e-9)


def test_the_ways_of_spec_1_5():
    """An event at 0.30 with two ways, the first's chance given it 0.7: worlds 0.21, 0.09 and 0.70; the second way, with
    no chance, takes what the first leaves."""
    out = read(["E", "W1", "W2"],
               [{"id": "w1", "relation": "way", "from": "E", "to": "W1", "side": "yes"},
                {"id": "w2", "relation": "way", "from": "E", "to": "W2", "side": "yes"}],
               [chance("E", 0.30), chance("W1", 0.7, ["E"])])
    assert (p(out, "E"), p(out, "W1"), p(out, "W2")) == pytest.approx((0.30, 0.21, 0.09), abs=1e-9)
    assert out["props"]["E"]["rule"] == "chance" and out["props"]["W2"]["rule"] == "derived"


def test_ways_of_the_no_side_and_ways_under_ways():
    """Ways of an event's No side make up its complement; a way's ways make up the way."""
    out = read(["E", "N1", "N2", "Y", "Y1", "Y2"],
               [{"id": "a", "relation": "way", "from": "E", "to": "N1", "side": "no"},
                {"id": "b", "relation": "way", "from": "E", "to": "N2", "side": "no"},
                {"id": "c", "relation": "way", "from": "E", "to": "Y", "side": "yes"},
                {"id": "d", "relation": "way", "from": "Y", "to": "Y1"},
                {"id": "f", "relation": "way", "from": "Y", "to": "Y2"}],
               [chance("E", 0.4), chance("N1", 0.25, given_not=["E"]), chance("Y1", 0.5, ["Y"])])
    assert (p(out, "N1"), p(out, "N2"), p(out, "Y"), p(out, "Y1"), p(out, "Y2")) == \
        pytest.approx((0.15, 0.45, 0.40, 0.20, 0.20), abs=1e-9)


# ---------------------------------------------------------------- the nine, as statements


def test_the_nine_each_as_the_statements_an_analyst_makes():
    """Each of the derivation table's nine rows, said as links and chances, gives its formula, on drawn values."""
    rng = random.Random(7)
    for _ in range(20):
        a, b, d, c, c1, c2 = (rng.uniform(0.05, 0.95) for _ in range(6))
        lo_, hi_ = sorted((a, b))
        # 1. E implies B, the chance of E given B
        out = read(["E", "B"], [implies("l", "E", "B")], [chance("E", c, ["B"]), chance("B", b)])
        assert p(out, "E") == pytest.approx(c * b, abs=1e-9)
        # 2. A implies E, the chance of E given not A
        out = read(["E", "A"], [implies("l", "A", "E")], [chance("E", c, given_not=["A"]), chance("A", a)])
        assert p(out, "E") == pytest.approx(a + c * (1 - a), abs=1e-9)
        # 3. A implies E implies B, the chance of E given B and not A
        out = read(["E", "A", "B"], [implies("l1", "A", "E"), implies("l2", "E", "B")],
                   [chance("E", c, ["B"], ["A"]), chance("A", lo_), chance("B", hi_)])
        assert p(out, "E") == pytest.approx(lo_ + c * (hi_ - lo_), abs=1e-9)
        # 4. D neither way, the chance of E given D and given not D
        out = read(["E", "D"], [], [chance("E", c1, ["D"]), chance("E", c2, given_not=["D"], key="E|~D"),
                                    chance("D", d)])
        assert p(out, "E") == pytest.approx(d * c1 + (1 - d) * c2, abs=1e-9)
        # 5. E = B and C: the chance of C given B; E = B then C: the chance of E given B
        both = [{"id": "x", "relation": "and", "from": "E", "to": "B"}, {"id": "y", "relation": "and", "from": "E",
                                                                         "to": "C"}]
        out = read(["E", "B", "C"], both, [chance("C", c, ["B"]), chance("B", b)])
        assert p(out, "E") == pytest.approx(b * c, abs=1e-9)
        then = [{"id": "x", "relation": "then", "from": "E", "to": "B", "step": 1},
                {"id": "y", "relation": "then", "from": "E", "to": "C", "step": 2}]
        out = read(["E", "B", "C"], then, [chance("E", c, ["B"]), chance("B", b)])
        assert p(out, "E") == pytest.approx(b * c, abs=1e-9)
        # 6. E = B or C, each priced: the chance of C given B
        either = [{"id": "x", "relation": "or", "from": "E", "to": "B"}, {"id": "y", "relation": "or", "from": "E",
                                                                          "to": "C"}]
        cb = min(c, d / b) if b > 0 else c  # a share of B that C's own probability allows
        out = read(["E", "B", "C"], either, [chance("C", cb, ["B"]), chance("B", b), chance("C", d, key="C")])
        if out["cannot_hold"]:
            continue
        assert p(out, "E") == pytest.approx(b + d - b * cb, abs=1e-9)
        # 7. E = B or C: the chance of C given not B
        out = read(["E", "B", "C"], either, [chance("C", c, given_not=["B"]), chance("B", b)])
        assert p(out, "E") == pytest.approx(b + c * (1 - b), abs=1e-9)
        # 8. what B adds to A: A and E are B's two ways
        ways = [{"id": "x", "relation": "way", "from": "B", "to": "A", "side": "yes"},
                {"id": "y", "relation": "way", "from": "B", "to": "E", "side": "yes"}]
        out = read(["B", "A", "E"], ways, [chance("A", lo_), chance("B", hi_)])
        assert p(out, "E") == pytest.approx(hi_ - lo_, abs=1e-9)
        # 9. an event from its ways
        ways = [{"id": f"w{i}", "relation": "way", "from": "E", "to": f"W{i}", "side": "yes"} for i in range(3)]
        shares = [rng.uniform(0, 0.3) for _ in range(3)]
        out = read(["E", "W0", "W1", "W2"], ways, [chance(f"W{i}", s, key=f"W{i}") for i, s in enumerate(shares)])
        assert p(out, "E") == pytest.approx(sum(shares), abs=1e-9)


def test_a_composite_of_two_priced_parts_and_a_priced_either_is_fixed_by_the_books():
    """Books on B, C and "B or C" fix A = B and C with nothing stated: 0.52 + 0.42 - 0.72 = 0.22."""
    links = [{"id": "x", "relation": "and", "from": "A", "to": "B"}, {"id": "y", "relation": "and", "from": "A", "to": "C"},
             {"id": "u", "relation": "or", "from": "O", "to": "B"}, {"id": "v", "relation": "or", "from": "O", "to": "C"}]
    out = read(["A", "B", "C", "O"], links, books=[book("b", "B", 0.50, 0.54), book("c", "C", 0.40, 0.44),
                                                  book("o", "O", 0.70, 0.74)])
    assert p(out, "A") == pytest.approx(0.22, abs=1e-9) and out["props"]["A"]["rule"] == "derived"


# ---------------------------------------------------------------- open, and what cannot hold


def test_a_chance_given_r_with_no_link_leaves_t_open_settled_by_its_chance_given_not_r():
    """Without "T implies R", the chance of T given R 0.62 leaves T open: 0.62 x 0.305 = 0.1891 to that plus 0.695;
    the chance of T given not R would settle it."""
    out = read(["T", "R"], [], [chance("T", 0.62, ["R"])], [book("m", "R", 0.28, 0.33)])
    o = out["props"]["T"]["open"]
    assert (o["lo"], o["hi"]) == pytest.approx((0.1891, 0.1891 + 0.695), abs=1e-8)
    assert o["settled_by"] == [[{"of": "T", "given": [], "given_not": ["R"]}]] and "p" not in out["props"]["T"]


def test_an_open_probability_that_rests_on_an_open_one_points_to_it():
    """E implies B and the chance of E given B is 0.6, with nothing said of B: E is open, 0 to 0.6, and B's own
    probability is what would settle it; B's open range is settled by its chance given not E."""
    out = read(["E", "B"], [implies("l", "E", "B")], [chance("E", 0.6, ["B"])])
    e, b = out["props"]["E"]["open"], out["props"]["B"]["open"]
    assert (e["lo"], e["hi"]) == pytest.approx((0.0, 0.6)) and e["with"] == ["B"] and e["settled_by"] == []
    assert (b["lo"], b["hi"]) == pytest.approx((0.0, 1.0)) and b["settled_by"]


def test_two_drivers_left_open_by_how_they_relate_are_settled_by_the_chance_of_one_given_the_other():
    """E has a chance given each case of A and B, A at 0.40 and B at 0.30, and nothing says how A and B relate: E is
    open, and what closes it is the chance of A given B, or of B given A, not a chance of E's own (evaluation's case)."""
    out = read(["E", "A", "B"], [],
               [chance("E", 0.9, ["A", "B"], key="e1"), chance("E", 0.5, ["A"], ["B"], key="e2"),
                chance("E", 0.4, ["B"], ["A"], key="e3"), chance("E", 0.1, [], ["A", "B"], key="e4"),
                chance("A", 0.40, key="a"), chance("B", 0.30, key="b")])
    o = out["props"]["E"]["open"]
    assert (o["lo"], o["hi"]) == pytest.approx((0.35, 0.38), abs=1e-9)
    assert o["settled_by"] == [[{"of": "A", "given": ["B"], "given_not": []}],
                              [{"of": "B", "given": ["A"], "given_not": []}]]


def and3(lid, c, parts):
    return [{"id": f"{lid}#{i + 1}", "relation": "and", "from": c, "to": x} for i, x in enumerate(parts)]


def three_parts(extra=()):
    """Evaluation's breakdown draw 288, as drawn: P4 is P3 and P1 and P2, P3 priced, P1 and P2 open; P5 implies P4. The
    sets that put P2 lowest and highest give P1 the same probability, so P2's chances given P1 and given not P1 are
    equal at every set between them, ends included."""
    return read(["P0", "P1", "P2", "P3", "P4", "P5"],
                [implies("P3 implies P0", "P3", "P0"), implies("P5 implies P4", "P5", "P4"),
                 *and3("P4 = P3 and P1 and P2", "P4", ["P3", "P1", "P2"])],
                [chance("P0", 0.5549, key="chance of P0"), chance("P4", 0.0, [], ["P2", "P0"], key="P4 given not P2, P0"),
                 chance("P5", 0.0, key="chance of P5"), *extra],
                [book("M1 on P3", "P3", 0.2733, 0.2902, 26.19140510908119),
                 book("M2 on P5", "P5", None, 0.0809, 34.12764737807144)])


def test_chances_that_fix_a_probability_only_once_an_open_one_is_fixed_are_not_named_as_closing_it():
    """P2's chances given P3 close it, P3 being priced; its chances given P1 would fix it only once P1, itself open, is
    fixed, so they are not named, and P1 stands beside as what P2 rests on. Stated, the first pair gives P2 a
    probability; the second, at shares that differ, leaves it open."""
    o = three_parts()["props"]["P2"]["open"]
    assert o["settled_by"] == [[{"of": "P2", "given": ["P3"], "given_not": []},
                               {"of": "P2", "given": [], "given_not": ["P3"]}]]
    assert o["with"] == ["P1", "P4"]
    by_p3 = three_parts([chance("P2", 0.5, ["P3"]), chance("P2", 0.5, [], ["P3"])])["props"]["P2"]
    assert by_p3["p"] == pytest.approx(0.5, abs=1e-8)
    by_p1 = three_parts([chance("P2", 0.6, ["P1"]), chance("P2", 0.3, [], ["P1"])])["props"]["P2"]
    assert "open" in by_p1 and "p" not in by_p1


def test_a_set_of_chances_is_named_by_the_fewest_of_them_that_close_the_probability():
    """Evaluation's breakdown draw 14, renamed: O is A or B, A and O priced. B given not A is already fixed, since B and
    not A is O and not A, so B given A alone closes B, and B given O alone (B given not O being 0). Naming B given not A
    as well would point to a chance that, stated at any other value, stretches a book."""
    def structure(extra=()):
        return read(["A", "B", "E", "O", "F"],
                    [{"id": "O#1", "relation": "or", "from": "O", "to": "A"},
                     {"id": "O#2", "relation": "or", "from": "O", "to": "B"}],
                    [chance("A", 0.666, ["O"]), chance("E", 0.5833, ["F"], ["A"]), *extra],
                    [book("m1", "A", 0.5695, 0.5967, 53.22), book("m2", "A", 0.5925, 0.6163, 47.64),
                     book("m3", "O", 0.8714, 0.9108, 72.96), book("m4", "F", 0.2979, 0.315, 35.31)])
    o = structure()["props"]["B"]["open"]
    assert o["settled_by"] == [[{"of": "B", "given": ["O"], "given_not": []}],
                              [{"of": "B", "given": ["A"], "given_not": []}]]
    assert structure([chance("B", 0.5, ["A"])])["props"]["B"]["p"] is not None
    assert structure([chance("B", 0.5, [], ["A"])])["stretched"]


def test_chances_that_cannot_hold_list_each_smallest_set_and_the_probabilities_wait():
    """The chance of E given B 0.5 and E given nothing 0.4, with B given nothing 0.9: E and B alone would carry 0.45,
    more than E's 0.4, whatever links E to B, so the three chances are the set. A second conflict apart from it, two
    chances of F: both sets are listed, and nothing has a probability."""
    out = read(["E", "B", "F"], [implies("l", "E", "B")],
               [chance("E", 0.5, ["B"], key="e|b"), chance("E", 0.4, key="e"), chance("B", 0.9, key="b"),
                chance("F", 0.2, key="f1"), chance("F", 0.3, key="f2")])
    sets = [set(c["statements"]) for c in out["cannot_hold"]]
    assert {"e|b", "e", "b"} in sets and {"f1", "f2"} in sets
    assert all("p" not in v for v in out["props"].values())


def test_ways_that_overrun_their_parent_leave_it_no_probability_and_are_listed():
    """Two ways at 0.6 each given their event hold only where the event has no probability: a chance given it says
    nothing there, so the chances and the ways' links are listed, and the probabilities wait."""
    ways = [{"id": "w1", "relation": "way", "from": "E", "to": "A", "side": "yes"},
            {"id": "w2", "relation": "way", "from": "E", "to": "B", "side": "yes"}]
    out = read(["E", "A", "B"], ways, [chance("A", 0.6, ["E"], key="a"), chance("B", 0.6, ["E"], key="b")])
    (c,) = out["cannot_hold"]
    assert {"a", "b"} <= set(c["statements"]) and c.get("leaves_no_probability") == {"E": True}
    assert all("p" not in v for v in out["props"].values())


def test_a_chance_stands_over_a_taken_book_on_its_own_proposition_and_the_book_is_stretched():
    """A chance of 0.40 given nothing on a proposition whose book is 0.30-0.34: the chance stands, the book is
    stretched 0.06 above its ask."""
    out = read(["E"], [], [chance("E", 0.40)], [book("m", "E", 0.30, 0.34)])
    assert p(out, "E") == pytest.approx(0.40) and out["props"]["E"]["rule"] == "chance"
    (s,) = out["stretched"]
    assert s["above"] == pytest.approx(0.06, abs=1e-9)


def test_a_chance_given_others_keeps_its_propositions_own_book_to_stay_near():
    """The chance of E given B 0.5, B's book 0.38-0.42 and E's own 0.18-0.22: E is not fixed by its chance, so E's book
    and B's inform each other, nearest both prices in their half-spreads."""
    out = read(["E", "B"], [implies("l", "E", "B")], [chance("E", 0.5, ["B"])],
               [book("b", "B", 0.38, 0.42), book("e", "E", 0.18, 0.22)])
    assert p(out, "E") == pytest.approx(0.5 * p(out, "B"), abs=1e-9)
    assert p(out, "B") == pytest.approx(0.40, abs=1e-9)  # 0.5 x 0.40 = 0.20, both at their prices


# ---------------------------------------------------------------- the junction tree against every combination


def drawn(seed: int) -> dict:
    """A structure drawn by rule: 4 to 8 propositions; implications, an and or an or composite, a then, ways of one
    side; chances given nothing or given one or two others; books on some propositions, of drawn depths."""
    rng = random.Random(seed)
    n = rng.randint(4, 8)
    ps = [f"P{i}" for i in range(n)]
    links, k = [], 0
    used: set[tuple] = set()

    def lid():
        nonlocal k
        k += 1
        return f"l{k}"

    for _ in range(rng.randint(1, n)):
        a, b = rng.sample(ps, 2)
        if (a, b) not in used:
            used.add((a, b))
            links.append(implies(lid(), a, b))
    if n >= 5 and rng.random() < 0.6:
        e, b, c = rng.sample(ps, 3)
        rel = rng.choice(["and", "or", "then"])
        links += [{"id": lid(), "relation": rel, "from": e, "to": b, **({"step": 1} if rel == "then" else {})},
                  {"id": lid(), "relation": rel, "from": e, "to": c, **({"step": 2} if rel == "then" else {})}]
    if n >= 6 and rng.random() < 0.5:
        e, *ws = rng.sample(ps, 3)
        side = rng.choice(["yes", "no"])
        links += [{"id": lid(), "relation": "way", "from": e, "to": w, "side": side} for w in ws]
    chances = []
    for x in rng.sample(ps, rng.randint(0, 3)):
        given = rng.sample([q for q in ps if q != x], rng.randint(0, 2))
        cut = rng.randint(0, len(given))
        chances.append(chance(x, round(rng.uniform(0.05, 0.95), 3), given[:cut], given[cut:], key=f"{x}.c"))
    books = []
    for i, x in enumerate(rng.sample(ps, rng.randint(1, n))):
        lo = round(rng.uniform(0.0, 0.8), 3)
        books.append(book(f"m{i}", x, lo, round(lo + rng.uniform(0.0, 0.2), 3), rng.choice([10.0, 100.0, 1000.0])))
    return {"props": ps, "links": links, "chances": chances, "books": books}


def whole(m: dict) -> dict:
    """The same reading with every proposition in one group: every combination of them, held whole."""
    real = oneform._cliques
    try:
        oneform._cliques = lambda nodes, scopes: [tuple(nodes)]
        return oneform.read(m)
    finally:
        oneform._cliques = real


@pytest.mark.parametrize("seed", range(60))
def test_the_junction_tree_reads_as_every_combination_does(seed):
    m = drawn(seed)
    tree, full = oneform.read(m), whole(m)
    assert bool(tree["cannot_hold"]) == bool(full["cannot_hold"])
    for x in m["props"]:
        a, b = tree["props"][x], full["props"][x]
        if "range" in b:
            assert (a["range"]["lo"], a["range"]["hi"]) == pytest.approx((b["range"]["lo"], b["range"]["hi"]), abs=1e-7)
        if tree["cannot_hold"]:
            continue
        assert ("p" in a) == ("p" in b), (seed, x, a, b)
        if "p" in b:
            assert a["p"] == pytest.approx(b["p"], abs=1e-6)
        else:
            assert (a["open"]["lo"], a["open"]["hi"]) == pytest.approx((b["open"]["lo"], b["open"]["hi"]), abs=1e-6)
    if not tree["cannot_hold"]:
        named = lambda out: {s["link"]: (round(s["below"], 5), round(s["above"], 5)) for s in out["stretched"]  # noqa
                             if max(s["below"], s["above"]) > 1e-6}
        assert named(tree) == named(full)


def test_a_group_that_holds_a_smaller_conflicting_one_is_not_listed_beside_it():
    """MATH.md 1.10, "each smallest group" (evaluation's 47ad case): T implies R, the chance of R given T at 0.5, and of T
    given R at 0.62. The link and the first chance cannot hold together; the three together leave R no probability
    too, but only because they hold that pair, so the pair alone is listed."""
    out = read(["T", "R"], [implies("i", "T", "R")], [chance("R", 0.5, ["T"]), chance("T", 0.62, ["R"])])
    assert [set(c["statements"]) for c in out["cannot_hold"]] == [{"R|T|", "i"}]
