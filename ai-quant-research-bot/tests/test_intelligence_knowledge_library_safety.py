"""Structural safety guardrail for intelligence/knowledge_library.py
(AI Quant Trading Platform sprint, deliverable E): the RAG engine must
never be able to execute retrieved content, make a network/LLM call,
or touch execution/broker code - by source inspection, mirroring the
existing pattern in test_intelligence_tradingagents_safety.py."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = (REPO_ROOT / "src/intelligence/knowledge_library.py").read_text(encoding="utf-8")

_FORBIDDEN_TOKENS = [
    "eval(", "exec(", "subprocess", "requests.", "urllib", "socket.",
    "os.system", "order_manager", "execution_policy", "ibkr_client", "broker.",
    "anthropic", "openai",
]


def _code_only() -> str:
    tree = ast.parse(SOURCE)
    docstring = ast.get_docstring(tree)
    text = SOURCE
    if docstring:
        text = text.replace(docstring, "", 1)
    return text


def test_knowledge_library_never_mentions_forbidden_execution_or_network_code():
    code = _code_only()
    for forbidden in _FORBIDDEN_TOKENS:
        assert forbidden not in code, f"{forbidden!r} found in knowledge_library.py"


def test_knowledge_library_has_no_network_or_execution_imports():
    tree = ast.parse(SOURCE)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_modules = {"subprocess", "socket", "requests", "urllib", "http"}
    assert not (imported & forbidden_modules), f"forbidden imports: {imported & forbidden_modules}"


def test_knowledge_library_only_reads_files_never_writes_outside_its_own_scope():
    """Every file-opening call must be a read (`read_text`/`open(...,
    "r"...)`), never a write - this module's whole job is retrieval,
    not persistence."""
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            # Path.open(...) - check for a write mode literal
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "w" in arg.value:
                    raise AssertionError("found a write-mode file open in knowledge_library.py")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "w" in arg.value:
                    raise AssertionError("found a write-mode open() in knowledge_library.py")
