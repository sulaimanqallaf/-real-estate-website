"""Model artifact storage + champion/challenger lifecycle (Phase 6 Parts J/K).

Every trained model is saved under `data/models/<model_id>/` as
`model.joblib` (the fitted estimator/feature-spec/calibration bundle) plus a
human-readable `metadata.json` (model_type, target, horizon, trained_at,
train/validation/test date ranges, feature list, hyperparameters, metrics,
code/schema version, dataset fingerprint, status). **A new model is ALWAYS
saved under a brand-new `model_id` - `save_model()` has no code path that
overwrites an existing file.** Promotion to CHAMPION is a separate, explicit
action (`promote_to_champion()`), never a side effect of saving, so the
current champion is never silently replaced.

**Champion slot key: `(task, target, horizon, model_type)` - one champion
PER MODEL FAMILY, not one overall.** This is what makes Part K
(champion/challenger promotion) and Part O (a 3-model ensemble at prediction
time) compose cleanly: a new Logistic Regression challenger only ever
competes against the current Logistic Regression champion for that
(task, horizon) slot, never against the Random Forest or
HistGradientBoosting champion - so `predictor.py`'s ensemble can load "the
champion of each of the three families" and combine them, while each
family's own lineage is still independently, transparently promotion-gated.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

CODE_VERSION = "phase6-ml-v1"

STATUS_EXPERIMENT = "EXPERIMENT"
STATUS_CHALLENGER = "CHALLENGER"
STATUS_CHAMPION = "CHAMPION"
STATUS_RETIRED = "RETIRED"
ALL_STATUSES = (STATUS_EXPERIMENT, STATUS_CHALLENGER, STATUS_CHAMPION, STATUS_RETIRED)


def new_model_id(model_type: str, target: str, horizon: int) -> str:
    return f"{target}_{horizon}d_{model_type}_{uuid.uuid4().hex[:10]}"


def compute_dataset_fingerprint(df: pd.DataFrame) -> str:
    """A short, stable hash of a dataset's shape/columns/date range AND its
    actual content - stored with every model so it's always possible to
    tell which dataset version a model was trained on, without storing the
    entire dataset itself. Content is included (via `pandas.util.hash_pandas_
    object`) specifically because two datasets can share identical shape/
    column/date-range metadata while holding completely different values
    (e.g. two synthetic fixtures of the same size and date range) - hashing
    metadata alone would collide for those."""
    if df is None or len(df) == 0:
        payload = "empty"
    else:
        cols = ",".join(sorted(df.columns.astype(str)))
        ts_min = str(df["timestamp"].min()) if "timestamp" in df.columns else "?"
        ts_max = str(df["timestamp"].max()) if "timestamp" in df.columns else "?"
        content_hash = int(pd.util.hash_pandas_object(df, index=False).sum())
        payload = f"{cols}|{len(df)}|{ts_min}|{ts_max}|{content_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class ModelMetadata:
    model_id: str
    model_type: str
    task: str
    target: str
    horizon: int
    trained_at: str
    train_start: str | None
    train_end: str | None
    validation_end: str | None
    test_end: str | None
    feature_list: list[str]
    hyperparameters: dict[str, Any]
    metrics: dict[str, Any]
    code_version: str
    dataset_fingerprint: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelMetadata":
        return cls(**data)


def build_metadata(
    model_type: str,
    task: str,
    target: str,
    horizon: int,
    train_start: Any,
    train_end: Any,
    validation_end: Any,
    test_end: Any,
    feature_list: list[str],
    hyperparameters: dict[str, Any],
    metrics: dict[str, Any],
    dataset_fingerprint: str,
    status: str = STATUS_EXPERIMENT,
) -> ModelMetadata:
    return ModelMetadata(
        model_id=new_model_id(model_type, target, horizon),
        model_type=model_type,
        task=task,
        target=target,
        horizon=horizon,
        trained_at=datetime.now(timezone.utc).isoformat(),
        train_start=str(train_start) if train_start is not None else None,
        train_end=str(train_end) if train_end is not None else None,
        validation_end=str(validation_end) if validation_end is not None else None,
        test_end=str(test_end) if test_end is not None else None,
        feature_list=list(feature_list),
        hyperparameters=dict(hyperparameters),
        metrics=metrics,
        code_version=CODE_VERSION,
        dataset_fingerprint=dataset_fingerprint,
        status=status,
    )


class ModelRegistry:
    def __init__(self, registry_dir: str | Path):
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)

    def _model_dir(self, model_id: str) -> Path:
        return self.registry_dir / model_id

    def save_model(self, bundle: dict[str, Any], metadata: ModelMetadata) -> Path:
        """Always a NEW directory (`metadata.model_id` is freshly generated
        by `build_metadata`/`new_model_id`) - there is no overwrite path."""
        model_dir = self._model_dir(metadata.model_id)
        model_dir.mkdir(parents=True, exist_ok=False)
        joblib.dump(bundle, model_dir / "model.joblib")
        self._write_metadata(metadata)
        return model_dir

    def load_model(self, model_id: str) -> tuple[dict[str, Any], ModelMetadata]:
        bundle = joblib.load(self._model_dir(model_id) / "model.joblib")
        metadata = self.read_metadata(model_id)
        return bundle, metadata

    def read_metadata(self, model_id: str) -> ModelMetadata:
        with open(self._model_dir(model_id) / "metadata.json", encoding="utf-8") as f:
            return ModelMetadata.from_dict(json.load(f))

    def _write_metadata(self, metadata: ModelMetadata) -> None:
        model_dir = self._model_dir(metadata.model_id)
        model_dir.mkdir(parents=True, exist_ok=True)
        with open(model_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata.to_dict(), f, indent=2, default=str)

    def list_metadata(
        self, task: str | None = None, target: str | None = None, horizon: int | None = None, status: str | None = None
    ) -> list[ModelMetadata]:
        results = []
        if not self.registry_dir.exists():
            return results
        for entry in sorted(self.registry_dir.iterdir()):
            meta_path = entry / "metadata.json"
            if not meta_path.exists():
                continue
            with open(meta_path, encoding="utf-8") as f:
                meta = ModelMetadata.from_dict(json.load(f))
            if task is not None and meta.task != task:
                continue
            if target is not None and meta.target != target:
                continue
            if horizon is not None and meta.horizon != horizon:
                continue
            if status is not None and meta.status != status:
                continue
            results.append(meta)
        return results

    def get_champion(self, task: str, target: str, horizon: int, model_type: str | None = None) -> ModelMetadata | None:
        """With `model_type` given, the champion for that specific model
        family's slot. Without it, ANY champion for this (task, target,
        horizon) - useful for reporting "is there a champion at all" without
        caring which family it is."""
        matches = self.list_metadata(task=task, target=target, horizon=horizon, status=STATUS_CHAMPION)
        if model_type is not None:
            matches = [m for m in matches if m.model_type == model_type]
        return matches[0] if matches else None

    def get_all_champions(self, task: str, target: str, horizon: int) -> dict[str, ModelMetadata]:
        """Every model family's current champion for this (task, target,
        horizon) slot, keyed by `model_type` - what `predictor.py`'s
        ensemble loads."""
        matches = self.list_metadata(task=task, target=target, horizon=horizon, status=STATUS_CHAMPION)
        return {m.model_type: m for m in matches}

    def set_status(self, model_id: str, status: str) -> ModelMetadata:
        if status not in ALL_STATUSES:
            raise ValueError(f"Unknown status '{status}' - expected one of {ALL_STATUSES}.")
        meta = self.read_metadata(model_id)
        meta.status = status
        self._write_metadata(meta)
        return meta

    def promote_to_champion(self, challenger_id: str) -> ModelMetadata:
        """The ONLY way a model becomes CHAMPION. Retires whatever champion
        already existed for the SAME (task, target, horizon, model_type)
        slot - never leaves two CHAMPIONs for the same model family, and
        never touches a different family's champion."""
        challenger = self.read_metadata(challenger_id)
        existing_champion = self.get_champion(challenger.task, challenger.target, challenger.horizon, challenger.model_type)
        if existing_champion is not None and existing_champion.model_id != challenger_id:
            self.set_status(existing_champion.model_id, STATUS_RETIRED)
        return self.set_status(challenger_id, STATUS_CHAMPION)


