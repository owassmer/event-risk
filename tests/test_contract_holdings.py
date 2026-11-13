"""Held contracts are actual portfolio positions, priced by side and kept through edits."""
import pytest

from breakdown import World
from harness.store import Store, StoreError
from harness.derive import default_registry


def test_contracts_are_marked_in_the_portfolio_and_do_not_become_company_exposure(tmp_path):
    w = World(tmp_path / "contracts")
    mid = w.market("event", "Will it happen?", .30, .40)
    yes = w.store.link("portfolio", "holds", mid, {"side": "yes", "quantity": 100})
    no = w.store.link("portfolio", "holds", mid, {"side": "no", "quantity": 20})
    got = w.store.engine.value("holdings", "portfolio").output
    assert got["V"] == pytest.approx(1048)
    assert got["holds"][yes["id"]]["dollars"] == pytest.approx(35)
    assert got["holds"][no["id"]]["dollars"] == pytest.approx(13)
    looked = w.store.engine.value("lookthrough", "portfolio").output
    assert looked["e"] == {"name:CASH:USD": 1000}
    assert looked["owned"]["kind:derivative"]["dollars"] == pytest.approx(48)
    assert looked["by_holding"][yes["id"]] == {}
    w.store.edit(yes["id"], "quantity", 150)
    assert w.store.stated_of(yes["id"]) == {"side": "yes", "quantity": 150}
    assert w.store.engine.value("holdings", "portfolio").output["V"] == pytest.approx(1065.5)
    w.store.remove(no["id"])
    assert w.store.type_of(mid) == "Market"
    w.store.close()


def test_portfolio_accepts_a_contract_in_its_original_holdings(tmp_path):
    w = World(tmp_path / "given")
    mid = w.market("event", "Will it happen?", .30, .40)
    w.store.remove("portfolio")
    w.store.add("Portfolio", {"asof": "2026-11-04", "holdings": [
        {"cash": 100}, {"market": mid, "side": "no", "quantity": 10}]})
    assert w.store.engine.value("holdings", "portfolio").output["V"] == pytest.approx(106.5)
    w.store.close()


def test_a_settled_contract_uses_its_payout_instead_of_a_stale_book_and_keeps_zero_value_quantity(tmp_path):
    w = World(tmp_path / "settled-contract")
    mid = w.market("event", "Will it happen?", .30, .40)
    w.venues.records[mid]["market"].update({"closed": True, "umaResolutionStatus": "resolved",
                                          "outcomePrices": '["0", "1"]', "closedTime": "2026-11-04T12:00:00Z"})
    yes = w.store.link("portfolio", "holds", mid, {"side": "yes", "quantity": 100})
    no = w.store.link("portfolio", "holds", mid, {"side": "no", "quantity": 20})
    got = w.store.engine.value("holdings", "portfolio").output
    assert got["V"] == 1020
    assert got["holds"][yes["id"]]["quantity"] == 100
    assert got["holds"][yes["id"]]["dollars"] == 0
    assert got["holds"][no["id"]]["dollars"] == 20
    assert got["holds"][yes["id"]]["price_from"] == "settlement"
    w.store.close()


@pytest.mark.parametrize("fields", [{"quantity": 2}, {"side": "yes", "dollars": 10},
                                    {"side": "no", "quantity": 10, "ticker": "SPY"}])
def test_contract_position_names_its_side_and_actual_quantity(tmp_path, fields):
    w = World(tmp_path / "refused")
    mid = w.market("event", "Will it happen?", .30, .40)
    with pytest.raises(StoreError, match="contract held names"):
        w.store.link("portfolio", "holds", mid, fields)
    assert w.store.type_of(mid) is None
    w.store.close()


def test_two_values_for_one_deal_survive_reopening(tmp_path):
    # A cash target keeps this storage/migration check independent of external price observations.
    w = World(tmp_path / "worth")
    e = w.prop("Deal", chance=.4, baseline="no")
    for side, amount in (("yes", 40), ("no", 20)):
        w.store.link(e, "worth", "name:CASH:USD", {"world": side, "value": {
            "value": {"amount": amount}, "note": "The consideration in this outcome."}})
    w.store.close()
    reopened = Store.open(tmp_path / "worth", registry=default_registry())
    assert {x["stated"]["world"] for x in reopened.links_of(e, "worth")} == {"yes", "no"}
    reopened.close()
