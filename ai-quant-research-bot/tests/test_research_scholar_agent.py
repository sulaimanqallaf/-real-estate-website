"""src/agents/research_scholar.py - the Research Scholar Agent
(Sprint 4, Priority 2). `run_once()` is tested end-to-end: the three
providers are faked out (their own HTTP contracts are tested in their
own dedicated test files), but dedup/scoring/hypothesis-generation/
sandbox-backtesting/ledger-recording/paper-memory-recording all run
for REAL against a deterministic synthetic price history - same
pattern as tests/test_walk_forward.py and
tests/test_strategy_validation_report.py."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import data_collector
from src.agents import budget, registry, research_scholar as rs
from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.data_providers import arxiv_provider, base, openalex_provider, semantic_scholar_provider
from src.research.papers import Paper
from src.utils import load_config

LOGGER = logging.getLogger("test")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _config(tmp_path, **research_overrides):
    """Built on the REAL config/settings.yaml (same pattern as
    test_walk_forward.py/test_strategy_validation_report.py) rather
    than a hand-rolled dict, so every key indicators.py/risk_manager.py
    actually needs is present - a hand-rolled minimal config is exactly
    how a real KeyError like `sma_windows` gets missed."""
    config = load_config(CONFIG_PATH)
    scholar_cfg = {
        "queries": ["momentum anomaly"],
        "symbols": ["SYN1"],
        "min_evidence_score_for_hypothesis": 0.5,
        "daily_api_limit_per_source": 20,
    }
    scholar_cfg.update(research_overrides)
    config["data"]["journal_dir"] = str(tmp_path / "journal")
    config["research"]["scholar"] = scholar_cfg
    config["research"]["hypothesis_db_path"] = str(tmp_path / "research" / "hyp.db")
    config["research"]["paper_memory_db_path"] = str(tmp_path / "research" / "mem.db")
    config["backtest"]["benchmarks"] = []
    return config


_HIGH_SCORE_PAPER = Paper(
    title="Momentum Breakout Effects in Equities", authors=("A. Author",), year=2020, venue="JF", citation_count=200,
    abstract="An empirical backtest of momentum breakout, out-of-sample.", url="https://example.com/1",
    external_ids={"doi": "10.1/momentum"}, source="openalex",
)
_LOW_SCORE_PAPER = Paper(
    title="A Theoretical Discussion of Utility", authors=(), year=2000, venue=None, citation_count=None,
    abstract="Pure theory, no empirical content.", url=None, external_ids={}, source="arxiv",
)


def _fake_fetch(papers):
    return lambda query, config, logger, **kwargs: base.ok("fake", papers)


def _patch_all_providers(monkeypatch, openalex_papers=(), arxiv_papers=(), semantic_papers=()):
    monkeypatch.setattr(openalex_provider, "fetch_papers", _fake_fetch(list(openalex_papers)))
    monkeypatch.setattr(arxiv_provider, "fetch_papers", _fake_fetch(list(arxiv_papers)))
    monkeypatch.setattr(semantic_scholar_provider, "fetch_papers", _fake_fetch(list(semantic_papers)))


def _patch_price_history(monkeypatch, config):
    fixture = generate_synthetic_ohlcv(seed=1, periods=550)
    monkeypatch.setattr(data_collector, "fetch_all_price_history", lambda symbols, cfg, logger: {s: fixture for s in symbols})


# --- run_once: end-to-end wiring ------------------------------------------------------------


def test_run_once_generates_and_tests_a_hypothesis_for_a_high_evidence_paper(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_all_providers(monkeypatch, openalex_papers=[_HIGH_SCORE_PAPER])
    _patch_price_history(monkeypatch, config)

    summary = rs.run_once(config, LOGGER)

    assert summary["errors"] == []
    assert summary["papers_seen"] == 1
    assert summary["papers_deduped"] == 1
    assert summary["hypotheses_generated"] >= 1

    status = registry.read_status(config, rs.AGENT_NAME)
    assert status["status"] == registry.STATUS_SUCCESS


def test_run_once_skips_hypothesis_generation_for_a_low_evidence_paper(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_all_providers(monkeypatch, arxiv_papers=[_LOW_SCORE_PAPER])
    _patch_price_history(monkeypatch, config)

    summary = rs.run_once(config, LOGGER)

    assert summary["hypotheses_generated"] == 0
    from src.research import paper_memory

    rows = paper_memory.query_papers(config["research"]["paper_memory_db_path"])
    assert len(rows) == 1  # still recorded in permanent memory, just not hypothesis-tested
    assert rows[0]["hypothesis_ids"] == []


def test_run_once_dedupes_the_same_paper_seen_from_two_sources(tmp_path, monkeypatch):
    config = _config(tmp_path)
    from_semantic = Paper(
        title=_HIGH_SCORE_PAPER.title, authors=_HIGH_SCORE_PAPER.authors, year=2020, venue="JF", citation_count=200,
        abstract=_HIGH_SCORE_PAPER.abstract, url="https://semanticscholar.org/x",
        external_ids={"doi": "10.1/momentum"}, source="semantic_scholar",
    )
    _patch_all_providers(monkeypatch, openalex_papers=[_HIGH_SCORE_PAPER], semantic_papers=[from_semantic])
    _patch_price_history(monkeypatch, config)

    summary = rs.run_once(config, LOGGER)

    assert summary["papers_seen"] == 2
    assert summary["papers_deduped"] == 1


def test_run_once_records_a_failed_agent_cycle_when_a_crash_happens(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(data_collector, "fetch_all_price_history", _raise)
    _patch_all_providers(monkeypatch, openalex_papers=[_HIGH_SCORE_PAPER])

    summary = rs.run_once(config, LOGGER)

    assert summary["errors"]
    status = registry.read_status(config, rs.AGENT_NAME)
    assert status["status"] == registry.STATUS_FAILED


def test_run_once_isolates_one_provider_crashing(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("openalex is down")

    monkeypatch.setattr(openalex_provider, "fetch_papers", _raise)
    monkeypatch.setattr(arxiv_provider, "fetch_papers", _fake_fetch([_HIGH_SCORE_PAPER]))
    monkeypatch.setattr(semantic_scholar_provider, "fetch_papers", _fake_fetch([]))
    _patch_price_history(monkeypatch, config)

    summary = rs.run_once(config, LOGGER)

    assert summary["papers_seen"] == 1  # arxiv's paper still got through
    assert summary["errors"] == []  # provider-level failures are isolated, not surfaced as a run failure
    status = registry.read_status(config, rs.AGENT_NAME)
    assert status["status"] == registry.STATUS_SUCCESS  # isolated failure never flips the overall run to FAILED

    # Sprint 5: the isolated failure must still be VISIBLE, not silently
    # indistinguishable from a provider that simply found nothing.
    assert len(summary["provider_issues"]) == 1
    issue = summary["provider_issues"][0]
    assert issue["source"] == "openalex"
    assert issue["status"] == "exception"
    assert "openalex is down" in issue["detail"]


def test_run_once_records_a_provider_error_status_as_a_visible_issue(tmp_path, monkeypatch):
    """Distinct from an exception (above): a provider that returns a
    real STATUS_ERROR/STATUS_BLOCKED (never raises) must be equally
    visible, not silently collapsed to '0 results, all fine'."""
    config = _config(tmp_path)
    monkeypatch.setattr(openalex_provider, "fetch_papers", lambda *a, **k: base.provider_error("openalex", "simulated 404"))
    monkeypatch.setattr(arxiv_provider, "fetch_papers", lambda *a, **k: base.provider_blocked("arxiv", "simulated connection refused"))
    monkeypatch.setattr(semantic_scholar_provider, "fetch_papers", _fake_fetch([]))
    _patch_price_history(monkeypatch, config)

    summary = rs.run_once(config, LOGGER)

    assert summary["errors"] == []
    issues_by_source = {issue["source"]: issue for issue in summary["provider_issues"]}
    assert issues_by_source["openalex"]["status"] == base.STATUS_ERROR
    assert issues_by_source["arxiv"]["status"] == base.STATUS_BLOCKED


def test_run_once_does_not_record_an_issue_for_a_genuinely_empty_result(tmp_path, monkeypatch):
    """STATUS_UNAVAILABLE (not configured, or honestly no results) is
    not a failure - must never be recorded as a provider issue."""
    config = _config(tmp_path)
    _patch_all_providers(monkeypatch)  # every provider returns []
    _patch_price_history(monkeypatch, config)

    summary = rs.run_once(config, LOGGER)
    assert summary["provider_issues"] == []


def test_run_once_respects_the_daily_budget_per_source(tmp_path, monkeypatch):
    config = _config(tmp_path, daily_api_limit_per_source=0)

    def _should_not_be_called(*a, **k):
        raise AssertionError("fetch_papers must not be called - daily budget is already exhausted")

    monkeypatch.setattr(openalex_provider, "fetch_papers", _should_not_be_called)
    monkeypatch.setattr(arxiv_provider, "fetch_papers", _should_not_be_called)
    monkeypatch.setattr(semantic_scholar_provider, "fetch_papers", _should_not_be_called)

    summary = rs.run_once(config, LOGGER)
    assert summary["papers_seen"] == 0


def test_run_once_records_a_heartbeat(tmp_path, monkeypatch):
    from src.agents import heartbeat

    config = _config(tmp_path)
    _patch_all_providers(monkeypatch)
    _patch_price_history(monkeypatch, config)

    rs.run_once(config, LOGGER)
    assert heartbeat.heartbeat_status(config, rs.AGENT_NAME)["never_started"] is False


def test_run_once_clears_checkpoint_after_a_full_successful_run(tmp_path, monkeypatch):
    from src.agents import checkpoint

    config = _config(tmp_path, queries=["q1", "q2"])
    _patch_all_providers(monkeypatch)
    _patch_price_history(monkeypatch, config)

    rs.run_once(config, LOGGER)
    assert checkpoint.load_checkpoint(config, rs.AGENT_NAME) is None


def test_run_once_resumes_from_a_checkpoint_skipping_completed_queries(tmp_path, monkeypatch):
    from src.agents import checkpoint

    config = _config(tmp_path, queries=["q1", "q2"])
    checkpoint.save_checkpoint(config, rs.AGENT_NAME, {"completed_queries": ["q1"]})

    seen_queries = []

    def _tracking_fetch(papers):
        def _fetch(query, cfg, logger, **kwargs):
            seen_queries.append(query)
            return base.ok("fake", list(papers))
        return _fetch

    monkeypatch.setattr(openalex_provider, "fetch_papers", _tracking_fetch([]))
    monkeypatch.setattr(arxiv_provider, "fetch_papers", _tracking_fetch([]))
    monkeypatch.setattr(semantic_scholar_provider, "fetch_papers", _tracking_fetch([]))
    _patch_price_history(monkeypatch, config)

    rs.run_once(config, LOGGER)
    assert "q1" not in seen_queries
    assert "q2" in seen_queries


# --- main() CLI --------------------------------------------------------------------------


def test_main_returns_nonzero_when_already_locked(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})

    from src.execution.process_lock import acquire_singleton_lock
    from src.utils import resolve_path

    lock_path = resolve_path(config["data"]["journal_dir"]) / "agents" / "research_scholar.lock"
    held_handle = acquire_singleton_lock(lock_path, "test_holder")
    try:
        assert rs.main() == 1
    finally:
        held_handle.close()


def test_main_sends_a_telegram_summary_when_configured(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.setattr(rs, "run_once", lambda cfg, logger: {"queries_run": [], "papers_seen": 0, "papers_deduped": 0, "hypotheses_generated": 0, "hypotheses_tested": 0, "errors": [], "provider_issues": []})

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent = {}

    def _fake_send_report(token, chat_id, text, max_chars, logger):
        sent["text"] = text
        return True

    monkeypatch.setattr("src.telegram_bot.send_report", _fake_send_report)

    assert rs.main() == 0
    assert "Research Scholar Agent" in sent["text"]


def test_telegram_summary_surfaces_provider_issues(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.setattr(
        rs, "run_once",
        lambda cfg, logger: {
            "queries_run": ["q"], "papers_seen": 0, "papers_deduped": 0, "hypotheses_generated": 0, "hypotheses_tested": 0,
            "errors": [], "provider_issues": [{"source": "openalex", "query": "q", "status": "blocked", "detail": "connection refused"}],
        },
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent = {}
    monkeypatch.setattr("src.telegram_bot.send_report", lambda token, chat_id, text, max_chars, logger: sent.setdefault("text", text) or True)

    rs.main()
    assert "Provider issues" in sent["text"]
    assert "openalex" in sent["text"]


def test_main_skips_telegram_when_not_configured(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.setattr(rs, "run_once", lambda cfg, logger: {"queries_run": [], "papers_seen": 0, "papers_deduped": 0, "hypotheses_generated": 0, "hypotheses_tested": 0, "errors": [], "provider_issues": []})
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def _should_not_be_called(*a, **k):
        raise AssertionError("send_report must not be called without Telegram configured")

    monkeypatch.setattr("src.telegram_bot.send_report", _should_not_be_called)
    assert rs.main() == 0
