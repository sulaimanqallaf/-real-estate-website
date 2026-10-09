#!/bin/bash
# AI Quant Trading Platform sprint, deliverable I: double-click launcher
# for the dashboard's OWN two services (backend + frontend) - nothing
# else. See README.md in this directory for exactly what this does and
# does not do, and its honest limitations (no code signing, no native
# GUI - this is a Terminal window, not an app window).
#
# SAFE BY CONSTRUCTION:
# - Never starts/stops/modifies the Mac launchd research scheduler.
# - Never sets DASHBOARD_ALLOW_DEMO=1 itself (DEMO mode stays opt-in).
# - Never touches .env, config/settings.yaml, or anything under data/.
# - Never runs src.main or anything that could place a trade or incur
#   an LLM API cost - it only ever starts the read-only dashboard.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/dashboard/backend"
FRONTEND_DIR="$REPO_ROOT/dashboard/frontend"
RUN_DIR="$SCRIPT_DIR/.run"
mkdir -p "$RUN_DIR"

echo "=============================================="
echo " AI Quant Research Bot - Dashboard Launcher"
echo "=============================================="
echo

fail() { echo "ERROR: $1"; echo; echo "Press Enter to close this window."; read -r _; exit 1; }

is_running() {
  local pidfile="$1"
  [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

# --- 1. Environment / dependency check (never installs anything paid) ------

echo "Checking dependencies..."
command -v python3 >/dev/null 2>&1 || fail "python3 not found. Install Python 3.11+ from https://python.org, then re-run this launcher."
command -v node    >/dev/null 2>&1 || fail "node not found. Install Node.js 20+ from https://nodejs.org, then re-run this launcher."
command -v npm     >/dev/null 2>&1 || fail "npm not found (it normally ships with Node.js)."

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "  python3: $PYTHON_VERSION"
echo "  node:    $(node --version)"
echo

# --- 2. Backend: venv + deps (local to dashboard/backend, never touches
#        your system Python or any other project) --------------------------

if is_running "$RUN_DIR/backend.pid"; then
  echo "Backend already running (PID $(cat "$RUN_DIR/backend.pid")) - skipping."
else
  if [ ! -d "$BACKEND_DIR/.venv" ]; then
    echo "First run: creating a local Python environment for the dashboard backend..."
    python3 -m venv "$BACKEND_DIR/.venv" || fail "Could not create a Python virtual environment."
    "$BACKEND_DIR/.venv/bin/pip" install --quiet --upgrade pip
  fi
  # Re-run on EVERY start, not just the first - a no-op if nothing
  # changed, but this is what makes "Check for Updates.command" pulling
  # a new requirements.txt actually take effect without extra steps.
  echo "Checking backend dependencies are up to date (reads pypi.org, no account/payment needed)..."
  "$BACKEND_DIR/.venv/bin/pip" install --quiet -r "$REPO_ROOT/requirements.txt" || fail "Failed to install the bot's own Python dependencies."
  "$BACKEND_DIR/.venv/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt" || fail "Failed to install the dashboard backend's Python dependencies."

  echo "Starting dashboard backend on http://localhost:8800 ..."
  (
    cd "$BACKEND_DIR"
    export DASHBOARD_ALLOW_DEMO="${DASHBOARD_ALLOW_DEMO:-0}"
    exec "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8800
  ) > "$RUN_DIR/backend.log" 2>&1 &
  echo $! > "$RUN_DIR/backend.pid"
  sleep 1
  if ! kill -0 "$(cat "$RUN_DIR/backend.pid")" 2>/dev/null; then
    echo "Backend failed to start. Last log lines:"
    tail -n 20 "$RUN_DIR/backend.log" || true
    fail "Backend did not start - see the log above."
  fi
fi

# --- 3. Frontend: npm install + dev server ----------------------------------

if is_running "$RUN_DIR/frontend.pid"; then
  echo "Frontend already running (PID $(cat "$RUN_DIR/frontend.pid")) - skipping."
else
  echo "Checking frontend dependencies are up to date (npm install, no account/payment needed)..."
  (cd "$FRONTEND_DIR" && npm install --silent) || fail "npm install failed - see the output above."

  echo "Starting dashboard frontend on http://localhost:5173 ..."
  (
    cd "$FRONTEND_DIR"
    exec npm run dev -- --port 5173 --host 127.0.0.1
  ) > "$RUN_DIR/frontend.log" 2>&1 &
  echo $! > "$RUN_DIR/frontend.pid"
  sleep 2
fi

# --- 4. Open the browser ------------------------------------------------------

echo
echo "Dashboard starting up - waiting a moment before opening your browser..."
sleep 2
if command -v open >/dev/null 2>&1; then
  open "http://localhost:5173" || true
else
  echo "(Could not auto-open a browser on this system - open http://localhost:5173 manually.)"
fi

echo
echo "=============================================="
echo " Dashboard is running."
echo "   Frontend: http://localhost:5173"
echo "   Backend:  http://localhost:8800"
echo
echo " To stop it, double-click 'Stop Dashboard.command' in this folder."
echo " To check status, double-click 'Dashboard Status.command'."
echo "=============================================="
echo
echo "This window can stay open or be closed - the dashboard keeps running"
echo "in the background either way. Press Enter to close this window."
read -r _
