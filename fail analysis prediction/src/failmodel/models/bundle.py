"""Model artifacts: save, load and score with a versioned bundle under ``artifacts/models/<version>/``."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ..config import Settings, provisional_report
from ..features.build import CATEGORICAL, FEATURE_NAMES
from .explain import reason_codes
from .metrics import reference_profile
from .train import DirectionResult, lgb_matrix


@dataclass
class DirectionModel:
    booster: lgb.Booster
    calibrator: object
    levels: dict
    reference: dict


@dataclass
class ModelBundle:
    version: str
    path: Path
    manifest: dict
    directions: dict[str, DirectionModel]

    def score(self, frame: pd.DataFrame, reasons: int = 3) -> pd.DataFrame:
        """Calibrated fail probability, rank within (scoring date, book, direction) and reason codes per trade."""
        parts = []
        for direction, model in self.directions.items():
            rows = frame[frame["direction"].eq(direction)]
            if rows.empty:
                continue
            X = lgb_matrix(rows, model.levels)
            raw = model.booster.predict(X)
            out = pd.DataFrame({"raw_score": raw, "fail_probability": model.calibrator.predict(raw)}, index=rows.index)
            parts.append(out.join(reason_codes(model.booster, X, reasons)))
        scored = pd.concat(parts).reindex(frame.index)
        groups = [frame["scoring_date"], frame["book"], frame["direction"]]
        scored["risk_rank"] = scored.groupby(groups)["fail_probability"].rank(method="first", ascending=False).astype("Int64")
        return scored


def _reference(result: DirectionResult) -> dict:
    feats = {f: reference_profile(result.training_rows[f], f in CATEGORICAL) for f in FEATURE_NAMES}
    hm = result.holdout_metrics.set_index("model").loc["lightgbm"]
    return {"features": feats,
            "score": reference_profile(result.holdout_predictions["fail_probability"], False),
            "holdout": {k: float(hm[k]) for k in ("pr_auc", "roc_auc", "base_rate", "mean_score", "ece", "brier",
                                                  "precision_at_50", "precision_at_5pct")}}


def save_bundle(results: dict[str, DirectionResult], settings: Settings, version: str, root: Path | None = None) -> Path:
    path = (root or settings.artifacts_dir / "models") / version
    path.mkdir(parents=True, exist_ok=True)
    directions = {}
    for direction, r in results.items():
        r.booster.save_model(str(path / f"model_{direction}.txt"))
        with open(path / f"calibrator_{direction}.pkl", "wb") as fh:
            pickle.dump(r.calibrator, fh)
        (path / f"reference_{direction}.json").write_text(json.dumps(_reference(r)))
        directions[direction] = {
            "params": r.params, "rounds": r.rounds, "best_iterations": r.best_iterations, "levels": r.levels,
            "calibrator": r.calibrator.describe(), "dev_calibrator": r.dev_calibrator.describe(),
            "windows": r.windows, "acceptance": r.acceptance,
        }
    manifest = {"version": version, "created": datetime.now().isoformat(timespec="seconds"),
                "features": FEATURE_NAMES, "categorical": CATEGORICAL, "directions": directions,
                "provisional_settings": provisional_report(settings)}
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    metrics = {d: {"folds": r.folds.to_dict("records"), "fold_metrics": r.fold_metrics.to_dict("records"),
                   "holdout": r.holdout_metrics.to_dict("records"), "slices": r.slices.to_dict("records"),
                   "importance": r.importance.head(25).to_dict("records"),
                   "tuning": None if r.tuning is None else r.tuning.to_dict("records")}
               for d, r in results.items()}
    (path / "metrics.json").write_text(json.dumps(metrics, indent=2, default=_json_default))
    return path


def _json_default(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    return str(v)


def load_bundle(path: Path) -> ModelBundle:
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["features"] != FEATURE_NAMES:
        raise ValueError("bundle feature list differs from the code's FEATURE_CATALOG; retrain or check out the matching code")
    directions = {}
    for direction, meta in manifest["directions"].items():
        booster = lgb.Booster(model_file=str(path / f"model_{direction}.txt"))
        with open(path / f"calibrator_{direction}.pkl", "rb") as fh:
            calibrator = pickle.load(fh)
        reference = json.loads((path / f"reference_{direction}.json").read_text())
        directions[direction] = DirectionModel(booster, calibrator, meta["levels"], reference)
    return ModelBundle(manifest["version"], path, manifest, directions)


def latest_bundle(settings: Settings) -> Path:
    root = settings.artifacts_dir / "models"
    candidates = sorted(p for p in root.glob("*") if (p / "manifest.json").exists()) if root.exists() else []
    if not candidates:
        raise FileNotFoundError(f"no model bundle under {root}; run `failmodel train` first")
    return max(candidates, key=lambda p: json.loads((p / "manifest.json").read_text())["created"])
