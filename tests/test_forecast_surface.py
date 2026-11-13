"""The page reads the distribution, stated values, a following chance and held contracts as the engine returns them:
each outcome's mean and middle 90% at the horizon beside the tree's P&L today, a weight that contradicts the price said
as one, our estimate beside what it follows, and a held contract by its side and question."""
import re
from types import SimpleNamespace

from harness import render


def test_the_outcomes_at_the_horizon_read_the_engines_distribution_each_in_whole_dollars_in_its_tone():
    """Each outcome's 5th and 95th percentiles at the horizon's end are the engine's (MATH.md 1.8), in whole dollars, each
    in the tone of the money it gains or loses and opening the distribution's working. The mean there is in the
    outcome's working, not a row of the grid: beside its P&L today it reads as the same figure twice."""
    result = {
        "horizon_end": "2027-05-03", "mean": -125.0,
        "worlds": {"yes": {"p": 0.3, "mean": -400.0, "centre": -450.0, "quantiles": {"0.05": -1000.0, "0.95": 200.0}},
                   "no": {"p": 0.7, "mean": 100.0, "centre": 80.0, "quantiles": {"0.05": -300.0, "0.95": 800.0}}}}
    worlds = [{"id": "yes"}, {"id": "no"}]
    rows = "".join(render._horizon_rows(result, "cmp:42", worlds))
    text = re.sub(r"<[^>]+>", "|", rows)
    assert "Mean" not in text
    assert "5th percentile, May 3" in text and "|−$1,000|" in text and "|+$800|" in text
    assert rows.count('data-go="cmp:42"') == 4 and 'class="n v loss"' in rows and 'class="n v gain"' in rows


def test_the_picture_is_one_working_its_centres_the_ones_its_distribution_read():
    """The picture draws each outcome from the distribution alone: its probability, its mass, its percentiles and its
    centre. A row worked out again since the distribution was gives the titles, never a centre, so the picture never
    puts one working's centres on another's masses."""
    import json
    dist = {"horizon_end": "2027-05-03", "mean": -125.0, "bins": {"edges": [-1000.0, 0.0, 1000.0], "mass": [.3, .7]},
            "quantiles": {"0.01": -900.0, "0.05": -800.0, "0.95": 700.0, "0.99": 900.0},
            "worlds": {"yes": {"p": 0.3, "mass": [.3, 0], "centre": -450.0,
                               "quantiles": {"0.01": -900.0, "0.05": -800.0, "0.95": -100.0, "0.99": -50.0}},
                       "no": {"p": 0.7, "mass": [0, .7], "centre": 80.0,
                              "quantiles": {"0.01": 10.0, "0.05": 20.0, "0.95": 700.0, "0.99": 900.0}}}}
    row = {"outcomes": [{"id": "yes", "title": "If it happens", "world": True, "A": -999.0},
                        {"id": "no", "title": "If it does not", "world": True, "A": 999.0, "baseline": True}]}
    html = render._picture_of(dist, None, row)
    spec = json.loads(re.search(r'data-dist="([^"]+)"', html).group(1).replace("&quot;", '"'))
    assert [(x["title"], x["centre"], x["p"]) for x in spec["rows"]] == [("If it happens", -450.0, "30.0%"),
                                                                         ("If it does not", 80.0, "70.0%")]


def test_the_outcomes_at_the_horizon_are_absent_while_the_distribution_is():
    assert render._horizon_rows({}, None, [{"id": "yes"}]) == []


def test_following_a_name_keeps_an_unidentified_weight_open():
    reader = SimpleNamespace(store=SimpleNamespace(type_of=lambda _: None))
    html = render._following(reader, {"chances": [{"follow": {
        "name": "name:TARGET", "responsiveness": 0.5, "q_then": .4, "q_now": None,
        "then_at": "2026-11-01", "now_at": "2026-11-12", "why": "The values of the ways remain open."}}]})
    assert 'data-go="name:TARGET"' in html and "Implied by its price" in html
    assert "40.0%" in html and ">0.0%<" not in html
    assert "The values of the ways remain open." in html
    reader = SimpleNamespace(store=reader.store, output=lambda *args: None)
    ours = render._following(reader, {"chances": [{"stated": .25, "value": .3, "follow": {
        "market": "market:kalshi:X", "responsiveness": 0.5, "q_then": .4, "q_now": .5,
        "then_at": "2026-11-01", "now_at": "2026-11-12"}}]})
    assert "Forecast" in ours and "Stated, " in ours and "Now, " in ours and "25.0%" in ours and "30.0%" in ours
    assert "in odds" not in ours


