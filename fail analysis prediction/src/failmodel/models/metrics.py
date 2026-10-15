"""Model metrics (plan 05): ranking, calibration, daily top-K, dollar-weighted recall, slices, and the
reference distributions that phase-4 drift monitoring compares against (PSI)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

NOTIONAL_BANDS = (0.0, 1e5, 1e6, 1e7, np.inf)
NOTIONAL_LABELS = ("<100k", "100k-1m", "1m-10m", ">=10m")
SLICES = ("book", "product_group", "counterparty_type", "notional_band")


def failed_value(frame: pd.DataFrame, y: np.ndarray) -> np.ndarray:
    """Dollar value at risk of each positive: failed notional, falling back to trade notional."""
    fail = frame["fail_notional"].astype(float).to_numpy() if "fail_notional" in frame else np.zeros(len(frame))
    notional = frame["notional"].astype(float).fillna(0).to_numpy()
    return np.where(y == 1, np.where(fail > 0, fail, notional), 0.0)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """ECE over equal-frequency bins (equal-width bins are nearly empty at low base rates)."""
    if len(y) == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    groups = np.array_split(order, min(bins, len(y)))
    return float(sum(len(g) / len(y) * abs(p[g].mean() - y[g].mean()) for g in groups if len(g)))


def top_k(days: np.ndarray, score: np.ndarray, y: np.ndarray, value: np.ndarray, ks, pct: float = 0.05) -> dict:
    """Daily top-K precision (mean over days), pooled recall and dollar recall; the same for the daily top
    ``pct`` share of trades, which does not depend on daily volume."""
    df = pd.DataFrame({"day": days, "s": score, "y": y, "v": value})
    df["rank"] = df.groupby("day")["s"].rank(method="first", ascending=False)
    df["n_day"] = df.groupby("day")["y"].transform("size")
    out = {}
    total_y, total_v = df["y"].sum(), df["v"].sum()
    cuts = [(str(k), np.minimum(k, df["n_day"])) for k in ks]
    cuts.append((f"{round(pct * 100)}pct", np.ceil(df["n_day"] * pct)))
    for label, cap in cuts:
        sel = df["rank"] <= cap
        g = df[sel].groupby("day")
        per_day = g["y"].sum() / g["rank"].size()
        out[f"precision_at_{label}"] = float(per_day.mean()) if len(per_day) else np.nan
        out[f"recall_at_{label}"] = float(df.loc[sel, "y"].sum() / total_y) if total_y else np.nan
        out[f"dollar_recall_at_{label}"] = float(df.loc[sel, "v"].sum() / total_v) if total_v else np.nan
    return out


def evaluate(frame: pd.DataFrame, y: np.ndarray, p: np.ndarray, ks, day_col: str = "settle_date") -> dict:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    both = 0 < y.sum() < len(y)
    out = {
        "rows": int(len(y)), "positives": int(y.sum()), "base_rate": float(y.mean()) if len(y) else np.nan,
        "mean_score": float(p.mean()) if len(p) else np.nan,
        "pr_auc": float(average_precision_score(y, p)) if both else np.nan,
        "roc_auc": float(roc_auc_score(y, p)) if both else np.nan,
        "brier": float(brier_score_loss(y, np.clip(p, 0, 1))) if len(y) else np.nan,
        "ece": expected_calibration_error(y, np.clip(p, 0, 1)),
    }
    out.update(top_k(frame[day_col].to_numpy(), p, y, failed_value(frame, y), ks))
    return out


def notional_band(notional: pd.Series) -> pd.Series:
    return pd.cut(notional.astype(float).fillna(0), bins=list(NOTIONAL_BANDS), labels=list(NOTIONAL_LABELS),
                  right=False).astype("string")


def slice_metrics(frame: pd.DataFrame, y: np.ndarray, p: np.ndarray, ks, slices=SLICES,
                  day_col: str = "settle_date") -> pd.DataFrame:
    f = frame.assign(notional_band=notional_band(frame["notional"]), _y=np.asarray(y), _p=np.asarray(p))
    rows = []
    for col in slices:
        for level, part in f.groupby(f[col].astype("string").fillna("<NA>")):
            m = evaluate(part, part["_y"].to_numpy(), part["_p"].to_numpy(), ks, day_col)
            rows.append({"slice": col, "level": level, **{k: m[k] for k in
                         ("rows", "positives", "base_rate", "mean_score", "pr_auc", "roc_auc", "ece")}})
    return pd.DataFrame(rows)


def reference_profile(values: pd.Series, categorical: bool, bins: int = 10) -> dict:
    """Bin edges (numeric deciles, plus a missing bin) or level shares (categorical) for PSI."""
    if categorical:
        shares = values.astype("string").fillna("<NA>").value_counts(normalize=True)
        return {"kind": "categorical", "levels": shares.index.tolist(), "shares": shares.round(6).tolist()}
    v = values.astype(float).replace([np.inf, -np.inf], np.nan)
    finite = v.dropna()
    edges = np.unique(np.quantile(finite, np.linspace(0, 1, bins + 1))) if len(finite) else np.array([0.0])
    return {"kind": "numeric", "edges": edges.tolist(), "shares": profile_shares(v, edges.tolist()).round(6).tolist()}


def profile_shares(values: pd.Series, edges: list[float]) -> np.ndarray:
    """Shares per bin: interior bins from ``edges`` (open-ended at both sides) plus a final missing bin."""
    v = values.astype(float).replace([np.inf, -np.inf], np.nan).to_numpy()
    inner = np.asarray(edges[1:-1]) if len(edges) > 2 else np.asarray([], dtype=float)
    idx = np.where(np.isnan(v), len(inner) + 1, np.searchsorted(inner, np.nan_to_num(v), side="right"))
    counts = np.bincount(idx.astype(int), minlength=len(inner) + 2)
    return counts / max(len(v), 1)


def psi(reference: dict, values: pd.Series, eps: float = 1e-4) -> float:
    if reference["kind"] == "categorical":
        cur = values.astype("string").fillna("<NA>").value_counts(normalize=True)
        levels = list(dict.fromkeys(reference["levels"] + cur.index.tolist()))
        ref = pd.Series(reference["shares"], index=reference["levels"]).reindex(levels, fill_value=0).to_numpy()
        now = cur.reindex(levels, fill_value=0).to_numpy()
    else:
        ref = np.asarray(reference["shares"], dtype=float)
        now = profile_shares(values, reference["edges"])
    ref, now = np.clip(ref, eps, None), np.clip(now, eps, None)
    return float(np.sum((now - ref) * np.log(now / ref)))
