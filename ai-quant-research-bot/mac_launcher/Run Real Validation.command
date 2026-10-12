#!/bin/bash
# Sprint 4, Priority 1: a guided, one-click run of src.validation_suite -
# real historical-data strategy validation, an Alpaca Basic/IEX data
# check, and a read-only IBKR Paper connectivity preflight, with a
# single PASS/FAIL/SKIPPED/BLOCKED report backed by real evidence.
#
# SAFE BY CONSTRUCTION:
# - Never prompts for, displays, or logs a credential value. Credentials
#   (ALPACA_API_KEY_ID/SECRET, IBKR_*) come ONLY from your own .env file -
#   this script never asks you to type or paste one anywhere.
# - Never places, cancels, or modifies a broker order.
# - Never touches autonomous_paper.* or execution.mode in config/settings.yaml.
# - Never starts, stops, or modifies the Mac launchd research scheduler.
# - The optional full reconnect check (off by default) is the one
#   genuinely interactive step - it asks YOU to manually stop/restart
#   your own TWS/Gateway, never asks for a credential.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/dashboard/backend"

echo "=================================================================="
echo " AI Quant Research Bot - One-Click Real-World Validation"
echo "=================================================================="
echo
echo "This runs three checks against REAL systems (not synthetic data):"
echo "  1. Strategy validation on real yfinance market history"
echo "  2. Alpaca Basic/IEX data check (only if you've set credentials in .env)"
echo "  3. IBKR Paper connectivity preflight (only if TWS/Gateway is running)"
echo
echo "SECURITY: this script will NEVER ask you to type or paste a"
echo "credential. Your ALPACA_*/IBKR_* values (if any) are read only"
echo "from your own .env file, exactly like every other command in this"
echo "project - never from this prompt, never echoed to this window."
echo
echo "This will NOT place any order, will NOT touch autonomous trading"
echo "settings, and will NOT affect your existing Mac research scheduler."
echo

if [ ! -x "$BACKEND_DIR/.venv/bin/python" ]; then
  echo "ERROR: Python environment not found."
  echo "Run 'Start Dashboard.command' once first to set it up, then re-run this."
  echo
  echo "Press Enter to close this window."
  read -r _
  exit 1
fi

read -r -p "Also run the INTERACTIVE IBKR reconnect check (you'll be asked to manually stop/restart TWS)? [y/N] " RECONNECT_ANSWER
EXTRA_ARGS=()
if [ "${RECONNECT_ANSWER:-}" = "y" ] || [ "${RECONNECT_ANSWER:-}" = "Y" ]; then
  EXTRA_ARGS+=("--full-reconnect-check")
  echo "Will run the full interactive reconnect check (up to 2 minutes)."
else
  echo "Skipping the interactive reconnect check (default) - run with it later if you want that step exercised."
fi

echo
echo "Running the validation suite now - this fetches real market data"
echo "and may take a minute or two..."
echo

cd "$REPO_ROOT"
"$BACKEND_DIR/.venv/bin/python" -m src.validation_suite "${EXTRA_ARGS[@]}"
EXIT_CODE=$?

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "Done - no FAILs. See the report above (and the saved file it names) for the full evidence."
else
  echo "Done - one or more checks FAILED. See the report above (and the saved file it names) for details."
fi

echo
echo "Press Enter to close this window."
read -r _
