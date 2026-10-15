"""Inventory and borrow family: Beta box, lendable supply, street borrow and borrow cost, by CUSIP-9."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..asof import asof_join, cutoff_dates
from ..bizcal import to_day
from ..canonical import normalize_cusip9
from ..sources import lag
from .base import FeatureContext, num

FAMILY = "inventory"
_SEG = ("beta_segregation", "SnapShotDate <= D - lag")
_SL = ("smartloan_avail", "FileDate <= D - lag")
_LN = ("loanet_positions", "TradeDate <= D - lag")
_BR = ("borrow_rates", "FileDate <= D - lag")

SPECS = [
    num("box_deficit_log", FAMILY, *_SEG, "B", "log(1 + max(Required - Available, 0)) in the Beta box"),
    num("deficit_to_qty", FAMILY, *_SEG, "B", "Box deficit / trade quantity"),
    num("qty_to_box_available", FAMILY, *_SEG, "B", "Trade quantity / box Available"),
    num("sod_deficit_log", FAMILY, *_SEG, "B", "log(1 + start-of-day deficit quantity)"),
    num("stock_borrow_log", FAMILY, *_SEG, "B", "log(1 + stock borrow quantity)"),
    num("stock_loan_log", FAMILY, *_SEG, "B", "log(1 + stock loan quantity)"),
    num("no_loan_flag", FAMILY, *_SEG, "B", "NoLoanInd is Y"),
    num("sl_total_avail_log", FAMILY, *_SL, "B, P", "log(1 + SmartLoan total available)"),
    num("qty_to_sl_avail", FAMILY, *_SL, "B, P", "Trade quantity / SmartLoan total available"),
    num("sl_firm_share", FAMILY, *_SL, "B, P", "Share of SmartLoan availability from firm inventory"),
    num("loanet_borrow_log", FAMILY, *_LN, "B, P", "log(1 + street borrow quantity)"),
    num("loanet_loan_log", FAMILY, *_LN, "B, P", "log(1 + street loan quantity)"),
    num("loanet_borrow_contras", FAMILY, *_LN, "B, P", "Number of contras on the borrow side"),
    num("loanet_rebate", FAMILY, *_LN, "B, P", "Average borrow rebate (low or negative = hard to borrow)"),
    num("loanet_min_rebate", FAMILY, *_LN, "B, P", "Minimum borrow rebate"),
    num("borrow_vwar", FAMILY, *_BR, "B, P", "Volume-weighted average borrow rate"),
    num("borrow_count", FAMILY, *_BR, "B, P", "Number of borrows"),
]


def _segregation(raw) -> pd.DataFrame:
    g = raw.get("beta_segregation", pd.DataFrame())
    if g.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    f = pd.DataFrame({"date": to_day(g["SnapShotDate"]), "cusip9": normalize_cusip9(g["CUSIP"])})
    for c in ["Required", "Available", "StartOfDayDeficitQty", "StockBorrowQty", "StockLoanQty"]:
        f[c] = pd.to_numeric(g[c], errors="coerce")
    f["no_loan_flag"] = g["NoLoanInd"].astype("string").str.strip().str.upper().eq("Y").astype(float)
    agg = f.groupby(["date", "cusip9"], as_index=False).agg(
        Required=("Required", "sum"), Available=("Available", "sum"), sod=("StartOfDayDeficitQty", "sum"),
        borrow=("StockBorrowQty", "sum"), loan=("StockLoanQty", "sum"), no_loan_flag=("no_loan_flag", "max"))
    return agg


def _smartloan(raw) -> pd.DataFrame:
    s = raw.get("smartloan_avail", pd.DataFrame())
    if s.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    f = pd.DataFrame({"date": to_day(s["FileDate"]), "cusip9": normalize_cusip9(s["CUSIP"]),
                      "total": pd.to_numeric(s["TotalAvailable"], errors="coerce"),
                      "firm": pd.to_numeric(s["AvailableFromFirm"], errors="coerce")})
    return f.groupby(["date", "cusip9"], as_index=False)[["total", "firm"]].sum()


def _loanet(raw) -> pd.DataFrame:
    ln = raw.get("loanet_positions", pd.DataFrame())
    if ln.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    f = pd.DataFrame({"date": to_day(ln["TradeDate"]), "cusip9": normalize_cusip9(ln["CUSIP"]),
                      "side": ln["BorrowLoan"].astype("string").str.strip().str.upper().str[:1],
                      "qty": pd.to_numeric(ln["TotalQuantity"], errors="coerce"),
                      "contras": pd.to_numeric(ln["ContraCount"], errors="coerce"),
                      "rebate": pd.to_numeric(ln["AvgRebate"], errors="coerce"),
                      "min_rebate": pd.to_numeric(ln["MinRebate"], errors="coerce")})
    borrow = f[f["side"] == "B"].groupby(["date", "cusip9"], as_index=False).agg(
        borrow_qty=("qty", "sum"), contras=("contras", "sum"), rebate=("rebate", "mean"), min_rebate=("min_rebate", "min"))
    loan = f[f["side"] == "L"].groupby(["date", "cusip9"], as_index=False).agg(loan_qty=("qty", "sum"))
    return borrow.merge(loan, on=["date", "cusip9"], how="outer")


def _borrow_rates(raw) -> pd.DataFrame:
    b = raw.get("borrow_rates", pd.DataFrame())
    if b.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    f = pd.DataFrame({"date": to_day(b["FileDate"]), "cusip9": normalize_cusip9(b["CUSIP"]),
                      "vwar": pd.to_numeric(b["BorrowVWAR"], errors="coerce"),
                      "count": pd.to_numeric(b["NumberOfBorrows"], errors="coerce")})
    return f.groupby(["date", "cusip9"], as_index=False).agg(vwar=("vwar", "mean"), count=("count", "sum"))


def _get(frame: pd.DataFrame, col: str) -> pd.Series:
    return frame[col].astype(float) if col in frame.columns else pd.Series(np.nan, index=frame.index)


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    cal, raw, stale = ctx.cal, ctx.raw, ctx.settings.max_staleness_bd
    keys = base[["cusip9"]]
    qty = base["quantity"].astype(float)

    def join(frame, source):
        return asof_join(keys, frame, by=["cusip9"], right_date="date",
                         cutoff=cutoff_dates(base["scoring_date"], cal, lag(source)), cal=cal,
                         max_staleness_bd=stale, audit_name=source)

    out = pd.DataFrame(index=base.index)
    seg = join(_segregation(raw), "beta_segregation")
    beta = base["book"].eq("B")
    deficit = (_get(seg, "Required") - _get(seg, "Available")).clip(lower=0)
    avail = _get(seg, "Available")
    out["box_deficit_log"] = np.log1p(deficit)
    out["deficit_to_qty"] = deficit / qty.where(qty > 0)
    out["qty_to_box_available"] = qty / avail.where(avail > 0)
    out["sod_deficit_log"] = np.log1p(_get(seg, "sod").clip(lower=0))
    out["stock_borrow_log"] = np.log1p(_get(seg, "borrow").clip(lower=0))
    out["stock_loan_log"] = np.log1p(_get(seg, "loan").clip(lower=0))
    out["no_loan_flag"] = _get(seg, "no_loan_flag")
    seg_cols = [s.name for s in SPECS if s.source == "beta_segregation"]
    out.loc[~beta, seg_cols] = np.nan
    seg.loc[~beta, "_asof_beta_segregation"] = pd.NaT

    sl = join(_smartloan(raw), "smartloan_avail")
    total = _get(sl, "total")
    out["sl_total_avail_log"] = np.log1p(total.clip(lower=0))
    out["qty_to_sl_avail"] = qty / total.where(total > 0)
    out["sl_firm_share"] = _get(sl, "firm") / total.where(total > 0)

    ln = join(_loanet(raw), "loanet_positions")
    out["loanet_borrow_log"] = np.log1p(_get(ln, "borrow_qty").clip(lower=0))
    out["loanet_loan_log"] = np.log1p(_get(ln, "loan_qty").clip(lower=0))
    out["loanet_borrow_contras"] = _get(ln, "contras")
    out["loanet_rebate"] = _get(ln, "rebate")
    out["loanet_min_rebate"] = _get(ln, "min_rebate")

    br = join(_borrow_rates(raw), "borrow_rates")
    out["borrow_vwar"] = _get(br, "vwar")
    out["borrow_count"] = _get(br, "count")
    for frame in (seg, sl, ln, br):
        for c in frame.columns:
            if c.startswith("_asof_"):
                out[c] = frame[c]
    return out
