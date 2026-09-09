"""Integration tests for the Phase 4 pipeline:

    Individual Risk Manager -> Market Regime Filter -> Portfolio Risk Manager
    -> Top Candidate -> Telegram Approval

Exercises the real main.analyze_symbol() -> portfolio_risk.
run_regime_and_portfolio_pipeline() -> report_writer.select_top_candidates() ->
main._send_paper_trade_approvals() chain, not just each module in isolation.
"""

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main, market_regime, paper_trades, portfolio_risk, report_writer, telegram_bot
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
LOGGER = logging.getLogger("test_regime_portfolio_integration")


def fresh_config(tmp_path):
    config = load_config(CONFIG_PATH)
    config["data"]["journal_dir"] = str(tmp_path)
    config["risk"]["account_equity"] = 10_000.0
    return config


BULL_TREND_REGIME = market_regime.MarketRegime(
    primary=market_regime.BULL_TREND, trend="bullish", volatility="normal", risk_state="risk_on",
    confidence=1.0, explanation="test fixture",
)
RISK_OFF_REGIME = market_regime.MarketRegime(
    primary=market_regime.RISK_OFF, trend="bearish", volatility="elevated", risk_state="risk_off",
    confidence=1.0, explanation="test fixture",
)
HIGH_VOLATILITY_REGIME = market_regime.MarketRegime(
    primary=market_regime.HIGH_VOLATILITY, trend="mixed", volatility="elevated", risk_state="risk_off",
    confidence=1.0, explanation="test fixture",
)


def make_dip_snapshot(price=90.0):
    """Triggers ONLY Aggressive Mean Reversion (deviation 2.5 std exceeds its 2.0
    threshold; trend is broken via ema_50<ema_200, so neither Safe mean reversion
    nor Trend Following trigger) while still clearing enough of the universal
    scoring checklist (above_sma_50, healthy RSI, positive momentum, volume,
    risk/reward bonus = score 55, "Weak watchlist") to pass the non-Avoid gate -
    unlike test_mean_reversion_modes.py's harsher fixture (score 15, Avoid),
    which would be rejected before regime even runs."""
    return {
        "close": price, "high": price * 1.01, "low": price * 0.99, "volume": 1_500_000.0,
        "sma_20": 100.0, "sma_50": 88.0, "sma_200": 95.0,
        "ema_50": 90.0, "ema_200": 92.0,
        "rsi_14": 55.0, "atr_14": 3.0,
        "bb_mid": 100.0, "bb_upper": 108.0, "bb_lower": 92.0, "bb_std": 4.0,
        "momentum_20d": 2.0, "momentum_60d": 3.0,
        "return_1m": -3.0, "relative_volume": 1.5, "daily_volatility_pct": 2.0,
        "rolling_high_20": 95.0,
    }


def make_bullish_snapshot(price=100.0):
    """Engineered to satisfy Trend Following and Momentum Breakout (assigned to
    AMD/NVDA/SMH), matching the pattern used in test_strategy_assignment.py."""
    return {
        "close": price, "high": price * 1.01, "low": price * 0.99, "volume": 3_000_000.0,
        "sma_20": price * 0.95, "sma_50": price * 0.93, "sma_200": price * 0.85,
        "ema_50": price * 0.94, "ema_200": price * 0.88,
        "rsi_14": 60.0, "atr_14": price * 0.02,
        "bb_mid": price * 0.95, "bb_upper": price * 1.0, "bb_lower": price * 0.9, "bb_std": price * 0.025,
        "momentum_20d": 5.0, "momentum_60d": 8.0,
        "return_1m": 5.0, "relative_volume": 2.0, "daily_volatility_pct": 1.5,
        "rolling_high_20": price * 0.98,
    }


def make_ticker_result(symbol, config, price=100.0):
    snapshot = make_bullish_snapshot(price)
    return main.analyze_symbol(symbol, snapshot, None, snapshot, config)


def make_open_trade(ticker, entry_price=100.0, stop_loss=99.0, position_size=10):
    return {
        "trade_id": f"{ticker}_x", "approved_at": "x", "ticker": ticker, "strategy": "Trend Following",
        "mode": "N/A", "signal": "Strong candidate", "score": 85, "entry_price": entry_price,
        "stop_loss": stop_loss, "target_price": entry_price * 1.2, "risk_reward": 2.0,
        "position_size": position_size, "risk_amount": (entry_price - stop_loss) * position_size,
        "status": "OPEN", "opened_at": "2026-01-01", "exit_price": "", "exited_at": "", "exit_reason": "",
        "pnl_dollars": "", "pnl_pct": "", "holding_days": "", "notes": "",
    }


def make_open_trades_df(trades):
    return pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)


