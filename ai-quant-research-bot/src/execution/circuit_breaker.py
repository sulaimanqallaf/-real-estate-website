"""Account-level Risk Governor + kill switches (Phase 7 Parts K/L/M).

A triggered breaker BLOCKS NEW ENTRIES ONLY - it never forces an existing
protected exit to stop being managed (Part L: "Existing protected exits
should still be managed when possible"). `check_all()` is the single
function `execution_policy.py`/`order_manager.py` call before any new
entry; it never raises, it returns a structured result the caller acts on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .broker import ACCOUNT_MODE_PAPER, AccountSummary

BREAKER_DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
BREAKER_WEEKLY_LOSS_LIMIT = "WEEKLY_LOSS_LIMIT"
BREAKER_MAX_DRAWDOWN = "MAX_DRAWDOWN"
BREAKER_DATA_STALE = "DATA_STALE"
BREAKER_BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
BREAKER_ACCOUNT_MODE_UNVERIFIED = "ACCOUNT_MODE_UNVERIFIED"
BREAKER_RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
BREAKER_EXCESSIVE_ORDER_REJECTIONS = "EXCESSIVE_ORDER_REJECTIONS"
BREAKER_MANUAL_KILL_SWITCH = "MANUAL_KILL_SWITCH"
BREAKER_ABNORMAL_POSITION_STATE = "ABNORMAL_POSITION_STATE"
BREAKER_MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
BREAKER_MAX_NEW_TRADES_PER_DAY = "MAX_NEW_TRADES_PER_DAY"

ALL_BREAKERS = (
    BREAKER_DAILY_LOSS_LIMIT, BREAKER_WEEKLY_LOSS_LIMIT, BREAKER_MAX_DRAWDOWN, BREAKER_DATA_STALE,
    BREAKER_BROKER_DISCONNECTED, BREAKER_ACCOUNT_MODE_UNVERIFIED, BREAKER_RECONCILIATION_FAILURE,
    BREAKER_EXCESSIVE_ORDER_REJECTIONS, BREAKER_MANUAL_KILL_SWITCH, BREAKER_ABNORMAL_POSITION_STATE,
    BREAKER_MAX_OPEN_POSITIONS, BREAKER_MAX_NEW_TRADES_PER_DAY,
)

DEFAULT_EXECUTION_RISK = {
    "max_risk_per_trade_pct": 0.005,
    "max_total_open_risk_pct": 0.02,
    "max_daily_loss_pct": 0.01,
    "max_weekly_loss_pct": 0.03,
    "max_drawdown_pct": 0.10,
    "max_open_positions": 6,
    "max_new_trades_per_day": 3,
}


@dataclass(frozen=True)
class BreakerResult:
    tripped: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return len(self.tripped) > 0


def effective_execution_risk_limits(config: dict[str, Any]) -> dict[str, Any]:
    """Merge `config.execution_risk` with the DEFAULT (stricter-by-design)
    values, then clamp against any OVERLAPPING existing `portfolio_risk`
    limit so this layer can never be LOOSER than what already existed
    (Part K: "Do NOT increase old risk limits. Where existing limits are
    stricter, preserve the stricter value.") - `max_open_positions` is the
    one field both configs define; every other field here is new to this
    phase, so there's nothing pre-existing to compare it against."""
    limits = dict(DEFAULT_EXECUTION_RISK)
    limits.update(config.get("execution_risk", {}))

    existing_max_positions = config.get("portfolio_risk", {}).get("max_open_positions")
    if existing_max_positions is not None:
        limits["max_open_positions"] = min(limits["max_open_positions"], existing_max_positions)

    existing_max_risk_pct = config.get("portfolio_risk", {}).get("max_total_open_risk_pct")
    if existing_max_risk_pct is not None:
        limits["max_total_open_risk_pct"] = min(limits["max_total_open_risk_pct"], existing_max_risk_pct)

    return limits


# --- manual kill switch: a durable file-based halt state ---------------------------


def _halt_file_path(config: dict[str, Any]) -> Path:
    from ..utils import resolve_path

    path = config.get("execution", {}).get("halt_state_file", "data/runtime/trading_halt.json")
    return resolve_path(path)


def halt(config: dict[str, Any], reason: str = "Manual halt") -> None:
    path = _halt_file_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"halted": True, "reason": reason, "halted_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def resume(config: dict[str, Any]) -> None:
    """Clears ONLY the manual kill switch. Never clears
    reconciliation-failure or hard-breaker state on its own - those are
    separate, independently-persisted conditions this function has no
    access to (Part M: "Resume must not automatically clear broker/account
    reconciliation failures.")."""
    path = _halt_file_path(config)
    if path.exists():
        path.unlink()


def is_halted(config: dict[str, Any]) -> tuple[bool, str | None]:
    """Returns `(True, reason)` if the halt-state file exists but could
    not be read/parsed - Sprint 3 (Reliability: fail-closed audit):
    this is a real, found-not-assumed gap - the file read/JSON parse
    here had no try/except at all, so a corrupted file (a disk error,
    a crash mid-write) would have raised straight through `check_all()`
    (whose own docstring promises "never raises"). Deliberately the
    OPPOSITE conservatism from `read_reconciliation_status()`'s own
    "treat corrupt as no-record, never halt on it" choice - that one is
    defense-in-depth with other independent checks also gating entries;
    THIS is the manual kill switch, the one place the user's own
    explicit halt intent is recorded, with no other independent signal
    anywhere else - an unreadable file could be the kill switch mid-
    write, so the safe assumption is halted, never silently not-halted."""
    path = _halt_file_path(config)
    if not path.exists():
        return False, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("halted")), data.get("reason")
    except (OSError, ValueError) as exc:
        return True, f"halt-state file exists but could not be read ({exc}) - failing closed"


