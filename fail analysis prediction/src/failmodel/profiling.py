"""Profiling checks P1 to P8 (plan 02) over landed extracts, with evidence-based config recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .bizcal import BusinessCalendar, to_day
from .canonical import P3_KEY_COLS, canonical_fails, canonical_trades
from .config import Settings
from .extract import load_raw
from .labels import base_rates, build_labels, reconcile
from .sources import SOURCES

SCORING_HOUR = 22.5
P3_SOURCE_SWITCH = pd.Timestamp("2025-08-14")


def md_table(frame: pd.DataFrame, max_rows: int = 30, floatfmt: str = "{:.4g}") -> str:
    if frame is None or frame.empty:
        return "_(no rows)_"
    frame = frame.head(max_rows)
    cols = [str(c) for c in frame.columns]

    def fmt(v):
        if isinstance(v, (float, np.floating)):
            return "" if np.isnan(v) else floatfmt.format(v)
        if isinstance(v, pd.Timestamp):
            return v.strftime("%Y-%m-%d")
        return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(fmt(v) for v in row) + " |")
    return "\n".join(lines)


@dataclass
class Check:
    id: str
    question: str
    status: str
    summary: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass
class ProfileResult:
    checks: list[Check]
    recommendations: dict[str, tuple[object, str]]


def load_all(raw_dir) -> dict[str, pd.DataFrame]:
    return {sid: load_raw(sid, raw_dir) for sid in SOURCES}


def calendar_from(raw: dict[str, pd.DataFrame]) -> BusinessCalendar:
    cal_frame = raw.get("business_calendar")
    if cal_frame is None or cal_frame.empty:
        raise ValueError("business_calendar extract is required")
    return BusinessCalendar.from_date_master(cal_frame)


def _landing_hours(frame: pd.DataFrame, date_col: str, time_col: str) -> pd.Series:
    if frame.empty or time_col not in frame.columns:
        return pd.Series(dtype=float)
    per_date = frame.groupby(to_day(frame[date_col]))[time_col].max()
    loaded = pd.to_datetime(per_date, errors="coerce")
    return ((loaded - per_date.index) / pd.Timedelta(hours=1)).dropna()


def p1_history(raw: dict[str, pd.DataFrame], cal: BusinessCalendar) -> Check:
    rows = []
    for sid, frame in raw.items():
        src = SOURCES[sid]
        if frame.empty or src.asof_col not in frame.columns:
            rows.append({"source": sid, "status": "no extract"})
            continue
        dates = to_day(pd.to_datetime(frame[src.asof_col], errors="coerce")).dropna()
        unique = pd.DatetimeIndex(dates.unique()).sort_values()
        expected = cal.between(unique.min(), unique.max())
        missing = expected.difference(unique)
        non_bd = unique.difference(cal.days)
        rows.append({
            "source": sid, "status": "ok" if len(missing) == 0 else "gaps",
            "first": unique.min(), "last": unique.max(), "dates_with_rows": len(unique),
            "business_days_in_range": len(expected), "missing_business_days": len(missing),
            "first_missing": ", ".join(d.strftime("%Y-%m-%d") for d in missing[:5]),
            "non_business_dates": len(non_bd), "rows": len(frame),
        })
    table = pd.DataFrame(rows)
    missing_extracts = (table["status"] == "no extract").sum()
    gaps = table.loc[table["status"] == "gaps", "source"].tolist()
    summary = f"{len(table) - missing_extracts} of {len(table)} sources extracted. Sources with gaps: {', '.join(gaps) or 'none'}."
    return Check("P1", "History depth and gaps per source", "ok" if not gaps else "warn", summary, {"history": table})


def p2_snapshot_timing(raw: dict[str, pd.DataFrame], fails: pd.DataFrame, cal: BusinessCalendar,
                       settings: Settings) -> tuple[Check, dict]:
    recs = {}
    landing = _landing_hours(raw["beta_fail_snapshots"], "SnapShotDate", "UpdateDateTime")
    align_rows = []
    if not fails.empty:
        first = fails.dropna(subset=["settle_date"]).groupby(["book", "fail_key"]).agg(
            first_snapshot=("snapshot_date", "min"), settle_date=("settle_date", "min"))
        first = first[first["settle_date"] >= to_day(settings.label_start)]
        lag = cal.bd_diff(first["settle_date"], first["first_snapshot"])
        dist = lag.groupby(level="book").value_counts().rename("fails").reset_index()
        dist.columns = ["book", "first_snapshot_minus_settle_bd", "fails"]
        align_rows = dist.sort_values(["book", "first_snapshot_minus_settle_bd"])
    align = pd.DataFrame(align_rows)
    status, notes = "ok", []
    if not align.empty and (align["book"] == "B").any():
        beta = align[align["book"] == "B"]
        mode = int(beta.loc[beta["fails"].idxmax(), "first_snapshot_minus_settle_bd"])
        recs["beta_snapshot_offset_bd"] = (max(mode, 0), f"modal first-appearance lag for Beta fails is {mode} business days")
    else:
        status = "blocked"
        notes.append("no Beta fail snapshots")
    if len(landing):
        med, p95 = landing.median(), landing.quantile(0.95)
        offset = recs.get("beta_snapshot_offset_bd", (settings.beta_snapshot_offset_bd, ""))[0]
        late = p95 > SCORING_HOUR
        recs["label_availability_lag_bd"] = (
            int(offset + (1 if late else 0)),
            f"Beta snapshot lands {med:.1f}h (median) / {p95:.1f}h (p95) after midnight of its SnapShotDate; "
            f"{'after' if late else 'before'} 22:30 ET",
        )
        recs["lag.beta_fail_snapshots"] = (1 if late else 0, "Beta fail snapshot landing time")
        notes.append(f"Beta snapshot landing hours: median {med:.1f}, p95 {p95:.1f}")
    else:
        notes.append("Beta UpdateDateTime missing; landing time unknown")
    landing_table = pd.DataFrame({"hours_after_snapshot_midnight": landing.describe()}).reset_index() if len(landing) else pd.DataFrame()
    return Check("P2", "Beta snapshot business day and landing time", status, "; ".join(notes),
                 {"first_appearance_alignment": align, "beta_landing_hours": landing_table}), recs


def p3_borrow_runs(raw: dict[str, pd.DataFrame]) -> Check:
    slb, bfn = raw["stock_loan_borrow"], raw["borrow_for_needs"]
    if slb.empty and bfn.empty:
        return Check("P3", "StockLoanBorrow / BorrowForNeeds run timing", "blocked",
                     "No extract. Neither table has a load timestamp, so run timing needs msdb job history or a WSI answer "
                     "(request section 5, question 3). Both stay conditional and unused in v1.")
    tables = {}
    for name, frame in (("stock_loan_borrow", slb), ("borrow_for_needs", bfn)):
        if not frame.empty:
            tables[name] = frame.groupby(to_day(frame["SettleDate"])).size().describe().rename("rows_per_settle_date").reset_index()
    return Check("P3", "StockLoanBorrow / BorrowForNeeds run timing", "blocked",
                 "Rows per SettleDate profiled; run timing still needs msdb or WSI confirmation. Sources stay conditional.", tables)


def p4_landing(raw: dict[str, pd.DataFrame]) -> tuple[Check, dict]:
    specs = [("beta_security", "SnapShotDate", "UpdateDateTime"), ("beta_segregation", "SnapShotDate", "UpdateDateTime"),
             ("smartloan_avail", "FileDate", "LastUpdateDateTime"), ("cns_p3", "PositionDate", "UpDateDateTime"),
             ("cns_beta", "PositionDate", "UpDateDateTime"), ("p3_rpe_fails", "SnapShotDate", "UpdateDateTime")]
    rows, recs = [], {}
    for sid, date_col, time_col in specs:
        hours = _landing_hours(raw.get(sid, pd.DataFrame()), date_col, time_col)
        if not len(hours):
            rows.append({"source": sid, "status": "no timestamp or extract"})
            continue
        p95 = hours.quantile(0.95)
        lag = 0 if p95 <= SCORING_HOUR else 1
        recs[f"lag.{sid}"] = (lag, f"p95 landing {p95:.1f}h after midnight of the as-of date")
        rows.append({"source": sid, "status": "ok", "median_hours": hours.median(), "p95_hours": p95,
                     "recommended_lag_bd": lag, "registry_lag_bd": SOURCES[sid].availability_lag_bd})
    table = pd.DataFrame(rows)
    return Check("P4", "Landing time of daily snapshots vs 22:30 ET", "ok" if recs else "blocked",
                 f"{len(recs)} sources timed. Sources without a timestamp keep the conservative 1-day lag.",
                 {"landing": table}), recs


def p5_join(raw: dict[str, pd.DataFrame], recon: dict[str, pd.DataFrame]) -> tuple[Check, dict]:
    summary = recon["summary"]
    by_type = recon["p3_by_record_type"]
    tables = {"age1_reconciliation": summary, "p3_age1_by_record_type": by_type}
    p3 = raw["p3_trades"]
    recs = {}
    if not by_type.empty:
        trade_types = tuple(sorted(str(t) for t in by_type.loc[by_type["match_rate"] >= 0.5, "record_type"].dropna()))
        recs["p3_trade_record_types"] = (
            trade_types,
            "P3 RECORD_TYPE codes whose Age-1 fails mostly match a trade: "
            + ", ".join(f"{r.record_type} {r.match_rate:.0%} of {r.age1_fails}" for r in by_type.itertuples()),
        )
    if not p3.empty:
        keyed = p3.groupby(P3_KEY_COLS, dropna=False).agg(
            rows=("SEQ_NBR", "size"), seq_numbers=("SEQ_NBR", "nunique"),
            sides=("SIDE_CODE", "nunique"), settle_dates=("SETTLE_FULL_DATE", "nunique"))
        dist = keyed.value_counts().rename("trade_keys").reset_index()
        tables["p3_rows_per_key"] = dist
        multi_seq = (keyed["seq_numbers"] > 1).mean()
        multi_side = (keyed["sides"] > 1).mean()
        rule = "latest_seq_nbr"
        recs["p3_dedup_rule"] = (rule, f"{multi_seq:.1%} of P3 keys have several SEQ_NBR, {multi_side:.1%} several sides")
    rates = summary.set_index("book")["match_rate_trade_originated"].to_dict() if not summary.empty else {}
    ok = all(v >= 0.9 for v in rates.values()) and bool(rates)
    text = ", ".join(f"{b}: {r:.1%}" for b, r in rates.items()) or "no Age-1 fails"
    return Check("P5", "Trade-to-fail join hit rates", "ok" if ok else "warn",
                 f"Match rate of trade-originated Age-1 fails: {text} (target at least 90%).", tables), recs


def p6_semantics(raw: dict[str, pd.DataFrame], fails: pd.DataFrame, fail_matches: pd.DataFrame,
                 trades: pd.DataFrame) -> tuple[Check, dict]:
    tables, recs, notes = {}, {}, []
    p3f = fails[fails["book"] == "P"]
    if len(p3f):
        ct = pd.crosstab(p3f["raw_buy_sell"].fillna("?"), p3f["direction"].fillna("?")).reset_index()
        tables["p3_buy_sell_vs_quantity_sign"] = ct
        agree = (p3f["raw_buy_sell"].map({"B": "D", "S": "R"}) == p3f["direction"]).mean()
        notes.append(f"P3 BUY_SELL agrees with quantity sign on {agree:.1%} of fail rows")
    m = fail_matches[fail_matches["matched"]]
    if len(m):
        joined = m.merge(trades[["trade_key", "direction"]].rename(columns={"direction": "trade_direction"}),
                         on="trade_key", how="left")
        joined = joined.dropna(subset=["trade_direction"])
        if len(joined):
            agree = joined.groupby("book").apply(lambda g: (g["trade_direction"] == g["direction"]).mean(),
                                                 include_groups=False).rename("direction_agreement").reset_index()
            tables["trade_vs_fail_direction"] = agree
            notes.append("trade/fail direction agreement: " + ", ".join(f"{r.book} {r.direction_agreement:.1%}" for r in agree.itertuples()))
    bf = fails[fails["book"] == "B"]
    if len(bf):
        zero = bf["control_number"].fillna(0).eq(0).mean()
        notes.append(f"Beta fail rows with ControlNumber = 0: {zero:.1%}")
    for sid in ("cns_p3", "cns_beta"):
        frame = raw[sid]
        if not frame.empty:
            date_col = SOURCES[sid].asof_col
            cycles = frame.groupby("Cycle").agg(rows=("Cycle", "size"), dates=(date_col, "nunique")).reset_index()
            tables[f"{sid}_cycles"] = cycles
            last = sorted(cycles["Cycle"].dropna().astype(str))[-1]
            recs["cns_eod_cycle"] = (last, f"latest cycle code in {sid}: {', '.join(cycles['Cycle'].astype(str))}")
    for book, col in (("B", "standing_instruction"), ("P", "standing_instruction")):
        sub = trades[trades["book"] == book]
        if len(sub):
            tables[f"{book}_settlement_instruction_codes"] = sub[col].value_counts(dropna=False).rename("trades").reset_index()
    notes.append("DVP mapping still needs the current-state comparison in sql/profiling/04 (part 5)")
    return Check("P6", "Code semantics (direction, control numbers, CNS cycle, DVP codes)", "ok", "; ".join(notes), tables), recs


def p7_base_rates(labels: pd.DataFrame) -> Check:
    table = base_rates(labels)
    text = ", ".join(f"{r.book}/{r.direction} {r.base_rate:.2%}" for r in table.itertuples())
    return Check("P7", "Base rates by book and direction", "ok", f"Label base rates: {text}.", {"base_rates": table})


RPE_JOIN = ["snap", "acct", "tran", "trade_date", "market", "cusip9"]


def _rpe_join_frame(frame: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame({
        "snap": to_day(frame[cols["snap"]]), "settle": to_day(frame[cols["settle"]]),
        "acct": frame[cols["acct"]].astype("string").str.strip(),
        "tran": frame[cols["tran"]].astype("string").str.strip(),
        "trade_date": to_day(frame[cols["trade_date"]]),
        "market": frame[cols["market"]].astype("string").str.strip(),
        "cusip9": frame[cols["cusip"]].astype("string").str.strip().replace("", "000000000").fillna("000000000").str[:9],
        "acct_type": frame[cols["acct_type"]].astype("string").str.strip(),
    })
    return out[(out["settle"] == out["snap"]) & ~out["acct_type"].isin(["2", "3"])]


def p3_rpe_coverage(eod: pd.DataFrame, rpe: pd.DataFrame) -> pd.DataFrame:
    """Age-1 key coverage between the RPE files and EODFAIMST on the join key of the report's combined view."""
    e = _rpe_join_frame(eod, {"snap": "SnapShotDate", "settle": "SETTLE_DATE", "acct": "ACCT_NBR", "tran": "TRAN_NBR",
                              "trade_date": "TRADE_FULL_DATE", "market": "MARKET", "cusip": "CUSIP_NUMBER",
                              "acct_type": "ACCT_TYPE"})
    r = _rpe_join_frame(rpe, {"snap": "SnapShotDate", "settle": "SettleDate", "acct": "AccountNumber", "tran": "TranNbr",
                              "trade_date": "TradeDate", "market": "Market", "cusip": "CUSIP", "acct_type": "AccountType"})
    e = e[e["snap"] >= P3_SOURCE_SWITCH]
    r = r[r["snap"] >= P3_SOURCE_SWITCH]
    e_keys = e[RPE_JOIN].drop_duplicates().assign(in_eod=True)
    r_keys = r[RPE_JOIN].drop_duplicates().assign(in_rpe=True)
    both = e_keys.merge(r_keys, on=RPE_JOIN, how="outer")
    rpe_rows = both["in_rpe"].fillna(False).astype(bool)
    eod_rows = both["in_eod"].fillna(False).astype(bool)
    return pd.DataFrame({
        "measure": ["rpe_age1_keys_found_in_eodfaimst", "eodfaimst_age1_keys_found_in_rpe"],
        "keys": [int(rpe_rows.sum()), int(eod_rows.sum())],
        "match_share": [(eod_rows & rpe_rows).sum() / max(rpe_rows.sum(), 1),
                        (eod_rows & rpe_rows).sum() / max(eod_rows.sum(), 1)],
    })


