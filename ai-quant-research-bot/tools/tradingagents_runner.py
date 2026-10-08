#!/usr/bin/env python
"""Runs INSIDE the isolated `.venvs/tradingagents/` environment ONLY -
never imported or run from this project's own Python environment. GitHub
Issue #1: "build a separate optional integration using the real
TradingAgents Python package... connect it through a modular Python
adapter."

This script is deliberately a thin, dumb boundary: it builds a
`tradingagents.graph.trading_graph.TradingAgentsGraph`, calls `.propagate()`,
and dumps the raw resulting fields to stdout as JSON - it does NOT decide
BUY/SELL/HOLD, does not compute confidence, and does not know anything
about this project's own schemas. All of that mapping/interpretation
lives in `src/intelligence/tradingagents_adapter.py`, which runs in the
MAIN project environment and only ever talks to this script over a
subprocess boundary (stdin: one JSON request; stdout: one JSON response;
stderr: logs) - so the "smart" logic stays testable without the
TradingAgents package installed at all, and a change to TradingAgents'
internal shape only ever needs a change here or in the adapter's mapping
function, never both.

Usage: python tradingagents_runner.py <request.json>
Reads the request from the given file (not argv/stdin directly, to avoid
any shell-escaping of ticker/portfolio data); writes exactly one JSON
object to stdout. Never raises past main() - any failure becomes
`{"ok": false, "error": "...", "error_type": "..."}` on stdout, so the
calling adapter never has to parse a Python traceback out of stderr to
know whether the call succeeded.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: tradingagents_runner.py <request.json>", "error_type": "UsageError"}))
        return 1

    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            request = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"could not read request file: {exc}", "error_type": type(exc).__name__}))
        return 1

    try:
        result = _run(request)
    except Exception as exc:  # noqa: BLE001 - this process's only job is to never crash without reporting why
        print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}))
        return 0

    print(json.dumps(result))
    return 0


def _run(request: dict) -> dict:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = dict(DEFAULT_CONFIG)
    config.update(request.get("config_overrides") or {})
    # Always isolated under this run's own work_dir - never the real
    # upstream default of ~/.tradingagents, which would mix this
    # project's cache/results/memory-log files with anything else on
    # the machine that happens to run TradingAgents.
    work_dir = request["work_dir"]
    config["results_dir"] = f"{work_dir}/results"
    config["data_cache_dir"] = f"{work_dir}/cache"
    config["memory_log_path"] = f"{work_dir}/memory/trading_memory.md"
    config["checkpoint_enabled"] = False

    selected_analysts = request.get("selected_analysts") or ["market", "social", "news", "fundamentals"]
    graph = TradingAgentsGraph(selected_analysts=selected_analysts, config=config)

    final_state, signal = graph.propagate(
        request["ticker"], request["trade_date"],
        asset_type=request.get("asset_type", "stock"),
        portfolio=request.get("portfolio"),
    )

    debate = final_state.get("investment_debate_state") or {}
    risk_debate = final_state.get("risk_debate_state") or {}

    return {
        "ok": True,
        "signal": signal,
        "final_rating": final_state.get("final_rating"),
        "reports": {
            "market": final_state.get("market_report"),
            "sentiment": final_state.get("sentiment_report"),
            "news": final_state.get("news_report"),
            "fundamentals": final_state.get("fundamentals_report"),
        },
        "bull_history": debate.get("bull_history"),
        "bear_history": debate.get("bear_history"),
        "investment_plan": final_state.get("investment_plan"),
        "trader_investment_plan": final_state.get("trader_investment_plan"),
        "final_trade_decision": final_state.get("final_trade_decision"),
        "risk_debate_history": risk_debate.get("history"),
        # run_settings() is an explicit allowlist in upstream (no keys, no
        # local paths) - safe to pass through as-is.
        "run_settings": graph.run_settings() if hasattr(graph, "run_settings") else None,
    }


if __name__ == "__main__":
    raise SystemExit(main())
