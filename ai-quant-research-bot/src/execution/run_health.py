"""Daily Reliability & Safe Automation milestone: read-only health/status
tracking for the scheduled daily run (`src/main.py`'s `run()`), plus the
Telegram failure-alert sender used when a run aborts or raises.

**Read-only / monitoring only - never touches execution, a broker, or an
LLM provider.** This module only ever reads log files, a small JSON
status marker it writes itself, and the existing TradingAgents spend
ledger (`intelligence/tradingagents_spend.py`, read-only here via
`spent_today_and_month()`). See
`tests/test_execution_run_health_safety.py`'s grep-based guardrail.

The status marker is colocated with `data.journal_dir` - same convention
as `execution/circuit_breaker.py`'s reconciliation-state file and
`intelligence/tradingagents_spend.py`'s ledger: deliberately no separate,
independently-configurable path key (see those modules' docstrings for
the test-pollution footgun that convention avoids).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..utils import redact_secrets, resolve_path

_STATUS_FILENAME = "run_health_status.json"
_LOCK_FILENAME = "main_daily_run.lock"


def _journal_dir(config: dict[str, Any]) -> Path:
    journal_dir = config.get("data", {}).get("journal_dir")
    if journal_dir:
        return resolve_path(journal_dir)
    return resolve_path("data/journal")


def resolve_lock_path(config: dict[str, Any]) -> Path:
    """The singleton-lock path for `main.py`'s daily run - same mechanism
    (`execution/process_lock.py`) already used by `position_monitor.py`
    and `approval_listener.py`."""
    return _journal_dir(config) / _LOCK_FILENAME


def _status_file_path(config: dict[str, Any]) -> Path:
    return _journal_dir(config) / _STATUS_FILENAME


def _now_iso(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _read_raw_status(config: dict[str, Any]) -> dict[str, Any]:
    path = _status_file_path(config)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_raw_status(config: dict[str, Any], data: dict[str, Any]) -> None:
    path = _status_file_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def record_run_start(config: dict[str, Any], now: datetime | None = None) -> None:
    """Called once at the very top of `main.py`'s `run()`, right after the
    singleton lock is acquired - before any network/LLM/broker call."""
    data = _read_raw_status(config)
    data["last_run_started_at"] = _now_iso(now)
    data["last_run_finished_at"] = None
    data["last_run_ok"] = None
    _write_raw_status(config, data)


def record_run_result(
    config: dict[str, Any],
    ok: bool,
    summary: str,
    failed_symbols: list[str] | None = None,
    now: datetime | None = None,
) -> None:
    """Called once at the end of `run()` - on a clean return AND from the
    `except` branch that also sends the Telegram failure alert, so the
    health status always reflects the true outcome of the most recent
    attempt, never just the most recent success."""
    data = _read_raw_status(config)
    finished_at = _now_iso(now)
    data["last_run_finished_at"] = finished_at
    data["last_run_ok"] = ok
    data["last_run_summary"] = redact_secrets(summary)
    data["last_run_failed_symbols"] = failed_symbols or []
    if ok:
        data["last_success_at"] = finished_at
    _write_raw_status(config, data)


def read_status(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only snapshot of the persisted run history. Every field is
    `None` ("unknown" - e.g. the bot has never run, or the file predates
    this milestone) rather than a fabricated zero/empty default."""
    data = _read_raw_status(config)
    return {
        "last_run_started_at": data.get("last_run_started_at"),
        "last_run_finished_at": data.get("last_run_finished_at"),
        "last_run_ok": data.get("last_run_ok"),
        "last_run_summary": data.get("last_run_summary"),
        "last_run_failed_symbols": data.get("last_run_failed_symbols", []),
        "last_success_at": data.get("last_success_at"),
    }


def already_succeeded_today(config: dict[str, Any], now: datetime | None = None) -> bool:
    """True if the daily run has already recorded a success (`last_
    success_at`) on the SAME calendar date as `now`. Backs `main.run()`'s
    reboot/`RunAtLoad` safety: launchd's `StartCalendarInterval` does not
    retroactively fire a run that was entirely missed while the Mac was
    off or asleep through the scheduled time, so the plist also sets
    `RunAtLoad` to catch that case on the next boot/login - but
    `RunAtLoad` ALSO fires on every ordinary login, not only after a
    missed run, so without this guard a normal day with two logins would
    silently run the whole pipeline twice. Set `FORCE_RERUN=1` to bypass
    this guard deliberately (e.g. for manual testing)."""
    last_success = read_status(config)["last_success_at"]
    if not last_success:
        return False
    reference = now or datetime.now(timezone.utc)
    try:
        last_success_dt = datetime.fromisoformat(last_success)
    except ValueError:
        return False
    return last_success_dt.date() == reference.date()


def compute_next_scheduled_run(config: dict[str, Any], now: datetime | None = None) -> str | None:
    """Next `StartCalendarInterval` fire time, computed from the optional
    `schedule.daily.hour`/`schedule.daily.minute` config keys (meant to be
    kept in sync with the actual launchd plist - see README section 6).
    Returns `None` ("unknown", not "midnight") when that schedule isn't
    configured, since launchd's own schedule isn't otherwise introspectable
    from Python without shelling out to `launchctl` (fragile, Mac-only,
    and not something this read-only module should depend on)."""
    daily = config.get("schedule", {}).get("daily", {})
    hour = daily.get("hour")
    minute = daily.get("minute", 0)
    if hour is None:
        return None

    reference = now or datetime.now(timezone.utc)
    candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= reference:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def tail_recent_errors(config: dict[str, Any], max_lines: int = 10, log_filename: str = "app.log") -> list[str]:
    """Last `max_lines` ERROR-level lines from the main log file (see
    `utils.setup_logging()`'s `log_dir`/`log_filename` convention).
    Returns `[]` when the log file doesn't exist yet - never raises, this
    is read-only diagnostics, not something that should ever crash the
    health command itself."""
    log_dir = config.get("logging", {}).get("log_dir", "data/reports")
    log_path = resolve_path(log_dir) / log_filename
    if not log_path.exists():
        return []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    error_lines = [line.rstrip("\n") for line in lines if "[ERROR]" in line]
    return [redact_secrets(line) for line in error_lines[-max_lines:]]


