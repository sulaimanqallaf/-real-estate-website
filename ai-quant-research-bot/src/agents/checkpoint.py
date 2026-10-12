"""Recovery checkpoints for Sprint 4's new agents (Priority 6:
"recovery checkpoints") - a resumable-state file so a long, paginated
fetch job (e.g. working through hundreds of OpenAlex/arXiv results)
can pick up where it left off after a crash or a kill, instead of
restarting from page 1 and re-spending its daily API budget (see
`budget.py`) on work it already did.

One JSON file per agent (`data/journal/agents/{agent_name}_checkpoint.json`).
The STATE itself is entirely the caller's own shape - this module only
persists/retrieves/clears it, atomically, and never fabricates a
checkpoint: `load_checkpoint()` returns `None` (never `{}`) when none
exists or the file is unreadable, so a caller can tell "nothing to
resume, start fresh" apart from "there was a checkpoint but it's
corrupt" if that distinction matters to it - today, both honestly mean
the same thing to every caller in this codebase: start fresh.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..utils import resolve_path

_AGENTS_SUBDIR = "agents"


def _checkpoint_path(config: dict[str, Any], agent_name: str) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir", "data/journal")
    return resolve_path(journal_dir) / _AGENTS_SUBDIR / f"{agent_name}_checkpoint.json"


def save_checkpoint(config: dict[str, Any], agent_name: str, state: dict[str, Any]) -> None:
    path = _checkpoint_path(config, agent_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp_path, path)


def load_checkpoint(config: dict[str, Any], agent_name: str) -> dict[str, Any] | None:
    path = _checkpoint_path(config, agent_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_checkpoint(config: dict[str, Any], agent_name: str) -> None:
    """Call once a job completes a full cycle successfully - a cleared
    checkpoint means the NEXT run starts fresh rather than resuming
    stale, already-finished work."""
    path = _checkpoint_path(config, agent_name)
    if path.exists():
        path.unlink()
