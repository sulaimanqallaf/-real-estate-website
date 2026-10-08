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
    cached_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS call_log (
    call_date TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);
"""


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


def _cache_set(db_path: Path, key: str, result: dict[str, Any], now: datetime) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (cache_key, result_json, cached_at) VALUES (?, ?, ?)",
            (key, json.dumps(result), now.isoformat()),
        )


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


def _build_portfolio_context(config: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    """A READ-ONLY snapshot of broker-paper positions and account equity -
    never credentials, never a broker handle. Degrades to `{}` on any
    failure (GitHub Issue #1 integration-plan point 2: "read-only...
    never credentials or control capabilities")."""
    try:
        from .. import paper_trades

        df = paper_trades.load_paper_trades_df(config)
        open_rows = df[df["status"] == "OPEN"] if "status" in df.columns else df.iloc[0:0]
        positions = [{"ticker": r["symbol"], "shares": r.get("shares")} for _, r in open_rows.iterrows()]
        equity = config.get("risk", {}).get("account_equity")
        return {"positions": positions, "account_equity": equity}
    except Exception as exc:  # noqa: BLE001 - portfolio context is advisory only
        logger.warning("TradingAgents adapter: could not build portfolio context: %s", exc)
        return {}


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
        "portfolio": portfolio_context or {},
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
        _cache_set(state_db, key, result, now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TradingAgents adapter: cache write failed for %s: %s", ticker, exc)

    return result


def _map_rating_to_action(rating: str | None) -> str:
    if not rating:
        return ACTION_HOLD
    return _RATING_TO_ACTION.get(rating.strip().lower(), ACTION_HOLD)


def build_assessment(
    ticker: str, report_date: str, as_of: str, raw: dict[str, Any],
    quant_agent_decision: str | None = None, duration_ms: float | None = None,
) -> AgentResearchAssessment:
    """Pure mapping from the runner's raw JSON to this project's own
    `AgentResearchAssessment` - no subprocess, no I/O, fully unit
    testable without the TradingAgents package installed anywhere."""
    rating = raw.get("final_rating") or raw.get("signal")
    action = _map_rating_to_action(rating)
    reports = raw.get("reports") or {}

    evidence = []
    for label, key in (("Market", "market"), ("Sentiment", "sentiment"), ("News", "news"), ("Fundamentals", "fundamentals")):
        text = reports.get(key)
        if text:
            evidence.append(f"[{label} analyst, upstream TradingAgents] {text[:400]}")

    bull_points = [raw["bull_history"][:800]] if raw.get("bull_history") else []
    bear_points = [raw["bear_history"][:800]] if raw.get("bear_history") else []

    thesis = (
        raw.get("final_trade_decision") or raw.get("investment_plan") or raw.get("trader_investment_plan")
        or f"Upstream TradingAgents rating: {rating or 'REVIEW (no parseable rating)'}."
    )
    confidence = 1.0 if action != ACTION_HOLD else 0.0

    opinion = AgentOpinion(
        analyst="tradingagents_upstream", action=action, confidence=confidence, thesis=thesis[:2000],
        evidence=evidence, data_available=bool(reports) or bool(raw.get("final_trade_decision")),
    )

    risk_notes = [f"Raw upstream rating: {rating}"] if rating else ["Upstream produced no parseable rating (REVIEW)."]
    cost_info = raw.get("estimated_cost_usd")
    if cost_info is not None:
        if cost_info.get("total_usd") is not None:
            risk_notes.append(f"Estimated cost: ${cost_info['total_usd']:.4f} (real token usage x configured pricing).")
        elif cost_info.get("unknown_models"):
            risk_notes.append(f"Estimated cost: unknown (no pricing entry for: {', '.join(cost_info['unknown_models'])}).")

    return AgentResearchAssessment(
        ticker=ticker, report_date=report_date, as_of=as_of, action=action, confidence=confidence,
        thesis=thesis[:2000], bull_points=bull_points, bear_points=bear_points,
        risk_notes=risk_notes,
        analyst_opinions={"tradingagents_upstream": opinion},
        data_provenance=["source: upstream TauricResearch/TradingAgents package (real LangGraph run)"],
        quant_agent_decision=quant_agent_decision,
        duration_ms=duration_ms,
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
