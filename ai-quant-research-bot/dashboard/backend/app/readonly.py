"""The ONLY place this dashboard backend touches the main bot's code or
data. Every function here is read-only: it loads the same `config/
settings.yaml` the bot itself uses (for file paths only - no network
call), and reads already-written journals/logs/caches/ledgers. Nothing
here ever writes to any bot-owned file, places an order, calls an LLM
provider, or imports anything execution/broker-facing.

**Hard safety invariant, enforced structurally** (see
`tests/test_readonly_safety.py`): this module may import from
`src.execution.run_health`, `src.execution.circuit_breaker`,
`src.ml.decision_ledger`, `src.intelligence.tradingagents_adapter`,
`src.intelligence.tradingagents_spend`, `src.paper_trades`, and
`src.utils` only - never `src.execution.order_manager`,
`src.execution.execution_policy`, `src.execution.ibkr_client`,
`src.execution.broker`, `src.execution.approval_bridge`, or
`src.main.run`. The dashboard has no code path that can place an order,
change a risk setting, or trigger a real run, by construction.

**No credentials reach the frontend.** Every string this module returns
is passed through `redact_secrets()` before being handed to a caller -
belt-and-suspenders on top of the fact that none of the functions below
ever read `.env`/API keys/bot tokens in the first place (they only read
`config/settings.yaml`-relative file PATHS via `utils.load_config()`,
never `utils.get_env_var()`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# dashboard/backend/app/readonly.py -> repo root is 4 parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.execution import circuit_breaker, run_health  # noqa: E402
from src.ml import decision_ledger  # noqa: E402
from src.utils import load_config, redact_secrets  # noqa: E402


def _redact_deep(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redact_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_deep(v) for v in value]
    return value


_config_cache: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """Loaded once per process (`config/settings.yaml` only - never
    `.env`/secrets; `load_config()` itself never reads environment
    variables). Cached because every request/poll needs it and it never
    changes while the server runs."""
    global _config_cache
    if _config_cache is None:
        _config_cache = load_config(None)
    return _config_cache


def health_report() -> dict[str, Any]:
    """`run_health.build_health_report()`, read-only - last run, last
    success, launchd status, staleness, spend, circuit breaker state.
    See `src/execution/run_health.py`."""
    import logging

    config = get_config()
    logger = logging.getLogger("dashboard.readonly")
    report = run_health.build_health_report(config, logger)
    return _redact_deep(report)


def run_status() -> dict[str, Any]:
    return _redact_deep(run_health.read_status(get_config()))


def circuit_breaker_status() -> dict[str, Any]:
    return _redact_deep(circuit_breaker.status(get_config()))


def recent_decisions(limit: int = 25) -> list[dict[str, Any]]:
    """The most recent rows from the decision ledger (every candidate
    the pipeline ever classified, win or lose, traded or not) - oldest
    first is `query_decisions()`'s own order, so this takes the tail."""
    config = get_config()
    db_path = decision_ledger.resolve_db_path(config)
    rows = decision_ledger.query_decisions(db_path)
    return _redact_deep(rows[-limit:])


def tradingagents_outputs(limit: int = 10) -> list[dict[str, Any]]:
    """Cached real-upstream-TradingAgents results, if that integration
    is enabled and has ever run - `None`/`[]` (never fabricated) when
    it's disabled or has no cache yet, exactly like every other
    "Data Unavailable" source in this codebase."""
    from src.intelligence import tradingagents_adapter

    config = get_config()
    if not config.get("intelligence", {}).get("tradingagents", {}).get("enabled", False):
        return []
    try:
        results = tradingagents_adapter.inspect_cached_results(config)
    except Exception:  # noqa: BLE001 - dashboard diagnostics must never crash on a malformed cache row
        return []
    return _redact_deep(results[-limit:])


def spend_summary() -> dict[str, Any] | None:
    return _redact_deep(run_health.spend_report(get_config()))


def paper_pnl_summary() -> dict[str, Any] | None:
    """Real paper P&L from `data/journal/paper_trades.csv` ONLY - never
    a fabricated number. Returns `None` when the file doesn't exist or
    has no rows at all (not even an empty-but-real file has been
    written yet), so the frontend can distinguish "no data" from "$0.00
    P&L"."""
    from src import paper_trades

    config = get_config()
    df = paper_trades.load_paper_trades_df(config)
    if df.empty:
        return None

    closed = df[df["status"] == "CLOSED"]
    open_trades = df[df["status"] == "OPEN"]
    closed_pnl = closed["pnl_dollars"].dropna()
    return {
        "open_count": int(len(open_trades)),
        "closed_count": int(len(closed)),
        "realized_pnl_dollars": round(float(closed_pnl.sum()), 2) if not closed_pnl.empty else 0.0,
        "win_count": int((closed_pnl > 0).sum()) if not closed_pnl.empty else 0,
        "loss_count": int((closed_pnl < 0).sum()) if not closed_pnl.empty else 0,
    }
