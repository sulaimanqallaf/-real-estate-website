"""Integration tests for Phase 5's Big Money wiring: main._compute_big_money_scores /
main._fetch_macro_snapshot_safely against the real main.analyze_symbol() ->
portfolio_risk pipeline -> report_writer chain, proving the new context layer
never changes approval safety and degrades cleanly with every external
provider unavailable.
"""

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import big_money, main, market_regime, paper_trades, portfolio_risk, report_writer, telegram_bot
from src.data_providers import base as provider_base, options_flow_provider
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
LOGGER = logging.getLogger("test_big_money_integration")

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


def make_avoid_snapshot(price=50.0):
    """Deeply broken trend/momentum on every axis - lands on Avoid regardless
    of how strong any institutional context might be."""
    return {
        "close": price, "high": price * 1.005, "low": price * 0.995, "volume": 500_000.0,
        "sma_20": price * 1.10, "sma_50": price * 1.15, "sma_200": price * 1.25,
        "ema_50": price * 1.12, "ema_200": price * 1.20,
        "rsi_14": 30.0, "atr_14": price * 0.03,
        "bb_mid": price * 1.10, "bb_upper": price * 1.15, "bb_lower": price * 1.05, "bb_std": price * 0.03,
        "momentum_20d": -10.0, "momentum_60d": -15.0,
        "return_1m": -12.0, "relative_volume": 0.5, "daily_volatility_pct": 3.0,
        "rolling_high_20": price * 1.20,
    }


def make_open_trades_df(trades):
    return pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)


def run_full_pipeline(ticker_results, config, regime=BULL_TREND_REGIME):
    portfolio_risk.run_regime_and_portfolio_pipeline(ticker_results, regime, make_open_trades_df([]), {}, config, LOGGER)


# --- daily run works with every external provider unavailable ---------------------


def test_daily_pipeline_completes_with_every_big_money_provider_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    config = fresh_config(tmp_path)
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    ticker_results = [entry]
    run_full_pipeline(ticker_results, config)

    scores = main._compute_big_money_scores(ticker_results, config, LOGGER)
    big_money.apply_big_money_ranking_filter(ticker_results, scores, config)

    assert entry["big_money_score"] is not None
    # relative_volume is computed locally (not an external provider), so it's
    # still present even with SEC/FRED fully unconfigured - see the next test.
    assert entry["big_money_score"].components[big_money.COMPONENT_INSTITUTIONAL] is None
    assert entry["big_money_score"].components[big_money.COMPONENT_OPTIONS_FLOW] is None

    macro = main._fetch_macro_snapshot_safely(config, LOGGER)
    assert macro is None  # FRED not configured - clean None, not a crash, not a fabricated number

    # The pipeline must still complete and still produce a Top Candidate.
    top = report_writer.select_top_candidates(ticker_results, config)
    assert len(top) == 1


def test_relative_volume_component_is_the_one_reliably_available_component(tmp_path):
    """Documented in README: with no SEC/FRED/options-flow credentials
    configured, relative_volume (already computed from existing indicators)
    is the one Big Money component that's reliably available - not a bug,
    an honest reflection of what's actually wired up without credentials."""
    config = fresh_config(tmp_path)
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    scores = main._compute_big_money_scores([entry], config, LOGGER)
    score = scores["AMD"]
    assert score.components[big_money.COMPONENT_RELATIVE_VOLUME] is not None
    assert score.components[big_money.COMPONENT_INSTITUTIONAL] is None
    assert score.components[big_money.COMPONENT_INSIDER] is None
    assert score.has_any_data is True  # relative volume alone is still "some" data


# --- daily run works with synthetic SEC/insider data -------------------------------


