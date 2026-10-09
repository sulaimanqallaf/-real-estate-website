"""src/analytics/monte_carlo.py - pure bootstrap math, no mocks needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics import monte_carlo


def test_returns_none_below_min_trades():
    assert monte_carlo.run_monte_carlo_stress_test([1.0] * (monte_carlo.MIN_TRADES_FOR_STRESS_TEST - 1)) is None


def test_runs_at_exactly_the_minimum_trade_count():
    returns = [1.0, -0.5, 2.0, -1.0, 0.5, 1.5, -0.3, 0.8, -0.6, 1.1][: monte_carlo.MIN_TRADES_FOR_STRESS_TEST]
    result = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=200, seed=1)
    assert result is not None
    assert result["n_trades"] == monte_carlo.MIN_TRADES_FOR_STRESS_TEST


def test_deterministic_given_the_same_seed():
    returns = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 1.0, 0.5, -1.5, 2.5, 0.2, -0.1]
    a = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=500, seed=42)
    b = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=500, seed=42)
    assert a == b


def test_different_seeds_can_give_different_results():
    returns = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 1.0, 0.5, -1.5, 2.5, 0.2, -0.1]
    a = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=500, seed=1)
    b = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=500, seed=2)
    assert a["simulated_total_return_pct"] != b["simulated_total_return_pct"]


def test_all_positive_trades_never_show_a_simulated_loss():
    returns = [1.0, 2.0, 0.5, 1.5, 3.0, 0.2, 1.1, 0.8, 2.2, 1.3]
    result = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=500, seed=7)
    assert result["probability_of_loss"] == 0.0
    assert result["simulated_total_return_pct"]["p5"] > 0.0


def test_all_negative_trades_always_show_a_loss():
    returns = [-1.0, -2.0, -0.5, -1.5, -3.0, -0.2, -1.1, -0.8, -2.2, -1.3]
    result = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=500, seed=7)
    assert result["probability_of_loss"] == 1.0
    assert result["simulated_total_return_pct"]["p95"] < 0.0


def test_observed_total_return_matches_direct_compounding():
    returns_pct = [10.0, -5.0, 10.0, -5.0]
    result = monte_carlo.run_monte_carlo_stress_test(returns_pct * 3, n_simulations=10, seed=1)
    expected = 1.0
    for r in returns_pct * 3:
        expected *= (1.0 + r / 100.0)
    assert result["observed_total_return_pct"] == round((expected - 1.0) * 100.0, 2)


def test_percentiles_are_non_decreasing():
    returns = [3.0, -2.0, 1.0, -1.5, 2.5, -0.5, 0.8, -3.0, 1.2, -0.2, 2.0, -1.0]
    result = monte_carlo.run_monte_carlo_stress_test(returns, n_simulations=1000, seed=3)
    p = result["simulated_total_return_pct"]
    assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]
