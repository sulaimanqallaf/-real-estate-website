"""Structural safety guardrail for the whole dashboard backend
(Phase 1 MVP, "completely read-only" requirement): no file anywhere
under `dashboard/backend/app/` may import anything that could place an
order, change a risk/execution setting, or trigger a real bot run - by
source inspection, not just convention, mirroring the main project's own
`test_intelligence_tradingagents_safety.py` pattern."""

import ast
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

_FORBIDDEN_SUBSTRINGS = [
    "order_manager",
    "execution_policy",
    "ibkr_client",
    "approval_bridge",
    "Broker(",
    "src.main",  # the dashboard must never be able to trigger a real scheduled run
    "src import main",
]

_APP_PY_FILES = sorted((BACKEND_ROOT / "app").rglob("*.py"))


def _code_only(path: Path) -> str:
    """Source with the module docstring stripped - `readonly.py`'s own
    docstring explains the invariant below BY NAMING the forbidden
    modules it must never import, which would otherwise trip this same
    substring check on prose, not code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree)
    text = path.read_text(encoding="utf-8")
    if docstring:
        text = text.replace(docstring, "", 1)
    return text


def test_no_backend_file_mentions_forbidden_execution_code():
    assert _APP_PY_FILES, "expected to find backend source files"
    for path in _APP_PY_FILES:
        text = _code_only(path)
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in text, f"{forbidden!r} found in {path}"


def test_readonly_module_only_imports_the_allow_listed_src_modules():
    """`app/readonly.py` is the single integration seam into the main
    bot's code - this pins its `from src...` imports to an explicit
    allow-list so a future edit can't quietly widen it."""
    allowed = {
        "src.execution.circuit_breaker", "src.execution.run_health",
        "src.ml.decision_ledger", "src.utils.load_config", "src.utils.redact_secrets",
        "src.intelligence.tradingagents_adapter", "src.paper_trades",
    }
    tree = ast.parse((BACKEND_ROOT / "app" / "readonly.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}")

    assert found, "expected at least one src.* import in readonly.py"
    assert found <= allowed, f"readonly.py imports outside the allow-list: {found - allowed}"


def test_no_endpoint_handler_accepts_a_write_style_http_method():
    """Every `@app.<method>` decorator in main.py must be a `get` or a
    `websocket` (read streams only) - no `post`/`put`/`delete`/`patch`
    anywhere, so there is no HTTP route that could ever be a write
    path."""
    text = (BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    methods_used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "app":
            methods_used.add(node.func.attr)

    assert methods_used, "expected at least one @app.<method> decorator"
    assert methods_used <= {"get", "websocket", "add_middleware"}, f"unexpected HTTP methods: {methods_used}"
