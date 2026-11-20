"""The range a chance can take (MATH.md 1.2, TOOLS.md 5): the least and the most its figure can be with every link and every other
chance kept and each taken book within its bid and ask. A statement's return gives it for each chance it writes, under
its proposition, by what the chance is given as the analyst stated it (tools._structure, `chances`, the keys of the
chances written as the list stands after the write). Within it the chance holds with no book stretched and no
statement waiting; outside it, a book is stretched or the statements wait, as the return's `stretched` and
`cannot_hold` then say."""
from __future__ import annotations

import pytest

from breakdown import World
from harness.tools import _structure


@pytest.fixture
def two(tmp_path):
    """Two events on markets with nothing between them: A at 0.80 to 0.82, B at 0.40 to 0.42."""
    w = World(tmp_path / "scratch")
    a, b = w.prop("A"), w.prop("B")
    ma, mb = w.market("A", "A?", 0.80, 0.82), w.market("B", "B?", 0.40, 0.42)
    w.link(a, "asks", ma)
    w.link(b, "asks", mb)
    yield w, a, b, ma
    w.store.close()


def test_a_chance_between_two_events_returns_the_range_their_books_allow_by_what_it_is_given(two):
    """The chance of B given A is P(A and B) / P(A), which A's and B's books put between 0.20 / 0.80 and 0.42 / 0.80.
    Stated at 0.50, it lies within, and no book is stretched. Given A's market in place of A, which asks A, it is the
    same chance, shown as it was stated."""
    w, a, b, ma = two
    w.chances(b, {"given": [a], "value": 0.5})
    got = _structure(w.store, b, chances_of=[b])
    assert got["propositions"][b]["chances"] == [{"given": [a], "given_not": [], "value": 0.5, "range": [0.25, 0.525]}]
    assert "chances" not in got["propositions"][a] and not got.get("stretched")
    w.chances(b, {"given": [ma], "value": 0.5}, keep=False)
    got = _structure(w.store, b, chances_of=[b])
    assert got["propositions"][b]["chances"] == [{"given": [ma], "given_not": [], "value": 0.5, "range": [0.25, 0.525]}]
    assert "chances" not in _structure(w.store, b)["propositions"][b]  # only the chances a statement writes


def test_a_chance_outside_its_range_stretches_a_book_and_its_range_says_what_it_could_have_been(two):
    """Stated at 0.90, the chance of B given A lies past 0.525: a book is stretched, and the range is the same."""
    w, a, b, _ = two
    w.chances(b, {"given": [a], "value": 0.9})
    got = _structure(w.store, b, chances_of=[b])
    assert got["propositions"][b]["chances"] == [{"given": [a], "given_not": [], "value": 0.9, "range": [0.25, 0.525]}]
    assert any(max(s["below"], s["above"]) > 1e-6 for s in got["stretched"].values())


def test_a_chance_the_analysts_own_chances_cannot_hold_returns_its_range_beside_what_cannot_hold(tmp_path):
    """One event at 0.42 and another at 0.12, each on a chance of its own: the second given the first is at most
    0.12 / 0.42. Stated at 0.90 it cannot hold, and the range says what it could have been. B's chance on its own is
    ranged with B given A kept: B holds 0.90 of A's 0.42, and A's other 0.10 lies outside it, so 0.12 lies below."""
    w = World(tmp_path / "scratch")
    a, b = w.prop("A", chance=0.42), w.prop("B", chance=0.12)
    w.chances(b, {"given": [a], "value": 0.9})
    got = _structure(w.store, b, chances_of=[b])
    assert got["cannot_hold"]
    own, given_a = got["propositions"][b]["chances"]
    assert own == {"given": [], "given_not": [], "value": 0.12, "range": [0.378, 0.958]}
    assert given_a == {"given": [a], "given_not": [], "value": 0.9, "range": [0.0, round(0.12 / 0.42, 6)]}
    w.store.close()


def test_a_chance_whose_other_statements_cannot_hold_among_themselves_has_no_range(tmp_path):
    """X implies Y, X at 0.5 and Y at 0.3 cannot hold together, whatever the chance of Z given X is: its range is
    null, and cannot_hold names the three that conflict."""
    w = World(tmp_path / "scratch")
    x, y, z = w.prop("X", chance=0.5), w.prop("Y", chance=0.3), w.prop("Z")
    w.link(x, "implies", y)
    w.chances(z, {"given": [x], "value": 0.4})
    got = _structure(w.store, z, chances_of=[z])
    assert got["propositions"][z]["chances"] == [{"given": [x], "given_not": [], "value": 0.4, "range": None}]
    assert z not in {s.get("of") for c in got["cannot_hold"] for s in c["statements"] if isinstance(s, dict)}
    w.store.close()
