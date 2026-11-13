"""The page's rounding and the conversation as the page shows it from the trace."""

from __future__ import annotations

import pytest

from harness.render import amount, chance, money
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
    (0.19, "19.0%"), (0.0803, "8.0%"), (0.018, "1.8%"), (0.0949, "9.5%"), (0.0004, "0.0%"),
    (0.0996, "10.0%"), (0.0995, "10.0%"),  # one decimal whatever the size: a half rounds up
    (0.125, "12.5%"), (0.325, "32.5%"),    # a market's mid between two whole cents keeps its half
    (0.5, "50.0%"), (1.0, "100.0%"),
])
def test_a_probability_is_a_percent_to_one_decimal_whatever_its_size(p, shown):
    """Each kind of figure has one form wherever it appears, whatever its size."""
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


def test_a_reply_that_ends_without_its_answer_says_why():
    """A reply the model declined, or one cut off at its length or its context window, carries why it ended
    (stop_reason), so the page says so where its words would be; a reply that ends as replies do carries nothing."""
    s = "s1"
    entries = [
        {"type": "session", "session": s},
        {"type": "user", "session": s, "turn": 1, "at": "2026-11-05T10:00:00+00:00", "text": "What could move this?"},
        {"type": "assistant", "session": s, "turn": 1, "at": "2026-11-05T10:00:30+00:00", "stop_reason": "end_turn",
         "content": [{"type": "text", "text": "Here it is."}]},
        {"type": "user", "session": s, "turn": 2, "at": "2026-11-05T10:01:00+00:00", "text": "Why that one?"},
        {"type": "assistant", "session": s, "turn": 2, "at": "2026-11-05T10:01:40+00:00", "stop_reason": "refusal",
         "content": [{"type": "thinking", "thinking": "Reading my context."}]},
        {"type": "user", "session": s, "turn": 3, "at": "2026-11-05T10:02:00+00:00", "text": "Go on."},
        {"type": "assistant", "session": s, "turn": 3, "at": "2026-11-05T10:02:40+00:00", "stop_reason": "max_tokens",
         "content": [{"type": "text", "text": "The first half of"}]},
    ]
    shown = turns(entries, s)
    assert [t.get("ended") for t in shown] == [None, "refusal", "max_tokens"]
    assert shown[1]["items"][0]["thinking"] == ["Reading my context."]


# ---------------------------------------------------------------- a row's sentences, as the page and the download read them


class Read:
    """A row's sentences read with nothing being worked out, or with the named values waiting."""

    def __init__(self, waiting=()):
        self.pending, self.store, self.waiting = bool(waiting), None, set(waiting)

    def waits(self, name, subject):
        return name in self.waiting

    def reading_waits(self, pid):
        return "structure" in self.waiting


def said(row, reader=None):
    from harness.render import figures_text, sentences
    return figures_text(sentences(reader or Read(), row))


SECTION_10 = {"id": "prop:1", "probability": 0.1891, "range": [0.10, 0.33], "coverage": 0.77, "yes": -2829.33,
              "no": 659.79, "full_impact": -3489.12,
              "contributors": [{"name": "name:NVDA", "through": ["name:QQQ"], "full_impact": -1250.49},
                               {"name": "name:AAPL", "through": ["name:QQQ"], "full_impact": -800.5},
                               {"name": "name:MSFT", "through": ["name:QQQ"], "full_impact": -499.49}]}


def test_a_rows_sentences_read_each_kind_in_its_form_and_the_full_impact_as_yes_less_no_as_shown():
    """A forecast to one decimal; what the event does in whole dollars, the full impact as Yes less No as they
    are shown (−$2,829 less +$660), its exact figure one click away; each largest contributor with its amount."""
    assert said(SECTION_10) == (
        f"Forecast 18.9%, market-implied range 10.0%–33.0%; Yes {MINUS}$2,829; No +$660; Full impact "
        f"{MINUS}$3,489; Largest contributors NVDA {MINUS}$1,250, AAPL {MINUS}$801, MSFT {MINUS}$499, through QQQ.")


def test_a_forecast_the_markets_imply_nothing_of_reads_as_its_figure_alone():
    """TOOLS.md 9: where no taken market bears on an event, its row gives the forecast alone; how it is set, a
    chance stated on its own or chances given other propositions, is its probability tab's and its calculation's."""
    row = {**SECTION_10, "probability": 0.02, "range": [0.0, 1.0], "coverage": 0.0, "yes": -1600.0, "no": 32.65,
           "full_impact": -1632.66, "contributors": [{"name": "name:TLT", "through": [], "full_impact": -900.0},
                                                     {"name": "name:NVDA", "through": ["name:SOXX"], "full_impact": -400.0}]}
    assert said(row) == (
        f"Forecast 2.0%; Yes {MINUS}$1,600; No +$33; Full "
        f"impact {MINUS}$1,633; Largest contributors TLT {MINUS}$900, NVDA {MINUS}$400, through SOXX.")
    assert said({**row, "point": {"rule": "derived"}}).startswith("Forecast 2.0%; Yes")


def test_a_probability_its_statements_leave_open_reads_as_the_range_they_leave_and_nothing_names_a_fault():
    """MATH.md 1.2; ONTOLOGY.md 2.1; TOOLS.md 9: a proposition's forecast, or the range its statements leave it, in
    its place, in the form of the market-implied range, a floor or a ceiling alone where it has one; the market-implied
    range beside it only where the two differ as shown; nothing in its place where it is anywhere from 0 to 1; and
    where the statements cannot all hold, the market-implied range under its own label (the page shows nothing whose
    purpose is a fault)."""
    waiting = {"yes": None, "no": None, "full_impact": None, "contributors": None, "probability": None}
    same = {**SECTION_10, **waiting, "range": [0.0, 0.02], "coverage": 0.98,
            "left_open": {"lo": 0.0, "hi": 0.02, "settled_by": [], "with": []}}
    assert said(same) == "Forecast ≤ 2.0%."  # not "market-implied range ≤ 2.0%" as well
    wider = {**same, "left_open": {"lo": 0.05, "hi": 0.3, "settled_by": []}, "range": [0.0, 0.33], "coverage": 0.67}
    assert said(wider) == "Forecast 5.0%–30.0%, market-implied range ≤ 33.0%."
    nothing = {**same, "left_open": {"lo": 0.0, "hi": 1.0, "settled_by": []}, "range": [0.0, 1.0], "coverage": 0.0}
    assert said(nothing) == ""  # not "our estimate": no chance gives it
    held = {**same, "left_open": None, "cannot_hold": [{"statements": ["link:1", "prop:1.chances[0]"]}]}
    assert said(held) == "Market-implied range ≤ 2.0%."
    for row in (same, wider, nothing, held):
        assert not any(w in said(row) for w in ("open", "cannot", "restated", "finished"))


def test_a_range_markets_bound_on_one_side_reads_as_at_least_or_at_most():
    """A floor and no ceiling, or a ceiling and no floor, each in its words."""
    floor = {**SECTION_10, "range": [0.10, 1.0], "coverage": 0.10}
    assert said(floor).startswith("Forecast 18.9%, market-implied range ≥ 10.0%; Yes")
    ceiling = {**SECTION_10, "range": [0.0, 0.33], "coverage": 0.67}
    assert said(ceiling).startswith("Forecast 18.9%, market-implied range ≤ 33.0%; Yes")
    from harness.render import sentences, sentences_html
    html = sentences_html(sentences(Read(), ceiling)[0])
    assert html.count('data-go="x:interval:prop:1"') == 1  # the one bound opens the range


