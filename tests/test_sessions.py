"""Each exchange's sessions, from its calendar (harness.sessions): what a calendar answers rests on the days asked, so a
computation gives the same in any process and on any day it runs."""

from __future__ import annotations

from datetime import date

from harness import sessions
from harness.derive import series


def test_a_calendar_answers_for_the_days_asked_whatever_day_it_is_built_on(monkeypatch):
    """A year well past today's has its holidays: New Year's Day 2030 falls on a Tuesday and the exchange is closed,
    and the sessions to a date in it count none of its holidays. Built over one year or over many, and in whichever
    order the questions come, a calendar answers each day alike."""
    monkeypatch.setattr(sessions, "_built", {})
    far = sessions.holidays("XNYS", 2030)
    assert date(2030, 1, 1) in far and date(2030, 12, 25) in far
    weekdays = sum(1 for k in range(365) if date(2030, 1, 1).toordinal() + k and
                   date.fromordinal(date(2030, 1, 1).toordinal() + k).weekday() < 5)
    series.nyse_holidays.cache_clear()
    try:
        assert series.sessions_after("2029-12-31", "2030-12-31") == weekdays - len(far)
    finally:
        series.nyse_holidays.cache_clear()
    one_year = {d: sessions.close_of("XNYS", d) for d in (date(2025, 11, 28), date(2025, 12, 24), date(2026, 1, 2))}
    monkeypatch.setattr(sessions, "_built", {})
    assert sessions.closes("XNYS", "2020-01-01", "2030-12-31")  # one span over many years first
    assert {d: sessions.close_of("XNYS", d) for d in one_year} == one_year
    assert sessions.holidays("XNYS", 2030) == far


def test_a_day_past_what_the_library_records_has_no_session():
    """Bombay's holidays are recorded to the end of 2026 (its calendar's bound_max): a day of 2027 has no calendar
    there, as before, whatever the day of the question."""
    assert sessions.calendar("XBOM", date(2026, 11, 2)) is not None
    assert sessions.calendar("XBOM", date(2027, 1, 4)) is None
    assert not sessions.is_session("XBOM", date(2027, 1, 4))
    assert sessions.closes("XBOM", "2026-12-28", "2027-01-08").keys() <= {"2026-12-28", "2026-12-29", "2026-12-30",
                                                                          "2026-12-31"}
