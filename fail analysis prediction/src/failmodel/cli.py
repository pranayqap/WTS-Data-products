"""Command line entry point: ``python -m failmodel.cli <command>``."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .config import Settings, provisional_report


def _settings(args) -> Settings:
    settings = Settings()
    if getattr(args, "data_dir", None):
        settings = settings.with_overrides(data_dir=Path(args.data_dir))
    if getattr(args, "artifacts_dir", None):
        settings = settings.with_overrides(artifacts_dir=Path(args.artifacts_dir))
    if getattr(args, "label_start", None):
        settings = settings.with_overrides(label_start=date.fromisoformat(args.label_start))
    if getattr(args, "extract_start", None):
        settings = settings.with_overrides(extract_start=date.fromisoformat(args.extract_start))
    if getattr(args, "p3_record_types", None):
        settings = settings.with_overrides(p3_trade_record_types=tuple(args.p3_record_types.split(",")))
    return settings


def _write_report(settings: Settings, name: str, text: str) -> Path:
    path = settings.artifacts_dir / "reports" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def cmd_synth(args) -> None:
    from .synthetic import SyntheticSpec, write_synthetic

    settings = _settings(args)
    spec = SyntheticSpec(start=args.start, end=args.end, n_cusips=args.cusips, trades_per_day=args.trades_per_day,
                         seed=args.seed)
    counts = write_synthetic(settings.raw_dir, spec)
    for source, rows in counts.items():
        print(f"{source:28s} {rows:>10,d}")


def cmd_extract(args) -> None:
    from .extract import WrapperClient, run_extracts
    from .sources import active_sources

    settings = _settings(args)
    ids = args.source or [s.id for s in active_sources(args.include_conditional)]
    client = WrapperClient(settings.wrapper)
    log = run_extracts(ids, date.fromisoformat(args.start), date.fromisoformat(args.end), settings, client, args.overwrite)
    print(log.to_string(index=False) if len(log) else "nothing to extract (all windows present)")


def cmd_profile(args) -> None:
    from .profiling import run_profile, to_markdown

    settings = _settings(args)
    result = run_profile(settings)
    text = to_markdown(result) + "\n## Provisional settings in effect\n\n" + "\n".join(
        f"- {line}" for line in provisional_report(settings)) + "\n"
    path = _write_report(settings, f"profile_{date.today():%Y%m%d}.md", text)
    for check in result.checks:
        print(f"{check.id} [{check.status}] {check.summary}")
    print(f"report: {path}")


def cmd_labels(args) -> None:
    from .labels import base_rates, exclusion_counts, label_daily_counts, reconcile
    from .pipeline import label_dataset, load_dataset, write_processed
    from .profiling import md_table

    settings = _settings(args)
    ds = load_dataset(settings)
    result = label_dataset(ds, settings)
    recon = reconcile(result, ds.cal, settings)
    write_processed(settings, trades=ds.trades, fails=ds.fails, labels=result.labels)
    reports = settings.artifacts_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    label_daily_counts(result.labels).to_csv(reports / "label_daily_counts.csv", index=False)
    recon["daily"].to_csv(reports / "age1_reconciliation_daily.csv", index=False)
    rates = base_rates(result.labels)
    sections = [
        ("Age-1 reconciliation", recon["summary"]),
        ("P3 Age-1 fails by RECORD_TYPE", recon["p3_by_record_type"]),
        ("Matched fails on excluded trades", recon["excluded_reasons"]),
        ("Positives whose fail settle date differs from S", recon["settle_differs"]),
        ("Base rates", rates),
        ("Trade universe by exclusion reason", exclusion_counts(result.universe)),
    ]
    text = "# Label build report\n\n" + "\n\n".join(f"## {title}\n\n{md_table(frame)}" for title, frame in sections) + "\n"
    path = _write_report(settings, f"labels_{date.today():%Y%m%d}.md", text)
    print(md_table(recon["summary"]))
    print(md_table(rates))
    print(f"report: {path}")


def cmd_features(args) -> None:
    from . import leakage as L
    from .features.build import (CATEGORICAL, FEATURE_NAMES, build_feature_table, catalog_markdown,
                                 coverage_by_book, deferred_markdown)
    from .pipeline import write_processed
    from .profiling import load_all, md_table

    settings = _settings(args)
    raw = load_all(settings.raw_dir)
    frame, ctx = build_feature_table(settings, raw)
    write_processed(settings, features=frame)
    coverage = coverage_by_book(frame)
    audit = L.asof_audit(frame, ctx.cal, settings)
    screen = L.single_feature_screen(frame, FEATURE_NAMES, CATEGORICAL)
    shuffle = L.label_shuffle_test(frame, FEATURE_NAMES, CATEGORICAL)
    perturbation = None
    if args.perturb_cutoff:
        perturbation = L.perturbation_test(settings, raw, args.perturb_cutoff)
    forbidden = L.forbidden_columns(FEATURE_NAMES)
    status = L.leakage_status(screen, perturbation, audit, forbidden)
    reports = settings.artifacts_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(reports / "feature_coverage.csv", index=False)
    screen.to_csv(reports / "single_feature_screen.csv", index=False)
    catalog = catalog_markdown(status, coverage)
    (reports / "feature_catalog.md").write_text(catalog + "\n")
    pert_lines = ["Not run (pass --perturb-cutoff YYYY-MM-DD)."]
    if perturbation is not None:
        pert_lines = [f"- Mode `{mode}`: {int(d['differences'].sum())} differing values across {len(d) - 1} features "
                      f"(rows with D <= {args.perturb_cutoff})" for mode, d in perturbation.items()]
    known = frame["failed_on_settle"].notna()
    text = "\n\n".join([
        "# Feature build report",
        f"Rows: {len(frame):,} ({int(known.sum()):,} with a known label). Features: {len(FEATURE_NAMES)}. "
        f"Settle dates {frame['settle_date'].min():%Y-%m-%d} to {frame['settle_date'].max():%Y-%m-%d}.",
        "## Forbidden-column check\n\n" + ("None found." if not forbidden else ", ".join(forbidden)),
        "## As-of audit\n\n" + md_table(audit),
        "## Future perturbation\n\n" + "\n".join(pert_lines),
        "## Single-feature screen (top 15)\n\n" + md_table(screen.head(15)),
        f"## Label shuffle\n\nHoldout PR-AUC {shuffle.pr_auc:.4f} vs base rate {shuffle.base_rate:.4f} "
        f"(ROC-AUC {shuffle.roc_auc:.3f}); {'pass' if shuffle.passed else 'FAIL'}.",
        "## Feature catalog\n\n" + catalog,
        "## Deferred features\n\n" + deferred_markdown(),
        "## Provisional settings in effect\n\n" + "\n".join(f"- {line}" for line in provisional_report(settings)),
    ]) + "\n"
    path = _write_report(settings, f"features_{date.today():%Y%m%d}.md", text)
    print(md_table(audit))
    print("\n".join(pert_lines))
    print(f"label shuffle: PR-AUC {shuffle.pr_auc:.4f} vs base {shuffle.base_rate:.4f} -> {'pass' if shuffle.passed else 'FAIL'}")
    print(f"flagged by screen: {screen.loc[screen['flagged'], 'feature'].tolist()}")
    print(f"report: {path}")


def _feature_frame(settings: Settings):
    from .features.build import build_feature_table
    from .pipeline import write_processed

    path = settings.processed_dir / "features.parquet"
    if path.exists():
        import pandas as pd

        return pd.read_parquet(path)
    frame, _ = build_feature_table(settings)
    write_processed(settings, features=frame)
    return frame


def cmd_train(args) -> None:
    from .models.bundle import save_bundle
    from .models.report import model_card, training_report
    from .models.train import train_all
    from .pipeline import is_synthetic, load_calendar

    settings = _settings(args)
    frame = _feature_frame(settings)
    if args.train_end:
        import pandas as pd

        frame = frame[frame["settle_date"] <= pd.Timestamp(args.train_end)]
    cal = load_calendar(settings)
    synthetic = is_synthetic(settings)
    version = args.version or f"fm-v1-{date.today():%Y%m%d}{'-synthetic' if synthetic else ''}"
    results = train_all(frame, cal, settings, tune=args.tune)
    path = save_bundle(results, settings, version)
    (path / "model_card.md").write_text(model_card(results, version, settings, synthetic))
    report = _write_report(settings, f"train_{version}.md", training_report(results, version, settings, synthetic))
    for d, r in results.items():
        print(d, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.acceptance.items()})
    print(f"bundle: {path}")
    print(f"report: {report}")


def _bundle(settings: Settings, path: str | None):
    from .models.bundle import latest_bundle, load_bundle

    return load_bundle(Path(path) if path else latest_bundle(settings))


def _dates(settings: Settings, start: str, through: str | None):
    from .pipeline import load_calendar

    cal = load_calendar(settings)
    days = cal.between(start, through or start)
    if not len(days):
        raise SystemExit(f"no business days between {start} and {through or start}")
    return list(days)


def cmd_score(args) -> None:
    from .scoring import score_dates

    settings = _settings(args)
    bundle = _bundle(settings, args.bundle)
    runs = score_dates(settings, _dates(settings, args.date, args.through), bundle)
    print(f"model {bundle.version}: {sum(r.run.get('rows', 0) for r in runs):,} trades over {len(runs)} scoring dates "
          f"-> {settings.scores_dir}")


def cmd_monitor(args) -> None:
    from .monitoring import monitor_dates, shadow_summary
    from .profiling import md_table

    settings = _settings(args)
    bundle = _bundle(settings, args.bundle)
    days = _dates(settings, args.date, args.through)
    result = monitor_dates(settings, days, bundle)
    summary = shadow_summary(settings, bundle)
    counts = result.alerts.groupby(["signal", "severity"]).size().rename("alerts").reset_index() if len(result.alerts) else result.alerts
    print(md_table(counts))
    print(md_table(result.rolling.tail(4)))
    print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in summary.items()})
    if args.report:
        text = shadow_report(settings, bundle, result, summary, days)
        print(f"report: {_write_report(settings, f'shadow_{days[0]:%Y%m%d}_{days[-1]:%Y%m%d}.md', text)}")


def shadow_report(settings: Settings, bundle, result, summary: dict, days) -> str:
    from .pipeline import is_synthetic
    from .profiling import md_table

    banner = "> **Synthetic data.** Shadow results on the synthetic test bed; not real performance.\n\n" if is_synthetic(settings) else ""
    counts = (result.alerts.groupby(["signal", "severity"]).size().rename("alerts").reset_index()
              if len(result.alerts) else result.alerts)
    first = result.alerts.groupby("signal")["date"].min().rename("first_alert").reset_index() if len(result.alerts) else result.alerts
    roll = result.rolling.copy()
    weekly = roll[roll["as_of"].isin(sorted(roll["as_of"].unique())[::5])] if len(roll) else roll
    sd = result.score_drift
    return "\n\n".join([
        f"# Shadow scoring report — {bundle.version}",
        banner + f"Scoring dates {days[0]:%Y-%m-%d} to {days[-1]:%Y-%m-%d} ({len(days)} business days).",
        "## Go-live checks\n\n"
        + f"Ratios compare the latest {settings.monitoring.rolling_window_bd}-day realized window to holdout; `_ok` needs "
        f"ratio >= {settings.monitoring.pr_auc_floor_ratio}. `info_` flags are not gates.\n\n"
        + "\n".join(f"- `{k}`: {round(v, 3) if isinstance(v, float) else v}" for k, v in summary.items())
        + "\n- Ops review of top-50 lists and reason codes: pending (manual)\n- Integration path agreed with the WTS data team: pending (manual)\n- Retraining cadence agreed: pending (manual)",
        "## Alerts by signal\n\n" + md_table(counts) + "\n\n" + md_table(first),
        "## Rolling performance (every 5th day)\n\n" + md_table(weekly, max_rows=60),
        "## Daily realized performance\n\n" + md_table(result.daily, max_rows=120),
        "## Score drift (PSI vs holdout scores)\n\n" + md_table(sd.groupby("direction")["psi"].describe().reset_index() if len(sd) else sd),
    ]) + "\n"


def cmd_persistence(args) -> None:
    from .persistence import build_persistence_table, reconcile_summary, train_persistence
    from .pipeline import write_processed

    settings = _settings(args)
    table = build_persistence_table(settings)
    write_processed(settings, persistence=table.frame)
    recon = table.reconcile()
    reports = settings.artifacts_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    recon.to_csv(reports / "persistence_reconciliation.csv", index=False)
    results = train_persistence(table.frame, table.cal, settings, tasks=args.task, tune=args.tune)
    text = persistence_report(settings, table, reconcile_summary(recon), results)
    print(f"report: {_write_report(settings, f'persistence_{date.today():%Y%m%d}.md', text)}")


def persistence_report(settings: Settings, table, recon_summary, results) -> str:
    import pandas as pd

    from .models.train import accepted
    from .persistence import LABELS, results_table
    from .pipeline import is_synthetic
    from .profiling import md_table

    f = table.frame
    banner = "> **Synthetic data.** Results on the synthetic test bed; not real performance.\n\n" if is_synthetic(settings) else ""
    labels = pd.DataFrame([{"label": c, "labelled_rows": int(f[c].notna().sum()),
                            "rate": float(f[c].astype(float).mean())} for c in LABELS])
    counts = f.groupby(["book", "direction"]).size().rename("rows").reset_index()
    parts = [
        "# Fail persistence and close-out risk (phase 5)",
        banner + f"Tracked fail-snapshot rows: {len(table.tracked):,}; without an eligible trade (excluded): "
        f"{table.unmatched:,}; modelling rows from {settings.label_start}: {len(f):,}.",
        "## Rows and labels\n\n" + md_table(counts) + "\n\n" + md_table(labels),
        "## Reconciliation with snapshot counts\n\n" + md_table(recon_summary),
        "## Holdout results\n\n" + md_table(results_table(results))
        + "\n\nThe plan-07 exit criterion is `still_open_h3` beating `age_only` on holdout PR-AUC. \"Accepted\" below "
          "applies the phase-3 gates as well (ECE <= 0.02, fold stability >= 0.7), which small holdouts rarely "
          "meet (documentation DEC-19).",
    ]
    for name, by_dir in results.items():
        for direction, r in by_dir.items():
            acc = "\n".join(f"- `{k}`: {round(v, 3) if isinstance(v, float) else v}" for k, v in r.acceptance.items())
            ages = r.slices[r.slices["slice"].eq("age_bucket")].drop(columns="slice")
            parts.append(f"### {name}, direction {direction} (accepted: {accepted(r.acceptance)})\n\n{acc}\n\n"
                         + md_table(r.holdout_metrics[["model", "rows", "base_rate", "mean_score", "pr_auc", "roc_auc", "ece"]])
                         + "\n\nBy age bucket:\n\n" + md_table(ages)
                         + "\n\nTop features (mean |SHAP|):\n\n" + md_table(r.importance.head(10)))
    return "\n\n".join(parts) + "\n"


def cmd_catalog(args) -> None:
    from .features.build import doc_catalog_markdown, deferred_markdown, write_doc_catalog

    if args.doc:
        write_doc_catalog(args.doc)
        print(f"catalog written into {args.doc}")
        return
    print(doc_catalog_markdown())
    print()
    print(deferred_markdown())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="failmodel")
    parser.add_argument("--data-dir")
    parser.add_argument("--artifacts-dir")
    parser.add_argument("--label-start")
    parser.add_argument("--extract-start")
    parser.add_argument("--p3-record-types", help="comma-separated P3 RECORD_TYPE codes from trades (profiling P5)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("synth", help="write synthetic extracts in the native layout")
    p.add_argument("--start", default="2024-01-02")
    p.add_argument("--end", default="2025-06-30")
    p.add_argument("--cusips", type=int, default=400)
    p.add_argument("--trades-per-day", type=int, default=150)
    p.add_argument("--seed", type=int, default=11)
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("extract", help="pull extracts through the WSI DB Wrapper")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--source", action="append")
    p.add_argument("--include-conditional", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("profile", help="run profiling checks P1-P8 over landed extracts")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("labels", help="build the trade universe, labels and Age-1 reconciliation")
    p.set_defaults(func=cmd_labels)

    p = sub.add_parser("features", help="build the point-in-time feature table and run the leakage checks")
    p.add_argument("--perturb-cutoff", help="run the future-perturbation test at this date")
    p.set_defaults(func=cmd_features)

    p = sub.add_parser("train", help="train, validate and calibrate the deliver and receive models")
    p.add_argument("--version", help="bundle version (default fm-v1-<date>)")
    p.add_argument("--tune", action="store_true", help="grid-search num_leaves x min_data_in_leaf on the rolling folds")
    p.add_argument("--train-end", help="last settle date used for training (to leave a later shadow window unseen)")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("score", help="nightly shadow scoring for trades settling the next business day")
    p.add_argument("--date", required=True, help="scoring date D")
    p.add_argument("--through", help="score every business day from --date through this date (backfill)")
    p.add_argument("--bundle", help="model bundle directory (default: latest under artifacts/models)")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("monitor", help="completeness, drift, realized performance and alerts")
    p.add_argument("--date", required=True)
    p.add_argument("--through")
    p.add_argument("--bundle")
    p.add_argument("--report", action="store_true", help="write the shadow report with go-live checks")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("persistence", help="phase 5: fail tracking, persistence and close-out models")
    p.add_argument("--task", action="append", help="task to train (repeatable; default: settings.persistence.tasks)")
    p.add_argument("--tune", action="store_true")
    p.set_defaults(func=cmd_persistence)

    p = sub.add_parser("catalog", help="print the feature catalog generated from the code")
    p.add_argument("--doc", help="rewrite the catalog block of this markdown file (failanalysispredictivemodel.md)")
    p.set_defaults(func=cmd_catalog)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