def approximate_spend(config: dict[str, Any], now: datetime | None = None) -> dict[str, float] | None:
    """Today/this-month TradingAgents spend from the existing ledger
    (`intelligence/tradingagents_spend.py`) - read-only, same reserve+
    commit totals the spend-cap enforcement itself uses. Returns `None`
    (never a fabricated 0.0) when TradingAgents is disabled or the ledger
    has never been created."""
    if not config.get("intelligence", {}).get("tradingagents", {}).get("enabled", False):
        return None
    from ..intelligence import tradingagents_spend

    db_path = tradingagents_spend.resolve_ledger_path(config)
    if not db_path.exists():
        return None
    try:
        return tradingagents_spend.spent_today_and_month(db_path, now or datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - this is best-effort diagnostics, never allowed to crash the health command
        return None


def stale_tickers(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> list[str]:
    """Tickers whose most recently cached daily bar is older than
    `data.max_bar_age_days_warning` (default 3 calendar days - generous
    enough to span an ordinary weekend without a false alarm on Monday
    morning, before that day's run has fetched fresh data yet). Reuses
    `data_collector.latest_bar_age_days()` - the same staleness signal
    `circuit_breaker.check_all()` already uses - read-only, no network
    call."""
    from .. import data_collector

    threshold = config.get("data", {}).get("max_bar_age_days_warning", 3)
    stale: list[str] = []
    for symbol in config.get("tickers", []):
        age = data_collector.latest_bar_age_days(symbol, config, logger, now=now)
        if age is not None and age > threshold:
            stale.append(symbol)
    return stale


def build_health_report(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, Any]:
    """Everything the read-only health/status command reports: last run,
    last success, next scheduled run, latest errors, approximate spend,
    and any tickers whose cached data has gone stale."""
    from . import circuit_breaker

    reference = now or datetime.now(timezone.utc)
    return {
        "status": read_status(config),
        "next_scheduled_run": compute_next_scheduled_run(config, reference),
        "latest_errors": tail_recent_errors(config),
        "approximate_spend_usd": approximate_spend(config, reference),
        "stale_tickers": stale_tickers(config, logger, reference),
        "circuit_breaker": circuit_breaker.status(config),
    }


def format_health_text(report: dict[str, Any]) -> str:
    status = report["status"]
    lines = [
        "AI Quant Research Bot - health status",
        f"Last run started:  {status['last_run_started_at'] or 'never'}",
        f"Last run finished: {status['last_run_finished_at'] or 'never'}",
        f"Last run result:   {'OK' if status['last_run_ok'] else ('FAILED' if status['last_run_ok'] is not None else 'unknown')}",
    ]
    if status["last_run_summary"]:
        lines.append(f"Last run summary:  {status['last_run_summary']}")
    if status["last_run_failed_symbols"]:
        lines.append(f"Last run failed symbols: {', '.join(status['last_run_failed_symbols'])}")
    lines.append(f"Last success:       {status['last_success_at'] or 'never'}")
    lines.append(f"Next scheduled run: {report['next_scheduled_run'] or 'unknown (schedule.daily not configured)'}")

    spend = report["approximate_spend_usd"]
    if spend is None:
        lines.append("Approximate TradingAgents spend: unavailable (disabled, or no calls made yet)")
    else:
        lines.append(f"Approximate TradingAgents spend: ${spend['today_usd']:.4f} today / ${spend['month_usd']:.4f} this month")

    if report["stale_tickers"]:
        lines.append(f"Stale cached data (> warning threshold): {', '.join(report['stale_tickers'])}")
    else:
        lines.append("Stale cached data: none")

    cb = report["circuit_breaker"]
    lines.append(f"Circuit breaker halted: {cb['halted']}" + (f" ({cb['reason']})" if cb.get("reason") else ""))

    if report["latest_errors"]:
        lines.append("")
        lines.append("Latest errors:")
        lines.extend(f"  {line}" for line in report["latest_errors"])
    else:
        lines.append("Latest errors: none")

    return "\n".join(lines) + "\n"


def send_failure_alert(config: dict[str, Any], logger: logging.Logger, label: str, summary: str) -> bool:
    """Best-effort Telegram alert for a failed scheduled run. Redacts
    `summary` before it ever reaches a log line or the Telegram API (see
    `utils.redact_secrets()`) - never raises, since a failure in the
    alerting path itself must never mask or replace the original failure
    it's reporting on."""
    import os

    from . import lifecycle_notices

    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.warning("Run failure alert not sent: Telegram not configured.")
            return False
        from .. import telegram_bot

        text = lifecycle_notices.format_run_failure_notice(label, redact_secrets(summary))
        return telegram_bot.send_telegram_message(token, chat_id, text, logger)
    except Exception as exc:  # noqa: BLE001 - alerting itself must never raise
        logger.error("Failed to send run failure alert: %s", redact_secrets(str(exc)))
        return False


def main() -> int:
    """`python -m src.execution.run_health` - prints the health report.
    Read-only: no network call other than none (Telegram is only ever
    touched by `send_failure_alert()`, not by this CLI)."""
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="run_health.log")
    report = build_health_report(config, logger)
    print(format_health_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
