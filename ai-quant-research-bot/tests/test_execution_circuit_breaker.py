"""Phase 7 Part Z - Risk (daily/weekly/drawdown breakers, stale-data
breaker, manual halt, max open positions, max new trades/day) and the
"never loosen existing limits" invariant.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.execution import circuit_breaker as cb
from src.execution.broker import ACCOUNT_MODE_LIVE, ACCOUNT_MODE_PAPER, CONNECTION_CONNECTED, CONNECTION_DISCONNECTED, AccountSummary


def paper_account():
    return AccountSummary(account_id="DU1", account_mode=ACCOUNT_MODE_PAPER, net_liquidation=10_000, available_funds=10_000, buying_power=20_000)


# --- effective_execution_risk_limits: never looser than existing limits -----------


def test_effective_limits_default_to_the_documented_defaults_when_config_is_empty():
    limits = cb.effective_execution_risk_limits({})
    assert limits == cb.DEFAULT_EXECUTION_RISK


def test_effective_limits_are_clamped_by_stricter_existing_portfolio_risk_max_positions():
    config = {"execution_risk": {"max_open_positions": 10}, "portfolio_risk": {"max_open_positions": 3}}
    limits = cb.effective_execution_risk_limits(config)
    assert limits["max_open_positions"] == 3


def test_effective_limits_never_loosened_when_execution_risk_is_stricter():
    config = {"execution_risk": {"max_open_positions": 2}, "portfolio_risk": {"max_open_positions": 8}}
    limits = cb.effective_execution_risk_limits(config)
    assert limits["max_open_positions"] == 2


def test_effective_limits_clamps_total_open_risk_pct_too():
    config = {"execution_risk": {"max_total_open_risk_pct": 0.05}, "portfolio_risk": {"max_total_open_risk_pct": 0.01}}
    limits = cb.effective_execution_risk_limits(config)
    assert limits["max_total_open_risk_pct"] == 0.01


# --- individual breaker checks -------------------------------------------------------


def test_check_daily_loss_trips_at_the_limit():
    limits = {"max_daily_loss_pct": 0.01}
    assert cb.check_daily_loss(-0.01, limits) == cb.BREAKER_DAILY_LOSS_LIMIT
    assert cb.check_daily_loss(-0.005, limits) is None
    assert cb.check_daily_loss(None, limits) is None


def test_check_weekly_loss_trips_at_the_limit():
    limits = {"max_weekly_loss_pct": 0.03}
    assert cb.check_weekly_loss(-0.03, limits) == cb.BREAKER_WEEKLY_LOSS_LIMIT
    assert cb.check_weekly_loss(-0.01, limits) is None


def test_check_drawdown_trips_at_the_limit():
    limits = {"max_drawdown_pct": 0.10}
    assert cb.check_drawdown(0.10, limits) == cb.BREAKER_MAX_DRAWDOWN
    assert cb.check_drawdown(0.05, limits) is None


def test_check_data_staleness_trips_past_max_age():
    assert cb.check_data_staleness(4, max_age_days=3) == cb.BREAKER_DATA_STALE
    assert cb.check_data_staleness(2, max_age_days=3) is None
    assert cb.check_data_staleness(None) is None


def test_check_reconciliation_trips_when_not_ok():
    assert cb.check_reconciliation(False) == cb.BREAKER_RECONCILIATION_FAILURE
    assert cb.check_reconciliation(True) is None


def test_check_excessive_rejections_trips_at_threshold():
    assert cb.check_excessive_rejections(3, max_rejections=3) == cb.BREAKER_EXCESSIVE_ORDER_REJECTIONS
    assert cb.check_excessive_rejections(2, max_rejections=3) is None


def test_check_abnormal_position_state_trips_on_any_unexplained_position():
    assert cb.check_abnormal_position_state(1) == cb.BREAKER_ABNORMAL_POSITION_STATE
    assert cb.check_abnormal_position_state(0) is None


def test_check_max_open_positions_trips_at_the_limit():
    limits = {"max_open_positions": 6}
    assert cb.check_max_open_positions(6, limits) == cb.BREAKER_MAX_OPEN_POSITIONS
    assert cb.check_max_open_positions(5, limits) is None


def test_check_max_new_trades_per_day_trips_at_the_limit():
    limits = {"max_new_trades_per_day": 3}
    assert cb.check_max_new_trades_per_day(3, limits) == cb.BREAKER_MAX_NEW_TRADES_PER_DAY
    assert cb.check_max_new_trades_per_day(2, limits) is None


def test_check_account_mode_trips_for_live_or_missing_account():
    assert cb.check_account_mode(None) == cb.BREAKER_ACCOUNT_MODE_UNVERIFIED
    live = AccountSummary(account_id="U1", account_mode=ACCOUNT_MODE_LIVE, net_liquidation=1, available_funds=1, buying_power=1)
    assert cb.check_account_mode(live) == cb.BREAKER_ACCOUNT_MODE_UNVERIFIED
    assert cb.check_account_mode(paper_account()) is None


def test_check_broker_connection_trips_when_not_connected():
    assert cb.check_broker_connection(CONNECTION_DISCONNECTED) == cb.BREAKER_BROKER_DISCONNECTED
    assert cb.check_broker_connection(CONNECTION_CONNECTED) is None


# --- check_all aggregation ------------------------------------------------------------


def test_check_all_clean_state_is_not_blocked():
    result = cb.check_all({}, account=paper_account(), connection_state=CONNECTION_CONNECTED)
    assert result.blocked is False
    assert result.tripped == []


def test_check_all_aggregates_every_tripped_breaker():
    result = cb.check_all(
        {}, account=None, connection_state=CONNECTION_DISCONNECTED,
        realized_pnl_today_pct=-0.5, reconciliation_ok=False,
    )
    assert result.blocked is True
    assert cb.BREAKER_ACCOUNT_MODE_UNVERIFIED in result.tripped
    assert cb.BREAKER_BROKER_DISCONNECTED in result.tripped
    assert cb.BREAKER_DAILY_LOSS_LIMIT in result.tripped
    assert cb.BREAKER_RECONCILIATION_FAILURE in result.tripped


# --- manual kill switch (Part M) ------------------------------------------------------


def test_halt_creates_persistent_state_and_is_halted_reports_it(tmp_path):
    config = {"execution": {"halt_state_file": str(tmp_path / "halt.json")}}
    assert cb.is_halted(config) == (False, None)
    cb.halt(config, reason="test halt")
    halted, reason = cb.is_halted(config)
    assert halted is True
    assert reason == "test halt"


def test_resume_clears_manual_halt_file(tmp_path):
    config = {"execution": {"halt_state_file": str(tmp_path / "halt.json")}}
    cb.halt(config)
    cb.resume(config)
    assert cb.is_halted(config) == (False, None)


def test_resume_is_a_noop_when_never_halted(tmp_path):
    config = {"execution": {"halt_state_file": str(tmp_path / "halt.json")}}
    cb.resume(config)  # must not raise
    assert cb.is_halted(config) == (False, None)


def test_manual_kill_switch_breaker_trips_check_all(tmp_path):
    config = {"execution": {"halt_state_file": str(tmp_path / "halt.json")}}
    cb.halt(config, reason="stop everything")
    result = cb.check_all(config, account=paper_account(), connection_state=CONNECTION_CONNECTED)
    assert cb.BREAKER_MANUAL_KILL_SWITCH in result.tripped


def test_status_reports_the_same_thing_as_is_halted(tmp_path):
    config = {"execution": {"halt_state_file": str(tmp_path / "halt.json")}}
    cb.halt(config, reason="abc")
    assert cb.status(config) == {"halted": True, "reason": "abc"}


def test_cli_halt_resume_status_round_trip(tmp_path, capsys, monkeypatch):
    halt_file = tmp_path / "halt.json"

    from src import utils

    monkeypatch.setattr(utils, "load_config", lambda path=None: {"execution": {"halt_state_file": str(halt_file)}})

    import sys as _sys

    monkeypatch.setattr(_sys, "argv", ["circuit_breaker", "halt", "--reason", "cli test"])
    cb.main()
    assert halt_file.exists()

    monkeypatch.setattr(_sys, "argv", ["circuit_breaker", "status"])
    cb.main()
    out = capsys.readouterr().out
    assert "cli test" in out

    monkeypatch.setattr(_sys, "argv", ["circuit_breaker", "resume"])
    cb.main()
    assert not halt_file.exists()
