"""Daily shadow scoring (plan 06): population for S = next business day after D, completeness gate,
point-in-time features, calibrated scores with reason codes, and the run log.

Extracts are truncated to what is known on the last scoring date, so a backfill sees no later data. Within a
backfill one context serves every date; the future-perturbation test (plan 04) is what makes the features
for each D identical to a run on D alone.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .bizcal import BusinessCalendar, to_day
from .config import Settings
from .features.build import FEATURE_NAMES, build_context, build_features
from .leakage import perturb_future
from .monitoring import completeness, degraded_sources, mask_degraded
from .pipeline import load_dataset
from .profiling import load_all

ID_OUT = ["scoring_date", "settle_date", "book", "direction", "trade_key", "unique_ref", "account", "cusip9",
          "product_group", "counterparty_type", "quantity", "notional"]
REASON_OUT = [f"reason_{i}{suffix}" for i in (1, 2, 3) for suffix in ("", "_text", "_contrib")]
SCORE_COLUMNS = (["model_version", "run_id", "run_timestamp"] + ID_OUT
                 + ["fail_probability", "raw_score", "risk_rank"] + REASON_OUT + ["degraded_sources", "shadow_mode"])
SNAPSHOT_COLUMNS = ID_OUT + [f for f in FEATURE_NAMES if f not in ID_OUT]


def truncate_to(raw: dict[str, pd.DataFrame], day) -> dict[str, pd.DataFrame]:
    """Drop every source row known after ``day`` (a no-op on a live extract)."""
    return perturb_future(raw, day, mode="drop")


@dataclass
class DayRun:
    scoring_date: pd.Timestamp
    settle_date: pd.Timestamp
    scores: pd.DataFrame
    features: pd.DataFrame
    completeness: pd.DataFrame
    degraded: list[str]
    run: dict = field(default_factory=dict)


def score_one(day, ctx, raw: dict, cal: BusinessCalendar, settings: Settings, bundle, shadow: bool = True) -> DayRun:
    d = to_day(day)
    s = cal.next_business_day(d)
    table = completeness(raw, cal, d, settings)
    degraded = sorted(degraded_sources(table))
    u = ctx.universe
    base = u[u["in_population"] & u["settle_date"].eq(s)]
    run_id = uuid.uuid4().hex[:12]
    stamp = datetime.now().isoformat(timespec="seconds")
    if base.empty:
        empty = pd.DataFrame(columns=SCORE_COLUMNS)
        return DayRun(d, s, empty, pd.DataFrame(columns=SNAPSHOT_COLUMNS), table, degraded,
                      {"run_id": run_id, "rows": 0})
    feats = build_features(base, ctx)
    masked = mask_degraded(feats, set(degraded))
    scored = bundle.score(feats)
    out = feats[ID_OUT].join(scored)
    out.insert(0, "run_timestamp", stamp)
    out.insert(0, "run_id", run_id)
    out.insert(0, "model_version", bundle.version)
    out["degraded_sources"] = ",".join(degraded) if degraded else None
    out["shadow_mode"] = shadow
    counts = out.groupby(["book", "direction"]).size()
    run = {"run_id": run_id, "run_timestamp": stamp, "model_version": bundle.version, "scoring_date": str(d.date()),
           "settle_date": str(s.date()), "rows": int(len(out)),
           "rows_by_book_direction": {f"{b}{r}": int(n) for (b, r), n in counts.items()},
           "degraded_sources": degraded, "masked_features": masked, "shadow_mode": shadow}
    return DayRun(d, s, out[SCORE_COLUMNS], feats[SNAPSHOT_COLUMNS], table, degraded, run)


def score_dates(settings: Settings, dates, bundle, raw: dict | None = None, log=print) -> list[DayRun]:
    raw = raw if raw is not None else load_all(settings.raw_dir)
    days = sorted(to_day(pd.Series(list(dates))))
    known = truncate_to(raw, days[-1])
    ds = load_dataset(settings, known)
    ctx, _ = build_context(ds, settings)
    runs = []
    for d in days:
        run = score_one(d, ctx, known, ds.cal, settings, bundle)
        write_day(settings, run)
        log(f"{d.date()} -> S {run.settle_date.date()}: {run.run.get('rows', 0)} trades scored; "
            f"degraded {run.degraded or 'none'}")
        runs.append(run)
    return runs


def day_dir(settings: Settings, day):
    return settings.scores_dir / f"score_date={to_day(day):%Y-%m-%d}"


def write_day(settings: Settings, run: DayRun) -> None:
    path = day_dir(settings, run.scoring_date)
    path.mkdir(parents=True, exist_ok=True)
    run.scores.to_parquet(path / "scores.parquet", index=False)
    run.features.to_parquet(path / "features.parquet", index=False)
    run.completeness.to_csv(path / "completeness.csv", index=False)
    (path / "run.json").write_text(json.dumps(run.run, indent=2, default=str))


def read_scores(settings: Settings, through=None) -> pd.DataFrame:
    parts = []
    for p in sorted(settings.scores_dir.glob("score_date=*/scores.parquet")) if settings.scores_dir.exists() else []:
        day = pd.Timestamp(p.parent.name.split("=", 1)[1])
        if through is None or day <= to_day(through):
            parts.append(pd.read_parquet(p))
    if not parts:
        return pd.DataFrame(columns=SCORE_COLUMNS)
    out = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else parts[0]
    for c in ("scoring_date", "settle_date"):
        out[c] = to_day(out[c])
    return out


def read_day(settings: Settings, day, name: str) -> pd.DataFrame | None:
    path = day_dir(settings, day) / name
    if not path.exists():
        return None
    return pd.read_csv(path) if name.endswith(".csv") else pd.read_parquet(path)
