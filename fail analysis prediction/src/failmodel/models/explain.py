"""Reason codes from LightGBM native SHAP contributions (``pred_contrib=True``)."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..features.build import FEATURE_CATALOG

DESCRIPTIONS = {s.name: s.description for s in FEATURE_CATALOG}
_LOG_DESC = re.compile(r"^(signed )?log\(1 \+ (.*)\)(.*)$", re.IGNORECASE)


def contributions(booster, X: pd.DataFrame) -> np.ndarray:
    """SHAP contributions in log-odds per feature (bias column dropped)."""
    return np.asarray(booster.predict(X, pred_contrib=True))[:, : X.shape[1]]


def _plain(name: str, value) -> str:
    desc = DESCRIPTIONS.get(name, name)
    if isinstance(value, str) or not np.issubdtype(type(value), np.number):
        return f"{desc}: {value}"
    if pd.isna(value):
        return f"{desc}: missing"
    m = _LOG_DESC.match(desc)
    if m and (name.endswith("_log") or name.startswith("log_")):
        raw = np.sign(value) * np.expm1(abs(value))
        return f"{m.group(2)[0].upper()}{m.group(2)[1:]}{m.group(3)}: {raw:,.0f}"
    if float(value).is_integer():
        return f"{desc}: {int(value):,d}"
    return f"{desc}: {value:,.3g}"


def reason_codes(booster, X: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """Top ``n`` features pushing each trade's risk up, with plain-language text."""
    contrib = contributions(booster, X)
    names = np.asarray(X.columns)
    order = np.argsort(-contrib, axis=1)[:, :n]
    out = {}
    values = X.to_numpy(dtype=object)
    rows = np.arange(len(X))
    for i in range(n):
        idx = order[:, i]
        c = contrib[rows, idx]
        up = c > 0
        out[f"reason_{i + 1}"] = np.where(up, names[idx], None)
        out[f"reason_{i + 1}_contrib"] = np.where(up, c, np.nan)
        out[f"reason_{i + 1}_text"] = [(_plain(names[j], values[r, j]) if u else None) for r, j, u in zip(rows, idx, up)]
    return pd.DataFrame(out, index=X.index)


def global_importance(booster, X: pd.DataFrame) -> pd.DataFrame:
    contrib = np.abs(contributions(booster, X))
    mean = contrib.mean(axis=0)
    out = pd.DataFrame({"feature": X.columns, "mean_abs_shap": mean, "share": mean / mean.sum() if mean.sum() else mean})
    return out.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)
