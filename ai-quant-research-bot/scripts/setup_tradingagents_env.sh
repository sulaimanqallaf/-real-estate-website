#!/usr/bin/env bash
# Creates the isolated Python environment for the OPTIONAL upstream
# TradingAgents adapter (GitHub Issue #1). Run once (and again after any
# change to requirements-tradingagents.txt):
#
#   ./scripts/setup_tradingagents_env.sh
#
# This environment is NEVER used to run this project's own code or test
# suite - only src/intelligence/tradingagents_adapter.py ever shells out to
# it, via tools/tradingagents_runner.py, exactly to keep TradingAgents'
# much heavier dependency footprint (LangGraph/LangChain) from ever
# touching this project's own environment. See requirements-tradingagents.txt
# and src/intelligence/tradingagents_adapter.py's module docstrings.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venvs/tradingagents"
PYTHON_BIN="${TRADINGAGENTS_SETUP_PYTHON:-python3}"

echo "Creating isolated TradingAgents environment at ${VENV_DIR} ..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${REPO_ROOT}/requirements-tradingagents.txt"

echo
echo "Done. Set intelligence.tradingagents.python_executable in config/settings.yaml to:"
echo "  ${VENV_DIR}/bin/python"
echo
echo "You still need an LLM provider API key (e.g. ANTHROPIC_API_KEY or OPENAI_API_KEY)"
echo "in your .env for TradingAgents to actually run - this script only installs the package."
