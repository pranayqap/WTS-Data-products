"""Fail-pressure family: open fails and CNS fail-to-deliver positions known at D.

Absence from a snapshot means zero, so each row is pinned to the latest snapshot date on or before its
cutoff (per book and source) and joined exactly on that date; values are zero-filled only when that
snapshot exists and is fresh.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..asof import AUDIT_PREFIX, cutoff_dates, reference_dates
from ..bizcal import BusinessCalendar, to_day
from ..canonical import normalize_cusip9
from ..sources import lag
from .base import FeatureContext, num

FAMILY = "fail_pressure"
_F = ("beta_fail_snapshots / p3_fail_snapshots", "Latest snapshot <= D - lag (B lag 1, P lag 0)")
_C = ("cns_beta / cns_p3", "Latest position date <= D - lag; end-of-day cycle")

SPECS = [
    num("cusip_open_fails_same_dir", FAMILY, *_F, "B, P", "Open fails in the CUSIP, same book and direction"),
    num("cusip_open_fail_qty_same_dir_log", FAMILY, *_F, "B, P", "log(1 + open fail quantity in the CUSIP, same direction)"),
    num("cusip_open_fails_opp_dir", FAMILY, *_F, "B, P", "Open fails in the CUSIP, opposite direction"),
    num("cusip_aged_fail_qty_log", FAMILY, *_F, "B, P", "log(1 + open fail quantity aged >= aged_fail_min_age_bd), same direction"),
    num("qty_to_cusip_open_fail_qty", FAMILY, *_F, "B, P", "Trade quantity / open fail quantity in the CUSIP, same direction"),
    num("account_open_fails", FAMILY, *_F, "B, P", "Open fails on the account, both directions"),
    num("book_open_fails_same_dir_log", FAMILY, *_F, "B, P", "log(1 + open fails in the book, same direction)"),
    num("cns_ftd_qty_log", FAMILY, *_C, "B, P", "log(1 + CNS fail-to-deliver quantity in the CUSIP)"),
    num("cns_consec_ftd_days", FAMILY, *_C, "B, P", "Consecutive CNS position dates with a fail to deliver"),
]

FAIL_SOURCE = {"B": "beta_fail_snapshots", "P": "p3_fail_snapshots"}
CNS_SOURCE = {"B": "cns_beta", "P": "cns_p3"}


def _fail_aggregates(fails: pd.DataFrame, cal: BusinessCalendar, min_age: int):
    open_now = fails["settle_date"].isna() | (fails["settle_date"] <= fails["snapshot_date"])
    f = fails.loc[open_now, ["book", "snapshot_date", "direction", "cusip9", "account", "quantity", "settle_date"]].copy()
    f["age_bd"] = cal.bd_diff(f["settle_date"], f["snapshot_date"]).to_numpy()
    f["aged_qty"] = f["quantity"].where(f["age_bd"] >= min_age, 0.0)
    by_cusip = (
        f.groupby(["book", "snapshot_date", "cusip9", "direction"])
        .agg(cnt=("quantity", "size"), qty=("quantity", "sum"), aged=("aged_qty", "sum"))
        .unstack("direction", fill_value=0)
    )
    by_cusip.columns = [f"{a}_{b}" for a, b in by_cusip.columns]
    by_account = f.groupby(["book", "snapshot_date", "account"]).size().rename("account_cnt").reset_index()
    by_book = f.groupby(["book", "snapshot_date", "direction"]).size().unstack("direction", fill_value=0)
    by_book.columns = [f"book_cnt_{c}" for c in by_book.columns]
    return by_cusip.reset_index(), by_account, by_book.reset_index()


def _cns_positions(frame: pd.DataFrame, cycle: str | None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "cusip9": pd.Series(dtype="string"),
                             "ftd_qty": pd.Series(dtype="float64"), "run": pd.Series(dtype="float64")})
    c = pd.DataFrame({"date": to_day(frame["PositionDate"]), "cycle": frame["Cycle"].astype("string").str.strip(),
                      "cusip9": normalize_cusip9(frame["CUSIP"]),
                      "cnp": pd.to_numeric(frame["ClosingNetPosition"], errors="coerce")})
    if cycle is not None:
        c = c[c["cycle"] == cycle]
    else:
        c = c.sort_values("cycle").drop_duplicates(["date", "cusip9"], keep="last")
    c = c.groupby(["date", "cusip9"], as_index=False)["cnp"].sum()
    c["ftd_qty"] = (-c["cnp"]).clip(lower=0)
    dates = np.sort(c["date"].unique())
    c["didx"] = np.searchsorted(dates, c["date"].to_numpy())
    ftd = c[c["ftd_qty"] > 0].sort_values(["cusip9", "didx"]).copy()
    new_run = ftd.groupby("cusip9")["didx"].diff().ne(1)
    ftd["run"] = ftd.groupby([ftd["cusip9"], new_run.cumsum()]).cumcount() + 1
    c = c.merge(ftd[["date", "cusip9", "run"]], on=["date", "cusip9"], how="left")
    c["run"] = c["run"].fillna(0)
    return c[["date", "cusip9", "ftd_qty", "run"]]


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    cal, s = ctx.cal, ctx.settings
    out = pd.DataFrame(index=base.index, columns=[spec.name for spec in SPECS], dtype="float64")
    for source in [*FAIL_SOURCE.values(), *CNS_SOURCE.values()]:
        out[AUDIT_PREFIX + source] = pd.Series(pd.NaT, index=base.index, dtype="datetime64[ns]")
    by_cusip, by_account, by_book = _fail_aggregates(ctx.fails, cal, s.aged_fail_min_age_bd)
    for book in ("B", "P"):
        rows = base.index[base["book"].eq(book)]
        if not len(rows):
            continue
        b = base.loc[rows]
        source = FAIL_SOURCE[book]
        present = ctx.fails.loc[ctx.fails["book"].eq(book), "snapshot_date"]
        ref = reference_dates(cutoff_dates(b["scoring_date"], cal, lag(source)), present, cal, s.max_staleness_bd)
        q = pd.DataFrame({"book": book, "snapshot_date": ref, "cusip9": b["cusip9"], "account": b["account"],
                          "direction": b["direction"]}, index=rows)
        m = q.merge(by_cusip, on=["book", "snapshot_date", "cusip9"], how="left").set_index(rows)
        m = m.merge(by_account, on=["book", "snapshot_date", "account"], how="left").set_index(rows)
        m = m.merge(by_book, on=["book", "snapshot_date"], how="left").set_index(rows)
        has = ref.notna()
        for c in m.columns:
            if c.startswith(("cnt_", "qty_", "aged_", "account_cnt", "book_cnt_")):
                m[c] = m[c].astype(float).fillna(0.0).where(has)
        dlv = q["direction"].eq("D")

        def side(prefix, same=True):
            d, r = m.get(f"{prefix}_D", 0.0), m.get(f"{prefix}_R", 0.0)
            return pd.Series(np.where(dlv == same, d, r), index=rows).where(has)

        same_qty = side("qty")
        out.loc[rows, "cusip_open_fails_same_dir"] = side("cnt")
        out.loc[rows, "cusip_open_fail_qty_same_dir_log"] = np.log1p(same_qty)
        out.loc[rows, "cusip_open_fails_opp_dir"] = side("cnt", same=False)
        out.loc[rows, "cusip_aged_fail_qty_log"] = np.log1p(side("aged"))
        out.loc[rows, "qty_to_cusip_open_fail_qty"] = (b["quantity"].astype(float) / same_qty.where(same_qty > 0))
        out.loc[rows, "account_open_fails"] = m["account_cnt"]
        out.loc[rows, "book_open_fails_same_dir_log"] = np.log1p(side("book_cnt"))
        out.loc[rows, AUDIT_PREFIX + source] = ref

        cns_source = CNS_SOURCE[book]
        cns = _cns_positions(ctx.raw.get(cns_source, pd.DataFrame()), s.cns_eod_cycle)
        cref = reference_dates(cutoff_dates(b["scoring_date"], cal, lag(cns_source)), cns["date"], cal, s.max_staleness_bd)
        cq = pd.DataFrame({"date": cref, "cusip9": b["cusip9"]}, index=rows)
        cm = cq.merge(cns, on=["date", "cusip9"], how="left").set_index(rows)
        chas = cref.notna()
        out.loc[rows, "cns_ftd_qty_log"] = np.log1p(cm["ftd_qty"].astype(float).fillna(0.0)).where(chas)
        out.loc[rows, "cns_consec_ftd_days"] = cm["run"].astype(float).fillna(0.0).where(chas)
        out.loc[rows, AUDIT_PREFIX + cns_source] = cref
    return out
