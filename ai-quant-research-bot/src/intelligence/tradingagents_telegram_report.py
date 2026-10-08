"""Safe, idempotent delivery of the cached TradingAgents preview to
Telegram (GitHub Issue #1 follow-up).

Builds directly on `tradingagents_preview.py`'s `format_preview()` - the
SAME rendering this project's real daily report already uses - and never
re-implements it. Kept in its own module (rather than inside
`tradingagents_preview.py`) so that module's own "never sends a Telegram
message" invariant and grep/AST-based safety tests stay exactly true;
only THIS module ever imports `telegram_bot`.

**Dry-run is the default and the only thing that happens without
`--send`.** Running this module with no flags prints the exact same
output `tradingagents_preview.py` would. `--send` is required, explicit,
and sends AT MOST once per distinct cached content (see the idempotency
key below) - never automatic, never triggered by `main.py`'s daily run
or any scheduled process; nothing in this codebase calls this module's
`send_cached_report()` except its own CLI.

Reads ONLY the existing cache - zero new LLM API calls, zero IBKR
orders, and (in dry-run, the default) zero Telegram sends. Shadow mode
and DRY_RUN execution are unaffected either way: this module has no
code path into `execution_policy.py`, `circuit_breaker.py`,
`portfolio_risk.py`, or any order-submission code - see
`tests/test_intelligence_tradingagents_safety.py`'s grep-based guardrail,
which also covers this file.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .. import telegram_bot
from . import tradingagents_preview

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (
    idempotency_key TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    chunk_count INTEGER NOT NULL
);
"""

_DB_FILENAME = "tradingagents_preview_sent.db"

# A safety margin under Telegram's own TELEGRAM_HARD_LIMIT_CHARS (4096) -
# telegram_bot.send_report()/chunk_message() clamp to the hard limit
# regardless, this just leaves headroom for the "[i/N]" part prefix.
DEFAULT_MAX_MESSAGE_CHARS = 4000


def resolve_sent_log_path(config: dict[str, Any]) -> Path:
    """Colocated with `data.journal_dir`, same convention used
    throughout this codebase - no separate, independently-configurable
    path key (see `ml/decision_ledger.resolve_db_path()`'s docstring for
    the test-pollution footgun that convention avoids)."""
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


def compute_idempotency_key(text: str) -> str:
    """A stable identity for ONE exact rendered report. Deliberately
    content-based (a hash of the text itself), not time-based - running
    the send command again on UNCHANGED cached data is always safely a
    no-op; new/changed cached data (a new ticker, a re-priced cost,
    anything) produces a different key and is allowed through."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def was_already_sent(config: dict[str, Any], idempotency_key: str) -> bool:
    db_path = resolve_sent_log_path(config)
    if not Path(db_path).exists():
        return False
    with _connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM sent WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    return row is not None


def _record_sent(config: dict[str, Any], idempotency_key: str, chat_id: str, chunk_count: int, now: datetime) -> None:
    db_path = resolve_sent_log_path(config)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sent (idempotency_key, chat_id, sent_at, chunk_count) VALUES (?, ?, ?, ?)",
            (idempotency_key, chat_id, now.isoformat(), chunk_count),
        )


def send_cached_report(
    config: dict[str, Any],
    logger: logging.Logger,
    token: str,
    chat_id: str,
    force: bool = False,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sends the SAME text `tradingagents_preview.format_preview()`
    would print, chunked for Telegram's length limit via the EXISTING
    `telegram_bot.send_report()`/`chunk_message()` helpers (break on
    blank-line boundaries - i.e. between ticker blocks where possible -
    never a reimplementation of that logic), guarded by a content-based
    idempotency key so re-running this with unchanged cached data is
    always a safe no-op. `force=True` is the one explicit override, for
    a deliberate resend.

    Returns `{"sent": bool, "reason": str | None, "idempotency_key": str,
    "chunks": int}`. Never raises - a Telegram failure is reported, not
    thrown, and is NEVER recorded as sent, so a legitimate retry after a
    transient failure is always possible."""
    now = now or datetime.now(timezone.utc)
    text = tradingagents_preview.format_preview(config, logger)
    key = compute_idempotency_key(text)

    if not force and was_already_sent(config, key):
        logger.info("TradingAgents report delivery: identical content already sent - skipping (idempotency key %s...).", key[:12])
        return {"sent": False, "reason": "duplicate - this exact content was already sent", "idempotency_key": key, "chunks": 0}

    chunks = telegram_bot.chunk_message(text, max_message_chars)
    ok = telegram_bot.send_report(token, chat_id, text, max_message_chars, logger)
    if not ok:
        return {"sent": False, "reason": "Telegram delivery failed - see logs; safe to retry", "idempotency_key": key, "chunks": len(chunks)}

    _record_sent(config, key, chat_id, len(chunks), now)
    return {"sent": True, "reason": None, "idempotency_key": key, "chunks": len(chunks)}


def main() -> int:
    """`python -m src.intelligence.tradingagents_telegram_report`
    previews (default, no flags). `--send` actually sends; `--force`
    bypasses the idempotency check for a deliberate resend."""
    import argparse
    import os

    from ..utils import load_config, load_env, setup_logging

    parser = argparse.ArgumentParser(description="Preview (default) or send (--send) the cached TradingAgents report to Telegram.")
    parser.add_argument("--send", action="store_true", help="Actually send to Telegram. Without this flag, only prints a dry-run preview (the default) - nothing is ever sent automatically.")
    parser.add_argument("--force", action="store_true", help="Send even if this exact content was already sent before (bypasses the idempotency check).")
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="tradingagents_preview.log")

    if not args.send:
        print(tradingagents_preview.format_preview(config, logger))
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: --send requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to be set (in your .env). Nothing was sent.")
        return 1

    result = send_cached_report(config, logger, token, chat_id, force=args.force)
    if result["sent"]:
        print(f"Sent ({result['chunks']} message part(s)). Idempotency key: {result['idempotency_key'][:12]}...")
        return 0
    print(f"Not sent: {result['reason']}")
    return 0 if result["reason"] and "duplicate" in result["reason"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