def status(config: dict[str, Any]) -> dict[str, Any]:
    halted, reason = is_halted(config)
    return {"halted": halted, "reason": reason}


# --- durable, cross-process reconciliation state (GitHub Issue #1 P0/P2) -----------
#
# position_monitor.py's own tick is the only place a reconciliation
# discrepancy is actually detected, but it's a SEPARATE process from
# whatever submits a NEW entry (main.py's daily run, approval_bridge's
# manual-approval path) - without persisting the result somewhere every
# process reads, a reconciliation failure flagged by position_monitor was
# invisible to every other entry point, which could keep submitting new
# orders into a broker/local state position_monitor had already found
# inconsistent. This is what "never start a second entry session while
# state unknown" actually requires.


def _reconciliation_file_path(config: dict[str, Any]) -> Path:
    """ALWAYS colocated with `data.journal_dir` - deliberately no
    separate `execution.reconciliation_state_file`-style override (same
    reasoning as `decision_ledger.resolve_db_path()`): a second,
    independent path config is exactly what let a test loading the real
    settings.yaml with only `journal_dir` overridden keep silently
    writing into (and reading stale state back from) the real repo's
    `data/runtime/` directory - caught and fixed during this same
    change. Falls back to the literal default only when even
    `data.journal_dir` is missing."""
    from ..utils import resolve_path

    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir) / "reconciliation_status.json"
    return resolve_path("data/runtime/reconciliation_status.json")


def record_reconciliation_status(config: dict[str, Any], ok: bool, summary: str | None = None) -> None:
    """Called once per `position_monitor.run_one_tick()` - the durable
    record every OTHER process's entry-time breaker check reads via
    `read_reconciliation_status()`."""
    path = _reconciliation_file_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "summary": summary, "checked_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)


def read_reconciliation_status(config: dict[str, Any]) -> tuple[bool, str | None]:
    """No file yet (position_monitor has never ticked - e.g. execution
    mode isn't IBKR_PAPER, or this is a fresh deployment) is treated as
    OK: there is nothing on record to be inconsistent with, and the
    account-mode/connection breakers already gate entries independently
    either way. Never raises on a corrupt/unreadable file - treated the
    same as "no record", since failing closed here would mean a
    transient file-read glitch halts the whole system, which is worse
    than the (already covered elsewhere) risk it would guard against."""
    path = _reconciliation_file_path(config)
    if not path.exists():
        return True, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("ok", True)), data.get("summary")
    except (OSError, ValueError):
        return True, None


# --- individual breaker checks ------------------------------------------------------


def check_manual_kill_switch(config: dict[str, Any]) -> str | None:
    halted, _ = is_halted(config)
    return BREAKER_MANUAL_KILL_SWITCH if halted else None


def check_account_mode(account: AccountSummary | None) -> str | None:
    if account is None or account.account_mode != ACCOUNT_MODE_PAPER:
        return BREAKER_ACCOUNT_MODE_UNVERIFIED
    return None


def check_broker_connection(connection_state: str) -> str | None:
    from .broker import CONNECTION_CONNECTED

    return None if connection_state == CONNECTION_CONNECTED else BREAKER_BROKER_DISCONNECTED


def check_daily_loss(realized_pnl_today_pct: float | None, limits: dict[str, Any]) -> str | None:
    if realized_pnl_today_pct is None:
        return None
    return BREAKER_DAILY_LOSS_LIMIT if realized_pnl_today_pct <= -limits["max_daily_loss_pct"] else None


