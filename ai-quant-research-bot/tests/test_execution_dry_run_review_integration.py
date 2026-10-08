"""Phase 7 continuation - proves the DRY_RUN order-review path wired into
`main._process_execution_layer` never contacts a broker, no matter what
candidates it sees, and that it correctly logs a full order review for
every Top Candidate.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.main import _process_execution_layer

# A fixed Wednesday, mid-session NY time - every test here injects this
# instead of relying on real wall-clock time, so results never depend on
# what day/hour the suite happens to run (previously these tests failed
# outside 9:30-16:00 ET on a weekday, and would ALSO have failed on any
# weekend even within that window).
TRADING_HOURS_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))


def qa(confidence="VERY_HIGH", edge="POSITIVE"):
    return type("QA", (), {"ml_confidence": confidence, "strategy_edge": edge})()


def make_entry(symbol="AMD", **overrides):
    entry = {
        "symbol": symbol,
        "label": "Top Candidate",
        "score": 90,
        "best_risk_result": {"tradeable": True, "strategy": "Trend Following"},
        "regime_evaluation": {"blocked": False, "regime": "TRENDING_UP"},
        "portfolio_evaluation": {
            "decision": "ACCEPT",
            "position": {
                "strategy": "Trend Following", "entry": 100.0, "stop_loss": 95.0, "target": 115.0,
                "shares": 10, "dollar_risk": 50.0, "risk_reward": 3.0,
            },
        },
        "quant_assessment": qa(),
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def dry_run_config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {
        "execution": {"mode": "DRY_RUN", "journal_path": str(tmp_path / "executions.jsonl")},
        "autonomous_paper": {
            "enabled": True,
            "auto_execute": {
                "enabled": True, "allowed_confidence": ["VERY_HIGH"], "minimum_signal_score": 85,
                "minimum_risk_reward": 2.0, "require_strategy_edge": False, "require_good_data_quality": True,
            },
        },
        "execution_risk": {"max_risk_per_trade_pct": 0.05},
        "portfolio_risk": {},
        "risk": {"account_equity": 10_000},
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv", "pending_approvals_file": "pending_approvals.json"},
        "telegram": {"top_candidates_limit": 10},
    }


class _PoisonedBroker:
    """A `Broker` whose every broker-contacting method raises - passing
    this into `_process_execution_layer` under DRY_RUN and seeing no
    exception propagate is the strongest possible proof that the DRY_RUN
    path never reaches any of them."""

    def connect(self):
        raise AssertionError("connect() must never be called in DRY_RUN")

    def disconnect(self):
        raise AssertionError("disconnect() must never be called in DRY_RUN")

    def connection_state(self):
        raise AssertionError("connection_state() must never be called in DRY_RUN")

    def account_summary(self):
        raise AssertionError("account_summary() must never be called in DRY_RUN")

    def positions(self):
        raise AssertionError("positions() must never be called in DRY_RUN")

    def open_orders(self):
        raise AssertionError("open_orders() must never be called in DRY_RUN")

    def get_order(self, broker_order_id):
        raise AssertionError("get_order() must never be called in DRY_RUN")

    def submit_order(self, intent):
        raise AssertionError("submit_order() must NEVER be called in DRY_RUN - this is the one hard rule.")

    def cancel_order(self, broker_order_id):
        raise AssertionError("cancel_order() must never be called in DRY_RUN")

    def replace_order(self, broker_order_id, **changes):
        raise AssertionError("replace_order() must never be called in DRY_RUN")

    def executions(self):
        raise AssertionError("executions() must never be called in DRY_RUN")


def test_dry_run_never_touches_any_broker_method_even_for_an_auto_execute_grade_candidate(dry_run_config, caplog):
    """The candidate here is deliberately AUTO_EXECUTE-grade (would bypass
    approval if execution.mode were IBKR_PAPER) - the strongest version of
    this proof uses a candidate that WOULD otherwise trigger broker
    activity, not a weak one that never would have anyway."""
    entry = make_entry("AMD")
    poisoned = _PoisonedBroker()

    with caplog.at_level(logging.INFO):
        executed = _process_execution_layer([entry], "2026-09-09", dry_run_config, logging.getLogger("test"), None, None, broker=poisoned, now=TRADING_HOURS_NOW)

    assert executed == set()
    assert entry["execution_decision"].decision == "AUTO_EXECUTE"  # correctly classified - just never acted on
    assert any("DRY_RUN ORDER REVIEW" in r.message for r in caplog.records)


def test_dry_run_review_logs_every_top_candidate_not_only_auto_execute_grade_ones(dry_run_config, caplog):
    weak_entry = make_entry("MSFT", score=50)  # fails AUTO_EXECUTE's minimum_signal_score, still a Top Candidate
    with caplog.at_level(logging.INFO):
        executed = _process_execution_layer([weak_entry], "2026-09-09", dry_run_config, logging.getLogger("test"), None, None, broker=None, now=TRADING_HOURS_NOW)

    assert executed == set()
    assert weak_entry["execution_decision"].decision == "REQUIRE_APPROVAL"
    review_logs = [r.message for r in caplog.records if "DRY_RUN ORDER REVIEW" in r.message]
    assert any("MSFT" in msg for msg in review_logs)


def test_dry_run_review_reports_would_submit_no_for_a_blocked_candidate(dry_run_config, caplog):
    entry = make_entry("TSLA", portfolio_evaluation={"decision": "ACCEPT", "position": {"strategy": "Trend Following", "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 5000.0, "risk_reward": 3.0}})
    with caplog.at_level(logging.INFO):
        _process_execution_layer([entry], "2026-09-09", dry_run_config, logging.getLogger("test"), None, None, broker=None, now=TRADING_HOURS_NOW)

    review_logs = [r.message for r in caplog.records if "DRY_RUN ORDER REVIEW" in r.message and "TSLA" in r.message]
    assert review_logs
    assert "Would submit: NO" in review_logs[0]


def test_avoid_candidate_never_reviewed_at_all_in_dry_run(dry_run_config, caplog):
    """select_top_candidates() already excludes Avoid/rejected entries -
    the review loop never even considers them, consistent with every
    other execution-layer path in this codebase."""
    entry = make_entry("GME", label="Avoid")
    with caplog.at_level(logging.INFO):
        _process_execution_layer([entry], "2026-09-09", dry_run_config, logging.getLogger("test"), None, None, broker=None, now=TRADING_HOURS_NOW)

    review_logs = [r.message for r in caplog.records if "DRY_RUN ORDER REVIEW" in r.message and "GME" in r.message]
    assert review_logs == []
