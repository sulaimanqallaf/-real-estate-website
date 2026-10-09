"""HTTP endpoint tests - every `readonly.*` call is monkeypatched so
these tests never touch the real repo's journals/logs (and never incur
any real file I/O cost tied to the user's actual bot data), and never
make a network/LLM call either way since `readonly` itself can't."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app import main, readonly


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(readonly, "health_report", lambda: {"status": {"last_run_ok": True}})
    monkeypatch.setattr(readonly, "run_status", lambda: {"last_run_ok": True})
    monkeypatch.setattr(readonly, "circuit_breaker_status", lambda: {"halted": False, "reason": None})
    monkeypatch.setattr(readonly, "recent_decisions", lambda limit=25: [{"ticker": "AMD"}][:limit])
    monkeypatch.setattr(readonly, "tradingagents_outputs", lambda limit=10: [])
    monkeypatch.setattr(readonly, "spend_summary", lambda: None)
    monkeypatch.setattr(readonly, "paper_pnl_summary", lambda: None)
    return TestClient(main.app)


def test_health_endpoint_returns_the_readonly_report(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": {"last_run_ok": True}}


def test_run_status_endpoint(client):
    response = client.get("/api/run-status")
    assert response.status_code == 200
    assert response.json()["last_run_ok"] is True


def test_decisions_endpoint_respects_limit_param(client, monkeypatch):
    monkeypatch.setattr(readonly, "recent_decisions", lambda limit=25: [{"i": i} for i in range(limit)])
    response = client.get("/api/decisions?limit=3")
    assert response.json() == [{"i": 0}, {"i": 1}, {"i": 2}]


def test_paper_pnl_endpoint_returns_null_when_no_real_data_exists(client):
    response = client.get("/api/paper-pnl")
    assert response.json() is None


def test_spend_endpoint_returns_null_when_tradingagents_disabled(client):
    response = client.get("/api/spend")
    assert response.json() is None


def test_modes_endpoint_reports_demo_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("DASHBOARD_ALLOW_DEMO", raising=False)
    response = client.get("/api/modes")
    body = response.json()
    assert body["live"] is True
    assert body["replay"] is True
    assert body["demo"] is False


def test_modes_endpoint_reports_demo_enabled_when_env_var_set(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_ALLOW_DEMO", "1")
    response = client.get("/api/modes")
    assert response.json()["demo"] is True
