"""Persistent memory of every multi-agent research assessment this
pipeline has ever produced (TradingAgents-inspired `memory/log.py`), kept
in a dedicated SQLite database - **deliberately separate from
`ml/decision_ledger.py`.** GitHub Issue #1's own integration plan point 7:
"Keep two distinct learning loops... Never mix alpha on hypothetical
recommendations with realized trade P&L." This database only ever
stores a research OPINION and, once resolved, a textual reflection on it
- `evaluation.py` joins it against the real decision ledger (by ticker +
report_date) read-only, at query time, rather than merging the two data
models.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .schemas import AgentResearchAssessment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assessments (
    assessment_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    report_date TEXT NOT NULL,
    as_of TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    thesis TEXT,
    bull_points TEXT,
    bear_points TEXT,
    risk_notes TEXT,
    analyst_opinions TEXT,
    data_provenance TEXT,
    quant_agent_decision TEXT,
    reflection_note TEXT,
    duration_ms REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessments_ticker ON assessments(ticker);
CREATE INDEX IF NOT EXISTS idx_assessments_report_date ON assessments(report_date);
"""

_DB_FILENAME = "agent_research_memory.db"


def resolve_db_path(config: dict[str, Any]) -> Path:
    """Same colocation convention as `ml/decision_ledger.resolve_db_path()`
    and `ml/model_events.resolve_log_path()` - ALWAYS under
    `data.journal_dir`, never an independently-configurable key, to avoid
    the exact test-pollution footgun this codebase has already hit (and
    fixed) three times."""
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / _DB_FILENAME
    return resolve_path(f"data/ml/{_DB_FILENAME}")


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


def record_assessment(db_path: str | Path, assessment: AgentResearchAssessment) -> str:
    """Appends one new row - this table is never updated in place for the
    assessment itself; `record_reflection()` is the one field allowed to
    be set after the fact, exactly like `ml/decision_ledger.record_outcome()`
    is the one post-hoc update allowed there."""
    assessment_id = str(uuid.uuid4())
    payload = assessment.to_dict()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO assessments (
                assessment_id, ticker, report_date, as_of, action, confidence, thesis,
                bull_points, bear_points, risk_notes, analyst_opinions, data_provenance,
                quant_agent_decision, reflection_note, duration_ms, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id, payload["ticker"], payload["report_date"], payload["as_of"],
                payload["action"], payload["confidence"], payload["thesis"],
                json.dumps(payload["bull_points"]), json.dumps(payload["bear_points"]), json.dumps(payload["risk_notes"]),
                json.dumps(payload["analyst_opinions"]), json.dumps(payload["data_provenance"]),
                payload["quant_agent_decision"], payload["reflection_note"], payload["duration_ms"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return assessment_id


def record_reflection(db_path: str | Path, assessment_id: str, reflection_note: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("UPDATE assessments SET reflection_note = ? WHERE assessment_id = ?", (reflection_note, assessment_id))


def query_assessments(
    db_path: str | Path, ticker: str | None = None, since: str | None = None, until: str | None = None
) -> list[dict[str, Any]]:
    """Returns matching rows, oldest first - `[]` for a missing database,
    never raises."""
    if not Path(db_path).exists():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if ticker is not None:
        clauses.append("ticker = ?")
        params.append(ticker)
    if since is not None:
        clauses.append("report_date >= ?")
        params.append(since)
    if until is not None:
        clauses.append("report_date <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM assessments {where} ORDER BY report_date ASC", params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        for key in ("bull_points", "bear_points", "risk_notes", "analyst_opinions", "data_provenance"):
            d[key] = json.loads(d[key]) if d[key] else None
        results.append(d)
    return results


def fetch_recent_reflections(db_path: str | Path, ticker: str, limit: int = 3) -> list[str]:
    """The reflection context TradingAgents' own `memory/log.py` +
    `reflection.py` pair would feed back into a future LLM prompt - here,
    since no LLM is called, these are instead surfaced directly as a
    caveat note in the next assessment's `thesis` (see pipeline.py)."""
    if not Path(db_path).exists():
        return []
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT reflection_note FROM assessments WHERE ticker = ? AND reflection_note IS NOT NULL ORDER BY report_date DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
    return [r["reflection_note"] for r in rows]
