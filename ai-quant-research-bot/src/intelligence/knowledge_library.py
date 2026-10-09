"""Offline RAG research knowledge library (AI Quant Trading Platform
sprint, deliverable E: "a research knowledge library using retrieval-
augmented generation for legally available documents. Cite sources and
isolate untrusted document instructions from executable commands.").

**Pure, offline, keyword retrieval - no embeddings, no LLM calls, no new
dependency.** Deliberately avoids both a per-call API cost and a new ML
dependency: this is a BM25-style ranker (`KnowledgeIndex._score()`)
implemented in plain Python, indexing whatever the user drops into
`data/knowledge/` (gitignored - see that directory's own note, and
`docs/platform/BLOCKERS.md` item 4: this repo ships the retrieval
ENGINE, never any actual copyrighted content). Good enough to prove the
capability and to retrieve relevant passages for a citation-backed
answer; swapping in a real embedding model later is a drop-in
replacement for `KnowledgeIndex`'s scoring alone, should you want it
(and decide the LLM/embedding cost is worth it) - nothing else in this
module's contract would need to change.

**Citations are mandatory, not optional.** Every `RetrievedChunk` this
module returns carries its exact source filename and chunk index - a
caller that discards that before showing a user a synthesized answer
is discarding the one thing that makes a RAG answer checkable, and
`format_citations_for_prompt()` keeps the citation marker attached to
each chunk's text for exactly that reason.

**Untrusted-content isolation, structurally enforced.** This module
only ever reads, tokenizes, scores, and returns plain text - it never
calls `eval`/`exec`, never shells out, and never imports anything LLM-
or network-facing (see `tests/test_intelligence_knowledge_library_
safety.py`'s grep/AST guardrail, mirroring the existing pattern for
`intelligence/tradingagents_adapter.py`). `format_citations_for_prompt()`
wraps every retrieved excerpt in an explicit, clearly-delimited
"untrusted reference material" block - a document that happens to
contain text shaped like an instruction (e.g. a book excerpt saying
"ignore your previous instructions") is still just a string inside that
block to any caller that honors the wrapping; this module cannot force
a downstream LLM call to honor it, but it never gives a caller the
excerpt any other way, and never itself treats retrieved text as
anything other than data.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import resolve_path

_TOKEN_RE = re.compile(r"[a-z0-9]+")

UNTRUSTED_BLOCK_OPEN = "<retrieved_document trust=\"untrusted\" source=\"{source}\" chunk=\"{chunk_index}\">"
UNTRUSTED_BLOCK_CLOSE = "</retrieved_document>"


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class DocumentChunk:
    source: str          # filename, relative to the documents directory
    chunk_index: int
    text: str


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: DocumentChunk
    score: float

    @property
    def citation(self) -> str:
        return f"{self.chunk.source}#chunk{self.chunk.chunk_index}"


def resolve_documents_dir(config: dict[str, Any]) -> Path:
    configured = config.get("intelligence", {}).get("knowledge_library", {}).get("documents_dir")
    if configured:
        return resolve_path(configured)
    return resolve_path("data/knowledge")


def chunk_document(text: str, source: str, max_chunk_chars: int = 1200) -> list[DocumentChunk]:
    """Splits on blank lines (paragraphs), then greedily merges
    consecutive paragraphs up to `max_chunk_chars` so a chunk is a
    coherent, citation-sized unit - never a single sentence, never an
    entire book. Deterministic: the same document always produces the
    same chunks, which is what makes citations stable across runs."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[DocumentChunk] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chunk_chars and current:
            chunks.append(DocumentChunk(source=source, chunk_index=len(chunks), text=current))
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(DocumentChunk(source=source, chunk_index=len(chunks), text=current))
    return chunks


def load_documents(documents_dir: Path, extensions: tuple[str, ...] = (".txt", ".md")) -> list[DocumentChunk]:
    """Reads every matching file directly under `documents_dir`
    (non-recursive - a deliberately simple, predictable scope for
    Phase 1) and chunks each one. Returns `[]` (never raises) if the
    directory doesn't exist yet - "no documents provided" is a normal,
    expected state, not an error."""
    if not documents_dir.exists():
        return []
    chunks: list[DocumentChunk] = []
    for path in sorted(documents_dir.iterdir()):
        if path.suffix.lower() not in extensions or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.extend(chunk_document(text, source=path.name))
    return chunks


