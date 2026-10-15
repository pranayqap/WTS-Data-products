"""Security, liquidity and Reg SHO family. All joins are by CUSIP-9 so both books are covered."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..asof import asof_join, cutoff_dates, knowledge_date, pinned_join
from ..bizcal import to_day
from ..canonical import normalize_cusip9
from ..sources import lag
from .base import FeatureContext, num

FAMILY = "security"
SPECS = [
    num("chill_any", FAMILY, "beta_security", "SnapShotDate <= D - lag", "B, P", "Any DTC chill (deposit, COD, withdrawal, DO)"),
    num("dtc_frozen", FAMILY, "beta_security", "SnapShotDate <= D - lag", "B, P", "DTC frozen flag"),
    num("cns_ineligible", FAMILY, "beta_security", "SnapShotDate <= D - lag", "B, P", "DTCCNSEligibilityCode is not Y"),
    num("spread_bps", FAMILY, "beta_security", "SnapShotDate <= D - lag", "B, P", "Bid-ask spread in basis points of mid"),
    num("log_close_price", FAMILY, "beta_security", "SnapShotDate <= D - lag", "B, P", "log(1 + last close price)"),
    num("log_adv20", FAMILY, "beta_close_history", "TradeDate <= D - lag", "B, P", "log(1 + 20-day average close volume)"),
    num("qty_to_adv20", FAMILY, "beta_close_history", "TradeDate <= D - lag", "B, P", "Trade quantity / 20-day average volume"),
    num("vol20", FAMILY, "beta_close_history", "TradeDate <= D - lag", "B, P", "20-day standard deviation of daily log returns"),
    num("price_vs_eod", FAMILY, "p3_eod_prices", "SnapShotDate <= D - lag", "B, P", "Trade price / latest end-of-day price - 1, clipped to [-1, 1]"),
    num("threshold_flag", FAMILY, "beta_threshold_history", "max(EffectiveDate, AddedTimestamp) <= D", "B, P", "Reg SHO threshold security"),
    num("reg204_sod_fail_log", FAMILY, "reg204", "ReportDate <= D - lag", "B, P", "log(1 + 204 start-of-day CNS fail quantity); 0 if not on the grid"),
    num("reg204_htb", FAMILY, "reg204", "ReportDate <= D - lag", "B, P", "BorrowDesc marks the security hard to borrow"),
    num("reg204_threshold", FAMILY, "reg204", "ReportDate <= D - lag", "B, P", "204 grid threshold index"),
]

CHILLS = ["DTCChillDeposit", "DTCChillCOD", "DTCChillWdl", "DTCChilldo"]


def _flag(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.upper().isin(["Y", "1", "T", "TRUE"]).astype(float)


def map_cusip(frame: pd.DataFrame, raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Attach cusip9 to rows keyed by (SecurityNumber, date) using the security snapshot on or before that
    date, so the mapping never depends on later snapshots. Rows dated before the first snapshot use it."""
    sec = raw.get("beta_security", pd.DataFrame())
    if sec.empty or frame.empty:
        return frame.iloc[0:0].assign(cusip9=pd.Series(dtype="string"))
    m = pd.DataFrame({"_lookup": to_day(sec["SnapShotDate"]),
                      "SecurityNumber": pd.to_numeric(sec["SecurityNumber"], errors="coerce"),
                      "cusip9": normalize_cusip9(sec["CUSIP"])}).dropna(subset=["_lookup", "SecurityNumber"])
    m = m.drop_duplicates(["SecurityNumber", "_lookup"], keep="last").sort_values("_lookup")
    left = frame.dropna(subset=["date", "SecurityNumber"])
    left = left.assign(_lookup=left["date"].clip(lower=m["_lookup"].min())).sort_values("_lookup")
    out = pd.merge_asof(left, m, on="_lookup", by="SecurityNumber", direction="backward")
    return out[out["cusip9"].notna()].drop(columns="_lookup")


def _security_snapshot(raw) -> pd.DataFrame:
    sec = raw.get("beta_security", pd.DataFrame())
    if sec.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    bid, ask = pd.to_numeric(sec["BidPrice"], errors="coerce"), pd.to_numeric(sec["AskPrice"], errors="coerce")
    mid = (bid + ask) / 2
    frame = pd.DataFrame({
        "date": to_day(sec["SnapShotDate"]),
        "cusip9": normalize_cusip9(sec["CUSIP"]),
        "chill_any": pd.concat([_flag(sec[c]) for c in CHILLS if c in sec.columns], axis=1).max(axis=1),
        "dtc_frozen": _flag(sec["DTCfrozen"]),
        "cns_ineligible": 1.0 - _flag(sec["DTCCNSEligibilityCode"]),
        "spread_bps": ((ask - bid) / mid * 1e4).where((mid > 0) & (ask >= bid)),
        "log_close_price": np.log1p(pd.to_numeric(sec["LastClosePrice"], errors="coerce").clip(lower=0)),
    })
    return frame.groupby(["date", "cusip9"], as_index=False).max()


