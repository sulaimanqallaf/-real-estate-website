"""src/intelligence/pipeline.py - the shadow-mode orchestrator, and its
wiring into main.py. The critical property under test throughout this
file: the research layer can observe but never influence execution -
every test here either proves it attaches a read-only field, or proves
it runs too late to matter."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.intelligence import memory, pipeline
from src.intelligence.schemas import ACTION_HOLD

logger = logging.getLogger("test")


@pytest.fixture
def config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}, "intelligence": {"enabled": True}}


def make_entry(symbol="AMD", **overrides):
    entry = {
        "symbol": symbol, "score": 90,
        "best_risk_result": {"tradeable": True, "strategy": "Trend Following"},
        "regime_evaluation": {"blocked": False, "regime": "TRENDING_UP"},
        "portfolio_evaluation": {"decision": "ACCEPT"},
    }
    entry.update(overrides)
    return entry


def test_run_shadow_research_attaches_agent_assessment_to_every_entry(config):
    entries = [make_entry("AMD"), make_entry("NVDA")]
    pipeline.run_shadow_research(entries, "2026-09-09", config, logger)
    assert all(e.get("agent_assessment") is not None for e in entries)


def test_run_shadow_research_does_not_touch_execution_affecting_fields(config):
    entry = make_entry("AMD")
    before = {
        "best_risk_result": dict(entry["best_risk_result"]),
        "regime_evaluation": dict(entry["regime_evaluation"]),
        "portfolio_evaluation": dict(entry["portfolio_evaluation"]),
    }
    pipeline.run_shadow_research([entry], "2026-09-09", config, logger)
    assert entry["best_risk_result"] == before["best_risk_result"]
    assert entry["regime_evaluation"] == before["regime_evaluation"]
    assert entry["portfolio_evaluation"] == before["portfolio_evaluation"]
    assert "execution_decision" not in entry  # never fabricated by this layer


def test_run_shadow_research_is_disabled_by_config(config):
    config["intelligence"]["enabled"] = False
    entry = make_entry("AMD")
    pipeline.run_shadow_research([entry], "2026-09-09", config, logger)
    assert "agent_assessment" not in entry


def test_run_shadow_research_records_to_memory(config):
    entry = make_entry("AMD")
    pipeline.run_shadow_research([entry], "2026-09-09", config, logger)
    rows = memory.query_assessments(memory.resolve_db_path(config), ticker="AMD")
    assert len(rows) == 1
    assert rows[0]["report_date"] == "2026-09-09"


def test_run_shadow_research_never_raises_when_one_entry_is_malformed(config):
    good = make_entry("AMD")
    malformed = {"symbol": "BAD"}  # missing every expected key
    pipeline.run_shadow_research([good, malformed], "2026-09-09", config, logger)
    assert good.get("agent_assessment") is not None
    # malformed entry degrades (analysts return Data-Unavailable opinions from missing keys) rather than crashing the run
    assert "agent_assessment" in malformed


def test_run_shadow_research_skips_entries_with_no_symbol(config):
    entry = {"score": 50}
    pipeline.run_shadow_research([entry], "2026-09-09", config, logger)
    assert "agent_assessment" not in entry


def test_run_shadow_research_surfaces_past_reflections_in_the_thesis(config, monkeypatch):
    db_path = memory.resolve_db_path(config)
    from src.intelligence.schemas import AgentOpinion, AgentResearchAssessment

    old = AgentResearchAssessment(
        ticker="AMD", report_date="2026-09-01", as_of="2026-09-01T00:00:00+00:00", action="BUY", confidence=0.5,
        thesis="old thesis", bull_points=[], bear_points=[], risk_notes=[],
        analyst_opinions={"technical": AgentOpinion(analyst="technical", action="BUY", confidence=0.5, thesis="t")},
        data_provenance=[],
    )
    aid = memory.record_assessment(db_path, old)
    memory.record_reflection(db_path, aid, "Missed: predicted BUY, trade lost.")

    entry = make_entry("AMD")
    pipeline.run_shadow_research([entry], "2026-09-09", config, logger)
    assert "Missed" in entry["agent_assessment"].thesis
