"""src/agents/{registry,heartbeat,budget,checkpoint}.py - shared
runtime for Sprint 4's new agents (Priority 6: "durable job
scheduling, independent agent workers, recovery checkpoints, cost
limits")."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta, timezone

import pytest

from src.agents import budget, checkpoint, heartbeat, registry


def _config(tmp_path):
    return {"data": {"journal_dir": str(tmp_path / "journal")}}


# --- registry -------------------------------------------------------------------------------


def test_read_status_is_none_when_never_run(tmp_path):
    assert registry.read_status(_config(tmp_path), "scholar") is None


def test_start_run_records_running(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    registry.start_run(config, "scholar", extra={"phase": "fetching"}, now=now)

    status = registry.read_status(config, "scholar")
    assert status["status"] == registry.STATUS_RUNNING
    assert status["finished_at"] is None
    assert status["extra"] == {"phase": "fetching"}


def test_finish_run_preserves_started_at_and_merges_extra(tmp_path):
    config = _config(tmp_path)
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc)
    registry.start_run(config, "scholar", extra={"phase": "fetching"}, now=start)
    registry.finish_run(config, "scholar", registry.STATUS_SUCCESS, summary="found 5 papers", extra={"papers": 5}, now=end)

    status = registry.read_status(config, "scholar")
    assert status["status"] == registry.STATUS_SUCCESS
    assert status["started_at"] == start.isoformat()
    assert status["finished_at"] == end.isoformat()
    assert status["summary"] == "found 5 papers"
    assert status["extra"] == {"phase": "fetching", "papers": 5}


def test_finish_run_rejects_a_running_status():
    with pytest.raises(ValueError, match="SUCCESS or FAILED"):
        registry.finish_run({}, "scholar", registry.STATUS_RUNNING)


def test_finish_run_records_an_error_on_failure(tmp_path):
    config = _config(tmp_path)
    registry.start_run(config, "scholar")
    registry.finish_run(config, "scholar", registry.STATUS_FAILED, error="network timeout")

    status = registry.read_status(config, "scholar")
    assert status["status"] == registry.STATUS_FAILED
    assert status["error"] == "network timeout"


def test_read_status_returns_none_for_a_corrupt_file(tmp_path):
    config = _config(tmp_path)
    registry.start_run(config, "scholar")
    path = config["data"]["journal_dir"] + "/agents/scholar_status.json"
    Path(path).write_text("{not valid json", encoding="utf-8")

    assert registry.read_status(config, "scholar") is None


def test_list_known_agents_and_all_statuses(tmp_path):
    config = _config(tmp_path)
    registry.start_run(config, "scholar")
    registry.start_run(config, "news")

    assert registry.list_known_agents(config) == ["news", "scholar"]
    statuses = registry.all_statuses(config)
    assert set(statuses.keys()) == {"news", "scholar"}
    assert statuses["scholar"]["status"] == registry.STATUS_RUNNING


def test_list_known_agents_is_empty_before_any_agent_has_run(tmp_path):
    assert registry.list_known_agents(_config(tmp_path)) == []


def test_writes_are_atomic_no_tmp_file_left_behind(tmp_path):
    config = _config(tmp_path)
    registry.start_run(config, "scholar")
    registry.finish_run(config, "scholar", registry.STATUS_SUCCESS)

    agents_dir = Path(config["data"]["journal_dir"]) / "agents"
    tmp_files = list(agents_dir.glob("*.tmp"))
    assert tmp_files == []


# --- heartbeat --------------------------------------------------------------------------------


def test_heartbeat_status_never_started(tmp_path):
    status = heartbeat.heartbeat_status(_config(tmp_path), "news")
    assert status == {"last_heartbeat_at": None, "age_seconds": None, "stale": False, "never_started": True}


def test_heartbeat_status_ok_when_fresh(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    heartbeat.record_heartbeat(config, "news", now=now)

    status = heartbeat.heartbeat_status(config, "news", now=now + timedelta(seconds=30))
    assert status["never_started"] is False
    assert status["stale"] is False
    assert status["age_seconds"] == pytest.approx(30.0)


def test_heartbeat_status_stale_past_max_age(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    heartbeat.record_heartbeat(config, "news", now=now)

    status = heartbeat.heartbeat_status(config, "news", max_age_seconds=60, now=now + timedelta(seconds=120))
    assert status["stale"] is True


def test_heartbeat_status_treats_a_corrupt_file_as_never_started(tmp_path):
    config = _config(tmp_path)
    heartbeat.record_heartbeat(config, "news")
    path = Path(config["data"]["journal_dir"]) / "agents" / "news_heartbeat.json"
    path.write_text("not json", encoding="utf-8")

    assert heartbeat.heartbeat_status(config, "news")["never_started"] is True


def test_heartbeat_never_raises_on_an_unwritable_path(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def _raise_mkdir(*a, **k):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)
    heartbeat.record_heartbeat(config, "news")  # must not raise


def test_heartbeats_are_independent_per_agent(tmp_path):
    config = _config(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    heartbeat.record_heartbeat(config, "news", now=now)

    assert heartbeat.heartbeat_status(config, "news", now=now)["never_started"] is False
    assert heartbeat.heartbeat_status(config, "scholar", now=now)["never_started"] is True


# --- budget -----------------------------------------------------------------------------------


def test_check_and_increment_allows_calls_under_the_limit(tmp_path):
    config = _config(tmp_path)
    for _ in range(3):
        assert budget.check_and_increment(config, "scholar", "openalex", daily_limit=5) is True
    assert budget.calls_used_today(config, "scholar", "openalex") == 3


def test_check_and_increment_blocks_once_the_limit_is_reached(tmp_path):
    config = _config(tmp_path)
    for _ in range(3):
        budget.check_and_increment(config, "scholar", "openalex", daily_limit=3)

    assert budget.check_and_increment(config, "scholar", "openalex", daily_limit=3) is False
    assert budget.calls_used_today(config, "scholar", "openalex") == 3  # not incremented past the limit


def test_budget_resets_on_a_new_utc_date(tmp_path):
    config = _config(tmp_path)
    day1 = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, 0, 1, tzinfo=timezone.utc)

    for _ in range(3):
        budget.check_and_increment(config, "scholar", "openalex", daily_limit=3, now=day1)
    assert budget.check_and_increment(config, "scholar", "openalex", daily_limit=3, now=day1) is False

    assert budget.check_and_increment(config, "scholar", "openalex", daily_limit=3, now=day2) is True
    assert budget.calls_used_today(config, "scholar", "openalex", now=day2) == 1


def test_budget_tracks_sources_independently(tmp_path):
    config = _config(tmp_path)
    for _ in range(2):
        budget.check_and_increment(config, "scholar", "openalex", daily_limit=5)
    budget.check_and_increment(config, "scholar", "arxiv", daily_limit=5)

    assert budget.calls_used_today(config, "scholar", "openalex") == 2
    assert budget.calls_used_today(config, "scholar", "arxiv") == 1


def test_remaining_today_reflects_usage(tmp_path):
    config = _config(tmp_path)
    budget.check_and_increment(config, "scholar", "openalex", daily_limit=5)
    assert budget.remaining_today(config, "scholar", "openalex", daily_limit=5) == 4


def test_remaining_today_never_negative(tmp_path):
    config = _config(tmp_path)
    for _ in range(5):
        budget.check_and_increment(config, "scholar", "openalex", daily_limit=3)
    assert budget.remaining_today(config, "scholar", "openalex", daily_limit=3) == 0


def test_budget_ignores_a_corrupt_file_and_starts_fresh(tmp_path):
    config = _config(tmp_path)
    budget.check_and_increment(config, "scholar", "openalex", daily_limit=5)
    path = Path(config["data"]["journal_dir"]) / "agents" / "scholar_budget.json"
    path.write_text("not json", encoding="utf-8")

    assert budget.check_and_increment(config, "scholar", "openalex", daily_limit=5) is True
    assert budget.calls_used_today(config, "scholar", "openalex") == 1


# --- checkpoint --------------------------------------------------------------------------------


def test_load_checkpoint_is_none_before_any_save(tmp_path):
    assert checkpoint.load_checkpoint(_config(tmp_path), "scholar") is None


def test_save_and_load_checkpoint_round_trips(tmp_path):
    config = _config(tmp_path)
    checkpoint.save_checkpoint(config, "scholar", {"page": 3, "seen_ids": ["a", "b"]})
    assert checkpoint.load_checkpoint(config, "scholar") == {"page": 3, "seen_ids": ["a", "b"]}


def test_save_checkpoint_overwrites_the_previous_one(tmp_path):
    config = _config(tmp_path)
    checkpoint.save_checkpoint(config, "scholar", {"page": 1})
    checkpoint.save_checkpoint(config, "scholar", {"page": 2})
    assert checkpoint.load_checkpoint(config, "scholar") == {"page": 2}


def test_clear_checkpoint_removes_it(tmp_path):
    config = _config(tmp_path)
    checkpoint.save_checkpoint(config, "scholar", {"page": 1})
    checkpoint.clear_checkpoint(config, "scholar")
    assert checkpoint.load_checkpoint(config, "scholar") is None


def test_clear_checkpoint_is_a_noop_when_none_exists(tmp_path):
    checkpoint.clear_checkpoint(_config(tmp_path), "scholar")  # must not raise


def test_load_checkpoint_returns_none_for_a_corrupt_file(tmp_path):
    config = _config(tmp_path)
    checkpoint.save_checkpoint(config, "scholar", {"page": 1})
    path = Path(config["data"]["journal_dir"]) / "agents" / "scholar_checkpoint.json"
    path.write_text("not json", encoding="utf-8")

    assert checkpoint.load_checkpoint(config, "scholar") is None


def test_checkpoints_are_independent_per_agent(tmp_path):
    config = _config(tmp_path)
    checkpoint.save_checkpoint(config, "scholar", {"page": 1})
    checkpoint.save_checkpoint(config, "news", {"page": 9})

    assert checkpoint.load_checkpoint(config, "scholar") == {"page": 1}
    assert checkpoint.load_checkpoint(config, "news") == {"page": 9}
