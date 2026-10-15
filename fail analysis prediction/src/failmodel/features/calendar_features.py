"""Calendar family: position of S in the business calendar and the firm's settlement load known at D.

Load counts use every canonical trade entered on or before D (current cancellation status is ignored
because it is not known at D), excluding the account and security types the report never shows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import FeatureContext, num

FAMILY = "calendar"
_CAL = ("business_calendar", "Known in advance")
_LOAD = ("beta_trades, p3_trades", "Trades entered <= D")

SPECS = [
    num("settle_dow", FAMILY, *_CAL, "B, P", "Day of week of S (0 = Monday)"),
    num("settle_month_end", FAMILY, *_CAL, "B, P", "S is the last business day of the month"),
    num("settle_quarter_end", FAMILY, *_CAL, "B, P", "S is the last business day of the quarter"),
    num("settle_post_holiday", FAMILY, *_CAL, "B, P", "S is the first business day after a market holiday"),
    num("book_trades_on_s_log", FAMILY, *_LOAD, "B, P", "log(1 + trades in the book settling on S)"),
    num("book_trades_entered_on_d_log", FAMILY, *_LOAD, "B, P", "log(1 + trades entered in the book on D)"),
    num("cusip_trades_on_s", FAMILY, *_LOAD, "B, P", "Trades in the CUSIP and book settling on S"),
    num("cusip_net_deliver_qty_log", FAMILY, *_LOAD, "B, P", "Signed log(1 + |deliver - receive quantity|) in the CUSIP on S"),
    num("trade_share_of_cusip_qty", FAMILY, *_LOAD, "B, P", "Trade quantity / same-direction quantity in the CUSIP on S"),
]


def _load_universe(universe: pd.DataFrame) -> pd.DataFrame:
    reasons = universe["exclusion_reason"].fillna("")
    keep = ~reasons.isin(["account_type_excluded", "security_type_excluded", "zero_quantity", "direction_unknown"])
    return universe.loc[keep, ["book", "settle_date", "entry_date", "cusip9", "direction", "quantity"]]


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    out = pd.DataFrame(index=base.index)
    flags = ctx.cal.month_end_flags().rename(columns={"day": "settle_date"})
    f = base[["settle_date"]].merge(flags, on="settle_date", how="left").set_index(base.index)
    out["settle_dow"] = base["settle_date"].dt.dayofweek.astype(float)
    out["settle_month_end"] = f["is_month_end"].astype(float)
    out["settle_quarter_end"] = f["is_quarter_end"].astype(float)
    out["settle_post_holiday"] = f["is_post_holiday"].astype(float)

    load = _load_universe(ctx.universe)
    entered = load.groupby(["book", "entry_date"]).size().rename("entered").reset_index()
    u = load.merge(base[["settle_date", "scoring_date"]].drop_duplicates(), on="settle_date", how="inner")
    u = u[u["entry_date"] <= u["scoring_date"]]
    sd = ["book", "settle_date", "scoring_date"]
    book_s = u.groupby(sd).size().rename("book_s").reset_index()
    signed = np.where(u["direction"].eq("D"), 1.0, -1.0) * u["quantity"].astype(float)
    cusip = (
        u.assign(signed=signed)
        .groupby(sd + ["cusip9", "direction"])
        .agg(n=("quantity", "size"), qty=("quantity", "sum"), signed=("signed", "sum"))
        .reset_index()
    )
    cusip_tot = cusip.groupby(sd + ["cusip9"]).agg(n=("n", "sum"), net=("signed", "sum")).reset_index()

    keys = base[["book", "settle_date", "scoring_date", "cusip9", "direction", "quantity"]]
    m = keys.merge(book_s, on=sd, how="left")
    m = m.merge(cusip_tot, on=sd + ["cusip9"], how="left")
    m = m.merge(cusip[sd + ["cusip9", "direction", "qty"]], on=sd + ["cusip9", "direction"], how="left")
    m = m.merge(entered.rename(columns={"entry_date": "scoring_date"}), on=["book", "scoring_date"], how="left")
    m.index = base.index
    out["book_trades_on_s_log"] = np.log1p(m["book_s"].astype(float).fillna(0))
    out["book_trades_entered_on_d_log"] = np.log1p(m["entered"].astype(float).fillna(0))
    out["cusip_trades_on_s"] = m["n"].astype(float).fillna(0)
    net = m["net"].astype(float).fillna(0)
    out["cusip_net_deliver_qty_log"] = np.sign(net) * np.log1p(net.abs())
    qty = m["qty"].astype(float)
    out["trade_share_of_cusip_qty"] = base["quantity"].astype(float) / qty.where(qty > 0)
    return out
