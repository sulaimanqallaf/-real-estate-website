"""src/research/alpha_factors.py - a small Qlib Alpha158-family
technical factor library, independently written (no qlib/pyqlib
import). Tests run against a real seeded synthetic price series
(src.analytics.synthetic_fixtures), never a package we don't depend on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from src.analytics.synthetic_fixtures import generate_synthetic_ohlcv
from src.research import alpha_factors

DF = generate_synthetic_ohlcv(seed=11, periods=120)


# --- the mandatory no-lookahead regression test (same technique as --------------------
# --- tests/test_dataset_builder.py's) --------------------------------------------------


def test_factor_row_identical_regardless_of_future_bars_present():
    """Recomputing the full factor table on a TRUNCATED price history
    must produce identical values, up to the truncation point, to
    computing it on the full history - if a future bar could change an
    already-produced factor row, this fails."""
    truncate_at = 90
    full_table = alpha_factors.build_factor_table(DF)
    truncated_table = alpha_factors.build_factor_table(DF.iloc[:truncate_at])

    pd.testing.assert_frame_equal(
        full_table.iloc[:truncate_at].reset_index(drop=True),
        truncated_table.reset_index(drop=True),
        check_exact=False,
    )


def test_build_factor_table_never_mutates_the_input_dataframe():
    original = DF.copy()
    alpha_factors.build_factor_table(DF)
    pd.testing.assert_frame_equal(DF, original)


# --- K-bar shape factors -----------------------------------------------------------------


def test_kmid_matches_hand_computed_formula():
    row = pd.DataFrame({"open": [100.0], "high": [105.0], "low": [98.0], "close": [103.0], "volume": [1_000_000.0]})
    assert alpha_factors.kmid(row).iloc[0] == (103.0 - 100.0) / 100.0


def test_klen_is_never_negative():
    assert (alpha_factors.klen(DF) >= 0.0).all()


def test_kup_and_klow_are_never_negative():
    assert (alpha_factors.kup(DF) >= -1e-9).all()
    assert (alpha_factors.klow(DF) >= -1e-9).all()


def test_ksft_bounded_by_the_bars_own_range():
    # (2c - h - l) can range at most from (l - h) to (h - l) i.e. +-klen*open.
    ksft_abs = alpha_factors.ksft(DF).abs()
    klen_vals = alpha_factors.klen(DF)
    assert (ksft_abs <= klen_vals + 1e-9).all()


# --- rolling price factors ----------------------------------------------------------------


def test_roc_matches_hand_computed_formula():
    d = 10
    result = alpha_factors.roc(DF, d)
    expected = DF["close"].iloc[50] / DF["close"].iloc[50 - d] - 1.0
    assert result.iloc[50] == expected


def test_ma_ratio_is_nan_until_window_fills():
    d = 20
    result = alpha_factors.ma_ratio(DF, d)
    assert result.iloc[: d - 1].isna().all()
    assert result.iloc[d - 1 :].notna().all()


def test_rank_is_bounded_zero_to_one():
    result = alpha_factors.rank(DF, 20).dropna()
    assert (result >= 0.0).all() and (result <= 1.0).all()


def test_rank_is_one_when_todays_close_is_the_window_maximum():
    # Construct a strictly increasing close series - today's close is
    # always the window's max, so its percentile rank must be 1.0.
    dates = pd.bdate_range("2024-01-01", periods=40)
    rising = pd.DataFrame({
        "open": np.arange(100.0, 140.0), "high": np.arange(100.0, 140.0) + 1,
        "low": np.arange(100.0, 140.0) - 1, "close": np.arange(100.0, 140.0), "volume": 1_000_000.0,
    }, index=dates)
    result = alpha_factors.rank(rising, 10).dropna()
    assert (result == 1.0).all()


def test_qtlu_ratio_always_at_least_qtld_ratio():
    windows = (5, 10, 20)
    qtlu = {d: alpha_factors.qtl_ratio(DF, d, 0.8) for d in windows}
    qtld = {d: alpha_factors.qtl_ratio(DF, d, 0.2) for d in windows}
    for d in windows:
        valid = qtlu[d].notna() & qtld[d].notna()
        assert (qtlu[d][valid] >= qtld[d][valid] - 1e-9).all()


def test_rsqr_bounded_zero_to_one():
    result = alpha_factors.rsqr(DF, 20).dropna()
    assert (result >= -1e-9).all() and (result <= 1.0 + 1e-9).all()


def test_rsqr_is_one_for_a_perfectly_linear_window():
    dates = pd.bdate_range("2024-01-01", periods=30)
    perfectly_linear = pd.DataFrame({
        "open": np.arange(30.0), "high": np.arange(30.0), "low": np.arange(30.0),
        "close": 100.0 + 2.0 * np.arange(30.0), "volume": 1_000_000.0,
    }, index=dates)
    result = alpha_factors.rsqr(perfectly_linear, 10).dropna()
    assert (result > 0.999).all()


def test_beta_is_positive_for_a_strictly_rising_window():
    dates = pd.bdate_range("2024-01-01", periods=30)
    rising = pd.DataFrame({
        "open": np.arange(30.0), "high": np.arange(30.0), "low": np.arange(30.0),
        "close": 100.0 + np.arange(30.0), "volume": 1_000_000.0,
    }, index=dates)
    result = alpha_factors.beta(rising, 10).dropna()
    assert (result > 0.0).all()


# --- up/down-day counting -----------------------------------------------------------------


def test_cntp_cntn_cntd_relationship_holds():
    d = 20
    p, n, net = alpha_factors.cntp(DF, d), alpha_factors.cntn(DF, d), alpha_factors.cntd(DF, d)
    valid = p.notna() & n.notna() & net.notna()
    assert np.allclose(net[valid], (p - n)[valid])
    assert ((p[valid] + n[valid]) <= 1.0 + 1e-9).all()  # flat (unchanged-close) days count toward neither


# --- build_factor_table shape --------------------------------------------------------------


def test_build_factor_table_has_expected_columns_for_each_window():
    windows = (5, 10)
    table = alpha_factors.build_factor_table(DF, windows=windows)
    for kbar_col in ("kmid", "klen", "kup", "klow", "ksft"):
        assert kbar_col in table.columns
    for rolling_col in ("roc", "ma_ratio", "std_ratio", "beta", "rsqr", "rank", "vma_ratio", "corr", "qtlu", "qtld"):
        for d in windows:
            assert f"{rolling_col}_{d}" in table.columns


def test_build_factor_table_index_matches_input():
    table = alpha_factors.build_factor_table(DF)
    pd.testing.assert_index_equal(table.index, DF.index)
