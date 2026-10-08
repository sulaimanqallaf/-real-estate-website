"""Structural safety guardrails for the TradingAgents integration (GitHub
Issue #1 follow-up requirements 2, 7, 12): credentials flow via normal
environment-variable inheritance and are never written to disk in plain
sight, and nothing in the IBKR execution/risk stack can ever import or
reach this integration, by construction - not just by convention."""

import json
import logging
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import tradingagents_adapter as ta

logger = logging.getLogger("test")


# --- IBKR execution/risk stack never reaches this integration --------------------------


EXECUTION_SAFETY_FILES = [
    "src/execution/circuit_breaker.py",
    "src/execution/execution_policy.py",
    "src/execution/order_manager.py",
    "src/execution/approval_bridge.py",
    "src/execution/position_monitor.py",
    "src/execution/ibkr_client.py",
    "src/portfolio_risk.py",
    "src/risk_manager.py",
]


@pytest.mark.parametrize("relative_path", EXECUTION_SAFETY_FILES)
def test_ibkr_execution_and_risk_files_never_mention_tradingagents(relative_path):
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / relative_path).read_text(encoding="utf-8").lower()
    assert "tradingagents" not in text


# --- credentials: environment inheritance, never written to disk -----------------------


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}, "intelligence": {"tradingagents": {"enabled": True}}}


_FAKE_RUNNER_ECHO_ENV = textwrap.dedent("""
    import json, os, sys
    with open(sys.argv[1]) as f:
        request = json.load(f)
    print(json.dumps({
        "ok": True, "signal": "Buy", "final_rating": "Buy",
        "saw_api_key": os.environ.get("ANTHROPIC_API_KEY"),
    }))
""")


def test_api_key_reaches_the_subprocess_via_normal_env_inheritance_not_via_the_request(config, tmp_path, monkeypatch):
    """No code anywhere in this adapter reads an API key and forwards it -
    it works because `subprocess.run` inherits the parent process's
    environment by default, exactly like every other env var. This test
    proves that inheritance actually happens, AND that the key is never
    separately written into the request.json file on disk."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-totally-fake-test-key-123456")
    script = tmp_path / "fake_runner_env.py"
    script.write_text(_FAKE_RUNNER_ECHO_ENV)
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: script)

    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={}, now=now)

    assert result["saw_api_key"] == "sk-ant-totally-fake-test-key-123456"  # inherited, not forwarded by our code

    work_dir = ta.resolve_work_dir(config) / "AMD_2026-09-09"
    request_text = (work_dir / "request.json").read_text(encoding="utf-8")
    assert "sk-ant-totally-fake-test-key-123456" not in request_text  # never written to disk


def test_portfolio_context_payload_never_contains_the_word_key_or_token(config):
    ctx = ta._build_portfolio_context(config, logger)
    dumped = json.dumps(ctx).lower()
    assert "key" not in dumped
    assert "token" not in dumped
    assert "secret" not in dumped


# --- fail-safe on an unsupported model config / reported API failure -------------------


def test_unsupported_model_config_degrades_to_none_without_raising(config, tmp_path, monkeypatch):
    script = tmp_path / "unsupported_model_runner.py"
    script.write_text(
        "import json; print(json.dumps({'ok': False, 'error': \"Model 'not-a-real-model' is not supported by provider 'anthropic'\", 'error_type': 'ValueError'}))"
    )
    monkeypatch.setattr(ta, "resolve_python_executable", lambda cfg: Path(sys.executable))
    monkeypatch.setattr(ta, "resolve_runner_script", lambda: script)

    result = ta.run_one("AMD", "2026-09-09", config, logger, portfolio_context={})
    assert result is None  # never raises, never fabricates a fallback assessment


def test_a_run_one_exception_inside_the_shadow_orchestrator_never_propagates(config, monkeypatch):
    """Even if run_one() itself somehow raised (it is documented never to,
    but this is the actual isolation boundary main.py's safe_run() relies
    on at the per-ticker level) - one ticker's crash must never stop the
    whole shadow research pass or escape to the caller."""
    def boom(*a, **k):
        raise RuntimeError("simulated unexpected crash")

    monkeypatch.setattr(ta, "run_one", boom)
    entry = {"symbol": "AMD", "score": 90, "best_risk_result": {"tradeable": True}, "portfolio_evaluation": {"decision": "ACCEPT"}}
    ta.run_shadow_tradingagents_research([entry], "2026-09-09", config, logger)  # must not raise
    assert "tradingagents_assessment" not in entry
