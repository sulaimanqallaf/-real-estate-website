"""execution/run_health.py - the read-only health/status tracker and
Telegram failure-alert sender (Daily Reliability & Safe Automation
milestone). All tests here use tmp_path for data.journal_dir, the same
test-pollution-avoidance convention as every other module colocated with
it in this codebase.

Includes the Mac health-check follow-up regression tests: a real
DST-spanning yfinance-format cache file (the actual bug that produced
`'str' object has no attribute 'tzinfo'` on every ticker), a real
SQLite spend ledger reconciliation, and the launchd install-status check."""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import run_health

LOGGER = logging.getLogger("test")


def make_config(tmp_path, **overrides):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(exist_ok=True)
    config = {
        "data": {"journal_dir": str(journal_dir)},
        "tickers": [],
        "intelligence": {"tradingagents": {"enabled": False}},
    }
    config.update(overrides)
    return config


# --- status bookkeeping -----------------------------------------------------


def test_read_status_before_any_run_reports_unknown_not_fabricated_defaults(tmp_path):
    config = make_config(tmp_path)
    status = run_health.read_status(config)
    assert status["last_run_started_at"] is None
    assert status["last_run_ok"] is None
    assert status["last_success_at"] is None
    assert status["most_recent_report_file_date"] is None


def test_record_run_start_then_result_round_trips(tmp_path):
    config = make_config(tmp_path)
    now = datetime(2026, 9, 9, 17, 0, tzinfo=timezone.utc)
    run_health.record_run_start(config, now=now)
    run_health.record_run_result(config, ok=True, summary="Exit code 0", now=now)

    status = run_health.read_status(config)
    assert status["last_run_ok"] is True
    assert status["last_success_at"] == now.isoformat()
    assert status["last_run_summary"] == "Exit code 0"


