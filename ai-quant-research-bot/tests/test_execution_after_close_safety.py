"""Structural safety guardrail for `execution/after_close.py`'s
`--simulate`/`--dry-run` path (final production-safe daily scheduling
milestone): simulate mode must be provably incapable of importing
`src.main` (and therefore of ever reaching an LLM call, a Telegram send,
a broker order, or a price-data fetch) - not just by convention, by
source inspection, mirroring the existing pattern in
`test_intelligence_tradingagents_safety.py` and
`test_execution_run_health_safety.py`."""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
AFTER_CLOSE_SOURCE = (REPO_ROOT / "src/execution/after_close.py").read_text(encoding="utf-8")
README_TEXT = (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_documents_the_after_close_plist_without_runatload():
    """Requirement 5: loading/installing the after-close job must never
    itself trigger a live run. `RunAtLoad` fires once immediately on
    `launchctl load`/boot - the documented plist for
    `com.aiquantresearchbot.afterclose` must never include it (unlike
    the plain daily job's plist, which deliberately does, for a
    different reason - see the README section introducing this one)."""
    start = README_TEXT.index("com.aiquantresearchbot.afterclose.plist`:\n\n```xml")
    end = README_TEXT.index("```", start + len("com.aiquantresearchbot.afterclose.plist`:\n\n```xml"))
    plist_block = README_TEXT[start:end]
    assert "RunAtLoad" not in plist_block
    assert "StartInterval" in plist_block


def test_module_level_imports_never_include_main_or_broker_or_llm_code():
    """No import of src.main (or anything execution/LLM-facing) at
    MODULE level - every risky import must be deferred into a function
    body that only runs on the real (non-simulate) path."""
    tree = ast.parse(AFTER_CLOSE_SOURCE)
    module_level_imports = set()
    for node in tree.body:  # top-level only - NOT ast.walk, which would also catch deferred imports inside functions
        if isinstance(node, ast.Import):
            module_level_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level_imports.add(node.module)

    for forbidden in ("main", "tradingagents_adapter", "telegram_bot", "ibkr_client", "order_manager"):
        assert not any(forbidden in m for m in module_level_imports), f"{forbidden} imported at module level"


def test_the_simulate_code_path_never_calls_main_run():
    """`main()`'s `if args.simulate:` branch must return before ever
    reaching `_run_real()` (the only function that imports `src.main`)."""
    tree = ast.parse(AFTER_CLOSE_SOURCE)
    main_func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    simulate_if = next(
        n for n in ast.walk(main_func)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Attribute) and n.test.attr == "simulate"
    )
    calls_in_branch = [
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        for node in ast.walk(simulate_if)
        if isinstance(node, ast.Call)
    ]
    assert "_run_real" not in calls_in_branch


def test_run_real_is_the_only_function_that_imports_src_main():
    tree = ast.parse(AFTER_CLOSE_SOURCE)
    functions_importing_main = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom) and inner.level == 2 and inner.module is None:
                    if any(alias.name == "main" for alias in inner.names):
                        functions_importing_main.append(node.name)

    assert functions_importing_main == ["_run_real"]
