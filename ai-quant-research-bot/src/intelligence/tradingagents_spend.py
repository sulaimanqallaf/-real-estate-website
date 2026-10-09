"""Concurrency-safe daily + monthly API-spend ledger for the TradingAgents
adapter (GitHub Issue #1: "add hard daily and monthly API-spend limits,
including concurrency-safe reservations").

**Reserve-then-reconcile pattern.** Before any subprocess call that might
spend real money, `reserve()` atomically checks the running daily+monthly
total (already-committed actual spend, plus anything currently reserved
by another in-flight call) against both caps and, if there's room,
inserts a `'reserved'` row for a conservative per-call ceiling
(`max_cost_per_call_usd`) - using SQLite's `BEGIN IMMEDIATE` to acquire
the write lock up front, so two processes (or two tickers processed
concurrently in a future version) racing to reserve against the SAME
database file can never both succeed past the limit; the second one
blocks until the first's transaction commits, then re-reads the
now-updated total. After the real call finishes, `commit()` replaces the
reservation with the ACTUAL cost (which may be less - or, conservatively,
exactly the reserved ceiling when the actual cost is unknown - than what
was reserved); `release()` removes a reservation entirely for a call that
never actually ran (e.g. it could not even start the subprocess).
"""

from __future__ import annotations

import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend_ledger (
    entry_id TEXT PRIMARY KEY,
    call_date TEXT NOT NULL,
    call_month TEXT NOT NULL,
    reserved_usd REAL NOT NULL,
    actual_usd REAL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spend_ledger_date ON spend_ledger(call_date);
CREATE INDEX IF NOT EXISTS idx_spend_ledger_month ON spend_ledger(call_month);
"""


def resolve_ledger_path(config: dict[str, Any]) -> Path:
    """Colocated with `data.journal_dir`, same convention used throughout
    this codebase - deliberately no separate, independently-configurable
    path key (see `ml/decision_ledger.resolve_db_path()`'s docstring for
    the test-pollution footgun that convention avoids)."""
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / "tradingagents_spend_ledger.db"
    return resolve_path("data/ml/tradingagents_spend_ledger.db")


# Retry budget for the connect+PRAGMA+schema-init+BEGIN IMMEDIATE sequence
# below. Root cause of the previously-observed flaky "database is locked"
# (sqlite3.OperationalError from inside `PRAGMA journal_mode=WAL`): on a
# brand-new ledger file, converting to WAL mode itself briefly requires an
# exclusive filesystem lock, and two processes/threads opening the SAME
# not-yet-WAL file for the first time at the same instant can race for
# that conversion in a way `connect(timeout=...)`'s busy-wait does not
# fully absorb (that timeout governs waiting on the normal SQLITE_BUSY
# path once the DB is already in WAL mode, not this one-time conversion).
# A bounded retry-with-backoff on a FRESH connection (the failed
# connection cannot be reused - its pragma/schema state is undefined)
# makes this self-healing instead of occasionally propagating a spurious
# failure up through `reserve()`/`commit()`/`release()`.
_MAX_LOCK_RETRY_ATTEMPTS = 8
_RETRY_BASE_DELAY_SECONDS = 0.05


def _is_lock_contention_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


@contextmanager
def _connect_immediate(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """A connection that opens an explicit `BEGIN IMMEDIATE` transaction -
    see module docstring. Schema creation happens BEFORE the transaction
    starts (DDL inside a manually-managed transaction is needlessly
    fragile across sqlite3 driver versions, and offers no concurrency
    benefit here). Retries the whole connect+PRAGMA+schema+BEGIN sequence
    with jittered backoff on lock contention - see `_MAX_LOCK_RETRY_
    ATTEMPTS`'s comment above for why this is needed even with a
    connection-level `timeout`."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn: sqlite3.Connection | None = None
    for attempt in range(_MAX_LOCK_RETRY_ATTEMPTS):
        conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            conn.close()
            conn = None
            is_last_attempt = attempt == _MAX_LOCK_RETRY_ATTEMPTS - 1
            if not _is_lock_contention_error(exc) or is_last_attempt:
                raise
            time.sleep(_RETRY_BASE_DELAY_SECONDS * (2**attempt) + random.uniform(0, 0.02))

    assert conn is not None  # loop above either `break`s with conn set, or raises
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _committed_or_reserved_total(conn: sqlite3.Connection, column: str, value: str) -> float:
    row = conn.execute(
        f"SELECT COALESCE(SUM(CASE WHEN status = 'committed' THEN actual_usd ELSE reserved_usd END), 0) AS total "
        f"FROM spend_ledger WHERE {column} = ? AND status IN ('reserved', 'committed')",
        (value,),
    ).fetchone()
    return row["total"]


def reserve(db_path: str | Path, entry_id: str, estimate_usd: float, daily_limit_usd: float, monthly_limit_usd: float, now: datetime) -> bool:
    """Atomically reserves `estimate_usd` against both caps. Returns
    `False` (nothing reserved - the caller must skip the real call
    entirely) if doing so would exceed either the daily or the monthly
    limit."""
    call_date = now.strftime("%Y-%m-%d")
    call_month = now.strftime("%Y-%m")
    with _connect_immediate(db_path) as conn:
        if _committed_or_reserved_total(conn, "call_date", call_date) + estimate_usd > daily_limit_usd:
            return False
        if _committed_or_reserved_total(conn, "call_month", call_month) + estimate_usd > monthly_limit_usd:
            return False
        conn.execute(
            "INSERT INTO spend_ledger (entry_id, call_date, call_month, reserved_usd, actual_usd, status, created_at) "
            "VALUES (?, ?, ?, ?, NULL, 'reserved', ?)",
            (entry_id, call_date, call_month, estimate_usd, now.isoformat()),
        )
    return True


def commit(db_path: str | Path, entry_id: str, actual_usd: float) -> None:
    with _connect_immediate(db_path) as conn:
        conn.execute("UPDATE spend_ledger SET status = 'committed', actual_usd = ? WHERE entry_id = ?", (actual_usd, entry_id))


def release(db_path: str | Path, entry_id: str) -> None:
    with _connect_immediate(db_path) as conn:
        conn.execute("UPDATE spend_ledger SET status = 'released' WHERE entry_id = ?", (entry_id,))


def spent_today_and_month(db_path: str | Path, now: datetime) -> dict[str, float]:
    """The CAP-ENFORCEMENT total - committed (actual) spend PLUS any
    still-outstanding reservation, exactly what `reserve()` itself checks
    against `daily_limit_usd`/`monthly_limit_usd`. This number is
    deliberately conservative (it's what keeps the cap honest against an
    in-flight call whose real cost isn't known yet) and must NOT be
    read as "actual dollars billed so far" - see `spend_breakdown()`
    below for the committed/reserved split a health report needs to
    report that distinction honestly."""
    call_date = now.strftime("%Y-%m-%d")
    call_month = now.strftime("%Y-%m")
    with _connect_immediate(db_path) as conn:
        return {
            "today_usd": round(_committed_or_reserved_total(conn, "call_date", call_date), 6),
            "month_usd": round(_committed_or_reserved_total(conn, "call_month", call_month), 6),
        }


def _committed_total(conn: sqlite3.Connection, column: str, value: str) -> float:
    row = conn.execute(
        f"SELECT COALESCE(SUM(actual_usd), 0) AS total FROM spend_ledger "
        f"WHERE {column} = ? AND status = 'committed'",
        (value,),
    ).fetchone()
    return row["total"]


def _reserved_total(conn: sqlite3.Connection, column: str, value: str) -> float:
    row = conn.execute(
        f"SELECT COALESCE(SUM(reserved_usd), 0) AS total FROM spend_ledger "
        f"WHERE {column} = ? AND status = 'reserved'",
        (value,),
    ).fetchone()
    return row["total"]


def spend_breakdown(db_path: str | Path, now: datetime) -> dict[str, float]:
    """Health-report-facing view: committed (actual, real `actual_usd`
    from a call that finished and had its real cost recorded via
    `commit()`) kept SEPARATE from reserved (the conservative ceiling of
    a call that is still in flight, or - see `outstanding_reservation_
    count` below - never got `commit()`ted or `release()`d, e.g. because
    the process crashed mid-call). Never blends the two into one number
    presented as "actual spend" - that blended number exists (`spent_
    today_and_month()`) specifically for cap enforcement, which needs to
    be conservative, not for reporting what was actually billed."""
    call_date = now.strftime("%Y-%m-%d")
    call_month = now.strftime("%Y-%m")
    with _connect_immediate(db_path) as conn:
        outstanding_row = conn.execute(
            "SELECT COUNT(*) AS n FROM spend_ledger WHERE status = 'reserved'"
        ).fetchone()
        return {
            "committed_today_usd": round(_committed_total(conn, "call_date", call_date), 6),
            "committed_month_usd": round(_committed_total(conn, "call_month", call_month), 6),
            "reserved_today_usd": round(_reserved_total(conn, "call_date", call_date), 6),
            "reserved_month_usd": round(_reserved_total(conn, "call_month", call_month), 6),
            "outstanding_reservation_count": outstanding_row["n"],
        }
