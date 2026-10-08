"""src/intelligence/pricing.py - real token usage x configurable $/million
pricing, never fabricating a cost for an unpriced model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
