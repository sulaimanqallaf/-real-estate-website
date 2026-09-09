"""Model families (Phase 6 Part A/L): Logistic Regression, Random Forest, and
a gradient-boosting fallback chain.

Dependency decision (documented here and in README "Dependencies evaluated
for this phase"): `xgboost`'s wheel is ~130MB and `lightgbm` would be a
second ML library's API surface to wrap/test on top of scikit-learn (already
needed for Logistic Regression and Random Forest) - both rejected in favor of
`sklearn.ensemble.HistGradientBoostingClassifier/Regressor`, which ships with
scikit-learn at zero extra dependency cost, natively handles missing values
(no imputation needed), and satisfies the "gradient boosting" model family
requirement directly. This keeps the ENTIRE Phase 6 ML layer to one new
dependency.

Every model here is returned as a small, explicit wrapper (`ModelSpec`)
rather than a bare estimator, so `trainer.py`/`validator.py` can treat all
three families uniformly (same `fit`/`predict`/`predict_proba` contract) while
each family's own imputation policy (impute for Logistic Regression/Random
Forest; pass NaN through untouched for HistGradientBoosting) stays local to
this module - see `ModelSpec.prepare_X`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression

from . import features as ml_features

TASK_CLASSIFICATION = "classification"
TASK_REGRESSION = "regression"

MODEL_LOGISTIC_REGRESSION = "logistic_regression"
MODEL_RANDOM_FOREST = "random_forest"
MODEL_GRADIENT_BOOSTING = "gradient_boosting"

ALL_MODEL_TYPES = (MODEL_LOGISTIC_REGRESSION, MODEL_RANDOM_FOREST, MODEL_GRADIENT_BOOSTING)

# Deliberately small, modest grids (Part L: "keep tuning modest... do NOT run
# huge brute-force searches"). Each is a handful of reasonable values, never
# hundreds of knobs.
DEFAULT_PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    MODEL_LOGISTIC_REGRESSION: {"C": [0.1, 1.0, 10.0]},
    MODEL_RANDOM_FOREST: {"n_estimators": [100, 200], "max_depth": [4, 8, None]},
    MODEL_GRADIENT_BOOSTING: {"max_depth": [3, 5], "learning_rate": [0.05, 0.1]},
}

DEFAULT_HYPERPARAMETERS: dict[str, dict[str, Any]] = {
    MODEL_LOGISTIC_REGRESSION: {"C": 1.0, "max_iter": 3000},
    MODEL_RANDOM_FOREST: {"n_estimators": 150, "max_depth": 6, "min_samples_leaf": 5, "random_state": 42},
    MODEL_GRADIENT_BOOSTING: {"max_depth": 4, "learning_rate": 0.08, "max_iter": 150, "random_state": 42},
}


@dataclass
class ModelSpec:
    """Uniform wrapper around one fitted-or-unfitted estimator. `needs_imputation`
    controls whether `prepare_X` fills NaN using a `FittedFeatureSpec`
    (Logistic Regression, Random Forest) or leaves them as native NaN
    (HistGradientBoosting, which handles missingness itself)."""

    model_type: str
    task: str
    estimator: Any
    needs_imputation: bool
    feature_names: list[str]

    def prepare_X(self, df: pd.DataFrame, feature_spec: ml_features.FittedFeatureSpec) -> pd.DataFrame:
        if self.needs_imputation:
            return feature_spec.transform(df)

        prepared = feature_spec.transform_allow_nan(df)
        # HistGradientBoosting's binning step raises on a column with fewer
        # than 2 distinct NON-MISSING values (all-NaN when a data source is
        # entirely unavailable for the whole training window, e.g.
        # institutional_score with no SEC_IDENTITY configured, or a
        # genuinely constant feature). That column already carries zero
        # information either way, so it's neutralized to an explicit 0.0
        # here rather than left to crash the binner - this doesn't fabricate
        # a meaningful value FOR that feature, it just keeps training
        # possible when one whitelisted feature happens to be empty.
        for col in prepared.columns:
            if prepared[col].dropna().nunique() < 2:
                prepared[col] = 0.0
        return prepared

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "ModelSpec":
        self.estimator.fit(X.values, y.values)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict(X.values)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Positive-class probability. Raises if this isn't a classifier -
        callers should only call this for TASK_CLASSIFICATION specs."""
        return self.estimator.predict_proba(X.values)[:, 1]

    def feature_importances(self) -> dict[str, float] | None:
        """Tree models expose `feature_importances_`; Logistic Regression
        exposes signed coefficients instead (see `signed_coefficients`).
        `None` (never fabricated) for anything else."""
        if hasattr(self.estimator, "feature_importances_"):
            return dict(zip(self.feature_names, (float(v) for v in self.estimator.feature_importances_)))
        return None

    def signed_coefficients(self) -> dict[str, float] | None:
        if hasattr(self.estimator, "coef_"):
            coefs = np.ravel(self.estimator.coef_)
            return dict(zip(self.feature_names, (float(v) for v in coefs)))
        return None


def build_model(
    model_type: str,
    task: str,
    feature_names: list[str],
    hyperparameters: dict[str, Any] | None = None,
) -> ModelSpec:
    params = dict(DEFAULT_HYPERPARAMETERS.get(model_type, {}))
    params.update(hyperparameters or {})

    if model_type == MODEL_LOGISTIC_REGRESSION:
        if task != TASK_CLASSIFICATION:
            raise ValueError("Logistic Regression only supports the classification task in this phase.")
        estimator = LogisticRegression(**params)
        return ModelSpec(model_type, task, estimator, needs_imputation=True, feature_names=feature_names)

    if model_type == MODEL_RANDOM_FOREST:
        cls = RandomForestClassifier if task == TASK_CLASSIFICATION else RandomForestRegressor
        estimator = cls(**params)
        return ModelSpec(model_type, task, estimator, needs_imputation=True, feature_names=feature_names)

    if model_type == MODEL_GRADIENT_BOOSTING:
        cls = HistGradientBoostingClassifier if task == TASK_CLASSIFICATION else HistGradientBoostingRegressor
        estimator = cls(**params)
        return ModelSpec(model_type, task, estimator, needs_imputation=False, feature_names=feature_names)

    raise ValueError(f"Unknown model_type '{model_type}' - expected one of {ALL_MODEL_TYPES}.")


def build_all_model_specs(task: str, feature_names: list[str], hyperparameters_by_type: dict[str, dict[str, Any]] | None = None) -> dict[str, ModelSpec]:
    """Build every applicable model family for `task` in one call - Logistic
    Regression is skipped for regression (Part A only lists it as a
    classification-style model; Random Forest/HistGradientBoosting both
    support regression natively via their Regressor variants)."""
    hyperparameters_by_type = hyperparameters_by_type or {}
    specs = {}
    for model_type in ALL_MODEL_TYPES:
        if model_type == MODEL_LOGISTIC_REGRESSION and task != TASK_CLASSIFICATION:
            continue
        specs[model_type] = build_model(model_type, task, feature_names, hyperparameters_by_type.get(model_type))
    return specs