def test_a_row_with_no_consequence_shows_the_first_sentence_only():
    row = {**SECTION_10, "probability": 0.345, "range": [0.33, 0.36], "coverage": 0.97, "yes": None, "no": None, "full_impact": None,
           "contributors": None}
    assert said(row) == "Forecast 34.5%, market-implied range 33.0%–36.0%."


def test_names_held_directly_name_no_fund():
    row = {**SECTION_10, "probability": 0.0995, "range": [0.099, 0.1], "coverage": 0.999, "yes": 2850.0, "no": -315.0,
           "full_impact": 3165.0, "contributors": [{"name": "name:AAPL", "through": [], "full_impact": 3165.0}]}
    assert said(row) == (
        f"Forecast 10.0%, market-implied range 9.9%–10.0%; Yes +$2,850; No {MINUS}$315; Full impact "
        f"+$3,165; "
        f"Largest contributors AAPL +$3,165.")


def test_a_value_being_worked_out_has_its_place_and_one_not_there_has_none():
    from harness.render import WAITING, sentences
    waiting = {**SECTION_10, "probability": None, "yes": None, "no": None, "full_impact": None, "contributors": None}
    first = sentences(Read({"structure"}), waiting)[0]
    assert WAITING in first  # being read: a placeholder where it sits
    assert said(waiting) == "Market-implied range 10.0%–33.0%."  # no forecast there: the range under its own label
    assert said({**waiting, "range": [0.0, 1.0], "coverage": 0.0}) == ""  # nothing to say of it: no sentence
    dollars = sentences(Read({"impact"}), {**SECTION_10, "yes": None, "no": None, "full_impact": None, "contributors": None})
    assert len(dollars) == 5 and all(row.count(WAITING) == 1 for row in dollars[1:])


# ---------------------------------------------------------------- the ledger panel's order and a market's chip


from test_tools import EVENTS  # noqa: E402


def test_the_ledger_lines_hold_their_place_and_carry_both_rankings(events):  # noqa: F811
    import re
    from harness.render import Reader, ledger_panel
    w, ids = events
    html = ledger_panel(Reader(w.store))
    lines = re.findall(r'<li class="event" data-k="([^"]+)" data-volatility="(\d+)" data-worst="(\d+)">', html)
    assert [k for k, _, _ in lines] == [ids[e] for e in EVENTS]  # in the order watched: lines hold their place
    by = lambda col: [k for k, *_ in sorted(lines, key=lambda x: int(x[col]))]  # noqa: E731
    assert by(1) == [ids[e] for e in ("E4", "E2", "E6", "E3", "E1", "E7", "E5")]  # the switch's first order
    assert by(2) == [ids[e] for e in ("E4", "E2", "E6", "E3", "E1", "E5", "E7")]  # and worst, the largest loss first
    e4 = html.split(f'data-k="{ids["E4"]}"', 1)[1].split("</li>", 1)[0]
    # if it happens, in whole dollars, and the forecast, with nothing of how it is set: the probability tab says it
    assert re.search(rf'{MINUS}\$29,\d{{3}}<', e4) and ">2.0%<" in e4
    assert "our estimate" not in e4 and "derived" not in e4


def test_a_markets_chip_shows_its_price(tmp_path):
    from breakdown import World
    from harness.render import chips
    w = World(tmp_path / "scratch-chip")
    near = w.market("1", "Will the US raise tariffs?", 0.28, 0.33)
    wide = w.market("2", "Will Hormuz close?", 0.04, 0.19, last=0.07)
    for mid in (near, wide):
        w.link(w.prop(mid), "asks", mid)
    got = chips(w.store, [near, wide, "market:polymarket:9"])
    # its book as observed, whatever its width
    assert (got[near]["label"], got[near]["value"]) == ("Will the US raise tariffs?", "YES bid 28\u00a2 \u00b7 ask 33\u00a2")
    assert got[wide]["value"] == "YES bid 4\u00a2 \u00b7 ask 19\u00a2"
    assert got["market:polymarket:9"] == {"label": "market:polymarket:9", "value": "", "exists": False}
    w.store.close()


def test_a_forecast_its_statements_leave_open_shows_the_conditional_probabilities_that_leave_it_open(tmp_path):
    """MATH.md 1.2: where the analyst's statements leave an event's forecast open, its probability tab still shows each
    conditional probability it states, beside its whole condition, as stated: they are what the forecast rests on."""
    import re
    from breakdown import World
    from harness.render import Reader, event_view
    w = World(tmp_path / "scratch-open-inputs")
    e, a = w.prop("E"), w.prop("A")
    w.chances(e, {"given": [a], "value": 0.3}, {"given_not": [a], "value": 0.6})
    w.link("portfolio", "watches", e)
    assert w.point(e) is None and w.left_open(e)
    with w.store.lock:
        opened = event_view(Reader(w.store), e)["html"]
    inputs = re.sub(r"<[^>]+>", "|", opened.split('data-k="inputs"', 1)[1])
    assert inputs.count("Conditional probability") == 2 and inputs.count("Analyst input") == 2
    assert "|30.0%|" in inputs and "|60.0%|" in inputs
    w.store.close()


def test_a_markets_price_opens_to_its_book_as_observed_and_the_midpoint_of_its_bid_and_ask(tmp_path):
    """MATH.md 3.3: a market's price is the midpoint of its best bid and ask wherever both sides quote, however far
    apart they stand. Its calculation shows the book as observed, YES bid and ask in cents, and the midpoint with its
    arithmetic, and names no other rule; a market with a side unquoted has no midpoint."""
    import re
    from html import unescape
    from breakdown import World
    from harness.render import view
    w = World(tmp_path / "scratch-price")
    wide = w.market("2", "Will Hormuz close?", 0.04, 0.19, last=0.07)
    one = w.market("3", "Will it rain?", 0.30, None)
    for mid in (wide, one):
        w.link(w.prop(mid), "asks", mid)
    text = lambda html: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(html)))  # noqa: E731
    shown = text(view(w.store, f"x:price:{wide}")["html"])
    assert "YES bid 4\u00a2" in shown and "YES ask 19\u00a2" in shown and "Midpoint 11.5\u00a2" in shown
    assert "(4\u00a2 + 19\u00a2) / 2 = 11.5\u00a2" in shown
    assert "10.0%" not in shown and "last trade:" not in shown.lower() and "apart" not in shown
    lone = text(view(w.store, f"x:price:{one}")["html"])
    assert "YES bid 30\u00a2" in lone and "YES ask none" in lone and "Midpoint" not in lone
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
    w.link(e, "asks", m)
    w.link("portfolio", "watches", e)
    assert w.point(e) is None  # read with the client, which failed: this revision's memo holds no value
    with cache_only(w.store):
        line = ledger_panel(Reader(w.store, pending=True)).split('data-k="' + e + '"', 1)[1].split("</li>", 1)[0]
    assert WAIT in line and "Not finished" not in line  # being worked out: its place, as a figure arriving
    with cache_only(w.store):
        assert WAIT not in ledger_panel(Reader(w.store))  # read with no clients and nothing pending: not there
    w.store.fetchers["book"] = book
    assert ">35.0%<" in ledger_panel(Reader(w.store))  # with the client, the book is read and the chance is in
    w.store.close()