def _liquidity(raw) -> pd.DataFrame:
    hist = raw.get("beta_close_history", pd.DataFrame())
    if hist.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    h = pd.DataFrame({"date": to_day(hist["TradeDate"]),
                      "SecurityNumber": pd.to_numeric(hist["SecurityNumber"], errors="coerce"),
                      "price": pd.to_numeric(hist["ClosePrice"], errors="coerce"),
                      "volume": pd.to_numeric(hist["CloseVolume"], errors="coerce")})
    h = h.sort_values(["SecurityNumber", "date"]).reset_index(drop=True)
    g = h.groupby("SecurityNumber", sort=False)
    h["adv20"] = g["volume"].transform(lambda v: v.rolling(20, min_periods=5).mean())
    h["_ret"] = np.log(h["price"].where(h["price"] > 0)).groupby(h["SecurityNumber"]).diff()
    h["vol20"] = h.groupby("SecurityNumber", sort=False)["_ret"].transform(lambda v: v.rolling(20, min_periods=5).std())
    h = map_cusip(h, raw)
    if h.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    return h.groupby(["date", "cusip9"], as_index=False)[["adv20", "vol20"]].max()


def _eod_prices(raw) -> pd.DataFrame:
    p = raw.get("p3_eod_prices", pd.DataFrame())
    if p.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    frame = pd.DataFrame({"date": to_day(p["SnapShotDate"]), "cusip9": normalize_cusip9(p["CUSIP_NUMBER"]),
                          "eod_price": pd.to_numeric(p["CURR_PRICE"], errors="coerce")})
    return frame.groupby(["date", "cusip9"], as_index=False).max()


def _threshold(raw) -> pd.DataFrame:
    th = raw.get("beta_threshold_history", pd.DataFrame())
    if th.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    t = pd.DataFrame({"date": knowledge_date("beta_threshold_history", th),
                      "SecurityNumber": pd.to_numeric(th["SecurityNumber"], errors="coerce"),
                      "threshold_flag": _flag(th["ThresholdInd"]),
                      "_order": pd.to_numeric(th["ID"], errors="coerce") if "ID" in th.columns else 0})
    t = map_cusip(t, raw)
    if t.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    t = t.sort_values(["date", "_order"])
    return t.drop_duplicates(["date", "cusip9"], keep="last")[["date", "cusip9", "threshold_flag"]]


def _reg204(raw) -> pd.DataFrame:
    r = raw.get("reg204", pd.DataFrame())
    if r.empty:
        return pd.DataFrame(columns=["date", "cusip9"])
    frame = pd.DataFrame({
        "date": to_day(r["ReportDate"]), "cusip9": normalize_cusip9(r["CUSIP"]),
        "sod": pd.to_numeric(r["SOD_CNS_FailDayQty"], errors="coerce").fillna(0),
        "reg204_htb": r["BorrowDesc"].astype("string").str.upper().str.contains("HTB|HARD", regex=True).fillna(False).astype(float),
        "reg204_threshold": _flag(r["ThresholdIndex"]),
    })
    return frame.groupby(["date", "cusip9"], as_index=False).agg(sod=("sod", "sum"), reg204_htb=("reg204_htb", "max"),
                                                                  reg204_threshold=("reg204_threshold", "max"))


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    cal, raw, stale = ctx.cal, ctx.raw, ctx.settings.max_staleness_bd
    keys = base[["cusip9"]]
    out = pd.DataFrame(index=base.index)

    def join(frame, source, staleness=stale):
        return asof_join(keys, frame, by=["cusip9"], right_date="date",
                         cutoff=cutoff_dates(base["scoring_date"], cal, lag(source)), cal=cal,
                         max_staleness_bd=staleness, audit_name=source)

    sec = join(_security_snapshot(raw), "beta_security")
    for c in ["chill_any", "dtc_frozen", "cns_ineligible", "spread_bps", "log_close_price"]:
        out[c] = sec.get(c, np.nan)
    liq = join(_liquidity(raw), "beta_close_history")
    adv = liq.get("adv20", pd.Series(np.nan, index=base.index)).astype(float)
    out["log_adv20"] = np.log1p(adv.clip(lower=0))
    out["qty_to_adv20"] = (base["quantity"].astype(float) / adv.where(adv > 0)).astype(float)
    out["vol20"] = liq.get("vol20", np.nan)
    eod = join(_eod_prices(raw), "p3_eod_prices")
    eod_price = eod.get("eod_price", pd.Series(np.nan, index=base.index)).astype(float)
    out["price_vs_eod"] = (base["price"].astype(float) / eod_price.where(eod_price > 0) - 1).clip(-1, 1)
    th = join(_threshold(raw), "beta_threshold_history", staleness=None)
    out["threshold_flag"] = th.get("threshold_flag", pd.Series(np.nan, index=base.index)).astype(float).fillna(0.0)
    r204 = pinned_join(keys, _reg204(raw), by=["cusip9"], right_date="date",
                       cutoff=cutoff_dates(base["scoring_date"], cal, lag("reg204")), cal=cal,
                       max_staleness_bd=stale, audit_name="reg204")
    out["reg204_sod_fail_log"] = np.log1p(r204.get("sod", pd.Series(np.nan, index=base.index)).astype(float))
    out["reg204_htb"] = r204.get("reg204_htb", pd.Series(np.nan, index=base.index)).astype(float)
    out["reg204_threshold"] = r204.get("reg204_threshold", pd.Series(np.nan, index=base.index)).astype(float)
    for frame in (sec, liq, eod, th, r204):
        for c in frame.columns:
            if c.startswith("_asof_"):
                out[c] = frame[c]
    return out
