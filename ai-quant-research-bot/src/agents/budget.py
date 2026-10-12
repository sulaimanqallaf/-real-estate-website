"""Daily call-count budget for Sprint 4's new agents' external API
calls (OpenAlex/arXiv/Semantic Scholar/SEC) - Priority 6: "cost
limits." These free APIs have no dollar cost, but they have real
daily/rate quotas worth respecting to stay a good citizen and avoid
the whole project's outbound IP getting rate-limited or blocked - a
budget is still the right abstraction even at $0, so this mirrors
`src.intelligence.tradingagents_spend`'s reserve-before-you-spend
shape without needing a dollar amount at all.

One JSON file per agent (`data/journal/agents/{agent_name}_budget.json`),
keyed by UTC calendar date so it resets automatically at midnight UTC
with no cron/cleanup job needed - a file from a previous day is simply
ignored (not deleted) the next time `check_and_increment()` runs for a
new date.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import resolve_path

_AGENTS_SUBDIR = "agents"


def _budget_path(config: dict[str, Any], agent_name: str) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir", "data/journal")
    return resolve_path(journal_dir) / _AGENTS_SUBDIR / f"{agent_name}_budget.json"


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).date().isoformat()


def _read(config: dict[str, Any], agent_name: str) -> dict[str, Any]:
    path = _budget_path(config, agent_name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(config: dict[str, Any], agent_name: str, data: dict[str, Any]) -> None:
    out_dir = _budget_path(config, agent_name).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _budget_path(config, agent_name)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def calls_used_today(config: dict[str, Any], agent_name: str, source: str, now: datetime | None = None) -> int:
    data = _read(config, agent_name)
    if data.get("date") != _today(now):
        return 0
    return int(data.get("counts", {}).get(source, 0))


def check_and_increment(config: dict[str, Any], agent_name: str, source: str, daily_limit: int, now: datetime | None = None) -> bool:
    """Returns `True` and increments the counter if `source`'s call
    count for TODAY is still under `daily_limit`; returns `False`
    (caller MUST NOT make the call) once the limit is reached.
    Resets automatically when the UTC date rolls over - no cleanup job
    needed, the previous day's file is simply superseded."""
    today = _today(now)
    data = _read(config, agent_name)
    if data.get("date") != today:
        data = {"date": today, "counts": {}}

    counts = data.setdefault("counts", {})
    used = int(counts.get(source, 0))
    if used >= daily_limit:
        return False

    counts[source] = used + 1
    _write(config, agent_name, data)
    return True


def remaining_today(config: dict[str, Any], agent_name: str, source: str, daily_limit: int, now: datetime | None = None) -> int:
    return max(0, daily_limit - calls_used_today(config, agent_name, source, now=now))