def test_a_note_about_the_event_or_about_several_things_it_shows_shows_once_and_the_rest_point_to_it(tmp_path):
    """An opened event shows a note once: at its end when the note is about the event or about more than one thing the
    event shows, each of those pointing to it; in place when it is about one thing alone."""
    from test_impact import channel, event, holding_world
    from harness.render import Reader, event_view
    w = holding_world(tmp_path)
    oil = w.link(e := event(w, p=0.3), "consequence", channel(w, "USO"), shock=-0.1)
    stocks = w.link(e, "consequence", channel(w, "SPY"), shock=0.04)
    shared = w.store.note("Both sizes come from the 2022 episode.", [oil, stocks])["id"]
    alone = w.store.note("The oil figure alone: from the futures curve.", [oil])["id"]
    own = w.store.note("Why this event matters.", [e])["id"]
    from harness.render import view
    html = event_view(Reader(w.store), e)["html"]
    before, rest = html.split('data-k="notes"', 1)
    notes, after = rest.split("</details>", 1)
    body = before + after
    assert body.count('data-md="Both sizes') == 0 and notes.count('data-md="Both sizes') == 1
    assert notes.count('data-md="Why this event') == 1
    reason = w.store.stated_of(e)["chances"][0]["note"]  # the reasoning the chance was stated with: a note about the event
    assert notes.count(f'data-note="{reason}"') == 1 and notes.count("data-note=") == 3
    assert 'data-md="The oil figure alone' not in html  # a note about one shock alone is with that shock, a click in
    assert f'data-go="x:consequence:{oil}"' in html
    assert view(w.store, oil)["html"].count('data-md="The oil figure alone') == 1
    w.store.close()


def test_a_metric_reads_by_its_name_wherever_it_is_labelled(tmp_path):
    """The name the analyst gives a metric is what the page, the chips, and the line saying what the analyst does
    call it; a price is its ticker's; a metric from a store before metrics had names reads by its id. A shock reads by
    the factor or the name whose price it moves."""
    from test_impact import channel, event, holding_world, metric
    from harness.render import Reader, chips, event_view, label, labels
    w = holding_world(tmp_path)
    x = metric(w, "x_level", 70.0)
    w.store.edit(x, "name", "the level of X")
    oil = channel(w, "USO")
    lid = w.link(e := event(w, p=0.3), "consequence", oil, shock=-0.1)
    assert label(w.store, x) == "the level of X" and labels(w.store, [x]) == {x: "the level of X"}
    assert chips(w.store, [x])[x]["label"] == "the level of X"
    assert f'<a class="go name" data-go="{oil}">Crude oil</a>' in event_view(Reader(w.store), e)["html"]
    assert label(w.store, lid) == "the shock to crude oil from \u201cE\u201d"
    assert label(w.store, "metric:price:NVDA") == "NVDA's price"
    with w.store.transaction():  # as a store whose metric has no name holds it
        w.store.db.execute("UPDATE object SET stated = json_remove(stated, '$.name') WHERE id = ?", (x,))
    assert label(w.store, x) == "x level"
    w.store.close()


def test_a_link_and_a_computation_each_show_as_a_chip_and_open_where_they_explain_and_a_judgment_opens_nothing(
        tmp_path):
    """A link shows as what it relates, a consequence with its figure, opening its explanation; a Computation as how
    it was worked out, opening its full details. A Judgment is the judge's, kept in the store and the record, and the
    member's page shows nothing of it: its id opens to nothing."""
    from test_impact import channel, event, holding_world
    from breakdown import World
    from harness.render import chips, view
    w = holding_world(tmp_path)
    e = event(w, p=0.3)
    gap = w.link(e, "consequence", channel(w, "USO"), shock=-0.1)
    level = w.link(e, "consequence", "name:NVDA", shock=0.085)
    watch = next(link["id"] for link in w.store.links_of("portfolio", "watches"))
    cid = w.store.get(e)["derived"]["full_impact"]["basis"]
    got = chips(w.store, [gap, level, watch, cid])
    assert got[gap] == {"label": "crude oil", "value": f"whole move {MINUS}10.00%", "exists": True, "kind": "link",
                        "title": "the shock to crude oil from \u201cE\u201d", "tone": "loss"}  # its direction's colour
    assert got[level]["label"] == "NVDA" and got[level]["value"] == "whole move +8.50%" and got[level]["tone"] == "gain"
    assert got[watch]["label"] == "\u201cE\u201d" and got[watch]["value"] == ""
    assert got[cid]["label"] == "Calculation"
    assert view(w.store, gap)["title"] == "Shock to crude oil"  # the consequence's explanation
    assert view(w.store, cid)["title"] == "Full details"
    w.store.close()

    q = "Will it happen?"
    b = World(tmp_path / "scratch-judged", {("market_forward", "E", q): 0.97, ("market_converse", "E", q): 0.95})
    m = b.market("1", q, 0.30, 0.40)
    lid = b.link(b.prop("E"), "asks", m)
    jid = b.store.computation_output(b.store.get(lid)["derived"]["forward"]["basis"])["judgments"]["forward"]
    assert view(b.store, jid)["title"] == "Not found"
    b.store.close()


def test_a_stated_value_rests_on_its_own_note_and_a_link_on_the_notes_about_it(tmp_path):
    """A chance's basis is the note it was stated with: a note about its proposition is about the proposition, which
    states other chances with notes of their own, so it shows in the event's notes and in no chance's basis. A
    statement that is a link, such as a consequence, rests on its note and on every note about that link."""
    from breakdown import World

    from harness.render import Reader, _stated_basis

    w = World(tmp_path / "basis")
    a, b = w.prop("A"), w.prop("B")
    lid = w.link(a, "implies", b)
    own = w.store.note("Why this chance.", [a])["id"]
    w.store.note("About the proposition.", [a])
    about_link = w.store.note("About the link.", [lid])["id"]
    reader = Reader(w.store)
    chance_basis = _stated_basis(reader, {"value": 0.4, "note": own}, a)
    assert "Why this chance." in chance_basis and "About the proposition." not in chance_basis
    shown_below = _stated_basis(reader, {"value": 0.4, "note": own}, a, below=[own])
    assert shown_below.count(f'data-to="{own}"') == 1 and "Rationale" in shown_below  # one pointer, to its own note
    link_basis = _stated_basis(reader, {"value": -5.0, "note": about_link}, lid)
    assert "About the link." in link_basis
    other = w.store.note("More about the link.", [lid])["id"]
    assert "More about the link." in _stated_basis(reader, {"value": -5.0, "note": about_link}, lid) and other


