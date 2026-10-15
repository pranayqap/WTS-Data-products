"""Phase 5 (plan 07): fail persistence and close-out risk.

Unit: one open fail on snapshot date F (report population, business-day age >= 1) that originates from a trade
passing the v1 population rules, with entry by F in place of entry by S - 1. Fails are tracked across snapshots by the phase-1 fail key (Beta ``FailUniqueId``, or the
report's composite key when ``ControlNumber`` = 0; P3 trade key + record type).

Prediction time is when snapshot F is available (F + the book's label lag). Features use only data known then:
- the phase-2 features, re-anchored with ``scoring_date = F`` (each source at F - its lag);
- fail state from snapshot F itself, allowed here because the fail already exists;
- cure history: smoothed shares of earlier fails still open after h days, from fails whose horizon closed by
  F (G + h <= F).

Labels:
- ``still_open_h{h}``: the fail key is on snapshot F + h business days (missing when that snapshot is missing);
- ``reg204_breach``: a deliver-side equity fail still open on S + ``reg204_default_deadline_bd``;
- ``muni_breach``: a municipal fail still open on S + ``muni_closeout_bd``;
both close-out labels only for rows observed before the deadline.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .bizcal import BusinessCalendar, to_day
from .config import PersistenceSettings, Settings
from .features.base import cat, num
from .features.build import CATEGORICAL, FEATURE_NAMES, build_context, build_features
from .features.history import _cumulative, _lookup
from .labels import population_rules, snapshot_presence
from .models.metrics import SLICES
from .models.train import DIRECTIONS, DirectionResult, Task, train_direction
from .pipeline import load_dataset

FAMILY = "fail_state"
HORIZONS = PersistenceSettings.horizons_bd
_SNAP = "fail snapshot F"
_SNAP_RULE = "The fail row on snapshot F (and earlier snapshots of the same key)"
_CURE_RULE = "Fails on snapshots G with G + h <= F"

SPECS = (
    [
        num("age_bd", FAMILY, _SNAP, _SNAP_RULE, "B, P", "Fail age in business days (1 on the settle date)"),
        num("fail_qty_log", FAMILY, _SNAP, _SNAP_RULE, "B, P", "log(1 + open fail quantity)"),
        num("fail_amount_log", FAMILY, _SNAP, _SNAP_RULE, "B, P", "log(1 + open fail amount)"),
        num("remaining_to_trade_qty", FAMILY, _SNAP, _SNAP_RULE, "B, P",
            "Open fail quantity / trade quantity (below 1 after a partial cure)"),
        num("remaining_to_original", FAMILY, _SNAP, _SNAP_RULE, "P",
            "Open fail quantity / original fail quantity (P3 ORIGINAL_QUANTITY)"),
        num("qty_change_prev", FAMILY, _SNAP, _SNAP_RULE, "B, P",
            "Open fail quantity / quantity on the previous business day's snapshot (missing if not on it)"),
        num("reappeared", FAMILY, _SNAP, _SNAP_RULE, "B, P",
            "1 if the fail was seen before but is not on the previous business day's snapshot"),
        num("bd_since_first_seen", FAMILY, _SNAP, _SNAP_RULE, "B, P", "Business days since the fail key first appeared"),
        num("dk_count", FAMILY, _SNAP, _SNAP_RULE, "B", "DK count on the fail record"),
        cat("fail_reason", FAMILY, _SNAP, _SNAP_RULE, "B, P",
            "Fail reason code, prefixed by book (Beta DTCReasonCode, P3 FAIL_REASON)"),
        cat("fail_condition", FAMILY, _SNAP, _SNAP_RULE, "B, P",
            "Fail condition, prefixed by book (Beta FailConditionCode, P3 DTC_DO_REASON_CODE)"),
        cat("fail_who", FAMILY, _SNAP, _SNAP_RULE, "B", "Beta FailWho"),
    ]
    + [spec for h in HORIZONS for spec in (
        num(f"book_persist_rate_h{h}", FAMILY, "fail snapshots", _CURE_RULE, "B, P",
            f"Share of the book and direction's fails still open after {h} business days, last 250 business days"),
        num(f"cusip_persist_rate_h{h}", FAMILY, "fail snapshots", _CURE_RULE, "B, P",
            f"Smoothed share of the CUSIP's fails still open after {h} business days"),
        num(f"fail_cp_persist_rate_h{h}", FAMILY, "fail snapshots", _CURE_RULE, "B, P",
            f"Smoothed share of the fail counterparty's fails still open after {h} business days"),
    )]
)
STATE_FEATURES = [s.name for s in SPECS]
STATE_CATEGORICAL = [s.name for s in SPECS if s.kind == "categorical"]
FEATURES = FEATURE_NAMES + STATE_FEATURES
CATEGORICAL_ALL = CATEGORICAL + STATE_CATEGORICAL
LABELS = [f"still_open_h{h}" for h in HORIZONS] + ["reg204_breach", "muni_breach"]
KEY = ["book", "fail_key", "snapshot_date"]


def _present_at(t: pd.DataFrame, target: pd.Series, presence: pd.DataFrame, keys: pd.DataFrame) -> pd.Series:
    """1 if the fail key is on the book's snapshot ``target``, 0 if that snapshot exists without it, else missing."""
    q = pd.DataFrame({"book": t["book"].to_numpy(), "fail_key": t["fail_key"].to_numpy(),
                      "snapshot_date": target.to_numpy()})
    exists = q.merge(presence, on=["book", "snapshot_date"], how="left")["snapshot_exists"].fillna(False).to_numpy(bool)
    present = q.merge(keys.assign(_p=True), on=KEY, how="left")["_p"].fillna(False).to_numpy(bool)
    out = pd.Series(present.astype(int), index=t.index, dtype="Int8")
    return out.where(exists)


