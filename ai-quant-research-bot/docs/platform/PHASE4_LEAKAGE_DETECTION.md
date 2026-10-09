# Phase 4: leakage-detection tests inspired by Freqtrade's techniques

Per the sprint instruction: *"review Freqtrade's leakage-detection techniques
and implement compatible tests."* No Freqtrade code is used or copied
(GPL-3.0 - see `docs/platform/OSS_INTEGRATION_AUDIT.md`); this is original
test code written against this project's own `src/ml/` pipeline, informed by
the general purged/embargoed walk-forward-CV technique that FreqAI's own
documentation (and the quantitative-finance literature more broadly, e.g.
López de Prado) discusses.

## What was already covered (not duplicated)

`tests/test_dataset_builder.py` and `tests/test_smc_features.py` already
have thorough no-lookahead coverage at the FEATURE level: recomputing every
feature/SMC construct with and without future bars present and asserting
identical values up to the truncation point. `src/ml/audit.py` already flags
a feature literally named like a label column, or suspiciously correlated
with it. None of that needed re-testing here.

## The real gap found: label-window leakage across a walk-forward fold boundary

This project's ML labels are `forward_{horizon}d_return` columns - a label
at row T is built from price data up to row T+horizon (by design; see
`src/ml/labels.py`). `src/ml/splits.py`'s `walk_forward_folds` put a fold's
validation window immediately after its train window with **no gap**. That
means the last `horizon` rows of train carry labels computed FROM price
action inside that very fold's validation window - the model is trained
using information that overlaps the period it is then "evaluated"
out-of-sample on.

Reproduced directly (`tests/test_ml_leakage_detection.py::
test_plain_walk_forward_without_embargo_leaks_label_windows_into_validation`):
on a synthetic dataset with an honest `forward_10d_return` label, every fold
produced by `walk_forward_folds(..., embargo_rows=0)` (the historical
default) had at least one train row whose label window reached into that
fold's own validation window.

## Fix (not just a test - the production code path too)

`src/ml/splits.py`'s `walk_forward_folds` gained an `embargo_rows` parameter
(default `0`, fully backward compatible - every existing call site and test
is unaffected) that drops the last `embargo_rows` rows of each fold's train
slice before returning it. `src/ml/validator.py`'s
`run_walk_forward_evaluation` now passes `embargo_rows=horizon` - the exact
value needed since the label depends on exactly `horizon` rows of forward
data. Verified: `test_embargo_rows_closes_the_label_window_leak` confirms
zero overlap after the fix, on the identical fold boundaries
(`test_embargo_rows_does_not_change_fold_cadence_or_validation_windows`
confirms validation windows and fold count are unchanged - only which train
rows are included shrinks), and
`test_validator_run_walk_forward_evaluation_applies_the_label_horizon_as_embargo`
pins the call-site wiring itself so a future edit can't silently drop it.

## Other tests added

- `test_embargo_larger_than_a_fold_train_window_never_produces_a_negative_size_slice`
  - a misconfigured embargo larger than a fold's train window degrades to an
    empty (never negative/garbage) train slice.
- `test_no_single_row_appears_in_both_a_folds_train_and_validation` /
  `test_chronological_split_train_and_test_never_share_a_row` - a simpler,
  direct row-identity contamination check (no shared timestamp between
  train and validation/test), complementing the window-overlap check above.

## Scope not covered here

A parallel embargo for `src/ml/splits.py`'s `chronological_split` (train /
validation / test, used by `trainer.py`) was considered but not added: that
split already puts its single train/validation/test boundary once per
dataset rather than repeating it across many rolling folds, and
`trainer.py`'s own `rows_with_known_label` + the `min_rows`-gated audit
already bound how much of this can matter in practice. Revisiting it is a
candidate for a future, narrowly-scoped follow-up rather than part of this
sprint.
