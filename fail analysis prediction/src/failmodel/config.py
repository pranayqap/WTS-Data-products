"""Project settings.

Values listed in ``PROVISIONAL`` are defaults that profiling (plan 02) must confirm or replace with
evidence before real-data results are reported. ``provisional_report`` lists them for run logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

MODEL_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WrapperSettings:
    base_url: str = ""
    api_key: str = ""
    bearer_token: str = ""
    database: str = "OperationsPortalDB"
    schema: str = "dbo"
    calls_per_minute: int = 90
    command_timeout_seconds: int = 300
    max_retries: int = 5

    @classmethod
    def from_env(cls) -> "WrapperSettings":
        return cls(
            base_url=os.environ.get("WSI_WRAPPER_BASE_URL", "").rstrip("/"),
            api_key=os.environ.get("WSI_WRAPPER_API_KEY", ""),
            bearer_token=os.environ.get("WSI_WRAPPER_BEARER_TOKEN", ""),
        )


@dataclass(frozen=True)
class ModelSettings:
    n_folds: int = 6
    purge_bd: int = 5
    holdout_months: int = 3
    top_k: tuple[int, ...] = (25, 50, 100)
    isotonic_min_positives: int = 500
    calibration_folds: int = 2
    num_boost_round: int = 2000
    early_stopping_rounds: int = 100
    lgbm_params: dict = field(
        default_factory=lambda: {
            "objective": "binary",
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_data_in_leaf": 100,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l2": 1.0,
            "verbosity": -1,
            "seed": 7,
            "num_threads": 1,
        }
    )
    reason_codes: int = 3


@dataclass(frozen=True)
class MonitoringSettings:
    feature_psi_alert: float = 0.25
    score_psi_alert: float = 0.2
    top_features: int = 20
    completeness_window_bd: int = 20
    completeness_band: float = 0.5
    rolling_window_bd: int = 20
    pr_auc_floor_ratio: float = 0.8
    calibration_ratio_bounds: tuple[float, float] = (0.7, 1.3)
    precision_k: int = 50
    min_rolling_positives: int = 20
    # Event-style sources whose daily row count legitimately swings; checked for presence only.
    presence_only_sources: tuple[str, ...] = ("beta_threshold_history", "reg204", "beta_id_confirms", "locates")
    # Fail lists: the row count is the outcome itself, so only the required date's snapshot must exist.
    snapshot_presence_sources: tuple[str, ...] = ("beta_fail_snapshots", "p3_fail_snapshots")
    # PSI pools the trailing scoring days: one day is ~100s of trades and many features are constant per book-day.
    drift_window_bd: int = 20
    drift_min_days: int = 5


@dataclass(frozen=True)
class PersistenceSettings:
    horizons_bd: tuple[int, ...] = (1, 3, 5)
    reg204_default_deadline_bd: int = 3
    reg204_short_sale_deadline_bd: int = 1
    muni_closeout_bd: int = 10
    age_buckets: tuple[tuple[int, int], ...] = ((1, 1), (2, 5), (6, 30), (31, 100000))
    tasks: tuple[str, ...] = ("still_open_h1", "still_open_h3", "still_open_h5", "reg204_breach", "muni_breach")
    # Cure-history rates look back this many business days and shrink toward the book-direction rate.
    history_window_bd: int = 250
    # The age-only benchmark pools ages above this cap.
    age_cap_bd: int = 30
    # A task and direction is trained only with at least this many positives and negatives.
    min_class_rows: int = 50


@dataclass(frozen=True)
class Settings:
    data_dir: Path = MODEL_ROOT / "data"
    artifacts_dir: Path = MODEL_ROOT / "artifacts"

    label_start: date = date(2024, 5, 28)
    extract_start: date = date(2023, 5, 1)

    # PROVISIONAL (P2): business days between S and the Beta snapshot that shows S's Age-1 fails.
    beta_snapshot_offset_bd: int = 0
    # PROVISIONAL (P2): business days after S before S's label is known at scoring time.
    label_availability_lag_bd: int = 1
    # PROVISIONAL (P6): CNS S1 cycle treated as end of day; None keeps the last cycle per date.
    cns_eod_cycle: str | None = None
    # PROVISIONAL (P5): how duplicate P3 trade rows per trade key are resolved.
    p3_dedup_rule: str = "latest_seq_nbr"
    # PROVISIONAL (P5): P3 RECORD_TYPE codes that come from trades; None treats every fail row as trade-originated.
    p3_trade_record_types: tuple[str, ...] | None = None
    # PROVISIONAL (P6): direction maps (firm side), confirmed against fail-record signs.
    beta_direction_map: dict = field(default_factory=lambda: {"P": "D", "S": "R"})
    p3_direction_map: dict = field(default_factory=lambda: {"B": "D", "S": "R"})
    # PROVISIONAL (P6): trade-level DVP codes standing in for current-state account flags.
    beta_dvp_codes: tuple[str, ...] = ("5",)
    p3_dvp_codes: tuple[str, ...] = ("V",)

    max_staleness_bd: int = 3
    late_day_hour: int = 15
    history_windows_bd: tuple[int, ...] = (20, 60, 250)
    history_prior_strength: float = 20.0
    aged_fail_min_age_bd: int = 5

    model: ModelSettings = field(default_factory=ModelSettings)
    monitoring: MonitoringSettings = field(default_factory=MonitoringSettings)
    persistence: PersistenceSettings = field(default_factory=PersistenceSettings)
    wrapper: WrapperSettings = field(default_factory=WrapperSettings.from_env)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def scores_dir(self) -> Path:
        return self.artifacts_dir / "scores"

    @property
    def monitoring_dir(self) -> Path:
        return self.artifacts_dir / "monitoring"

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)


PROVISIONAL = {
    "beta_snapshot_offset_bd": "P2",
    "label_availability_lag_bd": "P2",
    "cns_eod_cycle": "P6",
    "p3_dedup_rule": "P5",
    "p3_trade_record_types": "P5",
    "beta_direction_map": "P6",
    "p3_direction_map": "P6",
    "beta_dvp_codes": "P6",
    "p3_dvp_codes": "P6",
}


def provisional_report(settings: Settings) -> list[str]:
    return [f"{name} = {getattr(settings, name)!r} (confirm in {check})" for name, check in PROVISIONAL.items()]
