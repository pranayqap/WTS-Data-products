"""Figures and quoted numbers for project-overview-and-handover.md.

Built only from saved artifacts (model bundles, shadow scores, monitoring logs, reports), so they can be
regenerated whenever those change. Run from Model/:

    PYTHONPATH=src .venv/bin/python docs/make_figures.py
    PYTHONPATH=src .venv/bin/python docs/make_figures.py --data-dir data --artifacts-dir artifacts --live

Needs matplotlib (pip install -e ".[docs]").
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from failmodel.models.baseline import rule_score

MODEL_ROOT = Path(__file__).resolve().parents[1]
OUT = MODEL_ROOT / "docs" / "figures"
MODEL, LOGIT, RULE, RANDOM, ALERT = "#1f4e79", "#2a9d8f", "#e76f51", "#9e9e9e", "#c0392b"
DIRECTIONS = {"D": "Fail to deliver", "R": "Fail to receive"}
LABELS = {
    "qty_to_sl_avail": "Trade size vs. lendable supply",
    "borrow_vwar": "Cost to borrow (borrow rate)",
    "cusip_fail_rate_250": "CUSIP fail history (250 days)",
    "cusip_fail_rate_60": "CUSIP fail history (60 days)",
    "sl_total_avail_log": "Lendable supply (SmartLoan)",
    "reg204_sod_fail_log": "Street CNS fails (Rule 204 grid)",
    "cp_fail_rate_250": "Counterparty fail history (250 days)",
    "spread_bps": "Bid-ask spread",
    "acct_fail_rate_250": "Account fail history (250 days)",
    "loanet_rebate": "Borrow rebate (hard-to-borrow signal)",
    "log_quantity": "Trade quantity",
    "qty_to_adv20": "Trade size vs. average daily volume",
    "qty_to_box_available": "Trade size vs. firm box inventory",
}


def _style(ax, title: str) -> None:
    ax.set_title(title, fontsize=11, loc="left", fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.3)


def _save(fig, name: str, note: str) -> None:
    fig.text(0.01, 0.005, note, fontsize=8, color="#555555", ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(OUT / name, dpi=150)
    plt.close(fig)
    print(f"wrote docs/figures/{name}")


def _md_table(path: Path, heading: str) -> pd.DataFrame:
    lines = path.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    block: list[list[str]] = []
    for line in lines[start + 1:]:
        if line.startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif block:
            break
    return pd.DataFrame(block[2:], columns=block[0])


def shadow_outcomes(data_dir: Path, artifacts_dir: Path) -> pd.DataFrame:
    days = sorted((artifacts_dir / "scores").glob("score_date=*"))
    scores = pd.concat([pd.read_parquet(d / "scores.parquet") for d in days], ignore_index=True)
    feats = pd.concat([pd.read_parquet(d / "features.parquet",
                                       columns=["scoring_date", "trade_key", "cusip_open_fails_same_dir",
                                                "box_deficit_log"]) for d in days], ignore_index=True)
    s = scores.merge(feats, on=["scoring_date", "trade_key"], how="left")
    labels = pd.read_parquet(data_dir / "processed" / "labels.parquet",
                             columns=["trade_key", "settle_date", "failed_on_settle", "fail_notional"])
    s = s.merge(labels, on=["trade_key", "settle_date"], how="left")
    s = s[s["failed_on_settle"].notna()].copy()
    s["failed"] = s["failed_on_settle"].astype(int)
    s["rule"] = np.nan
    for _, idx in s.groupby(["scoring_date", "direction"]).groups.items():
        s.loc[idx, "rule"] = rule_score(s.loc[idx])
    return s


def caught_share(s: pd.DataFrame, score: str, budgets: np.ndarray) -> np.ndarray:
    """Share of fails caught when ops reviews the top b of each night's trades (per direction)."""
    pct = s.groupby(["scoring_date", "direction"])[score].rank(ascending=False, method="first", pct=True)
    total = s["failed"].sum()
    return np.array([s.loc[pct <= b, "failed"].sum() / total for b in budgets])


