"""Opt-in launchd schedule installer for Sprint 4/5's new autonomous
agents (Sprint 5, Priority 9: "explicit, opt-in, conflict-safe daily
scheduling for Scholar, News and Strategy Lab, plus periodic
fleet-health monitoring").

**Opt-in means opt-in - nothing here runs on its own.** Every function
in this module is inert until a human explicitly calls `install()` for
one job at a time (from `mac_launcher/Manage Agent Schedules.command`,
which asks "install this job? [y/N]" per job, never a blanket "install
everything"). Nothing in this codebase calls `install()` automatically
- not a test, not another agent, not a scheduled trigger. This is the
first code in this project that actually writes a `.plist` file and
calls `launchctl load`/`unload`; every prior sprint's launchd setup
was README-documented prose only (see README "Project structure"/
section 6) - the four jobs this module can install are new,
Sprint-4/5-only jobs, never a change to anything that existed before.

**Conflict-safe by hard assertion, not by convention.**
`RESERVED_EXISTING_LABELS` lists the project's 7 pre-existing
`com.aiquantresearchbot.*` launchd jobs by name; `assert_label_is_new()`
raises if asked to install/uninstall any of them, and
`DEFAULT_JOB_SPECS`' own labels are checked against that set at import
time (`test_launchd_installer_safety.py` pins this). This module can
never touch `com.aiquantresearchbot.daily`/`approvals`/`monitor`/
`retrain`/`weeklyreport`/`afterclose`/`watchdog` even if called
incorrectly.

**Never embeds a credential.** Every `JobSpec`'s `ProgramArguments`
is just `[python_path, "-m", module]` - the agent process itself
reads `.env` via `load_env()` exactly like every other command in
this codebase; no credential is ever written into a `.plist` file or
passed as a launchd `EnvironmentVariables` key.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

LABEL_RESEARCH_SCHOLAR = "com.aiquantresearchbot.researchscholar"
LABEL_NEWS_AGENT = "com.aiquantresearchbot.newsagent"
LABEL_STRATEGY_LAB = "com.aiquantresearchbot.strategylab"
LABEL_AGENT_FLEET_HEALTH = "com.aiquantresearchbot.agentfleethealth"

# The project's 7 pre-existing jobs (see README "Project structure" /
# section 6) - this module must NEVER be able to install, uninstall,
# or report on any of these under its own label-based API.
RESERVED_EXISTING_LABELS = frozenset(
    {
        "com.aiquantresearchbot.daily",
        "com.aiquantresearchbot.approvals",
        "com.aiquantresearchbot.monitor",
        "com.aiquantresearchbot.retrain",
        "com.aiquantresearchbot.weeklyreport",
        "com.aiquantresearchbot.afterclose",
        "com.aiquantresearchbot.watchdog",
    }
)


class ReservedLabelError(ValueError):
    """Raised when asked to install/uninstall one of the 7 existing jobs."""


def assert_label_is_new(label: str) -> None:
    if label in RESERVED_EXISTING_LABELS:
        raise ReservedLabelError(f"{label!r} is one of the 7 existing com.aiquantresearchbot.* jobs - this module must never touch it.")


@dataclass(frozen=True)
class JobSpec:
    name: str  # agent_name, matches registry.py/heartbeat.py's AGENT_NAME
    label: str
    module: str  # e.g. "src.agents.research_scholar", run as `python -m <module>`
    description: str
    # Exactly one of these two schedule kinds is set.
    calendar_hour: int | None = None
    calendar_minute: int | None = None
    interval_seconds: int | None = None


DEFAULT_JOB_SPECS: tuple[JobSpec, ...] = (
    JobSpec("research_scholar", LABEL_RESEARCH_SCHOLAR, "src.agents.research_scholar", "Daily academic research (OpenAlex/arXiv/Semantic Scholar)", calendar_hour=11, calendar_minute=30),
    JobSpec("news_agent", LABEL_NEWS_AGENT, "src.agents.news_agent", "Daily SEC filings + FRED economic data briefing", calendar_hour=12, calendar_minute=0),
    JobSpec("strategy_lab", LABEL_STRATEGY_LAB, "src.agents.strategy_lab", "Daily autonomous strategy candidate generation", calendar_hour=12, calendar_minute=30),
    JobSpec("agent_fleet_health", LABEL_AGENT_FLEET_HEALTH, "src.agents.health_report", "Periodic fleet health report + Telegram failure alerts", interval_seconds=1800),
)

for _spec in DEFAULT_JOB_SPECS:
    assert_label_is_new(_spec.label)


def plist_path(label: str) -> Path:
    assert_label_is_new(label)
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def generate_plist(spec: JobSpec, python_path: Path, repo_root: Path, log_dir: Path) -> str:
    """Pure string generation - no file I/O, no credential, matches
    the exact XML shape already documented (as copy-paste prose) in
    README's own plist examples for the 7 existing jobs."""
    assert_label_is_new(spec.label)

    if spec.interval_seconds is not None:
        schedule_xml = f"    <key>StartInterval</key>\n    <integer>{spec.interval_seconds}</integer>\n"
    else:
        schedule_xml = (
            "    <key>StartCalendarInterval</key>\n    <dict>\n"
            f"        <key>Hour</key>\n        <integer>{spec.calendar_hour}</integer>\n"
            f"        <key>Minute</key>\n        <integer>{spec.calendar_minute}</integer>\n"
            "    </dict>\n"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"    <key>Label</key>\n    <string>{spec.label}</string>\n"
        "    <key>ProgramArguments</key>\n    <array>\n"
        f"        <string>{python_path}</string>\n        <string>-m</string>\n        <string>{spec.module}</string>\n"
        "    </array>\n"
        f"    <key>WorkingDirectory</key>\n    <string>{repo_root}</string>\n"
        f"{schedule_xml}"
        f"    <key>StandardOutPath</key>\n    <string>{log_dir / (spec.name + '.launchd.out.log')}</string>\n"
        f"    <key>StandardErrorPath</key>\n    <string>{log_dir / (spec.name + '.launchd.err.log')}</string>\n"
        "</dict>\n</plist>\n"
    )


