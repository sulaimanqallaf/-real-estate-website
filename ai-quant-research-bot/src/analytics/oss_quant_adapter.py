"""Subprocess boundary into the isolated `.venvs/oss_quant/` environment
(QuantStats, VectorBT, CCXT) - see
`docs/platform/OSS_INTEGRATION_AUDIT.md` for why these three are
isolated rather than added to this project's own `requirements.txt`
(in short: VectorBT requires `pandas>=3.0.3`/`numpy>=2.4.6`, both major-
version bumps past what the rest of this codebase is written and
tested against).

This module is the ONLY thing in the main project environment that
knows `tools/oss_quant_runner.py` exists - everything else
(`performance_report.py`, the dashboard, tests) calls through here, the
same two-sided isolation shape `intelligence/tradingagents_adapter.py`
already uses for the real upstream TradingAgents package.

Never fabricates a result: a missing venv, a timeout, or an
unparseable response all come back as `None` (never a guessed number)
with the reason logged.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 120


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_python_executable(config: dict[str, Any]) -> Path:
    configured = config.get("analytics", {}).get("oss_quant", {}).get("python_executable")
    if configured:
        return Path(configured)
    return _repo_root() / ".venvs" / "oss_quant" / "bin" / "python"


def resolve_runner_script() -> Path:
    return _repo_root() / "tools" / "oss_quant_runner.py"


def is_available(config: dict[str, Any]) -> bool:
    """Read-only check (no subprocess call) - whether the isolated venv
    has actually been set up (via `scripts/setup_oss_quant_env.sh`).
    Every caller of `run_task()` should check this first so "not set up
    yet" reads as a clear, honest state rather than a generic timeout/
    error."""
    return resolve_python_executable(config).exists()


def run_task(
    task: str, payload: dict[str, Any], config: dict[str, Any], logger: logging.Logger,
    timeout_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Runs one task inside the isolated venv and returns its parsed
    JSON response, or `None` (logged, never raised) if the venv isn't
    set up, the subprocess fails to start, times out, or returns
    unparseable output. The caller is responsible for checking
    `result.get("ok")` - a response CAN come back with `"ok": false`
    (a real, reported failure inside the task, e.g. CCXT's network call
    failing) without this function itself returning `None`."""
    python_executable = resolve_python_executable(config)
    if not python_executable.exists():
        logger.warning(
            "OSS quant tooling not set up (expected %s) - run scripts/setup_oss_quant_env.sh first.",
            python_executable,
        )
        return None

    request = {"task": task, **payload}
    with tempfile.TemporaryDirectory() as tmpdir:
        request_path = Path(tmpdir) / "request.json"
        with open(request_path, "w", encoding="utf-8") as f:
            json.dump(request, f, default=str)

        try:
            proc = subprocess.run(
                [str(python_executable), str(resolve_runner_script()), str(request_path)],
                capture_output=True, text=True, timeout=timeout_seconds or DEFAULT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.warning("OSS quant task %r timed out after %ss.", task, timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
            return None
        except Exception as exc:  # noqa: BLE001 - e.g. a misconfigured python_executable
            logger.warning("OSS quant task %r could not start: %s", task, exc)
            return None

        try:
            return json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else None
        except (ValueError, IndexError):
            logger.warning("OSS quant task %r returned unparseable output (exit %d): %s", task, proc.returncode, proc.stderr[-500:])
            return None
