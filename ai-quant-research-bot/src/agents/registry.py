"""Durable job-status registry for Sprint 4's new autonomous agents
(Priority 6: "durable job scheduling"; Priority 5: "show all...agents
with genuine activity, job status, progress, errors, findings and
results").

One JSON file PER AGENT (`data/journal/agents/{agent_name}_status.json`,
override the parent directory via `config["data"]["journal_dir"]`),
holding that agent's MOST RECENT run only - this is a liveness/status
registry, not a run history (each agent's own findings store, e.g.
`hypothesis_ledger.py`, is where a durable history of its actual
output belongs). One file per agent, rather than one shared file for
every agent, is deliberate: a write that's interrupted mid-flight (a
crash, a disk error) can only ever corrupt ONE agent's status file,
never every other agent's at the same time - the same reasoning
`circuit_breaker.py`'s halt-state file and
`execution/reconciliation.py`'s reconciliation-status file already use
for their own single pieces of durable state.

Writes are atomic (write to a sibling temp file, then `os.replace()`)
so a crash mid-write leaves either the old, complete file or the new,
complete file - never a half-written one.

Never fabricates a status: `read_status()` returns `None` for an agent
that has never run (no file) or whose file can't be read/parsed -
callers must treat `None` as "unknown," never as "idle" or "ok."
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import resolve_path

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

_AGENTS_SUBDIR = "agents"


@dataclass
class AgentRun:
    agent_name: str
    status: str
    started_at: str
    finished_at: str | None = None
    summary: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _agents_dir(config: dict[str, Any]) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir", "data/journal")
    return resolve_path(journal_dir) / _AGENTS_SUBDIR


def _status_path(config: dict[str, Any], agent_name: str) -> Path:
    return _agents_dir(config) / f"{agent_name}_status.json"


def _write(config: dict[str, Any], agent_name: str, run: AgentRun) -> None:
    out_dir = _agents_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _status_path(config, agent_name)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(asdict(run), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def start_run(config: dict[str, Any], agent_name: str, extra: dict[str, Any] | None = None, now: datetime | None = None) -> None:
    """Call at the START of an agent's job - marks it RUNNING. This
    overwrites any previous record (the registry only ever tracks the
    LATEST run) - a crashed agent whose last recorded status is
    RUNNING forever (never followed by a finish_run()) is exactly how
    a stuck/crashed job is detected, the same way a stale heartbeat is."""
    _write(config, agent_name, AgentRun(agent_name=agent_name, status=STATUS_RUNNING, started_at=_now_iso(now), extra=extra or {}))


def finish_run(
    config: dict[str, Any],
    agent_name: str,
    status: str,
    summary: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Call at the END of an agent's job. `status` must be `SUCCESS` or
    `FAILED` - never `RUNNING` (that's what `start_run()` is for)."""
    if status not in (STATUS_SUCCESS, STATUS_FAILED):
        raise ValueError(f"finish_run status must be SUCCESS or FAILED, got {status!r}")
    existing = read_status(config, agent_name)
    started_at = (existing or {}).get("started_at") or _now_iso(now)
    merged_extra = dict((existing or {}).get("extra") or {})
    merged_extra.update(extra or {})
    _write(
        config, agent_name,
        AgentRun(agent_name=agent_name, status=status, started_at=started_at, finished_at=_now_iso(now), summary=summary, error=error, extra=merged_extra),
    )


def read_status(config: dict[str, Any], agent_name: str) -> dict[str, Any] | None:
    path = _status_path(config, agent_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_known_agents(config: dict[str, Any]) -> list[str]:
    """Names of every agent that has ever recorded a status - derived
    from which `*_status.json` files exist, never from a hardcoded
    list, so a brand-new agent shows up here the first time it runs."""
    agents_dir = _agents_dir(config)
    if not agents_dir.exists():
        return []
    return sorted(p.name[: -len("_status.json")] for p in agents_dir.glob("*_status.json"))


def all_statuses(config: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    return {name: read_status(config, name) for name in list_known_agents(config)}
