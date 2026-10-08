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
DEFAULT_PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "gpt-5.1": {"input": 1.25, "output": 10.00},
    "gpt-5.1-mini": {"input": 0.25, "output": 2.00},
    "claude-opus-5-5": {"input": 15.00, "output": 75.00},
    "claude-sonnet-5-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-5-5": {"input": 0.80, "output": 4.00},
    "gemini-3-pro": {"input": 1.25, "output": 10.00},
    "gemini-3-flash": {"input": 0.075, "output": 0.30},
}


def estimate_cost_usd(token_usage: dict[str, dict[str, Any]] | None, pricing_table: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    """`token_usage`: `{model_name: {"input_tokens": int, "output_tokens": int, "calls": int}}`
    - the shape `tools/tradingagents_runner.py`'s usage-tracking callback
    returns.

    Returns `{"total_usd": float|None, "by_model": {...}, "unknown_models": [...]}`.
    `total_usd` is exactly `0.0` when `token_usage` is empty/None (genuinely
    zero tokens spent - known, not unknown), and `None` whenever at least
    one model actually used has no pricing entry - the caller must be able
    to tell "no cost" apart from "cost is incomplete," never silently
    under-report a partial sum as the real total."""
    table = pricing_table or DEFAULT_PRICING_USD_PER_MILLION_TOKENS
    by_model: dict[str, Any] = {}
    unknown_models: list[str] = []
    total = 0.0
    any_unknown = False

    for model, usage in (token_usage or {}).items():
        input_tokens = (usage or {}).get("input_tokens", 0) or 0
        output_tokens = (usage or {}).get("output_tokens", 0) or 0
        rates = table.get(model)
        if rates is None:
            unknown_models.append(model)
            any_unknown = True
            by_model[model] = {"usd": None, "input_tokens": input_tokens, "output_tokens": output_tokens}
            continue
        usd = (input_tokens / 1_000_000.0) * rates["input"] + (output_tokens / 1_000_000.0) * rates["output"]
        by_model[model] = {"usd": round(usd, 6), "input_tokens": input_tokens, "output_tokens": output_tokens}
        total += usd

    return {
        "total_usd": None if any_unknown else round(total, 6),
        "by_model": by_model,
        "unknown_models": unknown_models,
    }
