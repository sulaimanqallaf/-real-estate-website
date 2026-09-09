"""Time-aware train/validation/test splitting and walk-forward folds
(Phase 6 Parts E/F).

**Policy, documented per Part E: never a random shuffle.** Every split in
this module sorts by `timestamp` GLOBALLY across the whole dataset (not
per-ticker) before slicing, so a multi-ticker dataset's split boundaries fall
at the same point in calendar time for every ticker. This is deliberate: if
splits were instead made per-ticker independently, a later NVDA row could
land in "train" while an earlier AAPL row (from the same calendar week, under
the same market regime) landed in "test" - leaking regime-correlated
information across the boundary even though no single ticker's own rows were
shuffled. Sorting globally first avoids that: the train/validation/test
boundary is one shared calendar cutoff every ticker's rows are sliced
against consistently.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ChronoSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    train_end: pd.Timestamp | None
    validation_end: pd.Timestamp | None
    test_end: pd.Timestamp | None


def chronological_split(
    df: pd.DataFrame,
    train_pct: float = 0.6,
    validation_pct: float = 0.2,
    test_pct: float = 0.2,
    timestamp_col: str = "timestamp",
) -> ChronoSplit:
    """Global chronological train -> validation -> test split, in that
    order, with configurable percentages (defaulting to the phase brief's
    60/20/20). Never shuffles - sorts by `timestamp_col` first."""
    total = train_pct + validation_pct + test_pct
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"train_pct + validation_pct + test_pct must sum to 1.0, got {total}")

    sorted_df = df.sort_values(timestamp_col).reset_index(drop=True)
    n = len(sorted_df)
    train_end_idx = int(n * train_pct)
    val_end_idx = train_end_idx + int(n * validation_pct)

    train = sorted_df.iloc[:train_end_idx]
    validation = sorted_df.iloc[train_end_idx:val_end_idx]
    test = sorted_df.iloc[val_end_idx:]

    def _last_ts(part: pd.DataFrame) -> pd.Timestamp | None:
        return part[timestamp_col].iloc[-1] if len(part) else None

    return ChronoSplit(
        train=train, validation=validation, test=test,
        train_end=_last_ts(train), validation_end=_last_ts(validation), test_end=_last_ts(test),
    )


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train: pd.DataFrame
    validation: pd.DataFrame
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


def walk_forward_folds(
    df: pd.DataFrame,
    min_train_rows: int,
    validation_rows: int,
    step_rows: int | None = None,
    expanding: bool = True,
    timestamp_col: str = "timestamp",
) -> list[WalkForwardFold]:
    """Rolling (`expanding=False`) or expanding (`expanding=True`, default)
    walk-forward folds over the GLOBAL chronological order - see module
    docstring. Each fold's train window ends exactly where its validation
    window begins (no gap, no overlap): fold `k`'s validation rows are never
    part of fold `k`'s (or any earlier fold's) training rows. `step_rows`
    (default `validation_rows`) controls how far the window advances between
    folds."""
    step = step_rows or validation_rows
    sorted_df = df.sort_values(timestamp_col).reset_index(drop=True)
    n = len(sorted_df)

    folds: list[WalkForwardFold] = []
    train_start = 0
    train_end = min_train_rows
    fold_index = 0

    while train_end + validation_rows <= n:
        val_start = train_end
        val_end = train_end + validation_rows

        train_slice = sorted_df.iloc[train_start:train_end]
        val_slice = sorted_df.iloc[val_start:val_end]

        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train=train_slice,
                validation=val_slice,
                train_start=train_slice[timestamp_col].iloc[0],
                train_end=train_slice[timestamp_col].iloc[-1],
                validation_start=val_slice[timestamp_col].iloc[0],
                validation_end=val_slice[timestamp_col].iloc[-1],
            )
        )

        fold_index += 1
        train_end += step
        if not expanding:
            train_start += step

    return folds
