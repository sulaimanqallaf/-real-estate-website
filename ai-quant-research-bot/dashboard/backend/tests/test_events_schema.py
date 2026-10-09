import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from pydantic import ValidationError

from app.events import ALL_AGENT_IDS, AgentEvent, make_event


def test_all_agent_ids_is_exactly_the_eight_phase_1_agents():
    assert ALL_AGENT_IDS == (
        "market_scout", "bull_analyst", "bear_analyst", "chief_manager",
        "risk_officer", "strategy_scientist", "execution_agent", "learning_agent",
    )


def test_make_event_assigns_an_id_and_timestamp_automatically():
    event = make_event("live", "scan", "test")
    assert event.id
    assert event.ts
    assert event.agent is None
    assert event.zone is None


def test_event_requires_a_valid_mode():
    with pytest.raises(ValidationError):
        AgentEvent(mode="production", event_type="scan", summary="x")  # type: ignore[arg-type]


def test_event_requires_a_valid_event_type():
    with pytest.raises(ValidationError):
        AgentEvent(mode="live", event_type="not_a_real_type", summary="x")  # type: ignore[arg-type]


def test_event_rejects_an_unknown_agent_id():
    with pytest.raises(ValidationError):
        AgentEvent(mode="live", event_type="scan", summary="x", agent="ceo")  # type: ignore[arg-type]


def test_event_serializes_to_json_with_all_fields():
    event = make_event("demo", "debate", "Bull case", agent="bull_analyst", zone="debate_room", ticker="AMD")
    payload = event.model_dump_json()
    assert '"mode":"demo"' in payload
    assert '"ticker":"AMD"' in payload


def test_event_is_frozen_immutable():
    event = make_event("live", "scan", "x")
    with pytest.raises(ValidationError):
        event.summary = "changed"  # type: ignore[misc]
