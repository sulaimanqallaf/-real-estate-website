"""Safe dry-run test for the Daily Reliability & Safe Automation
milestone's scheduler/lock wiring in `main.run()`: a second overlapping
invocation must be refused by the singleton lock BEFORE any data fetch,
LLM call, or broker/order call ever happens - zero unnecessary work, let
alone a real IBKR order. Uses `tmp_path` for `data.journal_dir` so the
lock file never touches the real repo's state."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main
from src.execution import process_lock, run_health


def make_config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}, "tickers": ["AMD"], "logging": {"log_dir": str(tmp_path / "reports")}}


def test_run_refuses_to_start_a_second_overlapping_copy_without_fetching_anything(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "load_config", lambda config_path: config)
    monkeypatch.setattr(main, "setup_logging", lambda cfg: __import__("logging").getLogger("test"))

    def boom_if_called(*args, **kwargs):
        raise AssertionError("data_collector.fetch_all_price_history must never be called once the lock is already held")

    from src import data_collector

    monkeypatch.setattr(data_collector, "fetch_all_price_history", boom_if_called)

    # Hold the lock ourselves first, simulating an already-running copy.
    lock_path = run_health.resolve_lock_path(config)
    held_lock = process_lock.acquire_singleton_lock(lock_path, "test holder")
    try:
        exit_code = main.run(config_path=None)
    finally:
        held_lock.close()

    assert exit_code == 1


def test_run_releases_the_lock_on_a_clean_exit_so_a_later_run_can_proceed(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "load_config", lambda config_path: config)
    monkeypatch.setattr(main, "setup_logging", lambda cfg: __import__("logging").getLogger("test"))
    monkeypatch.setattr(main, "_execute", lambda cfg, logger: 0)

    assert main.run(config_path=None) == 0

    # The lock must be released (closed) - a second call must succeed too, not deadlock/refuse.
    assert main.run(config_path=None) == 0


def test_run_skips_a_same_day_relaunch_without_re_executing_the_pipeline(tmp_path, monkeypatch):
    """RunAtLoad-on-reboot safety: once today's run has already succeeded,
    a second invocation (e.g. a normal login triggering RunAtLoad again)
    must skip the whole pipeline - not re-fetch data, re-call an LLM, or
    resubmit anything - unless FORCE_RERUN=1 is set."""
    config = make_config(tmp_path)
    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "load_config", lambda config_path: config)
    monkeypatch.setattr(main, "setup_logging", lambda cfg: __import__("logging").getLogger("test"))
    calls = []
    monkeypatch.setattr(main, "_execute", lambda cfg, logger: calls.append(1) or 0)
    monkeypatch.delenv("FORCE_RERUN", raising=False)

    assert main.run(config_path=None) == 0
    assert calls == [1]

    assert main.run(config_path=None) == 0  # skipped - already succeeded today
    assert calls == [1]  # _execute was NOT called a second time

    monkeypatch.setenv("FORCE_RERUN", "1")
    assert main.run(config_path=None) == 0
    assert calls == [1, 1]  # override forces a real second run


def test_run_records_health_status_and_releases_the_lock_even_when_execute_raises(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(main, "load_env", lambda: None)
    monkeypatch.setattr(main, "load_config", lambda config_path: config)
    monkeypatch.setattr(main, "setup_logging", lambda cfg: __import__("logging").getLogger("test"))

    def boom(cfg, logger):
        raise RuntimeError("simulated crash mid-run")

    monkeypatch.setattr(main, "_execute", boom)
    monkeypatch.setattr(run_health, "send_failure_alert", lambda *a, **k: False)

    exit_code = main.run(config_path=None)
    assert exit_code == 1

    status = run_health.read_status(config)
    assert status["last_run_ok"] is False
    assert "simulated crash mid-run" in status["last_run_summary"]

    # Lock released - a subsequent run must not be refused.
    monkeypatch.setattr(main, "_execute", lambda cfg, logger: 0)
    assert main.run(config_path=None) == 0
