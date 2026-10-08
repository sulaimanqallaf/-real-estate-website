"""Append-only audit log of model lifecycle events (promotions,
rollbacks) - GitHub Issue #1 P0's "keep append-only audit events" applied
to the learning pipeline, parallel to `order_manager.ExecutionJournal`
for the execution side. JSONL, colocated with the decision ledger (same
`data.journal_dir` convention - see `decision_ledger.resolve_db_path()`).

This is what the weekly Telegram report (`src/ml/weekly_report.py`) reads
to say what actually changed, with the real reason, rather than only
being able to infer "a model's status is now X" with no explanation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENT_PROMOTED = "PROMOTED"
EVENT_ROLLED_BACK = "ROLLED_BACK"

_FILENAME = "model_events.jsonl"


def resolve_log_path(config: dict[str, Any]) -> Path:
    """Same convention as `decision_ledger.resolve_db_path()` - always
    colocated with `data.journal_dir`, no separate override, so
    redirecting `journal_dir` is always enough to isolate this too."""
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / _FILENAME
    return resolve_path(f"data/ml/{_FILENAME}")


def record_event(config: dict[str, Any], event_type: str, model_id: str, model_type: str | None = None, reason: str | None = None, **extra: Any) -> None:
    """Appends one event. Never raises on a write failure in the sense
    that matters to callers - callers already wrap this in `safe_run()`,
    same discipline as every other side-channel audit write in this
    codebase (the decision ledger, lifecycle Telegram notices)."""
    path = resolve_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "event": event_type, "model_id": model_id, "model_type": model_type, "reason": reason,
        "recorded_at": datetime.now(timezone.utc).isoformat(), **extra,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_events(config: dict[str, Any], since: str | None = None) -> list[dict[str, Any]]:
    """Returns every event, oldest first, optionally filtered to
    `recorded_at >= since` (an ISO timestamp string - plain lexical
    comparison works since every timestamp here is written in the same
    ISO-8601 UTC form). Returns `[]` for a missing log file - never
    raises."""
    path = resolve_log_path(config)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if since is None or row.get("recorded_at", "") >= since:
                rows.append(row)
    return rows
