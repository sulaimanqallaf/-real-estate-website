"""src/research/paper_memory.py - permanent research memory with
citations (Sprint 4, Priority 2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

from src.research import paper_memory
from src.research.papers import EvidenceScore, Paper


def _paper(**overrides):
    defaults = dict(
        title="Momentum Anomaly", authors=("A. Author",), year=2020, venue="JF", citation_count=10,
        abstract="abstract text", url="https://example.com/1", external_ids={"doi": "10.1/abc"}, source="openalex",
    )
    defaults.update(overrides)
    return Paper(**defaults)


_SCORE = EvidenceScore(citation_tier="medium", has_empirical_keywords=True, matched_strategies=("Momentum Breakout",), overall_score=0.7)


def test_default_db_path_is_separate_from_hypothesis_ledger():
    from src.research.hypothesis_ledger import default_db_path as hyp_db_path

    assert paper_memory.default_db_path({}).name != hyp_db_path({}).name


def test_default_db_path_is_overridable_via_config(tmp_path):
    config = {"research": {"paper_memory_db_path": str(tmp_path / "custom.db")}}
    assert paper_memory.default_db_path(config) == tmp_path / "custom.db"


def test_paper_key_prefers_doi():
    assert paper_memory.paper_key(_paper(external_ids={"doi": "10.1/ABC", "arxiv": "2001.1"})) == "doi:10.1/abc"


def test_paper_key_falls_back_to_arxiv_without_doi():
    assert paper_memory.paper_key(_paper(external_ids={"arxiv": "2001.00001"})) == "arxiv:2001.00001"


def test_paper_key_falls_back_to_title_with_neither():
    assert paper_memory.paper_key(_paper(external_ids={}, title="Some Paper!")) == "title:some paper"


def test_get_paper_is_none_before_any_record(tmp_path):
    db_path = tmp_path / "papers.db"
    assert paper_memory.get_paper(db_path, "doi:x") is None


def test_record_and_get_paper_round_trips(tmp_path):
    db_path = tmp_path / "papers.db"
    key = paper_memory.record_paper(db_path, _paper(), _SCORE, hypothesis_ids=["hyp_1"])

    record = paper_memory.get_paper(db_path, key)
    assert record["title"] == "Momentum Anomaly"
    assert record["authors"] == ["A. Author"]
    assert record["external_ids"] == {"doi": "10.1/abc"}
    assert record["sources"] == ["openalex"]
    assert record["hypothesis_ids"] == ["hyp_1"]
    assert record["evidence_score"]["overall_score"] == 0.7


def test_record_paper_upserts_by_key_never_duplicates(tmp_path):
    db_path = tmp_path / "papers.db"
    paper_memory.record_paper(db_path, _paper(citation_count=10), _SCORE)
    paper_memory.record_paper(db_path, _paper(citation_count=99), _SCORE)

    all_rows = paper_memory.query_papers(db_path)
    assert len(all_rows) == 1
    assert all_rows[0]["citation_count"] == 99  # refreshed to the latest known value


def test_record_paper_merges_sources_across_runs(tmp_path):
    db_path = tmp_path / "papers.db"
    paper_memory.record_paper(db_path, _paper(source="openalex"), _SCORE)
    paper_memory.record_paper(db_path, _paper(source="semantic_scholar"), _SCORE)

    record = paper_memory.get_paper(db_path, paper_memory.paper_key(_paper()))
    assert set(record["sources"]) == {"openalex", "semantic_scholar"}


def test_record_paper_merges_hypothesis_ids_across_runs(tmp_path):
    db_path = tmp_path / "papers.db"
    paper_memory.record_paper(db_path, _paper(), _SCORE, hypothesis_ids=["hyp_1"])
    paper_memory.record_paper(db_path, _paper(), _SCORE, hypothesis_ids=["hyp_2"])

    record = paper_memory.get_paper(db_path, paper_memory.paper_key(_paper()))
    assert set(record["hypothesis_ids"]) == {"hyp_1", "hyp_2"}


def test_record_paper_preserves_first_seen_at_across_updates(tmp_path):
    db_path = tmp_path / "papers.db"
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = datetime(2026, 2, 1, tzinfo=timezone.utc)
    paper_memory.record_paper(db_path, _paper(), _SCORE, now=first)
    paper_memory.record_paper(db_path, _paper(), _SCORE, now=second)

    record = paper_memory.get_paper(db_path, paper_memory.paper_key(_paper()))
    assert record["first_seen_at"] == first.isoformat()
    assert record["last_seen_at"] == second.isoformat()


def test_query_papers_returns_empty_list_on_missing_database(tmp_path):
    assert paper_memory.query_papers(tmp_path / "nonexistent.db") == []


def test_query_papers_most_recent_first(tmp_path):
    db_path = tmp_path / "papers.db"
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    paper_memory.record_paper(db_path, _paper(title="Older", external_ids={"doi": "10.1/old"}), _SCORE, now=t1)
    paper_memory.record_paper(db_path, _paper(title="Newer", external_ids={"doi": "10.1/new"}), _SCORE, now=t2)

    rows = paper_memory.query_papers(db_path)
    assert [r["title"] for r in rows] == ["Newer", "Older"]


def test_query_papers_filters_by_min_overall_score(tmp_path):
    db_path = tmp_path / "papers.db"
    low = EvidenceScore(citation_tier="low", has_empirical_keywords=False, matched_strategies=(), overall_score=0.1)
    high = EvidenceScore(citation_tier="high", has_empirical_keywords=True, matched_strategies=("Momentum Breakout",), overall_score=0.9)
    paper_memory.record_paper(db_path, _paper(title="Weak", external_ids={"doi": "10.1/weak"}), low)
    paper_memory.record_paper(db_path, _paper(title="Strong", external_ids={"doi": "10.1/strong"}), high)

    rows = paper_memory.query_papers(db_path, min_overall_score=0.5)
    assert [r["title"] for r in rows] == ["Strong"]


def test_query_papers_respects_limit(tmp_path):
    db_path = tmp_path / "papers.db"
    for i in range(5):
        paper_memory.record_paper(db_path, _paper(title=f"Paper {i}", external_ids={"doi": f"10.1/{i}"}), _SCORE)
    assert len(paper_memory.query_papers(db_path, limit=2)) == 2
