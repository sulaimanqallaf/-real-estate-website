"""src/intelligence/pricing.py - real token usage x configurable $/million
pricing, never fabricating a cost for an unpriced model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import pricing


def test_zero_usage_is_a_known_zero_not_unknown():
    result = pricing.estimate_cost_usd({})
    assert result["total_usd"] == 0.0
    assert result["unknown_models"] == []


def test_none_usage_is_a_known_zero():
    result = pricing.estimate_cost_usd(None)
    assert result["total_usd"] == 0.0


def test_computes_cost_for_a_known_model():
    table = {"test-model": {"input": 1.00, "output": 2.00}}
    usage = {"test-model": {"input_tokens": 1_000_000, "output_tokens": 500_000}}
    result = pricing.estimate_cost_usd(usage, table)
    assert result["total_usd"] == 1.00 + 1.00  # 1M in @ $1 + 0.5M out @ $2/M
    assert result["by_model"]["test-model"]["usd"] == 2.00


def test_unknown_model_makes_total_none_not_a_partial_sum():
    table = {"known-model": {"input": 1.00, "output": 2.00}}
    usage = {
        "known-model": {"input_tokens": 1_000_000, "output_tokens": 0},
        "mystery-model": {"input_tokens": 1_000_000, "output_tokens": 0},
    }
    result = pricing.estimate_cost_usd(usage, table)
    assert result["total_usd"] is None
    assert result["unknown_models"] == ["mystery-model"]
    assert result["by_model"]["known-model"]["usd"] == 1.00  # per-model figure still reported
    assert result["by_model"]["mystery-model"]["usd"] is None


def test_default_pricing_table_is_used_when_no_override_given():
    usage = {"claude-haiku-5-5": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}}
    result = pricing.estimate_cost_usd(usage)
    assert result["total_usd"] == 0.80 + 4.00


def test_gpt6_sol_and_luna_have_pricing_entries():
    assert "gpt-6-sol" in pricing.DEFAULT_PRICING_USD_PER_MILLION_TOKENS
    assert "gpt-6-luna" in pricing.DEFAULT_PRICING_USD_PER_MILLION_TOKENS


def test_gpt6_luna_cost_is_no_longer_null():
    usage = {"gpt-6-luna": {"input_tokens": 1000, "output_tokens": 500, "cached_input_tokens": 0}}
    result = pricing.estimate_cost_usd(usage)
    assert result["total_usd"] is not None
    assert result["total_usd"] == pytest.approx((1000 / 1_000_000) * 0.10 + (500 / 1_000_000) * 0.50)


def test_gpt6_sol_cost_is_no_longer_null():
    usage = {"gpt-6-sol": {"input_tokens": 1000, "output_tokens": 500, "cached_input_tokens": 0}}
    result = pricing.estimate_cost_usd(usage)
    assert result["total_usd"] == pytest.approx((1000 / 1_000_000) * 2.00 + (500 / 1_000_000) * 10.00)


def test_cached_input_tokens_are_priced_at_the_cached_rate_not_the_full_input_rate():
    table = {"m": {"input": 10.00, "output": 20.00, "cached_input": 1.00}}
    usage = {"m": {"input_tokens": 1_000_000, "output_tokens": 0, "cached_input_tokens": 1_000_000}}
    result = pricing.estimate_cost_usd(usage, table)
    assert result["total_usd"] == 1.00  # fully cached - cached rate only, not the $10 full-input rate
    assert result["by_model"]["m"]["cached_input_tokens"] == 1_000_000


def test_cached_input_tokens_fall_back_to_the_plain_input_rate_when_no_cached_rate_is_published():
    table = {"m": {"input": 5.00, "output": 20.00}}  # no cached_input entry
    usage = {"m": {"input_tokens": 1_000_000, "output_tokens": 0, "cached_input_tokens": 1_000_000}}
    result = pricing.estimate_cost_usd(usage, table)
    assert result["total_usd"] == 5.00  # no discount assumed


def test_cached_input_tokens_are_clamped_to_input_tokens_never_counted_twice():
    table = {"m": {"input": 10.00, "output": 0.0, "cached_input": 1.00}}
    usage = {"m": {"input_tokens": 100, "output_tokens": 0, "cached_input_tokens": 999}}  # malformed: more cached than total
    result = pricing.estimate_cost_usd(usage, table)
    # clamped to 100 cached, 0 uncached - never a negative uncached count
    assert result["total_usd"] == pytest.approx((100 / 1_000_000) * 1.00)
