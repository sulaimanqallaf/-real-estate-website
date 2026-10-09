"""Phase 7 Part Z - Risk (daily/weekly/drawdown breakers, stale-data
breaker, manual halt, max open positions, max new trades/day) and the
"never loosen existing limits" invariant.
"""

import logging
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


def test_is_halted_fails_closed_on_a_corrupted_halt_file(tmp_path):
    """Sprint 3 fault injection: a REAL corrupted halt-state file (a
    disk error, a crash mid-write) must be treated as halted - the
    opposite conservatism from read_reconciliation_status()'s "corrupt
    = no record" choice, because the manual kill switch has no other
    independent signal anywhere else. Must never raise either, since
    check_all()'s own docstring promises that."""
    halt_path = tmp_path / "halt.json"
    halt_path.write_text("{not valid json!!!", encoding="utf-8")
    config = {"execution": {"halt_state_file": str(halt_path)}}

    halted, reason = cb.is_halted(config)

    assert halted is True
    assert "failing closed" in reason.lower()


def test_check_all_never_raises_on_a_corrupted_halt_file(tmp_path):
    halt_path = tmp_path / "halt.json"
    halt_path.write_text("{not valid json!!!", encoding="utf-8")
    config = {"execution": {"halt_state_file": str(halt_path)}}

    result = cb.check_all(config)  # must not raise

    assert cb.BREAKER_MANUAL_KILL_SWITCH in result.tripped


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


# --- live_risk_inputs: the daily/weekly/drawdown/new-trade/open-position breakers ---
# must actually be fed real numbers, not just be definable - these prove the feed,
# then prove it actually blocks a real entry end to end.


from datetime import datetime, timezone  # noqa: E402

from src import paper_trades  # noqa: E402


def _closed_row(ticker="AMD", opened_at="2026-09-09", exited_at="2026-09-09", pnl_dollars=0.0, status="TARGET_HIT"):
    row = {col: "" for col in paper_trades.PAPER_TRADE_COLUMNS}
    row.update(
        trade_id=f"{ticker}_{opened_at}_x", ticker=ticker, strategy="Trend Following", mode="",
        status=status, opened_at=opened_at, exited_at=exited_at, exit_price=100.0, exit_reason="test",
        pnl_dollars=pnl_dollars, pnl_pct=0.0, holding_days=0, entry_price=100.0, position_size=10,
    )
    return row


def _open_row(ticker="NVDA", opened_at="2026-09-09"):
    row = {col: "" for col in paper_trades.PAPER_TRADE_COLUMNS}
    row.update(trade_id=f"{ticker}_{opened_at}_x", ticker=ticker, strategy="Trend Following", status="OPEN", opened_at=opened_at, entry_price=100.0, position_size=10)
    return row


def _config_with_paper_trades(tmp_path, rows, account_equity=10_000.0):
    import pandas as pd

    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    df = pd.DataFrame(rows, columns=paper_trades.PAPER_TRADE_COLUMNS)
    config = {
        "data": {"journal_dir": str(journal_dir)},
        "paper_trading": {"paper_trades_file": "paper_trades.csv"},
        "risk": {"account_equity": account_equity},
    }
    paper_trades.save_paper_trades_df(df, config)
    return config


AS_OF = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)  # a Wednesday


def test_compute_daily_realized_pnl_pct_sums_only_todays_closes(tmp_path):
    rows = [_closed_row(exited_at="2026-09-09", pnl_dollars=-50.0), _closed_row(exited_at="2026-09-08", pnl_dollars=-500.0)]
    config = _config_with_paper_trades(tmp_path, rows)
    df = paper_trades.load_paper_trades_df(config)
    pct = cb.compute_daily_realized_pnl_pct(df, AS_OF, config["risk"]["account_equity"])
    assert pct == pytest.approx(-50.0 / 10_000.0)


def test_compute_daily_realized_pnl_pct_is_zero_not_none_with_no_trades_today(tmp_path):
    config = _config_with_paper_trades(tmp_path, [])
    df = paper_trades.load_paper_trades_df(config)
    assert cb.compute_daily_realized_pnl_pct(df, AS_OF, config["risk"]["account_equity"]) == 0.0


def test_compute_weekly_realized_pnl_pct_sums_the_whole_week(tmp_path):
    # 2026-09-09 is a Wednesday; Monday is 2026-09-07.
    rows = [_closed_row(exited_at="2026-09-07", pnl_dollars=-100.0), _closed_row(exited_at="2026-09-09", pnl_dollars=-50.0), _closed_row(exited_at="2026-08-31", pnl_dollars=-9999.0)]
    config = _config_with_paper_trades(tmp_path, rows)
    df = paper_trades.load_paper_trades_df(config)
    pct = cb.compute_weekly_realized_pnl_pct(df, AS_OF, config["risk"]["account_equity"])
    assert pct == pytest.approx(-150.0 / 10_000.0)


