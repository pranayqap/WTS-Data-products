"""Affirmation family: Beta institutional confirm state, joined on the trade's BETAUniqueKey."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..asof import asof_join, cutoff_dates
from ..bizcal import to_day
from ..sources import lag
from .base import FeatureContext, num

FAMILY = "affirmation"
_SRC = ("beta_id_confirms", "SnapshotDate <= D - lag (each confirm archived once)")

SPECS = [
    num("confirm_seen", FAMILY, *_SRC, "B (DVP)", "An institutional confirm for the trade is visible"),
    num("confirm_matched", FAMILY, *_SRC, "B (DVP)", "Visible confirm is matched"),
    num("confirm_affirmed", FAMILY, *_SRC, "B (DVP)", "Visible confirm is affirmed"),
]


def _confirms(raw) -> pd.DataFrame:
    c = raw.get("beta_id_confirms", pd.DataFrame())
    if c.empty:
        return pd.DataFrame(columns=["date", "unique_ref"])
    f = pd.DataFrame({
        "date": to_day(c["SnapshotDate"]),
        "unique_ref": c["BetaTradeUniqueKey"].astype("string").str.strip(),
        "matched": c["MatchedIndicator"].astype("string").str.strip().str.upper().eq("Y").astype(float),
        "affirmed": c["ConfirmAffirmIndicator"].astype("string").str.strip().str.upper().eq("A").astype(float),
    })
    return f.sort_values("date").drop_duplicates(["date", "unique_ref"], keep="last")


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    keys = base[["unique_ref"]].assign(unique_ref=base["unique_ref"].astype("string").str.strip())
    cf = asof_join(keys, _confirms(ctx.raw), by=["unique_ref"], right_date="date",
                   cutoff=cutoff_dates(base["scoring_date"], ctx.cal, lag("beta_id_confirms")), cal=ctx.cal,
                   max_staleness_bd=None, audit_name="beta_id_confirms")
    eligible = base["book"].eq("B") & base["dvp_flag"].astype(float).eq(1)
    seen = cf["_asof_beta_id_confirms"].notna()
    out = pd.DataFrame(index=base.index)
    out["confirm_seen"] = seen.astype(float)
    out["confirm_matched"] = cf.get("matched", pd.Series(np.nan, index=base.index)).astype(float).fillna(0.0)
    out["confirm_affirmed"] = cf.get("affirmed", pd.Series(np.nan, index=base.index)).astype(float).fillna(0.0)
    out.loc[~eligible, ["confirm_seen", "confirm_matched", "confirm_affirmed"]] = np.nan
    out["_asof_beta_id_confirms"] = cf["_asof_beta_id_confirms"].where(eligible)
    return out
