"""The page's rounding and the conversation as the page shows it from the trace."""

from __future__ import annotations

import pytest

from harness.render import chance, money
from harness.server import turns

MINUS = "\u2212"


@pytest.mark.parametrize("x, sign, shown", [
    (-2829.33, False, f"{MINUS}$2,829.33"),  # a worked row's pnl_yes
    (659.79, True, "+$659.79"),                # its pnl_no
    (61_250.4, False, "$61,250.40"),           # a holding's value
    (2854.995, False, "$2,855.00"),            # a half cent rounds up
    (34_649.96, True, "+$34,649.96"),
    (0.0453, False, "$0.05"),
    (0, True, "$0.00"),
    (-0.004, False, "$0.00"),                  # under half a cent is nothing
    (-1.1e-12, True, "$0.00"),                 # a fund's betas on later factors are zero to the float's precision
    (-0.006, False, f"{MINUS}$0.01"),
])
def test_every_dollar_figure_is_exact_to_the_cent(x, sign, shown):
    assert money(x, sign=sign) == shown


@pytest.mark.parametrize("p, shown", [
    (0.19, "19%"), (0.0803, "8.0%"), (0.018, "1.8%"), (0.0949, "9.5%"), (0.0004, "0.0%"),
    (0.0996, "10%"),   # one decimal only below 10%, read on the rounded figure
    (0.0995, "10%"),   # 9.95 rounds up to 10.0, which is not below 10%
    (0.125, "13%"),    # a half rounds up
    (0.5, "50%"), (1.0, "100%"),
])
def test_chances_are_whole_percents_with_one_decimal_below_ten(p, shown):
    assert chance(p) == shown


def test_the_page_shows_the_live_conversation_with_the_calls_a_stop_left_unrun():
    s = "s1"
    use = lambda i, name: {"type": "tool_use", "id": i, "name": name, "input": {}}  # noqa: E731
    entries = [
        {"type": "session", "session": s},
        {"type": "user", "session": s, "turn": 1, "at": "2026-11-05T10:00:00+00:00", "text": "Hold these."},
        {"type": "assistant", "session": s, "turn": 1, "at": "2026-11-05T10:00:05+00:00",
         "content": [{"type": "thinking", "thinking": "First the portfolio."}, use("u1", "add")]},
        {"type": "call", "session": s, "turn": 1, "call": 1, "tool": "add", "input": {}, "result": {"id": "portfolio"},
         "at": "2026-11-05T10:00:20+00:00"},
        {"type": "results", "session": s, "turn": 1, "results": [{"tool_use_id": "u1", "is_error": False, "call": 1}]},
        {"type": "assistant", "session": s, "turn": 1, "at": "2026-11-05T10:00:30+00:00",
         "content": [{"type": "text", "text": "Priced."}]},
        {"type": "user", "session": s, "turn": 2, "at": "2026-11-05T10:01:00+00:00", "text": "Go on."},
        {"type": "assistant", "session": s, "turn": 2, "at": "2026-11-05T10:01:09+00:00",
         "content": [use("u2", "candidates"), use("u3", "ledger")]},
        {"type": "call", "session": s, "turn": 2, "call": 2, "tool": "candidates", "input": {}, "result": {},
         "at": "2026-11-05T10:01:10+00:00"},
        {"type": "results", "session": s, "turn": 2, "results": [
            {"tool_use_id": "u2", "is_error": False, "call": 2},
            {"tool_use_id": "u3", "is_error": True, "content": "Not run: the person stopped the turn before this call."}]},
        {"type": "stop", "session": s, "turn": 2, "at": "2026-11-05T10:01:11+00:00"},
        {"type": "user", "session": s, "turn": 3, "at": "2026-11-05T10:02:00+00:00", "text": "Replaced later."},
        {"type": "rewind", "session": s, "turn": 3, "reason": "edit"},
        {"type": "user", "session": s, "turn": 3, "at": "2026-11-05T10:03:00+00:00", "text": "Edited."},
    ]
    shown = turns(entries, s)
    assert [(t["turn"], t["text"]) for t in shown] == [(1, "Hold these."), (2, "Go on."), (3, "Edited.")]
    work, text = shown[0]["items"]
    assert work["thinking"] == ["First the portfolio."] and text == {"kind": "text", "text": "Priced."}
    assert [c.get("tool") for c in work["calls"]] == [None, "add"] and work["calls"][1]["ok"] is True
    assert (work["from"], work["to"]) == ("2026-11-05T10:00:00+00:00", "2026-11-05T10:00:20+00:00")
    stopped = shown[1]
    assert stopped["stopped"] and [(c["tool"], c.get("call"), c.get("not_run", False)) for c in stopped["items"][0]["calls"]] \
        == [("candidates", 2, False), ("ledger", None, True)]
    assert all(t["editable"] for t in shown) and not any(t["editable"] for t in turns(entries, "another"))


