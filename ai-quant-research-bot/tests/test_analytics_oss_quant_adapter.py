"""src/analytics/oss_quant_adapter.py - the subprocess boundary into the
isolated .venvs/oss_quant/ environment. Mirrors
test_intelligence_tradingagents_adapter.py's exact pattern: tests point
`resolve_python_executable`/`resolve_runner_script` at `sys.executable`
and a small fake script, so NONE of these tests need the real isolated
venv or the real third-party packages installed."""

import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics import oss_quant_adapter as adapter

import logging

LOGGER = logging.getLogger("test")


def test_is_available_is_false_when_the_venv_does_not_exist(tmp_path):
    config = {"analytics": {"oss_quant": {"python_executable": str(tmp_path / "nonexistent" / "python")}}}
    assert adapter.is_available(config) is False


def test_is_available_is_true_when_the_configured_executable_exists(tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\n")
    config = {"analytics": {"oss_quant": {"python_executable": str(fake_python)}}}
    assert adapter.is_available(config) is True


_FAKE_RUNNER_ECHO = textwrap.dedent("""
    import json, sys
    with open(sys.argv[1]) as f:
        request = json.load(f)
    print(json.dumps({"ok": True, "echo": request}))
""")


def test_run_task_returns_the_parsed_json_response(tmp_path, monkeypatch):
    script = tmp_path / "fake_runner.py"
    script.write_text(_FAKE_RUNNER_ECHO)
    monkeypatch.setattr(adapter, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(adapter, "resolve_runner_script", lambda: script)

    result = adapter.run_task("performance_report", {"returns": {"2026-01-01": 0.01}}, {}, LOGGER)
    assert result["ok"] is True
    assert result["echo"]["task"] == "performance_report"
    assert result["echo"]["returns"] == {"2026-01-01": 0.01}


def test_run_task_returns_none_when_the_venv_does_not_exist(tmp_path):
    config = {"analytics": {"oss_quant": {"python_executable": str(tmp_path / "nonexistent" / "python")}}}
    assert adapter.run_task("performance_report", {}, config, LOGGER) is None


_FAKE_RUNNER_HANGS = "import time; time.sleep(5)"


def test_run_task_returns_none_on_timeout(tmp_path, monkeypatch):
    script = tmp_path / "slow_runner.py"
    script.write_text(_FAKE_RUNNER_HANGS)
    monkeypatch.setattr(adapter, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(adapter, "resolve_runner_script", lambda: script)

    result = adapter.run_task("performance_report", {}, {}, LOGGER, timeout_seconds=1)
    assert result is None


_FAKE_RUNNER_BAD_OUTPUT = "print('not json')"


def test_run_task_returns_none_on_unparseable_output(tmp_path, monkeypatch):
    script = tmp_path / "bad_runner.py"
    script.write_text(_FAKE_RUNNER_BAD_OUTPUT)
    monkeypatch.setattr(adapter, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(adapter, "resolve_runner_script", lambda: script)

    result = adapter.run_task("performance_report", {}, {}, LOGGER)
    assert result is None


_FAKE_RUNNER_REPORTS_TASK_FAILURE = 'import json; print(json.dumps({"ok": False, "error": "simulated failure"}))'


def test_run_task_can_return_ok_false_without_itself_returning_none(tmp_path, monkeypatch):
    """A task-level failure (e.g. CCXT's network call failing) is a real,
    reported result - not the same as the subprocess boundary itself
    failing to produce a response."""
    script = tmp_path / "failing_runner.py"
    script.write_text(_FAKE_RUNNER_REPORTS_TASK_FAILURE)
    monkeypatch.setattr(adapter, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(adapter, "resolve_runner_script", lambda: script)

    result = adapter.run_task("ccxt_ohlcv", {}, {}, LOGGER)
    assert result == {"ok": False, "error": "simulated failure"}
