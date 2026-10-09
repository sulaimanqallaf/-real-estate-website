"""Crash-recovery / watchdog monitoring for `position_monitor.py`'s
continuous loop (Sprint 3, Reliability milestone: "implement crash
recovery and watchdog monitoring").

**Two complementary layers, neither new to this sprint except the
heartbeat itself:**

1. **Process-level auto-restart**: `KeepAlive`/`RunAtLoad` in the
   launchd plist README.md already documents (section "Position
   monitor" - `~/Library/LaunchAgents/com.aiquantresearchbot.monitor.
   plist`) - macOS itself restarts the process if it crashes. This is
   a manual, documented, opt-in setup step (same precedent as every
   other heavy/optional piece of this project - see
   `scripts/setup_oss_quant_env.sh`'s README section), never silently
   auto-installed.
2. **State recovery on restart**: `order_manager.OrderManager.
   restore_from_journal_rows()` already rebuilds every still-active
   managed order from the append-only execution journal before the
   new process's loop starts - this is what makes a restart safe
   rather than just "the process starts again with amnesia."

**What was genuinely missing, and what this module adds**: nothing
previously recorded whether the loop was ACTUALLY alive and ticking
right now, as opposed to "crashed and launchd hasn't restarted it yet"
or "launchd was never configured at all" (a real possibility - the
plist setup is a manual step). `record_heartbeat()` writes a small,
durable timestamp (colocated with `data.journal_dir`, same convention
as every other state file in `execution/`) on every tick, REGARDLESS
of broker connection state - this is a liveness signal for the PROCESS
itself, independent of `circuit_breaker.check_broker_connection()`'s
separate broker-connectivity signal. `heartbeat_status()` is read by
`run_health.build_health_report()` (so it's visible in the existing
health report/dashboard immediately) and by this module's own `main()`
CLI, which can additionally send a Telegram alert when the heartbeat
has gone stale - meant to be scheduled independently (e.g. a launchd
`StartInterval` job, documented in README.md, never auto-installed)
since a crashed position_monitor obviously cannot alert about its own
death.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HEARTBEAT_FILENAME = "position_monitor_heartbeat.json"

# 8x the default 15s poll_interval_seconds - generous enough to absorb
# one or two unusually slow ticks (e.g. a slow broker response) without
# falsely reporting "stale" on ordinary jitter, while still catching a
# genuinely dead process well before a human would otherwise notice.
DEFAULT_MAX_AGE_SECONDS = 120


def _journal_dir(config: dict[str, Any]) -> Path:
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir)
    return resolve_path("data/journal")


def _heartbeat_path(config: dict[str, Any]) -> Path:
    return _journal_dir(config) / _HEARTBEAT_FILENAME


def record_heartbeat(config: dict[str, Any], extra: dict[str, Any] | None = None, now: datetime | None = None) -> None:
    """Called once per tick from `position_monitor.run_one_tick()`,
    UNCONDITIONALLY (before the broker-connection early-return) - this
    is a liveness signal for the process itself, not for the broker
    connection. Never raises: a heartbeat-write failure must never be
    allowed to interrupt the actual monitoring tick it's recording."""
    path = _heartbeat_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_heartbeat_at": (now or datetime.now(timezone.utc)).isoformat(), "extra": extra or {}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def heartbeat_status(config: dict[str, Any], max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS, now: datetime | None = None) -> dict[str, Any]:
    """Read-only. Returns `{"last_heartbeat_at", "age_seconds", "stale",
    "never_started"}` - `never_started` (no heartbeat file at all) is
    kept distinct from `stale` (a heartbeat exists but is older than
    `max_age_seconds`) so a caller can tell "this deployment never had
    a position_monitor running" apart from "it WAS running and died."
    Never raises on a missing/corrupt file."""
    path = _heartbeat_path(config)
    reference = now or datetime.now(timezone.utc)
    if not path.exists():
        return {"last_heartbeat_at": None, "age_seconds": None, "stale": False, "never_started": True}

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        last_heartbeat_at = data.get("last_heartbeat_at")
        last_dt = datetime.fromisoformat(last_heartbeat_at)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"last_heartbeat_at": None, "age_seconds": None, "stale": True, "never_started": False}

    age_seconds = (reference - last_dt).total_seconds()
    return {
        "last_heartbeat_at": last_heartbeat_at,
        "age_seconds": round(age_seconds, 1),
        "stale": age_seconds > max_age_seconds,
        "never_started": False,
    }


def send_stale_heartbeat_alert(config: dict[str, Any], logger: logging.Logger, status: dict[str, Any]) -> bool:
    """Best-effort Telegram alert, mirroring `run_health.send_failure_
    alert()`'s exact pattern - never raises, since the alerting path
    itself must never mask the condition it's reporting on."""
    import os

    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.warning("Stale position_monitor heartbeat alert not sent: Telegram not configured.")
            return False
        from .. import telegram_bot
        from ..utils import redact_secrets

        text = (
            "ALERT: position_monitor heartbeat is STALE.\n"
            f"Last heartbeat: {status['last_heartbeat_at']} ({status['age_seconds']}s ago)\n"
            "The process may have crashed. If a launchd KeepAlive agent is configured it should "
            "restart it automatically - verify with `launchctl list | grep aiquantresearchbot`.\n"
            "New entries are frozen while this process is down (see circuit_breaker BROKER_DISCONNECTED)."
        )
        return telegram_bot.send_telegram_message(token, chat_id, redact_secrets(text), logger)
    except Exception as exc:  # noqa: BLE001 - alerting itself must never raise
        logger.error("Failed to send stale-heartbeat alert: %s", exc)
        return False


def main() -> int:
    """`python -m src.execution.watchdog` - prints heartbeat status and
    sends a Telegram alert if stale. Meant to be scheduled
    independently from position_monitor itself (e.g. a launchd
    `StartInterval` job - see README.md) since a crashed process
    obviously cannot alert about its own death. Read-only otherwise -
    never touches a broker, never places an order."""
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="watchdog.log")

    status = heartbeat_status(config)
    if status["never_started"]:
        print("position_monitor heartbeat: never recorded (not running, or not yet reached its first tick).")
        return 0
    if status["stale"]:
        print(f"position_monitor heartbeat: STALE - last seen {status['last_heartbeat_at']} ({status['age_seconds']}s ago).")
        send_stale_heartbeat_alert(config, logger, status)
        return 1
    print(f"position_monitor heartbeat: OK - last seen {status['last_heartbeat_at']} ({status['age_seconds']}s ago).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
