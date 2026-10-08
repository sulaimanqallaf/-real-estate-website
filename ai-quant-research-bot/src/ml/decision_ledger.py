"""Immutable decision + outcome ledger (GitHub Issue #1 P1).

One row per candidate the research pipeline ever classified for execution
- not only ones that became trades (`decision` can be AUTO_EXECUTE,
REQUIRE_APPROVAL, WATCH_ONLY, or REJECT) - recording every feature/
decision field known AT THAT TIME (`record_decision()`, called once,
never backfilled with hindsight), later updated EXACTLY ONCE with the
actual outcome once it resolves (`record_outcome()`, matched by
`trade_id`). A candidate that was blocked or never approved keeps
`outcome_status IS NULL` forever - that absence IS the "missed/blocked"
signal the analysis side reads, not a value to fill in later.

SQLite, not the JSONL append-only shape the execution journal uses
(`order_manager.ExecutionJournal`): this module exists specifically so
the learning pipeline can run real analytical queries - group by
strategy/regime/ticker/volatility, filter a chronological window for a
purged/embargoed retraining split - which a flat JSONL file can't do
without reading and parsing the entire file every time. The execution
journal has no such need (it only ever replays itself in full at process
startup) and stays exactly as it is.

**Never auto-retrains, never adjusts a safety limit.** This module only
records and queries. `src/ml/trainer.py` (fully separate, manual) and
`src/ml/retrain_scheduler.py` (scheduled, but still only ever producing a
CHALLENGER - see that module) are what consume this data.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    trade_id TEXT,
    as_of TEXT NOT NULL,
    report_date TEXT,
    ticker TEXT NOT NULL,
    strategy TEXT,
    regime TEXT,
    signal_score INTEGER,
    quant_score REAL,
    ml_confidence TEXT,
    calibrated_probability REAL,
    model_horizon TEXT,
    decision TEXT NOT NULL,
    decision_reasons TEXT,
    signal_entry_price REAL,
    stop_loss REAL,
    target_price REAL,
    planned_shares REAL,
    dollar_risk REAL,
    provenance TEXT,
    outcome_status TEXT,
    actual_entry_price REAL,
    actual_exit_price REAL,
    exit_reason TEXT,
    exited_at TEXT,
    pnl_dollars REAL,
    pnl_pct REAL,
    slippage_pct REAL,
    commission REAL,
    outcome_recorded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_decisions_strategy ON decisions(strategy);
CREATE INDEX IF NOT EXISTS idx_decisions_trade_id ON decisions(trade_id);
CREATE INDEX IF NOT EXISTS idx_decisions_as_of ON decisions(as_of);
CREATE INDEX IF NOT EXISTS idx_decisions_regime ON decisions(regime);
"""

OUTCOME_NOT_TRADED = "NOT_TRADED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_DB_FILENAME = "decision_ledger.db"


def resolve_db_path(config: dict[str, Any]) -> Path:
    """The decision-ledger database path for a given run `config` -
    ALWAYS colocated with the SAME journal directory every other per-run
    artifact (`paper_trades.csv`, `pending_approvals.json`, the execution
    journal) already uses (`resolve_path(config.data.journal_dir)`),
    exactly like `paper_trades._paper_trades_path()`. Deliberately has NO
    separate `ml.decision_ledger_path`-style override: a second,
    independent path config would let a test (or a real deployment) that
    only redirects `data.journal_dir` - the established, universal
    isolation mechanism this whole codebase's test suite already relies
    on - silently miss this one and keep writing into the real repo's
    `data/ml/` directory. Falls back to the literal default only when
    even `data.journal_dir` is missing (a genuinely minimal config that
    never touches anything journal-related at all)."""
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


