"""Mode isolation (Phase 1 requirement: "no fake trades or profits in
LIVE mode", "DEMO mode mock events allowed only here"): DEMO must be
unreachable unless explicitly opted into via env var, and every event a
source emits must be stamped with that source's own mode - never
silently mislabeled."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from fastapi.testclient import TestClient

from app import main


def _collect(async_gen, limit=None):
    async def _run():
        collected = []
        async for item in async_gen:
            collected.append(item)
            if limit is not None and len(collected) >= limit:
                break
        return collected

    return asyncio.run(_run())


def test_demo_websocket_is_refused_when_not_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ALLOW_DEMO", raising=False)
    client = TestClient(main.app)
    with client.websocket_connect("/ws?mode=demo") as ws:
        message = ws.receive_json()
        assert "error" in message
        assert "disabled" in message["error"].lower()


def test_demo_websocket_streams_events_once_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ALLOW_DEMO", "1")
    client = TestClient(main.app)
    with client.websocket_connect("/ws?mode=demo&speed=1000") as ws:
        message = ws.receive_json()
        assert message["mode"] == "demo"


def test_unknown_mode_is_rejected():
    client = TestClient(main.app)
    with client.websocket_connect("/ws?mode=bogus") as ws:
        message = ws.receive_json()
        assert "error" in message


def test_every_demo_event_is_stamped_mode_demo():
    from app.sources.demo_source import demo_events

    events = _collect(demo_events(step_delay_seconds=0.0), limit=5)
    assert all(e.mode == "demo" for e in events)


def test_replay_never_emits_an_event_with_mode_other_than_replay(monkeypatch, tmp_path):
    from app import readonly
    from app.sources.replay_source import replay_events

    monkeypatch.setattr(readonly, "get_config", lambda: {"data": {"journal_dir": str(tmp_path)}})

    events = _collect(replay_events())
    assert events  # "no rows" still emits one info event
    assert all(e.mode == "replay" for e in events)
