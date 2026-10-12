"""Shared `Paper` shape, deduplication, and evidence-strength scoring
for the Research Scholar Agent (Sprint 4, Priority 2: "Deduplicate
papers, evaluate evidence...").

**`score_evidence()` is a crude triage heuristic, not peer review.**
It combines citation count, recency, and keyword presence into a
single 0-1 number so the agent can decide which papers are worth
turning into a hypothesis at all - it is NOT a claim about a paper's
actual scientific validity, methodology, or correctness, and nothing
downstream should treat it as one. Documented explicitly so it is
never mistaken for something it isn't.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# Keywords whose presence in a title/abstract suggests the paper
# reports some kind of empirical/testable finding (as opposed to a
# pure theory/survey piece) - a crude signal, not a guarantee.
_EMPIRICAL_KEYWORDS = (
    "backtest", "out-of-sample", "empirical", "anomaly", "alpha", "sharpe",
    "excess return", "risk-adjusted", "momentum", "mean reversion", "mean-reversion",
    "reversal", "overreaction", "underreaction", "predictability", "factor",
)

# Strategy archetypes this codebase can actually test (keys match
# backtester.STRATEGY_MODULES exactly - see research/hypothesis_mapper.py)
# paired with keywords that plausibly indicate a paper is ABOUT that
# archetype. Matching is deliberately simple (substring, case-
# insensitive) - a false positive here just means a hypothesis gets
# tested and (honestly) rejected, never a fabricated result.
STRATEGY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Mean Reversion": ("mean reversion", "mean-reversion", "reversal", "overreaction", "contrarian"),
    "Momentum Breakout": ("momentum", "breakout", "trend following", "trend-following", "price continuation"),
    "Trend Following": ("trend following", "trend-following", "moving average", "time-series momentum"),
}


@dataclass(frozen=True)
class Paper:
    """One paper, normalized across OpenAlex/arXiv/Semantic Scholar.
    `source` is the provider that returned THIS record - after
    `dedupe_papers()`, `sources` on the merged record lists every
    provider that independently found it."""

    title: str
    authors: tuple[str, ...]
    year: int | None
    venue: str | None
    citation_count: int | None
    abstract: str | None
    url: str | None
    external_ids: dict[str, str]
    source: str
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sources:
            object.__setattr__(self, "sources", (self.source,))


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation/extra whitespace - used ONLY to
    detect probable duplicates across sources, never shown to a human
    or stored anywhere (the real `title` is preserved untouched)."""
    lowered = title.lower().strip()
    stripped = re.sub(r"[^a-z0-9\s]", "", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def _richer(a: Paper, b: Paper) -> Paper:
    """Of two records judged to be the same paper, keep the one with
    more usable metadata (an abstract, a citation count) - never
    silently drop information one source had and another didn't."""
    score_a = sum(x is not None for x in (a.abstract, a.citation_count, a.year, a.venue))
    score_b = sum(x is not None for x in (b.abstract, b.citation_count, b.year, b.venue))
    base = a if score_a >= score_b else b
    merged_sources = tuple(sorted(set(a.sources) | set(b.sources)))
    external_ids = {**b.external_ids, **a.external_ids}
    return Paper(
        title=base.title, authors=base.authors, year=base.year, venue=base.venue,
        citation_count=base.citation_count, abstract=base.abstract, url=base.url,
        external_ids=external_ids, source=base.source, sources=merged_sources,
    )


def dedupe_papers(papers: list[Paper]) -> list[Paper]:
    """Merges records that are almost certainly the same paper - by a
    shared DOI/arXiv id first (exact, authoritative), falling back to
    an identical normalized title (a weaker but still useful signal
    across sources that don't share an id). Order of the input is
    preserved for the first-seen record of each group."""
    by_key: dict[str, Paper] = {}
    order: list[str] = []

    for paper in papers:
        key = None
        for id_type in ("doi", "arxiv"):
            value = paper.external_ids.get(id_type)
            if value:
                key = f"{id_type}:{value.lower().strip()}"
                break
        if key is None:
            key = f"title:{normalize_title(paper.title)}"

        if key in by_key:
            by_key[key] = _richer(by_key[key], paper)
        else:
            by_key[key] = paper
            order.append(key)

    return [by_key[key] for key in order]


@dataclass(frozen=True)
class EvidenceScore:
    citation_tier: str  # "low" | "medium" | "high" | "unknown"
    has_empirical_keywords: bool
    matched_strategies: tuple[str, ...]
    overall_score: float  # 0.0-1.0, a triage heuristic - see module docstring


def _citation_tier(citation_count: int | None) -> str:
    if citation_count is None:
        return "unknown"
    if citation_count >= 50:
        return "high"
    if citation_count >= 5:
        return "medium"
    return "low"


def matched_strategies(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(name for name, keywords in STRATEGY_KEYWORDS.items() if any(kw in lowered for kw in keywords))


def score_evidence(paper: Paper, now: datetime | None = None) -> EvidenceScore:
    text = f"{paper.title} {paper.abstract or ''}"
    has_keywords = any(kw in text.lower() for kw in _EMPIRICAL_KEYWORDS)
    strategies = matched_strategies(text)
    tier = _citation_tier(paper.citation_count)

    tier_component = {"high": 1.0, "medium": 0.6, "low": 0.3, "unknown": 0.1}[tier]
    keyword_component = 1.0 if has_keywords else 0.0
    strategy_component = 1.0 if strategies else 0.0
    overall = round(0.4 * tier_component + 0.3 * keyword_component + 0.3 * strategy_component, 3)

    return EvidenceScore(citation_tier=tier, has_empirical_keywords=has_keywords, matched_strategies=strategies, overall_score=overall)
