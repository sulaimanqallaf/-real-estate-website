"""The ONLY place this dashboard backend touches the main bot's code or
data. Every function here is read-only: it loads the same `config/
settings.yaml` the bot itself uses (for file paths only - no network
call), and reads already-written journals/logs/caches/ledgers. Nothing
here ever writes to any bot-owned file, places an order, calls an LLM
provider, or imports anything execution/broker-facing.

**Hard safety invariant, enforced structurally** (see
`tests/test_readonly_safety.py`): this module may import from
`src.execution.run_health`, `src.execution.circuit_breaker`,
`src.execution.unattended_readiness`, `src.ml.decision_ledger`,
`src.ml.model_registry`, `src.ml.model_events`, `src.universe`,
`src.intelligence.tradingagents_adapter`, `src.intelligence.
tradingagents_spend`, `src.paper_trades`, and `src.utils` only - never
`src.execution.order_manager`, `src.execution.execution_policy`,
`src.execution.ibkr_client`, `src.execution.broker`,
`src.execution.approval_bridge`, or `src.main.run`. The dashboard has
no code path that can place an order, change a risk setting, or
trigger a real run, by construction. `positions_and_orders()` below
reads the SAME `executions.jsonl` file `order_manager.ExecutionJournal`
writes, but does so with a plain local JSONL parser rather than
importing `order_manager` at all - entirely sidesteps needing that
module in this file's import graph, and its constructor's side-
effecting `mkdir` along with it (see that function's own docstring).
`backtest_performance()` similarly reads an already-saved
`backtest_*.csv` report file directly - it never imports `backtester.
py` or calls `run_backtest()`; this dashboard displays the most recent
backtest someone already ran, it never triggers a new one.

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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """A plain, local, read-only JSONL parser - deliberately NOT the
    execution package's own journal class (see this file's module
    docstring for exactly why: avoids that class's constructor side
    effect and keeps the execution-journal module out of this file's
    import graph entirely). A malformed line is skipped, never raised -
    this is dashboard diagnostics, not the real restart-recovery path,
    which has its own, separate, stricter handling elsewhere, untouched
    by this."""
    if not path.exists():
        return []
    rows = []
    import json

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def positions_and_orders(limit: int = 50) -> dict[str, Any]:
    """The tail of the real execution journal (`executions.jsonl`) -
    every order-lifecycle state change the bot has ever recorded
    (submission, fill, rejection, protection sync, close). This is
    research/paper-trading history, never a live brokerage feed."""
    from src.utils import resolve_path

    config = get_config()
    journal_path = config.get("execution", {}).get("journal_path", "data/journal/executions.jsonl")
    rows = _read_jsonl(resolve_path(journal_path))
    return _redact_deep({"rows": rows[-limit:], "total_count": len(rows)})


def risk_status() -> dict[str, Any]:
    """Circuit-breaker state plus the EFFECTIVE risk limits currently
    in force (`circuit_breaker.effective_execution_risk_limits()`) -
    read-only; this never changes a limit, only reports the ones
    already configured."""
    config = get_config()
    return _redact_deep({
        "breaker": circuit_breaker.status(config),
        "limits": circuit_breaker.effective_execution_risk_limits(config),
    })


def backtest_performance() -> dict[str, Any] | None:
    """The most recently saved `backtest_*.csv` report
    (`backtester.save_backtest_report()`'s own output) - this dashboard
    never calls `run_backtest()` itself; it only ever displays a report
    someone already generated by running the backtester separately.
    Returns `None` when no backtest report has ever been saved."""
    import pandas as pd
    from src.utils import resolve_path

    config = get_config()
    reports_dir = resolve_path(config.get("data", {}).get("reports_dir", "data/reports"))
    if not reports_dir.exists():
        return None
    candidates = sorted(reports_dir.glob("backtest_*.csv"))
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        df = pd.read_csv(latest)
    except Exception:  # noqa: BLE001 - diagnostics must never crash on a malformed report file
        return None
    return _redact_deep({"report_date": latest.stem.replace("backtest_", ""), "rows": df.to_dict("records")})


def learning_experiments(limit: int = 20) -> dict[str, Any]:
    """Champion/challenger model registry state + the model lifecycle
    event log (promotions/rollbacks/training runs) - read-only, never
    trains or promotes anything itself."""
    from src.ml import model_events, model_registry
    from src.utils import resolve_path

    config = get_config()
    registry_dir = resolve_path(config.get("ml", {}).get("registry_dir", "data/models"))
    registry = model_registry.ModelRegistry(registry_dir)
    try:
        models = [m.to_dict() for m in registry.list_metadata()]
    except Exception:  # noqa: BLE001 - diagnostics must never crash on a malformed registry entry
        models = []
    events = model_events.read_events(config)
    return _redact_deep({"models": models, "events": events[-limit:]})


def market_scanner(limit: int = 100) -> dict[str, Any] | None:
    """The most recently saved daily `report_*.json` (every ticker the
    last real run scored, sorted by score) PLUS the currently resolved
    universe (`universe.resolve_universe()` - pure, read-only, makes no
    network call). Returns `None` when no report has ever been saved."""
    import json

    from src.utils import resolve_path

    config = get_config()
    reports_dir = resolve_path(config.get("data", {}).get("reports_dir", "data/reports"))
    universe_symbols: list[str] = []
    try:
        from src import universe as universe_module

        universe_symbols = universe_module.resolve_universe(config, _diagnostics_logger())
    except Exception:  # noqa: BLE001
        universe_symbols = []

    if not reports_dir.exists():
        return {"report_date": None, "tickers": [], "universe_size": len(universe_symbols)} if universe_symbols else None
    candidates = sorted(reports_dir.glob("report_*.json"))
    if not candidates:
        return {"report_date": None, "tickers": [], "universe_size": len(universe_symbols)} if universe_symbols else None
    latest = candidates[-1]
    try:
        with open(latest, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"report_date": None, "tickers": [], "universe_size": len(universe_symbols)}
    tickers = data.get("tickers", [])[:limit]
    return _redact_deep({"report_date": data.get("report_date"), "tickers": tickers, "universe_size": len(universe_symbols)})


def _diagnostics_logger():
    import logging

    return logging.getLogger("dashboard.readonly")


def research_analytics(hypothesis_limit: int = 20) -> dict[str, Any]:
    """Phase 7 (AI Quant Trading Platform OSS integration sprint):
    QuantStats-powered paper-trading performance (Sharpe/Sortino/max
    drawdown/profit factor/transaction costs), a native Monte Carlo
    bootstrap stress test over the same real closed trades, a
    market-regime performance breakdown, and the research-sandbox
    hypothesis ledger (Phase 2's RD-Agent concept - results only,
    nothing here can promote a hypothesis into production). Every
    sub-field is independently `None`/empty (never fabricated) when
    its own source has nothing yet - `build_performance_report()` and
    `build_regime_breakdown()` already return `None` rather than a
    zeroed report, and this function passes that straight through."""
    from src.analytics import monte_carlo, performance_report, regime_breakdown, trade_history
    from src.research import hypothesis_ledger

    config = get_config()
    logger = _diagnostics_logger()

    try:
        performance = performance_report.build_performance_report(config, logger)
    except Exception:  # noqa: BLE001 - dashboard diagnostics must never crash on a missing/misconfigured OSS quant venv
        performance = None

    try:
        regime = regime_breakdown.build_regime_breakdown(config, logger)
    except Exception:  # noqa: BLE001
        regime = None

    mc = None
    try:
        closed = trade_history.closed_trade_rows(config)
        returns_pct = [float(r["pnl_pct"]) for r in closed if r.get("pnl_pct") is not None]
        if returns_pct:
            mc = monte_carlo.run_monte_carlo_stress_test(returns_pct)
    except Exception:  # noqa: BLE001
        mc = None

    try:
        db_path = hypothesis_ledger.default_db_path(config)
        hypotheses = hypothesis_ledger.query_results(db_path)[-hypothesis_limit:]
    except Exception:  # noqa: BLE001
        hypotheses = []

    return _redact_deep({
        "performance_report": performance,
        "monte_carlo": mc,
        "regime_breakdown": regime,
        "hypotheses": hypotheses,
    })


def data_provider_health() -> dict[str, Any]:
    """Whether each OPTIONAL data provider is configured - a pure
    `is_configured()`-style check for every one (env var present, or
    for crypto, whether the isolated oss_quant venv exists), never a
    real network call. `market_provider.py` (yfinance) isn't included:
    it's the main pipeline's primary, always-on provider, not one of
    the optional add-ons this panel is about."""
    from src.data_providers import crypto_provider, forex_provider, macro_provider, options_flow_provider, sec_provider

    config = get_config()
    options_flow = options_flow_provider.get_default_provider(config)
    providers = [
        {"name": "sec_13f_form4", "configured": sec_provider.sec_identity_configured()},
        {"name": "fred_macro", "configured": macro_provider.fred_configured()},
        {"name": "options_flow", "configured": options_flow.is_configured()},
        {"name": "forex", "configured": forex_provider.forex_configured()},
        {"name": "crypto", "configured": crypto_provider.crypto_configured(config)},
    ]
    return _redact_deep({"providers": providers})
