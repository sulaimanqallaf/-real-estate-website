"""Weekly Telegram learning report (GitHub Issue #1 P1 - "send me a
weekly Telegram report explaining what the bot learned"). `python -m
src.ml.weekly_report` is meant to run on its own weekly schedule - a
separate process, same pattern as `retrain_scheduler.py`/
`position_monitor.py`.

Reads ONLY from the decision ledger (`decision_ledger.py`) and the model
event log (`model_events.py`) - never computes anything itself, never
retrains, never touches a safety limit. Every number here is either a
plain count or a confidence-interval estimate computed from real, already-
recorded outcomes; nothing is fabricated when the sample is too small to
say anything - see `_win_rate_with_ci()`.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import decision_ledger, model_events

# Below this many resolved trades, a win-rate/expectancy figure is too
# noisy to report as anything but "insufficient sample" - matches the
# same philosophy as performance_tracker.MIN_TRADES_FOR_ADVANCED_STATS.
MIN_TRADES_FOR_RATE_ESTIMATE = 5


def _win_rate_with_ci(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate + a 95% normal-approximation confidence interval - never
    fabricated for a tiny sample (`has_data=False` below the minimum)."""
    n = len(resolved)
    if n < MIN_TRADES_FOR_RATE_ESTIMATE:
        return {"has_data": False, "sample_size": n}
    wins = sum(1 for r in resolved if (r.get("pnl_dollars") or 0) > 0)
    p = wins / n
    margin = 1.96 * math.sqrt(max(p * (1 - p), 0.0) / n)
    return {
        "has_data": True, "sample_size": n, "wins": wins, "losses": n - wins,
        "win_rate_pct": round(p * 100.0, 1),
        "win_rate_ci_low_pct": round(max(0.0, p - margin) * 100.0, 1),
        "win_rate_ci_high_pct": round(min(1.0, p + margin) * 100.0, 1),
    }


def _group_stats(resolved: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in resolved:
        group_key = row.get(key) or "UNKNOWN"
        groups.setdefault(group_key, []).append(row)
    return {group_key: _win_rate_with_ci(rows) for group_key, rows in groups.items()}


def _expectancy_net_of_costs(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Average $ P&L per resolved trade, net of commission - never
    presented without the sample size that backs it."""
    n = len(resolved)
    if n < MIN_TRADES_FOR_RATE_ESTIMATE:
        return {"has_data": False, "sample_size": n}
    net_pnls = [(r.get("pnl_dollars") or 0.0) - (r.get("commission") or 0.0) for r in resolved]
    return {"has_data": True, "sample_size": n, "expectancy_per_trade_dollars": round(sum(net_pnls) / n, 2)}


def compute_weekly_summary(config: dict[str, Any], as_of: datetime | None = None, window_days: int = 7) -> dict[str, Any]:
    """Everything the report needs, computed fresh from the decision
    ledger + model event log for the last `window_days` days ending at
    `as_of` (defaults to real now - test-only override, same convention
    as every other `now`/`as_of` parameter in this codebase)."""
    as_of = as_of or datetime.now(timezone.utc)
    since_iso = (as_of - timedelta(days=window_days)).isoformat()

    db_path = decision_ledger.resolve_db_path(config)
    rows = decision_ledger.query_decisions(db_path, since=since_iso, until=as_of.isoformat())
    resolved = [r for r in rows if r.get("outcome_status")]
    broker_resolved = [r for r in resolved if r.get("provenance") == "BROKER_PAPER"]
    simulated_resolved = [r for r in resolved if r.get("provenance") == "SIMULATED"]

    decision_counts = Counter(r["decision"] for r in rows)

    events = model_events.read_events(config, since=since_iso)

    return {
        "window_days": window_days,
        "as_of": as_of.isoformat(),
        "total_decisions": len(rows),
        "decision_counts": dict(decision_counts),
        "overall": _win_rate_with_ci(resolved),
        "broker_paper": _win_rate_with_ci(broker_resolved),
        "simulated": _win_rate_with_ci(simulated_resolved),
        "expectancy": _expectancy_net_of_costs(resolved),
        "by_strategy": _group_stats(resolved, "strategy"),
        "by_regime": _group_stats(resolved, "regime"),
        "by_ticker": _group_stats(resolved, "ticker"),
        "model_events": events,
    }


def _format_rate(stats: dict[str, Any]) -> str:
    if not stats.get("has_data"):
        return f"insufficient sample (n={stats.get('sample_size', 0)})"
    return f"{stats['win_rate_pct']}% win rate (95% CI {stats['win_rate_ci_low_pct']}-{stats['win_rate_ci_high_pct']}%, n={stats['sample_size']})"


def _format_rate_line(label: str, stats: dict[str, Any]) -> str:
    return f"{label}: {_format_rate(stats)}"


def format_weekly_report(summary: dict[str, Any]) -> str:
    lines = [
        f"📚 WEEKLY LEARNING REPORT ({summary['window_days']}d window)",
        "",
        f"Decisions this window: {summary['total_decisions']} ({', '.join(f'{k}: {v}' for k, v in summary['decision_counts'].items()) or 'none'})",
        "",
        _format_rate_line("Overall", summary["overall"]),
        _format_rate_line("Broker-paper (real IBKR fills)", summary["broker_paper"]),
        _format_rate_line("Simulated (never a broker fill)", summary["simulated"]),
    ]

    expectancy = summary["expectancy"]
    if expectancy.get("has_data"):
        lines.append(f"Expectancy/trade (net of commission): ${expectancy['expectancy_per_trade_dollars']:.2f} (n={expectancy['sample_size']})")
    else:
        lines.append(f"Expectancy/trade: insufficient sample (n={expectancy.get('sample_size', 0)})")

    if summary["by_strategy"]:
        lines.append("")
        lines.append("By strategy:")
        for strategy, stats in summary["by_strategy"].items():
            lines.append(f"  {strategy}: {_format_rate(stats)}")

    if summary["by_regime"]:
        lines.append("")
        lines.append("By regime:")
        for regime, stats in summary["by_regime"].items():
            lines.append(f"  {regime}: {_format_rate(stats)}")

    events = summary["model_events"]
    lines.append("")
    if events:
        lines.append("Model changes this window:")
        for event in events:
            reason = f" - {event['reason']}" if event.get("reason") else ""
            lines.append(f"  {event['event']} {event.get('model_type', '')} ({event['model_id']}){reason}")
    else:
        lines.append("Model changes this window: none.")

    return "\n".join(lines)


def send_weekly_report(config: dict[str, Any], logger: logging.Logger, token: str, chat_id: str, as_of: datetime | None = None) -> bool:
    from .. import telegram_bot

    summary = compute_weekly_summary(config, as_of=as_of)
    text = format_weekly_report(summary)
    return telegram_bot.send_telegram_message(token, chat_id, text, logger)


def main() -> int:
    import os

    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="ml_weekly_report.log")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("Weekly report: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured - printing instead.")
        print(format_weekly_report(compute_weekly_summary(config)))
        return 0

    send_weekly_report(config, logger, token, chat_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
