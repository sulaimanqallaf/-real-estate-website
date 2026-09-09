"""Unit tests for paper_trade_tracker.py: the paper-trade lifecycle engine.

Covers stop/target detection without lookahead, the conservative same-bar
stop-wins rule, time exits, idempotency across repeated runs, and P&L/holding-day
math - plus a couple of structural regression checks (High Risk Dip Watchlist
still can't create a paper trade; Aggressive-disabled still blocks approval)
confirming this phase didn't loosen anything from the prior one.
"""

import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import paper_trade_tracker, paper_trades, report_writer
from src.strategies.mean_reversion import STRATEGY_NAME_AGGRESSIVE, STRATEGY_NAME_SAFE
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
LOGGER = logging.getLogger("test_paper_trade_tracker")


def fresh_config(tmp_path, max_holding_days=20):
    config = load_config(CONFIG_PATH)
    config["data"]["journal_dir"] = str(tmp_path)
    config["paper_trading"]["max_holding_days"] = max_holding_days
    return config


def make_open_trade(
    trade_id="NVDA_2026-01-05_aaaaaaaa",
    ticker="NVDA",
    strategy=STRATEGY_NAME_SAFE,
    mode="Safe",
    entry_price=100.0,
    stop_loss=90.0,
    target_price=115.0,
    position_size=10,
    opened_at="2026-01-05",
):
    return {
        "trade_id": trade_id,
        "approved_at": "2026-01-05T10:00:00+00:00",
        "ticker": ticker,
        "strategy": strategy,
        "mode": mode,
        "signal": "Strong candidate",
        "score": 85,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "risk_reward": 1.5,
        "position_size": position_size,
        "risk_amount": 100.0,
        "status": "OPEN",
        "opened_at": opened_at,
        "exit_price": "",
        "exited_at": "",
        "exit_reason": "",
        "pnl_dollars": "",
        "pnl_pct": "",
        "holding_days": "",
        "notes": "",
    }


def make_price_df(bars: dict):
    """bars: {date_str: (open, high, low, close, volume)}"""
    dates = pd.to_datetime(list(bars.keys()))
    rows = list(bars.values())
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=dates)
    df.index.name = "date"
    return df


def seed_paper_trades_csv(config, trades: list[dict]):
    df = pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)
    paper_trades.save_paper_trades_df(df, config)


# --- evaluate_open_trade: no-lookahead stop/target/time-exit detection ------------


def test_open_trade_with_no_exit_condition_stays_open(tmp_path):
    config = fresh_config(tmp_path)
    trade = make_open_trade()
    price_df = make_price_df(
        {
            "2026-01-06": (101, 103, 99, 102, 1_000_000),
            "2026-01-07": (102, 104, 100, 103, 1_000_000),
        }
    )
    result = paper_trade_tracker.evaluate_open_trade(trade, price_df, config)
    assert result is None


def test_target_hit_closes_trade_correctly(tmp_path):
    config = fresh_config(tmp_path)
    trade = make_open_trade(entry_price=100.0, stop_loss=90.0, target_price=115.0, position_size=10)
    price_df = make_price_df(
        {
            "2026-01-06": (101, 105, 99, 103, 1_000_000),
            "2026-01-07": (103, 118, 102, 116, 1_000_000),  # high >= target (115)
        }
    )
    result = paper_trade_tracker.evaluate_open_trade(trade, price_df, config)
    assert result is not None
    assert result["status"] == "TARGET_HIT"
    assert result["exit_price"] == 115.0
    assert result["exited_at"] == "2026-01-07"
    assert result["exit_reason"] == "Target price reached"
    assert result["pnl_dollars"] == pytest.approx((115.0 - 100.0) * 10)
    assert result["pnl_pct"] == pytest.approx((115.0 - 100.0) / 100.0 * 100.0)
    assert result["holding_days"] == 2


def test_stop_hit_closes_trade_correctly(tmp_path):
    config = fresh_config(tmp_path)
    trade = make_open_trade(entry_price=100.0, stop_loss=90.0, target_price=115.0, position_size=10)
    price_df = make_price_df(
        {
            "2026-01-06": (99, 100, 95, 97, 1_000_000),
            "2026-01-07": (97, 98, 88, 89, 1_000_000),  # low <= stop (90)
        }
    )
    result = paper_trade_tracker.evaluate_open_trade(trade, price_df, config)
    assert result is not None
    assert result["status"] == "STOPPED"
    assert result["exit_price"] == 90.0
    assert result["exit_reason"] == "Stop loss triggered"
    assert result["pnl_dollars"] == pytest.approx((90.0 - 100.0) * 10)
    assert result["pnl_dollars"] < 0
    assert result["pnl_pct"] == pytest.approx((90.0 - 100.0) / 100.0 * 100.0)


