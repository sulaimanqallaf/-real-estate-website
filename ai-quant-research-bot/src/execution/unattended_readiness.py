"""Unattended PAPER-mode readiness check (AI Quant Trading Platform
sprint, deliverable D: "design unattended paper operation, but DO NOT
automatically enable execution until broker-facing safety checks
pass").

**This is a pre-flight SUMMARY for a human operator, not a new gate.**
Every individual check it reports already exists and already runs on
every real trading decision (`circuit_breaker.check_all()`,
`pretrade_checks.py`, `reconciliation.py`) - this module adds nothing
to the enforcement path. What it adds is a single, read-only place to
ask "is it actually sane to flip `autonomous_paper.enabled`/
`auto_execute.enabled` to true right now" BEFORE doing so, and to
re-check periodically afterward for drift (e.g. `ibapi` silently
missing after a Python upgrade) without having to remember which five
different modules to check by hand.

**Cannot verify a real broker connection from here.** `is_ready()`
checks what's checkable without one (config sanity, whether `ibapi` is
importable, current circuit-breaker/reconciliation state, whether
Telegram alerting is configured so the kill switch is actually
reachable) - it does NOT and cannot call
`ibkr_client.verify_paper_account()` for real, because that needs a
live TWS/Gateway session this environment has no path to. See
`docs/platform/BLOCKERS.md` item 1. A report where every static check
passes is "ready to attempt," not "broker-verified" - the two are
kept explicitly distinct in `ReadinessReport.summary()`.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from typing import Any

from . import circuit_breaker


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    checks: list[ReadinessCheck] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> str:
        lines = ["Unattended PAPER-mode readiness (static checks only - NOT a broker-verified connection test):"]
        for check in self.checks:
            lines.append(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
        lines.append("")
        if self.all_passed:
            lines.append(
                "All static checks passed. This does NOT confirm a real IBKR TWS/Gateway "
                "session is reachable - verify that separately on your Mac before relying on "
                "unattended operation (see docs/platform/BLOCKERS.md item 1)."
            )
        else:
            lines.append("NOT READY - resolve the FAIL item(s) above before enabling unattended autonomy.")
        return "\n".join(lines)


def _check_execution_mode(config: dict[str, Any]) -> ReadinessCheck:
    mode = config.get("execution", {}).get("mode", "DRY_RUN")
    if mode == "IBKR_PAPER":
        return ReadinessCheck("execution.mode", True, "IBKR_PAPER")
    return ReadinessCheck("execution.mode", False, f"{mode!r} - must be IBKR_PAPER for any order to ever be submitted (DRY_RUN is the safe default and is expected here before you're ready)")


def _check_not_live_capable(config: dict[str, Any]) -> ReadinessCheck:
    from ..utils import VALID_EXECUTION_MODES

    has_live_mode = "IBKR_LIVE" in VALID_EXECUTION_MODES or "LIVE" in VALID_EXECUTION_MODES
    return ReadinessCheck("no live-money mode exists", not has_live_mode, "confirmed: this codebase has no LIVE execution mode" if not has_live_mode else "UNEXPECTED: a live mode exists")


def _check_ibapi_importable(config: dict[str, Any]) -> ReadinessCheck:
    found = importlib.util.find_spec("ibapi") is not None
    return ReadinessCheck("ibapi installed", found, "importable" if found else "not installed - `pip install ibapi` on the machine that will run position_monitor.py/main.py against IBKR_PAPER")


def _check_not_halted(config: dict[str, Any]) -> ReadinessCheck:
    halted, reason = circuit_breaker.is_halted(config)
    return ReadinessCheck("manual kill switch", not halted, "not halted" if not halted else f"HALTED: {reason}")


def _check_reconciliation(config: dict[str, Any]) -> ReadinessCheck:
    """`read_reconciliation_status()` itself treats "no record yet" as
    `(True, None)` - see its own docstring for why that's the right
    default (nothing on record to be inconsistent with). Reflected
    here as-is, not re-interpreted."""
    ok, summary = circuit_breaker.read_reconciliation_status(config)
    detail = "OK (or no reconciliation run yet)" if ok else f"FAILED: {summary}"
    return ReadinessCheck("last reconciliation", ok, detail)


def _check_telegram_configured(config: dict[str, Any]) -> ReadinessCheck:
    has_token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    has_chat = bool(os.environ.get("TELEGRAM_CHAT_ID"))
    configured = has_token and has_chat
    return ReadinessCheck(
        "Telegram alerting configured", configured,
        "configured - the /halt kill switch and failure alerts are reachable" if configured
        else "NOT configured - you would have no remote kill switch or failure alert if something goes wrong unattended",
    )


def _check_autonomy_still_requires_explicit_opt_in(config: dict[str, Any]) -> ReadinessCheck:
    """Not a pass/fail gate on the CURRENT value - a reminder check that
    always passes, documenting that both flags default to false and
    were not silently changed by anything in this codebase. Exists so
    `is_ready()`'s report is self-contained without the operator having
    to go re-read settings.yaml separately."""
    autonomous = config.get("autonomous_paper", {}).get("enabled", False)
    auto_execute = config.get("autonomous_paper", {}).get("auto_execute", {}).get("enabled", False)
    return ReadinessCheck(
        "autonomy flags (informational)", True,
        f"autonomous_paper.enabled={autonomous}, auto_execute.enabled={auto_execute}",
    )


def check_readiness(config: dict[str, Any]) -> ReadinessReport:
    return ReadinessReport(checks=[
        _check_execution_mode(config),
        _check_not_live_capable(config),
        _check_ibapi_importable(config),
        _check_not_halted(config),
        _check_reconciliation(config),
        _check_telegram_configured(config),
        _check_autonomy_still_requires_explicit_opt_in(config),
    ])


def main() -> int:
    from ..utils import load_config, load_env

    load_env()
    config = load_config(None)
    report = check_readiness(config)
    print(report.summary())
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
