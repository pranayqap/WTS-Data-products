"""Shared loading of landed extracts into canonical frames, labels and features."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .bizcal import BusinessCalendar
from .canonical import canonical_fails, canonical_trades
from .config import Settings
from .labels import LabelResult, build_labels
from .profiling import calendar_from, load_all


@dataclass
class Dataset:
    raw: dict[str, pd.DataFrame]
    cal: BusinessCalendar
    trades: pd.DataFrame
    fails: pd.DataFrame


def load_dataset(settings: Settings, raw: dict[str, pd.DataFrame] | None = None) -> Dataset:
    raw = raw if raw is not None else load_all(settings.raw_dir)
    return Dataset(
        raw=raw,
        cal=calendar_from(raw),
        trades=canonical_trades(raw["beta_trades"], raw["p3_trades"], settings),
        fails=canonical_fails(raw["beta_fail_snapshots"], raw["p3_fail_snapshots"], settings),
    )


def label_dataset(ds: Dataset, settings: Settings, label_window: tuple | None = None) -> LabelResult:
    return build_labels(ds.trades, ds.fails, ds.cal, settings, label_window)


def write_processed(settings: Settings, **frames: pd.DataFrame) -> None:
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        frame.to_parquet(settings.processed_dir / f"{name}.parquet", index=False)


def read_processed(settings: Settings, name: str) -> pd.DataFrame:
    return pd.read_parquet(settings.processed_dir / f"{name}.parquet")


def is_synthetic(settings: Settings) -> bool:
    """True when the landed extracts were written by ``failmodel synth``."""
    return (settings.raw_dir / "business_calendar" / "synthetic.parquet").exists()


def load_calendar(settings: Settings) -> BusinessCalendar:
    from .profiling import load_raw

    return calendar_from({"business_calendar": load_raw("business_calendar", settings.raw_dir)})