# ---------------------------------------------------------------- a row's sentences, as the page and the download read them


class Read:
    """A row's sentences read with nothing being worked out, or with the named values waiting."""

    def __init__(self, waiting=()):
        self.pending, self.store, self.waiting = bool(waiting), None, set(waiting)

    def waits(self, name, subject):
        return name in self.waiting


def said(row, reader=None):
    from harness.render import figures_text, sentences
    return figures_text(sentences(reader or Read(), row))


SECTION_10 = {"id": "prop:1", "p": 0.1891, "lo": 0.10, "hi": 0.33, "coverage": 0.77, "pnl_yes": -2829.33,
              "pnl_no": 659.79, "D": -3489.12,
              "by_name": [{"name": "name:NVDA", "through": ["name:QQQ"]}, {"name": "name:AAPL", "through": ["name:QQQ"]},
                          {"name": "name:MSFT", "through": ["name:QQQ"]}]}


def test_a_rows_sentences_are_rounded_where_they_are_read():
    assert said(SECTION_10) == (
        f"Probability 19%, markets imply 10%–33%; Yes {MINUS}$2,829.33; No +$659.79; Full impact "
        f"{MINUS}$3,489.12; Largest contributors NVDA, AAPL, MSFT, through QQQ.")


def test_at_coverage_0_the_chance_is_our_estimate_and_no_market_prices_the_event():
    row = {**SECTION_10, "p": 0.02, "lo": 0.0, "hi": 1.0, "coverage": 0.0, "pnl_yes": -1600.0, "pnl_no": 32.65,
           "D": -1632.66, "by_name": [{"name": "name:TLT", "through": []}, {"name": "name:NVDA", "through": ["name:SOXX"]}]}
    assert said(row) == (
        f"Probability 2.0%, our estimate, no market prices it; Yes {MINUS}$1,600.00; No +$32.65; Full "
        f"impact {MINUS}$1,632.66; Largest contributors TLT, NVDA, through SOXX.")


def test_a_range_markets_bound_on_one_side_reads_as_at_least_or_at_most():
    """A floor and no ceiling, or a ceiling and no floor, each in its words."""
    floor = {**SECTION_10, "lo": 0.10, "hi": 1.0, "coverage": 0.10}
    assert said(floor).startswith("Probability 19%, markets imply ≥ 10%; Yes")
    ceiling = {**SECTION_10, "lo": 0.0, "hi": 0.33, "coverage": 0.67}
    assert said(ceiling).startswith("Probability 19%, markets imply ≤ 33%; Yes")
    from harness.render import sentences, sentences_html
    html = sentences_html(sentences(Read(), ceiling)[0])
    assert html.count('data-go="x:interval:prop:1"') == 1  # the one bound opens the range


def test_a_row_with_no_consequence_shows_the_first_sentence_only():
    row = {**SECTION_10, "p": 0.345, "lo": 0.33, "hi": 0.36, "coverage": 0.97, "pnl_yes": None, "pnl_no": None, "D": None,
           "by_name": None}
    assert said(row) == "Probability 35%, markets imply 33%–36%."


def test_names_held_directly_name_no_fund_and_rounding_reads_each_figure_on_its_own():
    row = {**SECTION_10, "p": 0.0995, "lo": 0.099, "hi": 0.1, "coverage": 0.999, "pnl_yes": 2850.0, "pnl_no": -315.0,
           "D": 3165.0, "by_name": [{"name": "name:AAPL", "through": []}]}
    assert said(row) == (
        f"Probability 10%, markets imply 9.9%–10%; Yes +$2,850.00; No {MINUS}$315.00; Full impact "
        f"+$3,165.00; "
        f"Largest contributors AAPL.")


def test_a_value_being_worked_out_has_its_place_and_one_not_there_has_none():
    from harness.render import WAITING, sentences
    waiting = {**SECTION_10, "p": None}
    first = sentences(Read({"point"}), waiting)[0]
    assert WAITING in first and said(waiting) == ""  # waiting: a placeholder where it sits; otherwise, no sentence
    dollars = sentences(Read({"impact"}), {**SECTION_10, "pnl_yes": None, "pnl_no": None, "D": None, "by_name": None})
    assert len(dollars) == 5 and all(row.count(WAITING) == 1 for row in dollars[1:])


# ---------------------------------------------------------------- the ledger panel's order and a market's chip


from test_tools import EVENTS  # noqa: E402