def track_fails(fails: pd.DataFrame, cal: BusinessCalendar, settings: Settings) -> pd.DataFrame:
    """One row per open fail key and snapshot (report population, age >= 1) with fail state and ``still_open_h``."""
    f = fails[fails["in_report_population"].eq(1) & fails["settle_date"].notna() & fails["fail_key"].notna()
              & fails["direction"].notna()]
    t = f.groupby(KEY, sort=True).agg(
        trade_key=("trade_key", "first"), fallback_key=("fallback_key", "first"), direction=("direction", "first"),
        cusip9=("cusip9", "first"), settle_date=("settle_date", "min"), counterparty_id=("counterparty_id", "first"),
        fail_qty=("quantity", "sum"), original_qty=("original_quantity", "sum"), fail_amount=("amount", "sum"),
        dk_count=("dk_count", "max"), reason_code=("reason_code", "first"),
        fail_condition_code=("fail_condition", "first"), fail_who_code=("fail_who", "first"),
    ).reset_index()
    t["age_bd"] = cal.bd_diff(t["settle_date"], t["snapshot_date"]).to_numpy() + 1
    t = t[t["age_bd"] >= 1].reset_index(drop=True)

    presence = snapshot_presence(fails)
    keys = t[KEY]
    first_seen = t.groupby(["book", "fail_key"])["snapshot_date"].transform("min")
    t["bd_since_first_seen"] = cal.bd_diff(first_seen, t["snapshot_date"]).to_numpy().astype(float)
    prev = cal.add(t["snapshot_date"], -1)
    prev_q = pd.DataFrame({"book": t["book"], "fail_key": t["fail_key"], "snapshot_date": prev.to_numpy()})
    prev_q = prev_q.merge(t[KEY + ["fail_qty"]], on=KEY, how="left")
    prev_exists = (pd.DataFrame({"book": t["book"], "snapshot_date": prev.to_numpy()})
                   .merge(presence, on=["book", "snapshot_date"], how="left")["snapshot_exists"]
                   .fillna(False).to_numpy(bool))
    prev_qty = prev_q["fail_qty"].to_numpy(dtype=float)
    t["qty_change_prev"] = t["fail_qty"].to_numpy(dtype=float) / np.where(prev_qty > 0, prev_qty, np.nan)
    gone = (first_seen < t["snapshot_date"]).to_numpy() & np.isnan(prev_qty)
    t["reappeared"] = np.where(prev_exists, gone.astype(float), np.nan)
    orig = t["original_qty"].astype(float)
    t["remaining_to_original"] = (t["fail_qty"] / orig.where(orig > 0)).where(t["book"].eq("P")).astype(float)
    t["dk_count"] = t["dk_count"].astype(float).where(t["book"].eq("B"))
    for name, col in (("fail_reason", "reason_code"), ("fail_condition", "fail_condition_code"),
                      ("fail_who", "fail_who_code")):
        t[name] = (t["book"] + ":" + t[col].astype("string")).astype("string")
    t["fail_who"] = t["fail_who"].where(t["book"].eq("B"))

    for h in HORIZONS:
        t[f"still_open_h{h}"] = _present_at(t, cal.add(t["snapshot_date"], h), presence, keys)
    return t