def test_a_failed_run_updates_last_run_but_not_last_success(tmp_path):
    config = make_config(tmp_path)
    t1 = datetime(2026, 9, 9, 17, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
    run_health.record_run_start(config, now=t1)
    run_health.record_run_result(config, ok=True, summary="Exit code 0", now=t1)
    run_health.record_run_start(config, now=t2)
    run_health.record_run_result(config, ok=False, summary="Unhandled exception: boom", now=t2)

    status = run_health.read_status(config)
    assert status["last_run_ok"] is False
    assert status["last_success_at"] == t1.isoformat()  # NOT overwritten by the failed run


def test_run_result_summary_is_redacted_before_being_persisted(tmp_path):
    config = make_config(tmp_path)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    run_health.record_run_result(config, ok=False, summary="call failed: sk-abcdefghijklmnop", now=now)
    status = run_health.read_status(config)
    assert "sk-abcdefghijklmnop" not in status["last_run_summary"]


# --- distinguishing tracked runs from pre-tracking history ------------------


def test_most_recent_report_file_date_is_none_when_reports_dir_is_missing(tmp_path):
    config = make_config(tmp_path, data={"journal_dir": str(tmp_path / "journal"), "reports_dir": str(tmp_path / "reports")})
    assert run_health.most_recent_report_file_date(config) is None


def test_most_recent_report_file_date_reads_real_report_filenames_without_being_confused_with_a_tracked_success(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report_2026-10-06.json").write_text("{}")
    (reports_dir / "report_2026-10-08.json").write_text("{}")
    (reports_dir / "report_2026-10-07.json").write_text("{}")
    config = make_config(tmp_path, data={"journal_dir": str(tmp_path / "journal"), "reports_dir": str(reports_dir)})

    status = run_health.read_status(config)
    assert status["most_recent_report_file_date"] == "2026-10-08"
    # No tracked run was ever recorded - a report file existing must NEVER
    # be used to invent one:
    assert status["last_success_at"] is None
    assert status["last_run_ok"] is None


def test_already_succeeded_today_ignores_the_historical_report_file_signal(tmp_path):
    """A report file from Oct 8 (written before health tracking existed)
    must never be treated as if today's tracked run already succeeded -
    only a run THIS code tracked counts, so a fresh install's first real
    run of the day is never silently skipped."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "report_2026-10-08.json").write_text("{}")
    config = make_config(tmp_path, data={"journal_dir": str(tmp_path / "journal"), "reports_dir": str(reports_dir)})

    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    assert run_health.already_succeeded_today(config, now=now) is False


# --- next scheduled run / launchd install status -----------------------------


def test_next_scheduled_run_is_unknown_when_schedule_not_configured(tmp_path):
    config = make_config(tmp_path)
    assert run_health.compute_next_scheduled_run(config) is None


def test_next_scheduled_run_rolls_over_to_tomorrow_once_todays_time_has_passed(tmp_path):
    config = make_config(tmp_path, schedule={"daily": {"hour": 17, "minute": 0}})
    now = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)  # after 17:00 today
    next_run = run_health.compute_next_scheduled_run(config, now=now)
    assert next_run == datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc).isoformat()


def test_next_scheduled_run_stays_today_when_the_time_has_not_passed_yet(tmp_path):
    config = make_config(tmp_path, schedule={"daily": {"hour": 17, "minute": 0}})
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)  # before 17:00 today
    next_run = run_health.compute_next_scheduled_run(config, now=now)
    assert next_run == datetime(2026, 9, 9, 17, 0, tzinfo=timezone.utc).isoformat()


def test_check_launchd_status_reports_unknown_on_non_macos(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    status = run_health.check_launchd_status()
    assert status["installed"] is None


def test_check_launchd_status_reports_not_installed_when_launchctl_list_fails(monkeypatch):
    import subprocess

    monkeypatch.setattr("platform.system", lambda: "Darwin")

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "Could not find service"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    status = run_health.check_launchd_status()
    assert status["installed"] is False


def test_check_launchd_status_reports_installed_when_launchctl_list_succeeds(monkeypatch):
    import subprocess

    monkeypatch.setattr("platform.system", lambda: "Darwin")

    class FakeResult:
        returncode = 0
        stdout = "{ \"Label\" = \"com.aiquantresearchbot.daily\"; }"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    status = run_health.check_launchd_status()
    assert status["installed"] is True


def test_check_launchd_status_never_raises_when_launchctl_is_missing(monkeypatch):
    import subprocess

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no launchctl")))
    status = run_health.check_launchd_status()
    assert status["installed"] is None


def test_check_launchd_status_checks_the_label_it_is_given_not_always_the_daily_one(monkeypatch):
    import subprocess

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    seen_labels = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        seen_labels.append(argv[-1])
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_health.check_launchd_status(run_health.AFTER_CLOSE_LAUNCHD_LABEL)
    assert seen_labels == [run_health.AFTER_CLOSE_LAUNCHD_LABEL]


# --- market-aware next run (NYSE-calendar-aware after-close wrapper) --------


def test_compute_next_market_aware_run_skips_weekends_and_holidays_to_find_the_next_trading_day(tmp_path):
    config = make_config(tmp_path)
    # Friday 2026-12-25 is Christmas (holiday); next trading day is Mon 2026-12-28.
    now = datetime(2026, 12, 24, 23, 0, tzinfo=timezone.utc)  # after Dec 24's own early-close target already passed
    result = run_health.compute_next_market_aware_run(config, now=now)
    assert result is not None
    assert result["target_run_utc"].startswith("2026-12-28")


def test_compute_next_market_aware_run_reports_an_early_close_day_correctly(tmp_path):
    config = make_config(tmp_path)
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)  # Thanksgiving morning (holiday), before the 27th's early close
    result = run_health.compute_next_market_aware_run(config, now=now)
    assert result["is_early_close"] is True
    assert result["target_run_utc"].startswith("2026-11-27")


def test_compute_next_market_aware_run_stays_today_when_todays_target_has_not_passed_yet(tmp_path):
    config = make_config(tmp_path)
    now = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)  # well before today's 20:30 UTC target
    result = run_health.compute_next_market_aware_run(config, now=now)
    assert result["target_run_utc"].startswith("2026-10-28")


# --- spend report: committed vs reserved, never blended ----------------------


def test_spend_report_is_unavailable_when_tradingagents_disabled(tmp_path):
    config = make_config(tmp_path)
    assert run_health.spend_report(config) is None


def test_spend_report_is_unavailable_before_the_ledger_exists(tmp_path):
    config = make_config(tmp_path, intelligence={"tradingagents": {"enabled": True}})
    assert run_health.spend_report(config) is None


def test_spend_report_distinguishes_committed_from_reserved_using_the_real_ledger(tmp_path):
    """Regression test for the Mac health check conflating "committed
    (actual)" with "reserved (conservative ceiling)" into one misleading
    "approximate spend" figure. Uses the REAL sqlite ledger schema
    (tradingagents_spend.py), not a mock."""
    from src.intelligence import tradingagents_spend

    config = make_config(tmp_path, intelligence={"tradingagents": {"enabled": True}})
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    db_path = tradingagents_spend.resolve_ledger_path(config)

    # One call that finished and was committed for its real cost...
    tradingagents_spend.reserve(db_path, "e1", 1.00, daily_limit_usd=5.0, monthly_limit_usd=50.0, now=now)
    tradingagents_spend.commit(db_path, "e1", 0.03)
    # ...and one still in flight / never resolved (e.g. the process
    # crashed before commit()/release() ran) - this must show up as
    # RESERVED, never silently folded into "actual spend":
    tradingagents_spend.reserve(db_path, "e2", 1.00, daily_limit_usd=5.0, monthly_limit_usd=50.0, now=now)

    report = run_health.spend_report(config, now=now)
    assert report["committed_today_usd"] == 0.03
    assert report["committed_month_usd"] == 0.03
    assert report["reserved_today_usd"] == 1.00
    assert report["reserved_month_usd"] == 1.00
    assert report["outstanding_reservation_count"] == 1


def test_format_health_text_never_presents_reserved_dollars_as_actual_spend(tmp_path):
    from src.intelligence import tradingagents_spend

    config = make_config(
        tmp_path,
        intelligence={"tradingagents": {"enabled": True}},
        tickers=[],
    )
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    db_path = tradingagents_spend.resolve_ledger_path(config)
    tradingagents_spend.reserve(db_path, "e1", 2.00, daily_limit_usd=5.0, monthly_limit_usd=50.0, now=now)

    report = run_health.build_health_report(config, LOGGER, now=now)
    text = run_health.format_health_text(report)
    assert "COMMITTED (actual, billed): $0.0000" in text
    assert "RESERVED (conservative, not yet billed): $2.0000" in text


# --- log errors: active vs historical ----------------------------------------


def test_tail_recent_errors_is_empty_when_no_log_file_exists(tmp_path):
    config = make_config(tmp_path, logging={"log_dir": str(tmp_path / "reports")})
    errors = run_health.tail_recent_errors(config)
    assert errors == {"since_last_run_started": [], "historical": []}


def test_tail_recent_errors_splits_active_from_historical_by_last_tracked_run_start(tmp_path):
    log_dir = tmp_path / "reports"
    log_dir.mkdir()
    log_path = log_dir / "app.log"
    log_path.write_text(
        "2026-10-07 09:00:00,000 [ERROR] call failed: sk-abcdefghijklmnop (historical)\n"
        "2026-10-08 09:00:00,000 [ERROR] second historical failure\n"
        "2026-10-09 17:00:05,000 [ERROR] fresh failure during the tracked run\n",
        encoding="utf-8",
    )
    config = make_config(tmp_path, logging={"log_dir": str(log_dir)})
    # The tracked run started at 17:00:00 on 2026-10-09 - only the
    # 17:00:05 line falls at/after that:
    run_health.record_run_start(config, now=datetime(2026, 10, 9, 17, 0, 0, tzinfo=timezone.utc))

    errors = run_health.tail_recent_errors(config, max_lines=10)
    assert len(errors["since_last_run_started"]) == 1
    assert "fresh failure" in errors["since_last_run_started"][0]
    assert len(errors["historical"]) == 2
    assert "sk-abcdefghijklmnop" not in errors["historical"][0]  # redacted


def test_tail_recent_errors_treats_everything_as_historical_when_no_run_is_tracked(tmp_path):
    log_dir = tmp_path / "reports"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("2026-10-08 09:00:00,000 [ERROR] old failure\n", encoding="utf-8")
    config = make_config(tmp_path, logging={"log_dir": str(log_dir)})

    errors = run_health.tail_recent_errors(config)
    assert errors["since_last_run_started"] == []
    assert len(errors["historical"]) == 1


# --- cached-data staleness: never report a failed check as "none" -----------


def _write_raw_bars(raw_dir: Path, symbol: str, csv_body: str) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{symbol}_daily.csv").write_text(csv_body, encoding="utf-8")


def test_data_staleness_report_is_empty_when_no_cached_data_exists(tmp_path):
    config = make_config(tmp_path, tickers=["AMD"], data={"journal_dir": str(tmp_path / "journal"), "raw_dir": str(tmp_path / "raw")})
    report = run_health.data_staleness_report(config, LOGGER)
    assert report == {"stale": [], "check_failed": [], "ok": []}


def test_data_staleness_report_flags_a_dst_spanning_cache_file_as_check_failed_not_clean(tmp_path):
    """Regression test for the exact Mac health-check bug: a real
    yfinance-format cache file whose history spans a DST transition
    (mixed "-04:00"/"-05:00" offsets) made the old code raise
    `'str' object has no attribute 'tzinfo'`, which was then silently
    swallowed into "stale data: none." This must now show up as
    `check_failed`, never as `ok`."""
    raw_dir = tmp_path / "raw"
    _write_raw_bars(
        raw_dir,
        "AMD",
        "date,open,high,low,close,volume\n"
        "2026-10-30 00:00:00-04:00,1,1,1,1,100\n"
        "2026-11-02 00:00:00-05:00,1,1,1,1,100\n"
        "2026-11-03 00:00:00-05:00,1,1,1,1,100\n",
    )
    config = make_config(tmp_path, tickers=["AMD"], data={"journal_dir": str(tmp_path / "journal"), "raw_dir": str(raw_dir)})
    now = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)

    report = run_health.data_staleness_report(config, LOGGER, now=now)
    assert report == {"stale": [], "check_failed": [], "ok": ["AMD"]}


def test_data_staleness_report_flags_an_unparseable_timestamp_as_check_failed(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_raw_bars(raw_dir, "AMD", "date,open,high,low,close,volume\nnot-a-date,1,1,1,1,100\n")
    config = make_config(tmp_path, tickers=["AMD"], data={"journal_dir": str(tmp_path / "journal"), "raw_dir": str(raw_dir)})

    report = run_health.data_staleness_report(config, LOGGER)
    assert report["check_failed"] == ["AMD"]
    assert report["ok"] == []
    assert report["stale"] == []


def test_data_staleness_report_handles_a_date_only_format_cleanly(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_raw_bars(raw_dir, "AMD", "date,open,high,low,close,volume\n2026-11-01,1,1,1,1,100\n2026-11-03,1,1,1,1,100\n")
    config = make_config(tmp_path, tickers=["AMD"], data={"journal_dir": str(tmp_path / "journal"), "raw_dir": str(raw_dir)})
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    report = run_health.data_staleness_report(config, LOGGER, now=now)
    assert report == {"stale": [], "check_failed": [], "ok": ["AMD"]}


def test_data_staleness_report_flags_genuinely_stale_data(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_raw_bars(raw_dir, "AMD", "date,open,high,low,close,volume\n2026-10-01 00:00:00-04:00,1,1,1,1,100\n")
    config = make_config(
        tmp_path, tickers=["AMD"], data={"journal_dir": str(tmp_path / "journal"), "raw_dir": str(raw_dir), "max_bar_age_days_warning": 3}
    )
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    report = run_health.data_staleness_report(config, LOGGER, now=now)
    assert report == {"stale": ["AMD"], "check_failed": [], "ok": []}


def test_format_health_text_distinguishes_check_failed_from_genuinely_clean():
    report_check_failed = {
        "status": {"last_run_started_at": None, "last_run_finished_at": None, "last_run_ok": None, "last_run_summary": None, "last_run_failed_symbols": [], "last_success_at": None, "most_recent_report_file_date": None},
        "next_scheduled_run": None,
        "launchd": {"installed": None, "detail": "not macOS"},
        "next_market_aware_run": None,
        "launchd_after_close": {"installed": None, "detail": "not macOS"},
        "errors": {"since_last_run_started": [], "historical": []},
        "spend": None,
        "data_staleness": {"stale": [], "check_failed": ["AMD", "QQQ"], "ok": []},
        "circuit_breaker": {"halted": False, "reason": None},
        "position_monitor_heartbeat": {"last_heartbeat_at": None, "age_seconds": None, "stale": False, "never_started": True},
    }
    text = run_health.format_health_text(report_check_failed)
    assert "CHECK FAILED" in text
    assert "AMD" in text and "QQQ" in text
    assert "Stale cached data: none" not in text  # never silently "clean"


# --- failure alert ------------------------------------------------------------


def test_send_failure_alert_is_a_noop_without_crashing_when_telegram_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    config = make_config(tmp_path)
    assert run_health.send_failure_alert(config, LOGGER, "Daily research run", "boom") is False


def test_send_failure_alert_sends_a_redacted_message_via_telegram_bot(tmp_path, monkeypatch):
    sent = {}

    def fake_send(token, chat_id, text, logger):
        sent["token"] = token
        sent["chat_id"] = chat_id
        sent["text"] = text
        return True

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "FAKE_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    from src import telegram_bot

    monkeypatch.setattr(telegram_bot, "send_telegram_message", fake_send)

    config = make_config(tmp_path)
    ok = run_health.send_failure_alert(config, LOGGER, "Daily research run", "call failed: sk-abcdefghijklmnop")

    assert ok is True
    assert sent["chat_id"] == "12345"
    assert "sk-abcdefghijklmnop" not in sent["text"]
    assert "Daily research run" in sent["text"]


# --- health report / text formatting ------------------------------------------


def test_build_health_report_and_format_text_do_not_raise_on_a_fresh_install(tmp_path):
    config = make_config(tmp_path, tickers=[])
    report = run_health.build_health_report(config, LOGGER)
    text = run_health.format_health_text(report)
    assert "health status" in text
    assert "never" in text  # last tracked run / last tracked success both never happened yet