def test_the_ledger_lines_hold_their_place_and_carry_both_rankings(events):  # noqa: F811
    import re
    from harness.render import Reader, ledger_panel
    w, ids = events
    html = ledger_panel(Reader(w.store))
    lines = re.findall(r'<li class="event" data-k="([^"]+)" data-emove="(\d+)" data-worst="(\d+)">', html)
    assert [k for k, _, _ in lines] == [ids[e] for e in EVENTS]  # in the order watched: lines hold their place
    by = lambda col: [k for k, *_ in sorted(lines, key=lambda x: int(x[col]))]  # noqa: E731
    assert by(1) == [ids[e] for e in ("E2", "E6", "E4", "E3", "E1", "E7", "E5")]  # the switch's first order: emove
    assert by(2) == [ids[e] for e in ("E4", "E2", "E6", "E3", "E1", "E7", "E5")]  # and worst, the largest loss first
    e4 = html.split(f'data-k="{ids["E4"]}"', 1)[1].split("</li>", 1)[0]
    assert f'{MINUS}$29,400' in e4 and ">2.0%<" in e4 and "our estimate" in e4  # if it happens, the chance


def test_a_markets_chip_shows_the_price_its_venue_displays(tmp_path):
    from breakdown import World
    from harness.render import chips
    w = World(tmp_path / "scratch-chip")
    near = w.market("1", "Will the US raise tariffs?", 0.28, 0.33)
    wide = w.market("2", "Will Hormuz close?", 0.04, 0.19, last=0.07)
    for mid in (near, wide):
        w.link(w.prop(mid), "quoted_by", mid)
    got = chips(w.store, [near, wide, "market:polymarket:9"])
    assert (got[near]["label"], got[near]["value"]) == ("Will the US raise tariffs?", "30.5¢")  # the midpoint
    assert got[wide]["value"] == "7¢"  # a wide book shows its last trade
    assert got["market:polymarket:9"] == {"label": "market:polymarket:9", "value": "", "exists": False}
    w.store.close()


def test_a_value_waiting_on_a_client_shows_its_placeholder_even_after_a_read_with_the_client_failed(tmp_path):
    """While the analyst works the page reads from what the store holds: a chance whose market's book is not yet read
    waits, and shows so, even when the analyst's own call just tried the read and it failed."""
    from breakdown import World
    from harness.render import WAIT, Reader, cache_only, ledger_panel
    w = World(tmp_path / "scratch-wait")
    m = w.market("1", "Will it happen?", 0.30, 0.40)
    e = w.prop("E")
    book = w.store.fetchers["book"]

    def timed_out(subject, covering):
        raise TimeoutError("the venue did not answer")

    w.store.fetchers["book"] = timed_out
    w.link(e, "quoted_by", m)
    w.link("portfolio", "watches", e)
    assert w.point(e) is None  # read with the client, which failed: this revision's memo holds no value
    with cache_only(w.store):
        line = ledger_panel(Reader(w.store, pending=True)).split('data-k="' + e + '"', 1)[1].split("</li>", 1)[0]
    assert WAIT in line.split('class="ep"', 1)[1]  # the chance is being worked out, where it will sit
    with cache_only(w.store):
        assert WAIT not in ledger_panel(Reader(w.store))  # read with no clients and nothing pending: not there
    w.store.fetchers["book"] = book
    assert ">35%<" in ledger_panel(Reader(w.store))  # with the client, the book is read and the chance is in
    w.store.close()


def test_a_check_the_second_model_has_not_answered_waits_in_its_place_while_the_analyst_works(tmp_path):
    from breakdown import World
    from harness.render import WAIT, cache_only, view
    q = "Will it happen?"
    w = World(tmp_path / "scratch-check", {("market_forward", "E", q): None, ("market_converse", "E", q): None})
    m = w.market("1", q, 0.30, 0.40)
    e = w.prop("E")
    lid = w.link(e, "quoted_by", m)  # asked, and left unanswered: nothing is kept, and the next read asks again
    w.link("portfolio", "watches", e)
    with cache_only(w.store):
        html = view(w.store, e, pending=True)["html"]
    assert html.split('class="mline checks"', 1)[1].split("</div>", 1)[0].count(WAIT) == 1  # waiting, in its place
    w.judge.answers.update({("market_forward", "E", q): 0.97, ("market_converse", "E", q): 0.95})
    shown = view(w.store, e)["html"].split('class="mline checks"', 1)[1].split("</div>", 1)[0]
    assert WAIT not in shown and ">Matches<" in shown  # with the second model, its reading
    both = view(w.store, f"x:checks:{lid}")["html"]
    assert ">97%<" in both and ">95%<" in both  # and its answers both ways, in the checks it opens
    w.store.close()