def test_synthetic_institutional_and_insider_data_flows_into_composite_score(tmp_path):
    config = fresh_config(tmp_path)
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    facts = {"has_data": True, "new_positions": 3, "increased_positions": 2, "reduced_positions": 0, "exited_positions": 0}
    insider_features = {"has_data": True, "insider_buy_value_30d": 200_000, "insider_sell_value_30d": 0, "cluster_buying": True}

    score = big_money.compute_big_money_score(
        "AMD", institutional_facts=facts, insider_features=insider_features,
        relative_volume=entry["snapshot"]["relative_volume"],
    )
    ticker_results = [entry]
    run_full_pipeline(ticker_results, config)
    big_money.apply_big_money_ranking_filter(ticker_results, {"AMD": score}, config)

    assert entry["big_money_score"].composite_score is not None
    assert entry["big_money_score"].composite_score > 0

    # Still governed entirely by the existing gates - a strong institutional
    # score changes nothing about the approval decision itself.
    top = report_writer.select_top_candidates(ticker_results, config)
    assert len(top) == 1
    assert top[0]["portfolio_evaluation"]["decision"] in ("ACCEPT", "ACCEPT_WITH_REDUCED_SIZE")


# --- Avoid candidate with strong institutional context remains Avoid --------------


def test_avoid_candidate_with_strong_institutional_context_still_gets_no_approval(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    entry = main.analyze_symbol("AMD", make_avoid_snapshot(), None, make_avoid_snapshot(), config)
    assert entry["label"] == "Avoid"

    facts = {"has_data": True, "new_positions": 10, "increased_positions": 5, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("AMD", institutional_facts=facts)
    assert score.composite_score == 1.0  # about as strong as it gets

    ticker_results = [entry]
    run_full_pipeline(ticker_results, config)
    config["big_money"]["use_for_ranking"] = True
    big_money.apply_big_money_ranking_filter(ticker_results, {"AMD": score}, config)

    assert entry["label"] == "Avoid"
    top = report_writer.select_top_candidates(ticker_results, config)
    assert top == []

    sent = []
    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", lambda *a, **k: (sent.append(1), 999)[1])
    main._send_paper_trade_approvals(ticker_results, "2026-01-05", "TOKEN", "123", config, LOGGER)
    assert sent == []


# --- valid Top Candidate gets institutional context in the report -----------------


def test_valid_top_candidate_gets_context_in_report_without_changing_approval(tmp_path, monkeypatch):
    config = fresh_config(tmp_path)
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    ticker_results = [entry]
    run_full_pipeline(ticker_results, config)

    facts = {"has_data": True, "new_positions": 4, "increased_positions": 1, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("AMD", institutional_facts=facts, relative_volume=entry["snapshot"]["relative_volume"])
    big_money.apply_big_money_ranking_filter(ticker_results, {"AMD": score}, config)

    top = report_writer.select_top_candidates(ticker_results, config)
    assert len(top) == 1

    block = report_writer.format_candidate_block(top[0])
    assert "Institutional context" in block
    assert "Big Money composite" in block

    section = report_writer.format_big_money_section(ticker_results, config)
    assert "Big Money" in section

    # Approval flow is completely unaffected by the presence of this context.
    sent_texts = []
    monkeypatch.setattr(telegram_bot, "send_message_with_keyboard", lambda *a, **k: (sent_texts.append(1), 999)[1])
    monkeypatch.setattr(paper_trades, "save_pending_approval", lambda *a, **k: None)
    main._send_paper_trade_approvals(ticker_results, "2026-01-05", "TOKEN", "123", config, LOGGER)
    assert len(sent_texts) == 1


def test_report_text_includes_big_money_section_and_existing_sections_still_render(tmp_path):
    config = fresh_config(tmp_path)
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    ticker_results = [entry]
    run_full_pipeline(ticker_results, config)
    scores = main._compute_big_money_scores(ticker_results, config, LOGGER)
    big_money.apply_big_money_ranking_filter(ticker_results, scores, config)

    text = report_writer.format_report_text(ticker_results, "2026-01-05", [], config, BULL_TREND_REGIME)
    assert "Big Money" in text
    assert "Top Candidates" in text
    assert "Portfolio Risk" in text
    assert "No real trades are placed" in text or "No trades" in text or "no trades" in text.lower()


# --- options-flow provider unavailable never crashes the scoring path -------------


def test_options_flow_unavailable_does_not_crash_big_money_scoring(tmp_path):
    config = fresh_config(tmp_path)
    config["providers"]["options_flow"]["enabled"] = False
    entry = main.analyze_symbol("AMD", bullish_snapshot(), None, bullish_snapshot(), config)
    scores = main._compute_big_money_scores([entry], config, LOGGER)
    assert scores["AMD"].components[big_money.COMPONENT_OPTIONS_FLOW] is None
