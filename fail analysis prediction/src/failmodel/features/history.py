"""History family: smoothed past fail rates from labels with S <= D - label_availability_lag_bd.

Rates are shrunk toward the trailing 250-business-day rate of the book and direction:
(fails + k * prior) / (trades + k), with k = settings.history_prior_strength.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..asof import AUDIT_PREFIX, _as_key
from ..bizcal import BusinessCalendar
from ..config import Settings
from .base import FeatureContext, num

FAMILY = "history"
_SRC = "phase-1 labels"
_RULE = "Settle date S' <= D - label_availability_lag_bd"

KEYS = {
    "cp": (["book", "direction", "counterparty_id"], "counterparty"),
    "acct": (["book", "direction", "account"], "account"),
    "cusip": (["direction", "cusip9"], "CUSIP (both books)"),
}
WINDOWS = Settings.history_windows_bd

SPECS = (
    [num(f"{k}_fail_rate_{w}", FAMILY, _SRC, _RULE, "B, P", f"Smoothed fail rate by {label}, last {w} business days")
     for k, (_, label) in KEYS.items() for w in WINDOWS]
    + [num(f"{k}_days_since_fail", FAMILY, _SRC, _RULE, "B, P", f"Business days since the last fail by {label}")
       for k, (_, label) in KEYS.items()]
    + [num("cp_trades_60_log", FAMILY, _SRC, _RULE, "B, P", "log(1 + labelled trades with the counterparty, last 60 business days)"),
       num("global_fail_rate_250", FAMILY, _SRC, _RULE, "B, P", "Fail rate of the book and direction, last 250 business days")]
)


def _events(history: pd.DataFrame, cal: BusinessCalendar) -> pd.DataFrame:
    h = history[history["failed_on_settle"].notna()].copy()
    h["idx"] = cal.index_on_or_before(h["settle_date"])
    h["y"] = h["failed_on_settle"].astype(int)
    return h[h["idx"] >= 0]


def _cumulative(events: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    g = events.groupby(keys + ["idx"], dropna=False).agg(n=("y", "size"), f=("y", "sum")).reset_index()
    g = _as_key(g, keys).sort_values("idx")
    g["cn"] = g.groupby(keys)["n"].cumsum()
    g["cf"] = g.groupby(keys)["f"].cumsum()
    g["last_fail_idx"] = g["idx"].where(g["f"] > 0)
    g["last_fail_idx"] = g.groupby(keys)["last_fail_idx"].ffill()
    return g[keys + ["idx", "cn", "cf", "last_fail_idx"]]


def _lookup(q: pd.DataFrame, cum: pd.DataFrame, keys: list[str], at: np.ndarray) -> pd.DataFrame:
    """Cumulative trades/fails per key over settle index <= at (zeros when none)."""
    left = _as_key(q[keys], keys)
    left["_at"] = at.astype("int64")
    left["_row"] = np.arange(len(left))
    m = pd.merge_asof(left.sort_values("_at"), cum.astype({"idx": "int64"}), left_on="_at", right_on="idx",
                      by=keys, direction="backward")
    m = m.set_index("_row").sort_index()
    return pd.DataFrame({"cn": m["cn"].fillna(0).to_numpy(), "cf": m["cf"].fillna(0).to_numpy(),
                         "last_fail_idx": m["last_fail_idx"].to_numpy(), "idx": m["idx"].to_numpy()}, index=q.index)


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    cal, s = ctx.cal, ctx.settings
    out = pd.DataFrame(index=base.index)
    d_idx = cal.index_on_or_before(base["scoring_date"])
    cut = d_idx - s.label_availability_lag_bd
    events = _events(ctx.history, cal)
    k = s.history_prior_strength

    gkeys = ["book", "direction"]
    gcum = _cumulative(events, gkeys)
    g_hi = _lookup(base, gcum, gkeys, cut)
    g_lo = _lookup(base, gcum, gkeys, cut - 250)
    gn, gf = g_hi["cn"] - g_lo["cn"], g_hi["cf"] - g_lo["cf"]
    prior = (gf / gn.where(gn > 0)).astype(float)
    out["global_fail_rate_250"] = prior
    last_idx = g_hi["idx"]
    audit = pd.Series(pd.NaT, index=base.index, dtype="datetime64[ns]")
    ok = last_idx.notna()
    audit[ok] = cal.days.values[last_idx[ok].astype(int).to_numpy()]
    out[AUDIT_PREFIX + "labels"] = audit

    for name, (keys, _) in KEYS.items():
        missing = base[keys].isna().any(axis=1)
        cum = _cumulative(events, keys)
        hi = _lookup(base, cum, keys, cut)
        for w in WINDOWS:
            lo = _lookup(base, cum, keys, cut - w)
            n, f = hi["cn"] - lo["cn"], hi["cf"] - lo["cf"]
            shrunk = (f + k * prior) / (n + k)
            raw_rate = f / n.where(n > 0)
            rate = shrunk.where(prior.notna(), raw_rate)
            out[f"{name}_fail_rate_{w}"] = rate.where(~missing).astype(float)
            if name == "cp" and w == 60:
                out["cp_trades_60_log"] = np.log1p(n).where(~missing).astype(float)
        since = pd.Series(d_idx, index=base.index) - hi["last_fail_idx"]
        out[f"{name}_days_since_fail"] = since.where(~missing).astype(float)
    return out[[spec.name for spec in SPECS] + [AUDIT_PREFIX + "labels"]]
