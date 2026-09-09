"""Tests for src/dataset_builder.py: point-in-time feature correctness,
forward-return labels using future data only as targets (never leaking into
feature construction), and Parquet output."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import big_money, dataset_builder as db
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def make_price_df(n: int = 260, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(rng.normal(0, 1.0, n))
    df = pd.DataFrame(
        {
            "open": close, "high": close + 1.0, "low": close - 1.0, "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


@pytest.fixture
def config():
    return load_config(CONFIG_PATH)


# --- forward return labels: the only place allowed to look forward ----------------


def test_forward_returns_computed_correctly():
    df = make_price_df(n=30)
    out = db.compute_forward_returns(df, i=0, horizons=(5,))
    expected = (df["close"].iloc[5] - df["close"].iloc[0]) / df["close"].iloc[0]
    assert out["forward_5d_return"] == pytest.approx(expected)


def test_forward_returns_none_when_horizon_not_yet_reached():
    df = make_price_df(n=10)
    out = db.compute_forward_returns(df, i=8, horizons=(5,))
    assert out["forward_5d_return"] is None  # index 13 doesn't exist


# --- point-in-time feature correctness ---------------------------------------------


def test_feature_row_identical_regardless_of_future_bars_present(config):
    df = make_price_df(n=260)
    i = 220
    row_full = db.build_feature_row("TEST", df, i, config)

    truncated = df.iloc[: i + 1]
    row_truncated = db.build_feature_row("TEST", truncated, i, config)

    for key in db.FEATURE_COLUMNS:
        a, b = row_full[key], row_truncated[key]
        if a is None and b is None:
            continue
        assert a == pytest.approx(b), f"feature {key} differs when future bars are present"


def test_feature_row_does_not_include_label_columns(config):
    df = make_price_df(n=260)
    row = db.build_feature_row("TEST", df, 220, config)
    for label_col in db.LABEL_COLUMNS:
        assert label_col not in row


def test_no_overlap_between_feature_and_label_columns():
    assert set(db.FEATURE_COLUMNS).isdisjoint(set(db.LABEL_COLUMNS))


def test_build_dataset_rows_columns_are_exactly_key_feature_label(config):
    df = make_price_df(n=260)
    rows = db.build_dataset_rows("TEST", df, config, min_history_bars=200)
    assert list(rows.columns) == db.KEY_COLUMNS + db.FEATURE_COLUMNS + db.LABEL_COLUMNS


def test_recent_rows_have_none_labels_but_populated_features(config):
    df = make_price_df(n=260)
    rows = db.build_dataset_rows("TEST", df, config, min_history_bars=200)
    last_row = rows.iloc[-1]
    assert pd.isna(last_row["forward_5d_return"])
    assert last_row["close"] is not None and not pd.isna(last_row["close"])


def test_older_rows_have_populated_labels(config):
    df = make_price_df(n=260)
    rows = db.build_dataset_rows("TEST", df, config, min_history_bars=200)
    older_row = rows.iloc[0]
    assert not pd.isna(older_row["forward_20d_return"])


def test_dataset_rows_start_at_min_history_bars(config):
    df = make_price_df(n=260)
    rows = db.build_dataset_rows("TEST", df, config, min_history_bars=200)
    assert rows.iloc[0]["timestamp"] == df.index[200]
    assert len(rows) == len(df) - 200


def test_big_money_score_flows_into_feature_row(config):
    df = make_price_df(n=260)
    facts = {"has_data": True, "new_positions": 2, "increased_positions": 0, "reduced_positions": 0, "exited_positions": 0}
    score = big_money.compute_big_money_score("TEST", institutional_facts=facts)
    rows = db.build_dataset_rows("TEST", df, config, big_money_score=score, min_history_bars=200)
    assert (rows["institutional_score"] == 1.0).all()
    assert (rows["big_money_composite_score"] == score.composite_score).all()


# --- Parquet output ------------------------------------------------------------------


def test_save_dataset_writes_and_round_trips_parquet(tmp_path, config):
    df = make_price_df(n=260)
    rows = db.build_dataset_rows("TEST", df, config, min_history_bars=200)
    config = dict(config)
    config["dataset"] = {"output_dir": str(tmp_path)}
    path = db.save_dataset(rows, "TEST", config, "2026-01-01")
    assert path.exists()

    read_back = pd.read_parquet(path)
    assert len(read_back) == len(rows)
    assert list(read_back.columns) == list(rows.columns)


def test_empty_dataset_has_correct_columns_and_still_saves(tmp_path, config):
    df = make_price_df(n=5)  # way too short for min_history_bars
    rows = db.build_dataset_rows("TEST", df, config, min_history_bars=200)
    assert rows.empty
    assert list(rows.columns) == db.KEY_COLUMNS + db.FEATURE_COLUMNS + db.LABEL_COLUMNS

    config = dict(config)
    config["dataset"] = {"output_dir": str(tmp_path)}
    path = db.save_dataset(rows, "TEST", config, "2026-01-01")
    assert path.exists()
