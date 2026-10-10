#!/bin/bash
# Read-only status check: dashboard process state, backend API reachability,
# whether the Mac launchd research scheduler is running (via `launchctl
# list`), AND - Sprint 3 Task A1 - a reliability/freshness summary
# (position_monitor heartbeat, circuit breaker, cached-data staleness for
# both the yfinance and Alpaca/IEX caches) via `python -m src.execution.
# run_health`, so you never have to open Terminal yourself to check this.
# Never started/stopped/modified from here - read-only, same as
# run_health.py's own docstring promises ("no LLM call, no broker call, no
# order").

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/dashboard/backend"
RUN_DIR="$SCRIPT_DIR/.run"

echo "=============================================="
echo " AI Quant Research Bot - Dashboard Status"
echo "=============================================="
echo

check_service() {
  local name="$1" pidfile="$RUN_DIR/$2.pid" url="$3"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  $name: RUNNING (PID $(cat "$pidfile"))"
  else
    echo "  $name: not running"
    return
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl -s -o /dev/null -w "" --max-time 2 "$url"; then
      echo "    -> reachable at $url"
    else
      echo "    -> process is running but $url is not responding yet"
    fi
  fi
}

echo "Dashboard services:"
check_service "Backend " backend "http://localhost:8800/api/modes"
check_service "Frontend" frontend "http://localhost:5173/"
echo

echo "Research scheduler (read-only check - this launcher never starts,"
echo "stops, or modifies it):"
if command -v launchctl >/dev/null 2>&1; then
  if launchctl list 2>/dev/null | grep -q aiquantresearchbot; then
    echo "  launchd jobs found:"
    launchctl list 2>/dev/null | grep aiquantresearchbot | sed 's/^/    /'
  else
    echo "  No com.aiquantresearchbot.* launchd jobs are currently loaded."
  fi
else
  echo "  launchctl not available on this system (not macOS?)."
fi
echo

echo "Reliability status (Sprint 3 - watchdog, circuit breaker, data freshness):"
if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  (cd "$REPO_ROOT" && "$BACKEND_DIR/.venv/bin/python" -m src.execution.run_health) | sed 's/^/  /' \
    || echo "  Could not run the reliability check (see any error above)."
else
  echo "  Unavailable - run 'Start Dashboard.command' at least once first to set up the Python environment this needs."
fi

echo
echo "Press Enter to close this window."
read -r _
