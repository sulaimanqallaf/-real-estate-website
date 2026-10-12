#!/bin/bash
# Sprint 5: AI QUANT LABS - REAL-WORLD ACTIVATION. Runs src.activation_suite -
# everything Run Real Validation.command already does (real yfinance
# strategy validation, Alpaca/IEX data check, IBKR Paper preflight), PLUS:
#   - a real smoke test of OpenAlex/arXiv/Semantic Scholar, with a real
#     sample paper recorded into permanent research memory if any
#     provider returns one
#   - a real SEC ticker->CIK lookup + a real FRED economic-data check
#   - one real Telegram test message, if TELEGRAM_BOT_TOKEN/CHAT_ID are
#     already configured
# Every result is a real PASS/FAIL/BLOCKED/SKIPPED, never a fabricated
# success - see the report this prints (and saves) for the evidence.
#
# SAFE BY CONSTRUCTION:
# - Never prompts for, displays, or logs a credential value. Every
#   credential (ALPACA_*/IBKR_*/SEC_IDENTITY/FRED_API_KEY/TELEGRAM_*)
#   comes ONLY from your own .env file - this script never asks you to
#   type or paste one anywhere.
# - Never places, cancels, or modifies a broker order.
# - Never touches autonomous_paper.* or execution.mode in config/settings.yaml.
# - Never starts, stops, or modifies the Mac launchd research scheduler.
# - The one real write in this whole check is a single sample paper row
#   in data/research/paper_memory.db (only if a real provider returned
#   one) - every other check is read-only, including the one real
#   Telegram message sent if you already have Telegram configured.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/dashboard/backend"

echo "=================================================================="
echo " AI Quant Research Bot - Real-World ACTIVATION Check (Sprint 5)"
echo "=================================================================="
echo
echo "This runs SIX checks against REAL systems (not synthetic data):"
echo "  1. Strategy validation on real yfinance market history"
echo "  2. Alpaca Basic/IEX data check (only if you've set credentials in .env)"
echo "  3. IBKR Paper connectivity preflight (only if TWS/Gateway is running)"
echo "  4. OpenAlex/arXiv/Semantic Scholar - real papers, recorded into"
echo "     permanent research memory if any provider returns one"
echo "  5. SEC ticker->CIK lookup + FRED economic data (only if SEC_IDENTITY/"
echo "     FRED_API_KEY are set in .env)"
echo "  6. One real Telegram test message (only if already configured)"
echo
echo "SECURITY: this script will NEVER ask you to type or paste a"
echo "credential. Every credential (if any) is read only from your own"
echo ".env file, exactly like every other command in this project -"
echo "never from this prompt, never echoed to this window."
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
echo "Running the activation suite now - this fetches real market data,"
echo "real academic papers, and real SEC/FRED data, and may take a few"
echo "minutes..."
echo

cd "$REPO_ROOT"
"$BACKEND_DIR/.venv/bin/python" -m src.activation_suite "${EXTRA_ARGS[@]}"
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
