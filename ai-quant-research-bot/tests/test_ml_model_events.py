"""Append-only model lifecycle audit log (GitHub Issue #1 P0's "keep
append-only audit events," applied to the learning pipeline).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ml import model_events


def _config(tmp_path):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return {"data": {"journal_dir": str(journal_dir)}}


def test_read_events_returns_empty_list_for_a_missing_log(tmp_path):
    assert model_events.read_events(_config(tmp_path)) == []


def test_record_and_read_round_trip(tmp_path):
    config = _config(tmp_path)
    model_events.record_event(config, model_events.EVENT_PROMOTED, "model1", model_type="logistic_regression", reason="beat baselines")

    rows = model_events.read_events(config)
    assert len(rows) == 1
    assert rows[0]["event"] == "PROMOTED"
    assert rows[0]["model_id"] == "model1"
    assert rows[0]["reason"] == "beat baselines"
    assert "recorded_at" in rows[0]


def test_events_are_appended_not_overwritten(tmp_path):
    config = _config(tmp_path)
    model_events.record_event(config, model_events.EVENT_PROMOTED, "model1")
    model_events.record_event(config, model_events.EVENT_ROLLED_BACK, "model1", reason="losing record")

    rows = model_events.read_events(config)
    assert len(rows) == 2
    assert rows[0]["event"] == "PROMOTED"
    assert rows[1]["event"] == "ROLLED_BACK"


def test_read_events_filters_by_since(tmp_path):
    config = _config(tmp_path)
    model_events.record_event(config, model_events.EVENT_PROMOTED, "model1", recorded_at="2026-01-01T00:00:00+00:00")
    model_events.record_event(config, model_events.EVENT_PROMOTED, "model2", recorded_at="2026-06-01T00:00:00+00:00")

    recent = model_events.read_events(config, since="2026-03-01T00:00:00+00:00")
    assert len(recent) == 1
    assert recent[0]["model_id"] == "model2"


def test_resolve_log_path_is_colocated_with_journal_dir(tmp_path):
    config = _config(tmp_path)
    path = model_events.resolve_log_path(config)
    assert path.parent == Path(config["data"]["journal_dir"])
