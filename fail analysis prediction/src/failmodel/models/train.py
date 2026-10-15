"""Training (plan 05): deliver and receive LightGBM models pooled across books, rolling-origin folds,
calibration on late out-of-fold predictions, out-of-time holdout, benchmarks and acceptance checks.

Holdout metrics come from a model trained only on data before the holdout purge. The deployed model is
then refit on every labelled row with the same rounds, and its calibrator is refit on the late out-of-fold
plus holdout predictions (all out of time).

A ``Task`` names the target, features, time axis and benchmark. The default is the v1 new-fail task; the
phase-5 persistence tasks (``failmodel.persistence``) reuse the same engine.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

import lightgbm as lgb
import numpy as np
import pandas as pd

from ..bizcal import BusinessCalendar
from ..config import Settings
from ..features.build import CATEGORICAL, FEATURE_NAMES, TARGET
from .baseline import logistic_baseline, logistic_matrix, rule_score
from .calibration import Calibrator
from .explain import global_importance
from .metrics import SLICES, evaluate, slice_metrics
from .validation import Fold, fold_table, holdout_fold, rolling_folds

DIRECTIONS = ("D", "R")
NUMERIC = [f for f in FEATURE_NAMES if f not in CATEGORICAL]
TUNING_GRID = {"num_leaves": (15, 31, 63), "min_data_in_leaf": (50, 100, 200)}


@dataclass(frozen=True)
class Task:
    """What a model predicts. ``baseline(train_frame, y_train, frame)`` scores ``frame``; it may fit on the
    training rows (the holdout baseline only ever sees development rows)."""

    name: str
    target: str
    features: tuple[str, ...]
    categorical: tuple[str, ...]
    baseline: Callable
    baseline_name: str = "rule"
    date_col: str = "settle_date"
    pred_columns: tuple[str, ...] = ("trade_key", "book", "settle_date", "scoring_date", "notional")
    slices: tuple[str, ...] = SLICES
    top_k: tuple[int, ...] | None = None
    gate_precision_at: int | None = 50

    @property
    def numeric(self) -> list[str]:
        return [f for f in self.features if f not in self.categorical]


NEW_FAIL = Task("new_fail", TARGET, tuple(FEATURE_NAMES), tuple(CATEGORICAL),
                baseline=lambda train, y, frame: rule_score(frame))


def category_levels(frame: pd.DataFrame, categorical) -> dict[str, list[str]]:
    return {c: sorted(frame[c].astype("string").dropna().unique().tolist()) for c in categorical}


def lgb_matrix(frame: pd.DataFrame, levels: dict[str, list[str]], task: Task = NEW_FAIL) -> pd.DataFrame:
    X = frame[task.numeric].astype(float).replace([np.inf, -np.inf], np.nan)
    for c in task.categorical:
        X[c] = pd.Categorical(frame[c].astype("string"), categories=levels[c])
    return X[list(task.features)]


def _categorical(X: pd.DataFrame) -> list[str]:
    return [c for c in X.columns if isinstance(X[c].dtype, pd.CategoricalDtype)]


def fit_lgbm(X: pd.DataFrame, y: np.ndarray, params: dict, rounds: int, X_valid=None, y_valid=None,
             early_stopping: int | None = None) -> lgb.Booster:
    pos = max(int(y.sum()), 1)
    p = {**params, "scale_pos_weight": (len(y) - pos) / pos, "metric": "average_precision"}
    train = lgb.Dataset(X, y, categorical_feature=_categorical(X), free_raw_data=False)
    if X_valid is None:
        return lgb.train(p, train, num_boost_round=rounds)
    valid = lgb.Dataset(X_valid, y_valid, categorical_feature=_categorical(X), reference=train)
    return lgb.train(p, train, num_boost_round=rounds, valid_sets=[valid],
                     callbacks=[lgb.early_stopping(early_stopping, verbose=False)])


@dataclass
class DirectionResult:
    direction: str
    params: dict
    levels: dict
    folds: pd.DataFrame
    fold_metrics: pd.DataFrame
    holdout_metrics: pd.DataFrame
    slices: pd.DataFrame
    importance: pd.DataFrame
    acceptance: dict
    best_iterations: list[int]
    rounds: int
    calibrator: Calibrator
    dev_calibrator: Calibrator
    booster: lgb.Booster
    holdout_predictions: pd.DataFrame
    training_rows: pd.DataFrame
    tuning: pd.DataFrame | None = None
    windows: dict = field(default_factory=dict)
    task: str = NEW_FAIL.name


def _fold_models(d, X, XL, y, folds: list[Fold], params, ms, task: Task = NEW_FAIL):
    when = d[task.date_col]
    ks = task.top_k or ms.top_k
    oof = np.full(len(d), np.nan)
    rows, best = [], []
    for f in folds:
        tr, va = f.train_mask(when).to_numpy(), f.valid_mask(when).to_numpy()
        if y[tr].sum() == 0 or y[va].sum() == 0:
            continue
        booster = fit_lgbm(X[tr], y[tr], params, ms.num_boost_round, X[va], y[va], ms.early_stopping_rounds)
        it = booster.best_iteration or ms.num_boost_round
        best.append(int(it))
        p = booster.predict(X[va], num_iteration=it)
        oof[va] = p
        lr = logistic_baseline(task.numeric, list(task.categorical)).fit(XL[tr], y[tr])
        part = d[va]
        for name, s in (("lightgbm", p), ("logistic", lr.predict_proba(XL[va])[:, 1]),
                        (task.baseline_name, task.baseline(d[tr], y[tr], part))):
            rows.append({"fold": f.name, "model": name, **evaluate(part, y[va], s, ks, task.date_col)})
    return oof, pd.DataFrame(rows), best


def tune_params(d, X, XL, y, folds, ms, task: Task = NEW_FAIL) -> tuple[dict, pd.DataFrame]:
    rows = []
    when = d[task.date_col]
    for leaves, min_leaf in itertools.product(*TUNING_GRID.values()):
        params = {**ms.lgbm_params, "num_leaves": leaves, "min_data_in_leaf": min_leaf}
        scores = []
        for f in folds:
            tr, va = f.train_mask(when).to_numpy(), f.valid_mask(when).to_numpy()
            b = fit_lgbm(X[tr], y[tr], params, ms.num_boost_round, X[va], y[va], ms.early_stopping_rounds)
            scores.append(evaluate(d[va], y[va], b.predict(X[va], num_iteration=b.best_iteration), (),
                                   task.date_col)["pr_auc"])
        rows.append({"num_leaves": leaves, "min_data_in_leaf": min_leaf, "mean_fold_pr_auc": float(np.nanmean(scores))})
    grid = pd.DataFrame(rows).sort_values("mean_fold_pr_auc", ascending=False).reset_index(drop=True)
    best = grid.iloc[0]
    return {**ms.lgbm_params, "num_leaves": int(best["num_leaves"]), "min_data_in_leaf": int(best["min_data_in_leaf"])}, grid


def acceptance_checks(holdout: pd.DataFrame, fold_metrics: pd.DataFrame, task: Task = NEW_FAIL) -> dict:
    """Plan-05 criteria. ``info_*`` entries are reported but do not gate acceptance."""
    h = holdout.set_index("model")
    b = task.baseline_name
    lgbm, base_model = h.loc["lightgbm"], h.loc[b]
    base, ece = float(lgbm["base_rate"]), float(lgbm["ece"])
    fold_pr = fold_metrics.loc[fold_metrics["model"] == "lightgbm", "pr_auc"]
    stability = float(fold_pr.min() / fold_pr.mean()) if len(fold_pr) else np.nan
    out = {f"beats_{b}_pr_auc": bool(lgbm["pr_auc"] > base_model["pr_auc"])}
    if task.gate_precision_at:
        k = f"precision_at_{task.gate_precision_at}"
        out[f"beats_{b}_{k}"] = bool(lgbm[k] > base_model[k])
    out.update({
        "ece_ok": bool(ece <= 0.02 or (base < 0.02 and ece <= 0.25 * base)),
        "fold_stability_ratio": stability,
        "fold_stability_ok": bool(stability >= 0.7) if not np.isnan(stability) else False,
        "info_beats_logistic_pr_auc": bool(lgbm["pr_auc"] > h.loc["logistic", "pr_auc"]),
        f"info_beats_{b}_precision_at_5pct": bool(lgbm["precision_at_5pct"] > base_model["precision_at_5pct"]),
    })
    return out


def accepted(acceptance: dict) -> bool:
    return all(v for k, v in acceptance.items() if k.startswith("beats_") or k.endswith("_ok"))


def train_direction(frame: pd.DataFrame, direction: str, cal: BusinessCalendar, settings: Settings,
                    tune: bool = False, log=print, task: Task = NEW_FAIL) -> DirectionResult:
    ms = settings.model
    ks = task.top_k or ms.top_k
    d = (frame[frame["direction"].eq(direction) & frame[task.target].notna()]
         .sort_values(task.date_col).reset_index(drop=True))
    y = d[task.target].astype(int).to_numpy()
    when = d[task.date_col]
    hold = holdout_fold(when, cal, ms.holdout_months, ms.purge_bd)
    folds = rolling_folds(when, cal, ms.n_folds, ms.purge_bd, hold.valid_start)
    dev_mask = (when <= hold.train_end).to_numpy()
    levels = category_levels(d[dev_mask], task.categorical)
    X, XL = lgb_matrix(d, levels, task), logistic_matrix(d, task.numeric, list(task.categorical))
    tag = direction if task is NEW_FAIL else f"{task.name}:{direction}"

    params, grid = dict(ms.lgbm_params), None
    if tune:
        log(f"[{tag}] tuning {len(list(itertools.product(*TUNING_GRID.values())))} configurations on {len(folds)} folds")
        params, grid = tune_params(d, X, XL, y, folds, ms, task)
    log(f"[{tag}] rolling folds: {[f.name for f in folds]}; holdout {hold.valid_start.date()} to {hold.valid_end.date()}")
    oof, fold_metrics, best = _fold_models(d, X, XL, y, folds, params, ms, task)
    rounds = int(np.median(best)) if best else ms.num_boost_round

    late = np.zeros(len(d), dtype=bool)
    for f in folds[-ms.calibration_folds:]:
        late |= f.valid_mask(when).to_numpy()
    late &= ~np.isnan(oof)
    dev_cal = Calibrator(ms.isotonic_min_positives).fit(oof[late], y[late])

    ho = hold.valid_mask(when).to_numpy()
    dev_model = fit_lgbm(X[dev_mask], y[dev_mask], params, rounds)
    raw = dev_model.predict(X[ho])
    prob = dev_cal.predict(raw)
    lr = logistic_baseline(task.numeric, list(task.categorical)).fit(XL[dev_mask], y[dev_mask])
    part = d[ho]
    holdout_rows = []
    for name, s in (("lightgbm", prob), ("lightgbm_uncalibrated", raw),
                    ("logistic", lr.predict_proba(XL[ho])[:, 1]),
                    (task.baseline_name, task.baseline(d[dev_mask], y[dev_mask], part))):
        holdout_rows.append({"model": name, **evaluate(part, y[ho], s, ks, task.date_col)})
    holdout_metrics = pd.DataFrame(holdout_rows)
    acceptance = acceptance_checks(holdout_metrics, fold_metrics, task)
    slices = slice_metrics(part, y[ho], prob, ks, task.slices, task.date_col)
    importance = global_importance(dev_model, X[ho])

    deployed = fit_lgbm(X, y, params, rounds)
    recal = late | ho
    calib_raw = np.where(ho, 0.0, oof)
    calib_raw[ho] = raw
    calibrator = Calibrator(ms.isotonic_min_positives, method=dev_cal.method).fit(calib_raw[recal], y[recal])
    preds = part[list(task.pred_columns)].assign(y=y[ho], raw_score=raw, fail_probability=prob)
    log(f"[{tag}] holdout PR-AUC lightgbm {holdout_metrics.loc[0, 'pr_auc']:.4f} vs {task.baseline_name} "
        f"{holdout_metrics.loc[3, 'pr_auc']:.4f}; calibration {dev_cal.method}; rounds {rounds}")
    return DirectionResult(
        direction=direction, params=params, levels=levels, folds=fold_table(folds + [hold], when, pd.Series(y)),
        fold_metrics=fold_metrics, holdout_metrics=holdout_metrics, slices=slices, importance=importance,
        acceptance=acceptance, best_iterations=best, rounds=rounds, calibrator=calibrator, dev_calibrator=dev_cal,
        booster=deployed, holdout_predictions=preds, training_rows=d[list(task.features)], tuning=grid,
        windows={"label_start": str(when.min().date()), "label_end": str(when.max().date()),
                 "holdout_start": str(hold.valid_start.date()), "dev_train_end": str(hold.train_end.date()),
                 "rows": int(len(d)), "positives": int(y.sum())},
        task=task.name,
    )


def train_all(frame: pd.DataFrame, cal: BusinessCalendar, settings: Settings, tune: bool = False,
              log=print, task: Task = NEW_FAIL) -> dict[str, DirectionResult]:
    return {direction: train_direction(frame, direction, cal, settings, tune, log, task) for direction in DIRECTIONS}
