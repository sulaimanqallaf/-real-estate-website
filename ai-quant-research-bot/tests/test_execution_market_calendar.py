"""execution/market_calendar.py - the NYSE-calendar-aware run/skip
decision (final production-safe daily scheduling milestone). Uses the
REAL `pandas_market_calendars` NYSE calendar, not a mock - the whole
point under test is that holidays/early closes/weekends come from a
maintained data source, not a hand-rolled list that goes stale."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import market_calendar as mc


# --- market_day() / target_run_time_utc() ------------------------------------


def test_a_normal_trading_day_closes_at_16_00_eastern():
    day = mc.market_day("2026-10-28")  # Wednesday, regular session
    assert day.is_trading_day is True
    assert day.is_early_close is False
    close_ny = day.market_close_utc.astimezone(mc.NY_TZ)
    assert (close_ny.hour, close_ny.minute) == (16, 0)


def test_a_weekend_is_not_a_trading_day():
    day = mc.market_day("2026-10-31")  # Saturday
    assert day.is_trading_day is False
    assert day.market_close_utc is None


def test_christmas_is_a_full_holiday_not_an_early_close():
    day = mc.market_day("2026-12-25")
    assert day.is_trading_day is False


def test_the_day_after_thanksgiving_is_a_real_early_close():
    day = mc.market_day("2026-11-27")
    assert day.is_trading_day is True
    assert day.is_early_close is True
    close_ny = day.market_close_utc.astimezone(mc.NY_TZ)
    assert (close_ny.hour, close_ny.minute) == (13, 0)


def test_target_run_time_is_exactly_30_minutes_after_actual_close_on_a_normal_day():
    target = mc.target_run_time_utc("2026-10-28")
    target_ny = target.astimezone(mc.NY_TZ)
    assert (target_ny.hour, target_ny.minute) == (16, 30)


def test_target_run_time_is_exactly_30_minutes_after_actual_close_on_an_early_close_day():
    """Explicit documentation/requirement: an early-close session runs
    30 minutes after its ACTUAL close, never a fixed 16:30."""
    target = mc.target_run_time_utc("2026-11-27")
    target_ny = target.astimezone(mc.NY_TZ)
    assert (target_ny.hour, target_ny.minute) == (13, 30)


def test_target_run_time_is_none_on_a_non_trading_day():
    assert mc.target_run_time_utc("2026-12-25") is None


# --- decide_after_close_run(): the actual scheduling decision ---------------


def test_skips_before_todays_target_time():
    now = datetime(2026, 10, 28, 20, 29, tzinfo=timezone.utc)  # 16:29 EDT, 1 min before target
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "skip"
    assert "before" in reason


def test_runs_exactly_at_todays_target_time():
    now = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc)  # exactly 16:30 EDT
    decision, _ = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "run"


def test_skips_weekends_regardless_of_time_of_day():
    now = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)  # Saturday evening
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "skip"
    assert "not a NYSE trading day" in reason


def test_skips_a_nyse_holiday():
    now = datetime(2026, 12, 25, 23, 0, tzinfo=timezone.utc)
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "skip"
    assert "not a NYSE trading day" in reason


def test_early_close_day_runs_at_its_own_earlier_target_not_at_1630():
    now = datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)  # 13:30 EST
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "run"
    assert "early-close" in reason


def test_early_close_day_still_skips_before_its_own_earlier_target():
    now = datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc)  # 15:00 EST - AFTER 13:30 target already...
    # pick a time clearly BEFORE the early-close target instead:
    now = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)  # 13:00 EST, exactly at close, before +30min target
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "skip"


def test_duplicate_run_protection_skips_once_already_succeeded_today():
    now = datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc)  # well after target
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=True)
    assert decision == "skip"
    assert "already ran" in reason


def test_late_wake_from_sleep_still_catches_up_the_same_day():
    """Mac asleep/off through the whole target window, wakes late in the
    evening (still the same NY calendar day) - must still run (a late
    same-day report), not silently lose the day."""
    now = datetime(2026, 10, 28, 23, 59, tzinfo=timezone.utc)  # 19:59 EDT - hours after the 16:30 target
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    assert decision == "run"


def test_late_wake_does_not_retroactively_run_for_a_day_that_has_already_passed():
    """Crossing into a NEW NY calendar day must evaluate THAT day's own
    schedule, never try to "catch up" on the prior day once it's over."""
    # 2026-10-29 00:05 UTC is still 2026-10-28 20:05 EDT (same NY day) -
    # pick a genuinely later UTC instant that is a new NY calendar day:
    now = datetime(2026, 10, 29, 5, 0, tzinfo=timezone.utc)  # 01:00 EDT on Oct 29 - a new NY day
    decision, reason = mc.decide_after_close_run(now, already_succeeded_today=False)
    # Oct 29 2026 is a Thursday trading day; 01:00 ET is before ITS OWN
    # 16:30 target, so this correctly skips as "too early" for the NEW
    # day, not as a catch-up for the 28th:
    assert decision == "skip"
    assert "before" in reason


# --- the core UK/US DST-mismatch proof ---------------------------------------
#
# UK (BST) ends on the LAST Sunday of October; US (EDT) ends on the
# FIRST Sunday of November. In 2026: UK -> GMT on Oct 25, US -> EST on
# Nov 1. Between those two dates, UK local time is GMT (UTC+0) while
# NY local time is STILL EDT (UTC-4) - four hours apart. If this module
# ever accidentally used UK/host-local time instead of real NY time,
# these tests would see the wrong hour and fail.


def test_decision_uses_real_ny_time_not_uk_time_during_the_dst_mismatch_window():
    # 2026-10-28 (Wed, regular trading day). Target = 16:30 EDT = 20:30 UTC.
    # If this code mistakenly treated "now" as 16:30 UK-local (GMT, during
    # the mismatch window) instead of computing real NY time, it would
    # compare against 16:30 UTC - four hours too early - and incorrectly
    # say "run" at 16:30 UTC. It must not:
    now_as_if_1630_uk_time = datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc)
    decision, _ = mc.decide_after_close_run(now_as_if_1630_uk_time, already_succeeded_today=False)
    assert decision == "skip"  # it's only 12:30 EDT in NY - correctly too early

    # The REAL NY 16:30 EDT instant is 20:30 UTC, and that's what must run:
    now_real_ny_1630 = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc)
    decision, _ = mc.decide_after_close_run(now_real_ny_1630, already_succeeded_today=False)
    assert decision == "run"


def test_decision_is_correct_on_both_sides_of_the_us_spring_forward_transition():
    # US DST starts 2026-03-08 (2am local -> EDT). 2026-03-06 (Fri, before
    # transition) closes at 16:00 EST = 21:00 UTC, target 21:30 UTC.
    before = mc.market_day("2026-03-06")
    assert before.market_close_utc.astimezone(mc.NY_TZ).hour == 16
    decision, _ = mc.decide_after_close_run(datetime(2026, 3, 6, 21, 30, tzinfo=timezone.utc), False)
    assert decision == "run"

    # 2026-03-09 (Mon, after transition) closes at 16:00 EDT = 20:00 UTC,
    # target 20:30 UTC - an hour earlier in UTC than the pre-transition
    # Friday, purely because of the DST shift:
    decision, _ = mc.decide_after_close_run(datetime(2026, 3, 9, 20, 30, tzinfo=timezone.utc), False)
    assert decision == "run"
    decision, _ = mc.decide_after_close_run(datetime(2026, 3, 9, 20, 29, tzinfo=timezone.utc), False)
    assert decision == "skip"
