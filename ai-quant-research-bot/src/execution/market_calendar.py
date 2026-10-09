"""NYSE-calendar-aware scheduling decision for the after-close daily run
(Daily Reliability & Safe Automation milestone, "final production-safe
daily scheduling setup" follow-up).

**Why this exists at all**: a fixed `launchd StartCalendarInterval` entry
like "Hour=16, Minute=30" fires at 16:30 in whatever timezone the Mac's
SYSTEM CLOCK is set to - NOT 16:30 America/New_York. If the Mac's system
timezone is anything other than US-Eastern (e.g. UK), that's already
wrong, and even if it happens to BE US-Eastern, UK and US daylight-saving
transitions land on DIFFERENT Sundays (UK: last Sunday of March/October;
US: second Sunday of March / first Sunday of November), so a naive
"local hour" schedule silently drifts by up to an hour for the ~1-2 week
mismatch window every spring and fall. The fix used here: NEVER ask
launchd to encode "16:30 Eastern" at all. `scripts/after_close.py` is
instead invoked frequently (`StartInterval`, not `StartCalendarInterval`
- see README) and this module makes the ACTUAL run/skip decision at
decision time using a timezone-AWARE `America/New_York` datetime
(`zoneinfo.ZoneInfo`, IANA tzdata) compared against the real NYSE
calendar - correct regardless of the host OS's own timezone or either
country's DST rule, by construction, not by schedule-math convention.

**Exchange calendar**: holidays/weekends/early closes come from
`pandas_market_calendars`'s `"NYSE"` calendar (a maintained, versioned
data source - not a hand-rolled holiday list that silently goes stale
every year).

**Early-close policy, explicit**: the run always fires
`market_close + EARLY_CLOSE_GRACE_MINUTES` (30 minutes) after the ACTUAL
close for that specific day - never a fixed 16:30 regardless of an
early close. On a normal day that's 16:00 ET + 30min = 16:30 ET,
matching requirement 1 exactly as its ordinary case; on a half day
(e.g. the day after Thanksgiving, 13:00 ET close) it's 13:30 ET, not
16:30 - running 3 hours "early" relative to a fixed clock, but exactly
30 minutes after the market this bot researches actually closed, which
is the only time that's ever meaningful for a report about that day's
close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

NY_TZ = ZoneInfo("America/New_York")
RUN_AFTER_CLOSE_MINUTES = 30

_NYSE_CALENDAR = mcal.get_calendar("NYSE")


@dataclass(frozen=True)
class MarketDay:
    date: str  # "YYYY-MM-DD"
    is_trading_day: bool
    market_close_utc: datetime | None  # None when not a trading day
    is_early_close: bool


def market_day(date_str: str) -> MarketDay:
    """NYSE schedule for one calendar date (`"YYYY-MM-DD"`), via
    `pandas_market_calendars` - never a hand-maintained holiday list.
    `is_early_close` is true whenever that day's actual close is earlier
    than a REGULAR 16:00 ET close (the day after Thanksgiving, Christmas
    Eve when it falls on a trading day, etc - whatever the calendar
    itself says, not a fixed list of dates maintained here)."""
    schedule = _NYSE_CALENDAR.schedule(start_date=date_str, end_date=date_str)
    if schedule.empty:
        return MarketDay(date=date_str, is_trading_day=False, market_close_utc=None, is_early_close=False)

    close_utc = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(timezone.utc)
    close_ny = close_utc.astimezone(NY_TZ)
    is_early_close = (close_ny.hour, close_ny.minute) < (16, 0)
    return MarketDay(date=date_str, is_trading_day=True, market_close_utc=close_utc, is_early_close=is_early_close)


def target_run_time_utc(date_str: str) -> datetime | None:
    """`market_close + RUN_AFTER_CLOSE_MINUTES` for one calendar date, in
    UTC. `None` when that date isn't a NYSE trading day at all."""
    day = market_day(date_str)
    if day.market_close_utc is None:
        return None
    return day.market_close_utc + timedelta(minutes=RUN_AFTER_CLOSE_MINUTES)


def decide_after_close_run(now_utc: datetime, already_succeeded_today: bool) -> tuple[str, str]:
    """The actual run/skip decision for `scripts/after_close.py`, made
    entirely in real `America/New_York` time against the real NYSE
    calendar for "today" (today = `now_utc`'s OWN calendar date in NY
    time - never the UTC date, which can differ from the NY date near
    midnight UTC). Returns `("run" | "skip", reason)`.

    Decision table, in order:
    1. Not a NYSE trading day (weekend/holiday) -> skip.
    2. Already recorded a tracked success today -> skip (duplicate-run
       protection - see `run()`'s own singleton lock for the
       complementary same-moment race protection; this is the
       same-DAY protection across repeated `StartInterval` ticks, sleep/
       wake cycles, and manual re-invocation).
    3. Before today's target run time (`market_close + 30min` ET) -> skip
       ("too early").
    4. At or after today's target run time, and not yet succeeded -> run.
       This deliberately has NO upper bound: if the Mac was asleep/off
       all afternoon and only wakes at, say, 22:00 ET, this still
       returns "run" (a late same-day catch-up) rather than silently
       losing the whole day - see module docstring's early-close policy
       for why "run late" is preferred over "never run." It will still
       only run ONCE (see point 2), and never for a day that has already
       passed (each invocation only ever evaluates `now_utc`'s OWN
       NY calendar date, never yesterday's)."""
    now_ny = now_utc.astimezone(NY_TZ)
    today_str = now_ny.strftime("%Y-%m-%d")
    day = market_day(today_str)

    if not day.is_trading_day:
        return "skip", f"{today_str} is not a NYSE trading day (holiday or weekend)"

    if already_succeeded_today:
        return "skip", f"already ran successfully today ({today_str})"

    target_utc = day.market_close_utc + timedelta(minutes=RUN_AFTER_CLOSE_MINUTES)
    if now_utc < target_utc:
        target_ny = target_utc.astimezone(NY_TZ)
        return "skip", (
            f"before today's target run time "
            f"({target_ny.strftime('%H:%M %Z')} - market close "
            f"{'(early close) ' if day.is_early_close else ''}+ {RUN_AFTER_CLOSE_MINUTES}min)"
        )

    early_note = " (early-close day)" if day.is_early_close else ""
    return "run", f"at/after today's target run time{early_note}, not yet run today"
