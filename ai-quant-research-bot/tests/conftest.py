"""Shared pytest fixtures for the Phase 6 ML test suite. Session-scoped
dataset construction avoids rebuilding the same synthetic point-in-time
dataset (indicator/SMC recomputation per row is not free) dozens of times
across test files that don't need a fresh one.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import dataset_builder as db
from src.utils import load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


@pytest.fixture(scope="session")
def base_config():
    return load_config(CONFIG_PATH)


def build_synthetic_price_df(n: int = 500, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    price = 100 + np.cumsum(rng.normal(0, 1.0, n))
    df = pd.DataFrame(
        {
            "open": price, "high": price + 1, "low": price - 1, "close": price,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def build_synthetic_dataset(config, ticker: str = "TEST", n: int = 500, seed: int = 5, min_history_bars: int = 200) -> pd.DataFrame:
    price_df = build_synthetic_price_df(n, seed)
    return db.build_dataset_rows(ticker, price_df, config, min_history_bars=min_history_bars)


def build_separable_dataset(n: int = 1500, seed: int = 1, momentum_scale: float = 5.0) -> pd.DataFrame:
    """A dataset with a REAL, learnable signal (Scenario A): forward_5d_return
    is a noisy monotonic function of momentum_20d. Used across trainer/
    validator/predictor tests that need models to actually learn something,
    not just run without crashing."""
    rng = np.random.default_rng(seed)
    n_rows = n
    momentum = rng.normal(0, momentum_scale, n_rows)
    noise = rng.normal(0, 0.5, n_rows)
    forward_5d = 0.02 * np.tanh(momentum / momentum_scale) + noise * 0.01
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2022-01-01", periods=n_rows, freq="B"),
            "ticker": "TEST",
            "momentum_20d": momentum,
            "rsi_14": 50.0 + momentum,
            "close": 100.0,
            "score": np.clip(50 + momentum * 3, 0, 100),
            "forward_5d_return": forward_5d,
            "forward_10d_return": forward_5d * 1.5,
            "forward_20d_return": forward_5d * 2.0,
        }
    )
    from src.ml import features as ml_features

    for col in ml_features.FEATURE_WHITELIST:
        if col not in df.columns:
            df[col] = np.nan
    return df


def build_noise_dataset(n: int = 1500, seed: int = 2) -> pd.DataFrame:
    """A dataset with NO real signal (Scenario B): forward returns are pure
    noise, independent of every feature. Used to prove ML correctly fails
    to beat baselines rather than reporting fake confidence."""
    rng = np.random.default_rng(seed)
    n_rows = n
    momentum = rng.normal(0, 5.0, n_rows)
    forward_5d = rng.normal(0, 0.01, n_rows)  # independent of momentum
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2022-01-01", periods=n_rows, freq="B"),
            "ticker": "TEST",
            "momentum_20d": momentum,
            "rsi_14": 50.0 + rng.normal(0, 5, n_rows),
            "close": 100.0,
            "score": rng.uniform(0, 100, n_rows),
            "forward_5d_return": forward_5d,
            "forward_10d_return": forward_5d * 1.2,
            "forward_20d_return": forward_5d * 1.5,
        }
    )
    from src.ml import features as ml_features

    for col in ml_features.FEATURE_WHITELIST:
        if col not in df.columns:
            df[col] = np.nan
    return df


@pytest.fixture(scope="session")
def base_dataset(base_config):
    return build_synthetic_dataset(base_config)


@pytest.fixture(scope="session")
def separable_dataset():
    return build_separable_dataset()


@pytest.fixture(scope="session")
def noise_dataset():
    return build_noise_dataset()
