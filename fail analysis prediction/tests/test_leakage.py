from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from failmodel import leakage as L
from failmodel.bizcal import BusinessCalendar
from failmodel.config import Settings
from failmodel.features import fail_pressure
from failmodel.features.base import FeatureContext
from failmodel.features.build import CATEGORICAL, FEATURE_CATALOG, FEATURE_NAMES, catalog_markdown
from failmodel.sources import SOURCES

CUTOFF = "2024-09-30"


def test_catalog_is_unique_and_matches_build(feature_table):
    frame, _ = feature_table
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
    assert all(n in frame.columns for n in FEATURE_NAMES)
    assert {s.kind for s in FEATURE_CATALOG} <= {"numeric", "categorical"}
    md = catalog_markdown()
    assert all(f"`{n}`" in md for n in FEATURE_NAMES)


def test_documentation_catalog_matches_code():
    from pathlib import Path

    from failmodel.features.build import DOC_BEGIN, DOC_END, deferred_markdown, doc_catalog_markdown

    doc = (Path(__file__).resolve().parents[1] / "failanalysispredictivemodel.md").read_text()
    block = doc[doc.index(DOC_BEGIN) + len(DOC_BEGIN):doc.index(DOC_END)]
    assert doc_catalog_markdown() in block and deferred_markdown() in block, "run: failmodel catalog --doc"


def test_forbidden_columns():
    assert L.forbidden_columns(FEATURE_NAMES) == []
    bad = ["DKCount", "fail_quantity", "failed_on_settle", "reason_code", "log_notional"]
    assert L.forbidden_columns(bad) == bad[:4]


def test_asof_audit_clean_and_detects_a_shifted_source(feature_table, settings):
    frame, ctx = feature_table
    audit = L.asof_audit(frame, ctx.cal, settings)
    assert audit["violations"].sum() == 0
    assert {"beta_security", "beta_fail_snapshots", "p3_fail_snapshots", "labels"} <= set(audit["source"])
    leaky = frame.copy()
    leaky["_asof_beta_security"] = ctx.cal.add(leaky["_asof_beta_security"], 2)
    bad = L.asof_audit(leaky, ctx.cal, settings).set_index("source")
    assert bad.loc["beta_security", "violations"] > 0


def test_future_perturbation_leaves_past_features_unchanged(settings, raw_frames):
    results = L.perturbation_test(settings, raw_frames, CUTOFF, seed=3)
    for mode, diff in results.items():
        assert diff["differences"].sum() == 0, (mode, diff[diff["differences"] > 0])


def test_future_perturbation_catches_a_lookahead(settings, raw_frames, monkeypatch):
    monkeypatch.setitem(SOURCES, "beta_security", replace(SOURCES["beta_security"], availability_lag_bd=-1))
    diff = L.perturbation_test(settings, raw_frames, CUTOFF, seed=3, modes=("noise",))["noise"].set_index("feature")
    assert diff.loc["spread_bps", "differences"] > 0


def test_single_feature_screen_flags_a_label_proxy(feature_table):
    frame, _ = feature_table
    screen = L.single_feature_screen(frame, FEATURE_NAMES, CATEGORICAL)
    assert not screen["flagged"].any(), screen.head()
    leaky = frame.assign(proxy=frame["failed_on_settle"].astype(float) + np.random.default_rng(0).normal(0, 0.05, len(frame)))
    flagged = L.single_feature_screen(leaky, ["proxy"], [])
    assert flagged.loc[0, "flagged"]


def test_label_shuffle_scores_near_base_rate(feature_table):
    frame, _ = feature_table
    result = L.label_shuffle_test(frame, FEATURE_NAMES, CATEGORICAL, seed=1)
    assert result.passed, result
    assert 0.4 < result.roc_auc < 0.6


def test_fail_pressure_respects_book_lags():
    cal = BusinessCalendar.weekdays("2024-01-01", "2024-01-31")
    d, prev = pd.Timestamp("2024-01-10"), pd.Timestamp("2024-01-09")
    fails = pd.DataFrame({
        "book": ["B", "B", "B", "P", "P"],
        "snapshot_date": [prev, d, d, prev, d],
        "direction": ["D"] * 5,
        "cusip9": ["X12345678"] * 5,
        "account": ["1", "2", "3", "4", "5"],
        "quantity": [100.0] * 5,
        "settle_date": [prev, d, d, prev, d],
    })
    base = pd.DataFrame({"book": ["B", "P"], "scoring_date": [d, d], "cusip9": ["X12345678"] * 2,
                         "account": ["9", "9"], "direction": ["D", "D"], "quantity": [50.0, 50.0]})
    ctx = FeatureContext(raw={}, cal=cal, settings=Settings(), fails=fails, universe=pd.DataFrame(), history=pd.DataFrame())
    out = fail_pressure.compute(base, ctx)
    assert out.loc[0, "cusip_open_fails_same_dir"] == 1
    assert out.loc[0, "_asof_beta_fail_snapshots"] == prev
    assert out.loc[1, "cusip_open_fails_same_dir"] == 1
    assert out.loc[1, "_asof_p3_fail_snapshots"] == d
    assert np.isnan(out.loc[0, "cns_ftd_qty_log"])


@pytest.mark.parametrize("mode", ["noise", "drop"])
def test_perturb_future_only_touches_future_rows(raw_frames, mode):
    out = L.perturb_future(raw_frames, CUTOFF, seed=0, mode=mode)
    sec, new = raw_frames["beta_security"], out["beta_security"]
    past = sec["SnapShotDate"] <= pd.Timestamp(CUTOFF)
    pd.testing.assert_frame_equal(sec[past].reset_index(drop=True), new[new["SnapShotDate"] <= pd.Timestamp(CUTOFF)].reset_index(drop=True),
                                  check_dtype=False)
    assert out["business_calendar"] is raw_frames["business_calendar"]
