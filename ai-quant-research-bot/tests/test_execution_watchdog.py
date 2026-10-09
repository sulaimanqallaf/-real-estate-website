"""src/execution/watchdog.py - position_monitor's crash-recovery
heartbeat (Sprint 3, Reliability milestone)."""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import watchdog

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {"data": {"journal_dir": str(tmp_path)}}


def test_heartbeat_status_never_started_with_no_file(tmp_path):
    status = watchdog.heartbeat_status(_config(tmp_path))
    assert status["never_started"] is True
    assert status["stale"] is False
    assert status["last_heartbeat_at"] is None


def test_record_then_read_heartbeat_round_trips(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    watchdog.record_heartbeat(config, now=now)

    status = watchdog.heartbeat_status(config, now=now)
    assert status["never_started"] is False
    assert status["stale"] is False
    assert status["age_seconds"] == 0.0
    assert status["last_heartbeat_at"] == now.isoformat()


def test_heartbeat_is_stale_once_older_than_max_age(tmp_path):
    config = _config(tmp_path)
    recorded_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    watchdog.record_heartbeat(config, now=recorded_at)

    later = recorded_at + timedelta(seconds=watchdog.DEFAULT_MAX_AGE_SECONDS + 1)
    status = watchdog.heartbeat_status(config, now=later)
    assert status["stale"] is True
    assert status["never_started"] is False


def test_heartbeat_just_under_max_age_is_not_stale(tmp_path):
    config = _config(tmp_path)
    recorded_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    watchdog.record_heartbeat(config, now=recorded_at)

    later = recorded_at + timedelta(seconds=watchdog.DEFAULT_MAX_AGE_SECONDS - 1)
    status = watchdog.heartbeat_status(config, now=later)
    assert status["stale"] is False


def test_heartbeat_status_tolerates_a_corrupt_file(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "position_monitor_heartbeat.json"
    path.write_text("not valid json {{{", encoding="utf-8")

    status = watchdog.heartbeat_status(config)
    assert status["never_started"] is False
    assert status["stale"] is True  # a corrupt file is treated as a real problem, not silently "fine"


def test_record_heartbeat_never_raises_on_an_unwritable_path(tmp_path, monkeypatch):
    config = {"data": {"journal_dir": "/this/path/should/not/exist/for/real"}}
    watchdog.record_heartbeat(config)  # must not raise


def test_send_stale_heartbeat_alert_is_a_noop_without_crashing_when_telegram_is_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    status = {"last_heartbeat_at": "2026-01-01T00:00:00+00:00", "age_seconds": 500.0}
    assert watchdog.send_stale_heartbeat_alert({}, LOGGER, status) is False


def test_send_stale_heartbeat_alert_sends_via_telegram_bot(monkeypatch):
    sent = {}

    def fake_send(token, chat_id, text, logger):
        sent["token"] = token
        sent["chat_id"] = chat_id
        sent["text"] = text
        return True

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake-chat")
    from src import telegram_bot

    monkeypatch.setattr(telegram_bot, "send_telegram_message", fake_send)
    status = {"last_heartbeat_at": "2026-01-01T00:00:00+00:00", "age_seconds": 500.0}

    assert watchdog.send_stale_heartbeat_alert({}, LOGGER, status) is True
    assert "STALE" in sent["text"]
    assert "500.0" in sent["text"]