def test_the_receipts_read_an_event_with_ways_from_its_outcomes(tmp_path):
    """MATH.md 1.5's ways illustration: an event at 0.30, its Yes side two ways at 0.21 and 0.09, No at 0.70, the No side
    the baseline; the worlds' P&L -$2,213.07, -$17,653.11 and +$2,933.61. The volatility opens to the three outcomes
    and its definition filled with them, sqrt(0.21 x 2,213.07^2 + 0.09 x 17,653.11^2 + 0.70 x 2,933.61^2) = 5,924.50,
    then its parts between the sides and within Yes; Yes opens to its two ways averaged, (0.21 x -2,213.07 +
    0.09 x -17,653.11) / 0.30 = -6,845.08; the figures name the worst outcome by its way. With a weight on the second
    way stated, the row carries an expected value, and Yes and No still read as their sides' means."""
    from test_impact import channel, event, world
    from test_ways import way
    from breakdown import stated
    from harness.render import Reader, figures_text, sentences, view
    from harness.ledger import row
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.30)
    first = way(w, e, "the first way", side="yes", weight=0.7)
    second = way(w, e, "the second way", side="yes")
    w.link(first, "consequence", channel(w, "SPY"), shock=-0.05)
    w.link(second, "consequence", channel(w, "SPY"), shock=-0.2)
    import re
    text = lambda html: re.sub(r"<[^>]+>", "", html)  # noqa: E731  the page's text, as a reader reads it
    vol = text(view(w.store, f"x:volatility:{e}")["html"])
    for title, p_, A in (("the first way", "21.0000%", f"{MINUS}$2,213.07"), ("the second way", "9.0000%",
                                                                              f"{MINUS}$17,653.11"),
                         ("No", "70.0000%", "+$2,933.61")):
        assert f"{title}probability {p_}{A}" in vol
    assert "21.0000% × (−$2,213.07)²" in vol and "9.0000% × (−$17,653.11)²" in vol and "70.0000% × $2,933.61²" in vol
    assert "±$5,924.50" in vol and "Within Yes" in vol and "Within No" not in vol and "√(p × (1 − p))" not in vol
    swing = text(view(w.store, f"x:swing:{e}")["html"])
    assert "(21.0000% × (−$2,213.07) + 9.0000% × (−$17,653.11)) / 30.0000%" in swing and f"{MINUS}$6,845.08" in swing
    assert "(1 − p) × full impact" not in swing
    said = figures_text(sentences(Reader(w.store), row(w.store, e)))
    assert f"Worst outcome {MINUS}$17,653 · the second way, 9.0%" in said and "Expected value" not in said
    w.store.edit(second, "priced_in", stated(0.15))
    r = row(w.store, e)
    said = figures_text(sentences(Reader(w.store), r))
    assert f"Expected value {amount(r['expected'])}" in said
    swing = text(view(w.store, f"x:swing:{e}")["html"])
    assert "Expected value" in swing and money(r["yes"], sign=True) in swing  # Yes, its outcomes at their probabilities
    assert "(21.0000% × (" in swing and "(1 − p) × full impact" not in swing
    w.store.close()


def test_where_the_weight_is_reads_three_ways_each_adding_to_the_portfolio_as_shown(tmp_path):
    """MATH.md 3.1: the portfolio's weight by company, by industry and by country, each list shown whole adding to the
    portfolio's value as the panel shows it and to 100.0%, what is not a company under its kind; a company held both
    directly and through a fund with its two parts, adding to it; an industry or a country opening to its companies,
    which add to it. NVDA is $5,000 held and $2,400 through FUND, XOM $1,400 through FUND, cash $1,000."""
    import copy
    import re
    from test_impact import holding_world
    from harness.render import Reader, portfolio_panel, view
    w = holding_world(tmp_path)

    class Placed(Reader):  # the store's own weights, each company given an industry and a country
        def output(self, name, subject):
            got = super().output(name, subject)
            if name != "weights" or not got:
                return got
            got = copy.deepcopy(got)
            place = {"name:NVDA": ("Semiconductors", "US"), "name:XOM": ("Oil & Gas Integrated", "US")}
            for c in got["by_company"]:
                if c.get("name") in place:
                    c["industry"], c["country"] = place[c["name"]]
            kinds = [g for g in got["by_industry"] if g.get("kind")]
            got["by_industry"] = [{"industry": "Semiconductors", "sector": "Technology", "dollars": 7400.0,
                                   "companies": 1, "names": ["name:NVDA"]},
                                  {"industry": "Oil & Gas Integrated", "sector": "Energy", "dollars": 1400.0,
                                   "companies": 1, "names": ["name:XOM"]}] + kinds
            got["by_country"] = [{"country": "US", "country_name": "United States", "dollars": 8800.0, "companies": 2,
                                  "names": ["name:NVDA", "name:XOM"]}] + kinds
            return got

    import harness.render as render
    real = render.Reader
    render.Reader = Placed  # every read of the page goes through it, as a view builds its own
    try:
        panel = portfolio_panel(Placed(w.store))
        assert all(f'data-wt="{by}"' in panel for by in ("company", "industry", "country"))
        nvda = panel.split('data-k="name:NVDA"', 1)[1].split("</li>", 1)[0]
        assert "$7,400" in nvda and "$5,000" in nvda and "directly" in nvda and "$2,400" in nvda
        cells = lambda row: re.findall(r"<td>(.*?)</td>", row)  # noqa: E731
        plain = lambda html: re.sub(r"<[^>]+>", "", html)  # noqa: E731
        for by in ("company", "industry", "country"):
            html = view(w.store, f"weights:{by}")["html"]
            rows = re.findall(r'<tr class="(?:|more|kind)">(.*?)</tr>', html)
            shown = [(plain(cells(r)[-2]), plain(cells(r)[-1])) for r in rows]
            assert sum(float(d.strip("$").replace(",", "")) for d, _ in shown) == 10_000
            assert round(sum(float(t.strip("%")) for _, t in shown), 1) == 100.0
            assert ("$1,000", "10.0%") in shown and 'data-go="' not in rows[-1]  # cash, a kind, opens nothing
            assert "$10,000" in html.split('class="sum"', 1)[1] and "100.0%" in html.split('class="sum"', 1)[1]
        country = view(w.store, "country:US")["html"]
        rows = re.findall(r'<tr class="(?:|more)">(.*?)</tr>', country)
        assert sum(float(plain(cells(r)[-2]).strip("$").replace(",", "")) for r in rows) == 8_800
        assert "$8,800" in country and "88.0%" in country
        semis = view(w.store, "industry:Semiconductors")["html"]
        assert "$7,400" in semis and "Technology" in semis and "through FUND" in semis
        from test_impact import event
        from harness.ledger import moves
        from harness.render import amount
        spy = next(f["id"] for f in w.store.query("Factor") if f["stated"]["proxy"] == "SPY")
        e = event(w, "The market falls", p=0.3)
        w.link(e, "consequence", spy, shock=-0.1)
        country = view(w.store, "country:US")["html"]
        found = moves(w.store, ["name:NVDA", "name:XOM"])[0]  # the engine's: the two companies' shares summed
        events = country.split(">Events<", 1)[1].split("</table>", 1)[0]
        assert "The market falls" in events and f">{amount(found['yes'])}<" in events
        assert f">{amount(found['no'])}<" in events and "largest contributors NVDA and XOM" in events
    finally:
        render.Reader = real
    w.store.close()


def test_a_probabilitys_history_is_what_the_record_kept_at_each_reply_and_each_outcomes_with_it(tmp_path):
    """ONTOLOGY.md 2: the history draws the probability as the record kept it at each reply's end, under the definition
    it has now, and each outcome's the same way, for when one is selected; once the event is restated as another
    question, what was said of the one before is not its history."""
    import json
    import re
    from html import unescape
    from test_impact import channel, event, world
    from test_ways import way
    from harness import record
    from harness.ledger import row
    from harness.render import Reader, event_view
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.30)
    first = way(w, e, "the first way", side="yes", weight=0.7)
    second = way(w, e, "the second way", side="yes")
    w.link(first, "consequence", channel(w, "SPY"), shock=-0.05)
    w.link(second, "consequence", channel(w, "SPY"), shock=-0.2)
    record.reply(w.store, session="s", turn=1, stopped=False)
    w.chances(e, {"given": [], "given_not": [], "value": 0.40}, keep=False)
    record.reply(w.store, session="s", turn=2, stopped=False)

    def history():
        found = re.search(r'data-history="([^"]+)"', event_view(Reader(w.store), e)["html"])
        return json.loads(unescape(found.group(1))) if found else None

    kept = [x["created_at"] for x in w.store.records()]
    spec = history()
    assert [t for t, _ in spec["ours"]][:2] == kept and [round(p, 6) for _, p in spec["ours"]][:2] == [0.3, 0.4]
    way_id = next(o["id"] for o in row(w.store, e)["outcomes"] if o["title"] == "the first way")
    assert [round(p, 6) for _, p in spec["outcomes"][way_id]["points"]][:2] == [0.21, 0.28]  # 0.7 of 0.30, then 0.40
    w.store.edit(e, "text", "Another question.")
    spec = history()
    assert spec is None or not set(t for t, _ in spec["ours"]) & set(kept)
    w.store.close()