def cure_history(t: pd.DataFrame, cal: BusinessCalendar, settings: Settings) -> pd.DataFrame:
    """Smoothed persistence rates per CUSIP and fail counterparty from fails whose horizon closed by F."""
    k, window = settings.history_prior_strength, settings.persistence.history_window_bd
    at = cal.index_on_or_before(t["snapshot_date"])
    out = pd.DataFrame(index=t.index)
    for h in HORIZONS:
        lab = t[f"still_open_h{h}"]
        known = lab.notna().to_numpy()
        ev = t.loc[known, ["book", "direction", "cusip9", "counterparty_id"]].assign(
            idx=at[known] + h, y=lab[known].astype(int).to_numpy())
        gkeys = ["book", "direction"]
        gcum = _cumulative(ev, gkeys)
        hi, lo = _lookup(t, gcum, gkeys, at), _lookup(t, gcum, gkeys, at - window)
        gn, gf = hi["cn"] - lo["cn"], hi["cf"] - lo["cf"]
        prior = (gf / gn.where(gn > 0)).astype(float)
        out[f"book_persist_rate_h{h}"] = prior
        for name, keys in (("cusip", ["direction", "cusip9"]), ("fail_cp", ["book", "direction", "counterparty_id"])):
            missing = t[keys].isna().any(axis=1)
            cum = _cumulative(ev, keys)
            hi, lo = _lookup(t, cum, keys, at), _lookup(t, cum, keys, at - window)
            n, f = hi["cn"] - lo["cn"], hi["cf"] - lo["cf"]
            rate = ((f + k * prior) / (n + k)).where(prior.notna(), f / n.where(n > 0))
            out[f"{name}_persist_rate_h{h}"] = rate.where(~missing).astype(float)
    return out


def eligible_trades(universe: pd.DataFrame, cal: BusinessCalendar, settings: Settings) -> pd.DataFrame:
    """The v1 population rules without entry by S - 1: a trade entered later is known once its fail is open on F
    (entry by F is checked per row)."""
    rules = population_rules(universe, cal, to_day(settings.extract_start), cal.days[-1])
    excluded = np.logical_or.reduce([m for name, m in rules.items() if name != "entered_after_scoring_date"])
    return universe[~excluded]


def match_trades(t: pd.DataFrame, u: pd.DataFrame) -> pd.Series:
    """Eligible trade key for each tracked fail: exact trade key, else a unique fallback-key match on the
    key's first sighting (the fallback key embeds quantity, which changes after partial cures)."""
    exact = t["trade_key"].where(t["trade_key"].isin(set(u["trade_key"])))
    first = t.sort_values("snapshot_date").groupby(["book", "fail_key"])["fallback_key"].first()
    fb_first = pd.Series(list(zip(t["book"], t["fail_key"])), index=t.index).map(first.to_dict())
    ufb = u.dropna(subset=["fallback_key"]).drop_duplicates("fallback_key", keep=False)
    fb = fb_first.map(ufb.set_index("fallback_key")["trade_key"].to_dict())
    return exact.fillna(fb)


