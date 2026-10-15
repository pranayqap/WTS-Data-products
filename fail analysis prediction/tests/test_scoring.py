import dataclasses
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from failmodel.asof import knowledge_date
from failmodel.features.build import CATEGORICAL, FEATURE_NAMES
from failmodel.models.bundle import load_bundle, save_bundle
from failmodel.monitoring import (alerts_for, bundle_top_features, completeness, degraded_sources, feature_drift,
                                  mask_degraded, monitor_dates, realized_daily, rolling_performance, shadow_summary)
from failmodel.scoring import SCORE_COLUMNS, read_scores, score_dates
from failmodel.sources import lag

D = pd.Timestamp("2024-11-14")


@pytest.fixture(scope="module")
def bundle(trained, settings, tmp_path_factory):
    results, _, _ = trained
    return load_bundle(save_bundle(results, settings, "test-shadow", root=tmp_path_factory.mktemp("bundle")))


def _thin(raw, sid, cal, keep):
    """Keep ``keep`` rows (fraction if < 1) of ``sid`` on its required date for D."""
    frame = raw[sid]
    on_day = knowledge_date(sid, frame).eq(cal.add(pd.Series([D]), -lag(sid)).iloc[0]).to_numpy()
    idx = np.flatnonzero(on_day)
    n = int(round(len(idx) * keep)) if keep < 1 else int(keep)
    drop = idx[n:]
    return frame.drop(frame.index[drop])


def test_completeness_statuses_and_roles(raw_frames, feature_table, settings):
    cal = feature_table[1].cal
    base = completeness(raw_frames, cal, D, settings).set_index("source")
    assert base.loc["p3_rpe_fails", "role"] == "reconciliation"
    assert (base.loc[base["role"].ne("reconciliation"), "status"] == "ok").all()

    raw = dict(raw_frames)
    raw["beta_segregation"] = _thin(raw, "beta_segregation", cal, 0)
    raw["loanet_positions"] = _thin(raw, "loanet_positions", cal, 0.2)
    raw["beta_fail_snapshots"] = _thin(raw, "beta_fail_snapshots", cal, 1)
    raw["reg204"] = _thin(raw, "reg204", cal, 0)
    raw["beta_trades"] = _thin(raw, "beta_trades", cal, 0)
    table = completeness(raw, cal, D, settings)
    status = table.set_index("source")["status"]
    assert status["beta_segregation"] == "missing"
    assert status["loanet_positions"] == "out_of_band"
    assert status["beta_fail_snapshots"] == "ok"
    assert status["reg204"] == "ok"
    assert status["beta_trades"] == "missing"
    assert degraded_sources(table) == {"beta_segregation", "loanet_positions"}
    alerts = {a.scope: a.severity for a in alerts_for(D, table, None, None, None, settings)}
    assert alerts == {"beta_segregation": "warning", "loanet_positions": "warning", "beta_trades": "critical"}


def test_degraded_source_masks_its_book_only(feature_table):
    frame = feature_table[0]
    sample = pd.concat([frame[frame["book"].eq(b)].head(50) for b in ("B", "P")]).copy()
    before = sample.copy()
    masked = mask_degraded(sample, {"cns_beta"})
    assert masked == ["cns_consec_ftd_days", "cns_ftd_qty_log"]
    beta = sample["book"].eq("B")
    assert sample.loc[beta, masked].isna().all().all()
    pd.testing.assert_frame_equal(sample.loc[~beta], before.loc[~beta])
    untouched = [c for c in FEATURE_NAMES if c not in masked]
    pd.testing.assert_frame_equal(sample[untouched], before[untouched])


@pytest.fixture(scope="module")
def shadow(raw_frames, feature_table, settings, bundle, tmp_path_factory):
    cal = feature_table[1].cal
    days = list(cal.between("2024-12-02", "2024-12-10"))
    shadow_settings = dataclasses.replace(settings, artifacts_dir=tmp_path_factory.mktemp("shadow"))
    runs = score_dates(shadow_settings, days, bundle, raw=raw_frames, log=lambda *_: None)
    return shadow_settings, days, runs


