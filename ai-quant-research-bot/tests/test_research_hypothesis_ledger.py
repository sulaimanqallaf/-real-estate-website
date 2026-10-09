"""src/research/hypothesis_ledger.py - a real on-disk SQLite database,
same "test against the real schema" convention as decision_ledger's own
tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import decision_ledger
from src.research import hypothesis_ledger


def test_default_db_path_is_never_the_decision_ledger_path(tmp_path):
    config = {"data": {"journal_dir": str(tmp_path)}}
    research_path = hypothesis_ledger.default_db_path(config)
    trading_path = decision_ledger.resolve_db_path(config)
    assert research_path != trading_path
    assert "research" in str(research_path)


def test_default_db_path_respects_explicit_override(tmp_path):
    override = tmp_path / "custom" / "hyp.db"
    config = {"research": {"hypothesis_db_path": str(override)}}
    assert hypothesis_ledger.default_db_path(config) == override


def test_record_and_query_round_trips_a_result(tmp_path):
    db_path = tmp_path / "research_hypotheses.db"
    result = {
        "hypothesis_id": "wider_stop_v1", "description": "double the ATR stop multiplier",
        "strategy_name": "Trend Following", "symbol": "SYN1", "data_provenance": "synthetic_fixture",
        "config_overrides": {"strategies": {"trend_following": {"atr_stop_multiplier": 5.0}}},
        "trade_count": 14, "stats": {"total_return_pct": 2.46, "sharpe_ratio": 1.01},
    }
    hypothesis_ledger.record_result(db_path, result)

    rows = hypothesis_ledger.query_results(db_path)
    assert len(rows) == 1
    assert rows[0]["hypothesis_id"] == "wider_stop_v1"
    assert rows[0]["config_overrides"] == result["config_overrides"]
    assert rows[0]["stats"] == result["stats"]


def test_record_zero_trade_result_with_null_stats(tmp_path):
    db_path = tmp_path / "research_hypotheses.db"
    result = {
        "hypothesis_id": "no_trades_v1", "description": "flat series", "strategy_name": "Trend Following",
        "symbol": "FLAT", "data_provenance": "synthetic_fixture", "config_overrides": {}, "trade_count": 0, "stats": None,
    }
    hypothesis_ledger.record_result(db_path, result)
    rows = hypothesis_ledger.query_results(db_path)
    assert rows[0]["stats"] is None
    assert rows[0]["trade_count"] == 0


def test_each_record_call_is_a_new_immutable_row(tmp_path):
    db_path = tmp_path / "research_hypotheses.db"
    base = {
        "hypothesis_id": "same_id", "description": "d", "strategy_name": "Trend Following",
        "symbol": "SYN1", "data_provenance": "synthetic_fixture", "config_overrides": {}, "trade_count": 1, "stats": None,
    }
    id_a = hypothesis_ledger.record_result(db_path, base)
    id_b = hypothesis_ledger.record_result(db_path, base)
    assert id_a != id_b
    assert len(hypothesis_ledger.query_results(db_path, hypothesis_id="same_id")) == 2


def test_query_filters_by_strategy_name(tmp_path):
    db_path = tmp_path / "research_hypotheses.db"
    for strategy in ("Trend Following", "Mean Reversion"):
        hypothesis_ledger.record_result(db_path, {
            "hypothesis_id": f"{strategy}_h", "description": "d", "strategy_name": strategy,
            "symbol": "SYN1", "data_provenance": "synthetic_fixture", "config_overrides": {}, "trade_count": 1, "stats": None,
        })
    rows = hypothesis_ledger.query_results(db_path, strategy_name="Mean Reversion")
    assert len(rows) == 1
    assert rows[0]["strategy_name"] == "Mean Reversion"


def test_query_results_on_nonexistent_db_returns_empty_list(tmp_path):
    assert hypothesis_ledger.query_results(tmp_path / "does_not_exist.db") == []


def test_module_has_no_promote_function():
    source = Path(hypothesis_ledger.__file__).read_text()
    assert "def promote" not in source
