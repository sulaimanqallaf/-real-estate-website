"""New command-center readonly functions (AI Quant Trading Platform
sprint, deliverable G): positions/orders, risk/drawdown, backtest
performance, learning/challenger experiments, market scanner. Each
test writes the REAL file format the corresponding main-bot module
would write, read-only, never importing/constructing any writer class."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import readonly


def _config(tmp_path, **overrides):
    config = {
        "data": {"journal_dir": str(tmp_path / "journal"), "reports_dir": str(tmp_path / "reports")},
        "execution": {"journal_path": str(tmp_path / "journal" / "executions.jsonl")},
        "ml": {"registry_dir": str(tmp_path / "models")},
        "tickers": ["AMD"],
        "intelligence": {"tradingagents": {"enabled": False}},
    }
    config.update(overrides)
    return config


# --- positions_and_orders ----------------------------------------------------


def test_positions_and_orders_is_empty_when_no_journal_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.positions_and_orders()
    assert result == {"rows": [], "total_count": 0}


def test_positions_and_orders_reads_real_jsonl_rows(tmp_path, monkeypatch):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(parents=True)
    journal_path = journal_dir / "executions.jsonl"
    rows = [{"event": "state", "type": "submitting", "ticker": "AMD"}, {"event": "state", "type": "fill", "ticker": "AMD"}]
    journal_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.positions_and_orders()
    assert result["total_count"] == 2
    assert result["rows"][-1]["type"] == "fill"


def test_positions_and_orders_skips_a_malformed_line_without_raising(tmp_path, monkeypatch):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(parents=True)
    journal_path = journal_dir / "executions.jsonl"
    journal_path.write_text('{"event": "state", "type": "fill"}\nNOT VALID JSON\n', encoding="utf-8")

    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.positions_and_orders()
    assert result["total_count"] == 1


def test_positions_and_orders_respects_limit(tmp_path, monkeypatch):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(parents=True)
    journal_path = journal_dir / "executions.jsonl"
    rows = [{"i": i} for i in range(10)]
    journal_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.positions_and_orders(limit=3)
    assert len(result["rows"]) == 3
    assert result["total_count"] == 10  # total is real, even though only the tail is returned


# --- risk_status --------------------------------------------------------------


def test_risk_status_reports_breaker_and_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path, execution={
        "journal_path": str(tmp_path / "journal" / "executions.jsonl"),
        "halt_state_file": str(tmp_path / "halt.json"),
        "risk": {"max_daily_loss_pct": 0.02},
    }))
    result = readonly.risk_status()
    assert result["breaker"]["halted"] is False
    assert "max_daily_loss_pct" in result["limits"]


# --- backtest_performance -----------------------------------------------------


def test_backtest_performance_is_none_when_no_report_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    assert readonly.backtest_performance() is None


def test_backtest_performance_reads_the_most_recent_real_csv(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "backtest_2026-10-01.csv").write_text("strategy,total_return_pct\nTrend Following,5.2\n", encoding="utf-8")
    (reports_dir / "backtest_2026-10-08.csv").write_text("strategy,total_return_pct\nTrend Following,7.1\n", encoding="utf-8")

    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.backtest_performance()
    assert result["report_date"] == "2026-10-08"
    assert result["rows"][0]["total_return_pct"] == 7.1


# --- learning_experiments ------------------------------------------------------


def test_learning_experiments_is_empty_when_no_registry_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.learning_experiments()
    assert result == {"models": [], "events": []}


def test_learning_experiments_reads_real_model_events(tmp_path, monkeypatch):
    from src.ml import model_events

    config = _config(tmp_path)
    monkeypatch.setattr(readonly, "get_config", lambda: config)
    model_events.record_event(config, event_type="promoted", model_id="m1", model_type="logistic_regression")

    result = readonly.learning_experiments()
    assert len(result["events"]) == 1
    assert result["events"][0]["event"] == "promoted"


# --- market_scanner ------------------------------------------------------------


def test_market_scanner_returns_none_when_nothing_exists_and_universe_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path, tickers=[]))
    assert readonly.market_scanner() is None


def test_market_scanner_reads_the_most_recent_real_report(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "report_2026-10-07.json").write_text(json.dumps({"report_date": "2026-10-07", "tickers": [{"symbol": "AMD", "score": 10}]}), encoding="utf-8")
    (reports_dir / "report_2026-10-08.json").write_text(json.dumps({"report_date": "2026-10-08", "tickers": [{"symbol": "AMD", "score": 85}]}), encoding="utf-8")

    monkeypatch.setattr(readonly, "get_config", lambda: _config(tmp_path))
    result = readonly.market_scanner()
    assert result["report_date"] == "2026-10-08"
    assert result["tickers"][0]["score"] == 85
    assert result["universe_size"] == 1  # ["AMD"] from config["tickers"], universe.enabled defaults false
