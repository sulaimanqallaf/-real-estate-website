"""src/research/papers.py - shared Paper shape, dedup, and evidence
scoring for the Research Scholar Agent (Sprint 4, Priority 2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.research import papers


def _paper(**overrides):
    defaults = dict(
        title="Momentum Anomaly in Equity Returns", authors=("A. Author",), year=2020, venue="Journal of Finance",
        citation_count=100, abstract="We document a momentum anomaly using backtests.", url="https://example.com/1",
        external_ids={}, source="openalex",
    )
    defaults.update(overrides)
    return papers.Paper(**defaults)


# --- normalize_title -----------------------------------------------------------------------


def test_normalize_title_lowercases_and_strips_punctuation():
    assert papers.normalize_title("Momentum, Anomaly! (2020)") == "momentum anomaly 2020"


def test_normalize_title_collapses_whitespace():
    assert papers.normalize_title("A   Study   of  Reversal") == "a study of reversal"


# --- dedupe_papers ---------------------------------------------------------------------------


def test_dedupe_merges_by_shared_doi():
    a = _paper(source="openalex", external_ids={"doi": "10.1/ABC"}, abstract=None, citation_count=None)
    b = _paper(source="semantic_scholar", external_ids={"doi": "10.1/abc"}, abstract="real abstract", citation_count=50)
    merged = papers.dedupe_papers([a, b])
    assert len(merged) == 1
    assert merged[0].abstract == "real abstract"  # richer record kept
    assert set(merged[0].sources) == {"openalex", "semantic_scholar"}


def test_dedupe_merges_by_shared_arxiv_id_when_no_doi():
    a = _paper(source="arxiv", external_ids={"arxiv": "2001.00001"})
    b = _paper(source="semantic_scholar", external_ids={"arxiv": "2001.00001"})
    merged = papers.dedupe_papers([a, b])
    assert len(merged) == 1


def test_dedupe_falls_back_to_normalized_title_with_no_shared_id():
    a = _paper(source="openalex", title="Momentum Anomaly!", external_ids={})
    b = _paper(source="arxiv", title="momentum anomaly", external_ids={})
    merged = papers.dedupe_papers([a, b])
    assert len(merged) == 1


def test_dedupe_keeps_genuinely_different_papers_separate():
    a = _paper(title="Momentum Anomaly", external_ids={})
    b = _paper(title="Mean Reversion in FX Markets", external_ids={})
    merged = papers.dedupe_papers([a, b])
    assert len(merged) == 2


def test_dedupe_preserves_first_seen_order():
    a = _paper(title="First Paper", external_ids={})
    b = _paper(title="Second Paper", external_ids={})
    merged = papers.dedupe_papers([a, b])
    assert [p.title for p in merged] == ["First Paper", "Second Paper"]


def test_dedupe_never_drops_external_ids_from_either_record():
    a = _paper(source="openalex", external_ids={"doi": "10.1/x"})
    b = _paper(source="arxiv", external_ids={"doi": "10.1/x", "arxiv": "2001.00001"})
    merged = papers.dedupe_papers([a, b])
    assert merged[0].external_ids.get("arxiv") == "2001.00001"


# --- matched_strategies / score_evidence ----------------------------------------------------


def test_matched_strategies_detects_momentum():
    assert "Momentum Breakout" in papers.matched_strategies("A study of price momentum and breakout patterns")


def test_matched_strategies_detects_mean_reversion():
    assert "Mean Reversion" in papers.matched_strategies("Evidence of mean reversion and overreaction in prices")


def test_matched_strategies_returns_empty_for_unrelated_text():
    assert papers.matched_strategies("A survey of macroeconomic inflation models") == ()


def test_score_evidence_high_citation_and_keywords_scores_high():
    paper = _paper(citation_count=200, abstract="An empirical backtest of the momentum anomaly, out-of-sample.")
    score = papers.score_evidence(paper)
    assert score.citation_tier == "high"
    assert score.has_empirical_keywords is True
    assert score.matched_strategies
    assert score.overall_score > 0.8


def test_score_evidence_unknown_citation_and_no_keywords_scores_low():
    paper = _paper(citation_count=None, abstract="A theoretical discussion of utility functions.", title="Utility Theory")
    score = papers.score_evidence(paper)
    assert score.citation_tier == "unknown"
    assert score.has_empirical_keywords is False
    assert score.matched_strategies == ()
    assert score.overall_score < 0.2


def test_score_evidence_citation_tiers():
    assert papers.score_evidence(_paper(citation_count=0)).citation_tier == "low"
    assert papers.score_evidence(_paper(citation_count=5)).citation_tier == "medium"
    assert papers.score_evidence(_paper(citation_count=50)).citation_tier == "high"
    assert papers.score_evidence(_paper(citation_count=None)).citation_tier == "unknown"


def test_paper_sources_defaults_to_its_own_source_when_unset():
    paper = papers.Paper(title="X", authors=(), year=None, venue=None, citation_count=None, abstract=None, url=None, external_ids={}, source="arxiv")
    assert paper.sources == ("arxiv",)