def is_darwin() -> bool:
    return platform.system() == "Darwin"


def is_installed(label: str, logger: logging.Logger) -> bool | None:
    """Read-only `launchctl list <label>` check - same pattern as
    `execution/run_health.check_launchd_status()`. Returns `None`
    ("unknown") off macOS or if `launchctl` itself couldn't run;
    never raises."""
    assert_label_is_new(label)
    if not is_darwin():
        return None
    try:
        result = subprocess.run(["launchctl", "list", label], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("launchd_installer: could not run launchctl to check %s: %s", label, exc)
        return None
    return result.returncode == 0


def install(spec: JobSpec, python_path: Path, repo_root: Path, log_dir: Path, logger: logging.Logger) -> bool:
    """Writes the `.plist` to `~/Library/LaunchAgents/` and runs
    `launchctl load -w`. macOS-only (returns `False` elsewhere, never
    raises); the ONE function in this module with a real side effect
    outside this repo - every caller must only reach it after an
    explicit human confirmation (see `mac_launcher/Manage Agent
    Schedules.command`), never automatically."""
    assert_label_is_new(spec.label)
    if not is_darwin():
        logger.warning("launchd_installer: install(%s) skipped - not macOS.", spec.label)
        return False

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = plist_path(spec.label)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generate_plist(spec, python_path, repo_root, log_dir), encoding="utf-8")
        result = subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("launchd_installer: install(%s) failed: %s", spec.label, exc)
        return False

    if result.returncode != 0:
        logger.error("launchd_installer: launchctl load for %s exited %s: %s", spec.label, result.returncode, result.stderr)
        return False
    return True


def uninstall(label: str, logger: logging.Logger) -> bool:
    """Runs `launchctl unload`, then removes the `.plist` file. Never
    raises; returns `True` if the end state is "not loaded, file
    gone" (idempotent - uninstalling something already uninstalled is
    a success, not an error)."""
    assert_label_is_new(label)
    if not is_darwin():
        logger.warning("launchd_installer: uninstall(%s) skipped - not macOS.", label)
        return False

    path = plist_path(label)
    try:
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True, timeout=10)
            path.unlink()
    except OSError as exc:
        logger.error("launchd_installer: uninstall(%s) failed: %s", label, exc)
        return False
    return True


def default_python_path(repo_root: Path) -> Path:
    """The ROOT `.venv/` (see README section 1 "Install on Mac") -
    matches the convention every one of the 7 EXISTING launchd jobs
    already documents (plain `src.*` module runs), distinct from
    `dashboard/backend/.venv/` which only the dashboard's own
    `mac_launcher/*.command` scripts use."""
    return repo_root / ".venv" / "bin" / "python"


def spec_by_name(name: str) -> JobSpec | None:
    return next((spec for spec in DEFAULT_JOB_SPECS if spec.name == name), None)


def main() -> int:
    """`python -m src.agents.launchd_installer <list|install|uninstall> [job_name]`
    - the primitive CLI `mac_launcher/Manage Agent Schedules.command`
    shells out to. This module itself asks no confirmation (the bash
    script's own "install this job? [y/N]" prompt IS the opt-in gate -
    see module docstring); `main()` just performs exactly the one
    action its caller already decided on."""
    import argparse

    from ..utils import load_config, load_env, resolve_path, setup_logging

    parser = argparse.ArgumentParser(description="Opt-in launchd schedule installer for Sprint 4/5's new agents.")
    parser.add_argument("action", choices=["list", "install", "uninstall"])
    parser.add_argument("job_name", nargs="?", help="One of: " + ", ".join(spec.name for spec in DEFAULT_JOB_SPECS))
    args = parser.parse_args()

    load_env()
    config = load_config(None)
    logger = setup_logging(config, log_filename="launchd_installer.log")
    repo_root = Path(__file__).resolve().parent.parent.parent

    if args.action == "list":
        for spec in DEFAULT_JOB_SPECS:
            installed = is_installed(spec.label, logger)
            state = "unknown (not macOS, or launchctl unavailable)" if installed is None else ("INSTALLED" if installed else "not installed")
            schedule = f"every {spec.interval_seconds}s" if spec.interval_seconds else f"daily at {spec.calendar_hour:02d}:{spec.calendar_minute:02d} UTC"
            print(f"{spec.name} ({spec.label}): {state} - {schedule} - {spec.description}")
        return 0

    if not args.job_name:
        print("job_name is required for install/uninstall.")
        return 1

    spec = spec_by_name(args.job_name)
    if spec is None:
        print(f"Unknown job_name {args.job_name!r}. Known: {', '.join(s.name for s in DEFAULT_JOB_SPECS)}")
        return 1

    if args.action == "install":
        python_path = default_python_path(repo_root)
        log_dir = resolve_path(config.get("data", {}).get("journal_dir", "data/journal")).parent / "reports"
        ok = install(spec, python_path, repo_root, log_dir, logger)
        print(f"Installed {spec.label}." if ok else f"FAILED to install {spec.label} - see launchd_installer.log.")
        return 0 if ok else 1

    ok = uninstall(spec.label, logger)
    print(f"Uninstalled {spec.label}." if ok else f"FAILED to uninstall {spec.label} - see launchd_installer.log.")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
