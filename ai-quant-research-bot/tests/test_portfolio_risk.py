"""Unit tests for portfolio_risk.py: portfolio state derivation (OPEN-only),
resizing rules that can only shrink or reject, sector concentration, overlap
groups, and correlation-based caps.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import paper_trades, portfolio_risk
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def fresh_config(**overrides):
    config = load_config(CONFIG_PATH)
    config["risk"]["account_equity"] = 10_000.0
    for key, value in overrides.items():
        config["portfolio_risk"][key] = value
    return config


def make_open_trade(ticker="NVDA", strategy="Trend Following", mode="N/A", entry_price=100.0, stop_loss=90.0,
                     position_size=10, status="OPEN"):
    return {
        "trade_id": f"{ticker}_x", "approved_at": "x", "ticker": ticker, "strategy": strategy, "mode": mode,
        "signal": "Strong candidate", "score": 85, "entry_price": entry_price, "stop_loss": stop_loss,
        "target_price": entry_price * 1.2, "risk_reward": 2.0, "position_size": position_size,
        "risk_amount": (entry_price - stop_loss) * position_size, "status": status, "opened_at": "2026-01-01",
        "exit_price": "", "exited_at": "", "exit_reason": "", "pnl_dollars": "", "pnl_pct": "", "holding_days": "",
        "notes": "",
    }


def make_open_trades_df(trades):
    return pd.DataFrame(trades, columns=paper_trades.PAPER_TRADE_COLUMNS)


def make_proposal(ticker="AMD", entry=100.0, stop_loss=90.0, target=120.0, shares=20):
    return {
        "ticker": ticker, "strategy": "Momentum Breakout", "entry": entry, "stop_loss": stop_loss,
        "target": target, "shares": shares, "dollar_risk": (entry - stop_loss) * shares,
        "position_value": entry * shares, "risk_reward": (target - entry) / (entry - stop_loss),
    }


def make_price_df(seed, n=90):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-06-01", periods=n, freq="B")
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    df = pd.DataFrame({"open": close, "high": close * 1.005, "low": close * 0.995, "close": close, "volume": [1e6] * n}, index=dates)
    df.index.name = "date"
    return df


def make_correlated_price_dfs(n=90):
    """Two series that move nearly in lockstep - correlation should come back high."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-06-01", periods=n, freq="B")
    base_returns = rng.normal(0.0005, 0.012, n)
    close_a = 100 * np.cumprod(1 + base_returns)
    close_b = 50 * np.cumprod(1 + base_returns + rng.normal(0, 0.0005, n))  # tiny idiosyncratic noise
    df_a = pd.DataFrame({"open": close_a, "high": close_a * 1.005, "low": close_a * 0.995, "close": close_a, "volume": [1e6] * n}, index=dates)
    df_b = pd.DataFrame({"open": close_b, "high": close_b * 1.005, "low": close_b * 0.995, "close": close_b, "volume": [1e6] * n}, index=dates)
    df_a.index.name = df_b.index.name = "date"
    return df_a, df_b


# --- compute_portfolio_state -------------------------------------------------------


def test_empty_portfolio_state_has_zero_everything():
    config = fresh_config()
    state = portfolio_risk.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)
    assert state["num_open_positions"] == 0
    assert state["total_open_risk_fraction"] == 0.0
    assert state["gross_exposure_fraction"] == 0.0


def test_closed_trades_do_not_count_toward_exposure():
    config = fresh_config()
    df = make_open_trades_df([
        make_open_trade(ticker="NVDA", status="OPEN"),
        make_open_trade(ticker="AMD", status="TARGET_HIT"),   # closed - must not count
        make_open_trade(ticker="SMH", status="STOPPED"),        # closed - must not count
    ])
    state = portfolio_risk.compute_portfolio_state(df, config)
    assert state["num_open_positions"] == 1
    assert state["tickers_open"] == ["NVDA"]
    assert "AMD" not in state["exposure_by_ticker"]
    assert "SMH" not in state["exposure_by_ticker"]


