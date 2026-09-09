"""Unit tests for the Safe/Aggressive mean-reversion split and its hard gating rule:
Aggressive candidates must never reach Top Candidates or the trade journal unless
config.strategies.mean_reversion.aggressive_mode.enabled is explicitly true.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main, report_writer
from src.strategies import mean_reversion
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config():
    """A brand-new config dict each time, so tests can freely mutate it (e.g. flip
    aggressive_mode.enabled) without bleeding into other tests."""
    return load_config(CONFIG_PATH)


def make_snapshot(**overrides) -> dict:
    base = {
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "volume": 1_000_000.0,
        "sma_20": 100.0,
        "sma_50": 97.0,
        "sma_200": 90.0,
        "ema_50": 95.0,
        "ema_200": 92.0,
        "rsi_14": 50.0,
        "atr_14": 3.0,
        "bb_mid": 100.0,
        "bb_upper": 108.0,
        "bb_lower": 92.0,
        "bb_std": 4.0,
        "momentum_20d": -1.0,
        "momentum_60d": 1.0,
        "return_1m": -1.0,
        "relative_volume": 1.0,
        "daily_volatility_pct": 2.0,
        "rolling_high_20": 105.0,
    }
    base.update(overrides)
    return base


def make_dip_snapshot(**overrides) -> dict:
    """A ticker down hard: price well below its 20D MA (2.5 std devs) AND below its
    200D MA. Safe mode requires trend intact (price > SMA200) so it will NOT trigger
    here; Aggressive mode (deviation threshold 2.0, no trend-intact requirement)
    will. Also engineered so the resulting candidate clears every risk rule (RSI
    healthy, R:R >= 1.5) except the SMA200 one that mean reversion is exempt from."""
    base = make_snapshot(
        close=90.0,
        sma_20=100.0,
        sma_50=97.0,
        sma_200=100.0,
        ema_50=95.0,
        ema_200=98.0,  # ema_50 < ema_200 -> Trend Following also won't trigger
        rsi_14=50.0,
        atr_14=3.0,
        bb_std=4.0,  # deviation = (100-90)/4 = 2.5 std devs
        momentum_20d=-8.0,
        momentum_60d=-5.0,
        return_1m=-8.0,
    )
    base.update(overrides)
    return base


# --- strategies/mean_reversion.py: the two modes in isolation ---------------------


def test_safe_mode_does_not_trigger_when_trend_is_broken():
    config = fresh_config()
    snapshot = make_dip_snapshot()  # price (90) < SMA200 (100) -> trend broken
    result = mean_reversion.evaluate(snapshot, config, mode="safe")
    assert result["triggered"] is False
    assert result["candidate"] is None


def test_safe_mode_triggers_when_trend_is_intact():
    config = fresh_config()
    snapshot = make_dip_snapshot(sma_200=80.0)  # price (90) now above SMA200 (80)
    result = mean_reversion.evaluate(snapshot, config, mode="safe")
    assert result["triggered"] is True
    assert result["candidate"] is not None
    assert result["strategy"] == mean_reversion.STRATEGY_NAME_SAFE


def test_aggressive_mode_triggers_even_when_trend_is_broken():
    config = fresh_config()
    snapshot = make_dip_snapshot()  # price (90) < SMA200 (100) -> trend broken
    result = mean_reversion.evaluate(snapshot, config, mode="aggressive")
    assert result["triggered"] is True
    assert result["candidate"] is not None
    assert result["strategy"] == mean_reversion.STRATEGY_NAME_AGGRESSIVE


def test_aggressive_mode_requires_a_deeper_dip_than_safe():
    config = fresh_config()
    # Deviation of exactly 1.8 std devs: above Safe's 1.5 threshold, below
    # Aggressive's 2.0 threshold. Trend intact so Safe is free to trigger.
    snapshot = make_dip_snapshot(sma_200=80.0, bb_std=(100.0 - 90.0) / 1.8)
    safe_result = mean_reversion.evaluate(snapshot, config, mode="safe")
    aggressive_result = mean_reversion.evaluate(snapshot, config, mode="aggressive")
    assert safe_result["triggered"] is True
    assert aggressive_result["triggered"] is False


def test_unknown_mode_raises():
    config = fresh_config()
    try:
        mean_reversion.evaluate(make_snapshot(), config, mode="reckless")
        assert False, "expected a ValueError for an unrecognized mode"
    except ValueError:
        pass


# --- main.analyze_symbol wiring: aggressive_mode.enabled actually gates things ----


def test_aggressive_candidate_is_computed_but_never_becomes_best_risk_result_when_disabled():
    config = fresh_config()
    assert config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] is False

    snapshot = make_dip_snapshot()
    result = main.analyze_symbol("SPY", snapshot, None, snapshot, config)

    # It's computed and available for the watchlist...
    assert result["mean_reversion_aggressive_result"]["triggered"] is True
    assert result["aggressive_risk_result"] is not None
    assert result["aggressive_risk_result"]["tradeable"] is True  # would otherwise pass every rule

    # ...but it never becomes the ticker's actionable candidate while disabled.
    assert result["best_risk_result"] is None


def test_aggressive_candidate_becomes_best_risk_result_once_explicitly_enabled():
    config = fresh_config()
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = True

    snapshot = make_dip_snapshot()
    result = main.analyze_symbol("SPY", snapshot, None, snapshot, config)

    assert result["best_risk_result"] is not None
    assert result["best_risk_result"]["tradeable"] is True
    assert result["best_risk_result"]["strategy"] == mean_reversion.STRATEGY_NAME_AGGRESSIVE


# --- report_writer.py: defense-in-depth check at the Top Candidates/journal choke point ---


def _fake_aggressive_entry(tradeable: bool = True, label: str = "Watchlist") -> dict:
    """A hand-built ticker_result as if an Aggressive candidate had somehow reached
    best_risk_result - used to prove select_top_candidates refuses it independently
    of whatever main.py did, per the "must never... unless explicitly enabled" rule.
    """
    return {
        "symbol": "SPY",
        "score": 70,
        "label": label,
        "best_risk_result": {
            "strategy": mean_reversion.STRATEGY_NAME_AGGRESSIVE,
            "tradeable": tradeable,
            "blocked_reasons": [],
            "entry": 90.0,
            "stop_loss": 84.75,
            "target": 100.0,
            "risk_reward": 1.9,
            "expected_upside_pct": 11.11,
            "expected_downside_pct": 5.83,
            "shares": 10,
            "dollar_risk": 52.5,
        },
    }


def test_select_top_candidates_rejects_aggressive_entry_when_disabled():
    config = fresh_config()
    assert config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] is False
    top = report_writer.select_top_candidates([_fake_aggressive_entry()], config)
    assert top == []


def test_select_top_candidates_accepts_aggressive_entry_when_enabled():
    config = fresh_config()
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = True
    top = report_writer.select_top_candidates([_fake_aggressive_entry()], config)
    assert len(top) == 1
    assert top[0]["symbol"] == "SPY"


def test_journal_never_gets_an_aggressive_row_when_disabled(tmp_path):
    config = fresh_config()
    config["data"]["journal_dir"] = str(tmp_path)
    journal_path = report_writer.append_to_journal([_fake_aggressive_entry()], config, "2026-01-01")
    assert not journal_path.exists()  # nothing was ever written


def test_journal_gets_an_aggressive_row_once_enabled(tmp_path):
    config = fresh_config()
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = True
    config["data"]["journal_dir"] = str(tmp_path)
    journal_path = report_writer.append_to_journal([_fake_aggressive_entry()], config, "2026-01-01")
    assert journal_path.exists()


def test_high_risk_watchlist_lists_triggered_tickers_and_states_the_mode():
    config = fresh_config()
    snapshot = make_dip_snapshot()
    entry = main.analyze_symbol("SPY", snapshot, None, snapshot, config)
    text = report_writer.format_high_risk_dip_watchlist([entry], config)
    assert "SPY" in text
    assert "DISABLED" in text
    assert "Would-be entry" in text
