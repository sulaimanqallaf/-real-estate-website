"""src/data_providers/arxiv_provider.py - real arXiv search scoped to
q-fin (Sprint 4, Priority 2). Mocked `requests.get` responses use
arXiv's PUBLICLY DOCUMENTED Atom feed shape as best understood - see
the module's own docstring for why this is explicitly labeled
unverified against a real response."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_providers import arxiv_provider as av
from src.data_providers import base

LOGGER = logging.getLogger("test")

_REALISTIC_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2001.00001v1</id>
    <title>  Momentum Anomaly
      in Equity Returns  </title>
    <summary>  We document a momentum anomaly using a real backtest.  </summary>
    <published>2020-01-02T00:00:00Z</published>
    <author><name>A. Author</name></author>
    <author><name>B. Author</name></author>
  </entry>
</feed>
"""


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def _disable_retry_backoff(monkeypatch):
    monkeypatch.setattr(av.reliability, "retrying", lambda *a, **k: (lambda f: f))


def test_fetch_papers_parses_a_realistic_feed(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_REALISTIC_FEED))

    result = av.fetch_papers("momentum", {}, LOGGER)

    assert result.ok
    assert result.source == av.SOURCE_ARXIV
    paper = result.data[0]
    assert paper.title == "Momentum Anomaly in Equity Returns"
    assert paper.abstract == "We document a momentum anomaly using a real backtest."
    assert paper.year == 2020
    assert paper.authors == ("A. Author", "B. Author")
    assert paper.external_ids == {"arxiv": "2001.00001v1"}
    assert paper.citation_count is None  # arXiv has no citation data - never fabricated
    assert paper.venue == "arXiv"


def test_fetch_papers_scopes_the_query_to_the_configured_category(monkeypatch):
    import requests

    captured = {}

    def _fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_REALISTIC_FEED)

    monkeypatch.setattr(requests, "get", _fake_get)
    av.fetch_papers("momentum", {}, LOGGER, category="q-fin")
    assert captured["params"]["search_query"] == "cat:q-fin.* AND all:momentum"


def test_fetch_papers_with_no_category_searches_all_fields(monkeypatch):
    import requests

    captured = {}

    def _fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(_REALISTIC_FEED)

    monkeypatch.setattr(requests, "get", _fake_get)
    av.fetch_papers("momentum", {}, LOGGER, category="")
    assert captured["params"]["search_query"] == "all:momentum"


def test_fetch_papers_unavailable_on_an_empty_feed(monkeypatch):
    import requests

    empty_feed = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(empty_feed))
    result = av.fetch_papers("obscure", {}, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE


def test_fetch_papers_skips_an_entry_missing_a_title(monkeypatch):
    import requests

    bad_entry_feed = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/abs/9999</id><summary>no title here</summary></entry>
    </feed>
    """
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(bad_entry_feed))
    result = av.fetch_papers("query", {}, LOGGER)
    assert result.status == base.STATUS_UNAVAILABLE


def test_fetch_papers_handles_malformed_xml_as_provider_error(monkeypatch):
    import requests

    _disable_retry_backoff(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(b"not xml at all <<<"))
    result = av.fetch_papers("query", {}, LOGGER)
    assert result.status == base.STATUS_ERROR


def test_fetch_papers_returns_provider_blocked_on_a_network_failure(monkeypatch):
    """Sprint 5: a connection-level failure is BLOCKED, not FAIL -
    distinct from the malformed-XML case above, which IS a real FAIL."""
    import requests

    def _raise(*a, **k):
        raise requests.exceptions.ConnectionError("simulated failure")

    _disable_retry_backoff(monkeypatch)
    monkeypatch.setattr(requests, "get", _raise)
    result = av.fetch_papers("query", {}, LOGGER)
    assert result.status == base.STATUS_BLOCKED


def test_fetch_papers_handles_an_entry_with_no_published_date(monkeypatch):
    import requests

    feed = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/abs/2001.1</id><title>No Date Paper</title><summary>abstract</summary></entry>
    </feed>
    """
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(feed))
    result = av.fetch_papers("query", {}, LOGGER)
    assert result.ok
    assert result.data[0].year is None
