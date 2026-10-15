"""Point-in-time joins (plan 04 as-of contract).

A row scored on D may only use source rows whose knowledge date is on or before D minus the source's
availability lag. Snapshot values older than ``max_staleness_bd`` business days at that cutoff are dropped.
Every join records the source date it used in an ``_asof_<source>`` column for the as-of audit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .bizcal import BusinessCalendar, to_day
from .sources import SOURCES

AUDIT_PREFIX = "_asof_"


def knowledge_date(source_id: str, frame: pd.DataFrame) -> pd.Series:
    """Date from which a source row may be used. Threshold history becomes known when it is recorded."""
    known = to_day(frame[SOURCES[source_id].asof_col])
    if source_id == "beta_threshold_history" and "AddedTimestamp" in frame.columns:
        added = to_day(pd.to_datetime(frame["AddedTimestamp"].astype("string").str[:10], errors="coerce"))
        known = known.where(added.isna() | (added <= known), added)
    return known


def cutoff_dates(scoring_dates: pd.Series, cal: BusinessCalendar, lag_bd) -> pd.Series:
    return cal.add(scoring_dates, -np.asarray(lag_bd))


def _as_key(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        out[c] = out[c].astype("string").fillna("<NA>").astype(object)
    return out


def asof_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    by: list[str],
    right_date: str,
    cutoff: pd.Series,
    cal: BusinessCalendar,
    max_staleness_bd: int | None,
    audit_name: str,
) -> pd.DataFrame:
    """Latest ``right`` row per ``by`` key with ``right_date`` <= ``cutoff``; result is aligned to ``left``.

    ``right`` must hold at most one row per (``by``, ``right_date``). Values from rows staler than
    ``max_staleness_bd`` business days at the cutoff are set to missing.
    """
    value_cols = [c for c in right.columns if c not in by and c != right_date]
    audit = AUDIT_PREFIX + audit_name
    if right.empty or left.empty:
        out = pd.DataFrame({c: np.full(len(left), np.nan) for c in value_cols}, index=left.index)
        out[audit] = pd.Series(pd.NaT, index=left.index, dtype="datetime64[ns]")
        return out
    q = _as_key(left[by], by)
    q["_cut"] = to_day(pd.Series(np.asarray(cutoff), index=left.index))
    q["_row"] = np.arange(len(q))
    q = q.dropna(subset=["_cut"]).sort_values("_cut")
    r = _as_key(right.rename(columns={right_date: "_rdate"}), by)
    r["_rdate"] = to_day(r["_rdate"])
    r = r.dropna(subset=["_rdate"]).sort_values("_rdate")
    m = pd.merge_asof(q, r, left_on="_cut", right_on="_rdate", by=by, direction="backward")
    if max_staleness_bd is not None:
        stale = cal.bd_diff(m["_rdate"], m["_cut"]).to_numpy() > max_staleness_bd
        m["_rdate"] = m["_rdate"].mask(stale)
        for c in value_cols:
            m[c] = m[c].mask(stale)
    aligned = m.set_index("_row").reindex(pd.RangeIndex(len(left)))
    out = pd.DataFrame({c: aligned[c].to_numpy() for c in value_cols}, index=left.index)
    out[audit] = pd.Series(aligned["_rdate"].to_numpy(), index=left.index).astype("datetime64[ns]")
    return out


def reference_dates(cutoff: pd.Series, present: pd.Series, cal: BusinessCalendar, max_staleness_bd: int | None) -> pd.Series:
    """Latest date in ``present`` on or before each cutoff, or NaT if none within the staleness limit."""
    days = np.sort(to_day(pd.Series(present)).dropna().unique()).astype("datetime64[ns]")
    cut = to_day(pd.Series(cutoff))
    ref = pd.Series(pd.NaT, index=cut.index, dtype="datetime64[ns]")
    if not len(days):
        return ref
    pos = np.searchsorted(days, cut.to_numpy(dtype="datetime64[ns]"), side="right") - 1
    ok = (pos >= 0) & cut.notna().to_numpy()
    ref[ok] = days[pos[ok]]
    if max_staleness_bd is None:
        return ref
    return ref.where(cal.bd_diff(ref, cut).to_numpy() <= max_staleness_bd)


def pinned_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    by: list[str],
    right_date: str,
    cutoff: pd.Series,
    cal: BusinessCalendar,
    max_staleness_bd: int | None,
    audit_name: str,
    fill: float = 0.0,
) -> pd.DataFrame:
    """Join on the latest ``right`` snapshot date on or before the cutoff, for sources where a key's
    absence from a snapshot means zero. Values are ``fill`` when the snapshot exists without the key and
    missing when no fresh snapshot exists."""
    value_cols = [c for c in right.columns if c not in by and c != right_date]
    audit = AUDIT_PREFIX + audit_name
    r = right.rename(columns={right_date: "_rdate"})
    r["_rdate"] = to_day(r["_rdate"])
    ref = reference_dates(pd.Series(np.asarray(cutoff), index=left.index), r["_rdate"], cal, max_staleness_bd)
    q = _as_key(left[by], by).assign(_rdate=ref.to_numpy())
    m = q.merge(_as_key(r, by), on=by + ["_rdate"], how="left")
    has = ref.notna().to_numpy()
    out = pd.DataFrame({c: np.where(has, m[c].astype(float).fillna(fill).to_numpy(), np.nan) for c in value_cols},
                       index=left.index)
    out[audit] = pd.Series(ref.to_numpy(), index=left.index).astype("datetime64[ns]")
    return out


def latest_per_key(frame: pd.DataFrame, keys: list[str], order: str) -> pd.DataFrame:
    return frame.sort_values(order).drop_duplicates(keys, keep="last")
