"""Benchmarks (plan 05): the ops triage rule and a class-weighted logistic regression."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


def rule_score(frame: pd.DataFrame) -> np.ndarray:
    """1 when the CUSIP already has an open fail in the same direction or the Beta box is in deficit; ties are
    broken by notional (scaled into [0, 0.5) so the rule always dominates)."""
    open_fail = frame["cusip_open_fails_same_dir"].fillna(0).to_numpy() > 0
    deficit = frame["box_deficit_log"].fillna(0).to_numpy() > 0
    tie = frame["notional"].astype(float).rank(pct=True).fillna(0).to_numpy() * 0.5 * (1 - 1e-9)
    return (open_fail | deficit).astype(float) + tie


def logistic_baseline(numeric: list[str], categorical: list[str], seed: int = 7) -> Pipeline:
    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler()), numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), categorical),
    ])
    model = LogisticRegression(class_weight="balanced", max_iter=2000, C=0.5, random_state=seed)
    return Pipeline([("pre", pre), ("lr", model)])


def logistic_matrix(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    X = frame[numeric].astype(float).replace([np.inf, -np.inf], np.nan)
    for c in categorical:
        X[c] = frame[c].astype("string").fillna("<NA>").astype(object)
    return X
