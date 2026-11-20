"""The page reads the distribution, stated values, a following chance and held contracts as the engine returns them:
each outcome's mean and middle 90% on the event's date beside the tree's P&L today, a weight that contradicts the price said
as one, our estimate beside what it follows, and a held contract by its side and question."""
import re
from types import SimpleNamespace

from harness import render


def test_the_outcomes_on_the_events_date_read_the_engines_distribution_each_in_whole_dollars_in_its_tone():
    """Each outcome's 5th and 95th percentiles on the event's date are the engine's (MATH.md 1.8), in whole dollars, each
    in the tone of the money it gains or loses and opening the distribution's working. The mean there is in the
    outcome's working, not a row of the grid: beside its P&L today it reads as the same figure twice."""
    result = {
        "drawn_on": "2027-05-03", "mean": -125.0,
        "worlds": {"yes": {"p": 0.3, "mean": -400.0, "centre": -450.0, "quantiles": {"0.05": -1000.0, "0.95": 200.0}},
                   "no": {"p": 0.7, "mean": 100.0, "centre": 80.0, "quantiles": {"0.05": -300.0, "0.95": 800.0}}}}
    worlds = [{"id": "yes"}, {"id": "no"}]
    rows = "".join(render._percentile_rows(result, "cmp:42", worlds))
    text = re.sub(r"<[^>]+>", "|", rows)
    assert "Mean" not in text
    assert "5th percentile, May 3" in text and "|−$1,000|" in text and "|+$800|" in text
    assert rows.count('data-go="cmp:42"') == 4 and 'class="n v loss"' in rows and 'class="n v gain"' in rows


def test_the_picture_is_one_working_its_centres_the_ones_its_distribution_read():
    """The picture draws each outcome from the distribution alone: its probability, its mass, its percentiles and its
    centre. A row worked out again since the distribution was gives the titles, never a centre, so the picture never
    puts one working's centres on another's masses."""
    import json
    dist = {"drawn_on": "2027-05-03", "mean": -125.0, "bins": {"edges": [-1000.0, 0.0, 1000.0], "mass": [.3, .7]},
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


def test_the_outcomes_percentiles_are_absent_while_the_distribution_is():
    assert render._percentile_rows({}, None, [{"id": "yes"}]) == []


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
    assert '<h3 class="sub">Held</h3>' in market and ">20<" in market and "$13.00" in market
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
    assert opened["title"] == "Value of DEAL" and '<h3 class="sub">Value today</h3>' in opened["html"]
    assert "$40.00" in opened["html"] and "the deal terms" in opened["html"]
    event_html = render.view(w.store, a)["html"]
    assert "Values stated" in event_html and f'data-go="{yes}"' in event_html
    assert "Read events together" not in render.ledger_panel(render.Reader(w.store))
    # the weight the price implies, (30 - 25) / (40 - 25), under its label, the event's probability beneath it
    import re
    from html import unescape
    plain = lambda html: unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))  # noqa: E731
    implied = "Implied by today\u2019s price 33.3% Probability 30.0%"
    assert implied in plain(event_html) and implied in plain(opened["html"])
    # it opens to its working: today's price between the value stated for each side, with no formula
    working = f'data-go="x:implied:{a}:name:DEAL"'
    assert working in event_html and working in opened["html"]
    shown = plain(render.view(w.store, f"x:implied:{a}:name:DEAL")["html"])
    for said in ("Implied by today\u2019s price 33.3%", "Value if No $25.00", "Price today $30.00", "Value if Yes $40.00"):
        assert said in shown, said
    assert "/" not in shown.split("Full details")[0] and "0000%" not in shown
    # the expected value's working names the values it comes from: 100 shares at 0.3 * $10 - 0.7 * $5
    swing = plain(render.view(w.store, f"x:swing:{a}")["html"])
    assert "Expected value" in swing and "\u2212$50" in swing
    assert "Values stated DEAL \u00b7 per share" in swing and "$40.00" in swing and "$25.00" in swing and implied in swing
    w.store.close()