def fig_gains(s: pd.DataFrame, note: str) -> dict:
    budgets = np.linspace(0, 0.30, 61)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    quoted = {}
    for ax, (d, name) in zip(axes, DIRECTIONS.items()):
        sd = s[s["direction"].eq(d)]
        model, rule = caught_share(sd, "fail_probability", budgets), caught_share(sd, "rule", budgets)
        ax.plot(budgets * 100, model * 100, color=MODEL, lw=2.5, label="Model")
        ax.plot(budgets * 100, rule * 100, color=RULE, lw=2, label="Today's rule (CUSIP already failing or box deficit)")
        ax.plot(budgets * 100, budgets * 100, color=RANDOM, lw=1.5, ls="--", label="Random review")
        for b in (0.05, 0.10):
            i = int(round(b / 0.005))
            ax.annotate(f"{model[i]*100:.0f}%", (b * 100, model[i] * 100), textcoords="offset points",
                        xytext=(-6, 8), color=MODEL, fontsize=9, fontweight="bold")
            ax.annotate(f"{rule[i]*100:.0f}%", (b * 100, rule[i] * 100), textcoords="offset points",
                        xytext=(4, -14), color=RULE, fontsize=9)
            quoted[f"{d}_model_top{int(b*100)}pct"] = round(model[i] * 100, 1)
            quoted[f"{d}_rule_top{int(b*100)}pct"] = round(rule[i] * 100, 1)
        quoted[f"{d}_trades"], quoted[f"{d}_fails"] = len(sd), int(sd["failed"].sum())
        _style(ax, f"{name}: {int(sd['failed'].sum())} fails, {len(sd):,} trades")
        ax.set_xlabel("Share of each night's trades reviewed (%)")
        ax.set_ylabel("Share of next-day fails caught (%)")
        ax.set_xlim(0, 30)
        ax.set_ylim(0, 100)
    axes[0].legend(loc="upper left", fontsize=8, frameon=False)
    fig.suptitle("Reviewing the model's top-ranked trades catches more fails than today's rule", fontsize=12,
                 fontweight="bold", x=0.01, ha="left")
    _save(fig, "fig1_fails_caught.png", note)
    return quoted


def fig_holdout(bundle: Path, note: str) -> None:
    m = json.loads((bundle / "metrics.json").read_text())
    models = [("lightgbm", "Model (LightGBM)", MODEL), ("logistic", "Logistic benchmark", LOGIT),
              ("rule", "Today's rule", RULE)]
    metrics = [("pr_auc", "PR-AUC (ranking quality)"), ("recall_at_5pct", "Fails caught in daily top 5%"),
               ("dollar_recall_at_5pct", "Fail dollars caught in daily top 5%")]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    x = np.arange(2)
    for ax, (metric, title) in zip(axes, metrics):
        for k, (key, label, color) in enumerate(models):
            vals = [next(r[metric] for r in m[d]["holdout"] if r["model"] == key) for d in DIRECTIONS]
            bars = ax.bar(x + (k - 1) * 0.27, vals, 0.27, color=color, label=label)
            ax.bar_label(bars, labels=[f"{v:.2f}" for v in vals], fontsize=8, padding=2)
        base = [m[d]["holdout"][0]["base_rate"] for d in DIRECTIONS]
        if metric == "pr_auc":
            for i, b in enumerate(base):
                ax.hlines(b, i - 0.42, i + 0.42, color=RANDOM, ls="--", lw=1,
                          label="Random ranking (fail rate)" if i == 0 else None)
        _style(ax, title)
        ax.set_xticks(x, ["Deliver", "Receive"])
        ax.set_ylim(0, max(0.6, ax.get_ylim()[1]))
    axes[0].legend(fontsize=8, frameon=False, loc="upper right")
    fig.suptitle("Out-of-time holdout (last 3 months, never used for tuning)", fontsize=12, fontweight="bold",
                 x=0.01, ha="left")
    _save(fig, "fig2_holdout_results.png", note)


