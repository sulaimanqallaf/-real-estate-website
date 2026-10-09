"""src/analytics/regime_breakdown.py - built against a REAL decision_ledger
SQLite database (via record_decision/record_outcome), same convention as
test_analytics_performance_report.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from src.analytics import regime_breakdown
from src.ml import decision_ledger

LOGGER = logging.getLogger("test")


def _config(tmp_path):
    return {"data": {"journal_dir": str(tmp_path)}}


def _seed_closed_trade(db_path, ticker, trade_id, pnl_pct, exited_at, regime=None, pnl_dollars=None):
    decision_ledger.record_decision(
        db_path, ticker=ticker, decision="AUTO_EXECUTE", trade_id=trade_id,
        signal_entry_price=100.0, regime=regime, as_of=exited_at,
    )
    decision_ledger.record_outcome(
        db_path, trade_id=trade_id, outcome_status="CLOSED", exit_reason="target",
        exited_at=exited_at, pnl_dollars=pnl_dollars if pnl_dollars is not None else pnl_pct * 10,
        pnl_pct=pnl_pct, actual_entry_price=100.0, actual_exit_price=100.0 + pnl_pct,
    )


def test_returns_none_with_zero_closed_trades(tmp_path):
    config = _config(tmp_path)
    assert regime_breakdown.build_regime_breakdown(config, LOGGER) is None


def test_groups_by_regime_and_computes_win_rate(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(4):
        _seed_closed_trade(db_path, "AAPL", f"bull_win_{i}", 2.0, f"2026-01-{i + 1:02d}T00:00:00", regime="BULL_TREND")
    _seed_closed_trade(db_path, "AAPL", "bull_loss", -1.0, "2026-01-05T00:00:00", regime="BULL_TREND")
    for i in range(3):
        _seed_closed_trade(db_path, "MSFT", f"bear_loss_{i}", -1.5, f"2026-02-{i + 1:02d}T00:00:00", regime="BEAR_TREND")

    result = regime_breakdown.build_regime_breakdown(config, LOGGER)

    assert result is not None
    assert result["total_closed_trades"] == 8
    rows_by_regime = {r["regime"]: r for r in result["rows"]}
    assert rows_by_regime["BULL_TREND"]["trade_count"] == 5
    assert rows_by_regime["BULL_TREND"]["win_rate_pct"] == 80.0
    assert rows_by_regime["BEAR_TREND"]["trade_count"] == 3
    assert rows_by_regime["BEAR_TREND"]["win_rate_pct"] == 0.0


def test_missing_regime_grouped_under_unknown_not_dropped(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(3):
        _seed_closed_trade(db_path, "AAPL", f"norecime_{i}", 1.0, f"2026-01-{i + 1:02d}T00:00:00", regime=None)

    result = regime_breakdown.build_regime_breakdown(config, LOGGER)

    assert result is not None
    assert result["rows"][0]["regime"] == regime_breakdown.UNKNOWN_REGIME_LABEL
    assert result["rows"][0]["trade_count"] == 3


def test_regime_with_too_few_trades_is_excluded_but_counted(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(4):
        _seed_closed_trade(db_path, "AAPL", f"common_{i}", 1.0, f"2026-01-{i + 1:02d}T00:00:00", regime="BULL_TREND")
    _seed_closed_trade(db_path, "MSFT", "rare_1", -2.0, "2026-02-01T00:00:00", regime="RISK_OFF")
    _seed_closed_trade(db_path, "MSFT", "rare_2", -2.0, "2026-02-02T00:00:00", regime="RISK_OFF")

    result = regime_breakdown.build_regime_breakdown(config, LOGGER)

    assert len(result["rows"]) == 1
    assert result["rows"][0]["regime"] == "BULL_TREND"
    assert result["excluded_insufficient_sample"] == 2
    assert result["total_closed_trades"] == 6


def test_format_regime_breakdown_handles_none():
    assert "unavailable" in regime_breakdown.format_regime_breakdown(None)


def test_format_regime_breakdown_renders_rows():
    breakdown = {
        "rows": [{"regime": "BULL_TREND", "trade_count": 5, "win_rate_pct": 80.0, "avg_pnl_pct": 1.5, "total_pnl_dollars": 75.0}],
        "total_closed_trades": 5, "excluded_insufficient_sample": 0, "min_trades_per_regime_row": 3,
    }
    text = regime_breakdown.format_regime_breakdown(breakdown)
    assert "BULL_TREND" in text
    assert "80.0% win rate" in text
