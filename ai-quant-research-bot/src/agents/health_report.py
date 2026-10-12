"""Daily fleet health report + deduplicated Telegram failure alerts for
Sprint 4's new autonomous agent fleet (Priority 6: "durable job
scheduling, independent agent workers, recovery checkpoints, cost
limits, daily health reporting and Telegram failure notifications").

Distinct from `src/execution/run_health.py` (Sprint 3's daily-run/
position_monitor health report, which this module does not touch or
duplicate): this is the equivalent report for the THREE agents Sprint
4 introduced - Research Scholar, News Agent, Strategy Lab - built the
same way: read-only aggregation of `registry.read_status()`,
`heartbeat.heartbeat_status()`, and `budget.remaining_today()` for
each one, never a network/LLM/broker call of its own.

**Alert deduplication is new ground this module adds** (Sprint 3's own
`send_failure_alert()`/`send_stale_heartbeat_alert()` send
unconditionally every time they're called, with no dedup primitive to
reuse - see their docstrings). A FAILED run only re-alerts once a NEW
run with a different `finished_at` has also failed; a stale heartbeat
alerts once when it first goes stale, and is eligible to alert again
only after it has recovered and gone stale a second time. Dedup state
is a small per-agent JSON file under the same `data/journal/agents/`
directory `registry.py`/`heartbeat.py`/`budget.py` already use -
corrupted/missing state is read as "never alerted," never as "already
alerted" (fail toward sending one extra alert, never toward silently
swallowing a real failure).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import redact_secrets, resolve_path
from . import budget, heartbeat, registry

KNOWN_AGENT_NAMES = ("research_scholar", "news_agent", "strategy_lab")

# (agent_name, config_section_key, default_daily_limit, [source names]) -
# mirrors each agent's own `_research_config()`/`_news_config()` +
# `DEFAULT_DAILY_LIMIT_PER_SOURCE` exactly (see research_scholar.py/
# news_agent.py) without importing their private helpers.
_BUDGET_SOURCES: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "research_scholar": ("scholar", 20, ("openalex", "arxiv", "semantic_scholar")),
    "news_agent": ("news_agent", 50, ("sec_filings", "fred")),
}

_AGENTS_SUBDIR = "agents"


def _agents_dir(config: dict[str, Any]) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir", "data/journal")
    return resolve_path(journal_dir) / _AGENTS_SUBDIR


def _alert_state_path(config: dict[str, Any], agent_name: str) -> Path:
    return _agents_dir(config) / f"{agent_name}_last_alert.json"


def _read_alert_state(config: dict[str, Any], agent_name: str) -> dict[str, Any]:
    path = _alert_state_path(config, agent_name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_alert_state(config: dict[str, Any], agent_name: str, state: dict[str, Any]) -> None:
    out_dir = _agents_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _alert_state_path(config, agent_name)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def _budget_report_for(config: dict[str, Any], agent_name: str, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    entry = _BUDGET_SOURCES.get(agent_name)
    if entry is None:
        return {}
    section_key, default_limit, sources = entry
    daily_limit = config.get("research", {}).get(section_key, {}).get("daily_api_limit_per_source", default_limit)
    report = {}
    for source in sources:
        used = budget.calls_used_today(config, agent_name, source, now=now)
        remaining = budget.remaining_today(config, agent_name, source, daily_limit=daily_limit, now=now)
        report[source] = {"used": used, "limit": daily_limit, "remaining": remaining, "exhausted": remaining == 0}
    return report


def build_fleet_report(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, Any]:
    """One entry per known agent name (the union of `KNOWN_AGENT_NAMES`
    and any agent that has ever recorded a status, so a renamed or
    future agent still shows up) - never fabricates a status for an
    agent that has never run (`registry.read_status()` already returns
    `None` for that case; this just passes it through)."""
    reference = now or datetime.now(timezone.utc)
    agent_names = sorted(set(KNOWN_AGENT_NAMES) | set(registry.list_known_agents(config)))
    agents = {}
    for name in agent_names:
        agents[name] = {
            "status": registry.read_status(config, name),
            "heartbeat": heartbeat.heartbeat_status(config, name, now=reference),
            "budget": _budget_report_for(config, name, now=reference),
        }
    return {"generated_at": reference.isoformat(), "agents": agents}


def format_fleet_report_text(report: dict[str, Any]) -> str:
    lines = ["AI Quant Research Bot - agent fleet health report", f"Generated: {report['generated_at']}", ""]
    for name, entry in sorted(report["agents"].items()):
        status = entry["status"]
        hb = entry["heartbeat"]
        lines.append(f"=== {name} ===")
        if status is None:
            lines.append("  Status: never run")
        else:
            lines.append(f"  Status: {status['status']} (started {status['started_at']}, finished {status['finished_at'] or 'in progress'})")
            if status.get("summary"):
                lines.append(f"  Summary: {status['summary']}")
            if status.get("error"):
                lines.append(f"  Error: {redact_secrets(status['error'])}")
        if hb["never_started"]:
            lines.append("  Heartbeat: never started")
        elif hb["stale"]:
            lines.append(f"  Heartbeat: STALE (last seen {hb['age_seconds']:.0f}s ago)")
        else:
            lines.append(f"  Heartbeat: OK (last seen {hb['age_seconds']:.0f}s ago)")
        for source, info in sorted(entry["budget"].items()):
            exhausted_note = " - EXHAUSTED for today" if info["exhausted"] else ""
            lines.append(f"  Budget ({source}): {info['used']}/{info['limit']} calls used today{exhausted_note}")
        lines.append("")
    return "\n".join(lines)


def _send_alert(config: dict[str, Any], logger: logging.Logger, text: str) -> bool:
    """Best-effort, never raises - identical posture to
    `run_health.send_failure_alert()`."""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.warning("Agent fleet alert not sent: Telegram not configured.")
            return False
        from .. import telegram_bot

        return telegram_bot.send_telegram_message(token, chat_id, text, logger)
    except Exception as exc:  # noqa: BLE001 - alerting itself must never raise
        logger.error("Failed to send agent fleet alert: %s", redact_secrets(str(exc)))
        return False


def check_and_alert_fleet(config: dict[str, Any], logger: logging.Logger, report: dict[str, Any], now: datetime | None = None) -> list[str]:
    """Sends a deduplicated Telegram alert for each agent that is newly
    FAILED or newly stale since the last call, and updates the
    per-agent dedup state file either way. Returns the names of agents
    actually alerted this call (for tests/visibility) - an agent with
    nothing new to report is silently skipped, not re-alerted."""
    alerted: list[str] = []
    for name, entry in sorted(report["agents"].items()):
        state = _read_alert_state(config, name)
        status = entry["status"]
        hb = entry["heartbeat"]
        changed = False

        if status is not None and status["status"] == registry.STATUS_FAILED:
            finished_at = status.get("finished_at")
            if finished_at and finished_at != state.get("last_alerted_failed_finished_at"):
                text = (
                    f"🚨 Agent '{name}' FAILED - needs attention\n"
                    f"{redact_secrets(status.get('error') or status.get('summary') or 'no detail recorded')}\n"
                    "Check the agent fleet health report and its own log file for detail."
                )
                _send_alert(config, logger, text)
                state["last_alerted_failed_finished_at"] = finished_at
                changed = True
                alerted.append(name)

        is_stale = bool(hb.get("stale"))
        if is_stale and not state.get("stale_alert_active"):
            text = f"🚨 Agent '{name}' heartbeat STALE - last seen {hb['last_heartbeat_at']} ({hb['age_seconds']:.0f}s ago)"
            _send_alert(config, logger, text)
            state["stale_alert_active"] = True
            changed = True
            if name not in alerted:
                alerted.append(name)
        elif not is_stale and state.get("stale_alert_active"):
            state["stale_alert_active"] = False
            changed = True

        if changed:
            _write_alert_state(config, name, state)

    return alerted


def main() -> int:
    """`python -m src.agents.health_report` - prints the fleet report
    and sends any newly-due Telegram alerts. Read-only beyond its own
    small dedup-state files and the Telegram API call; no network/LLM/
    broker call of its own. Reserve launchd label (documented only,
    never one of the 7 existing jobs): `com.aiquantresearchbot.
    agentfleethealth`, e.g. `StartInterval` every 1800s."""
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="agent_health_report.log")

    report = build_fleet_report(config, logger)
    print(format_fleet_report_text(report))
    check_and_alert_fleet(config, logger, report)

    any_failed = any((e["status"] or {}).get("status") == registry.STATUS_FAILED for e in report["agents"].values())
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