def p8_switch(raw: dict[str, pd.DataFrame]) -> Check:
    title = "2025-08-14 P3 report source switch (EODFAIMST to RPE200/RPE201)"
    frame = raw["p3_fail_snapshots"]
    if frame.empty:
        return Check("P8", title, "blocked", "No P3 fail snapshots.")
    daily = frame.groupby(to_day(frame["SnapShotDate"])).size()
    before, after = daily[daily.index < P3_SOURCE_SWITCH], daily[daily.index >= P3_SOURCE_SWITCH]
    table = pd.DataFrame({
        "period": ["before", "after"], "snapshot_dates": [len(before), len(after)],
        "mean_rows": [before.mean() if len(before) else np.nan, after.mean() if len(after) else np.nan],
        "std_rows": [before.std() if len(before) else np.nan, after.std() if len(after) else np.nan],
    })
    tables = {"eodfaimst_daily_rows": table}
    if not len(after):
        return Check("P8", title, "blocked",
                     "Extract window ends before 2025-08-14; rerun once post-switch data is extracted.", tables)
    ratio = after.mean() / before.mean() if len(before) else np.nan
    notes = [f"mean daily EODFAIMST rows after / before the switch: {ratio:.2f}"]
    status = "ok" if 0.7 <= ratio <= 1.3 else "warn"
    rpe = raw.get("p3_rpe_fails", pd.DataFrame())
    if rpe.empty:
        notes.append("no RPE extract, so EODFAIMST cannot yet be reconciled with the report's post-switch source")
        status = "warn"
    else:
        coverage = p3_rpe_coverage(frame, rpe)
        tables["age1_key_coverage_after_switch"] = coverage
        share = coverage.set_index("measure")["match_share"]
        notes.append(f"{share['rpe_age1_keys_found_in_eodfaimst']:.1%} of RPE Age-1 keys found in EODFAIMST, "
                     f"{share['eodfaimst_age1_keys_found_in_rpe']:.1%} the other way")
        if share["rpe_age1_keys_found_in_eodfaimst"] < 0.95:
            status = "warn"
    text = "; ".join(notes)
    return Check("P8", title, status, text[0].upper() + text[1:] + ".", tables)


