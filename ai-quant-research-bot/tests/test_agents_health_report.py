"""src/agents/health_report.py - the Sprint 4 Priority 6 daily fleet
health report + deduplicated Telegram failure/stale-heartbeat alerts
for the new agent fleet (research_scholar/news_agent/strategy_lab)."""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import budget, health_report, heartbeat, registry

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {"data": {"journal_dir": str(tmp_path / "journal")}}


# --- build_fleet_report / format_fleet_report_text --------------------------------------------


def test_build_fleet_report_includes_every_known_agent_even_before_first_run(tmp_path):
    report = health_report.build_fleet_report(_config(tmp_path), LOGGER)
    assert set(report["agents"].keys()) == set(health_report.KNOWN_AGENT_NAMES)
    for entry in report["agents"].values():
        assert entry["status"] is None
        assert entry["heartbeat"]["never_started"] is True


def test_build_fleet_report_reflects_a_real_run_and_heartbeat(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    heartbeat.record_heartbeat(config, "research_scholar", now=now)
    registry.start_run(config, "research_scholar", now=now)
    registry.finish_run(config, "research_scholar", registry.STATUS_SUCCESS, summary="5 papers seen", now=now)

    report = health_report.build_fleet_report(config, LOGGER, now=now + timedelta(seconds=10))
    entry = report["agents"]["research_scholar"]
    assert entry["status"]["status"] == registry.STATUS_SUCCESS
    assert entry["status"]["summary"] == "5 papers seen"
    assert entry["heartbeat"]["never_started"] is False
    assert entry["heartbeat"]["stale"] is False


def test_build_fleet_report_surfaces_budget_exhaustion(tmp_path):
    config = _config(tmp_path)
    config["research"] = {"scholar": {"daily_api_limit_per_source": 2}}
    for _ in range(2):
        budget.check_and_increment(config, "research_scholar", "openalex", daily_limit=2)

    report = health_report.build_fleet_report(config, LOGGER)
    openalex_budget = report["agents"]["research_scholar"]["budget"]["openalex"]
    assert openalex_budget == {"used": 2, "limit": 2, "remaining": 0, "exhausted": True}
    # strategy_lab has no external-API budget sources at all
    assert report["agents"]["strategy_lab"]["budget"] == {}


def test_format_fleet_report_text_includes_every_agent_name(tmp_path):
    report = health_report.build_fleet_report(_config(tmp_path), LOGGER)
    text = health_report.format_fleet_report_text(report)
    for name in health_report.KNOWN_AGENT_NAMES:
        assert name in text
    assert "never run" in text


def test_format_fleet_report_text_redacts_an_error_message(tmp_path):
    config = _config(tmp_path)
    registry.start_run(config, "news_agent")
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="token=sk-ant-SECRETVALUE1234567890")

    report = health_report.build_fleet_report(config, LOGGER)
    text = health_report.format_fleet_report_text(report)
    assert "SECRETVALUE1234567890" not in text
    assert "[REDACTED]" in text


# --- check_and_alert_fleet: dedup ---------------------------------------------------------------


def _telegram_capture(monkeypatch):
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _fake_send(token, chat_id, text, logger):
        sent.append(text)
        return True

    monkeypatch.setattr("src.telegram_bot.send_telegram_message", _fake_send)
    return sent


def test_alerts_once_on_a_failed_run_and_not_again_for_the_same_failure(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sent = _telegram_capture(monkeypatch)

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry.start_run(config, "news_agent", now=now)
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="boom", now=now)

    report = health_report.build_fleet_report(config, LOGGER, now=now)
    alerted = health_report.check_and_alert_fleet(config, LOGGER, report, now=now)
    assert alerted == ["news_agent"]
    assert len(sent) == 1
    assert "news_agent" in sent[0] and "FAILED" in sent[0]

    # Same failed run, unchanged finished_at - must not re-alert.
    alerted_again = health_report.check_and_alert_fleet(config, LOGGER, report, now=now)
    assert alerted_again == []
    assert len(sent) == 1


def test_alerts_again_for_a_new_distinct_failed_run(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sent = _telegram_capture(monkeypatch)

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry.start_run(config, "news_agent", now=t1)
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="boom 1", now=t1)
    report1 = health_report.build_fleet_report(config, LOGGER, now=t1)
    health_report.check_and_alert_fleet(config, LOGGER, report1, now=t1)
    assert len(sent) == 1

    t2 = t1 + timedelta(hours=1)
    registry.start_run(config, "news_agent", now=t2)
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="boom 2", now=t2)
    report2 = health_report.build_fleet_report(config, LOGGER, now=t2)
    alerted2 = health_report.check_and_alert_fleet(config, LOGGER, report2, now=t2)
    assert alerted2 == ["news_agent"]
    assert len(sent) == 2


