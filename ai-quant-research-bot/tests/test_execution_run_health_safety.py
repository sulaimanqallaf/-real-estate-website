"""Structural safety guardrail for `execution/run_health.py` (Daily
Reliability & Safe Automation milestone): this module is read-only
monitoring/alerting only - it must never be able to place an order,
connect to a broker, or make an LLM/subprocess call, by construction -
not just by convention. Mirrors the existing pattern in
`test_intelligence_tradingagents_safety.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_HEALTH_SOURCE = (REPO_ROOT / "src/execution/run_health.py").read_text(encoding="utf-8")

_FORBIDDEN_TOKENS = [
    "ibkr_client",
    "order_manager",
    "execution_policy",
    "Broker(",
    "tradingagents_adapter",
]


@pytest.mark.parametrize("token", _FORBIDDEN_TOKENS)
def test_run_health_never_mentions_execution_or_llm_subprocess_code(token):
    assert token not in RUN_HEALTH_SOURCE


def test_run_health_s_only_subprocess_call_is_the_read_only_launchctl_status_check():
    """`run_health.py` is allowed exactly one `subprocess` use - the local,
    read-only `launchctl list <label>` status check in `check_launchd_
    status()`. This asserts that's still the ONLY subprocess invocation
    in the file (never a Python interpreter, never anything that could
    reach an LLM provider or a broker), rather than just trusting the
    docstring's claim."""
    import ast

    tree = ast.parse(RUN_HEALTH_SOURCE)
    run_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
            if node.args and isinstance(node.args[0], ast.List):
                elts = node.args[0].elts
                if elts and isinstance(elts[0], ast.Constant):
                    run_calls.append(elts[0].value)

    assert run_calls == ["launchctl"]


def test_run_health_never_imports_the_broker_module():
    import ast

    tree = ast.parse(RUN_HEALTH_SOURCE)
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any("broker" in m.lower() for m in imported_modules)
    assert not any("ibkr" in m.lower() for m in imported_modules)