def test_a_note_about_the_event_or_about_several_things_it_shows_shows_once_and_the_rest_point_to_it(tmp_path):
    """An opened event shows a note once: at its end when the note is about the event or about more than one thing the
    event shows, each of those pointing to it; in place when it is about one thing alone."""
    from test_impact import event, holding_world, metric
    from harness.render import Reader, event_view
    w = holding_world(tmp_path)
    oil = w.link(e := event(w, p=0.3), "consequence", metric(w, "x_level", 70.0, tracks={"USO": "price"}),
                 value=-7.0, meaning="gap")
    stocks = w.link(e, "consequence", metric(w, "y_level", 50.0, tracks={"SPY": "price"}), value=2.0, meaning="gap")
    shared = w.store.note("Both sizes come from the 2022 episode.", [oil, stocks])["id"]
    alone = w.store.note("The oil figure alone: from the futures curve.", [oil])["id"]
    own = w.store.note("Why this event matters.", [e])["id"]
    html = event_view(Reader(w.store), e)["html"]
    before, rest = html.split('data-k="notes"', 1)
    notes, after = rest.split("</section>", 1)
    body = before + after
    assert body.count('data-md="Both sizes') == 0 and notes.count('data-md="Both sizes') == 1
    assert body.count(f'data-to="{shared}"') == 2  # each consequence it is about points to it
    assert body.count('data-md="The oil figure alone') == 1 and f'data-to="{alone}"' not in html  # in place
    assert notes.count('data-md="Why this event') == 1
    assert body.count(f'data-to="{own}"') == 1  # from the stated chance, whose basis it is
    assert notes.count("data-note=") == 2
    w.store.close()


def test_a_metric_reads_by_its_name_wherever_it_is_labelled(tmp_path):
    """The name the analyst gives a metric is what the page, the chips, and the line saying what the analyst does
    call it; a price is its ticker's; a metric from a store before metrics had names reads by its id."""
    from test_impact import event, holding_world, metric
    from harness.render import Reader, chips, event_view, label, labels
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    w.store.edit(x, "name", "the level of X")
    lid = w.link(e := event(w, p=0.3), "consequence", x, value=-7.0, meaning="gap")
    assert label(w.store, x) == "the level of X" and labels(w.store, [x]) == {x: "the level of X"}
    assert chips(w.store, [x])[x]["label"] == "the level of X"
    assert '<a class="go ct" data-go="metric:x_level">The level of X</a>' in event_view(Reader(w.store), e)["html"]
    assert label(w.store, lid) == "the shock to the level of X from \u201cE\u201d"
    assert label(w.store, "metric:price:NVDA") == "NVDA's price"
    with w.store.transaction():  # as a store whose metric has no name holds it
        w.store.db.execute("UPDATE object SET stated = json_remove(stated, '$.name') WHERE id = ?", (x,))
    assert label(w.store, x) == "x level"
    w.store.close()


def test_a_link_a_computation_and_a_judgment_each_show_as_a_chip_and_open_where_they_explain(tmp_path):
    """A link shows as what it relates, a consequence with its figure, opening its explanation; a Computation
    as how it was worked out, opening its full details; a Judgment as the second model's check, opening the check."""
    from test_impact import event, holding_world, metric
    from breakdown import World
    from harness.render import chips, view
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0, tracks={"USO": "price"})
    e = event(w, p=0.3)
    gap = w.link(e, "consequence", x, value=-7.0, meaning="gap")
    level = w.link(e, "consequence", "metric:price:NVDA", value=150.0, meaning="level_if_happens")
    watch = next(link["id"] for link in w.store.links_of("portfolio", "watches"))
    cid = w.store.get(e)["derived"]["D"]["basis"]
    got = chips(w.store, [gap, level, watch, cid])
    assert got[gap] == {"label": "the x level", "value": f"shock {MINUS}$7.00", "exists": True, "kind": "link",
                        "title": "the shock to the x level from \u201cE\u201d"}
    assert got[level]["label"] == "NVDA's price" and got[level]["value"] == "shock +$108.40"
    assert got[watch]["label"] == "\u201cE\u201d" and got[watch]["value"] == ""
    assert got[cid]["label"] == "how it was worked out"
    assert view(w.store, gap)["title"] == "Shock to the x level"  # the consequence's explanation
    assert view(w.store, cid)["title"] == "Full details"
    w.store.close()

    q = "Will it happen?"
    b = World(tmp_path / "scratch-judged", {("market_forward", "E", q): 0.97, ("market_converse", "E", q): 0.95})
    m = b.market("1", q, 0.30, 0.40)
    lid = b.link(b.prop("E"), "quoted_by", m)
    jid = b.store.computation_output(b.store.get(lid)["derived"]["forward"]["basis"])["judgments"]["forward"]
    assert chips(b.store, [jid])[jid] == {"label": "the second model's check", "value": "97%", "exists": True,
                                          "kind": "Judgment", "title": "the second model's check"}
    assert view(b.store, jid)["title"] == "Second model"
    b.store.close()
