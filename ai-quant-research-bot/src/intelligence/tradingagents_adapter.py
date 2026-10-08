"""Modular adapter for the REAL upstream TauricResearch/TradingAgents
package (GitHub Issue #1: "build a separate optional integration using
the real TradingAgents Python package... Use its actual LangGraph-powered
analysts, Bull/Bear debate, research managers, risk reviewers and memory
system... Connect it through a modular Python adapter.").

**This module never imports `tradingagents` itself.** It talks to
`tools/tradingagents_runner.py` - a thin, dumb script that runs ONLY
inside the isolated `.venvs/tradingagents/` environment created by
`scripts/setup_tradingagents_env.sh` - over a subprocess boundary: one
JSON request file in, one JSON object on stdout. This is the "modular
adapter" requirement 4 asks for, and it is what makes requirement 2's
isolation real rather than cosmetic: a missing/incompatible/conflicting
TradingAgents dependency (it pulls in LangGraph/LangChain, a different
and much heavier footprint than this project uses) can never break this
project's own import graph or test suite, because this project's own
Python process never imports it.

**Disabled by default** (`intelligence.tradingagents.enabled: false`).
Enabling it requires: running `scripts/setup_tradingagents_env.sh` once,
an LLM provider API key in `.env` (see `tradingagents`'s own
`llm_clients/api_key_env.py` for which env var each provider reads), and
genuinely costs real API calls - see `DEFAULT_MAX_CALLS_PER_DAY` and the
per-run ticker cap below.

**Hard invariants, identical to `pipeline.py`'s (GitHub Issue #1
requirement 9/12):**
1. SHADOW MODE ONLY. `run_shadow_tradingagents_research()` is called from
   `main.py` at the exact same point as the deterministic engine's
   `pipeline.run_shadow_research()` - strictly AFTER the execution layer
   has already decided/submitted every order this run. It only ever adds
   `entry["tradingagents_assessment"]`.
2. Portfolio context passed to TradingAgents (`_build_portfolio_context()`)
   is a READ-ONLY snapshot (open positions + an equity figure) - never
   credentials, never a broker handle, never anything that could let
   upstream code place or modify an order even if it tried to.
3. Its predictions are stored in their OWN, separate SQLite database
   (`resolve_memory_db_path()` - a different file from both
   `ml/decision_ledger.py` and the deterministic engine's
   `intelligence/memory.py` database), per requirement 10 ("store its
   predictions separately").
4. No config path anywhere lets this reach `execution_policy.py`,
   `circuit_breaker.py`, or `portfolio_risk.py` - same as the
   deterministic engine.
5. The existing deterministic multi-agent engine (`pipeline.py`,
   `analysts.py`, etc.) is completely unmodified by this module -
   requirement 1.

**Cost controls (GitHub Issue #1 follow-up requirements 4-6):**
- Real per-model token usage (via a LangChain callback in
  `tools/tradingagents_runner.py`) x configurable `$`/million-token
  pricing (`pricing.py`) - never a fabricated cost for an unpriced model.
- Hard daily + monthly `$` spend caps, enforced through a
  concurrency-safe reserve/commit/release ledger (`tradingagents_spend.py`)
  so two overlapping calls can never jointly exceed either cap.
- `_select_worthwhile_candidates()` spends real API calls only on
  tickers the existing deterministic gates have NOT already rejected,
  ranked by the best available numeric signal - never on a candidate
  that could not trade regardless of what this layer concludes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import pricing, tradingagents_spend
from .schemas import ACTION_BUY, ACTION_HOLD, ACTION_SELL, AgentOpinion, AgentResearchAssessment

DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 5
DEFAULT_CACHE_TTL_HOURS = 24
# A call-COUNT ceiling, kept alongside (not instead of) the real dollar
# limits below - it bounds the worst-case number of subprocess
# invocations per day regardless of what any one of them costs.
DEFAULT_MAX_CALLS_PER_DAY = 10
DEFAULT_MAX_TICKERS_PER_RUN = 2
# A conservative per-call ceiling RESERVED before the real cost is known
# (see tradingagents_spend.py's module docstring for the reserve/commit/
# release pattern this backs) - deliberately generous so a single
# expensive multi-round debate on a pricier model doesn't itself blow
# the daily cap's accounting; the REAL cost, once known, is what actually
# gets committed and counted toward future calls.
DEFAULT_MAX_COST_PER_CALL_USD = 1.00
DEFAULT_MAX_DAILY_SPEND_USD = 5.00
DEFAULT_MAX_MONTHLY_SPEND_USD = 50.00

_RATING_TO_ACTION = {
    "buy": ACTION_BUY, "overweight": ACTION_BUY,
    "hold": ACTION_HOLD, "review": ACTION_HOLD,
    "sell": ACTION_SELL, "underweight": ACTION_SELL,
}

# Defensive redaction for anything that might end up in a log line or an
# error message surfaced up the chain (GitHub Issue #1's very first
# instruction this session, about the Telegram bot token leaking through
# HTTP error logs, applies equally here): a provider SDK's own exception
# text occasionally echoes the key it rejected. Never assume upstream's
# error strings are already safe to log verbatim.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key|authorization|token)\s*[:=]\s*\S+"),
]


def _redact(text: str | None) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_python_executable(config: dict[str, Any]) -> Path:
    configured = config.get("intelligence", {}).get("tradingagents", {}).get("python_executable")
    if configured:
        return Path(configured)
    return _repo_root() / ".venvs" / "tradingagents" / "bin" / "python"


def resolve_runner_script() -> Path:
    return _repo_root() / "tools" / "tradingagents_runner.py"


def resolve_state_db_path(config: dict[str, Any]) -> Path:
    """Cache + call-budget bookkeeping - colocated with `data.journal_dir`,
    same convention used throughout this codebase (see
    `ml/decision_ledger.resolve_db_path()`'s docstring for why a second,
    independently-configurable path key is deliberately never offered)."""
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / "tradingagents_adapter_state.db"
    return resolve_path("data/ml/tradingagents_adapter_state.db")


def resolve_memory_db_path(config: dict[str, Any]) -> Path:
    """TradingAgents' own predictions, stored SEPARATELY from the
    deterministic engine's `intelligence/memory.py` database and from
    `ml/decision_ledger.py` - requirement 10."""
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / "tradingagents_research_memory.db"
    return resolve_path("data/ml/tradingagents_research_memory.db")


def resolve_work_dir(config: dict[str, Any]) -> Path:
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    base = resolve_path(journal_dir) if journal_dir else resolve_path("data/ml")
    return base / "tradingagents_work"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    ticker TEXT,
    report_date TEXT
);
CREATE TABLE IF NOT EXISTS call_log (
    call_date TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);
"""


