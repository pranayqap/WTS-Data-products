import numpy as np
import pandas as pd
import pytest

from failmodel.bizcal import BusinessCalendar
from failmodel.canonical import FAIL_COLUMNS
from failmodel.features.build import CATEGORICAL
from failmodel.leakage import perturb_future
from failmodel.persistence import (FEATURES, KEY, age_only, build_persistence_table, reconcile_summary, track_fails,
                                   train_persistence)

CUTOFF = pd.Timestamp("2024-10-15")


@pytest.fixture(scope="module")
def ptable(settings, raw_frames):
    return build_persistence_table(settings, raw_frames)


def _fail(key, day, qty=100.0, settle="2024-06-03"):
    row = {c: pd.NA for c in FAIL_COLUMNS}
    row.update({"fail_key": key, "trade_key": f"T{key}", "book": "B", "snapshot_date": pd.Timestamp(day),
                "settle_date": pd.Timestamp(settle), "direction": "D", "cusip9": "123456789", "quantity": qty,
                "original_quantity": qty, "amount": qty * 10, "dk_count": 0.0, "in_report_population": 1})
    return row


def test_tracking_labels_on_a_hand_built_history(settings):
    cal = BusinessCalendar.weekdays("2024-05-01", "2024-07-31")
    rows = [_fail("A", d) for d in ("2024-06-03", "2024-06-04", "2024-06-05")]
    rows += [_fail("B", "2024-06-03"), _fail("B", "2024-06-05", qty=60.0)]
    rows += [_fail("C", "2024-06-07", settle="2024-06-07")]
    t = track_fails(pd.DataFrame(rows), cal, settings).set_index(["fail_key", "snapshot_date"])
    a = t.loc["A"]
    assert a["age_bd"].tolist() == [1, 2, 3]
    assert a["still_open_h1"].tolist()[:2] == [1, 1]
    assert pd.isna(a["still_open_h1"].iloc[2])
    assert a.loc["2024-06-04", "still_open_h3"] == 0
    assert pd.isna(a.loc["2024-06-03", "still_open_h3"])
    b = t.loc["B"]
    assert b.loc["2024-06-03", "still_open_h1"] == 0
    assert b.loc["2024-06-05", "reappeared"] == 1
    assert np.isnan(b.loc["2024-06-05", "qty_change_prev"])
    assert a.loc["2024-06-04", "qty_change_prev"] == 1.0 and a.loc["2024-06-04", "reappeared"] == 0
    assert b.loc["2024-06-05", "bd_since_first_seen"] == 2


def test_reconciliation_identities(ptable):
    recon = ptable.reconcile()
    assert recon["carry_ok"].all() and recon["flow_ok"].all()
    assert (recon["collapsed_rows"] == 0).all()
    summary = reconcile_summary(recon).set_index("book")
    assert summary["open_fail_rows"].sum() == recon["open"].sum()
    assert 0 < summary.loc["B", "persist_rate_h1"] < 1
    f = ptable.frame
    known = f[[f"still_open_h{h}" for h in (1, 3, 5)]].dropna().astype(int)
    assert (known["still_open_h5"] <= known["still_open_h3"]).all()
    assert (known["still_open_h3"] <= known["still_open_h1"]).all()


def test_closeout_labels(ptable, settings):
    f, ps = ptable.frame, settings.persistence
    reg = f[f["reg204_breach"].notna()]
    assert len(reg) and reg["direction"].eq("D").all() and reg["product_group"].eq("EQ").all()
    deadline = ptable.cal.add(reg["settle_date"], ps.reg204_default_deadline_bd)
    assert (reg["snapshot_date"] < deadline).all()
    keys = set(map(tuple, ptable.tracked[KEY].to_numpy()))
    expected = [int((b, k, d) in keys) for b, k, d in zip(reg["book"], reg["fail_key"], deadline)]
    assert reg["reg204_breach"].astype(int).tolist() == expected
    muni = f[f["muni_breach"].notna()]
    assert muni["product_group"].eq("MUNI").all()


def _compare(full: pd.DataFrame, cut: pd.DataFrame) -> dict[str, int]:
    keep = full["snapshot_date"] <= CUTOFF
    a = full[keep].set_index(["fail_key", "snapshot_date"]).sort_index()
    b = cut[cut["snapshot_date"] <= CUTOFF].set_index(["fail_key", "snapshot_date"]).sort_index()
    assert a.index.equals(b.index)
    diffs = {}
    for c in FEATURES:
        if c in CATEGORICAL or a[c].dtype == "string" or a[c].dtype == object:
            d = (a[c].astype("string").fillna("NA") != b[c].astype("string").fillna("NA")).sum()
        else:
            d = (~np.isclose(a[c].astype(float), b[c].astype(float), equal_nan=True)).sum()
        if d:
            diffs[c] = int(d)
    return diffs


def test_persistence_features_ignore_the_future(ptable, settings, raw_frames):
    cut = build_persistence_table(settings, perturb_future(raw_frames, CUTOFF, mode="drop"))
    assert _compare(ptable.frame, cut.frame) == {}
    lifetime = ptable.tracked.groupby(["book", "fail_key"])["snapshot_date"].transform("size")
    lifetime_cut = cut.tracked.groupby(["book", "fail_key"])["snapshot_date"].transform("size")
    full = ptable.tracked.assign(life=lifetime.to_numpy())
    trunc = cut.tracked.assign(life=lifetime_cut.to_numpy())
    joined = full[full["snapshot_date"] <= CUTOFF].merge(trunc, on=KEY, suffixes=("", "_cut"))
    assert (joined["life"] != joined["life_cut"]).any(), "negative control: a lifetime count must see the future"


def test_age_only_baseline():
    train = pd.DataFrame({"age_bd": [1, 1, 1, 2, 2, 2, 40, 40]})
    y = np.array([0, 0, 1, 1, 1, 1, 1, 1])
    score = age_only(cap=30, k=0)(train, y, pd.DataFrame({"age_bd": [1, 2, 35, 7]}))
    assert score[0] == pytest.approx(1 / 3) and score[1] == pytest.approx(1.0)
    assert score[2] == pytest.approx(1.0)
    assert score[3] == pytest.approx(y.mean())


def test_persistence_training_runs_on_the_engine(ptable, settings):
    res = train_persistence(ptable.frame, ptable.cal, settings, tasks=("still_open_h3",), log=lambda *_: None)
    r = res["still_open_h3"]["D"]
    h = r.holdout_metrics.set_index("model")
    assert set(h.index) == {"lightgbm", "lightgbm_uncalibrated", "logistic", "age_only"}
    assert {"beats_age_only_pr_auc", "ece_ok", "fold_stability_ok"} <= set(r.acceptance)
    assert not any(k.startswith("beats_age_only_precision_at_") for k in r.acceptance)
    assert "age_bucket" in set(r.slices["slice"])
    assert r.task == "still_open_h3"
    assert list(r.training_rows.columns) == FEATURES