# --- candidate passes individual risk but fails portfolio risk -------------------


def test_candidate_passing_individual_risk_but_failing_portfolio_risk_gets_no_approval(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    config["portfolio_risk"]["max_open_positions"] = 1

    open_trades_df = make_open_trades_df([make_open_trade("NVDA")])  # already at the cap
    entry = make_ticker_result("AMD", config)
    ticker_results = [entry]

    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, BULL_TREND_REGIME, open_trades_df, {}, config, LOGGER)

    assert entry["portfolio_evaluation"]["decision"] == "REJECT"
    assert "max open positions" in entry["portfolio_evaluation"]["rejection_reasons"][0]

    top = report_writer.select_top_candidates(ticker_results, config)
    assert top == []

    sent = []
    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", lambda *a, **k: (sent.append(1), 999)[1])
    main._send_paper_trade_approvals(ticker_results, "2026-01-05", "TOKEN", "123", config, LOGGER)
    assert sent == []


# --- candidate passes portfolio risk but fails regime -----------------------------


def test_candidate_passing_portfolio_risk_but_blocked_by_regime_gets_no_approval(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] = True

    dip_snapshot = make_dip_snapshot()
    entry = main.analyze_symbol("SPY", dip_snapshot, None, dip_snapshot, config)
    assert entry["best_risk_result"]["strategy"] == "Mean Reversion (Aggressive)"
    ticker_results = [entry]

    # Aggressive Mean Reversion is hard-"blocked" in HIGH_VOLATILITY with no
    # aggressive_mode.enabled exception (that exception is BEAR_TREND-only) -
    # portfolio risk (empty portfolio, would easily accept) never even gets a
    # chance to run.
    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, HIGH_VOLATILITY_REGIME, make_open_trades_df([]), {}, config, LOGGER)

    assert entry["regime_evaluation"]["blocked"] is True
    assert entry["portfolio_evaluation"]["decision"] == "REJECT"
    assert "regime" in entry["portfolio_evaluation"]["rejection_reasons"][0].lower()

    top = report_writer.select_top_candidates(ticker_results, config)
    assert top == []

    sent = []
    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", lambda *a, **k: (sent.append(1), 999)[1])
    main._send_paper_trade_approvals(ticker_results, "2026-01-05", "TOKEN", "123", config, LOGGER)
    assert sent == []


# --- valid candidate passes everything --------------------------------------------


def test_valid_candidate_passes_everything_and_gets_an_approval_message(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    entry = make_ticker_result("AMD", config)
    ticker_results = [entry]

    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, BULL_TREND_REGIME, make_open_trades_df([]), {}, config, LOGGER)

    assert entry["portfolio_evaluation"]["decision"] in ("ACCEPT", "ACCEPT_WITH_REDUCED_SIZE")

    top = report_writer.select_top_candidates(ticker_results, config)
    assert len(top) == 1
    assert top[0]["symbol"] == "AMD"

    sent_texts = []

    def fake_send(token, chat_id, text, keyboard, logger):
        sent_texts.append(text)
        return 999

    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", fake_send)
    monkeypatch.setattr(paper_trades, "save_pending_approval", lambda *a, **k: None)
    main._send_paper_trade_approvals(ticker_results, "2026-01-05", "TOKEN", "123", config, LOGGER)

    assert len(sent_texts) == 1
    assert "AMD" in sent_texts[0]


# --- sizing chain: individual -> regime reduction -> portfolio reduction ----------


def test_sizing_chain_individual_then_regime_then_portfolio():
    config_path = CONFIG_PATH

    config = load_config(config_path)
    config["risk"]["account_equity"] = 10_000.0
    entry = make_ticker_result("AMD", config)
    individual_shares = entry["best_risk_result"]["shares"]
    assert individual_shares > 0

    ticker_results = [entry]
    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, RISK_OFF_REGIME, make_open_trades_df([]), {}, config, LOGGER)

    regime_eval = entry["regime_evaluation"]
    portfolio_eval = entry["portfolio_evaluation"]

    # If not outright blocked/rejected, the chain must be monotonically non-increasing:
    # individual shares >= regime-adjusted shares >= final portfolio-adjusted shares.
    if portfolio_eval["decision"] != "REJECT":
        import math

        regime_shares = math.floor(individual_shares * regime_eval["combined_multiplier"])
        final_shares = portfolio_eval["position"]["shares"]
        assert individual_shares >= regime_shares >= final_shares


def test_no_pipeline_stage_ever_increases_size():
    config = load_config(CONFIG_PATH)
    config["risk"]["account_equity"] = 10_000.0

    for regime in (BULL_TREND_REGIME, RISK_OFF_REGIME):
        entry = make_ticker_result("AMD", config)
        individual_shares = entry["best_risk_result"]["shares"]
        ticker_results = [entry]
        portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, regime, make_open_trades_df([]), {}, config, LOGGER)

        portfolio_eval = entry["portfolio_evaluation"]
        if portfolio_eval["decision"] != "REJECT":
            assert portfolio_eval["position"]["shares"] <= individual_shares