def test_stop_wins_conservatively_when_both_touched_same_bar(tmp_path):
    """The documented rule: if a single bar's low <= stop AND high >= target, the
    stop is assumed to have been hit first - same convention as backtester.py."""
    config = fresh_config(tmp_path)
    trade = make_open_trade(entry_price=100.0, stop_loss=90.0, target_price=115.0, position_size=10)
    price_df = make_price_df(
        {
            "2026-01-06": (100, 120, 85, 110, 1_000_000),  # both stop (90) and target (115) touched
        }
    )
    result = paper_trade_tracker.evaluate_open_trade(trade, price_df, config)
    assert result["status"] == "STOPPED"
    assert result["exit_price"] == 90.0
    assert result["pnl_dollars"] < 0


def test_time_exit_after_max_holding_days(tmp_path):
    config = fresh_config(tmp_path, max_holding_days=3)
    trade = make_open_trade(entry_price=100.0, stop_loss=80.0, target_price=150.0, opened_at="2026-01-05")
    # Neither stop nor target ever touched; day 3 after entry forces a time exit.
    price_df = make_price_df(
        {
            "2026-01-06": (101, 103, 99, 102, 1_000_000),
            "2026-01-07": (102, 104, 100, 103, 1_000_000),
            "2026-01-08": (103, 105, 101, 104, 1_000_000),  # 3 days after opened_at
        }
    )
    result = paper_trade_tracker.evaluate_open_trade(trade, price_df, config)
    assert result is not None
    assert result["status"] == "TIME_EXIT"
    assert result["exit_price"] == 104.0  # latest available close
    assert result["exited_at"] == "2026-01-08"
    assert "Max holding period" in result["exit_reason"]


def test_no_lookahead_bar_before_entry_is_never_examined(tmp_path):
    """A bar dated the SAME DAY as (or before) opened_at must never be used to
    decide an exit - only bars strictly after it."""
    config = fresh_config(tmp_path)
    trade = make_open_trade(entry_price=100.0, stop_loss=90.0, target_price=115.0, opened_at="2026-01-05")
    price_df = make_price_df(
        {
            "2026-01-05": (100, 200, 1, 100, 1_000_000),  # entry day itself: would trigger both if examined
            "2026-01-06": (101, 103, 99, 102, 1_000_000),  # calm day after - no exit
        }
    )
    result = paper_trade_tracker.evaluate_open_trade(trade, price_df, config)
    assert result is None  # the entry-day bar must have been ignored


# --- check_open_trades: file-level lifecycle + idempotency -----------------------


def test_check_open_trades_closes_and_persists(tmp_path):
    config = fresh_config(tmp_path)
    seed_paper_trades_csv(config, [make_open_trade()])
    price_data = {
        "NVDA": make_price_df(
            {
                "2026-01-06": (101, 105, 99, 103, 1_000_000),
                "2026-01-07": (103, 118, 102, 116, 1_000_000),
            }
        )
    }

    closed = paper_trade_tracker.check_open_trades(price_data, config, LOGGER)

    assert len(closed) == 1
    assert closed[0]["status"] == "TARGET_HIT"

    persisted = paper_trades.load_paper_trades_df(config)
    assert len(persisted) == 1
    assert persisted.iloc[0]["status"] == "TARGET_HIT"


def test_open_trade_remains_open_if_no_exit_hit(tmp_path):
    config = fresh_config(tmp_path)
    seed_paper_trades_csv(config, [make_open_trade()])
    price_data = {"NVDA": make_price_df({"2026-01-06": (101, 103, 99, 102, 1_000_000)})}

    closed = paper_trade_tracker.check_open_trades(price_data, config, LOGGER)

    assert closed == []
    persisted = paper_trades.load_paper_trades_df(config)
    assert persisted.iloc[0]["status"] == "OPEN"


def test_closed_trade_is_not_processed_twice_within_one_call(tmp_path):
    config = fresh_config(tmp_path)
    open_trade = make_open_trade(trade_id="AAA")
    already_closed = {**make_open_trade(trade_id="BBB"), "status": "STOPPED", "exit_price": 80.0}
    seed_paper_trades_csv(config, [open_trade, already_closed])
    price_data = {
        "NVDA": make_price_df(
            {
                "2026-01-06": (101, 105, 99, 103, 1_000_000),
                "2026-01-07": (103, 118, 102, 116, 1_000_000),
            }
        )
    }

    closed = paper_trade_tracker.check_open_trades(price_data, config, LOGGER)

    # Only the OPEN trade (AAA) should have been evaluated/closed - BBB, already
    # STOPPED, must never be re-evaluated or appear in the returned list.
    assert [t["trade_id"] for t in closed] == ["AAA"]


