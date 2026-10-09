"""Isolated strategy-research sandbox (AI Quant Trading Platform sprint,
Phase 2, RD-Agent concept: "build isolated research sandbox where
strategies CANNOT submit broker orders... use real historical data
where legitimate, clearly label synthetic fixtures").

This is a CONCEPT taken from Microsoft RD-Agent's idea of an
AI-generated-hypothesis research loop (`docs/platform/
OSS_INTEGRATION_AUDIT.md` verdict: CONCEPT, not installed - `pyqlib`/
`rdagent` are not dependencies anywhere in this project). No RD-Agent
code is used. What's actually built here is a small, auditable harness
that evaluates a `ResearchHypothesis` (a named, described set of
config overrides to one of this project's existing strategies) against
ALREADY-LOADED price history, through the exact same backtester this
project trusts in production - and nothing else.

**Hard safety boundary - enforced, not just documented:**

1. **No network access.** `run_hypothesis` takes `price_history` as a
   parameter (a dict of already-loaded DataFrames) - it never calls
   `data_collector.fetch_all_price_history` or anything else that
   reaches the network. The caller decides what data exists; this
   module cannot go fetch more.
2. **No broker credentials, no order submission.** This module has NO
   import of `src.execution` (order_manager, ibkr_client, broker,
   approval_bridge, etc.) anywhere, by design -
   `tests/test_research_sandbox.py::test_sandbox_module_never_imports_the_execution_package`
   greps this file's own source and fails the suite if that ever
   changes, so a future edit can't silently add a path back to a real
   order.
3. **No production filesystem writes.** `run_hypothesis` is a pure
   function - it returns a result dict and writes nothing to disk
   itself. Persisting a result is the CALLER'S choice, via
   `hypothesis_ledger.py`'s own separate database file (never
   `decision_ledger.db` or `paper_trades.csv`).
4. **No secrets access.** Config overrides are restricted to a small
   allowlist of keys (`ALLOWED_OVERRIDE_SECTIONS`) - "strategies",
   "risk", "backtest", "indicators" - covering every strategy
   parameter and nothing else. An override targeting any other
   top-level config key (e.g. a broker/API/credential section) is
   rejected with `ValueError`, not silently merged.
5. **No LLM calls by default.** `ResearchHypothesis` is just a plain
   dataclass a human (or, in a future, separately-gated phase, a
   script) constructs directly - this module makes zero network or
   model-API calls of its own.
6. **Never promotes anything.** There is no `promote()` function here
   and no code path from a hypothesis result into `decision_ledger.py`,
   `model_registry.py`, or any execution config - a result is a plain
   dict/row, nothing more.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pandas as pd

ALLOWED_OVERRIDE_SECTIONS = {"strategies", "risk", "backtest", "indicators"}


@dataclass(frozen=True)
class ResearchHypothesis:
    hypothesis_id: str
    description: str
    strategy_name: str
    symbol: str
    config_overrides: dict[str, Any] = field(default_factory=dict)
    data_provenance: str = "synthetic_fixture"  # or "real_market_data" - caller's honest label, see hypothesis_ledger.py


def _apply_overrides(base_config: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep-merges `overrides` into a COPY of `base_config`, restricted
    to `ALLOWED_OVERRIDE_SECTIONS` - raises `ValueError` (never
    silently drops or silently allows) on any other top-level key."""
    disallowed = set(overrides) - ALLOWED_OVERRIDE_SECTIONS
    if disallowed:
        raise ValueError(
            f"config_overrides may only touch {sorted(ALLOWED_OVERRIDE_SECTIONS)}, got disallowed section(s): {sorted(disallowed)}"
        )

    merged = copy.deepcopy(base_config)
    for section, section_overrides in overrides.items():
        if not isinstance(section_overrides, dict):
            raise ValueError(f"config_overrides['{section}'] must be a dict of settings to merge, got {type(section_overrides).__name__}")
        merged.setdefault(section, {})
        _deep_merge(merged[section], section_overrides)
    return merged


def _deep_merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def run_hypothesis(
    hypothesis: ResearchHypothesis,
    price_history: dict[str, pd.DataFrame],
    base_config: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, Any] | None:
    """Runs one hypothesis through `src.backtester`'s real strategy loop
    (real trade-selection/sizing logic, not a stand-in) against
    ALREADY-PROVIDED `price_history` only. Returns `None` (never a
    fabricated result) if the hypothesis's strategy produced zero
    trades for its symbol - a hypothesis with no trades to show is a
    real, reportable outcome, not an error, but there is nothing to
    score."""
    from .. import backtester

    if hypothesis.strategy_name not in backtester.STRATEGY_MODULES:
        raise ValueError(f"unknown strategy: {hypothesis.strategy_name!r}")

    df = price_history.get(hypothesis.symbol)
    if df is None or df.empty:
        logger.info("Hypothesis %s skipped: no price data for %s.", hypothesis.hypothesis_id, hypothesis.symbol)
        return None

    config = _apply_overrides(base_config, hypothesis.config_overrides)

    lookback_days = backtester.period_to_days(config["backtest"]["lookback_period"])
    backtest_start = df.index.max() - timedelta(days=lookback_days)

    trades, equity_curve = backtester._run_strategy_backtest(
        hypothesis.strategy_name, [hypothesis.symbol], price_history, backtest_start, config, logger,
    )
    if not trades:
        logger.info("Hypothesis %s: zero trades for %s under this strategy/overrides.", hypothesis.hypothesis_id, hypothesis.symbol)
        return {
            "hypothesis_id": hypothesis.hypothesis_id,
            "description": hypothesis.description,
            "strategy_name": hypothesis.strategy_name,
            "symbol": hypothesis.symbol,
            "data_provenance": hypothesis.data_provenance,
            "config_overrides": hypothesis.config_overrides,
            "stats": None,
            "trade_count": 0,
        }

    stats = backtester._compute_stats(trades, equity_curve, config["backtest"]["initial_capital"])

    return {
        "hypothesis_id": hypothesis.hypothesis_id,
        "description": hypothesis.description,
        "strategy_name": hypothesis.strategy_name,
        "symbol": hypothesis.symbol,
        "data_provenance": hypothesis.data_provenance,
        "config_overrides": hypothesis.config_overrides,
        "stats": stats,
        "trade_count": len(trades),
    }
