"""Permanent research memory for the Research Scholar Agent (Sprint 4,
Priority 2: "...store permanent research memory with citations.") -
every paper the agent has ever seen, its evidence score, which
strategies it matched, and whether it led to a hypothesis, kept in its
OWN database file (`data/research/paper_memory.db` by default - never
`research_hypotheses.db`/`decision_ledger.db`), same separation
discipline `hypothesis_ledger.py` already established: a paper's
metadata is a different kind of record from a backtest result, and
neither should be mistaken for, or accidentally queried alongside, a
real trading decision.

**Insert-or-update by a stable paper key, never a duplicate row per
re-fetch.** The same paper will be re-fetched on later agent runs
(it's still relevant research); `record_paper()` upserts by
`paper_key` (DOI, else arXiv id, else a normalized-title hash) so the
permanent record accumulates/refreshes citation counts and
sources-seen over time rather than growing one row per day forever.

**Citations are never dropped.** `authors`/`venue`/`year`/`url`/
`external_ids` are stored on every row specifically so any hypothesis
this paper contributed to can always be traced back to a real,
checkable citation - this module has no code path that records a
paper's influence without also recording how to find/verify it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .papers import EvidenceScore, Paper, normalize_title

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors_json TEXT,
    year INTEGER,
    venue TEXT,
    citation_count INTEGER,
    abstract TEXT,
    url TEXT,
    external_ids_json TEXT,
    sources_json TEXT,
    evidence_score_json TEXT,
    hypothesis_ids_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
"""

_DB_FILENAME = "paper_memory.db"


def default_db_path(config: dict[str, Any] | None = None) -> Path:
    """Deliberately NOT `hypothesis_ledger.py`'s DB - see module
    docstring. Override via `config["research"]["paper_memory_db_path"]`."""
    from ..utils import resolve_path

    override = (config or {}).get("research", {}).get("paper_memory_db_path")
    if override:
        return resolve_path(override)
    return resolve_path(f"data/research/{_DB_FILENAME}")


def paper_key(paper: Paper) -> str:
    """A stable identity for upserting - DOI first (most authoritative),
    then arXiv id, falling back to the normalized title when a paper
    has neither (still deterministic, just weaker than a real id)."""
    if paper.external_ids.get("doi"):
        return f"doi:{paper.external_ids['doi'].lower().strip()}"
    if paper.external_ids.get("arxiv"):
        return f"arxiv:{paper.external_ids['arxiv'].lower().strip()}"
    return f"title:{normalize_title(paper.title)}"


@contextmanager
def _connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_paper(
    db_path: str | Path, paper: Paper, evidence: EvidenceScore, hypothesis_ids: list[str] | None = None, now: datetime | None = None
) -> str:
    """Upserts one paper by `paper_key()`. On an existing row, merges
    `sources` and `hypothesis_ids` (union, never a loss) and refreshes
    the citation count/evidence score/last_seen_at to the latest known
    values - `first_seen_at` is set once and never changed."""
    key = paper_key(paper)
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    new_hypothesis_ids = hypothesis_ids or []

    with _connect(db_path) as conn:
        existing = conn.execute("SELECT * FROM papers WHERE paper_key = ?", (key,)).fetchone()
        if existing is not None:
            merged_sources = sorted(set(json.loads(existing["sources_json"] or "[]")) | set(paper.sources))
            merged_hyp_ids = sorted(set(json.loads(existing["hypothesis_ids_json"] or "[]")) | set(new_hypothesis_ids))
            first_seen_at = existing["first_seen_at"]
        else:
            merged_sources = sorted(set(paper.sources))
            merged_hyp_ids = sorted(set(new_hypothesis_ids))
            first_seen_at = timestamp

        conn.execute(
            """INSERT INTO papers (
                paper_key, title, authors_json, year, venue, citation_count, abstract, url,
                external_ids_json, sources_json, evidence_score_json, hypothesis_ids_json,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_key) DO UPDATE SET
                title=excluded.title, authors_json=excluded.authors_json, year=excluded.year,
                venue=excluded.venue, citation_count=excluded.citation_count, abstract=excluded.abstract,
                url=excluded.url, external_ids_json=excluded.external_ids_json, sources_json=excluded.sources_json,
                evidence_score_json=excluded.evidence_score_json, hypothesis_ids_json=excluded.hypothesis_ids_json,
                last_seen_at=excluded.last_seen_at""",
            (
                key, paper.title, json.dumps(list(paper.authors)), paper.year, paper.venue, paper.citation_count,
                paper.abstract, paper.url, json.dumps(paper.external_ids), json.dumps(merged_sources),
                json.dumps(
                    {"citation_tier": evidence.citation_tier, "has_empirical_keywords": evidence.has_empirical_keywords,
                     "matched_strategies": list(evidence.matched_strategies), "overall_score": evidence.overall_score}
                ),
                json.dumps(merged_hyp_ids), first_seen_at, timestamp,
            ),
        )
    return key


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["authors"] = json.loads(d.pop("authors_json") or "[]")
    d["external_ids"] = json.loads(d.pop("external_ids_json") or "{}")
    d["sources"] = json.loads(d.pop("sources_json") or "[]")
    d["evidence_score"] = json.loads(d.pop("evidence_score_json") or "null")
    d["hypothesis_ids"] = json.loads(d.pop("hypothesis_ids_json") or "[]")
    return d


def get_paper(db_path: str | Path, key: str) -> dict[str, Any] | None:
    if not Path(db_path).exists():
        return None
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM papers WHERE paper_key = ?", (key,)).fetchone()
    return _row_to_dict(row) if row is not None else None


def query_papers(db_path: str | Path, min_overall_score: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Most-recently-seen first - never raises on a missing/empty
    database (returns `[]`). `min_overall_score` filters in Python
    (the score lives inside a JSON blob, not a queryable column) since
    this table is never expected to grow large enough for that to
    matter."""
    if not Path(db_path).exists():
        return []
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM papers ORDER BY last_seen_at DESC").fetchall()

    results = [_row_to_dict(r) for r in rows]
    if min_overall_score is not None:
        results = [r for r in results if (r["evidence_score"] or {}).get("overall_score", 0.0) >= min_overall_score]
    return results[:limit]
