"""Rolling-origin folds by settle date with a purge gap, and the out-of-time holdout (plan 05)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..bizcal import BusinessCalendar


@dataclass(frozen=True)
class Fold:
    name: str
    train_end: pd.Timestamp
    valid_start: pd.Timestamp
    valid_end: pd.Timestamp

    def train_mask(self, settle: pd.Series) -> pd.Series:
        return settle <= self.train_end

    def valid_mask(self, settle: pd.Series) -> pd.Series:
        return (settle >= self.valid_start) & (settle <= self.valid_end)


def _purged_end(cal: BusinessCalendar, start: pd.Timestamp, purge_bd: int) -> pd.Timestamp:
    """Last settle date allowed in training so that ``purge_bd`` business days separate it from ``start``."""
    first = cal.days[cal.days >= start][0]
    return cal.add(pd.Series([first]), -(purge_bd + 1)).iloc[0]


def holdout_fold(settle: pd.Series, cal: BusinessCalendar, months: int, purge_bd: int) -> Fold:
    last = settle.max()
    start = (last.to_period("M") - (months - 1)).start_time
    return Fold("holdout", _purged_end(cal, start, purge_bd), start, last)


def rolling_folds(settle: pd.Series, cal: BusinessCalendar, n_folds: int, purge_bd: int, before: pd.Timestamp) -> list[Fold]:
    """The last ``n_folds`` calendar months before ``before``; each trains on everything up to its purge."""
    dev = settle[settle < before]
    months = sorted(dev.dt.to_period("M").unique())[-n_folds:]
    folds = []
    for m in months:
        start, end = m.start_time, min(m.end_time.normalize(), dev.max())
        folds.append(Fold(str(m), _purged_end(cal, start, purge_bd), start, end))
    return folds


def fold_table(folds: list[Fold], settle: pd.Series, y: pd.Series) -> pd.DataFrame:
    rows = []
    for f in folds:
        tr, va = f.train_mask(settle), f.valid_mask(settle)
        rows.append({"fold": f.name, "train_end": f.train_end.date(), "valid_start": f.valid_start.date(),
                     "valid_end": f.valid_end.date(), "train_rows": int(tr.sum()), "train_positives": int(y[tr].sum()),
                     "valid_rows": int(va.sum()), "valid_positives": int(y[va].sum())})
    return pd.DataFrame(rows)
