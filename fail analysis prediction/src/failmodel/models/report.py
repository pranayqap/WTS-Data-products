"""Training report and model card (markdown) from the training results."""

from __future__ import annotations

import pandas as pd

from ..config import Settings, provisional_report
from ..profiling import md_table
from .train import DirectionResult, accepted

DIRECTION_NAME = {"D": "Deliver", "R": "Receive"}
HOLDOUT_COLS = ["model", "rows", "positives", "base_rate", "pr_auc", "roc_auc", "brier", "ece",
                "precision_at_25", "precision_at_50", "precision_at_100", "recall_at_50", "dollar_recall_at_50",
                "precision_at_5pct", "recall_at_5pct", "dollar_recall_at_5pct"]
FOLD_COLS = ["fold", "model", "rows", "positives", "pr_auc", "roc_auc", "precision_at_50", "precision_at_5pct",
             "recall_at_5pct"]


def _round(frame: pd.DataFrame, cols) -> pd.DataFrame:
    out = frame[[c for c in cols if c in frame.columns]].copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(4)
    return out


def training_report(results: dict[str, DirectionResult], version: str, settings: Settings, synthetic: bool) -> str:
    banner = ("> **Synthetic data.** These figures show that the pipeline recovers planted structure. They say "
              "nothing about real model performance.\n\n" if synthetic else "")
    parts = [f"# Training report — {version}\n\n{banner}"]
    for d, r in results.items():
        acc = r.acceptance
        parts.append(f"## {DIRECTION_NAME[d]} model\n\n"
                     f"Rows {r.windows['rows']:,} ({r.windows['positives']:,} fails), settle dates {r.windows['label_start']} to "
                     f"{r.windows['label_end']}. Holdout from {r.windows['holdout_start']} (development training ends "
                     f"{r.windows['dev_train_end']}). Rounds {r.rounds} (median best iteration of {r.best_iterations}). "
                     f"Calibration: {r.dev_calibrator.method} on {r.dev_calibrator.fit_positives} late out-of-fold positives; "
                     f"deployed calibrator {r.calibrator.method} on {r.calibrator.fit_positives} positives.\n")
        parts.append("### Folds\n\n" + md_table(r.folds))
        parts.append("### Holdout\n\n" + md_table(_round(r.holdout_metrics, HOLDOUT_COLS)))
        parts.append("### Acceptance\n\n" + "\n".join(f"- `{k}`: {v if not isinstance(v, float) else round(v, 3)}"
                                                      for k, v in acc.items()))
        parts.append("### Rolling folds\n\n" + md_table(_round(r.fold_metrics, FOLD_COLS)))
        parts.append("### Holdout slices (LightGBM, calibrated)\n\n" + md_table(_round(r.slices, r.slices.columns)))
        parts.append("### Top features (mean |SHAP| on the holdout)\n\n" + md_table(_round(r.importance.head(15), r.importance.columns)))
        if r.tuning is not None:
            parts.append("### Tuning grid (mean fold PR-AUC)\n\n" + md_table(_round(r.tuning, r.tuning.columns)))
    parts.append("## Provisional settings in effect\n\n" + "\n".join(f"- {line}" for line in provisional_report(settings)))
    return "\n\n".join(parts) + "\n"


def model_card(results: dict[str, DirectionResult], version: str, settings: Settings, synthetic: bool) -> str:
    lines = [f"# Model card — {version}", "",
             "| Field | Value |", "|---|---|",
             "| Model | Fail Analysis Predictive Model, new-fail v1 (deliver and receive LightGBM, pooled Beta and P3) |",
             "| Intended use | Advisory nightly ranking (about 22:30 ET on S − 1) of trades settling on S by fail probability, with reason codes, for the Ops Hub Fail Analysis tool |",
             "| Not for | Automated actions, client communication, regulatory reporting, Broadridge trades before re-validation |",
             "| Owner | Data & Decision Science — Pranay Koneni |",
             f"| Training data | {'SYNTHETIC test bed' if synthetic else 'WSI extracts'}; labels from {settings.label_start} |",
             "| Documentation | failanalysispredictivemodel.md |", ""]
    lines += ["| Direction | Holdout PR-AUC | Rule PR-AUC | Logistic PR-AUC | Base rate | ECE | Precision@5% | "
              "Dollar recall@5% | Acceptance |", "|---|---|---|---|---|---|---|---|---|"]
    for d, r in results.items():
        h = r.holdout_metrics.set_index("model")
        lines.append(f"| {DIRECTION_NAME[d]} | {h.loc['lightgbm', 'pr_auc']:.3f} | {h.loc['rule', 'pr_auc']:.3f} | "
                     f"{h.loc['logistic', 'pr_auc']:.3f} | {h.loc['lightgbm', 'base_rate']:.3f} | "
                     f"{h.loc['lightgbm', 'ece']:.4f} | {h.loc['lightgbm', 'precision_at_5pct']:.3f} | "
                     f"{h.loc['lightgbm', 'dollar_recall_at_5pct']:.3f} | {'pass' if accepted(r.acceptance) else 'see report'} |")
    lines += ["", "Limits: see documentation section 13. Provisional settings:", ""]
    lines += [f"- {line}" for line in provisional_report(settings)]
    return "\n".join(lines) + "\n"
