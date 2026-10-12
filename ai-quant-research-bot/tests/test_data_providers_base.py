"""src/data_providers/base.py - the shared ProviderResult contract,
including Sprint 5's new STATUS_BLOCKED/provider_blocked() (distinct
from STATUS_ERROR/provider_error() - see base.py's own docstring)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import base


def test_ok_result_is_ok():
    result = base.ok("src", [1, 2, 3])
    assert result.status == base.STATUS_OK
    assert result.ok is True
    assert result.data == [1, 2, 3]


def test_unavailable_result_is_not_ok_and_carries_no_data():
    result = base.unavailable("src", "not configured")
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.ok is False
    assert result.data is None
    assert result.error == "not configured"


def test_provider_error_result_is_not_ok():
    result = base.provider_error("src", "malformed response")
    assert result.status == base.STATUS_ERROR
    assert result.ok is False
    assert result.data is None
    assert result.error == "malformed response"


def test_provider_blocked_result_is_not_ok_and_distinct_from_error():
    result = base.provider_blocked("src", "connection refused")
    assert result.status == base.STATUS_BLOCKED
    assert result.ok is False
    assert result.data is None
    assert result.error == "connection refused"
    assert result.status != base.STATUS_ERROR


def test_ok_with_empty_data_is_not_truthy_via_the_ok_property():
    """`ProviderResult.ok` requires BOTH status==STATUS_OK and
    data is not None - an edge case worth pinning explicitly."""
    result = base.ProviderResult(source="src", status=base.STATUS_OK, data=None)
    assert result.ok is False
