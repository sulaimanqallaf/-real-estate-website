"""Tests for src/data_providers/options_flow_provider.py: the mock/no-data
provider returns a clean unavailable status, and the normalized event schema
works for a hand-built raw payload (standing in for whatever a future real
vendor would return)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import base, options_flow_provider as ofp


def test_mock_provider_is_never_configured():
    provider = ofp.MockOptionsFlowProvider()
    assert provider.is_configured() is False


def test_mock_provider_returns_clean_unavailable_status():
    provider = ofp.MockOptionsFlowProvider()
    result = provider.fetch_events("NVDA", config={})
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.data is None
    assert result.error


def test_get_default_provider_is_mock_even_if_enabled_in_config():
    """No real vendor is wired up yet - enabling providers.options_flow in
    config must not silently pretend one exists."""
    provider = ofp.get_default_provider({"providers": {"options_flow": {"enabled": True}}})
    assert isinstance(provider, ofp.MockOptionsFlowProvider)


def test_get_default_provider_is_mock_when_disabled():
    provider = ofp.get_default_provider({"providers": {"options_flow": {"enabled": False}}})
    assert isinstance(provider, ofp.MockOptionsFlowProvider)


def test_normalize_raw_event_builds_expected_schema():
    raw = {
        "timestamp": "2026-08-01T14:30:00",
        "ticker": "NVDA",
        "event_type": ofp.EVENT_CALL_SWEEP,
        "side": ofp.SIDE_ASK,
        "premium": 250_000.0,
        "strike": 130.0,
        "expiry": "2026-09-19",
        "call_put": "call",
        "price": 5.25,
        "size": 500,
    }
    event = ofp.normalize_raw_event(raw, source="mock_vendor")
    assert event.ticker == "NVDA"
    assert event.event_type == ofp.EVENT_CALL_SWEEP
    assert event.side == ofp.SIDE_ASK
    assert event.premium == 250_000.0
    assert event.source == "mock_vendor"
    assert event.available_at == event.timestamp


def test_normalize_raw_event_defaults_side_to_unknown_when_absent():
    raw = {"timestamp": datetime(2026, 8, 1), "ticker": "NVDA", "event_type": ofp.EVENT_BLOCK_TRADE}
    event = ofp.normalize_raw_event(raw, source="mock_vendor")
    assert event.side == ofp.SIDE_UNKNOWN
    assert event.premium is None
