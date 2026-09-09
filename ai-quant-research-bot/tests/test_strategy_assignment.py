"""Confirm every ticker in the universe is assigned to (and can trigger) a strategy.

These tests exist because a prior version left AAPL/MSFT/META/TSLA/GOOGL/AMZN/USO/GLD
without any strategy at all, so they could never produce a trade candidate. The
strategy_universe mapping was corrected so every configured ticker has at least one
strategy - these tests pin that mapping down and exercise the real wiring through
main.analyze_symbol, not just the config file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main
from src.utils import load_config

CONFIG = load_config(Path(__file__).resolve().parent.parent / "config" / "settings.yaml")

EXPECTED_STRATEGIES = {
    "SPY": {"mean_reversion", "trend_following"},
    "QQQ": {"mean_reversion", "trend_following"},
    "VOO": {"trend_following", "momentum_breakout"},
    "VGT": {"trend_following", "momentum_breakout"},
    "SMH": {"trend_following", "momentum_breakout"},
    "AAPL": {"momentum_breakout", "trend_following"},
    "MSFT": {"momentum_breakout", "trend_following"},
    "NVDA": {"momentum_breakout", "trend_following"},
    "AMD": {"momentum_breakout", "trend_following"},
    "META": {"momentum_breakout", "trend_following"},
    "TSLA": {"momentum_breakout", "trend_following"},
    "GOOGL": {"momentum_breakout", "trend_following"},
    "AMZN": {"momentum_breakout", "trend_following"},
    "GLD": {"trend_following"},
    "USO": {"trend_following"},
}


def make_bullish_snapshot(price: float = 100.0) -> dict:
    """Engineered to satisfy Trend Following (assigned to every ticker) and
    Momentum Breakout simultaneously: EMA50 > EMA200, price > SMA200, and a fresh
    20D-high breakout on 2x average volume."""
    return {
        "close": price,
        "high": price * 1.01,
        "low": price * 0.99,
        "volume": 3_000_000.0,
        "sma_20": price * 0.95,
        "sma_50": price * 0.93,
        "sma_200": price * 0.85,
        "ema_50": price * 0.94,
        "ema_200": price * 0.88,
        "rsi_14": 60.0,
        "atr_14": price * 0.02,
        "bb_mid": price * 0.95,
        "bb_upper": price * 1.0,
        "bb_lower": price * 0.9,
        "bb_std": price * 0.025,
        "momentum_20d": 5.0,
        "momentum_60d": 8.0,
        "return_1m": 5.0,
        "relative_volume": 2.0,
        "daily_volatility_pct": 1.5,
        "rolling_high_20": price * 0.98,
    }


def test_every_configured_ticker_has_the_expected_strategy_mapping():
    universe = CONFIG["strategy_universe"]
    for ticker in CONFIG["tickers"]:
        actual = {name for name, tickers in universe.items() if ticker in tickers}
        assert actual == EXPECTED_STRATEGIES[ticker], f"{ticker}: expected {EXPECTED_STRATEGIES[ticker]}, got {actual}"


def test_every_configured_ticker_is_assigned_to_at_least_one_strategy():
    universe = CONFIG["strategy_universe"]
    assigned = set(universe["mean_reversion"]) | set(universe["momentum_breakout"]) | set(universe["trend_following"])
    for ticker in CONFIG["tickers"]:
        assert ticker in assigned, f"{ticker} is not assigned to any strategy"


def test_every_ticker_can_be_analyzed_without_error():
    snapshot = make_bullish_snapshot()
    for ticker in CONFIG["tickers"]:
        result = main.analyze_symbol(ticker, snapshot, None, snapshot, CONFIG)
        assert result["symbol"] == ticker
        assert 0 <= result["score"] <= 100


def test_every_ticker_has_at_least_one_strategy_module_actually_invoked():
    snapshot = make_bullish_snapshot()
    for ticker in CONFIG["tickers"]:
        result = main.analyze_symbol(ticker, snapshot, None, snapshot, CONFIG)
        invoked = [result["trend_result"], result["breakout_result"], result["mean_reversion_safe_result"]]
        assert any(r is not None for r in invoked), f"{ticker} had no strategy module invoked at all"


def test_every_ticker_produces_a_tradeable_candidate_on_a_favorable_snapshot():
    """On a favorable enough snapshot, every ticker's assigned strategy/strategies
    should actually trigger a risk-manager-approved candidate - not just be wired up
    but inert. Trend Following alone (assigned to all 15 tickers) should be enough."""
    snapshot = make_bullish_snapshot()
    for ticker in CONFIG["tickers"]:
        result = main.analyze_symbol(ticker, snapshot, None, snapshot, CONFIG)
        risk = result["best_risk_result"]
        assert risk is not None, f"{ticker} produced no candidate at all on a favorable snapshot"
        assert risk["tradeable"] is True, f"{ticker} candidate was blocked: {risk['blocked_reasons']}"
        assert risk["risk_reward"] >= CONFIG["risk"]["min_risk_reward_ratio"]