def test_portfolio_state_computes_risk_to_stop_not_notional():
    config = fresh_config()
    # entry 100, stop 90, size 10 -> risk-to-stop = 10*10=100 ($), notional = 100*10=1000 ($)
    df = make_open_trades_df([make_open_trade(entry_price=100.0, stop_loss=90.0, position_size=10)])
    state = portfolio_risk.compute_portfolio_state(df, config)
    assert state["total_open_risk_dollars"] == 100.0
    assert state["total_deployed_capital"] == 1000.0
    assert state["total_open_risk_fraction"] == pytest.approx(100.0 / 10_000.0)
    assert state["gross_exposure_fraction"] == pytest.approx(1000.0 / 10_000.0)


def test_sector_exposure_groups_multiple_tickers_by_sector_map():
    config = fresh_config()
    df = make_open_trades_df([
        make_open_trade(ticker="NVDA", entry_price=100.0, position_size=5),
        make_open_trade(ticker="AMD", entry_price=50.0, position_size=10),
    ])
    state = portfolio_risk.compute_portfolio_state(df, config)
    # NVDA (5*100=500) + AMD (10*50=500) both map to "semiconductors"
    assert state["exposure_by_sector"]["semiconductors"] == 1000.0
    assert state["largest_sector"] == "semiconductors"


# --- evaluate_portfolio_candidate: basic accept / reject ---------------------------


def test_zero_open_positions_accepts_a_valid_candidate():
    config = fresh_config()
    state = portfolio_risk.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)
    result = portfolio_risk.evaluate_portfolio_candidate(make_proposal(), state, {}, config)
    assert result["decision"] == "ACCEPT"
    assert result["position"]["shares"] == 20
    assert result["rejection_reasons"] == []


def test_max_open_positions_blocks_a_new_candidate():
    config = fresh_config(max_open_positions=2)
    df = make_open_trades_df([make_open_trade(ticker="NVDA"), make_open_trade(ticker="SPY", strategy="Trend Following")])
    state = portfolio_risk.compute_portfolio_state(df, config)
    result = portfolio_risk.evaluate_portfolio_candidate(make_proposal(ticker="AMD"), state, {}, config)
    assert result["decision"] == "REJECT"
    assert "max open positions reached (2/2)" in result["rejection_reasons"][0]
    assert result["position"] is None


def test_candidate_position_is_never_increased():
    config = fresh_config()
    state = portfolio_risk.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)
    proposal = make_proposal(shares=5)
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, {}, config)
    assert result["position"]["shares"] <= proposal["shares"]


def test_total_risk_limit_reduces_when_a_smaller_size_is_still_viable():
    # max_total_open_risk_pct default 0.03 -> $300 budget on $10k equity.
    config = fresh_config(max_total_open_risk_pct=0.03)
    df = make_open_trades_df([make_open_trade(entry_price=100.0, stop_loss=90.0, position_size=25)])  # risk = 250
    state = portfolio_risk.compute_portfolio_state(df, config)
    # Candidate risk-per-share = 10 (entry 100, stop 90); 20 shares would need $200 more risk,
    # but only $50 of budget remains (300-250) -> should reduce to 5 shares, not reject.
    proposal = make_proposal(ticker="AMD", entry=100.0, stop_loss=90.0, shares=20)
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, {}, config)
    assert result["decision"] == "ACCEPT_WITH_REDUCED_SIZE"
    assert result["position"]["shares"] == 5
    assert any("total open-risk budget" in w for w in result["warnings"])


def test_total_risk_limit_rejects_when_no_budget_remains():
    config = fresh_config(max_total_open_risk_pct=0.03, min_viable_shares=1)
    df = make_open_trades_df([make_open_trade(entry_price=100.0, stop_loss=90.0, position_size=30)])  # risk = 300 = full budget
    state = portfolio_risk.compute_portfolio_state(df, config)
    proposal = make_proposal(ticker="AMD", entry=100.0, stop_loss=90.0, shares=20)
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, {}, config)
    assert result["decision"] == "REJECT"
    assert "total open risk" in result["rejection_reasons"][0]


