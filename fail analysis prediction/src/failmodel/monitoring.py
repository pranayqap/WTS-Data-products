"""Monitoring (plan 06): source completeness, feature and score drift (PSI), realized performance and
calibration, with alert thresholds from ``settings.monitoring``."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from .asof import knowledge_date
from .bizcal import BusinessCalendar, to_day
from .config import Settings
from .features.build import FEATURE_CATALOG, FEATURE_NAMES
from .models.metrics import psi
from .sources import SOURCES, active_sources, lag

TRADE_SOURCES = {"beta_trades", "p3_trades"}
EVENT_SOURCES = {"beta_threshold_history"}


@dataclass
class Alert:
    date: str
    signal: str
    scope: str
    value: float
    threshold: str
    detail: str
    severity: str = "warning"


def feature_sources() -> dict[str, set[str]]:
    """Source ids each feature reads (from the catalog's source field)."""
    out = {}
    for s in FEATURE_CATALOG:
        out[s.name] = {tok for tok in re.split(r"[\s,/]+", s.source) if tok in SOURCES}
    return out


def features_for_sources(sources: set[str]) -> list[str]:
    return [f for f, used in feature_sources().items() if used & sources]


def mask_degraded(features: pd.DataFrame, degraded: set[str]) -> list[str]:
    """Set the features of each degraded source to missing on that source's books only (in place)."""
    masked = set()
    for sid in sorted(degraded):
        cols = features_for_sources({sid})
        rows = features["book"].isin(SOURCES[sid].books)
        if cols and rows.any():
            features.loc[rows, cols] = np.nan
            masked.update(cols)
    return sorted(masked)


def source_role(sid: str) -> str:
    """``trade`` (no population without it), ``feature`` (feeds features) or ``reconciliation`` (neither)."""
    if sid in TRADE_SOURCES or sid == "business_calendar":
        return "trade"
    if any(sid in used for used in feature_sources().values()):
        return "feature"
    return "reconciliation"


def completeness(raw: dict[str, pd.DataFrame], cal: BusinessCalendar, scoring_date, settings: Settings) -> pd.DataFrame:
    """Rows on each source's required as-of date (D - lag) against its trailing median row count."""
    m = settings.monitoring
    d = to_day(scoring_date)
    rows = []
    for src in active_sources():
        sid = src.id
        if sid == "business_calendar":
            ok = bool(cal.is_business_day(pd.Series([d]))[0])
            rows.append({"source": sid, "required_date": d, "rows": int(ok), "median_rows": 1.0, "ratio": float(ok),
                         "status": "ok" if ok else "missing"})
            continue
        required = cal.add(pd.Series([d]), -lag(sid)).iloc[0]
        frame = raw.get(sid, pd.DataFrame())
        if frame is None or frame.empty or src.asof_col not in frame.columns:
            rows.append({"source": sid, "required_date": required, "rows": 0, "median_rows": np.nan, "ratio": np.nan,
                         "status": "missing"})
            continue
        counts = knowledge_date(sid, frame).value_counts()
        pos = int(cal.index_on_or_before(pd.Series([required]))[0])
        window = cal.days[max(pos - m.completeness_window_bd, 0):pos]
        median = float(counts.reindex(window, fill_value=0).median()) if len(window) else np.nan
        n = int(counts.get(required, 0))
        if sid in EVENT_SOURCES:
            status = "ok"
        elif sid in m.presence_only_sources:
            recent = cal.days[max(pos - settings.max_staleness_bd, 0):pos + 1]
            status = "ok" if counts.reindex(recent, fill_value=0).sum() > 0 else "missing"
        elif n == 0:
            status = "missing"
        elif sid in m.snapshot_presence_sources:
            status = "ok"
        elif median > 0 and abs(n / median - 1) > m.completeness_band:
            status = "out_of_band"
        else:
            status = "ok"
        rows.append({"source": sid, "required_date": required, "rows": n, "median_rows": median,
                     "ratio": n / median if median else np.nan, "status": status})
    out = pd.DataFrame(rows)
    out["role"] = out["source"].map(source_role)
    return out


def degraded_sources(table: pd.DataFrame) -> set[str]:
    """Feature sources that failed the gate; their features are masked to missing for the run."""
    return set(table.loc[table["status"].ne("ok") & table["role"].eq("feature"), "source"])


def feature_drift(features: pd.DataFrame, bundle, settings: Settings) -> pd.DataFrame:
    rows = []
    for direction, model in bundle.directions.items():
        part = features[features["direction"].eq(direction)]
        if part.empty:
            continue
        top = set(bundle_top_features(bundle, direction, settings.monitoring.top_features))
        for f in FEATURE_NAMES:
            value = psi(model.reference["features"][f], part[f])
            rows.append({"direction": direction, "feature": f, "psi": value, "top_feature": f in top,
                         "alert": bool(f in top and value > settings.monitoring.feature_psi_alert)})
    return pd.DataFrame(rows)


def score_drift(scores: pd.DataFrame, bundle) -> pd.DataFrame:
    rows = []
    for direction, model in bundle.directions.items():
        part = scores[scores["direction"].eq(direction)]
        if len(part):
            rows.append({"direction": direction, "psi": psi(model.reference["score"], part["fail_probability"]),
                         "mean_score": float(part["fail_probability"].mean()), "rows": int(len(part))})
    return pd.DataFrame(rows)


def bundle_top_features(bundle, direction: str, n: int) -> list[str]:
    metrics = json.loads((bundle.path / "metrics.json").read_text())
    return [r["feature"] for r in metrics[direction]["importance"][:n]]


def _precision_at(group: pd.DataFrame, k: int) -> float:
    top = group.nlargest(min(k, len(group)), "fail_probability")
    return float(top["y"].mean()) if len(top) else np.nan


def realized_daily(scores: pd.DataFrame, labels: pd.DataFrame, k: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per (scoring date, direction): scored rows, labelled rows, fails, observed vs predicted rate, precision@k.
    Also returns the scores joined to their labels (``y`` missing until the label is known)."""
    lab = labels[["trade_key", "failed_on_settle"]].drop_duplicates("trade_key")
    j = scores.merge(lab, on="trade_key", how="left")
    j["y"] = j["failed_on_settle"].astype("Float64")
    rows = []
    for (day, direction), g in j.groupby(["scoring_date", "direction"]):
        known = g[g["y"].notna()].assign(y=lambda x: x["y"].astype(int))
        both = known["y"].nunique() == 2
        rows.append({
            "scoring_date": day, "direction": direction, "scored": int(len(g)), "labelled": int(len(known)),
            "unlabelled": int(len(g) - len(known)), "fails": int(known["y"].sum()),
            "observed_rate": float(known["y"].mean()) if len(known) else np.nan,
            "mean_predicted": float(known["fail_probability"].mean()) if len(known) else np.nan,
            f"precision_at_{k}": _precision_at(known, k),
            "pr_auc": float(average_precision_score(known["y"], known["fail_probability"])) if both else np.nan,
        })
    return pd.DataFrame(rows), j


def rolling_performance(joined: pd.DataFrame, daily: pd.DataFrame, as_of, bundle, settings: Settings) -> pd.DataFrame:
    """Pooled metrics over the last ``rolling_window_bd`` evaluated scoring dates, against the holdout."""
    m = settings.monitoring
    rows = []
    for direction, model in bundle.directions.items():
        dd = daily[daily["direction"].eq(direction)].sort_values("scoring_date")
        days = dd["scoring_date"].tail(m.rolling_window_bd)
        part = joined[joined["direction"].eq(direction) & joined["scoring_date"].isin(days) & joined["y"].notna()]
        y = part["y"].astype(int).to_numpy()
        p = part["fail_probability"].to_numpy()
        both = 0 < y.sum() < len(y)
        hold = model.reference["holdout"]
        ratio = float(p.sum() / y.sum()) if y.sum() else np.nan
        rows.append({
            "as_of": to_day(as_of), "direction": direction, "days": int(len(days)), "rows": int(len(y)),
            "fails": int(y.sum()), "rolling_pr_auc": float(average_precision_score(y, p)) if both else np.nan,
            "holdout_pr_auc": hold["pr_auc"], f"rolling_precision_at_{m.precision_k}": float(dd[f"precision_at_{m.precision_k}"].tail(m.rolling_window_bd).mean()) if len(dd) else np.nan,
            f"holdout_precision_at_{m.precision_k}": hold.get(f"precision_at_{m.precision_k}", np.nan),
            "calibration_ratio": ratio,
        })
    return pd.DataFrame(rows)


def alerts_for(as_of, table: pd.DataFrame | None, fdrift: pd.DataFrame | None, sdrift: pd.DataFrame | None,
               rolling: pd.DataFrame | None, settings: Settings) -> list[Alert]:
    m = settings.monitoring
    day = str(to_day(as_of).date())
    out: list[Alert] = []
    if table is not None:
        for r in table[table["status"].ne("ok") & table["role"].ne("reconciliation")].itertuples():
            sev = "critical" if r.role == "trade" else "warning"
            out.append(Alert(day, "completeness", r.source, float(r.rows), f"within ±{m.completeness_band:.0%} of 20-day median",
                             f"{r.status}: {r.rows} rows on {pd.Timestamp(r.required_date).date()} (median {r.median_rows})", sev))
    if fdrift is not None and len(fdrift):
        for r in fdrift[fdrift["alert"]].itertuples():
            out.append(Alert(day, "feature_drift", f"{r.direction}:{r.feature}", float(r.psi), f"PSI <= {m.feature_psi_alert}",
                             f"top feature vs training reference, {getattr(r, 'window_days', 1)} scoring days pooled"))
    if sdrift is not None and len(sdrift):
        for r in sdrift[sdrift["psi"] > m.score_psi_alert].itertuples():
            out.append(Alert(day, "score_drift", r.direction, float(r.psi), f"PSI <= {m.score_psi_alert}",
                             f"mean score {r.mean_score:.4f}, {getattr(r, 'window_days', 1)} scoring days pooled"))
    if rolling is not None and len(rolling):
        lo, hi = m.calibration_ratio_bounds
        for r in rolling.itertuples():
            if r.fails < m.min_rolling_positives:
                continue
            if not np.isnan(r.rolling_pr_auc) and r.rolling_pr_auc < m.pr_auc_floor_ratio * r.holdout_pr_auc:
                out.append(Alert(day, "performance", r.direction, float(r.rolling_pr_auc),
                                 f">= {m.pr_auc_floor_ratio:.0%} of holdout {r.holdout_pr_auc:.3f}",
                                 f"rolling {r.days}-day PR-AUC", "critical"))
            if not np.isnan(r.calibration_ratio) and not lo <= r.calibration_ratio <= hi:
                out.append(Alert(day, "calibration", r.direction, float(r.calibration_ratio), f"{lo} to {hi}",
                                 f"predicted / observed fails over {r.days} days"))
    return out


def alerts_frame(alerts: list[Alert]) -> pd.DataFrame:
    cols = list(Alert.__dataclass_fields__)
    return pd.DataFrame([asdict(a) for a in alerts], columns=cols)


@dataclass
class MonitorResult:
    daily: pd.DataFrame
    rolling: pd.DataFrame
    alerts: pd.DataFrame
    feature_drift: pd.DataFrame
    score_drift: pd.DataFrame


def drift_window(scored_days: list, day, settings: Settings) -> list:
    """Stored scoring days pooled for PSI on ``day``: the trailing ``drift_window_bd`` up to and including it."""
    return [x for x in scored_days if x <= day][-settings.monitoring.drift_window_bd:]


def monitor_dates(settings: Settings, dates, bundle, raw: dict | None = None, log=print) -> MonitorResult:
    """Run the monitor for each date: drift over the trailing scoring days, realized labels for every scored S
    known by then (S <= date - 1 business day), rolling performance and alerts. Writes ``artifacts/monitoring/``."""
    from .pipeline import label_dataset, load_dataset
    from .profiling import load_all
    from .scoring import read_day, read_scores, truncate_to

    raw = raw if raw is not None else load_all(settings.raw_dir)
    days = sorted(to_day(pd.Series(list(dates))))
    scores = read_scores(settings, through=days[-1])
    if scores.empty:
        raise FileNotFoundError("no stored scores; run `failmodel score` first")
    ds = load_dataset(settings, truncate_to(raw, days[-1]))
    cal = ds.cal
    labels = label_dataset(ds, settings, (scores["settle_date"].min(), scores["settle_date"].max())).labels
    k = settings.monitoring.precision_k
    scored_days = sorted(scores["scoring_date"].unique())
    stored = {x: read_day(settings, x, "features.parquet") for x in scored_days}
    all_alerts, rolling_rows, fdrifts, sdrifts = [], [], [], []
    daily = pd.DataFrame()
    for d in days:
        evaluable = scores[scores["settle_date"] <= cal.add(pd.Series([d]), -1).iloc[0]]
        known = labels[labels["settle_date"] <= cal.add(pd.Series([d]), -1).iloc[0]]
        today_scores = scores[scores["scoring_date"].eq(d)]
        table = read_day(settings, d, "completeness.csv")
        window = drift_window(scored_days, d, settings)
        fd = sd = pd.DataFrame()
        if len(window) >= settings.monitoring.drift_min_days:
            parts = [stored[x] for x in window if stored[x] is not None and len(stored[x])]
            if parts:
                fd = feature_drift(pd.concat(parts, ignore_index=True), bundle, settings).assign(window_days=len(window))
            win_scores = scores[scores["scoring_date"].isin(window)]
            if len(win_scores):
                sd = score_drift(win_scores, bundle).assign(window_days=len(window))
        roll = pd.DataFrame()
        if len(evaluable):
            daily, joined = realized_daily(evaluable, known, k)
            roll = rolling_performance(joined, daily, d, bundle, settings)
            rolling_rows.append(roll)
        alerts = alerts_for(d, table, fd, sd, roll, settings)
        all_alerts += alerts
        if len(fd):
            fdrifts.append(fd.assign(as_of=d))
        if len(sd):
            sdrifts.append(sd.assign(as_of=d))
        log(f"{d.date()}: {len(today_scores)} scores, {len(evaluable)} evaluable, {len(alerts)} alerts")
    result = MonitorResult(daily=daily, rolling=pd.concat(rolling_rows, ignore_index=True) if rolling_rows else pd.DataFrame(),
                           alerts=alerts_frame(all_alerts),
                           feature_drift=pd.concat(fdrifts, ignore_index=True) if fdrifts else pd.DataFrame(),
                           score_drift=pd.concat(sdrifts, ignore_index=True) if sdrifts else pd.DataFrame())
    write_monitoring(settings, result)
    return result


def _upsert(path, frame: pd.DataFrame, keys: list[str]) -> None:
    if frame is None or frame.empty:
        return
    if path.exists():
        old = pd.read_csv(path)
        for k in keys:
            if k in ("as_of", "scoring_date", "date"):
                old[k] = pd.to_datetime(old[k])
                frame = frame.assign(**{k: pd.to_datetime(frame[k])})
        frame = pd.concat([old, frame], ignore_index=True).drop_duplicates(keys, keep="last")
    frame.to_csv(path, index=False)


def write_monitoring(settings: Settings, result: MonitorResult) -> None:
    out = settings.monitoring_dir
    out.mkdir(parents=True, exist_ok=True)
    _upsert(out / "performance_daily.csv", result.daily, ["scoring_date", "direction"])
    _upsert(out / "rolling.csv", result.rolling, ["as_of", "direction"])
    _upsert(out / "alerts.csv", result.alerts, ["date", "signal", "scope"])
    _upsert(out / "score_drift.csv", result.score_drift, ["as_of", "direction"])
    if len(result.feature_drift):
        top = result.feature_drift[result.feature_drift["top_feature"]]
        _upsert(out / "feature_drift_top.csv", top, ["as_of", "direction", "feature"])


def shadow_summary(settings: Settings, bundle) -> dict:
    """Go-live criteria status from the monitoring log (plan 06)."""
    out = settings.monitoring_dir
    read = lambda name: pd.read_csv(out / name) if (out / name).exists() else pd.DataFrame()
    rolling, alerts, daily = read("rolling.csv"), read("alerts.csv"), read("performance_daily.csv")
    scored = len(list(settings.scores_dir.glob("score_date=*/scores.parquet"))) if settings.scores_dir.exists() else 0
    evaluated = int(daily["scoring_date"].nunique()) if len(daily) else 0
    critical = alerts[alerts["severity"].eq("critical")] if len(alerts) else alerts
    open_critical = critical[critical["date"].eq(alerts["date"].max())] if len(critical) else critical
    latest = rolling.sort_values("as_of").groupby("direction").tail(1) if len(rolling) else rolling
    k = settings.monitoring.precision_k
    floor = settings.monitoring.pr_auc_floor_ratio
    checks = {"shadow_days_scored": scored, "shadow_days_evaluated": evaluated, "shadow_days_ok": evaluated >= 30,
              "critical_alerts_total": int(len(critical)), "critical_alerts_open": int(len(open_critical)),
              "no_open_critical_ok": len(open_critical) == 0}
    for r in latest.itertuples() if len(latest) else []:
        pr_ratio = r.rolling_pr_auc / r.holdout_pr_auc if r.holdout_pr_auc else np.nan
        p_ratio = getattr(r, f"rolling_precision_at_{k}") / getattr(r, f"holdout_precision_at_{k}")
        checks[f"{r.direction}_pr_auc_ratio"] = float(pr_ratio)
        checks[f"{r.direction}_pr_auc_ok"] = bool(pr_ratio >= floor)
        checks[f"{r.direction}_precision_at_{k}_ratio"] = float(p_ratio)
        checks[f"{r.direction}_precision_at_{k}_ok"] = bool(p_ratio >= floor)
        checks[f"{r.direction}_calibration_ratio"] = float(r.calibration_ratio)
        checks[f"info_{r.direction}_review_outperformance"] = bool(max(pr_ratio, p_ratio) > 2 - floor)
    return checks