def fig_drivers(bundle: Path, note: str) -> None:
    m = json.loads((bundle / "metrics.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    for ax, (d, name) in zip(axes, DIRECTIONS.items()):
        imp = pd.DataFrame(m[d]["importance"]).head(8).iloc[::-1]
        ax.barh([LABELS.get(f, f) for f in imp["feature"]], imp["share"] * 100, color=MODEL)
        for i, v in enumerate(imp["share"] * 100):
            ax.text(v + 0.3, i, f"{v:.0f}%", va="center", fontsize=8)
        _style(ax, name)
        ax.grid(axis="x", alpha=0.3)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Share of the model's explanation (mean |SHAP|, %)")
    fig.suptitle("What drives the predictions (top 8 features)", fontsize=12, fontweight="bold", x=0.01, ha="left")
    _save(fig, "fig3_drivers.png", note)


def fig_monitoring(artifacts_dir: Path, note: str) -> None:
    roll = pd.read_csv(artifacts_dir / "monitoring" / "rolling.csv", parse_dates=["as_of"])
    alerts = pd.read_csv(artifacts_dir / "monitoring" / "alerts.csv", parse_dates=["date"])
    fig, axes = plt.subplots(2, 2, figsize=(12, 6), sharex=True)
    for col, (d, name) in enumerate(DIRECTIONS.items()):
        r = roll[roll["direction"].eq(d)].sort_values("as_of")
        ok = r["fails"] >= 20
        top, bottom = axes[0, col], axes[1, col]
        top.plot(r["as_of"], r["rolling_pr_auc"].where(ok), color=MODEL, lw=2, label="Rolling 20-day PR-AUC")
        top.axhline(r["holdout_pr_auc"].iloc[0], color=RANDOM, ls="--", lw=1, label="Holdout")
        top.axhline(0.8 * r["holdout_pr_auc"].iloc[0], color=ALERT, ls=":", lw=1.5, label="Alert line (80% of holdout)")
        bottom.plot(r["as_of"], r["calibration_ratio"].where(ok), color=MODEL, lw=2, label="Predicted / actual fails")
        bottom.axhspan(0.7, 1.3, color="#2a9d8f", alpha=0.12, label="Healthy band 0.7-1.3")
        a = alerts[alerts["scope"].astype(str).str.startswith(f"{d}:") | alerts["scope"].astype(str).eq(d)]
        for k, (signal, label) in enumerate((("feature_drift", "drift"), ("calibration", "calibration"),
                                             ("performance", "performance"))):
            first = a.loc[a["signal"].eq(signal), "date"].min()
            if pd.notna(first):
                for ax in (top, bottom):
                    ax.axvline(first, color=ALERT, lw=1, alpha=0.6)
                top.annotate(f"{label} alert", (first, 1.0), xycoords=("data", "axes fraction"), xytext=(3, -10 - 11 * k),
                             textcoords="offset points", fontsize=7, color=ALERT)
        _style(top, f"{name}: ranking quality")
        _style(bottom, f"{name}: calibration")
        if col == 0:
            top.legend(fontsize=7, frameon=False, loc="lower left")
            bottom.legend(fontsize=7, frameon=False, loc="upper left")
        bottom.tick_params(axis="x", rotation=30)
    fig.suptitle("Shadow-run monitoring: a planted market change in May is caught by drift, then calibration, "
                 "then performance alerts", fontsize=11, fontweight="bold", x=0.01, ha="left")
    _save(fig, "fig4_shadow_monitoring.png", note)


def fig_persistence(artifacts_dir: Path, note: str) -> None:
    reports = sorted((artifacts_dir / "reports").glob("persistence_*.md"))
    t = _md_table(reports[-1], "## Holdout results")
    t = t[t["task"].ne("")]
    names = {"still_open_h1": "Still open\nin 1 day", "still_open_h3": "Still open\nin 3 days",
             "still_open_h5": "Still open\nin 5 days", "reg204_breach": "Reaches Rule 204\ndeadline",
             "muni_breach": "Reaches 10-day\nmuni deadline"}
    labels = [f"{names[k]}\n({'deliver' if d == 'D' else 'receive'})" for k, d in zip(t["task"], t["direction"])]
    model, age = t["lightgbm_pr_auc"].astype(float).to_numpy(), t["age_only_pr_auc"].astype(float).to_numpy()
    x = np.arange(len(t))
    fig, ax = plt.subplots(figsize=(12, 4.2))
    b1 = ax.bar(x - 0.2, model, 0.4, color=MODEL, label="Model")
    b2 = ax.bar(x + 0.2, age, 0.4, color=RULE, label="Age-only benchmark (how long it has been failing)")
    ax.bar_label(b1, labels=[f"{v:.2f}" for v in model], fontsize=8, padding=2)
    ax.bar_label(b2, labels=[f"{v:.2f}" for v in age], fontsize=8, padding=2)
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_ylabel("Holdout PR-AUC")
    _style(ax, "Phase 5, open fails: the model beats fail age alone on persistence, not yet on close-out deadlines")
    ax.legend(fontsize=8, frameon=False)
    _save(fig, "fig5_persistence.png", note)


def sample_list(s: pd.DataFrame, day: str, n: int = 8) -> str:
    d = s[s["scoring_date"].eq(pd.Timestamp(day)) & s["direction"].eq("D")]
    d = d.sort_values("fail_probability", ascending=False).head(n)
    rows = ["| Rank | Book | Product | Counterparty | Notional | Fail probability | Reason 1 | Reason 2 | Failed? |",
            "|---|---|---|---|---|---|---|---|---|"]
    for rank, (_, r) in enumerate(d.iterrows(), start=1):
        rows.append(f"| {rank} | {'Beta' if r['book'] == 'B' else 'P3'} | {r['product_group']} | "
                    f"{r['counterparty_type']} | ${r['notional']:,.0f} | {r['fail_probability']:.0%} | "
                    f"{r['reason_1_text']} | {r['reason_2_text']} | {'Yes' if r['failed'] else 'No'} |")
    total = s[s["scoring_date"].eq(pd.Timestamp(day)) & s["direction"].eq("D")]
    rows.append(f"\n{day}: {len(total)} deliver trades scored, {int(total['failed'].sum())} failed; "
                f"{int(d['failed'].sum())} of them are in this top {n}.")
    return "\n".join(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data/synthetic")
    p.add_argument("--artifacts-dir", default="artifacts/synthetic")
    p.add_argument("--holdout-bundle", default="fm-v1-20261014-synthetic")
    p.add_argument("--sample-day", default="2025-06-02")
    p.add_argument("--live", action="store_true", help="drop the synthetic-data caption")
    args = p.parse_args()
    data_dir, artifacts_dir = MODEL_ROOT / args.data_dir, MODEL_ROOT / args.artifacts_dir
    OUT.mkdir(parents=True, exist_ok=True)
    note = "" if args.live else "SYNTHETIC TEST BED: shows that the method works end to end, not real performance."

    s = shadow_outcomes(data_dir, artifacts_dir)
    quoted = fig_gains(s, note + "  Shadow run 2025-05-01 to 2025-06-27, out of sample.")
    bundle = artifacts_dir / "models" / args.holdout_bundle
    fig_holdout(bundle, note)
    fig_drivers(bundle, note)
    fig_monitoring(artifacts_dir, note)
    fig_persistence(artifacts_dir, note)
    print(json.dumps(quoted, indent=2))
    print(sample_list(s, args.sample_day))


if __name__ == "__main__":
    main()
