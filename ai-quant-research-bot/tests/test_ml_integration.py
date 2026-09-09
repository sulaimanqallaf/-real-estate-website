"""Integration tests for Phase 6's ML/Quant Agent wiring: main._compute_quant_
assessments against the real main.analyze_symbol() -> portfolio_risk pipeline
-> report_writer chain, proving the daily run degrades cleanly with no
registered model, works correctly with one loaded, and never changes
approval safety (Part Y "Integration")."""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main, market_regime, paper_trades, portfolio_risk, quant_agent, report_writer, telegram_bot
from src.ml import model_registry, models, trainer
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
LOGGER = logging.getLogger("test_ml_integration")

BULL_TREND_REGIME = market_regime.MarketRegime(
    primary=market_regime.BULL_TREND, trend="bullish", volatility="normal", risk_state="risk_on",
    confidence=1.0, explanation="test fixture",
)


def fresh_config(tmp_path):
    config = load_config(CONFIG_PATH)
    config["data"]["journal_dir"] = str(tmp_path)
    config["risk"]["account_equity"] = 10_000.0
    return config


def bullish_snapshot(price=100.0):
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


def make_dip_snapshot(price=90.0):
    return {
        "close": price, "high": price * 1.01, "low": price * 0.99, "volume": 1_500_000.0,
        "sma_20": 100.0, "sma_50": 88.0, "sma_200": 95.0, "ema_50": 90.0, "ema_200": 92.0,
        "rsi_14": 55.0, "atr_14": 3.0, "bb_mid": 100.0, "bb_upper": 108.0, "bb_lower": 92.0, "bb_std": 4.0,
        "momentum_20d": 2.0, "momentum_60d": 3.0, "return_1m": -3.0, "relative_volume": 1.5,
        "daily_volatility_pct": 2.0, "rolling_high_20": 95.0,
    }


def make_open_trades_df():
    return pd.DataFrame([], columns=paper_trades.PAPER_TRADE_COLUMNS)


def synthetic_price_df(n=300, seed=9):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    price = 100 + np.cumsum(rng.normal(0, 1.0, n))
    df = pd.DataFrame({"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": rng.integers(1_000_000, 5_000_000, n).astype(float)}, index=dates)
    df.index.name = "date"
    return df


def run_full_pipeline(entry, config, regime=BULL_TREND_REGIME):
    results = [entry]
    portfolio_risk.run_regime_and_portfolio_pipeline(results, regime, make_open_trades_df(), {}, config, LOGGER)
    return results


# --- daily run works with no model registered -------------------------------------------


def test_daily_run_completes_with_no_registered_model(tmp_path):
    config = fresh_config(tmp_path)
    config["ml"]["registry_dir"] = str(tmp_path / "models")

    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    results = run_full_pipeline(entry, config)
    price_data = {"AMD": synthetic_price_df()}

    assessments = main._compute_quant_assessments(results, price_data, BULL_TREND_REGIME, config, LOGGER)
    quant_agent.apply_quant_agent_filtering(results, assessments, config)

    assert entry["quant_assessment"] is not None
    assert entry["quant_assessment"].ml_confidence == "UNAVAILABLE"
    assert entry["portfolio_evaluation"]["decision"] in ("ACCEPT", "ACCEPT_WITH_REDUCED_SIZE")

    top = report_writer.select_top_candidates(results, config)
    assert len(top) == 1  # ML unavailability never blocks an otherwise-eligible candidate


def test_daily_run_ml_disabled_skips_assessment_entirely(tmp_path):
    config = fresh_config(tmp_path)
    config["ml"]["enabled"] = False

    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    results = run_full_pipeline(entry, config)
    top = report_writer.select_top_candidates(results, config)
    assert len(top) == 1
    assert entry.get("quant_assessment") is None


# --- daily run works with a loaded (registered) model -----------------------------------


def test_daily_run_with_registered_champion_produces_ml_context(tmp_path):
    config = fresh_config(tmp_path)
    registry_dir = tmp_path / "models"
    config["ml"]["registry_dir"] = str(registry_dir)
    registry = model_registry.ModelRegistry(registry_dir)

    price_df = synthetic_price_df(n=1500, seed=3)
    from src import dataset_builder as db

    dataset = db.build_dataset_rows("AMD", price_df, config, min_history_bars=200)
    for model_type in models.ALL_MODEL_TYPES:
        trainer.train_challenger_and_maybe_promote(dataset, config["ml"]["primary_horizon"], models.TASK_CLASSIFICATION, model_type, registry)

    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    results = run_full_pipeline(entry, config)
    price_data = {"AMD": price_df}

    assessments = main._compute_quant_assessments(results, price_data, BULL_TREND_REGIME, config, LOGGER)
    quant_agent.apply_quant_agent_filtering(results, assessments, config)

    assert entry["quant_assessment"].ml_confidence != "UNAVAILABLE"
    assert entry["portfolio_evaluation"]["decision"] in ("ACCEPT", "ACCEPT_WITH_REDUCED_SIZE")  # gate outcome untouched

    text = report_writer.format_report_text(results, "2026-01-05", [], config, BULL_TREND_REGIME)
    assert "Quant / ML Intelligence" in text
    block = report_writer.format_candidate_block(entry)
    assert "ML:" in block


