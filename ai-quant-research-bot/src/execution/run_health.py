"""Daily Reliability & Safe Automation milestone: read-only health/status
tracking for the scheduled daily run (`src/main.py`'s `run()`), plus the
Telegram failure-alert sender used when a run aborts or raises.

**Read-only / monitoring only - never touches a broker or an LLM
provider, and never places an order or makes a billed API call.** This
module only ever reads log files, a small JSON status marker it writes
itself, the existing TradingAgents spend ledger (`intelligence/
tradingagents_spend.py`, read-only here), and - for `check_launchd_
status()` only - runs the local, read-only `launchctl list` command
(never anything LLM/broker-related). See
`tests/test_execution_run_health_safety.py`'s grep-based guardrail.

The status marker is colocated with `data.journal_dir` - same convention
as `execution/circuit_breaker.py`'s reconciliation-state file and
`intelligence/tradingagents_spend.py`'s ledger: deliberately no separate,
independently-configurable path key (see those modules' docstrings for
the test-pollution footgun that convention avoids).

**Mac health-check follow-up (GitHub Issue #1 follow-up):** the first
version of this module shipped with five real correctness bugs, found
against a real Mac's cached data/logs rather than synthetic test
fixtures:
1. `data_collector.latest_bar_age_days()` crashed on every ticker once
   the cache spanned a DST transition (`'str' object has no attribute
   'tzinfo'`) - fixed in `data_collector.bar_freshness()`, see its
   docstring. This module's stale-data check now also distinguishes a
   genuine parse/read FAILURE from "no data yet" from "fresh", instead
   of letting an exception collapse into "stale data: none."
2. "Last run: never" was indistinguishable from "a run happened before
   this health-tracking code existed" - `read_status()` now also
   surfaces `most_recent_report_file_date` (from `data.reports_dir`'s
   own filenames) as an explicitly separate, historical-only field.
3. "Approximate spend" blended committed (actual, billed) dollars with
   still-outstanding conservative reservations into one number -
   `spend_report()` now reports them separately and never claims the
   blended, cap-enforcement number is "actual spend."
4. The log tail showed old, already-resolved errors with no way to tell
   them apart from a problem in the CURRENT run - `tail_recent_errors()`
   now splits errors into "since the last run started" vs "older/
   historical", using each line's own logged timestamp.
5. "Next scheduled run" was computed purely from config and looked like
   a confirmed schedule even when launchd was never installed -
   `check_launchd_status()` now checks (best-effort, macOS-only) whether
   the job is actually loaded, and the text report labels the computed
   time accordingly.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..utils import redact_secrets, resolve_path

_STATUS_FILENAME = "run_health_status.json"
_LOCK_FILENAME = "main_daily_run.lock"

# Matches `utils.setup_logging()`'s `logging.Formatter("%(asctime)s
# [%(levelname)s] %(message)s")` - `%(asctime)s` defaults to
# "YYYY-MM-DD HH:MM:SS,mmm". Used to tell a recent log error apart from a
# historical one (requirement: "distinguish historical log errors from
# active failures") instead of presenting every error the log file has
# ever recorded as if it just happened.
_LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


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


def most_recent_report_file_date(config: dict[str, Any]) -> str | None:
    """Best-effort, purely informational: the newest `report_YYYY-MM-DD.
    json` date in `data.reports_dir` (written by `report_writer.save_
    reports()` on every run, long before this health-tracking milestone
    existed). This is NEVER treated as - or merged into - `last_success_
    at`: a report file only proves a run got far enough to write a
    report, not that nothing failed, and conflating "a report file from
    before health tracking existed" with "a tracked success" is exactly
    the kind of invented success record this field exists to avoid.
    Returns `None` when `data.reports_dir` is missing/empty - never a
    guessed date."""
    reports_dir_config = config.get("data", {}).get("reports_dir")
    if not reports_dir_config:
        return None
    reports_dir = resolve_path(reports_dir_config)
    if not reports_dir.exists():
        return None
    dates = []
    for path in reports_dir.glob("report_*.json"):
        match = re.match(r"report_(\d{4}-\d{2}-\d{2})\.json$", path.name)
        if match:
            dates.append(match.group(1))
    return max(dates) if dates else None


def read_status(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only snapshot of the persisted run history. Every tracked
    field is `None` ("unknown to health tracking") rather than a
    fabricated zero/empty default - including when the bot has genuinely
    never run AND when it ran before this health-tracking code existed
    (those two cases are NOT distinguishable from `last_run_started_at`
    alone, which is exactly why `most_recent_report_file_date` is
    surfaced as its own, separately-labeled field instead of being
    folded into - or mistaken for - a tracked run record)."""
    data = _read_raw_status(config)
    return {
        "last_run_started_at": data.get("last_run_started_at"),
        "last_run_finished_at": data.get("last_run_finished_at"),
        "last_run_ok": data.get("last_run_ok"),
        "last_run_summary": data.get("last_run_summary"),
        "last_run_failed_symbols": data.get("last_run_failed_symbols", []),
        "last_success_at": data.get("last_success_at"),
        "most_recent_report_file_date": most_recent_report_file_date(config),
    }