def test_an_event_reads_as_its_outcomes_in_the_ledger_the_tree_the_prices_and_each_outcome(tmp_path):
    """On the ways illustration of MATH.md 1.5 (worlds at 0.21, 0.09 and 0.70; the fund at 600 stands at 586.72, 494.08 and
    617.60; the worlds' P&L -$2,213.07, -$17,653.11 and +$2,933.61), with a second event beside it:
    - the ledger draws every row's marks on one axis, which holds every outcome and today, with round ticks;
    - the tree lists the outcomes in order, a way under its side, the baseline marked;
    - the prices give the fund's level in each outcome, the shock where an outcome states it;
    - an outcome opens to its holdings, which add to its P&L as shown."""
    import json
    import re
    from html import unescape
    from test_impact import channel, event, world
    from test_ways import way
    from harness.render import Reader, event_view, ledger_panel, view
    w = world(tmp_path, g={"SPY": 100_000.0}, coefficients={"SPY": {}}, levels={"SPY": 600.0})
    e = event(w, p=0.30)
    first = way(w, e, "the first way", side="yes", weight=0.7)
    second = way(w, e, "the second way", side="yes")
    w.link(first, "consequence", channel(w, "SPY"), shock=-0.05)
    w.link(second, "consequence", channel(w, "SPY"), shock=-0.2)
    other = event(w, "F", p=0.5)
    w.link(other, "consequence", channel(w, "SPY"), shock=0.04)
    panel = ledger_panel(Reader(w.store))
    head = re.findall(r'<span class="(start|mid|end)" style="left:([\d.]+)%">([^<]+)</span>',
                      panel.split('class="laxis"', 1)[1].split("</div></div>", 1)[0])
    assert head[0][:2] == ("start", "0.000") and head[-1][:2] == ("end", "100.000")  # it ends on labelled ticks
    assert [label for _, _, label in head].count("Today") == 1
    rows_ = re.findall(r'<li class="event".*?</li>', panel)
    today = {re.search(r'class="today" style="left:([\d.]+)%"', r_).group(1) for r_ in rows_}
    assert len(rows_) == 2 and len(today) == 1  # every row on one axis
    ways_row = next(r_ for r_ in rows_ if f'data-k="{e}"' in r_)
    ticks = [float(x) for x in re.findall(r'class="wt [a-z]*" style="left:([\d.]+)%"', ways_row)]
    # each world where its P&L lies, -2,213.07, -17,653.11 and +2,933.61, all within the axis, today among them
    assert len(ticks) == 3 and 0 < ticks[1] < ticks[0] < float(today.pop()) < ticks[2] < 100
    html = event_view(Reader(w.store), e)["html"]
    grid = html.split('data-k="outcomes"', 1)[1].split("</table>", 1)[0]
    head = grid.split("</thead>", 1)[0]
    assert re.findall(r'<th class="side" colspan="(\d)">(Yes|No)', head) == [("2", "Yes"), ("1", "No")]
    assert re.findall(r'<th data-outcome="([^"]+)"', head) == [first, second, "no"]  # a way under its side
    assert re.search(r'data-outcome="no"[^>]*><span class="otag">baseline', head)
    assumed = html.split('data-k="assumptions"', 1)[1].split("</table>", 1)[0]  # the prices, beside the outcomes
    levels = assumed.split('class="v-levels"', 1)[1].split("</tbody>", 1)[0]
    assert ">$586.72<" in levels and ">$494.08<" in levels and ">$617.60<" in levels and "today $600.00" in levels
    moves = assumed.split('class="v-moves"', 1)[1].split("</tbody>", 1)[0]
    assert ">−5.00%<" in moves and ">−20.00%<" in moves  # each way's stated shock, opening its basis
    # each move in the colour of its direction: these fall
    assert re.search(r' loss">−5\.00%<', moves) and re.search(r' loss">−20\.00%<', moves)
    assert 'class="n base"></td>' in moves and "\u2014" not in moves  # the baseline moves against itself: blank
    shown = re.sub(r"<[^>]+>", "", view(w.store, f"x:outcome:{e}:{second}")["html"])
    assert "Each holding" in shown and shown.count("−$17,653.11") >= 2  # the outcome's P&L, and its holdings' sum
    assert "the whole move on" not in shown and "its own whole move" in shown
    receipt_ = shown.split("Dollar beta × move", 1)[1].split("Each holding", 1)[0]  # the engine's parts (1.6)
    assert "$100,000.00 × −17.6531%" in receipt_ and receipt_.count("−$17,653.11") == 2  # 494.08 / 600 − 1
    w.store.close()


def test_a_probability_its_chances_fix_opens_to_total_probability_over_what_they_are_given(tmp_path):
    """E given D at 0.8 and given not D at 0.1, with D's market at 0.30: the probability opens to each case of D with
    its probability and the share of it in which E is true, and their sum, 0.3 x 0.8 + 0.7 x 0.1 = 0.31, with the basis
    of each chance."""
    from breakdown import World
    from harness.render import operand, view
    w = World(tmp_path / "scratch-receipt")
    e, d = w.prop("E"), w.prop("D")
    w.link(d, "asks", w.market("D", "D?", 0.28, 0.32))
    w.chances(e, {"given": [d], "value": 0.8}, {"given_not": [d], "value": 0.1})
    w.link("portfolio", "watches", e)
    html = view(w.store, f"x:p:{e}")["html"]
    assert f"{operand(0.3)} × {operand(0.8)} + {operand(0.7)} × {operand(0.1)} = {operand(0.31)}" in html
    import re
    text = re.sub(r"<[^>]+>", "", html)
    assert "P(E | A)" in text and "P(E | not A)" in text and text.count("Basis for P(E") == 2
    w.store.close()


def test_a_case_its_statements_leave_open_shows_its_range_and_its_symbol_in_the_sum(tmp_path):
    """E given A at 0.3 and given not A at 0.3 fixes E at 0.3 whatever A's probability, which nothing settles (MATH.md 1.2):
    each case shows the range its statements leave it, 0 to 1, with its share by its chance, and the sum keeps the
    case's symbol where its probability is open, so every figure shown is one the store gives. Opened, each case's
    lane says what its share is, the event's share of a part whose range is 0 to 1, and draws no share whose ends
    nothing fixes; a proposition whose own probability is open shows none."""
    import re

    from breakdown import World
    from harness.render import Reader, event_view, operand, view
    w = World(tmp_path / "scratch-open-case")
    e, a = w.prop("E"), w.prop("A")
    w.chances(e, {"given": [a], "value": 0.3}, {"given_not": [a], "value": 0.3})
    w.link("portfolio", "watches", e)
    text = re.sub(r"<[^>]+>", "", view(w.store, f"x:p:{e}")["html"])
    assert f"{operand(0.0)}–{operand(1.0)}" in text  # each case's range, in place of a probability
    assert f"= P(A) × {operand(0.3)} + P(not A) × {operand(0.3)} = {operand(0.3)}" in text
    assert text.count("Analyst input") == 2
    opened = event_view(Reader(w.store), e)["html"]
    inputs = re.sub(r"<[^>]+>", "|", opened.split('data-k="inputs"', 1)[1].split('data-k="none"', 1)[0])
    assert inputs.count("Conditional probability") == 2 and inputs.count("Analyst input") == 2
    assert inputs.count("|30.0%|") == 2 and "ours" not in inputs
    w.store.close()


