"""src/intelligence/tradingagents_spend.py - the concurrency-safe daily/
monthly $ spend ledger (reserve/commit/release). Uses real SQLite (not
mocked) since the whole point under test is the atomicity of the
BEGIN IMMEDIATE transaction."""

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.intelligence import tradingagents_spend as spend


def test_reserve_succeeds_within_both_limits(tmp_path):
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    assert spend.reserve(db, "e1", 1.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now) is True


def test_reserve_refuses_once_the_daily_cap_would_be_exceeded(tmp_path):
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    assert spend.reserve(db, "e1", 3.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now) is True
    assert spend.reserve(db, "e2", 3.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now) is False  # 3+3 > 5


def test_reserve_refuses_once_the_monthly_cap_would_be_exceeded(tmp_path):
    db = tmp_path / "ledger.db"
    day1 = datetime(2026, 9, 9, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 10, tzinfo=timezone.utc)  # different day, same month
    assert spend.reserve(db, "e1", 30.00, daily_limit_usd=100.00, monthly_limit_usd=50.00, now=day1) is True
    assert spend.reserve(db, "e2", 30.00, daily_limit_usd=100.00, monthly_limit_usd=50.00, now=day2) is False  # 30+30 > 50/month


def test_a_new_day_gets_a_fresh_daily_budget(tmp_path):
    db = tmp_path / "ledger.db"
    day1 = datetime(2026, 9, 9, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert spend.reserve(db, "e1", 4.00, daily_limit_usd=5.00, monthly_limit_usd=100.00, now=day1) is True
    assert spend.reserve(db, "e2", 4.00, daily_limit_usd=5.00, monthly_limit_usd=100.00, now=day2) is True


def test_commit_replaces_the_reservation_with_the_actual_cost(tmp_path):
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    spend.reserve(db, "e1", 1.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now)
    spend.commit(db, "e1", 0.02)  # the real cost was much less than the conservative reservation
    totals = spend.spent_today_and_month(db, now)
    assert totals["today_usd"] == 0.02  # not the original 1.00 reservation


def test_release_removes_the_reservation_entirely(tmp_path):
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    spend.reserve(db, "e1", 1.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now)
    spend.release(db, "e1")
    totals = spend.spent_today_and_month(db, now)
    assert totals["today_usd"] == 0.0


def test_committed_spend_from_a_previous_call_counts_toward_a_new_reservation(tmp_path):
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    spend.reserve(db, "e1", 1.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now)
    spend.commit(db, "e1", 4.50)
    assert spend.reserve(db, "e2", 1.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now) is False  # 4.50 + 1.00 > 5.00


def test_concurrent_reservations_never_jointly_exceed_the_daily_cap(tmp_path):
    """The actual concurrency-safety property under test: two threads
    racing to reserve against the SAME database file, where only one of
    two $3 reservations can fit inside a $5 cap, must never both succeed."""
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    results = {}

    def attempt(entry_id):
        results[entry_id] = spend.reserve(db, entry_id, 3.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now)

    t1 = threading.Thread(target=attempt, args=("e1",))
    t2 = threading.Thread(target=attempt, args=("e2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results.values()) == [False, True]  # exactly one succeeded, never both


def test_many_concurrent_first_time_opens_never_raise_database_is_locked(tmp_path):
    """Regression test for the flaky `sqlite3.OperationalError: database
    is locked` previously observed from inside `PRAGMA journal_mode=WAL`
    during `_connect_immediate()`'s first-ever open of a brand-new ledger
    file: that conversion briefly needs an exclusive lock, and enough
    threads racing to open the SAME not-yet-WAL file at once could win
    that race before the retry-with-backoff fix. 16 threads against a
    single fresh file is a much harder stress case than the 2-thread test
    above - this must complete with no thread raising, and the running
    total across every accepted reservation must still never exceed the
    daily cap."""
    db = tmp_path / "ledger.db"
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    results: dict[str, bool] = {}
    errors: list[BaseException] = []

    def attempt(entry_id):
        try:
            results[entry_id] = spend.reserve(db, entry_id, 1.00, daily_limit_usd=5.00, monthly_limit_usd=50.00, now=now)
        except BaseException as exc:  # noqa: BLE001 - capturing to fail the test with a clear assertion, not a thread-crash traceback
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(f"e{i}",)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sum(1 for ok in results.values() if ok) == 5  # exactly 5 of 16 $1 reservations fit the $5 cap
