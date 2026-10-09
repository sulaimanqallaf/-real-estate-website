"""src/reliability.py - the shared Tenacity retry/backoff policy
(Sprint 3, Reliability milestone). Uses tiny wait bounds throughout so
these tests run in well under a second, never the real multi-second
backoff a live deployment would use."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

from src import reliability

LOGGER = logging.getLogger("test")

_FAST = dict(max_attempts=3, min_wait_seconds=0.001, max_wait_seconds=0.01)


# --- is_retryable_network_error: the classification boundary ----------------------------


def test_connection_error_is_retryable():
    assert reliability.is_retryable_network_error(ConnectionError("boom")) is True


def test_timeout_error_is_retryable():
    assert reliability.is_retryable_network_error(TimeoutError("boom")) is True


def test_requests_connection_error_is_retryable():
    assert reliability.is_retryable_network_error(requests.exceptions.ConnectionError("boom")) is True


def test_requests_timeout_is_retryable():
    assert reliability.is_retryable_network_error(requests.exceptions.Timeout("boom")) is True


def test_http_500_is_retryable():
    response = requests.Response()
    response.status_code = 503
    exc = requests.exceptions.HTTPError(response=response)
    assert reliability.is_retryable_network_error(exc) is True


def test_http_404_is_not_retryable():
    response = requests.Response()
    response.status_code = 404
    exc = requests.exceptions.HTTPError(response=response)
    assert reliability.is_retryable_network_error(exc) is False


def test_http_error_with_no_response_object_is_not_retryable():
    """A defensive default - an HTTPError somehow constructed without a
    response must never be ASSUMED transient."""
    assert reliability.is_retryable_network_error(requests.exceptions.HTTPError()) is False


def test_value_error_is_never_retryable():
    """This project's own "no data returned" signal (e.g.
    data_collector.fetch_symbol_history's empty-dataframe check) must
    never be mistaken for a transient network blip."""
    assert reliability.is_retryable_network_error(ValueError("no data")) is False


def test_generic_exception_is_not_retryable_by_default():
    assert reliability.is_retryable_network_error(RuntimeError("something unrelated")) is False


# --- retrying(): the decorator's actual behavior -----------------------------------------


def test_retrying_succeeds_after_transient_failures():
    attempts = {"count": 0}

    @reliability.retrying(LOGGER, **_FAST)
    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert flaky() == "ok"
    assert attempts["count"] == 3


def test_retrying_reraises_the_original_exception_after_exhausting_attempts():
    attempts = {"count": 0}

    @reliability.retrying(LOGGER, **_FAST)
    def always_flaky():
        attempts["count"] += 1
        raise ConnectionError("persistent outage")

    with pytest.raises(ConnectionError, match="persistent outage"):
        always_flaky()
    assert attempts["count"] == _FAST["max_attempts"]


def test_retrying_never_retries_a_non_transient_error():
    """The core safety property: a ValueError (or any non-network
    exception) must fail on the FIRST attempt, never wasting retries
    or delaying an honest failure that retrying could never fix."""
    attempts = {"count": 0}

    @reliability.retrying(LOGGER, **_FAST)
    def raises_value_error():
        attempts["count"] += 1
        raise ValueError("invalid symbol")

    with pytest.raises(ValueError, match="invalid symbol"):
        raises_value_error()
    assert attempts["count"] == 1


def test_retrying_works_without_a_logger():
    """logger is optional - before_sleep_log must only be wired in when
    one is actually given."""
    attempts = {"count": 0}

    @reliability.retrying(None, **_FAST)
    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise TimeoutError("transient")
        return "ok"

    assert flaky() == "ok"
    assert attempts["count"] == 2
