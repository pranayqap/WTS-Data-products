# 02 — Phase 0b: Source Profiling

- **Status:** Done 2026-10-14 (profiling SQL, `failmodel.profiling` and the synthetic validation are complete; the live P1–P8 run waits on Q1, and the recommended configuration is recorded in the documentation, section 11.1)
- **Todo:** `phase0-profile`
- **Depends on:** [01-phase0-data-access.md](01-phase0-data-access.md). The queries can also be run directly by WSI before extracts exist.

## Objective

Confirm history depth, snapshot timing and join keys before any label is built. Every label and as-of rule in phases 1 and 2 depends on these facts. The mirrored definitions show what the tables look like, but not when or how they are actually loaded.

## Checks

| ID | Question | Why it matters | Query |
|---|---|---|---|
| P1 | History depth and gaps per source, against `ControlDB.dbo.DateMaster` business dates | Training window; missing snapshots must become "label unknown", never "no fail" | `sql/profiling/01_history_depth_and_gaps.sql` |
| P2 | What business day a Beta `DailyArch_OpenFailReceiveDeliverInfo.SnapShotDate` represents, and when it lands (`UpdateDateTime`, SQL Agent schedule for the `fal` load) | Sets `beta_snapshot_offset_bd` and `label_availability_lag_bd` | `sql/profiling/02_snapshot_timing.sql` |
| P3 | When `StockLoanBorrow` and `BorrowForNeeds` run for a `SettleDate`, and whether they are re-run | Whether their open-fail-derived columns are usable as of D | `sql/profiling/02_snapshot_timing.sql` |
| P4 | When `DailyArch_Security` and `DailyArch_SegregationAnalysisInfo` are written for a snapshot | Whether the D snapshot exists by 22:30 ET on D | `sql/profiling/02_snapshot_timing.sql` |
| P5 | Trade-to-fail join hit rates: Beta by `ControlNumber` (plus fallback), P3 by the 7-part key; `TRDDTL_ARCH` rows per key | Label coverage; how to deduplicate P3 trade rows | `sql/profiling/03_join_hit_rates.sql` |
| P6 | Code semantics: P3 `BUY_SELL` vs. the sign of `QUANTITY`; Beta `PurchaseOrSale` vs. `ReceiveDeliverCode`; share of `ControlNumber = 0`; CNS `Cycle` values | Direction mapping, fallback matching, choice of end-of-day CNS cycle | `sql/profiling/04_code_semantics.sql` |
| P7 | Base rates: Age-1 fails and settling trades per day, by book and direction | Class imbalance; top-K sizing | `sql/profiling/05_base_rates.sql` |
| P8 | Effect of the 2025-08-14 P3 source switch on `EODFAIMST` row counts | Labels built directly from `EODFAIMST` must be stable across the switch | `sql/profiling/05_base_rates.sql` |

The same checks run on landed extracts through `failmodel.profiling` (`python -m failmodel.cli profile`), so results can be reproduced from the Parquet files.

## Outputs

- [`../sql/profiling/`](../sql/profiling/): read-only queries. Each one is `SELECT` only, takes a date window, and reads no PII.
- `src/failmodel/profiling.py`: the same checks over extracts, producing a Markdown report.
- The results table in the documentation, section "Results — Phase 0", and the config values in `src/failmodel/config.py`.

## Exit criteria

- P1 to P8 each have a recorded result or a named blocker.
- These config values are set from evidence, not left at defaults:
  - `beta_snapshot_offset_bd`
  - `label_availability_lag_bd`
  - `cns_eod_cycle`
  - the P3 trade deduplication rule
- Any source whose D snapshot is not available by 22:30 ET on D gets a one-business-day availability lag in the source registry.

## Documentation sections updated

Data sources, Results — Phase 0, Open questions, Decisions log, Changelog.
