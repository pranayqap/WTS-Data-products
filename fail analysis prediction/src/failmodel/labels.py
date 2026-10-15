"""Trade universe, new-fail labels and Age-1 reconciliation (plan 03).

A trade settling on S is labelled 1 when a matching fail record is on the label snapshot for S, 0 when that
snapshot exists without a match, and NULL when the snapshot is missing (never a negative).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .bizcal import BusinessCalendar, to_day
from .config import Settings

LABEL_COLUMNS = [
    "failed_on_settle", "fail_quantity", "fail_qty_fraction", "fail_notional",
    "fail_settle_date_differs", "match_method", "fail_rows", "label_snapshot_date",
]

EXCLUSION_ORDER = [
    "settle_outside_window", "settle_not_business_day", "entered_after_scoring_date", "cancelled",
    "account_type_excluded", "security_type_excluded", "zero_quantity", "direction_unknown",
]


def scoring_dates(settle_dates: pd.Series, cal: BusinessCalendar) -> pd.Series:
    return cal.add(settle_dates, -1)


def build_trade_universe(
    trades: pd.DataFrame, cal: BusinessCalendar, settings: Settings, label_window: tuple | None = None
) -> pd.DataFrame:
    """Attach D and an exclusion reason to every canonical trade. In-population rows have no reason."""
    start = to_day(label_window[0]) if label_window else to_day(settings.label_start)
    end = to_day(label_window[1]) if label_window else cal.days[-1]
    u = trades.copy()
    u["scoring_date"] = scoring_dates(u["settle_date"], cal)
    rules = population_rules(u, cal, start, end)
    reason = pd.Series(pd.NA, index=u.index, dtype="string")
    for name in reversed(EXCLUSION_ORDER):
        reason = reason.mask(rules[name], name)
    u["exclusion_reason"] = reason
    u["in_population"] = reason.isna()
    return u


def population_rules(u: pd.DataFrame, cal: BusinessCalendar, start, end) -> dict[str, np.ndarray]:
    """Each exclusion rule as a boolean mask over trades that carry ``scoring_date``."""
    beta = u["book"].eq("B")
    rules = {
        "settle_outside_window": (u["settle_date"] < start) | (u["settle_date"] > end) | u["settle_date"].isna(),
        "settle_not_business_day": ~cal.is_business_day(u["settle_date"]),
        "entered_after_scoring_date": u["entry_date"] > u["scoring_date"],
        "cancelled": u["cancelled"].astype(int).eq(1),
        "account_type_excluded": (beta & u["account_type"].eq("8"))
        | (~beta & u["account_type"].isin(["2", "3"])),
        "security_type_excluded": (beta & u["security_type"].eq("P"))
        | (~beta & u["security_type"].isin(["5", "6"])),
        "zero_quantity": u["quantity"].fillna(0).eq(0),
        "direction_unknown": u["direction"].isna(),
    }
    return {name: pd.Series(rules[name], index=u.index).astype("boolean").fillna(False).to_numpy(dtype=bool)
            for name in EXCLUSION_ORDER}


def label_snapshot_dates(universe: pd.DataFrame, cal: BusinessCalendar, settings: Settings) -> pd.Series:
    offset = np.where(universe["book"].eq("B"), settings.beta_snapshot_offset_bd, 0)
    return cal.add(universe["settle_date"], offset)


def snapshot_presence(fails: pd.DataFrame) -> pd.DataFrame:
    """Snapshot dates that exist per book (a date with at least one fail row)."""
    return fails[["book", "snapshot_date"]].drop_duplicates().assign(snapshot_exists=True)


def _aggregate_matches(rows: pd.DataFrame, key: str) -> pd.DataFrame:
    return (
        rows.groupby(["book", "snapshot_date", key], dropna=True)
        .agg(fail_quantity=("quantity", "sum"), fail_notional=("amount", "sum"),
             fail_settle_min=("settle_date", "min"), fail_settle_max=("settle_date", "max"),
             fail_rows=("quantity", "size"))
        .reset_index()
    )


@dataclass
class LabelResult:
    labels: pd.DataFrame
    universe: pd.DataFrame
    fail_matches: pd.DataFrame


def build_labels(trades: pd.DataFrame, fails: pd.DataFrame, cal: BusinessCalendar, settings: Settings,
                 label_window: tuple | None = None) -> LabelResult:
    universe = build_trade_universe(trades, cal, settings, label_window)
    universe["label_snapshot_date"] = label_snapshot_dates(universe, cal, settings)
    pop = universe[universe["in_population"]].copy()

    exact_rows = fails[fails["trade_key"].notna()]
    exact = _aggregate_matches(exact_rows, "trade_key").rename(columns={"snapshot_date": "label_snapshot_date"})
    pop = pop.merge(exact, on=["book", "label_snapshot_date", "trade_key"], how="left")
    pop["match_method"] = np.where(pop["fail_rows"].notna(), "exact", None)

    zero_rows = fails[fails["book"].eq("B") & fails["control_number"].fillna(0).eq(0)]
    if len(zero_rows):
        fb = _aggregate_matches(zero_rows, "fallback_key").rename(columns={"snapshot_date": "label_snapshot_date"})
        fb = fb.add_prefix("fb_").rename(columns={"fb_book": "book", "fb_label_snapshot_date": "label_snapshot_date",
                                                   "fb_fallback_key": "fallback_key"})
        pop = pop.merge(fb, on=["book", "label_snapshot_date", "fallback_key"], how="left")
        use_fb = pop["match_method"].isna() & pop["fb_fail_rows"].notna()
        for col in ["fail_quantity", "fail_notional", "fail_settle_min", "fail_settle_max", "fail_rows"]:
            pop[col] = pop[col].where(~use_fb, pop[f"fb_{col}"])
        pop.loc[use_fb, "match_method"] = "fallback"
        pop = pop.drop(columns=[c for c in pop.columns if c.startswith("fb_")])

    presence = snapshot_presence(fails).rename(columns={"snapshot_date": "label_snapshot_date"})
    pop = pop.merge(presence, on=["book", "label_snapshot_date"], how="left")
    exists = pop["snapshot_exists"].fillna(False).astype(bool)
    matched = pop["match_method"].notna()
    pop["failed_on_settle"] = pd.array(np.where(matched, 1, 0), dtype="Int8")
    pop.loc[~exists & ~matched, "failed_on_settle"] = pd.NA
    pop["fail_quantity"] = pop["fail_quantity"].where(matched, 0.0)
    pop["fail_notional"] = pop["fail_notional"].where(matched, 0.0)
    pop["fail_qty_fraction"] = (pop["fail_quantity"] / pop["quantity"]).clip(upper=1.0).where(matched, 0.0)
    known_settle = pop["fail_settle_min"].notna()
    pop["fail_settle_date_differs"] = (
        matched & known_settle
        & ((pop["fail_settle_min"] != pop["settle_date"]) | (pop["fail_settle_max"] != pop["settle_date"]))
    ).astype("int8")
    pop["fail_rows"] = pop["fail_rows"].fillna(0).astype(int)
    labels = pop.drop(columns=["snapshot_exists", "fail_settle_min", "fail_settle_max", "in_population",
                               "exclusion_reason"])

    fail_matches = _fail_row_matches(universe, fails)
    return LabelResult(labels=labels.reset_index(drop=True), universe=universe, fail_matches=fail_matches)


def _fail_row_matches(universe: pd.DataFrame, fails: pd.DataFrame) -> pd.DataFrame:
    """For every fail row on a label snapshot, which trade (if any) it belongs to and whether in population."""
    ref = universe[["book", "trade_key", "fallback_key", "label_snapshot_date", "in_population",
                    "exclusion_reason", "product_group", "security_type"]]
    f = fails.reset_index(drop=True).copy()
    f["_row"] = np.arange(len(f))
    exact = f.merge(
        ref.drop(columns=["fallback_key"]).rename(columns={"label_snapshot_date": "snapshot_date"}),
        on=["book", "snapshot_date", "trade_key"], how="inner", suffixes=("", "_trade"),
    ).drop_duplicates("_row")
    exact["match_method"] = "exact"
    rest = f[~f["_row"].isin(exact["_row"]) & f["book"].eq("B") & f["control_number"].fillna(0).eq(0)]
    fb = rest.merge(
        ref.drop(columns=["trade_key"]).rename(columns={"label_snapshot_date": "snapshot_date"}),
        on=["book", "snapshot_date", "fallback_key"], how="inner", suffixes=("", "_trade"),
    ).sort_values("in_population", ascending=False).drop_duplicates("_row")
    fb["match_method"] = "fallback"
    keep = ["_row", "match_method", "in_population", "exclusion_reason", "product_group_trade", "security_type_trade"]
    matches = pd.concat([exact.reindex(columns=keep), fb.reindex(columns=keep)], ignore_index=True)
    out = f.merge(matches, on="_row", how="left")
    out["matched"] = out["match_method"].notna()
    return out.drop(columns="_row")


def reconcile(result: LabelResult, cal: BusinessCalendar, settings: Settings) -> dict[str, pd.DataFrame]:
    """Age-1 fails straight from the snapshots (report filters) vs matched positive labels."""
    fm = result.fail_matches
    offset = np.where(fm["book"].eq("B"), settings.beta_snapshot_offset_bd, 0)
    label_snap_for_settle = cal.add(fm["settle_date"], offset)
    p3_sec_excluded = fm["book"].eq("P") & fm["security_type_trade"].isin(["5", "6"]).fillna(False)
    age1 = fm[
        fm["in_report_population"].eq(1)
        & fm["settle_date"].notna()
        & (fm["snapshot_date"] == label_snap_for_settle)
        & ~p3_sec_excluded
    ].copy()
    in_window = age1["settle_date"] >= to_day(settings.label_start)
    age1 = age1[in_window]

    in_pop = age1["in_population"].astype("boolean").fillna(False).astype(bool)
    is_p3 = age1["book"].eq("P")
    if settings.p3_trade_record_types is None:
        p3_trade_type = is_p3
    else:
        p3_trade_type = is_p3 & age1["record_type"].isin(settings.p3_trade_record_types).fillna(False)
    has_control = p3_trade_type | (~is_p3 & age1["control_number"].fillna(0).ne(0))
    age1["category"] = np.select(
        [age1["matched"] & in_pop, age1["matched"] & ~in_pop, ~age1["matched"] & has_control, ~age1["matched"] & is_p3],
        ["matched_in_population", "matched_excluded_trade", "unmatched_trade_missing", "unmatched_non_trade_record"],
        default="unmatched_zero_control",
    )

    daily = (
        age1.groupby(["settle_date", "book", "direction", "category"]).size().unstack("category", fill_value=0)
        .reset_index()
    )
    labels = result.labels
    pos = (
        labels[labels["failed_on_settle"].eq(1)]
        .groupby(["settle_date", "book", "direction"]).size().rename("positive_labels").reset_index()
    )
    daily = daily.merge(pos, on=["settle_date", "book", "direction"], how="outer").fillna(0)

    summary = age1.groupby(["book", "category"]).size().unstack("category", fill_value=0)
    for col in ["matched_in_population", "matched_excluded_trade", "unmatched_trade_missing", "unmatched_zero_control",
                "unmatched_non_trade_record"]:
        if col not in summary.columns:
            summary[col] = 0
    summary["age1_fails"] = summary.sum(axis=1)
    summary["trade_originated"] = (
        summary["matched_in_population"] + summary["matched_excluded_trade"] + summary["unmatched_trade_missing"]
    )
    summary["match_rate_trade_originated"] = (
        (summary["matched_in_population"] + summary["matched_excluded_trade"]) / summary["trade_originated"]
    )
    excluded = (
        age1[age1["category"].eq("matched_excluded_trade")]
        .groupby(["book", "exclusion_reason"]).size().rename("fails").reset_index()
    )
    differs = (
        labels[labels["failed_on_settle"].eq(1)].groupby("book")["fail_settle_date_differs"].mean()
        .rename("share_positive_with_different_fail_settle").reset_index()
    )
    by_record_type = (
        age1[is_p3].assign(matched=age1.loc[is_p3, "matched"].astype(int))
        .groupby("record_type", dropna=False)["matched"].agg(["size", "mean"])
        .rename(columns={"size": "age1_fails", "mean": "match_rate"}).reset_index()
    )
    return {"daily": daily, "summary": summary.reset_index(), "excluded_reasons": excluded,
            "settle_differs": differs, "p3_by_record_type": by_record_type}


def base_rates(labels: pd.DataFrame) -> pd.DataFrame:
    known = labels[labels["failed_on_settle"].notna()]
    grouped = known.groupby(["book", "direction"])
    out = grouped.agg(
        trades=("failed_on_settle", "size"),
        fails=("failed_on_settle", "sum"),
        settle_dates=("settle_date", "nunique"),
        failed_notional=("fail_notional", "sum"),
        notional=("notional", "sum"),
    ).reset_index()
    out["base_rate"] = out["fails"] / out["trades"]
    out["fails_per_day"] = out["fails"] / out["settle_dates"]
    out["trades_per_day"] = out["trades"] / out["settle_dates"]
    out["notional_fail_rate"] = out["failed_notional"] / out["notional"]
    unknown = (
        labels.assign(unknown_label_rows=labels["failed_on_settle"].isna().astype(int))
        .groupby(["book", "direction"])["unknown_label_rows"].sum().reset_index()
    )
    return out.merge(unknown, on=["book", "direction"], how="left")


def label_daily_counts(labels: pd.DataFrame) -> pd.DataFrame:
    """Same layout as sql/labels/01_label_reference.sql, for a server-side cross-check."""
    f = labels.assign(
        positive=labels["failed_on_settle"].eq(1).fillna(False).astype(int),
        unknown=labels["failed_on_settle"].isna().astype(int),
        exact=labels["match_method"].eq("exact").fillna(False).astype(int),
        fallback=labels["match_method"].eq("fallback").fillna(False).astype(int),
    )
    return (
        f.groupby(["settle_date", "book", "direction"])
        .agg(trades=("positive", "size"), positives=("positive", "sum"), unknown=("unknown", "sum"),
             exact_positives=("exact", "sum"), fallback_positives=("fallback", "sum"))
        .reset_index()
    )


def exclusion_counts(universe: pd.DataFrame) -> pd.DataFrame:
    return (
        universe.assign(reason=universe["exclusion_reason"].fillna("in_population"))
        .groupby(["book", "reason"]).size().rename("trades").reset_index()
    )