def check_weekly_loss(realized_pnl_week_pct: float | None, limits: dict[str, Any]) -> str | None:
    if realized_pnl_week_pct is None:
        return None
    return BREAKER_WEEKLY_LOSS_LIMIT if realized_pnl_week_pct <= -limits["max_weekly_loss_pct"] else None


def check_drawdown(current_drawdown_pct: float | None, limits: dict[str, Any]) -> str | None:
    if current_drawdown_pct is None:
        return None
    return BREAKER_MAX_DRAWDOWN if current_drawdown_pct >= limits["max_drawdown_pct"] else None


def check_data_staleness(latest_bar_age_days: int | None, max_age_days: int = 3) -> str | None:
    if latest_bar_age_days is None:
        return None
    return BREAKER_DATA_STALE if latest_bar_age_days > max_age_days else None


def check_reconciliation(reconciliation_ok: bool) -> str | None:
    return None if reconciliation_ok else BREAKER_RECONCILIATION_FAILURE


def check_excessive_rejections(rejections_today: int, max_rejections: int = 3) -> str | None:
    return BREAKER_EXCESSIVE_ORDER_REJECTIONS if rejections_today >= max_rejections else None


def check_abnormal_position_state(unexplained_positions: int) -> str | None:
    return BREAKER_ABNORMAL_POSITION_STATE if unexplained_positions > 0 else None


def check_max_open_positions(current_open_positions: int, limits: dict[str, Any]) -> str | None:
    return BREAKER_MAX_OPEN_POSITIONS if current_open_positions >= limits["max_open_positions"] else None


def check_max_new_trades_per_day(new_trades_today: int, limits: dict[str, Any]) -> str | None:
    return BREAKER_MAX_NEW_TRADES_PER_DAY if new_trades_today >= limits["max_new_trades_per_day"] else None


# --- live risk inputs: the daily/weekly loss, drawdown, open-position and new-trade
# breakers above are only as real as the numbers fed into them. Computed purely from
# data/journal/paper_trades.csv (the same durable record performance_tracker.py and
# portfolio_risk.py already treat as the single source of truth for realized P&L and
# open positions) - never fabricated, and `None`/0 whenever there's nothing to compute
# from yet (a fresh paper_trades.csv), which `check_all()`'s own checks already treat
# as "nothing to trip on" rather than a false positive. -----------------------------


