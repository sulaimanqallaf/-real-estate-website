"""arXiv search, scoped to the `q-fin` (quantitative finance) category
by default (Sprint 4, "Autonomous academic research" milestone,
Priority 2). Free, no API key - arXiv's search API has never required
one.

**This Atom-XML contract is UNVERIFIED against a real response** - see
`openalex_provider.py`'s module docstring for why (no SDK to check
against, and `export.arxiv.org` is blocked from this sandbox,
confirmed via `curl` -> `403 Forbidden organization policy`; see
`docs/platform/BLOCKERS.md`). Written from arXiv's publicly documented
Atom feed format; `_parse_entry()` skips (never fabricates) an entry
whose expected tags are missing.

arXiv has no citation-count field at all (unlike OpenAlex/Semantic
Scholar) - every `Paper.citation_count` from this provider is `None`,
honestly, never a guessed 0.
"""

from __future__ import annotations

import logging
from typing import Any
from xml.etree import ElementTree

from .. import reliability
from ..research.papers import Paper
from . import base

SOURCE_ARXIV = "arxiv"
ARXIV_BASE_URL = "http://export.arxiv.org/api/query"
DEFAULT_CATEGORY = "q-fin"

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _parse_entry(entry: ElementTree.Element) -> Paper | None:
    title_el = entry.find("atom:title", _ATOM_NS)
    if title_el is None or not (title_el.text or "").strip():
        return None
    title = " ".join((title_el.text or "").split())

    summary_el = entry.find("atom:summary", _ATOM_NS)
    abstract = " ".join((summary_el.text or "").split()) if summary_el is not None and summary_el.text else None

    published_el = entry.find("atom:published", _ATOM_NS)
    year = None
    if published_el is not None and published_el.text:
        try:
            year = int(published_el.text[:4])
        except ValueError:
            year = None

    authors = tuple(
        (name_el.text or "").strip()
        for author_el in entry.findall("atom:author", _ATOM_NS)
        for name_el in [author_el.find("atom:name", _ATOM_NS)]
        if name_el is not None and name_el.text
    )

    id_el = entry.find("atom:id", _ATOM_NS)
    url = id_el.text.strip() if id_el is not None and id_el.text else None
    arxiv_id = url.rsplit("/", 1)[-1] if url else None

    return Paper(
        title=title,
        authors=authors,
        year=year,
        venue="arXiv",
        citation_count=None,
        abstract=abstract,
        url=url,
        external_ids={"arxiv": arxiv_id} if arxiv_id else {},
        source=SOURCE_ARXIV,
    )


def fetch_papers(
    query: str, config: dict[str, Any], logger: logging.Logger, limit: int = 25, category: str = DEFAULT_CATEGORY
) -> base.ProviderResult[list[Paper]]:
    """Real arXiv search for `query`, restricted to `category` (default
    `q-fin`, arXiv's own quantitative-finance category - the concrete
    "official financial research source" this provider targets within
    arXiv). See module docstring for the "unverified against a real
    response" caveat."""
    import requests

    search_query = f"cat:{category}.* AND all:{query}" if category else f"all:{query}"
    params = {"search_query": search_query, "start": 0, "max_results": min(limit, 100)}

    @reliability.retrying(logger)
    def _do_fetch() -> "requests.Response":
        resp = requests.get(ARXIV_BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp

    try:
        response = _do_fetch()
        root = ElementTree.fromstring(response.content)
    except Exception as exc:  # noqa: BLE001 - network/parse isolation boundary, same pattern as every other provider in this package
        return base.provider_error(SOURCE_ARXIV, f"failed to fetch arXiv results for query {query!r}: {exc}")

    entries = root.findall("atom:entry", _ATOM_NS)
    papers = [p for p in (_parse_entry(e) for e in entries) if p is not None]
    if not papers:
        return base.unavailable(SOURCE_ARXIV, f"no arXiv results for query {query!r} in category {category!r}")

    return base.ok(SOURCE_ARXIV, papers, freshness="live_search")
