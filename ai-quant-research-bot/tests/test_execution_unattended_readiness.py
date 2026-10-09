"""execution/unattended_readiness.py - pre-flight summary for unattended
PAPER-mode operation (AI Quant Trading Platform sprint, deliverable D).
Every individual check composes an EXISTING safety function - these
tests confirm the composition is correct, not that the underlying
checks themselves are (those have their own test files already)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import circuit_breaker, unattended_readiness as ur


def make_config(tmp_path, **overrides):
    config = {
        "data": {"journal_dir": str(tmp_path / "journal")},
        "execution": {"mode": "DRY_RUN", "halt_state_file": str(tmp_path / "halt.json")},
        "autonomous_paper": {"enabled": False, "auto_execute": {"enabled": False}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    return config


def test_dry_run_mode_fails_the_execution_mode_check(tmp_path):
    config = make_config(tmp_path)
    report = ur.check_readiness(config)
    mode_check = next(c for c in report.checks if c.name == "execution.mode")
    assert mode_check.passed is False
    assert report.all_passed is False


def test_ibkr_paper_mode_passes_the_execution_mode_check(tmp_path):
    config = make_config(tmp_path, execution={"mode": "IBKR_PAPER"})
    report = ur.check_readiness(config)
    mode_check = next(c for c in report.checks if c.name == "execution.mode")
    assert mode_check.passed is True


def test_no_live_execution_mode_exists_check_always_passes(tmp_path):
    report = ur.check_readiness(make_config(tmp_path))
    live_check = next(c for c in report.checks if c.name == "no live-money mode exists")
    assert live_check.passed is True


def test_a_manual_halt_fails_the_kill_switch_check(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    circuit_breaker.halt(config, reason="test halt")
    report = ur.check_readiness(config)
    halt_check = next(c for c in report.checks if c.name == "manual kill switch")
    assert halt_check.passed is False
    assert "test halt" in halt_check.detail


def test_no_reconciliation_run_yet_passes_with_an_informational_detail(tmp_path):
    report = ur.check_readiness(make_config(tmp_path))
    recon_check = next(c for c in report.checks if c.name == "last reconciliation")
    assert recon_check.passed is True
    assert "no reconciliation run yet" in recon_check.detail.lower()


def test_a_failed_reconciliation_fails_the_check(tmp_path):
    config = make_config(tmp_path)
    circuit_breaker.record_reconciliation_status(config, ok=False, summary="position mismatch")
    report = ur.check_readiness(config)
    recon_check = next(c for c in report.checks if c.name == "last reconciliation")
    assert recon_check.passed is False
    assert "position mismatch" in recon_check.detail


def test_telegram_not_configured_fails_that_check(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    report = ur.check_readiness(make_config(tmp_path))
    telegram_check = next(c for c in report.checks if c.name == "Telegram alerting configured")
    assert telegram_check.passed is False


def test_telegram_configured_passes_that_check(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake")
    report = ur.check_readiness(make_config(tmp_path))
    telegram_check = next(c for c in report.checks if c.name == "Telegram alerting configured")
    assert telegram_check.passed is True


def test_autonomy_flags_check_is_always_informational_never_a_failure(tmp_path):
    config = make_config(tmp_path, autonomous_paper={"enabled": True, "auto_execute": {"enabled": True}})
    report = ur.check_readiness(config)
    autonomy_check = next(c for c in report.checks if c.name == "autonomy flags (informational)")
    assert autonomy_check.passed is True
    assert "enabled=True" in autonomy_check.detail


def test_summary_text_distinguishes_ready_to_attempt_from_broker_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake")
    config = make_config(tmp_path, execution={"mode": "IBKR_PAPER"})
    report = ur.check_readiness(config)
    text = report.summary()
    assert "NOT a broker-verified connection test" in text or "does NOT confirm a real IBKR" in text


def test_all_passed_is_false_when_any_check_fails(tmp_path):
    report = ur.check_readiness(make_config(tmp_path))  # DRY_RUN -> execution.mode check fails
    assert report.all_passed is False


def test_all_passed_is_true_when_every_check_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake")
    config = make_config(tmp_path, execution={"mode": "IBKR_PAPER"})
    report = ur.check_readiness(config)
    failing = [c.name for c in report.checks if not c.passed]
    # ibapi may or may not be installed in this environment - that's the
    # only check allowed to vary here:
    assert failing in ([], ["ibapi installed"])