def test_compute_current_drawdown_pct_reflects_the_running_equity_curve(tmp_path):
    rows = [_closed_row(exited_at="2026-09-01", pnl_dollars=500.0), _closed_row(exited_at="2026-09-05", pnl_dollars=-1000.0)]
    config = _config_with_paper_trades(tmp_path, rows)
    df = paper_trades.load_paper_trades_df(config)
    pct = cb.compute_current_drawdown_pct(df, config["risk"]["account_equity"])
    # peak equity = 10500 (after the +500 trade); current = 9500 -> drawdown = 1000/10500
    assert pct == pytest.approx(1000.0 / 10_500.0)


def test_compute_current_drawdown_pct_is_zero_with_no_closed_trades(tmp_path):
    config = _config_with_paper_trades(tmp_path, [])
    df = paper_trades.load_paper_trades_df(config)
    assert cb.compute_current_drawdown_pct(df, config["risk"]["account_equity"]) == 0.0


def test_count_new_trades_today_counts_rows_opened_today_only(tmp_path):
    rows = [_open_row(opened_at="2026-09-09"), _open_row(ticker="AMD", opened_at="2026-09-09"), _closed_row(opened_at="2026-09-08", exited_at="2026-09-09")]
    config = _config_with_paper_trades(tmp_path, rows)
    df = paper_trades.load_paper_trades_df(config)
    assert cb.count_new_trades_today(df, AS_OF) == 2


def test_count_current_open_positions_counts_only_open_status(tmp_path):
    rows = [_open_row(), _open_row(ticker="AMD"), _closed_row()]
    config = _config_with_paper_trades(tmp_path, rows)
    df = paper_trades.load_paper_trades_df(config)
    assert cb.count_current_open_positions(df) == 2


def test_live_risk_inputs_returns_every_key_check_all_expects(tmp_path):
    config = _config_with_paper_trades(tmp_path, [_closed_row(pnl_dollars=-10.0)])
    inputs = cb.live_risk_inputs(config, as_of=AS_OF)
    assert set(inputs) == {"realized_pnl_today_pct", "realized_pnl_week_pct", "current_drawdown_pct", "new_trades_today", "current_open_positions"}


def test_live_risk_inputs_degrades_cleanly_with_an_incomplete_config():
    """A config with no data.journal_dir/paper_trading section (common in
    unit tests, and theoretically a genuinely incomplete deployment
    config) must never crash the caller - every breaker simply has
    nothing to trip on, same as "Data Unavailable" everywhere else."""
    inputs = cb.live_risk_inputs({"risk": {"account_equity": 10_000}})
    assert inputs["new_trades_today"] == 0
    assert inputs["current_open_positions"] == 0


def test_daily_loss_breaker_actually_trips_a_real_entry_via_approval_bridge(tmp_path):
    """End-to-end proof (not just the unit computation above): a real
    day's realized loss, read from paper_trades.csv exactly as it would
    be in production, blocks a brand-new entry through the SAME
    execute_approved_trade() path AUTO_EXECUTE and manual Telegram
    approval both use."""
    from src.execution import approval_bridge, order_manager
    from src.execution.broker import FakeBroker

    # execute_approved_trade() always evaluates against the real current
    # time (no `as_of` injection point, deliberately - production entries
    # are always "right now") - so this trade must be dated TODAY for the
    # daily-loss window to actually include it.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    config = _config_with_paper_trades(tmp_path, [_closed_row(exited_at=today, pnl_dollars=-200.0)], account_equity=10_000.0)
    config["execution_risk"] = {"max_daily_loss_pct": 0.01}  # -200/10_000 = -2% > 1% limit
    config["execution"] = {"trading_hours_start": "00:00", "trading_hours_end": "23:59"}

    broker = FakeBroker()
    broker.connect()
    manager = order_manager.OrderManager(broker, config)
    record = {"symbol": "AMD", "strategy": "Trend Following", "score": 90, "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 50.0}

    result = approval_bridge.execute_approved_trade(record, config, broker, manager, current_market_price=100.0, logger=logging.getLogger("test"))

    assert result["executed"] is False
    assert cb.BREAKER_DAILY_LOSS_LIMIT in result["reasons"]
    assert broker.submitted_intents == []


def test_max_new_trades_per_day_breaker_actually_trips_a_real_entry(tmp_path):
    from src.execution import approval_bridge, order_manager
    from src.execution.broker import FakeBroker

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    config = _config_with_paper_trades(tmp_path, [_open_row(ticker="NVDA", opened_at=today), _open_row(ticker="MSFT", opened_at=today)])
    config["execution_risk"] = {"max_new_trades_per_day": 2}
    config["execution"] = {"trading_hours_start": "00:00", "trading_hours_end": "23:59"}

    broker = FakeBroker()
    broker.connect()
    manager = order_manager.OrderManager(broker, config)
    record = {"symbol": "AMD", "strategy": "Trend Following", "score": 90, "entry": 100.0, "stop_loss": 95.0, "target": 115.0, "shares": 10, "dollar_risk": 50.0}

    result = approval_bridge.execute_approved_trade(record, config, broker, manager, current_market_price=100.0, logger=logging.getLogger("test"))

    assert result["executed"] is False
    assert cb.BREAKER_MAX_NEW_TRADES_PER_DAY in result["reasons"]
    assert broker.submitted_intents == []
