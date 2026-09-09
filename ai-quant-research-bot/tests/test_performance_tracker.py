"""Unit tests for performance_tracker.py: portfolio and strategy-level analytics
computed purely from CLOSED paper trades. Confirms zero-data cases are handled
cleanly (no fabricated 0% metrics) and that Safe/Aggressive + strategy-family
grouping is correct.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import paper_trades, performance_tracker
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config(tmp_path):
    config = load_config(CONFIG_PATH)
    config["data"]["journal_dir"] = str(tmp_path)
    return config


def make_trade(
    trade_id, ticker="NVDA", strategy="Trend Following", mode="N/A", status="TARGET_HIT",
    entry_price=100.0, exit_price=110.0, position_size=10, exited_at="2026-01-10", opened_at="2026-01-05",
):
    pnl_dollars = (exit_price - entry_price) * position_size
    pnl_pct = (exit_price - entry_price) / entry_price * 100.0
    return {
        "trade_id": trade_id, "approved_at": "2026-01-05T10:00:00+00:00", "ticker": ticker,
        "strategy": strategy, "mode": mode, "signal": "Strong candidate", "score": 80,
        "entry_price": entry_price, "stop_loss": entry_price * 0.9, "target_price": entry_price * 1.15,
        "risk_reward": 2.0, "position_size": position_size, "risk_amount": 50.0,
        "status": status, "opened_at": opened_at, "exit_price": exit_price if status != "OPEN" else "",
        "exited_at": exited_at if status != "OPEN" else "", "exit_reason": "test",
        "pnl_dollars": round(pnl_dollars, 2) if status != "OPEN" else "",
        "pnl_pct": round(pnl_pct, 2) if status != "OPEN" else "",
        "holding_days": 5 if status != "OPEN" else "", "notes": "",
    }


def seed(config, trades):
    df = pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)
    paper_trades.save_paper_trades_df(df, config)


# --- zero-data handling -----------------------------------------------------------


def test_portfolio_performance_with_no_trades_at_all(tmp_path):
    config = fresh_config(tmp_path)
    result = performance_tracker.compute_portfolio_performance(config)
    assert result == {"has_data": False, "total_trades": 0, "open_trades": 0, "closed_trades": 0}


def test_portfolio_performance_with_only_open_trades(tmp_path):
    config = fresh_config(tmp_path)
    seed(config, [make_trade("A", status="OPEN")])
    result = performance_tracker.compute_portfolio_performance(config)
    assert result["has_data"] is False
    assert result["total_trades"] == 1
    assert result["open_trades"] == 1
    assert result["closed_trades"] == 0


def test_strategy_breakdown_with_no_closed_trades(tmp_path):
    config = fresh_config(tmp_path)
    seed(config, [make_trade("A", status="OPEN")])
    result = performance_tracker.compute_strategy_breakdown(config)
    assert result == {"has_data": False, "by_strategy": {}, "by_mean_reversion_mode": {}, "by_ticker": {}}


# --- portfolio math ----------------------------------------------------------------


def test_pnl_dollars_and_pct_are_correct():
    config_trade = make_trade("A", entry_price=100.0, exit_price=115.0, position_size=10, status="TARGET_HIT")
    assert config_trade["pnl_dollars"] == 150.0
    assert config_trade["pnl_pct"] == 15.0


def test_holding_days_reflects_entry_to_exit_span():
    trade = make_trade("A", opened_at="2026-01-05", exited_at="2026-01-10")
    assert trade["holding_days"] == 5


def test_win_rate_and_profit_factor():
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    config = fresh_config(tmp)
    seed(
        config,
        [
            make_trade("A", entry_price=100.0, exit_price=110.0, status="TARGET_HIT"),  # win: +100
            make_trade("B", entry_price=100.0, exit_price=90.0, status="STOPPED"),  # loss: -100
            make_trade("C", entry_price=100.0, exit_price=120.0, status="TARGET_HIT"),  # win: +200
        ],
    )
    result = performance_tracker.compute_portfolio_performance(config)
    assert result["has_data"] is True
    assert result["closed_trades"] == 3
    assert result["wins"] == 2
    assert result["losses"] == 1
    assert result["win_rate_pct"] == round(2 / 3 * 100, 2)
    assert result["profit_factor"] == round(300.0 / 100.0, 2)
    assert result["total_pnl_dollars"] == 200.0


def test_profit_factor_is_none_when_there_are_zero_losses(tmp_path):
    config = fresh_config(tmp_path)
    seed(config, [make_trade("A", entry_price=100.0, exit_price=110.0, status="TARGET_HIT")])
    result = performance_tracker.compute_portfolio_performance(config)
    assert result["losses"] == 0
    assert result["profit_factor"] is None  # never a fabricated division by zero
    assert result["avg_loss_pct"] is None


def test_expectancy_hidden_below_minimum_sample_size(tmp_path):
    config = fresh_config(tmp_path)
    seed(config, [make_trade("A", entry_price=100.0, exit_price=110.0, status="TARGET_HIT")])
    result = performance_tracker.compute_portfolio_performance(config)
    assert result["sample_size"] == 1
    assert result["sample_size"] < performance_tracker.MIN_TRADES_FOR_ADVANCED_STATS
    assert result["expectancy_per_trade_dollars"] is None


def test_expectancy_shown_at_minimum_sample_size(tmp_path):
    config = fresh_config(tmp_path)
    n = performance_tracker.MIN_TRADES_FOR_ADVANCED_STATS
    seed(config, [make_trade(f"T{i}", entry_price=100.0, exit_price=110.0, status="TARGET_HIT") for i in range(n)])
    result = performance_tracker.compute_portfolio_performance(config)
    assert result["sample_size"] == n
    assert result["expectancy_per_trade_dollars"] == 100.0


def test_max_consecutive_wins_and_losses(tmp_path):
    config = fresh_config(tmp_path)
    # Chronological order (by exited_at): win, win, loss, loss, loss, win
    seed(
        config,
        [
            make_trade("A", exit_price=110.0, status="TARGET_HIT", exited_at="2026-01-01"),
            make_trade("B", exit_price=110.0, status="TARGET_HIT", exited_at="2026-01-02"),
            make_trade("C", exit_price=90.0, status="STOPPED", exited_at="2026-01-03"),
            make_trade("D", exit_price=90.0, status="STOPPED", exited_at="2026-01-04"),
            make_trade("E", exit_price=90.0, status="STOPPED", exited_at="2026-01-05"),
            make_trade("F", exit_price=110.0, status="TARGET_HIT", exited_at="2026-01-06"),
        ],
    )
    result = performance_tracker.compute_portfolio_performance(config)
    assert result["max_consecutive_wins"] == 2
    assert result["max_consecutive_losses"] == 3


# --- strategy / mode / ticker breakdown --------------------------------------------


def test_safe_and_aggressive_mean_reversion_are_separated(tmp_path):
    config = fresh_config(tmp_path)
    seed(
        config,
        [
            make_trade("A", strategy="Mean Reversion (Safe)", mode="Safe", exit_price=110.0, status="TARGET_HIT"),
            make_trade("B", strategy="Mean Reversion (Safe)", mode="Safe", exit_price=110.0, status="TARGET_HIT"),
            make_trade(
                "C", strategy="Mean Reversion (Aggressive)", mode="Aggressive", exit_price=90.0, status="STOPPED"
            ),
        ],
    )
    breakdown = performance_tracker.compute_strategy_breakdown(config)
    assert breakdown["has_data"] is True
    assert breakdown["by_mean_reversion_mode"]["Safe"]["sample_size"] == 2
    assert breakdown["by_mean_reversion_mode"]["Aggressive"]["sample_size"] == 1
    assert breakdown["by_mean_reversion_mode"]["Safe"]["win_rate_pct"] == 100.0
    assert breakdown["by_mean_reversion_mode"]["Aggressive"]["win_rate_pct"] == 0.0
    # Both modes collapse into one "Mean Reversion" family for the top-level breakdown.
    assert breakdown["by_strategy"]["Mean Reversion"]["sample_size"] == 3


def test_strategy_family_groups_momentum_and_trend_separately(tmp_path):
    config = fresh_config(tmp_path)
    seed(
        config,
        [
            make_trade("A", strategy="Momentum Breakout", mode="N/A", exit_price=110.0, status="TARGET_HIT"),
            make_trade("B", strategy="Trend Following", mode="N/A", exit_price=90.0, status="STOPPED"),
        ],
    )
    breakdown = performance_tracker.compute_strategy_breakdown(config)
    assert set(breakdown["by_strategy"].keys()) == {"Momentum Breakout", "Trend Following"}
    assert breakdown["by_strategy"]["Momentum Breakout"]["wins"] == 1
    assert breakdown["by_strategy"]["Trend Following"]["losses"] == 1
    assert breakdown["by_mean_reversion_mode"] == {}  # no mean-reversion trades at all


def test_ticker_level_breakdown(tmp_path):
    config = fresh_config(tmp_path)
    seed(
        config,
        [
            make_trade("A", ticker="NVDA", exit_price=110.0, status="TARGET_HIT"),
            make_trade("B", ticker="NVDA", exit_price=90.0, status="STOPPED"),
            make_trade("C", ticker="AMD", exit_price=110.0, status="TARGET_HIT"),
        ],
    )
    breakdown = performance_tracker.compute_strategy_breakdown(config)
    assert breakdown["by_ticker"]["NVDA"]["sample_size"] == 2
    assert breakdown["by_ticker"]["AMD"]["sample_size"] == 1
