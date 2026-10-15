from datetime import date

import pandas as pd
import pytest

from failmodel.config import ModelSettings, Settings
from failmodel.sources import SOURCES
from failmodel.synthetic import SyntheticSpec, generate

SPEC = SyntheticSpec(start="2024-01-02", end="2024-12-31", n_cusips=120, trades_per_day=70, seed=5)


@pytest.fixture(scope="session")
def raw_frames():
    frames = generate(SPEC)
    return {sid: frames.get(sid, pd.DataFrame()) for sid in SOURCES}


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    root = tmp_path_factory.mktemp("failmodel")
    return Settings(
        data_dir=root / "data",
        artifacts_dir=root / "artifacts",
        label_start=date(2024, 5, 28),
        extract_start=date(2024, 1, 2),
        p3_trade_record_types=("F",),
        model=ModelSettings(n_folds=2, holdout_months=1, num_boost_round=300, early_stopping_rounds=30,
                            isotonic_min_positives=500),
    )


@pytest.fixture(scope="session")
def feature_table(settings, raw_frames):
    from failmodel.features.build import build_feature_table

    return build_feature_table(settings, raw_frames)


@pytest.fixture(scope="session")
def trained(feature_table, settings):
    from failmodel.models.train import train_all

    frame, ctx = feature_table
    return train_all(frame, ctx.cal, settings, log=lambda *_: None), frame, ctx