def test_held_contracts_show_the_side_and_question_in_the_portfolio_and_the_market(tmp_path):
    from breakdown import World
    w = World(tmp_path / "contracts")
    mid = w.market("event", "Will it happen?", .30, .40)
    w.store.link("portfolio", "holds", mid, {"side": "no", "quantity": 20})
    html = render.portfolio_panel(render.Reader(w.store))
    assert "No · Will it happen?" in html and f'data-go="{mid}"' in html
    assert render.name_of(w.store, mid) == "No · Will it happen?"  # as the largest contributors name it
    market = render.view(w.store, mid)["html"]
    assert "Your holdings" in market and "20 contracts" in market and "$13.00" in market
    w.store.close()


def test_worth_values_open_to_their_working(tmp_path):
    from test_impact import world, event, channel
    from test_worth import holding, worth
    w = world(tmp_path)
    holding(w, "DEAL", 30, 3000)
    a, b = event(w, "Deal closes", .3), event(w, "Market rises", .4)
    yes = worth(w, a, "name:DEAL", "yes", {"amount": 40})
    worth(w, a, "name:DEAL", "no", {"amount": 25})
    w.link(b, "consequence", channel(w, "SPY"), shock=0.1)
    opened = render.view(w.store, yes)
    assert opened["title"] == "Value of DEAL" and "Value today in each world" in opened["html"]
    assert "$40.00" in opened["html"] and "the deal terms" in opened["html"]
    event_html = render.view(w.store, a)["html"]
    assert "Values stated" in event_html and f'data-go="{yes}"' in event_html
    assert "Read events together" not in render.ledger_panel(render.Reader(w.store))
    w.store.close()


def test_a_weight_outside_zero_to_one_reads_as_contradicting_the_price():
    import re
    words = lambda x: re.sub(r"<[^>]+>", "", render._implied_words(x))  # noqa: E731
    assert "implies 39.2% for the event" in words(0.392)
    contradicting = words(2.1)
    assert "contradict today’s price" in contradicting and "210%" in contradicting and "implies 210%" not in contradicting
    assert words(None) == ""


def test_both_sides_of_a_held_contract_show_actual_quantity_and_payoff_operands(tmp_path):
    import re
    from test_impact import world, event
    from test_worth import government_curve
    w = world(tmp_path)
    government_curve(w, rate=0)
    event_ = event(w, p=.3)
    mid = w.market("contract", "Will E occur?", .3, .4)
    w.link(event_, "asks", mid)
    yes = w.store.link("portfolio", "holds", mid, {"side": "yes", "quantity": 100})["id"]
    w.store.link("portfolio", "holds", mid, {"side": "no", "quantity": 20})
    plain = lambda text: re.sub('<[^>]+>', '', text)
    holding = plain(render.view(w.store, f"x:holding:{event_}:{yes}")["html"])
    assert "100 × $1 × 100.0000% × (100.0000% − 0.0000%)" in holding
    assert "100 × $1 × 100.0000% × 100.0000% − $35.00" in holding
    swing = plain(render.view(w.store, f"x:swing:{event_}")["html"])
    assert "100 × $1 × 100.0000% × (100.0000% − 0.0000%)" in swing
    assert "20 × $1 × 100.0000% × (0.0000% − 100.0000%)" in swing
    outcome = plain(render.view(w.store, f"x:outcome:{event_}:yes")["html"])
    assert "20 × $1 × 100.0000% × 0.0000% − $13.00" in outcome
    w.store.close()


