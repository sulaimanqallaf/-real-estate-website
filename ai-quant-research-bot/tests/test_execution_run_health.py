"""execution/run_health.py - the read-only health/status tracker and
Telegram failure-alert sender (Daily Reliability & Safe Automation
milestone). All tests here use tmp_path for data.journal_dir, the same
test-pollution-avoidance convention as every other module colocated with
it in this codebase."""

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


# --- next scheduled run ------------------------------------------------------


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


# --- approximate spend -------------------------------------------------------


def test_approximate_spend_is_unavailable_when_tradingagents_disabled(tmp_path):
    config = make_config(tmp_path)
    assert run_health.approximate_spend(config) is None


def test_approximate_spend_is_unavailable_before_the_ledger_exists(tmp_path):
    config = make_config(tmp_path, intelligence={"tradingagents": {"enabled": True}})
    assert run_health.approximate_spend(config) is None


def test_approximate_spend_reads_the_real_ledger_once_it_exists(tmp_path):
    from src.intelligence import tradingagents_spend

    config = make_config(tmp_path, intelligence={"tradingagents": {"enabled": True}})
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    db_path = tradingagents_spend.resolve_ledger_path(config)
    tradingagents_spend.reserve(db_path, "e1", 0.05, daily_limit_usd=5.0, monthly_limit_usd=50.0, now=now)
    tradingagents_spend.commit(db_path, "e1", 0.03)

    spend = run_health.approximate_spend(config, now=now)
    assert spend == {"today_usd": 0.03, "month_usd": 0.03}


# --- latest errors ------------------------------------------------------------


def test_tail_recent_errors_is_empty_when_no_log_file_exists(tmp_path):
    config = make_config(tmp_path, logging={"log_dir": str(tmp_path / "reports")})
    assert run_health.tail_recent_errors(config) == []


def test_tail_recent_errors_returns_only_error_lines_redacted_and_most_recent_first_capped(tmp_path):
    log_dir = tmp_path / "reports"
    log_dir.mkdir()
    log_path = log_dir / "app.log"
    log_path.write_text(
        "2026-09-09 10:00:00 [INFO] starting run\n"
        "2026-09-09 10:00:01 [ERROR] call failed: sk-abcdefghijklmnop\n"
        "2026-09-09 10:00:02 [WARNING] something minor\n"
        "2026-09-09 10:00:03 [ERROR] second failure\n",
        encoding="utf-8",
    )
    config = make_config(tmp_path, logging={"log_dir": str(log_dir)})
    errors = run_health.tail_recent_errors(config, max_lines=10)
    assert len(errors) == 2
    assert "second failure" in errors[-1]
    assert "sk-abcdefghijklmnop" not in errors[0]


# --- stale tickers -------------------------------------------------------------


def test_stale_tickers_is_empty_when_no_cached_data_exists(tmp_path):
    config = make_config(tmp_path, tickers=["AMD"], data={"journal_dir": str(tmp_path / "journal"), "raw_dir": str(tmp_path / "raw")})
    assert run_health.stale_tickers(config, LOGGER) == []


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
    assert "never" in text  # last run / last success both never happened yet
