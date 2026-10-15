"""Leakage controls (plan 04): forbidden columns, as-of audit, future perturbation, single-feature screen
and label shuffle."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from .asof import AUDIT_PREFIX, cutoff_dates, knowledge_date
from .bizcal import BusinessCalendar, to_day
from .config import Settings
from .labels import LABEL_COLUMNS
from .sources import SOURCES, lag

FORBIDDEN_SOURCE_FIELDS = {"DKCount", "DTCReasonCode", "FailWho", "FailConditionCode", "FAIL_REASON", "StockLoanRate"}
FORBIDDEN_CANONICAL = {"dk_count", "reason_code", "fail_who", "fail_condition", "stock_loan_rate", "cancelled",
                       "exclusion_reason", "in_population", "match_method", "fail_rows"}
SCREEN_AUC = 0.95
STATIC_SOURCES = {"business_calendar"}


def forbidden_columns(feature_names) -> list[str]:
    banned = {c.lower() for c in FORBIDDEN_SOURCE_FIELDS | FORBIDDEN_CANONICAL | set(LABEL_COLUMNS)}
    return [n for n in feature_names if n.lower() in banned or n.lower().startswith("fail_")]


def asof_audit(frame: pd.DataFrame, cal: BusinessCalendar, settings: Settings) -> pd.DataFrame:
    """Per audit column: rows with a value, latest source date used, and rows past the allowed cutoff."""
    rows = []
    for col in [c for c in frame.columns if c.startswith(AUDIT_PREFIX)]:
        source = col[len(AUDIT_PREFIX):]
        lag_bd = settings.label_availability_lag_bd if source == "labels" else lag(source)
        allowed = cutoff_dates(frame["scoring_date"], cal, lag_bd)
        used = to_day(frame[col])
        has = used.notna()
        late = has & (used > allowed)
        margin = cal.bd_diff(used[has], allowed[has])
        rows.append({"source": source, "lag_bd": lag_bd, "rows_with_value": int(has.sum()),
                     "max_used": used.max(), "min_margin_bd": float(margin.min()) if has.any() else np.nan,
                     "violations": int(late.sum())})
    return pd.DataFrame(rows)


def perturb_future(raw: dict[str, pd.DataFrame], cutoff, seed: int = 0, mode: str = "noise") -> dict[str, pd.DataFrame]:
    """Copy of ``raw`` where every source row known after ``cutoff`` is randomized (``noise``) or removed (``drop``).

    Noise scales numeric columns and shuffles text columns among the future rows; the columns that define the
    knowledge date are kept so the rows stay in the future.
    """
    rng = np.random.default_rng(seed)
    cut = to_day(cutoff)
    out = {}
    for sid, frame in raw.items():
        if sid in STATIC_SOURCES or sid not in SOURCES or frame.empty or SOURCES[sid].asof_col not in frame.columns:
            out[sid] = frame
            continue
        future = (knowledge_date(sid, frame) > cut).to_numpy()
        if not future.any():
            out[sid] = frame
            continue
        if mode == "drop":
            out[sid] = frame.loc[~future].reset_index(drop=True)
            continue
        f = frame.copy()
        idx = np.flatnonzero(future)
        keep = {SOURCES[sid].asof_col, "AddedTimestamp"}
        for c in f.columns:
            if c in keep:
                continue
            col = f[c]
            if pd.api.types.is_bool_dtype(col):
                continue
            if pd.api.types.is_numeric_dtype(col):
                vals = col.to_numpy(dtype="float64", copy=True)
                vals[idx] = vals[idx] * rng.uniform(0.3, 3.0, len(idx)) + rng.normal(0, 1, len(idx))
                f[c] = vals.round(0) if pd.api.types.is_integer_dtype(col) and not np.isnan(vals).any() else vals
            elif not pd.api.types.is_datetime64_any_dtype(col):
                vals = col.to_numpy(dtype=object, copy=True)
                vals[idx] = rng.permutation(vals[idx])
                f[c] = vals
        out[sid] = f
    return out


def compare_features(a: pd.DataFrame, b: pd.DataFrame, features: list[str], key: str = "trade_key",
                     rtol: float = 1e-9) -> pd.DataFrame:
    """Per feature, the number of rows (matched on ``key``) whose values differ between ``a`` and ``b``."""
    a, b = a[[key] + features].copy(), b[[key] + features].copy()
    m = a.merge(b, on=key, suffixes=("_a", "_b"), how="outer", indicator=True)
    rows = [{"feature": "<rows>", "differences": int((m["_merge"] != "both").sum())}]
    both = m[m["_merge"] == "both"]
    for f in features:
        x, y = both[f"{f}_a"], both[f"{f}_b"]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            xv, yv = x.to_numpy(dtype=float), y.to_numpy(dtype=float)
            same = np.isclose(xv, yv, rtol=rtol, atol=1e-12, equal_nan=True)
        else:
            same = (x.astype("string").fillna("<NA>") == y.astype("string").fillna("<NA>")).to_numpy()
        rows.append({"feature": f, "differences": int((~same).sum())})
    return pd.DataFrame(rows)


def _score(frame: pd.DataFrame, feature: str, y: np.ndarray, categorical: bool) -> np.ndarray:
    x = frame[feature]
    if categorical:
        codes = x.astype("string").fillna("<NA>")
        rates = pd.Series(y, index=frame.index).groupby(codes).mean()
        return codes.map(rates).to_numpy(dtype=float)
    v = x.astype(float)
    return v.fillna(v.median()).to_numpy()


def single_feature_screen(frame: pd.DataFrame, features: list[str], categorical: list[str],
                          target: str = "failed_on_settle") -> pd.DataFrame:
    known = frame[frame[target].notna()]
    y = known[target].astype(int).to_numpy()
    rows = []
    for f in features:
        s = _score(known, f, y, f in categorical)
        if np.nanstd(s) == 0 or len(np.unique(y)) < 2:
            rows.append({"feature": f, "auc": np.nan, "strength": np.nan, "flagged": False})
            continue
        auc = roc_auc_score(y, s)
        strength = max(auc, 1 - auc)
        rows.append({"feature": f, "auc": auc, "strength": strength, "flagged": strength > SCREEN_AUC})
    return pd.DataFrame(rows).sort_values("strength", ascending=False, na_position="last").reset_index(drop=True)


@dataclass
class ShuffleResult:
    pr_auc: float
    base_rate: float
    roc_auc: float
    passed: bool
    detail: dict = field(default_factory=dict)


def label_shuffle_test(frame: pd.DataFrame, features: list[str], categorical: list[str],
                       target: str = "failed_on_settle", seed: int = 0, max_ratio: float = 1.5) -> ShuffleResult:
    """Train on permuted labels (time-ordered 70/30 split); holdout PR-AUC must stay near the base rate."""
    import lightgbm as lgb

    known = frame[frame[target].notna()].sort_values("settle_date").reset_index(drop=True)
    y = np.random.default_rng(seed).permutation(known[target].astype(int).to_numpy())
    X = known[features].copy()
    for c in categorical:
        X[c] = X[c].astype("category")
    dates = np.sort(known["settle_date"].unique())
    split = dates[int(len(dates) * 0.7)]
    tr, te = (known["settle_date"] < split).to_numpy(), (known["settle_date"] >= split).to_numpy()
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 50,
              "verbosity": -1, "seed": seed, "num_threads": 1}
    model = lgb.train(params, lgb.Dataset(X[tr], y[tr], categorical_feature=categorical or "auto"), num_boost_round=100)
    p = model.predict(X[te])
    base = float(y[te].mean())
    pr = float(average_precision_score(y[te], p)) if 0 < base < 1 else np.nan
    roc = float(roc_auc_score(y[te], p)) if 0 < base < 1 else np.nan
    passed = bool(pr <= max(base * max_ratio, base + 0.01))
    return ShuffleResult(pr_auc=pr, base_rate=base, roc_auc=roc, passed=passed,
                         detail={"train_rows": int(tr.sum()), "test_rows": int(te.sum()), "split": str(pd.Timestamp(split).date())})


def perturbation_test(settings: Settings, raw: dict[str, pd.DataFrame], cutoff, seed: int = 0,
                      modes: tuple[str, ...] = ("noise", "drop")) -> dict[str, pd.DataFrame]:
    """Rebuild features from perturbed copies of ``raw`` and diff rows with D <= cutoff against the original."""
    from .features.build import FEATURE_NAMES, build_feature_table

    base, _ = build_feature_table(settings, raw)
    cut = to_day(cutoff)
    keep = base[base["scoring_date"] <= cut]
    results = {}
    for mode in modes:
        other, _ = build_feature_table(settings, perturb_future(raw, cut, seed, mode))
        results[mode] = compare_features(keep, other[other["scoring_date"] <= cut], FEATURE_NAMES)
    return results


def leakage_status(screen: pd.DataFrame, perturbation: dict[str, pd.DataFrame] | None, audit: pd.DataFrame,
                   forbidden: list[str]) -> dict[str, str]:
    """One status string per feature for the catalog."""
    status = {}
    flagged = set(screen.loc[screen["flagged"], "feature"])
    changed = set()
    for diff in (perturbation or {}).values():
        changed |= set(diff.loc[(diff["differences"] > 0) & (diff["feature"] != "<rows>"), "feature"])
    audit_ok = audit["violations"].sum() == 0 if len(audit) else True
    for f in screen["feature"]:
        if f in forbidden:
            status[f] = "FORBIDDEN"
        elif f in changed:
            status[f] = "FAIL (perturbation)"
        elif not audit_ok:
            status[f] = "FAIL (as-of audit)"
        elif f in flagged:
            status[f] = "review (AUC > 0.95)"
        else:
            status[f] = "pass" if perturbation is not None else "pass (perturbation not run)"
    return status