# --- report shows ML context / High Risk Dip Watchlist stays informational --------------


def test_report_includes_quant_ml_section(tmp_path):
    config = fresh_config(tmp_path)
    config["ml"]["registry_dir"] = str(tmp_path / "models")
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    results = run_full_pipeline(entry, config)
    price_data = {"AMD": synthetic_price_df()}
    assessments = main._compute_quant_assessments(results, price_data, BULL_TREND_REGIME, config, LOGGER)
    quant_agent.apply_quant_agent_filtering(results, assessments, config)

    text = report_writer.format_report_text(results, "2026-01-05", [], config, BULL_TREND_REGIME)
    assert "Quant / ML Intelligence" in text
    assert "advisory" in text.lower() or "Advisory" in text


def test_high_risk_dip_watchlist_remains_informational_with_ml_enabled(tmp_path):
    config = fresh_config(tmp_path)
    config["ml"]["registry_dir"] = str(tmp_path / "models")
    assert config["strategies"]["mean_reversion"]["aggressive_mode"]["enabled"] is False

    dip_snapshot = make_dip_snapshot()
    entry = main.analyze_symbol("SPY", dip_snapshot, None, dip_snapshot, config)
    assert entry["best_risk_result"] is None  # never promoted while aggressive_mode is disabled

    results = run_full_pipeline(entry, config)
    price_data = {"SPY": synthetic_price_df()}
    assessments = main._compute_quant_assessments(results, price_data, BULL_TREND_REGIME, config, LOGGER)
    quant_agent.apply_quant_agent_filtering(results, assessments, config)

    top = report_writer.select_top_candidates(results, config)
    assert top == []
    watchlist_text = report_writer.format_high_risk_dip_watchlist(results, config)
    assert "SPY" in watchlist_text
    assert "DISABLED" in watchlist_text


# --- Telegram approval flow remains safe -------------------------------------------------


def test_telegram_approval_flow_unaffected_by_ml_context(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    config["ml"]["registry_dir"] = str(tmp_path / "models")
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    results = run_full_pipeline(entry, config)
    price_data = {"AMD": synthetic_price_df()}
    assessments = main._compute_quant_assessments(results, price_data, BULL_TREND_REGIME, config, LOGGER)
    quant_agent.apply_quant_agent_filtering(results, assessments, config)

    sent_texts = []

    def fake_send(token, chat_id, text, keyboard, logger):
        sent_texts.append(text)
        return 999

    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", fake_send)
    monkeypatch.setattr(paper_trades, "save_pending_approval", lambda *a, **k: None)
    main._send_paper_trade_approvals(results, "2026-01-05", "TOKEN", "123", config, LOGGER)

    assert len(sent_texts) == 1
    assert "AMD" in sent_texts[0]


def test_avoid_candidate_gets_no_approval_even_with_ml_enabled(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    config["ml"]["registry_dir"] = str(tmp_path / "models")

    def make_avoid_snapshot(price=50.0):
        return {
            "close": price, "high": price * 1.005, "low": price * 0.995, "volume": 500_000.0,
            "sma_20": price * 1.10, "sma_50": price * 1.15, "sma_200": price * 1.25,
            "ema_50": price * 1.12, "ema_200": price * 1.20, "rsi_14": 30.0, "atr_14": price * 0.03,
            "bb_mid": price * 1.10, "bb_upper": price * 1.15, "bb_lower": price * 1.05, "bb_std": price * 0.03,
            "momentum_20d": -10.0, "momentum_60d": -15.0, "return_1m": -12.0, "relative_volume": 0.5,
            "daily_volatility_pct": 3.0, "rolling_high_20": price * 1.20,
        }

    entry = main.analyze_symbol("AMD", make_avoid_snapshot(), None, make_avoid_snapshot(), config)
    assert entry["label"] == "Avoid"
    results = run_full_pipeline(entry, config)
    price_data = {"AMD": synthetic_price_df()}
    assessments = main._compute_quant_assessments(results, price_data, BULL_TREND_REGIME, config, LOGGER)
    quant_agent.apply_quant_agent_filtering(results, assessments, config)

    sent = []
    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", lambda *a, **k: (sent.append(1), 999)[1])
    main._send_paper_trade_approvals(results, "2026-01-05", "TOKEN", "123", config, LOGGER)
    assert sent == []
