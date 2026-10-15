# 04 — Phase 2: Point-in-Time Features and Leakage Tests

- **Status:** Done 2026-10-14 (81 features in 7 families, `asof.py`, `leakage.py` and tests complete; the perturbation, forbidden-column and other checks pass on synthetic data, documentation section 11.3. The run on the full extract waits on Q1. Deferred features are listed in the generated catalog)
- **Todo:** `phase2-features`
- **Depends on:** [03-phase1-labels.md](03-phase1-labels.md)

## Objective

Build a canonical feature table, shared by Beta and P3, where every value is something that was actually known at 22:30 ET on the scoring date D. The leakage tests must fail the build if that is ever violated.

## As-of contract

- Each source is registered with an **as-of column** and an **availability lag**, in business days, in `src/failmodel/sources.py`.
- A row scored on D may only use source rows whose as-of date is at most D minus that lag.
- Snapshot sources take the latest snapshot that meets the rule. Gaps are not forward-filled more than `max_staleness_bd` (default 3).
- History features built from labels, such as counterparty fail rates, may only use trades with S ≤ D − `label_availability_lag_bd`.

## Feature families

| Family | Source (as-of key) | Examples |
|---|---|---|
| Trade | Trade record (entry) | direction, log notional, log quantity, account type, counterparty type, DVP flag (`StandingInstructionCode`, P3 `DEL_CODE`), capacity, execution hour, late-day flag, as-of flag, settle cycle length, non-USD, book |
| Security and liquidity | `DailyArch_Security` (`SnapShotDate`), `SecurityCloseHistory` (`TradeDate`) | DTC chill or frozen flags, CNS eligibility, bid-ask spread, ADV20, quantity / ADV20, 20-day volatility, price |
| Reg SHO | `SecurityExtensionHistory` (`EffectiveDate`), `CNS204FailSettlementData` (`ReportDate`) | threshold flag, 204 start-of-day fail quantity |
| Inventory and borrow | `DailyArch_SegregationAnalysisInfo`, `SmartLoanSLAvail` (`FileDate`), `LoanetOpenPositions` (`TradeDate`), `StockBorrowInterestRate` (`FileDate`) | box deficit, quantity / available, SmartLoan total and buckets, Loanet borrow and loan quantity, rebate, borrow VWAR |
| Fail pressure | Fail snapshots (`SnapShotDate`), CNS S1 (`SettleDate`/`TradeDate`) | open fails in the CUSIP by direction, aged fail quantity, consecutive CNS fail-to-deliver days |
| History | Phase 1 labels with S ≤ D − lag | smoothed fail rate over 20, 60 and 250 business days by counterparty, account and CUSIP; days since last fail; lagged DK count by counterparty |
| Calendar | Business calendar | day of week of S, month and quarter end, day after a holiday, firm trade count on D |

## Leakage controls (automated, in `src/failmodel/leakage.py` and `tests/test_leakage.py`)

1. **Forbidden-column check.** Outcome attributes (`DKCount`, `DTCReasonCode`, `FailWho`, `FailConditionCode`, `FAIL_REASON`, `StockLoanRate`) and every label column are rejected as direct features.
2. **As-of audit.** For each family, the maximum source as-of date used by any row must be at most D minus the lag.
3. **Future-perturbation test.** Randomize every source row dated after a cutoff date, rebuild features, and assert that features for rows with D ≤ cutoff are identical.
4. **Single-feature screen.** Any feature with a single-feature ROC-AUC above 0.95 is flagged for review.
5. **Label-shuffle test.** A model trained on permuted labels must score close to the base-rate PR-AUC.

## Outputs

- `src/failmodel/sources.py`: the source registry.
- `src/failmodel/asof.py`: point-in-time join helpers.
- `src/failmodel/features/`: one module per family, plus `build.py`.
- `src/failmodel/leakage.py`.
- Tests.
- A feature catalog in the documentation (name, family, source, as-of rule, leakage status, books covered).

## Exit criteria

- The perturbation and forbidden-column tests pass in CI and on the full extract.
- Feature coverage (share non-null) is reported per book. Features missing for one book are documented.
- The feature catalog in the documentation matches the code (generated from `features.build.FEATURE_CATALOG`).

## Documentation sections updated

Feature catalog, Leakage rules, Beta/P3 harmonization, Results — Phase 2, Changelog.
