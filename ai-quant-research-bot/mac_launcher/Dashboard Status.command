#!/bin/bash
# Read-only status check: dashboard process state, backend API reachability,
# AND whether the Mac launchd research scheduler is running - checked via
# `launchctl list`, exactly like `python -m src.execution.run_health` does,
# never started/stopped/modified from here.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
echo "Press Enter to close this window."
read -r _