class KnowledgeIndex:
    """An in-memory BM25 index over a fixed list of `DocumentChunk`s.
    Built once (`build_index()`), queried many times
    (`search()`) - cheap enough (pure Python + numpy over however many
    chunks a reasonable personal document collection produces) that
    there's no need for a persisted index file in Phase 1."""

    def __init__(self, chunks: list[DocumentChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._doc_tokens: list[list[str]] = [_tokenize(c.text) for c in chunks]
        self._doc_lengths = [len(tokens) for tokens in self._doc_tokens]
        self._avg_doc_length = (sum(self._doc_lengths) / len(self._doc_lengths)) if self._doc_lengths else 0.0
        self._doc_freq: dict[str, int] = {}
        for tokens in self._doc_tokens:
            for term in set(tokens):
                self._doc_freq[term] = self._doc_freq.get(term, 0) + 1
        self._n_docs = len(chunks)

    def _idf(self, term: str) -> float:
        df = self._doc_freq.get(term, 0)
        if df == 0 or self._n_docs == 0:
            return 0.0
        # Standard BM25 IDF with a +1 smoothing floor so a term present in
        # every single chunk still contributes a small positive weight
        # rather than going negative.
        return math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))

    def _score(self, doc_index: int, query_tokens: list[str]) -> float:
        tokens = self._doc_tokens[doc_index]
        if not tokens:
            return 0.0
        doc_length = self._doc_lengths[doc_index]
        term_counts: dict[str, int] = {}
        for term in tokens:
            term_counts[term] = term_counts.get(term, 0) + 1

        score = 0.0
        for term in query_tokens:
            if term not in term_counts:
                continue
            freq = term_counts[term]
            idf = self._idf(term)
            denom = freq + self.k1 * (1 - self.b + self.b * doc_length / (self._avg_doc_length or 1))
            score += idf * (freq * (self.k1 + 1)) / denom
        return score

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        query_tokens = _tokenize(query)
        if not query_tokens or not self.chunks:
            return []
        scored = [
            RetrievedChunk(chunk=self.chunks[i], score=self._score(i, query_tokens))
            for i in range(len(self.chunks))
        ]
        scored = [r for r in scored if r.score > 0]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]


def build_index(config: dict[str, Any]) -> KnowledgeIndex:
    """The one entry point a caller needs: loads whatever's in
    `data/knowledge/` (or the configured override) right now and
    builds a fresh index. No caching across calls in Phase 1 - rebuild
    is cheap and this avoids a stale-index bug class entirely."""
    documents_dir = resolve_documents_dir(config)
    chunks = load_documents(documents_dir)
    return KnowledgeIndex(chunks)


def format_citations_for_prompt(results: list[RetrievedChunk]) -> str:
    """Formats retrieved chunks as an explicitly untrusted, clearly-
    delimited reference block, each one tagged with its exact citation
    - the shape a caller should hand to an LLM prompt's "reference
    material" field, NEVER concatenated into the instruction/system
    portion of a prompt. Returns an empty string for no results (never
    a fabricated "no relevant documents found" claim dressed up as
    retrieved content)."""
    if not results:
        return ""
    blocks = []
    for result in results:
        open_tag = UNTRUSTED_BLOCK_OPEN.format(source=result.chunk.source, chunk_index=result.chunk.chunk_index)
        blocks.append(f"{open_tag}\n{result.chunk.text}\n{UNTRUSTED_BLOCK_CLOSE}")
    return "\n\n".join(blocks)


def search(config: dict[str, Any], query: str, top_k: int = 5) -> list[RetrievedChunk]:
    """Convenience one-shot: build a fresh index from config and search
    it. For repeated queries in the same process, build `KnowledgeIndex`
    once via `build_index()` and call `.search()` directly instead."""
    return build_index(config).search(query, top_k=top_k)
