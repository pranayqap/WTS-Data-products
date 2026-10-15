import numpy as np
import pandas as pd
import pytest

from failmodel.bizcal import BusinessCalendar
from failmodel.models.bundle import load_bundle, save_bundle
from failmodel.models.calibration import Calibrator
from failmodel.models.explain import _plain
from failmodel.models.metrics import evaluate, expected_calibration_error, psi, reference_profile, top_k
from failmodel.models.train import accepted, lgb_matrix
from failmodel.models.validation import holdout_fold, rolling_folds


def test_folds_are_purged_and_precede_the_holdout():
    cal = BusinessCalendar.weekdays("2024-01-01", "2024-12-31")
    settle = pd.Series(cal.days)
    hold = holdout_fold(settle, cal, months=2, purge_bd=5)
    folds = rolling_folds(settle, cal, n_folds=3, purge_bd=5, before=hold.valid_start)
    assert hold.valid_start == pd.Timestamp("2024-11-01")
    assert [f.name for f in folds] == ["2024-08", "2024-09", "2024-10"]
    for f in folds + [hold]:
        first_valid = cal.days[cal.days >= f.valid_start][0]
        assert cal.bd_diff(pd.Series([f.train_end]), pd.Series([first_valid])).iloc[0] == 6
        assert not (f.train_mask(settle) & f.valid_mask(settle)).any()
    assert folds[-1].valid_end < hold.valid_start


def test_top_k_precision_recall_and_dollars():
    days = np.array(["d1"] * 4 + ["d2"] * 4)
    score = np.array([0.9, 0.8, 0.1, 0.2, 0.7, 0.6, 0.5, 0.4])
    y = np.array([1, 0, 1, 0, 0, 0, 1, 0])
    value = np.array([100.0, 0, 50, 0, 0, 0, 25, 0])
    m = top_k(days, score, y, value, ks=(2,))
    assert m["precision_at_2"] == pytest.approx(0.25)
    assert m["recall_at_2"] == pytest.approx(1 / 3)
    assert m["dollar_recall_at_2"] == pytest.approx(100 / 175)
    assert m["precision_at_5pct"] == pytest.approx(0.5)


def test_ece_and_calibrator():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 0.3, 50_000)
    y = (rng.uniform(size=p.size) < p).astype(int)
    assert expected_calibration_error(y, p) < 0.01
    assert expected_calibration_error(y, np.clip(p + 0.1, 0, 1)) > 0.08
    raw = np.clip(p * 2.5, 0, 1)
    iso = Calibrator(500).fit(raw, y)
    sig = Calibrator(10**9).fit(raw, y)
    assert (iso.method, sig.method) == ("isotonic", "sigmoid")
    assert Calibrator(10**9, method="isotonic").fit(raw, y).method == "isotonic"
    for cal in (iso, sig):
        out = cal.predict(np.sort(raw))
        assert (np.diff(out) >= -1e-12).all() and out.min() >= 0 and out.max() <= 1
        assert expected_calibration_error(y, cal.predict(raw)) < expected_calibration_error(y, raw)


def test_psi_detects_shift():
    rng = np.random.default_rng(1)
    ref = reference_profile(pd.Series(rng.normal(0, 1, 20_000)), categorical=False)
    assert psi(ref, pd.Series(rng.normal(0, 1, 20_000))) < 0.01
    assert psi(ref, pd.Series(rng.normal(1, 1, 20_000))) > 0.25
    cat = reference_profile(pd.Series(["a"] * 80 + ["b"] * 20), categorical=True)
    assert psi(cat, pd.Series(["a"] * 20 + ["b"] * 80)) > 0.25


def test_plain_language_reason_text():
    assert _plain("cusip_open_fail_qty_same_dir_log", float(np.log1p(1500))) == \
        "Open fail quantity in the CUSIP, same direction: 1,500"
    assert _plain("cusip_open_fails_same_dir", 3.0) == "Open fails in the CUSIP, same book and direction: 3"
    assert _plain("product_group", "MUNI").endswith(": MUNI")


def test_training_beats_rule_and_calibrates(trained):
    results, _, _ = trained
    assert set(results) == {"D", "R"}
    d = results["D"].holdout_metrics.set_index("model")
    assert d.loc["lightgbm", "pr_auc"] > d.loc["rule", "pr_auc"]
    assert d.loc["lightgbm", "ece"] < d.loc["lightgbm_uncalibrated", "ece"]
    assert results["D"].calibrator.method == results["D"].dev_calibrator.method
    assert len(results["D"].fold_metrics) == 2 * 3
    assert isinstance(accepted(results["D"].acceptance), bool)


def test_bundle_round_trip_and_scoring(trained, settings, tmp_path):
    results, frame, _ = trained
    path = save_bundle(results, settings, "test-version", root=tmp_path)
    bundle = load_bundle(path)
    sample = frame[frame["settle_date"] >= frame["settle_date"].max() - pd.Timedelta(days=10)]
    scored = bundle.score(sample)
    assert scored.index.equals(sample.index)
    assert scored["fail_probability"].between(0, 1).all()
    top = scored[scored["risk_rank"] == 1]
    assert len(top) == sample.groupby(["scoring_date", "book", "direction"]).ngroups
    assert top["reason_1_text"].notna().all()
    direct = results["D"].booster.predict(lgb_matrix(sample[sample["direction"].eq("D")], results["D"].levels))
    assert np.allclose(scored.loc[sample["direction"].eq("D"), "raw_score"], direct)
    assert evaluate(sample[sample["failed_on_settle"].notna()],
                    sample.loc[sample["failed_on_settle"].notna(), "failed_on_settle"].astype(int).to_numpy(),
                    scored.loc[sample["failed_on_settle"].notna(), "fail_probability"].to_numpy(), (25,))["rows"] > 0
