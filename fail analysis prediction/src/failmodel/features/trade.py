"""Trade family: attributes of the trade record, all known at entry (on or before D)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import FeatureContext, cat, num

FAMILY = "trade"
SRC = "beta_trades, p3_trades"
RULE = "Trade record; entry date <= D (population rule)"

SPECS = [
    cat("book", FAMILY, SRC, RULE, "B, P", "Book indicator (B = Beta, P = P3)"),
    num("log_notional", FAMILY, SRC, RULE, "B, P", "log(1 + |net amount|), falling back to quantity x price"),
    num("log_quantity", FAMILY, SRC, RULE, "B, P", "log(1 + |quantity|)"),
    num("log_price", FAMILY, SRC, RULE, "B, P", "log(1 + trade price)"),
    cat("account_type", FAMILY, SRC, RULE, "B, P", "Account type code"),
    cat("counterparty_type", FAMILY, SRC, RULE, "B, P", "Broker, Suspense (P3) or Customer, from the account number"),
    num("dvp_flag", FAMILY, SRC, RULE, "B, P", "DVP/RVP settlement (Beta StandingInstructionCode, P3 DEL_CODE; provisional codes)"),
    cat("capacity", FAMILY, SRC, RULE, "B, P", "Firm capacity code"),
    cat("product_group", FAMILY, SRC, RULE, "B, P", "EQ, CORP, MUNI, FED or OTHER (explicit per-book mapping)"),
    num("exec_hour", FAMILY, SRC, RULE, "B, P", "Execution time in hours"),
    num("late_day", FAMILY, SRC, RULE, "B, P", "Executed at or after settings.late_day_hour"),
    num("is_asof", FAMILY, SRC, RULE, "B, P", "Entered after the trade date"),
    num("entry_lag_bd", FAMILY, SRC, RULE, "B, P", "Business days from trade date to entry"),
    num("entered_on_d", FAMILY, SRC, RULE, "B, P", "Entered on the scoring date"),
    num("settle_cycle_bd", FAMILY, SRC, RULE, "B, P", "Business days from trade date to S"),
    num("non_usd", FAMILY, SRC, RULE, "B, P", "Non-USD settlement currency"),
]


def compute(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    cal = ctx.cal
    out = pd.DataFrame(index=base.index)
    out["book"] = base["book"]
    out["log_notional"] = np.log1p(base["notional"].astype(float).abs())
    out["log_quantity"] = np.log1p(base["quantity"].astype(float).abs())
    out["log_price"] = np.log1p(base["price"].astype(float).clip(lower=0))
    out["account_type"] = base["account_type"]
    out["counterparty_type"] = base["counterparty_type"]
    out["dvp_flag"] = base["dvp_flag"].astype(float)
    out["capacity"] = base["capacity"]
    out["product_group"] = base["product_group"]
    out["exec_hour"] = base["exec_hour"].astype(float)
    out["late_day"] = (base["exec_hour"] >= ctx.settings.late_day_hour).astype(float).where(base["exec_hour"].notna())
    out["is_asof"] = base["is_asof"].astype(float)
    out["entry_lag_bd"] = cal.bd_diff(base["trade_date"], base["entry_date"])
    out["entered_on_d"] = (base["entry_date"] == base["scoring_date"]).astype(float)
    out["settle_cycle_bd"] = cal.bd_diff(base["trade_date"], base["settle_date"])
    out["non_usd"] = base["non_usd"].astype(float)
    return out
