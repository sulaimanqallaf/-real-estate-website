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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    path = _halt_file_path(config)
    if not path.exists():
        return False, None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return bool(data.get("halted")), data.get("reason")


def status(config: dict[str, Any]) -> dict[str, Any]:
    halted, reason = is_halted(config)
    return {"halted": halted, "reason": reason}


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

    from ..utils import load_config

    parser = argparse.ArgumentParser(description="Manual kill switch for the Phase 7 execution layer.")
    parser.add_argument("action", choices=["halt", "resume", "status"])
    parser.add_argument("--reason", default="Manual halt via CLI")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

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