def age_bucket(age: pd.Series, buckets) -> pd.Series:
    labels = [f"{lo}" if lo == hi else (f"{lo}+" if hi >= 10**5 else f"{lo}-{hi}") for lo, hi in buckets]
    out = pd.Series(pd.NA, index=age.index, dtype="string")
    for (lo, hi), lab in zip(buckets, labels):
        out = out.mask(age.between(lo, hi), lab)
    return out


@dataclass
class PersistenceTable:
    frame: pd.DataFrame
    tracked: pd.DataFrame
    fails: pd.DataFrame
    presence: pd.DataFrame
    cal: BusinessCalendar
    unmatched: int

    def reconcile(self) -> pd.DataFrame:
        return reconcile(self.tracked, self.fails, self.presence, self.cal)


def build_persistence_table(settings: Settings, raw: dict | None = None) -> PersistenceTable:
    ds = load_dataset(settings, raw)
    cal, ps = ds.cal, settings.persistence
    ctx, _ = build_context(ds, settings)
    t = track_fails(ds.fails, cal, settings)
    t = pd.concat([t, cure_history(t, cal, settings)], axis=1)
    u = eligible_trades(ctx.universe, cal, settings).drop_duplicates("trade_key").set_index("trade_key")
    key = match_trades(t, u.reset_index())
    entry = pd.to_datetime(key.map(u["entry_date"]))
    t["population_trade_key"] = key.where(~(entry > t["snapshot_date"]))
    rows = t[t["population_trade_key"].notna()
             & (t["snapshot_date"] >= pd.Timestamp(settings.label_start))].reset_index(drop=True)

    base = u.loc[rows["population_trade_key"]].reset_index()
    base["scoring_date"] = rows["snapshot_date"].to_numpy()
    feats = build_features(base, ctx)

    state = rows[[c for c in STATE_FEATURES if c in rows.columns]].copy()
    state["fail_qty_log"] = np.log1p(rows["fail_qty"].astype(float).clip(lower=0))
    state["fail_amount_log"] = np.log1p(rows["fail_amount"].astype(float).clip(lower=0))
    tq = feats["quantity"].astype(float).to_numpy()
    state["remaining_to_trade_qty"] = rows["fail_qty"].to_numpy(dtype=float) / np.where(tq > 0, tq, np.nan)
    frame = pd.concat([
        rows[["fail_key", "snapshot_date"]], feats, state[STATE_FEATURES],
        rows[["counterparty_id", "fail_amount"] + [f"still_open_h{h}" for h in HORIZONS]].rename(
            columns={"counterparty_id": "fail_counterparty_id", "fail_amount": "fail_notional"}),
    ], axis=1)

    presence, keys = snapshot_presence(ds.fails), t[KEY]
    rules = (("reg204_breach", ps.reg204_default_deadline_bd, frame["direction"].eq("D") & frame["product_group"].eq("EQ")),
             ("muni_breach", ps.muni_closeout_bd, frame["product_group"].eq("MUNI")))
    for name, k, applies in rules:
        deadline = cal.add(frame["settle_date"], k)
        lab = _present_at(frame, deadline, presence, keys)
        frame[name] = lab.where((applies & (frame["snapshot_date"] < deadline)).to_numpy())
        frame[f"{name}_horizon_bd"] = cal.bd_diff(frame["snapshot_date"], deadline).astype(float)
    frame["age_bucket"] = age_bucket(frame["age_bd"], ps.age_buckets)
    return PersistenceTable(frame=frame, tracked=t, fails=ds.fails, presence=presence, cal=cal,
                            unmatched=int(t["population_trade_key"].isna().sum()))