def test_an_event_its_statements_leave_open_shows_what_it_has_and_nothing_that_names_a_fault(tmp_path):
    """MATH.md 1.2; ONTOLOGY.md 2.1; TOOLS.md 5: E's chance given D is 0.5 and nothing says it given not D, so with D at
    0.30 its statements leave E anywhere from 0.15 to 0.85. Its row and its lead show that range in the probability's
    place, and nothing where its P&L would be; opened, each outcome shows what it does against the baseline, which
    needs no probability, the baseline's column blank; its probability opens to its floor and its ceiling with what
    each rests on. Nothing says the event is unfinished or what would settle it."""
    import re

    from harness.render import Reader, event_view, ledger_panel, view
    from test_impact import channel, holding_world
    w = holding_world(tmp_path)
    d = w.prop("D")
    w.link(d, "asks", w.market("D", "D?", 0.28, 0.32))
    e = w.prop("E", baseline="no")
    w.chances(e, {"given": [d], "value": 0.5})
    w.link("portfolio", "watches", e)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    text = lambda html: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))  # noqa: E731
    faults = ("finished", "Why", "settle", "restated", "cannot", "open")
    line = ledger_panel(Reader(w.store)).split(f'data-k="{e}"', 1)[1].split("</li>", 1)[0]
    assert "15.0%–85.0%" in text(line) and "$" not in text(line) and not any(f in line for f in faults)
    opened = event_view(Reader(w.store), e)["html"]
    lead = text(opened.split('class="strip"', 1)[1].split('data-k="outcomes"', 1)[0])
    assert "15.0%–85.0%" in lead and "Yes" not in lead
    grid = opened.split('data-k="outcomes"', 1)[1].split("</table>", 1)[0]
    row = grid.split("Against the baseline", 1)[1].split("</tr>", 1)[0]
    cells = re.findall(r'<td data-outcome="(\w+)" class="n">(.*?)</td>', row)
    assert [c[0] for c in cells] == ["yes", "no"] and cells[1][1] == ""  # the baseline's column blank
    assert re.search(r"−\$[\d,]+", cells[0][1]) and "P&amp;L" not in grid  # against the baseline, never as P&L
    assert "Each holding against the baseline" in grid and not any(f in text(opened) for f in faults[:5])
    working = text(view(w.store, f"x:p:{e}")["html"])
    assert "Forecast range" in working and "What sets the ceiling" in working
    assert "The chance of" in working and not any(f in working for f in faults[:5])
    w.store.close()


def test_a_stretched_book_is_a_step_of_its_probabilitys_working_and_its_market_reads_as_quoted(tmp_path):
    """MATH.md 1.2: where the statements stretch a taken book, the probabilities are read within it stretched, so how far
    it was stretched is a step of the probability's working, in ordinary ink beneath the book's bid and ask; the
    event's drawing shows the book as its venue quotes it, at its own bid and ask, its proposition's line opening the
    proposition. B, the way of E that A's 0.9 of E's 0.30 leaves, reads 0.03 within its book stretched 0.07
    below its bid; F, a chance of 0.40 stated on its own, stands 0.06 above its own book's ask."""
    import re

    from breakdown import World
    from harness.render import Reader, event_view, operand, view
    w = World(tmp_path / "scratch-stretch")
    e, a, b = w.prop("E", chance=0.30), w.prop("A"), w.prop("B")
    w.link(b, "asks", w.market("B", "B?", 0.10, 0.14))
    w.link("portfolio", "watches", e)
    w.link(e, "way", a, side="yes")
    w.chances(a, {"given": [e], "value": 0.9})
    w.link(e, "way", b, side="yes")
    text = lambda html: re.sub(r"<[^>]+>", "", html)  # noqa: E731
    shown = view(w.store, f"x:p:{b}")["html"]
    assert f"Stretched below its bid{operand(0.07)}" in text(shown) and "within its book as stretched" in text(shown)
    assert "held" not in shown  # a step of the working, in ordinary ink
    drawing = event_view(Reader(w.store), e)["html"].split('data-k="mv"', 1)[1].split('data-k="history"', 1)[0]
    assert "tretched" not in drawing and 'title="YES bid 10¢ · ask 14¢"' in drawing  # the book as its venue quotes it
    assert f'data-go="{b}"' in drawing

    f = w.prop("F")
    w.link(f, "asks", w.market("F", "F?", 0.30, 0.34))
    w.link("portfolio", "watches", f)
    w.chances(f, {"value": 0.40})
    shown = text(view(w.store, f"x:p:{f}")["html"])
    assert f"stretched {operand(0.06)} above its ask" in shown and "Markets that ask it" in shown
    w.store.close()


def test_what_the_system_computes_reads_in_whole_dollars_and_parts_add_to_their_whole_as_shown():
    """Money the system computes reads in whole dollars on every portfolio, halves away from zero, and a figure that is
    nothing as shown reads "–"; parts shown together add to their whole as shown, whatever their signs;
    a chart's axis marks its places in thousands or millions."""
    from harness.render import amount, as_shown, axis_label, either, whole_parts
    assert amount(-39_803.17) == f"{MINUS}$39,803" and amount(1_187.5) == "+$1,188"
    assert amount(1_234_567.4) == "+$1,234,567" and amount(-0.49) == "\u2013" and amount(0.5) == "+$1"
    assert amount(250.0, sign=False) == "$250" and as_shown(-2.5) == -3 and as_shown(2.49) == 2
    assert either(8_413.27) == "\u00b1$8,413" and either(8_413.27, cents=True) == "\u00b1$8,413.27"
    for parts, whole in (([-11_679.84, -9_824.1, -6_061.9, -4_742.7, 348.6, 293.2, 51.4], -31_615.34),
                         ([0.4, 0.4, 0.4, 0.4], 1.6), ([-149.4, 149.4, 49.4], 49.4)):
        shown = whole_parts(parts, whole)
        assert sum(shown) == as_shown(whole)  # they add to the whole as it is shown
        assert all(abs(x - y) < 1 for x, y in zip(shown, parts))  # each within a dollar of what it is
    assert [axis_label(t) for t in (-40_000, 0, 2_500, 20_000, 1_500_000)] == \
        [f"{MINUS}$40k", "Today", "+$2.5k", "+$20k", "+$1.5M"]


def test_every_move_is_a_percent_a_yields_change_in_percentage_points():
    """ledger.event_now gives a factor quoted as a yield its `yield` and its move in percentage points of it; the page
    reads every other move as a percent to two decimals, and a yield's change in percentage points, its level staying a
    percent."""
    from harness.render import factor_move
    assert factor_move({"factor": "factor:2", "yield": 4.94}, 0.5) == "+0.50 pp"
    assert factor_move({"factor": "factor:2", "yield": 4.94}, -0.25) == f"{MINUS}0.25 pp"
    assert factor_move({"factor": "factor:1"}, 0.0212) == "+2.12%"
    assert factor_move({"factor": "factor:1"}, None) == ""


