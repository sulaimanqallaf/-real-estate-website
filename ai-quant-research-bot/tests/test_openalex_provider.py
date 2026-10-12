"""src/data_providers/openalex_provider.py - real OpenAlex works
search (Sprint 4, Priority 2). Mocked `requests.get` responses match
OpenAlex's PUBLICLY DOCUMENTED response shape as best understood - see
the module's own docstring for why this is explicitly labeled
unverified against a real response (every academic host is blocked
from this sandbox)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.data_providers import base
from src.data_providers import openalex_provider as oa

LOGGER = logging.getLogger("test")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _disable_retry_backoff(monkeypatch):
    monkeypatch.setattr(oa.reliability, "retrying", lambda *a, **k: (lambda f: f))


_REALISTIC_WORK = {
    "id": "https://openalex.org/W123",
    "title": "Momentum Anomaly in Equity Returns",
    "doi": "https://doi.org/10.1/abc",
    "publication_year": 2020,
    "cited_by_count": 42,
    "primary_location": {"source": {"display_name": "Journal of Finance"}},
    "authorships": [{"author": {"display_name": "A. Author"}}, {"author": {"display_name": "B. Author"}}],
    "abstract_inverted_index": {"We": [0], "document": [1], "a": [2], "momentum": [3], "anomaly": [4]},
}


def test_fetch_papers_parses_a_realistic_response(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"results": [_REALISTIC_WORK]}))

    result = oa.fetch_papers("momentum anomaly", {}, LOGGER)

    assert result.ok
    assert result.source == oa.SOURCE_OPENALEX
    paper = result.data[0]
    assert paper.title == "Momentum Anomaly in Equity Returns"
    assert paper.year == 2020
    assert paper.citation_count == 42
    assert paper.venue == "Journal of Finance"
    assert paper.authors == ("A. Author", "B. Author")
    assert paper.external_ids == {"doi": "10.1/abc"}
    assert paper.abstract == "We document a momentum anomaly"
    assert paper.source == oa.SOURCE_OPENALEX


def test_fetch_papers_sends_mailto_when_contact_email_configured(monkeypatch):
    import requests

    captured = {}

    def _fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse({"results": [_REALISTIC_WORK]})

    monkeypatch.setattr(requests, "get", _fake_get)
    oa.fetch_papers("query", {"research": {"contact_email": "me@example.com"}}, LOGGER)
    assert captured["params"]["mailto"] == "me@example.com"


def test_fetch_papers_omits_mailto_when_not_configured(monkeypatch):
    import requests

    captured = {}

    def _fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse({"results": [_REALISTIC_WORK]})

    monkeypatch.setattr(requests, "get", _fake_get)
    oa.fetch_papers("query", {}, LOGGER)
    assert "mailto" not in captured["params"]


def test_fetch_papers_unavailable_when_no_results(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"results": []}))
    result = oa.fetch_papers("obscure query", {}, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE
    assert result.data is None


def test_fetch_papers_skips_a_work_missing_a_title(monkeypatch):
    import requests

    broken = {**_REALISTIC_WORK, "title": None, "display_name": None}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"results": [broken, _REALISTIC_WORK]}))
    result = oa.fetch_papers("query", {}, LOGGER)
    assert result.ok
    assert len(result.data) == 1


def test_fetch_papers_handles_missing_abstract_and_missing_doi_honestly(monkeypatch):
    import requests

    bare = {
        "id": "https://openalex.org/W999", "title": "Bare Paper", "doi": None,
        "publication_year": None, "cited_by_count": None, "primary_location": None,
        "authorships": [], "abstract_inverted_index": None,
    }
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"results": [bare]}))
    result = oa.fetch_papers("query", {}, LOGGER)
    paper = result.data[0]
    assert paper.abstract is None
    assert paper.external_ids == {}
    assert paper.citation_count is None
    assert paper.venue is None


def test_fetch_papers_returns_provider_error_on_network_failure(monkeypatch):
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("simulated failure")

    _disable_retry_backoff(monkeypatch)
    monkeypatch.setattr(requests, "get", _raise)
    result = oa.fetch_papers("query", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_fetch_papers_recovers_from_a_transient_failure(monkeypatch):
    import requests

    calls = {"n": 0}

    def _flaky_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("transient")
        return _FakeResponse({"results": [_REALISTIC_WORK]})

    monkeypatch.setattr(requests, "get", _flaky_get)
    result = oa.fetch_papers("query", {}, LOGGER)
    assert result.ok
    assert calls["n"] == 2


def test_reconstruct_abstract_handles_out_of_order_indices():
    inverted = {"anomaly": [3], "We": [0], "a": [2], "momentum": [1]}
    assert oa._reconstruct_abstract(inverted) == "We momentum a anomaly"


def test_reconstruct_abstract_handles_none():
    assert oa._reconstruct_abstract(None) is None
