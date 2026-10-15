"""Assemble the canonical feature table and the feature catalog.

One label build over the whole extract window feeds both the history family (all settle dates) and the
training rows (settle dates on or after ``settings.label_start``).
"""

from __future__ import annotations

import pandas as pd

from ..asof import AUDIT_PREFIX
from ..config import Settings
from ..labels import LABEL_COLUMNS
from ..pipeline import Dataset, label_dataset, load_dataset
from . import affirmation, calendar_features, fail_pressure, history, inventory, security, trade
from .base import FeatureContext, FeatureSpec

FAMILIES = [trade, security, inventory, affirmation, fail_pressure, history, calendar_features]
FEATURE_CATALOG: list[FeatureSpec] = [spec for module in FAMILIES for spec in module.SPECS]
FEATURE_NAMES = [s.name for s in FEATURE_CATALOG]
CATEGORICAL = [s.name for s in FEATURE_CATALOG if s.kind == "categorical"]
TARGET = "failed_on_settle"
ID_COLUMNS = ["trade_key", "unique_ref", "book", "direction", "trade_date", "settle_date", "scoring_date",
              "entry_date", "account", "counterparty_id", "cusip9", "product_group", "quantity", "notional"]
OUTCOME_COLUMNS = [c for c in LABEL_COLUMNS if c != "label_snapshot_date"]

DEFERRED = [
    ("dk_count_by_counterparty", "history",
     "Beta fail counterparties are NSCC numbers while trade counterparties are contra broker numbers; "
     "a lagged DK count needs a mapping between the two ID spaces (open question)."),
    ("counterparty_open_fails", "fail_pressure", "Same ID-space mismatch as dk_count_by_counterparty."),
    ("smartloan_buckets", "inventory", "SmartLoan bucket columns wait on the P4 column profile; only totals are used."),
    ("locate_demand", "inventory", "Locates live in LocateSecurity, which the wrapper cannot reach (access request)."),
    ("borrow_needs", "inventory", "StockLoanBorrow / BorrowForNeeds history depth is unconfirmed (P3)."),
]


def build_context(ds: Dataset, settings: Settings) -> tuple[FeatureContext, pd.DataFrame]:
    """Labels over the full extract window and the shared feature context."""
    window = (settings.extract_start, ds.cal.days[-1])
    result = label_dataset(ds, settings, window)
    ctx = FeatureContext(raw=ds.raw, cal=ds.cal, settings=settings, fails=ds.fails,
                         universe=result.universe, history=result.labels)
    return ctx, result.labels


def build_features(base: pd.DataFrame, ctx: FeatureContext) -> pd.DataFrame:
    base = base.reset_index(drop=True)
    parts = [module.compute(base, ctx) for module in FAMILIES]
    feats = pd.concat(parts, axis=1)
    dupes = feats.columns[feats.columns.duplicated()].tolist()
    if dupes:
        raise ValueError(f"duplicate feature columns: {dupes}")
    missing = [n for n in FEATURE_NAMES if n not in feats.columns]
    if missing:
        raise ValueError(f"families did not produce: {missing}")
    ids = base[[c for c in ID_COLUMNS if c not in FEATURE_NAMES]]
    outcomes = base[[c for c in OUTCOME_COLUMNS if c in base.columns]]
    audit = feats[[c for c in feats.columns if c.startswith(AUDIT_PREFIX)]]
    return pd.concat([ids, feats[FEATURE_NAMES], audit, outcomes], axis=1)


def build_feature_table(settings: Settings, raw: dict | None = None) -> tuple[pd.DataFrame, FeatureContext]:
    ds = load_dataset(settings, raw)
    ctx, labels = build_context(ds, settings)
    base = labels[labels["settle_date"] >= pd.Timestamp(settings.label_start)]
    return build_features(base, ctx), ctx


def coverage_by_book(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for spec in FEATURE_CATALOG:
        row = {"feature": spec.name, "family": spec.family}
        for book in ("B", "P"):
            part = frame.loc[frame["book"].eq(book), spec.name]
            row[f"coverage_{book}"] = float(part.notna().mean()) if len(part) else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def catalog_frame(leakage_status: dict[str, str] | None = None) -> pd.DataFrame:
    status = leakage_status or {}
    return pd.DataFrame([
        {"feature": s.name, "family": s.family, "kind": s.kind, "source": s.source, "asof_rule": s.asof_rule,
         "books": s.books, "leakage_status": status.get(s.name, "not run"), "description": s.description}
        for s in FEATURE_CATALOG
    ])


def catalog_markdown(leakage_status: dict[str, str] | None = None, coverage: pd.DataFrame | None = None) -> str:
    cat = catalog_frame(leakage_status)
    if coverage is not None:
        cat = cat.merge(coverage[["feature", "coverage_B", "coverage_P"]], on="feature", how="left")
    head = "| Feature | Family | Kind | Source | As-of rule | Books | Leakage status | Description |"
    sep = "|---|---|---|---|---|---|---|---|"
    if coverage is not None:
        head += " Coverage B | Coverage P |"
        sep += "---|---|"
    lines = [head, sep]
    for r in cat.itertuples(index=False):
        cells = [f"`{r.feature}`", r.family, r.kind, f"`{r.source}`", r.asof_rule, r.books, r.leakage_status,
                 r.description.replace("|", "\\|")]
        if coverage is not None:
            cells += [_pct(r.coverage_B), _pct(r.coverage_P)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


DOC_BEGIN = "<!-- BEGIN FEATURE CATALOG (generated: failmodel catalog --doc) -->"
DOC_END = "<!-- END FEATURE CATALOG -->"


def doc_catalog_markdown() -> str:
    """Data-free catalog for the official documentation (coverage and leakage status live in the results)."""
    lines = ["| Feature | Family | Kind | Source | As-of rule | Books | Description |", "|---|---|---|---|---|---|---|"]
    for s in FEATURE_CATALOG:
        lines.append(f"| `{s.name}` | {s.family} | {s.kind} | `{s.source}` | {s.asof_rule} | {s.books} | "
                     f"{s.description.replace('|', chr(92) + '|')} |")
    return "\n".join(lines)


def write_doc_catalog(path) -> None:
    from pathlib import Path

    text = Path(path).read_text()
    start, end = text.index(DOC_BEGIN) + len(DOC_BEGIN), text.index(DOC_END)
    block = "\n\n" + doc_catalog_markdown() + "\n\n**Deferred features**\n\n" + deferred_markdown() + "\n\n"
    Path(path).write_text(text[:start] + block + text[end:])


def deferred_markdown() -> str:
    lines = ["| Feature | Family | Why deferred |", "|---|---|---|"]
    lines += [f"| `{n}` | {f} | {why} |" for n, f, why in DEFERRED]
    return "\n".join(lines)


def _pct(v) -> str:
    return "n/a" if pd.isna(v) else f"{v:.0%}"