def test_single_position_cap_reduces_an_oversized_candidate():
    # max_single_position_pct default 0.20 -> $2000 cap on $10k equity.
    config = fresh_config(max_single_position_pct=0.20)
    state = portfolio_risk.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)
    proposal = make_proposal(ticker="AMD", entry=100.0, stop_loss=90.0, shares=50)  # notional 5000, way over cap
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, {}, config)
    assert result["decision"] == "ACCEPT_WITH_REDUCED_SIZE"
    assert result["position"]["position_value"] <= 2000.0


# --- sector concentration -----------------------------------------------------------


def test_sector_concentration_reduces_a_candidate_that_would_overshoot():
    # max_sector_exposure_pct default 0.35 -> $3500 cap. Existing NVDA+AMD already at $3000.
    # Tight stops (entry 100/stop 99) keep total open risk-to-stop negligible so
    # this isolates the sector check, not the separate total-risk-budget gate.
    config = fresh_config(max_sector_exposure_pct=0.35)
    df = make_open_trades_df([
        make_open_trade(ticker="NVDA", entry_price=100.0, stop_loss=99.0, position_size=15),  # 1500
        make_open_trade(ticker="AMD", entry_price=100.0, stop_loss=99.0, position_size=15),     # 1500
    ])
    state = portfolio_risk.compute_portfolio_state(df, config)
    proposal = make_proposal(ticker="SMH", entry=100.0, stop_loss=99.0, shares=30)  # would add 3000 -> total 6000 way over 3500
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, {}, config)
    assert result["decision"] in ("ACCEPT_WITH_REDUCED_SIZE", "REJECT")
    if result["decision"] == "ACCEPT_WITH_REDUCED_SIZE":
        assert result["position"]["position_value"] + 3000.0 <= 3500.0 + 1e-6
    else:
        assert "semiconductors" in result["rejection_reasons"][0]


def test_sector_concentration_rejects_when_already_at_the_cap():
    config = fresh_config(max_sector_exposure_pct=0.35, min_viable_shares=1)
    df = make_open_trades_df([make_open_trade(ticker="NVDA", entry_price=100.0, stop_loss=99.0, position_size=35)])  # 3500 = full cap
    state = portfolio_risk.compute_portfolio_state(df, config)
    proposal = make_proposal(ticker="AMD", entry=100.0, stop_loss=99.0, shares=10)
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, {}, config)
    assert result["decision"] == "REJECT"
    assert "semiconductors" in result["rejection_reasons"][0]


# --- overlap groups ------------------------------------------------------------------


def test_spy_voo_overlap_is_detected():
    config = fresh_config()
    df = make_open_trades_df([make_open_trade(ticker="SPY", strategy="Trend Following")])
    state = portfolio_risk.compute_portfolio_state(df, config)
    result = portfolio_risk.evaluate_portfolio_candidate(make_proposal(ticker="VOO"), state, {}, config)
    assert result["overlap_group"] == "broad_market_etfs"
    assert any("SPY" in w for w in result["warnings"])


def test_qqq_vgt_overlap_is_detected():
    config = fresh_config()
    df = make_open_trades_df([make_open_trade(ticker="QQQ", strategy="Trend Following")])
    state = portfolio_risk.compute_portfolio_state(df, config)
    result = portfolio_risk.evaluate_portfolio_candidate(make_proposal(ticker="VGT"), state, {}, config)
    assert result["overlap_group"] == "growth_tech_etfs"
    assert any("QQQ" in w for w in result["warnings"])


def test_smh_nvda_amd_cluster_is_detected():
    config = fresh_config()
    df = make_open_trades_df([
        make_open_trade(ticker="NVDA"),
        make_open_trade(ticker="AMD"),
    ])
    state = portfolio_risk.compute_portfolio_state(df, config)
    result = portfolio_risk.evaluate_portfolio_candidate(make_proposal(ticker="SMH"), state, {}, config)
    assert result["overlap_group"] == "semiconductor_cluster"
    assert any("NVDA" in w and "AMD" in w for w in result["warnings"])