def run_profile(settings: Settings, raw: dict[str, pd.DataFrame] | None = None) -> ProfileResult:
    raw = raw if raw is not None else load_all(settings.raw_dir)
    cal = calendar_from(raw)
    trades = canonical_trades(raw["beta_trades"], raw["p3_trades"], settings)
    fails = canonical_fails(raw["beta_fail_snapshots"], raw["p3_fail_snapshots"], settings)
    result = build_labels(trades, fails, cal, settings)
    recon = reconcile(result, cal, settings)
    checks, recs = [p1_history(raw, cal)], {}
    c2, r2 = p2_snapshot_timing(raw, fails, cal, settings)
    checks += [c2, p3_borrow_runs(raw)]
    c4, r4 = p4_landing(raw)
    c5, r5 = p5_join(raw, recon)
    c6, r6 = p6_semantics(raw, fails, result.fail_matches, trades)
    checks += [c4, c5, c6, p7_base_rates(result.labels), p8_switch(raw)]
    for r in (r2, r4, r5, r6):
        recs.update(r)
    return ProfileResult(checks, recs)


def to_markdown(result: ProfileResult, title: str = "Source profiling report") -> str:
    out = [f"# {title}", "", "| Check | Status | Summary |", "|---|---|---|"]
    out += [f"| {c.id} | {c.status} | {c.summary} |" for c in result.checks]
    out += ["", "## Recommended configuration", "", "| Setting | Value | Evidence |", "|---|---|---|"]
    out += [f"| `{k}` | `{v[0]}` | {v[1]} |" for k, v in sorted(result.recommendations.items())]
    for c in result.checks:
        out += ["", f"## {c.id}: {c.question}", "", c.summary]
        for name, table in c.tables.items():
            out += ["", f"**{name}**", "", md_table(table)]
    return "\n".join(out) + "\n"
