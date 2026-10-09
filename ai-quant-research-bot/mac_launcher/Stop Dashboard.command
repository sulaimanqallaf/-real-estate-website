#!/bin/bash
# Stops ONLY the two processes "Start Dashboard.command" started (tracked
# by PID file, in this directory's .run/ folder) - never touches the Mac
# launchd research scheduler or anything else running on your machine.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$SCRIPT_DIR/.run"

echo "=============================================="
echo " AI Quant Research Bot - Stopping Dashboard"
echo "=============================================="
echo

# `npm run dev` (the frontend) spawns a multi-level child tree (npm -> `sh
# -c vite` -> the actual node vite process) that does NOT reliably die just
# because the top PID does - kill the whole subtree, recursively, so
# nothing is ever left running after this script exits.
kill_tree() {
  local pid="$1" sig="$2"
  local child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child" "$sig"
  done
  kill "-$sig" "$pid" 2>/dev/null || true
}

stopped_any=0
for name in backend frontend; do
  pidfile="$RUN_DIR/$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill_tree "$pid" TERM
      sleep 1
      kill_tree "$pid" KILL
      echo "Stopped $name (PID $pid)."
      stopped_any=1
    else
      echo "$name was not running (stale PID file removed)."
    fi
    rm -f "$pidfile"
  else
    echo "$name was not running."
  fi
done

echo
if [ "$stopped_any" = "1" ]; then
  echo "Dashboard stopped."
else
  echo "Nothing was running."
fi
echo
echo "Press Enter to close this window."
read -r _
