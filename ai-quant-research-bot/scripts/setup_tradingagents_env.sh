#!/usr/bin/env bash
# ONE command to set up the OPTIONAL upstream TradingAgents adapter
# (GitHub Issue #1) on your Mac:
#
#   ./scripts/setup_tradingagents_env.sh
#
# Safe to re-run any time (idempotent - reuses the existing venv if
# present) and again after any change to requirements-tradingagents.txt.
#
# This environment is NEVER used to run this project's own code or test
# suite - only src/intelligence/tradingagents_adapter.py ever shells out
# to it, via tools/tradingagents_runner.py, exactly to keep TradingAgents'
# much heavier dependency footprint (LangGraph/LangChain) from ever
# touching this project's own environment. See requirements-tradingagents.txt
# and src/intelligence/tradingagents_adapter.py's module docstrings.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venvs/tradingagents"
PYTHON_BIN="${TRADINGAGENTS_SETUP_PYTHON:-python3}"

if [ -d "${VENV_DIR}" ]; then
  echo "Reusing existing environment at ${VENV_DIR} (re-running pip install to pick up any changes) ..."
else
  echo "Creating isolated TradingAgents environment at ${VENV_DIR} ..."
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${REPO_ROOT}/requirements-tradingagents.txt"

echo "Verifying the install ..."
"${VENV_DIR}/bin/python" -c "import tradingagents; print('tradingagents', tradingagents.__name__, 'imported OK')"

echo
echo "================================================================"
echo "Setup complete. Remaining steps (not done by this script):"
echo
echo "1. Add an LLM provider API key to your .env file, e.g. one of:"
echo "     ANTHROPIC_API_KEY=sk-ant-..."
echo "     OPENAI_API_KEY=sk-..."
echo "   (never commit .env - it is already gitignored)"
echo
echo "2. In config/settings.yaml, set:"
echo "     intelligence.tradingagents.enabled: true"
echo
echo "3. (Optional) choose which provider/model via"
echo "     intelligence.tradingagents.config_overrides, e.g.:"
echo '     {"llm_provider": "anthropic", "deep_think_llm": "claude-sonnet-5-5", "quick_think_llm": "claude-haiku-5-5"}'
echo
echo "4. Run python -m src.main as usual - the next report will include"
echo "   an 'Upstream TradingAgents (SHADOW, not executed)' line for the"
echo "   top-ranked worthwhile candidate(s)."
echo "================================================================"
