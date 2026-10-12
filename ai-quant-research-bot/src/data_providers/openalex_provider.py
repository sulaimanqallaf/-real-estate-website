"""OpenAlex scholarly-works search (Sprint 4, "Autonomous academic
research" milestone, Priority 2: "...using legitimate OpenAlex, arXiv,
Semantic Scholar...sources"). Free, no API key, no paid plan - the
"polite pool" (faster, more reliable rate limits) only needs a contact
email in the `mailto` query param, not a credential.

**This REST contract is UNVERIFIED against a real response.** Unlike
Sprint 3's Alpaca provider (verified against the real `alpaca-py` SDK
source on GitHub), there is no SDK to check OpenAlex's JSON shape
against - this is written from OpenAlex's publicly documented API
format, as best understood, and has never actually been run against
`api.openalex.org`: this sandbox's egress proxy blocks it (confirmed
directly via `curl` -> `403 Forbidden organization policy`, the same
class of block as every other external host this project touches -
see `docs/platform/BLOCKERS.md`). Treat this module as **real,
tested-against-fixtures code of unverified real-world accuracy** until
it's actually run with real network access - if OpenAlex's true
response shape differs from what's assumed here, `_parse_work()`
degrades to skipping that one malformed record (never raises, never
fabricates a Paper from a shape it doesn't recognize), but it will
quietly return fewer/no results rather than visibly erroring, so the
very first real run should have its output inspected by a human, not
assumed correct.

OpenAlex does not return a plain abstract string - it returns an
`abstract_inverted_index` (word -> list of positions), reconstructed
here by `_reconstruct_abstract()`.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import reliability
from ..research.papers import Paper
from . import base

SOURCE_OPENALEX = "openalex"
OPENALEX_BASE_URL = "https://api.openalex.org/works"


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    if not positions:
        return None
    return " ".join(positions[i] for i in sorted(positions))


def _parse_work(raw: dict[str, Any]) -> Paper | None:
    title = raw.get("title") or raw.get("display_name")
    if not title:
        return None
    authors = tuple(
        a.get("author", {}).get("display_name", "") for a in (raw.get("authorships") or []) if a.get("author", {}).get("display_name")
    )
    venue = (raw.get("primary_location") or {}).get("source", {}).get("display_name") if raw.get("primary_location") else None
    doi = raw.get("doi")
    external_ids = {"doi": doi.replace("https://doi.org/", "")} if doi else {}

    return Paper(
        title=title,
        authors=authors,
        year=raw.get("publication_year"),
        venue=venue,
        citation_count=raw.get("cited_by_count"),
        abstract=_reconstruct_abstract(raw.get("abstract_inverted_index")),
        url=raw.get("id"),
        external_ids=external_ids,
        source=SOURCE_OPENALEX,
    )


def fetch_papers(
    query: str, config: dict[str, Any], logger: logging.Logger, limit: int = 25
) -> base.ProviderResult[list[Paper]]:
    """Real OpenAlex works search for `query` - see module docstring
    for the "unverified against a real response" caveat. Returns
    `unavailable()` on a network/HTTP failure, never a fabricated empty
    success. A work OpenAlex returns in a shape `_parse_work()` doesn't
    recognize is skipped, not fabricated into a half-filled `Paper`."""
    import requests

    contact_email = config.get("research", {}).get("contact_email")
    params: dict[str, Any] = {"search": query, "per-page": min(limit, 200)}
    if contact_email:
        params["mailto"] = contact_email

    @reliability.retrying(logger)
    def _do_fetch() -> "requests.Response":
        resp = requests.get(OPENALEX_BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp

    try:
        payload = _do_fetch().json()
    except Exception as exc:  # noqa: BLE001 - network/parse isolation boundary, same pattern as every other provider in this package
        return base.provider_error(SOURCE_OPENALEX, f"failed to fetch OpenAlex works for query {query!r}: {exc}")

    raw_results = payload.get("results") or []
    papers = [p for p in (_parse_work(r) for r in raw_results) if p is not None]
    if not papers:
        return base.unavailable(SOURCE_OPENALEX, f"no OpenAlex results for query {query!r}")

    return base.ok(SOURCE_OPENALEX, papers, freshness="live_search")