def record_decision(
    db_path: str | Path,
    ticker: str,
    decision: str,
    report_date: str | None = None,
    strategy: str | None = None,
    regime: str | None = None,
    signal_score: int | None = None,
    quant_score: float | None = None,
    ml_confidence: str | None = None,
    calibrated_probability: float | None = None,
    model_horizon: str | None = None,
    reasons: list[str] | None = None,
    signal_entry_price: float | None = None,
    stop_loss: float | None = None,
    target_price: float | None = None,
    planned_shares: float | None = None,
    dollar_risk: float | None = None,
    trade_id: str | None = None,
    as_of: str | None = None,
) -> str:
    """Records one candidate's decision snapshot. Returns the new row's
    `decision_id`. Every field here must be known AT DECISION TIME -
    never pass anything computed from a later bar/fill."""
    decision_id = f"decision_{uuid.uuid4().hex[:16]}"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO decisions (
                decision_id, trade_id, as_of, report_date, ticker, strategy, regime,
                signal_score, quant_score, ml_confidence, calibrated_probability, model_horizon,
                decision, decision_reasons, signal_entry_price, stop_loss, target_price,
                planned_shares, dollar_risk
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id, trade_id, as_of or _now_iso(), report_date, ticker, strategy, regime,
                signal_score, quant_score, ml_confidence, calibrated_probability, model_horizon,
                decision, "; ".join(reasons) if reasons else None, signal_entry_price, stop_loss, target_price,
                planned_shares, dollar_risk,
            ),
        )
    return decision_id


def record_outcome(
    db_path: str | Path,
    trade_id: str,
    outcome_status: str,
    exit_reason: str | None = None,
    exited_at: str | None = None,
    pnl_dollars: float | None = None,
    pnl_pct: float | None = None,
    actual_entry_price: float | None = None,
    actual_exit_price: float | None = None,
    commission: float | None = None,
    provenance: str | None = None,
    as_of: str | None = None,
) -> bool:
    """Updates the decision row matching `trade_id` with its actual,
    now-final outcome - exactly once in the normal case (a row whose
    `outcome_status` is already set is left alone rather than
    overwritten, so a duplicate exit-fill poll can never corrupt an
    already-recorded real outcome with a second, possibly different,
    value). Returns True if a row was updated, False if no matching
    (outcome-less) row exists for this trade_id - never raises; a ledger
    write failing must never block the actual trade closure it's
    recording."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT decision_id, signal_entry_price FROM decisions WHERE trade_id = ? AND outcome_status IS NULL ORDER BY as_of DESC LIMIT 1",
            (trade_id,),
        ).fetchone()
        if row is None:
            return False

        signal_entry_price = row["signal_entry_price"]
        slippage_pct = None
        if actual_entry_price is not None and signal_entry_price:
            slippage_pct = (actual_entry_price - signal_entry_price) / signal_entry_price

        conn.execute(
            """UPDATE decisions SET
                outcome_status = ?, exit_reason = ?, exited_at = ?, pnl_dollars = ?, pnl_pct = ?,
                actual_entry_price = ?, actual_exit_price = ?, commission = ?, provenance = ?,
                slippage_pct = ?, outcome_recorded_at = ?
            WHERE decision_id = ?""",
            (
                outcome_status, exit_reason, exited_at, pnl_dollars, pnl_pct,
                actual_entry_price, actual_exit_price, commission, provenance,
                slippage_pct, as_of or _now_iso(), row["decision_id"],
            ),
        )
    return True


def query_decisions(
    db_path: str | Path,
    since: str | None = None,
    until: str | None = None,
    strategy: str | None = None,
    ticker: str | None = None,
    regime: str | None = None,
    only_with_outcome: bool = False,
) -> list[dict[str, Any]]:
    """Returns matching rows, oldest first, as plain dicts - never raises
    on an empty/missing database (returns `[]`, same as every other
    "Data Unavailable, never fabricated" source in this codebase)."""
    if not Path(db_path).exists():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append("as_of >= ?")
        params.append(since)
    if until is not None:
        clauses.append("as_of <= ?")
        params.append(until)
    if strategy is not None:
        clauses.append("strategy = ?")
        params.append(strategy)
    if ticker is not None:
        clauses.append("ticker = ?")
        params.append(ticker)
    if regime is not None:
        clauses.append("regime = ?")
        params.append(regime)
    if only_with_outcome:
        clauses.append("outcome_status IS NOT NULL")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM decisions {where} ORDER BY as_of ASC", params).fetchall()
    return [dict(r) for r in rows]
