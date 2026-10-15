# 03 — Phase 1: Trade Universe and Labels

- **Status:** Done 2026-10-14 (label build, reconciliation SQL and tests complete; validated on synthetic data in documentation section 11.2; the live build and the five-day report spot check wait on Q1, and the 90% criterion needs `p3_trade_record_types` from live P5, Q8)
- **Todo:** `phase1-labels`
- **Depends on:** [02-phase0-source-profiling.md](02-phase0-source-profiling.md), for the snapshot offset, the P3 deduplication rule and the direction semantics

## Objective

Produce one row per in-population trade, for both Beta and P3. Each row carries a label: did this trade fail on its contractual settle date? It also carries the diagnostics needed to reconcile with the existing Fail Analysis report.

## Definitions

- **S**: the contractual settle date of the trade (`Trade.SettleDate`, `TRDDTL_ARCH.SETTLE_FULL_DATE`).
- **D**: the scoring date, the business day before S. Features are taken as of D close.
- **Direction**, from the firm's side:
  - Beta: account purchase `PurchaseOrSale = 'P'` means the firm delivers (`ReceiveDeliverCode` 0); sale `'S'` means the firm receives (1).
  - P3: `BUY_SELL = 'B'` means deliver; `'S'` means receive.
  - P6 confirms both mappings against fail-record quantity signs.
- **Trade key:**
  - Beta: `B|ControlNumber|TradeDate|AccountNumber|SecurityNumber`.
  - P3: `P|TRADE_FULL_DATE|ACCT_NBR|CCURRENCY|ACCT_TYPE|MARKET|TRAN_NBR|FIRM_NBR`.

## Population rules

1. S falls in the label window, which starts at 2024-05-28.
2. The trade was entered on or before D, so it is visible at scoring time. Trades entered on S (same-day and late as-of trades) are excluded from v1.
3. Not cancelled or corrected:
   - Beta: `Cancelled = 0`.
   - P3: blank `CXL_COR_REASON`, `UNMATCHED_CANCEL` not 'Y', latest `SEQ_NBR` per key.
4. The fail-analysis exclusions:
   - Beta: `AccountType <> 8`, `SecurityTypeCode <> 'P'`.
   - P3: `ACCT_TYPE NOT IN ('2','3')`, `SEC_TYPE NOT IN ('5','6')`.
5. Quantity is non-zero. Money-only items are out of scope for v1.

## Label rules

- `failed_on_settle = 1` when a fail record matching the trade key appears in the label snapshot for S. That snapshot is the first fail snapshot dated S, shifted by `beta_snapshot_offset_bd` for Beta.
- `failed_on_settle = 0` when the label snapshot exists and has no match.
- `failed_on_settle = NULL` when the label snapshot for S is missing. These rows are dropped from training and evaluation, never treated as negatives.
- **Matching:**
  - Beta first matches exactly on `ControlNumber` together with trade date, account and security number.
  - For fail rows with `ControlNumber = 0`, it falls back to trade date, account, security number, direction and absolute quantity.
  - P3 matches on the 7-part key.
- **Extra columns on each row:**
  - `fail_quantity` and `fail_qty_fraction` (failed quantity divided by trade quantity), for partial fails.
  - `fail_notional`.
  - `fail_settle_date_differs`: the fail's settle date is not the trade's S.

## Reconciliation

1. Count Age-1 fails per S, book and direction directly from the snapshots, using the same filters as `GetBeta/P3FailAnalysisData_Summary` (`sql/labels/02_reconcile_age1.sql`).
2. Compare those counts with the matched positive labels. Report:
   - the match rate;
   - unmatched Age-1 fails, which are expected to be non-trade fails such as transfers, stock-loan returns and receive/deliver instructions;
   - positives whose fail settle date differs from S.
3. Spot-check five business days against `GetBetaFailAnalysisData_Report` and `GetP3FailAnalysisData_Report` through the wrapper. Both are already allowlisted.

## Outputs

- `src/failmodel/canonical.py`: maps Beta and P3 raw extracts to the canonical trade and fail schemas.
- `src/failmodel/labels.py`: builds the trade universe and labels, plus the reconciliation report.
- `sql/labels/01_label_reference.sql`: a server-side reference implementation, for WSI cross-checks.
- `sql/labels/02_reconcile_age1.sql`.
- Tests in `tests/test_labels.py`.

## Exit criteria

- The label table is built for the full window, for both books.
- Of the trade-originated Age-1 fails, at least 90% are matched to a trade. Every residual category is explained.
- Base rates per book and direction are recorded in the documentation.

## Documentation sections updated

Prediction unit, timing and label; Population rules; Results — Phase 1; Decisions log; Changelog.