# --- champion/challenger promotion RULES (Part K) -----------------------------------


def decide_promotion(challenger: ModelMetadata, champion: ModelMetadata | None) -> dict[str, Any]:
    """Transparent, multi-criterion promotion rule - "No autonomous promotion
    based solely on one metric" (Part K). With no existing champion, the
    challenger is promoted by default (there's nothing to compare against).
    Otherwise the challenger must win on AT LEAST TWO of three criteria
    (better PR-AUC, better Brier score, stronger top-decile/top-bucket
    expected return) AND show no NEW overfit warning the champion didn't
    already have. A tie or a single-criterion win keeps the current champion."""
    if champion is None:
        return {"promote": True, "reasons": ["No existing champion for this (task, target, horizon) - challenger becomes champion."]}

    c_metrics = challenger.metrics.get("test") or challenger.metrics.get("walk_forward") or {}
    champ_metrics = champion.metrics.get("test") or champion.metrics.get("walk_forward") or {}

    reasons = []
    criteria_won = 0

    c_pr_auc, champ_pr_auc = c_metrics.get("pr_auc"), champ_metrics.get("pr_auc")
    if c_pr_auc is not None and champ_pr_auc is not None and c_pr_auc > champ_pr_auc:
        criteria_won += 1
        reasons.append(f"better PR-AUC ({c_pr_auc:.4f} vs {champ_pr_auc:.4f})")

    c_brier, champ_brier = c_metrics.get("brier_score"), champ_metrics.get("brier_score")
    if c_brier is not None and champ_brier is not None and c_brier < champ_brier:
        criteria_won += 1
        reasons.append(f"better Brier score ({c_brier:.4f} vs {champ_brier:.4f})")

    c_top = (challenger.metrics.get("top_bucket") or {}).get("expected_return")
    champ_top = (champion.metrics.get("top_bucket") or {}).get("expected_return")
    if c_top is not None and champ_top is not None and c_top > champ_top:
        criteria_won += 1
        reasons.append(f"stronger top-bucket expected return ({c_top:.4f} vs {champ_top:.4f})")

    challenger_overfit = challenger.metrics.get("overfit_warning") is not None
    champion_overfit = champion.metrics.get("overfit_warning") is not None
    new_overfit_regression = challenger_overfit and not champion_overfit

    if new_overfit_regression:
        return {"promote": False, "reasons": ["Challenger shows a new overfit warning the champion didn't have - blocked regardless of other criteria."]}

    if criteria_won >= 2:
        return {"promote": True, "reasons": reasons}

    return {
        "promote": False,
        "reasons": reasons or ["Challenger did not clearly beat the champion on at least two criteria - keeping current champion."],
    }