# ---------------------------------------------------------------- a figure's working follows its value (ONTOLOGY.md 2)

def _words(ctx, nid):
    return {"text": ctx.object(nid)["stated"]["text"]}


def _twice(ctx, nid):
    return {"words": ctx.value("words", nid, ref=True)}


def test_a_figures_full_details_open_its_values_working_after_the_value_is_worked_out_again(make_store, caplog):
    """A figure links its value's working by the value, so the link keeps opening, and opens the working the value has
    now, after the Computation it was drawn from is replaced and removed. A Computation the conversation or
    the record names opens as itself; one the store no longer keeps is not linked, and the server's log says so."""
    from harness.derive import Derivation, Registry
    from harness.render import Reader, full, view, working

    words = Derivation("words", 1, "Note", _words, lambda m, load: {"n": len(m["text"].split())})
    twice = Derivation("twice", 1, "Note", _twice, lambda m, load: {"twice": 2 * load(m["words"])["n"]})
    store = make_store("working-test", registry_=Registry([words, twice]))
    note = store.note("one two three")["id"]
    with store.lock:
        drawn = store.engine.value("twice", note).basis
        link = full(Reader(store), drawn)
    assert store.computation_of(drawn) == {"derivation": "twice", "subject": note}
    target = f"raw:value:twice:{note}"
    assert f'data-go="{target}"' in link

    store.edit(note, "text", "one")  # the value is worked out again on its next read, and the one drawn goes
    with store.lock:
        opened = view(store, target)
        now = store.engine.value("twice", note).basis
    assert now != drawn and store.computation_of(drawn) is None
    assert f"&quot;id&quot;: &quot;{now}&quot;" in opened["html"] and "&quot;twice&quot;: 2" in opened["html"]
    assert view(store, "raw:" + drawn)["title"] == "Not found"  # an id is the one Computation it named

    with store.lock, caplog.at_level("WARNING", logger="harness.render"):
        assert working(Reader(store), drawn) is None and full(Reader(store), drawn) == ""
    assert drawn in caplog.text

    loose = store.put_computation("loose", 1, None, "h", {}, {"x": 1})  # a Computation of no subject opens by its id
    assert working(Reader(store), loose) == "raw:" + loose
    assert working(Reader(store), "jdg:1") == "raw:jdg:1"


def test_the_portfolios_volatility_keeps_its_place_while_a_reply_works_it_out_again():
    """The volatility line stays in the portfolio panel while the minute's prices have it worked out again on a reply's
    turn (render.cache_only, read pending): it is keyed, with the page's placeholder, so the page keeps the figure it
    shows until the new one arrives (app.js morph). Read between replies, where every value is computed, an absent one
    is no line."""
    from types import SimpleNamespace
    from harness.render import _volatility_line

    def reader(value, pending):
        got = {"volatility": {"value": value, "basis": "cmp:1"}} if value is not None else {}
        return SimpleNamespace(derived=lambda eid, kind, fields=None: got, pending=pending, waits=lambda *a: False)

    shown = _volatility_line(reader(33140.79, pending=False))
    assert 'data-k="pvol"' in shown and "\u00b1$33,141" in shown
    waiting = _volatility_line(reader(None, pending=True))
    assert 'data-k="pvol"' in waiting and 'class="wait"' in waiting
    assert _volatility_line(reader(None, pending=False)) == ""


def test_dollar_cash_has_no_move_and_cash_in_another_currency_moves_with_its_rate():
    """Cash in dollars responds to nothing, so the portfolio panel and the download show it no move; cash in another
    currency moves with its rate, and a share or a fund moves with its price (MATH.md 3.1)."""
    from harness.render import _has_move
    assert not _has_move({"to": "cash:USD"})
    assert _has_move({"to": "cash:EUR"}) and _has_move({"to": "name:NVDA", "stated": {"ticker": "NVDA"}})


def test_cash_opens_as_the_holding_it_is_and_only_cash_in_another_currency_shows_a_move(tmp_path):
    """MATH.md 3.1: dollar cash and cash in a currency are holdings, cash:{CURRENCY}. The portfolio panel and the
    download give dollar cash no move, since it responds to nothing; cash in another currency moves with its rate. Each
    opens as the holding it is, never as a listing that shares its code (USD is also a fund's ticker); a chip names it
    as the page does, with its value."""
    from massive_data import SESSIONS, put_rate
    from test_portfolio import flat_prices, hold, names
    from harness.derive import default_registry
    from harness.render import Reader, chips, ledger_markdown, portfolio_panel, view
    from harness.store import Store

    store = Store.create(tmp_path / "scratch-cash", registry=default_registry())
    try:
        flat_prices(store)
        put_rate(store, "C:EURUSD", {d: 1.10 for d in SESSIONS[-3:]}, base="EUR", quote="USD")
        names(store, ("NVDA", "CS"))
        hold(store, {"ticker": "NVDA", "dollars": 1000.0}, {"cash": 500.0}, {"cash": 200.0, "currency": "eur"})
        holds = {h["to"]: h["id"] for h in store.links_of("portfolio", "holds")}
        with store.lock:
            panel = portfolio_panel(Reader(store))
            dollars_ = view(store, "cash:USD")
            euros = view(store, "cash:EUR")
            chip = chips(store, ["cash:USD"])["cash:USD"]
        row = lambda hid: panel.split(f'data-k="{hid}"', 1)[1].split("</li>", 1)[0]  # noqa: E731
        assert 'class="mv' not in row(holds["cash:USD"]) and 'class="mv' in row(holds["cash:EUR"])
        assert dollars_["title"] == "Cash" and "$500.00" in dollars_["html"] and "Day change" not in dollars_["html"]
        assert euros["title"] == "Cash in EUR" and "$220.00" in euros["html"] and "Day change" in euros["html"]
        assert (chip["label"], chip["value"]) == ("cash", "$500.00")
        lines = {ln.split(" | ")[0]: ln for ln in ledger_markdown(store).splitlines() if ln.startswith("| Cash")}
        assert lines["| Cash"].endswith("|  |") and lines["| Cash in EUR"].endswith("| $0.00 (0.00%) |")
    finally:
        store.close()


def test_a_chip_works_out_only_the_figure_it_shows(tmp_path, monkeypatch):
    """A chip shows an object's title and one figure: an event's chip its probability, a holding's its value.
    It reads that figure alone, so it works out neither the event's row nor its distribution, nor the portfolio's
    factor exposures. The page reads every chip again as the store changes, so a chip that worked out the whole object
    held the store for seconds at each change."""
    from test_impact import channel, event, world
    from harness.derive import engine as engine_mod
    from harness.render import chips, portfolio_view, Reader
    w = world(tmp_path, g={"SPY": 10_000.0})
    e = event(w, p=0.3)
    w.link(e, "consequence", channel(w, "SPY"), shock=-0.1)
    store = w.store
    asked: list[str] = []
    value = engine_mod.Engine.value
    monkeypatch.setattr(engine_mod.Engine, "value", lambda self, name, subject, *a, **k: (
        asked.append(name), value(self, name, subject, *a, **k))[1])
    with store.lock:
        got = chips(store, [e])[e]
    assert got["value"] == "30.0%"
    assert "impact" not in asked and "distribution" not in asked, sorted(set(asked))
    held = next(h["to"] for h in store.links_of("portfolio", "holds"))
    asked.clear()
    with store.lock:
        shown = chips(store, [held])[held]
        view_ = portfolio_view(Reader(store), factors=False)
    assert shown["value"].startswith("$") and view_["g"] is None
    assert "g" not in asked and "betas" not in asked, sorted(set(asked))


