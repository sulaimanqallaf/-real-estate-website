"""GitHub Issue #1 follow-up requirement 8: "run a genuine end-to-end
LLM-backed research test when credentials are available."

This is the ONE test in the whole suite that actually calls a real,
billed LLM provider through the real upstream TradingAgents package -
every other test in this project either avoids the isolated environment
entirely or stubs it with a fixture script. It is SKIPPED (not faked,
not mocked into a false pass) whenever no LLM provider credential is
present in the environment, which is the case in this cloud sandbox as
of this writing - there is no ANTHROPIC_API_KEY/OPENAI_API_KEY here, and
there must never be one added just to make this test "pass" without a
human deciding to actually spend money. On a machine with a real key and
the isolated environment set up (`scripts/setup_tradingagents_env.sh`),
this test runs for real, kept as cheap as the framework allows (one
analyst, one debate round).
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import tradingagents_adapter as ta

logger = logging.getLogger("test")

_HAS_CREDENTIALS = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
_VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venvs" / "tradingagents" / "bin" / "python"


@pytest.mark.skipif(
    not _HAS_CREDENTIALS,
    reason="No ANTHROPIC_API_KEY/OPENAI_API_KEY in this environment - add one locally to actually run this test "
           "(GitHub Issue #1 requirement 8). Never add a key here just to turn this skip green.",
)
@pytest.mark.skipif(not _VENV_PYTHON.exists(), reason="Isolated TradingAgents environment not set up - run ./scripts/setup_tradingagents_env.sh first.")
def test_real_end_to_end_tradingagents_run_for_one_ticker(tmp_path):
    provider_overrides: dict = {"max_debate_rounds": 1, "max_risk_discuss_rounds": 1}
    if os.environ.get("ANTHROPIC_API_KEY"):
        provider_overrides.update({"llm_provider": "anthropic", "deep_think_llm": "claude-haiku-5-5", "quick_think_llm": "claude-haiku-5-5"})
    elif os.environ.get("OPENAI_API_KEY"):
        provider_overrides.update({"llm_provider": "openai"})

    config = {
        "data": {"journal_dir": str(tmp_path)},
        "intelligence": {
            "tradingagents": {
                "enabled": True,
                "selected_analysts": ["market"],  # the cheapest possible real run - one analyst, no debate rounds beyond the minimum
                "config_overrides": provider_overrides,
                "max_calls_per_day": 1,
                "max_daily_spend_usd": 2.00,
                "max_monthly_spend_usd": 2.00,
                "max_cost_per_call_usd": 2.00,
            }
        },
    }

    raw = ta.run_one("AMD", "2024-01-10", config, logger, portfolio_context={}, now=datetime.now(timezone.utc))
    assert raw is not None, "Real TradingAgents run failed - check test output / logs for the actual provider error."
    assert raw.get("final_rating") or raw.get("signal")

    assessment = ta.build_assessment("AMD", "2024-01-10", datetime.now(timezone.utc).isoformat(), raw)
    assert assessment.action in ("BUY", "SELL", "HOLD")
    # Real usage should have been tracked (even if a particular model has
    # no pricing entry, the token counts themselves must be real and non-empty).
    assert raw.get("token_usage")
