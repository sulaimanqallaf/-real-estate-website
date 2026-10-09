"""src/analytics/performance_report.py - built against a REAL
decision_ledger SQLite database (via record_decision/record_outcome,
the same functions execution/learning_feedback.py actually calls), not
a mock - consistent with this codebase's convention of testing every
ledger-backed module against its real schema.

The actual QuantStats subprocess call is mocked (monkeypatching
oss_quant_adapter.run_task) since these tests must pass without the
isolated .venvs/oss_quant/ environment set up."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from src.analytics import oss_quant_adapter, performance_report
from src.ml import decision_ledger

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {"data": {"journal_dir": str(tmp_path)}}


def _seed_closed_trade(db_path, ticker, trade_id, pnl_pct, exited_at, commission=1.0, entry=100.0, exit_price=101.0):
    decision_ledger.record_decision(
        db_path, ticker=ticker, decision="AUTO_EXECUTE", trade_id=trade_id,
        signal_entry_price=entry, as_of=exited_at,
    )
    decision_ledger.record_outcome(
        db_path, trade_id=trade_id, outcome_status="CLOSED", exit_reason="target",
        exited_at=exited_at, pnl_dollars=(exit_price - entry) * 10, pnl_pct=pnl_pct,
        actual_entry_price=entry, actual_exit_price=exit_price, commission=commission,
    )


def test_build_performance_report_returns_none_below_min_trade_count(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(MIN := performance_report.MIN_TRADES_FOR_REPORT - 1):
        _seed_closed_trade(db_path, "AAPL", f"trade_{i}", 1.0, f"2026-01-{i + 1:02d}T00:00:00")

    assert performance_report.build_performance_report(config, LOGGER) is None


def test_build_performance_report_returns_none_when_venv_not_set_up(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(performance_report.MIN_TRADES_FOR_REPORT):
        _seed_closed_trade(db_path, "AAPL", f"trade_{i}", 1.0, f"2026-01-{i + 1:02d}T00:00:00")

    # No config override and no real .venvs/oss_quant/ on this test host's
    # expected path for an isolated tmp_path-based repo root - but the
    # real adapter resolves against the real repo root, which usually DOES
    # have the venv set up in this dev sandbox. Force "not available" by
    # pointing at a nonexistent python_executable explicitly.
    config["analytics"] = {"oss_quant": {"python_executable": str(tmp_path / "nonexistent" / "python")}}

    assert performance_report.build_performance_report(config, LOGGER) is None


def test_build_performance_report_excludes_not_traded_rows(tmp_path, monkeypatch):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(performance_report.MIN_TRADES_FOR_REPORT):
        _seed_closed_trade(db_path, "AAPL", f"trade_{i}", 1.0, f"2026-01-{i + 1:02d}T00:00:00")
    # A NOT_TRADED row must never count toward MIN_TRADES_FOR_REPORT or
    # appear in the returns fed to QuantStats.
    decision_ledger.record_decision(db_path, ticker="MSFT", decision="REJECT", trade_id="rejected_1", as_of="2026-01-20T00:00:00")
    decision_ledger.record_outcome(db_path, trade_id="rejected_1", outcome_status=decision_ledger.OUTCOME_NOT_TRADED)

    captured = {}

    def fake_run_task(task, payload, cfg, logger, timeout_seconds=None):
        captured["payload"] = payload
        return {"ok": True, "metrics": {"sharpe": 1.5, "sortino": 2.0, "max_drawdown": -0.1, "profit_factor": 1.8, "win_rate": 0.6, "exposure": 0.4}}

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(performance_report.oss_quant_adapter, "run_task", fake_run_task)

    metrics = performance_report.build_performance_report(config, LOGGER)

    assert metrics is not None
    assert metrics["trade_count"] == performance_report.MIN_TRADES_FOR_REPORT
    assert len(captured["payload"]["returns"]) == performance_report.MIN_TRADES_FOR_REPORT
    assert metrics["basis"] == "per_trade_pnl_pct"


def test_build_performance_report_includes_real_commission_and_slippage(tmp_path, monkeypatch):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(performance_report.MIN_TRADES_FOR_REPORT):
        _seed_closed_trade(
            db_path, "AAPL", f"trade_{i}", 1.0, f"2026-01-{i + 1:02d}T00:00:00",
            commission=2.5, entry=100.0, exit_price=101.0,
        )

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        performance_report.oss_quant_adapter, "run_task",
        lambda task, payload, cfg, logger, timeout_seconds=None: {
            "ok": True, "metrics": {"sharpe": 1.0, "sortino": 1.0, "max_drawdown": -0.05, "profit_factor": 1.2, "win_rate": 0.5, "exposure": 0.3},
        },
    )

    metrics = performance_report.build_performance_report(config, LOGGER)

    assert metrics["total_commission_usd"] == 2.5 * performance_report.MIN_TRADES_FOR_REPORT
    # actual_entry_price == signal_entry_price (both 100.0) here, so
    # real slippage_pct recorded by decision_ledger is exactly 0.0.
    assert metrics["avg_slippage_pct"] == 0.0


def test_build_performance_report_compounds_same_day_exits(tmp_path, monkeypatch):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    # Two trades closing on the SAME day must compound into one return,
    # not silently overwrite each other in the returns-by-date dict.
    for i in range(performance_report.MIN_TRADES_FOR_REPORT - 1):
        _seed_closed_trade(db_path, "AAPL", f"trade_{i}", 1.0, f"2026-02-{i + 10:02d}T00:00:00")
    _seed_closed_trade(db_path, "MSFT", "trade_same_day_a", 2.0, "2026-02-01T09:00:00")
    _seed_closed_trade(db_path, "GOOG", "trade_same_day_b", 3.0, "2026-02-01T15:00:00")

    captured = {}

    def fake_run_task(task, payload, cfg, logger, timeout_seconds=None):
        captured["payload"] = payload
        return {"ok": True, "metrics": {"sharpe": 1.0, "sortino": 1.0, "max_drawdown": -0.05, "profit_factor": 1.2, "win_rate": 0.5, "exposure": 0.3}}

    monkeypatch.setattr(oss_quant_adapter, "is_available", lambda cfg: True)
    monkeypatch.setattr(performance_report.oss_quant_adapter, "run_task", fake_run_task)

    metrics = performance_report.build_performance_report(config, LOGGER)

    returns = captured["payload"]["returns"]
    # performance_report.MIN_TRADES_FOR_REPORT - 1 distinct-day trades,
    # plus ONE compounded entry for 2026-02-01 (not two).
    assert len(returns) == performance_report.MIN_TRADES_FOR_REPORT
    expected_compounded = (1 + 0.02) * (1 + 0.03) - 1
    assert abs(returns["2026-02-01"] - expected_compounded) < 1e-9
    assert metrics["trade_count"] == performance_report.MIN_TRADES_FOR_REPORT + 1


def test_format_performance_report_handles_none():
    assert "unavailable" in performance_report.format_performance_report(None)


def test_format_performance_report_renders_real_metrics():
    text = performance_report.format_performance_report({
        "basis": "per_trade_pnl_pct", "trade_count": 10, "sharpe": 1.23, "sortino": 1.5,
        "max_drawdown": -0.12, "profit_factor": 1.8, "win_rate": 0.6, "exposure": 0.4,
        "total_commission_usd": 25.0, "avg_slippage_pct": 0.001,
    })
    assert "1.230" in text
    assert "$25.00" in text
