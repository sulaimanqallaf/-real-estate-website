"""src/data_providers/semantic_scholar_provider.py - real Semantic
Scholar Graph API search (Sprint 4, Priority 2). Mocked `requests.get`
responses use Semantic Scholar's PUBLICLY DOCUMENTED Graph API shape
as best understood - see the module's own docstring for why this is
explicitly labeled unverified against a real response."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import base
from src.data_providers import semantic_scholar_provider as ss

LOGGER = logging.getLogger("test")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _disable_retry_backoff(monkeypatch):
    monkeypatch.setattr(ss.reliability, "retrying", lambda *a, **k: (lambda f: f))


_REALISTIC_PAPER = {
    "paperId": "abc123",
    "title": "Momentum Anomaly in Equity Returns",
    "abstract": "We document a momentum anomaly using a real backtest.",
    "year": 2020,
    "venue": "Journal of Finance",
    "citationCount": 42,
    "authors": [{"name": "A. Author"}, {"name": "B. Author"}],
    "externalIds": {"DOI": "10.1/abc", "ArXiv": "2001.00001"},
    "url": "https://www.semanticscholar.org/paper/abc123",
}


def test_fetch_papers_parses_a_realistic_response(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"data": [_REALISTIC_PAPER]}))

    result = ss.fetch_papers("momentum anomaly", {}, LOGGER)

    assert result.ok
    assert result.source == ss.SOURCE_SEMANTIC_SCHOLAR
    paper = result.data[0]
    assert paper.title == "Momentum Anomaly in Equity Returns"
    assert paper.citation_count == 42
    assert paper.external_ids == {"doi": "10.1/abc", "arxiv": "2001.00001"}
    assert paper.authors == ("A. Author", "B. Author")


def test_fetch_papers_requests_the_expected_fields(monkeypatch):
    import requests

    captured = {}

    def _fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse({"data": [_REALISTIC_PAPER]})

    monkeypatch.setattr(requests, "get", _fake_get)
    ss.fetch_papers("query", {}, LOGGER)
    assert "citationCount" in captured["params"]["fields"]
    assert "abstract" in captured["params"]["fields"]


def test_fetch_papers_unavailable_when_no_results(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"data": []}))
    result = ss.fetch_papers("obscure", {}, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE


def test_fetch_papers_skips_a_paper_missing_a_title(monkeypatch):
    import requests

    broken = {**_REALISTIC_PAPER, "title": None}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"data": [broken, _REALISTIC_PAPER]}))
    result = ss.fetch_papers("query", {}, LOGGER)
    assert result.ok
    assert len(result.data) == 1


def test_fetch_papers_handles_missing_external_ids_and_venue_honestly(monkeypatch):
    import requests

    bare = {"paperId": "x", "title": "Bare Paper", "abstract": None, "year": None, "venue": "", "citationCount": None, "authors": [], "externalIds": {}, "url": None}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"data": [bare]}))
    result = ss.fetch_papers("query", {}, LOGGER)
    paper = result.data[0]
    assert paper.external_ids == {}
    assert paper.venue is None
    assert paper.citation_count is None


def test_fetch_papers_returns_provider_blocked_on_a_network_failure(monkeypatch):
    """Sprint 5: a connection-level failure is BLOCKED, not FAIL."""
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("simulated failure")

    _disable_retry_backoff(monkeypatch)
    monkeypatch.setattr(requests, "get", _raise)
    result = ss.fetch_papers("query", {}, LOGGER)
    assert result.status == base.STATUS_BLOCKED


def test_fetch_papers_returns_provider_error_on_a_non_network_failure(monkeypatch):
    """A response was reached but couldn't be parsed - a real
    application-level failure, so FAIL, not BLOCKED."""
    import requests

    class _BadJsonResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not valid JSON")

    _disable_retry_backoff(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _BadJsonResponse())
    result = ss.fetch_papers("query", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_fetch_papers_recovers_from_a_transient_failure(monkeypatch):
    import requests

    calls = {"n": 0}

    def _flaky_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("transient")
        return _FakeResponse({"data": [_REALISTIC_PAPER]})

    monkeypatch.setattr(requests, "get", _flaky_get)
    result = ss.fetch_papers("query", {}, LOGGER)
    assert result.ok
    assert calls["n"] == 2