# --- a regime-reduced (but not rejected) candidate must still surface --------------


def test_reduced_candidate_still_reaches_top_candidates_with_reduced_size():
    """AMD/Trend Following scores 90 (clears RISK_OFF's score_thresholds bar of
    80) and is only "restricted" (not blocked) in RISK_OFF, so it must still
    reach Top Candidates - just at combined_multiplier 0.25*0.5=0.125, i.e. a
    real, visible size cut rather than a silent rejection."""
    config = load_config(CONFIG_PATH)
    config["risk"]["account_equity"] = 10_000.0
    entry = make_ticker_result("AMD", config)
    individual_shares = entry["best_risk_result"]["shares"]

    ticker_results = [entry]
    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, RISK_OFF_REGIME, make_open_trades_df([]), {}, config, LOGGER)

    assert entry["regime_evaluation"]["blocked"] is False
    assert entry["regime_evaluation"]["combined_multiplier"] < 1.0
    assert entry["portfolio_evaluation"]["decision"] != "REJECT"

    final_shares = entry["portfolio_evaluation"]["position"]["shares"]
    assert 0 < final_shares < individual_shares

    top = report_writer.select_top_candidates(ticker_results, config)
    assert len(top) == 1
    assert top[0]["symbol"] == "AMD"

    text = report_writer.format_candidate_block(entry)
    assert f"Suggested size: {final_shares} shares" in text
    assert "Regime-adjusted size" in text


# --- the regime/portfolio pipeline can only reject, never promote an Avoid ---------


def test_regime_pipeline_cannot_promote_an_avoid_labeled_candidate_into_top_candidates():
    """Even under the most favorable regime, an Avoid-labeled ticker must never
    become a candidate for the regime/portfolio pipeline (it fails
    risk_manager.passes_universal_gates before the pipeline even looks at it),
    and must never reach Top Candidates - regime logic may only add stricter
    gates on top of the existing label gate, never bypass it."""
    entry = {
        "symbol": "SPY",
        "score": 10,
        "label": "Avoid",
        "best_risk_result": {
            "strategy": "Trend Following",
            "tradeable": True,
            "blocked_reasons": [],
            "entry": 90.0,
            "stop_loss": 84.0,
            "target": 105.0,
            "risk_reward": 2.5,
            "expected_upside_pct": 16.67,
            "expected_downside_pct": 6.67,
            "shares": 20,
            "dollar_risk": 120.0,
        },
    }
    config = load_config(CONFIG_PATH)
    config["risk"]["account_equity"] = 10_000.0
    ticker_results = [entry]

    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, BULL_TREND_REGIME, make_open_trades_df([]), {}, config, LOGGER)

    # Never even entered the pipeline as a candidate.
    assert entry["regime_evaluation"] is None
    assert entry["portfolio_evaluation"] is None

    top = report_writer.select_top_candidates(ticker_results, config)
    assert top == []


# --- aggressive-mode gating is unchanged once the regime/portfolio pipeline runs ---


def test_aggressive_disabled_behavior_unchanged_through_regime_pipeline():
    """An Aggressive Mean Reversion candidate with aggressive_mode disabled never
    gets a best_risk_result at all (main.analyze_symbol's own gate, unchanged
    since Phase 3) - so it never enters the regime/portfolio pipeline as a
    candidate either, exactly like before this phase existed."""
    config = load_config(CONFIG_PATH)
    assert config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] is False

    dip_snapshot = make_dip_snapshot()
    entry = main.analyze_symbol("SPY", dip_snapshot, None, dip_snapshot, config)
    assert entry["mean_reversion_aggressive_result"]["triggered"] is True
    assert entry["aggressive_risk_result"] is not None  # informational watchlist data is still computed
    assert entry["best_risk_result"] is None  # but never promoted to an actionable candidate

    ticker_results = [entry]
    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, BULL_TREND_REGIME, make_open_trades_df([]), {}, config, LOGGER)

    assert entry["regime_evaluation"] is None
    assert entry["portfolio_evaluation"] is None

    top = report_writer.select_top_candidates(ticker_results, config)
    assert top == []

    # The High Risk Dip Watchlist stays purely informational: the pipeline must
    # not have mutated the underlying aggressive_risk_result it reads from.
    watchlist_text = report_writer.format_high_risk_dip_watchlist(ticker_results, config)
    assert "SPY" in watchlist_text
    assert "DISABLED" in watchlist_text