def already_succeeded_today(config: dict[str, Any], now: datetime | None = None) -> bool:
    """True if the daily run has already recorded a TRACKED success
    (`last_success_at`) on the SAME calendar date as `now`. Backs
    `main.run()`'s reboot/`RunAtLoad` safety: launchd's
    `StartCalendarInterval` does not retroactively fire a run that was
    entirely missed while the Mac was off or asleep through the
    scheduled time, so the plist also sets `RunAtLoad` to catch that
    case on the next boot/login - but `RunAtLoad` ALSO fires on every
    ordinary login, not only after a missed run, so without this guard a
    normal day with two logins would silently run the whole pipeline
    twice. Deliberately does NOT consider `most_recent_report_file_date`
    (a historical, pre-tracking signal) here - only a run THIS code
    itself tracked counts, so a freshly-added health-tracking install
    never silently skips its first real run of the day. Set
    `FORCE_RERUN=1` to bypass this guard deliberately (e.g. for manual
    testing)."""
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
    configured. This is PURELY a config calculation - it has no way to
    know whether launchd was ever actually told about this schedule; see
    `check_launchd_status()` for that, and never present this value
    without it alongside."""
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


DAILY_LAUNCHD_LABEL = "com.aiquantresearchbot.daily"


def check_launchd_status(label: str = DAILY_LAUNCHD_LABEL) -> dict[str, Any]:
    """Best-effort, macOS-only, READ-ONLY check of whether the daily
    launchd job is actually loaded (`launchctl list <label>`) - the only
    subprocess call anywhere in this module, and never anything other
    than this local OS status query (no LLM, no broker, nothing
    network-facing). Distinct from `compute_next_scheduled_run()`, which
    only echoes back whatever `schedule.daily` says in config and has no
    way to know if launchd was ever told about it at all.

    Returns `{"installed": True|False|None, "detail": str}` - `None`
    ("unknown") when not on macOS or `launchctl` itself couldn't be run;
    never raises."""
    import platform
    import subprocess

    if platform.system() != "Darwin":
        return {"installed": None, "detail": "not macOS - launchctl check not applicable"}

    try:
        result = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"installed": None, "detail": f"could not run launchctl: {exc}"}

    if result.returncode != 0:
        return {"installed": False, "detail": f"not loaded (launchctl list exit code {result.returncode})"}
    return {"installed": True, "detail": "loaded"}


AFTER_CLOSE_LAUNCHD_LABEL = "com.aiquantresearchbot.afterclose"


def compute_next_market_aware_run(config: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """The REAL next scheduled run for the `execution/after_close.py`
    wrapper - unlike `compute_next_scheduled_run()` (a fixed `schedule.
    daily.hour`/`minute`), this walks forward through the actual NYSE
    calendar (`market_calendar.py`) to find the next trading day's
    `market_close + 30min` target, correctly skipping weekends/holidays
    and reporting an early-close day's earlier target - exactly what
    `after_close.py` itself will decide. Returns `None` only if no
    trading day is found within the search horizon (should never happen
    in practice; a defensive bound, not an expected outcome)."""
    from . import market_calendar

    reference = now or datetime.now(timezone.utc)
    for offset in range(14):
        candidate_date = (reference + timedelta(days=offset)).astimezone(market_calendar.NY_TZ).strftime("%Y-%m-%d")
        day = market_calendar.market_day(candidate_date)
        if not day.is_trading_day:
            continue
        target = day.market_close_utc + timedelta(minutes=market_calendar.RUN_AFTER_CLOSE_MINUTES)
        if target <= reference:
            continue  # today's target already passed - keep looking forward
        return {"target_run_utc": target.isoformat(), "is_early_close": day.is_early_close}
    return None


def tail_recent_errors(
    config: dict[str, Any], max_lines: int = 10, log_filename: str = "app.log", now: datetime | None = None
) -> dict[str, list[str]]:
    """ERROR-level lines from the main log file (see `utils.setup_
    logging()`'s `log_dir`/`log_filename` convention), split into
    `"since_last_run_started"` (at or after `read_status()`'s own
    `last_run_started_at` - i.e. plausibly from the CURRENT/most recent
    attempt) and `"historical"` (everything older, or everything when
    there's no tracked run to compare against) - a health report that
    shows old, already-resolved errors with no visual distinction from a
    live failure is actively misleading. Each list independently capped
    at `max_lines`, most-recent-first within the cap. Returns `{"since_
    last_run_started": [], "historical": []}` when the log file doesn't
    exist yet - never raises."""
    empty = {"since_last_run_started": [], "historical": []}
    log_dir = config.get("logging", {}).get("log_dir", "data/reports")
    log_path = resolve_path(log_dir) / log_filename
    if not log_path.exists():
        return empty
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return empty

    last_run_started_at = read_status(config)["last_run_started_at"]
    cutoff: datetime | None = None
    if last_run_started_at:
        try:
            cutoff = datetime.fromisoformat(last_run_started_at)
        except ValueError:
            cutoff = None

    since_last_run: list[str] = []
    historical: list[str] = []
    for raw_line in lines:
        if "[ERROR]" not in raw_line:
            continue
        line = redact_secrets(raw_line.rstrip("\n"))
        match = _LOG_TIMESTAMP_RE.match(line)
        line_dt = None
        if match:
            try:
                line_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                line_dt = None
        if cutoff is not None and line_dt is not None:
            # last_run_started_at is UTC-aware; the log line is naive
            # local-clock text - compare on naive wall-clock terms only
            # (good enough for "is this from the run we just tracked",
            # never used for anything that needs real precision).
            is_recent = line_dt >= cutoff.replace(tzinfo=None)
        else:
            is_recent = False
        (since_last_run if is_recent else historical).append(line)

    return {
        "since_last_run_started": since_last_run[-max_lines:],
        "historical": historical[-max_lines:],
    }


def spend_report(config: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """TradingAgents spend from the existing ledger (`intelligence/
    tradingagents_spend.py`), reported as the committed (actual, billed)
    total kept SEPARATE from any still-outstanding reservation - see
    `tradingagents_spend.spend_breakdown()`'s docstring for why blending
    them (as `spent_today_and_month()` deliberately does, for cap
    enforcement) must never be presented as "actual spend." Returns
    `None` (never a fabricated $0.00) when TradingAgents is disabled or
    the ledger has never been created."""
    if not config.get("intelligence", {}).get("tradingagents", {}).get("enabled", False):
        return None
    from ..intelligence import tradingagents_spend

    db_path = tradingagents_spend.resolve_ledger_path(config)
    if not db_path.exists():
        return None
    try:
        return tradingagents_spend.spend_breakdown(db_path, now or datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - this is best-effort diagnostics, never allowed to crash the health command
        return None


def data_staleness_report(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, list[str]]:
    """Per-ticker cached-data freshness, using `data_collector.bar_
    freshness()` - split into `"stale"` (readable, but older than `data.
    max_bar_age_days_warning`), `"check_failed"` (the freshness check
    itself could not determine an age because of a genuine PARSE/READ
    problem on an existing file - never silently read as "fresh" or
    dropped from the report), `"ok"`, and (not reported as a problem -
    there is nothing to check yet, same "unknown, not stale" convention
    as every other "Data Unavailable" source in this codebase) tickers
    with no cached file at all are simply omitted from all three lists.
    Never collapses `check_failed` into `stale`'s "none" - a failed
    check is reported as failed, not as clean, so the affected tickers
    are always visible."""
    from .. import data_collector

    threshold = config.get("data", {}).get("max_bar_age_days_warning", 3)
    result: dict[str, list[str]] = {"stale": [], "check_failed": [], "ok": []}
    for symbol in config.get("tickers", []):
        freshness = data_collector.bar_freshness(symbol, config, logger, now=now)
        if freshness["status"] == "no_data":
            continue  # no cached file yet - "unknown", not a check failure
        if freshness["status"] != "ok":
            result["check_failed"].append(symbol)
        elif freshness["age_days"] > threshold:
            result["stale"].append(symbol)
        else:
            result["ok"].append(symbol)
    return result


def build_health_report(config: dict[str, Any], logger: logging.Logger, now: datetime | None = None) -> dict[str, Any]:
    """Everything the read-only health/status command reports: last
    tracked run, last tracked success, most recent historical report
    file, next scheduled run (plus whether launchd is actually
    installed - both the plain daily job AND the NYSE-calendar-aware
    after-close wrapper), recent vs historical log errors, committed/
    reserved TradingAgents spend, cached-data freshness (including any
    tickers whose freshness check itself failed), and - Sprint 3 -
    whether position_monitor.py's continuous loop is actually alive
    and ticking (see watchdog.py)."""
    from . import circuit_breaker, watchdog

    reference = now or datetime.now(timezone.utc)
    next_market_aware = compute_next_market_aware_run(config, reference)
    return {
        "status": read_status(config),
        "next_scheduled_run": compute_next_scheduled_run(config, reference),
        "launchd": check_launchd_status(),
        "next_market_aware_run": next_market_aware,
        "launchd_after_close": check_launchd_status(AFTER_CLOSE_LAUNCHD_LABEL),
        "errors": tail_recent_errors(config, now=reference),
        "spend": spend_report(config, reference),
        "data_staleness": data_staleness_report(config, logger, reference),
        "circuit_breaker": circuit_breaker.status(config),
        "position_monitor_heartbeat": watchdog.heartbeat_status(config, now=reference),
    }


def format_health_text(report: dict[str, Any]) -> str:
    status = report["status"]
    lines = [
        "AI Quant Research Bot - health status",
        f"Last TRACKED run started:  {status['last_run_started_at'] or 'never'}",
        f"Last TRACKED run finished: {status['last_run_finished_at'] or 'never'}",
        f"Last TRACKED run result:   {'OK' if status['last_run_ok'] else ('FAILED' if status['last_run_ok'] is not None else 'unknown')}",
    ]
    if status["last_run_summary"]:
        lines.append(f"Last run summary:  {status['last_run_summary']}")
    if status["last_run_failed_symbols"]:
        lines.append(f"Last run failed symbols: {', '.join(status['last_run_failed_symbols'])}")
    lines.append(f"Last TRACKED success:      {status['last_success_at'] or 'never'}")
    if status["most_recent_report_file_date"]:
        lines.append(
            f"Most recent report FILE on disk: {status['most_recent_report_file_date']} "
            "(historical signal only - may predate health tracking, NOT a confirmed successful run)"
        )
    else:
        lines.append("Most recent report file on disk: none found")

    launchd = report["launchd"]
    if launchd["installed"] is True:
        launchd_line = "INSTALLED/LOADED"
    elif launchd["installed"] is False:
        launchd_line = "NOT INSTALLED - the next-scheduled-run time below is only a config calculation, not a confirmed schedule"
    else:
        launchd_line = f"unknown ({launchd['detail']})"
    lines.append(f"launchd daily job ({DAILY_LAUNCHD_LABEL}): {launchd_line}")
    lines.append(f"Next scheduled run (from config, NOT a launchd confirmation): {report['next_scheduled_run'] or 'unknown (schedule.daily not configured)'}")

    after_close_launchd = report["launchd_after_close"]
    if after_close_launchd["installed"] is True:
        after_close_line = "INSTALLED/LOADED"
    elif after_close_launchd["installed"] is False:
        after_close_line = "NOT INSTALLED - the market-aware next run below is only a calendar calculation, not a confirmed schedule"
    else:
        after_close_line = f"unknown ({after_close_launchd['detail']})"
    lines.append(f"launchd after-close wrapper job ({AFTER_CLOSE_LAUNCHD_LABEL}): {after_close_line}")
    next_market_aware = report["next_market_aware_run"]
    if next_market_aware:
        early_note = " (early-close day)" if next_market_aware["is_early_close"] else ""
        lines.append(f"Next NYSE-calendar-aware run (market close + 30min{early_note}): {next_market_aware['target_run_utc']}")
    else:
        lines.append("Next NYSE-calendar-aware run: unknown (no trading day found in the search horizon)")

    spend = report["spend"]
    if spend is None:
        lines.append("TradingAgents spend: unavailable (disabled, or no calls made yet)")
    else:
        lines.append(
            f"TradingAgents spend - COMMITTED (actual, billed): ${spend['committed_today_usd']:.4f} today / "
            f"${spend['committed_month_usd']:.4f} this month"
        )
        lines.append(
            f"TradingAgents spend - RESERVED (conservative, not yet billed): ${spend['reserved_today_usd']:.4f} today / "
            f"${spend['reserved_month_usd']:.4f} this month"
            + (f" ({spend['outstanding_reservation_count']} outstanding reservation(s))" if spend["outstanding_reservation_count"] else "")
        )

    staleness = report["data_staleness"]
    if staleness["check_failed"]:
        lines.append(f"Stale-data CHECK FAILED for: {', '.join(staleness['check_failed'])} (age unknown - treat as a problem, not as fresh)")
    if staleness["stale"]:
        lines.append(f"Stale cached data: {', '.join(staleness['stale'])}")
    if not staleness["check_failed"] and not staleness["stale"]:
        lines.append("Stale cached data: none (all freshness checks succeeded)")

    cb = report["circuit_breaker"]
    lines.append(f"Circuit breaker halted: {cb['halted']}" + (f" ({cb['reason']})" if cb.get("reason") else ""))

    heartbeat = report["position_monitor_heartbeat"]
    if heartbeat["never_started"]:
        lines.append("position_monitor heartbeat: never recorded (not running, or not yet reached its first tick)")
    elif heartbeat["stale"]:
        lines.append(f"position_monitor heartbeat: STALE - last seen {heartbeat['last_heartbeat_at']} ({heartbeat['age_seconds']}s ago)")
    else:
        lines.append(f"position_monitor heartbeat: OK - last seen {heartbeat['last_heartbeat_at']} ({heartbeat['age_seconds']}s ago)")

    errors = report["errors"]
    if errors["since_last_run_started"]:
        lines.append("")
        lines.append("ACTIVE errors (since the last tracked run started):")
        lines.extend(f"  {line}" for line in errors["since_last_run_started"])
    else:
        lines.append("Active errors: none")
    if errors["historical"]:
        lines.append("")
        lines.append("Historical errors (older - for context only, not necessarily still relevant):")
        lines.extend(f"  {line}" for line in errors["historical"])

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
    The only subprocess this ever invokes is the read-only `launchctl
    list` status check (`check_launchd_status()`) - no LLM call, no
    broker call, no order."""
    from ..utils import load_config, load_env, setup_logging

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="run_health.log")
    report = build_health_report(config, logger)
    print(format_health_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