def test_the_markets_show_nothing_of_the_judge(tmp_path):
    """The member's page shows nothing of the judge. An opened event's markets show each book its forecast rests on,
    with its price, bid and ask and depth, and each proposition by its title; none carries a reading of its texts,
    whether they describe the same outcomes or not, and a Judgment opens to nothing. The readings stay in the store
    and the record."""
    from html import unescape
    from breakdown import World
    from harness.render import Reader, event_view, view
    w = World(tmp_path / "scratch-differs", {("implication", "A", "B"): 0.2, ("market_converse", "B", "Q?"): 0.3})
    try:
        a, b = w.prop("A"), w.prop("B")
        w.link(a, "asks", w.market("1", "P?", 0.40, 0.45))
        w.link(a, "implies", b)
        w.link(b, "asks", w.market("2", "Q?", 0.20, 0.42))  # B's ask sets A's ceiling, so A's forecast rests on it
        w.link("portfolio", "watches", a)
        store = w.store
        with store.lock:
            opened_event = unescape(event_view(Reader(store), a)["html"])
            judged = [r["id"] for r in store.db.execute("SELECT id FROM judgment")]
            opened = [view(store, j) for j in judged]
        drawing = opened_event.split('data-k="mv"', 1)[1]
        assert 'title="YES bid 20¢ · ask 42¢"' in drawing and 'title="YES bid 40¢ · ask 45¢"' in drawing
        assert "x:check" not in opened_event and "judge" not in opened_event.lower() and ">same<" not in opened_event
        assert "can resolve" not in opened_event and "can be true where" not in opened_event
        assert judged and all(o["title"] == "Not found" for o in opened)
    finally:
        w.store.close()


def test_a_shock_stated_as_a_factors_residual_opens_to_its_working(tmp_path):
    """MATH.md 1.3: a shock on a factor states its whole move against the baseline, or its residual. The page says which
    wherever the shock is shown: the consequence opens as the shock to the factor, with which it states beside the
    figure; its working is the factor's residual, then what the factors above give it, the whole move they come to,
    and that move as the grid shows it; its chip names it; and an outcome says the factor is set by its own residual.
    A whole move still reads as one, and a factor that moves with the stated ones says so."""
    from html import unescape
    from test_impact import channel, event, world
    from harness import schema
    from harness.ledger import MOVES_WITH
    from harness.render import _how_set_there, chips, view
    w = world(tmp_path, g={"SPY": 10_000.0, "UUP": 5_000.0},
              coefficients={"UUP": {"SPY": 1.4}})
    semis, spy = channel(w, "UUP"), channel(w, "SPY")
    e = event(w)
    own = w.store.link(e, "consequence", semis, {"shock": {"value": -0.1, "against": schema.FOLLOWS,
                                                         "note": "Beyond the market."}})["id"]
    shock = w.store.link(e, "consequence", spy, {"shock": {"value": -0.05, "note": "The market falls."}})["id"]
    store = w.store
    with store.lock:
        opened = view(store, own)
        chip, other = (chips(store, [own, shock])[x]["value"] for x in (own, shock))
    html = unescape(opened["html"])
    assert opened["title"].startswith("Shock to") and "the residual" in html
    assert "Residual" in html and "From the factors above" in html and "Whole move" in html
    assert "Factor shock" not in html and "own part" not in html.lower() and "what it follows" not in html
    # the compounded moves as what they are, adding, then the move they come to as the grid shows it: one figure each
    assert "\u22120.105361" in html and "\u22120.071811" in html and "\u22120.177171" in html
    assert "\u221216.24%" in html and "\u221210.5361%" not in html
    assert chip == "residual \u221210.00%" and other == "whole move \u22125.00%"
    assert _how_set_there(store, {"by": "own", "stated": {"value": -0.1, "against": schema.FOLLOWS}}) == \
        "its own residual"
    assert _how_set_there(store, {"by": "own", "stated": {"value": -0.05}}) == "its own whole move"
    assert _how_set_there(store, {"by": MOVES_WITH}) == MOVES_WITH


def test_a_market_standing_as_the_proposition_it_asks_reads_by_its_question_and_sits_in_the_drawing(tmp_path):
    """MATH.md 1.2: a market a chance is given, which no proposition asks, stands as the proposition its question
    defines. The page names it by its question wherever it names a proposition, in the event's drawing and in the
    probability's working: in the drawing it is a lane like any proposition's, its book at its quotes, and each
    conditional probability stands beside its whole condition, naming it by its question, once, its markdown
    emphasis rendered as its venue means it to read."""
    import re

    from breakdown import World, stated
    from harness.render import Reader, event_view, view
    w = World(tmp_path / "scratch-standing")
    m = w.market("m", "Will **it** happen?", 0.29, 0.31)  # a venue's markdown, rendered where the page names it
    e = w.prop("E")
    w.store.edit(e, "chances", [stated({"given": [m], "value": 0.8}), stated({"given_not": [m], "value": 0.05})])
    w.link("portfolio", "watches", e)
    text = lambda html: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))  # noqa: E731
    opened = event_view(Reader(w.store), e)["html"]
    drawing = opened.split('data-k="mv"', 1)[1].split('data-k="history"', 1)[0]
    shown = text(opened)
    assert m not in shown and "No market bears" not in shown and "Will it happen?" in text(drawing) and "**" not in shown
    inputs = text(opened.split('data-k="inputs"', 1)[1])
    assert "Will it happen?Conditional probability80.0%Analyst input" in inputs
    assert "NotWill it happen?Conditional probability5.0%Analyst input" in inputs
    assert 'title="YES bid 29¢ · ask 31¢"' in drawing
    assert m not in text(view(w.store, f"x:p:{e}")["html"])
    w.store.close()


def test_the_probability_draws_the_markets_its_forecast_rests_on_and_no_other(tmp_path):
    """MATH.md 1.2: an event's probability tab draws each market its forecast rests on: its own book, the books of
    the propositions its conditional probabilities are given, and those its market-implied range rests on. A
    proposition tied to the event only by a conditional probability of its own, given the event, is another event's
    breakdown: its market is read with the event's, and the event's forecast does not rest on it, so it is not drawn."""
    from breakdown import World, stated
    from harness.render import Reader, event_view
    w = World(tmp_path / "scratch-rests")
    e = w.prop("E")
    w.link(e, "asks", w.market("1", "Will E happen?", 0.59, 0.60))
    f = w.prop("F")
    w.link(f, "asks", w.market("2", "Will F happen?", 0.78, 0.84))
    w.store.edit(f, "chances", [stated({"given": [e], "value": 0.74}), stated({"given_not": [e], "value": 0.9})])
    w.link("portfolio", "watches", e)
    w.link("portfolio", "watches", f)
    with w.store.lock:
        of_e = event_view(Reader(w.store), e)["html"].split('data-k="mv"', 1)[1].split('data-k="history"', 1)[0]
        of_f = event_view(Reader(w.store), f)["html"].split('data-k="mv"', 1)[1].split('data-k="history"', 1)[0]
    assert 'title="YES bid 59¢ · ask 60¢"' in of_e and "Will F happen?" not in of_e and "bid 78.0%" not in of_e
    # F rests on E: E's book is drawn, and each of F's conditional probabilities names E's market once, by its question
    assert 'title="YES bid 59¢ · ask 60¢"' in of_f and 'title="YES bid 78¢ · ask 84¢"' in of_f
    w.store.close()