def _migrate_cache_table(conn: sqlite3.Connection) -> None:
    """Adds the `ticker`/`report_date` columns to a `cache` table created
    by a version of this module before they existed (GitHub Issue #1
    follow-up requirement 6 - inspecting cached results needs to know
    which ticker/date each row belongs to). `CREATE TABLE IF NOT EXISTS`
    alone never adds a column to an already-existing table, so a real
    database written before this change (e.g. from an earlier real Mac
    run) needs this explicit migration or every subsequent write would
    fail with "table cache has no column named ticker"."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cache)").fetchall()}
    for column in ("ticker", "report_date"):
        if column not in existing:
            conn.execute(f"ALTER TABLE cache ADD COLUMN {column} TEXT")


@contextmanager
def _connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        _migrate_cache_table(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cache_key(ticker: str, trade_date: str, selected_analysts: list[str], config_overrides: dict[str, Any]) -> str:
    payload = json.dumps({"ticker": ticker, "trade_date": trade_date, "analysts": sorted(selected_analysts), "overrides": config_overrides}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(db_path: Path, key: str, ttl_hours: float, now: datetime) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT result_json, cached_at FROM cache WHERE cache_key = ?", (key,)).fetchone()
    if row is None:
        return None
    cached_at = datetime.fromisoformat(row["cached_at"])
    if (now - cached_at).total_seconds() > ttl_hours * 3600:
        return None
    return json.loads(row["result_json"])


def _cache_set(db_path: Path, key: str, result: dict[str, Any], now: datetime, ticker: str | None = None, report_date: str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (cache_key, result_json, cached_at, ticker, report_date) VALUES (?, ?, ?, ?, ?)",
            (key, json.dumps(result), now.isoformat(), ticker, report_date),
        )


def _cache_key_from_request_payload(payload: dict[str, Any]) -> str | None:
    try:
        ticker = payload["ticker"]
        trade_date = payload["trade_date"]
    except (KeyError, TypeError):
        return None
    selected_analysts = payload.get("selected_analysts") or ["market", "social", "news", "fundamentals"]
    config_overrides = payload.get("config_overrides") or {}
    return _cache_key(ticker, trade_date, selected_analysts, config_overrides)


def recover_cache_metadata_from_request_files(config: dict[str, Any], logger: logging.Logger | None = None) -> dict[str, Any]:
    """Backfills `ticker`/`report_date` for cache rows a pre-migration
    database left `NULL` (GitHub Issue #1 follow-up), using the
    `request.json` files `run_one()` already writes under
    `resolve_work_dir()` for every real call it ever made.

    **Never guesses.** A request file's own `(ticker, trade_date,
    selected_analysts, config_overrides)` deterministically reproduces
    the EXACT SAME `cache_key` `_cache_key()` computed for that call in
    the first place - the two are cryptographically tied together by
    construction, so a match here is a verified identification of which
    call produced that cache row, never an inference from row order,
    position, or the cached signal/content itself. A `NULL` row with no
    matching request file anywhere under the work directory is left
    exactly as it was - still `NULL`, still clearly "unknown" - never
    filled in with a guess.

    **Makes no new LLM API call and writes nothing to `result_json`,
    `cached_at`, or `cache_key`** - only ever updates the `ticker`/
    `report_date` columns of a row that is currently `NULL` in at least
    one of them, on an exact cache_key match. Every existing cached
    result and every existing cache key survives untouched.

    Returns `{"recovered": int, "unmatched_rows": int, "checked_files": int}`.
    Never raises - a missing database or work directory just means
    nothing to recover."""
    db_path = resolve_state_db_path(config)
    if not Path(db_path).exists():
        return {"recovered": 0, "unmatched_rows": 0, "checked_files": 0}

    with _connect(db_path) as conn:
        null_rows = conn.execute("SELECT cache_key FROM cache WHERE ticker IS NULL OR report_date IS NULL").fetchall()
    null_keys = {row["cache_key"] for row in null_rows}
    if not null_keys:
        return {"recovered": 0, "unmatched_rows": 0, "checked_files": 0}

    work_dir = resolve_work_dir(config)
    recovered: dict[str, tuple[str, str]] = {}
    checked_files = 0
    if work_dir.exists():
        for request_path in work_dir.glob("*/request.json"):
            checked_files += 1
            try:
                payload = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            key = _cache_key_from_request_payload(payload)
            if key is None or key not in null_keys or key in recovered:
                continue
            ticker, trade_date = payload.get("ticker"), payload.get("trade_date")
            if not ticker or not trade_date:
                continue
            recovered[key] = (str(ticker), str(trade_date))

    if recovered:
        with _connect(db_path) as conn:
            for key, (ticker, trade_date) in recovered.items():
                conn.execute(
                    "UPDATE cache SET ticker = ?, report_date = ? WHERE cache_key = ? AND (ticker IS NULL OR report_date IS NULL)",
                    (ticker, trade_date, key),
                )

    if logger is not None and recovered:
        logger.info("TradingAgents adapter: recovered ticker/report_date for %d cached result(s) from request.json files.", len(recovered))

    return {"recovered": len(recovered), "unmatched_rows": len(null_keys) - len(recovered), "checked_files": checked_files}


def list_cached_raw_results(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns every cached TradingAgents result with its FULL raw
    payload (bull/bear history, reports, token usage, etc.) - the same
    shape `run_one()` itself returns, not `inspect_cached_results()`'s
    narrower summary. Zero API calls, zero subprocess use - a pure
    SQLite read. Used by `inspect_cached_results()` and by
    `tradingagents_preview.py` (GitHub Issue #1 follow-up: previewing the
    report from cache alone). Best-effort recovers `NULL` ticker/
    report_date first, same as `inspect_cached_results()`. Returns `[]`
    for a missing database, never raises."""
    db_path = resolve_state_db_path(config)
    if not Path(db_path).exists():
        return []

    try:
        recover_cache_metadata_from_request_files(config)
    except Exception:  # noqa: BLE001 - listing must proceed even if recovery fails
        pass

    with _connect(db_path) as conn:
        rows = conn.execute("SELECT ticker, report_date, cached_at, result_json FROM cache ORDER BY cached_at DESC").fetchall()

    results = []
    for row in rows:
        try:
            raw = json.loads(row["result_json"])
        except (ValueError, TypeError):
            continue
        results.append({"ticker": row["ticker"], "report_date": row["report_date"], "cached_at": row["cached_at"], "raw": raw})
    return results


def repriced_cost_info(raw: dict[str, Any], pricing_table: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """ONE shared helper for recomputing a result's dollar cost from its
    own real `token_usage` against a pricing table - used by `run_one()`'s
    cache-hit path, `inspect_cached_results()`, and `tradingagents_
    preview.py`, so the three can never drift from each other or
    double-count a cost (GitHub Issue #1 follow-up: "recompute pricing
    in-memory through one shared helper for both preview and cache
    inspection. No new OpenAI calls, no double counting.").

    Never calls the API, never mutates `raw` - returns a fresh cost-info
    dict (or whatever `raw` already had, untouched) for the CALLER to
    attach wherever it needs to. Recomputes only when `token_usage` was
    actually recorded; an entry from before token tracking existed keeps
    whatever (possibly absent/unknown) cost it already had, never a
    fabricated `$0.00`."""
    if "token_usage" not in raw:
        return raw.get("estimated_cost_usd")
    return pricing.estimate_cost_usd(raw.get("token_usage"), pricing_table)


def inspect_cached_results(config: dict[str, Any], pricing_table: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """GitHub Issue #1 follow-up requirement 6: inspect every cached
    TradingAgents result WITHOUT making a new API call (no subprocess,
    no network - a pure SQLite read). Each row's `estimated_cost_usd` is
    RE-COMPUTED with the CURRENT pricing table (the config override, or
    `pricing.DEFAULT_PRICING_USD_PER_MILLION_TOKENS`) rather than
    trusting whatever was baked in at cache-write time - this is exactly
    what turns a previously-`null` cost (e.g. from before gpt-6-sol/
    gpt-6-luna had pricing entries) into a real number once the pricing
    table is fixed, for a result that is already sitting in the cache.

    Before reading, best-effort recovers any `NULL` ticker/report_date
    left by a pre-migration database via `recover_cache_metadata_from_
    request_files()` - see that function's docstring for why this is a
    verified recovery, never a guess, and never an API call. A row that
    cannot be recovered keeps `ticker`/`report_date` as `None` ("unknown"),
    exactly as before.

    Returns `[]` for a missing database, never raises."""
    table = pricing_table if pricing_table is not None else config.get("intelligence", {}).get("tradingagents", {}).get("pricing")

    inspected = []
    for cached in list_cached_raw_results(config):
        result = cached["raw"]
        token_usage = result.get("token_usage")
        # Only re-price when usage was actually recorded - an entry from
        # before token tracking existed keeps whatever (possibly absent/
        # unknown) cost it already had, never a fabricated $0.00.
        cost_info = repriced_cost_info(result, table)
        inspected.append({
            "ticker": cached["ticker"], "report_date": cached["report_date"], "cached_at": cached["cached_at"],
            "final_rating": result.get("final_rating") or result.get("signal"),
            "token_usage": token_usage, "estimated_cost_usd": cost_info,
        })
    return inspected


def _call_budget_remaining(db_path: Path, call_date: str, max_calls_per_day: int) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT count FROM call_log WHERE call_date = ?", (call_date,)).fetchone()
    used = row["count"] if row else 0
    return max(0, max_calls_per_day - used)


def _record_call(db_path: Path, call_date: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO call_log (call_date, count) VALUES (?, 1) ON CONFLICT(call_date) DO UPDATE SET count = count + 1",
            (call_date,),
        )


def _build_portfolio_context(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any] | None:
    """A READ-ONLY snapshot of ACTUAL broker-paper positions - never
    credentials, never a broker handle (GitHub Issue #1 integration-plan
    point 2). Shaped to match upstream's own
    `tradingagents.portfolio.PortfolioContext` schema exactly (`cash`,
    `currency`, `positions: [{ticker, quantity, average_price}]`), which
    `tools/tradingagents_runner.py` validates straight into that type
    before calling `propagate()`.

    **Bug fixed (reported after a real Mac run):** this used to read
    `r["symbol"]`/`r.get("shares")`, but `paper_trades.load_paper_trades_
    df()`'s real columns are `ticker`/`position_size` (see
    `paper_trades.PAPER_TRADE_COLUMNS`) - every call crashed with
    `KeyError: 'symbol'`.

    **Simulated vs. broker-paper, never confused:** only rows whose
    `provenance` resolves (via `paper_trades.effective_provenance()`) to
    `PROVENANCE_BROKER_PAPER` are included - a `SIMULATED` row was never
    actually submitted to a broker and must never be reported to an
    external research tool as a real holding. In pure DRY_RUN/simulated
    deployments this correctly, deliberately reports zero positions: a
    simulated approval is not a real position, and saying otherwise
    would be exactly the "invented position" this must never do.

    **Returns `None` - not `{}`, not an empty positions list - on any
    failure.** Matches upstream's own three-state portfolio model
    (`portfolio.py`'s docstring: "a position, a flat book, and no
    context at all... treating 'not provided' as 'flat' would invent a
    fact about the caller's account"): `None` here means "unknown,"
    propagated by `run_one()`/the runner as `portfolio=None` (no context
    given at all) - never silently rendered as a confirmed flat book."""
    from ..utils import is_nan

    try:
        from .. import paper_trades

        df = paper_trades.load_paper_trades_df(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: could not read paper_trades.csv for portfolio context - treating as UNKNOWN, never as zero holdings: %s", exc)
        return None

    try:
        if df.empty:
            open_broker_rows = df.iloc[0:0]
        else:
            is_open = df["status"] == "OPEN"
            is_broker_paper = df["provenance"].apply(paper_trades.effective_provenance) == paper_trades.PROVENANCE_BROKER_PAPER
            open_broker_rows = df[is_open & is_broker_paper]

        positions = []
        for _, row in open_broker_rows.iterrows():
            ticker = row.get("ticker")
            quantity = row.get("position_size")
            if not ticker or is_nan(ticker) or quantity is None or is_nan(quantity):
                continue  # a malformed row is skipped, never guessed into a position
            entry_price = row.get("entry_price")
            positions.append({
                "ticker": str(ticker),
                "quantity": float(quantity),
                "average_price": None if entry_price is None or is_nan(entry_price) else float(entry_price),
            })

        # risk.account_equity is this paper account's configured equity -
        # the same figure circuit_breaker.py/portfolio_risk.py already
        # treat as authoritative for a PAPER account throughout this
        # codebase - not a live broker cash balance, but not invented
        # either; None (unknown) when not configured at all.
        cash = config.get("risk", {}).get("account_equity")
        return {"cash": cash, "currency": None, "positions": positions}
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: could not build portfolio context from loaded trades - treating as UNKNOWN, never as zero holdings: %s", exc)
        return None


def run_one(
    ticker: str,
    report_date: str,
    config: dict[str, Any],
    logger: logging.Logger,
    portfolio_context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Runs one ticker through the real upstream TradingAgents graph, via
    the isolated subprocess, with caching + a daily call budget + timeout +
    retries. Returns the raw `{"ok": true, ...}` payload from
    `tools/tradingagents_runner.py`, or `None` on any failure/budget
    exhaustion/cache miss-then-fail - never raises."""
    now = now or datetime.now(timezone.utc)
    ta_config = config.get("intelligence", {}).get("tradingagents", {})
    state_db = resolve_state_db_path(config)
    selected_analysts = ta_config.get("selected_analysts") or ["market", "social", "news", "fundamentals"]
    config_overrides = ta_config.get("config_overrides") or {}
    cache_ttl_hours = ta_config.get("cache_ttl_hours", DEFAULT_CACHE_TTL_HOURS)

    key = _cache_key(ticker, report_date, selected_analysts, config_overrides)
    try:
        cached = _cache_get(state_db, key, cache_ttl_hours, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: cache read failed for %s: %s", ticker, exc)
        cached = None
    if cached is not None:
        logger.info("TradingAgents adapter: cache hit for %s/%s.", ticker, report_date)
        # Re-price with the CURRENT pricing table rather than trusting
        # whatever was baked in when this was cached (GitHub Issue #1
        # follow-up requirement 6) - no new API call, so a pricing-table
        # fix (e.g. adding a previously-missing model) is reflected on
        # the very next cache hit, not just on a fresh (billed) call.
        cached["estimated_cost_usd"] = repriced_cost_info(cached, ta_config.get("pricing"))
        return cached

    max_calls_per_day = ta_config.get("max_calls_per_day", DEFAULT_MAX_CALLS_PER_DAY)
    call_date = now.strftime("%Y-%m-%d")
    try:
        remaining = _call_budget_remaining(state_db, call_date, max_calls_per_day)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: call-budget read failed, refusing to call (fail closed): %s", exc)
        return None
    if remaining <= 0:
        logger.warning("TradingAgents adapter: daily call budget (%d) exhausted - skipping %s.", max_calls_per_day, ticker)
        return None

    # Hard $ spend limits (GitHub Issue #1 requirement 5) - reserve a
    # conservative ceiling BEFORE the real subprocess call, so a daily/
    # monthly cap can never be exceeded by two calls racing each other;
    # see tradingagents_spend.py's module docstring.
    spend_db = tradingagents_spend.resolve_ledger_path(config)
    estimate_usd = ta_config.get("max_cost_per_call_usd", DEFAULT_MAX_COST_PER_CALL_USD)
    daily_limit_usd = ta_config.get("max_daily_spend_usd", DEFAULT_MAX_DAILY_SPEND_USD)
    monthly_limit_usd = ta_config.get("max_monthly_spend_usd", DEFAULT_MAX_MONTHLY_SPEND_USD)
    entry_id = str(uuid.uuid4())
    try:
        reserved = tradingagents_spend.reserve(spend_db, entry_id, estimate_usd, daily_limit_usd, monthly_limit_usd, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: spend-ledger reservation failed, refusing to call (fail closed): %s", exc)
        return None
    if not reserved:
        logger.warning("TradingAgents adapter: daily/monthly $ spend limit would be exceeded - skipping %s.", ticker)
        return None

    work_dir = resolve_work_dir(config) / f"{ticker}_{report_date}"
    work_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "ticker": ticker, "trade_date": report_date, "work_dir": str(work_dir),
        "selected_analysts": selected_analysts, "config_overrides": config_overrides,
        # None (unknown/not given) must survive as literal `null` in the
        # request JSON, never coerced to `{}` - see _build_portfolio_
        # context()'s docstring on why "unknown" and "flat book" are
        # deliberately distinct states.
        "portfolio": portfolio_context,
    }
    request_path = work_dir / "request.json"
    with open(request_path, "w", encoding="utf-8") as f:
        json.dump(request, f)

    python_executable = resolve_python_executable(config)
    timeout_seconds = ta_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    max_retries = ta_config.get("max_retries", DEFAULT_MAX_RETRIES)

    result = None
    last_error = None
    process_ever_started = False
    for attempt in range(max_retries + 1):
        try:
            proc = subprocess.run(
                [str(python_executable), str(resolve_runner_script()), str(request_path)],
                capture_output=True, text=True, timeout=timeout_seconds,
            )
            process_ever_started = True
        except subprocess.TimeoutExpired:
            # The process DID start - and a provider call may have already
            # been billed server-side before we gave up waiting for a
            # response. Treated as spent (see the reservation commit below),
            # never released.
            process_ever_started = True
            last_error = f"timed out after {timeout_seconds}s"
            logger.warning("TradingAgents adapter: %s on attempt %d/%d for %s.", last_error, attempt + 1, max_retries + 1, ticker)
            time.sleep(DEFAULT_RETRY_BACKOFF_SECONDS)
            continue
        except Exception as exc:  # noqa: BLE001 - e.g. a misconfigured python_executable (FileNotFoundError) - the process never ran, nothing was ever billed
            last_error = _redact(str(exc))
            logger.warning("TradingAgents adapter: could not start subprocess for %s: %s", ticker, last_error)
            time.sleep(DEFAULT_RETRY_BACKOFF_SECONDS)
            continue

        try:
            parsed = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else None
        except (ValueError, IndexError):
            parsed = None

        if parsed is None:
            last_error = f"no parseable JSON on stdout (exit {proc.returncode}): {_redact(proc.stderr[-500:])}"
            logger.warning("TradingAgents adapter: %s", last_error)
            time.sleep(DEFAULT_RETRY_BACKOFF_SECONDS)
            continue

        if not parsed.get("ok"):
            last_error = _redact(parsed.get("error", "unknown error"))
            logger.warning("TradingAgents adapter: run failed for %s: %s", ticker, last_error)
            # A real, reported failure (e.g. bad API key, provider error) is
            # not a transient blip worth retrying/backing off on - but a
            # genuinely malformed response is handled identically above, so
            # this still counts as the call it was (budget already spent
            # the moment the subprocess actually ran).
            result = None
            break

        result = parsed
        break

    # Reconcile the $ reservation with what actually happened - see
    # tradingagents_spend.py's module docstring. Only a call that never
    # even started a subprocess gets released; anything that ran (even a
    # timeout, even a reported failure) might have already been billed by
    # the provider, so it is committed, conservatively, at the reserved
    # ceiling when the real cost can't be determined.
    try:
        if not process_ever_started:
            tradingagents_spend.release(spend_db, entry_id)
            cost_info = {"total_usd": 0.0, "by_model": {}, "unknown_models": []}
        else:
            token_usage = (result or {}).get("token_usage")
            cost_info = pricing.estimate_cost_usd(token_usage, ta_config.get("pricing"))
            if cost_info["total_usd"] is None:
                # Fail CLOSED (GitHub Issue #1 follow-up requirement 2):
                # an unpriced model never slips past the spend cap for
                # free - the full conservative per-call reservation is
                # charged against the daily/monthly ledger instead of a
                # fabricated $0.00, so repeated calls on an unpriced
                # model burn through the cap quickly rather than quietly
                # bypassing it.
                logger.warning(
                    "TradingAgents adapter: no pricing entry for model(s) %s - charging the full $%.2f reservation "
                    "against the spend cap (fail closed) instead of an unknown/zero cost. Add pricing for this "
                    "model to intelligence.tradingagents.pricing or pricing.DEFAULT_PRICING_USD_PER_MILLION_TOKENS.",
                    cost_info["unknown_models"], estimate_usd,
                )
            actual_usd = cost_info["total_usd"] if cost_info["total_usd"] is not None else estimate_usd
            tradingagents_spend.commit(spend_db, entry_id, actual_usd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: could not reconcile spend ledger for %s: %s", ticker, exc)
        cost_info = {"total_usd": None, "by_model": {}, "unknown_models": []}

    try:
        _record_call(state_db, call_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: could not record call usage: %s", exc)

    if result is None:
        logger.error("TradingAgents adapter: giving up on %s/%s after %d attempt(s): %s", ticker, report_date, max_retries + 1, last_error)
        return None

    result["estimated_cost_usd"] = cost_info

    try:
        _cache_set(state_db, key, result, now, ticker=ticker, report_date=report_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: cache write failed for %s: %s", ticker, exc)

    return result


def _map_rating_to_action(rating: str | None) -> str:
    if not rating:
        return ACTION_HOLD
    return _RATING_TO_ACTION.get(rating.strip().lower(), ACTION_HOLD)


_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}[^\n]*\n?", re.MULTILINE)
_MARKDOWN_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_)")
_WHITESPACE_RE = re.compile(r"\s+")

# How much of a debate transcript to surface as a readable excerpt in the
# report - a Telegram-sized snippet, not the whole thing. The full
# transcript is never lost: it stays in the cache's raw payload (and, for
# the full on-disk record, the TradingAgents work directory) - this is
# only ever a DISPLAY excerpt.
_DEBATE_EXCERPT_CHARS = 280


def _summarize_debate_text(text: str | None, max_chars: int = _DEBATE_EXCERPT_CHARS) -> str | None:
    """Turns a raw Bull/Bear debate transcript into a readable excerpt,
    pure text processing - no LLM call, no summarization model (GitHub
    Issue #1 follow-up: "do not invoke an LLM just to preview cached
    results").

    Strips whole markdown HEADING LINES (`#`/`##`/... through end of
    line) - not just the `#` marker - because the heading's own words
    (e.g. "Bull Case", "Bear Case") are typically boilerplate that
    duplicates the "Bull case:"/"Bear case:" label the report already
    prepends; leaving them in produced a redundant "Bull case: Bull
    Case <actual content>" artifact. Also strips inline emphasis markers
    (`**`/`__`/`*`/`_`), so a transcript like "## Bull Case\\n\\n**Strong
    margins**" reads as a clean "Strong margins" excerpt. This only ever
    affects the DISPLAY excerpt built here - the original transcript in
    `raw["bull_history"]`/`raw["bear_history"]` (and the cached row it
    came from) is never altered. Collapses to a single excerpt
    truncated at a WORD boundary (never mid-word) and, when the source
    was longer than the excerpt, appends an explicit
    "(excerpt, N of M chars - see cached transcript for the full
    debate)" label - the report must never present a truncated snippet
    as if it were the complete analysis. Returns `None` for empty/
    missing input, never an empty string standing in for "no content"."""
    if not text:
        return None
    cleaned = _MARKDOWN_HEADING_RE.sub("", text)
    cleaned = _MARKDOWN_EMPHASIS_RE.sub("", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return None

    total_chars = len(cleaned)
    if total_chars <= max_chars:
        return cleaned

    excerpt = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:- ")
    return f"{excerpt}… (excerpt, {len(excerpt)} of {total_chars} chars - see cached transcript for the full debate)"


def build_assessment(
    ticker: str, report_date: str, as_of: str, raw: dict[str, Any],
    quant_agent_decision: str | None = None, duration_ms: float | None = None,
) -> AgentResearchAssessment:
    """Pure mapping from the runner's raw JSON to this project's own
    `AgentResearchAssessment` - no subprocess, no I/O, fully unit
    testable without the TradingAgents package installed anywhere.

    **`action` vs. `raw_label` (GitHub Issue #1 follow-up):** upstream's
    own 5-tier rating (e.g. "Overweight") is preserved VERBATIM as
    `raw_label` - it is never silently collapsed into this project's
    normalized 3-tier `action` (BUY/SELL/HOLD). Both are always present
    on the returned assessment, clearly distinguished.

    **`confidence` is `None` ("unavailable"), never a fabricated
    number.** Upstream gives a categorical rating, not a calibrated
    success probability - showing "confidence 100%" for every non-Hold
    call would misrepresent a label as a statistic. A real numerical
    probability belongs here only once backed by a separately evaluated,
    calibrated model (the same discipline `ml/calibration.py` already
    requires for this project's own ML predictions)."""
    rating = raw.get("final_rating") or raw.get("signal")
    action = _map_rating_to_action(rating)
    reports = raw.get("reports") or {}

    evidence = []
    for label, key in (("Market", "market"), ("Sentiment", "sentiment"), ("News", "news"), ("Fundamentals", "fundamentals")):
        text = reports.get(key)
        if text:
            evidence.append(f"[{label} analyst, upstream TradingAgents] {text[:400]}")

    bull_excerpt = _summarize_debate_text(raw.get("bull_history"))
    bear_excerpt = _summarize_debate_text(raw.get("bear_history"))
    bull_points = [bull_excerpt] if bull_excerpt else []
    bear_points = [bear_excerpt] if bear_excerpt else []

    thesis = (
        raw.get("final_trade_decision") or raw.get("investment_plan") or raw.get("trader_investment_plan")
        or f"Upstream TradingAgents rating: {rating or 'REVIEW (no parseable rating)'}."
    )
    confidence = None  # uncalibrated - see docstring

    opinion = AgentOpinion(
        analyst="tradingagents_upstream", action=action, confidence=confidence, thesis=thesis[:2000],
        evidence=evidence, data_available=bool(reports) or bool(raw.get("final_trade_decision")),
        raw_label=rating,
    )

    risk_notes = [f"Raw upstream rating: {rating}"] if rating else ["Upstream produced no parseable rating (REVIEW)."]
    cost_info = raw.get("estimated_cost_usd")
    if cost_info is not None:
        if cost_info.get("total_usd") is not None:
            risk_notes.append(f"Estimated cost: ${cost_info['total_usd']:.4f} (real token usage x configured pricing).")
        elif cost_info.get("unknown_models"):
            risk_notes.append(f"Estimated cost: unknown (no pricing entry for: {', '.join(cost_info['unknown_models'])}).")

    token_usage = raw.get("token_usage")
    if token_usage:
        parts = []
        for model, usage in token_usage.items():
            cached = usage.get("cached_input_tokens", 0) or 0
            cached_note = f" (cached {cached:,})" if cached else ""
            parts.append(f"{model}: in={usage.get('input_tokens', 0):,}{cached_note} out={usage.get('output_tokens', 0):,}")
        risk_notes.append("Token usage: " + " | ".join(parts))

    return AgentResearchAssessment(
        ticker=ticker, report_date=report_date, as_of=as_of, action=action, confidence=confidence,
        thesis=thesis[:2000], bull_points=bull_points, bear_points=bear_points,
        risk_notes=risk_notes,
        analyst_opinions={"tradingagents_upstream": opinion},
        data_provenance=["source: upstream TauricResearch/TradingAgents package (real LangGraph run)"],
        quant_agent_decision=quant_agent_decision,
        duration_ms=duration_ms,
        raw_label=rating,
    )


def _select_worthwhile_candidates(ticker_results: list[dict[str, Any]], max_tickers: int) -> list[dict[str, Any]]:
    """GitHub Issue #1 requirement 6: "ensure the bot selects only
    worthwhile candidate symbols for expensive multi-agent analysis."
    Reuses `risk_reviewer`'s own upstream-rejection check (never
    duplicated, never loosened here) to exclude anything the existing
    deterministic gates have already rejected - spending a real, billed
    LLM call analyzing a candidate that can never trade regardless of
    what it concludes would be pure waste. The remainder is ranked by the
    best available numeric signal (the Quant/ML composite score when
    present, else the plain rule-based score) and only the top
    `max_tickers` are selected."""
    from .risk_reviewer import _is_rejected_upstream

    eligible = [e for e in ticker_results if e.get("symbol") and not _is_rejected_upstream(e)]

    def _rank(entry: dict[str, Any]) -> float:
        quant = entry.get("quant_assessment")
        quant_score = getattr(quant, "quant_score", None)
        return quant_score if quant_score is not None else (entry.get("score", 0) or 0)

    eligible.sort(key=_rank, reverse=True)
    return eligible[:max_tickers]


def run_shadow_tradingagents_research(
    ticker_results: list[dict[str, Any]],
    report_date: str,
    config: dict[str, Any],
    logger: logging.Logger,
    as_of: datetime | None = None,
) -> None:
    """The TradingAgents counterpart to `pipeline.run_shadow_research()` -
    same shadow-mode contract, same call site in `main.py` (strictly after
    the execution layer already ran). **Disabled by default** and bounded
    to a small ticker subset per run (`max_tickers_per_run`) to bound real
    API cost - this calls a real, billed LLM provider when enabled."""
    ta_config = config.get("intelligence", {}).get("tradingagents", {})
    if not ta_config.get("enabled", False):
        return

    as_of_dt = as_of or datetime.now(timezone.utc)
    as_of_str = as_of_dt.isoformat()
    max_tickers = ta_config.get("max_tickers_per_run", DEFAULT_MAX_TICKERS_PER_RUN)
    memory_db = resolve_memory_db_path(config)

    portfolio_context = _build_portfolio_context(config, logger)

    candidates = _select_worthwhile_candidates(ticker_results, max_tickers)
    for entry in candidates:
        ticker = entry["symbol"]
        from ..utils import safe_run

        start = time.monotonic()
        raw = safe_run(logger, f"{ticker} TradingAgents run", lambda t=ticker: run_one(t, report_date, config, logger, portfolio_context, now=as_of_dt))
        duration_ms = round((time.monotonic() - start) * 1000.0, 2)
        if raw is None:
            continue

        quant_assessment = entry.get("quant_assessment")
        quant_decision = getattr(quant_assessment, "decision", None)
        assessment = safe_run(
            logger, f"{ticker} TradingAgents mapping",
            lambda t=ticker, r=raw, qd=quant_decision, dms=duration_ms: build_assessment(t, report_date, as_of_str, r, qd, dms),
        )
        if assessment is None:
            continue

        entry["tradingagents_assessment"] = assessment
        safe_run(logger, f"{ticker} record TradingAgents assessment", lambda a=assessment: _record(memory_db, a))


def _record(memory_db: Path, assessment: AgentResearchAssessment) -> None:
    from . import memory

    memory.record_assessment(memory_db, assessment)