def reconcile(tracked: pd.DataFrame, fails: pd.DataFrame, presence: pd.DataFrame, cal: BusinessCalendar) -> pd.DataFrame:
    """Per book and snapshot F with a next snapshot:
    - open fails at F = still open at F+1 + cured, and the next snapshot's rows = carried + new + reappeared
      (these hold whenever keys are unique, so they check the label code);
    - tracked open fails against the snapshot's raw fail rows (``collapsed_rows`` > 0 means fail keys collide
      within a snapshot);
    - ``reappeared`` counts keys that leave and come back, the sign of an unstable fail key."""
    t = tracked
    nxt = cal.add(t["snapshot_date"], 1)
    t = t.assign(_next=nxt.to_numpy())
    has_next = t.merge(presence.rename(columns={"snapshot_date": "_next"}), on=["book", "_next"],
                       how="left")["snapshot_exists"].fillna(False).to_numpy(bool)
    first_seen = t.groupby(["book", "fail_key"])["snapshot_date"].transform("min")
    prior_keys = t[["book", "fail_key", "snapshot_date"]].assign(_on_prev=True)
    prev = cal.add(t["snapshot_date"], -1)
    on_prev = (pd.DataFrame({"book": t["book"], "fail_key": t["fail_key"], "snapshot_date": prev.to_numpy()})
               .merge(prior_keys, on=KEY, how="left")["_on_prev"].fillna(False).to_numpy(bool))
    t = t.assign(_on_prev=on_prev, _new=(first_seen == t["snapshot_date"]).to_numpy(),
                 _reappeared=(~on_prev & (first_seen < t["snapshot_date"])).to_numpy())

    src = t[has_next].groupby(["book", "snapshot_date"]).agg(
        open=("fail_key", "size"), still_open_next=("still_open_h1", "sum")).reset_index()
    src["cured"] = src["open"] - src["still_open_next"]
    dst = t.groupby(["book", "snapshot_date"]).agg(
        next_open=("fail_key", "size"), next_carried=("_on_prev", "sum"), next_new=("_new", "sum"),
        next_reappeared=("_reappeared", "sum")).reset_index()
    dst["snapshot_date"] = cal.add(dst["snapshot_date"], -1).to_numpy()
    out = src.merge(dst, on=["book", "snapshot_date"], how="left")
    for c in ("next_open", "next_carried", "next_new", "next_reappeared"):
        out[c] = out[c].fillna(0).astype(int)
    out["still_open_next"] = out["still_open_next"].astype(int)
    out["carry_ok"] = out["still_open_next"].eq(out["next_carried"])
    out["flow_ok"] = out["next_open"].eq(out["next_carried"] + out["next_new"] + out["next_reappeared"])
    f = fails[fails["in_report_population"].eq(1) & fails["settle_date"].notna() & fails["fail_key"].notna()
              & fails["direction"].notna()]
    f = f[cal.bd_diff(f["settle_date"], f["snapshot_date"]).to_numpy() >= 0]
    raw_rows = f.groupby(["book", "snapshot_date"]).size().rename("snapshot_rows").reset_index()
    out = out.merge(raw_rows, on=["book", "snapshot_date"], how="left")
    out["collapsed_rows"] = out["snapshot_rows"].fillna(0).astype(int) - out["open"]
    return out


def reconcile_summary(recon: pd.DataFrame) -> pd.DataFrame:
    g = recon.groupby("book")
    return pd.DataFrame({
        "snapshot_pairs": g.size(), "snapshot_rows": g["snapshot_rows"].sum(), "collapsed_rows": g["collapsed_rows"].sum(),
        "open_fail_rows": g["open"].sum(), "still_open_next": g["still_open_next"].sum(),
        "cured": g["cured"].sum(), "persist_rate_h1": g["still_open_next"].sum() / g["open"].sum(),
        "new_next": g["next_new"].sum(), "reappeared_next": g["next_reappeared"].sum(),
        "reappeared_share": g["next_reappeared"].sum() / g["next_open"].sum(),
        "carry_identity_ok": g["carry_ok"].all(), "flow_identity_ok": g["flow_ok"].all(),
    }).reset_index()


