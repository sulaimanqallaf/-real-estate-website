"""Fault-injection tests: real SQLite database errors (Sprint 3,
Reliability milestone: "test... database errors").

Found via this testing, not assumed: `decision_ledger.record_outcome()`'s
own docstring explicitly promised "never raises; a ledger write failing
must never block the actual trade closure it's recording" - but the
code had no try/except at all, so a genuine `sqlite3.OperationalError`
(e.g. "database is locked") would have propagated straight out of it.
In the one real call site (`execution/learning_feedback.py`'s
`check_exit_fills()`), that's already caught by an outer `safe_run()`
wrapper, so production behavior was never actually unsafe - but the
function did not honor its OWN documented contract in isolation, which
is exactly the kind of latent gap that matters if anything ever calls
it directly without that wrapper. Fixed directly in decision_ledger.py
(wrapped in `except sqlite3.Error: return False` - the same `False`
already used for "no matching row," so callers don't need to
distinguish the two)."""

import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import decision_ledger

LOGGER = logging.getLogger("test")


def _seed_open_decision(db_path, trade_id: str) -> None:
    decision_ledger.record_decision(
        db_path, ticker="AAPL", decision="AUTO_EXECUTE", trade_id=trade_id, signal_entry_price=100.0,
    )


def test_record_outcome_recovers_from_a_real_transient_lock(tmp_path):
    """A REAL second SQLite connection holds an exclusive write lock for
    a brief moment (not mocked) - record_outcome's own `timeout=10`
    busy-handler should make it wait out that real, brief lock and
    still succeed, rather than failing on first contact."""
    db_path = tmp_path / "decision_ledger.db"
    _seed_open_decision(db_path, "trade_1")

    blocker_conn = sqlite3.connect(str(db_path), timeout=1, check_same_thread=False)
    blocker_conn.execute("BEGIN EXCLUSIVE")

    def _release_shortly():
        time.sleep(0.3)
        blocker_conn.commit()
        blocker_conn.close()

    threading.Thread(target=_release_shortly, daemon=True).start()

    result = decision_ledger.record_outcome(db_path, trade_id="trade_1", outcome_status="CLOSED", pnl_pct=1.5)

    assert result is True
    rows = decision_ledger.query_decisions(db_path, only_with_outcome=True)
    assert rows[0]["outcome_status"] == "CLOSED"


def test_record_outcome_returns_false_never_raises_when_the_write_itself_fails(tmp_path, monkeypatch):
    """The fix, proven directly: a persistent sqlite3.Error during the
    UPDATE itself must come back as False, never propagate - the exact
    contract record_outcome's own docstring claims."""
    db_path = tmp_path / "decision_ledger.db"
    _seed_open_decision(db_path, "trade_1")

    import src.ml.decision_ledger as dl_module

    real_connect = sqlite3.connect

    class _FailingConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self.row_factory = None

        def execute(self, sql, params=()):
            if sql.strip().upper().startswith("UPDATE"):
                raise sqlite3.OperationalError("database is locked")
            return self._real.execute(sql, params)

        def executescript(self, sql):
            return self._real.executescript(sql)

        def commit(self):
            return self._real.commit()

        def close(self):
            return self._real.close()

        def __setattr__(self, key, value):
            object.__setattr__(self, key, value)
            if key == "row_factory" and hasattr(self, "_real"):
                self._real.row_factory = value

    def _fake_connect(path, timeout=10):
        return _FailingConn(real_connect(path, timeout=timeout))

    monkeypatch.setattr(dl_module.sqlite3, "connect", _fake_connect)

    result = decision_ledger.record_outcome(db_path, trade_id="trade_1", outcome_status="CLOSED", pnl_pct=1.5)

    assert result is False


def test_record_outcome_false_for_a_failed_write_is_indistinguishable_from_no_matching_row(tmp_path):
    """Documents the deliberate design choice: both "nothing to update"
    and "the update failed" return the identical False - callers (see
    learning_feedback.check_exit_fills) treat them the same way
    (retry the exit-fill poll next tick), so this is a feature, not an
    ambiguity bug."""
    db_path = tmp_path / "decision_ledger.db"
    # No seeded row at all - "no matching row" case.
    result = decision_ledger.record_outcome(db_path, trade_id="nonexistent", outcome_status="CLOSED")
    assert result is False
