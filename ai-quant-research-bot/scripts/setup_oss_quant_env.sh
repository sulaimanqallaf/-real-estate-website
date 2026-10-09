#!/usr/bin/env bash
# ONE command to set up the OPTIONAL open-source quant tooling
# (QuantStats, VectorBT, CCXT - see docs/platform/OSS_INTEGRATION_AUDIT.md)
# on your Mac:
#
#   ./scripts/setup_oss_quant_env.sh
#
# Safe to re-run any time (idempotent - reuses the existing venv if
# present) and again after any change to requirements-oss-quant.txt.
#
# This environment is NEVER used to run this project's own code or test
# suite - only tools/oss_quant_runner.py ever runs inside it, via
# src/analytics/oss_quant_adapter.py's subprocess boundary, exactly to
# keep vectorbt's much newer pandas/numpy requirement from ever touching
# this project's own environment. See requirements-oss-quant.txt and
# src/analytics/oss_quant_adapter.py's module docstring.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venvs/oss_quant"
PYTHON_BIN="${OSS_QUANT_SETUP_PYTHON:-python3}"

if [ -d "${VENV_DIR}" ]; then
  echo "Reusing existing environment at ${VENV_DIR} (re-running pip install to pick up any changes) ..."
else
  echo "Creating isolated OSS quant tooling environment at ${VENV_DIR} ..."
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r "${REPO_ROOT}/requirements-oss-quant.txt"

echo "Verifying the install ..."
"${VENV_DIR}/bin/python" -c "
import quantstats, vectorbt, ccxt
print('quantstats', quantstats.__version__ if hasattr(quantstats, '__version__') else 'ok')
print('vectorbt', vectorbt.__version__)
print('ccxt', ccxt.__version__)
"

echo
echo "================================================================"
echo "Setup complete. QuantStats/VectorBT/CCXT are now usable via"
echo "src/analytics/oss_quant_adapter.py - nothing further needed."
echo
echo "Note: CCXT needs real network access to a crypto exchange's public"
echo "API to fetch real data - this was NOT reachable from the sandbox"
echo "that built this integration (see docs/platform/BLOCKERS.md). Run"
echo "'python -m src.data_providers.crypto_provider' after this setup to"
echo "verify it on your own machine."
echo "================================================================"