def _closed_trades_since(df: pd.DataFrame, since: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    closed = df[df["status"] != "OPEN"].copy()
    if closed.empty:
        return closed
    exited_at = pd.to_datetime(closed["exited_at"], errors="coerce", utc=True)
    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return closed[exited_at >= since_utc]


def compute_realized_pnl_pct(df: pd.DataFrame, since: datetime, account_equity: float | None) -> float | None:
    """Sum of realized `pnl_dollars` for every trade CLOSED at/after
    `since`, as a fraction of `account_equity` (PAPER trading's fixed
    sizing baseline - `config.risk.account_equity` - not a live broker
    balance, exactly like every other risk-sizing calculation in this
    codebase). `None` only when there's no equity baseline to divide by;
    zero realized trades in the window correctly yields 0.0, not None -
    "no loss yet" must never be treated the same as "cannot compute"."""
    if not account_equity:
        return None
    closed = _closed_trades_since(df, since)
    if closed.empty:
        return 0.0
    pnl = pd.to_numeric(closed["pnl_dollars"], errors="coerce").fillna(0.0).sum()
    return float(pnl) / float(account_equity)


def compute_daily_realized_pnl_pct(df: pd.DataFrame, as_of: datetime, account_equity: float | None) -> float | None:
    since = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    return compute_realized_pnl_pct(df, since, account_equity)


def compute_weekly_realized_pnl_pct(df: pd.DataFrame, as_of: datetime, account_equity: float | None) -> float | None:
    since = (as_of - timedelta(days=as_of.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return compute_realized_pnl_pct(df, since, account_equity)


def compute_current_drawdown_pct(df: pd.DataFrame, account_equity: float | None) -> float | None:
    """Peak-to-current drawdown of the running equity curve
    (`account_equity` + cumulative realized P&L, in CLOSE order), as a
    fraction of the peak. 0.0 (never None) once there's an equity baseline
    but no closed trades yet - there is nothing to be drawn down from."""
    if not account_equity:
        return None
    closed = df[df["status"] != "OPEN"].copy() if not df.empty else df
    if closed.empty:
        return 0.0
    closed["_exited_at"] = pd.to_datetime(closed["exited_at"], errors="coerce", utc=True)
    closed = closed.sort_values("_exited_at")
    pnl = pd.to_numeric(closed["pnl_dollars"], errors="coerce").fillna(0.0)
    equity_curve = float(account_equity) + pnl.cumsum()
    peak = equity_curve.cummax()
    if peak.iloc[-1] <= 0:
        return None  # account blown through zero - not a meaningful percentage, and check_max_drawdown would already be moot
    return float((peak.iloc[-1] - equity_curve.iloc[-1]) / peak.iloc[-1])


def count_new_trades_today(df: pd.DataFrame, as_of: datetime) -> int:
    """Every row opened on `as_of`'s calendar date, OPEN or since closed -
    a trade that already closed today still counts against today's
    new-trade limit; it did happen today."""
    if df.empty:
        return 0
    opened_at = pd.to_datetime(df["opened_at"], errors="coerce", utc=True)
    today = as_of.astimezone(timezone.utc).date() if as_of.tzinfo else as_of.date()
    return int((opened_at.dt.date == today).sum())


def count_current_open_positions(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    return int((df["status"] == "OPEN").sum())


def live_risk_inputs(config: dict[str, Any], as_of: datetime | None = None) -> dict[str, Any]:
    """Everything `check_all()` needs to make the daily/weekly loss,
    drawdown, open-position and new-trade-count breakers real, computed
    fresh from `data/journal/paper_trades.csv`. Callers pass this
    straight into `check_all(config, **live_risk_inputs(config), ...)`
    alongside whatever broker-specific kwargs (`account`,
    `connection_state`, `reconciliation_ok`) they already have."""
    from .. import paper_trades

    as_of = as_of or datetime.now(timezone.utc)
    account_equity = config.get("risk", {}).get("account_equity")
    try:
        df = paper_trades.load_paper_trades_df(config)
    except KeyError:
        # config has no data.journal_dir/paper_trading.paper_trades_file
        # configured (a minimal test config, or a genuinely incomplete
        # deployment config) - nothing to compute from, so every breaker
        # below stays un-tripped rather than crashing the caller. This is
        # the same "Data Unavailable, never fabricated" degradation every
        # other optional data source in this codebase uses.
        df = pd.DataFrame(columns=paper_trades.PAPER_TRADE_COLUMNS)

    return {
        "realized_pnl_today_pct": compute_daily_realized_pnl_pct(df, as_of, account_equity),
        "realized_pnl_week_pct": compute_weekly_realized_pnl_pct(df, as_of, account_equity),
        "current_drawdown_pct": compute_current_drawdown_pct(df, account_equity),
        "new_trades_today": count_new_trades_today(df, as_of),
        "current_open_positions": count_current_open_positions(df),
    }


def check_all(
    config: dict[str, Any],
    account: AccountSummary | None = None,
    connection_state: str | None = None,
    realized_pnl_today_pct: float | None = None,
    realized_pnl_week_pct: float | None = None,
    current_drawdown_pct: float | None = None,
    latest_bar_age_days: int | None = None,
    reconciliation_ok: bool = True,
    rejections_today: int = 0,
    unexplained_positions: int = 0,
    current_open_positions: int = 0,
    new_trades_today: int = 0,
) -> BreakerResult:
    """Run every breaker check and return one structured result. Any
    non-empty `tripped` list means new entries must be blocked - see
    module docstring on what stays managed regardless."""
    limits = effective_execution_risk_limits(config)
    tripped = []

    checks = [
        check_manual_kill_switch(config),
        check_account_mode(account),
        check_broker_connection(connection_state) if connection_state is not None else None,
        check_daily_loss(realized_pnl_today_pct, limits),
        check_weekly_loss(realized_pnl_week_pct, limits),
        check_drawdown(current_drawdown_pct, limits),
        check_data_staleness(latest_bar_age_days),
        check_reconciliation(reconciliation_ok),
        check_excessive_rejections(rejections_today),
        check_abnormal_position_state(unexplained_positions),
        check_max_open_positions(current_open_positions, limits),
        check_max_new_trades_per_day(new_trades_today, limits),
    ]
    tripped = [c for c in checks if c is not None]

    return BreakerResult(tripped=tripped, details={"limits": limits})


def main() -> int:
    import argparse

    from ..utils import load_config, load_env

    parser = argparse.ArgumentParser(description="Manual kill switch for the Phase 7 execution layer.")
    parser.add_argument("action", choices=["halt", "resume", "status"])
    parser.add_argument("--reason", default="Manual halt via CLI")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    load_env()
    config = load_config(args.config)
    if args.action == "halt":
        halt(config, args.reason)
        print("Trading halted.")
    elif args.action == "resume":
        resume(config)
        print("Manual halt cleared (reconciliation/hard-breaker state, if any, is unaffected).")
    else:
        print(json.dumps(status(config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
