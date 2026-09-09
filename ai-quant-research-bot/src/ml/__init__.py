"""Quant / ML Intelligence Layer (Phase 6).

This package is a DECISION-SUPPORT layer only. It never places an order,
never connects to a broker, never trades on margin/shorts/options, and never
bypasses the deterministic gates that already exist (signal label,
individual risk manager, market regime filter, portfolio risk manager,
aggressive-mode gate). See `src/quant_agent.py` for the integration point
that enforces this, and README "Quant / ML Intelligence Layer" for the full
rationale.

The authoritative pipeline (unchanged by this package):

    Market Data -> Features -> Strategy Signals -> Signal Score ->
    Individual Risk -> Market Regime -> Portfolio Risk -> ML Intelligence ->
    Top Candidate -> Telegram Approval -> Simulated Paper Trade

ML runs AFTER Portfolio Risk, and can only make admission stricter or
ranking better - never promote an Avoid candidate, never override a risk/
regime/portfolio rejection, never enable Aggressive mode, never increase a
position size.

Modules:
- `labels.py`: classification/regression target construction from
  `dataset_builder`'s already-causal `forward_*d_return` columns.
- `features.py`: the explicit feature whitelist and matrix-preparation
  helpers (imputation fit on train only - never test/validation).
- `audit.py`: pre-training data-quality/leakage checks (Part D).
- `splits.py`: chronological train/validation/test and walk-forward split
  logic (Parts E/F) - never a random shuffle.
- `models.py`: the three model families (Logistic Regression, Random
  Forest, HistGradientBoosting) as sklearn Pipelines.
- `calibration.py`: probability calibration (Platt/sigmoid or isotonic).
- `validator.py`: metric computation, overfit detection, baseline
  comparison, walk-forward orchestration (Parts F/H/I).
- `trainer.py`: offline training entry point - never invoked from
  `src.main` (Part X).
- `model_registry.py`: versioned artifact storage + champion/challenger
  status (Part J/K).
- `predictor.py`: `MLPrediction` schema, confidence bands, and the
  conservative ensemble (Parts M/N/O).
- `drift.py`: feature/prediction/calibration/data-quality drift monitoring
  (Part W) - reports only, never auto-retrains.
"""
