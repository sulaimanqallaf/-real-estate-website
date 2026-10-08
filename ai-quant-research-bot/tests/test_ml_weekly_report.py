"""GitHub Issue #1 P1 - weekly Telegram learning report: real numbers
computed from the decision ledger + model event log, never fabricated
for too-small a sample, and never crashing on an empty history.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import decision_ledger, model_events, weekly_report

LOGGER = logging.getLogger("test")
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}}


def _seed_resolved_trade(db_path, trade_id, pnl_dollars, strategy="Trend Following", regime="BULL_TREND", provenance="BROKER_PAPER", as_of=None):
    decision_ledger.record_decision(
        db_path, ticker="AMD", decision="AUTO_EXECUTE", strategy=strategy, regime=regime,
        trade_id=trade_id, as_of=(as_of or NOW).isoformat(),
    )
    decision_ledger.record_outcome(db_path, trade_id=trade_id, outcome_status="TARGET_HIT", pnl_dollars=pnl_dollars, provenance=provenance)


def test_compute_weekly_summary_on_empty_ledger_never_crashes(tmp_path):
    config = _config(tmp_path)
    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert summary["total_decisions"] == 0
    assert summary["overall"]["has_data"] is False
    text = weekly_report.format_weekly_report(summary)
    assert "insufficient sample" in text


def test_win_rate_reported_with_confidence_interval_above_min_sample(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(10):
        pnl = 10.0 if i < 7 else -10.0  # 70% win rate
        _seed_resolved_trade(db_path, f"t{i}", pnl)

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert summary["overall"]["has_data"] is True
    assert summary["overall"]["win_rate_pct"] == 70.0
    assert summary["overall"]["win_rate_ci_low_pct"] < 70.0 < summary["overall"]["win_rate_ci_high_pct"]


def test_below_minimum_sample_reports_insufficient_not_a_fabricated_rate(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(3):  # below MIN_TRADES_FOR_RATE_ESTIMATE
        _seed_resolved_trade(db_path, f"t{i}", 10.0)

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert summary["overall"]["has_data"] is False


def test_separates_broker_paper_from_simulated_outcomes(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(6):
        _seed_resolved_trade(db_path, f"broker{i}", 10.0, provenance="BROKER_PAPER")
    for i in range(6):
        _seed_resolved_trade(db_path, f"sim{i}", -10.0, provenance="SIMULATED")

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert summary["broker_paper"]["win_rate_pct"] == 100.0
    assert summary["simulated"]["win_rate_pct"] == 0.0


def test_trades_outside_the_window_are_excluded(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    old = NOW - timedelta(days=30)
    for i in range(10):
        _seed_resolved_trade(db_path, f"old{i}", 10.0, as_of=old)

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW, window_days=7)
    assert summary["total_decisions"] == 0


def test_decision_counts_include_blocked_and_rejected_candidates(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", as_of=NOW.isoformat())
    decision_ledger.record_decision(db_path, ticker="MSFT", decision="REJECT", as_of=NOW.isoformat())
    decision_ledger.record_decision(db_path, ticker="GME", decision="REQUIRE_APPROVAL", as_of=NOW.isoformat())

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert summary["decision_counts"]["AUTO_EXECUTE"] == 1
    assert summary["decision_counts"]["REJECT"] == 1
    assert summary["decision_counts"]["REQUIRE_APPROVAL"] == 1


def test_expectancy_is_net_of_commission(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(6):
        decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", trade_id=f"t{i}", as_of=NOW.isoformat())
        decision_ledger.record_outcome(db_path, trade_id=f"t{i}", outcome_status="TARGET_HIT", pnl_dollars=100.0, commission=5.0)

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert summary["expectancy"]["expectancy_per_trade_dollars"] == 95.0


def test_model_events_within_window_are_included(tmp_path):
    config = _config(tmp_path)
    model_events.record_event(config, model_events.EVENT_PROMOTED, "model1", model_type="logistic_regression", reason="beat baselines", recorded_at=NOW.isoformat())

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    assert len(summary["model_events"]) == 1
    text = weekly_report.format_weekly_report(summary)
    assert "PROMOTED" in text
    assert "model1" in text


def test_format_weekly_report_never_raises_on_a_rich_summary(tmp_path):
    config = _config(tmp_path)
    db_path = decision_ledger.resolve_db_path(config)
    for i in range(10):
        pnl = 10.0 if i < 6 else -10.0
        _seed_resolved_trade(db_path, f"t{i}", pnl, strategy="Momentum Breakout" if i % 2 else "Trend Following", regime="SIDEWAYS" if i % 3 else "BULL_TREND")
    model_events.record_event(config, model_events.EVENT_ROLLED_BACK, "model2", reason="losing record", recorded_at=NOW.isoformat())

    summary = weekly_report.compute_weekly_summary(config, as_of=NOW)
    text = weekly_report.format_weekly_report(summary)
    assert "WEEKLY LEARNING REPORT" in text
    assert "By strategy:" in text
    assert "By regime:" in text


def test_send_weekly_report_calls_telegram_with_the_formatted_text(tmp_path, monkeypatch):
    config = _config(tmp_path)
    sent = {}
    def fake_send(token, chat_id, text, logger):
        sent["text"] = text
        return True

    monkeypatch.setattr("src.telegram_bot.send_telegram_message", fake_send)

    result = weekly_report.send_weekly_report(config, LOGGER, "TOKEN", "123", as_of=NOW)

    assert result is True
    assert "WEEKLY LEARNING REPORT" in sent["text"]