def test_recovering_to_success_does_not_block_a_future_alert(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sent = _telegram_capture(monkeypatch)

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry.start_run(config, "news_agent", now=t1)
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="boom", now=t1)
    report1 = health_report.build_fleet_report(config, LOGGER, now=t1)
    health_report.check_and_alert_fleet(config, LOGGER, report1, now=t1)
    assert len(sent) == 1

    t2 = t1 + timedelta(hours=1)
    registry.start_run(config, "news_agent", now=t2)
    registry.finish_run(config, "news_agent", registry.STATUS_SUCCESS, summary="ok", now=t2)
    report2 = health_report.build_fleet_report(config, LOGGER, now=t2)
    alerted2 = health_report.check_and_alert_fleet(config, LOGGER, report2, now=t2)
    assert alerted2 == []  # a success never alerts
    assert len(sent) == 1


def test_alerts_once_on_stale_heartbeat_and_not_again_while_still_stale(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sent = _telegram_capture(monkeypatch)

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    heartbeat.record_heartbeat(config, "strategy_lab", now=t0)

    later = t0 + timedelta(seconds=heartbeat.DEFAULT_MAX_AGE_SECONDS + 100)
    report = health_report.build_fleet_report(config, LOGGER, now=later)
    alerted = health_report.check_and_alert_fleet(config, LOGGER, report, now=later)
    assert alerted == ["strategy_lab"]
    assert len(sent) == 1
    assert "STALE" in sent[0]

    even_later = later + timedelta(seconds=60)
    report2 = health_report.build_fleet_report(config, LOGGER, now=even_later)
    alerted2 = health_report.check_and_alert_fleet(config, LOGGER, report2, now=even_later)
    assert alerted2 == []
    assert len(sent) == 1


def test_stale_heartbeat_alerts_again_after_recovering_and_going_stale_a_second_time(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sent = _telegram_capture(monkeypatch)

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    heartbeat.record_heartbeat(config, "strategy_lab", now=t0)
    stale_time = t0 + timedelta(seconds=heartbeat.DEFAULT_MAX_AGE_SECONDS + 100)
    report1 = health_report.build_fleet_report(config, LOGGER, now=stale_time)
    health_report.check_and_alert_fleet(config, LOGGER, report1, now=stale_time)
    assert len(sent) == 1

    # Recovers: a fresh heartbeat clears the "stale alert active" flag.
    recovery_time = stale_time + timedelta(seconds=10)
    heartbeat.record_heartbeat(config, "strategy_lab", now=recovery_time)
    report2 = health_report.build_fleet_report(config, LOGGER, now=recovery_time)
    alerted2 = health_report.check_and_alert_fleet(config, LOGGER, report2, now=recovery_time)
    assert alerted2 == []
    assert len(sent) == 1

    # Goes stale again - must alert a second time.
    stale_again = recovery_time + timedelta(seconds=heartbeat.DEFAULT_MAX_AGE_SECONDS + 100)
    report3 = health_report.build_fleet_report(config, LOGGER, now=stale_again)
    alerted3 = health_report.check_and_alert_fleet(config, LOGGER, report3, now=stale_again)
    assert alerted3 == ["strategy_lab"]
    assert len(sent) == 2


def test_no_alert_sent_when_telegram_not_configured(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def _should_not_be_called(*a, **k):
        raise AssertionError("send_telegram_message must not be called without Telegram configured")

    monkeypatch.setattr("src.telegram_bot.send_telegram_message", _should_not_be_called)

    registry.start_run(config, "news_agent")
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="boom")
    report = health_report.build_fleet_report(config, LOGGER)
    alerted = health_report.check_and_alert_fleet(config, LOGGER, report)
    # Dedup state still advances even when the send itself was skipped -
    # never treated as "unsent, retry forever" by this module's own bookkeeping.
    assert alerted == ["news_agent"]


# --- main() CLI ----------------------------------------------------------------------------------


def test_main_returns_nonzero_when_an_agent_is_failed(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    registry.start_run(config, "news_agent")
    registry.finish_run(config, "news_agent", registry.STATUS_FAILED, error="boom")

    assert health_report.main() == 1


def test_main_returns_zero_when_nothing_has_failed(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert health_report.main() == 0