def test_the_weight_a_price_implies_shows_where_it_is_a_weight_and_the_values_stand_as_stated_where_it_is_not():
    """MATH.md 1.6: values that put today's price beyond them give a figure outside 0 to 1, which is no weight; the analyst
    reads what they contradict in check() (1.10), and the page shows the values as stated and no weight for them. A
    weight shows under its label, the event's probability beneath it where it is given."""
    import re
    from html import unescape
    words = lambda *a: unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", render._implied_row(*a)))).strip()  # noqa: E731
    assert words(0.392, "prop:1", 0.3) == "Implied by today\u2019s price 39.2% Probability 30.0%"
    assert words(0.392) == "Implied by today\u2019s price 39.2%"
    assert words(2.1, "prop:1", 0.3) == "" and words(-0.3) == "" and words(None) == ""


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
    # a contract by its side and quantity; in each outcome, the chance it pays there and its P&L: no operands
    holding = plain(render.view(w.store, f"x:holding:{event_}:{yes}")["html"])
    assert "Side" in holding and "Yes" in holding and ">100<" in render.view(w.store, f"x:holding:{event_}:{yes}")["html"]
    assert "100.0%" in holding and "+$65" in holding and "×" not in holding
    swing = plain(render.view(w.store, f"x:swing:{event_}")["html"])
    assert "Yes, 100 contracts · No, 20 contracts" in swing and "×" not in swing
    outcome = plain(render.view(w.store, f"x:parts:{event_}:yes")["html"])  # the outcome's P&L, worked
    assert "Yes, 100 contracts · No, 20 contracts" in outcome and "×" not in outcome
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


def dated(monkeypatch) -> dict:
    """A store's portfolio as of a day, and an event whose outcomes are all known by its own date, before the horizon's
    end: the date the store draws them on (ledger.drawn_on, which reads the event's tree of ways and is proven against
    the distribution's own in test_distribution)."""
    monkeypatch.setattr(render, "drawn_on", lambda store, *events: "2027-03-31")
    return {"type_of": lambda oid: "Portfolio" if oid == "portfolio" else "Proposition",
            "stated_of": lambda oid: {"asof": "2026-11-04"} if oid == "portfolio" else {"window_end": "2027-03-31"}}


def test_an_opened_events_picture_has_its_place_while_its_draws_are_first_worked_out_and_is_drawn_once_they_are(monkeypatch):
    """The opened event shows without waiting for its distribution's draws (MATH.md 1.8): while they are worked out for
    the first time, the picture's place says so (the view says it is being made, and the page reads it again); once
    they are, it is drawn."""
    from contextlib import nullcontext
    worlds = [{"id": "yes", "title": "Yes", "world": True, "A": -400.0, "p": 0.3},
              {"id": "no", "title": "No", "world": True, "A": 100.0, "p": 0.7, "baseline": True}]
    store = SimpleNamespace(**dated(monkeypatch), computation_of=lambda cid: {"derivation": "distribution", "subject": "prop:1"})
    waiting = SimpleNamespace(store=store, engine=SimpleNamespace(placeholders=nullcontext),
                              value=lambda *a: SimpleNamespace(waiting=True, output=None, basis=None))
    html = render._outcome_picture(waiting, {"id": "prop:1", "outcomes": worlds})
    assert "Working out each outcome" in html
    assert "At Mar 31, 2027" in html and "data-dist" not in html  # the event's own date, before its draws are made
    out = {"drawn_on": "2027-03-31", "mean": 0.0, "quantiles": {"0.01": -900.0, "0.05": -500.0, "0.95": 500.0,
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


def test_a_picture_shown_keeps_its_place_while_a_reply_works_its_distribution_out_again(monkeypatch):
    """On a reply's turn, a distribution the page's read cannot give (worked out on the turn) is being worked out: the
    picture's place and the horizon's rows say so, so the page keeps the picture it shows until the new one comes
    (app.js morph). Read between replies, where every value is worked out, an absent distribution is no picture."""
    from contextlib import nullcontext
    worlds = [{"id": "yes", "title": "Yes", "world": True, "A": -400.0, "p": 0.3},
              {"id": "no", "title": "No", "world": True, "A": 100.0, "p": 0.7, "baseline": True}]
    store = SimpleNamespace(**dated(monkeypatch))
    reader = lambda pending: SimpleNamespace(store=store, engine=SimpleNamespace(placeholders=nullcontext),  # noqa: E731
                                             value=lambda *a: None, pending=pending)
    on_the_turn = render._outcome_picture(reader(True), {"id": "prop:1", "outcomes": worlds})
    assert "Working out each outcome" in on_the_turn
    assert render._outcome_picture(reader(False), {"id": "prop:1", "outcomes": worlds}) == ""
