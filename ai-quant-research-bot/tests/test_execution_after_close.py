"""execution/after_close.py - the launchd entry point wrapping
main.run() with the NYSE-calendar-aware decision (final production-safe
daily scheduling milestone)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import after_close


def make_config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}}


def test_simulate_prints_a_decision_and_exits_zero_without_touching_main(tmp_path, monkeypatch, capsys):
    config = make_config(tmp_path)
    monkeypatch.setattr(after_close, "_load_config", lambda: config)

    def boom(*a, **k):
        raise AssertionError("src.main must never be imported/called in --simulate mode")

    monkeypatch.setattr(after_close, "_run_real", boom)

    # Pick a definitely-weekend UTC instant so the decision is deterministic.
    exit_code = after_close.main(["--simulate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Decision:" in captured.out
    assert "no OpenAI, Telegram, or IBKR call is possible" in captured.out or "--simulate" in captured.out


def test_dry_run_flag_is_an_alias_for_simulate(tmp_path, monkeypatch, capsys):
    config = make_config(tmp_path)
    monkeypatch.setattr(after_close, "_load_config", lambda: config)
    exit_code = after_close.main(["--dry-run"])
    assert exit_code == 0
    assert "Decision:" in capsys.readouterr().out


def test_real_path_skips_without_calling_main_run_when_decision_is_skip(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(after_close, "_load_config", lambda: config)
    monkeypatch.setattr(after_close, "_decide", lambda already: ("skip", "not a trading day"))

    calls = []
    import src.main as main_module

    monkeypatch.setattr(main_module, "run", lambda: calls.append(1) or 0)

    exit_code = after_close.main([])
    assert exit_code == 0
    assert calls == []  # main.run() was never called


def test_real_path_calls_main_run_when_decision_is_run(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(after_close, "_load_config", lambda: config)
    monkeypatch.setattr(after_close, "_decide", lambda already: ("run", "at/after target"))

    calls = []
    import src.main as main_module

    monkeypatch.setattr(main_module, "run", lambda: calls.append(1) or 0)

    exit_code = after_close.main([])
    assert exit_code == 0
    assert calls == [1]


def test_real_path_consults_already_succeeded_today_from_run_health(tmp_path, monkeypatch):
    """Duplicate-run protection: the decision function is handed the
    REAL `already_succeeded_today()` result from `run_health` - not a
    hardcoded False - so a second StartInterval tick after a successful
    run today is actually suppressed."""
    config = make_config(tmp_path)
    monkeypatch.setattr(after_close, "_load_config", lambda: config)

    from src.execution import run_health

    now = datetime.now(timezone.utc)  # "already succeeded today" compares against the real current date
    run_health.record_run_start(config, now=now)
    run_health.record_run_result(config, ok=True, summary="Exit code 0", now=now)

    seen_already_values = []
    real_decide = after_close._decide

    def spy_decide(already):
        seen_already_values.append(already)
        return real_decide(already)

    monkeypatch.setattr(after_close, "_decide", spy_decide)

    import src.main as main_module

    monkeypatch.setattr(main_module, "run", lambda: 0)
    after_close.main([])

    assert seen_already_values == [True]