# --- correlation ---------------------------------------------------------------------


def test_high_correlation_is_detected_and_reduces_size():
    config = fresh_config(max_correlated_positions=1, high_correlation_threshold=0.80)
    price_a, price_b = make_correlated_price_dfs()
    df = make_open_trades_df([make_open_trade(ticker="TICKX", entry_price=50.0, position_size=10)])
    state = portfolio_risk.compute_portfolio_state(df, config)
    price_data = {"TICKY": price_a, "TICKX": price_b}
    proposal = make_proposal(ticker="TICKY", entry=100.0, stop_loss=90.0, shares=20)
    result = portfolio_risk.evaluate_portfolio_candidate(proposal, state, price_data, config)
    assert any("correlation" in n.lower() and "unavailable" not in n.lower() for n in result["correlation_notes"])
    assert result["decision"] in ("ACCEPT_WITH_REDUCED_SIZE", "REJECT")


def test_missing_correlation_data_reports_unavailable_not_a_fabricated_number():
    config = fresh_config()
    df = make_open_trades_df([make_open_trade(ticker="TICKX")])
    state = portfolio_risk.compute_portfolio_state(df, config)
    # No price data at all for either ticker.
    result = portfolio_risk.evaluate_portfolio_candidate(make_proposal(ticker="TICKY"), state, {}, config)
    assert any("Data Unavailable" in n for n in result["correlation_notes"])


def test_compute_daily_return_correlation_returns_none_with_insufficient_overlap():
    config = fresh_config()
    short_df = make_price_df(1, n=5)
    other_df = make_price_df(2, n=90)
    assert portfolio_risk.compute_daily_return_correlation(short_df, other_df, config) is None


def test_compute_daily_return_correlation_detects_a_near_perfect_relationship():
    config = fresh_config()
    price_a, price_b = make_correlated_price_dfs()
    corr = portfolio_risk.compute_daily_return_correlation(price_a, price_b, config)
    assert corr is not None
    assert corr > 0.9


# --- apply_acceptance_to_state (sequential same-batch processing) ------------------


def test_apply_acceptance_to_state_updates_counts_and_exposure_without_mutating_input():
    config = fresh_config()
    state = portfolio_risk.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)
    position = {**make_proposal(ticker="NVDA", entry=100.0, stop_loss=90.0, shares=10)}
    new_state = portfolio_risk.apply_acceptance_to_state(state, position, config)

    assert state["num_open_positions"] == 0  # original untouched
    assert new_state["num_open_positions"] == 1
    assert new_state["exposure_by_sector"]["semiconductors"] == 1000.0
    assert new_state["total_open_risk_fraction"] == pytest.approx(100.0 / 10_000.0)


def test_sequential_batch_processing_lets_a_second_candidate_see_the_first():
    """Two candidates in the same sector, both proposed 'today': the second one
    must see the first's exposure via apply_acceptance_to_state, even though
    neither is in paper_trades.csv yet."""
    config = fresh_config(max_sector_exposure_pct=0.20)  # $2000 cap on $10k equity
    state = portfolio_risk.compute_portfolio_state(paper_trades.load_paper_trades_df(config), config)

    first = make_proposal(ticker="NVDA", entry=100.0, stop_loss=90.0, shares=15)  # 1500 notional
    first_result = portfolio_risk.evaluate_portfolio_candidate(first, state, {}, config)
    assert first_result["decision"] == "ACCEPT"

    state_after_first = portfolio_risk.apply_acceptance_to_state(state, first_result["position"], config)

    second = make_proposal(ticker="AMD", entry=100.0, stop_loss=90.0, shares=15)  # would add another 1500 -> 3000 > 2000 cap
    second_result = portfolio_risk.evaluate_portfolio_candidate(second, state_after_first, {}, config)
    assert second_result["decision"] in ("ACCEPT_WITH_REDUCED_SIZE", "REJECT")