def test_option_receipt_distinguishes_observed_price_boundary_and_world_values():
    reader = SimpleNamespace(store=None)
    html = render._option_values(reader, "prop:1", {"valuation_terms": {"options": {"name:FUND": {"contracts": [{
        "reference": {"ticker": "SPY"}, "kind": "call", "strike": 100, "expiry": "2027-05-01",
        "value_today": 5, "volatility": 0, "values": {"yes": 30, "no": 0},
        "pricing_gap": 1, "bound": "coherent strike surface", "surface": {"adjustment": 2}}]}}}}, "name:FUND")
    assert "Observed price" in html and "$5.00" in html
    assert "Pricing boundary: coherent strike surface" in html and "Initial pricing volatility" in html
    assert "Pricing gap" in html and "+$1.00" in html and "Strike constraints" in html


def test_an_opened_events_picture_has_its_place_while_its_draws_are_first_worked_out_and_is_drawn_once_they_are():
    """The opened event shows without waiting for its distribution's draws (MATH.md 1.8): while they are worked out for
    the first time, the picture's place says so (the view says it is being made, and the page reads it again); once
    they are, it is drawn."""
    from contextlib import nullcontext
    worlds = [{"id": "yes", "title": "Yes", "world": True, "A": -400.0, "p": 0.3},
              {"id": "no", "title": "No", "world": True, "A": 100.0, "p": 0.7, "baseline": True}]
    store = SimpleNamespace(horizon_end=lambda: "2027-05-03",
                            computation_of=lambda cid: {"derivation": "distribution", "subject": "prop:1"})
    waiting = SimpleNamespace(store=store, engine=SimpleNamespace(placeholders=nullcontext),
                              value=lambda *a: SimpleNamespace(waiting=True, output=None, basis=None))
    html = render._outcome_picture(waiting, {"id": "prop:1", "outcomes": worlds})
    assert "Working out each outcome at the horizon" in html
    assert "At May 3, 2027" in html and "data-dist" not in html
    out = {"horizon_end": "2027-05-03", "mean": 0.0, "quantiles": {"0.01": -900.0, "0.05": -500.0, "0.95": 500.0,
                                                                   "0.99": 900.0},
           "bins": {"edges": [-1000.0, 0.0, 1000.0], "mass": [0.5, 0.5]},
           "worlds": {w["id"]: {"p": w["p"], "mean": w["A"], "centre": w["A"], "mass": [0.25, 0.25],
                                "quantiles": {"0.01": -900.0, "0.05": -500.0, "0.95": 500.0, "0.99": 900.0}}
                      for w in worlds}}
    ready = SimpleNamespace(store=store, engine=SimpleNamespace(placeholders=nullcontext),
                            value=lambda *a: SimpleNamespace(waiting=False, output=out, basis="cmp:7"))
    drawn = render._outcome_picture(ready, {"id": "prop:1", "outcomes": worlds})
    assert "data-dist=" in drawn and "Working out" not in drawn
    assert 'data-go="raw:value:distribution:prop:1"' in drawn  # its working opens by its value, as the value moves


def test_a_picture_shown_keeps_its_place_while_a_reply_works_its_distribution_out_again():
    """On a reply's turn, a distribution the page's read cannot give (worked out on the turn) is being worked out: the
    picture's place and the horizon's rows say so, so the page keeps the picture it shows until the new one comes
    (app.js morph). Read between replies, where every value is worked out, an absent distribution is no picture."""
    from contextlib import nullcontext
    worlds = [{"id": "yes", "title": "Yes", "world": True, "A": -400.0, "p": 0.3},
              {"id": "no", "title": "No", "world": True, "A": 100.0, "p": 0.7, "baseline": True}]
    store = SimpleNamespace(horizon_end=lambda: "2027-05-03")
    reader = lambda pending: SimpleNamespace(store=store, engine=SimpleNamespace(placeholders=nullcontext),  # noqa: E731
                                             value=lambda *a: None, pending=pending)
    on_the_turn = render._outcome_picture(reader(True), {"id": "prop:1", "outcomes": worlds})
    assert "Working out each outcome at the horizon" in on_the_turn
    assert render._outcome_picture(reader(False), {"id": "prop:1", "outcomes": worlds}) == ""
