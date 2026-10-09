"""Lightweight research-hypothesis ledger (Phase 2, RD-Agent concept) -
a plain log of what was tried in `sandbox.py` and what happened, kept
in its OWN database file (`research_hypotheses.db`, default under
`data/research/` - never `decision_ledger.db`/`paper_trades.csv`) so a
research experiment can never be mistaken for, or accidentally queried
alongside, a real trading decision.

**There is no `promote()` function anywhere in this module, and never
will be** - recording a result here is the end of this module's
responsibility. Turning a promising hypothesis into an actual strategy
change is a deliberate, separate, human-reviewed step outside this
sandbox entirely (per the sprint's explicit rule: "Do not permit
RD-Agent or other agents to promote themselves into production
trading").
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    row_id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    description TEXT,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    data_provenance TEXT NOT NULL,
    config_overrides_json TEXT,
    trade_count INTEGER,
    stats_json TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hypotheses_hypothesis_id ON hypotheses(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_hypotheses_strategy ON hypotheses(strategy_name);
"""

_DB_FILENAME = "research_hypotheses.db"


def default_db_path(config: dict[str, Any] | None = None) -> Path:
    """Deliberately NOT derived from `config["data"]["journal_dir"]` (the
    path `decision_ledger.py`/`paper_trades.py` use) - a research
    experiment lives in its own location
    (`data/research/research_hypotheses.db` by default, or
    `config["research"]["hypothesis_db_path"]` if a caller sets one)
    specifically so it is structurally impossible to write a research
    row into the real trading ledger by passing the same config
    through both."""
    from ..utils import resolve_path

    override = (config or {}).get("research", {}).get("hypothesis_db_path")
    if override:
        return resolve_path(override)
    return resolve_path(f"data/research/{_DB_FILENAME}")


@contextmanager
def _connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_result(db_path: str | Path, result: dict[str, Any]) -> str:
    """Appends one `sandbox.run_hypothesis` result (or `None` handled by
    the caller before calling this - this function requires a real
    result dict). Never updates or overwrites an existing row - every
    call is a new, immutable entry, even for a hypothesis_id tested
    more than once (e.g. against different symbols or data)."""
    row_id = f"hyp_{uuid.uuid4().hex[:16]}"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO hypotheses (
                row_id, hypothesis_id, description, strategy_name, symbol, data_provenance,
                config_overrides_json, trade_count, stats_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id, result["hypothesis_id"], result.get("description"), result["strategy_name"], result["symbol"],
                result["data_provenance"], json.dumps(result.get("config_overrides") or {}),
                result.get("trade_count"), json.dumps(result.get("stats")) if result.get("stats") is not None else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return row_id


def query_results(db_path: str | Path, hypothesis_id: str | None = None, strategy_name: str | None = None) -> list[dict[str, Any]]:
    """Returns matching rows, oldest first, as plain dicts with
    `config_overrides`/`stats` decoded back from JSON - never raises on
    a missing/empty database (returns `[]`)."""
    if not Path(db_path).exists():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if hypothesis_id is not None:
        clauses.append("hypothesis_id = ?")
        params.append(hypothesis_id)
    if strategy_name is not None:
        clauses.append("strategy_name = ?")
        params.append(strategy_name)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM hypotheses {where} ORDER BY recorded_at ASC", params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["config_overrides"] = json.loads(d.pop("config_overrides_json") or "{}")
        stats_json = d.pop("stats_json")
        d["stats"] = json.loads(stats_json) if stats_json else None
        results.append(d)
    return results
