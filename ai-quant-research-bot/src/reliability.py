"""Shared retry/backoff policy for outbound network calls (Sprint 3,
Reliability milestone: "test internet outages, stale data... use
Tenacity where beneficial").

**Retries only TRANSIENT failures** - a connection error, a timeout, or
an HTTP 5xx server error. Never a definitive rejection: a 4xx client
error (bad request, invalid symbol, unauthorized), a `ValueError` this
project's own code raises for "no data returned" (e.g.
`data_collector.fetch_symbol_history`'s empty-dataframe check), or any
other non-network exception. Retrying a 404 or a malformed request
forever would only delay the same honest failure this codebase's
"Data Unavailable, never fabricated" convention already requires - it
would never turn into success.

Every retryable call site keeps its OWN existing, single point where a
final, exhausted failure becomes a `provider_error()`/`unavailable()`/
logged-and-skipped result (see `data_providers/sec_provider.py`,
`macro_provider.py`, `data_collector.py`). This module only decides HOW
MANY attempts and HOW LONG to wait between them before that existing
handling runs - it changes nothing about what happens on final
failure, and `reraise=True` ensures the original exception type (not a
tenacity-specific wrapper) is what that existing handling sees.
"""

from __future__ import annotations

import logging
from typing import Any

import tenacity

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MIN_WAIT_SECONDS = 1.0
DEFAULT_MAX_WAIT_SECONDS = 8.0


def is_retryable_network_error(exc: BaseException) -> bool:
    """True for a connection error, a timeout, or (only for `requests`'
    own `HTTPError`) a 5xx server error - covers both `requests`
    (`ConnectionError`/`Timeout`/`HTTPError` all subclass `requests.
    exceptions.RequestException`, itself an `OSError`) and yfinance's
    internal HTTP client, which surfaces a transient failure as a
    plain builtin `ConnectionError`/`TimeoutError` (confirmed directly
    against this project's own sandboxed network policy - see
    docs/platform/OSS_INTEGRATION_AUDIT.md's CCXT section for the same
    class of error). Never true for a 4xx client error or any
    exception type outside this short, explicit list - an unrecognized
    exception is treated as non-transient and NOT retried, the safer
    default when it's unclear whether retrying could ever help."""
    import requests

    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        return response is not None and response.status_code >= 500
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return False


def retrying(
    logger: logging.Logger | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    min_wait_seconds: float = DEFAULT_MIN_WAIT_SECONDS,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
) -> Any:
    """A `tenacity.retry` decorator: up to `max_attempts` tries total,
    jittered exponential backoff between them, retrying ONLY on
    `is_retryable_network_error()`. `reraise=True` means exhausting all
    attempts re-raises the ORIGINAL exception (never tenacity's own
    `RetryError` wrapper) so every existing `except Exception as exc:`
    call site downstream keeps working completely unchanged - this
    decorator is purely an insertion between "the call" and "the
    existing failure handling," not a replacement for either.

    Usage - wrap only the network call itself, never a whole function
    that also does its own non-retryable validation afterward:

        @reliability.retrying(logger)
        def _do_fetch():
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp

        resp = _do_fetch()  # still raises on exhaustion - unchanged contract
    """
    kwargs: dict[str, Any] = dict(
        stop=tenacity.stop_after_attempt(max_attempts),
        wait=tenacity.wait_random_exponential(multiplier=min_wait_seconds, max=max_wait_seconds),
        retry=tenacity.retry_if_exception(is_retryable_network_error),
        reraise=True,
    )
    if logger is not None:
        kwargs["before_sleep"] = tenacity.before_sleep_log(logger, logging.WARNING)
    return tenacity.retry(**kwargs)
