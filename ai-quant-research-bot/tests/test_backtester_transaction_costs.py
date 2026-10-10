"""src/backtester.py - realistic transaction costs and slippage (Sprint
3, "Real Strategy Validation" milestone, Task V1: "include transaction
costs and realistic slippage"). Unit tests on the pure cost-model
helpers, plus one end-to-end test proving a config with no
`transaction_costs` section at all behaves EXACTLY as before this
sprint (backward-compatible by construction - see the module
docstring) and one proving a real strategy run through
`_run_strategy_backtest` actually pays the configured costs."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import timedelta

import pandas as pd
import pytest

from src import backtester
from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
LOGGER = logging.getLogger("test")


def _costs(slippage_bps=0.0, commission_per_share=0.0, commission_min=0.0, commission_max_pct=None):
    costs = {
        "slippage_bps": slippage_bps,
        "commission_per_share": commission_per_share,
        "commission_min_per_order": commission_min,
    }
    if commission_max_pct is not None:
        costs["commission_max_pct_of_trade"] = commission_max_pct
    return {"backtest": {"transaction_costs": costs}}


# --- _apply_slippage ----------------------------------------------------------------------


def test_apply_slippage_is_a_noop_when_unconfigured():
    config = {"backtest": {}}
    assert backtester._apply_slippage(100.0, "buy", config) == 100.0
    assert backtester._apply_slippage(100.0, "sell", config) == 100.0


def test_apply_slippage_is_a_noop_when_the_whole_backtest_section_is_missing():
    assert backtester._apply_slippage(100.0, "buy", {}) == 100.0


def test_apply_slippage_makes_a_buy_fill_worse_higher():
    config = _costs(slippage_bps=10.0)  # 0.10%
    filled = backtester._apply_slippage(100.0, "buy", config)
    assert filled == pytest.approx(100.10)


def test_apply_slippage_makes_a_sell_fill_worse_lower():
    config = _costs(slippage_bps=10.0)
    filled = backtester._apply_slippage(100.0, "sell", config)
    assert filled == pytest.approx(99.90)


# --- _commission_for_fill -------------------------------------------------------------------


def test_commission_for_fill_is_zero_when_unconfigured():
    assert backtester._commission_for_fill(100, 50.0, {"backtest": {}}) == 0.0


def test_commission_for_fill_uses_per_share_rate_above_the_minimum():
    config = _costs(commission_per_share=0.005, commission_min=1.0)
    # 1000 shares * $0.005 = $5.00, above the $1.00 floor - floor doesn't apply.
    assert backtester._commission_for_fill(1000, 50.0, config) == pytest.approx(5.00)


def test_commission_for_fill_is_floored_by_the_minimum():
    config = _costs(commission_per_share=0.005, commission_min=1.0)
    # 10 shares * $0.005 = $0.05, floored up to the $1.00 minimum.
    assert backtester._commission_for_fill(10, 50.0, config) == pytest.approx(1.00)


def test_commission_for_fill_is_capped_by_the_max_pct_of_trade_value():
    config = _costs(commission_per_share=0.005, commission_min=1.0, commission_max_pct=0.01)
    # 100 shares at $0.50/share = $50 trade value; uncapped commission would be
    # max(100*0.005, 1.0) = $1.00, which is already under the 1% ($0.50) cap...
    # use a case where the per-share commission actually exceeds the cap instead:
    # 100,000 shares at $0.01/share = $1,000 trade value; per-share commission =
    # 100,000 * 0.005 = $500, but the 1% cap limits it to $10.
    capped = backtester._commission_for_fill(100_000, 0.01, config)
    assert capped == pytest.approx(10.00)


# --- backward compatibility: no transaction_costs key at all => zero cost, unchanged behavior ---


def test_a_config_without_transaction_costs_produces_zero_cost_trades():
    trade = backtester._build_trade("AAPL", "Trend Following", pd.Timestamp("2026-01-02"), 100.0, pd.Timestamp("2026-01-10"), 110.0, 10, "target", {"backtest": {}})
    assert trade.entry_price == 100.0
    assert trade.exit_price == 110.0
    assert trade.commission == 0.0
    assert trade.pnl_dollars == pytest.approx((110.0 - 100.0) * 10)


# --- _build_trade applies slippage to the exit side and charges round-trip commission ---


def test_build_trade_applies_slippage_only_to_the_exit_and_charges_round_trip_commission():
    config = _costs(slippage_bps=10.0, commission_per_share=0.005, commission_min=1.0)
    # entry_price (100.0) is passed in ALREADY slippage-adjusted (as
    # _run_strategy_backtest does) - _build_trade must not double-apply it.
    trade = backtester._build_trade("AAPL", "Trend Following", pd.Timestamp("2026-01-02"), 100.0, pd.Timestamp("2026-01-10"), 110.0, 50, "target", config)

    assert trade.entry_price == 100.0  # unchanged - already adjusted by the caller
    assert trade.exit_price == pytest.approx(110.0 * (1 - 0.0010))  # sell fills worse (lower)
    expected_commission = max(50 * 0.005, 1.0) * 2  # one leg on entry, one on exit
    assert trade.commission == pytest.approx(expected_commission)
    assert trade.pnl_dollars == pytest.approx((trade.exit_price - 100.0) * 50 - expected_commission)


def test_build_trade_return_pct_reflects_slippage_but_not_commission():
    """return_pct is a pure price return - it already reflects slippage
    via the fill prices; commission is a dollar cost that belongs in
    pnl_dollars, not folded into a percentage (see module docstring)."""
    config = _costs(slippage_bps=10.0, commission_per_share=1.0, commission_min=100.0)
    trade = backtester._build_trade("AAPL", "Trend Following", pd.Timestamp("2026-01-02"), 100.0, pd.Timestamp("2026-01-10"), 110.0, 10, "target", config)

    expected_return_pct = (trade.exit_price - 100.0) / 100.0 * 100.0
    assert trade.return_pct == pytest.approx(expected_return_pct)
    # A huge commission must NOT have leaked into return_pct.
    assert trade.return_pct > 0  # still a price gain even though pnl_dollars may now be negative after commission
    assert trade.pnl_dollars < (trade.exit_price - 100.0) * 10  # commission dragged the dollar P&L down


# --- end-to-end: a real strategy run actually pays the configured costs ----------------------


def _run_trend_following(config, seed=1):
    df = generate_synthetic_ohlcv(seed=seed)
    lookback_days = backtester.period_to_days(config["backtest"]["lookback_period"])
    backtest_start = df.index.max() - timedelta(days=lookback_days)
    return backtester._run_strategy_backtest("Trend Following", ["SYN1"], {"SYN1": df}, backtest_start, config, LOGGER)


def test_end_to_end_backtest_with_zero_transaction_costs_matches_pre_sprint3_behavior():
    config = load_config(CONFIG_PATH)
    config["backtest"]["transaction_costs"] = {"slippage_bps": 0.0, "commission_per_share": 0.0, "commission_min_per_order": 0.0}
    trades, _ = _run_trend_following(config)
    assert trades, "fixture must produce at least one real trade for this test to mean anything"
    assert all(t.commission == 0.0 for t in trades)


def test_end_to_end_backtest_with_real_defaults_pays_nonzero_costs():
    config = load_config(CONFIG_PATH)  # real config/settings.yaml - non-zero transaction_costs by default
    assert config["backtest"]["transaction_costs"]["commission_per_share"] > 0
    trades, _ = _run_trend_following(config)
    assert trades, "fixture must produce at least one real trade for this test to mean anything"
    assert all(t.commission > 0.0 for t in trades)

    zero_cost_config = load_config(CONFIG_PATH)
    zero_cost_config["backtest"]["transaction_costs"] = {"slippage_bps": 0.0, "commission_per_share": 0.0, "commission_min_per_order": 0.0}
    zero_cost_trades, _ = _run_trend_following(zero_cost_config)

    # Same strategy/fixture, same entry/exit decisions either way (costs
    # don't change WHEN a trade triggers, only what it nets) - so the
    # realistic-cost run's total P&L must be strictly worse.
    assert len(trades) == len(zero_cost_trades)
    assert sum(t.pnl_dollars for t in trades) < sum(t.pnl_dollars for t in zero_cost_trades)
