"""Semantic Scholar Graph API search (Sprint 4, "Autonomous academic
research" milestone, Priority 2). Free, no API key required for the
unauthenticated tier - rate-limited to a shared public pool
(documented as roughly 100 requests per 5 minutes per IP, unauthenticated;
see `DEFAULT_DAILY_LIMIT` and `src.agents.budget` for how this project
stays well under that).

**This REST contract is UNVERIFIED against a real response** - see
`openalex_provider.py`'s module docstring for why (no SDK to check
against, and `api.semanticscholar.org` is blocked from this sandbox,
confirmed via `curl` -> `403 Forbidden organization policy`; see
`docs/platform/BLOCKERS.md`). Written from Semantic Scholar's publicly
documented Graph API format; `_parse_paper()` skips (never fabricates)
a record whose expected fields are missing.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import reliability
from ..research.papers import Paper
from . import base

SOURCE_SEMANTIC_SCHOLAR = "semantic_scholar"
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
# A conservative default, well under the documented ~100-req/5-min
# unauthenticated pool - see src.agents.budget for daily enforcement.
DEFAULT_DAILY_LIMIT = 50

_FIELDS = "title,abstract,year,venue,citationCount,authors,externalIds,url"


def _parse_paper(raw: dict[str, Any]) -> Paper | None:
    title = raw.get("title")
    if not title:
        return None
    authors = tuple(a.get("name", "") for a in (raw.get("authors") or []) if a.get("name"))
    external_ids_raw = raw.get("externalIds") or {}
    external_ids: dict[str, str] = {}
    if external_ids_raw.get("DOI"):
        external_ids["doi"] = external_ids_raw["DOI"]
    if external_ids_raw.get("ArXiv"):
        external_ids["arxiv"] = external_ids_raw["ArXiv"]

    return Paper(
        title=title,
        authors=authors,
        year=raw.get("year"),
        venue=raw.get("venue") or None,
        citation_count=raw.get("citationCount"),
        abstract=raw.get("abstract"),
        url=raw.get("url"),
        external_ids=external_ids,
        source=SOURCE_SEMANTIC_SCHOLAR,
    )


def fetch_papers(
    query: str, config: dict[str, Any], logger: logging.Logger, limit: int = 25
) -> base.ProviderResult[list[Paper]]:
    """Real Semantic Scholar paper search for `query`. See module
    docstring for the "unverified against a real response" caveat."""
    import requests

    params = {"query": query, "limit": min(limit, 100), "fields": _FIELDS}

    @reliability.retrying(logger)
    def _do_fetch() -> "requests.Response":
        resp = requests.get(SEMANTIC_SCHOLAR_BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp

    try:
        payload = _do_fetch().json()
    except Exception as exc:  # noqa: BLE001 - network/parse isolation boundary, same pattern as every other provider in this package
        return base.provider_error(SOURCE_SEMANTIC_SCHOLAR, f"failed to fetch Semantic Scholar results for query {query!r}: {exc}")

    raw_results = payload.get("data") or []
    papers = [p for p in (_parse_paper(r) for r in raw_results) if p is not None]
    if not papers:
        return base.unavailable(SOURCE_SEMANTIC_SCHOLAR, f"no Semantic Scholar results for query {query!r}")

    return base.ok(SOURCE_SEMANTIC_SCHOLAR, papers, freshness="live_search")
