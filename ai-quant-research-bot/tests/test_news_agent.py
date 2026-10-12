"""src/agents/news_agent.py - the News Agent (Sprint 4, Priority 3).
SEC/FRED network calls are faked out at the provider-function level
(each provider has its own dedicated HTTP-contract tests) - these
tests focus on this module's own job: dedup against persistent "seen"
state, per-ticker/per-series isolation, budget enforcement, checkpoint
resumability, and briefing formatting."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date

from src.agents import news_agent as na
from src.agents import registry
from src.data_providers import base, macro_provider, sec_provider

LOGGER = logging.getLogger("test")


def _config(tmp_path, **news_overrides):
    news_cfg = {"watchlist": ["AAPL"], "fred_series": ["FEDFUNDS"], "daily_api_limit_per_source": 50}
    news_cfg.update(news_overrides)
    return {"data": {"journal_dir": str(tmp_path / "journal")}, "research": {"news_agent": news_cfg}}


def _patch_sec(monkeypatch, filings_by_ticker=None, cik="0000320193"):
    filings_by_ticker = filings_by_ticker or {}
    monkeypatch.setattr(sec_provider, "resolve_cik_for_ticker", lambda ticker, config, logger, **kwargs: cik)

    def _fetch(cik_arg, form_types=("8-K", "4")):
        return base.ok(sec_provider.SOURCE_13F, filings_by_ticker.get(cik_arg, []))

    monkeypatch.setattr(sec_provider, "fetch_recent_filings", _fetch)


def _patch_fred(monkeypatch, observations_by_series=None):
    observations_by_series = observations_by_series or {}

    def _fetch(series_id, **kwargs):
        obs = observations_by_series.get(series_id)
        if obs is None:
            return base.unavailable(macro_provider.SOURCE_FRED, "no data")
        return base.ok(macro_provider.SOURCE_FRED, obs)

    monkeypatch.setattr(macro_provider, "fetch_series_latest", _fetch)


def _observation(series_id="FEDFUNDS", value=5.25, obs_date=date(2026, 1, 1), release_date=date(2026, 1, 15)):
    return macro_provider.MacroObservation(series_id=series_id, value=value, observation_date=obs_date, release_date=release_date)


# --- run_once: SEC filings ------------------------------------------------------------------


def test_run_once_reports_a_new_filing(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_sec(monkeypatch, filings_by_ticker={"0000320193": [{"form": "8-K", "filing_date": "2026-01-01", "accession_number": "0001", "primary_document": "doc.htm"}]})
    _patch_fred(monkeypatch)

    summary = na.run_once(config, LOGGER)

    assert len(summary["new_filings"]) == 1
    assert summary["new_filings"][0]["ticker"] == "AAPL"
    assert summary["errors"] == []


def test_run_once_never_repeats_a_filing_already_seen(tmp_path, monkeypatch):
    config = _config(tmp_path)
    filing = {"form": "8-K", "filing_date": "2026-01-01", "accession_number": "0001", "primary_document": "doc.htm"}
    _patch_sec(monkeypatch, filings_by_ticker={"0000320193": [filing]})
    _patch_fred(monkeypatch)

    first = na.run_once(config, LOGGER)
    second = na.run_once(config, LOGGER)

    assert len(first["new_filings"]) == 1
    assert len(second["new_filings"]) == 0


def test_run_once_reports_a_second_genuinely_new_filing_on_a_later_run(tmp_path, monkeypatch):
    config = _config(tmp_path)
    filing1 = {"form": "8-K", "filing_date": "2026-01-01", "accession_number": "0001", "primary_document": "doc.htm"}
    filing2 = {"form": "8-K", "filing_date": "2026-02-01", "accession_number": "0002", "primary_document": "doc2.htm"}

    _patch_sec(monkeypatch, filings_by_ticker={"0000320193": [filing1]})
    _patch_fred(monkeypatch)
    na.run_once(config, LOGGER)

    _patch_sec(monkeypatch, filings_by_ticker={"0000320193": [filing1, filing2]})
    second = na.run_once(config, LOGGER)

    assert len(second["new_filings"]) == 1
    assert second["new_filings"][0]["accession_number"] == "0002"


def test_run_once_handles_an_unresolvable_ticker_gracefully(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(sec_provider, "resolve_cik_for_ticker", lambda *a, **k: None)
    _patch_fred(monkeypatch)

    summary = na.run_once(config, LOGGER)
    assert summary["new_filings"] == []
    assert summary["errors"] == []


def test_run_once_isolates_one_tickers_crash_from_the_rest(tmp_path, monkeypatch):
    config = _config(tmp_path, watchlist=["AAPL", "MSFT"])

    def _resolve(ticker, cfg, logger, **kwargs):
        return "0000320193" if ticker == "AAPL" else "0000789019"

    def _raise_for_aapl(cik, form_types=("8-K", "4")):
        if cik == "0000320193":
            raise RuntimeError("SEC is down")
        return base.ok(sec_provider.SOURCE_13F, [{"form": "8-K", "filing_date": "2026-01-01", "accession_number": "0099", "primary_document": "d.htm"}])

    monkeypatch.setattr(sec_provider, "resolve_cik_for_ticker", _resolve)
    monkeypatch.setattr(sec_provider, "fetch_recent_filings", _raise_for_aapl)
    _patch_fred(monkeypatch)

    summary = na.run_once(config, LOGGER)
    assert len(summary["new_filings"]) == 1
    assert summary["new_filings"][0]["ticker"] == "MSFT"
    assert summary["errors"] == []


def test_run_once_respects_the_sec_filings_daily_budget(tmp_path, monkeypatch):
    config = _config(tmp_path, daily_api_limit_per_source=0)

    def _should_not_be_called(*a, **k):
        raise AssertionError("fetch_recent_filings must not be called - budget exhausted")

    monkeypatch.setattr(sec_provider, "resolve_cik_for_ticker", lambda *a, **k: "0000320193")
    monkeypatch.setattr(sec_provider, "fetch_recent_filings", _should_not_be_called)
    _patch_fred(monkeypatch)

    summary = na.run_once(config, LOGGER)
    assert summary["new_filings"] == []


# --- run_once: economic events -----------------------------------------------------------


def test_run_once_reports_a_new_economic_event(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_sec(monkeypatch)
    _patch_fred(monkeypatch, observations_by_series={"FEDFUNDS": [_observation()]})

    summary = na.run_once(config, LOGGER)
    assert len(summary["new_economic_events"]) == 1
    assert summary["new_economic_events"][0]["series_id"] == "FEDFUNDS"


def test_run_once_never_repeats_an_economic_observation_already_seen(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_sec(monkeypatch)
    _patch_fred(monkeypatch, observations_by_series={"FEDFUNDS": [_observation()]})

    first = na.run_once(config, LOGGER)
    second = na.run_once(config, LOGGER)
    assert len(first["new_economic_events"]) == 1
    assert len(second["new_economic_events"]) == 0


def test_run_once_handles_fred_series_with_no_data(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_sec(monkeypatch)
    _patch_fred(monkeypatch, observations_by_series={})

    summary = na.run_once(config, LOGGER)
    assert summary["new_economic_events"] == []
    assert summary["errors"] == []


def test_run_once_isolates_one_series_crash_from_the_rest(tmp_path, monkeypatch):
    config = _config(tmp_path, fred_series=["FEDFUNDS", "CPIAUCSL"])
    _patch_sec(monkeypatch)

    def _fetch(series_id, **kwargs):
        if series_id == "FEDFUNDS":
            raise RuntimeError("FRED is down")
        return base.ok(macro_provider.SOURCE_FRED, [_observation(series_id="CPIAUCSL")])

    monkeypatch.setattr(macro_provider, "fetch_series_latest", _fetch)

    summary = na.run_once(config, LOGGER)
    assert len(summary["new_economic_events"]) == 1
    assert summary["new_economic_events"][0]["series_id"] == "CPIAUCSL"
    assert summary["errors"] == []


# --- registry / heartbeat / checkpoint -----------------------------------------------------


def test_run_once_records_success_in_the_registry(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _patch_sec(monkeypatch)
    _patch_fred(monkeypatch)

    na.run_once(config, LOGGER)
    status = registry.read_status(config, na.AGENT_NAME)
    assert status["status"] == registry.STATUS_SUCCESS


def test_run_once_records_failure_in_the_registry_on_a_crash(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def _raise(*a, **k):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(na, "_load_seen_state", _raise)

    summary = na.run_once(config, LOGGER)
    assert summary["errors"]
    status = registry.read_status(config, na.AGENT_NAME)
    assert status["status"] == registry.STATUS_FAILED


def test_run_once_clears_checkpoint_after_success(tmp_path, monkeypatch):
    from src.agents import checkpoint

    config = _config(tmp_path, watchlist=["AAPL", "MSFT"])
    _patch_sec(monkeypatch)
    _patch_fred(monkeypatch)

    na.run_once(config, LOGGER)
    assert checkpoint.load_checkpoint(config, na.AGENT_NAME) is None


def test_run_once_resumes_from_a_checkpoint_skipping_already_checked_tickers(tmp_path, monkeypatch):
    from src.agents import checkpoint

    config = _config(tmp_path, watchlist=["AAPL", "MSFT"])
    checkpoint.save_checkpoint(config, na.AGENT_NAME, {"tickers_checked": ["AAPL"]})

    checked = []

    def _resolve(ticker, cfg, logger, **kwargs):
        checked.append(ticker)
        return "0000320193"

    monkeypatch.setattr(sec_provider, "resolve_cik_for_ticker", _resolve)
    monkeypatch.setattr(sec_provider, "fetch_recent_filings", lambda *a, **k: base.ok(sec_provider.SOURCE_13F, []))
    _patch_fred(monkeypatch)

    na.run_once(config, LOGGER)
    assert "AAPL" not in checked
    assert "MSFT" in checked


# --- format_briefing_text -----------------------------------------------------------------


def test_format_briefing_text_lists_filings_and_events():
    summary = {
        "new_filings": [{"ticker": "AAPL", "form": "8-K", "filing_date": "2026-01-01", "accession_number": "0001"}],
        "new_economic_events": [{"series_id": "FEDFUNDS", "value": 5.25, "observation_date": "2026-01-01", "available_at": "2026-01-15"}],
        "errors": [],
    }
    text = na.format_briefing_text(summary)
    assert "AAPL" in text and "8-K" in text
    assert "FEDFUNDS" in text
    assert "never places, suggests, or triggers a trade" in text


def test_format_briefing_text_reports_none_honestly():
    summary = {"new_filings": [], "new_economic_events": [], "errors": []}
    text = na.format_briefing_text(summary)
    assert "New SEC filings: none" in text
    assert "New economic data: none" in text


# --- main() CLI and Telegram wiring ---------------------------------------------------------


def test_main_skips_telegram_when_nothing_new(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.setattr(na, "run_once", lambda cfg, logger: {"tickers_checked": [], "new_filings": [], "new_economic_events": [], "errors": []})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    def _should_not_be_called(*a, **k):
        raise AssertionError("send_report must not be called when nothing new happened")

    monkeypatch.setattr("src.telegram_bot.send_report", _should_not_be_called)
    assert na.main() == 0


def test_main_sends_a_briefing_when_something_new_happened(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})
    monkeypatch.setattr(
        na, "run_once",
        lambda cfg, logger: {"tickers_checked": ["AAPL"], "new_filings": [{"ticker": "AAPL", "form": "8-K", "filing_date": "x", "accession_number": "1"}], "new_economic_events": [], "errors": []},
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    sent = {}
    monkeypatch.setattr("src.telegram_bot.send_report", lambda token, chat_id, text, max_chars, logger: sent.setdefault("text", text) or True)

    assert na.main() == 0
    assert "AAPL" in sent["text"]


def test_main_returns_nonzero_when_already_locked(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("src.utils.load_env", lambda: None)
    monkeypatch.setattr("src.utils.load_config", lambda path: config | {"logging": {"log_dir": str(tmp_path / "logs")}})

    from src.execution.process_lock import acquire_singleton_lock
    from src.utils import resolve_path

    lock_path = resolve_path(config["data"]["journal_dir"]) / "agents" / "news_agent.lock"
    held_handle = acquire_singleton_lock(lock_path, "test_holder")
    try:
        assert na.main() == 1
    finally:
        held_handle.close()
