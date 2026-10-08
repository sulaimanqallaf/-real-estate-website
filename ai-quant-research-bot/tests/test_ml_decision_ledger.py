"""GitHub Issue #1 P1 - the decision+outcome ledger: every candidate's
decision snapshot is recorded once, an outcome is attached exactly once
(never overwritten by a duplicate poll), and queries never fabricate
data or crash on a missing database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import decision_ledger


def test_record_decision_returns_a_unique_id_each_time(tmp_path):
    db_path = tmp_path / "ledger.db"
    id1 = decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE")
    id2 = decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE")
    assert id1 != id2


def test_query_decisions_returns_empty_list_for_a_missing_database(tmp_path):
    assert decision_ledger.query_decisions(tmp_path / "does_not_exist.db") == []


def test_record_and_query_round_trip(tmp_path):
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(
        db_path, ticker="AMD", decision="AUTO_EXECUTE", report_date="2026-09-09",
        strategy="Trend Following", regime="BULL_TREND", signal_score=90, quant_score=0.8,
        ml_confidence="VERY_HIGH", calibrated_probability=0.72, model_horizon="10d",
        reasons=["Meets every configured AUTO_EXECUTE criterion."],
        signal_entry_price=100.0, stop_loss=95.0, target_price=115.0,
        planned_shares=10, dollar_risk=50.0, trade_id="AMD_2026-09-09_aaaaaaaa",
    )

    rows = decision_ledger.query_decisions(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "AMD"
    assert row["strategy"] == "Trend Following"
    assert row["regime"] == "BULL_TREND"
    assert row["decision"] == "AUTO_EXECUTE"
    assert row["decision_reasons"] == "Meets every configured AUTO_EXECUTE criterion."
    assert row["signal_entry_price"] == 100.0
    assert row["trade_id"] == "AMD_2026-09-09_aaaaaaaa"
    assert row["outcome_status"] is None  # no outcome recorded yet


def test_record_outcome_updates_the_matching_row(tmp_path):
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(
        db_path, ticker="AMD", decision="AUTO_EXECUTE", signal_entry_price=100.0,
        trade_id="AMD_2026-09-09_aaaaaaaa",
    )

    updated = decision_ledger.record_outcome(
        db_path, trade_id="AMD_2026-09-09_aaaaaaaa", outcome_status="TARGET_HIT",
        exit_reason="Target price reached", exited_at="2026-09-11", pnl_dollars=100.0, pnl_pct=10.0,
        actual_entry_price=100.2, actual_exit_price=110.0, commission=1.5, provenance="BROKER_PAPER",
    )
    assert updated is True

    rows = decision_ledger.query_decisions(db_path)
    row = rows[0]
    assert row["outcome_status"] == "TARGET_HIT"
    assert row["pnl_dollars"] == 100.0
    assert row["actual_entry_price"] == 100.2
    assert row["provenance"] == "BROKER_PAPER"
    assert row["outcome_recorded_at"] is not None


def test_record_outcome_computes_slippage_pct(tmp_path):
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", signal_entry_price=100.0, trade_id="t1")
    decision_ledger.record_outcome(db_path, trade_id="t1", outcome_status="FILLED", actual_entry_price=101.0)
    row = decision_ledger.query_decisions(db_path)[0]
    assert row["slippage_pct"] == 0.01


def test_record_outcome_returns_false_for_unknown_trade_id(tmp_path):
    db_path = tmp_path / "ledger.db"
    assert decision_ledger.record_outcome(db_path, trade_id="never-existed", outcome_status="TARGET_HIT") is False


def test_record_outcome_never_overwrites_an_already_recorded_outcome(tmp_path):
    """A duplicate exit-fill poll (position_monitor ticks every N
    seconds) must never corrupt an already-final real outcome with a
    second, possibly different, value."""
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", signal_entry_price=100.0, trade_id="t1")
    decision_ledger.record_outcome(db_path, trade_id="t1", outcome_status="TARGET_HIT", pnl_dollars=100.0)

    second_attempt = decision_ledger.record_outcome(db_path, trade_id="t1", outcome_status="STOPPED", pnl_dollars=-999.0)

    assert second_attempt is False
    row = decision_ledger.query_decisions(db_path)[0]
    assert row["outcome_status"] == "TARGET_HIT"
    assert row["pnl_dollars"] == 100.0


def test_query_decisions_filters_by_strategy_ticker_regime(tmp_path):
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", strategy="Trend Following", regime="BULL_TREND")
    decision_ledger.record_decision(db_path, ticker="MSFT", decision="REQUIRE_APPROVAL", strategy="Mean Reversion", regime="SIDEWAYS")

    assert len(decision_ledger.query_decisions(db_path, ticker="AMD")) == 1
    assert len(decision_ledger.query_decisions(db_path, strategy="Mean Reversion")) == 1
    assert len(decision_ledger.query_decisions(db_path, regime="BULL_TREND")) == 1
    assert len(decision_ledger.query_decisions(db_path, ticker="GME")) == 0


def test_query_decisions_only_with_outcome_filters_unresolved_rows(tmp_path):
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(db_path, ticker="AMD", decision="AUTO_EXECUTE", trade_id="t1")
    decision_ledger.record_decision(db_path, ticker="MSFT", decision="REJECT")  # never traded - no outcome ever
    decision_ledger.record_outcome(db_path, trade_id="t1", outcome_status="TARGET_HIT")

    all_rows = decision_ledger.query_decisions(db_path)
    resolved_rows = decision_ledger.query_decisions(db_path, only_with_outcome=True)
    assert len(all_rows) == 2
    assert len(resolved_rows) == 1
    assert resolved_rows[0]["ticker"] == "AMD"


def test_resolve_db_path_is_always_colocated_with_journal_dir(tmp_path):
    """No separate `ml.decision_ledger_path`-style override - deliberately,
    so redirecting config.data.journal_dir (the established, universal
    test-isolation mechanism this whole suite relies on) is always
    enough to isolate this too, with no second place to remember."""
    config = {"data": {"journal_dir": str(tmp_path)}}
    assert decision_ledger.resolve_db_path(config) == tmp_path / "decision_ledger.db"


def test_resolve_db_path_falls_back_to_the_literal_default_with_no_journal_dir():
    config: dict = {}
    path = decision_ledger.resolve_db_path(config)
    assert path.name == "decision_ledger.db"
    assert "data" in path.parts and "ml" in path.parts


def test_a_blocked_or_rejected_candidate_is_recorded_with_no_outcome_ever(tmp_path):
    """A REJECT/WATCH_ONLY/blocked candidate is still recorded (the
    "missed/blocked decision" the analysis side needs) but never gets an
    outcome row at all - there is no trade_id to match against, and
    that's correct, not a gap to fill."""
    db_path = tmp_path / "ledger.db"
    decision_ledger.record_decision(db_path, ticker="GME", decision="REJECT", reasons=["Avoid-labeled candidate."])
    rows = decision_ledger.query_decisions(db_path)
    assert len(rows) == 1
    assert rows[0]["decision"] == "REJECT"
    assert rows[0]["trade_id"] is None
    assert rows[0]["outcome_status"] is None