def test_shadow_population_scores_and_feature_parity(shadow, feature_table):
    shadow_settings, days, runs = shadow
    frame, ctx = feature_table
    cal = ctx.cal
    for run in runs:
        s = cal.next_business_day(run.scoring_date)
        assert run.settle_date == s
        batch = frame[frame["settle_date"].eq(s)]
        assert set(run.scores["trade_key"]) == set(batch["trade_key"])
        assert (batch["entry_date"] <= run.scoring_date).all()
        assert list(run.scores.columns) == SCORE_COLUMNS
        assert run.scores["scoring_date"].eq(run.scoring_date).all()
        assert run.scores["fail_probability"].between(0, 1).all()
        firsts = run.scores[run.scores["risk_rank"].eq(1)]
        assert len(firsts) == run.scores.groupby(["book", "direction"]).ngroups
        j = run.features.merge(batch, on="trade_key", suffixes=("_s", "_b"))
        assert len(j) == len(batch)
        for f in FEATURE_NAMES:
            a, b = j[f"{f}_s"], j[f"{f}_b"]
            if f in CATEGORICAL:
                assert a.astype("string").fillna("NA").equals(b.astype("string").fillna("NA")), f
            else:
                assert np.allclose(a.astype(float), b.astype(float), equal_nan=True), f
    stored = read_scores(shadow_settings)
    assert len(stored) == sum(len(r.scores) for r in runs)


def test_monitor_writes_logs_and_summary(shadow, raw_frames, bundle):
    shadow_settings, days, _ = shadow
    result = monitor_dates(shadow_settings, days, bundle, raw=raw_frames, log=lambda *_: None)
    assert set(result.daily["scoring_date"]) == set(days[:-2])
    assert (result.daily["labelled"] + result.daily["unlabelled"]).eq(result.daily["scored"]).all()
    assert set(result.rolling["direction"]) == {"D", "R"}
    assert result.feature_drift["window_days"].min() >= shadow_settings.monitoring.drift_min_days
    out = shadow_settings.monitoring_dir
    assert {"performance_daily.csv", "rolling.csv", "alerts.csv"} <= {p.name for p in out.iterdir()}
    rerun = monitor_dates(shadow_settings, days, bundle, raw=raw_frames, log=lambda *_: None)
    assert len(pd.read_csv(out / "rolling.csv")) == len(rerun.rolling.drop_duplicates(["as_of", "direction"]))
    summary = shadow_summary(shadow_settings, bundle)
    assert summary["shadow_days_scored"] == len(days)
    assert summary["shadow_days_evaluated"] == len(days) - 2
    assert summary["shadow_days_ok"] is False


def _realized(settings, scale, holdout_pr_auc):
    rng = np.random.default_rng(3)
    days = pd.bdate_range("2025-01-02", periods=25)
    n = len(days) * 200
    scores = pd.DataFrame({"scoring_date": np.repeat(days, 200), "direction": "D",
                           "trade_key": [f"t{i}" for i in range(n)], "fail_probability": rng.uniform(0.01, 0.3, n)})
    y = (rng.uniform(size=n) < scores["fail_probability"] * scale).astype(int)
    labels = pd.DataFrame({"trade_key": scores["trade_key"], "failed_on_settle": y}).iloc[10:]
    daily, joined = realized_daily(scores, labels, 50)
    ref = SimpleNamespace(reference={"holdout": {"pr_auc": holdout_pr_auc, "precision_at_50": 0.3}})
    roll = rolling_performance(joined, daily, days[-1], SimpleNamespace(directions={"D": ref}), settings)
    return daily, roll, alerts_for(days[-1], None, None, None, roll, settings)


def test_realized_performance_and_alerts(settings):
    daily, roll, alerts = _realized(settings, 1.0, holdout_pr_auc=0.25)
    assert daily["unlabelled"].sum() == 10
    assert roll["days"].iloc[0] == settings.monitoring.rolling_window_bd
    assert 0.9 < roll["calibration_ratio"].iloc[0] < 1.1
    assert alerts == []
    _, roll, alerts = _realized(settings, 0.5, holdout_pr_auc=0.9)
    assert roll["calibration_ratio"].iloc[0] > 1.6
    assert {(a.signal, a.severity) for a in alerts} == {("calibration", "warning"), ("performance", "critical")}


def test_feature_drift_flags_shifted_top_feature(trained, bundle, settings):
    results, _, _ = trained
    rows = results["D"].training_rows.assign(direction="D")
    assert not feature_drift(rows, bundle, settings)["alert"].any()
    top = next(f for f in bundle_top_features(bundle, "D", 20) if f not in CATEGORICAL)
    shifted = rows.assign(**{top: rows[top].astype(float) + 10 * rows[top].astype(float).std() + 1})
    drift = feature_drift(shifted, bundle, settings).set_index("feature")
    assert drift.loc[top, "alert"]
    assert drift["alert"].sum() == 1
