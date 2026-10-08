"""Configurable per-model dollar pricing for TradingAgents token usage
(GitHub Issue #1: "add actual dollar-cost accounting using provider token
usage and configurable pricing, not call counts alone").

Prices are $ per MILLION tokens, matching how providers publish their own
pricing. The built-in defaults are a best-effort snapshot taken when this
was written and WILL drift - always override `intelligence.tradingagents.
pricing` in config/settings.yaml with current published rates before
trusting the dollar figures this produces for anything but a rough order
of magnitude. An unrecognized model returns `None` ("unknown cost"), never
a fabricated $0.00 or an arbitrary default rate - the same "Data
Unavailable, never fabricated" discipline used everywhere else in this
codebase.
"""

from __future__ import annotations

from typing import Any

# $ per 1,000,000 tokens. A SNAPSHOT, NOT LIVE PRICING - override via config.
# `cached_input` is the rate for tokens served from the provider's prompt
# cache (cheaper than a fresh input token) - omit it for a model with no
# published cached-input rate and it falls back to the plain `input` rate
# (no discount assumed, never a fabricated one).
#
# gpt-6-sol/gpt-6-luna added after a real run on a Mac (GitHub Issue #1
# follow-up) hit `estimated_cost_usd: null` for both - they are TradingAgents'
# own DEFAULT_CONFIG deep/quick-think models (tradingagents/default_config.py)
# and were simply missing from this table. Rates below are from third-party
# pricing trackers as of 2026-10 (OpenAI's own pricing page could not be
# fetched directly) and multiple independent sources agree on them; VERIFY
# against https://developers.openai.com/api/docs/pricing before trusting
# them beyond a rough order of magnitude, per this module's own discipline.
DEFAULT_PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "gpt-5.1": {"input": 1.25, "output": 10.00},
    "gpt-5.1-mini": {"input": 0.25, "output": 2.00},
    "gpt-6-sol": {"input": 2.00, "output": 10.00, "cached_input": 0.20},
    "gpt-6-luna": {"input": 0.10, "output": 0.50, "cached_input": 0.01},
    "claude-opus-5-5": {"input": 15.00, "output": 75.00},
    "claude-sonnet-5-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-5-5": {"input": 0.80, "output": 4.00},
    "gemini-3-pro": {"input": 1.25, "output": 10.00},
    "gemini-3-flash": {"input": 0.075, "output": 0.30},
}


def estimate_cost_usd(token_usage: dict[str, dict[str, Any]] | None, pricing_table: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    """`token_usage`: `{model_name: {"input_tokens": int, "output_tokens": int,
    "cached_input_tokens": int, "calls": int}}` - the shape `tools/
    tradingagents_runner.py`'s usage-tracking callback returns.
    `cached_input_tokens` (a subset of `input_tokens`, not additional to
    it) is priced at the model's `cached_input` rate when the pricing
    table has one, else at the plain `input` rate.

    Returns `{"total_usd": float|None, "by_model": {...}, "unknown_models": [...]}`.
    `total_usd` is exactly `0.0` when `token_usage` is empty/None (genuinely
    zero tokens spent - known, not unknown), and `None` whenever at least
    one model actually used has no pricing entry - the caller must be able
    to tell "no cost" apart from "cost is incomplete," never silently
    under-report a partial sum as the real total. This is also how spend
    caps fail closed on an unknown model (GitHub Issue #1 follow-up
    requirement 2): `tradingagents_adapter.run_one()` treats `total_usd is
    None` as "charge the full conservative per-call reservation," never a
    silent `$0.00`, so an unpriced model still counts fully against the
    daily/monthly cap rather than slipping past it for free."""
    table = pricing_table or DEFAULT_PRICING_USD_PER_MILLION_TOKENS
    by_model: dict[str, Any] = {}
    unknown_models: list[str] = []
    total = 0.0
    any_unknown = False

    for model, usage in (token_usage or {}).items():
        input_tokens = (usage or {}).get("input_tokens", 0) or 0
        output_tokens = (usage or {}).get("output_tokens", 0) or 0
        cached_input_tokens = min((usage or {}).get("cached_input_tokens", 0) or 0, input_tokens)
        rates = table.get(model)
        if rates is None:
            unknown_models.append(model)
            any_unknown = True
            by_model[model] = {
                "usd": None, "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
            }
            continue

        cached_rate = rates.get("cached_input", rates["input"])
        uncached_input_tokens = input_tokens - cached_input_tokens
        usd = (
            (uncached_input_tokens / 1_000_000.0) * rates["input"]
            + (cached_input_tokens / 1_000_000.0) * cached_rate
            + (output_tokens / 1_000_000.0) * rates["output"]
        )
        by_model[model] = {
            "usd": round(usd, 6), "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
        }
        total += usd

    return {
        "total_usd": None if any_unknown else round(total, 6),
        "by_model": by_model,
        "unknown_models": unknown_models,
    }
