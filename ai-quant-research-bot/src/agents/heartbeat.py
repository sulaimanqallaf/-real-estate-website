"""Generic crash-recovery heartbeat for Sprint 4's new agents - the
exact same pattern `src/execution/watchdog.py` already established for
`position_monitor.py`, generalized across any `agent_name` rather than
hardcoded to one process. Sprint 3 only ever had one long-running
process needing this; Sprint 4 introduces three (Research Scholar,
News Agent, Strategy Lab), so this is written once, shared, instead of
copy-pasted three times.

A liveness signal for the PROCESS itself - written unconditionally at
the start of every work cycle, independent of whether that cycle's
actual work (a network fetch, a backtest) succeeds. Never fabricates
freshness: `heartbeat_status()` distinguishes "never started" from
"stale" from "ok," and never raises - a broken heartbeat file is
treated as `never_started`, not silently "ok."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import resolve_path

DEFAULT_MAX_AGE_SECONDS = 120 * 60  # 2 hours - these are daily/scheduled jobs, not a 15s poll loop like position_monitor
_AGENTS_SUBDIR = "agents"


def _heartbeat_path(config: dict[str, Any], agent_name: str) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir", "data/journal")
    return resolve_path(journal_dir) / _AGENTS_SUBDIR / f"{agent_name}_heartbeat.json"


def record_heartbeat(config: dict[str, Any], agent_name: str, extra: dict[str, Any] | None = None, now: datetime | None = None) -> None:
    """Never raises - a failure here must never crash the agent whose
    liveness it's trying to record."""
    try:
        path = _heartbeat_path(config, agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_heartbeat_at": (now or datetime.now(timezone.utc)).isoformat(), "extra": extra or {}}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def heartbeat_status(config: dict[str, Any], agent_name: str, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS, now: datetime | None = None) -> dict[str, Any]:
    path = _heartbeat_path(config, agent_name)
    if not path.exists():
        return {"last_heartbeat_at": None, "age_seconds": None, "stale": False, "never_started": True}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        last_heartbeat_at = data["last_heartbeat_at"]
        parsed = datetime.fromisoformat(last_heartbeat_at)
    except (OSError, ValueError, KeyError):
        return {"last_heartbeat_at": None, "age_seconds": None, "stale": False, "never_started": True}

    reference = now or datetime.now(timezone.utc)
    age_seconds = (reference - parsed).total_seconds()
    return {"last_heartbeat_at": last_heartbeat_at, "age_seconds": age_seconds, "stale": age_seconds > max_age_seconds, "never_started": False}
