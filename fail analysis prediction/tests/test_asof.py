import numpy as np
import pandas as pd

from failmodel.asof import asof_join, knowledge_date, pinned_join, reference_dates
from failmodel.bizcal import BusinessCalendar

CAL = BusinessCalendar.weekdays("2024-01-01", "2024-03-29")
D = pd.Timestamp


def test_asof_join_takes_latest_on_or_before_cutoff_and_masks_stale():
    left = pd.DataFrame({"k": ["A", "A", "B", "C"]}, index=[10, 11, 12, 13])
    right = pd.DataFrame({"k": ["A", "A", "A", "B"], "d": [D("2024-01-02"), D("2024-01-04"), D("2024-01-08"), D("2024-01-02")],
                          "v": [1.0, 2.0, 3.0, 9.0]})
    cutoff = pd.Series([D("2024-01-05"), D("2024-01-08"), D("2024-01-10"), D("2024-01-10")], index=left.index)
    out = asof_join(left, right, by=["k"], right_date="d", cutoff=cutoff, cal=CAL, max_staleness_bd=3, audit_name="src")
    assert list(out.index) == [10, 11, 12, 13]
    assert out["v"].tolist()[:2] == [2.0, 3.0]
    assert np.isnan(out.loc[12, "v"]) and pd.isna(out.loc[12, "_asof_src"])
    assert np.isnan(out.loc[13, "v"])
    assert out.loc[10, "_asof_src"] == D("2024-01-04")


def test_pinned_join_treats_absence_as_zero_but_missing_snapshot_as_unknown():
    left = pd.DataFrame({"k": ["A", "B", "A", "A"]})
    right = pd.DataFrame({"k": ["A", "A"], "d": [D("2024-01-02"), D("2024-01-03")], "v": [5.0, 7.0]})
    cutoff = pd.Series([D("2024-01-02"), D("2024-01-03"), D("2024-01-05"), D("2024-01-12")])
    out = pinned_join(left, right, by=["k"], right_date="d", cutoff=cutoff, cal=CAL, max_staleness_bd=3, audit_name="s")
    assert out["v"].tolist()[:3] == [5.0, 0.0, 7.0]
    assert np.isnan(out.loc[3, "v"])
    assert out["_asof_s"].tolist()[:3] == [D("2024-01-02"), D("2024-01-03"), D("2024-01-03")]


def test_reference_dates_respects_staleness():
    ref = reference_dates(pd.Series([D("2024-01-09"), D("2024-01-15")]), pd.Series([D("2024-01-08")]), CAL, 3)
    assert ref.iloc[0] == D("2024-01-08") and pd.isna(ref.iloc[1])


def test_threshold_history_known_when_recorded():
    frame = pd.DataFrame({"EffectiveDate": [D("2024-01-02"), D("2024-01-10")],
                          "AddedTimestamp": ["2024-01-05 08:00:00", "2024-01-03 08:00:00"]})
    known = knowledge_date("beta_threshold_history", frame)
    assert known.tolist() == [D("2024-01-05"), D("2024-01-10")]