def age_only(cap: int, k: float):
    """Benchmark: smoothed persistence rate by business-day age (capped), fitted on the training rows."""
    def score(train: pd.DataFrame, y: np.ndarray, frame: pd.DataFrame) -> np.ndarray:
        prior = float(np.mean(y)) if len(y) else 0.0
        g = pd.DataFrame({"a": np.minimum(train["age_bd"].to_numpy(), cap), "y": y}).groupby("a")["y"].agg(["sum", "count"])
        rate = (g["sum"] + k * prior) / (g["count"] + k)
        return pd.Series(np.minimum(frame["age_bd"].to_numpy(), cap)).map(rate).fillna(prior).to_numpy(dtype=float)
    return score


def task_for(name: str, settings: Settings) -> Task:
    ps = settings.persistence
    return Task(
        name=name, target=name, features=tuple(FEATURES), categorical=tuple(CATEGORICAL_ALL),
        baseline=age_only(ps.age_cap_bd, settings.history_prior_strength), baseline_name="age_only",
        date_col="snapshot_date", pred_columns=("fail_key", "book", "snapshot_date", "settle_date", "age_bd", "notional"),
        slices=SLICES + ("age_bucket",), top_k=(10, 25), gate_precision_at=None,
    )


def horizon_bd(name: str, frame: pd.DataFrame) -> int:
    if name.startswith("still_open_h"):
        return int(name.rsplit("h", 1)[1])
    return int(frame[f"{name}_horizon_bd"].max())


def train_persistence(frame: pd.DataFrame, cal: BusinessCalendar, settings: Settings, tasks=None,
                      tune: bool = False, log=print) -> dict[str, dict[str, DirectionResult]]:
    """Each task per direction, on the phase-3 engine with the purge widened to the label horizon."""
    out: dict[str, dict[str, DirectionResult]] = {}
    min_rows = settings.persistence.min_class_rows
    for name in tasks or settings.persistence.tasks:
        purge = max(settings.model.purge_bd, horizon_bd(name, frame))
        s = settings.with_overrides(model=dataclasses.replace(settings.model, purge_bd=purge))
        task = task_for(name, s)
        out[name] = {}
        for direction in DIRECTIONS:
            y = frame.loc[frame["direction"].eq(direction), name].dropna()
            if y.sum() < min_rows or (len(y) - y.sum()) < min_rows:
                log(f"[{name}:{direction}] skipped: {int(y.sum())} positives of {len(y)} labelled rows")
                continue
            out[name][direction] = train_direction(frame, direction, cal, s, tune, log, task)
    return out


def results_table(results: dict[str, dict[str, DirectionResult]]) -> pd.DataFrame:
    rows = []
    for name, by_dir in results.items():
        for direction, r in by_dir.items():
            h = r.holdout_metrics.set_index("model")
            rows.append({
                "task": name, "direction": direction, "rows": int(h.loc["lightgbm", "rows"]),
                "base_rate": h.loc["lightgbm", "base_rate"], "lightgbm_pr_auc": h.loc["lightgbm", "pr_auc"],
                "age_only_pr_auc": h.loc["age_only", "pr_auc"], "logistic_pr_auc": h.loc["logistic", "pr_auc"],
                "lightgbm_roc_auc": h.loc["lightgbm", "roc_auc"], "ece": h.loc["lightgbm", "ece"],
                "beats_age_only": r.acceptance["beats_age_only_pr_auc"],
                "fold_stability": r.acceptance["fold_stability_ratio"],
            })
    return pd.DataFrame(rows)