def test_duplicate_daily_run_is_idempotent(tmp_path):
    """Running the lifecycle check twice on identical data must not close the
    same trade twice, duplicate P&L, or return it as newly-closed a second time."""
    config = fresh_config(tmp_path)
    seed_paper_trades_csv(config, [make_open_trade()])
    price_data = {
        "NVDA": make_price_df(
            {
                "2026-01-06": (101, 105, 99, 103, 1_000_000),
                "2026-01-07": (103, 118, 102, 116, 1_000_000),
            }
        )
    }

    first_run_closed = paper_trade_tracker.check_open_trades(price_data, config, LOGGER)
    second_run_closed = paper_trade_tracker.check_open_trades(price_data, config, LOGGER)

    assert len(first_run_closed) == 1
    assert second_run_closed == []  # nothing new to close - already closed

    persisted = paper_trades.load_paper_trades_df(config)
    assert len(persisted) == 1  # no duplicate row
    assert persisted.iloc[0]["status"] == "TARGET_HIT"


def test_missing_price_data_leaves_trade_open_without_crashing(tmp_path):
    config = fresh_config(tmp_path)
    seed_paper_trades_csv(config, [make_open_trade(ticker="GHOST_TICKER")])

    closed = paper_trade_tracker.check_open_trades({}, config, LOGGER)

    assert closed == []
    persisted = paper_trades.load_paper_trades_df(config)
    assert persisted.iloc[0]["status"] == "OPEN"


def test_empty_paper_trades_file_returns_empty_list(tmp_path):
    config = fresh_config(tmp_path)
    assert paper_trade_tracker.check_open_trades({"NVDA": make_price_df({})}, config, LOGGER) == []


# --- exit notification formatting -------------------------------------------------


def test_format_exit_notification_uses_correct_icon_per_status():
    target_trade = {**make_open_trade(), "status": "TARGET_HIT", "exit_price": 115.0, "exited_at": "2026-01-07",
                     "exit_reason": "Target price reached", "pnl_dollars": 150.0, "pnl_pct": 15.0, "holding_days": 2}
    stop_trade = {**target_trade, "status": "STOPPED", "pnl_dollars": -100.0, "pnl_pct": -10.0}
    time_trade = {**target_trade, "status": "TIME_EXIT", "pnl_dollars": 20.0, "pnl_pct": 2.0}

    assert "🎯" in paper_trade_tracker.format_exit_notification(target_trade)
    assert "🛑" in paper_trade_tracker.format_exit_notification(stop_trade)
    assert "⏱" in paper_trade_tracker.format_exit_notification(time_trade)


def test_format_exit_notification_includes_required_fields():
    trade = {**make_open_trade(), "status": "TARGET_HIT", "exit_price": 115.0, "exited_at": "2026-01-07",
              "exit_reason": "Target price reached", "pnl_dollars": 150.0, "pnl_pct": 15.0, "holding_days": 2}
    text = paper_trade_tracker.format_exit_notification(trade)
    assert "NVDA" in text
    assert "Safe" in text
    assert "100.0" in text  # entry
    assert "115.0" in text  # exit
    assert "15.00%" in text
    assert "150.00" in text
    assert "2" in text  # holding days
    assert "Target price reached" in text
    assert "no real order" in text.lower()


# --- structural regression checks (nothing loosened from the prior phase) --------


def test_high_risk_dip_watchlist_entry_can_never_become_a_paper_trade(tmp_path):
    """A ticker that only ever appears in the High Risk Dip Watchlist (Aggressive
    triggered, but not eligible for Top Candidates) must never be able to produce
    a pending approval, and therefore never a paper_trades.csv row."""
    config = fresh_config(tmp_path)
    watchlist_only_entry = {
        "symbol": "SPY",
        "label": "Avoid",  # fails the non-Avoid gate even though a candidate exists
        "score": 20,
        "best_risk_result": {
            "strategy": STRATEGY_NAME_AGGRESSIVE,
            "tradeable": True,
            "entry": 90.0,
            "stop_loss": 85.0,
            "target": 100.0,
            "risk_reward": 2.0,
            "expected_upside_pct": 11.0,
            "expected_downside_pct": 5.0,
            "shares": 10,
            "dollar_risk": 50.0,
        },
    }
    top_candidates = report_writer.select_top_candidates([watchlist_only_entry], config)
    assert top_candidates == []  # never eligible, so main.py never sends it an approval message


def test_aggressive_disabled_still_blocks_approval_after_schema_change(tmp_path):
    config = fresh_config(tmp_path)
    assert config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] is False
    entry = {
        "symbol": "SPY",
        "label": "Weak watchlist",
        "score": 55,
        "best_risk_result": {
            "strategy": STRATEGY_NAME_AGGRESSIVE,
            "tradeable": True,
            "entry": 90.0,
            "stop_loss": 85.0,
            "target": 100.0,
            "risk_reward": 2.0,
            "expected_upside_pct": 11.0,
            "expected_downside_pct": 5.0,
            "shares": 10,
            "dollar_risk": 50.0,
        },
    }
    assert report_writer.select_top_candidates([entry], config) == []
